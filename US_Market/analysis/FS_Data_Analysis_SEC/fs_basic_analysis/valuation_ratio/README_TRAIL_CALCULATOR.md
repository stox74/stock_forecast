# Trail PER/PBR Calculator

FMP (Financial Modeling Prep) API를 사용하여 Trail PER, PBR, PSR을 계산하고 시각화하는 Python 프로그램입니다.

## 주요 기능

1. **일별/월별 Trail PER, PBR, PSR 계산**
   - FMP API에서 주가 데이터 및 재무제표 데이터 수집
   - TTM (Trailing Twelve Months) 기준으로 EPS, BPS, RPS 계산
   - 주가 대비 Trail 밸류에이션 비율 산출

2. **시각화**
   - Trail PER, PBR, PSR 시계열 차트
   - 기간 평균선 표시
   - 고해상도 PNG 파일로 저장

3. **Excel 저장**
   - 전체 데이터 (산출근거 포함)
   - 요약 통계 (평균, 중앙값, 최소/최대값, 표준편차)
   - 계산 방법 설명
   - 메타 정보

## 설치 방법

```bash
pip install requests pandas numpy matplotlib openpyxl
```

## 사용 방법

### 1. 기본 사용

```python
from trail_per_pbr_calculator import TrailValuationCalculator

# API 키 설정
API_KEY = "your_fmp_api_key_here"

# Calculator 인스턴스 생성
calculator = TrailValuationCalculator(
    api_key=API_KEY,
    output_folder='./results'  # 결과 저장 폴더
)

# 일별 데이터 분석
daily_df = calculator.analyze(
    symbol="AAPL",              # 종목 코드
    start_date="2023-01-01",    # 시작일
    end_date="2024-12-31",      # 종료일
    frequency='daily'           # 'daily' 또는 'monthly'
)
```

### 2. 월별 데이터 분석

```python
# 월말 데이터만 추출
monthly_df = calculator.analyze(
    symbol="AAPL",
    start_date="2023-01-01",
    end_date="2024-12-31",
    frequency='monthly'  # 매월 말일 데이터
)
```

### 3. 여러 종목 분석

```python
symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN']

for symbol in symbols:
    try:
        df = calculator.analyze(
            symbol=symbol,
            start_date="2023-01-01",
            end_date="2024-12-31",
            frequency='monthly'
        )
        print(f"{symbol} 분석 완료\n")
    except Exception as e:
        print(f"{symbol} 분석 실패: {e}\n")
```

### 4. 개별 메서드 사용

```python
# 주가 데이터만 가져오기
price_df = calculator.get_historical_prices("AAPL", "2023-01-01", "2024-12-31")

# TTM 재무지표 가져오기
ttm_metrics = calculator.get_ttm_metrics("AAPL")

# 분기별 재무지표 가져오기
quarterly_metrics = calculator.get_quarterly_metrics("AAPL", limit=40)
```

## 출력 파일

### 1. Excel 파일 구조

Excel 파일은 4개의 시트로 구성됩니다:

**Sheet 1: Trail_Valuation (메인 데이터)**
- date: 날짜
- close: 종가
- volume: 거래량
- trail_per: Trail PER
- trail_pbr: Trail PBR
- trail_psr: Trail PSR
- eps_ttm: TTM 기준 EPS
- book_value_per_share: 주당 순자산가치
- net_income_ttm: TTM 기준 순이익
- revenue_ttm: TTM 기준 매출
- stockholders_equity: 자본총계
- shares_outstanding: 발행주식수

**Sheet 2: Summary (요약 통계)**
- 각 지표별 평균, 중앙값, 최소값, 최대값, 표준편차

**Sheet 3: Info (메타정보)**
- 종목 코드, 데이터 주기, 기간, 생성일시 등

**Sheet 4: Calculation_Method (계산 방법)**
- 각 지표의 계산식 설명

### 2. 차트 파일

PNG 형식으로 저장되며, 3개의 서브플롯으로 구성:
- Trail PER 시계열 차트 (평균선 포함)
- Trail PBR 시계열 차트 (평균선 포함)
- Trail PSR 시계열 차트 (평균선 포함)

## 계산 방법

### Trail PER (Trailing Price-to-Earnings Ratio)
```
Trail PER = 주가(Close) / EPS(TTM)
EPS(TTM) = 순이익(TTM) / 발행주식수
```

### Trail PBR (Trailing Price-to-Book Ratio)
```
Trail PBR = 주가(Close) / 주당순자산가치
주당순자산가치 = 자본총계 / 발행주식수
```

### Trail PSR (Trailing Price-to-Sales Ratio)
```
Trail PSR = 주가(Close) / 주당매출(TTM)
주당매출(TTM) = 매출(TTM) / 발행주식수
```

### TTM (Trailing Twelve Months)
- 최근 4분기 재무제표 데이터를 합산하여 계산
- 각 날짜 기준으로 그 시점까지 발표된 가장 최근 4분기 데이터 사용

## 데이터 소스

### FMP API Endpoints 사용
1. `/stable/historical-price-eod/full` - 일별 주가 데이터
2. `/stable/key-metrics` - 분기별 재무지표
3. `/stable/income-statement` - 분기별 손익계산서
4. `/stable/balance-sheet-statement` - 분기별 재무상태표

## 주의사항

1. **API 키 필요**: FMP API 키가 필요합니다 (https://site.financialmodelingprep.com/)
2. **데이터 가용성**: 재무제표가 발표되기 전 기간에는 데이터가 없을 수 있습니다
3. **계산 방법**: TTM 계산을 위해 최소 4분기 재무제표 데이터가 필요합니다
4. **음수 처리**: EPS나 BPS가 음수인 경우 Trail PER/PBR은 NaN으로 처리됩니다

## 예제 출력

```
======================================================================
Trail Valuation 분석 시작
  종목: AAPL
  기간: 2023-01-01 ~ 2024-12-31
  주기: daily
======================================================================

AAPL 주가 데이터 수집 중... (2023-01-01 ~ 2024-12-31)
  - 504개의 일별 데이터 수집 완료

AAPL TTM 재무지표 수집 중...
  - TTM 재무지표 수집 완료

AAPL 분기별 재무지표 수집 중...
  - 40개의 분기 데이터 수집 완료

AAPL 분기별 재무제표 수집 중...
  - 40개의 분기 재무제표 수집 완료

AAPL Trail PER/PBR 계산 중...
  - 504개의 유효한 Trail PER/PBR 계산 완료

시각화 생성 중... (daily)
  - 차트 저장: ./trail_valuation_results/AAPL_trail_valuation_daily.png

Excel 파일 저장 중... (daily)
  - Excel 파일 저장: ./trail_valuation_results/AAPL_trail_valuation_daily_20260117_143025.xlsx

======================================================================
분석 완료 요약
======================================================================
전체 데이터 수: 504
유효 데이터 수: 504

평균 Trail PER: 28.45
평균 Trail PBR: 42.31
평균 Trail PSR: 7.89

결과 파일:
  - ./trail_valuation_results/AAPL_trail_valuation_daily_20260117_143025.xlsx
======================================================================
```

## 문의사항

프로그램 사용 중 문제가 발생하면 다음을 확인하세요:
1. FMP API 키가 올바르게 설정되었는지
2. 인터넷 연결 상태
3. 종목 코드가 FMP에서 지원되는지
4. 날짜 형식이 'YYYY-MM-DD' 형식인지
