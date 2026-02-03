import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional


def fetch_single_month(api_key: str, country_code: int, ed_code: str, dt: str) -> List[Dict]:
    """단일 월의 관광 데이터를 가져오는 함수

    Args:
        api_key: 공공데이터 포털 API 키
        country_code: 국가코드 (112: 중국, 130: 일본, 0: 전체)
        ed_code: 입출국 구분 ('E': 입국, 'D': 출국)
        dt: 조회 날짜 (YYYYMM 형식)

    Returns:
        데이터 딕셔너리 리스트
    """
    nat_cd = str(country_code) if country_code != 0 else ''

    url = (f'http://openapi.tour.go.kr/openapi/service/EdrcntTourismStatsService/'
           f'getEdrcntTourismStatsList?YM={dt}&NAT_CD={nat_cd}&ED_CD={ed_code}&serviceKey={api_key}')

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        xml_obj = BeautifulSoup(response.content, 'lxml-xml')
        items = xml_obj.find_all('item')

        data_list = []
        for item in items:
            data_dict = {tag.name: tag.text for tag in item.find_all()}
            data_list.append(data_dict)

        return data_list

    except Exception as e:
        print(f"Error fetching data for {dt}: {e}")
        return []


def fetch_tourism_data_bulk(
        api_key: str,
        country_code: int,
        ed_code: str,
        date_list: List[str],
        max_workers: int = 5
) -> pd.DataFrame:
    """여러 월의 관광 데이터를 병렬로 수집

    Args:
        api_key: 공공데이터 포털 API 키
        country_code: 국가코드
        ed_code: 입출국 구분
        date_list: 조회 날짜 리스트 (YYYYMM 형식)
        max_workers: 병렬 처리 워커 수

    Returns:
        수집된 데이터 DataFrame
    """
    all_data = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_dt = {
            executor.submit(fetch_single_month, api_key, country_code, ed_code, dt): dt
            for dt in date_list
        }

        for future in as_completed(future_to_dt):
            data = future.result()
            all_data.extend(data)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)

    # 컬럼명 매핑
    column_mapping = {
        'rnum': '항목',
        'ed': '구분',
        'natCd': '코드',
        'natKorNm': '국적',
        'num': '입국자수',
        'ym': '날짜'
    }

    df = df.rename(columns=column_mapping)

    # 날짜 및 타입 변환
    if '날짜' in df.columns:
        df['날짜'] = pd.to_datetime(df['날짜'], format='%Y%m') + pd.offsets.MonthEnd(1)
        df = df.set_index('날짜')

    if '입국자수' in df.columns:
        df['입국자수'] = pd.to_numeric(df['입국자수'], errors='coerce')

    return df


if __name__ == "__main__":
    # 테스트 코드
    from DATA.KEYS import *  # KEYS 딕셔너리가 있는 설정 파일

    key = KEYS['ODPE']
    dt_list = ['202301', '202302', '202303']

    df = fetch_tourism_data_bulk(
        api_key=key,
        country_code=112,  # 중국
        ed_code='E',
        date_list=dt_list,
        max_workers=5
    )

    print(df)