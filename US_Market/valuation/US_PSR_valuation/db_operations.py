# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from utils import log

def ddl(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
    """밸류에이션 테이블 DDL 생성"""
    tail = f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={collation};" if with_collate else \
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
    return f"""
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
      UNIQUE KEY `uq_tk_ca_cat_model_start`
        (`ticker`,`created_at`,`category`,`model`,`start_month_end`)
    ) {tail}
    """

def ddl_revenue_forecast(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
    """Revenue forecast 테이블 DDL 생성"""
    tail = f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={collation};" if with_collate else \
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
    return f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
      `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      `ticker` VARCHAR(16) NOT NULL,
      `date_month_end` DATE NOT NULL,
      `revenue_billions_sarima_noexog` DECIMAL(20,8) NULL,
      `revenue_billions_lstm_forecast` DECIMAL(20,8) NULL,
      `revenue_billions_prophet_forecast` DECIMAL(20,8) NULL,
      `revenue_billions_esq_forecast` DECIMAL(20,8) NULL,
      `created_at` DATETIME NOT NULL,
      `created_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_ticker_date` (`ticker`, `date_month_end`),
      KEY `idx_created_at` (`created_at`),
      UNIQUE KEY `uq_ticker_date` (`ticker`, `date_month_end`)
    ) {tail}
    """

def is_unknown_collation(err: Exception) -> bool:
    """알 수 없는 collation 에러 확인"""
    if "Unknown collation" in str(err):
        return True
    try:
        code = getattr(getattr(err, "orig", err), "args", [None])[0]
        return code == 1273
    except Exception:
        return False

def ensure_valuation_table(db_info: dict, table_name: str = "us_valuation_result"):
    """밸류에이션 테이블 존재 확인 및 생성"""
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with engine.begin() as conn:
        try:
            conn.execute(text(ddl(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
            return
        except Exception as e:
            if not is_unknown_collation(e):
                raise
        try:
            conn.execute(text(ddl(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
            return
        except Exception as e2:
            if not is_unknown_collation(e2):
                raise
        conn.execute(text(ddl(table_name, with_collate=False)))

def ensure_revenue_forecast_table(db_info: dict, table_name: str = "us_revenue_forecast_result"):
    """Revenue forecast 테이블 존재 확인 및 생성"""
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with engine.begin() as conn:
        try:
            conn.execute(text(ddl_revenue_forecast(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
            return
        except Exception as e:
            if not is_unknown_collation(e):
                raise
        try:
            conn.execute(text(ddl_revenue_forecast(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
            return
        except Exception as e2:
            if not is_unknown_collation(e2):
                raise
        conn.execute(text(ddl_revenue_forecast(table_name, with_collate=False)))

def upsert_long_to_db_on_ticker_created_at(long_df: pd.DataFrame,
                                           db_info: dict,
                                           table_name: str = "us_valuation_result") -> int:
    """Long 형식 데이터를 DB에 업서트"""
    if long_df.empty:
        return 0

    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )

    needed_cols = ['ticker', 'category', 'model', 'start_month_end', 'start_value', 'end_value', 'growth', 'created_at']
    for c in needed_cols:
        if c not in long_df.columns:
            long_df[c] = np.nan
    use_df = long_df[needed_cols].copy()

    affected = 0
    with engine.begin() as conn:
        grp_cols = ['ticker', 'created_at']
        for (tk, ca), g in use_df.groupby(grp_cols):
            del_sql = f"""
                DELETE FROM {table_name}
                WHERE ticker = :ticker AND created_at = :created_at
            """
            conn.execute(text(del_sql), {'ticker': str(tk), 'created_at': pd.to_datetime(ca)})

            g.to_sql(table_name, conn, if_exists='append', index=False)
            affected += len(g)

    return affected

def upsert_revenue_forecast_to_db(df: pd.DataFrame,
                                  db_info: dict,
                                  table_name: str = "us_revenue_forecast_result") -> int:
    """Revenue forecast 데이터를 DB에 업서트"""
    if df is None or df.empty:
        return 0

    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )

    base_cols = ['ticker', 'date_month_end',
                 'revenue_billions_sarima_noexog',
                 'revenue_billions_lstm_forecast',
                 'revenue_billions_prophet_forecast',
                 'revenue_billions_esq_forecast']

    use_df = df.copy()

    for col in base_cols:
        if col not in use_df.columns:
            use_df[col] = np.nan

    use_df['created_at'] = pd.Timestamp.utcnow().replace(tzinfo=None)
    use_df['date_month_end'] = pd.to_datetime(use_df['date_month_end'], errors='coerce')
    use_df = use_df.dropna(subset=['ticker', 'date_month_end'])

    if use_df.empty:
        return 0

    final_cols = base_cols + ['created_at']
    use_df = use_df[final_cols]
    use_df = use_df.drop_duplicates(subset=['ticker', 'date_month_end'], keep='last')

    affected = 0
    with engine.begin() as conn:
        for ticker, group in use_df.groupby('ticker'):
            for _, row in group.iterrows():
                delete_sql = text("""
                    DELETE FROM {table_name}
                    WHERE ticker = :ticker AND date_month_end = :date_month_end
                """.format(table_name=table_name))

                conn.execute(delete_sql, {
                    'ticker': str(row['ticker']),
                    'date_month_end': row['date_month_end']
                })

                insert_sql = text("""
                    INSERT INTO {table_name} 
                    (ticker, date_month_end, revenue_billions_sarima_noexog, 
                     revenue_billions_lstm_forecast, revenue_billions_prophet_forecast, 
                     revenue_billions_esq_forecast, created_at)
                    VALUES 
                    (:ticker, :date_month_end, :sarima, :lstm, :prophet, :es, :created_at)
                """.format(table_name=table_name))

                conn.execute(insert_sql, {
                    'ticker': str(row['ticker']),
                    'date_month_end': row['date_month_end'],
                    'sarima': row['revenue_billions_sarima_noexog'] if pd.notna(
                        row['revenue_billions_sarima_noexog']) else None,
                    'lstm': row['revenue_billions_lstm_forecast'] if pd.notna(
                        row['revenue_billions_lstm_forecast']) else None,
                    'prophet': row['revenue_billions_prophet_forecast'] if pd.notna(
                        row['revenue_billions_prophet_forecast']) else None,
                    'es': row['revenue_billions_esq_forecast'] if pd.notna(
                        row['revenue_billions_esq_forecast']) else None,
                    'created_at': row['created_at']
                })
                affected += 1

            log("DB-REV-UPSERT", f"{ticker} upserted {len(group)} rows")

    return affected
