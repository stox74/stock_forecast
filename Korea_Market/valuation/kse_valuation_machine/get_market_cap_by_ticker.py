# -*- coding: utf-8 -*-
"""
market_cap_data.py

Reusable function to fetch market capitalization time series by ticker
from MySQL/MariaDB and return a Date-indexed pandas DataFrame.

Requirements:
    pip install pandas SQLAlchemy pymysql

Usage:
    from market_cap_data import get_market_cap_by_ticker

    db_info = {
        "user": "USER",
        "password": "PWD",
        "host": "localhost",
        "port": 3306,
        "database": "investar",
    }
    df = get_market_cap_by_ticker(db_info, "005930")
    print(df.head())
"""

import sys
from pathlib import Path
import warnings
from DATA.stock_invest_function import *

warnings.filterwarnings('ignore')

import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta

__all__ = ["get_market_cap_by_ticker"]

# db_info = {
#     'host': get_db_host(),
#     'port': 3307,
#     'user': 'stox7412',
#     'password': 'Apt106503!~',
#     'database': 'investar'
# }


# Optional: project path bootstrap (kept similar to your snippet)
try:
    current_path = Path(__file__).resolve()
except NameError:
    current_path = Path().resolve()

for parent in current_path.parents:
    if (parent / "stock_forecast" / "DATA").is_dir():
        stock_forecast_path = parent / "stock_forecast"
        break
else:
    stock_forecast_path = None  # Do not raise here; function doesn't require this path

if stock_forecast_path and (str(stock_forecast_path) not in sys.path):
    sys.path.insert(0, str(stock_forecast_path))


def get_market_cap_by_ticker(db_info: dict, ticker: str) -> pd.DataFrame:
    """
    Fetch market cap time series for a given ticker.

    Parameters
    ----------
    db_info : dict
        Keys: user, password, host, port, database
    ticker : str
        Security code (e.g., '005930').

    Returns
    -------
    pd.DataFrame
        Date-indexed DataFrame with a single column 'MarketCap'.
    """
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )

        query = text("""
            SELECT date, value
            FROM ks_listed_company_daily_marketcap
            WHERE ticker = :ticker AND indicator = '시가총액'
            ORDER BY date
        """)

        df = pd.read_sql(query, con=engine, params={"ticker": ticker})

        # Ensure datetime index named 'Date' and a clear value column
        if not df.empty:
            df["Date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.drop(columns=["date"]).rename(columns={"value": "MarketCap"})
            df = df.set_index("Date").sort_index()
            df.index.name = "Date"

        return df

    except Exception as e:
        print(f"시가총액 데이터 조회 실패: {e}")
        return pd.DataFrame()