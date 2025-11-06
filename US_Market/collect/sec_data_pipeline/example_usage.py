#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SEC Data Pipeline 종합 사용 예시
"""

import sys
from pathlib import Path

# 프로젝트 경로 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from collectors import SECAPIClient, RateLimiter, BulkDownloader
from parsers import CompanyFactsParser, FinancialNormalizer
from storage import DBManager
from DATA.stock_invest_function import *

def example_1_basic_usage():
    """예시 1: 기본 사용법 - 단일 기업 데이터 수집"""
    print("\n" + "=" * 80)
    print("예시 1: 기본 사용법 - Apple(AAPL) 재무데이터 수집")
    print("=" * 80)

    # 1. SEC API 클라이언트 설정
    user_agent = "PersonalResearch stox1224@email.com"  # 실제 이메일로 수정됨
    rate_limiter = RateLimiter(max_calls=10, time_window=1.0)
    client = SECAPIClient(user_agent, rate_limiter)

    # 2. Company Facts 데이터 가져오기
    print("\n1. Fetching Apple company facts from SEC...")
    company_facts = client.get_company_facts_by_ticker('AAPL')

    if not company_facts:
        print("✗ Failed to fetch company facts")
        return

    # 3. 데이터 파싱
    print("\n2. Parsing company facts...")
    parser = CompanyFactsParser(company_facts)
    print(f"  Entity: {parser.entity_name}")
    print(f"  CIK: {parser.cik}")
    print(f"  Available taxonomies: {parser.get_available_taxonomies()}")

    # 4. 재무데이터 정규화
    print("\n3. Normalizing financial data...")
    normalizer = FinancialNormalizer(parser)
    df = normalizer.create_normalized_dataframe(period_type='quarterly')
    print(f"  Shape: {df.shape}")
    print(f"  Date range: {df.index.min()} ~ {df.index.max()}")
    print(f"  Items: {list(df.columns)}")

    # 5. Billions 변환 및 TTM 계산
    print("\n4. Converting to billions and calculating TTM...")
    df_billions = normalizer.convert_to_billions(df)
    df_ttm = normalizer.calculate_ttm(df_billions, columns=['revenue', 'net_income'])

    print("\n5. Recent quarterly revenue (billions):")
    if 'revenue' in df_ttm.columns and 'revenue_ttm' in df_ttm.columns:
        print(df_ttm[['revenue', 'revenue_ttm']].tail(8))

    # 6. CSV 저장
    print("\n6. Exporting to CSV...")
    normalizer.export_to_csv(df_billions, 'aapl_financial_data.csv')

    print("\n✓ Example 1 completed")


def example_2_multiple_companies():
    """예시 2: 여러 기업 데이터 수집 및 비교"""
    print("\n" + "=" * 80)
    print("예시 2: 여러 기업 데이터 수집 및 Revenue 비교")
    print("=" * 80)

    # 설정
    user_agent = "PersonalResearch stox1224@email.com"
    client = SECAPIClient(user_agent, RateLimiter(10, 1.0))
    downloader = BulkDownloader(client, output_dir="./sec_data", max_workers=3)

    # 비교할 기업들
    tickers = ['AAPL', 'MSFT', 'GOOGL']

    print(f"\n1. Downloading company facts for {len(tickers)} companies...")
    results = downloader.download_company_facts_batch(tickers)

    if not results:
        print("✗ No data downloaded")
        return

    # 각 기업의 revenue 데이터 추출
    print("\n2. Extracting and normalizing revenue data...")
    import pandas as pd
    revenue_comparison = {}

    for ticker, company_facts in results.items():
        try:
            parser = CompanyFactsParser(company_facts)
            normalizer = FinancialNormalizer(parser)

            # Revenue 데이터 정규화
            revenue_series = normalizer.normalize_single_item('revenue', period_type='quarterly')

            if revenue_series is not None:
                # Billions로 변환
                revenue_comparison[ticker] = revenue_series / 1_000_000_000
                print(f"  ✓ {ticker}: {len(revenue_series)} quarters")

        except Exception as e:
            print(f"  ✗ {ticker}: {e}")

    if revenue_comparison:
        # DataFrame으로 결합
        comparison_df = pd.DataFrame(revenue_comparison)

        print("\n3. Revenue comparison (last 8 quarters, in billions):")
        print(comparison_df.tail(8))

        # 성장률 계산
        print("\n4. YoY Revenue Growth (%):")
        growth_df = comparison_df.pct_change(periods=4) * 100
        print(growth_df.tail(4))

        # CSV 저장
        comparison_df.to_csv('revenue_comparison.csv')
        print("\n✓ Saved to revenue_comparison.csv")

    print("\n✓ Example 2 completed")


def example_3_database_storage():
    """예시 3: 데이터베이스 저장 및 조회"""
    print("\n" + "=" * 80)
    print("예시 3: 데이터베이스 저장 및 조회")
    print("=" * 80)

    # DB 설정을 함수 내부에서 정의
    db_config = {
        'host': get_db_host(),
        'port': 3307,
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'database': 'investar'
    }

    print("\n[주의] 데이터베이스 설정을 확인하세요:")
    print(f"   Host: {db_config['host']}")
    print(f"   Port: {db_config['port']}")
    print(f"   Database: {db_config['database']}")
    print(f"   User: {db_config['user']}")
    print(f"\n   MySQL/MariaDB가 실행 중이어야 합니다!")

    try:
        # 1. DB Manager 생성
        print("\n1. Connecting to database...")
        db_manager = DBManager(db_config)

        # 2. 테이블 생성
        print("\n2. Creating tables...")
        db_manager.create_tables()

        # 3. 데이터 수집
        print("\n3. Fetching company data...")
        user_agent = "PersonalResearch stox1224@email.com"
        client = SECAPIClient(user_agent, RateLimiter(10, 1.0))

        ticker = 'AAPL'
        company_facts = client.get_company_facts_by_ticker(ticker)

        if not company_facts:
            print("✗ Failed to fetch company facts")
            return

        # 4. 파싱 및 정규화
        print("\n4. Parsing and normalizing...")
        parser = CompanyFactsParser(company_facts)
        normalizer = FinancialNormalizer(parser)

        df = normalizer.create_normalized_dataframe(period_type='quarterly')
        df_billions = normalizer.convert_to_billions(df)

        # 5. DB 저장
        print(f"\n5. Saving {ticker} data to database...")
        db_manager.save_normalized_data(
            ticker=ticker,
            cik=parser.cik,
            df=df_billions
        )

        # 6. DB에서 조회
        print(f"\n6. Querying {ticker} revenue data from database...")
        revenue_data = db_manager.query_financial_data(
            ticker=ticker,
            item_name='revenue',
            start_date='2020-01-01'
        )

        if not revenue_data.empty:
            print(f"\n  Retrieved {len(revenue_data)} records:")
            print(revenue_data.tail(8))

        print("\n✓ Example 3 completed")

    except Exception as e:
        print(f"\n✗ Database example failed: {e}")
        print("\n  문제 해결 방법:")
        print("  1. MySQL/MariaDB가 설치되고 실행 중인지 확인")
        print("  2. 데이터베이스 'stock_db'가 생성되어 있는지 확인")
        print("     MySQL: CREATE DATABASE stock_db;")
        print("  3. 사용자 권한 확인")
        print("  4. 비밀번호가 올바른지 확인")


def example_4_financial_analysis():
    """예시 4: 재무 분석 - 비율 계산 및 성장률 분석"""
    print("\n" + "=" * 80)
    print("예시 4: 재무 분석 - Apple의 재무비율 및 성장률")
    print("=" * 80)

    # 데이터 수집
    user_agent = "PersonalResearch stox1224@email.com"
    client = SECAPIClient(user_agent, RateLimiter(10, 1.0))

    print("\n1. Fetching Apple company facts...")
    company_facts = client.get_company_facts_by_ticker('AAPL')

    if not company_facts:
        print("✗ Failed to fetch company facts")
        return

    # 파싱 및 정규화
    print("\n2. Parsing and normalizing...")
    parser = CompanyFactsParser(company_facts)
    normalizer = FinancialNormalizer(parser)

    df = normalizer.create_normalized_dataframe(period_type='quarterly')

    # 재무비율 계산
    print("\n3. Calculating financial ratios...")
    df_ratios = normalizer.calculate_financial_ratios(df)

    if 'profit_margin' in df_ratios.columns:
        print("\n  Profit Margin (last 8 quarters):")
        print(df_ratios[['revenue', 'net_income', 'profit_margin']].tail(8))

    if 'operating_margin' in df_ratios.columns:
        print("\n  Operating Margin (last 8 quarters):")
        print(df_ratios[['operating_income', 'operating_margin']].tail(8))

    # 성장률 계산
    print("\n4. Calculating growth rates...")
    df_billions = normalizer.convert_to_billions(df)
    df_growth = normalizer.calculate_growth_rates(df_billions, columns=['revenue', 'net_income'])

    if 'revenue_yoy_growth' in df_growth.columns:
        print("\n  YoY Revenue Growth % (last 8 quarters):")
        print(df_growth[['revenue', 'revenue_yoy_growth']].tail(8))

    # 최신 스냅샷
    print("\n5. Latest financial snapshot:")
    snapshot = normalizer.get_latest_financial_snapshot()
    for key, value in list(snapshot.items())[:10]:
        if value:
            print(f"  {key}: {value:,.0f}")

    print("\n✓ Example 4 completed")


def main():
    """메인 메뉴"""
    print("\n")
    print("█" * 80)
    print("SEC Data Pipeline - 사용 예시")
    print("█" * 80)

    examples = [
        ("기본 사용법 - 단일 기업 데이터 수집", example_1_basic_usage),
        ("여러 기업 데이터 수집 및 비교", example_2_multiple_companies),
        ("데이터베이스 저장 및 조회", example_3_database_storage),
        ("재무 분석 - 비율 및 성장률", example_4_financial_analysis),
    ]

    print("\n실행 가능한 예시:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")

    print("\n[주의] User-Agent가 올바르게 설정되었습니다!")
    print("     user_agent = 'PersonalResearch stox1224@email.com'\n")

    print("선택 (1-4, 0=전체 실행, q=종료): ", end="")

    try:
        choice = input().strip()

        if choice.lower() == 'q':
            print("종료합니다.")
            return

        if choice == '0':
            # 모든 예제 실행
            for name, func in examples:
                try:
                    func()
                except Exception as e:
                    print(f"\n✗ {name} 실행 중 오류: {e}")
                    import traceback
                    traceback.print_exc()
        else:
            # 선택한 예제만 실행
            idx = int(choice) - 1
            if 0 <= idx < len(examples):
                name, func = examples[idx]
                func()
            else:
                print("잘못된 선택입니다.")

    except ValueError:
        print("잘못된 입력입니다.")
    except KeyboardInterrupt:
        print("\n\n중단되었습니다.")
    except Exception as e:
        print(f"\n✗ 실행 중 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()