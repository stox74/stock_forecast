# -*- coding: utf-8 -*-
"""
revenue_extractor.py

Extract quarterly revenue time series from a financial statements table.
All outputs are returned as a Date-indexed DataFrame.

Main entry:
    extract_quarterly_revenue(db_info, table_name, target_indicator, ticker, ...)

Example:
    db_info = {"user":"USER","password":"PWD","host":"localhost","port":3306,"database":"investar"}
    df = extract_quarterly_revenue(db_info, "korea_fs_data", target_indicator="매출액(천원)", ticker="005930")
"""

from typing import Optional, Iterable, Dict
import pandas as pd
from sqlalchemy import create_engine, text

__all__ = ["extract_quarterly_fs_data", "fetch_table_data", "clean_numeric_data"]


def _get_engine(db_info: Dict):
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url, pool_recycle=3600, pool_pre_ping=True)


def fetch_table_data(db_info: Dict, table_name: str) -> pd.DataFrame:
    """
    Generic fetch helper that loads full table (use WHERE outside if you want).
    """
    eng = _get_engine(db_info)
    df = pd.read_sql(text(f"SELECT * FROM {table_name}"), eng)
    # Standardize Date column name if present
    if "Date" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"Date": "date"})
    return df


def clean_numeric_data(series: pd.Series, method: str = "fill_median") -> pd.Series:
    """
    Coerce to numeric and optionally handle NaNs.
    method: 'drop' | 'fill_median' | 'fill_zero'
    """
    s = pd.to_numeric(series, errors="coerce")
    if method == "drop":
        return s.dropna()
    elif method == "fill_zero":
        return s.fillna(0)
    elif method == "fill_median":
        med = s.median(skipna=True)
        return s.fillna(med)
    else:
        return s  # no-op


def extract_quarterly_fs_data(
    db_info: Dict,
    table_name: str,
    target_indicator: str,
    ticker: str,
    symbol_col: str = "symbol",
    indicator_col: str = "indicator",
    value_col: str = "value",
    date_col: str = "date",
) -> pd.DataFrame:
    """
    Build a quarterly revenue time series for a given ticker and indicator.

    Returns
    -------
    pd.DataFrame (Date-indexed)
        Columns: ['revenue', 'year', 'quarter', 'year_quarter', 'symbol']
    """
    # 1) 테이블 로드
    fs_df = fetch_table_data(db_info, table_name)
    if "Date" in fs_df.columns and date_col not in fs_df.columns:
        fs_df = fs_df.rename(columns={"Date": date_col})

    # 2) 필터링
    df = fs_df.copy()
    if indicator_col not in df.columns:
        raise KeyError(f"'{indicator_col}' column not found in {table_name}")
    if symbol_col not in df.columns:
        raise KeyError(f"'{symbol_col}' column not found in {table_name}")
    if value_col not in df.columns:
        raise KeyError(f"'{value_col}' column not found in {table_name}")
    if date_col not in df.columns:
        raise KeyError(f"'{date_col}' column not found in {table_name}")

    revenue_raw = df[df[indicator_col] == target_indicator].copy()
    revenue_company = revenue_raw[revenue_raw[symbol_col] == ticker].copy()

    if len(revenue_company) == 0:
        # return empty Date-indexed DataFrame with expected columns
        empty = pd.DataFrame(columns=["revenue", "year", "quarter", "year_quarter", symbol_col])
        empty.index.name = "Date"
        return empty

    # 3) 타입 정리
    revenue_company[date_col] = pd.to_datetime(revenue_company[date_col], errors="coerce")
    revenue_company[value_col] = pd.to_numeric(revenue_company[value_col], errors="coerce")

    revenue_company = revenue_company.dropna(subset=[value_col]).sort_values(date_col)

    # 4) 연/분기 파생
    revenue_company["year"] = revenue_company[date_col].dt.year
    revenue_company["quarter"] = revenue_company[date_col].dt.quarter
    revenue_company["year_quarter"] = revenue_company["year"].astype(str) + "Q" + revenue_company["quarter"].astype(str)

    # 5) 분기별 마지막 값 사용 (필요 시 'first'/'mean' 등 바꿔도 됨)
    grouped = (
        revenue_company.groupby(["year", "quarter"], as_index=False)
        .agg({date_col: "last", value_col: "last", "year_quarter": "last", symbol_col: "last"})
        .sort_values(["year", "quarter"])
        .reset_index(drop=True)
    )

    # 6) 숫자 정리 + 컬럼 구성
    grouped["revenue"] = clean_numeric_data(grouped[value_col], method="fill_median")
    result = grouped.rename(columns={date_col: "Date"})
    result = result[["Date", "revenue", "year", "quarter", "year_quarter", symbol_col]].copy()

    # 7) Date 인덱스화
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce")
    result = result.set_index("Date").sort_index()
    result.index.name = "date"

    return result


# if __name__ == "__main__":
#     # Minimal smoke test (adjust credentials before running this file directly).
#     import os
#     EXAMPLE_DB = {
#         "user": os.getenv("DB_USER", "USER"),
#         "password": os.getenv("DB_PASSWORD", "PWD"),
#         "host": os.getenv("DB_HOST", "localhost"),
#         "port": int(os.getenv("DB_PORT", "3306")),
#         "database": os.getenv("DB_NAME", "investar"),
#     }
#     try:
#         df_demo = extract_quarterly_revenue(
#             db_info=EXAMPLE_DB,
#             table_name="korea_fs_data",
#             target_indicator="매출액(천원)",
#             ticker="005930",
#         )
#         print(df_demo.head())
#     except Exception as e:
#         print("Smoke test failed:", e)
