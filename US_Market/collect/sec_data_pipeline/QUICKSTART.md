# SEC Data Pipeline - 빠른 시작 가이드

## 🚀 5분 안에 시작하기

### 1단계: 필수 라이브러리 설치

```bash
pip install requests pandas sqlalchemy pymysql
```

### 2단계: User-Agent 설정

**중요!** SEC API는 User-Agent가 필수입니다. `example_usage.py`에서 다음 부분을 수정하세요:

```python
# 이 부분을 반드시 수정하세요!
user_agent = "YourCompany Research admin@yourcompany.com"
```

형식: `"회사명 설명 이메일주소"`

### 3단계: 예시 실행

```bash
# 대화형 메뉴 실행
python example_usage.py

# 또는 직접 실행
python -c "from example_usage import example_1_basic_usage; example_1_basic_usage()"
```

## 📋 기본 사용 패턴

### 패턴 1: 단일 기업 재무데이터 가져오기

```python
from collectors import SECAPIClient, RateLimiter
from parsers import CompanyFactsParser, FinancialNormalizer

# 1. 클라이언트 설정
user_agent = "YourCompany admin@yourcompany.com"
client = SECAPIClient(user_agent, RateLimiter(10, 1.0))

# 2. 데이터 수집
company_facts = client.get_company_facts_by_ticker('AAPL')

# 3. 파싱
parser = CompanyFactsParser(company_facts)

# 4. 정규화
normalizer = FinancialNormalizer(parser)
df = normalizer.create_normalized_dataframe(period_type='quarterly')

# 5. 결과 확인
print(df.head())
```

### 패턴 2: 여러 기업 배치 다운로드

```python
from collectors import SECAPIClient, RateLimiter, BulkDownloader

# 클라이언트 설정
user_agent = "YourCompany admin@yourcompany.com"
client = SECAPIClient(user_agent, RateLimiter(10, 1.0))

# Bulk Downloader 생성
downloader = BulkDownloader(client, output_dir="./sec_data")

# 배치 다운로드
tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
results = downloader.download_company_facts_batch(tickers)

print(f"Downloaded {len(results)} companies")
```

### 패턴 3: 데이터베이스 저장

```python
from storage import DBManager

# DB 설정
db_config = {
    'host': 'localhost',
    'port': 3306,
    'database': 'stock_db',
    'user': 'root',
    'password': 'your_password'
}

# DB Manager 생성
db_manager = DBManager(db_config)
db_manager.create_tables()

# 데이터 저장 (위에서 생성한 df 사용)
db_manager.save_normalized_data(
    ticker='AAPL',
    cik=parser.cik,
    df=df
)
```

## 🔍 주요 기능 빠른 참조

### 재무항목 추출

```python
# Revenue 시계열
revenue = normalizer.normalize_single_item('revenue', period_type='quarterly')

# Net Income 시계열  
net_income = normalizer.normalize_single_item('net_income', period_type='quarterly')

# 모든 항목 한번에
all_data = normalizer.normalize_all_items(period_type='quarterly')
```

### 데이터 변환

```python
# Billions 단위로 변환
df_billions = normalizer.convert_to_billions(df)

# TTM (Trailing Twelve Months) 계산
df_ttm = normalizer.calculate_ttm(df_billions, columns=['revenue', 'net_income'])

# YoY 성장률 계산
df_growth = normalizer.calculate_growth_rates(df_billions, columns=['revenue'])

# 재무비율 계산
df_ratios = normalizer.calculate_financial_ratios(df)
```

### CSV 저장

```python
# 기본 저장
normalizer.export_to_csv(df, 'financial_data.csv')

# 또는 pandas DataFrame 직접 저장
df.to_csv('my_data.csv')
```

## 📊 지원하는 재무 항목

| 항목명 | 설명 |
|-------|------|
| revenue | 매출 |
| net_income | 순이익 |
| operating_income | 영업이익 |
| gross_profit | 매출총이익 |
| total_assets | 총자산 |
| total_liabilities | 총부채 |
| stockholders_equity | 자본 |
| cash | 현금및현금성자산 |
| cost_of_revenue | 매출원가 |
| operating_expenses | 영업비용 |
| research_development | 연구개발비 |
| earnings_per_share | 주당순이익 (EPS) |

더 많은 항목은 `parsers/financial_normalizer.py`의 `TAG_MAPPING` 참조

## ⚙️ 주요 설정

### Rate Limiting

```python
# 기본 (SEC 권장: 10 req/sec)
limiter = RateLimiter(max_calls=10, time_window=1.0)

# 적응형 (429 에러 시 자동 조절)
limiter = AdaptiveRateLimiter(max_calls=10, time_window=1.0)

# 버스트 허용
limiter = BurstRateLimiter(rate=10, burst=20)
```

### Period Type

```python
# 분기 데이터
df = normalizer.create_normalized_dataframe(period_type='quarterly')

# 연간 데이터
df = normalizer.create_normalized_dataframe(period_type='annual')

# 모든 데이터
df = normalizer.create_normalized_dataframe(period_type='any')
```

## 🐛 문제 해결

### 403 Forbidden 에러

→ User-Agent를 올바르게 설정했는지 확인

```python
user_agent = "YourCompany Research admin@yourcompany.com"
```

### 429 Too Many Requests

→ RateLimiter 사용 또는 AdaptiveRateLimiter로 변경

```python
limiter = AdaptiveRateLimiter(max_calls=8, time_window=1.0)  # 여유있게 설정
```

### 데이터가 없음 (empty DataFrame)

→ 해당 기업이 그 항목을 보고하지 않았을 수 있음. 다른 항목을 시도하거나 `get_available_tags()`로 확인

```python
parser = CompanyFactsParser(company_facts)
tags = parser.get_available_tags('us-gaap')
print(f"Available tags: {tags[:20]}")  # 처음 20개 확인
```

### DB 연결 실패

→ MySQL/MariaDB가 실행 중인지, 연결 정보가 정확한지 확인

```bash
# MySQL 상태 확인
sudo systemctl status mysql

# 또는
mysql -u root -p -e "SELECT 1"
```

## 📚 다음 단계

1. **README.md** - 전체 문서 읽기
2. **example_usage.py** - 4가지 예시 실행해보기
3. 실제 프로젝트에 통합하기
4. FMP 데이터와 교차 검증 추가 (validators 모듈 개발)

## 💡 팁

### 효율적인 데이터 수집

```python
# 1. Ticker 목록을 CSV로 저장
tickers_df = pd.DataFrame({'ticker': ['AAPL', 'MSFT', 'GOOGL']})
tickers_df.to_csv('my_tickers.csv', index=False)

# 2. CSV에서 읽어서 다운로드
results = downloader.download_from_file('my_tickers.csv')

# 3. 중단된 다운로드 재개
results = downloader.resume_download(all_tickers)
```

### 메모리 절약

```python
# 한 번에 처리하지 말고 배치로 나누기
ticker_batches = [tickers[i:i+10] for i in range(0, len(tickers), 10)]

for batch in ticker_batches:
    results = downloader.download_company_facts_batch(batch)
    # 처리...
    del results  # 메모리 해제
```

### 로그 남기기

```python
import logging

logging.basicConfig(
    filename='sec_pipeline.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 이후 print 대신 logging 사용
logging.info("Starting data collection...")
```

## 🔗 참고 링크

- [SEC EDGAR](https://www.sec.gov/edgar)
- [Company Facts API](https://www.sec.gov/edgar/sec-api-documentation#company-facts-api)
- [전체 문서](README.md)

---

문제가 있거나 질문이 있으시면 이슈를 등록해주세요!
