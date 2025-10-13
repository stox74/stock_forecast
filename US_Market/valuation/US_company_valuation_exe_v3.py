# -*- coding: utf-8 -*-

import sys, os
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Optional
from sqlalchemy import create_engine, text
import importlib
import DATA.us_sarima_forecast as sarima

importlib.reload(sarima)
import DATA.us_lstm_forecast_v2 as lstm_v2

importlib.reload(lstm_v2)
import DATA.us_prophet_forecast_v3 as prophet_v3

importlib.reload(prophet_v3)
import DATA.us_est_forecast_v2 as esmod

importlib.reload(esmod)

import gc
import requests
import warnings
from sqlalchemy import create_engine
from dateutil.relativedelta import relativedelta
from pandas.tseries.offsets import MonthEnd

import calendar
import time
from DATA.stock_invest_function import *

import traceback
import datetime as dt

DEBUG = True


def log(stage: str, msg: str):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {stage}: {msg}")


warnings.filterwarnings('ignore')


def _default_month_end_str(offset_months: int = 0) -> str:
    return (pd.Timestamp.today().normalize() + MonthEnd(offset_months)).strftime('%Y-%m-%d')


def audit_db_coverage(db_info, tickers):
    eng = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    miss_q, miss_m = [], []
    with eng.connect() as conn:
        for t in tickers:
            clean_ticker = t.strip().upper()
            c_q = pd.read_sql(text(
                "SELECT COUNT(*) c FROM US_fundq WHERE UPPER(ticker)=:t AND saleq IS NOT NULL"
            ), conn, params={"t": clean_ticker})['c'].iloc[0]
            c_m = pd.read_sql(text(
                "SELECT COUNT(*) c FROM US_fundm WHERE UPPER(ticker)=:t AND me IS NOT NULL"
            ), conn, params={"t": clean_ticker})['c'].iloc[0]
            if c_q == 0: miss_q.append(t)
            if c_m == 0: miss_m.append(t)
    log("AUDIT", f"US_fundq missing: {len(miss_q)} tickers");
    print(miss_q[:20])
    log("AUDIT", f"US_fundm missing: {len(miss_m)} tickers");
    print(miss_m[:20])
    return miss_q, miss_m


def add_repo_path():
    here = Path.cwd()
    for p in [here, *here.parents]:
        if (p / "DATA").exists():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return str(p)
    fallback = r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast"
    if os.path.isdir(fallback) and fallback not in sys.path:
        sys.path.insert(0, fallback)
    return fallback


project_path = add_repo_path()


def smoke_test_db_tables(db_info, ticker: str):
    eng = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with eng.begin() as conn:
        one = conn.exec_driver_sql("SELECT 1").scalar()
        log("SMOKE", f"SELECT 1 -> {one}")

        t1 = pd.read_sql("SHOW TABLES LIKE 'US_fundq';", conn)
        t2 = pd.read_sql("SHOW TABLES LIKE 'US_fundm';", conn)
        log("SMOKE", f"US_fundq exists? {not t1.empty} / US_fundm exists? {not t2.empty}")

        q_cnt = pd.read_sql(
            f"SELECT COUNT(*) AS c FROM US_fundq WHERE ticker='{ticker}' AND saleq IS NOT NULL;", conn
        )['c'].iloc[0]
        q_cnt_trim = pd.read_sql(
            f"SELECT COUNT(*) AS c FROM US_fundq WHERE TRIM(ticker)='{ticker}' AND saleq IS NOT NULL;", conn
        )['c'].iloc[0]
        log("SMOKE", f"US_fundq {ticker} count = {q_cnt} (raw) / {q_cnt_trim} (TRIM)")

        head = pd.read_sql(
            text("""SELECT date, ticker, saleq
                    FROM US_fundq
                    WHERE TRIM (ticker)=:ticker AND saleq IS NOT NULL
                    ORDER BY date DESC LIMIT 5;"""),
            conn, params={"ticker": ticker}
        )
        log("SMOKE", f"latest US_fundq rows for {ticker}:\n{head}")

    eng.dispose()


