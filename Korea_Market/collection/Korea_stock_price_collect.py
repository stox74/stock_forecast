"""
한국 주식 가격 데이터 수집 및 DB 저장
- pykrx API 사용
- 에러 핸들링 강화
- 배치 저장 최적화
- 진행 상황 모니터링
"""

import pandas as pd
import pymysql
from datetime import datetime, timedelta
from tqdm import tqdm
from pykrx import stock
import time
import sys
from pathlib import Path


# ==================== 경로 설정 ====================
def setup_data_path():
    """DATA 폴더 경로 추가"""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        data_folder = parent / "DATA"
        if data_folder.exists():
            if str(data_folder) not in sys.path:
                sys.path.insert(0, str(data_folder))
            return data_folder
    raise FileNotFoundError("DATA 폴더를 찾을 수 없습니다.")


try:
    setup_data_path()
    from DATA.config import get_db_info

    print("config 모듈 import 성공\n")
except ImportError as e:
    print(f"모듈 import 실패: {e}")
    print("DATA 폴더에 config.py 파일이 있는지 확인하세요.")
    sys.exit(1)

# ==================== 설정 ====================
# 데이터 수집 기간 설정
START_DATE = "20260101"  # 시작일 (YYYYMMDD)
END_DATE = datetime.today().strftime("%Y%m%d")  # 오늘까지

# API 호출 설정
DELAY_BETWEEN_CALLS = 0.2  # API 호출 간 대기 시간 (초)
MAX_RETRIES = 3  # 실패 시 재시도 횟수
BATCH_SIZE = 50  # 배치 저장 크기

# 테스트 모드
TEST_MODE = False  # True: 상위 50개만, False: 전체
TEST_SIZE = 50


# ==================== 함수 정의 ====================
def read_krx_code():
    """
    KRX로부터 상장기업 목록을 읽어와 데이터프레임으로 반환

    Returns:
        pd.DataFrame: code, company 컬럼을 가진 DataFrame
    """
    print("\nKRX 상장기업 목록 조회 중...")

    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        krx = pd.read_html(url, header=0, encoding='euc-kr')[0]
        krx = krx[['종목코드', '회사명']]
        krx = krx.rename(columns={'종목코드': 'code', '회사명': 'company'})

        # 코드를 문자열로 변환 후 6자리 형식 적용
        krx['code'] = krx['code'].astype(str).str.zfill(6)

        print(f"총 {len(krx)}개 종목 조회 완료")
        return krx

    except Exception as e:
        print(f"KRX 목록 조회 실패: {e}")
        sys.exit(1)


def fetch_single_stock_price(code, start_date, end_date, retries=MAX_RETRIES):
    """
    단일 종목의 주가 데이터를 가져오기 (재시도 로직 포함)

    Parameters:
        code: 종목 코드
        start_date: 시작일 (YYYYMMDD)
        end_date: 종료일 (YYYYMMDD)
        retries: 최대 재시도 횟수

    Returns:
        pd.DataFrame: 주가 데이터 또는 None (실패 시)
    """
    for attempt in range(retries):
        try:
            # API 호출
            price_data = stock.get_market_ohlcv(start_date, end_date, code)

            # 데이터가 비어있지 않은 경우
            if not price_data.empty:
                price_data['코드'] = code
                return price_data
            else:
                # 데이터가 없는 경우 (상장폐지, 기간 외 등)
                return None

        except Exception as e:
            if attempt < retries - 1:
                # 재시도 전 대기
                time.sleep(DELAY_BETWEEN_CALLS * 2)
            else:
                # 최종 실패
                print(f"\n  {code}: 데이터 조회 실패 - {str(e)[:50]}")
                return None

    return None


