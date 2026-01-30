import pandas as pd
import pymysql
import yfinance as yf
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import warnings
import time

warnings.filterwarnings('ignore')

from DATA.stock_invest_function import get_db_host
from DATA.us_target_ticker_list_2000 import ticker_list

# 데이터베이스 연결 정보
DB_CONFIG = {
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'host': get_db_host(),
    'port': 3307,
    'database': 'investar'
}


def get_last_collection_date(connection, ticker: str = 'NVDA', indicator: str = 'close_price') -> Optional[datetime]:
    """
    특정 ticker와 indicator의 가장 최근 수집 날짜 조회

    Parameters:
    -----------
    connection : pymysql.Connection
        DB 연결 객체
    ticker : str
        조회할 티커 (기본값: 'NVDA')
    indicator : str
        조회할 지표 (기본값: 'close_price')

    Returns:
    --------
    datetime or None
        가장 최근 수집 날짜
    """
    try:
        query = """
                SELECT MAX(date) as last_date
                FROM us_stock_daily_market_cap
                WHERE ticker = %s \
                  AND indicator = %s \
                """

        with connection.cursor() as cursor:
            cursor.execute(query, (ticker, indicator))
            result = cursor.fetchone()

            if result and result[0]:
                # date 타입을 datetime으로 변환
                last_date = result[0]
                if isinstance(last_date, datetime):
                    return last_date
                else:
                    # date 객체인 경우 datetime으로 변환
                    return datetime.combine(last_date, datetime.min.time())
            else:
                return None

    except Exception as e:
        print(f"Error getting last collection date: {str(e)}")
        return None


def get_collection_date_range() -> Tuple[datetime, datetime]:
    """
    데이터 수집 기간 결정

    Returns:
    --------
    Tuple[datetime, datetime]
        (시작일, 종료일)
    """
    try:
        connection = pymysql.connect(**DB_CONFIG, charset='utf8mb4')

        # NVDA 티커의 마지막 수집일 조회
        last_date = get_last_collection_date(connection, ticker='NVDA', indicator='close_price')
        connection.close()

        # 종료일은 오늘의 전일 (시간 정보 제거)
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

        if last_date:
            # 마지막 수집일의 다음날부터
            start_date = last_date.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            print(f"마지막 수집일: {last_date.strftime('%Y-%m-%d')}")
        else:
            # 데이터가 없으면 2015년부터
            start_date = datetime(2015, 1, 1)
            print("기존 데이터 없음. 2015-01-01부터 수집")

        print(f"수집 기간: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

        return start_date, end_date

    except Exception as e:
        print(f"Error determining date range: {str(e)}")
        # 오류 발생 시 최근 30일 데이터 수집
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        start_date = end_date - timedelta(days=30)
        return start_date, end_date


def fetch_stock_data(ticker: str, start_date: datetime, end_date: datetime) -> Optional[pd.DataFrame]:
    """
    Yahoo Finance에서 주가 데이터 다운로드

    Parameters:
    -----------
    ticker : str
        종목 티커
    start_date : datetime
        시작일
    end_date : datetime
        종료일

    Returns:
    --------
    pd.DataFrame or None
        주가 데이터
    """
    try:
        # Yahoo Finance에서 데이터 다운로드
        stock = yf.Ticker(ticker)
        df = stock.history(
            start=start_date.strftime('%Y-%m-%d'),
            end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d')
        )

        if df.empty:
            return None

        # 인덱스를 date 컬럼으로 변환
        df.reset_index(inplace=True)

        # Date 컬럼 처리
        if 'Date' in df.columns:
            # timezone 정보 완전히 제거하고 날짜만 추출
            df['date'] = pd.to_datetime(df['Date']).dt.date
            df.drop('Date', axis=1, inplace=True)
        elif 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.date

        return df

    except Exception as e:
        print(f"Error fetching data for {ticker}: {str(e)}")
        return None


def prepare_data_for_insert(ticker: str, df: pd.DataFrame, source: str = 'yf_price_stock') -> pd.DataFrame:
    """
    DB 저장 형식에 맞게 데이터 변환

    Parameters:
    -----------
    ticker : str
        종목 티커
    df : pd.DataFrame
        Yahoo Finance 데이터
    source : str
        데이터 소스

    Returns:
    --------
    pd.DataFrame
        DB 저장용 데이터프레임
    """
    records = []
    created_at = datetime.now()

    for _, row in df.iterrows():
        # Close 가격
        records.append({
            'date': row['date'],  # 이미 date 타입
            'ticker': ticker,
            'indicator': 'close_price',
            'value': float(row['Close']),
            'source': source,
            'created_at': created_at
        })

    return pd.DataFrame(records)


def insert_stock_data(connection, df: pd.DataFrame) -> int:
    """
    DB에 주가 데이터 저장

    Parameters:
    -----------
    connection : pymysql.Connection
        DB 연결 객체
    df : pd.DataFrame
        저장할 데이터

    Returns:
    --------
    int
        저장된 레코드 수
    """
    try:
        insert_query = """
                       INSERT INTO us_stock_daily_market_cap
                           (date, ticker, indicator, value, source, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY \
                       UPDATE \
                           value = \
                       VALUES (value), source = \
                       VALUES (source), created_at = \
                       VALUES (created_at) \
                       """

        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                # date를 문자열로 변환하여 저장
                date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])

                cursor.execute(insert_query, (
                    date_str,
                    row['ticker'],
                    row['indicator'],
                    row['value'],
                    row['source'],
                    row['created_at']
                ))

        connection.commit()
        return len(df)

    except Exception as e:
        connection.rollback()
        print(f"Error inserting data: {str(e)}")
        return 0


