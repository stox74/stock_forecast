# US PSR Valuation 실행 가이드

## 파일 구성
- `us_psr_valuation_run.py`: 메인 밸류에이션 스크립트
- `run_valuation_0_10.py`: Ticker 0-10 실행 스크립트

## 실행 방법

### 1. 기본 실행 (Ticker 0-10, Batch 10)
```bash
python run_valuation_0_10.py
```

### 2. 원본 스크립트 직접 실행
```bash
# Ticker 0-10, Batch 10
python us_psr_valuation_run.py --ticker-range 0:10 --batch-size 10

# Ticker 0-50, Batch 20
python us_psr_valuation_run.py --ticker-range 0:50 --batch-size 20

# 전체 Ticker, 기본 Batch
python us_psr_valuation_run.py

# CSV 파일에서 Ticker 읽기
python us_psr_valuation_run.py --ticker-file my_tickers.csv --batch-size 15
```

## 파라미터 설명
- `--ticker-range`: Ticker 범위 지정 (예: 0:10, 50:100)
- `--batch-size`: 배치 크기 (기본값: config.py의 BATCH_SIZE)
- `--ticker-file`: Ticker 리스트 CSV 파일 경로

## 출력 결과
- **DB 테이블**:
  - `us_psr_valuation_result`: PSR 밸류에이션 결과
  - `us_revenue_forecast_result`: 매출 예측 결과
- **CSV 파일**:
  - `valuation_error_list.csv`: 오류 발생 Ticker 리스트
  - `market_cap_missing_ticker_YYYYMMDD.csv`: Market cap 데이터 누락 Ticker

## 실행 예시
```bash
# 실행
$ python run_valuation_0_10.py

================================================================================
US PSR Valuation 실행
================================================================================
스크립트: /path/to/us_psr_valuation_run.py
Ticker 범위: 0:10
Batch 크기: 10
================================================================================

실행 명령어: python us_psr_valuation_run.py --ticker-range 0:10 --batch-size 10

[TICKER] 1/10 AAPL
[OK-FMP-REV] AAPL revenue data fetched
...
[BATCH-FLUSH] valuation=10, revenue=10, is_last=True
[PSR-VALUATION-SAVE] Saved 150 rows to us_psr_valuation_result
[REV-FORECAST-SAVE] Saved 200 rows to us_revenue_forecast_result

================================================================================
처리 완료
================================================================================
성공 ticker: 10
PSR Valuation 업서트 rows: 150
Revenue forecast 업서트 rows: 200
오류 ticker: 0
```

## 주의사항
1. 실행 전 필수 모듈 임포트 확인 필요
2. DB 연결 정보 (config.py) 확인
3. FMP API 키 (config.py) 확인
4. 네트워크 연결 상태 확인