def fetch_stock_price_data(code_list, start_date, end_date):
    """
    종목 코드 리스트를 사용하여 시가, 종가, 거래량 등 주식 가격 데이터를 가져온다.

    Parameters:
        code_list: 종목 코드 리스트
        start_date: 시작일 (YYYYMMDD)
        end_date: 종료일 (YYYYMMDD)

    Returns:
        pd.DataFrame: 전체 주가 데이터
        list: 실패한 종목 코드 리스트
    """
    print(f"\n주가 데이터 수집 시작:")
    print(f"  기간: {start_date} ~ {end_date}")
    print(f"  종목 수: {len(code_list)}개")
    print(f"  API 호출 간격: {DELAY_BETWEEN_CALLS}초")

    price_list = []
    failed_codes = []
    success_count = 0

    start_time = datetime.now()

    for i, code in enumerate(tqdm(code_list, desc="주가 데이터 수집")):
        # API 호출
        price_data = fetch_single_stock_price(code, start_date, end_date)

        if price_data is not None:
            price_list.append(price_data)
            success_count += 1
        else:
            failed_codes.append(code)

        # API rate limiting 방지를 위한 대기
        if i < len(code_list) - 1:  # 마지막이 아니면
            time.sleep(DELAY_BETWEEN_CALLS)

        # 진행 상황 출력 (100개마다)
        if (i + 1) % 100 == 0:
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = elapsed / (i + 1)
            remaining = (len(code_list) - i - 1) * rate
            print(f"\n  진행: {i + 1}/{len(code_list)} | "
                  f"성공: {success_count} | "
                  f"실패: {len(failed_codes)} | "
                  f"예상 남은 시간: {remaining / 60:.1f}분")

    print(f"\n데이터 수집 완료:")
    print(f"  성공: {success_count}개 ({success_count / len(code_list) * 100:.1f}%)")
    print(f"  실패: {len(failed_codes)}개")

    if price_list:
        combined_df = pd.concat(price_list, ignore_index=False)
        print(f"  총 데이터 행 수: {len(combined_df):,}개")
        return combined_df, failed_codes
    else:
        print("  경고: 수집된 데이터가 없습니다!")
        return pd.DataFrame(), failed_codes


def setup_database_connection(db_info: dict):
    """
    데이터베이스 연결 설정 (config.py 사용)

    Parameters:
        db_info: DB 연결 정보 딕셔너리

    Returns:
        pymysql.Connection: DB 연결 객체
    """
    try:
        conn = pymysql.connect(
            host=db_info["host"],
            port=db_info["port"],
            user=db_info["user"],
            password=db_info["password"],
            db=db_info["database"],
            autocommit=False,
            charset='utf8mb4'
        )
        print("\nDB 연결 성공")
        return conn

    except Exception as e:
        print(f"\nDB 연결 실패: {e}")
        sys.exit(1)


