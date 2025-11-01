# -*- coding: utf-8 -*-

import pandas as pd
from sqlalchemy import create_engine, text
from utils import log

def audit_db_coverage(db_info, tickers):
    """데이터베이스에서 ticker 커버리지를 감사"""
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
    log("AUDIT", f"US_fundq missing: {len(miss_q)} tickers")
    log("AUDIT", f"US_fundm missing: {len(miss_m)} tickers")
    return miss_q, miss_m

def smoke_test_db_tables(db_info, ticker: str):
    """데이터베이스 테이블 연결 및 데이터 존재 확인"""
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
                    WHERE TRIM(ticker)=:ticker AND saleq IS NOT NULL
                    ORDER BY date DESC LIMIT 5;"""),
            conn, params={"ticker": ticker}
        )
        log("SMOKE", f"latest US_fundq rows for {ticker}:\n{head}")

    eng.dispose()

def diag_revenue_and_mcap_gaps(db_info, ticker: str):
    """매출 및 시가총액 데이터 진단"""
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
                      OR TRIM(ticker)=:t
                      OR UPPER(TRIM(ticker))= UPPER(:t)
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
