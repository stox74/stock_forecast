# 주식 가치평가 예측 시스템 개선 버전

## 주요 개선 사항

### 1. 예측 모델 추가 ✅
기존 모델에 **Exponential Smoothing**과 **Theta** 모델을 추가하여 총 5가지 예측 모델을 사용합니다.

**예측 모델 목록:**
- SARIMA (기존)
- LSTM (기존)
- Prophet (기존)
- **Exponential Smoothing (신규)**
- **Theta (신규)**

각 모델의 예측 결과를 앙상블하여 최종 예측값을 생성합니다.

---

### 2. 코드 함수화 및 모듈화 ✅
반복되는 코드를 함수로 분리하여 재사용성과 가독성을 향상시켰습니다.

**주요 함수:**

#### 유틸리티 함수
- `clean_numeric_data()`: 숫자 데이터 정제
- `create_db_engine()`: DB 엔진 생성
- `get_market_cap_by_ticker()`: 시가총액 조회
- `save_valuation_to_db()`: DB 저장

#### 데이터 추출 함수
- `extract_revenue_data()`: 매출 데이터 추출
- `extract_export_data()`: 수출 데이터 추출
- `calculate_yoy_growth()`: YoY 성장률 계산

#### 예측 모델 함수
- `sarima_forecast()`: SARIMA 예측
- `lstm_forecast()`: LSTM 예측
- `prophet_forecast()`: Prophet 예측
- `exponential_smoothing_forecast()`: Exponential Smoothing 예측 (신규)
- `theta_forecast()`: Theta 예측 (신규)

#### 통합 처리 함수
- `combine_data()`: 데이터 결합
- `run_all_forecasts()`: 모든 예측 모델 실행
- `create_ensemble_forecast()`: 앙상블 예측 생성
- `process_single_ticker()`: 단일 티커 처리
- `process_multiple_tickers()`: 여러 티커 일괄 처리

---

### 3. 다중 티커 순차 처리 기능 ✅
여러 개의 티커 코드를 리스트로 입력하면 순차적으로 루프를 돌며 작업을 수행합니다.

**사용 예시:**
```python
ticker_list = [
    {'ticker': 'A084370', 'hs_code': None},
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
]

results = process_multiple_tickers(
    ticker_list=ticker_list,
    db_info=db_info,
    table_name='Korea_company_valuation_ver2'
)
```

---

### 4. 효율적인 메모리 관리 ✅
메모리 누수를 방지하고 효율적인 메모리 사용을 위한 코드를 추가했습니다.

**메모리 관리 기법:**
- `gc.collect()`: 각 처리 단계마다 가비지 컬렉션 실행
- `del` 명령어: 사용 완료한 대형 객체 명시적 삭제
- `engine.dispose()`: DB 연결 명시적 종료
- 각 함수 종료 시 불필요한 변수 정리

**적용 위치:**
- 데이터 추출 후
- 모델 학습 및 예측 후
- 단일 티커 처리 완료 후

---

### 5. 에러 안전장치 ✅
에러 발생 시 전체 프로세스가 중단되지 않고, 에러가 난 티커만 기록하고 다음 티커로 넘어갑니다.

**에러 처리 메커니즘:**
- 각 함수에 `try-except` 블록 적용
- 에러 발생 시 에러 메시지 출력 및 로그 파일 기록
- 에러 티커 리스트 자동 생성
- 처리 계속 진행

**에러 로그 파일 (`error_log.txt`):**
```
에러 로그 - 2025-10-26 15:30:45
==================================================

A123456: 처리 실패 (데이터 부족 또는 저장 실패)
A789012: LSTM 예측 실패: Invalid input shape
```

---

### 6. 진행 상황 표시 (tqdm) ✅
`tqdm` 라이브러리를 사용하여 실시간 진행 상황을 표시합니다.

**진행 표시 예시:**
```
티커 처리 진행: 45%|████████▌         | 45/100 [05:32<06:48,  7.42s/ticker]
```

**표시 정보:**
- 현재 진행률 (%)
- 진행 바
- 처리된 티커 수 / 전체 티커 수
- 경과 시간 / 예상 남은 시간
- 평균 처리 속도

