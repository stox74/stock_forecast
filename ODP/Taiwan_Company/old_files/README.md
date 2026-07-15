# 대만 대표기업 월별 매출 수집·예측·시각화

대만 상장사가 매월 공시하는 월별 매출(MOPS)을 수집해 SQLite에 저장하고, SARIMA로 예측한 뒤 시계열 차트로 시각화하는 파이프라인입니다.

## 구조

```
config.py      # 대상 기업, URL, SARIMA 파라미터 등 설정
db.py          # SQLite 스키마(revenue, forecast)와 UPSERT 헬퍼
collector.py   # MOPS 정적 페이지(과거) + TWSE OpenAPI(최신) 수집
forecaster.py  # 로그변환 SARIMA(1,1,1)(1,1,1,12) 예측 → 예측 이력 저장
visualizer.py  # 실적+최신예측 차트, 과거 예측 이력 비교 차트
main.py        # CLI
```

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

```bash
# 1) 과거 이력 일괄 수집 (예: 2019년 1월 ~ 지난달)
python main.py collect --start 2019-01

# 2) 예측 (기본 6개월) — 예측 이력이 forecast 테이블에 누적 저장됨
python main.py forecast --horizon 6

# 3) 시각화 → output/ 폴더에 PNG 생성
python main.py plot

# 매월 정기 실행 (증분 수집 → 예측 → 시각화)
python main.py run
```

## 매월 자동 실행

대만 상장사는 매월 10일까지 전월 매출을 공시하므로, 매월 11~12일경 실행을 권장합니다.

crontab 예시 (매월 12일 오전 9시):
```
0 9 12 * * cd /path/to/tw-revenue && python main.py run >> run.log 2>&1
```

## 데이터/스키마 메모

- 매출 단위: NTD 천 (MOPS 원본 단위). 차트에서는 십억(billion)으로 환산 표시
- MOPS URL의 연도는 민국 기원(서기 − 1911), 페이지 인코딩은 Big5
- `forecast` 테이블은 (기업, basis 연월, target 연월)이 기본키라 매월 예측을 다시 돌려도
  과거 예측이 덮어써지지 않고 이력으로 남습니다 → `*_history.png`에서 실적과 비교
- 대상 기업 추가는 `config.py`의 `COMPANIES`에 종목코드만 넣으면 됩니다

## 주의

- MOPS 요청 사이에 2초 대기(`REQUEST_DELAY_SEC`)를 둡니다. 과도한 요청은 차단될 수 있습니다.
- MOPS 사이트 개편으로 구/신 도메인(mops / mopsov)을 순서대로 시도합니다.
  둘 다 실패하면 `config.py`의 `MOPS_URL_TEMPLATES`를 최신 URL로 갱신하세요.
