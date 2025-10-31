# -*- coding: utf-8 -*-
"""
save_long_forecast_to_db_fixed.py

long_df (index=date, columns=['hs_code','indicator','value','forecast_date'])를
MySQL/MariaDB DB에 저장하는 모듈.

조건:
1. forecast_date가 같으면 덮어쓰기(업데이트)
2. 다르면 새로 저장
3. 테이블 없으면 자동 생성
4. 테이블명: korea_monthly_trade_forecast_v2
"""

from __future__ import annotations
import pandas as pd
from sqlalchemy import create_engine, MetaData, Table, Column, String, Date, Float
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.types import DATE
from datetime import datetime


def _build_engine(db_info: dict):
    """
    db_info 예시:
    db_info = {
        'host': '192.168.0.230',
        'port': 3307,
        'user': 'root',
        'password': '1234',
        'database': 'investar'
    }
    """
    conn_str = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(conn_str, pool_pre_ping=True, future=True)


def _ensure_table(engine, table_name: str):
    """
    korea_monthly_trade_forecast_v2 테이블이 없으면 생성
    """
    ddl = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
        `date` DATE NOT NULL,
        `hs_code` VARCHAR(16) NOT NULL,
        `indicator` VARCHAR(64) NOT NULL,
        `value` DOUBLE NULL,
        `forecast_date` DATE NOT NULL,
        PRIMARY KEY (`date`, `hs_code`, `indicator`, `forecast_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)


def save_long_forecast_to_db(
    long_df: pd.DataFrame,
    db_info: dict,
    table_name: str = "korea_monthly_trade_forecast_v2",
    chunksize: int = 1000,
):
    """
    long_df를 DB에 저장.
    forecast_date가 같으면 덮어쓰기, 다르면 새로 저장.

    Parameters
    ----------
    long_df : pd.DataFrame
        index=date, columns=['hs_code','indicator','value','forecast_date']
    db_info : dict
        DB 접속 정보
    table_name : str
        저장할 테이블 이름 (기본값: korea_monthly_trade_forecast_v2)
    chunksize : int
        대용량 데이터 분할 저장 시 batch 크기
    """

    # --- DB 연결 ---
    engine = _build_engine(db_info)
    _ensure_table(engine, table_name)

    # --- DataFrame 확인 ---
    df = long_df.copy()
    if "date" not in df.columns and df.index.name == "date":
        df = df.reset_index()

    required_cols = ["date", "hs_code", "indicator", "value", "forecast_date"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise KeyError(f"long_df에 다음 컬럼이 필요합니다: {missing}")

    # 날짜 형식 정리
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["forecast_date"] = pd.to_datetime(df["forecast_date"]).dt.date

    # --- 테이블 객체 준비 ---
    metadata = MetaData()
    table = Table(
        table_name,
        metadata,
        Column("date", Date, primary_key=True),
        Column("hs_code", String(16), primary_key=True),
        Column("indicator", String(64), primary_key=True),
        Column("value", Float),
        Column("forecast_date", Date, primary_key=True),
        extend_existing=True,
    )

    # --- INSERT + ON DUPLICATE KEY UPDATE ---
    records = df.to_dict(orient="records")
    with engine.begin() as conn:
        for i in range(0, len(records), chunksize):
            batch = records[i:i+chunksize]
            if not batch:
                continue
            ins_stmt = mysql_insert(table).values(batch)
            upsert_stmt = ins_stmt.on_duplicate_key_update(
                value=ins_stmt.inserted.value
            )
            conn.execute(upsert_stmt)

    print(f"[INFO] {len(df)} rows saved into '{table_name}' (Upsert by forecast_date).")


# 단독 테스트용
# if __name__ == "__main__":
#     import numpy as np
#
#     idx = pd.date_range("2025-11-30", periods=6, freq="M")
#     long_df = pd.DataFrame({
#         "date": idx,
#         "hs_code": "854232",
#         "indicator": ["sarima_expDlr", "ets_expDlr", "prophet_expDlr",
#                       "lstm_expDlr", "theta_expDlr", "앙상블_expDlr"],
#         "value": np.linspace(8e9, 9e9, 6),
#         "forecast_date": pd.Timestamp(datetime.today()).date(),
#     })
#
#     db_info = {
#         'host': '192.168.0.230',
#         'port': 3307,
#         'user': 'root',
#         'password': '1234',
#         'database': 'investar'
#     }
#
#     save_long_forecast_to_db(long_df, db_info)
