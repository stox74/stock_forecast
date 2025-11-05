# -*- coding: utf-8 -*-
"""
수입 데이터 API 테스트 스크립트
실제 API 응답을 확인하여 문제를 진단합니다.
"""

import requests
import json

# API 키
api_key = 'bf388499b71a365d725e1c888201736f7409d7e4'

# 테스트용 HS 코드와 날짜
test_hs_codes = ['270900', '851762', '847150']  # 석유, 전자기기, 컴퓨터
test_year = '2024'
test_month = '01'

print("=" * 80)
print("미국 수입 데이터 API 테스트")
print("=" * 80)

for hs_code in test_hs_codes:
    print(f"\n[테스트] HS Code: {hs_code}, 날짜: {test_year}-{test_month}")
    print("-" * 80)

    # IMPORTS API 테스트
    imports_url = (
        f"https://api.census.gov/data/timeseries/intltrade/imports/hs"
        f"?get=ALL_VAL_MO&key={api_key}&YEAR={test_year}&MONTH={test_month}&I_COMMODITY={hs_code}"
    )

    print(f"URL: {imports_url}")

    try:
        response = requests.get(imports_url, timeout=10)
        print(f"상태 코드: {response.status_code}")

        if response.status_code == 200:
            data = json.loads(response.text)
            print(f"응답 길이: {len(data)}")
            print(f"응답 내용:")
            for i, row in enumerate(data):
                print(f"  [{i}] {row}")

            if len(data) > 1:
                print(f"\n[성공] 데이터: {data[1]}")
            else:
                print(f"\n[실패] 데이터가 없습니다 (헤더만 있음)")
        else:
            print(f"[오류] HTTP {response.status_code}")
            print(f"응답: {response.text[:200]}")

    except Exception as e:
        print(f"[오류] 예외 발생: {e}")

# 비교를 위해 EXPORTS API도 테스트
print("\n\n" + "=" * 80)
print("비교: 수출 데이터 API 테스트")
print("=" * 80)

test_hs_code = '270900'
print(f"\n[테스트] HS Code: {test_hs_code}, 날짜: {test_year}-{test_month}")
print("-" * 80)

exports_url = (
    f"https://api.census.gov/data/timeseries/intltrade/exports/hs"
    f"?get=ALL_VAL_MO&key={api_key}&YEAR={test_year}&MONTH={test_month}&E_COMMODITY={test_hs_code}"
)

print(f"URL: {exports_url}")

try:
    response = requests.get(exports_url, timeout=10)
    print(f"상태 코드: {response.status_code}")

    if response.status_code == 200:
        data = json.loads(response.text)
        print(f"응답 길이: {len(data)}")
        print(f"응답 내용:")
        for i, row in enumerate(data):
            print(f"  [{i}] {row}")
except Exception as e:
    print(f"[오류] 예외 발생: {e}")

print("\n" + "=" * 80)
print("테스트 완료")
print("=" * 80)