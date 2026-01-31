# LSTM 주가 예측 시스템 사용 가이드

## 개요

SARIMA 기반 예측 시스템을 LSTM (Long Short-Term Memory) 딥러닝 모델로 전환한 버전입니다.
GPU 가속을 활용하여 예측 속도를 크게 향상시켰습니다.

## 주요 특징

### 1. GPU 가속
- RTX 3090 GPU를 활용한 고속 학습
- 배치 처리 중 자동 GPU 메모리 관리
- CPU 대비 2~5배 빠른 학습 속도

### 2. 하이퍼파라미터 최적화
- 시간 기반 교차 검증 (Time Series Split)
- 자동으로 최적 파라미터 탐색:
  * 시퀀스 길이 (12, 24, 36개월)
  * LSTM 레이어 구조 ([64], [128,64], [128,64,32])
  * 드롭아웃 비율
  * 학습률

### 3. SARIMA 대비 장점
- 비선형 패턴 학습 능력
- 복잡한 시계열 관계 포착
- GPU를 통한 빠른 처리
- 대용량 데이터 처리에 유리

## 파일 구조

```
stock_price_forecast_lstm.py  # LSTM 예측 모듈 (핵심 로직)
run_lstm_forecast.py           # 실행 스크립트
test_lstm_quick.py             # 빠른 테스트 스크립트
```

## 설치 및 환경 설정

### 1. 필수 패키지 설치

```bash
pip install tensorflow==2.10.1
pip install numpy pandas scikit-learn pymysql
```

### 2. GPU 설정 확인

```python
python -c "import tensorflow as tf; print(f'GPU: {len(tf.config.list_physical_devices(\"GPU\"))}개')"
```

출력이 `GPU: 1개` 이상이면 정상입니다.

## 사용 방법

### 방법 1: 빠른 테스트 (DB 연결 불필요)

```bash
python test_lstm_quick.py
```

이 스크립트는:
- GPU 환경 확인
- 샘플 데이터로 LSTM 모델 학습
- 6개월 예측 수행
- CPU vs GPU 성능 비교 (선택)

### 방법 2: 실제 데이터로 예측 실행

```bash
python run_lstm_forecast.py
```

실행 시 선택 사항:
1. 모드 선택 (5개 ~ 2000개 티커)
2. 최적화 여부
3. 실행 확인

## 예측 프로세스

### 1. 데이터 추출
```python
# DB에서 월말 종가 데이터 추출
df_monthly = get_monthly_close_price(ticker, connection)
```

### 2. 데이터 정규화
```python
# MinMaxScaler로 0~1 범위로 정규화
scaler = MinMaxScaler()
scaled_prices = scaler.fit_transform(prices)
```

### 3. 시퀀스 생성
```python
# 시계열을 고정 길이 시퀀스로 변환
# 예: 24개월 데이터 → 다음 1개월 예측
X, y = create_sequences(data, seq_length=24)
```

### 4. 모델 학습 (GPU)
```python
with tf.device('/GPU:0'):
    model.fit(X, y, epochs=100, callbacks=[early_stop])
```

### 5. 미래 예측
```python
# 반복적 예측 (Recursive Forecasting)
for i in range(6):  # 6개월 예측
    next_month = model.predict(current_sequence)
    # 시퀀스 업데이트 (슬라이딩 윈도우)
```

### 6. 역정규화 및 저장
```python
# 정규화된 값을 원래 가격으로 변환
forecast_prices = scaler.inverse_transform(forecast_scaled)
# DB에 저장
save_forecast_to_db(results, connection)
```

## 최적화 옵션

### optimize=True (권장: 소량 티커)
- 각 티커마다 최적 파라미터 탐색
- 더 높은 정확도
- 티커당 30~60초 소요

```python
process_tickers_batch(
    tickers=ticker_list[:20],
    optimize_params=True
)
```

### optimize=False (권장: 대량 티커)
- 기본 파라미터 사용
- 빠른 처리
- 티커당 10~20초 소요

```python
process_tickers_batch(
    tickers=ticker_list,
    optimize_params=False
)
```

## 모델 파라미터 설명

### 시퀀스 길이 (seq_length)
- **의미**: 예측에 사용할 과거 데이터 개월 수
- **옵션**: 12, 24, 36개월
- **영향**: 
  * 짧을수록: 빠른 학습, 최근 패턴 강조
  * 길수록: 장기 패턴 포착, 과적합 위험

### LSTM 유닛 (lstm_units)
- **의미**: 각 LSTM 레이어의 뉴런 개수
- **옵션**: 
  * [64]: 단일 레이어, 빠른 학습
  * [128, 64]: 2개 레이어, 균형잡힌 성능
  * [128, 64, 32]: 3개 레이어, 복잡한 패턴 학습
- **영향**: 
  * 많을수록: 더 복잡한 패턴 학습, 과적합 위험
  * 적을수록: 빠른 학습, 일반화 능력

### 드롭아웃 (dropout)
- **의미**: 과적합 방지를 위한 무작위 뉴런 비활성화 비율
- **값**: 0.2 (20%)
- **영향**: 모델 일반화 능력 향상

