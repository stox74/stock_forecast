"""
Step 3: 일별 주가 × 유동주식수로 일별 시가총액 계산
"""

import pandas as pd
import pymysql
from tqdm import tqdm
from DATA.stock_invest_function import get_db_host

def create_market_cap_table(db_config):
    """시가총액 데이터 저장 테이블 생성"""
    connection = pymysql.connect(**db_config)
    
    try:
        with connection.cursor() as cursor:
            create_table_query = """
            CREATE TABLE IF NOT EXISTS us_stock_daily_market_cap (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ticker VARCHAR(20) NOT NULL,
                date DATE NOT NULL,
                close_price DOUBLE,
                shares_outstanding BIGINT,
                market_cap DOUBLE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_ticker_date (ticker, date),
                INDEX idx_ticker (ticker),
                INDEX idx_date (date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
            cursor.execute(create_table_query)
            connection.commit()
            print("시가총액 테이블 생성 완료: us_stock_daily_market_cap")
    finally:
        connection.close()

def get_ticker_list(db_config):
    """주가 데이터가 있는 ticker 리스트 조회"""
    connection = pymysql.connect(**db_config)
    
    try:
        query = "SELECT DISTINCT ticker FROM us_stock_daily_price ORDER BY ticker"
        df = pd.read_sql(query, connection)
        return df['ticker'].tolist()
    finally:
        connection.close()

def calculate_market_cap_for_ticker(ticker, db_config):
    """특정 ticker의 일별 시가총액 계산"""
    connection = pymysql.connect(**db_config)
    
    try:
        # 1. 일별 주가 데이터 조회
        price_query = """
        SELECT date, close_price
        FROM us_stock_daily_price
        WHERE ticker = %s
        ORDER BY date
        """
        df_price = pd.read_sql(price_query, connection, params=(ticker,))
        
        if df_price.empty:
            return None
        
        df_price['date'] = pd.to_datetime(df_price['date'])
        
        # 2. 유동주식수 데이터 조회
        shares_query = """
        SELECT date, shares_outstanding
        FROM us_stock_shares_outstanding
        WHERE ticker = %s
        ORDER BY date
        """
        df_shares = pd.read_sql(shares_query, connection, params=(ticker,))
        
        if df_shares.empty:
            return None
        
        df_shares['date'] = pd.to_datetime(df_shares['date'])
        
        # 3. 두 데이터 병합 (asof merge - 가장 가까운 과거 유동주식수 사용)
        df_price = df_price.sort_values('date')
        df_shares = df_shares.sort_values('date')
        
        # pandas merge_asof: 각 일별 주가에 가장 가까운 과거 분기 유동주식수 매칭
        df_merged = pd.merge_asof(
            df_price,
            df_shares,
            on='date',
            direction='backward'  # 과거 방향으로 매칭
        )
        
        # 4. 시가총액 계산 (일별 주가 × 유동주식수)
        df_merged['market_cap'] = df_merged['close_price'] * df_merged['shares_outstanding']
        
        # 결측값 제거
        df_merged = df_merged.dropna()
        
        return df_merged
        
    except Exception as e:
        print(f"\n{ticker} 시가총액 계산 실패: {str(e)}")
        return None
    finally:
        connection.close()

def save_market_cap_data(ticker, df_data, db_config):
    """시가총액 데이터를 데이터베이스에 저장"""
    if df_data is None or len(df_data) == 0:
        return 0
    
    connection = pymysql.connect(**db_config)
    inserted_count = 0
    
    try:
        with connection.cursor() as cursor:
            for _, row in df_data.iterrows():
                insert_query = """
                INSERT INTO us_stock_daily_market_cap 
                (ticker, date, close_price, shares_outstanding, market_cap)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    close_price = VALUES(close_price),
                    shares_outstanding = VALUES(shares_outstanding),
                    market_cap = VALUES(market_cap)
                """
                cursor.execute(insert_query, (
                    ticker,
                    row['date'].strftime('%Y-%m-%d'),
                    float(row['close_price']) if pd.notna(row['close_price']) else None,
                    int(row['shares_outstanding']) if pd.notna(row['shares_outstanding']) else None,
                    float(row['market_cap']) if pd.notna(row['market_cap']) else None
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

def calculate_all_market_cap(db_config, test_mode=False, test_count=10):
    """모든 ticker의 일별 시가총액 계산 메인 함수"""
    
    print("=" * 80)
    print("[Step 3] 일별 시가총액 계산 (일별 주가 × 유동주식수)")
    print("=" * 80)
    
    create_market_cap_table(db_config)
    
    # ticker 리스트 조회
    ticker_list = get_ticker_list(db_config)
    
    # 테스트 모드 설정
    if test_mode:
        tickers_to_process = ticker_list[:test_count]
        mode_text = f"테스트 모드 - {test_count}개"
    else:
        tickers_to_process = ticker_list
        mode_text = "전체 계산"
    
    print(f"모드: {mode_text}")
    print(f"총 Ticker 수: {len(tickers_to_process)}")
    print(f"저장 위치: {db_config['database']}.us_stock_daily_market_cap")
    print("=" * 80)
    
    total_inserted = 0
    success_count = 0
    fail_count = 0
    
    print("\n시가총액 계산 시작...\n")
    
    for ticker in tqdm(tickers_to_process, desc="진행 상황", unit="ticker"):
        df_market_cap = calculate_market_cap_for_ticker(ticker, db_config)
        
        if df_market_cap is not None and len(df_market_cap) > 0:
            inserted = save_market_cap_data(ticker, df_market_cap, db_config)
            total_inserted += inserted
            success_count += 1
        else:
            fail_count += 1
    
    print("\n" + "=" * 80)
    print("[Step 3] 시가총액 계산 완료")
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
    DB_CONFIG = {
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'host': get_db_host(),
        'port': 3307,
        'database': 'investar'
    }
    
    result = calculate_all_market_cap(
        db_config=DB_CONFIG,
        test_mode=True,
        test_count=10
    )
    
    print(f"\n최종 결과: {result}")