---

## 사용 방법

### 1. 필수 라이브러리 설치
```bash
pip install pandas numpy tqdm scikit-learn tensorflow prophet statsmodels sqlalchemy pymysql matplotlib --break-system-packages
```

### 2. 단일 티커 처리
```python
from improved_forecast_system import process_single_ticker

db_info = {
    'host': 'your_host',
    'port': 3307,
    'user': 'your_user',
    'password': 'your_password',
    'database': 'investar'
}

success = process_single_ticker(
    ticker='A084370',
    hs_code=None,
    db_info=db_info,
    table_name='Korea_company_valuation_ver2'
)
```

### 3. 여러 티커 일괄 처리
```python
from improved_forecast_system import process_multiple_tickers

ticker_list = [
    {'ticker': 'A084370', 'hs_code': None},
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
]

results = process_multiple_tickers(
    ticker_list=ticker_list,
    db_info=db_info,
    table_name='Korea_company_valuation_ver2',
    error_log_file='forecast_error_log.txt'
)

# 결과 확인
print(f"전체: {results['total']}")
print(f"성공: {results['success']}")
print(f"실패: {results['failed']}")
print(f"실패 티커: {results['error_tickers']}")
```

---

## 실행 결과 예시

```
티커 처리 진행: 100%|██████████████████| 50/50 [15:23<00:00, 18.47s/ticker]

[A084370] 완료 - 192개 레코드 저장
[A005930] 완료 - 200개 레코드 저장
[A000660] 데이터 부족 (최소 8개 필요, 현재 5개)
[A123456] 처리 중 오류 발생: Database connection failed

==================================================
전체 처리 완료!
총 티커 수: 50
성공: 48
실패: 2

실패한 티커 목록: A000660, A123456
상세 에러 로그: forecast_error_log.txt
```

---

## 데이터베이스 저장 형식

Long format으로 저장되며, 각 예측 모델별로 별도의 레코드가 생성됩니다.

**저장되는 컬럼:**
- `date`: 예측 날짜
- `ticker`: 종목 코드
- `indicator`: 예측 모델명 또는 지표명
  - sarima_forecast
  - lstm_forecast
  - prophet_forecast
  - exp_smoothing_forecast (신규)
  - theta_forecast (신규)
  - ensemble_forecast
- `value`: 예측값

---

## 성능 최적화

### 메모리 사용량 감소
- 각 티커 처리 후 메모리 정리
- 대용량 DataFrame 처리 후 즉시 삭제
- DB 연결 재사용 및 명시적 종료

### 처리 속도 향상
- 함수화로 인한 코드 재사용
- 불필요한 연산 제거
- 예측 모델별 독립 실행으로 에러 전파 방지

---

## 주의사항

1. **최소 데이터 요구사항**: 각 티커당 최소 8개 분기 데이터 필요
2. **외생변수**: hs_code가 None이면 외생변수 없이 예측 수행
3. **메모리**: 대량 티커 처리 시 충분한 메모리 확보 권장
4. **실행 시간**: 티커당 평균 15-20초 소요 (모델 학습 포함)

---

## 문제 해결

### 1. 모델 예측 실패
- 데이터 길이 확인 (최소 8개 필요)
- 데이터 품질 확인 (결측치, 이상치)
- 특정 모델만 실패하는 경우 해당 모델 함수 로그 확인

### 2. DB 저장 실패
- DB 연결 정보 확인
- 테이블 존재 여부 확인
- 권한 확인

### 3. 메모리 부족
- 한 번에 처리하는 티커 수 줄이기
- 배치 단위로 나누어 처리

---

## 향후 개선 사항

1. 병렬 처리 기능 추가 (multiprocessing)
2. 모델별 가중치 조정 기능
3. 실시간 알림 기능 (Slack, Email)
4. 대시보드 시각화
5. 자동 재시도 메커니즘
6. 성능 벤치마크 기록

---

## 라이선스 및 연락처

- 작성일: 2025-10-26
- 버전: 2.0
- 문의: 시스템 관리자
