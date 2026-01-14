# Financial Analysis System - 설치 및 시작 가이드

## 📦 필요한 패키지 설치

```bash
pip install pandas numpy matplotlib seaborn scipy scikit-learn --break-system-packages
```

## 📁 파일 구조

```
your_project/
├── financial_data_integrator.py      # 재무비율 계산 (이미 보유)
├── financial_analysis_system.py      # 종합 분석 시스템 (신규)
├── import_helper.py                  # 경로 자동 설정 (신규)
├── example_usage.py                  # 사용 예제 (신규)
├── notebook_example.py               # 노트북 예제 (신규)
├── FINANCIAL_METRICS_GUIDE.md        # 지표 설명서 (신규)
└── INSTALLATION_GUIDE.md             # 이 파일
```

## 🔧 경로 설정 (중요!)

여러 환경(노트북, 데스크탑, 서버)에서 모듈을 사용하려면 경로 설정이 필요합니다.

### 방법 1: import_helper 사용 (가장 간단, 권장)

```python
# 모든 스크립트 시작 부분에 추가
from import_helper import quick_setup
quick_setup()

# 이제 어디서든 import 가능
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem
```

**작동 원리:**
- 현재 디렉토리부터 시작해서 상위 3단계까지 자동으로 모듈 탐색
- 발견된 모듈 경로를 자동으로 sys.path에 추가
- 한 번만 실행하면 나머지는 자동!

### 방법 2: 환경 변수 설정 (영구적)

**Windows:**
```cmd
setx FINANCIAL_ANALYSIS_PATH "C:\Users\YourName\Projects\FinancialAnalysis"
```

**Linux/Mac:**
```bash
echo 'export FINANCIAL_ANALYSIS_PATH="/home/yourname/projects/financial_analysis"' >> ~/.bashrc
source ~/.bashrc
```

**Python에서 사용:**
```python
from import_helper import quick_setup
quick_setup()  # 환경 변수를 자동으로 인식
```

### 방법 3: 직접 경로 지정 (정확한 경로를 아는 경우)

```python
import sys

# Windows
sys.path.insert(0, r'C:\Users\YourName\Projects\FinancialAnalysis')

# Linux/Mac
sys.path.insert(0, '/home/yourname/projects/financial_analysis')

# 이제 import
from financial_data_integrator import integrate_financial_ratios
```

### 방법 4: Jupyter Notebook에서 사용

```python
# 셀 1: 경로 설정
import sys
import os

# 현재 노트북 위치 기준으로 모듈 찾기
current_dir = os.getcwd()
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 또는 import_helper 사용
from import_helper import quick_setup
quick_setup()

# 셀 2: 모듈 import
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem

print("✓ Import 성공!")
```

## 🚀 빠른 시작

### Step 1: 기본 워크플로우

```python
# 경로 설정 (한 번만)
from import_helper import quick_setup
quick_setup()

# 모듈 import
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem

# SEC 데이터 수집 (기존 코드 사용)
# df_normalized = your_existing_sec_data_pipeline()

# 재무비율 추가
df_with_ratios = integrate_financial_ratios(df_normalized)

# 분석 시스템 초기화
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 전체 리포트 생성 (한 번에!)
results = analyzer.generate_full_report(
    company_name="Apple Inc.",
    ticker="AAPL",
    output_dir="./financial_reports"
)
```

### Step 2: 결과 확인

생성된 파일:
- `./financial_reports/AAPL/AAPL_analysis_report.txt` - 텍스트 리포트
- `./financial_reports/AAPL/AAPL_full_data.csv` - 전체 데이터
- `./financial_reports/AAPL/*.png` - 7개의 차트 이미지

## 📊 추출되는 주요 데이터

### 1. 성장성 (12개 지표)
- Revenue/Operating Income/Net Income YoY/QoQ 성장률
- TTM 성장률

### 2. 수익성 (12개 지표)
- ROE, ROA, ROIC
- Gross/Operating/Net Margin
- TTM 평균 및 추세

