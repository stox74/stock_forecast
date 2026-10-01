# 항공·관광 코드 비교 및 통합본 사용 안내

분석 기준: 2026-10-01 첨부한 네 노트북의 코드 및 저장된 실행 출력. 실제 서버 조회나 DB 접속은 수행하지 않았다.

## 1. 파일별 수집 범위와 선별

| 원본 | 수집 대상 | 기간·범위 | 저장 | 선별 |
|---|---|---|---|---|
| 2_Air_Tourism.ipynb | FDR WTI·브렌트·USD/KRW, BOK 여행비 지출전망 CSI, 인천공항공사 항공사별 월 여객·화물 | FDR 2010년 이후, BOK 2008-09~2025-12, 인천 2022년 이후. 인천 항공사 기본 KE/OZ/7C/AA | DataFrame. CSV 저장문은 주석 | 통합본 전환 후 별도 실행 불필요. 장애 진단용 보관 |
| 2_Air_Tourism_Air_Portal.ipynb | 항공정보포털 항공사별 공급석·운항편·여객·화물 및 합계행 | 월별 Excel 응답, 국내/국제·도착/출발 등 요청은 total. 출력에 2015-01~2025-12 | DataFrame. CSV·Excel 저장문은 주석 | 통합본 전환 후 별도 실행 불필요 |
| 2_Air_Tourism_KAC_Crawling.ipynb | 한국공항공사 항공사별 통계 JSON, 여객·화물·도착/출발 관련 필드 | 기본 2020-01~2025-12. 단월 테스트 요청도 별도 실행 | DataFrame. CSV 저장문 주석 | 단월 테스트 제거, 통합본으로 대체 |
| Air_Tourism_Collector_FIXED_FINAL(1).ipynb | 위 세 파일의 수집 기능 + Long 변환·분석·대시보드 | 예시 실행 AirPortal/KAC 2022-01~2025-12, 인천 2022년~현재월. FDR 포함, BOK 키 사용 | 원자료/분석 CSV 및 PNG | 기존 파일 중 주 실행본. 아래 정리본으로 전환 권장 |

기능 중복과 조회기간 중복은 다르다. 통합 예시에서는 KAC 2020~2021, AirPortal 2015~2021이 빠진다. 과거 데이터가 필요하면 --start를 앞당겨 해당 출처만 수집한다. 보관된 과거 자료는 삭제하지 않는다.

세 출처는 동일 지표·동일 모집단이라고 검증된 것이 아니다. 인천공항공사/한국공항공사/항공정보포털 통계를 전부 더하면 중복 집계할 수 있다. 총계·소계·항공사 상세행을 함께 합산하지 않는다.

## 2. 실제 관광객 출입국·공항별 통계와의 차이

- 현재 코드에는 국적별 외국인 입국자, 내국인 출국자, 관광 목적별 방문객을 직접 수집하는 관광통계 API가 없다.
- 여객 수는 항공 이용 통계이며 고유 인원·관광객 수와 같다고 볼 수 없다.
- KAC의 요청 경로는 airLineStatsList.do, AirPortal 요청은 getDetailedAirTransportStats1Excel.do로 항공사별 통계다. KAC AIRPORT_CHK 필터는 실제 요청에 없으며, 공항을 순회하는 로직도 없다.
- 응답에 공항 구분이 있을 수 있으나, 현재 코드만으로 공항별 시계열 확보를 보장하지 못한다. 공항별 분석에는 응답 스키마 및 별도 공항별 요청/필터 검증이 필요하다.

## 3. 원본 DB 저장 여부

네 원본 모두 DB 접속/INSERT/to_sql 코드가 없다. investar에 적재되는 테이블도 없다.

통합 노트북에서 실행되는 저장 파일:
- air_tourism_long_V3.csv
- air_tourism_analyzed_전체.csv
- air_tourism_analyzed_주요지표.csv
- air_tourism_analyzed_2020이후.csv
- air_tourism_dashboard.png

상대경로로 작업 폴더에 저장한다. 원자료 저장 메시지는 air_tourism_long.csv지만 실제 파일명은 V3 포함이다. Wide/Selected CSV는 저장문이 주석인데 성공 메시지만 출력한다.

## 4. 원본에서 확인한 문제

- simple_transform.py 외부 의존: 첨부되지 않아 지표 매핑/단위 변환 로직을 재현할 수 없다.
- 분석 단계가 특정 Windows 절대경로의 CSV를 다시 읽음: 직전 수집 데이터 대신 다른 파일을 읽을 수 있다.
- Long 중복 제거 키에 extra_json/추가 구분이 없음: 같은 날짜·항공사·지표의 서로 다른 구분이 사라질 수 있다.
- AirPortal의 SEQ 같은 순번을 수치 지표로 오인할 수 있음.
- 실행 출력에 중복 컬럼 경고가 있음. extras와 id_cols를 합칠 때 중복을 제거하지 않은 부분도 있음.
- 결측치를 0 또는 전방 채우기로 대체하고 출처별 값을 합산하는 분석 예시가 있음. 모집단/기간 범위 검증 전에 적용하면 오해를 낳을 수 있다.
- 공급석과 여객 합계의 범위가 일치하는지 확인하지 않은 탑승률은 해석에 주의가 필요하다.