def diag_revenue_and_mcap_gaps(db_info, ticker: str):
    eng = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with eng.begin() as conn:
        print("=== SCHEMA / CONNECTIVITY ===")
        one = conn.exec_driver_sql("SELECT 1").scalar()
        print("SELECT 1 ->", one)
        current_db = conn.exec_driver_sql("SELECT DATABASE();").scalar()
        print("DATABASE() ->", current_db)

        print("\n=== TABLE EXISTENCE ===")
        t_q = pd.read_sql("SHOW TABLES LIKE 'US_fundq';", conn)
        t_m = pd.read_sql("SHOW TABLES LIKE 'US_fundm';", conn)
        print("US_fundq exists? ", not t_q.empty)
        print("US_fundm exists? ", not t_m.empty)

        print(f"\n=== US_fundq presence for {ticker} ===")
        q_raw = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundq WHERE ticker=:t AND saleq IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        q_trim = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundq WHERE TRIM(ticker)=:t AND saleq IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        q_upper = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundq WHERE UPPER(TRIM(ticker))=UPPER(:t) AND saleq IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        q_like = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundq WHERE (ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3) AND saleq IS NOT NULL"
        ), conn, params={"p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})['c'].iloc[0]
        print(f"raw={q_raw}, trim={q_trim}, upper={q_upper}, like(aliases)={q_like}")

        if max(q_raw, q_trim, q_upper, q_like) > 0:
            print("\n--- sample matched tickers in US_fundq ---")
            sample = pd.read_sql(text(
                """SELECT DISTINCT ticker
                   FROM US_fundq
                   WHERE (ticker = :t OR TRIM(ticker) = :t OR UPPER(TRIM(ticker)) = UPPER(:t)
                       OR ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3) LIMIT 20;"""
            ), conn, params={"t": ticker, "p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})
            print(sample)

            print("\n--- latest 5 rows (by date) ---")
            latest = pd.read_sql(text(
                """SELECT date, ticker, saleq
                   FROM US_fundq
                   WHERE (ticker=:t
                      OR TRIM (ticker)=:t
                      OR UPPER (TRIM (ticker))= UPPER (:t)
                      OR ticker LIKE :p1
                      OR ticker LIKE :p2
                      OR ticker LIKE :p3)
                     AND saleq IS NOT NULL
                   ORDER BY date DESC
                       LIMIT 5;"""
            ), conn, params={"t": ticker, "p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})
            print(latest)
        else:
            print(f">>> US_fundq에 {ticker} 관련 매출 데이터가 전혀 없습니다.")

        print(f"\n=== US_fundm presence for {ticker} ===")
        m_raw = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundm WHERE ticker=:t AND me IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        m_trim = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundm WHERE TRIM(ticker)=:t AND me IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        m_upper = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundm WHERE UPPER(TRIM(ticker))=UPPER(:t) AND me IS NOT NULL"
        ), conn, params={"t": ticker})['c'].iloc[0]
        m_like = pd.read_sql(text(
            "SELECT COUNT(*) c FROM US_fundm WHERE (ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3) AND me IS NOT NULL"
        ), conn, params={"p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})['c'].iloc[0]
        print(f"raw={m_raw}, trim={m_trim}, upper={m_upper}, like(aliases)={m_like}")

        print("\n=== US_fundm coverage sample (top 10 tickers by count) ===")
        coverage = pd.read_sql(
            "SELECT ticker, COUNT(*) AS c, MIN(date) AS from_dt, MAX(date) AS to_dt "
            "FROM US_fundm WHERE me IS NOT NULL GROUP BY ticker ORDER BY c DESC LIMIT 10;",
            conn
        )
        print(coverage)

    eng.dispose()


def to_month_end_safe(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, errors="coerce")
    prev_mask = s.dt.day.between(1, 5, inclusive="both")
    out = s.copy()
    out.loc[prev_mask] = (s.loc[prev_mask] + MonthEnd(-1))
    out.loc[~prev_mask] = (s.loc[~prev_mask] + MonthEnd(0))
    return out


def process_daily_to_monthly_market_data(daily_data, ticker):
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


def fetch_revenue_data(ticker, api_key):
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
    params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}
    try:
        if DEBUG: log("FMP-REV-REQ", f"{ticker} url={url} limit=200 period=quarter apikey=***")
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
        if DEBUG: log("FMP-REV-EXC", f"{ticker} exc={e} tb={traceback.format_exc().splitlines()[-1]}")
        return None, f"오류: {str(e)}"


def fetch_market_data_yearly(ticker, api_key, start_year=2010):
    all_data = []
    current_year = dt.datetime.now().year
    for year in range(start_year, current_year + 1):
        url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
        params = {'from': f"{year}-01-01", 'to': f"{year}-12-31", 'apikey': api_key}
        try:
            if DEBUG and year in (start_year, start_year + 1, current_year):
                log("FMP-MCAP-REQ", f"{ticker} year={year} url={url} apikey=***")
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
    if end_date is None:
        end_date = (pd.Timestamp.today().normalize() - MonthEnd(1)).strftime('%Y-%m-%d')
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )
        # TRIM 제거하고 ticker를 미리 정리
        clean_ticker = ticker.strip().upper()
        sql = text("""
                   SELECT date, ticker, saleq
                   FROM US_fundq
                   WHERE UPPER (ticker) = :ticker
                     AND saleq IS NOT NULL
                     AND date <= :end_date
                   ORDER BY date ASC
                   """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"ticker": clean_ticker, "end_date": end_date})
        if df.empty:
            log("DB-REV-EMPTY", f"{ticker} 0 rows (<= {end_date})");
            return df
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])
        df['revenue_billions'] = df['saleq'] / 1000.0
        df['date_month_end'] = to_month_end_safe(df['date'])
        log("DB-REV-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
        return df[['ticker', 'date', 'date_month_end', 'revenue_billions']]
    except Exception as e:
        log("DB-REV-EXC", f"{ticker} {type(e).__name__}: {e}");
        return pd.DataFrame()


def fetch_db_market_data(ticker, db_info, end_date=None):
    if end_date is None:
        end_date = _default_month_end_str(1)
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )
        # TRIM 제거하고 ticker를 미리 정리
        clean_ticker = ticker.strip().upper()
        sql = text("""
                   SELECT date, ticker, me
                   FROM US_fundm
                   WHERE UPPER (ticker) = :ticker
                     AND me IS NOT NULL
                     AND date <= :end_date
                   ORDER BY date ASC
                   """)
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"ticker": clean_ticker, "end_date": end_date})
        if df.empty:
            log("DB-MCAP-EMPTY", f"{ticker} 0 rows (<= {end_date})");
            return df
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])
        df['market_cap_billions'] = df['me'] / 1000.0
        df['date_month_end'] = to_month_end_safe(df['date'])
        log("DB-MCAP-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
        return df[['ticker', 'date', 'date_month_end', 'market_cap_billions']]
    except Exception as e:
        log("DB-MCAP-EXC", f"{ticker} {type(e).__name__}: {e}");
        return pd.DataFrame()


def calculate_enhanced_ttm_and_psr(merged_data):
    """Calculate enhanced TTM and PSR"""
    df = merged_data.copy()

    df['date_month_end'] = pd.to_datetime(df['date_month_end'], errors='coerce')
    df = df.sort_values(['date_month_end']).reset_index(drop=True)
    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0,
                                                                                                                    drop=True)
    df['revenue_ttm_billions'] = df['revenue_ttm']

    df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)

    df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']

    df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)

    return df