### 3. 재무건전성 (7개 지표)
- 부채비율 (D/E, D/A)
- 유동비율, 당좌비율
- 이자보상배율
- Altman Z-Score

### 4. 효율성 (7개 지표)
- 자산/재고/매출채권 회전율
- 회전일수
- 현금순환주기 (CCC)

### 5. 현금흐름 (7개 지표)
- OCF, FCF, FCF TTM
- OCF/Net Income 비율
- FCF Margin

### 6. 밸류에이션 (5개 지표)
- P/E, P/B, P/S
- EV/EBITDA
- Market Cap

### 7. 예측 (5개 지표)
- 매출-영업이익 회귀모델
- 향후 4분기 매출/영업이익 예측
- R² 및 평균 영업이익률

**총 60개 이상의 재무 지표 + 시각화**

## 🎨 생성되는 차트

1. **Growth Chart**: 성장률 분석 (4개 서브차트)
2. **Profitability Chart**: 수익성 분석 (4개 서브차트)
3. **Financial Health Chart**: 재무건전성 (4개 서브차트)
4. **Cash Flow Chart**: 현금흐름 (4개 서브차트)
5. **Revenue-OI Model Chart**: 회귀 모델 (2개 서브차트)
6. **Forecast Chart**: 실적 전망 (2개 서브차트)
7. **Comprehensive Dashboard**: 종합 대시보드 (9개 서브차트)

## 🔍 개별 분석 실행

전체 리포트 대신 개별 분석만 원하는 경우:

```python
# 분석 시스템 초기화
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 개별 분석 실행
growth_df = analyzer.calculate_growth_rates()
prof_df = analyzer.analyze_profitability_trends()
health_df = analyzer.analyze_financial_health()
eff_df = analyzer.analyze_efficiency()
cf_df = analyzer.analyze_cash_flow()

# 개별 차트 생성
analyzer.create_growth_chart("./growth.png")
analyzer.create_profitability_chart("./profitability.png")

# 텍스트 리포트만 생성
report = analyzer.generate_summary_report()
print(report)
```

## 📈 매출-영업이익 예측 모델

회귀 분석을 통한 영업이익 예측:

```python
# 모델 구축
model = analyzer.build_revenue_operating_income_model()

# 모델 정보 확인
print(f"회귀식: OI = {model['slope']:.4f} * Revenue + {model['intercept']:.2f}")
print(f"R² = {model['r2']:.4f}")
print(f"평균 영업이익률 = {model['avg_operating_margin']:.2f}%")

# 예측 함수 사용
predict_func = model['predict_function']
predicted_oi = predict_func(100000000)  # 매출 1억 달러 가정
print(f"예상 영업이익: {predicted_oi:,.0f}")
```

## 🔄 배치 분석 (여러 기업)

여러 기업을 한 번에 분석:

```python
companies = [
    ("AAPL", "Apple Inc.", 150.0, 16000000000),
    ("MSFT", "Microsoft Corp.", 300.0, 7500000000),
    ("GOOGL", "Alphabet Inc.", 130.0, 13000000000),
]

for ticker, name, price, shares in companies:
    # SEC 데이터 로드
    df_normalized = load_sec_data(ticker)
    
    # 재무비율 추가
    df_with_ratios = integrate_financial_ratios(df_normalized)
    
    # 분석 실행
    analyzer = FinancialAnalysisSystem(df_with_ratios)
    results = analyzer.generate_full_report(
        company_name=name,
        ticker=ticker,
        current_price=price,
        shares_outstanding=shares
    )
    
    print(f"{ticker} 분석 완료!")
```

## 📝 주요 활용 시나리오