def collect_daily_stock_prices(
        tickers: List[str],
        start_date: datetime,
        end_date: datetime,
        batch_size: int = 50,
        delay: float = 0.5
) -> Tuple[int, int, List[str]]:
    """
    여러 티커의 일일 주가 데이터 수집 및 저장

    Parameters:
    -----------
    tickers : List[str]
        수집할 티커 리스트
    start_date : datetime
        시작일
    end_date : datetime
        종료일
    batch_size : int
        배치 크기 (commit 주기)
    delay : float
        API 호출 간 대기 시간 (초)

    Returns:
    --------
    Tuple[int, int, List[str]]
        (성공 개수, 실패 개수, 실패 티커 리스트)
    """
    connection = pymysql.connect(**DB_CONFIG, charset='utf8mb4')

    success_count = 0
    fail_count = 0
    failed_tickers = []
    total_records = 0

    try:
        for idx, ticker in enumerate(tickers, 1):
            print(f"[{idx}/{len(tickers)}] Processing {ticker}...", end=' ')

            try:
                # Yahoo Finance에서 데이터 다운로드
                df = fetch_stock_data(ticker, start_date, end_date)

                if df is None or df.empty:
                    print("No data")
                    fail_count += 1
                    failed_tickers.append(ticker)
                    continue

                # DB 저장 형식으로 변환
                df_insert = prepare_data_for_insert(ticker, df)

                # DB에 저장
                records_inserted = insert_stock_data(connection, df_insert)

                if records_inserted > 0:
                    print(f"OK ({records_inserted} records)")
                    success_count += 1
                    total_records += records_inserted
                else:
                    print("Insert failed")
                    fail_count += 1
                    failed_tickers.append(ticker)

                # API 호출 제한 방지를 위한 대기
                time.sleep(delay)

            except Exception as e:
                print(f"Error: {str(e)}")
                fail_count += 1
                failed_tickers.append(ticker)
                continue

        print("\n" + "=" * 60)
        print("데이터 수집 완료")
        print("=" * 60)
        print(f"성공: {success_count}개 티커 ({total_records} records)")
        print(f"실패: {fail_count}개 티커")

        if failed_tickers:
            print(f"\n실패한 티커 목록 ({len(failed_tickers)}개):")
            for i in range(0, len(failed_tickers), 10):
                print(", ".join(failed_tickers[i:i + 10]))

        return success_count, fail_count, failed_tickers

    finally:
        connection.close()


def main():
    """
    메인 실행 함수
    """
    print("=" * 60)
    print("미국 주식 일일 주가 데이터 수집")
    print("=" * 60)

    # 수집 기간 결정
    start_date, end_date = get_collection_date_range()

    # 수집 기간이 유효한지 확인
    if start_date >= end_date:
        print("\n수집할 데이터가 없습니다. (이미 최신 상태)")
        return

    # 티커 리스트 로드
    print(f"\n수집 대상: {len(ticker_list)}개 티커")
    print(f"샘플 티커: {ticker_list[:10]}")

    # 테스트 모드 설정
    test_mode = True  # True: 상위 10개만, False: 전체

    if test_mode:
        test_tickers = ticker_list[:10]  # 상위 10개만
        print(f"\n[테스트 모드] 수집 대상: {len(test_tickers)}개 티커")
        print(f"티커 목록: {test_tickers}")
    else:
        test_tickers = ticker_list
        print(f"\n수집 대상: {len(test_tickers)}개 티커")
        print(f"샘플 티커: {test_tickers[:10]}")

    # 수집 시작 확인
    user_input = input("\n데이터 수집을 시작하시겠습니까? (y/n): ")
    if user_input.lower() != 'y':
        print("수집을 취소했습니다.")
        return

    # 데이터 수집 실행
    print("\n데이터 수집 시작...\n")
    success, fail, failed_list = collect_daily_stock_prices(
        tickers=ticker_list,
        start_date=start_date,
        end_date=end_date,
        batch_size=50,
        delay=0.5  # API 호출 간 0.5초 대기
    )

    # 실패한 티커 재시도 (옵션)
    if failed_list and len(failed_list) < 50:
        retry_input = input(f"\n실패한 {len(failed_list)}개 티커를 재시도하시겠습니까? (y/n): ")
        if retry_input.lower() == 'y':
            print("\n재시도 중...\n")
            collect_daily_stock_prices(
                tickers=failed_list,
                start_date=start_date,
                end_date=end_date,
                batch_size=10,
                delay=1.0  # 재시도 시 더 긴 대기 시간
            )


if __name__ == "__main__":
    main()