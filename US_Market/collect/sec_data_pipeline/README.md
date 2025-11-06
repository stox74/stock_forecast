# SEC Data Pipeline

미국 SEC EDGAR API를 통해 상장기업 재무데이터를 수집, 파싱, 정규화하여 DB에 저장하는 파이프라인입니다.

## 📁 프로젝트 구조

```
sec_data_pipeline/
├── collectors/              # 데이터 수집
│   ├── __init__.py
│   ├── sec_api_client.py   # SEC API 클라이언트
│   ├── rate_limiter.py     # Rate limit 관리
│   └── bulk_downloader.py  # 대량 다운로드
│
├── parsers/                 # 데이터 파싱 및 정규화
│   ├── __init__.py
│   ├── company_facts_parser.py      # Company Facts JSON 파싱
│   └── financial_normalizer.py      # 재무항목 표준화
│
├── storage/                 # 데이터 저장
│   ├── __init__.py
│   ├── db_manager.py       # DB 저장 관리
│   └── cache_manager.py    # 캐시 관리 (TODO)
│
├── validators/              # 데이터 검증
│   ├── __init__.py
│   ├── data_validator.py   # FMP와 데이터 비교 검증 (TODO)
│   └── consistency_checker.py  # 일관성 체크 (TODO)
│
└── api/                     # API 서비스
    ├── __init__.py
    ├── endpoints.py        # FastAPI 엔드포인트 (TODO)
    └── query_builder.py    # 쿼리 빌더 (TODO)
```

## 🚀 빠른 시작

### 1. 필수 라이브러리 설치

```bash
pip install requests pandas sqlalchemy pymysql
```

### 2. 기본 사용 예시

```python
from collectors import SECAPIClient, RateLimiter
from parsers import CompanyFactsParser, FinancialNormalizer

# 1. SEC API 클라이언트 설정
user_agent = "YourCompany Research admin@yourcompany.com"  # 필수!
rate_limiter = RateLimiter(max_calls=10, time_window=1.0)
client = SECAPIClient(user_agent, rate_limiter)

# 2. Company Facts 데이터 가져오기
company_facts = client.get_company_facts_by_ticker('AAPL')

# 3. 데이터 파싱
parser = CompanyFactsParser(company_facts)

# 4. 재무데이터 정규화
normalizer = FinancialNormalizer(parser)
df = normalizer.create_normalized_dataframe(period_type='quarterly')

# 5. Billions 변환 및 TTM 계산
df_billions = normalizer.convert_to_billions(df)
df_ttm = normalizer.calculate_ttm(df_billions, columns=['revenue', 'net_income'])

print(df_ttm[['revenue', 'revenue_ttm']].tail())
```

## 📚 주요 기능

### 1. Collectors (데이터 수집)

#### SECAPIClient
```python
client = SECAPIClient(user_agent, rate_limiter)

# Ticker로 Company Facts 조회
data = client.get_company_facts_by_ticker('AAPL')

# CIK로 Company Facts 조회
data = client.get_company_facts('0000320193')

# 전체 기업 Ticker-CIK 매핑
tickers = client.get_company_tickers()

# Submissions 조회
submissions = client.get_submissions('0000320193')
```

#### Rate Limiter
```python
# 기본 Rate Limiter (SEC 권장: 10 req/sec)
limiter = RateLimiter(max_calls=10, time_window=1.0)

# 적응형 Rate Limiter (429 에러 시 자동으로 속도 조절)
adaptive = AdaptiveRateLimiter(max_calls=10, time_window=1.0)
adaptive.on_rate_limit_error()  # 429 에러 발생 시 호출

# 버스트 허용 Rate Limiter
burst = BurstRateLimiter(rate=10, burst=20)
```

#### Bulk Downloader
```python
downloader = BulkDownloader(client, output_dir="./sec_data", max_workers=5)

# Ticker 리스트로 배치 다운로드
tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
results = downloader.download_company_facts_batch(tickers)

# CSV 파일에서 Ticker 읽어서 다운로드
results = downloader.download_from_file('tickers.csv')

# CIK 범위로 다운로드
results = downloader.download_by_cik_range(start_cik=1000, end_cik=2000)

# 다운로드 리포트 생성
downloader.create_download_report(results)
```

### 2. Parsers (데이터 파싱)

#### CompanyFactsParser
```python
parser = CompanyFactsParser(company_facts_data)

# 사용 가능한 태그 확인
taxonomies = parser.get_available_taxonomies()  # ['us-gaap', 'dei', ...]
tags = parser.get_available_tags('us-gaap')     # ['Revenues', 'Assets', ...]

# 특정 태그 데이터 추출
revenue_df = parser.extract_tag_data('Revenues', taxonomy='us-gaap', unit='USD')

# 분기/연간 데이터 추출
quarterly = parser.get_quarterly_data('Revenues')
annual = parser.get_annual_data('Revenues')

# 시계열 Series 생성
revenue_series = parser.create_time_series('Revenues', period_type='quarterly')

# 최신값 조회
latest_revenue = parser.get_latest_value('Revenues', period_type='quarterly')

# 재무제표 요약
summary = parser.get_financial_statement_summary()
```

