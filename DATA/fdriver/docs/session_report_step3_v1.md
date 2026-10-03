# fdriver 세션 리포트: 작업 순서 3번 v1

작성일: 2026-10-03 / 범위: 2번 리뷰 반영(정규식 버그, market_daily) + entity.py + adapters 4개 + 7절 최소 테스트 + 시범 6개 기업 조회 점검

## 0. 요약

- **정규식 버그 수정**: `ON UPDATE CURRENT_TIMESTAMP`, `ON DUPLICATE KEY UPDATE`, `ON DELETE CASCADE` 같은 구문을 더 이상 쓰기 명령으로 잡지 않습니다. 고치는 과정에서 `CREATE TABLE IF NOT EXISTS`의 `IF`를 테이블 이름으로 잡던 두 번째 버그도 찾아 함께 고쳤습니다.
- **market_daily**: 기간말 + 2영업일(C), 실시간 수집분 A, 백필 기준 규칙일 + 30일로 바꿨습니다. 영업일은 이제 KSE_Price 거래일 달력으로 셉니다.
- **entity**: `fd_entity_master` 2,954행(한국 2,952, 대만 2), `fd_entity_link` 859행을 만들어 넣었습니다. **DB 쓰기는 이 두 테이블뿐입니다**(5절).
- **adapters 4개**: 시범 6개 기업 전부 조회됩니다. DART와 DG 매출을 겹치는 분기에서 비교한 결과 최대 차이가 0.000003%라 1% 경고는 0건입니다.
- **테스트 51개 전부 통과**: 2번의 29개 + 22개 추가. 이 가운데 7개는 실제 DB에서 돌았고, 건너뛴 테스트는 없습니다.
- **시범 데이터 as_of 검사**: 6개 기업 전체 61,000여 행에서 무작위 날짜 10개를 뽑아 봤고, 미래 값 누출은 0건입니다.

**확인 필요**: 지시문의 "명세서 9절(v2 변경 사항)"을 받지 못했고, 로컬에서도 v2 명세서를 찾지 못했습니다. 그래서 지금까지 승인된 결정(D1~D7, market_daily, asof_note, 수작업 HS 연결)을 9절로 보고 구현했습니다. 9절에 다른 내용이 있으면 알려 주세요. `fd_` 테이블 두 개는 다시 만들 수 있습니다.

## 1. 2번 리뷰 반영

### 1.1 정규식 버그 (db.py)

| 문제 | 원인 | 수정 |
| --- | --- | --- |
| STD-1 DDL 거부 | `UPDATE` 단어가 `ON UPDATE CURRENT_TIMESTAMP` 안에서도 걸림 → 대상 `CURRENT_TIMESTAMP` | 대상 추출 전에 `ON DUPLICATE KEY UPDATE`, `ON UPDATE`, `ON DELETE` 구문을 지움 (`_NON_COMMAND_CLAUSES`) |
| upsert 문장에서 컬럼 이름이 대상으로 잡힘 | `ON DUPLICATE KEY UPDATE \`b\`` → 대상 `b` | 위와 같음 |
| (추가 발견) `CREATE TABLE IF NOT EXISTS \`{변수}\`` 의 `IF`가 대상으로 잡힘 | 선택 구문 `IF NOT EXISTS`를 건너뛰고 `IF`를 이름으로 인식 | 대상 이름 앞에 `(?!IF\b)` 조건 추가 |

추가한 테스트(5개, 모두 통과):
- STD-1 형식 DDL(`updated_at ... ON UPDATE CURRENT_TIMESTAMP`) 통과
- `ON DELETE CASCADE ON UPDATE CASCADE`가 있는 fd_ DDL 통과
- upsert 문장의 대상은 fd_ 테이블 하나만 잡힘
- 진짜 `UPDATE KSE_Price`, `DELETE FROM ...`은 여전히 잡힘. fd_가 아닌 CREATE와 다중 문장 우회도 거부
- entity.py의 실제 DDL 두 개 통과

기존 쓰기 거부 테스트 19종도 그대로 통과합니다.

### 1.2 market_daily 와 거래일 달력

- `RELEASE_RULES["market_daily"]`: `{"bdays": 2}`, rule_grade C, observed `first_collected_at` → A, backfill 규칙일 + 30일
- `asof.set_trading_calendar()`를 추가했습니다. `read_price.trading_calendar()`가 KSE_Price에서 종목이 100개 이상인 날만 거래일로 뽑아 등록합니다(2013-05-21 ~ 2026-09-01, 3,260일). 달력 범위 밖 날짜는 주말만 빼는 기존 방식으로 셉니다.
- 테스트: 추석 연휴(목·금) 가정 달력에서 수요일 + 2영업일 = 다음 주 화요일, 범위 밖은 BDay 사용

