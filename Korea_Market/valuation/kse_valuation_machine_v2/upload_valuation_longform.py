# -*- coding: utf-8 -*-
"""
Valuation Long Form 업로드 (중복 저장 절대 방지)
- 같은 forecast_date의 기존 데이터 삭제 후 삽입
- 날짜 표준화 적용
"""
import pandas as pd
from typing import List, Dict, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _ensure_date_col(df: pd.DataFrame) -> pd.DataFrame:
    """date 컬럼 확보"""
    if 'date' in df.columns:
        out = df.copy()
    else:
        out = df.reset_index()
        if 'date' not in out.columns:
            for cand in ['index', 'Date', 'ds', out.columns[0]]:
                if cand in out.columns:
                    out = out.rename(columns={cand: 'date'})
                    break
    return out


def _melt_long(df: pd.DataFrame, ticker: str, forecast_date: Optional[str]) -> pd.DataFrame:
    """
    Wide format → Long format 변환
    - 날짜는 그대로 유지 (분기말/월말 표준화는 이미 완료된 상태)
    """
    df = _ensure_date_col(df)

    # 숫자형 컬럼만 value로
    value_cols = [c for c in df.columns if c != 'date' and pd.api.types.is_numeric_dtype(df[c])]
    if not value_cols:
        return pd.DataFrame(columns=['date', 'ticker', 'indicator', 'value', 'forecast_date'])

    long_df = df.melt(id_vars=['date'], value_vars=value_cols,
                      var_name='indicator', value_name='value')

    # 날짜 표준화 (이미 표준화되어 있어야 하지만 한번 더 확인)
    long_df['date'] = pd.to_datetime(long_df['date']).dt.date

    # NaN 제거
    long_df = long_df.dropna(subset=['value'])

    long_df['ticker'] = ticker

    # forecast_date 기본값
    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()
    long_df['forecast_date'] = pd.to_datetime(forecast_date).date()

    # 컬럼 순서
    long_df = long_df[['date', 'ticker', 'indicator', 'value', 'forecast_date']]

    return long_df


def _build_engine(db_info: Dict) -> Engine:
    """SQLAlchemy 엔진 생성"""
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url, pool_recycle=3600)


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
    Long form 업로드 (중복 저장 절대 방지)

    동작:
    ----
    1. 같은 ticker + forecast_date의 기존 데이터 전체 삭제
    2. 새로운 데이터 INSERT (중복 불가능)
    """
    # Long form 변환
    parts: List[pd.DataFrame] = []
    parts.append(_melt_long(valuation_forecast_result, ticker, forecast_date))
    parts.append(_melt_long(fc_table, ticker, forecast_date))
    parts.append(_melt_long(rev_final, ticker, forecast_date))
    parts.append(_melt_long(psr_df, ticker, forecast_date))

    long_all = pd.concat(parts, ignore_index=True)

    # 중복 제거 (같은 지표가 여러 DF에 있을 경우 마지막 우선)
    long_all = long_all.drop_duplicates(
        subset=['date', 'ticker', 'indicator', 'forecast_date'],
        keep='last'
    )

    if long_all.empty:
        print("[INFO] 업로드할 데이터가 없습니다.")
        return

    # forecast_date 확정
    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()
    forecast_date_val = pd.to_datetime(forecast_date).date()

    # DB 연결
    engine = _build_engine(db_info)

    # 테이블 생성 (없으면)
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
        `date` DATE NOT NULL,
        `ticker` VARCHAR(16) NOT NULL,
        `indicator` VARCHAR(64) NOT NULL,
        `value` DOUBLE NULL,
        `forecast_date` DATE NOT NULL,
        PRIMARY KEY (`ticker`, `date`, `indicator`, `forecast_date`),
        INDEX `idx_date` (`date`),
        INDEX `idx_ticker` (`ticker`),
        INDEX `idx_forecast_date` (`forecast_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.execute(text(create_sql))

    # 기존 데이터 삭제 (같은 ticker + forecast_date)
    delete_sql = f"""
    DELETE FROM `{table_name}`
    WHERE `ticker` = :ticker AND `forecast_date` = :forecast_date
    """

    with engine.begin() as conn:
        result = conn.execute(
            text(delete_sql),
            {"ticker": ticker, "forecast_date": forecast_date_val}
        )
        deleted_count = result.rowcount
        if deleted_count > 0:
            print(f"[INFO] 기존 데이터 삭제: {deleted_count} rows (ticker={ticker}, forecast_date={forecast_date_val})")

    # 새로운 데이터 INSERT
    rows = long_all.to_dict(orient='records')

    insert_sql = f"""
    INSERT INTO `{table_name}` (`date`, `ticker`, `indicator`, `value`, `forecast_date`)
    VALUES (:date, :ticker, :indicator, :value, :forecast_date)
    """

    with engine.begin() as conn:
        for i in range(0, len(rows), chunksize):
            conn.execute(text(insert_sql), rows[i:i + chunksize])

    print(f"[OK] {len(rows)} rows inserted into {table_name} for ticker={ticker} (forecast_date={forecast_date_val}).")