# fdriver 2g 참고 예측 방법 v1 (method_2g_ref_v1)

- 작성: 2026-10-20. 2026Q3 실적 발표 기간 전, 참고 예측의 어떤 결과도 계산하기 전에 쓴다.
- 이 문서는 git 커밋으로 시점을 남긴다. 고치면 '변경 기록'에 날짜와 이유를 적는다.
- **참고 열만 더한다.**
  - 2g 주 예측 E5 (method_2g_v1, 커밋 5dc9fdb) 와 2d (사전 등록 v2) 는 바꾸지 않는다.
  - 판정 가설은 없다.

## 1. 드라이버 자동 선정 (fd_driver_ref_2g, 승인 아님)

- **대상**: 2g 대상(fd_target_2g) 중 M1 이 없는 기업 전부.
  - M1 이 있는 기업 = 기존 레이더 39곳 중 레이더 드라이버 모형 값이 나오는 기업. 이 기업들은 빼고, 시범 대조군 2곳은 넣는다.
- **후보**: 2d K2·K2b 와 같은 방식 (`eval2g/k8_drivers.py` 와 같은 코드)
  - DART 주요 제품 표 키워드(K2b 보강 사전) → 없으면 kr_company_hs_map → 없으면 KSIC 표
  - 내수 후보(KSIC 대응표), K2b 수동 후보·제외
  - **반도체 장비·부품 기업은 848620, 848630, 848640, 848690 을 반드시 후보에 넣는다** (source semi8486).
    - 반도체 장비·부품 기업 = KSIC 앞자리 292·2612, 또는 DART 주요 제품 행에 '반도체' 와 '장비·부품·부분품·소모품' 이 함께 있는 기업
  - K8 6번 결과(정상·레이더 378곳)는 같은 코드·같은 시드라 다시 계산해도 같다. 새 집단 786곳은 DART 주요 제품 표부터 새로 받는다.
- **상관**
  - 선정 구간 2019Q1~2023Q4, 분기 매출 YoY (fd_rev_adj_2g) 와 후보 YoY
  - corr_full (12분기 이상), corr_excovid (2020~2021 제외, 8분기 이상)
- **위약**: 무관 수출 코드를 수출 후보 수만큼 무작위로 200회, 기업별 시드 [0, 종목코드]. 위약 대비 초과 = 최고 corr_full - 위약 최고상관 평균.
- **기업별 최고 후보 1개** = corr_full 최대인 후보
- **등급**
  - A: corr_full >= 0.3, corr_excovid >= 0.3, 위약 대비 초과 >= 0.3
  - B: corr_full >= 0.3, corr_excovid >= 0.3, 0.2 <= 위약 대비 초과 < 0.3
  - 그 외: 드라이버 없음 (none)
- **drv_ksic_only = 1**: 최고 후보의 근거가 업종코드뿐인 경우 (경제적 타당성 미확인)
  - 출처가 ksic 인 경우
  - 출처가 semi8486 이고 반도체 판단 근거가 KSIC 뿐인 경우 (제품 표 근거 없음)
  - 내수(domestic) 후보도 KSIC 대응표로 붙지만, 사람이 만든 업종-지표 대응표라 0 으로 둔다 (drv_basis 열에 'KSIC 대응 내수' 로 표시).
- **저장**: fd_driver_ref_2g (entity_id, code, source, grade, corr_full, corr_excovid, excess_over_placebo, ksic_only, run_id)

## 2. 참고 예측 (A·B 등급 기업, 기준일 t, 분기 Q = t 의 마지막 끝난 분기, Q+1)

- **M1_ref**: 2d M1 과 같은 방식
  - 드라이버 1개 OLS, 확장창, 최소 12분기
  - 예측 분기에 t 까지 공표된 달만 합한 로그 YoY, 학습 분기도 같은 달 구성
  - Q+1 은 공표된 달이 없으면 결측이다.
- **SARIMAX_ref**
  - 로그 매출, 계절 4, 외생 변수 x = 같은 분기 드라이버 수준의 로그
    - 월간: 3개월 합
    - 일별: 분기 평균
  - 차수: SARIMA 후보 8개 (k7_methods.CANDIDATES) 중 AICc 최소
  - 학습 구간: radar.ts.training_series (마지막 연속·양수 구간) 중 x 가 있는 분기, 최소 12분기
- **미래 드라이버 값** (x 의 예측 분기 값), 열 `x_src_Q`, `x_src_Q1` 에 출처를 남긴다.
  - Q: t 까지 공표된 달 + 남은 달은 2d E_fill 방식 (월 로그 계열 ETS 감쇠 추세 + 계절 12, 36개월 미만이면 전년 같은 달 x 최근 3개월 평균 YoY)
    - 출처: 'known' (3개월 모두 공표) / 'known+fill'
  - Q+1
    - 수출 드라이버: 저장된 전국 수출 예측 korea_monthly_trade_forecast_v2 를 쓴다.
      - 그 코드의 forecast_date <= t 중 가장 늦은 것, indicator ensemble_expDlr
      - Q+1 의 세 달이 모두 있어야 한다 (출처 'stored_fc').
      - Q 의 아직 공표 안 된 달도 같은 예측값이 있으면 쓴다.
    - 저장된 예측이 없으면 E_fill 방식 (출처 'fill')
    - 내수 월간 드라이버: E_fill 방식
    - 일별 드라이버(FRED, KOMIS): 채우지 않고 t 까지 마지막 30일 평균을 Q+1 값으로 둔다 (출처 'last30d')
- **참고 조합** (로그 성장률 평균 = 기하평균)
  - E5_ref = SARIMA·ETS·Theta·B2·M1_ref. M1_ref 가 없으면 결측.
  - E6_ref = SARIMA·ETS·Theta·B2·(M1_ref 있으면)·SARIMAX_ref. SARIMAX_ref 가 없으면 결측.
  - gap_E6_ref_vs_B2 = ln(E6_ref) - ln(B2) (참고)

## 3. 기록

- fd_live_2g 에 같은 기준일의 새 스냅샷(run_id)으로 누적한다. 기존 행은 바꾸지 않는다.
- 참고 열
  - drv_code_ref, drv_source_ref, drv_grade, drv_ksic_only
  - g_M1_ref, M1_ref, g_SARIMAX_ref, SARIMAX_ref, sarimax_order_ref, sarimax_n_train
  - x_src (그 분기 드라이버 값 출처)
  - g_E5_ref, E5_ref, g_E6_ref, E6_ref
- 주간 스냅샷에도 포함한다. 소요 시간이 실행 제한(3시간)을 넘으면 늘리고 보고한다.
- 공개 출력(레이더)에는 HS 코드와 드라이버 이름을 넣지 않는다 (drv_code_ref 는 출력하지 않음).

## 4. 과거 구간 탐색 (판정 아님)

- 2024Q1~2026Q2, 기준일 pre5, A·B 등급 기업
- 비교: SARIMA, M1_ref, SARIMAX_ref, E5, E5_ref, E6_ref, B2, 컨센서스
- **과거 구간 SARIMAX_ref 는 같은 분기 SARIMA 가 고른 차수를 그대로 쓴다** (계산량 절감). 실전 기록은 AICc 로 다시 고른다.
- 이 문서 커밋 뒤에만 계산한다.

## 5. 변경 기록

- (없음)
