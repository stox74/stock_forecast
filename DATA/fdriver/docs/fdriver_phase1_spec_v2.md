# fdriver 1단계 명세서 v2 - 분석 기반 구축

Oct 3, 2026 · @호영님으로 불러줘

## 1. 개요

1단계의 목표는 흩어진 원천 데이터를 **기업 기준표 하나와 표준 읽기 함수 몇 개**로 묶어, 어떤 기업이든 "매출과 후보 드라이버를 같은 시간축에, 그 시점에 알 수 있었던 값으로" 불러올 수 있게 만드는 것입니다. 드라이버를 실제로 찾고 검증하는 일은 2단계에서 합니다.

**산출물**

| 구분 | 이름 | 한 줄 설명 |
| --- | --- | --- |
| A | 기업 기준표 `fd_entity_master`, `fd_entity_link` | 종목코드 기준으로 기업명, 시장, DART 코드, HS 코드, 해외 비교기업을 한곳에 연결 |
| B | 표준 읽기 함수 (fdriver.adapters) | 매출, 수출입, 주가, STD-1 시계열을 같은 모양(period\_end, value, available\_at)으로 반환 |
| C | 시범 분석 6개 기업 | A와 B가 실제 분석 흐름에서 작동하는지 확인 |

**원칙**

1. **원본 무수정**: investar의 기존 테이블에는 쓰지 않습니다. 1단계가 만드는 테이블은 `fd_` 접두어를 붙여 분석층임을 구분합니다.
2. **STD-1 준수**: 새 테이블과 함수 반환값은 STD-1 규격(종목코드 문자열, period\_end, freq, unit/currency, 수집 시각)을 따릅니다.
3. **시점 인식**: 모든 값에 `available_at`(그 값을 알 수 있게 된 날)과 `asof_quality`(그 날짜의 신뢰도)를 붙입니다. 정확한 발표일을 모르면 합리적 규칙으로 추정하고, 추정이라는 사실을 표시합니다 (5절).
4. **작게 시작**: 시범 6개 기업으로 흐름을 먼저 완성하고, 전 종목 확장은 2단계에서 합니다.

## 2. 입력 데이터

1단계는 0단계에서 확정한 아래 테이블만 읽습니다 (2026-10-01 schema\_finder 기준). 기존 테이블은 형태가 제각각이라 읽기 함수가 변환을 맡고, STD-1 테이블은 공용 함수 하나로 읽습니다.

| 용도 | 테이블 | 형태 | 비고 |
| --- | --- | --- | --- |
| 기업 식별 | korea\_dart\_corp\_master | 기존 | corp\_code ↔ stock\_code |
| 기업-HS 매핑 | kr\_company\_hs\_map | STD-1 | 앞자리 0 보정 완료. 원본 korea\_company\_hscode\_map은 사용 안 함 |
| 매출 (1순위) | korea\_fs\_data\_from\_DART\_V3 | 기존 | 공시일 관련 컬럼 존재 여부 확인 필요 (5절) |
| 매출 (2순위) | korea\_fs\_data\_from\_DG | 기존 | DART에 없는 기업·기간 보완 |
| 수출입 | korea\_monthly\_trade\_data | 기존, value FLOAT | 5자리·9자리 예전 코드 행은 무시 |
| 주가 | KSE\_Price | 기존, 약 650만 행 | 종목코드 컬럼 이름 확인 필요 |
| 대만 월매출 | tw\_revenue\_monthly | STD-1 | first\_collected\_at 있음 |
| 거시·업종 시계열 | us\_fred\_data, kr\_kosis\_data, kr\_komis\_mineral\_price, kr\_airline\_traffic, kr\_tourism\_visitors\_monthly | STD-1 | FRED는 ALFRED 공표 이력 있음 |
| 증시 수급 | kr\_stock\_credit\_balance\_daily, kr\_stock\_market\_funds\_daily | STD-1 | 1단계에서는 읽기 함수만 |
| 대체 데이터 | amazon\_bsr\_daily, apr\_us\_google\_trends | 기존 | 에이피알 시범용, collected\_at 있음 |

