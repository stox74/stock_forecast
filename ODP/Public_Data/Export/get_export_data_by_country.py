# -*- coding: utf-8 -*-
"""
공공데이터포털 - 관세청 수출입무역통계 API
엔드포인트: http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList
"""

import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
from datetime import datetime
from dateutil.relativedelta import relativedelta
import sys
import os
import time


# ================================
# 프로젝트 경로 설정
# ================================
def setup_project_path():
    """프로젝트 루트 경로를 Python path에 추가"""
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(current_file))

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    return project_root


setup_project_path()

# KEYS 임포트
try:
    from DATA.KEYS import KEYS
except ImportError:
    KEYS = {}
    print("[경고] KEYS를 불러올 수 없습니다.")

# ================================
# 국가 코드 매핑 (ISO 2자리 코드)
# ================================
COUNTRY_CODES = {
    '미국': 'US',
    '홍콩': 'HK',
    '일본': 'JP',
    '중국': 'CN',
    '베트남': 'VN',
    '싱가포르': 'SG',
    '인도': 'IN',
    '독일': 'DE',
    '영국': 'GB',
    '프랑스': 'FR',
    '대만': 'TW',
    '말레이시아': 'MY',
    '태국': 'TH',
    '인도네시아': 'ID',
    '필리핀': 'PH',
    '캐나다': 'CA',
    '호주': 'AU',
    '전체': ''
}


# ================================
# 날짜 리스트 생성 함수
# ================================
def generate_date_list(start_date: str, end_date: str = None) -> List[str]:
    """
    시작일부터 종료일까지 월별 날짜 리스트 생성 (YYYYMM 형식)
    """
    start = pd.to_datetime(start_date)

    if end_date is None:
        end = datetime.now()
    else:
        end = pd.to_datetime(end_date)

    date_list = []
    current = start.replace(day=1)

    while current <= end:
        date_list.append(current.strftime('%Y%m'))
        current += relativedelta(months=1)

    return date_list


# ================================
# API 데이터 수집 함수
# ================================
def fetch_trade_data_single(
        api_key: str,
        hs_code: str,
        country_code: str,
        year_month: str,
        trade_type: str = '1'
) -> List[Dict]:
    """
    단일 월의 무역 데이터를 가져오는 함수
    """

    base_url = "http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

    params = {
        'serviceKey': api_key,
        'strtYymm': year_month,
        'endYymm': year_month,
        'hsSgn': hs_code,  # 입력된 그대로 사용
        'cntyCd': country_code,
        'exim': trade_type,
        'numOfRows': '100',
        'pageNo': '1'
    }

    try:
        response = requests.get(base_url, params=params, timeout=30)

        if response.status_code != 200:
            print(f"[HTTP {response.status_code}] {year_month}, {country_code}")
            return []

        xml_obj = BeautifulSoup(response.content, 'lxml-xml')

        result_code = xml_obj.find('resultCode')
        if result_code:
            if result_code.text != '00':
                result_msg = xml_obj.find('resultMsg')
                error_msg = result_msg.text if result_msg else 'Unknown'

                if 'NO' in error_msg.upper() or 'DATA' in error_msg.upper():
                    return []
                else:
                    print(f"[API] {year_month}, {country_code}: {error_msg}")
                    return []

        items = xml_obj.find_all('item')

        if not items:
            return []

        data_list = []
        for item in items:
            data_dict = {}
            for tag in item.find_all():
                data_dict[tag.name] = tag.text

            data_dict['year_month'] = year_month
            data_dict['hs_code_input'] = hs_code
            data_dict['country_code_input'] = country_code

            data_list.append(data_dict)

        time.sleep(0.1)

        if data_list:
            print(f"[OK] {year_month}, {country_code}: {len(data_list)}개")

        return data_list

    except requests.exceptions.ConnectionError:
        print(f"[연결실패] {year_month}, {country_code}")
        return []

    except Exception as e:
        print(f"[오류] {year_month}, {country_code}: {type(e).__name__}")
        return []


def fetch_trade_data_bulk(
        api_key: str,
        hs_code: str,
        country_codes: List[str],
        date_list: List[str],
        trade_type: str = '1',
        max_workers: int = 2
) -> pd.DataFrame:
    """
    여러 국가 및 기간의 무역 데이터를 병렬로 수집
    """

    all_data = []
    total = len(country_codes) * len(date_list)
    completed = 0
    successful = 0

    print(f"\n[시작] 총 {total}개 요청")
    print(f"HS: {hs_code}, 국가: {country_codes}")
    print(f"기간: {date_list[0]}~{date_list[-1]}\n")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for country_code in country_codes:
            for year_month in date_list:
                future = executor.submit(
                    fetch_trade_data_single,
                    api_key, hs_code, country_code, year_month, trade_type
                )
                futures[future] = (country_code, year_month)

        for future in as_completed(futures):
            try:
                data = future.result()
                if data:
                    all_data.extend(data)
                    successful += 1
                completed += 1

                if completed % 10 == 0:
                    print(f"[진행] {completed}/{total} (성공: {successful})")

            except Exception as e:
                completed += 1

    print(f"\n[완료] {len(all_data)}개 레코드 (성공: {successful}/{total})")

    if not all_data:
        return pd.DataFrame()

    return pd.DataFrame(all_data)


