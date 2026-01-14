#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Financial Analysis - Jupyter Notebook Quick Start
노트북 환경에서 바로 사용 가능한 예제

셀 1: 환경 설정
셀 2: 데이터 로드
셀 3: 분석 실행
셀 4: 결과 확인
"""

# ============================================================================
# 셀 1: 환경 설정 및 모듈 Import
# ============================================================================

# 경로 자동 설정
import sys
import os

# 방법 A: import_helper 사용 (가장 간단)
try:
    from import_helper import quick_setup

    quick_setup()
except ImportError:
    # import_helper가 없으면 수동으로 경로 추가
    print("수동 경로 설정 중...")

    # 현재 디렉토리와 부모 디렉토리를 sys.path에 추가
    current_dir = os.getcwd()
    parent_dir = os.path.dirname(current_dir)

    for path in [current_dir, parent_dir]:
        if path not in sys.path:
            sys.path.insert(0, path)
            print(f"  추가됨: {path}")

# 방법 B: 직접 경로 지정 (정확한 경로를 아는 경우)
# 아래 주석을 해제하고 실제 경로로 변경하세요
# sys.path.insert(0, r'C:\Users\YourName\Projects\FinancialAnalysis')
# sys.path.insert(0, r'/home/yourname/projects/financial_analysis')

# 필요한 라이브러리 import
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 재무 분석 모듈 import
try:
    from financial_data_integrator import integrate_financial_ratios
    from financial_analysis_system import FinancialAnalysisSystem

    print("✓ 모듈 import 성공!")
except ImportError as e:
    print(f"✗ Import 오류: {e}")
    print("\n해결 방법:")
    print("1. 모듈 파일들(.py)이 노트북과 같은 폴더에 있는지 확인")
    print("2. 또는 위의 '방법 B'를 사용해서 정확한 경로 지정")
    raise

# Matplotlib 설정
plt.style.use('seaborn-v0_8-whitegrid')
plt.rcParams['figure.figsize'] = (14, 8)
plt.rcParams['figure.dpi'] = 100

print("\n환경 설정 완료!")


# ============================================================================
# 셀 2: 데이터 로드 예제
# ============================================================================

# 예제 1: CSV 파일에서 로드 (가장 일반적)
def load_from_csv(filepath):
    """
    CSV 파일에서 정규화된 재무 데이터 로드

    Args:
        filepath: CSV 파일 경로

    Returns:
        DataFrame
    """
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    return df


# 예제 2: SEC 데이터에서 직접 로드 (SEC API 사용)
def load_from_sec(ticker, headers=None):
    """
    SEC API에서 데이터 수집 및 정규화

    Args:
        ticker: 주식 티커 (예: "AAPL")
        headers: SEC API 헤더 (User-Agent 필요)

    Returns:
        정규화된 DataFrame
    """
    # 주의: 이 부분은 사용자의 SEC 데이터 파이프라인에 맞게 수정 필요
    """
    from sec_data_pipeline.collectors.sec_utils import fetch_company_facts
    from sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
    from sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer

    if headers is None:
        headers = {"User-Agent": "YourName Research <your@email.com>"}

    facts = fetch_company_facts(ticker, headers=headers)
    parser = CompanyFactsParser(facts)
    normalizer = FinancialNormalizer(parser)
    df_normalized = normalizer.create_normalized_dataframe("quarterly")

    return df_normalized
    """
    raise NotImplementedError("SEC 데이터 파이프라인 코드를 활성화하세요")


# 예제 3: 데이터베이스에서 로드
def load_from_database(ticker, connection_string=None):
    """
    MySQL/PostgreSQL 데이터베이스에서 로드

    Args:
        ticker: 주식 티커
        connection_string: 데이터베이스 연결 문자열

    Returns:
        DataFrame
    """
    import sqlalchemy

    if connection_string is None:
        # 기본 연결 문자열 (실제 사용시 수정 필요)
        connection_string = "mysql+pymysql://user:password@localhost/financial_db"

    engine = sqlalchemy.create_engine(connection_string)

    query = f"""
    SELECT * FROM financial_data 
    WHERE ticker = '{ticker}'
    ORDER BY date
    """

    df = pd.read_sql(query, engine, index_col='date', parse_dates=['date'])

    return df


print("데이터 로드 함수 준비 완료!")


# ============================================================================
# 셀 3: 분석 실행
# ============================================================================

def analyze_company_simple(df_normalized, ticker, company_name):
    """
    간단한 분석 실행

    Args:
        df_normalized: 정규화된 재무 데이터 DataFrame
        ticker: 주식 티커
        company_name: 회사명

    Returns:
        분석 결과 딕셔너리
    """
    print(f"\n{'=' * 70}")
    print(f"분석 시작: {company_name} ({ticker})")
    print(f"{'=' * 70}\n")

    # Step 1: 재무비율 계산
    print("[1/3] 재무비율 계산 중...")
    df_with_ratios = integrate_financial_ratios(df_normalized)
    print(f"  완료! 총 {len(df_with_ratios.columns)}개 컬럼")

    # Step 2: 분석 시스템 초기화
    print("\n[2/3] 분석 시스템 초기화...")
    analyzer = FinancialAnalysisSystem(df_with_ratios)

    # Step 3: 전체 리포트 생성
    print("\n[3/3] 종합 리포트 생성 중...")
    results = analyzer.generate_full_report(
        company_name=company_name,
        ticker=ticker,
        output_dir="./financial_reports"
    )

    print(f"\n{'=' * 70}")
    print("분석 완료!")
    print(f"{'=' * 70}")

    return results


# ============================================================================
# 셀 4: 빠른 분석 (차트만)
# ============================================================================

def quick_chart_analysis(df_with_ratios, output_dir="./charts"):
    """
    빠른 차트 분석 (리포트 없이 차트만 생성)

    Args:
        df_with_ratios: 재무비율이 포함된 DataFrame
        output_dir: 차트 저장 디렉토리

    Returns:
        생성된 차트 리스트
    """
    import os
    os.makedirs(output_dir, exist_ok=True)

    analyzer = FinancialAnalysisSystem(df_with_ratios)

    # 각종 분석 실행
    print("분석 실행 중...")
    analyzer.calculate_growth_rates()
    analyzer.analyze_profitability_trends()
    analyzer.analyze_financial_health()
    analyzer.analyze_cash_flow()
    analyzer.build_revenue_operating_income_model()

    # 차트 생성
    print("\n차트 생성 중...")
    charts = []

    print("  1/4 성장률 차트...")
    fig1 = analyzer.create_growth_chart(f"{output_dir}/growth.png")
    charts.append(fig1)

    print("  2/4 수익성 차트...")
    fig2 = analyzer.create_profitability_chart(f"{output_dir}/profitability.png")
    charts.append(fig2)

    print("  3/4 재무건전성 차트...")
    fig3 = analyzer.create_financial_health_chart(f"{output_dir}/health.png")
    charts.append(fig3)

    print("  4/4 현금흐름 차트...")
    fig4 = analyzer.create_cash_flow_chart(f"{output_dir}/cashflow.png")
    charts.append(fig4)

    print(f"\n완료! 차트 저장: {output_dir}")

    return charts


# ============================================================================
# 셀 5: 주요 지표만 추출
# ============================================================================

def extract_key_metrics(df_with_ratios, latest_n=1):
    """
    주요 재무 지표만 추출

    Args:
        df_with_ratios: 재무비율이 포함된 DataFrame
        latest_n: 최근 N개 분기 (기본값: 1)

    Returns:
        주요 지표 DataFrame
    """
    key_metrics = [
        # 수익성
        'roe', 'roa', 'roic',
        'gross_margin', 'operating_margin', 'net_margin',

        # 성장성
        'revenue', 'operating_income', 'net_income',

        # 재무건전성
        'debt_to_equity', 'current_ratio', 'quick_ratio',

        # 효율성
        'asset_turnover',

        # 현금흐름
        'operating_cash_flow', 'free_cash_flow'
    ]

    # 존재하는 컬럼만 선택
    available_metrics = [m for m in key_metrics if m in df_with_ratios.columns]

    # 최근 N개 분기 데이터 추출
    result = df_with_ratios[available_metrics].tail(latest_n)

    return result


# ============================================================================
# 셀 6: 사용 예제
# ============================================================================

def example_usage():
    """
    실제 사용 예제
    """
    print("\n" + "=" * 70)
    print("Financial Analysis System - 사용 예제")
    print("=" * 70)

    # 예제 1: CSV 파일에서 로드하여 분석
    print("\n[예제 1] CSV 파일 분석")
    print("-" * 70)
    print("""
