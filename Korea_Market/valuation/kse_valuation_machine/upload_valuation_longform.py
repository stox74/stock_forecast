# -*- coding: utf-8 -*-
import pandas as pd
from typing import List, Dict, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# ------------------------------
# 0) 유틸
# ------------------------------
def _ensure_date_col(df: pd.DataFrame) -> pd.DataFrame:
    """date 컬럼이 없으면 index를 reset하여 date로 이름 정규화."""
    if 'date' in df.columns:
        out = df.copy()
    else:
        out = df.reset_index()
        if 'date' not in out.columns:
            # 가능한 후보를 date로 정규화
            for cand in ['index', 'Date', 'ds', out.columns[0]]:
                if cand in out.columns:
                    out = out.rename(columns={cand: 'date'})
                    break
    return out

def _melt_long(df: pd.DataFrame, ticker: str, forecast_date: Optional[str]) -> pd.DataFrame:
    """
    df: 반드시 date + 지표컬럼들 형태
    -> date, indicator, value 로 melt 후 ticker/forecast_date 추가
    """
    df = _ensure_date_col(df)
    # 숫자형만 value 후보로: (날짜/범주형 보호)
    value_cols = [c for c in df.columns if c != 'date' and pd.api.types.is_numeric_dtype(df[c])]
    if not value_cols:
        return pd.DataFrame(columns=['date','ticker','indicator','value','forecast_date'])

    long_df = df.melt(id_vars=['date'], value_vars=value_cols,
                      var_name='indicator', value_name='value')
    # 날짜 표준화(일자 정보 없으면 월말로 변환되지 않도록 그대로 보존)
    long_df['date'] = pd.to_datetime(long_df['date']).dt.date
    # value NaN 제거
    long_df = long_df.dropna(subset=['value'])

    long_df['ticker'] = ticker
    # forecast_date 기본값: 오늘 날짜(YYYY-MM-DD)
    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()
    long_df['forecast_date'] = pd.to_datetime(forecast_date).date()

    # 컬럼 순서 정리
    long_df = long_df[['date', 'ticker', 'indicator', 'value', 'forecast_date']]
    return long_df

def _build_engine(db_info: Dict) -> Engine:
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url, pool_recycle=3600)

# ------------------------------
# 1) 메인: long form 생성 + 업서트
# ------------------------------
def upload_valuation_longform(
    valuation_forecast_result: pd.DataFrame,
    fc_table: pd.DataFrame,
    rev_final: pd.DataFrame,
    psr_df: pd.DataFrame,
    ticker: str,
    db_info: Dict,
    table_name: str = "Korea_company_valuation_ver2",
    forecast_date: Optional[str] = None,
    chunksize: int = 1000,
):
    """
    네 개의 DF를 long form으로 변환 후, MySQL에 업서트.
    - 같은 forecast_date이면 (ticker, date, indicator) 기준 덮어쓰기
    - forecast_date 다르면 신규 추가
    """
    # 1) long form 변환
    parts: List[pd.DataFrame] = []
    parts.append(_melt_long(valuation_forecast_result, ticker, forecast_date))
    parts.append(_melt_long(fc_table,                   ticker, forecast_date))
    parts.append(_melt_long(rev_final,                  ticker, forecast_date))
    parts.append(_melt_long(psr_df,                     ticker, forecast_date))

    long_all = pd.concat(parts, ignore_index=True)
    # 중복 제거(동일 지표가 여러 DF에 있을 경우 마지막 우선)
    long_all = long_all.drop_duplicates(subset=['date','ticker','indicator','forecast_date'], keep='last')

    # 2) DB 연결 및 테이블 생성(없으면)
    engine = _build_engine(db_info)
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
        `date` DATE NOT NULL,
        `ticker` VARCHAR(16) NOT NULL,
        `indicator` VARCHAR(64) NOT NULL,
        `value` DOUBLE NULL,
        `forecast_date` DATE NOT NULL,
        PRIMARY KEY (`ticker`, `date`, `indicator`, `forecast_date`),
        INDEX `idx_date` (`date`),
        INDEX `idx_ticker` (`ticker`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.execute(text(create_sql))

    # 3) UPSERT (ON DUPLICATE KEY UPDATE)
    rows = long_all.to_dict(orient='records')
    if not rows:
        print("[INFO] 업로드할 데이터가 없습니다.")
        return

    insert_sql = f"""
    INSERT INTO `{table_name}` (`date`, `ticker`, `indicator`, `value`, `forecast_date`)
    VALUES (:date, :ticker, :indicator, :value, :forecast_date)
    ON DUPLICATE KEY UPDATE
        `value` = VALUES(`value`);
    """

    with engine.begin() as conn:
        # 청크 업로드
        for i in range(0, len(rows), chunksize):
            conn.execute(text(insert_sql), rows[i:i+chunksize])

    print(f"[OK] {len(rows)} rows upserted into {table_name} for ticker={ticker} (forecast_date={rows[0]['forecast_date']}).")
