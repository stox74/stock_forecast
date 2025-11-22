"""
티커 리스트 관리 파일
- 예측할 종목 리스트를 이곳에서 관리합니다
- hs_code: 수출 데이터와 연동할 HS Code (없으면 None)
"""

# 전체 티커 리스트
ALL_TICKERS = [
    {'ticker': 'A005930', 'hs_code': '854232'},  # 삼성전자 - 반도체 0
    {'ticker': 'A000660', 'hs_code': '854232'},     # SK하이닉스 0
    {'ticker': 'A051910', 'hs_code': None},     # LG화학
    {'ticker': 'A035420', 'hs_code': None},     # NAVER
    {'ticker': 'A005380', 'hs_code': '8703'},    # 현대차 0
    {'ticker': 'A006400', 'hs_code': '850760'},  # 삼성SDI 0
    {'ticker': 'A035720', 'hs_code': None},      # 카카오
    {'ticker': 'A000270', 'hs_code': '8703'},    # 기아 0
    {'ticker': 'A068270', 'hs_code': '300214'},  # 셀트리온
    {'ticker': 'A207940', 'hs_code': '300214'},  # 삼성바이오로직스
    {'ticker': 'A042700', 'hs_code': '854232'},  # 한미반도체 0
    {'ticker': 'A043150', 'hs_code': '902213'},  # 바텍
    {'ticker': 'A131290', 'hs_code': '903090'},  # 티에스이 0
    {'ticker': 'A006910', 'hs_code': '853720'},  # 보성파워텍 0
    {'ticker': 'A140860', 'hs_code': '901210'},  # 파크시스템
    {'ticker': 'A009150', 'hs_code': '8542'},    # 삼성전기 0
    {'ticker': 'A095610', 'hs_code': '8486208410'},  # 테스 0
    {'ticker': 'A001440', 'hs_code': '854460'},  # 대한전선
    {'ticker': 'A000500', 'hs_code': '854460'},  # 가온전선
    {'ticker': 'A004000', 'hs_code': '281512'},  # 롯데정밀화학
    {'ticker': 'A010120', 'hs_code': '850650'},  # LS ELEC 0
    {'ticker': 'A044820', 'hs_code': '330499'},  # 코스맥스 비티아이 0
    {'ticker': 'A010140', 'hs_code': '8408103000'},  # 삼성중공업 0
    {'ticker': 'A011780', 'hs_code': '292610'},  # 금호석유화학
    {'ticker': 'A033500', 'hs_code': '730890'},  # 동성화인텍 0
    {'ticker': 'A036190', 'hs_code': '8502'},    # 금화피에스시 0
    {'ticker': 'A042370', 'hs_code': '853530'},  # 비츠로테크 0
    {'ticker': 'A042660', 'hs_code': '8408103000'},  # 한화오션 0
    {'ticker': 'A375500', 'hs_code': '850423'},  # DL이앤씨 0
    {'ticker': 'A052690', 'hs_code': '850423'},  # 한전기술 0
    {'ticker': 'A068270', 'hs_code': '300214'},  # 삼성바이오로직스
    {'ticker': 'A071280', 'hs_code': '848630'},  # 로체시스템 0
    {'ticker': 'A077360', 'hs_code': '710610'},  # 덕산하이메탈 0
    {'ticker': 'A082740', 'hs_code': '8408103000'},  # 한화엔진 0
    {'ticker': 'A103140', 'hs_code': '7404'},  # 풍산 0
    {'ticker': 'A103590', 'hs_code': '854442'},  # 일진전기
    {'ticker': 'A105630', 'hs_code': '6203'},  # 한세실업 0
    {'ticker': 'A114810', 'hs_code': '848640'},  # 한솔아이원스 0
    {'ticker': 'A123700', 'hs_code': '840820'},  # SJM 0
    {'ticker': 'A161390 ', 'hs_code': '401120'},  # 한국타이어
    {'ticker': 'A073240', 'hs_code': '401120'},  # 금호타이어
    {'ticker': 'A002350', 'hs_code': '401120'},  # 넥센타이어
    {'ticker': 'A253590', 'hs_code': '9031809091'},  # 네오셈
    {'ticker': 'A298040', 'hs_code': '850422'},  # 효성중공업 0
    {'ticker': 'A007690', 'hs_code': '390730'},  # 국도화학
    {'ticker': 'A131970', 'hs_code': '854232'},  # 두산테스나 0
    {'ticker': 'A033100', 'hs_code': '8504'},  # 제룡전기 0
    {'ticker': 'A098120', 'hs_code': '854232'},  # 마이크로 컨택솔 0
    {'ticker': 'A214150', 'hs_code': None},  # 클래시스
    {'ticker': 'A009160', 'hs_code': None},  # 심팩
    {'ticker': 'A058470', 'hs_code': "848690"},  # 리노산업 상관관계 0.8
    {'ticker': 'A003230', 'hs_code': "1902301010"},  # 삼양식품 상관관계 0.6
    {'ticker': 'A353200', 'hs_code': "853400"},  # 대덕전자 상관관계 0.6
    {'ticker': 'A007660', 'hs_code': "8529909643"}, #이수페타시스 상관관계 0.55
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
