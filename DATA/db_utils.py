# -*- coding: utf-8 -*-
from typing import Optional, Dict
import pandas as pd
import numpy as np
from sqlalchemy import text
from config import get_engine, log

TABLE_VAL = "us_valuation_result"
TABLE_REV_FC = "us_revenue_forecast_result"

# ---------- 공통 테이블 보장 ----------
def ensure_valuation_table(db_info: Dict[str,str], table_name: str = TABLE_VAL):
    ddl = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
      `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      `ticker` VARCHAR(16) NOT NULL,
      `category` VARCHAR(32) NOT NULL,
      `model` VARCHAR(64) NOT NULL,
      `start_month_end` DATE NULL,
      `start_value` DECIMAL(20,8) NULL,
      `end_value` DECIMAL(20,8) NULL,
      `growth` DECIMAL(20,8) NULL,
      `created_at` DATETIME NOT NULL,
      `created_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_created_at` (`created_at`),
      KEY `idx_ticker_created` (`ticker`, `created_at`),
      UNIQUE KEY `uq_tk_ca_cat_model_start` (`ticker`,`created_at`,`category`,`model`,`start_month_end`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    eng = get_engine(db_info)
    with eng.begin() as conn:
        conn.execute(text(ddl))

def ensure_us_revenue_forecast_result_table(db_info: Dict[str,str], table_name: str = TABLE_REV_FC):
    ddl = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
      `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      `ticker` VARCHAR(16) NOT NULL,
      `date_month_end` DATE NOT NULL,
      `market_cap_billions` DECIMAL(20,8) NULL,
      `revenue_billions` DECIMAL(20,8) NULL,
      `created_at` DATETIME NOT NULL,
      `created_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uq_tk_dme_ca` (`ticker`,`date_month_end`,`created_at`),
      KEY `idx_created_at` (`created_at`),
      KEY `idx_ticker_dme` (`ticker`,`date_month_end`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    eng = get_engine(db_info)
    with eng.begin() as conn:
        conn.execute(text(ddl))

# ---------- us_valuation_result 업서트 ----------
def upsert_long_to_db_on_ticker_created_at(
    long_df: pd.DataFrame,
    db_info: Dict[str,str],
    table_name: str = TABLE_VAL,
) -> int:
    """
    (ticker, created_at) 조합 단위로 기존 삭제 후 insert → '같은 날 갱신, 다른 날 누적'
    """
    if long_df is None or long_df.empty:
        return 0

    ensure_valuation_table(db_info, table_name)
    needed = ['ticker','category','model','start_month_end','start_value','end_value','growth','created_at']
    for c in needed:
        if c not in long_df.columns:
            long_df[c] = np.nan
    use_df = long_df[needed].copy()

    eng = get_engine(db_info)
    affected = 0
    with eng.begin() as conn:
        for (tk, ca), g in use_df.groupby(['ticker','created_at']):
            conn.execute(
                text(f"DELETE FROM {table_name} WHERE ticker=:tk AND created_at=:ca"),
                {"tk": str(tk), "ca": pd.to_datetime(ca)}
            )
            g.to_sql(table_name, conn, if_exists='append', index=False)
            affected += len(g)
    return affected

# ---------- us_revenue_forecast_result 업서트 ----------
def upsert_enhanced_merged_df(
    enhanced_merged_df: pd.DataFrame,
    db_info: Dict[str,str],
    ticker: Optional[str],
    created_at: str,                     # ex) measurement_date + " 00:00:00"
    table_name: str = TABLE_REV_FC,
    chunk_size: int = 1000,
) -> int:
    """
    enhanced_merged_df → us_revenue_forecast_result
    키: (ticker, date_month_end, created_at) → ON DUPLICATE KEY UPDATE
    """
    if enhanced_merged_df is None or enhanced_merged_df.empty:
        return 0

    ensure_us_revenue_forecast_result_table(db_info, table_name)
    df = enhanced_merged_df.copy()

    # 필수 컬럼 보정
    if 'date_month_end' not in df.columns:
        if 'date' in df.columns:
            df['date_month_end'] = pd.to_datetime(df['date']).dt.to_period('M').dt.to_timestamp('M')
        else:
            raise ValueError("enhanced_merged_df에 'date_month_end' 또는 'date' 컬럼이 필요합니다.")
    df['date_month_end'] = pd.to_datetime(df['date_month_end']).dt.date

    if 'ticker' not in df.columns or df['ticker'].isna().all():
        if not ticker:
            raise ValueError("ticker 파라미터가 필요합니다 (enhanced_merged_df에 ticker가 없음).")
        df['ticker'] = str(ticker)

    for c in ['market_cap_billions','revenue_billions']:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors='coerce')

    df['created_at'] = pd.to_datetime(created_at)

    use_cols = ['ticker','date_month_end','market_cap_billions','revenue_billions','created_at']
    df_up = df[use_cols].drop_duplicates(subset=['ticker','date_month_end'])

    eng = get_engine(db_info)
    rows = 0
    insert_sql = text(f"""
        INSERT INTO {table_name}
        (ticker, date_month_end, market_cap_billions, revenue_billions, created_at)
        VALUES (:ticker, :date_month_end, :market_cap_billions, :revenue_billions, :created_at)
        ON DUPLICATE KEY UPDATE
            market_cap_billions = VALUES(market_cap_billions),
            revenue_billions    = VALUES(revenue_billions)
    """)
    with eng.begin() as conn:
        for start in range(0, len(df_up), chunk_size):
            chunk = df_up.iloc[start:start+chunk_size].copy()
            params = []
            for _, r in chunk.iterrows():
                params.append({
                    "ticker": str(r['ticker']),
                    "date_month_end": pd.to_datetime(r['date_month_end']).date(),
                    "market_cap_billions": None if pd.isna(r['market_cap_billions']) else float(r['market_cap_billions']),
                    "revenue_billions":    None if pd.isna(r['revenue_billions'])    else float(r['revenue_billions']),
                    "created_at":          pd.to_datetime(r['created_at']).to_pydatetime(),
                })
            conn.execute(insert_sql, params)
            rows += len(params)
    log("REV-FC-UPLOAD", f"ticker={ticker} rows={rows}")
    return rows
