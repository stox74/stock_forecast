# -*- coding: utf-8 -*-
# us_import_data_downloader_fast.py

import pandas as pd
import numpy as np
import requests
import json
from tqdm import tqdm
from sqlalchemy import create_engine, text
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time

from DATA.stock_invest_function import *

# 1. HS 코드 모듈에서 불러오기
from us_top_import_hs_code import get_hs_codes

hs_code = get_hs_codes()
print(f"총 HS 코드 수: {len(hs_code)}")


# 2. 세션 생성 함수 (연결 재사용 + 자동 재시도)
def create_session():
    """HTTP 세션을 생성하고 재시도 로직을 설정합니다."""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=100, pool_maxsize=100)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# 3. 단일 API 요청 함수 (IMPORTS용)
def fetch_single_import_data(hs_code, year, month, api_key, session):
    """단일 HS 코드와 날짜에 대한 수입 데이터를 가져옵니다."""
    # IMPORTS API는 I_COMMODITY 파라미터와 GEN_VAL_MO 변수 사용
    url = (
        f"https://api.census.gov/data/timeseries/intltrade/imports/hs"
        f"?get=GEN_VAL_MO&key={api_key}&YEAR={year}&MONTH={month}&I_COMMODITY={hs_code}"
    )
    try:
        res = session.get(url, timeout=10)
        if res.status_code == 200:
            data = json.loads(res.text)
            if len(data) > 1:
                return data[1]  # [impDlr, year, month, hs_code]
    except Exception as e:
        pass
    return None


# 4. 병렬 처리로 수입 데이터 수집
def get_us_import_data_parallel(hs_list, start='2013-01', end='2025-07', api_key='your_key_here', max_workers=50):
    """
    병렬 처리로 미국 HS 코드별 수입 데이터를 빠르게 수집합니다.

    Parameters:
    - hs_list: HS 코드 리스트
    - start: 시작 날짜 (YYYY-MM 형식)
    - end: 종료 날짜 (YYYY-MM 형식)
    - api_key: US Census API 키
    - max_workers: 동시 실행 스레드 수 (기본 50, 더 높이면 더 빠르지만 API 제한 주의)

    Returns:
    - df_monthly: 월별 수입 데이터 DataFrame
    - df_quarterly: 분기별 수입 데이터 DataFrame
    """
    us_import_hs = []
    date_range = pd.date_range(start=start, end=end, freq='MS')

    # 모든 작업 리스트 생성
    tasks = []
    for hs in hs_list:
        for dt in date_range:
            tasks.append((hs, dt.strftime('%Y'), dt.strftime('%m')))

    print(f"총 요청 수: {len(tasks)}")
    print(f"병렬 처리 워커 수: {max_workers}")

    # 세션 생성
    session = create_session()

    # 병렬 처리 실행
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 모든 작업 제출
        future_to_task = {
            executor.submit(fetch_single_import_data, hs, year, month, api_key, session): (hs, year, month)
            for hs, year, month in tasks
        }

        # 진행 상황 표시
        with tqdm(total=len(tasks), desc="미국 수입 데이터 다운로드 중") as pbar:
            for future in as_completed(future_to_task):
                result = future.result()
                if result:
                    us_import_hs.append(result)
                pbar.update(1)

    if not us_import_hs:
        print("[오류] 가져온 데이터가 없습니다.")
        return None, None

    # 데이터프레임 생성 및 전처리
    df = pd.DataFrame(us_import_hs, columns=['impDlr', 'year', 'month', 'hs_code'])
    df['impDlr'] = pd.to_numeric(df['impDlr'], errors='coerce')

    # 이상치 제거 (너무 큰 값)
    df.loc[df['impDlr'] > 1e18, 'impDlr'] = np.nan

    # 날짜 컬럼 생성
    df['date'] = pd.to_datetime(df['year'] + '-' + df['month'], errors='coerce') + pd.offsets.MonthEnd(0)
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)

    # 분기 정보 추가
    df['quarter'] = df.index.to_period('Q')

    # 월별 데이터
    df_monthly = df.copy()

    # 분기별 데이터 (합계)
    df_quarterly = df.groupby(['quarter', 'hs_code'])['impDlr'].sum().reset_index()
    df_quarterly['quarter'] = df_quarterly['quarter'].dt.to_timestamp()

    print(f"[완료] 총 {len(df_monthly)}개의 데이터 수집 완료")

    return df_monthly, df_quarterly


