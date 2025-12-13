# us_export_data_downloader_fast.py

import pandas as pd
import numpy as np
import requests
import json
from tqdm import tqdm
from sqlalchemy import create_engine
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time

# 1. 파일에서 HS 코드 불러오기
path = r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\DATA\미국_500대_수출금액_.HScode_202508.xlsx"
hs_raw = pd.read_excel(path)
hs_raw['hs_code'] = hs_raw['HS_Code'].astype(str).str[:6]
hs_code = hs_raw['hs_code'].unique().tolist()

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


# 3. 단일 API 요청 함수
def fetch_single_data(hs_code, year, month, api_key, session):
    """단일 HS 코드와 날짜에 대한 데이터를 가져옵니다."""
    url = (
        f"https://api.census.gov/data/timeseries/intltrade/exports/hs"
        f"?get=ALL_VAL_MO&key={api_key}&YEAR={year}&MONTH={month}&E_COMMODITY={hs_code}"
    )
    try:
        res = session.get(url, timeout=10)
        if res.status_code == 200:
            data = json.loads(res.text)
            if len(data) > 1:
                return data[1]  # [expDlr, year, month, hs_code]
    except Exception as e:
        pass
    return None


# 4. 병렬 처리로 데이터 수집
def get_us_export_data_parallel(hs_list, start='2013-01', end='2025-05', api_key='your_key_here', max_workers=50):
    """
    병렬 처리로 미국 HS 코드별 수출 데이터를 빠르게 수집합니다.

    Parameters:
    - max_workers: 동시 실행 스레드 수 (기본 50, 더 높이면 더 빠르지만 API 제한 주의)
    """
    us_export_hs = []
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
            executor.submit(fetch_single_data, hs, year, month, api_key, session): (hs, year, month)
            for hs, year, month in tasks
        }

        # 진행 상황 표시
        with tqdm(total=len(tasks), desc="미국 수출 데이터 다운로드 중") as pbar:
            for future in as_completed(future_to_task):
                result = future.result()
                if result:
                    us_export_hs.append(result)
                pbar.update(1)

    if not us_export_hs:
        print("[오류] 가져온 데이터가 없습니다.")
        return None, None

    # 데이터프레임 생성 및 전처리
    df = pd.DataFrame(us_export_hs, columns=['expDlr', 'year', 'month', 'hs_code'])
    df['expDlr'] = pd.to_numeric(df['expDlr'], errors='coerce')
    df.loc[df['expDlr'] > 1e18, 'expDlr'] = np.nan
    df['date'] = pd.to_datetime(df['year'] + '-' + df['month'], errors='coerce') + pd.offsets.MonthEnd(0)
    df.dropna(subset=['date'], inplace=True)
    df.set_index('date', inplace=True)
    df['quarter'] = df.index.to_period('Q')

    df_monthly = df.copy()
    df_quarterly = df.groupby(['quarter', 'hs_code'])['expDlr'].sum().reset_index()
    df_quarterly['quarter'] = df_quarterly['quarter'].dt.to_timestamp()

    print(f"[완료] 총 {len(df_monthly)}개의 데이터 수집 완료")

    return df_monthly, df_quarterly


# 5. DB 업로드 함수
def upload_trade_data_to_db(df, db_info, table_name='us_trade_data'):
    """미국 월별 수출 데이터를 지정한 DB 테이블에 업로드합니다."""
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )

    try:
        df_reset = df.reset_index()
        df_reset.to_sql(name=table_name, con=engine, if_exists='replace', index=False, chunksize=1000)
        print(f"[완료] 데이터가 '{table_name}' 테이블에 성공적으로 업로드되었습니다.")
    except Exception as e:
        print(f"[오류] DB 업로드 실패: {e}")


# 6. 실행 영역
if __name__ == "__main__":
    # API 키
    key = 'bf388499b71a365d725e1c888201736f7409d7e4'

    # 시작 시간 기록
    start_time = time.time()

    # 병렬 처리로 데이터 수집 (max_workers 조정 가능)
    us_export_month, us_export_quarter = get_us_export_data_parallel(
        hs_list=hs_code,
        start='2013-01',
        end='2025-09',
        api_key=key,
        max_workers=50  # 동시 실행 스레드 수 (30-100 사이 권장)
    )

    # 소요 시간 출력
    elapsed_time = time.time() - start_time
    print(f"\n[시간] 총 소요 시간: {elapsed_time / 60:.2f}분 ({elapsed_time:.2f}초)")

    # DB 정보
    db_info = {
        'host': '192.168.0.230',
        'port': 3307,
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'database': 'investar'
    }

    # DB 업로드
    if us_export_month is not None:
        upload_trade_data_to_db(us_export_month, db_info)