## 2. entity.py: 기업 기준표

### 2.1 fd_entity_master (2,954행)

| 국가 | 시장 | 상장 상태 | 행 수 |
| --- | --- | --- | --- |
| KR | KOSPI | listed / delisted | 779 / 6 |
| KR | KOSDAQ | listed / delisted | 783 / 2 |
| KR | (미상) | listed / delisted | 1,139 / 243 |
| TW | TWSE | listed | 2 (TSMC, Nanya) |

- 대상: korea_dart_corp_master 가운데 KSE_Price 또는 DG에 나오는 종목. stock_code가 `000000`(비상장 표시)인 행은 제외했습니다.
- 상장 상태: KSE_Price 마지막 거래일이 전체 최신일(2026-09-01)에서 14일 이내면 listed
- 결산월: acc_mt가 있으면 그 값, 없으면 12. 적재된 기업 중 12월이 아닌 기업은 0개입니다.
- 시범 6개 기업: 모두 KOSPI, listed, 결산월 12, is_pilot=1
- **한계: 시장 구분 미상이 1,382개입니다.** DG(1,585개 기업)에 없고 corp_master의 corp_cls도 비어 있는 종목입니다. 시가총액 테이블에도 시장 컬럼이 없습니다. 2단계 전 종목 확장 전에 시장 구분 원천이 필요합니다.

### 2.2 fd_entity_link (859행)

| link_type | 출처 | 행 수 | 기업 수 |
| --- | --- | --- | --- |
| hs_code | kr_company_hs_map | 786 | 512 |
| hs_code | manual (854231 삼성전자, 330420 한국화장품제조) | 2 | 2 |
| tw_peer | manual (TW:2330 TSMC, TW:2408 Nanya → 삼성전자) | 2 | 1 |
| kr_peer | manual (000660 SK하이닉스 → 삼성전자) | 1 | 1 |
| fred_series | manual (HOUST·PERMIT → 현대건설기계, RSHPCS → 에이피알) | 3 | 2 |
| kosis_series | manual (온라인쇼핑 화장품, 소매판매지수 화장품 → 에이피알) | 2 | 1 |
| tourism_series | manual (D:100 출국, E:TOTAL 방한 합계 → 하나투어) | 2 | 1 |
| airline_series | manual (국적사 9곳 여객 → 하나투어) | 9 | 1 |
| alt_data | manual (아마존 BSR 50개 ASIN, 구글 트렌드 2개 → 에이피알) | 52 | 1 |

명세서와 다른 점 (확장):
1. **`series_key` 컬럼 추가**: adapter에 넘길 키입니다. hs_code는 link_key가 원래 코드(예: `8542321010`)이고 series_key가 `hs6`(`854232`)입니다. 이 값은 kr_company_hs_map의 `hs6`를 그대로 가져왔고, 직접 보정하지 않았습니다(D1).
2. **link_type 추가**: `kr_peer`(D7) 외에 `tourism_series`, `airline_series`. 대체 데이터는 `alt_data`에 `amazon_bsr:US:<ASIN>`, `google_trends:US:<키워드>` 형식으로 넣었고, `entity.link_family()`가 adapter family를 알려 줍니다.
3. 외래키: link.entity_id → master.entity_id (`ON DELETE CASCADE ON UPDATE CASCADE`)

**연결에서 빠진 행: 24행, 21개 종목.** master에 없어서 넣지 않았습니다. 사용자의 HS 맵 노트북에서 확인이 필요합니다.

| 원인 | 종목 |
| --- | --- |
| **종목코드가 잘못 들어감** | 금호석유 `011781`~`011785` 5개 (실제 011780. 011785는 우선주 코드라 KSE_Price에 2023~2024 일부만 있고 DART master에는 없음) |
| 상장폐지·합병으로 KSE_Price에 없음 | 롯데푸드 002270, 한국제지 002300, 아트라스BX 023890, SK머티리얼즈 036490, 사조해표 079660, 코오롱머티리얼 144620 등 15개 |
| DART master에 없음 | 220630 |

## 3. adapters

모든 함수는 `COMMON_COLUMNS` 11개 컬럼(key, period_end, freq, item, value, unit, currency, available_at, asof_quality, asof_note, source)을 같은 순서·형식으로 돌려주고, available_at은 `asof.stamp`/`asof.resolve`로 붙입니다. 코드 인자는 문자열만 받으며, 숫자를 넘기면 TypeError가 납니다(앞자리 0 보호).