지수시세(kr\_stock\_index\_daily)와 내외국인 수출입은 1단계 범위에서 제외합니다.

## 3. 산출물 A: 기업 기준표

기업 정보는 한 기업 한 행인 `fd_entity_master`에, 기업과 외부 데이터의 연결은 한 연결 한 행인 `fd_entity_link`에 둡니다. 연결을 별도 표로 두면 HS 코드, 대만 비교기업, FRED 시리즈 등을 같은 방식으로 계속 추가할 수 있습니다.

**fd\_entity\_master** (PK: entity\_id)

| 컬럼 | 형식 | 내용 |
| --- | --- | --- |
| entity\_id | VARCHAR(20) | 한국 기업은 종목코드 6자리 (`005930`). 해외 기업은 `TW:2330`, `US:AAPL` |
| country | CHAR(2) | KR, TW, US |
| ticker | VARCHAR(20) | 거래소 원 코드 |
| market | VARCHAR(10) | KOSPI, KOSDAQ, TWSE, TPEx 등 |
| name\_kr / name\_en | VARCHAR(100) | 기업명 |
| dart\_corp\_code | CHAR(8) | DART 고유번호 (한국만) |
| fiscal\_year\_end | TINYINT | 결산월. 확인 불가 시 12 |
| listed\_status | VARCHAR(10) | listed, delisted |
| is\_pilot | TINYINT | 시범 분석 대상이면 1 |
| first\_collected\_at / last\_collected\_at | DATETIME | STD-1 |

**fd\_entity\_link** (PK: entity\_id, link\_type, link\_key)

| 컬럼 | 형식 | 내용 |
| --- | --- | --- |
| entity\_id | VARCHAR(20) | fd\_entity\_master 참조 |
| link\_type | VARCHAR(20) | hs\_code, tw\_peer, us\_peer, fred\_series, kosis\_series, komis\_series, alt\_data |
| link\_key | VARCHAR(100) | 연결 대상 키 (`854232`, `TW:2330`, `RSHPCS`, `kosis_xxxx` 등) |
| link\_source | VARCHAR(50) | kr\_company\_hs\_map, manual 등 출처 |
| role | VARCHAR(20) | product(자사 품목), customer(고객 수요), input(원가), peer(비교기업) |
| note | VARCHAR(255) | 연결 이유 |
| is\_active, first/last\_collected\_at |  | STD-1 |

**만드는 방법**

1. korea\_dart\_corp\_master에서 상장 기업(stock\_code 있음)을 읽어 master를 채웁니다. 시장 구분은 KSE\_Price 또는 시가총액 테이블에서 가져옵니다.
2. kr\_company\_hs\_map의 활성 행을 link\_type = hs\_code로 옮깁니다. fix\_type이 unresolved인 행은 제외합니다.
3. 대만·미국 비교기업과 시계열 연결은 1단계에서는 시범 6개 기업만 수작업(manual)으로 넣습니다. 자동 발굴은 2단계 과제입니다.

## 4. 산출물 B: 표준 읽기 함수

모든 읽기 함수는 원천과 상관없이 **같은 모양의 긴 표**(long DataFrame)를 돌려줍니다. 분석 코드는 원천 테이블 이름이나 컬럼 이름을 몰라도 됩니다.

**공통 반환 컬럼**

| 컬럼 | 내용 |
| --- | --- |
| key | 시계열 식별자. 매출·주가는 entity\_id, 수출입은 hs\_code, STD-1은 series\_id |
| period\_end | 기간 말일 (일별은 그날) |
| freq | D, W, M, Q, Y |
| item | 지표 이름 (revenue, expDlr, close 등) |
| value | 값 (원 단위 그대로) |
| unit / currency | 단위와 통화 |
| available\_at | 그 값을 알 수 있게 된 날 (5절) |
| asof\_quality | A, B, C (5절) |
| source | 원천 테이블 이름 |