def prepare_revenue_ttm(
        df: pd.DataFrame,
        revenue_key: str = "revenue_billions",
        min_periods: int = 1,
) -> pd.DataFrame:
    d = df.copy()

    if 'date_month_end' not in d.columns:
        d = d.reset_index().rename(columns={'index': 'date_month_end'})
    d['date_month_end'] = pd.to_datetime(d['date_month_end'])

    if 'ticker' not in d.columns:
        raise ValueError("ticker 칼럼이 필요합니다.")

    rev_cols = [c for c in d.columns if revenue_key in c]
    if not rev_cols:
        raise ValueError(f"'{revenue_key}' 가 포함된 칼럼을 찾지 못했습니다.")

    uniq_tickers = d['ticker'].dropna().unique()
    if len(uniq_tickers) == 1:
        d['ticker'] = d['ticker'].ffill().bfill()
    else:
        d = d[~d['ticker'].isna()].copy()

    d = d.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    row_mean = d[rev_cols].mean(axis=1, skipna=True)
    for c in rev_cols:
        d[c] = d[c].fillna(row_mean)

    for c in rev_cols:
        ttm_col = f"{c}_ttm"
        d[ttm_col] = (
            d.groupby('ticker', group_keys=False)[c]
            .rolling(window=4, min_periods=min_periods)
            .sum()
            .reset_index(level=0, drop=True)
        )

    d = d.set_index('date_month_end')
    return d


def clean_rev_data(rev_data: pd.DataFrame) -> pd.DataFrame:
    required = ['revenue', 'calendar_year', 'period']
    missing = [c for c in required if c not in rev_data.columns]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {missing}")

    d = rev_data.copy()

    before = len(d)
    d = d[~d['revenue'].isna()].copy()

    d = d.drop_duplicates(subset=['calendar_year', 'period'], keep='first').reset_index(drop=True)
    return d


def _safe_get_db_market_df(ticker, db_info):
    try:
        if DEBUG: log("DB-MCAP-REQ", f"{ticker}")
        df = fetch_db_market_data(ticker, db_info)
        n = 0 if df is None else len(df)
        if DEBUG: log("DB-MCAP-RESP", f"{ticker} rows={n}")
        if df is None or n == 0:
            return pd.DataFrame()
        return df.copy()
    except Exception as e:
        if DEBUG: log("DB-MCAP-EXC", f"{ticker} exc={e} tb={traceback.format_exc().splitlines()[-1]}")
        return pd.DataFrame()


def identify_revenue_columns(columns):
    rev_cols = [c for c in columns if c.startswith("revenue_billions")]
    model_map = {"sarima": [], "lstm": [], "prophet": [], "es": []}
    for c in rev_cols:
        low = c.lower()
        if "sarima" in low:
            model_map["sarima"].append(c)
        elif "lstm" in low:
            model_map["lstm"].append(c)
        elif "prophet" in low:
            model_map["prophet"].append(c)
        elif "es" in low:
            model_map["es"].append(c)
    return rev_cols, model_map


def identify_valuation_columns(columns):
    val_cols = [c for c in columns if c.endswith("_valuation")]
    model_map = {"sarima": None, "lstm": None, "prophet": None, "es": None}
    for c in val_cols:
        low = c.lower()
        if "sarima" in low:
            model_map["sarima"] = c
        elif "lstm" in low:
            model_map["lstm"] = c
        elif "prophet" in low:
            model_map["prophet"] = c
        elif "es" in low:
            model_map["es"] = c
    return val_cols, model_map


def compute_growth(series: pd.Series, start_dt: pd.Timestamp) -> dict:
    s = series.dropna()
    s = s.loc[s.index >= start_dt]
    if s.empty:
        return {"start_value": np.nan, "end_value": np.nan, "growth": np.nan}
    start_value = s.iloc[0]
    end_value = s.iloc[-1]
    if pd.isna(start_value) or start_value == 0:
        growth = np.nan
    else:
        growth = (end_value / start_value) - 1.0
    return {"start_value": start_value, "end_value": end_value, "growth": growth}


