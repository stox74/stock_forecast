# Financial Analysis System - 추출 데이터 및 지표 목록

## 📊 종합 개요

이 시스템은 SEC API 데이터를 기반으로 **8가지 주요 분석 영역**에서 **60개 이상의 재무 지표**를 추출하고 시각화합니다.

---

## 1️⃣ 성장성 분석 (Growth Analysis)

### 추출 데이터
- **매출액 성장률**
  - `revenue_yoy_growth`: 전년 동기 대비 성장률 (%)
  - `revenue_qoq_growth`: 전분기 대비 성장률 (%)
  - `revenue_ttm`: TTM(Trailing 12 Months) 매출액
  - `revenue_ttm_yoy_growth`: TTM 매출 YoY 성장률 (%)

- **영업이익 성장률**
  - `operating_income_yoy_growth`: 전년 동기 대비 성장률 (%)
  - `operating_income_qoq_growth`: 전분기 대비 성장률 (%)

- **순이익 성장률**
  - `net_income_yoy_growth`: 전년 동기 대비 성장률 (%)
  - `net_income_qoq_growth`: 전분기 대비 성장률 (%)
  - `net_income_ttm`: TTM 순이익
  - `net_income_ttm_yoy_growth`: TTM 순이익 YoY 성장률 (%)

- **총매출이익 성장률**
  - `gross_profit_yoy_growth`: 전년 동기 대비 성장률 (%)
  - `gross_profit_qoq_growth`: 전분기 대비 성장률 (%)

- **자산/자본 성장률**
  - `total_assets_yoy_growth`: 총자산 YoY 성장률 (%)
  - `stockholders_equity_yoy_growth`: 자기자본 YoY 성장률 (%)

### 시각화
- 4개의 차트 (Revenue, Operating Income, Net Income Growth + TTM 추이)
- YoY/QoQ 성장률 동시 비교
- TTM 기준 절대값 추이

---

## 2️⃣ 수익성 분석 (Profitability Analysis)

### 추출 데이터
- **자본수익률 (Return Ratios)**
  - `roe`: 자기자본이익률 (Return on Equity, %)
  - `roa`: 총자산이익률 (Return on Assets, %)
  - `roic`: 투하자본이익률 (Return on Invested Capital, %)
  - `roe_ttm`: ROE TTM 평균 (%)
  - `roa_ttm`: ROA TTM 평균 (%)
  - `roic_ttm`: ROIC TTM 평균 (%)

- **이익률 (Profit Margins)**
  - `gross_margin`: 매출총이익률 (%)
  - `operating_margin`: 영업이익률 (%)
  - `net_margin`: 순이익률 (%)
  - `gross_margin_ttm`: 매출총이익률 TTM (%)
  - `operating_margin_ttm`: 영업이익률 TTM (%)
  - `net_margin_ttm`: 순이익률 TTM (%)

- **추세 분석**
  - `roe_trend`: ROE 8분기 선형 추세
  - `roa_trend`: ROA 8분기 선형 추세
  - `roic_trend`: ROIC 8분기 선형 추세

### 계산 로직
- **ROE** = 순이익(TTM) / 평균 자기자본 × 100
- **ROA** = 순이익(TTM) / 평균 총자산 × 100
- **ROIC** = NOPAT / 평균 투하자본 × 100
  - NOPAT = 영업이익 × (1 - 실효세율)
  - 투하자본 = 순운전자본 + 순고정자산

### 시각화
- ROE/ROA/ROIC 비교 차트
- Profit Margins 추이 차트
- ROE TTM 및 Operating Margin TTM 트렌드

---

## 3️⃣ 재무건전성 분석 (Financial Health)

### 추출 데이터
- **레버리지 지표 (Leverage Ratios)**
  - `debt_to_equity`: 부채비율 (D/E Ratio, %)
  - `debt_to_assets`: 부채비율 (D/A Ratio, %)
  - `equity_multiplier`: 자기자본승수

- **유동성 지표 (Liquidity Ratios)**
  - `current_ratio`: 유동비율
  - `quick_ratio`: 당좌비율

- **이자보상배율 (Interest Coverage)**
  - `interest_coverage`: 영업이익 / 이자비용 (배수)

- **종합 건전성 지표**
  - `altman_z_score`: Altman Z-Score (파산예측모델)
    - Z > 3.0: Safe Zone (안전)
    - 1.8 < Z < 3.0: Grey Zone (주의)
    - Z < 1.8: Distress Zone (위험)

### 계산 로직
- **Altman Z-Score** = 1.2×X1 + 1.4×X2 + 3.3×X3 + 0.6×X4 + 1.0×X5
  - X1 = 순운전자본 / 총자산
  - X2 = 이익잉여금 / 총자산
  - X3 = 영업이익 / 총자산
  - X4 = 자기자본 / 총부채
  - X5 = 매출액 / 총자산

