"""
Step 4: 데이터 검증 및 요약 통계
"""

import pandas as pd
import pymysql
from DATA.stock_invest_function import get_db_host

def validate_and_summarize(db_config):
    """데이터 검증 및 요약 통계"""
    
    print("=" * 80)
    print("[Step 4] 데이터 검증 및 요약")
    print("=" * 80)
    
    connection = pymysql.connect(**db_config)
    
    try:
        # 1. 주가 데이터 통계
        print("\n[1] 주가 데이터 (us_stock_daily_price)")
        print("-" * 80)
        
        query = """
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT ticker) as unique_tickers,
            MIN(date) as start_date,
            MAX(date) as end_date
        FROM us_stock_daily_price
        """
        df = pd.read_sql(query, connection)
        print(df.to_string(index=False))
        
        # 2. 유동주식수 데이터 통계
        print("\n[2] 유동주식수 데이터 (us_stock_shares_outstanding)")
        print("-" * 80)
        
        query = """
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT ticker) as unique_tickers,
            MIN(date) as start_date,
            MAX(date) as end_date
        FROM us_stock_shares_outstanding
        """
        df = pd.read_sql(query, connection)
        print(df.to_string(index=False))
        
        # 3. 시가총액 데이터 통계
        print("\n[3] 시가총액 데이터 (us_stock_daily_market_cap)")
        print("-" * 80)
        
        query = """
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT ticker) as unique_tickers,
            MIN(date) as start_date,
            MAX(date) as end_date,
            AVG(market_cap) as avg_market_cap,
            MIN(market_cap) as min_market_cap,
            MAX(market_cap) as max_market_cap
        FROM us_stock_daily_market_cap
        """
        df = pd.read_sql(query, connection)
        print(df.to_string(index=False))
        
        # 4. 샘플 데이터 (상위 5개 ticker)
        print("\n[4] 샘플 시가총액 데이터 (최근 데이터)")
        print("-" * 80)
        
        query = """
        SELECT 
            ticker,
            date,
            close_price,
            shares_outstanding,
            ROUND(market_cap / 1e9, 2) as market_cap_billions
        FROM us_stock_daily_market_cap
        WHERE date = (SELECT MAX(date) FROM us_stock_daily_market_cap)
        ORDER BY market_cap DESC
        LIMIT 10
        """
        df = pd.read_sql(query, connection)
        print(df.to_string(index=False))
        
        # 5. ticker별 데이터 건수
        print("\n[5] Ticker별 시가총액 데이터 건수 (상위 10개)")
        print("-" * 80)
        
        query = """
        SELECT 
            ticker,
            COUNT(*) as record_count,
            MIN(date) as first_date,
            MAX(date) as last_date
        FROM us_stock_daily_market_cap
        GROUP BY ticker
        ORDER BY record_count DESC
        LIMIT 10
        """
        df = pd.read_sql(query, connection)
        print(df.to_string(index=False))
        
        # 6. 데이터 무결성 체크
        print("\n[6] 데이터 무결성 체크")
        print("-" * 80)
        
        # 주가는 있지만 유동주식수가 없는 ticker
        query = """
        SELECT DISTINCT p.ticker
        FROM us_stock_daily_price p
        LEFT JOIN us_stock_shares_outstanding s ON p.ticker = s.ticker
        WHERE s.ticker IS NULL
        ORDER BY p.ticker
        LIMIT 10
        """
        df = pd.read_sql(query, connection)
        if len(df) > 0:
            print(f"주가는 있지만 유동주식수가 없는 ticker: {len(df)}개")
            print(df['ticker'].tolist())
        else:
            print("모든 ticker에 유동주식수 데이터 존재 ✓")
        
        # 유동주식수는 있지만 주가가 없는 ticker
        query = """
        SELECT DISTINCT s.ticker
        FROM us_stock_shares_outstanding s
        LEFT JOIN us_stock_daily_price p ON s.ticker = p.ticker
        WHERE p.ticker IS NULL
        ORDER BY s.ticker
        LIMIT 10
        """
        df = pd.read_sql(query, connection)
        if len(df) > 0:
            print(f"\n유동주식수는 있지만 주가가 없는 ticker: {len(df)}개")
            print(df['ticker'].tolist())
        else:
            print("모든 ticker에 주가 데이터 존재 ✓")
        
        print("\n" + "=" * 80)
        print("[Step 4] 데이터 검증 완료")
        print("=" * 80)
        
    finally:
        connection.close()

if __name__ == "__main__":
    DB_CONFIG = {
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'host': get_db_host(),
        'port': 3307,
        'database': 'investar'
    }
    
    validate_and_summarize(DB_CONFIG)