| 함수 | 구현 요점 |
| --- | --- |
| `read_revenue(ids, start, end, basis, return_meta)` | 원천 우선순위 V3 → V2 → DG(D2). 분기 단독값: Q1·Q2·Q3는 보고서의 3개월 값, Q4 = 연간 - (Q1+Q2+Q3). V3는 누적값과 대조해 "3개월 칸에 누적이 적힌" 보고서를 바로잡음. DG는 x1000, 분기말 정규화(영업이익 항목의 분기말이 아닌 날짜는 제외). 결산월이 12월이 아닌 기업은 DART를 건너뛰고 DG만 쓰며 경고. available_at: Q1~Q3 + 45일, Q4 + 90일, 모두 C. `return_meta=True`면 DART-DG 비교표와 경고 목록 반환(D4) |
| `read_trade(hs_codes, items, start, end)` | 코드를 바인딩해 조회하고 JOIN은 하지 않음(utf16/utf8mb4 충돌). 5·9자리 코드는 경고 후 제외. 단위: 금액 USD, 중량 kg |
| `read_price(ids, start, end, field)` + `trading_calendar()` | KSE_Price 수정주가. available_at = 거래일(A) |
| `read_series(family, keys, start, end, vintage, items)` | family 설정표 `FAMILIES` 10개: fred, kosis, komis, airline, tourism, tw_revenue, credit_balance, market_funds, amazon_bsr, google_trends. 키 형식은 키 컬럼을 `:`로 이음(`D:100`, `US:medicube`). tourism은 `E:TOTAL`/`D:TOTAL`(합계 제외 표시가 없는 국가 합계) 지원. FRED는 공표일이 없으면 최초 수집 시각 규칙으로 대체. vintage=True는 첫 버전에 일반 규칙(백필 판정 포함), 이후 개정은 관측일(A), `asof_note=revision`. 아마존 BSR의 보간 행은 `asof_note=ffilled` |

보조: `asof.latest_known(df)`는 vintage 표에서 시점별 최신 값만 남깁니다. FRED RSHPCS로 2024-03-20 시점 값을 재현해 확인했습니다. 1월분은 3/14 개정치, 2월분은 첫 공표치가 나옵니다.

## 4. 테스트 (51개 통과)

```bash
python -m unittest discover -s DATA/fdriver/tests -t . -v
```

| 파일 | 수 | 내용 |
| --- | --- | --- |
| test_asof.py | 21 | 2번의 19개 + market_daily 규칙 + 거래일 달력 |
| test_db.py | 15 | 2번의 10개 + 정규식 버그 5개 |
| test_adapters.py | 15 | 아래 |

명세서 7절 최소 테스트:
- **분기 단독값 합계 = 연간**: 합성 데이터 5개(3개월 값, 누적이 적힌 경우, V2 형식, 3분기 누적으로 Q4 계산, 정보 부족 시 Q4 없음) + 실제 DB(삼성전자 2024년 V2, 2025년 V3에서 4개 분기 합 / 사업보고서 연간 = 1.000000000)
- **read_trade 030341**: 문자열 키 `030341` 그대로 반환, 2007-01 이전부터 200행 이상. 숫자 30341은 TypeError, 5·9자리는 제외
- **공통 컬럼 일치**: adapter·family 12종(매출, 수출입, 주가, fred, kosis, tourism, airline, tw_revenue, credit_balance, amazon_bsr, google_trends, fred vintage)이 컬럼 이름·순서·형식이 모두 같음. available_at이 비거나 기간말보다 이른 행 없음

그 외: 매출 발표일 규칙(2025-03-31 → 05-15, 2025-12-31 → 2026-03-31), DG 천원 → 원 변환과 분기말 정규화, HS 링크가 맵 값을 그대로 쓰고 unresolved를 제외하는지, 시범 수작업 링크 형식

## 5. DB 쓰기 확인

| 확인 방법 | 결과 |
| --- | --- |
| 실행 기록 (`db.WRITE_LOG`) | DDL 2건(fd_entity_master, fd_entity_link), upsert 2건(2,954행, 859행). 그 외 없음 |
| 쓰기 경로 | `db.write_fd`, `db.execute_fd_ddl`만 쓰기를 하고, 둘 다 `fd_[a-z0-9_]+`가 아니면 DB에 보내기 전에 거부 |
| 읽기 | 읽기 엔진은 접속마다 READ ONLY 세션(`tx_read_only = 1` 테스트로 확인). adapter와 entity의 조회는 모두 이 엔진 사용 |
| 코드 검사 테스트 | db.py를 뺀 fdriver 코드에 fd_ 이외 테이블 쓰기 문장, `.to_sql(`, `create_engine(`, `.begin()` 없음 |
| 실행 전후 information_schema 비교 | 새 테이블은 fd_entity_master, fd_entity_link 둘뿐이고, 기존 테이블의 UPDATE_TIME 변화 없음. **단, MariaDB가 UPDATE_TIME을 기록하는 테이블이 132개 중 6개뿐이라 보조 증거입니다** |

