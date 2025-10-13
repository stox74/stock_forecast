# import sys, os
# from pathlib import Path
# import pandas as pd
# import numpy as np
# from typing import Optional
# # from stock_forecast.Korea_Market.valuation.kse_valuation_machine_v1 import psr_forecast_df
# from sqlalchemy import create_engine, text
# import importlib
# import DATA.us_sarima_forecast as sarima
# importlib.reload(sarima)
# import DATA.us_lstm_forecast_v2 as lstm_v2
# importlib.reload(lstm_v2)
# import DATA.us_prophet_forecast_v3 as prophet_v3
# importlib.reload(prophet_v3)
# import DATA.us_est_forecast_v2 as esmod
# importlib.reload(esmod)
#
# import gc
# import requests
# import warnings
# from sqlalchemy import create_engine
# from dateutil.relativedelta import relativedelta
# from pandas.tseries.offsets import MonthEnd
#
# import calendar
# import time
# # from DATA.stock_invest_function import *
# from DATA.stock_invest_function import *
#
# import traceback
# import datetime as dt
#
# DEBUG = True  # 필요하면 False로 꺼도 됩니다.
#
# def log(stage: str, msg: str):
#     print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {stage}: {msg}")
#
# warnings.filterwarnings('ignore')
#
# def _default_month_end_str(offset_months: int = 0) -> str:
#     return (pd.Timestamp.today().normalize() + MonthEnd(offset_months)).strftime('%Y-%m-%d')
#
# def audit_db_coverage(db_info, tickers):
#     eng = create_engine(
#         f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#         f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#     )
#     miss_q, miss_m = [], []
#     with eng.connect() as conn:
#         for t in tickers:
#             c_q = pd.read_sql(text(
#                 "SELECT COUNT(*) c FROM US_fundq WHERE UPPER(TRIM(ticker))=UPPER(:t) AND saleq IS NOT NULL"
#             ), conn, params={"t": t})['c'].iloc[0]
#             c_m = pd.read_sql(text(
#                 "SELECT COUNT(*) c FROM US_fundm WHERE UPPER(TRIM(ticker))=UPPER(:t) AND me IS NOT NULL"
#             ), conn, params={"t": t})['c'].iloc[0]
#             if c_q == 0: miss_q.append(t)
#             if c_m == 0: miss_m.append(t)
#     log("AUDIT", f"US_fundq missing: {len(miss_q)} tickers");  print(miss_q[:20])
#     log("AUDIT", f"US_fundm missing: {len(miss_m)} tickers");  print(miss_m[:20])
#     return miss_q, miss_m
#
#
# def add_repo_path():
#     here = Path.cwd()
#     # 현재 위치부터 상위 폴더를 훑으며 DATA 폴더가 보이는 지점 찾기
#     for p in [here, *here.parents]:
#         if (p / "DATA").exists():
#             if str(p) not in sys.path:
#                 sys.path.insert(0, str(p))
#             return str(p)
#     # 못 찾으면 로컬 고정 경로(본인 PC 경로로) 마지막 보루로 추가
#     fallback = r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast"
#     if os.path.isdir(fallback) and fallback not in sys.path:
#         sys.path.insert(0, fallback)
#     return fallback
#
# project_path = add_repo_path()
#
# def smoke_test_db_tables(db_info, ticker: str):
#     eng = create_engine(
#         f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#         f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#     )
#     with eng.begin() as conn:
#         one = conn.exec_driver_sql("SELECT 1").scalar()
#         log("SMOKE", f"SELECT 1 -> {one}")
#
#         # 테이블 존재
#         t1 = pd.read_sql("SHOW TABLES LIKE 'US_fundq';", conn)
#         t2 = pd.read_sql("SHOW TABLES LIKE 'US_fundm';", conn)
#         log("SMOKE", f"US_fundq exists? {not t1.empty} / US_fundm exists? {not t2.empty}")
#
#         # 티커 매칭: 원본·TRIM·대소 비교
#         q_cnt = pd.read_sql(
#             f"SELECT COUNT(*) AS c FROM US_fundq WHERE ticker='{ticker}' AND saleq IS NOT NULL;", conn
#         )['c'].iloc[0]
#         q_cnt_trim = pd.read_sql(
#             f"SELECT COUNT(*) AS c FROM US_fundq WHERE TRIM(ticker)='{ticker}' AND saleq IS NOT NULL;", conn
#         )['c'].iloc[0]
#         log("SMOKE", f"US_fundq {ticker} count = {q_cnt} (raw) / {q_cnt_trim} (TRIM)")
#
#         # 최근 5행 샘플
#         head = pd.read_sql(
#             text("""SELECT date, ticker, saleq
#                     FROM US_fundq
#                     WHERE TRIM(ticker)=:ticker AND saleq IS NOT NULL
#                     ORDER BY date DESC LIMIT 5;"""),
#             conn, params={"ticker": ticker}
#         )
#         log("SMOKE", f"latest US_fundq rows for {ticker}:\n{head}")
#
#     eng.dispose()
#
# # 유틸리티 함수들
#
# def diag_revenue_and_mcap_gaps(db_info, ticker: str):
#     eng = create_engine(
#         f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#         f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#     )
#     with eng.begin() as conn:
#         print("=== SCHEMA / CONNECTIVITY ===")
#         one = conn.exec_driver_sql("SELECT 1").scalar()
#         print("SELECT 1 ->", one)
#         current_db = conn.exec_driver_sql("SELECT DATABASE();").scalar()
#         print("DATABASE() ->", current_db)
#
#         print("\n=== TABLE EXISTENCE ===")
#         t_q = pd.read_sql("SHOW TABLES LIKE 'US_fundq';", conn)
#         t_m = pd.read_sql("SHOW TABLES LIKE 'US_fundm';", conn)
#         print("US_fundq exists? ", not t_q.empty)
#         print("US_fundm exists? ", not t_m.empty)
#
#         print(f"\n=== US_fundq presence for {ticker} ===")
#         # 원형 / TRIM / 대소문자 / LIKE 변형
#         q_raw = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundq WHERE ticker=:t AND saleq IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         q_trim = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundq WHERE TRIM(ticker)=:t AND saleq IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         q_upper = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundq WHERE UPPER(TRIM(ticker))=UPPER(:t) AND saleq IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         q_like = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundq WHERE (ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3) AND saleq IS NOT NULL"
#         ), conn, params={"p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})['c'].iloc[0]
#         print(f"raw={q_raw}, trim={q_trim}, upper={q_upper}, like(aliases)={q_like}")
#
#         if max(q_raw, q_trim, q_upper, q_like) > 0:
#             # 어떤 형태로 저장돼 있는지 실제 샘플
#             print("\n--- sample matched tickers in US_fundq ---")
#             sample = pd.read_sql(text(
#                 """SELECT DISTINCT ticker
#                    FROM US_fundq
#                    WHERE (ticker=:t OR TRIM(ticker)=:t OR UPPER(TRIM(ticker))=UPPER(:t)
#                           OR ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3)
#                    LIMIT 20;"""
#             ), conn, params={"t": ticker, "p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})
#             print(sample)
#
#             print("\n--- latest 5 rows (by date) ---")
#             latest = pd.read_sql(text(
#                 """SELECT date, ticker, saleq
#                    FROM US_fundq
#                    WHERE (ticker=:t OR TRIM(ticker)=:t OR UPPER(TRIM(ticker))=UPPER(:t)
#                           OR ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3)
#                      AND saleq IS NOT NULL
#                    ORDER BY date DESC
#                    LIMIT 5;"""
#             ), conn, params={"t": ticker, "p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})
#             print(latest)
#         else:
#             print(f">>> US_fundq에 {ticker} 관련 매출 데이터가 전혀 없습니다.")
#
#         print(f"\n=== US_fundm presence for {ticker} ===")
#         m_raw = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundm WHERE ticker=:t AND me IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         m_trim = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundm WHERE TRIM(ticker)=:t AND me IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         m_upper = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundm WHERE UPPER(TRIM(ticker))=UPPER(:t) AND me IS NOT NULL"
#         ), conn, params={"t": ticker})['c'].iloc[0]
#         m_like = pd.read_sql(text(
#             "SELECT COUNT(*) c FROM US_fundm WHERE (ticker LIKE :p1 OR ticker LIKE :p2 OR ticker LIKE :p3) AND me IS NOT NULL"
#         ), conn, params={"p1": f"{ticker}%", "p2": f"{ticker}:%", "p3": f"{ticker}.%"})['c'].iloc[0]
#         print(f"raw={m_raw}, trim={m_trim}, upper={m_upper}, like(aliases)={m_like}")
#
#         # US_fundm 전체 보급 상태(표본)
#         print("\n=== US_fundm coverage sample (top 10 tickers by count) ===")
#         coverage = pd.read_sql(
#             "SELECT ticker, COUNT(*) AS c, MIN(date) AS from_dt, MAX(date) AS to_dt "
#             "FROM US_fundm WHERE me IS NOT NULL GROUP BY ticker ORDER BY c DESC LIMIT 10;",
#             conn
#         )
#         print(coverage)
#
#     eng.dispose()
#
# def to_month_end_safe(s: pd.Series) -> pd.Series:
#     s = pd.to_datetime(s, errors="coerce")
#     prev_mask = s.dt.day.between(1, 5, inclusive="both")
#     out = s.copy()
#     out.loc[prev_mask] = (s.loc[prev_mask] + MonthEnd(-1))
#     out.loc[~prev_mask] = (s.loc[~prev_mask] + MonthEnd(0))
#     return out
#
# def process_daily_to_monthly_market_data(daily_data, ticker):
#     if not daily_data:
#         return pd.DataFrame()
#     df = pd.DataFrame(daily_data)
#     df['date'] = pd.to_datetime(df['date'])
#     df = df.sort_values('date')
#     df['year_month'] = df['date'].dt.to_period('M')
#     monthly_data = []
#     for year_month in df['year_month'].unique():
#         month_data = df[df['year_month'] == year_month]
#         last_day_data = month_data.loc[month_data['date'].idxmax()]
#         monthly_data.append({
#             'ticker': ticker,
#             'date': last_day_data['date'],
#             'market_cap': last_day_data['marketCap'],
#             'market_cap_billions': round(last_day_data['marketCap'] / 1_000_000_000, 2),
#         })
#     return pd.DataFrame(monthly_data)
#
#
# def fetch_revenue_data(ticker, api_key):
#     url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
#     params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}
#     try:
#         if DEBUG: log("FMP-REV-REQ", f"{ticker} url={url} limit=200 period=quarter apikey=***")
#         response = requests.get(url, params=params, timeout=30)
#         if DEBUG: log("FMP-REV-RESP", f"{ticker} status={response.status_code}")
#         if response.status_code != 200:
#             return None, f"HTTP {response.status_code}"
#         data = response.json()
#         if DEBUG: log("FMP-REV-DATA", f"{ticker} type={type(data).__name__} size={len(data) if isinstance(data, list) else 'dict'}")
#         if isinstance(data, dict) and 'Error Message' in data:
#             return None, f"API 오류: {data['Error Message']}"
#         if not data:
#             return None, "데이터 없음"
#         return data, None
#     except Exception as e:
#         if DEBUG: log("FMP-REV-EXC", f"{ticker} exc={e} tb={traceback.format_exc().splitlines()[-1]}")
#         return None, f"오류: {str(e)}"
#
# def fetch_market_data_yearly(ticker, api_key, start_year=2010):
#     all_data = []
#     current_year = dt.datetime.now().year
#     for year in range(start_year, current_year + 1):
#         url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
#         params = {'from': f"{year}-01-01", 'to': f"{year}-12-31", 'apikey': api_key}
#         try:
#             if DEBUG and year in (start_year, start_year+1, current_year):
#                 log("FMP-MCAP-REQ", f"{ticker} year={year} url={url} apikey=***")
#             response = requests.get(url, params=params, timeout=30)
#             if DEBUG and year in (start_year, start_year+1, current_year):
#                 log("FMP-MCAP-RESP", f"{ticker} year={year} status={response.status_code}")
#             if response.status_code == 200:
#                 data = response.json()
#                 if data and isinstance(data, list):
#                     all_data.extend(data)
#             time.sleep(0.3)
#         except Exception as e:
#             if DEBUG: log("FMP-MCAP-EXC", f"{ticker} year={year} exc={e}")
#             continue
#     if DEBUG: log("FMP-MCAP-DONE", f"{ticker} total_records={len(all_data)}")
#     return all_data if all_data else None, None
#
# def fetch_db_revenue_data(ticker, db_info, end_date=None):
#     if end_date is None:
#         # 예: 직전 월말
#         end_date = (pd.Timestamp.today().normalize() - MonthEnd(1)).strftime('%Y-%m-%d')
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#         )
#         sql = text("""
#             SELECT date, ticker, saleq
#             FROM US_fundq
#             WHERE UPPER(TRIM(ticker)) = UPPER(:ticker)
#               AND saleq IS NOT NULL
#               AND date <= :end_date
#             ORDER BY date ASC
#         """)
#         with engine.connect() as conn:
#             df = pd.read_sql(sql, conn, params={"ticker": ticker, "end_date": end_date})
#         if df.empty:
#             log("DB-REV-EMPTY", f"{ticker} 0 rows (<= {end_date})");  return df
#         df['date'] = pd.to_datetime(df['date'], errors='coerce')
#         df = df.dropna(subset=['date'])
#         df['revenue_billions'] = df['saleq'] / 1000.0
#         df['date_month_end'] = to_month_end_safe(df['date'])
#         log("DB-REV-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
#         return df[['ticker','date','date_month_end','revenue_billions']]
#     except Exception as e:
#         log("DB-REV-EXC", f"{ticker} {type(e).__name__}: {e}");  return pd.DataFrame()
#
# def fetch_db_market_data(ticker, db_info, end_date=None):
#     if end_date is None:
#         # 예: 당월 말일 예상치까지 받고 싶으면 MonthEnd(1), 확정된 최신 월말까지만이면 MonthEnd(0)/(-1)
#         end_date = _default_month_end_str(1)   # 1달 뒤 월말(캘린더 상 최대치)로 여유
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#         )
#         sql = text("""
#             SELECT date, ticker, me
#             FROM US_fundm
#             WHERE UPPER(TRIM(ticker)) = UPPER(:ticker)
#               AND me IS NOT NULL
#               AND date <= :end_date
#             ORDER BY date ASC
#         """)
#         with engine.connect() as conn:
#             df = pd.read_sql(sql, conn, params={"ticker": ticker, "end_date": end_date})
#         if df.empty:
#             log("DB-MCAP-EMPTY", f"{ticker} 0 rows (<= {end_date})");  return df
#         df['date'] = pd.to_datetime(df['date'], errors='coerce')
#         df = df.dropna(subset=['date'])
#         df['market_cap_billions'] = df['me'] / 1000.0
#         df['date_month_end'] = to_month_end_safe(df['date'])
#         log("DB-MCAP-OK", f"{ticker} rows={len(df)} range={df['date'].min().date()}~{df['date'].max().date()}")
#         return df[['ticker','date','date_month_end','market_cap_billions']]
#     except Exception as e:
#         log("DB-MCAP-EXC", f"{ticker} {type(e).__name__}: {e}");  return pd.DataFrame()
#
#
# def calculate_enhanced_ttm_and_psr(merged_data):
#     """Calculate enhanced TTM and PSR"""
#     df = merged_data.copy()
#
#     # ✅ 날짜형으로 변환
#     df['date_month_end'] = pd.to_datetime(df['date_month_end'], errors='coerce')
#     df = df.sort_values(['date_month_end']).reset_index(drop=True)
#     df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)
#
#     # Calculate TTM from quarterly revenue
#     df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0, drop=True)
#     df['revenue_ttm_billions'] = df['revenue_ttm']
#
#     # Apply 2-month shift
#     df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)
#
#     # Calculate PSR
#     df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']
#
#     # Handle infinite values
#     df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)
#
#     return df
#
# def prepare_revenue_ttm(
#     df: pd.DataFrame,
#     revenue_key: str = "revenue_billions",
#     min_periods: int = 1,   # 완전한 TTM만 원하면 4로 바꾸세요
# ) -> pd.DataFrame:
#     """
#     1) revenue 칼럼들의 NaN을 '해당 행의 revenue 평균'으로 채움
#     2) 각 revenue 칼럼의 4분기 합(TTM)을 *_ttm 칼럼으로 생성 (시차 없음)
#     - 그룹 기준: ticker
#     - 정렬 기준: date_month_end (월말 날짜)
#     """
#     d = df.copy()
#
#     # date_month_end: index에 있으면 칼럼으로 복구
#     if 'date_month_end' not in d.columns:
#         d = d.reset_index().rename(columns={'index': 'date_month_end'})
#     d['date_month_end'] = pd.to_datetime(d['date_month_end'])
#
#     if 'ticker' not in d.columns:
#         raise ValueError("ticker 칼럼이 필요합니다.")
#
#     # revenue 칼럼 자동 탐지
#     rev_cols = [c for c in d.columns if revenue_key in c]
#     if not rev_cols:
#         raise ValueError(f"'{revenue_key}' 가 포함된 칼럼을 찾지 못했습니다.")
#
#     # ticker NaN 보정
#     uniq_tickers = d['ticker'].dropna().unique()
#     if len(uniq_tickers) == 1:
#         d['ticker'] = d['ticker'].ffill().bfill()
#     else:
#         d = d[~d['ticker'].isna()].copy()
#
#     # 정렬
#     d = d.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)
#
#     # NaN 보간: 행 평균으로 revenue 결측치 채우기
#     row_mean = d[rev_cols].mean(axis=1, skipna=True)
#     for c in rev_cols:
#         d[c] = d[c].fillna(row_mean)
#
#     # TTM 계산 (최근 4분기 합, 시차 없음)
#     for c in rev_cols:
#         ttm_col = f"{c}_ttm"
#         d[ttm_col] = (
#             d.groupby('ticker', group_keys=False)[c]
#              .rolling(window=4, min_periods=min_periods)
#              .sum()
#              .reset_index(level=0, drop=True)
#         )
#
#     d = d.set_index('date_month_end')
#     return d
#
# def clean_rev_data(rev_data: pd.DataFrame) -> pd.DataFrame:
#     """
#     1) 'revenue' 컬럼 값이 NaN인 행 제거
#     2) (calendar_year, period) 중복 행 제거 (첫 번째 행만 유지)
#        - 입력 순서를 그대로 기준으로 '첫째 데이터'를 보존
#     """
#     required = ['revenue', 'calendar_year', 'period']
#     missing = [c for c in required if c not in rev_data.columns]
#     if missing:
#         raise ValueError(f"필수 컬럼이 없습니다: {missing}")
#
#     d = rev_data.copy()
#
#     # 1) revenue NaN인 행 제거
#     before = len(d)
#     d = d[~d['revenue'].isna()].copy()
#
#     # 2) (calendar_year, period) 중복 제거 — 첫 행 유지
#     d = d.drop_duplicates(subset=['calendar_year', 'period'], keep='first').reset_index(drop=True)
#     return d
#
# # 1) _safe_get_db_market_df 시그니처/호출부 수정
# def _safe_get_db_market_df(ticker, db_info):
#     try:
#         if DEBUG: log("DB-MCAP-REQ", f"{ticker}")
#         df = fetch_db_market_data(ticker, db_info)
#         n = 0 if df is None else len(df)
#         if DEBUG: log("DB-MCAP-RESP", f"{ticker} rows={n}")
#         if df is None or n == 0:
#             return pd.DataFrame()
#         return df.copy()
#     except Exception as e:
#         if DEBUG: log("DB-MCAP-EXC", f"{ticker} exc={e} tb={traceback.format_exc().splitlines()[-1]}")
#         return pd.DataFrame()
#
# def identify_revenue_columns(columns):
#     rev_cols = [c for c in columns if c.startswith("revenue_billions")]
#     model_map = {"sarima": [], "lstm": [], "prophet": [], "es": []}
#     for c in rev_cols:
#         low = c.lower()
#         if "sarima" in low:
#             model_map["sarima"].append(c)
#         elif "lstm" in low:
#             model_map["lstm"].append(c)
#         elif "prophet" in low:
#             model_map["prophet"].append(c)
#         elif "es" in low:
#             model_map["es"].append(c)
#     return rev_cols, model_map
#
# def identify_valuation_columns(columns):
#     val_cols = [c for c in columns if c.endswith("_valuation")]
#     model_map = {"sarima": None, "lstm": None, "prophet": None, "es": None}
#     for c in val_cols:
#         low = c.lower()
#         if "sarima" in low:
#             model_map["sarima"] = c
#         elif "lstm" in low:
#             model_map["lstm"] = c
#         elif "prophet" in low:
#             model_map["prophet"] = c
#         elif "es" in low:
#             model_map["es"] = c
#     return val_cols, model_map
#
# def compute_growth(series: pd.Series, start_dt: pd.Timestamp) -> dict:
#     s = series.dropna()
#     s = s.loc[s.index >= start_dt]
#     if s.empty:
#         return {"start_value": np.nan, "end_value": np.nan, "growth": np.nan}
#     start_value = s.iloc[0]
#     end_value = s.iloc[-1]
#     if pd.isna(start_value) or start_value == 0:
#         growth = np.nan
#     else:
#         growth = (end_value / start_value) - 1.0
#     return {"start_value": start_value, "end_value": end_value, "growth": growth}
#
# def make_growth_summaries(df: pd.DataFrame):
#     """
#     final_valuation_df처럼 날짜가 'index' 컬럼에 들어있는 경우를 지원.
#     - 'index' → date_month_end(월말 정규화) 생성
#     - ticker별로 revenue/valuation 성장률 계산
#     - revenue: Sarima/LSTM/Prophet/ES + 4개 평균
#     - valuation: Sarima/LSTM/Prophet/ES + 최저 제외 Top3 평균
#     """
#     d = df.copy()
#
#     # 1) 날짜: 'index' 컬럼을 날짜로 파싱해서 date_month_end 생성
#     if "date_month_end" not in d.columns:
#         if "index" in d.columns:
#             d["date_month_end"] = pd.to_datetime(d["index"], errors="coerce")
#         else:
#             idx_dt = pd.to_datetime(d.index, errors="coerce")
#             if idx_dt.notna().any():
#                 d = d.reset_index().rename(columns={"index": "date_month_end"})
#                 d["date_month_end"] = pd.to_datetime(d["date_month_end"], errors="coerce")
#             else:
#                 raise KeyError("날짜가 들어있는 'index' 컬럼(또는 date_month_end)을 찾을 수 없습니다.")
#     # 월말 정규화
#     d["date_month_end"] = (d["date_month_end"] + MonthEnd(0))
#     d = d.dropna(subset=["date_month_end"])
#
#     # 2) ticker 보강(없으면 기본값)
#     if "ticker" not in d.columns:
#         d["ticker"] = d.get("Ticker", d.get("symbol", "UNKNOWN"))
#
#     # 정렬
#     d = d.sort_values(["ticker", "date_month_end"]).reset_index(drop=True)
#
#     # 3) 매출/밸류에이션 컬럼 분리
#     rev_cols, rev_model_map = identify_revenue_columns(d.columns)
#     val_cols, val_model_map = identify_valuation_columns(d.columns)
#
#     # 4) 이번달 말일 시작점
#     start_dt = (pd.Timestamp.today() + MonthEnd(0)).normalize()
#
#     revenue_growth_rows = []
#     valuation_growth_rows = []
#
#     for ticker, g in d.groupby("ticker"):
#         g = g.set_index("date_month_end").copy()
#
#         # --- 매출: 모델별 성장률 ---
#         rev_model_cols = {
#             "sarima": rev_model_map["sarima"][0] if rev_model_map["sarima"] else None,
#             "lstm": rev_model_map["lstm"][0] if rev_model_map["lstm"] else None,
#             "prophet": rev_model_map["prophet"][0] if rev_model_map["prophet"] else None,
#             "es": rev_model_map["es"][0] if rev_model_map["es"] else None,
#         }
#         for model, col in rev_model_cols.items():
#             if col is None or col not in g.columns:
#                 continue
#             m = compute_growth(g[col], start_dt)
#             revenue_growth_rows.append({
#                 "ticker": ticker,
#                 "series": f"revenue_{model}",
#                 "start_date": start_dt.date(),
#                 "start_value": m["start_value"],
#                 "end_value": m["end_value"],
#                 "growth": m["growth"],
#             })
#
#         # --- 매출: 4개 평균 ---
#         present_rev_cols = [c for c in rev_model_cols.values() if c and c in g.columns]
#         if present_rev_cols:
#             g["revenue_avg_of_4"] = g[present_rev_cols].mean(axis=1, skipna=True)
#             m = compute_growth(g["revenue_avg_of_4"], start_dt)
#             revenue_growth_rows.append({
#                 "ticker": ticker,
#                 "series": "revenue_avg_of_4",
#                 "start_date": start_dt.date(),
#                 "start_value": m["start_value"],
#                 "end_value": m["end_value"],
#                 "growth": m["growth"],
#             })
#
#         # --- 밸류에이션: 모델별 성장률 ---
#         val_model_cols = {k: v for k, v in val_model_map.items() if v is not None and v in g.columns}
#         for model, col in val_model_cols.items():
#             m = compute_growth(g[col], start_dt)
#             valuation_growth_rows.append({
#                 "ticker": ticker,
#                 "series": f"valuation_{model}",
#                 "start_date": start_dt.date(),
#                 "start_value": m["start_value"],
#                 "end_value": m["end_value"],
#                 "growth": m["growth"],
#             })
#
#         # --- 밸류에이션: 최저 제외 Top3 평균 ---
#         present_val_cols = list(val_model_cols.values())
#         if present_val_cols:
#             vals = g[present_val_cols].copy()
#             row_min = vals.min(axis=1)
#             top3_avg = (vals.sum(axis=1) - row_min) / np.maximum(vals.count(axis=1) - 1, 1)
#             g["valuation_avg_top3"] = top3_avg
#             m = compute_growth(g["valuation_avg_top3"], start_dt)
#             valuation_growth_rows.append({
#                 "ticker": ticker,
#                 "series": "valuation_avg_top3",
#                 "start_date": start_dt.date(),
#                 "start_value": m["start_value"],
#                 "end_value": m["end_value"],
#                 "growth": m["growth"],
#             })
#
#     revenue_growth_summary = pd.DataFrame(revenue_growth_rows)
#     valuation_growth_summary = pd.DataFrame(valuation_growth_rows)
#     return revenue_growth_summary, valuation_growth_summary
#
#
# def _to_long(df: pd.DataFrame, category: str) -> pd.DataFrame:
#     """
#     rev/val summary 공통 포맷(series 컬럼을 분해)
#     - category: 'revenue' 또는 'valuation'
#     """
#     if df is None or df.empty:
#         return pd.DataFrame(columns=[
#             "ticker","category","model","start_month_end","start_value","end_value","growth","created_at"
#         ])
#
#     out = df.copy()
#
#     # start_date -> start_month_end 로 통일
#     if "start_date" in out.columns:
#         out = out.rename(columns={"start_date": "start_month_end"})
#
#     # series: 'revenue_sarima' / 'valuation_avg_top3' 등
#     out["category"] = category
#     # 'revenue_' 또는 'valuation_' prefix 제거 → model
#     prefix = f"{category}_"
#     out["model"] = out["series"].str.replace(prefix, "", regex=False)
#
#     # 정리
#     out["start_month_end"] = pd.to_datetime(out["start_month_end"], errors="coerce")
#     out["created_at"] = pd.Timestamp.utcnow()
#
#     cols = ["ticker","category","model","start_month_end","start_value","end_value","growth","created_at"]
#     return out[cols]
#
# # 1) 테이블 보장: 존재하지 않으면 생성
# def _ddl(table_name: str, with_collate: bool = True, collation: str = "utf8mb4_0900_ai_ci") -> str:
#     tail = f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={collation};" if with_collate else \
#            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
#     return f"""
#     CREATE TABLE IF NOT EXISTS `{table_name}` (
#       `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
#       `ticker` VARCHAR(16) NOT NULL,
#       `category` VARCHAR(32) NOT NULL,
#       `model` VARCHAR(64) NOT NULL,
#       `start_month_end` DATE NULL,
#       `start_value` DECIMAL(20,8) NULL,
#       `end_value` DECIMAL(20,8) NULL,
#       `growth` DECIMAL(20,8) NULL,
#       `created_at` DATETIME NOT NULL,
#       `created_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
#       `updated_ts` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
#       PRIMARY KEY (`id`),
#       KEY `idx_created_at` (`created_at`),
#       KEY `idx_ticker_created` (`ticker`, `created_at`),
#       UNIQUE KEY `uq_tk_ca_cat_model_start`
#         (`ticker`,`created_at`,`category`,`model`,`start_month_end`)
#     ) {tail}
#     """
#
# def _is_unknown_collation(err: Exception) -> bool:
#     # pymysql / mysqlclient 모두에서 1273 코드 또는 메시지 포함 여부로 감지
#     if "Unknown collation" in str(err):
#         return True
#     try:
#         code = getattr(getattr(err, "orig", err), "args", [None])[0]
#         return code == 1273
#     except Exception:
#         return False
#
# def ensure_valuation_table(db_info: dict, table_name: str = "us_valuation_result"):
#     engine = create_engine(
#         f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#         f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#     )
#     with engine.begin() as conn:
#         # 1) MySQL8 전용 콜레이션 시도
#         try:
#             conn.execute(text(_ddl(table_name, with_collate=True, collation="utf8mb4_0900_ai_ci")))
#             return
#         except Exception as e:
#             if not _is_unknown_collation(e):
#                 raise
#         # 2) 광범위 호환 콜레이션 시도
#         try:
#             conn.execute(text(_ddl(table_name, with_collate=True, collation="utf8mb4_unicode_ci")))
#             return
#         except Exception as e2:
#             if not _is_unknown_collation(e2):
#                 raise
#         # 3) COLLATE 제거 (서버 기본값 사용)
#         conn.execute(text(_ddl(table_name, with_collate=False)))
#
# def upsert_long_to_db_on_ticker_created_at(long_df: pd.DataFrame,
#                                            db_info: dict,
#                                            table_name: str = "us_valuation_result") -> int:
#     """
#     요구사항 #3:
#       - (ticker, created_at) 동일 묶음은 기존 레코드 삭제 후 일괄 insert → '갱신'
#       - 그 외는 누적(append)
#     전제:
#       long_df에는 최소 ['ticker','created_at']가 존재.
#     """
#     if long_df.empty:
#         return 0
#
#     engine = create_engine(
#         f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
#     )
#
#     # 안전을 위해 필요한 열만 제한적으로 사용(없으면 생성)
#     needed_cols = ['ticker','category','model','start_month_end','start_value','end_value','growth','created_at']
#     for c in needed_cols:
#         if c not in long_df.columns:
#             long_df[c] = np.nan
#     use_df = long_df[needed_cols].copy()
#
#     affected = 0
#     with engine.begin() as conn:
#         # (ticker, created_at) 조합별로 삭제 후 insert
#         grp_cols = ['ticker','created_at']
#         for (tk, ca), g in use_df.groupby(grp_cols):
#             # 1) delete
#             del_sql = f"""
#                 DELETE FROM {table_name}
#                 WHERE ticker = :ticker AND created_at = :created_at
#             """
#             conn.execute(text(del_sql), {'ticker': str(tk), 'created_at': pd.to_datetime(ca)})
#
#             # 2) insert
#             g.to_sql(table_name, conn, if_exists='append', index=False)
#             affected += len(g)
#
#     return affected
#
#
# # ==========================
# # 복수 ticker 처리 입력값/리스트
# # ==========================
#
# # ===== 메인 실행 코드 =====
# if __name__ == "__main__":
#     import argparse
#     import gc
#
#     # 파일 상단에 import 추가
#     from DATA.us_target_ticker_list import ticker_list
#
#     parser = argparse.ArgumentParser(description="Run valuation pipeline in batches")
#     parser.add_argument("--start", type=int, default=0, help="시작 인덱스(포함)")
#     parser.add_argument("--end", type=int, default=len(ticker_list), help="끝 인덱스(미포함)")
#     parser.add_argument("--batch-size", type=int, default=20, help="배치 크기 (기본 20)")
#     args = parser.parse_args()
#
#     # ========================
#     # 설정
#     # ========================
#
#
#     api_key = 'hT0gAk87j9xZx4PlBApvBqfVL5IahvgV'
#     db_info = {
#         'host': get_db_host(),
#         'port': 3307,
#         'user': 'stox7412',
#         'password': 'Apt106503!~',
#         'database': 'investar'
#     }
#     start_date_month = '2011-03-01'
#     end_date_month = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
#     measurement_date = pd.Timestamp.today().strftime('%Y-%m-%d')
#
#
#     start_idx = 0
#     end_idx = 2
#     BATCH_SIZE = 20
#
#     # 결과/상태 누적
#     error_ticker_list = []
#     total_success_tickers = 0
#     total_upsert_rows = 0
#
#     # 배치 컨테이너
#     batch_results = []
#
#     # 대상 티커 슬라이싱
#     # for ticker in us_tickers[:5]:
#     target_tickers = us_tickers[start_idx:end_idx]
#
#
#     # 메인 시작 시 1회:
#     # 메인 시작 시 1회: 커버리지 점검 + US_fundm 미존재 티커 CSV 저장
#     miss_q, miss_m = audit_db_coverage(db_info, target_tickers)
#
#     if miss_m:
#         fname = f"market_cap_missing_ticker_{pd.Timestamp.today().strftime('%Y%m%d')}.csv"
#         pd.DataFrame({"ticker": miss_m}).to_csv(fname, index=False, encoding="utf-8-sig")
#         log("AUDIT-SAVE", f"US_fundm missing {len(miss_m)} tickers saved -> {fname}")
#     else:
#         log("AUDIT-SAVE", "US_fundm missing tickers: 0 (no file saved)")
#
#
#     def _flush_batch_and_upload(batch_results, db_info):
#         """
#         배치 → 성장요약 → long 두 종류(valuation, str=revenues) → DB 업서트
#         """
#         global total_upsert_rows
#         if not batch_results:
#             return
#
#         ensure_valuation_table(db_info, table_name="us_valuation_result")
#
#         try:
#             final_df = pd.concat(batch_results, axis=0, ignore_index=True)
#
#             # make_growth_summaries가 (rev_summary, val_summary) 형태를 반환한다고 가정
#             rev_summary_batch, val_summary_batch = make_growth_summaries(final_df)
#
#             # --- valuation long ---
#             long_val = _to_long(val_summary_batch, category='valuation')
#
#             # --- revenue long (카테고리=str, 모델명 통일) ---
#             long_rev = _to_long(rev_summary_batch, category='revenue')
#             # 모델명 표준화
#             model_map = {
#                 'revenue_billions_sarima_noexog_ttm': 'revenue_sarima',
#                 'revenue_billions_lstm_forecast_ttm': 'revenue_lstm',
#                 'revenue_billions_prophet_forecast_ttm': 'revenue_prophet',
#                 'revenue_billions_esq_forecast_ttm': 'revenue_es',
#                 'revenue_billions_avg_of_4_ttm': 'revenue_avg_of_4'
#             }
#             if 'model' in long_rev.columns:
#                 long_rev['model'] = long_rev['model'].replace(model_map)
#
#             # --- 두 long 합치기 ---
#             final_long = pd.concat([long_val, long_rev], axis=0, ignore_index=True)
#
#             # --- 업서트 (요구사항 #3 반영: ticker+created_at 기준 갱신, 아니면 누적) ---
#             affected = upsert_long_to_db_on_ticker_created_at(final_long, db_info, table_name="us_valuation_result")
#             total_upsert_rows += int(affected or 0)
#             log("BATCH-UPLOADED", f"rows={affected}, total_upsert_rows={total_upsert_rows}")
#         finally:
#             del batch_results[:]
#             gc.collect()
#
#     # ===== 메인 루프 (강화된 디버그 로그) =====
#     for idx, ticker in enumerate(target_tickers, 1):
#         log("TICKER", f"{idx}/{len(target_tickers)} {ticker}")
#
#         # 1) FMP 매출
#         try:
#             revenue_data, error = fetch_revenue_data(ticker, api_key)
#             if revenue_data is None:
#                 msg = f"FMP revenue fetch failed: {error}"
#                 log("ERR-FMP-REV", f"{ticker} {msg}")
#                 error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_revenue', 'error': msg})
#                 continue
#             rows = len(revenue_data) if isinstance(revenue_data, list) else 0
#             log("OK-FMP-REV", f"{ticker} raw_rows={rows}")
#
#             all_revenue_data = [{
#                 'ticker': ticker,
#                 'date': it.get('date', ''),
#                 'calendar_year': it.get('calendarYear', ''),
#                 'period': it.get('period', ''),
#                 'revenue': it.get('revenue', 0) if it.get('revenue') is not None else 0,
#                 'revenue_billions': round((it.get('revenue', 0) or 0) / 1_000_000_000, 2),
#             } for it in revenue_data]
#
#             fmp_revenue_df = pd.DataFrame(all_revenue_data)
#             fmp_revenue_df['date'] = pd.to_datetime(fmp_revenue_df['date'])
#             fmp_revenue_df = fmp_revenue_df.sort_values(['ticker', 'date'])
#             fmp_revenue_df['date_month_end'] = to_month_end_safe(fmp_revenue_df['date'])
#             bad = fmp_revenue_df['date_month_end'].isna().sum()
#             log("CHK-FMP-MEND", f"{ticker} nan_mend={bad} / {len(fmp_revenue_df)} "
#                                 f"sample_date={fmp_revenue_df['date'][:3].tolist()}")
#             fmp_revenue_df = fmp_revenue_df.dropna(subset=['date_month_end'])
#             fmp_revenue_df = fmp_revenue_df.drop_duplicates(subset=['date_month_end']).sort_values(
#                 'date_month_end').reset_index(drop=True)
#         except Exception as e:
#             log("EXC-FMP-REV", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_revenue_preproc', 'error': str(e)})
#             continue
#
#         # 2) DB 매출
#         try:
#             db_revenue_raw = fetch_db_revenue_data(ticker, db_info)
#             rows_db = 0 if db_revenue_raw is None else len(db_revenue_raw)
#             log("OK-DB-REV", f"{ticker} rows={rows_db}")
#             if rows_db:
#                 db_revenue_df = db_revenue_raw.loc[
#                     db_revenue_raw['revenue_billions'] != db_revenue_raw['revenue_billions'].shift()
#                     ]
#             else:
#                 db_revenue_df = pd.DataFrame(columns=['ticker', 'date', 'date_month_end', 'revenue_billions'])
#
#             mereged_rev_data = pd.merge(fmp_revenue_df, db_revenue_df, on=['ticker', 'date_month_end'], how='outer')
#             rev_data = mereged_rev_data[mereged_rev_data['date_month_end'] >= start_date_month]
#             if 'revenue_billions_x' in rev_data.columns:
#                 rev_data['revenue_billions_x'] = rev_data['revenue_billions_x'].fillna(
#                     rev_data.get('revenue_billions_y'))
#                 rev_data = rev_data.rename(columns={'revenue_billions_x': 'revenue_billions'})
#             log("REV-MERGE", f"{ticker} merged_rows={len(rev_data)} cols={list(rev_data.columns)}")
#
#             rev_data = clean_rev_data(rev_data)
#             yr_min = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].min()
#             yr_max = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].max()
#             log("OK-REV-CLEAN", f"{ticker} rows={len(rev_data)} yrmin={yr_min} yrmax={yr_max}")
#         except Exception as e:
#             log("EXC-DB-REV", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'db_revenue_merge_clean', 'error': str(e)})
#             continue
#
#         # 3) 매출 예측
#         try:
#             periods = 4
#             sarima_df, _ = sarima.run_sarima_prediction(rev_data, forecast_quarters=periods, exog_col=None)
#             sarima_df = sarima_df.sort_values("date_month_end").set_index("date_month_end")
#             lstm_raw_df, _ = lstm_v2.run_lstm_revenue_prediction(rev_data, ticker=ticker, prediction_quarters=4)
#             lstm_df = lstm_raw_df.drop_duplicates(subset=['revenue_billions_lstm_forecast'], keep='last')
#             prophet_raw_df, _ = prophet_v3.run_prophet_revenue_only(rev_data, ticker=ticker, prediction_quarters=4)
#             es_raw_df, _ = esmod.run_es_revenue_quarterly(rev_data, ticker=ticker, prediction_quarters=4)
#             log("OK-REV-FORECAST",
#                 f"{ticker} sarima={sarima_df.shape} lstm={lstm_df.shape} prophet={prophet_raw_df.shape} es={es_raw_df.shape}")
#         except Exception as e:
#             log("EXC-REV-FORECAST", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_forecast', 'error': str(e)})
#             continue
#
#         # 4) FMP 시총
#         try:
#             market_data, _ = fetch_market_data_yearly(ticker, api_key, start_year=2010)
#             if not market_data:
#                 msg = "FMP market data fetch failed"
#                 log("ERR-FMP-MCAP", f"{ticker} {msg}")
#                 error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_market', 'error': msg})
#                 continue
#             fmp_market_df = process_daily_to_monthly_market_data(market_data, ticker).copy()
#             fmp_market_df['date_month_end'] =  to_month_end_safe(fmp_market_df['date'])
#             fmp_market_df = fmp_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
#                 'date_month_end').reset_index(drop=True)
#             log("OK-FMP-MCAP",
#                 f"{ticker} rows={len(fmp_market_df)} range={fmp_market_df['date_month_end'].min()}~{fmp_market_df['date_month_end'].max()}")
#         except Exception as e:
#             log("EXC-FMP-MCAP", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_market_preproc', 'error': str(e)})
#             continue
#
#         # 5) DB 시총 병합
#         try:
#             db_market_df = _safe_get_db_market_df(ticker, db_info)
#             if (not db_market_df.empty) and ('date_month_end' not in db_market_df.columns):
#                 if 'date' in db_market_df.columns:
#                     db_market_df['date_month_end'] = to_month_end_safe(db_market_df['date'])
#                 else:
#                     db_market_df = pd.DataFrame()
#
#             if db_market_df.empty:
#                 merged_market_df = fmp_market_df.copy()
#                 merged_market_df['market_cap_billions_from_db'] = np.nan
#             else:
#                 if 'market_cap_billions' in db_market_df.columns:
#                     db_market_df_renamed = db_market_df.rename(
#                         columns={'market_cap_billions': 'market_cap_billions_from_db'})
#                 else:
#                     db_market_df_renamed = db_market_df[['date_month_end']].copy()
#                     db_market_df_renamed['market_cap_billions_from_db'] = np.nan
#                 merged_market_df = fmp_market_df.merge(
#                     db_market_df_renamed[['date_month_end', 'market_cap_billions_from_db']],
#                     on='date_month_end', how='left'
#                 )
#
#             if 'market_cap_billions' not in merged_market_df.columns:
#                 merged_market_df['market_cap_billions'] = np.nan
#             if 'market_cap_billions_from_db' not in merged_market_df.columns:
#                 merged_market_df['market_cap_billions_from_db'] = np.nan
#
#             merged_market_df['market_cap_billions'] = merged_market_df['market_cap_billions'].fillna(
#                 merged_market_df['market_cap_billions_from_db']
#             )
#             merged_market_df = merged_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
#                 'date_month_end').reset_index(drop=True)
#             log("OK-MCAP-MERGE",
#                 f"{ticker} rows={len(merged_market_df)} nan_mcap={merged_market_df['market_cap_billions'].isna().sum()}")
#         except Exception as e:
#             log("EXC-MCAP-MERGE", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'market_merge', 'error': str(e)})
#             continue
#
#         # 6) PSR 계산/예측
#         try:
#             enhanced_merged_df = pd.merge(
#                 merged_market_df[['date_month_end', 'market_cap_billions']],
#                 rev_data, on='date_month_end', how='outer'
#             )
#             market_cap_resize = enhanced_merged_df[
#                 ['date_month_end', 'market_cap_billions', 'ticker', 'revenue_billions']].copy()
#             market_cap_resize.dropna(subset=['market_cap_billions'], inplace=True)
#             market_cap_resize.ffill(limit=2, inplace=True)
#             market_cap_resize = market_cap_resize[(market_cap_resize['date_month_end'] >= start_date_month) & (
#                         market_cap_resize['date_month_end'] <= end_date_month)]
#             market_cap_resize = market_cap_resize.dropna(axis=0)
#             log("OK-PSR-PREP", f"{ticker} rows={len(market_cap_resize)}")
#
#             enhanced_merged_df_with_ttm = calculate_enhanced_ttm_and_psr(market_cap_resize)
#             psr_ok = enhanced_merged_df_with_ttm[['date_month_end', 'PSR_ttm']].dropna()
#             if psr_ok.empty or psr_ok['PSR_ttm'].count() < 6:
#                 msg = "PSR series too short after TTM shift"
#                 log("ERR-PSR-SHORT", f"{ticker} {msg}")
#                 error_ticker_list.append({'ticker': ticker, 'stage': 'psr_prepare', 'error': msg})
#                 continue
#
#             psr_sarima_df, _ = sarima.run_sarima_psr_only(
#                 df=enhanced_merged_df_with_ttm,
#                 periods=12,
#                 target_col="PSR_ttm",
#                 analysis_start="2012-06-01",
#                 warmup_months=6,
#                 fill_method="interpolate",
#                 ic="aic"
#             )
#             psr_lstm_df, _ = lstm_v2.run_lstm_psr_prediction(enhanced_merged_df_with_ttm, ticker=ticker,
#                                                              prediction_months=12)
#             psr_prophet_df, _ = prophet_v3.run_prophet_psr_only(enhanced_merged_df_with_ttm, ticker=ticker,
#                                                                 prediction_months=12)
#             psr_es_df, _ = esmod.run_es_psr_only(df=enhanced_merged_df_with_ttm, ticker=ticker, prediction_months=12,
#                                                  start_date=None)
#             log("OK-PSR-FORECAST",
#                 f"{ticker} sarima={psr_sarima_df.shape} lstm={psr_lstm_df.shape} prophet={psr_prophet_df.shape} es={psr_es_df.shape}")
#         except Exception as e:
#             log("EXC-PSR-FORECAST", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'psr_forecast', 'error': str(e)})
#             continue
#
#         # 7) Valuation 종합  (수정 버전: 날짜 인덱스 정렬 + inner join)
#         try:
#             # ---- (1) 각 예측 결과에 date_month_end 붙이고 인덱스로 정렬 ----
#             def _pick(df, cols):
#                 # date_month_end 보장 + 중복 제거
#                 d = df.copy()
#                 if 'date_month_end' not in d.columns:
#                     # 가능하면 인덱스가 날짜인 경우를 대비
#                     d = d.reset_index()
#                     if 'date_month_end' not in d.columns and 'index' in d.columns:
#                         d = d.rename(columns={'index': 'date_month_end'})
#                 d = d.drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')
#                 return d[['date_month_end'] + cols].set_index('date_month_end')
#
#
#             rev_sarima = _pick(sarima_df, ['revenue_billions_sarima_noexog'])
#             rev_lstm = _pick(lstm_df, ['revenue_billions_lstm_forecast'])
#             rev_prophet = _pick(prophet_raw_df, ['revenue_billions_prophet_forecast'])
#             rev_es = _pick(es_raw_df, ['revenue_billions_esq_forecast'])
#
#             # ---- (2) 4개 revenue 예측 wide ----
#             revenue_forecast_df = pd.concat([rev_sarima, rev_lstm, rev_prophet, rev_es], axis=1, join='outer')
#
#             # ---- (3) TTM 변환 + 평균 (함수는 date_month_end 컬럼 필요하므로 reset_index) ----
#             revenue_forecast_df_reset = revenue_forecast_df.reset_index()  # date_month_end 컬럼 생성
#             # ✅ 여기 두 줄 추가!
#             if 'ticker' not in revenue_forecast_df_reset.columns:
#                 revenue_forecast_df_reset['ticker'] = ticker
#
#             revenue_forecast_ = prepare_revenue_ttm(revenue_forecast_df_reset)
#             # prepare_revenue_ttm 결과에서 TTM 컬럼만 추출
#             revenue_forecast_ttm = revenue_forecast_.filter(like='_ttm').copy()
#             # 날짜 복원 후 인덱스 설정
#
#             if 'ticker' not in revenue_forecast_df_reset.columns or revenue_forecast_df_reset['ticker'].isna().all():
#                 raise ValueError("ticker 칼럼이 필요합니다. (Valuation pack 직전)")
#
#             if 'date_month_end' not in revenue_forecast_.columns:
#                 # prepare_revenue_ttm 내부에서 날짜를 보존하지 않는 경우 대비
#                 revenue_forecast_ttm['date_month_end'] = revenue_forecast_df_reset['date_month_end'].values
#             else:
#                 revenue_forecast_ttm['date_month_end'] = revenue_forecast_['date_month_end'].values
#             revenue_forecast_ttm = revenue_forecast_ttm.drop_duplicates(subset=['date_month_end']).set_index(
#                 'date_month_end')
#
#             # 평균 TTM
#             revenue_cols_ttm = [
#                 'revenue_billions_sarima_noexog_ttm',
#                 'revenue_billions_lstm_forecast_ttm',
#                 'revenue_billions_prophet_forecast_ttm',
#                 'revenue_billions_esq_forecast_ttm'
#             ]
#             revenue_forecast_ttm['revenue_billions_avg_of_4_ttm'] = revenue_forecast_ttm[revenue_cols_ttm].mean(axis=1)
#
#             # ---- (4) PSR 예측도 날짜 인덱스 정렬 ----
#             psr_sarima = _pick(psr_sarima_df, ['PSR_ttm_sarima_forecast'])
#             psr_lstm = _pick(psr_lstm_df, ['PSR_ttm_lstm_forecast'])
#             psr_prophet = _pick(psr_prophet_df, ['PSR_prophet_forecast_noexog'])
#             psr_es = _pick(psr_es_df, ['PSR_es_forecast'])
#             psr_forecast_df = pd.concat([psr_sarima, psr_lstm, psr_prophet, psr_es], axis=1, join='outer')
#
#             # ---- (5) 날짜 기준 inner-join → 공통 구간만 결합 ----
#             valuation_df = revenue_forecast_ttm.join(psr_forecast_df, how='inner')
#             valuation_df['ticker'] = ticker  # 티커 부여
#
#             # ---- (6) ffill (ticker + revenue 계열만) ----
#             valuation_filled = valuation_df.copy()
#             cols_to_ffill = ['ticker'] + [c for c in valuation_filled.columns if 'revenue_billions' in c]
#             valuation_filled[cols_to_ffill] = valuation_filled[cols_to_ffill].ffill(limit=2)
#
#             # ---- (7) Valuation 계산 ----
#             valuation_filled['sarima_valuation'] = valuation_filled['revenue_billions_sarima_noexog_ttm'] * \
#                                                    valuation_filled['PSR_ttm_sarima_forecast']
#             valuation_filled['lstm_valuation'] = valuation_filled['revenue_billions_lstm_forecast_ttm'] * \
#                                                  valuation_filled['PSR_ttm_lstm_forecast']
#             valuation_filled['prophet_valuation'] = valuation_filled['revenue_billions_prophet_forecast_ttm'] * \
#                                                     valuation_filled['PSR_prophet_forecast_noexog']
#             valuation_filled['es_valuation'] = valuation_filled['revenue_billions_esq_forecast_ttm'] * valuation_filled[
#                 'PSR_es_forecast']
#
#             # ---- (8) 최신 15개만 ----
#             valuation_filled = valuation_filled.sort_index()
#             valuation_result = (
#                 valuation_filled
#                 .groupby('ticker', group_keys=False)
#                 .apply(lambda d: d.tail(15))
#                 .reset_index()
#                 .rename(columns={'index': 'date_month_end'})
#             )
#
#             batch_results.append(valuation_result)
#             total_success_tickers += 1
#             log("OK-VAL-PACK", f"{ticker} packed={len(valuation_result)} batch={len(batch_results)}")
#
#         except Exception as e:
#             log("EXC-VAL-PACK", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'valuation_pack', 'error': str(e)})
#             continue
#
#         # 9) 배치 업로드  (복구)
#         try:
#             is_last = (idx == len(target_tickers))
#             if (len(batch_results) >= BATCH_SIZE) or is_last:
#                 log("BATCH-FLUSH", f"size={len(batch_results)} is_last={is_last}")
#                 _flush_batch_and_upload(batch_results, db_info)
#         except Exception as e:
#             log("EXC-BATCH-FLUSH", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
#             error_ticker_list.append({'ticker': ticker, 'stage': 'batch_flush', 'error': str(e)})
#
#
#         # 10) 메모리 정리
#         try:
#             del (revenue_data, fmp_revenue_df, db_revenue_raw, db_revenue_df, mereged_rev_data,
#                  rev_data, sarima_df, lstm_raw_df, lstm_df, prophet_raw_df, es_raw_df,
#                  market_data, fmp_market_df, db_market_df, merged_market_df, enhanced_merged_df,
#                  market_cap_resize, enhanced_merged_df_with_ttm, psr_sarima_df, psr_lstm_df,
#                  psr_prophet_df, psr_es_df, revenue_forecast_df, revenue_forecast_, revenue_forecast_ttm,
#                  valuation_df, valuation_filled, valuation_result)
#             gc.collect()
#         except Exception as e:
#             log("EXC-GC", f"{ticker} e={e}")
#
#
#     # 요약
#     print(f"[DONE] 성공 ticker: {total_success_tickers}, 업서트 rows: {total_upsert_rows}")
#     if error_ticker_list:
#         try:
#             pd.DataFrame(error_ticker_list).to_csv("valuation_error_list.csv", index=False, encoding="utf-8-sig")
#             print(f"[INFO] 오류 리스트 저장: valuation_error_list.csv (총 {len(error_ticker_list)}개)")
#         except Exception:
#             print(f"[WARN] 오류 리스트 저장 실패 (총 {len(error_ticker_list)}개)")
#     else:
#         print("[INFO] 오류 없이 완료")


