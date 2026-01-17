"""
Trail PER/PBR Calculator 사용 예시

이 스크립트는 FMP API를 사용하여 Trail PER/PBR을 계산하는 예제입니다.
"""

from trail_per_pbr_calculator import TrailValuationCalculator

def example_single_stock_daily():
    """예제 1: 단일 종목 일별 데이터 분석"""
    print("\n" + "="*80)
    print("예제 1: 단일 종목 일별 Trail PER/PBR 분석")
    print("="*80)
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results'
    )
    
    daily_df = calculator.analyze(
        symbol="AAPL",
        start_date="2023-01-01",
        end_date="2024-12-31",
        frequency='daily'
    )
    
    return daily_df


def example_single_stock_monthly():
    """예제 2: 단일 종목 월별 데이터 분석"""
    print("\n" + "="*80)
    print("예제 2: 단일 종목 월별 Trail PER/PBR 분석")
    print("="*80)
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results'
    )
    
    monthly_df = calculator.analyze(
        symbol="MSFT",
        start_date="2022-01-01",
        end_date="2024-12-31",
        frequency='monthly'
    )
    
    return monthly_df


def example_multiple_stocks():
    """예제 3: 여러 종목 분석"""
    print("\n" + "="*80)
    print("예제 3: 여러 종목 월별 Trail PER/PBR 분석")
    print("="*80)
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results_multiple'
    )
    
    symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']
    
    results = {}
    
    for symbol in symbols:
        print(f"\n{'='*80}")
        print(f"{symbol} 분석 시작")
        print(f"{'='*80}")
        
        try:
            df = calculator.analyze(
                symbol=symbol,
                start_date="2023-01-01",
                end_date="2024-12-31",
                frequency='monthly'
            )
            results[symbol] = df
            print(f"\n{symbol} 분석 성공!")
            
        except Exception as e:
            print(f"\n{symbol} 분석 실패: {e}")
            results[symbol] = None
    
    success_count = sum(1 for v in results.values() if v is not None)
    print(f"\n\n총 {len(symbols)}개 종목 중 {success_count}개 성공")
    
    return results


def example_custom_period():
    """예제 4: 특정 기간 분석"""
    print("\n" + "="*80)
    print("예제 4: 특정 기간 Trail PER/PBR 분석 (최근 6개월)")
    print("="*80)
    
    from datetime import datetime, timedelta
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results'
    )
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180)
    
    df = calculator.analyze(
        symbol="NVDA",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        frequency='daily'
    )
    
    return df


def example_korean_stock():
    """예제 5: 한국 주식 분석 (가능한 경우)"""
    print("\n" + "="*80)
    print("예제 5: 한국 주식 Trail PER/PBR 분석")
    print("="*80)
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results_korea'
    )
    
    # 삼성전자 ADR (미국 상장)
    df = calculator.analyze(
        symbol="SSNLF",
        start_date="2023-01-01",
        end_date="2024-12-31",
        frequency='monthly'
    )
    
    return df


def example_get_latest_metrics():
    """예제 6: 최신 TTM 재무지표만 가져오기"""
    print("\n" + "="*80)
    print("예제 6: 최신 TTM 재무지표 조회")
    print("="*80)
    
    API_KEY = "YOUR_FMP_API_KEY_HERE"
    
    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_results'
    )
    
    symbol = "AAPL"
    
    ttm_metrics = calculator.get_ttm_metrics(symbol)
    print(f"\n{symbol} 최신 TTM 재무지표:")
    print(ttm_metrics[['date', 'revenuePerShare', 'netIncomePerShare', 
                       'bookValuePerShare', 'peRatio', 'priceToBookRatio']].head())
    
    quarterly_metrics = calculator.get_quarterly_metrics(symbol, limit=8)
    print(f"\n{symbol} 최근 8분기 재무지표:")
    print(quarterly_metrics[['date', 'revenuePerShare', 'netIncomePerShare', 
                            'bookValuePerShare']].head(8))
    
    return ttm_metrics, quarterly_metrics


def main():
    """메인 함수 - 원하는 예제 선택"""
    
    print("\n" + "="*80)
    print("Trail PER/PBR Calculator - 사용 예제")
    print("="*80)
    print("\n사용할 예제를 선택하세요:")
    print("1. 단일 종목 일별 분석 (AAPL)")
    print("2. 단일 종목 월별 분석 (MSFT)")
    print("3. 여러 종목 분석 (AAPL, MSFT, GOOGL, AMZN, TSLA)")
    print("4. 특정 기간 분석 (NVDA, 최근 6개월)")
    print("5. 한국 주식 분석 (삼성전자 ADR)")
    print("6. 최신 TTM 재무지표만 조회")
    print("0. 모든 예제 실행")
    
    choice = input("\n선택 (0-6): ").strip()
    
    if choice == "1":
        example_single_stock_daily()
    elif choice == "2":
        example_single_stock_monthly()
    elif choice == "3":
        example_multiple_stocks()
    elif choice == "4":
        example_custom_period()
    elif choice == "5":
        example_korean_stock()
    elif choice == "6":
        example_get_latest_metrics()
    elif choice == "0":
        print("\n모든 예제를 순차적으로 실행합니다...\n")
        example_single_stock_daily()
        example_single_stock_monthly()
        example_multiple_stocks()
        example_custom_period()
        example_korean_stock()
        example_get_latest_metrics()
    else:
        print("올바른 번호를 선택해주세요.")


if __name__ == "__main__":
    
    print("""
    ╔════════════════════════════════════════════════════════════════════╗
    ║                                                                    ║
    ║         Trail PER/PBR Calculator - 사용 예제 스크립트              ║
    ║                                                                    ║
    ║  ※ 주의: 실행 전 API_KEY를 본인의 FMP API 키로 변경하세요!         ║
    ║                                                                    ║
    ╚════════════════════════════════════════════════════════════════════╝
    """)
    
    API_KEY = input("\nFMP API 키를 입력하세요: ").strip()
    
    if not API_KEY or API_KEY == "YOUR_FMP_API_KEY_HERE":
        print("\n경고: 유효한 API 키를 입력해야 합니다.")
        print("FMP API 키는 https://site.financialmodelingprep.com/ 에서 발급받을 수 있습니다.")
    else:
        import sys
        sys.path.insert(0, '../../../../../../../../../Downloads')
        
        main()