#### FinancialNormalizer
```python
normalizer = FinancialNormalizer(parser)

# 단일 항목 정규화 (우선순위 기반 태그 선택)
revenue_series = normalizer.normalize_single_item('revenue', period_type='quarterly')

# 모든 표준 항목 정규화
normalized_data = normalizer.normalize_all_items(period_type='quarterly')

# DataFrame 생성
df = normalizer.create_normalized_dataframe(period_type='quarterly')

# Billions 변환
df_billions = normalizer.convert_to_billions(df)

# TTM 계산
df_ttm = normalizer.calculate_ttm(df_billions, columns=['revenue', 'net_income'])

# 성장률 계산 (YoY)
df_growth = normalizer.calculate_growth_rates(df_billions, columns=['revenue'])

# 재무비율 계산
df_ratios = normalizer.calculate_financial_ratios(df)

# 최신 재무 스냅샷
snapshot = normalizer.get_latest_financial_snapshot()

# CSV 저장
normalizer.export_to_csv(df, 'financial_data.csv')
```

### 3. Storage (데이터 저장)

#### DBManager
```python
db_config = {
    'host': 'localhost',
    'port': 3306,
    'database': 'stock_db',
    'user': 'root',
    'password': ''
}

db_manager = DBManager(db_config)

# 테이블 생성
db_manager.create_tables()

# 정규화된 데이터 저장
db_manager.save_normalized_data(
    ticker='AAPL',
    cik='0000320193',
    df=df_normalized
)

# 데이터 조회
df = db_manager.query_financial_data(
    ticker='AAPL',
    item_name='revenue',
    start_date='2020-01-01',
    end_date='2023-12-31'
)
```

## 🔧 표준 재무항목 매핑

FinancialNormalizer는 다양한 XBRL 태그를 다음 표준 항목으로 자동 매핑합니다:

| 표준 항목 | XBRL 태그 (우선순위 순) |
|---------|----------------------|
| revenue | Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet |
| net_income | NetIncomeLoss, ProfitLoss |
| operating_income | OperatingIncomeLoss |
| gross_profit | GrossProfit |
| total_assets | Assets |
| current_assets | AssetsCurrent |
| total_liabilities | Liabilities |
| current_liabilities | LiabilitiesCurrent |
| stockholders_equity | StockholdersEquity |
| cash | CashAndCashEquivalentsAtCarryingValue, Cash |
| long_term_debt | LongTermDebt, LongTermDebtNoncurrent |
| cost_of_revenue | CostOfRevenue, CostOfGoodsAndServicesSold |
| operating_expenses | OperatingExpenses |
| research_development | ResearchAndDevelopmentExpense |
| shares_outstanding | CommonStockSharesOutstanding |
| earnings_per_share | EarningsPerShareBasic, EarningsPerShareDiluted |

## 💡 고급 사용 예시

### 예시 1: 여러 기업 데이터 수집 및 비교

```python
from collectors import SECAPIClient, RateLimiter, BulkDownloader
from parsers import CompanyFactsParser, FinancialNormalizer
import pandas as pd

# 설정
user_agent = "MyCompany admin@mycompany.com"
client = SECAPIClient(user_agent, RateLimiter(10, 1.0))
downloader = BulkDownloader(client)

# 여러 기업 다운로드
tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN']
results = downloader.download_company_facts_batch(tickers)

# 각 기업의 revenue 데이터 추출 및 비교
revenue_comparison = {}

for ticker, company_facts in results.items():
    parser = CompanyFactsParser(company_facts)
    normalizer = FinancialNormalizer(parser)
    
    revenue_series = normalizer.normalize_single_item('revenue', period_type='quarterly')
    if revenue_series is not None:
        revenue_comparison[ticker] = revenue_series / 1_000_000_000  # Billions

# DataFrame으로 결합
comparison_df = pd.DataFrame(revenue_comparison)
comparison_df = comparison_df.tail(8)  # 최근 8분기

print(comparison_df)
```

### 예시 2: DB 저장 및 조회 파이프라인

