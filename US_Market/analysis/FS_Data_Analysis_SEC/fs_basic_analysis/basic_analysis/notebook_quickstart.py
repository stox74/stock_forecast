# Financial Analysis System - 노트북 초간단 사용 가이드
# 각 셀을 순서대로 복사해서 Jupyter Notebook에 붙여넣으세요

# ============================================================================
# 📌 셀 1: 환경 설정 (첫 번째 셀에 복사)
# ============================================================================

# 자동 경로 탐색 및 모듈 import
import sys
import os


def auto_import():
    """노트북에서 자동으로 모듈 찾기"""
    current_dir = os.getcwd()
    target = "financial_data_integrator.py"

    print("=" * 70)
    print("Financial Analysis System - 경로 자동 설정")
    print("=" * 70)
    print(f"현재 위치: {current_dir}\n")

    # 현재 디렉토리
    if os.path.exists(os.path.join(current_dir, target)):
        if current_dir not in sys.path:
            sys.path.insert(0, current_dir)
        print(f"✓ 모듈 발견: 현재 디렉토리")
        return True

    # 하위 디렉토리 검색
    for item in os.listdir(current_dir):
        item_path = os.path.join(current_dir, item)
        if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, target)):
            sys.path.insert(0, item_path)
            print(f"✓ 모듈 발견: {item}")
            return True

    # 상위 디렉토리 검색
    search = current_dir
    for level in range(5):
        search = os.path.dirname(search)
        if search == os.path.dirname(search):
            break
        if os.path.exists(os.path.join(search, target)):
            sys.path.insert(0, search)
            print(f"✓ 모듈 발견: 상위 {level + 1}단계")
            return True

    print("✗ 모듈을 찾을 수 없습니다")
    print("\n직접 경로를 지정하세요:")
    print("  sys.path.insert(0, r'실제경로')")
    return False


# 경로 설정 실행
if auto_import():
    from financial_data_integrator import integrate_financial_ratios
    from financial_analysis_system import FinancialAnalysisSystem

    print("\n✓ Import 성공! 사용 준비 완료")
    print("=" * 70)
else:
    print("\n직접 경로를 지정한 후 다시 실행하세요")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['figure.figsize'] = (14, 8)


# ============================================================================
# 📌 셀 2: 데이터 로드 (두 번째 셀에 복사)
# ============================================================================

# 예제 1: CSV 파일에서 로드
def load_data_from_csv(filepath):
    """CSV 파일에서 데이터 로드"""
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    print(f"✓ 데이터 로드 완료: {len(df)}개 분기")
    print(f"  기간: {df.index[0]} ~ {df.index[-1]}")
    print(f"  컬럼: {len(df.columns)}개")
    return df


# 사용 예:
# df_normalized = load_data_from_csv('your_data.csv')


# 예제 2: 데이터베이스에서 로드
def load_data_from_db(ticker, connection_string):
    """MySQL/PostgreSQL에서 로드"""
    import sqlalchemy
    engine = sqlalchemy.create_engine(connection_string)
    query = f"SELECT * FROM financial_data WHERE ticker = '{ticker}' ORDER BY date"
    df = pd.read_sql(query, engine, index_col='date', parse_dates=['date'])
    print(f"✓ 데이터 로드 완료: {len(df)}개 분기 ({ticker})")
    return df


# 사용 예:
# connection = "mysql+pymysql://user:password@localhost/database"
# df_normalized = load_data_from_db('AAPL', connection)


# 예제 3: SEC API에서 직접 로드 (사용자의 파이프라인 사용)
# df_normalized = your_sec_pipeline_function('AAPL')

print("데이터 로드 함수 준비 완료!")
print("\n다음 중 하나를 실행하세요:")
print("  df_normalized = load_data_from_csv('파일경로.csv')")
print("  df_normalized = load_data_from_db('AAPL', '연결문자열')")


# ============================================================================
# 📌 셀 3: 빠른 분석 실행 (세 번째 셀에 복사)
# ============================================================================

# 간단한 전체 분석 (한 줄로!)
def quick_analysis(df, ticker="AAPL", company_name="Apple Inc."):
    """원클릭 전체 분석"""
    print("\n" + "=" * 70)
    print(f"분석 시작: {company_name} ({ticker})")
    print("=" * 70 + "\n")

    # 1. 재무비율 계산
    print("[1/3] 재무비율 계산 중...")
    df_with_ratios = integrate_financial_ratios(df)
    print(f"  ✓ 완료 (총 {len(df_with_ratios.columns)}개 지표)\n")

    # 2. 분석 시스템 초기화
    print("[2/3] 분석 시스템 초기화...")
    analyzer = FinancialAnalysisSystem(df_with_ratios)
    print("  ✓ 완료\n")

    # 3. 전체 리포트 생성
    print("[3/3] 리포트 생성 중...")
    results = analyzer.generate_full_report(
        company_name=company_name,
        ticker=ticker,
        output_dir="../financial_reports"
    )

    print("\n" + "=" * 70)
    print("✓ 분석 완료!")
    print("=" * 70)
    print(f"\n저장 위치: {results['output_dir']}")
    print(f"  • 리포트: {results['report_path']}")
    print(f"  • 데이터: {results['data_path']}")
    print(f"  • 차트: 7개 PNG 파일")

    return results, df_with_ratios, analyzer


# 사용 예:
# results, df_with_ratios, analyzer = quick_analysis(df_normalized, 'AAPL', 'Apple Inc.')


# ============================================================================
# 📌 셀 4: 주요 지표만 확인 (네 번째 셀에 복사)
# ============================================================================

