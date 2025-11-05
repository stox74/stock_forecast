"""
us_top_import_hs_codes.py 모듈 사용 예제
"""

# 모듈 import
from us_top_import_hs_code import (
    get_hs_codes,
    get_unique_hs_codes,
    get_hs_code_count,
    get_unique_hs_code_count,
    is_hs_code_in_list,
    get_hs_code_frequency,
    US_TOP_IMPORT_HS_CODES
)


def example_usage():
    """사용 예제"""
    
    print("=" * 60)
    print("미국 수입 상위 500개 상품 HS 코드 - 사용 예제")
    print("=" * 60)
    
    # 방법 1: 전체 리스트 가져오기
    all_codes = get_hs_codes()
    print(f"\n[1] 전체 HS 코드 개수: {get_hs_code_count()}")
    print(f"    처음 10개: {all_codes[:10]}")
    print(f"    마지막 10개: {all_codes[-10:]}")
    
    # 방법 2: 고유 코드만 가져오기 (중복 제거)
    unique_codes = get_unique_hs_codes()
    print(f"\n[2] 고유 HS 코드 개수: {get_unique_hs_code_count()}")
    print(f"    처음 20개 고유 코드: {unique_codes[:20]}")
    
    # 방법 3: 직접 리스트 접근
    print(f"\n[3] 직접 접근 - 인덱스로 특정 코드 가져오기")
    print(f"    첫 번째 코드: {US_TOP_IMPORT_HS_CODES[0]}")
    print(f"    10번째 코드: {US_TOP_IMPORT_HS_CODES[9]}")
    print(f"    100번째 코드: {US_TOP_IMPORT_HS_CODES[99]}")
    
    # 방법 4: 특정 HS 코드 검색
    search_codes = ['270900', '847150', '999999']
    print(f"\n[4] 특정 HS 코드 검색")
    for code in search_codes:
        exists = is_hs_code_in_list(code)
        if exists:
            freq = get_hs_code_frequency(code)
            print(f"    HS 코드 {code}: 리스트에 있음 (출현 {freq}회)")
        else:
            print(f"    HS 코드 {code}: 리스트에 없음")
    
    # 방법 5: HS 코드 빈도 분석
    from collections import Counter
    code_frequency = Counter(US_TOP_IMPORT_HS_CODES)
    print(f"\n[5] HS 코드 빈도 분석")
    if len(code_frequency) == len(US_TOP_IMPORT_HS_CODES):
        print(f"    모든 HS 코드가 고유합니다 (중복 없음)")
    else:
        print(f"    중복되는 상위 10개 HS 코드:")
        for code, count in code_frequency.most_common(10):
            if count > 1:
                print(f"      {code}: {count}회")
    
    # 방법 6: 특정 패턴의 HS 코드 필터링
    print(f"\n[6] 패턴 기반 필터링 예제")
    # 84로 시작하는 HS 코드 찾기 (기계류)
    codes_starting_with_84 = [code for code in unique_codes if code.startswith('84')]
    print(f"    '84'로 시작하는 HS 코드 개수: {len(codes_starting_with_84)}")
    print(f"    예시: {codes_starting_with_84[:10]}")
    
    # 방법 7: 데이터프레임으로 변환 (pandas 사용 시)
    print(f"\n[7] 실제 사용 예제 - 데이터 분석")
    try:
        import pandas as pd
        df = pd.DataFrame({
            'Rank': range(1, len(US_TOP_IMPORT_HS_CODES) + 1),
            'HS_Code': US_TOP_IMPORT_HS_CODES
        })
        print(f"    데이터프레임 생성 성공!")
        print(f"    Shape: {df.shape}")
        print(f"\n    처음 5개 행:")
        print(df.head().to_string(index=False))
    except ImportError:
        print(f"    pandas가 설치되지 않았습니다.")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    example_usage()
