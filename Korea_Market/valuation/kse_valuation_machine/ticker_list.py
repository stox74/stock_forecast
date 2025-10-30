"""
티커 리스트 관리 파일
- 예측할 종목 리스트를 이곳에서 관리합니다
- hs_code: 수출 데이터와 연동할 HS Code (없으면 None)
"""

# 전체 티커 리스트
ALL_TICKERS = [
    {'ticker': 'A005930', 'hs_code': '854232'},  # 삼성전자 - 반도체
    {'ticker': 'A000660', 'hs_code': '854232'},     # SK하이닉스
    {'ticker': 'A051910', 'hs_code': None},     # LG화학
    {'ticker': 'A035420', 'hs_code': None},     # NAVER
    {'ticker': 'A005380', 'hs_code': '8703'},     # 현대차
    {'ticker': 'A006400', 'hs_code': '850760'},     # 삼성SDI
    {'ticker': 'A035720', 'hs_code': None},     # 카카오
    {'ticker': 'A000270', 'hs_code': '8703'},     # 기아
    {'ticker': 'A068270', 'hs_code': '300214'},     # 셀트리온
    {'ticker': 'A207940', 'hs_code': '300214'},     # 삼성바이오로직스
    {'ticker': 'A042700', 'hs_code': '854232'}, # 한미반도체
    {'ticker': 'A043150', 'hs_code': '902213'},     # 바텍
    {'ticker': 'A131290', 'hs_code': '903090'},  # 티에스이
    {'ticker': 'A006910', 'hs_code': '853720'},  # 보성파워텍
    {'ticker': 'A140860', 'hs_code': '901210'},  # 파크시스템
    {'ticker': 'A009150', 'hs_code': '8542'},  # 삼성전기
    {'ticker': 'A095610', 'hs_code': '8486208410'},  # 테스
]

# 테스트용 소수 티커 리스트
TEST_TICKERS = [
    {'ticker': 'A131290', 'hs_code': '903090'},  # 티에스이
    {'ticker': 'A043150', 'hs_code': '902213'},     # 바텍
    {'ticker': 'A140860', 'hs_code': '901210'},     # 파크시스템
]

# 반도체 섹터
SEMICONDUCTOR_TICKERS = [
    {'ticker': 'A131290', 'hs_code': '8542'},  # 삼성전자
    {'ticker': 'A000660', 'hs_code': '902213'},     # SK하이닉스
    {'ticker': 'A006400', 'hs_code': '902213'},     # 삼성SDI
]

# IT 섹터
IT_TICKERS = [
    {'ticker': 'A035420', 'hs_code': None},     # NAVER
    {'ticker': 'A035720', 'hs_code': None},     # 카카오
]

# 자동차 섹터
AUTO_TICKERS = [
    {'ticker': 'A005380', 'hs_code': None},     # 현대차
    {'ticker': 'A000270', 'hs_code': None},     # 기아
]

# 바이오 섹터
BIO_TICKERS = [
    {'ticker': 'A068270', 'hs_code': None},     # 셀트리온
    {'ticker': 'A207940', 'hs_code': None},     # 삼성바이오로직스
]

# 화학 섹터
CHEMICAL_TICKERS = [
    {'ticker': 'A051910', 'hs_code': None},     # LG화학
]


# 사용자 정의 그룹 추가 예시
# CUSTOM_GROUP_1 = [
#     {'ticker': 'A123456', 'hs_code': None},
#     {'ticker': 'A789012', 'hs_code': '1234'},
# ]


def get_ticker_list(group='all'):
    """
    티커 리스트를 반환하는 함수
    
    Parameters:
    -----------
    group : str
        'all' : 전체 티커 (기본값)
        'test' : 테스트용 3개
        'semiconductor' : 반도체 섹터
        'it' : IT 섹터
        'auto' : 자동차 섹터
        'bio' : 바이오 섹터
        'chemical' : 화학 섹터
    
    Returns:
    --------
    list : 티커 딕셔너리 리스트
    """
    groups = {
        'all': ALL_TICKERS,
        'test': TEST_TICKERS,
        'semiconductor': SEMICONDUCTOR_TICKERS,
        'it': IT_TICKERS,
        'auto': AUTO_TICKERS,
        'bio': BIO_TICKERS,
        'chemical': CHEMICAL_TICKERS,
    }
    
    if group.lower() in groups:
        return groups[group.lower()]
    else:
        print(f"⚠️  알 수 없는 그룹: {group}")
        print(f"사용 가능한 그룹: {', '.join(groups.keys())}")
        return ALL_TICKERS


def add_ticker(ticker, hs_code=None, group='custom'):
    """
    새로운 티커를 추가하는 함수 (런타임에서 사용)
    
    Parameters:
    -----------
    ticker : str
        종목 코드 (예: 'A005930')
    hs_code : str or None
        HS Code (없으면 None)
    group : str
        추가할 그룹 이름
    
    Returns:
    --------
    dict : 추가된 티커 정보
    """
    new_ticker = {'ticker': ticker, 'hs_code': hs_code}
    return new_ticker


def get_tickers_by_hs_code(hs_code):
    """
    특정 HS Code를 가진 티커만 필터링
    
    Parameters:
    -----------
    hs_code : str
        HS Code (예: '8542')
    
    Returns:
    --------
    list : 필터링된 티커 리스트
    """
    return [t for t in ALL_TICKERS if t['hs_code'] == hs_code]


def get_tickers_without_hs_code():
    """
    HS Code가 없는 티커만 필터링
    
    Returns:
    --------
    list : HS Code가 None인 티커 리스트
    """
    return [t for t in ALL_TICKERS if t['hs_code'] is None]


# 사용 예시
if __name__ == "__main__":
    print("=" * 60)
    print("티커 리스트 관리 파일")
    print("=" * 60)
    
    print(f"\n전체 티커 수: {len(ALL_TICKERS)}")
    print(f"테스트 티커 수: {len(TEST_TICKERS)}")
    
    print("\n전체 티커 목록:")
    for t in ALL_TICKERS:
        hs_info = f"(HS: {t['hs_code']})" if t['hs_code'] else "(HS: 없음)"
        print(f"  - {t['ticker']} {hs_info}")
    
    print("\n섹터별 티커 수:")
    print(f"  - 반도체: {len(SEMICONDUCTOR_TICKERS)}개")
    print(f"  - IT: {len(IT_TICKERS)}개")
    print(f"  - 자동차: {len(AUTO_TICKERS)}개")
    print(f"  - 바이오: {len(BIO_TICKERS)}개")
    print(f"  - 화학: {len(CHEMICAL_TICKERS)}개")
    
    print("\n함수 테스트:")
    test_list = get_ticker_list('test')
    print(f"  get_ticker_list('test'): {len(test_list)}개 반환")
    
    hs_list = get_tickers_by_hs_code('8542')
    print(f"  get_tickers_by_hs_code('8542'): {len(hs_list)}개 반환")
    
    no_hs_list = get_tickers_without_hs_code()
    print(f"  get_tickers_without_hs_code(): {len(no_hs_list)}개 반환")