**함수 목록**

| 함수 | 입력 | 원천 | 꼭 처리할 변환 |
| --- | --- | --- | --- |
| read\_revenue(entity\_ids, start, end, basis) | basis: consolidated(기본) / separate | DART V3 → DG 보완 | 누적 공시를 분기 단독값으로 변환 (Q4 = 연간 - 3분기 누적). DART와 DG가 같은 분기에 모두 있으면 DART 우선, 차이 1% 초과 시 경고 |
| read\_trade(hs\_codes, items, start, end) | items: expDlr, expWgt, impDlr, impWgt | korea\_monthly\_trade\_data | 코드는 문자열 그대로 매칭. 5자리·9자리 예전 코드 제외. FLOAT 값을 float64로 |
| read\_price(entity\_ids, start, end, field) | field: close 등 | KSE\_Price | 종목코드 형식 통일. available\_at = 거래일 |
| read\_series(family, keys, start, end, vintage) | family: fred, kosis, komis, airline, tourism, tw\_revenue | STD-1 테이블 | vintage=True면 공표 이력(\_vintage)으로 시점별 값 재현 |
| entity\_links(entity\_id, link\_type) |  | fd\_entity\_link | 기업에 연결된 HS 코드, 비교기업, 시계열 목록 |

**보조 함수**

- `as_of(df, date)`: available\_at이 date 이전인 값만 남겨, 그날 알 수 있던 정보만 돌려줍니다.
- `to_fiscal_quarter(df, entity_id)`: 월별 시계열을 그 기업의 결산 분기에 맞춰 합계 또는 평균으로 바꿉니다. 합계(금액, 물량)와 평균(지수, 가격)은 item별 기본값을 둡니다.
- `panel(entity_id, start, end)`: 매출과 연결된 모든 시계열을 분기 단위 한 표로 묶습니다. 시범 분석의 기본 입력입니다.

## 5. 발표 시점(as-of) 처리 방침

원칙은 **모르면 늦게 잡는다** 입니다. 발표일을 실제보다 이르게 잡으면 백테스트가 미래 정보를 쓰게 되므로, 확인된 날짜가 없으면 법정 기한이나 보수적 규칙으로 늦게 잡고 등급을 낮춰 표시합니다. 더 정확한 날짜를 확보하면 등급을 올립니다.

**신뢰도 등급**

| 등급 | 뜻 | 예 |
| --- | --- | --- |
| A | 실제 발표·공시·관측 날짜 | DART 접수일, ALFRED 공표일, 실시간 수집 시각 |
| B | 같은 기업·통계의 과거 실제 지연으로 추정 | 그 기업의 과거 "기간말 → 공시" 지연 중앙값 |
| C | 법정 기한이나 일반 규칙으로 보수적 추정 | 분기말 + 45일 |

**원천별 규칙**

| 원천 | 1순위 (A) | 대체 규칙 | 대체 등급 |
| --- | --- | --- | --- |
| 매출 (DART) | DART V3에 접수일(rcept\_dt 등) 컬럼이 있으면 그 날짜 | 분기·반기보고서 기간말 + 45일, 사업보고서 기간말 + 90일 (법정 제출기한) | C |
| 매출 (DG) | 없음 | DART와 같은 규칙 | C |
| 수출입 | 실시간 수집분의 최초 관측 시각 | 기간말 + 15일 (관세청 월간 확정치 발표 시점, 근사치로 1단계에서 검증) | C |
| 대만 월매출 | tw\_revenue\_monthly.first\_collected\_at (실시간 수집분) | 익월 10일 (법정 공시 기한) | B |
| FRED | ALFRED 공표 이력의 최초 공표일 | 이력 없는 시리즈는 최초 수집 시각과 규칙 중 늦은 날 | C |
| KOSIS·KOMIS·항공·관광 | 실시간 수집분의 최초 관측 시각 | 월간: 기간말 + 35일, 일별 가격: 다음 영업일 | C |
| 주가·일별 시장 데이터 | 거래일 당일 | 없음 | A |

