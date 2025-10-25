"""
주식 가치평가 예측 시스템 - 실행 예제 스크립트

이 스크립트는 개선된 예측 시스템의 사용 예제를 보여줍니다.
"""

from improved_forecast_system import (
    process_single_ticker,
    process_multiple_tickers,
    get_db_host
)

# ==================== 설정 ====================

# DB 연결 정보
db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# 테이블 이름
table_name = 'Korea_company_valuation_ver2'

# 에러 로그 파일명
error_log_file = 'forecast_error_log.txt'


# ==================== 예제 1: 단일 티커 처리 ====================

def example_single_ticker():
    """단일 티커 처리 예제"""
    print("=" * 60)
    print("예제 1: 단일 티커 처리")
    print("=" * 60)
    
    ticker = 'A084370'
    hs_code = None  # 외생변수 없이 예측
    
    print(f"\n티커 {ticker} 처리 시작...")
    
    success = process_single_ticker(
        ticker=ticker,
        hs_code=hs_code,
        db_info=db_info,
        table_name=table_name
    )
    
    if success:
        print(f"✅ {ticker} 처리 성공!")
    else:
        print(f"❌ {ticker} 처리 실패")
    
    print("\n" + "=" * 60 + "\n")


# ==================== 예제 2: 소수 티커 일괄 처리 ====================