## 5. 정리본 구성

air_tourism_unified.py는 기존 통합본의 API 경로/요청방식/파서를 재사용해 세 개별 노트북을 한 실행 흐름으로 정리했다. import 시 수집하지 않는다. 외부 simple_transform.py와 특정 사용자 절대경로를 제거했다.

- 출처별 raw CSV를 먼저 보관한다.
- 추가 구분정보를 extra_json에 보존하고 출처를 유지한다.
- 순번·코드 컬럼을 지표에서 제외한다. AirPortal/KAC 측정값은 원본과 유사한 수치 변환 탐색이므로 스키마가 바뀌면 raw CSV 점검이 필요하다.
- 정확히 동일한 행만 제거한다. 같은 관측키의 서로 다른 값은 분석/DB 단계에서 오류 처리한다.
- 월별 분석에서 출처/항공사/지표/구분별 시계열을 분리한다. FDR은 월말 마지막 가격, 항공 월별 실적은 원값을 사용한다.
- MoM/YoY는 달력 기준 1개월/12개월 비교, 누락월·0 분모는 NaN. 퍼센트 단위다.
- 출처별 자동 합산, 탑승률, 친숙한 한글 19개 컬럼 매핑은 만들지 않는다. 이 부분은 원본 simple_transform.py와 모집단·단위 확인 후 추가해야 한다.
- 기존 PNG를 완전히 재현하지 않으며, --png는 최대 9개 인천/거시계열의 간단한 차트를 만든다.
- collection_status.csv에 요청별 성공/실패를 기록한다. 실패하면 확보된 CSV는 남기지만 분석/DB 저장은 기본 중단. --allow-partial은 의도적으로 부분 자료를 사용할 때만 지정한다.
- 예외 기록은 API 키가 노출될 수 있는 URL 대신 예외 종류만 남긴다.

## 6. 설치 및 실행 (v2)

air_tourism_unified_v2.py를 investment_strategy 프로젝트 안에 두고 실행한다. DATA 폴더를 자동으로 찾아 DATA.KEYS(ODPD, BOK)와 DATA/config.py를 사용한다.

```powershell
python air_tourism_unified_v2.py                                # 지난달 odp/airportal/kac → investar
python air_tourism_unified_v2.py --start 201501 --end 202608    # 기간 백필
python air_tourism_unified_v2.py --start 202608 --end 202608 --no-db --save-files   # DB 없이 파일 점검
```

- 기본: DB 적재 켜짐, 파일 저장 꺼짐 (스크립트 상단 SAVE_FILES = False, SAVE_DB = True).
- 파일이 필요하면 SAVE_FILES = True 또는 --save-files. 저장 위치는 OUTPUT_DIR 또는 --output.
- --start를 생략하면 --end와 같은 달 하나만 수집한다.
- 아직 발표되지 않은 달은 empty로 기록되고 실패로 보지 않는다. 실제 오류(HTTP 오류 등)가 있으면 DB 저장을 건너뛰며, 의도적이면 --allow-partial.
- 인천공항 수집 항공사는 상단 ICN_AIRLINES에서 바꾼다.

## 7. DB 적재 (investar, 표준 규격 STD-1)

| 테이블 | 내용 | 키 |
|---|---|---|
| kr_airline_traffic_info | 시리즈 설명표: 출처, 지표, 항공사, 구분정보(dims_json), 단위 | series_id |
| kr_airline_traffic | 월별 값 최신본 | series_id, period_end |
| kr_airline_traffic_vintage | 값이 처음 들어오거나 바뀐 이력 | id |

- series_id = 출처약어(icn/apt/kac) + 출처·지표·항공사·구분정보 해시 16자리. 같은 구분의 시계열은 항상 같은 series_id.
- 합계·소계 행은 entity_type = 'total'로 구분한다. 항공사 상세행과 더하지 않는다.
- 단위는 인천공항(여객 persons, 화물 tons)만 기록. AirPortal/KAC는 출처 원 단위이며 확인 후 채운다.
- bok/fdr은 참고지표라 DB에 저장하지 않는다 (유가·환율은 FRED와 korea_fx 테이블에 이미 있음).
- v1의 air_tourism_indicators 테이블은 사용하지 않는다.

## 8. 검증과 한계

문법 컴파일 및 합성 데이터 오프라인 검증: KAC 중첩 JSON 평탄화, 항공사 식별, 공항/구분 보존, 코드/순번 제외, 중복 제거, 누락월 MoM, 충돌 방지, PNG 생성 통과. HTTP 의존성은 테스트에서 대체했다.

실제 API 수집/서버 스키마 및 실제 MySQL 저장은 검증하지 못했다. 현재 실행 환경에 requests/SQLAlchemy가 없어 네트워크 요청과 DB 적재 통합 검증은 수행하지 않았다. 사용자 환경에서 의존성 설치 후 한 달 수집 → raw/상태/지표 확인 → 전체 기간 실행을 권장한다.