**실시간 수집분 판정**: 오늘 적재한 과거 데이터는 first\_collected\_at이 실제 발표보다 몇 년 늦습니다. 그래서 최초 관측 시각이 규칙 날짜보다 30일 이상 늦으면 백필로 보고 규칙 날짜를 씁니다. 앞으로 정기 수집되는 값은 최초 관측 시각이 발표에 가까워 자연스럽게 A 등급이 됩니다.

**이후 개선 순서** (1단계 범위 밖, 순서만 정해 둠)

1. DART 공시목록 API로 정기보고서 접수일을 일괄 수집 → 매출 A 등급 확대
2. 기업별 과거 공시 지연 패턴 계산 → 접수일이 없는 기간을 B 등급으로
3. 잠정실적(영업실적 공정공시) 날짜 수집 → 정기보고서보다 이른 실제 시장 반영 시점. 서프라이즈 분석에 필요하므로 2단계에서 우선 검토

규칙 값(45일, 15일, 35일 등)은 코드 안 설정 딕셔너리 `RELEASE_RULES`에 모아, 검증 결과에 따라 한곳에서 고칩니다.

## 6. 산출물 C: 시범 분석

드라이버가 뚜렷하고 원천이 서로 다른 6개 기업으로 A와 B가 실제 분석 흐름에서 작동하는지 확인합니다. 1단계의 목적은 **흐름 검증**이며, 드라이버의 예측력 평가는 2단계에서 합니다.

| 기업 | 후보 드라이버 | 검증하는 기능 |
| --- | --- | --- |
| 삼성전자 (005930) | 메모리 수출 854232, 프로세서 854231, 대만 TSMC·SK하이닉스 비교 | 수출입 읽기, 대만 월매출 연결 |
| 동원산업 (006040) | 냉동 참치 수출입 030341\~030344 | 보정된 HS 코드가 끝까지 연결되는지 |
| 현대건설기계 (267270) | 휠로더 842951, 굴착기 842952, FRED 주택착공·건설지표 | 수출입 + FRED 혼합 |
| 에이피알 (278470) | amazon\_bsr\_daily, apr\_us\_google\_trends, FRED 건강·뷰티 소매판매(RSHPCS), KOSIS 온라인쇼핑 | 대체 데이터와 거시 시계열 연결 |
| 한국화장품제조 (003350) | HS 330499(기타 화장품)·330420(눈화장용 제품류)의 월별 수출총액(expDlr, USD)·수출중량(expWgt, kg) | read\_trade로 금액·중량을 함께 조회. 단가(USD/kg) 계산과 분기 합산(to\_fiscal\_quarter) 확인 |
| 하나투어 (039130) | 방한·출국 관광객, 항공 수송통계 | 관광·항공 STD-1 시계열 |

**성공 기준** (모두 충족하면 1단계 완료)

- [ ] 6개 기업 모두 `panel()` 한 줄로 매출과 연결 시계열이 분기 단위 한 표로 나온다
- [ ] 모든 값에 available\_at과 asof\_quality가 붙어 있고, 등급별 비율이 기업마다 보고된다
- [ ] `as_of(panel, 날짜)`가 그 날짜 이후에 발표된 값을 하나도 포함하지 않는다 (자동 테스트)
- [ ] 기업별로 매출 YoY와 각 후보 드라이버 YoY의 상관(동행, 1분기·2분기 선행)을 표와 그림으로 출력한다
- [ ] 원본 테이블 쓰기 0건 (DB 로그 또는 코드 검사로 확인)

