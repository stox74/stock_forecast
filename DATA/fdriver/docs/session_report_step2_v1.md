# fdriver 세션 리포트: 작업 순서 2번 v1

작성일: 2026-10-03 / 범위: 명세서 v1 7절 작업 순서 2번 (config, db, asof) + 비밀번호 파일 git 추적 제외 + 사용자 데이터 작업 확인

## 0. 요약

- `fdriver/config.py`, `db.py`, `asof.py`를 작성했고 테스트 29개가 모두 통과했습니다(실제 DB 읽기 전용 접속 1건 포함).
- 결정 사항(D2~D7, FRED·대만·구글 트렌드 규칙)은 `RELEASE_RULES`에 반영했습니다. D1은 결정대로 `kr_company_hs_map`을 읽기만 하고, 자체 0 보정 로직은 만들지 않았습니다.
- `DATA/config.py`, `DATA/KEYS.py`는 git 추적에서 뺐습니다. 커밋은 하지 않았고 삭제만 스테이징해 두었습니다.
- DB 쓰기는 0건입니다. 모든 조회는 읽기 전용 세션으로 했습니다.
- 사용자 데이터 작업 확인: HS 맵 810행, 0303xx 백필 2006-01부터, 출국자(D) 2011-09부터 모두 정상입니다. 다만 entity·adapters 단계 전에 알아야 할 점이 세 가지 있습니다(4.4절).

## 1. git 추적 제외

| 작업 | 결과 |
| --- | --- |
| `DATA/.gitignore` 신규 생성 | `/config.py`, `/KEYS.py` (앞에 `/`를 붙여 DATA 바로 아래 파일만 대상. `fdriver/config.py`는 무시되지 않음을 `git check-ignore`로 확인) |
| `git rm --cached DATA/config.py DATA/KEYS.py` | 실행함. 로컬 파일은 그대로 있음 |
| 커밋 | **하지 않음.** `git status`에 `D DATA/config.py`, `D DATA/KEYS.py`, `?? DATA/.gitignore`로 남아 있음. 다음 커밋 때 함께 반영됨 |

주의: 스테이징 영역에 사용자가 이미 올려 둔 `company_hs_map_build_v1.ipynb`(AM)가 있습니다. 커밋할 때 함께 들어가니 확인하세요.

### git 이력 정리 (실행하지 않음, 명령만 기록)

현재 상태: `config.py`가 커밋 7개, `KEYS.py`가 5개에 들어 있고, `origin/main`(github.com/stox74/stock_forecast)에 push되어 있습니다. 원격 브랜치는 `main`, `backup/before-clean`, `wip/diffusion_rate` 3개입니다.

1. **먼저 할 일 (이력 정리보다 중요)**: DB 비밀번호를 바꾸고 KEYS.py의 API 키를 재발급하세요. 이미 push된 값은 이력을 지워도 복제본·캐시에 남아 있을 수 있습니다.
2. 이력에서 두 파일 제거 (git-filter-repo 사용, 저장소 루트에서 실행):

```bash
git clone --mirror https://github.com/stox74/stock_forecast.git stock_forecast-mirror.git
```

```bash
cd stock_forecast-mirror.git && git filter-repo --invert-paths --path DATA/config.py --path DATA/KEYS.py
```

```bash
cd stock_forecast-mirror.git && git push --force --mirror origin
```

3. 그 후 작업 폴더는 새로 clone하거나 `git fetch` 후 `git reset --hard origin/main`으로 맞춥니다. 로컬 `config.py`, `KEYS.py`는 미리 따로 백업해 두세요. 세 원격 브랜치 모두 다시 쓰이므로 다른 PC의 복제본도 다시 받아야 합니다.

## 2. 작성한 코드

```
DATA/fdriver/
  __init__.py
  config.py        RELEASE_RULES, COMMON_COLUMNS, get_db_info(), get_key()
  db.py            읽기 전용 조회, fd_ 전용 쓰기 (유일한 쓰기 경로)
  asof.py          rule_dates, resolve, stamp, as_of, quality_report
  tests/
    conftest.py    import 경로 설정
    test_asof.py   19개
    test_db.py     10개
  docs/
    session_report_step2_v1.md   (이 문서)
  adapters/, pilots/             (빈 폴더, 3·4번에서 작성)
```

### 2.1 config.py

