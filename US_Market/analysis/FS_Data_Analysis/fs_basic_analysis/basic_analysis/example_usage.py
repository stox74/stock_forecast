#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SEC 데이터 기반 기업 분석 리포트 생성 예제

이 스크립트는 전체 워크플로우를 보여줍니다:
1. SEC API에서 데이터 수집
2. 재무비율 계산
3. 종합 분석 실행
4. 리포트 및 차트 생성
"""

import sys
import os

# ==========================================
# 방법 1: import_helper 사용 (권장)
# ==========================================
try:
    from import_helper import quick_setup

    quick_setup()
except ImportError:
    print("Note: import_helper를 찾을 수 없습니다. 기본 경로 설정을 시도합니다...")
    # 기본 경로들을 sys.path에 추가
    possible_paths = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
    ]
    for path in possible_paths:
        if path not in sys.path:
            sys.path.insert(0, path)

# ==========================================
# 방법 2: 직접 경로 지정
# ==========================================
# 정확한 경로를 알고 있다면 주석을 해제하고 사용하세요
# sys.path.append(r"C:\Users\YourName\Projects\FinancialAnalysis")
# sys.path.append(r"/home/yourname/projects/financial_analysis")

# ==========================================
# 방법 3: 환경 변수 사용
# ==========================================
# Windows: set FINANCIAL_ANALYSIS_PATH=C:\Users\YourName\Projects
# Linux/Mac: export FINANCIAL_ANALYSIS_PATH=/home/yourname/projects

# 이제 모듈 import
try:
    from financial_data_integrator import integrate_financial_ratios
    from financial_analysis_system import FinancialAnalysisSystem

    print("✓ 모듈 import 성공!")
except ImportError as e:
    print(f"✗ Import 실패: {e}")
    print("\n해결 방법:")
    print("1. import_helper.py를 사용하세요:")
    print("   from import_helper import quick_setup")
    print("   quick_setup()")
    print("\n2. 또는 직접 경로를 지정하세요:")
    print("   sys.path.append(r'C:\\your\\actual\\path')")
    sys.exit(1)


# SEC 데이터 수집 관련 (사용자 환경에 맞게 수정)
# from sec_data_pipeline.collectors.sec_utils import fetch_company_facts
# from sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
# from sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer


def analyze_company(ticker: str, company_name: str,
                    current_price: float = None,
                    shares_outstanding: float = None):
    """
    기업 분석 실행

    Args:
        ticker: 주식 티커 (예: "AAPL", "MSFT")
        company_name: 회사명
        current_price: 현재 주가 (optional)
        shares_outstanding: 발행주식수 (optional)
    """
    print(f"\n{'=' * 80}")
    print(f"Starting Analysis: {company_name} ({ticker})")
    print(f"{'=' * 80}\n")

    try:
        # Step 1: SEC 데이터 수집
        print("[Step 1] Fetching SEC data...")

        # 사용자의 SEC 데이터 수집 방법에 맞게 수정
        # 예시:
        """
        headers = {"User-Agent": "YourName Research <your@email.com>"}
        facts = fetch_company_facts(ticker, headers=headers)

        # Parse and Normalize
        parser = CompanyFactsParser(facts)
        normalizer = FinancialNormalizer(parser)
        df_q = normalizer.create_normalized_dataframe("quarterly")
        """

        # 임시: 이미 준비된 DataFrame이 있다고 가정
        # df_normalized = your_prepared_dataframe

        # 실제 사용시에는 위의 주석을 해제하고 데이터를 로드하세요
        print("  Note: 실제 사용시 SEC 데이터 수집 코드를 활성화하세요")
        return None

        # Step 2: 재무비율 계산
        print("\n[Step 2] Calculating financial ratios...")
        df_with_ratios = integrate_financial_ratios(df_normalized)
        print(f"  Total columns: {len(df_with_ratios.columns)}")
        print(f"  Date range: {df_with_ratios.index[0]} to {df_with_ratios.index[-1]}")

        # Step 3: 분석 시스템 초기화
        print("\n[Step 3] Initializing analysis system...")
        analyzer = FinancialAnalysisSystem(df_with_ratios)

        # Step 4: 전체 리포트 생성
        print("\n[Step 4] Generating comprehensive report...")
        results = analyzer.generate_full_report(
            company_name=company_name,
            ticker=ticker,
            output_dir="../financial_reports",
            current_price=current_price,
            shares_outstanding=shares_outstanding
        )

        print(f"\n{'=' * 80}")
        print("Analysis Complete!")
        print(f"{'=' * 80}")
        print(f"\nOutput Directory: {results['output_dir']}")
        print(f"Report: {results['report_path']}")
        print(f"Data: {results['data_path']}")

        # Step 5: 주요 결과 출력
        print(f"\n{'=' * 80}")
        print("KEY INSIGHTS")
        print(f"{'=' * 80}")

        # 최근 데이터
        latest = df_with_ratios.iloc[-1]
        print(f"\nLatest Quarter ({df_with_ratios.index[-1].strftime('%Y-%m-%d')}):")
        print(f"  ROE: {latest.get('roe', 'N/A')}")
        print(f"  Net Margin: {latest.get('net_margin', 'N/A')}")
        print(f"  Current Ratio: {latest.get('current_ratio', 'N/A')}")
        print(f"  D/E Ratio: {latest.get('debt_to_equity', 'N/A')}")

        # 성장률
        if 'growth_rates' in results['results']:
            growth = results['results']['growth_rates'].iloc[-1]
            print(f"\nGrowth Rates:")
            print(f"  Revenue YoY: {growth.get('revenue_yoy_growth', 'N/A')}%")
            print(f"  Net Income YoY: {growth.get('net_income_yoy_growth', 'N/A')}%")

        # 예측
        if 'forecast' in results['results']:
            forecast = results['results']['forecast']
            print(f"\nForecast (Next Quarter):")
            print(f"  Revenue: {forecast.iloc[0].get('revenue_forecast', 'N/A'):,.0f}")
            print(f"  Operating Income: {forecast.iloc[0].get('operating_income_forecast', 'N/A'):,.0f}")

        return results

    except Exception as e:
        print(f"\nError during analysis: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def batch_analyze_companies(companies: list):
    """
    여러 기업 배치 분석

    Args:
        companies: [(ticker, company_name, price, shares), ...] 리스트
    """
    print(f"\n{'=' * 80}")
    print(f"BATCH ANALYSIS - {len(companies)} companies")
    print(f"{'=' * 80}\n")

    results = {}

    for i, company_info in enumerate(companies, 1):
        ticker, company_name = company_info[0], company_info[1]
        price = company_info[2] if len(company_info) > 2 else None
        shares = company_info[3] if len(company_info) > 3 else None

        print(f"\n[{i}/{len(companies)}] Processing {ticker}...")

        result = analyze_company(ticker, company_name, price, shares)
        results[ticker] = result

        print(f"{'=' * 80}\n")

    return results


def quick_analysis_demo():
    """
    빠른 분석 데모 (단일 차트만 생성)
    """
    print("\nQuick Analysis Demo")
    print("=" * 80)
    print("이 데모는 이미 준비된 DataFrame으로 빠른 분석을 수행합니다.")
    print("실제 사용시에는 analyze_company() 함수를 사용하세요.")
    print("=" * 80)

    # 실제 사용 예시:
    """
    # 1. DataFrame 준비
    df_with_ratios = your_prepared_dataframe

    # 2. 분석 시스템 초기화
    analyzer = FinancialAnalysisSystem(df_with_ratios)

    # 3. 개별 분석 실행
    analyzer.calculate_growth_rates()
    analyzer.analyze_profitability_trends()

    # 4. 차트 생성
    analyzer.create_growth_chart("./growth_chart.png")
    analyzer.create_profitability_chart("./profitability_chart.png")

    # 5. 텍스트 리포트
    report = analyzer.generate_summary_report()
    print(report)
    """

    pass


def main():
    """
    메인 실행 함수
    """
    print("\n" + "=" * 80)
    print("SEC Financial Analysis System")
    print("=" * 80)

    # 사용 방법 선택
    print("\nUsage Options:")
    print("1. Single company analysis")
    print("2. Batch analysis (multiple companies)")
    print("3. Quick demo (individual charts)")
    print("\n" + "=" * 80)

    # Option 1: 단일 기업 분석
    print("\n[Option 1] Single Company Analysis")
    print("-" * 80)
    print("Example:")
    print("""
    results = analyze_company(
        ticker="AAPL",
        company_name="Apple Inc.",
        current_price=150.0,
        shares_outstanding=16000000000
    )
    """)

    # Option 2: 배치 분석
    print("\n[Option 2] Batch Analysis")
    print("-" * 80)
    print("Example:")
    print("""
    companies = [
        ("AAPL", "Apple Inc.", 150.0, 16000000000),
        ("MSFT", "Microsoft Corp.", 300.0, 7500000000),
        ("GOOGL", "Alphabet Inc.", 130.0, 13000000000),
    ]

    batch_results = batch_analyze_companies(companies)
    """)

    # Option 3: 빠른 데모
    print("\n[Option 3] Quick Demo")
    print("-" * 80)
    print("Example:")
    print("""
    quick_analysis_demo()
    """)

    print("\n" + "=" * 80)
    print("실행하려면 위의 예제 코드를 활성화하세요.")
    print("=" * 80)


if __name__ == "__main__":
    main()