에이피알의 상장일과 하나투어 비교 시계열의 범위는 구현 첫 단계에서 확인하고, 매출 이력이 8분기 미만이면 대체 기업으로 바꿉니다.

## 7. 구현 지침 (Claude Code)

구현은 Claude Code가 investment\_strategy 아래 `fdriver/` 패키지로 합니다. 수집 코드와 섞지 않고, investar에는 `fd_` 테이블만 씁니다.

**패키지 구조**

```
fdriver/
  config.py          DATA/config.py, KEYS.py 연결. RELEASE_RULES 정의
  db.py              엔진, 읽기 전용 조회 함수, fd_ 테이블 쓰기 함수(유일한 쓰기 경로)
  entity.py          fd_entity_master, fd_entity_link 생성·조회
  asof.py            available_at, asof_quality 계산, as_of() 필터
  adapters/
    revenue.py       read_revenue (DART V3 + DG, 분기 단독값 변환)
    trade.py         read_trade
    price.py         read_price
    series.py        read_series (STD-1 공용, vintage 지원)
  panel.py           to_fiscal_quarter, panel
  pilots/
    pilot_v1.ipynb   시범 6개 기업 실행·결과
  tests/             pytest
```

**작업 순서**

1. **사전 조사 (코드 작성 전)**: DART V3·DG의 컬럼 구조와 공시일 관련 컬럼 유무, KSE\_Price 종목코드 형식, korea\_dart\_corp\_master의 결산월 컬럼 유무를 조사해 보고서로 제출. 이 결과로 5절 규칙과 4절 변환을 확정
2. config, db, asof 작성 → entity 생성 (fd\_ 테이블 2개)
3. adapters 4개 작성. 각 함수마다 시범 기업 1곳으로 결과 확인
4. panel 작성 → pilot\_v1 노트북으로 6개 기업 실행
5. 6절 성공 기준 점검표를 채운 세션 리포트 제출

**테스트 (최소)**

- 분기 단독값 변환: Q1\~Q4 합계가 연간값과 일치
- as\_of: 임의 날짜 10개에서 available\_at > 날짜인 값이 0건
- read\_trade: 앞자리 0 코드(`030341`)가 숫자로 바뀌지 않고 조회됨
- 반환 컬럼: 모든 adapter가 4절 공통 컬럼을 같은 이름·형식으로 반환

**하지 말 것**

- 기존 테이블에 INSERT, UPDATE, DELETE, ALTER (fd\_ 테이블과 무관한 쓰기 전부)
- 비밀번호·API 키를 코드나 노트북에 쓰기 (config.py, KEYS.py만 사용)
- 코드에 이모지 사용
- 같은 파일 덮어쓰기: 수정본은 파일명 버전을 올림 (pilot\_v1 → pilot\_v2)
- 1단계 범위 밖 작업 (드라이버 자동 발굴, 전 종목 확장, 예측 모델)

## 8. 미결 사항

아래는 구현 전후에 결정이 필요한 항목입니다. 1번과 2번은 작업 순서 1단계(사전 조사) 결과를 보고 정합니다.

| # | 항목 | 기본안 | 결정 시점 |
| --- | --- | --- | --- |
| 1 | DART V3에 공시 접수일이 없을 때 | 법정 기한(C)으로 시작, 2단계에서 공시목록 API로 보강 | 사전 조사 후 |
| 2 | 연결/별도 재무 기준 | 연결 우선, 연결이 없는 기업만 별도 | 사전 조사 후 |
| 3 | 분석층 테이블 접두어 | `fd_` (원천 적재 테이블 kr\_/us\_/tw\_와 구분) | 이 명세서 승인 시 |
| 4 | 수출입 발표 시점 규칙(기간말 + 15일) | 관세청 발표 일정으로 1단계 중 검증 후 RELEASE\_RULES 수정 | 1단계 중 |
| 5 | 시범 기업 교체 | 매출 이력 8분기 미만이면 같은 업종 대체 기업 | 사전 조사 후 |
| 6 | 기존 테이블의 STD-1 이관 | 1단계에서는 하지 않음. 읽기 함수가 변환을 맡음 | 2단계 이후 |