- 접속 정보는 `DATA.config.get_db_info()`, 키는 `DATA.KEYS.KEYS`에서만 읽습니다. fdriver 코드에는 비밀번호가 없습니다(테스트로 검사).
- `COMMON_COLUMNS`: 명세서 4절 공통 컬럼에 **`asof_note`를 하나 추가**했습니다. 구글 트렌드 "재조정 영향" 표시처럼 등급만으로 담을 수 없는 주의사항을 적는 칸입니다. 모든 adapter가 같은 컬럼을 반환하므로 형식 통일 원칙은 지켜집니다. (명세서와 다른 점 1)

### 2.2 RELEASE_RULES (결정 반영)

| 규칙 | 규칙 날짜 | 규칙 등급 | 관측 날짜 → 등급 | 백필 판정 |
| --- | --- | --- | --- | --- |
| revenue_quarter | 기간말 + 45일 | C | 사용 안 함 (report_date 금지, D3) | - |
| revenue_annual | 기간말 + 90일 | C | 사용 안 함 | - |
| trade | 기간말 + 15일 | C | 없음 | - |
| tw_revenue | 다음 달 10일 | **B** | first_collected_at → A | 관측일 > 규칙일 + 30일 |
| fred | M +45일, Q +90일, W +7일, D +1영업일, Y +120일 | C | first_release_date → A | **관측일 > 기간말 + 60일** (승인안) |
| fred_collected | fred와 같음 | C | first_collected_at → A | 관측일 > 규칙일 + 30일 (공표 이력 없는 시리즈용) |
| kosis | M +35일, D +1영업일 (Q +75일, Y +120일) | C | first_collected_at → A | 규칙일 + 30일 |
| komis | D +1영업일, W +7일, M +35일 | C | 같음 | 같음 |
| airline, tourism | M +35일 | C | 같음 | 같음 (tourism은 E·D 공통) |
| price, market_daily | 당일 | A | 기간말 = 관측일 | - |
| amazon_bsr | 다음 날 | C | first_collected_at → A | 규칙일 + 30일 |
| google_trends | 주 시작일 + 7일 | C | 사용 안 함 | - / asof_note = `rescaled_at_collection` |

명세서에 없어서 제가 정한 값(검토 필요):
- FRED 규칙 날짜(명세서는 "규칙"이라고만 함): 월 +45일, 분기 +90일, 주 +7일, 일 +1영업일. 실제 공표 지연은 월 11~19일, 분기 GDP 30일, 주간 실업수당 5일이라 모두 실제보다 늦게 잡은 값입니다.
- KOSIS 분기 +75일, 연 +120일. 현재 KOSIS 데이터는 모두 월간이라 당장 쓰이지는 않습니다.
- 증시 수급 일별(`market_daily`)은 명세서대로 당일 A로 두었습니다. 다만 신용잔고는 금융투자협회가 다음 영업일 이후에 공표하므로, "모르면 늦게" 원칙대로라면 +1~2영업일이 맞습니다. 수정 여부를 정해 주세요. (명세서와 다를 수 있는 점 2)

### 2.3 asof.py

- `resolve(period_end, rule, observed, freq)`: 관측일이 있고 백필이 아니며 기간말보다 늦으면 관측일과 관측 등급을, 그렇지 않으면 규칙 날짜와 규칙 등급을 씁니다. 관측일이 기간말보다 이르면 데이터 오류로 보고 버립니다.
- `stamp(df, rule)`: 위 결과를 df에 붙여 돌려줍니다(원본은 그대로).
- `as_of(df, 날짜)`: `available_at <= 날짜`인 행만 남깁니다. available_at이 비어 있으면 제외합니다.
- `quality_report(df, by)`: 그룹별 A/B/C 비율과 행 수 (6절 성공 기준 2번용).
- **available_at 규약**: 날짜만 씁니다(시각 없음). 뜻은 "그날 장 마감까지 알 수 있었던 값"입니다. 수집 시각은 날짜로 내립니다. 그래서 `as_of(d)`는 d일 종가 시점까지 알려진 정보이고, 백테스트 매매는 d 다음 영업일 기준을 권합니다.
- 알려진 한계: 영업일 계산이 주말만 빼고 공휴일은 반영하지 않습니다. 일별 시계열이 공휴일 전날이면 최대 며칠 이르게 잡힐 수 있습니다. KSE_Price 거래일 달력으로 바꾸는 작업은 adapters 단계에서 하겠습니다.

### 2.4 db.py 안전장치 (원본 쓰기 0건 보장)