### 시각화
- Leverage Ratios (D/E, D/A)
- Liquidity Ratios (Current, Quick) with 기준선
- Interest Coverage with 안전/위험 기준선
- Altman Z-Score with 위험 구간 표시

---

## 4️⃣ 효율성 분석 (Efficiency Analysis)

### 추출 데이터
- **회전율 지표 (Turnover Ratios)**
  - `asset_turnover`: 총자산회전율 (회)
  - `inventory_turnover`: 재고자산회전율 (회)
  - `receivables_turnover`: 매출채권회전율 (회)

- **회전일수 지표 (Days)**
  - `days_inventory`: 재고자산회전일수 (일)
  - `days_receivables`: 매출채권회전일수 (일)
  - `days_payable`: 매입채무회전일수 (일)

- **현금순환주기 (Cash Conversion Cycle)**
  - `cash_conversion_cycle`: CCC (일)
  - CCC = 재고회전일수 + 매출채권회전일수 - 매입채무회전일수
  - 낮을수록 현금 효율성이 높음

### 계산 로직
- **재고회전율** = 매출원가 / 평균 재고자산
- **매출채권회전율** = 매출액 / 평균 매출채권
- **총자산회전율** = 매출액 / 평균 총자산
- **회전일수** = 365 / 회전율

---

## 5️⃣ 현금흐름 분석 (Cash Flow Analysis)

### 추출 데이터
- **현금흐름 구성요소**
  - `operating_cash_flow`: 영업활동 현금흐름
  - `investing_cash_flow`: 투자활동 현금흐름
  - `financing_cash_flow`: 재무활동 현금흐름
  - `ocf_ttm`: 영업현금흐름 TTM

- **자유현금흐름 (Free Cash Flow)**
  - `free_cash_flow`: FCF = OCF - CAPEX
  - `fcf_ttm`: FCF TTM
  - `fcf_margin`: FCF 마진 (%)

- **현금흐름 품질 지표**
  - `ocf_to_net_income`: OCF/순이익 비율
    - > 1.0: 양호 (이익의 현금화 우수)
    - < 0.8: 주의 (이익의 현금화 미흡)

### 계산 로직
- **FCF** = 영업현금흐름 - 자본적지출
- **FCF Margin** = FCF / 매출액 × 100
- **Quality of Earnings** = OCF / 순이익

### 시각화
- 3가지 현금흐름 구성요소 비교
- Free Cash Flow (양수/음수 구분)
- OCF/Net Income 비율 (품질 기준선 표시)
- FCF Margin 추이

---

## 6️⃣ 밸류에이션 분석 (Valuation)

### 추출 데이터
- **시장 기반 지표** (주가 및 발행주식수 입력 필요)
  - `market_cap`: 시가총액
  - `pe_ratio`: P/E Ratio (주가수익비율)
  - `pb_ratio`: P/B Ratio (주가순자산비율)
  - `ps_ratio`: P/S Ratio (주가매출비율)
  - `ev_ebitda`: EV/EBITDA

- **기업가치 계산**
  - Enterprise Value (EV) = 시가총액 + 총부채 - 현금성자산

### 계산 로직
- **P/E Ratio** = 시가총액 / 순이익(TTM)
- **P/B Ratio** = 시가총액 / 자기자본
- **P/S Ratio** = 시가총액 / 매출액(TTM)
- **EV/EBITDA** = 기업가치 / EBITDA(TTM)

---

## 7️⃣ 예측 모델 (Forecasting)

### 매출-영업이익 회귀 모델

**모델 정보**
- `slope`: 회귀계수 (영업이익률)
- `intercept`: 절편
- `r2`: 결정계수 (모델 적합도)
- `avg_operating_margin`: 평균 영업이익률 (%)

**회귀식**
```
Operating Income = slope × Revenue + intercept
```

**모델 활용**
- TTM 데이터 기반 선형 회귀
- 향후 매출 전망 → 영업이익 예측
- R² 값으로 예측 신뢰도 평가

### 향후 실적 예측 (4분기)

**예측 데이터**
- `revenue_forecast`: 매출 예측치
- `operating_income_forecast`: 영업이익 예측치
- `operating_margin_forecast`: 영업이익률 예측치 (%)

**예측 방법**
- 최근 8분기 평균 성장률 기반
- 회귀 모델 적용 영업이익 예측
- 분기별 예측치 제공

### 시각화
- 회귀 산점도 + 회귀선
- Residuals Plot (잔차 분석)
- 과거 실적 + 향후 4분기 예측 차트

---

## 8️⃣ 종합 리포트 (Summary Report)

### 텍스트 리포트 포함 내용
1. **최근 분기 주요 지표**
   - 매출액, 순이익, 총자산, 자기자본

2. **성장성 요약**
   - YoY/QoQ 성장률
   - TTM 성장률

3. **수익성 요약**
   - ROE, ROA, ROIC
   - Gross/Operating/Net Margin