# CSV 파일 로드
df_normalized = load_from_csv('path/to/your/data.csv')

# 간단 분석 실행
results = analyze_company_simple(
    df_normalized=df_normalized,
    ticker='AAPL',
    company_name='Apple Inc.'
)
    """)

    # 예제 2: 빠른 차트 분석
    print("\n[예제 2] 빠른 차트 분석")
    print("-" * 70)
    print("""
# 데이터 로드
df_normalized = load_from_csv('path/to/your/data.csv')

# 재무비율 계산
df_with_ratios = integrate_financial_ratios(df_normalized)

# 차트만 생성 (리포트 없이)
charts = quick_chart_analysis(df_with_ratios)

# Jupyter에서 차트 표시
plt.show()
    """)

    # 예제 3: 주요 지표만 확인
    print("\n[예제 3] 주요 지표 확인")
    print("-" * 70)
    print("""
# 재무비율 계산
df_with_ratios = integrate_financial_ratios(df_normalized)

# 최근 4분기 주요 지표 추출
key_metrics = extract_key_metrics(df_with_ratios, latest_n=4)

# 표시
print(key_metrics.T)  # 전치해서 보기 좋게
    """)

    # 예제 4: 개별 분석
    print("\n[예제 4] 개별 분석 실행")
    print("-" * 70)
    print("""
# 분석 시스템 초기화
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 개별 분석 실행
growth = analyzer.calculate_growth_rates()
profitability = analyzer.analyze_profitability_trends()
health = analyzer.analyze_financial_health()

# 결과 확인
print("최근 분기 ROE:", profitability['roe'].iloc[-1])
print("최근 분기 매출 성장률:", growth['revenue_yoy_growth'].iloc[-1])
    """)

    print("\n" + "=" * 70)


# 실행
if __name__ == "__main__":
    example_usage()

    print("\n노트북에서 사용하려면:")
    print("1. 위의 셀들을 순서대로 실행")
    print("2. df_normalized를 준비")
    print("3. analyze_company_simple() 또는 quick_chart_analysis() 실행")
    print("\n즐거운 분석되세요!")