1. **읽기 엔진**: 새 접속마다 `SET SESSION TRANSACTION READ ONLY`를 실행합니다. 테스트에서 실제 DB의 `tx_read_only = 1`을 확인했습니다.
2. **읽기 문장 검사**: SELECT/WITH/SHOW/DESCRIBE/EXPLAIN으로 시작하지 않거나, 여러 문장이거나, FOR UPDATE·INTO OUTFILE이 있으면 DB에 보내기 전에 거부합니다.
3. **쓰기 경로 단일화**: `write_fd`(upsert)와 `execute_fd_ddl`만 쓰기를 하고, 테이블 이름이 `fd_[a-z0-9_]+`가 아니면 거부합니다. 쓰기 엔진은 읽기 엔진과 분리했고, 이번 프로세스에서 한 쓰기는 `WRITE_LOG`에 남습니다.
4. **코드 검사 테스트**: db.py를 뺀 fdriver 코드에서 fd_ 이외 테이블에 대한 INSERT/UPDATE/DELETE/ALTER/CREATE/DROP 문장, `.to_sql(`, `create_engine(`, `.begin()`이 나오면 실패합니다.
5. `in_clause()`: IN 목록 값을 항상 바인딩해 `030341` 같은 코드가 숫자로 바뀌지 않게 합니다.

## 3. 테스트 결과

pytest가 설치돼 있지 않아 테스트를 `unittest` 형식으로 썼습니다. 이 형식은 pytest로도 그대로 돌아갑니다. pytest 설치는 사용자 판단에 맡깁니다(`pip install pytest`).

```bash
python -m unittest discover -s DATA/fdriver/tests -t . -v
```

(`investment_strategy` 폴더에서 실행)

```
Ran 29 tests in 6.4s
OK
```

| 구분 | 테스트 | 결과 |
| --- | --- | --- |
| 규칙 설정 | 모든 규칙 형식, 필수 규칙 존재 | 통과 |
| 규칙 날짜 | 매출 45/90일, report_date를 넘겨도 C, 수출입 15일, 대만 익월 10일, freq 누락·미지원 시 오류 | 통과 |
| 등급 판정 | 대만 실시간 A / 백필 B, FRED 60일 기준 A/C, FRED 분기·주·일, KOSIS 백필 C / 실시간 A, 기간말보다 이른 관측일 무시, 주가 당일 A, 구글 트렌드 C + 표시, 모든 규칙에서 available_at ≥ 기간말 | 통과 |
| as_of | **임의 날짜 10개에서 미래 값 0건** + 과하게 버린 값도 0건, 경계일 포함, 빈 날짜 제외 | 통과 |
| db | 읽기 허용·쓰기 거부 문장 19종, fd_ 이름 검사, DDL 대상 검사, write_fd가 접속 전에 거부, 코드 검사 3종, 실제 DB 읽기 전용 세션, 앞자리 0 바인딩 | 통과 |

명세서 7절의 나머지 최소 테스트(분기 단독값 변환, read_trade 030341, adapter 공통 컬럼)는 3번(adapters) 범위라 그때 작성합니다.

## 4. 사용자 데이터 작업 확인 (읽기 전용)

### 4.1 kr_company_hs_map

- **810행 (전부 is_active=1), 533개 기업.** ticker는 6자리 문자열 (`005930`)
- 컬럼: ticker, hs_code, ticker_raw, company_name, hs6, hs_digits, hs_name, in_collect_list, hs_code_raw, fix_type, fix_note, source_tables, is_active, first/last_collected_at, created_at, updated_at
- fix_type별: none 802 (4자리 145, 6자리 340, 10자리 317), leading_zero 5, trailing_zero 2, manual 1. **unresolved 행은 없음.**
- entity.py는 `hs_code`를 그대로 link_key로 쓰고, 수출입 조회용 6자리는 `hs6` 컬럼을 그대로 쓰겠습니다(자체 보정 없음).

시범 기업 HS 코드와 수출입 기간 (expDlr 기준):

| 기업 | hs6 | fix_type | 수출입 기간 |
| --- | --- | --- | --- |
| 한국화장품제조 003350 | 330499 | none | 2007-01 ~ 2026-08 (236개월) |
| 삼성전자 005930 | 854232 (10자리 2개), 854239, 852351, 851762, 845020, 841810 | none | 2007-01 ~ 2026-08 |
| | 847130, 852872 | none | 2007-01 ~ **2025-07** (그 뒤 끊김) |
| | 851770 | none | 2007-01 ~ **2022-09** |
| | 852851 | none | 2007-01 ~ **2017-07** |
| | 851712 | none | **수출입 데이터 없음** |
| | 8542 (4자리) | none | 2008-01 ~ 2026-01 |
| 동원산업 006040 | 030341, 030342, 030343, 030344 | leading_zero | **2006-01 ~ 2026-08 (248개월), expWgt 포함** |
| 현대건설기계 267270 | 842951 | manual (원본 842951101) | 2007-01 ~ 2026-08 |
| | 842952, 842720, 843149 | none | 2007-01 ~ 2026-08 |
| 에이피알, 하나투어 | 없음 (예상대로) | | |

