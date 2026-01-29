"""
Step 2: FMP에서 분기별 유동주식수 데이터 수집
"""

import requests
import pandas as pd
import pymysql
from tqdm import tqdm
import time
from DATA.stock_invest_function import get_db_host

def create_shares_table(db_config):
    """유동주식수 데이터 저장 테이블 생성"""
    connection = pymysql.connect(**db_config)
    
    try:
        with connection.cursor() as cursor:
            create_table_query = """
            CREATE TABLE IF NOT EXISTS us_stock_shares_outstanding (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                date DATE NOT NULL,
                shares_outstanding BIGINT,
                period VARCHAR(10),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_ticker_date (ticker, date),
                INDEX idx_ticker (ticker),
                INDEX idx_date (date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
            cursor.execute(create_table_query)
            connection.commit()
            print("유동주식수 테이블 생성 완료: us_stock_shares_outstanding")
    finally:
        connection.close()

def get_shares_outstanding_data(ticker, api_key, start_date):
    """FMP에서 분기별 유동주식수 데이터 수집"""
    try:
        # Key Metrics (분기별) API 사용
        url = f"https://financialmodelingprep.com/api/v3/key-metrics/{ticker}"
        params = {
            'apikey': api_key,
            'period': 'quarter',
            'limit': 120  # 최근 30년치 (분기 기준)
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if not data or len(data) == 0:
            return None
        
        # 데이터프레임 생성
        df = pd.DataFrame(data)
        
        # 필요한 컬럼 확인
        if 'date' not in df.columns or 'weightedAverageShsOut' not in df.columns:
            # 대안: enterprise-values API 시도
            return get_shares_from_enterprise_values(ticker, api_key, start_date)
        
        df['date'] = pd.to_datetime(df['date'])
        
        # 시작 날짜 이후 데이터만 필터링
        start_dt = pd.to_datetime(start_date)
        df = df[df['date'] >= start_dt]
        
        if df.empty:
            return None
        
        # 데이터 정리
        result_df = pd.DataFrame({
            'date': df['date'],
            'shares_outstanding': df['weightedAverageShsOut'],
            'period': df['period'] if 'period' in df.columns else 'Q'
        })
        
        result_df = result_df.dropna()
        result_df = result_df.sort_values('date')
        
        return result_df
        
    except Exception as e:
        print(f"\n{ticker} 유동주식수 수집 실패: {str(e)}")
        return None

def get_shares_from_enterprise_values(ticker, api_key, start_date):
    """대안: enterprise-values API로 유동주식수 수집"""
    try:
        url = f"https://financialmodelingprep.com/api/v3/enterprise-values/{ticker}"
        params = {
            'apikey': api_key,
            'period': 'quarter',
            'limit': 120
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if not data or len(data) == 0:
            return None
        
        df = pd.DataFrame(data)
        
        if 'date' not in df.columns or 'numberOfShares' not in df.columns:
            return None
        
        df['date'] = pd.to_datetime(df['date'])
        start_dt = pd.to_datetime(start_date)
        df = df[df['date'] >= start_dt]
        
        if df.empty:
            return None
        
        result_df = pd.DataFrame({
            'date': df['date'],
            'shares_outstanding': df['numberOfShares'],
            'period': 'Q'
        })
        
        result_df = result_df.dropna()
        result_df = result_df.sort_values('date')
        
        return result_df
        
    except:
        return None

def save_shares_data(ticker, df_data, db_config):
    """유동주식수 데이터를 데이터베이스에 저장"""
    if df_data is None or len(df_data) == 0:
        return 0
    
    connection = pymysql.connect(**db_config)
    inserted_count = 0
    
    try:
        with connection.cursor() as cursor:
            for _, row in df_data.iterrows():
                insert_query = """
                INSERT INTO us_stock_shares_outstanding (ticker, date, shares_outstanding, period)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    shares_outstanding = VALUES(shares_outstanding),
                    period = VALUES(period)
                """
                cursor.execute(insert_query, (
                    ticker,
                    row['date'].strftime('%Y-%m-%d'),
                    int(row['shares_outstanding']) if pd.notna(row['shares_outstanding']) else None,
                    row['period'] if pd.notna(row['period']) else 'Q'
                ))
                inserted_count += 1
            
            connection.commit()
    except Exception as e:
        print(f"\n{ticker} DB 저장 실패: {str(e)}")
        connection.rollback()
        inserted_count = 0
    finally:
        connection.close()
    
    return inserted_count

def collect_shares_data(ticker_list, api_key, start_date, db_config, test_mode=False, test_count=10):
    """유동주식수 데이터 수집 메인 함수"""
    
    # 테스트 모드 설정
    if test_mode:
        tickers_to_process = ticker_list[:test_count]
        mode_text = f"테스트 모드 - {test_count}개"
    else:
        tickers_to_process = ticker_list
        mode_text = "전체 수집"
    
    print("=" * 80)
    print("[Step 2] FMP 유동주식수 데이터 수집")
    print("=" * 80)
    print(f"모드: {mode_text}")
    print(f"총 Ticker 수: {len(tickers_to_process)}")
    print(f"수집 시작일: {start_date}")
    print(f"저장 위치: {db_config['database']}.us_stock_shares_outstanding")
    print("=" * 80)
    
    create_shares_table(db_config)
    
    total_inserted = 0
    success_count = 0
    fail_count = 0
    
    print("\n데이터 수집 시작...\n")
    
    for ticker in tqdm(tickers_to_process, desc="진행 상황", unit="ticker"):
        df_shares = get_shares_outstanding_data(ticker, api_key, start_date)
        
        if df_shares is not None and len(df_shares) > 0:
            inserted = save_shares_data(ticker, df_shares, db_config)
            total_inserted += inserted
            success_count += 1
        else:
            fail_count += 1
        
        time.sleep(0.2)  # API 부하 방지
    
    print("\n" + "=" * 80)
    print("[Step 2] 유동주식수 데이터 수집 완료")
    print("=" * 80)
    print(f"성공: {success_count} ticker")
    print(f"실패: {fail_count} ticker")
    print(f"총 저장 레코드 수: {total_inserted:,}")
    print("=" * 80)
    
    return {
        'success': success_count,
        'fail': fail_count,
        'total_records': total_inserted
    }

if __name__ == "__main__":
    from DATA.us_target_ticker_list_2000 import ticker_list
    
    DB_CONFIG = {
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'host': get_db_host(),
        'port': 3307,
        'database': 'investar'
    }
    
    FMP_API_KEY = 'rAR7gF6c5ctPrpCKylqVTpxyGw6QFRrp'
    START_DATE = '2015-01-01'
    
    result = collect_shares_data(
        ticker_list=ticker_list,
        api_key=FMP_API_KEY,
        start_date=START_DATE,
        db_config=DB_CONFIG,
        test_mode=True,
        test_count=10
    )
    
    print(f"\n최종 결과: {result}")
