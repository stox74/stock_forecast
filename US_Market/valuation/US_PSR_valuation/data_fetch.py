# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
import requests
import time
import datetime as dt
import traceback
from sqlalchemy import create_engine, text
from pandas.tseries.offsets import MonthEnd
from utils import log, to_month_end_safe
from config import DEBUG

def fetch_revenue_data(ticker, api_key):
    """FMP API에서 매출 데이터 가져오기"""
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
    params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}
    try:
        if DEBUG: log("FMP-REV-REQ", f"{ticker} url={url} limit=200 period=quarter")
        response = requests.get(url, params=params, timeout=30)
        if DEBUG: log("FMP-REV-RESP", f"{ticker} status={response.status_code}")
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        data = response.json()
        if DEBUG: log("FMP-REV-DATA",
                      f"{ticker} type={type(data).__name__} size={len(data) if isinstance(data, list) else 'dict'}")
        if isinstance(data, dict) and 'Error Message' in data:
            return None, f"API 오류: {data['Error Message']}"
        if not data:
            return None, "데이터 없음"
        return data, None
    except Exception as e:
        if DEBUG: log("FMP-REV-EXC", f"{ticker} exc={e}")
        return None, f"오류: {str(e)}"

def fetch_market_data_yearly(ticker, api_key, start_year=2010):
    """FMP API에서 시가총액 데이터를 연도별로 가져오기"""
    all_data = []
    current_year = dt.datetime.now().year
    for year in range(start_year, current_year + 1):
        url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
        params = {'from': f"{year}-01-01", 'to': f"{year}-12-31", 'apikey': api_key}
        try:
            if DEBUG and year in (start_year, start_year + 1, current_year):
                log("FMP-MCAP-REQ", f"{ticker} year={year}")
            response = requests.get(url, params=params, timeout=30)
            if DEBUG and year in (start_year, start_year + 1, current_year):
                log("FMP-MCAP-RESP", f"{ticker} year={year} status={response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    all_data.extend(data)
            time.sleep(0.3)
        except Exception as e:
            if DEBUG: log("FMP-MCAP-EXC", f"{ticker} year={year} exc={e}")
            continue
    if DEBUG: log("FMP-MCAP-DONE", f"{ticker} total_records={len(all_data)}")
    return all_data if all_data else None, None

def fetch_db_revenue_data(ticker, db_info, end_date=None):
    """데이터베이스에서 매출 데이터 가져오기"""
    if end_date is None:
        end_date = (pd.Timestamp.today().normalize() - MonthEnd(1)).strftime('%Y-%m-%d')
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )
        clean_ticker = ticker.strip().upper()
        sql = text("""
                   SELECT date, ticker, saleq
                   FROM US_fundq
                   WHERE UPPER(ticker) = :ticker
                     AND saleq IS NOT NULL
                     AND date <= :end_date
                   ORDER BY date ASC
                   """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"ticker": clean_ticker, "end_date": end_date})
        if df.empty:
            log("DB-REV-EMPTY", f"{ticker} 0 rows (<= {end_date})")
            return df
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])
        df['revenue_billions'] = df['saleq'] / 1000.0
        df['date_month_end'] = to_month_end_safe(df['date'])
        log("DB-REV-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
        return df[['ticker', 'date', 'date_month_end', 'revenue_billions']]
    except Exception as e:
        log("DB-REV-EXC", f"{ticker} {type(e).__name__}: {e}")
        return pd.DataFrame()

def fetch_db_market_data(ticker, db_info, end_date=None):
    """데이터베이스에서 시가총액 데이터 가져오기"""
    if end_date is None:
        end_date = (pd.Timestamp.today().normalize() + MonthEnd(1)).strftime('%Y-%m-%d')
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )
        clean_ticker = ticker.strip().upper()
        sql = text("""
                   SELECT date, ticker, me
                   FROM US_fundm
                   WHERE UPPER(ticker) = :ticker
                     AND me IS NOT NULL
                     AND date <= :end_date
                   ORDER BY date ASC
                   """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"ticker": clean_ticker, "end_date": end_date})
        if df.empty:
            log("DB-MCAP-EMPTY", f"{ticker} 0 rows (<= {end_date})")
            return df
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])
        df['market_cap_billions'] = df['me'] / 1000.0
        df['date_month_end'] = to_month_end_safe(df['date'])
        log("DB-MCAP-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
        return df[['ticker', 'date', 'date_month_end', 'market_cap_billions']]
    except Exception as e:
        log("DB-MCAP-EXC", f"{ticker} {type(e).__name__}: {e}")
        return pd.DataFrame()

def safe_get_db_market_df(ticker, db_info):
    """안전하게 DB 시가총액 데이터를 가져오는 래퍼 함수"""
    try:
        if DEBUG: log("DB-MCAP-REQ", f"{ticker}")
        df = fetch_db_market_data(ticker, db_info)
        n = 0 if df is None else len(df)
        if DEBUG: log("DB-MCAP-RESP", f"{ticker} rows={n}")
        if df is None or n == 0:
            return pd.DataFrame()
        return df.copy()
    except Exception as e:
        if DEBUG: log("DB-MCAP-EXC", f"{ticker} exc={e}")
        return pd.DataFrame()

def process_daily_to_monthly_market_data(daily_data, ticker):
    """일별 시가총액 데이터를 월별로 변환"""
    if not daily_data:
        return pd.DataFrame()
    df = pd.DataFrame(daily_data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    df['year_month'] = df['date'].dt.to_period('M')
    monthly_data = []
    for year_month in df['year_month'].unique():
        month_data = df[df['year_month'] == year_month]
        last_day_data = month_data.loc[month_data['date'].idxmax()]
        monthly_data.append({
            'ticker': ticker,
            'date': last_day_data['date'],
            'market_cap': last_day_data['marketCap'],
            'market_cap_billions': round(last_day_data['marketCap'] / 1_000_000_000, 2),
        })
    return pd.DataFrame(monthly_data)