# 최근 분기 주요 지표 추출
def show_key_metrics(df_with_ratios, n_quarters=4):
    """주요 재무 지표 표시"""
    metrics = {
        '수익성': ['roe', 'roa', 'roic', 'net_margin', 'operating_margin'],
        '성장성': ['revenue', 'operating_income', 'net_income'],
        '건전성': ['debt_to_equity', 'current_ratio', 'quick_ratio'],
        '효율성': ['asset_turnover', 'cash_conversion_cycle'],
        '현금흐름': ['operating_cash_flow', 'free_cash_flow']
    }

    print("\n" + "=" * 70)
    print(f"주요 재무 지표 (최근 {n_quarters}분기)")
    print("=" * 70 + "\n")

    for category, cols in metrics.items():
        available_cols = [c for c in cols if c in df_with_ratios.columns]
        if available_cols:
            print(f"[{category}]")
            data = df_with_ratios[available_cols].tail(n_quarters)
            print(data.T)  # 전치해서 보기 좋게
            print()

    # 최근 분기 요약
    latest = df_with_ratios.iloc[-1]
    print("=" * 70)
    print(f"최근 분기 요약 ({df_with_ratios.index[-1].strftime('%Y-%m-%d')})")
    print("=" * 70)
    if 'roe' in latest:
        print(f"ROE: {latest['roe']:.2f}%")
    if 'net_margin' in latest:
        print(f"순이익률: {latest['net_margin']:.2f}%")
    if 'current_ratio' in latest:
        print(f"유동비율: {latest['current_ratio']:.2f}")
    if 'debt_to_equity' in latest:
        print(f"부채비율: {latest['debt_to_equity']:.2f}%")
    print("=" * 70)


# 사용 예:
# show_key_metrics(df_with_ratios, n_quarters=8)


# ============================================================================
# 📌 셀 5: 개별 차트 생성 (다섯 번째 셀에 복사)
# ============================================================================

# 특정 차트만 생성
def create_charts(analyzer, save_dir='./charts'):
    """개별 차트 생성"""
    import os
    os.makedirs(save_dir, exist_ok=True)

    print("\n차트 생성 중...")

    # 필요한 분석 실행
    analyzer.calculate_growth_rates()
    analyzer.analyze_profitability_trends()
    analyzer.analyze_financial_health()
    analyzer.analyze_cash_flow()

    # 차트 생성
    print("  1/4 성장률...")
    analyzer.create_growth_chart(f"{save_dir}/growth.png")

    print("  2/4 수익성...")
    analyzer.create_profitability_chart(f"{save_dir}/profitability.png")

    print("  3/4 재무건전성...")
    analyzer.create_financial_health_chart(f"{save_dir}/health.png")

    print("  4/4 현금흐름...")
    analyzer.create_cash_flow_chart(f"{save_dir}/cashflow.png")

    print(f"\n✓ 완료! 저장 위치: {save_dir}")

    # 노트북에서 차트 표시
    plt.show()


# 사용 예:
# create_charts(analyzer)


# ============================================================================
# 📌 셀 6: 예측 모델 (여섯 번째 셀에 복사)
# ============================================================================

# 매출-영업이익 예측 모델
def forecast_operating_income(analyzer, future_revenue):
    """향후 매출로 영업이익 예측"""
    print("\n매출-영업이익 예측 모델 구축 중...\n")

    # 모델 구축
    model = analyzer.build_revenue_operating_income_model()

    if not model:
        print("✗ 데이터 부족으로 모델을 구축할 수 없습니다")
        return None

    # 모델 정보
    print("=" * 70)
    print("회귀 모델 정보")
    print("=" * 70)
    print(f"회귀식: OI = {model['slope']:.4f} × Revenue + {model['intercept']:,.0f}")
    print(f"R² = {model['r2']:.4f}")
    print(f"평균 영업이익률 = {model['avg_operating_margin']:.2f}%")
    print("=" * 70 + "\n")

    # 예측
    if future_revenue:
        predicted_oi = model['predict_function'](future_revenue)
        predicted_margin = (predicted_oi / future_revenue) * 100

        print("예측 결과:")
        print(f"  매출: {future_revenue:,.0f}")
        print(f"  예상 영업이익: {predicted_oi:,.0f}")
        print(f"  예상 영업이익률: {predicted_margin:.2f}%")

    return model


# 사용 예:
# model = forecast_operating_income(analyzer, future_revenue=100000000)


# ============================================================================
# 📌 전체 워크플로우 예제 (한 번에 실행)
# ============================================================================

"""
# 1단계: 환경 설정
(셀 1 실행)

# 2단계: 데이터 로드
df_normalized = load_data_from_csv('your_data.csv')

# 3단계: 빠른 분석
results, df_with_ratios, analyzer = quick_analysis(df_normalized, 'AAPL', 'Apple Inc.')

# 4단계: 주요 지표 확인
show_key_metrics(df_with_ratios)

# 5단계: 차트 생성
create_charts(analyzer)

# 6단계: 예측 모델
model = forecast_operating_income(analyzer, future_revenue=100000000)
"""

print("\n" + "=" * 70)
print("노트북 사용 가이드")
print("=" * 70)
print("\n위의 셀들을 순서대로 복사-붙여넣기 하세요:")
print("\n1. 셀 1: 환경 설정 (필수)")
print("2. 셀 2: 데이터 로드 함수")
print("3. 셀 3: 빠른 분석 실행")
print("4. 셀 4: 주요 지표 확인")
print("5. 셀 5: 개별 차트 생성")
print("6. 셀 6: 예측 모델")
print("\n또는 하단의 '전체 워크플로우 예제'를 참고하세요")
print("=" * 70)