### 4.2 0303xx 등 백필 코드

- 앞자리 0인 6자리 코드 10개 중 **030341~030344는 2006-01부터** 매달 빠짐없이 있습니다(2007·2008·2015·2024·2025년 각 12개월, 2026년 8개월 확인). 요청하신 "2007년부터"보다 1년 더 깁니다.
- 나머지: 080610, 081010은 2007-01부터인데 **2026-01에서 멈춤**. 030354, 030389, 030487은 2012-01부터, 030633은 2017-01부터입니다.

### 4.3 kr_tourism_visitors_monthly direction='D'

- **2011-09 ~ 2026-08, 180개월 빠짐없음**, nat_code `100` 하나(출국 합계로 보임), first_collected_at 2026-10-03(백필 → 규칙 C).
- 방한(E)과 기간이 같습니다(2011-09~2026-08). 하나투어 시범에 바로 쓸 수 있습니다. read_series의 tourism family는 키를 `direction:nat_code`(예: `D:100`) 형태로 만들겠습니다.

### 4.4 다음 단계 전에 알아야 할 것

1. **명세서 시범 드라이버 중 HS 맵에 없는 코드**: 삼성전자 854231(프로세서), 한국화장품제조 330420(눈화장용 제품). 수출입 데이터는 둘 다 2007-01부터 있습니다. entity 단계에서 시범 기업 수작업 연결(link_source='manual')로 추가하겠습니다. 이렇게 하면 HS 맵은 건드리지 않습니다.
2. **문자셋 불일치**: korea_monthly_trade_data와 KSE_Price의 코드 컬럼은 utf16, kr_company_hs_map은 utf8mb4입니다. SQL에서 두 테이블을 JOIN하면 오류가 납니다(이번 조사에서 실제로 발생). read_trade는 JOIN 없이 코드 목록을 바인딩해서 조회하겠습니다.
3. **맵에 있지만 수출입이 없는 코드**: 395개 hs6 중 37개(43행, 24개 기업)는 수출입 테이블에 행이 없습니다. 현대제철의 `4000`, `5000`처럼 HS 코드로 보기 어려운 값도 섞여 있습니다. 1단계 시범과는 무관하지만, 2단계 전 종목 확장 전에 정리가 필요합니다.

## 5. 성공 기준 진행 상황 (명세서 6절)

| 기준 | 상태 |
| --- | --- |
| 6개 기업 panel() 한 줄 | 미착수 (4번) |
| 모든 값에 available_at, asof_quality + 등급 비율 보고 | 함수 준비 완료 (stamp, quality_report) |
| as_of 미래 값 0건 자동 테스트 | **통과** (합성 데이터 기준. 실제 panel 대상 테스트는 4번에서 추가) |
| 매출 YoY와 드라이버 YoY 상관 표·그림 | 미착수 (4번) |
| 원본 테이블 쓰기 0건 | **이번 세션 0건.** 읽기 전용 세션 + 코드 검사 테스트로 보장 |

## 6. 다음 작업 (작업 순서 2번 후반 ~ 3번)

1. entity.py: fd_entity_master, fd_entity_link 생성 (**첫 DB 쓰기**. fd_ 테이블 2개 CREATE + 적재)
   - master: korea_dart_corp_master + KSE_Price(상장 판정) + DG market
   - link: kr_company_hs_map 활성 행 + 시범 기업 수작업 연결(854231, 330420, TW 비교기업, kr_peer 000660, FRED·KOSIS·관광·항공·대체 데이터)
2. adapters 4개 (D2 매출 3단, D4 DART와 DG 1% 비교 경고, 문자셋 불일치 회피)

## 7. 결정 요청

1. `market_daily`(신용잔고·증시자금) 발표 시점: 명세서대로 당일 A를 유지할지, +1영업일 C로 늦출지
2. `asof_note` 컬럼 추가(명세서와 다른 점) 승인 여부
3. 시범 기업 수작업 HS 연결(854231, 330420) 방식 승인 여부