**이번 명세서 승인 후 바로 할 일**: Claude Code에 이 문서를 전달하고 작업 순서 1번(사전 조사) 보고서를 받습니다.

## 9. v2 변경 사항 (사전 조사·작업 순서 2번 결과 반영, 2026-10-03)

아래 내용이 1\~8절과 다르면 이 절이 우선합니다.

| 구분 | v2 결정 |
| --- | --- |
| 패키지 위치 | `DATA/fdriver/` (git 저장소가 DATA 폴더) |
| 기업-HS 매핑 | `kr_company_hs_map` 생성 완료(810행, unresolved 0). entity는 읽기만 하고 자체 보정 로직은 만들지 않음 |
| 시범 수작업 연결 | 맵에 없는 854231(삼성전자), 330420(한국화장품제조)은 fd\_entity\_link에 link\_source = manual로 추가. 맵은 수정하지 않음 |
| 매출 원천 (D2) | DART V3(2025\~) → DART V2(2015\~2024) → DG(2009\~, 빈칸 보완). DG는 천원 단위라 ×1000, 날짜는 달력 분기말로 정규화 |
| 분기 단독값 | Q1·Q2·Q3는 각 보고서의 thstrm\_amount, Q4 = 사업보고서 연간 - (Q1+Q2+Q3). 매출 계정은 ifrs-full\_Revenue 우선, dart\_Revenue 계열 2순위 |
| 연결/별도 (D4) | V3는 CFS 우선, 없으면 OFS. V2·DG는 연결로 간주하되 시범 6개 기업 모두 DART와 DG를 비교해 차이 1% 초과 시 경고 |
| 매출 발표 시점 (D3) | 원천에 공시 접수일이 없음. report\_date는 기간말이므로 사용 금지. 법정 기한(분기 45일, 연간 90일) C등급 |
| 동원산업 (D5) | 참치 0303xx를 2006-01부터 재수집 완료. 정상 시범 기업으로 유지. 2022년 합병 단절은 결과 해석 시 표시 |
| 하나투어 (D6) | 출국자(direction = D, nat\_code 100)를 2011-09부터 수집 완료. 항공 여객은 보조 |
| 비교기업 (D7) | link\_type에 kr\_peer 추가 (SK하이닉스 000660) |
| 공통 반환 컬럼 | `asof_note` 추가 (등급으로 표현할 수 없는 주의사항, 예: 구글 트렌드 재조정) |
| FRED 규칙 | 규칙 날짜 월 +45일, 분기 +90일, 주 +7일, 일 +1영업일. first\_release\_date가 기간말 + 60일 이내면 A, 넘으면 백필로 보고 규칙 C |
| 대만 월매출 | 실시간 수집분 first\_collected\_at A, 그 이전 익월 10일 B |
| 증시 수급 일별 | 당일 A → **기간말 + 2영업일 C**로 변경. 정기 수집분은 first\_collected\_at으로 A |
| 구글 트렌드 | 주 시작일 + 7일 C, asof\_note = rescaled\_at\_collection |
| 문자셋 | 수출입·주가 코드 컬럼(utf16)과 STD-1 테이블(utf8mb4)은 SQL JOIN 금지. 코드 목록을 바인딩해 따로 조회 |
| 영업일 | 1단계는 주말만 제외. 공휴일은 KSE\_Price 거래일 달력으로 adapters 단계에서 보완 |
| 2단계 과제로 넘김 | 맵에는 있으나 수출입이 없는 hs6 37개 정리, 수집 목록에 없는 매핑 코드 추가, DART 공시 접수일 수집 |