### 1. 투자 의사결정
```python
# 기업 분석 실행
results = analyzer.generate_full_report(...)

# 최근 분기 주요 지표 확인
latest = df_with_ratios.iloc[-1]
print(f"ROE: {latest['roe']:.2f}%")
print(f"Net Margin: {latest['net_margin']:.2f}%")
print(f"D/E Ratio: {latest['debt_to_equity']:.2f}%")

# Altman Z-Score로 재무 위험 평가
if 'altman_z_score' in latest:
    z = latest['altman_z_score']
    if z > 3.0:
        print("재무상태: 안전")
    elif z > 1.8:
        print("재무상태: 주의")
    else:
        print("재무상태: 위험")
```

### 2. 동종업계 비교
```python
# 여러 기업 분석 후 비교
companies = ["AAPL", "MSFT", "GOOGL"]
comparison = {}

for ticker in companies:
    # 각 기업 분석
    analyzer = FinancialAnalysisSystem(df_with_ratios[ticker])
    latest = analyzer.df.iloc[-1]
    
    comparison[ticker] = {
        'ROE': latest['roe'],
        'Net Margin': latest['net_margin'],
        'Current Ratio': latest['current_ratio']
    }

# 비교 결과 출력
import pandas as pd
comp_df = pd.DataFrame(comparison).T
print(comp_df)
```

### 3. 시계열 추세 분석
```python
# 수익성 추세 분석
prof_df = analyzer.analyze_profitability_trends()

# ROE 추세 확인
roe_trend = prof_df['roe_trend'].iloc[-1]
if roe_trend > 0:
    print("ROE 상승 추세")
else:
    print("ROE 하락 추세")
```

## ⚙️ 고급 설정

### 예측 기간 조정
```python
# 기본: 4분기 예측
forecast_df = analyzer.forecast_next_periods(periods=4)

# 8분기 예측
forecast_df = analyzer.forecast_next_periods(periods=8)
```

### 차트 스타일 커스터마이징
```python
import matplotlib.pyplot as plt

# 전역 스타일 변경
plt.style.use('seaborn-v0_8-darkgrid')
plt.rcParams['figure.figsize'] = (16, 10)
plt.rcParams['font.size'] = 12

# 분석 실행
analyzer.generate_full_report(...)
```

### 특정 지표만 추출
```python
# 원하는 지표만 선택
selected_metrics = ['roe', 'roa', 'net_margin', 'current_ratio']
selected_df = df_with_ratios[selected_metrics]

# CSV로 저장
selected_df.to_csv("selected_metrics.csv")
```

## 🐛 문제 해결

### 1. 데이터 부족 에러
```python
# 최소 8분기 데이터 필요
if len(df_with_ratios) < 8:
    print("경고: 데이터 포인트 부족 (최소 8분기 필요)")
```

### 2. 결측치 처리
```python
# 시스템이 자동으로 처리하지만, 확인 방법:
print(df_with_ratios.isnull().sum())
```

### 3. 이상치 확인
```python
# 재무비율 이상치 확인
print(df_with_ratios[['roe', 'roa', 'roic']].describe())
```

## 📚 추가 리소스

- `FINANCIAL_METRICS_GUIDE.md`: 모든 지표의 상세 설명
- `example_usage.py`: 다양한 사용 예제
- `financial_analysis_system.py`: 전체 소스 코드 및 주석

## 💡 팁

1. **정기적 업데이트**: 분기마다 SEC 데이터를 업데이트하여 최신 분석 유지
2. **비교 분석**: 여러 기업을 동시에 분석하여 상대적 강점 파악
3. **추세 모니터링**: TTM 지표로 단기 변동성 제거
4. **예측 검증**: 실제 실적과 예측치를 비교하여 모델 정확도 확인
5. **차트 활용**: 시각화 자료를 활용하여 빠른 인사이트 도출

## 🎯 다음 단계

1. SEC 데이터 파이프라인과 통합
2. 자동화 스크립트 작성 (정기 업데이트)
3. 데이터베이스 연동 (MySQL/PostgreSQL)
4. 웹 대시보드 구축 (Streamlit/Dash)
5. 알림 시스템 구축 (이메일/Slack)

---

**참고**: 이 시스템은 SEC 공시 데이터를 기반으로 하므로, 미국 상장기업에 적용 가능합니다.