def process_trade_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    데이터 정제
    """

    if df.empty:
        return df

    print(f"\n[컬럼] {df.columns.tolist()}")

    df['date'] = pd.to_datetime(df['year_month'], format='%Y%m') + pd.offsets.MonthEnd(1)

    numeric_cols = ['expDlr', 'impDlr', 'expWgt', 'impWgt', 'dlr', 'wgt']

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.sort_values('date')

    return df


# ================================
# 메인 함수
# ================================
def get_export_data_by_countries(
        hs_code: str,
        countries: List[str],
        start_date: str,
        end_date: str = None,
        api_key: str = None,
        return_wide_format: bool = False
) -> pd.DataFrame:
    """
    특정 HS Code의 여러 국가별 수출 데이터 수집

    Args:
        hs_code: HS Code (입력된 그대로 사용, 예: '330499', '3304990000', '8542310000')
        countries: 국가 리스트
        start_date: 시작 날짜 (YYYY-MM)
        end_date: 종료 날짜
        api_key: API 키
        return_wide_format: Wide format 반환 여부
    """

    if api_key is None:
        api_key = KEYS.get('ODPD') or KEYS.get('ODP')
        if not api_key:
            raise ValueError("API 키 없음")

    # HS Code는 입력된 그대로 사용
    print(f"[HS Code] {hs_code} ({len(hs_code)}자리)")

    # 국가 코드 변환
    country_codes = []
    for country in countries:
        if country in COUNTRY_CODES:
            country_codes.append(COUNTRY_CODES[country])
        else:
            print(f"[경고] 알 수 없는 국가: {country}")

    if not country_codes:
        raise ValueError("유효한 국가 없음")

    date_list = generate_date_list(start_date, end_date)

    print(f"\n{'=' * 60}")
    print(f"HS: {hs_code}, 국가: {countries}")
    print(f"기간: {len(date_list)}개월")
    print(f"{'=' * 60}")

    df = fetch_trade_data_bulk(
        api_key=api_key,
        hs_code=hs_code,
        country_codes=country_codes,
        date_list=date_list,
        trade_type='1'
    )

    if df.empty:
        print("\n[실패] 데이터 없음")
        return pd.DataFrame()

    df = process_trade_data(df)

    if return_wide_format:
        value_col = 'expDlr' if 'expDlr' in df.columns else 'dlr'

        if value_col not in df.columns:
            return df

        country_name_map = {v: k for k, v in COUNTRY_CODES.items()}

        if 'cntyCd' in df.columns:
            df['country_name'] = df['cntyCd'].map(country_name_map)
        elif 'country_code_input' in df.columns:
            df['country_name'] = df['country_code_input'].map(country_name_map)

        df_wide = df.pivot_table(
            index='date',
            columns='country_name',
            values=value_col,
            aggfunc='sum'
        )

        return df_wide

    return df


# ================================
# 테스트 함수
# ================================
def test_api_detailed(api_key: str):
    """
    여러 조건으로 API 테스트
    """
    print("\n=== 상세 API 테스트 ===\n")

    url = 'http://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList'

    test_cases = [
        {
            'name': '테스트 1: 반도체 (10자리)',
            'params': {
                'serviceKey': api_key,
                'strtYymm': '202312',
                'endYymm': '202312',
                'hsSgn': '8542310000',
                'cntyCd': 'CN'
            }
        },
        {
            'name': '테스트 2: 반도체 (6자리)',
            'params': {
                'serviceKey': api_key,
                'strtYymm': '202312',
                'endYymm': '202312',
                'hsSgn': '854231',
                'cntyCd': 'CN'
            }
        },
        {
            'name': '테스트 3: 화장품 (6자리)',
            'params': {
                'serviceKey': api_key,
                'strtYymm': '202312',
                'endYymm': '202312',
                'hsSgn': '330499',
                'cntyCd': 'US'
            }
        },
        {
            'name': '테스트 4: 승용차 (10자리)',
            'params': {
                'serviceKey': api_key,
                'strtYymm': '202312',
                'endYymm': '202312',
                'hsSgn': '8703230000',
                'cntyCd': 'US'
            }
        }
    ]

    success_count = 0

    for test in test_cases:
        print(f"{test['name']}")

        try:
            response = requests.get(url, params=test['params'], timeout=30)

            xml_obj = BeautifulSoup(response.content, 'lxml-xml')
            result_code = xml_obj.find('resultCode')
            result_msg = xml_obj.find('resultMsg')
            items = xml_obj.find_all('item')

            print(f"  상태: {response.status_code}")
            print(f"  결과: {result_msg.text if result_msg else 'N/A'}")
            print(f"  데이터: {len(items)}개")

            if items:
                print(f"  ✓ 성공!")
                success_count += 1
            else:
                print(f"  ✗ 데이터 없음")

            print()
            time.sleep(0.5)

        except Exception as e:
            print(f"  ✗ 오류: {e}")
            print()

    print(f"{'=' * 60}")
    print(f"결과: {success_count}/{len(test_cases)} 성공")
    print(f"{'=' * 60}\n")

    return success_count > 0


if __name__ == "__main__":

    api_key = KEYS.get('ODPD') or KEYS.get('ODP')

    if not api_key:
        print("API 키 없음")
        exit(1)

    # 테스트
    if test_api_detailed(api_key):
        print("✓ 일부 테스트 성공\n")

        # 실제 수집 (6자리 HS Code)
        print("=== 6자리 HS Code로 실제 수집 ===")
        df = get_export_data_by_countries(
            hs_code='330499',  # 6자리 그대로
            countries=['미국', '중국', '일본'],
            start_date='2023-10',
            end_date='2023-12',
            return_wide_format=True
        )

        if not df.empty:
            print("\n결과:")
            print(df)
    else:
        print("✗ 모든 테스트 실패")