결론: 이번 세션의 DB 쓰기는 fd_entity_master, fd_entity_link 두 테이블에만 있었습니다. 원본 테이블 쓰기는 0건입니다.

## 6. 시범 6개 기업 조회 점검

### 6.1 adapter별 행 수, 기간, 등급 비율

| 기업 | adapter | 상세 | 키 | 행 | 시작 | 끝 | A | B | C |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 삼성전자 | read_revenue | V2 36 + DG 25 + V3 6 | 1 | 67 | 2009-12 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | close | 1 | 3,260 | 2013-05-21 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_trade | hs6 13개 (851712는 데이터 없음) | 12 | 4,287 | 2007-01 | 2026-08 | 0 | 0 | 1.00 |
| | read_revenue (kr_peer) | SK하이닉스 | 1 | 67 | 2009-12 | 2026-06 | 0 | 0 | 1.00 |
| | read_series: tw_revenue | TSMC, Nanya | 2 | 184 | 2019-01 | 2026-08 | 0.03 | 0.97 | 0 |
| 동원산업 | read_revenue | V2 36 + DG 25 + V3 6 | 1 | 67 | 2009-12 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | | 1 | 3,260 | 2013-05-21 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_trade | 030341~030344 | 4 | 1,984 | **2006-01** | 2026-08 | 0 | 0 | 1.00 |
| 현대건설기계 | read_revenue | V2 30 + V3 6 + DG 1 | 1 | 37 | 2017-06 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | | 1 | 2,284 | 2017-05-10 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_trade | 842720, 842951, 842952, 843149 | 4 | 1,888 | 2007-01 | 2026-08 | 0 | 0 | 1.00 |
| | read_series: fred | HOUST, PERMIT | 2 | 640 | 2000-01 | 2026-08 | 0.99 | 0 | 0.01 |
| 에이피알 | read_revenue | V2 20 + DG 8 + V3 6 | 1 | 34 | 2018-03 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | | 1 | 611 | 2024-02-27 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_series: amazon_bsr | ASIN 50개 | 50 | 32,060 | 2017-08-02 | 2026-08-03 | 0.05 | 0 | 0.95 |
| | read_series: fred | RSHPCS | 1 | 320 | 2000-01 | 2026-08 | 0.51 | 0 | 0.49 |
| | read_series: google_trends | medicube, korean skincare | 2 | 524 | 2021-08-01 | 2026-08-02 | 0 | 0 | 1.00 |
| | read_series: kosis | 온라인쇼핑·소매판매 화장품 | 2 | 316 | 2010-01 | 2026-08 | 0.01 | 0 | 0.99 |
| 한국화장품제조 | read_revenue | V2 36 + DG 25 + V3 6 | 1 | 67 | 2009-12 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | | 1 | 3,260 | 2013-05-21 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_trade | 330420, 330499 | 2 | 944 | 2007-01 | 2026-08 | 0 | 0 | 1.00 |
| 하나투어 | read_revenue | V2 35 + DG 26 + V3 6 | 1 | 67 | 2009-12 | 2026-06 | 0 | 0 | 1.00 |
| | read_price | | 1 | 3,260 | 2013-05-21 | 2026-09-01 | 1.00 | 0 | 0 |
| | read_series: airline | 국적사 9곳 여객 | 9 | 1,089 | 2015-01 | 2026-08 | 0.02 | 0 | 0.98 |
| | read_series: tourism | D:100, E:TOTAL | 2 | 360 | 2011-09 | 2026-08 | 0.01 | 0 | 0.99 |

(수출입 행 수는 expDlr와 expWgt 두 항목을 합친 것)

기업별 전체 등급 비율 (모든 adapter 합계, 일별 주가 행이 많아 A가 높게 나옴):

| 기업 | A | B | C | 행 |
| --- | --- | --- | --- | --- |
| 삼성전자 | 0.415 | 0.023 | 0.562 | 7,865 |
| 동원산업 | 0.614 | 0 | 0.386 | 5,311 |
| 현대건설기계 | 0.602 | 0 | 0.398 | 4,849 |
| 에이피알 | 0.070 | 0 | 0.930 | 33,865 |
| 한국화장품제조 | 0.763 | 0 | 0.237 | 4,271 |
| 하나투어 | 0.687 | 0 | 0.313 | 4,776 |

