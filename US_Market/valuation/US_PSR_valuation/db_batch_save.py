# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from utils import log


def ddl_psr_valuation_result(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
    """PSR Valuation Result 테이블 DDL 생성"""
    tail = f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={collation};" if with_collate else \
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
    return f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
      `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      `date` DATE NOT NULL,
      `ticker` VARCHAR(16) NOT NULL,
      `indicator` VARCHAR(128) NOT NULL,
      `value` DECIMAL(20,8) NULL,
      `forecast_date` DATE NOT NULL,
      `created_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      `updated_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (`id`),
      KEY `idx_ticker_date` (`ticker`, `date`),
      KEY `idx_forecast_date` (`forecast_date`),
      UNIQUE KEY `uq_ticker_date_indicator_forecast`
        (`ticker`, `date`, `indicator`, `forecast_date`)
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


def ensure_psr_valuation_table(db_info: dict, table_name: str = "us_psr_valuation_result"):
    """PSR Valuation 테이블 존재 확인 및 생성"""
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with engine.begin() as conn:
        try:
            conn.execute(text(ddl_psr_valuation_result(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
            log("DB-TABLE", f"Table {table_name} ensured with utf8mb4_0900_ai_ci")
            return
        except Exception as e:
            if not is_unknown_collation(e):
                raise
        try:
            conn.execute(text(ddl_psr_valuation_result(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
            log("DB-TABLE", f"Table {table_name} ensured with utf8mb4_unicode_ci")
            return
        except Exception as e2:
            if not is_unknown_collation(e2):
                raise
        conn.execute(text(ddl_psr_valuation_result(table_name, with_collate=False)))
        log("DB-TABLE", f"Table {table_name} ensured without collation")


def convert_to_long_format(batch_results, forecast_date=None):
    """
    batch_results를 Long format으로 변환

    Args:
        batch_results: list of DataFrames
        forecast_date: 예측 날짜 (기본값: 오늘)

    Returns:
        DataFrame with columns: date, ticker, indicator, value, forecast_date
    """
    if forecast_date is None:
        forecast_date = pd.Timestamp.today().strftime('%Y-%m-%d')

    if not batch_results:
        return pd.DataFrame(columns=['date', 'ticker', 'indicator', 'value', 'forecast_date'])

    # 모든 batch_results를 하나의 DataFrame으로 병합
    combined_df = pd.concat(batch_results, axis=0, ignore_index=True)

    # date_month_end를 date로 변경
    if 'date_month_end' in combined_df.columns:
        combined_df = combined_df.rename(columns={'date_month_end': 'date'})

    # date와 ticker를 제외한 나머지 컬럼들
    id_vars = ['date', 'ticker']
    value_vars = [col for col in combined_df.columns if col not in id_vars]

    # Wide format을 Long format으로 변환
    long_df = pd.melt(
        combined_df,
        id_vars=id_vars,
        value_vars=value_vars,
        var_name='indicator',
        value_name='value'
    )

    # forecast_date 추가
    long_df['forecast_date'] = pd.to_datetime(forecast_date).date()

    # date를 datetime으로 변환
    long_df['date'] = pd.to_datetime(long_df['date']).dt.date

    # NaN 값 제거
    long_df = long_df.dropna(subset=['value'])

    # 정렬
    long_df = long_df.sort_values(['ticker', 'date', 'indicator']).reset_index(drop=True)

    log("LONG-FORMAT", f"Converted {len(combined_df)} wide rows to {len(long_df)} long rows")

    return long_df[['date', 'ticker', 'indicator', 'value', 'forecast_date']]


def upsert_psr_valuation_to_db(long_df: pd.DataFrame,
                               db_info: dict,
                               table_name: str = "us_psr_valuation_result") -> int:
    """
    PSR Valuation 데이터를 DB에 업서트
    - (ticker, date, indicator, forecast_date) 기준으로 덮어쓰기
    """
    if long_df is None or long_df.empty:
        log("DB-UPSERT", "No data to upsert")
        return 0

    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )

    affected = 0
    with engine.begin() as conn:
        for _, row in long_df.iterrows():
            # 기존 데이터 삭제 (같은 ticker, date, indicator, forecast_date)
            delete_sql = text("""
                DELETE FROM {table_name}
                WHERE ticker = :ticker 
                  AND date = :date 
                  AND indicator = :indicator 
                  AND forecast_date = :forecast_date
            """.format(table_name=table_name))

            conn.execute(delete_sql, {
                'ticker': str(row['ticker']),
                'date': row['date'],
                'indicator': str(row['indicator']),
                'forecast_date': row['forecast_date']
            })

            # 새 데이터 삽입
            insert_sql = text("""
                INSERT INTO {table_name} 
                (date, ticker, indicator, value, forecast_date)
                VALUES 
                (:date, :ticker, :indicator, :value, :forecast_date)
            """.format(table_name=table_name))

            conn.execute(insert_sql, {
                'date': row['date'],
                'ticker': str(row['ticker']),
                'indicator': str(row['indicator']),
                'value': float(row['value']) if pd.notna(row['value']) else None,
                'forecast_date': row['forecast_date']
            })
            affected += 1

    log("DB-UPSERT", f"Upserted {affected} rows to {table_name}")
    return affected


def save_batch_results_to_db(batch_results, db_info, forecast_date=None, table_name="us_psr_valuation_result"):
    """
    batch_results를 Long format으로 변환하고 DB에 저장하는 통합 함수

    Args:
        batch_results: list of DataFrames
        db_info: DB 연결 정보
        forecast_date: 예측 날짜 (기본값: 오늘)
        table_name: 테이블 이름 (기본값: us_psr_valuation_result)

    Returns:
        int: 업서트된 행의 수
    """
    try:
        # 1. 테이블 생성 확인
        ensure_psr_valuation_table(db_info, table_name)

        # 2. Long format으로 변환
        long_df = convert_to_long_format(batch_results, forecast_date)

        if long_df.empty:
            log("SAVE-BATCH", "No data to save")
            return 0

        # 3. DB에 업서트
        affected = upsert_psr_valuation_to_db(long_df, db_info, table_name)

        log("SAVE-BATCH", f"Successfully saved {affected} rows to {table_name}")
        return affected

    except Exception as e:
        log("SAVE-BATCH-ERR", f"Failed to save batch_results: {e}")
        import traceback
        log("SAVE-BATCH-TB", traceback.format_exc())
        return 0