4. **재무건전성 요약**
   - 부채비율, 유동비율
   - 이자보상배율, Altman Z-Score

5. **현금흐름 요약**
   - OCF, FCF
   - OCF/Net Income 비율
   - FCF Margin

6. **예측 모델 요약**
   - 회귀식 및 R²
   - 평균 영업이익률

7. **향후 4분기 전망**
   - 매출 예측
   - 영업이익 예측
   - 영업이익률 예측

---

## 📈 생성되는 시각화 자료

### 7개의 종합 차트
1. **Growth Chart** (`{ticker}_growth.png`)
   - 4개 서브차트: Revenue/Operating Income/Net Income Growth + TTM

2. **Profitability Chart** (`{ticker}_profitability.png`)
   - 4개 서브차트: Return Ratios/Profit Margins/ROE TTM/Operating Margin TTM

3. **Financial Health Chart** (`{ticker}_health.png`)
   - 4개 서브차트: Leverage/Liquidity/Interest Coverage/Altman Z-Score

4. **Cash Flow Chart** (`{ticker}_cashflow.png`)
   - 4개 서브차트: CF Components/FCF/Quality of Earnings/FCF Margin

5. **Revenue-OI Model Chart** (`{ticker}_revenue_oi_model.png`)
   - 2개 서브차트: Regression Scatter Plot/Residuals Plot

6. **Forecast Chart** (`{ticker}_forecast.png`)
   - 2개 서브차트: Revenue Forecast/Operating Income Forecast

7. **Comprehensive Dashboard** (`{ticker}_dashboard.png`)
   - 9개 서브차트로 구성된 종합 대시보드

---

## 💾 출력 파일

### 디렉토리 구조
```
financial_reports/
└── {TICKER}/
    ├── {TICKER}_analysis_report.txt    # 텍스트 리포트
    ├── {TICKER}_full_data.csv          # 전체 데이터 (원본 + 모든 계산 지표)
    ├── {TICKER}_growth.png
    ├── {TICKER}_profitability.png
    ├── {TICKER}_health.png
    ├── {TICKER}_cashflow.png
    ├── {TICKER}_revenue_oi_model.png
    ├── {TICKER}_forecast.png
    └── {TICKER}_dashboard.png
```

### CSV 파일 포함 컬럼 (60개 이상)
- 원본 재무제표 항목 (BS, IS, CF)
- 재무비율 (Ratios)
- 성장률 (Growth Rates)
- TTM 지표
- 예측치
- 모든 계산된 중간 지표

---

## 🔧 주요 기능

### 1. 자동화된 데이터 처리
- 결측치 안전 처리 (`_safe_divide`)
- 평균값 계산 (기초/기말 평균)
- TTM 계산 (4분기 rolling sum)
- 이상치 제한 (예: 세율 0-50%)

### 2. 고급 분석 기능
- 선형 회귀 분석 (매출-영업이익)
- 시계열 추세 분석
- 예측 모델링
- Altman Z-Score 파산예측

### 3. 투자 의사결정 지원
- 다차원 재무 분석
- 추세 파악 및 시각화
- 향후 실적 전망
- 종합 건전성 평가

---

## 📋 사용 예시

```python
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem

# 1. 재무비율 계산
df_with_ratios = integrate_financial_ratios(df_normalized)

# 2. 분석 시스템 초기화
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 3. 전체 리포트 생성
results = analyzer.generate_full_report(
    company_name="Apple Inc.",
    ticker="AAPL",
    output_dir="../financial_reports",
    current_price=150.0,
    shares_outstanding=16000000000
)

# 4. 개별 분석 (선택)
growth_df = analyzer.calculate_growth_rates()
prof_df = analyzer.analyze_profitability_trends()
health_df = analyzer.analyze_financial_health()
cf_df = analyzer.analyze_cash_flow()

# 5. 매출-영업이익 예측
model = analyzer.build_revenue_operating_income_model()
predicted_oi = model['predict_function'](100000000)  # 매출 1억 달러 가정
```

---

## ✅ 핵심 장점

1. **포괄성**: 60개 이상의 재무 지표를 한 번에 추출
2. **시각화**: 7개의 전문적인 차트 자동 생성
3. **자동화**: 데이터 수집부터 리포트 생성까지 원클릭
4. **예측**: 회귀 모델 기반 향후 실적 전망
5. **품질**: 결측치 처리, 이상치 제한 등 데이터 품질 관리
6. **확장성**: 배치 분석으로 여러 기업 동시 분석 가능
7. **저장**: CSV, PNG, TXT 형태로 모든 결과 저장

---

## 📞 추가 기능 제안

향후 추가 가능한 기능:
- Peer comparison (동종 업계 비교)
- Historical percentile ranking (과거 데이터 대비 백분위)
- Monte Carlo 시뮬레이션 (확률적 예측)
- DCF 밸류에이션 모델
- Technical analysis indicators
- ESG 지표 통합
- 자동 업데이트 스케줄링