### 6.2 DART와 DG 매출 비교 (D4)

| 기업 | 겹치는 분기 | 기간 | 최대 차이 | 1% 초과 경고 |
| --- | --- | --- | --- | --- |
| 삼성전자 | 42 | 2016 Q1 ~ 2026 Q2 | 0 | 0 |
| 동원산업 | 42 | 2016 Q1 ~ 2026 Q2 | 0.0000001% | 0 |
| 현대건설기계 | 36 | 2017 Q2 ~ 2026 Q2 | 0.00000002% | 0 |
| 에이피알 | 26 | 2020 Q1 ~ 2026 Q2 | 0.0000009% | 0 |
| 한국화장품제조 | 42 | 2016 Q1 ~ 2026 Q2 | 0.000003% | 0 |
| 하나투어 | 41 | 2016 Q1 ~ 2026 Q2 | 0.000002% | 0 |

두 원천이 사실상 같은 값입니다. DG가 DART 공시를 그대로 가공한 것으로 보이고, 분기 단독값 변환 규칙도 맞다는 뜻입니다. 남은 차이는 천원 단위 반올림 수준입니다. DART V2는 2015년 보고서가 있지만 분기가 모두 갖춰진 2016년부터 단독값이 나오고, 그 이전은 DG로 채웁니다.

### 6.3 읽을 때 주의할 점

1. **최근 30일 안팎의 STD-1 값은 A로 표시됩니다.** 일괄 적재일(2026-10-01, 03)이 규칙일 + 30일 안에 들어오면 실제 관측일로 보기 때문입니다. available_at은 실제 발표보다 늦은 쪽(적재일)으로 잡혀 미래 정보 문제는 없습니다. 다만 "실제 발표일"이라는 뜻의 A와는 다릅니다. 정기 수집이 시작되면 자연히 맞아집니다. 아마존 BSR(A 5%), 신용잔고 등도 같은 이유입니다.
2. **KSE_Price는 2026-09-01에서 멈춰 있고, 상장폐지 기업 일부가 빠져 있습니다**(롯데푸드 등). 2단계 백테스트에서는 생존 편향의 원인이 됩니다.
3. 대만 월매출의 A(3%)는 2026-07월분 이후 실시간 수집분입니다.
4. FRED HOUST·PERMIT는 2000년부터 공표일이 정상이라 99%가 A입니다. RSHPCS는 2012년 이전 공표일이 이력 시작일로 찍혀 있어, 60일 기준으로 C 처리되었습니다(49%).
5. 하나투어 항공 시계열은 항공사별 9개입니다. 합산은 4번(panel)에서 합니다. AIRPORTAL 여객에 국내선이 포함되는지는 확인하지 못했습니다.

## 7. 다음 작업 (작업 순서 4번)

1. `panel.py`: `to_fiscal_quarter`(합계/평균 기본값을 item별로), `panel(entity_id, start, end)`. entity_links로 연결 시계열을 찾아 분기 한 표로 묶음. 항공 9개는 합계로
2. `pilots/pilot_v1.ipynb`: 6개 기업 panel, 등급 비율, as_of 자동 테스트(실제 panel 대상), 매출 YoY와 드라이버 YoY 상관(동행, 1·2분기 선행) 표·그림
3. 위약 검정(무관 품목 + 시간 추세)을 상관 표에 함께 넣을지 결정 필요 (기존 분석 원칙)

## 8. 결정 요청

1. 명세서 9절 내용 확인 (위 0절)
2. 금호석유 `011781~011785` 등 HS 맵 종목코드 오류 수정 (사용자 노트북)
3. 시장 구분 미상 1,382개: 1단계에서는 그대로 두고 2단계에서 원천을 정할지

## 부록: 파일

```
DATA/fdriver/
  config.py, db.py, asof.py, entity.py
  adapters/ __init__.py, _common.py, revenue.py, trade.py, price.py, series.py
  tests/   test_asof.py, test_db.py, test_adapters.py, conftest.py
  docs/    session_report_step2_v1.md, session_report_step3_v1.md
OneDrive/INVESTMENT/fdriver/202610/
  step3_load_entity.py, step3_pilot_summary.py, step3_check_drop.py   (이번 점검 스크립트)
  step3_tables_before.csv, step3_tables_after.csv                    (쓰기 전후 테이블 목록)
  step3_pilot_adapter_summary.csv, step3_pilot_grade_ratio.csv, step3_revenue_dart_vs_dg.csv
```