```python
from collectors import SECAPIClient, RateLimiter
from parsers import CompanyFactsParser, FinancialNormalizer
from storage import DBManager

# 설정
user_agent = "MyCompany admin@mycompany.com"
client = SECAPIClient(user_agent, RateLimiter(10, 1.0))

db_config = {
    'host': 'localhost',
    'port': 3306,
    'database': 'stock_db',
    'user': 'root',
    'password': ''
}
db_manager = DBManager(db_config)
db_manager.create_tables()

# 데이터 수집 및 저장
ticker = 'AAPL'
company_facts = client.get_company_facts_by_ticker(ticker)

parser = CompanyFactsParser(company_facts)
normalizer = FinancialNormalizer(parser)

# 정규화된 DataFrame
df = normalizer.create_normalized_dataframe(period_type='quarterly')
df_billions = normalizer.convert_to_billions(df)

# DB 저장
db_manager.save_normalized_data(
    ticker=ticker,
    cik=parser.cik,
    df=df_billions
)

# DB에서 조회
revenue_data = db_manager.query_financial_data(
    ticker='AAPL',
    item_name='revenue',
    start_date='2020-01-01'
)

print(revenue_data)
```

### 예시 3: 자동화된 데이터 업데이트 스크립트

```python
import schedule
import time
from datetime import datetime

def update_financial_data():
    """재무데이터 업데이트 작업"""
    print(f"\n[{datetime.now()}] Starting financial data update...")
    
    # Ticker 리스트
    tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
    
    # 데이터 수집
    client = SECAPIClient(user_agent, RateLimiter(10, 1.0))
    downloader = BulkDownloader(client)
    results = downloader.download_company_facts_batch(tickers)
    
    # DB 저장
    db_manager = DBManager(db_config)
    
    for ticker, company_facts in results.items():
        try:
            parser = CompanyFactsParser(company_facts)
            normalizer = FinancialNormalizer(parser)
            df = normalizer.create_normalized_dataframe(period_type='quarterly')
            df_billions = normalizer.convert_to_billions(df)
            
            db_manager.save_normalized_data(ticker, parser.cik, df_billions)
            print(f"  ✓ Updated {ticker}")
        except Exception as e:
            print(f"  ✗ Failed to update {ticker}: {e}")
    
    print(f"[{datetime.now()}] Update completed\n")

# 매일 오전 6시에 실행
schedule.every().day.at("06:00").do(update_financial_data)

# 또는 매주 월요일 오전 6시
# schedule.every().monday.at("06:00").do(update_financial_data)

print("Scheduler started. Press Ctrl+C to exit.")
while True:
    schedule.run_pending()
    time.sleep(60)
```

## ⚠️ 주의사항

### 1. User-Agent 필수 설정
SEC API는 User-Agent 헤더가 필수입니다. 회사명과 이메일을 포함해야 합니다.

```python
# 올바른 예시
user_agent = "MyCompany Research admin@mycompany.com"

# 잘못된 예시 (차단될 수 있음)
user_agent = "Python Script"
```

### 2. Rate Limiting
- SEC는 초당 10 요청으로 제한합니다
- RateLimiter를 반드시 사용하세요
- 429 에러 발생 시 AdaptiveRateLimiter 사용 권장

### 3. 데이터 정합성
- 동일한 재무 항목이 여러 XBRL 태그로 보고될 수 있습니다
- FinancialNormalizer가 우선순위 기반으로 자동 선택합니다
- 중요한 데이터는 FMP와 교차 검증 권장

### 4. TTM 계산
- TTM은 최근 4분기 데이터가 필요합니다
- 분기 데이터에서만 계산 가능합니다

## 📊 데이터베이스 스키마

### sec_financial_data 테이블

| 컬럼 | 타입 | 설명 |
|-----|------|------|
| id | INT | Primary Key |
| ticker | VARCHAR(20) | 주식 티커 |
| cik | VARCHAR(20) | CIK 번호 |
| date | DATE | 재무 데이터 날짜 |
| fiscal_year | INT | 회계연도 |
| fiscal_period | VARCHAR(10) | 회계기간 (Q1, Q2, etc) |
| item_name | VARCHAR(100) | 재무 항목명 |
| value | FLOAT | 값 |
| unit | VARCHAR(20) | 단위 |
| data_source | VARCHAR(50) | 데이터 소스 |
| created_at | DATETIME | 생성 시각 |
| updated_at | DATETIME | 업데이트 시각 |

## 🔜 TODO (향후 개발 계획)

### validators/
- [ ] data_validator.py - FMP 데이터와 교차 검증
- [ ] consistency_checker.py - 시계열 일관성 체크

### storage/
- [ ] cache_manager.py - Redis 캐싱

### api/
- [ ] endpoints.py - FastAPI REST API
- [ ] query_builder.py - 복잡한 쿼리 빌더

### parsers/
- [ ] xbrl_parser.py - 원시 XBRL 문서 파싱

## 📖 참고 자료

- [SEC EDGAR API Documentation](https://www.sec.gov/edgar/sec-api-documentation)
- [SEC Company Facts API](https://www.sec.gov/edgar/sec-api-documentation#company-facts-api)
- [XBRL US GAAP Taxonomy](https://xbrl.us/data-rule/dqc_0015-negative-values/)

## 📄 라이선스

MIT License

## 👤 문의

이슈나 질문이 있으시면 GitHub Issues를 이용해주세요.