### 학습률 (learning_rate)
- **의미**: 모델 가중치 업데이트 속도
- **옵션**: 0.001 (기본), 0.0005 (느림)
- **영향**:
  * 높을수록: 빠른 학습, 불안정할 수 있음
  * 낮을수록: 안정적 학습, 느린 수렴

## DB 테이블 구조

```sql
CREATE TABLE us_stock_price_forecast_result (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,              -- 예측 대상 날짜
    ticker VARCHAR(20) NOT NULL,     -- 종목 티커
    item VARCHAR(50) NOT NULL,       -- 모델명 ('lstm')
    value DOUBLE,                    -- 예측 주가
    forecast_at DATE NOT NULL,       -- 예측 수행 날짜
    model_params JSON,               -- 모델 파라미터
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_forecast (date, ticker, item, forecast_at)
);
```

## 예측 결과 조회

### Python에서 조회

```python
from stock_price_forecast_lstm import get_forecast_summary

# 특정 티커의 오늘 예측
df = get_forecast_summary(ticker='AAPL', forecast_date='2026-01-31')

# 특정 티커의 모든 예측
df = get_forecast_summary(ticker='AAPL')

# 오늘의 모든 예측
df = get_forecast_summary(forecast_date='2026-01-31')

# 최근 100개 예측
df = get_forecast_summary()
```

### SQL로 조회

```sql
-- 특정 티커의 최신 예측
SELECT date, value 
FROM us_stock_price_forecast_result
WHERE ticker = 'AAPL' 
  AND item = 'lstm'
  AND forecast_at = (
      SELECT MAX(forecast_at) 
      FROM us_stock_price_forecast_result 
      WHERE ticker = 'AAPL' AND item = 'lstm'
  )
ORDER BY date;

-- 예측 현황 요약
SELECT ticker, 
       MAX(forecast_at) as latest_forecast,
       COUNT(*) as forecast_count
FROM us_stock_price_forecast_result
WHERE item = 'lstm'
GROUP BY ticker
ORDER BY latest_forecast DESC;
```

## 성능 벤치마크

### GPU (RTX 3090) vs CPU

| 설정 | GPU 시간 | CPU 시간 | 속도 향상 |
|------|---------|---------|----------|
| 최적화 ON (1 티커) | ~30초 | ~80초 | 2.7배 |
| 최적화 OFF (1 티커) | ~10초 | ~25초 | 2.5배 |
| 배치 10개 티커 | ~5분 | ~12분 | 2.4배 |

### 예상 소요 시간

| 티커 수 | 최적화 ON | 최적화 OFF |
|--------|----------|-----------|
| 10개 | 5분 | 2분 |
| 50개 | 25분 | 8분 |
| 100개 | 50분 | 17분 |
| 2000개 | ~17시간 | ~6시간 |

## 문제 해결

### GPU 인식 안됨
```bash
# TensorFlow 버전 확인
python -c "import tensorflow as tf; print(tf.__version__)"

# 2.10.1이 아니면 재설치
pip uninstall tensorflow -y
pip install tensorflow==2.10.1
```

### 메모리 부족 오류
```python
# 배치 크기 줄이기
process_tickers_batch(
    tickers=ticker_list,
    batch_size=5  # 기본값 10에서 5로 감소
)
```

### 학습이 너무 느림
```python
# 최적화 비활성화
process_tickers_batch(
    tickers=ticker_list,
    optimize_params=False
)
```

### DB 연결 오류
```python
# DB 설정 확인
DB_CONFIG = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}
```

## SARIMA vs LSTM 비교

| 특성 | SARIMA | LSTM |
|-----|--------|------|
| 모델 타입 | 통계적 | 딥러닝 |
| 비선형 패턴 | 제한적 | 우수 |
| 계절성 처리 | 명시적 | 자동 학습 |
| 학습 속도 | 빠름 | 느림 (GPU로 보완) |
| 해석 가능성 | 높음 | 낮음 |
| 데이터 요구량 | 적음 | 많음 |
| 과적합 위험 | 낮음 | 중간 (드롭아웃으로 완화) |

## 권장 사용 시나리오

### LSTM 권장
- 복잡한 비선형 패턴이 예상되는 경우
- 충분한 과거 데이터 (36개월 이상)
- GPU 사용 가능
- 높은 정확도가 중요한 경우

### SARIMA 권장
- 명확한 계절성 패턴
- 데이터가 부족한 경우 (~36개월)
- CPU만 사용 가능
- 빠른 처리가 중요한 경우
- 해석 가능성이 중요한 경우

## 추가 개선 방안

### 1. 앙상블 모델
```python
# SARIMA + LSTM 결합 예측
sarima_pred = forecast_sarima(...)
lstm_pred = forecast_lstm(...)
final_pred = 0.5 * sarima_pred + 0.5 * lstm_pred
```

### 2. 다변량 입력
```python
# 거래량, 경제지표 등 추가
X = create_sequences_multivariate(
    prices, volumes, indicators
)
```

### 3. Attention 메커니즘
```python
# 중요 시점에 가중치 부여
model.add(tf.keras.layers.Attention())
```

## 문의 및 지원

- 코드 문제: 로그 파일 확인
- GPU 문제: nvidia-smi 실행
- DB 문제: MySQL 연결 확인

---

마지막 업데이트: 2026-01-31
