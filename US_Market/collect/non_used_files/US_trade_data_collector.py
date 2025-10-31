# us_export_data_downloader.py

import pandas as pd
import numpy as np
import requests
import json
from tqdm import tqdm
from sqlalchemy import create_engine
from concurrent.futures import ThreadPoolExecutor, as_completed
from DATA.stock_invest_function import *
import time
from functools import partial

# 1. 파일에서 HS 코드 불러오기
path = r"C:\Users\MetaM\PycharmProjects\stock_forecast\DATA\미국_500대_수출금액_.HScode_202508.xlsx"
hs_raw = pd.read_excel(path)
hs_raw['hs_code'] = hs_raw['HS_Code'].astype(str).str[:6]
hs_code = hs_raw['hs_code'].unique().tolist()
hs_list = hs_code.copy()

print(f"총 HS 코드 수: {len(hs_code)}")
print("수집할 HS 코드 리스트:")
for i, code in enumerate(hs_code):
    print(f"{i + 1:3d}. {code}")
print("-" * 50)

# 2. 날짜 설정
start_year = '2016-03-01'
end_date = '2025-06'
start_y = 2025
start_q = 1
export_import = 'expDlr'

# 월말 기준 날짜 리스트
dates_period = pd.date_range(start='2020-01', end=end_date, freq='M')
dates_list1 = [str(dates)[:7] for dates in dates_period]

# 3. 전역 변수로 진행 상황 추적
current_hs_status = {}
completed_hs_codes = set()

# print(len(hs_list))

def fetch_single_export_data(hs_code, year, month, api_key, session=None):
    """
    단일 API 호출을 수행합니다.
    """
    if session is None:
        session = requests.Session()

    # 현재 처리 중인 HS 코드 추적
    if hs_code not in current_hs_status:
        current_hs_status[hs_code] = {'total': 0, 'success': 0, 'failed': 0}
    current_hs_status[hs_code]['total'] += 1

    url = (
        f"https://api.census.gov/data/timeseries/intltrade/exports/hs"
        f"?get=ALL_VAL_MO&key={api_key}&YEAR={year}&MONTH={month}&E_COMMODITY={hs_code}"
    )

    try:
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            data = json.loads(response.text)
            if len(data) > 1:
                current_hs_status[hs_code]['success'] += 1
                return data[1]  # [expDlr, year, month, hs_code]
        else:
            current_hs_status[hs_code]['failed'] += 1
            print(f"❌ 실패: {year}-{month} {hs_code} → Status: {response.status_code}")
            return None
    except Exception as e:
        current_hs_status[hs_code]['failed'] += 1
        print(f"⚠️ 예외 발생: {year}-{month} {hs_code} → {e}")
        return None


# 4. 진행 상황 출력 함수
def print_progress_board(hs_list, current_batch_num, total_batches):
    """
    현재 HS 코드별 수집 진행 상황을 출력합니다.
    """
    print("\n" + "=" * 80)
    print(f"📊 배치 {current_batch_num}/{total_batches} - HS 코드별 수집 현황")
    print("=" * 80)

    completed_count = 0
    in_progress_count = 0

    for i, hs_code in enumerate(hs_list, 1):
        if hs_code in completed_hs_codes:
            status = "✅ 완료"
            completed_count += 1
        elif hs_code in current_hs_status:
            stats = current_hs_status[hs_code]
            total_requests = stats['total']
            success_requests = stats['success']
            failed_requests = stats['failed']

            if total_requests > 0:
                success_rate = (success_requests / total_requests) * 100
                status = f"🔄 수집중 ({success_requests}/{total_requests}, {success_rate:.1f}%)"
                in_progress_count += 1
            else:
                status = "⏳ 대기중"
        else:
            status = "⏳ 대기중"

        print(f"{i:2d}. {hs_code} - {status}")

    print("-" * 80)
    print(
        f"📈 진행 요약: 완료 {completed_count}개 | 수집중 {in_progress_count}개 | 대기중 {len(hs_list) - completed_count - in_progress_count}개")
    print("=" * 80 + "\n")