def example_small_batch():
    """소수 티커 일괄 처리 예제 (테스트용)"""
    print("=" * 60)
    print("예제 2: 소수 티커 일괄 처리 (3개)")
    print("=" * 60)
    
    ticker_list = [
        {'ticker': 'A084370', 'hs_code': None},
        {'ticker': 'A005930', 'hs_code': '8542'},
        {'ticker': 'A000660', 'hs_code': None},
    ]
    
    results = process_multiple_tickers(
        ticker_list=ticker_list,
        db_info=db_info,
        table_name=table_name,
        error_log_file=error_log_file
    )
    
    print("\n" + "=" * 60)
    print("처리 결과 요약:")
    print("=" * 60)
    print(f"총 티커 수: {results['total']}")
    print(f"성공: {results['success']} ({results['success']/results['total']*100:.1f}%)")
    print(f"실패: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
    
    if results['error_tickers']:
        print(f"\n실패 티커: {', '.join(results['error_tickers'])}")
    
    print("\n" + "=" * 60 + "\n")


# ==================== 예제 3: 대량 티커 일괄 처리 ====================

def example_large_batch():
    """대량 티커 일괄 처리 예제"""
    print("=" * 60)
    print("예제 3: 대량 티커 일괄 처리 (10개)")
    print("=" * 60)
    
    # 실제 티커 리스트 (예시)
    ticker_list = [
        {'ticker': 'A005930', 'hs_code': '8542'},  # 삼성전자 - 반도체
        {'ticker': 'A000660', 'hs_code': None},     # SK하이닉스
        {'ticker': 'A051910', 'hs_code': None},     # LG화학
        {'ticker': 'A035420', 'hs_code': None},     # NAVER
        {'ticker': 'A005380', 'hs_code': None},     # 현대차
        {'ticker': 'A006400', 'hs_code': None},     # 삼성SDI
        {'ticker': 'A035720', 'hs_code': None},     # 카카오
        {'ticker': 'A000270', 'hs_code': None},     # 기아
        {'ticker': 'A068270', 'hs_code': None},     # 셀트리온
        {'ticker': 'A207940', 'hs_code': None},     # 삼성바이오로직스
    ]
    
    print(f"\n총 {len(ticker_list)}개 티커 처리 시작...")
    print("진행 상황은 진행 바를 통해 확인하세요.\n")
    
    results = process_multiple_tickers(
        ticker_list=ticker_list,
        db_info=db_info,
        table_name=table_name,
        error_log_file=error_log_file
    )
    
    print("\n" + "=" * 60)
    print("처리 결과 요약:")
    print("=" * 60)
    print(f"총 티커 수: {results['total']}")
    print(f"성공: {results['success']} ({results['success']/results['total']*100:.1f}%)")
    print(f"실패: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
    
    if results['error_tickers']:
        print(f"\n실패 티커: {', '.join(results['error_tickers'])}")
        print(f"상세 에러 로그: {error_log_file}")
    
    print("\n" + "=" * 60 + "\n")


# ==================== 예제 4: CSV 파일에서 티커 읽기 ====================

def example_from_csv(csv_file='ticker_list.csv'):
    """CSV 파일에서 티커 리스트를 읽어 처리하는 예제"""
    print("=" * 60)
    print("예제 4: CSV 파일에서 티커 읽기")
    print("=" * 60)
    
    import pandas as pd
    
    try:
        # CSV 파일 읽기
        # CSV 형식: ticker, hs_code
        # 예: A005930,8542
        #     A000660,
        df = pd.read_csv(csv_file)
        
        ticker_list = []
        for _, row in df.iterrows():
            ticker_list.append({
                'ticker': row['ticker'],
                'hs_code': row['hs_code'] if pd.notna(row['hs_code']) else None
            })
        
        print(f"\nCSV에서 {len(ticker_list)}개 티커 로드 완료")
        print("처리 시작...\n")
        
        results = process_multiple_tickers(
            ticker_list=ticker_list,
            db_info=db_info,
            table_name=table_name,
            error_log_file=error_log_file
        )
        
        print("\n" + "=" * 60)
        print("처리 결과 요약:")
        print("=" * 60)
        print(f"총 티커 수: {results['total']}")
        print(f"성공: {results['success']} ({results['success']/results['total']*100:.1f}%)")
        print(f"실패: {results['failed']} ({results['failed']/results['total']*100:.1f}%)")
        
        if results['error_tickers']:
            print(f"\n실패 티커: {', '.join(results['error_tickers'])}")
        
        print("\n" + "=" * 60 + "\n")
        
    except FileNotFoundError:
        print(f"❌ CSV 파일을 찾을 수 없습니다: {csv_file}")
        print("\nCSV 파일 형식 예시:")
        print("ticker,hs_code")
        print("A005930,8542")
        print("A000660,")
        print("A051910,")
    except Exception as e:
        print(f"❌ CSV 처리 중 오류: {e}")


# ==================== 예제 5: 배치 단위 처리 ====================

def example_batch_processing(batch_size=20):
    """대량 티커를 배치 단위로 나누어 처리하는 예제"""
    print("=" * 60)
    print(f"예제 5: 배치 단위 처리 (배치 크기: {batch_size})")
    print("=" * 60)
    
    # 전체 티커 리스트 (예시: 50개)
    all_tickers = []
    for i in range(50):
        all_tickers.append({
            'ticker': f'A{str(i).zfill(6)}',
            'hs_code': None
        })
    
    print(f"\n총 {len(all_tickers)}개 티커를 {batch_size}개씩 배치 처리")
    
    total_results = {
        'total': 0,
        'success': 0,
        'failed': 0,
        'error_tickers': []
    }
    
    # 배치 단위로 나누어 처리
    for batch_num in range(0, len(all_tickers), batch_size):
        batch = all_tickers[batch_num:batch_num + batch_size]
        
        print(f"\n{'='*60}")
        print(f"배치 {batch_num//batch_size + 1} 처리 중... ({len(batch)}개 티커)")
        print(f"{'='*60}")
        
        results = process_multiple_tickers(
            ticker_list=batch,
            db_info=db_info,
            table_name=table_name,
            error_log_file=f'error_log_batch_{batch_num//batch_size + 1}.txt'
        )
        
        # 결과 누적
        total_results['total'] += results['total']
        total_results['success'] += results['success']
        total_results['failed'] += results['failed']
        total_results['error_tickers'].extend(results['error_tickers'])
    
    print("\n" + "=" * 60)
    print("전체 배치 처리 완료!")
    print("=" * 60)
    print(f"총 티커 수: {total_results['total']}")
    print(f"성공: {total_results['success']} ({total_results['success']/total_results['total']*100:.1f}%)")
    print(f"실패: {total_results['failed']} ({total_results['failed']/total_results['total']*100:.1f}%)")
    
    if total_results['error_tickers']:
        print(f"\n총 실패 티커 수: {len(total_results['error_tickers'])}")
        print(f"실패 티커: {', '.join(total_results['error_tickers'][:10])}...")  # 처음 10개만 표시
    
    print("\n" + "=" * 60 + "\n")


# ==================== 메인 실행 ====================

def main():
    """메인 실행 함수"""
    print("\n" + "=" * 60)
    print("주식 가치평가 예측 시스템 - 실행 예제")
    print("=" * 60)
    
    print("\n실행할 예제를 선택하세요:")
    print("1. 단일 티커 처리")
    print("2. 소수 티커 일괄 처리 (3개)")
    print("3. 대량 티커 일괄 처리 (10개)")
    print("4. CSV 파일에서 티커 읽기")
    print("5. 배치 단위 처리 (50개)")
    print("0. 종료")
    
    choice = input("\n선택 (0-5): ").strip()
    
    if choice == '1':
        example_single_ticker()
    elif choice == '2':
        example_small_batch()
    elif choice == '3':
        example_large_batch()
    elif choice == '4':
        csv_file = input("CSV 파일명을 입력하세요 (기본: ticker_list.csv): ").strip()
        if not csv_file:
            csv_file = 'ticker_list.csv'
        example_from_csv(csv_file)
    elif choice == '5':
        example_batch_processing(batch_size=20)
    elif choice == '0':
        print("프로그램을 종료합니다.")
    else:
        print("잘못된 선택입니다. 0-5 사이의 숫자를 입력하세요.")


if __name__ == "__main__":
    # 사용자 선택 실행
    main()
    
    # 또는 직접 함수 호출
    # example_single_ticker()
    # example_small_batch()
    # example_large_batch()