def create_table(cursor):
    """
    데이터베이스에 KSE_Price 테이블 생성
    """
    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS KSE_Price
                   (
                       date
                       DATE,
                       open
                       INT,
                       high
                       INT,
                       low
                       INT,
                       close
                       INT,
                       volume
                       BIGINT,
                       prc_change
                       FLOAT,
                       code
                       VARCHAR
                   (
                       10
                   ),
                       PRIMARY KEY
                   (
                       date,
                       code
                   ),
                       INDEX idx_code
                   (
                       code
                   ),
                       INDEX idx_date
                   (
                       date
                   )
                       ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                   ''')
    print("테이블 생성/확인 완료")


def insert_stock_data_batch(cursor, data, batch_size=BATCH_SIZE):
    """
    배치 단위로 데이터베이스에 주식 데이터를 삽입

    Parameters:
        cursor: DB 커서
        data: 주가 데이터 DataFrame
        batch_size: 배치 크기

    Returns:
        int: 삽입된 행 수
    """
    print(f"\nDB 저장 시작 (배치 크기: {batch_size}개)...")

    total_inserted = 0
    batch_buffer = []

    for idx, row in tqdm(data.iterrows(), total=len(data), desc="DB 저장"):
        batch_buffer.append((
            row['date'],
            int(row['open']) if pd.notna(row['open']) else None,
            int(row['high']) if pd.notna(row['high']) else None,
            int(row['low']) if pd.notna(row['low']) else None,
            int(row['close']) if pd.notna(row['close']) else None,
            int(row['volume']) if pd.notna(row['volume']) else None,
            float(row['prc_change']) if pd.notna(row['prc_change']) else None,
            row['code']
        ))

        # 배치 크기에 도달하면 저장
        if len(batch_buffer) >= batch_size:
            cursor.executemany('''
                               INSERT
                               IGNORE INTO KSE_Price 
                VALUES (
                               %s,
                               %s,
                               %s,
                               %s,
                               %s,
                               %s,
                               %s,
                               %s
                               )
                               ''', batch_buffer)
            total_inserted += len(batch_buffer)
            batch_buffer = []

    # 남은 데이터 저장
    if batch_buffer:
        cursor.executemany('''
                           INSERT
                           IGNORE INTO KSE_Price 
            VALUES (
                           %s,
                           %s,
                           %s,
                           %s,
                           %s,
                           %s,
                           %s,
                           %s
                           )
                           ''', batch_buffer)
        total_inserted += len(batch_buffer)

    print(f"\n총 {total_inserted:,}개 행 저장 완료")
    return total_inserted


def main():
    """메인 실행 함수"""
    print("=" * 80)
    print("한국 주식 가격 데이터 수집 시스템")
    print("=" * 80)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"수집 기간: {START_DATE} ~ {END_DATE}")
    print(f"테스트 모드: {'ON (상위 {}개)'.format(TEST_SIZE) if TEST_MODE else 'OFF (전체)'}")
    print("=" * 80)

    # DB 연결 정보 가져오기
    db_info = get_db_info()

    # 1. KRX 상장 기업 목록 가져오기
    krx_df = read_krx_code()
    code_list = krx_df['code'].tolist()

    # 테스트 모드
    if TEST_MODE:
        code_list = code_list[:TEST_SIZE]
        print(f"\n테스트 모드: 상위 {len(code_list)}개 종목만 처리")

    # 2. 주식 데이터 가져오기
    price_df, failed_codes = fetch_stock_price_data(code_list, START_DATE, END_DATE)

    if price_df.empty:
        print("\n수집된 데이터가 없습니다. 프로그램을 종료합니다.")
        return

    # 3. 데이터 전처리
    print("\n데이터 전처리 중...")
    price_df_re = price_df.reset_index()
    price_df_re.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'prc_change', 'code']

    # 날짜 형식 변환
    price_df_re['date'] = pd.to_datetime(price_df_re['date']).dt.date

    print(f"전처리 완료: {len(price_df_re):,}개 행")
    print(f"데이터 샘플:")
    print(price_df_re.head())

    # 4. 데이터베이스 연결 및 작업
    cnx = setup_database_connection(db_info)
    cursor = cnx.cursor()

    try:
        # 테이블 생성
        create_table(cursor)

        # 데이터 삽입
        inserted_count = insert_stock_data_batch(cursor, price_df_re, BATCH_SIZE)

        # 커밋
        cnx.commit()
        print("\n데이터베이스 커밋 완료")

        # 저장 결과 확인
        cursor.execute("SELECT COUNT(*) as cnt FROM KSE_Price")
        total_rows = cursor.fetchone()[0]
        print(f"DB 내 총 데이터: {total_rows:,}개 행")

        # 최근 데이터 확인
        cursor.execute("""
                       SELECT date, code, close, volume
                       FROM KSE_Price
                       ORDER BY date DESC, code
                           LIMIT 5
                       """)
        recent_data = cursor.fetchall()
        print(f"\n최근 저장 데이터 샘플:")
        for row in recent_data:
            print(f"  {row}")

    except Exception as e:
        print(f"\n오류 발생: {e}")
        cnx.rollback()
        print("데이터베이스 롤백 완료")
        raise

    finally:
        cursor.close()
        cnx.close()
        print("\nDB 연결 종료")

    # 5. 실패한 종목 처리
    if failed_codes:
        print(f"\n실패한 종목 ({len(failed_codes)}개):")
        if len(failed_codes) <= 20:
            for code in failed_codes:
                company = krx_df[krx_df['code'] == code]['company'].values[0]
                print(f"  {code}: {company}")
        else:
            print(f"  처음 20개:")
            for code in failed_codes[:20]:
                company = krx_df[krx_df['code'] == code]['company'].values[0]
                print(f"  {code}: {company}")

        # 재시도 옵션
        if len(failed_codes) <= 50:
            retry = input(f"\n실패한 {len(failed_codes)}개 종목을 재시도하시겠습니까? (y/n): ").strip().lower()
            if retry == 'y':
                print("\n재시도 중...\n")
                retry_df, retry_failed = fetch_stock_price_data(failed_codes, START_DATE, END_DATE)

                if not retry_df.empty:
                    retry_df_re = retry_df.reset_index()
                    retry_df_re.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'prc_change', 'code']
                    retry_df_re['date'] = pd.to_datetime(retry_df_re['date']).dt.date

                    cnx = setup_database_connection(db_info)
                    cursor = cnx.cursor()

                    try:
                        retry_count = insert_stock_data_batch(cursor, retry_df_re, BATCH_SIZE)
                        cnx.commit()
                        print(f"\n재시도 성공: {retry_count:,}개 행 추가 저장")
                    finally:
                        cursor.close()
                        cnx.close()

                print(f"\n재시도 결과:")
                print(f"  성공: {len(failed_codes) - len(retry_failed)}개")
                print(f"  실패: {len(retry_failed)}개")

    print("\n" + "=" * 80)
    print("모든 작업 완료")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n치명적 오류 발생: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)