def make_growth_summaries(df: pd.DataFrame):
    d = df.copy()

    if "date_month_end" not in d.columns:
        if "index" in d.columns:
            d["date_month_end"] = pd.to_datetime(d["index"], errors="coerce")
        else:
            idx_dt = pd.to_datetime(d.index, errors="coerce")
            if idx_dt.notna().any():
                d = d.reset_index().rename(columns={"index": "date_month_end"})
                d["date_month_end"] = pd.to_datetime(d["date_month_end"], errors="coerce")
            else:
                raise KeyError("날짜가 들어있는 'index' 컬럼(또는 date_month_end)을 찾을 수 없습니다.")
    d["date_month_end"] = (d["date_month_end"] + MonthEnd(0))
    d = d.dropna(subset=["date_month_end"])

    if "ticker" not in d.columns:
        d["ticker"] = d.get("Ticker", d.get("symbol", "UNKNOWN"))

    d = d.sort_values(["ticker", "date_month_end"]).reset_index(drop=True)

    rev_cols, rev_model_map = identify_revenue_columns(d.columns)
    val_cols, val_model_map = identify_valuation_columns(d.columns)

    start_dt = (pd.Timestamp.today() + MonthEnd(0)).normalize()

    revenue_growth_rows = []
    valuation_growth_rows = []

    for ticker, g in d.groupby("ticker"):
        g = g.set_index("date_month_end").copy()

        rev_model_cols = {
            "sarima": rev_model_map["sarima"][0] if rev_model_map["sarima"] else None,
            "lstm": rev_model_map["lstm"][0] if rev_model_map["lstm"] else None,
            "prophet": rev_model_map["prophet"][0] if rev_model_map["prophet"] else None,
            "es": rev_model_map["es"][0] if rev_model_map["es"] else None,
        }
        for model, col in rev_model_cols.items():
            if col is None or col not in g.columns:
                continue
            m = compute_growth(g[col], start_dt)
            revenue_growth_rows.append({
                "ticker": ticker,
                "series": f"revenue_{model}",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        present_rev_cols = [c for c in rev_model_cols.values() if c and c in g.columns]
        if present_rev_cols:
            g["revenue_avg_of_4"] = g[present_rev_cols].mean(axis=1, skipna=True)
            m = compute_growth(g["revenue_avg_of_4"], start_dt)
            revenue_growth_rows.append({
                "ticker": ticker,
                "series": "revenue_avg_of_4",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        val_model_cols = {k: v for k, v in val_model_map.items() if v is not None and v in g.columns}
        for model, col in val_model_cols.items():
            m = compute_growth(g[col], start_dt)
            valuation_growth_rows.append({
                "ticker": ticker,
                "series": f"valuation_{model}",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        present_val_cols = list(val_model_cols.values())
        if present_val_cols:
            vals = g[present_val_cols].copy()
            row_min = vals.min(axis=1)
            top3_avg = (vals.sum(axis=1) - row_min) / np.maximum(vals.count(axis=1) - 1, 1)
            g["valuation_avg_top3"] = top3_avg
            m = compute_growth(g["valuation_avg_top3"], start_dt)
            valuation_growth_rows.append({
                "ticker": ticker,
                "series": "valuation_avg_top3",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

    revenue_growth_summary = pd.DataFrame(revenue_growth_rows)
    valuation_growth_summary = pd.DataFrame(valuation_growth_rows)
    return revenue_growth_summary, valuation_growth_summary


def _to_long(df: pd.DataFrame, category: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ticker", "category", "model", "start_month_end", "start_value", "end_value", "growth", "created_at"
        ])

    out = df.copy()

    if "start_date" in out.columns:
        out = out.rename(columns={"start_date": "start_month_end"})

    out["category"] = category
    prefix = f"{category}_"
    out["model"] = out["series"].str.replace(prefix, "", regex=False)

    out["start_month_end"] = pd.to_datetime(out["start_month_end"], errors="coerce")
    out["created_at"] = pd.Timestamp.utcnow()

    cols = ["ticker", "category", "model", "start_month_end", "start_value", "end_value", "growth", "created_at"]
    return out[cols]


def _ddl(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
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


def _ddl_revenue_forecast(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
    """Revenue forecast 테이블 DDL - 4개 모델 예측값 저장"""
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


def _is_unknown_collation(err: Exception) -> bool:
    if "Unknown collation" in str(err):
        return True
    try:
        code = getattr(getattr(err, "orig", err), "args", [None])[0]
        return code == 1273
    except Exception:
        return False


def ensure_valuation_table(db_info: dict, table_name: str = "us_valuation_result"):
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with engine.begin() as conn:
        try:
            conn.execute(text(_ddl(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
            return
        except Exception as e:
            if not _is_unknown_collation(e):
                raise
        try:
            conn.execute(text(_ddl(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
            return
        except Exception as e2:
            if not _is_unknown_collation(e2):
                raise
        conn.execute(text(_ddl(table_name, with_collate=False)))


def ensure_revenue_forecast_table(db_info: dict, table_name: str = "us_revenue_forecast_result"):
    """Revenue forecast 테이블 생성 보장"""
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    with engine.begin() as conn:
        try:
            conn.execute(text(_ddl_revenue_forecast(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
            return
        except Exception as e:
            if not _is_unknown_collation(e):
                raise
        try:
            conn.execute(text(_ddl_revenue_forecast(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
            return
        except Exception as e2:
            if not _is_unknown_collation(e2):
                raise
        conn.execute(text(_ddl_revenue_forecast(table_name, with_collate=False)))


def upsert_long_to_db_on_ticker_created_at(long_df: pd.DataFrame,
                                           db_info: dict,
                                           table_name: str = "us_valuation_result") -> int:
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
    """
    Revenue forecast 데이터를 DB에 업서트 (4개 모델 예측값)
    - (ticker, date_month_end) 기준으로 덮어쓰기
    - 같은 ticker + 같은 날짜 = UPDATE
    - 같은 ticker + 다른 날짜 = INSERT
    """
    if df is None or df.empty:
        return 0

    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )

    # 필요한 컬럼: 4개 모델의 예측값
    base_cols = ['ticker', 'date_month_end',
                 'revenue_billions_sarima_noexog',
                 'revenue_billions_lstm_forecast',
                 'revenue_billions_prophet_forecast',
                 'revenue_billions_esq_forecast']

    use_df = df.copy()

    # 컬럼 존재 확인 및 추가
    for col in base_cols:
        if col not in use_df.columns:
            use_df[col] = np.nan

    # 수정 후
    use_df['created_at'] = pd.Timestamp.utcnow().replace(tzinfo=None)

    # date_month_end를 datetime으로 변환
    use_df['date_month_end'] = pd.to_datetime(use_df['date_month_end'], errors='coerce')

    # NaN 제거
    use_df = use_df.dropna(subset=['ticker', 'date_month_end'])

    if use_df.empty:
        return 0

    final_cols = base_cols + ['created_at']
    use_df = use_df[final_cols]

    # ★★★ (ticker, date_month_end) 기준 중복 제거 - 최신 데이터만 유지 ★★★
    use_df = use_df.drop_duplicates(subset=['ticker', 'date_month_end'], keep='last')

    affected = 0
    with engine.begin() as conn:
        for ticker, group in use_df.groupby('ticker'):
            # ★★★ REPLACE INTO 또는 ON DUPLICATE KEY UPDATE 방식 사용 ★★★
            # MySQL의 REPLACE INTO는 (ticker, date_month_end)가 존재하면 삭제 후 삽입
            for _, row in group.iterrows():
                # 기존 데이터 삭제
                delete_sql = text("""
                    DELETE FROM {table_name}
                    WHERE ticker = :ticker AND date_month_end = :date_month_end
                """.format(table_name=table_name))

                result = conn.execute(delete_sql, {
                    'ticker': str(row['ticker']),
                    'date_month_end': row['date_month_end']
                })

                # 새 데이터 삽입
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


def _flush_batch_and_upload(batch_results, batch_revenue_results, db_info):
    """
    배치 → 성장요약 → long 두 종류(valuation, revenues) → DB 업서트
    + Revenue forecast 데이터도 함께 저장
    """
    global total_upsert_rows, total_revenue_rows

    if batch_results:
        ensure_valuation_table(db_info, table_name="us_valuation_result")

        try:
            final_df = pd.concat(batch_results, axis=0, ignore_index=True)
            rev_summary_batch, val_summary_batch = make_growth_summaries(final_df)

            long_val = _to_long(val_summary_batch, category='valuation')

            long_rev = _to_long(rev_summary_batch, category='revenue')
            model_map = {
                'revenue_billions_sarima_noexog_ttm': 'revenue_sarima',
                'revenue_billions_lstm_forecast_ttm': 'revenue_lstm',
                'revenue_billions_prophet_forecast_ttm': 'revenue_prophet',
                'revenue_billions_esq_forecast_ttm': 'revenue_es',
                'revenue_billions_avg_of_4_ttm': 'revenue_avg_of_4'
            }
            if 'model' in long_rev.columns:
                long_rev['model'] = long_rev['model'].replace(model_map)

            final_long = pd.concat([long_val, long_rev], axis=0, ignore_index=True)

            affected = upsert_long_to_db_on_ticker_created_at(final_long, db_info, table_name="us_valuation_result")
            total_upsert_rows += int(affected or 0)
            log("BATCH-UPLOADED", f"valuation rows={affected}, total={total_upsert_rows}")
        except Exception as e:
            log("BATCH-VAL-ERR", f"valuation upload failed: {e}")
        finally:
            del batch_results[:]
            gc.collect()

    if batch_revenue_results:
        ensure_revenue_forecast_table(db_info, table_name="us_revenue_forecast_result")

        try:
            # ★★★ concat 전에 각 배치의 ticker 확인 ★★★
            tickers_in_batch = [df['ticker'].iloc[0] if 'ticker' in df.columns and len(df) > 0 else 'UNKNOWN'
                                for df in batch_revenue_results]
            log("BATCH-REV-CHECK", f"revenue batch contains tickers: {tickers_in_batch}")

            revenue_df = pd.concat(batch_revenue_results, axis=0, ignore_index=True)

            # ★★★ timezone이 있는 datetime 컬럼을 timezone 없는 형태로 변환 ★★★
            for col in revenue_df.columns:
                if pd.api.types.is_datetime64_any_dtype(revenue_df[col]):
                    if hasattr(revenue_df[col].dtype, 'tz') and revenue_df[col].dtype.tz is not None:
                        revenue_df[col] = revenue_df[col].dt.tz_localize(None)

            # ★★★ concat 후 중복 확인 ★★★
            dup_check = revenue_df.groupby(['ticker', 'date_month_end']).size()
            if (dup_check > 1).any():
                log("BATCH-REV-DUP", f"Found duplicates in batch:\n{dup_check[dup_check > 1]}")

            affected = upsert_revenue_forecast_to_db(revenue_df, db_info, table_name="us_revenue_forecast_result")
            total_revenue_rows += int(affected or 0)
            log("BATCH-UPLOADED", f"revenue forecast rows={affected}, total={total_revenue_rows}")
        except Exception as e:
            log("BATCH-REV-ERR", f"revenue upload failed: {e}")
            import traceback
            log("BATCH-REV-TB", traceback.format_exc())
        finally:
            del batch_revenue_results[:]
            gc.collect()


if __name__ == "__main__":
    import argparse
    import gc

    from DATA.us_target_ticker_list import ticker_list

    parser = argparse.ArgumentParser(description="Run valuation pipeline in batches")
    parser.add_argument("--start", type=int, default=0, help="시작 인덱스(포함)")
    parser.add_argument("--end", type=int, default=len(ticker_list), help="끝 인덱스(미포함)")
    parser.add_argument("--batch-size", type=int, default=20, help="배치 크기 (기본 20)")
    args = parser.parse_args()

    api_key = 'hT0gAk87j9xZx4PlBApvBqfVL5IahvgV'
    db_info = {
        'host': get_db_host(),
        'port': 3307,
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'database': 'investar'
    }
    start_date_month = '2011-03-01'
    end_date_month = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
    measurement_date = pd.Timestamp.today().strftime('%Y-%m-%d')

    start_idx = 0
    end_idx = 2
    BATCH_SIZE = 20

    error_ticker_list = []
    total_success_tickers = 0
    total_upsert_rows = 0
    total_revenue_rows = 0

    batch_results = []
    batch_revenue_results = []

    target_tickers = ticker_list[start_idx:end_idx]

    miss_q, miss_m = audit_db_coverage(db_info, target_tickers)

    if miss_m:
        fname = f"market_cap_missing_ticker_{pd.Timestamp.today().strftime('%Y%m%d')}.csv"
        pd.DataFrame({"ticker": miss_m}).to_csv(fname, index=False, encoding="utf-8-sig")
        log("AUDIT-SAVE", f"US_fundm missing {len(miss_m)} tickers saved -> {fname}")
    else:
        log("AUDIT-SAVE", "US_fundm missing tickers: 0 (no file saved)")

    for idx, ticker in enumerate(target_tickers, 1):
        log("TICKER", f"{idx}/{len(target_tickers)} {ticker}")

        try:
            revenue_data, error = fetch_revenue_data(ticker, api_key)
            if revenue_data is None:
                msg = f"FMP revenue fetch failed: {error}"
                log("ERR-FMP-REV", f"{ticker} {msg}")
                error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_revenue', 'error': msg})
                continue
            rows = len(revenue_data) if isinstance(revenue_data, list) else 0
            log("OK-FMP-REV", f"{ticker} raw_rows={rows}")

            all_revenue_data = [{
                'ticker': ticker,
                'date': it.get('date', ''),
                'calendar_year': it.get('calendarYear', ''),
                'period': it.get('period', ''),
                'revenue': it.get('revenue', 0) if it.get('revenue') is not None else 0,
                'revenue_billions': round((it.get('revenue', 0) or 0) / 1_000_000_000, 2),
            } for it in revenue_data]

            fmp_revenue_df = pd.DataFrame(all_revenue_data)
            fmp_revenue_df['date'] = pd.to_datetime(fmp_revenue_df['date'])
            fmp_revenue_df = fmp_revenue_df.sort_values(['ticker', 'date'])
            fmp_revenue_df['date_month_end'] = to_month_end_safe(fmp_revenue_df['date'])
            bad = fmp_revenue_df['date_month_end'].isna().sum()
            log("CHK-FMP-MEND", f"{ticker} nan_mend={bad} / {len(fmp_revenue_df)} "
                                f"sample_date={fmp_revenue_df['date'][:3].tolist()}")
            fmp_revenue_df = fmp_revenue_df.dropna(subset=['date_month_end'])
            fmp_revenue_df = fmp_revenue_df.drop_duplicates(subset=['date_month_end']).sort_values(
                'date_month_end').reset_index(drop=True)
        except Exception as e:
            log("EXC-FMP-REV", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_revenue_preproc', 'error': str(e)})
            continue

        try:
            db_revenue_raw = fetch_db_revenue_data(ticker, db_info)
            rows_db = 0 if db_revenue_raw is None else len(db_revenue_raw)
            log("OK-DB-REV", f"{ticker} rows={rows_db}")
            if rows_db:
                db_revenue_df = db_revenue_raw.loc[
                    db_revenue_raw['revenue_billions'] != db_revenue_raw['revenue_billions'].shift()
                    ]
            else:
                db_revenue_df = pd.DataFrame(columns=['ticker', 'date', 'date_month_end', 'revenue_billions'])

            mereged_rev_data = pd.merge(fmp_revenue_df, db_revenue_df, on=['ticker', 'date_month_end'], how='outer')
            rev_data = mereged_rev_data[mereged_rev_data['date_month_end'] >= start_date_month]
            if 'revenue_billions_x' in rev_data.columns:
                rev_data['revenue_billions_x'] = rev_data['revenue_billions_x'].fillna(
                    rev_data.get('revenue_billions_y'))
                rev_data = rev_data.rename(columns={'revenue_billions_x': 'revenue_billions'})
            log("REV-MERGE", f"{ticker} merged_rows={len(rev_data)} cols={list(rev_data.columns)}")

            rev_data = clean_rev_data(rev_data)
            yr_min = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].min()
            yr_max = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].max()
            log("OK-REV-CLEAN", f"{ticker} rows={len(rev_data)} yrmin={yr_min} yrmax={yr_max}")
        except Exception as e:
            log("EXC-DB-REV", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'db_revenue_merge_clean', 'error': str(e)})
            continue

        try:
            periods = 4
            sarima_df, _ = sarima.run_sarima_prediction(rev_data, forecast_quarters=periods, exog_col=None)
            sarima_df = sarima_df.sort_values("date_month_end").set_index("date_month_end")
            lstm_raw_df, _ = lstm_v2.run_lstm_revenue_prediction(rev_data, ticker=ticker, prediction_quarters=4)
            lstm_df = lstm_raw_df.drop_duplicates(subset=['revenue_billions_lstm_forecast'], keep='last')
            prophet_raw_df, _ = prophet_v3.run_prophet_revenue_only(rev_data, ticker=ticker, prediction_quarters=4)
            es_raw_df, _ = esmod.run_es_revenue_quarterly(rev_data, ticker=ticker, prediction_quarters=4)
            log("OK-REV-FORECAST",
                f"{ticker} sarima={sarima_df.shape} lstm={lstm_df.shape} prophet={prophet_raw_df.shape} es={es_raw_df.shape}")
        except Exception as e:
            log("EXC-REV-FORECAST", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_forecast', 'error': str(e)})
            continue

        try:
            market_data, _ = fetch_market_data_yearly(ticker, api_key, start_year=2010)
            if not market_data:
                msg = "FMP market data fetch failed"
                log("ERR-FMP-MCAP", f"{ticker} {msg}")
                error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_market', 'error': msg})
                continue
            fmp_market_df = process_daily_to_monthly_market_data(market_data, ticker).copy()
            fmp_market_df['date_month_end'] = to_month_end_safe(fmp_market_df['date'])
            fmp_market_df = fmp_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
                'date_month_end').reset_index(drop=True)
            log("OK-FMP-MCAP",
                f"{ticker} rows={len(fmp_market_df)} range={fmp_market_df['date_month_end'].min()}~{fmp_market_df['date_month_end'].max()}")
        except Exception as e:
            log("EXC-FMP-MCAP", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_market_preproc', 'error': str(e)})
            continue

        try:
            db_market_df = _safe_get_db_market_df(ticker, db_info)
            if (not db_market_df.empty) and ('date_month_end' not in db_market_df.columns):
                if 'date' in db_market_df.columns:
                    db_market_df['date_month_end'] = to_month_end_safe(db_market_df['date'])
                else:
                    db_market_df = pd.DataFrame()

            if db_market_df.empty:
                merged_market_df = fmp_market_df.copy()
                merged_market_df['market_cap_billions_from_db'] = np.nan
            else:
                if 'market_cap_billions' in db_market_df.columns:
                    db_market_df_renamed = db_market_df.rename(
                        columns={'market_cap_billions': 'market_cap_billions_from_db'})
                else:
                    db_market_df_renamed = db_market_df[['date_month_end']].copy()
                    db_market_df_renamed['market_cap_billions_from_db'] = np.nan
                merged_market_df = fmp_market_df.merge(
                    db_market_df_renamed[['date_month_end', 'market_cap_billions_from_db']],
                    on='date_month_end', how='left'
                )

            if 'market_cap_billions' not in merged_market_df.columns:
                merged_market_df['market_cap_billions'] = np.nan
            if 'market_cap_billions_from_db' not in merged_market_df.columns:
                merged_market_df['market_cap_billions_from_db'] = np.nan

            merged_market_df['market_cap_billions'] = merged_market_df['market_cap_billions'].fillna(
                merged_market_df['market_cap_billions_from_db']
            )
            merged_market_df = merged_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
                'date_month_end').reset_index(drop=True)
            log("OK-MCAP-MERGE",
                f"{ticker} rows={len(merged_market_df)} nan_mcap={merged_market_df['market_cap_billions'].isna().sum()}")
        except Exception as e:
            log("EXC-MCAP-MERGE", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'market_merge', 'error': str(e)})
            continue

        try:
            enhanced_merged_df = pd.merge(
                merged_market_df[['date_month_end', 'market_cap_billions']],
                rev_data, on='date_month_end', how='outer'
            )
            market_cap_resize = enhanced_merged_df[
                ['date_month_end', 'market_cap_billions', 'ticker', 'revenue_billions']].copy()
            market_cap_resize.dropna(subset=['market_cap_billions'], inplace=True)
            market_cap_resize.ffill(limit=2, inplace=True)
            market_cap_resize = market_cap_resize[(market_cap_resize['date_month_end'] >= start_date_month) & (
                    market_cap_resize['date_month_end'] <= end_date_month)]
            market_cap_resize = market_cap_resize.dropna(axis=0)
            log("OK-PSR-PREP", f"{ticker} rows={len(market_cap_resize)}")

            enhanced_merged_df_with_ttm = calculate_enhanced_ttm_and_psr(market_cap_resize)

            # ★★★ Revenue forecast 데이터를 배치에 추가 ★★★
            # revenue_forecast_data = enhanced_merged_df_with_ttm.copy()
            # revenue_forecast_data['ticker'] = ticker
            # batch_revenue_results.append(revenue_forecast_data)
            # log("OK-REV-BATCH", f"{ticker} added to revenue batch, batch_size={len(batch_revenue_results)}")

            psr_ok = enhanced_merged_df_with_ttm[['date_month_end', 'PSR_ttm']].dropna()
            if psr_ok.empty or psr_ok['PSR_ttm'].count() < 6:
                msg = "PSR series too short after TTM shift"
                log("ERR-PSR-SHORT", f"{ticker} {msg}")
                error_ticker_list.append({'ticker': ticker, 'stage': 'psr_prepare', 'error': msg})
                continue

            psr_sarima_df, _ = sarima.run_sarima_psr_only(
                df=enhanced_merged_df_with_ttm,
                periods=12,
                target_col="PSR_ttm",
                analysis_start="2012-06-01",
                warmup_months=6,
                fill_method="interpolate",
                ic="aic"
            )
            psr_lstm_df, _ = lstm_v2.run_lstm_psr_prediction(enhanced_merged_df_with_ttm, ticker=ticker,
                                                             prediction_months=12)
            psr_prophet_df, _ = prophet_v3.run_prophet_psr_only(enhanced_merged_df_with_ttm, ticker=ticker,
                                                                prediction_months=12)
            psr_es_df, _ = esmod.run_es_psr_only(df=enhanced_merged_df_with_ttm, ticker=ticker, prediction_months=12,
                                                 start_date=None)
            log("OK-PSR-FORECAST",
                f"{ticker} sarima={psr_sarima_df.shape} lstm={psr_lstm_df.shape} prophet={psr_prophet_df.shape} es={psr_es_df.shape}")
        except Exception as e:
            log("EXC-PSR-FORECAST", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'psr_forecast', 'error': str(e)})
            continue

        try:
            def _pick(df, cols):
                d = df.copy()
                if 'date_month_end' not in d.columns:
                    d = d.reset_index()
                    if 'date_month_end' not in d.columns and 'index' in d.columns:
                        d = d.rename(columns={'index': 'date_month_end'})
                d = d.drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')
                return d[['date_month_end'] + cols].set_index('date_month_end')


            rev_sarima = _pick(sarima_df, ['revenue_billions_sarima_noexog'])
            rev_lstm = _pick(lstm_df, ['revenue_billions_lstm_forecast'])
            rev_prophet = _pick(prophet_raw_df, ['revenue_billions_prophet_forecast'])
            rev_es = _pick(es_raw_df, ['revenue_billions_esq_forecast'])

            revenue_forecast_df = pd.concat([rev_sarima, rev_lstm, rev_prophet, rev_es], axis=1, join='outer')

            # ★★★ revenue_forecast_df를 배치에 추가 (ticker 컬럼 추가) ★★★
            revenue_forecast_for_db = revenue_forecast_df.reset_index()  # date_month_end를 컬럼으로
            revenue_forecast_for_db['ticker'] = ticker
            batch_revenue_results.append(revenue_forecast_for_db)
            log("OK-REV-BATCH", f"{ticker} added to revenue batch, batch_size={len(batch_revenue_results)}")

            # TTM 계산을 위한 준비 (배치 추가와는 별개)
            revenue_forecast_df_reset = revenue_forecast_df.reset_index()
            if 'ticker' not in revenue_forecast_df_reset.columns:
                revenue_forecast_df_reset['ticker'] = ticker

            revenue_forecast_ = prepare_revenue_ttm(revenue_forecast_df_reset)
            revenue_forecast_ttm = revenue_forecast_.filter(like='_ttm').copy()

            if 'ticker' not in revenue_forecast_df_reset.columns or revenue_forecast_df_reset['ticker'].isna().all():
                raise ValueError("ticker 칼럼이 필요합니다. (Valuation pack 직전)")

            if 'date_month_end' not in revenue_forecast_.columns:
                revenue_forecast_ttm['date_month_end'] = revenue_forecast_df_reset['date_month_end'].values
            else:
                revenue_forecast_ttm['date_month_end'] = revenue_forecast_['date_month_end'].values
            revenue_forecast_ttm = revenue_forecast_ttm.drop_duplicates(subset=['date_month_end']).set_index(
                'date_month_end')

            revenue_cols_ttm = [
                'revenue_billions_sarima_noexog_ttm',
                'revenue_billions_lstm_forecast_ttm',
                'revenue_billions_prophet_forecast_ttm',
                'revenue_billions_esq_forecast_ttm'
            ]
            revenue_forecast_ttm['revenue_billions_avg_of_4_ttm'] = revenue_forecast_ttm[revenue_cols_ttm].mean(axis=1)

            psr_sarima = _pick(psr_sarima_df, ['PSR_ttm_sarima_forecast'])
            psr_lstm = _pick(psr_lstm_df, ['PSR_ttm_lstm_forecast'])
            psr_prophet = _pick(psr_prophet_df, ['PSR_prophet_forecast_noexog'])
            psr_es = _pick(psr_es_df, ['PSR_es_forecast'])
            psr_forecast_df = pd.concat([psr_sarima, psr_lstm, psr_prophet, psr_es], axis=1, join='outer')
            revenue_forecast_ttm = revenue_forecast_.filter(like='_ttm').copy()

            if 'ticker' not in revenue_forecast_df_reset.columns or revenue_forecast_df_reset['ticker'].isna().all():
                raise ValueError("ticker 칼럼이 필요합니다. (Valuation pack 직전)")

            if 'date_month_end' not in revenue_forecast_.columns:
                revenue_forecast_ttm['date_month_end'] = revenue_forecast_df_reset['date_month_end'].values
            else:
                revenue_forecast_ttm['date_month_end'] = revenue_forecast_['date_month_end'].values
            revenue_forecast_ttm = revenue_forecast_ttm.drop_duplicates(subset=['date_month_end']).set_index(
                'date_month_end')

            revenue_cols_ttm = [
                'revenue_billions_sarima_noexog_ttm',
                'revenue_billions_lstm_forecast_ttm',
                'revenue_billions_prophet_forecast_ttm',
                'revenue_billions_esq_forecast_ttm'
            ]
            revenue_forecast_ttm['revenue_billions_avg_of_4_ttm'] = revenue_forecast_ttm[revenue_cols_ttm].mean(axis=1)

            psr_sarima = _pick(psr_sarima_df, ['PSR_ttm_sarima_forecast'])
            psr_lstm = _pick(psr_lstm_df, ['PSR_ttm_lstm_forecast'])
            psr_prophet = _pick(psr_prophet_df, ['PSR_prophet_forecast_noexog'])
            psr_es = _pick(psr_es_df, ['PSR_es_forecast'])
            psr_forecast_df = pd.concat([psr_sarima, psr_lstm, psr_prophet, psr_es], axis=1, join='outer')

            valuation_df = revenue_forecast_ttm.join(psr_forecast_df, how='inner')
            valuation_df['ticker'] = ticker

            valuation_filled = valuation_df.copy()
            cols_to_ffill = ['ticker'] + [c for c in valuation_filled.columns if 'revenue_billions' in c]
            valuation_filled[cols_to_ffill] = valuation_filled[cols_to_ffill].ffill(limit=2)

            valuation_filled['sarima_valuation'] = valuation_filled['revenue_billions_sarima_noexog_ttm'] * \
                                                   valuation_filled['PSR_ttm_sarima_forecast']
            valuation_filled['lstm_valuation'] = valuation_filled['revenue_billions_lstm_forecast_ttm'] * \
                                                 valuation_filled['PSR_ttm_lstm_forecast']
            valuation_filled['prophet_valuation'] = valuation_filled['revenue_billions_prophet_forecast_ttm'] * \
                                                    valuation_filled['PSR_prophet_forecast_noexog']
            valuation_filled['es_valuation'] = valuation_filled['revenue_billions_esq_forecast_ttm'] * valuation_filled[
                'PSR_es_forecast']

            valuation_filled = valuation_filled.sort_index()
            valuation_result = (
                valuation_filled
                .groupby('ticker', group_keys=False)
                .apply(lambda d: d.tail(15))
                .reset_index()
                .rename(columns={'index': 'date_month_end'})
            )

            batch_results.append(valuation_result)
            total_success_tickers += 1
            log("OK-VAL-PACK", f"{ticker} packed={len(valuation_result)} batch={len(batch_results)}")

        except Exception as e:
            log("EXC-VAL-PACK", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'valuation_pack', 'error': str(e)})
            continue

        try:
            is_last = (idx == len(target_tickers))
            if (len(batch_results) >= BATCH_SIZE) or is_last:
                log("BATCH-FLUSH",
                    f"valuation={len(batch_results)}, revenue={len(batch_revenue_results)}, is_last={is_last}")
                _flush_batch_and_upload(batch_results, batch_revenue_results, db_info)
        except Exception as e:
            log("EXC-BATCH-FLUSH", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'batch_flush', 'error': str(e)})

        try:
            del (revenue_data, fmp_revenue_df, db_revenue_raw, db_revenue_df, mereged_rev_data,
                 rev_data, sarima_df, lstm_raw_df, lstm_df, prophet_raw_df, es_raw_df,
                 market_data, fmp_market_df, db_market_df, merged_market_df, enhanced_merged_df,
                 market_cap_resize, enhanced_merged_df_with_ttm, psr_sarima_df, psr_lstm_df,
                 psr_prophet_df, psr_es_df, revenue_forecast_df, revenue_forecast_for_db,
                 revenue_forecast_df_reset, revenue_forecast_, revenue_forecast_ttm,
                 valuation_df, valuation_filled, valuation_result)
            gc.collect()
        except Exception as e:
            log("EXC-GC", f"{ticker} e={e}")

    print(f"[DONE] 성공 ticker: {total_success_tickers}")
    print(f"[DONE] Valuation 업서트 rows: {total_upsert_rows}")
    print(f"[DONE] Revenue forecast 업서트 rows: {total_revenue_rows}")

    if error_ticker_list:
        try:
            pd.DataFrame(error_ticker_list).to_csv("valuation_error_list.csv", index=False, encoding="utf-8-sig")
            print(f"[INFO] 오류 리스트 저장: valuation_error_list.csv (총 {len(error_ticker_list)}개)")
        except Exception:
            print(f"[WARN] 오류 리스트 저장 실패 (총 {len(error_ticker_list)}개)")
    else:
        print("[INFO] 오류 없이 완료")