def get_us_export_data_multithreaded(hs_list, start='2013-01', end= end_date, api_key='your_key_here', max_workers=10,
                                     batch_num=1, total_batches=1):
    """
    미국 HS 코드별 수출 데이터를 멀티스레딩으로 병렬 수집합니다.
    """
    global current_hs_status, completed_hs_codes

    # 현재 배치 시작 시 상태 초기화
    for hs_code in hs_list:
        current_hs_status[hs_code] = {'total': 0, 'success': 0, 'failed': 0}

    date_range = pd.date_range(start=start, end=end, freq='MS')

    # 모든 요청 조합 생성
    tasks_data = []
    for hs in hs_list:
        for dt in date_range:
            year = dt.strftime('%Y')
            month = dt.strftime('%m')
            tasks_data.append((hs, year, month))

    print(f"📡 총 {len(tasks_data)}개의 API 요청을 {max_workers}개 스레드로 병렬 처리합니다.")
    print(f"📅 수집 기간: {start} ~ {end}")
    print(f"🎯 대상 HS 코드: {len(hs_list)}개\n")

    # 초기 진행 상황 출력
    print_progress_board(hs_list, batch_num, total_batches)

    us_export_hs = []
    session = requests.Session()

    # 완료된 요청 수를 HS 코드별로 추적
    completed_requests_per_hs = {hs: 0 for hs in hs_list}
    total_requests_per_hs = {hs: len(date_range) for hs in hs_list}

    # ThreadPoolExecutor를 사용한 병렬 처리
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 모든 작업을 제출
        future_to_task = {
            executor.submit(fetch_single_export_data, hs_code, year, month, api_key, session): (hs_code, year, month)
            for hs_code, year, month in tasks_data
        }

        # tqdm으로 진행률 표시
        with tqdm(total=len(tasks_data), desc=f"🔄 배치 {batch_num} API 수집") as pbar:
            completed_count = 0

            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    hs_code, year, month = future_to_task[future]

                    if result is not None:
                        us_export_hs.append(result)

                    # HS 코드별 완료 요청 수 업데이트
                    completed_requests_per_hs[hs_code] += 1

                    # HS 코드가 모든 요청 완료되면 완료 목록에 추가
                    if completed_requests_per_hs[hs_code] == total_requests_per_hs[hs_code]:
                        completed_hs_codes.add(hs_code)
                        print(
                            f"✅ HS 코드 {hs_code} 수집 완료! ({completed_requests_per_hs[hs_code]}/{total_requests_per_hs[hs_code]} 요청)")

                    completed_count += 1

                    # 매 100개 요청마다 진행 상황 업데이트
                    if completed_count % 100 == 0:
                        print(f"\n📊 중간 진행 상황 ({completed_count}/{len(tasks_data)} 완료):")
                        for hs in hs_list:
                            if hs in current_hs_status:
                                stats = current_hs_status[hs]
                                if stats['total'] > 0:
                                    success_rate = (stats['success'] / stats['total']) * 100
                                    print(
                                        f"   {hs}: {completed_requests_per_hs[hs]}/{total_requests_per_hs[hs]} 완료 (성공률: {success_rate:.1f}%)")
                        print()

                except Exception as e:
                    hs_code, year, month = future_to_task[future]
                    print(f"⚠️ 작업 실패: {year}-{month} {hs_code} → {e}")
                finally:
                    pbar.update(1)

    session.close()

    # 최종 결과 출력
    print("\n" + "🎉" * 20 + " 수집 완료 " + "🎉" * 20)
    print(f"📊 배치 {batch_num} 최종 결과:")
    for hs_code in hs_list:
        if hs_code in current_hs_status:
            stats = current_hs_status[hs_code]
            success_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
            print(
                f"   📈 {hs_code}: 총 {stats['total']}개 요청 | 성공 {stats['success']}개 | 실패 {stats['failed']}개 | 성공률 {success_rate:.1f}%")
    print("🎉" * 50 + "\n")

    if not us_export_hs:
        print("❌ 가져온 데이터가 없습니다.")
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

    return df_monthly, df_quarterly


# 5. 기존 함수명 호환을 위한 래퍼
def get_us_export_data_batch(hs_list, start='2013-01', end = end_date, api_key='your_key_here', max_workers=10,
                             batch_num=1, total_batches=1):
    """
    하위 호환성을 위한 래퍼 함수입니다.
    """
    return get_us_export_data_multithreaded(hs_list, start, end, api_key, max_workers, batch_num, total_batches)