# 5. DB 업로드 함수 (수입 데이터용 - 테이블 자동 생성 포함)
def upload_trade_import_data_to_db(df, db_info, table_name='us_import_data'):
    """
    미국 월별 수입 데이터를 지정한 DB 테이블에 업로드합니다.
    테이블이 없으면 자동으로 생성합니다.

    Parameters:
    - df: 업로드할 DataFrame (월별 수입 데이터)
    - db_info: DB 연결 정보 (dict)
    - table_name: 테이블 이름 (기본값: 'us_import_data')
    """
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )

    try:
        # 인덱스를 컬럼으로 변환 (date)
        df_reset = df.reset_index()

        # 컬럼 순서 정리: date, impDlr, year, month, hs_code, quarter
        column_order = ['date', 'impDlr', 'year', 'month', 'hs_code', 'quarter']
        df_reset = df_reset[column_order]

        # quarter를 문자열로 변환
        df_reset['quarter'] = df_reset['quarter'].astype(str)

        # 테이블 존재 여부 확인 및 생성
        with engine.connect() as conn:
            # 테이블 존재 여부 확인
            result = conn.execute(text(f"""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                  AND table_name = '{table_name}'
            """))
            table_exists = result.fetchone()[0] > 0

            if not table_exists:
                print(f"[정보] '{table_name}' 테이블이 존재하지 않습니다. 새로 생성합니다...")
                # 테이블 생성
                conn.execute(text(f"""
                    CREATE TABLE {table_name} (
                        date DATETIME NOT NULL,
                        impDlr DOUBLE DEFAULT NULL,
                        year INT DEFAULT NULL,
                        month INT DEFAULT NULL,
                        hs_code VARCHAR(20) DEFAULT NULL,
                        quarter VARCHAR(10) DEFAULT NULL,
                        KEY idx_date (date),
                        KEY idx_hs_code (hs_code),
                        KEY idx_quarter (quarter)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """))
                conn.commit()
                print(f"[완료] '{table_name}' 테이블 생성 완료")

        # DB에 데이터 업로드 (replace: 기존 데이터 삭제 후 새로 입력)
        print(f"[진행] 데이터 업로드 중...")
        df_reset.to_sql(
            name=table_name,
            con=engine,
            if_exists='replace',  # replace 모드
            index=False,
            chunksize=1000
        )

        print(f"[완료] 데이터가 '{table_name}' 테이블에 성공적으로 업로드되었습니다.")
        print(f"       총 레코드 수: {len(df_reset):,}개")

    except Exception as e:
        print(f"[오류] DB 업로드 실패: {e}")
        import traceback
        traceback.print_exc()
    finally:
        engine.dispose()


# 6. 데이터 미리보기 함수
def preview_data(df_monthly, df_quarterly, n=10):
    """수집된 데이터를 미리보기합니다."""
    print("\n" + "=" * 80)
    print("[수입 데이터 미리보기]")
    print("=" * 80)

    if df_monthly is not None:
        print(f"\n[월별 데이터] - 처음 {n}개 행:")
        print(df_monthly.head(n))
        print(f"\n[월별 데이터] - 기본 통계:")
        print(df_monthly['impDlr'].describe())

    if df_quarterly is not None:
        print(f"\n[분기별 데이터] - 처음 {n}개 행:")
        print(df_quarterly.head(n))
        print(f"\n[분기별 데이터] - 기본 통계:")
        print(df_quarterly['impDlr'].describe())

    print("\n" + "=" * 80)


# 7. 실행 영역
if __name__ == "__main__":
    print("=" * 80)
    print("[미국 수입 데이터 수집 프로그램]")
    print("=" * 80)

    # API 키
    api_key = 'bf388499b71a365d725e1c888201736f7409d7e4'

    # 시작 시간 기록
    start_time = time.time()

    # 병렬 처리로 수입 데이터 수집
    print("\n[단계 1/3] 데이터 수집 시작...")
    us_import_month, us_import_quarter = get_us_import_data_parallel(
        hs_list=hs_code,
        start='2013-01',
        end='2025-07',
        api_key=api_key,
        max_workers=50  # 동시 실행 스레드 수 (30-100 사이 권장)
    )

    # 소요 시간 출력
    elapsed_time = time.time() - start_time
    print(f"\n[시간] 데이터 수집 소요 시간: {elapsed_time / 60:.2f}분 ({elapsed_time:.2f}초)")

    # 데이터 미리보기
    if us_import_month is not None:
        print("\n[단계 2/3] 데이터 미리보기...")
        preview_data(us_import_month, us_import_quarter, n=10)

        # DB 정보
        db_info = {
            'host': get_db_host(),
            'port': 3307,
            'user': 'stox7412',
            'password': 'Apt106503!~',
            'database': 'investar'
        }

        # DB 업로드 (테이블 자동 생성 포함)
        print("\n[단계 3/3] DB 업로드 시작...")
        upload_trade_import_data_to_db(us_import_month, db_info, table_name='us_import_data')

        # 총 소요 시간
        total_time = time.time() - start_time
        print(f"\n{'=' * 80}")
        print(f"[완료] 모든 작업 완료!")
        print(f"   총 소요 시간: {total_time / 60:.2f}분 ({total_time:.2f}초)")
        print(f"{'=' * 80}")
    else:
        print("\n[오류] 데이터 수집에 실패했습니다.")
        print("   - API 키를 확인하세요.")
        print("   - 네트워크 연결을 확인하세요.")
        print("   - API 엔드포인트가 올바른지 확인하세요.")