# 6. DB 업로드 함수 (개선된 버전)
def upload_trade_data_to_db(df, db_info, table_name='us_trade_data', batch_num=None):
    """
    미국 월별 수출 데이터를 지정한 DB 테이블에 업로드합니다.
    """
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )

    try:
        df_reset = df.reset_index()

        # 첫 번째 배치는 테이블을 새로 만들고, 이후 배치는 추가
        if_exists_mode = 'replace' if batch_num == 1 else 'append'

        df_reset.to_sql(name=table_name, con=engine, if_exists=if_exists_mode, index=False)

        batch_info = f" (배치 {batch_num})" if batch_num else ""
        print(f"✅ 데이터가 '{table_name}' 테이블에 성공적으로 업로드되었습니다{batch_info}.")
        print(f"   업로드된 레코드 수: {len(df_reset)}")

    except Exception as e:
        print(f"❌ DB 업로드 실패{batch_info}: {e}")


# 7. 배치별 처리 함수 (멀티스레딩 버전)
def process_hs_codes_in_batches(hs_code_list, batch_size=50, start='2013-01', end=end_date,
                                api_key='your_key', db_info=None, max_workers=10):
    """
    HS 코드를 배치별로 나누어 멀티스레딩으로 고속 처리하고 각 배치마다 DB에 업로드합니다.
    """
    total_codes = len(hs_code_list)
    batch_count = (total_codes + batch_size - 1) // batch_size

    print(f"총 {total_codes}개의 HS 코드를 {batch_size}개씩 {batch_count}개 배치로 나누어 처리합니다.")
    print(f"동시 스레드 수: {max_workers}개")
    print(f"예상 속도 향상: 기존 대비 약 {max_workers // 2}-{max_workers}배\n")

    total_start_time = time.time()

    for batch_num in range(1, batch_count + 1):
        start_idx = (batch_num - 1) * batch_size
        end_idx = min(start_idx + batch_size, total_codes)

        current_batch = hs_code_list[start_idx:end_idx]

        print(f"🔄 배치 {batch_num}/{batch_count} 시작 (HS코드 {len(current_batch)}개)")
        print(f"   처리 범위: {start_idx + 1}~{end_idx} 번째 코드")
        print(f"   HS코드: {', '.join(current_batch[:5])}{'...' if len(current_batch) > 5 else ''}")

        batch_start_time = time.time()

        try:
            # 멀티스레딩 고속 데이터 수집 (배치 정보 전달)
            monthly_data, quarterly_data = get_us_export_data_batch(
                hs_list=current_batch,
                start=start,
                end=end,
                api_key=api_key,
                max_workers=max_workers,
                batch_num=batch_num,
                total_batches=batch_count
            )

            batch_end_time = time.time()
            collection_time = batch_end_time - batch_start_time

            # DB 업로드
            if monthly_data is not None and not monthly_data.empty:
                upload_start_time = time.time()
                upload_trade_data_to_db(monthly_data, db_info, 'us_trade_data', batch_num)
                upload_time = time.time() - upload_start_time

                print(f"✅ 배치 {batch_num} 완료")
                print(f"   📊 수집된 레코드: {len(monthly_data)}개")
                print(f"   ⏱️ 데이터 수집 시간: {collection_time:.1f}초")
                print(f"   💾 DB 업로드 시간: {upload_time:.1f}초")
                print(f"   📈 총 소요 시간: {collection_time + upload_time:.1f}초\n")
            else:
                print(f"⚠️ 배치 {batch_num}에서 수집된 데이터가 없습니다.")
                print(f"   ⏱️ 소요 시간: {collection_time:.1f}초\n")

        except Exception as e:
            print(f"❌ 배치 {batch_num} 처리 중 오류 발생: {e}")
            print(f"   다음 배치로 계속 진행합니다...\n")
            continue

    total_time = time.time() - total_start_time
    print(f"🎉 모든 배치 처리가 완료되었습니다!")
    print(f"⏱️ 전체 소요 시간: {total_time / 60:.1f}분")


# 8. 실제 실행 영역
if __name__ == "__main__":
    # 테스트용으로 처음 100개만 사용
    # hs_code = hs_code[:]

    # API 키 입력
    key = 'bf388499b71a365d725e1c888201736f7409d7e4'  # 실제 API 키로 교체 필요

    # DB 정보
    db_info = {
        'host': get_db_host(),
        'port': 3307,
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'database': 'investar'
    }

    # 멀티스레딩 배치별 처리 실행
    process_hs_codes_in_batches(
        hs_code_list=hs_code,
        batch_size=50,
        start='2013-01',
        end=end_date,
        api_key=key,
        db_info=db_info,
        max_workers=10  # 스레드 수 (필요에 따라 조정 가능: 5-20)
    )