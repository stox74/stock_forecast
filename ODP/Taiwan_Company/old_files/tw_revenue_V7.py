"""tw_revenue_V7.py — 대만 대표기업 월 매출 수집·예측·시각화 (단일 파일)

사용법: 더블클릭 → 메뉴 선택.
  - 데이터: MariaDB investar (DATA/config.py 접속 정보 사용)
  - 차트:   이 파일과 같은 폴더의 output/

파일 V7 변경사항: 저장소를 로컬 SQLite(revenue.db) → MariaDB investar로 전환
 - investar 표준 규격 STD-1 적용 (db 섹션 상단 설명 참조)
 - 신규 테이블: tw_revenue_monthly, tw_revenue_monthly_vintage, tw_revenue_forecast
 - 최초 관측 시각(first_collected_at)과 값 변경 이력(vintage) 저장 → as-of 재현 가능
 - 예측에 horizon, engine(user/builtin/mean), forecast_at 기록
 - 기존 revenue.db 일괄 이관 메뉴 [7] / CLI `migrate` 추가 (원본 파일은 변경하지 않음)
 - 기존 investar.taiwan_company_revenue 테이블은 건드리지 않음

v8 변경사항: TPEx OpenAPI 안정화
 - OpenAPI 조회 시 최대 3회 재시도, 2회차부터 gzip 압축 해제(identity) 및
   Connection: close 적용 (TPEx IncompleteRead 오류 대응)
 - collect_latest를 시장별 독립 처리로 변경: TWSE/TPEx 중 실패한 쪽만
   MOPS(sii/otc) 지난달 페이지로 폴백 → 상궤 종목 조용한 누락 방지

v7 변경사항: 상궤(TPEx) 시장 수집 지원
 - COMPANIES에 8299 Phison Electronics 추가 (TPEx 상장)
 - MOPS 과거 수집: sii(상장) + otc(상궤) 두 시장 페이지를 모두 순회
 - 최신 월 증분 수집: TWSE OpenAPI(t187ap05_L)에 더해
   TPEx OpenAPI(mopsfin_t187ap05_O)도 함께 조회 (source='openapi-tpex')
 - 접속 진단 메뉴에서 otc 페이지와 TPEx OpenAPI 상태도 함께 점검

v6 변경사항: 사용자 예측 모듈 연동
 - setup_universal_paths(): 스크립트 위치/현재 폴더에서 상위로 DATA 폴더를
   자동 탐색해 sys.path에 추가 (Korea_revenue_forecast_v2와 동일 방식)
 - 예측 시 DATA/universal_ts_forecast_function의
   forecast_sarima / forecast_ets / forecast_theta (try_transforms=True, 계절주기 12)를
   1순위로 사용, Ensemble은 세 모델 예측의 단순 평균
 - DATA 폴더나 모듈이 없으면 자동으로 내장 statsmodels 엔진으로 폴백
 - 예측 로그에 모델별 사용 엔진 표시 (예: sarima:user / sarima:builtin)
"""
# --- 필요 패키지 자동 설치 (최초 1회) -------------------------------
def _ensure_packages():
    import importlib.util, subprocess, sys
    need = {"requests": "requests", "pandas": "pandas", "lxml": "lxml",
            "statsmodels": "statsmodels", "matplotlib": "matplotlib",
            "sqlalchemy": "sqlalchemy", "pymysql": "pymysql"}
    missing = [pip for mod, pip in need.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"필요 패키지 자동 설치 중: {', '.join(missing)} (잠시 기다려 주세요)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
        print("설치 완료.\n")

_ensure_packages()
# ---------------------------------------------------------------------
# ======================================================================
# === config.py ===
# ======================================================================
"""프로젝트 전역 설정."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def setup_universal_paths(quiet: bool = False):
    """
    어떤 PC에서도 작동하는 범용 경로 설정.
    스크립트 위치와 현재 작업 폴더에서 상위로 올라가며 DATA 폴더를 찾아
    sys.path에 추가한다 (사용자 예측 모듈 import용).
    찾지 못해도 오류 없이 None을 반환한다 (내장 엔진 폴백).
    """
    seen = set()
    for start in (Path.cwd(), BASE_DIR):
        for parent in [start, *start.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            data_folder = parent / "DATA"
            if data_folder.exists():
                for p in (str(parent), str(data_folder)):
                    if p not in sys.path:
                        sys.path.insert(0, p)
                if not quiet:
                    print("=" * 60)
                    print("경로 설정 완료")
                    print(f"프로젝트 루트: {parent}")
                    print(f"DATA 폴더:    {data_folder}")
                    print("=" * 60)
                return {"project_root": parent, "data_folder": data_folder,
                        "current": Path.cwd()}
    if not quiet:
        print("[경로] DATA 폴더를 찾지 못했습니다 → 내장 예측 엔진을 사용합니다.")
    return None
# 구버전 로컬 SQLite 경로 (이관 메뉴에서만 사용, 읽기 전용)
LEGACY_SQLITE_PATH = BASE_DIR / "revenue.db"
OUTPUT_DIR = BASE_DIR / "output"

# 수집 대상 기업 (종목코드: 표시용 이름)
# 대만 시가총액 상위 50개 (2026년 TWSE 가권지수 시총 비중 기준, 전부 상장 sii)
# 필요 시 자유롭게 추가/삭제하면 됩니다.
COMPANIES = {
    "2330": "TSMC",
    "2308": "Delta Electronics",
    "2454": "MediaTek",
    "2317": "Hon Hai (Foxconn)",
    "3711": "ASE Technology",
    "2383": "Elite Material",
    "3037": "Unimicron",
    "2345": "Accton Technology",
    "2881": "Fubon Financial",
    "2382": "Quanta Computer",
    "2882": "Cathay Financial",
    "3017": "Asia Vital Components",
    "2412": "Chunghwa Telecom",
    "2891": "CTBC Financial",
    "2303": "UMC",
    "2360": "Chroma ATE",
    "7769": "Grand Process Tech",
    "6669": "Wiwynn",
    "3653": "Jentech Precision",
    "1303": "Nan Ya Plastics",
    "2368": "Gold Circuit Electronics",
    "2885": "Yuanta Financial",
    "2408": "Nanya Technology",
    "2327": "Yageo",
    "8046": "Nan Ya PCB",
    "2887": "Taishin Shinkong Financial",
    "2886": "Mega Financial",
    "3443": "Global Unichip",
    "3665": "BizLink-KY",
    "6505": "Formosa Petrochemical",
    "2884": "E.SUN Financial",
    "4958": "Zhen Ding-KY",
    "2890": "SinoPac Financial",
    "2880": "Hua Nan Financial",
    "2603": "Evergreen Marine",
    "3231": "Wistron",
    "2357": "ASUSTeK",
    "3045": "Taiwan Mobile",
    "2892": "First Financial",
    "2344": "Winbond Electronics",
    "1216": "Uni-President",
    "2301": "Lite-On Technology",
    "6515": "WinWay Technology",
    "2059": "King Slide Works",
    "2449": "King Yuan Electronics",
    "2883": "KGI Financial",
    "5880": "Taiwan Cooperative Financial",
    "3008": "Largan Precision",
    "4904": "Far EasTone",
    "1301": "Formosa Plastics",
    "8299": "Phison Electronics",  # TPEx(상궤) 상장 — otc 시장에서 수집됨
}

# MOPS 월별 매출 집계 페이지
# 2024년 사이트 개편 이후 신 도메인(mops)의 정적 페이지는 404이며,
# 구버전 아카이브(mopsov)에 데이터가 남아 있으므로 mopsov를 먼저 시도한다.
MOPS_URL_TEMPLATES = [
    "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_0.html",
    "https://mops.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_0.html",
]

# TWSE OpenAPI - 상장사 최신 월 매출 (JSON)
TWSE_OPENAPI_MONTHLY = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
# TPEx OpenAPI - 상궤(上櫃)사 최신 월 매출 (JSON) — 8299 Phison 등 TPEx 종목용
TPEX_OPENAPI_MONTHLY = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

MARKET = "sii"          # (구버전 호환용 기본값)
OTC_TICKERS = {"8299"}  # 상궤(TPEx) 종목. market 정보가 없는 행(구 SQLite 등)의 보정용
MARKETS = ["sii", "otc"]  # sii=상장(TWSE), otc=상궤(TPEx). rotc=흥궤는 미포함

# MOPS는 브라우저가 아닌 요청을 차단하는 경우가 있어 브라우저형 헤더를 최대한 갖춘다
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://mopsov.twse.com.tw/mops/web/index",
    "Connection": "keep-alive",
}

# 요청 간 대기 시간(초) - 서버 부담 방지
REQUEST_DELAY_SEC = 2.0

# SARIMA 기본 설정
SARIMA_ORDER = (1, 1, 1)
SARIMA_SEASONAL_ORDER = (1, 1, 1, 12)
MIN_OBS_FOR_FORECAST = 30  # 최소 관측치(개월)


# ======================================================================
# === db.py (MariaDB investar, 표준 규격 STD-1) ===
# ======================================================================
"""MariaDB investar 저장 계층.

investar 표준 규격 STD-1 (향후 전 테이블 통일 기준)
 1. 테이블명  소문자 snake_case, {국가}_{대상}_{주기 또는 용도}  예) tw_revenue_monthly
 2. 문자셋    ENGINE=InnoDB, CHARSET=utf8mb4, COLLATE=utf8mb4_general_ci
 3. 식별자    ticker VARCHAR(20) = 거래소 원 코드 문자열(접두/접미 없음), market 컬럼 병기
 4. 기간      period_end DATE = 기간 말일(월말, 분기말), freq CHAR(1) = D/W/M/Q/Y
 5. 값        원 단위 그대로 저장, unit 과 currency(ISO 4217) 컬럼으로 명시
 6. 시점      first_collected_at(최초 관측, 이후 갱신 안 함), last_collected_at(최근 관측), KST
 7. 이력      값이 처음 들어오거나 바뀔 때 {테이블}_vintage 에 append (as-of 재현용)
 8. 예측      basis_period_end(사용한 마지막 실적), target_period_end, horizon, model,
              engine, forecast_at. 기준월이 다르면 별도 행으로 이력 보존
 9. 감사      created_at, updated_at (DB 자동 관리)

테이블
 - tw_revenue_monthly         : 실적 월 매출 최신값 (PK ticker, period_end)
 - tw_revenue_monthly_vintage : 실적의 신규/수정 이력 (append only)
 - tw_revenue_forecast        : 예측 이력 (PK ticker, model, basis_period_end, target_period_end)

collector/forecaster/visualizer는 기존처럼 (year, month) dict/tuple을 주고받고,
period_end 변환은 이 계층 안에서만 처리한다.
"""
import calendar
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import bindparam, text

KST = timezone(timedelta(hours=9))

T_REV = "tw_revenue_monthly"
T_REV_VIN = "tw_revenue_monthly_vintage"
T_FC = "tw_revenue_forecast"

FREQ = "M"
UNIT = "thousand"   # MOPS 원본 단위: 천
CURRENCY = "TWD"    # 대만달러 ISO 4217

_TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"

DDL = [
    f"""
CREATE TABLE IF NOT EXISTS {T_REV} (
    ticker             VARCHAR(20)  NOT NULL COMMENT '거래소 원 코드 (예: 2330)',
    market             VARCHAR(10)  NULL     COMMENT 'sii=TWSE 상장, otc=TPEx 상궤',
    company_name       VARCHAR(100) NULL,
    period_end         DATE         NOT NULL COMMENT '월말일',
    freq               CHAR(1)      NOT NULL DEFAULT 'M',
    revenue            BIGINT       NULL     COMMENT '당월 매출',
    mom_pct            DOUBLE       NULL     COMMENT '전월 대비 %',
    yoy_pct            DOUBLE       NULL     COMMENT '전년 동월 대비 %',
    unit               VARCHAR(20)  NOT NULL DEFAULT 'thousand',
    currency           CHAR(3)      NOT NULL DEFAULT 'TWD',
    source             VARCHAR(20)  NULL     COMMENT 'mops | openapi | openapi-tpex',
    first_collected_at DATETIME     NOT NULL COMMENT '최초 관측 시각(KST)',
    last_collected_at  DATETIME     NOT NULL COMMENT '최근 관측 시각(KST)',
    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, period_end),
    KEY idx_period_end (period_end)
) {_TABLE_OPTS} COMMENT='대만 기업 월 매출 실적 (STD-1)'
""",
    f"""
CREATE TABLE IF NOT EXISTS {T_REV_VIN} (
    id           BIGINT      NOT NULL AUTO_INCREMENT,
    ticker       VARCHAR(20) NOT NULL,
    period_end   DATE        NOT NULL,
    revenue      BIGINT      NULL,
    mom_pct      DOUBLE      NULL,
    yoy_pct      DOUBLE      NULL,
    source       VARCHAR(20) NULL,
    change_type  VARCHAR(10) NOT NULL COMMENT 'new | revised',
    collected_at DATETIME    NOT NULL COMMENT '이 값을 관측한 시각(KST)',
    created_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_key_time (ticker, period_end, collected_at)
) {_TABLE_OPTS} COMMENT='대만 기업 월 매출 관측 이력 (STD-1 vintage)'
""",
    f"""
CREATE TABLE IF NOT EXISTS {T_FC} (
    ticker            VARCHAR(20) NOT NULL,
    model             VARCHAR(20) NOT NULL COMMENT 'sarima | theta | ets | ensemble',
    basis_period_end  DATE        NOT NULL COMMENT '예측에 사용한 마지막 실적 월말',
    target_period_end DATE        NOT NULL COMMENT '예측 대상 월말',
    freq              CHAR(1)     NOT NULL DEFAULT 'M',
    horizon           SMALLINT    NOT NULL COMMENT '기준월 대비 몇 개월 뒤',
    value             DOUBLE      NULL     COMMENT '예측값',
    lower_95          DOUBLE      NULL,
    upper_95          DOUBLE      NULL,
    unit              VARCHAR(20) NOT NULL DEFAULT 'thousand',
    currency          CHAR(3)     NOT NULL DEFAULT 'TWD',
    engine            VARCHAR(20) NULL     COMMENT 'user | builtin | mean',
    forecast_at       DATETIME    NOT NULL COMMENT '예측 실행 시각(KST)',
    created_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, model, basis_period_end, target_period_end),
    KEY idx_target (ticker, target_period_end)
) {_TABLE_OPTS} COMMENT='대만 기업 월 매출 예측 이력 (STD-1)'
""",
]

# 같은 키가 이미 있으면: 더 최근 관측일 때만 값 갱신, first는 더 이른 시각 유지.
# MariaDB는 UPDATE 절을 왼쪽부터 평가하므로 last_collected_at 갱신을 맨 뒤에 둔다.
_UPSERT_REV = f"""
INSERT INTO {T_REV}
    (ticker, market, company_name, period_end, freq, revenue, mom_pct, yoy_pct,
     unit, currency, source, first_collected_at, last_collected_at)
VALUES
    (:ticker, :market, :company_name, :period_end, '{FREQ}', :revenue, :mom_pct, :yoy_pct,
     '{UNIT}', '{CURRENCY}', :source, :ts, :ts)
ON DUPLICATE KEY UPDATE
    market       = IF(VALUES(last_collected_at) >= last_collected_at, COALESCE(VALUES(market), market), market),
    company_name = IF(VALUES(last_collected_at) >= last_collected_at, COALESCE(VALUES(company_name), company_name), company_name),
    revenue      = IF(VALUES(last_collected_at) >= last_collected_at, VALUES(revenue), revenue),
    mom_pct      = IF(VALUES(last_collected_at) >= last_collected_at, VALUES(mom_pct), mom_pct),
    yoy_pct      = IF(VALUES(last_collected_at) >= last_collected_at, VALUES(yoy_pct), yoy_pct),
    source       = IF(VALUES(last_collected_at) >= last_collected_at, VALUES(source), source),
    first_collected_at = LEAST(first_collected_at, VALUES(first_collected_at)),
    last_collected_at  = GREATEST(last_collected_at, VALUES(last_collected_at))
"""

_INSERT_VIN = f"""
INSERT INTO {T_REV_VIN}
    (ticker, period_end, revenue, mom_pct, yoy_pct, source, change_type, collected_at)
VALUES
    (:ticker, :period_end, :revenue, :mom_pct, :yoy_pct, :source, :change_type, :ts)
"""

_UPSERT_FC = f"""
INSERT INTO {T_FC}
    (ticker, model, basis_period_end, target_period_end, freq, horizon,
     value, lower_95, upper_95, unit, currency, engine, forecast_at)
VALUES
    (:ticker, :model, :basis_period_end, :target_period_end, '{FREQ}', :horizon,
     :value, :lower_95, :upper_95, '{UNIT}', '{CURRENCY}', :engine, :ts)
ON DUPLICATE KEY UPDATE
    horizon     = VALUES(horizon),
    value       = VALUES(value),
    lower_95    = VALUES(lower_95),
    upper_95    = VALUES(upper_95),
    engine      = VALUES(engine),
    forecast_at = VALUES(forecast_at)
"""


def _period_end(year, month):
    """(연, 월) → 그 달의 말일 date."""
    year, month = int(year), int(month)
    return date(year, month, calendar.monthrange(year, month)[1])


def _now():
    """현재 시각(KST, tz 정보 없는 datetime, 초 단위)."""
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


def _market(r, ticker):
    m = r.get("market")
    if m:
        return m
    src = r.get("source") or ""
    if "tpex" in src or ticker in OTC_TICKERS:
        return "otc"
    return "sii"


def get_conn():
    """DATA/config.py로 investar 엔진을 만들고 표준 테이블을 준비한다."""
    setup_universal_paths(quiet=True)
    try:
        import config as investar_config
        engine = investar_config.get_engine(investar_config.get_db_info())
    except ImportError as e:
        raise RuntimeError(
            "DATA/config.py를 찾지 못했습니다. 이 파일을 investment_strategy 폴더 "
            "아래(DATA 폴더가 보이는 위치)에 두고 실행하세요."
        ) from e
    with engine.begin() as cx:
        for ddl in DDL:
            cx.execute(text(ddl))
    return engine


def upsert_revenue(conn, rows, collected_at=None):
    """실적 저장. rows: dict 리스트
    (company_id, company_name, year, month, revenue, mom_pct, yoy_pct, source[, market])

    신규 키이거나 매출 값이 바뀐 행은 vintage 테이블에도 기록한다.
    반환: vintage에 기록한 행 수
    """
    if not rows:
        return 0
    ts = collected_at or _now()
    recs = {}
    for r in rows:
        tk = str(r["company_id"]).strip()
        rec = {
            "ticker": tk,
            "market": _market(r, tk),
            "company_name": r.get("company_name"),
            "period_end": _period_end(r["year"], r["month"]),
            "revenue": None if r.get("revenue") is None else int(r["revenue"]),
            "mom_pct": r.get("mom_pct"),
            "yoy_pct": r.get("yoy_pct"),
            "source": r.get("source"),
            "ts": ts,
        }
        recs[(tk, rec["period_end"])] = rec  # 같은 배치 안 중복 키는 마지막 값 사용
    recs = list(recs.values())

    q_exist = text(
        f"SELECT ticker, period_end, revenue FROM {T_REV} "
        f"WHERE ticker IN :tk AND period_end IN :pe"
    ).bindparams(bindparam("tk", expanding=True), bindparam("pe", expanding=True))

    with conn.begin() as cx:
        existing = {}
        res = cx.execute(q_exist, {"tk": sorted({x["ticker"] for x in recs}),
                                   "pe": sorted({x["period_end"] for x in recs})})
        for tk, pe, rv in res:
            existing[(tk, pe)] = rv
        vin = []
        for x in recs:
            key = (x["ticker"], x["period_end"])
            if key not in existing:
                vin.append(dict(x, change_type="new"))
            elif existing[key] != x["revenue"]:
                vin.append(dict(x, change_type="revised"))
        cx.execute(text(_UPSERT_REV), recs)
        if vin:
            cx.execute(text(_INSERT_VIN), vin)
    return len(vin)


def upsert_forecast(conn, rows, forecast_at=None):
    """예측 저장. rows: dict 리스트
    (company_id, model, basis_year, basis_month, target_year, target_month,
     predicted, lower_95, upper_95[, engine])
    """
    if not rows:
        return 0
    ts = forecast_at or _now()
    recs = []
    for r in rows:
        by, bm = int(r["basis_year"]), int(r["basis_month"])
        ty, tm = int(r["target_year"]), int(r["target_month"])
        recs.append({
            "ticker": str(r["company_id"]).strip(),
            "model": r["model"],
            "basis_period_end": _period_end(by, bm),
            "target_period_end": _period_end(ty, tm),
            "horizon": (ty - by) * 12 + (tm - bm),
            "value": r.get("predicted"),
            "lower_95": r.get("lower_95"),
            "upper_95": r.get("upper_95"),
            "engine": r.get("engine"),
            "ts": ts,
        })
    with conn.begin() as cx:
        cx.execute(text(_UPSERT_FC), recs)
    return len(recs)


def load_revenue_series(conn, company_id):
    """특정 기업의 월 매출을 (year, month, revenue) 오름차순으로 반환."""
    q = text(
        f"SELECT YEAR(period_end), MONTH(period_end), revenue FROM {T_REV} "
        f"WHERE ticker = :t AND revenue IS NOT NULL ORDER BY period_end"
    )
    with conn.connect() as cx:
        return [tuple(r) for r in cx.execute(q, {"t": str(company_id)})]


def load_forecasts(conn, company_id, basis=None, model=None):
    """예측 이력 조회. basis=(year, month), model('sarima'|'theta'|'ets'|'ensemble') 필터 가능.
    반환 열: basis_year, basis_month, target_year, target_month, predicted, lower_95, upper_95, model
    """
    q = (f"SELECT YEAR(basis_period_end), MONTH(basis_period_end), "
         f"YEAR(target_period_end), MONTH(target_period_end), "
         f"value, lower_95, upper_95, model FROM {T_FC} WHERE ticker = :t")
    args = {"t": str(company_id)}
    if basis:
        q += " AND basis_period_end = :b"
        args["b"] = _period_end(basis[0], basis[1])
    if model:
        q += " AND model = :m"
        args["m"] = model
    q += " ORDER BY basis_period_end, target_period_end"
    with conn.connect() as cx:
        return [tuple(r) for r in cx.execute(text(q), args)]


def latest_basis(conn, company_id):
    """가장 최근 실적의 (year, month). 없으면 None."""
    q = text(f"SELECT MAX(period_end) FROM {T_REV} "
             f"WHERE ticker = :t AND revenue IS NOT NULL")
    with conn.connect() as cx:
        pe = cx.execute(q, {"t": str(company_id)}).scalar()
    return (pe.year, pe.month) if pe else None


def _parse_legacy_ts(s):
    """구 SQLite의 UTC ISO 문자열 → KST naive datetime. 실패 시 None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).replace(tzinfo=None, microsecond=0)


def migrate_from_sqlite(conn, sqlite_path=None):
    """구 revenue.db(SQLite)의 revenue/forecast를 investar 표준 테이블로 이관.

    - SQLite 파일은 읽기만 한다 (수정/삭제 없음).
    - 원래 수집 시각(collected_at, created_at)을 KST로 바꿔 보존한다.
      주의: 구 collected_at은 '마지막 갱신 시각'이므로 first_collected_at은 근사치다.
    - 여러 번 실행해도 결과가 같다 (UPSERT).
    """
    import sqlite3
    from collections import defaultdict

    path = Path(sqlite_path) if sqlite_path else LEGACY_SQLITE_PATH
    if not path.exists():
        print(f"[migrate] SQLite 파일이 없습니다: {path}")
        return
    print(f"[migrate] 원본: {path}")
    sc = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in sc.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

        # 실적
        n_rev = 0
        if "revenue" in tables:
            groups = defaultdict(list)
            for (cid, nm, y, m, rv, mom, yoy, src, cat) in sc.execute(
                    "SELECT company_id, company_name, year, month, revenue, "
                    "mom_pct, yoy_pct, source, collected_at FROM revenue"):
                groups[_parse_legacy_ts(cat)].append({
                    "company_id": cid, "company_name": nm, "year": y, "month": m,
                    "revenue": rv, "mom_pct": mom, "yoy_pct": yoy, "source": src})
            for ts in sorted(groups, key=lambda x: (x is None, x)):
                upsert_revenue(conn, groups[ts], collected_at=ts)
                n_rev += len(groups[ts])
        print(f"[migrate] 실적 {n_rev}행 처리")

        # 예측
        n_fc = 0
        if "forecast" in tables:
            cols = {r[1] for r in sc.execute("PRAGMA table_info(forecast)")}
            model_expr = "model" if "model" in cols else "'sarima'"
            groups = defaultdict(list)
            for (cid, md, by, bm, ty, tm, pr, lo, hi, cat) in sc.execute(
                    f"SELECT company_id, {model_expr}, basis_year, basis_month, "
                    f"target_year, target_month, predicted, lower_95, upper_95, "
                    f"created_at FROM forecast"):
                groups[_parse_legacy_ts(cat)].append({
                    "company_id": cid, "model": md, "basis_year": by,
                    "basis_month": bm, "target_year": ty, "target_month": tm,
                    "predicted": pr, "lower_95": lo, "upper_95": hi, "engine": None})
            for ts in sorted(groups, key=lambda x: (x is None, x)):
                upsert_forecast(conn, groups[ts], forecast_at=ts)
                n_fc += len(groups[ts])
        print(f"[migrate] 예측 {n_fc}행 처리")
    finally:
        sc.close()

    # 검증: 건수 비교
    with conn.connect() as cx:
        db_rev = cx.execute(text(f"SELECT COUNT(*) FROM {T_REV}")).scalar()
        db_fc = cx.execute(text(f"SELECT COUNT(*) FROM {T_FC}")).scalar()
    print(f"[migrate] investar 현재 건수: {T_REV}={db_rev}, {T_FC}={db_fc}")
    print("[migrate] 완료. revenue.db는 그대로 두었습니다 (확인 후 보관/삭제는 직접 결정).")


# ======================================================================
# === collector.py ===
# ======================================================================
"""대만 상장사 월별 매출 수집기.

데이터 소스
1) MOPS 월별 매출 집계 정적 페이지 (과거 이력 수집용)
   https://mops.twse.com.tw/nas/t21/sii/t21sc03_{민국연도}_{월}_0.html
   - 연도는 민국(서기-1911), 인코딩은 Big5
2) TWSE OpenAPI t187ap05_L (최신 월 JSON, 증분 수집용 보조 소스)
"""
import io
import time

import pandas as pd
import requests



def _decode(content: bytes) -> str:
    """MOPS 페이지 인코딩 자동 감지: big5 → utf-8 → cp950 순으로 시도.
    한자 마커가 정상적으로 보이는 디코딩을 채택한다."""
    for enc in ("big5", "utf-8", "cp950", "utf-8-sig"):
        try:
            text = content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if ("公司代號" in text) or ("營收" in text) or ("公司" in text):
            return text
    return content.decode("big5", errors="ignore")


def _find_col(columns, keyword):
    """평탄화된 컬럼명 중 keyword를 포함하는 첫 컬럼을 찾는다."""
    for c in columns:
        if keyword in c:
            return c
    return None


def _flatten_columns(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["_".join(str(x) for x in tup if str(x) != "nan")
                      for tup in df.columns]
    else:
        df.columns = [str(c) for c in df.columns]
    return df


def _to_float(v):
    try:
        s = str(v).replace(",", "").replace("%", "").strip()
        if s in ("", "-", "nan", "None"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_mops_month(year: int, month: int, market: str = MARKET):
    """특정 (서기)연/월의 전 상장사 월 매출 표를 가져와 dict 리스트로 반환.

    페이지가 아직 없으면(미발표 등) None 반환.
    """
    roc_year = year - 1911
    html = None
    for tpl in MOPS_URL_TEMPLATES:
        url = tpl.format(market=market, roc_year=roc_year, month=month)
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"\n  [warn] 요청 실패 {url}: {e}")
            continue
        if r.status_code == 200 and len(r.content) > 5000:
            html = _decode(r.content)
            break
        else:
            # 원인을 알 수 있도록 상태를 출력 (404=페이지 없음, 403=차단 가능성)
            print(f"\n  [info] HTTP {r.status_code}, {len(r.content)} bytes ← {url}")
    if html is None:
        return None

    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return None

    # 1차: 헤더 이름(공司代號/當月營收)으로 파싱
    rows = []
    for df in tables:
        df = _flatten_columns(df)
        code_col = _find_col(df.columns, "公司代號")
        rev_col = _find_col(df.columns, "當月營收")
        if not code_col or not rev_col:
            continue
        name_col = _find_col(df.columns, "公司名稱")
        mom_col = _find_col(df.columns, "上月比較增減")
        yoy_col = _find_col(df.columns, "去年同月增減")

        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            if not code.isdigit():  # 소계/합계 행 제거
                continue
            rev = _to_float(row[rev_col])
            rows.append({
                "company_id": code,
                "company_name": str(row[name_col]).strip() if name_col else None,
                "year": year,
                "month": month,
                "revenue": int(rev) if rev is not None else None,
                "mom_pct": _to_float(row[mom_col]) if mom_col else None,
                "yoy_pct": _to_float(row[yoy_col]) if yoy_col else None,
                "source": "mops",
                "market": market,
            })
    if rows:
        return rows

    # 2차 폴백: 헤더가 깨졌어도 표준 열 배치로 파싱
    # (0:公司代號, 1:公司名稱, 2:當月營收, 3:上月營收, 4:去年當月營收,
    #  5:上月比較增減%, 6:去年同月增減%, ...)
    for df in tables:
        if df.shape[1] < 7:
            continue
        col0 = df.iloc[:, 0].astype(str).str.strip()
        mask = col0.str.fullmatch(r"\d{4,6}")
        if not mask.any():
            continue
        sub = df[mask.values]
        for _, row in sub.iterrows():
            rev = _to_float(row.iloc[2])
            rows.append({
                "company_id": str(row.iloc[0]).strip(),
                "company_name": str(row.iloc[1]).strip(),
                "year": year,
                "month": month,
                "revenue": int(rev) if rev is not None else None,
                "mom_pct": _to_float(row.iloc[5]),
                "yoy_pct": _to_float(row.iloc[6]),
                "source": "mops",
                "market": market,
            })
    if rows:
        return rows

    # 둘 다 실패 → 원인 파악을 위한 진단 출력
    cols = list(map(str, tables[0].columns))[:8] if tables else []
    print(f"\n  [parse] 표 {len(tables)}개 발견, 파싱 매칭 실패. "
          f"첫 표 컬럼 예시: {cols}")
    return None


def _fetch_openapi_one(url: str, source: str, retries: int = 3):
    """단일 OpenAPI 엔드포인트에서 최신 월 매출(JSON)을 가져와 파싱. 실패 시 None.

    TPEx OpenAPI는 전송 중 연결이 끊기는(IncompleteRead) 경우가 있어
    최대 retries회 재시도하며, 2회차부터는 gzip 압축을 끄고(Accept-Encoding:
    identity) 연결을 매 요청마다 닫아(Connection: close) 안정성을 높인다.
    """
    data = None
    for attempt in range(1, retries + 1):
        headers = dict(REQUEST_HEADERS)
        if attempt > 1:
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
        try:
            r = requests.get(url, headers=headers, timeout=60)
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            print(f"  [warn] OpenAPI 실패 ({source}, {attempt}/{retries}회차): {e}")
            if attempt < retries:
                time.sleep(2)
    if data is None:
        return None

    rows = []
    for item in data:
        # 필드명이 바뀔 수 있어 부분 일치로 방어적으로 탐색
        def pick(kw):
            for k, v in item.items():
                if kw in k:
                    return v
            return None

        code = str(pick("公司代號") or "").strip()
        ym = str(pick("資料年月") or "").strip()  # 예: '11506' (민국 115년 6월)
        if not code.isdigit() or len(ym) < 4:
            continue
        try:
            year = int(ym[:-2]) + 1911
            month = int(ym[-2:])
        except ValueError:
            continue
        rev = _to_float(pick("當月營收"))
        rows.append({
            "company_id": code,
            "company_name": str(pick("公司名稱") or "").strip() or None,
            "year": year,
            "month": month,
            "revenue": int(rev) if rev is not None else None,
            "mom_pct": _to_float(pick("上月比較增減")),
            "yoy_pct": _to_float(pick("去年同月增減")),
            "source": source,
            "market": "otc" if "tpex" in source else "sii",
        })
    return rows or None


def fetch_openapi_latest():
    """TWSE(상장) + TPEx(상궤) OpenAPI에서 최신 월 매출을 모두 가져온다.

    둘 중 하나만 성공해도 그 결과를 반환하고, 둘 다 실패하면 None.
    """
    rows = []
    for url, source in ((TWSE_OPENAPI_MONTHLY, "openapi"),
                        (TPEX_OPENAPI_MONTHLY, "openapi-tpex")):
        got = _fetch_openapi_one(url, source)
        if got:
            rows.extend(got)
    return rows or None


def _month_range(start, end):
    """('YYYY-MM','YYYY-MM') → [(y,m), ...]"""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def collect_range(conn, start: str, end: str, companies=None):
    """기간 내 MOPS 페이지를 순회하며 대상 기업 매출을 DB에 저장."""
    targets = set(companies or COMPANIES.keys())

    for (y, m) in _month_range(start, end):
        print(f"[collect] {y}-{m:02d} ...", end=" ")
        rows = []
        for market in MARKETS:  # 상장(sii) + 상궤(otc) 모두 수집
            got = fetch_mops_month(y, m, market=market)
            if got:
                rows.extend(got)
            time.sleep(REQUEST_DELAY_SEC)
        if not rows:
            print("데이터 없음(미발표이거나 페이지 없음)")
        else:
            picked = [r for r in rows if r["company_id"] in targets]
            # 표시용 이름을 영문으로 통일
            for r in picked:
                r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
            upsert_revenue(conn, picked)
            print(f"{len(picked)}개 기업 저장")


def collect_latest(conn, companies=None):
    """OpenAPI로 최신 월 증분 수집. 실패한 시장만 MOPS 페이지로 폴백.

    TWSE(상장)와 TPEx(상궤)를 각각 처리하므로, 예컨대 TPEx OpenAPI만
    실패해도 8299 등 상궤 종목이 조용히 누락되지 않고 MOPS otc
    페이지에서 지난달 데이터를 보충 수집한다.
    """
    from datetime import date
    targets = set(companies or COMPANIES.keys())

    def _save(rows, label):
        picked = [r for r in rows if r["company_id"] in targets]
        for r in picked:
            r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
        if picked:
            upsert_revenue(conn, picked)
            got = {(r['year'], r['month']) for r in picked}
            print(f"[collect-latest] {label}에서 {len(picked)}건 저장 "
                  f"(연월: {sorted(got)})")
        return len(picked)

    failed_markets = []
    for url, source, market in ((TWSE_OPENAPI_MONTHLY, "openapi", "sii"),
                                (TPEX_OPENAPI_MONTHLY, "openapi-tpex", "otc")):
        rows = _fetch_openapi_one(url, source)
        if rows:
            _save(rows, source)
        else:
            failed_markets.append(market)

    if not failed_markets:
        return

    # 폴백: 실패한 시장만 지난달 MOPS 페이지 시도
    today = date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    for market in failed_markets:
        print(f"[collect-latest] {market} OpenAPI 실패 → MOPS {market} 폴백 ({y}-{m:02d})")
        rows = fetch_mops_month(y, m, market=market)
        if rows:
            _save(rows, f"mops-{market}")
        else:
            print(f"[collect-latest] MOPS {market} 폴백도 실패 (미발표이거나 페이지 없음)")
        time.sleep(REQUEST_DELAY_SEC)


# ======================================================================
# === forecaster.py ===
# ======================================================================
"""월 매출 예측: SARIMA / Theta / ETS / Ensemble.

예측 엔진 우선순위
1) 사용자 모듈: DATA 폴더의 universal_ts_forecast_function
   (forecast_sarima / forecast_ets / forecast_theta, try_transforms=True)
   → Korea_revenue_forecast_v2 노트북과 동일한 방식. 월 데이터이므로 계절주기 12 사용.
2) 내장 엔진: statsmodels (사용자 모듈이 없거나 개별 모델 실패 시 폴백)

Ensemble은 세 모델 예측치의 단순 평균 (노트북과 동일).
"""
import warnings

import numpy as np
import pandas as pd


MODELS = ("sarima", "theta", "ets", "ensemble")
SEASONAL_M = 12  # 월 데이터 계절 주기

# ---------------- 사용자 예측 모듈 로딩 (1회) ----------------
_USER_FNS = None


def _load_user_module():
    """DATA 폴더의 universal_ts_forecast_function을 시도 로드."""
    global _USER_FNS
    if _USER_FNS is not None:
        return _USER_FNS
    _USER_FNS = {}
    try:
        setup_universal_paths(quiet=True)
        from universal_ts_forecast_function import (forecast_ets,
                                                    forecast_sarima,
                                                    forecast_theta)
        _USER_FNS = {"sarima": forecast_sarima, "ets": forecast_ets,
                     "theta": forecast_theta}
        print("[forecast] 예측 엔진: DATA/universal_ts_forecast_function (사용자 모듈)")
    except Exception as e:
        print(f"[forecast] 사용자 모듈 로드 실패({type(e).__name__}: {e}) "
              f"→ 내장 statsmodels 엔진 사용")
    return _USER_FNS


# ---------------- 사용자 모듈 래퍼 ----------------
def _user_fit(kind, fn, series, horizon):
    """사용자 함수 호출. series는 PeriodIndex(M) Series (노트북과 동일 패턴).
    반환: (mean, lo, hi) — CI가 없으면 lo/hi는 NaN 배열."""
    kwargs = dict(y=series, forecast_horizon=horizon, try_transforms=True)
    if kind == "sarima":
        kwargs["seasonal_period"] = SEASONAL_M
    else:
        kwargs["m"] = SEASONAL_M
    res = fn(**kwargs)

    mean = np.asarray(res.get("forecast"), dtype=float)[:horizon]
    lo = hi = np.full(horizon, np.nan)
    if isinstance(res, dict):
        for lk, hk in (("lower", "upper"), ("lower_95", "upper_95"),
                       ("lo", "hi"), ("pi_lower", "pi_upper")):
            if res.get(lk) is not None and res.get(hk) is not None:
                lo = np.asarray(res[lk], dtype=float)[:horizon]
                hi = np.asarray(res[hk], dtype=float)[:horizon]
                break
        else:
            ci = res.get("conf_int")
            if ci is not None:
                ci = pd.DataFrame(ci)
                lo = ci.iloc[:horizon, 0].to_numpy(dtype=float)
                hi = ci.iloc[:horizon, 1].to_numpy(dtype=float)
    return mean, lo, hi


# ---------------- 내장(statsmodels) 엔진 ----------------
def _builtin_sarima(y, horizon):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    model = SARIMAX(y, order=SARIMA_ORDER, seasonal_order=SARIMA_SEASONAL_ORDER,
                    enforce_stationarity=False, enforce_invertibility=False)
    res = model.fit(disp=False)
    fc = res.get_forecast(steps=horizon)
    ci = fc.conf_int(alpha=0.05)
    return (fc.predicted_mean.to_numpy(), ci.iloc[:, 0].to_numpy(),
            ci.iloc[:, 1].to_numpy())


def _builtin_ets(y, horizon):
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    model = ETSModel(y, error="add", trend="add", seasonal="add",
                     seasonal_periods=SEASONAL_M)
    res = model.fit(disp=False)
    pred = res.get_prediction(start=len(y), end=len(y) + horizon - 1)
    sf = pred.summary_frame(alpha=0.05)
    return (sf["mean"].to_numpy(), sf["pi_lower"].to_numpy(),
            sf["pi_upper"].to_numpy())


def _builtin_theta(y, horizon):
    from statsmodels.tsa.forecasting.theta import ThetaModel
    method = "multiplicative" if (y > 0).all() else "additive"
    model = ThetaModel(y, period=SEASONAL_M, method=method)
    res = model.fit()
    mean = res.forecast(horizon)
    pi = res.prediction_intervals(horizon, alpha=0.05)
    return (mean.to_numpy(), pi.iloc[:, 0].to_numpy(), pi.iloc[:, 1].to_numpy())


_BUILTIN = {"sarima": _builtin_sarima, "ets": _builtin_ets,
            "theta": _builtin_theta}


def _builtin_fit(kind, vals, horizon):
    """내장 엔진: 전부 양수면 로그 변환, 0 이하 포함이면 원 스케일."""
    use_log = bool((vals > 0).all())
    y = np.log(vals) if use_log else vals
    mean, lo, hi = _BUILTIN[kind](y, horizon)
    if use_log:
        mean, lo, hi = np.exp(mean), np.exp(lo), np.exp(hi)
    return mean, lo, hi


# ---------------- 메인 파이프라인 ----------------
def _series_to_frame(rows):
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df = df.set_index("date").asfreq("MS")
    return df


def forecast_company(conn, company_id: str, horizon: int = 6):
    """SARIMA/Theta/ETS/Ensemble 예측 후 모두 DB 저장."""
    rows = load_revenue_series(conn, company_id)
    if len(rows) < MIN_OBS_FOR_FORECAST:
        print(f"[forecast] {company_id}: 관측치 {len(rows)}개 "
              f"(최소 {MIN_OBS_FOR_FORECAST}개 필요) → 건너뜀")
        return None

    df = _series_to_frame(rows)
    vals = df["revenue"].astype(float).interpolate(limit_direction="both")
    # 사용자 모듈용: 노트북과 동일하게 PeriodIndex Series로 전달
    per_series = pd.Series(vals.to_numpy(),
                           index=pd.PeriodIndex(vals.index, freq="M"))

    user_fns = _load_user_module()
    future_idx = pd.date_range(vals.index[-1] + pd.offsets.MonthBegin(1),
                               periods=horizon, freq="MS")
    basis_year, basis_month = int(rows[-1][0]), int(rows[-1][1])

    results, engines = {}, {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in ("sarima", "theta", "ets"):
            # 1순위: 사용자 모듈
            if name in user_fns:
                try:
                    results[name] = _user_fit(name, user_fns[name],
                                              per_series, horizon)
                    engines[name] = "user"
                    continue
                except Exception as e:
                    print(f"[forecast] {company_id} {name} 사용자 모듈 실패({e}) "
                          f"→ 내장 엔진 폴백")
            # 2순위: 내장 엔진
            try:
                results[name] = _builtin_fit(name, vals, horizon)
                engines[name] = "builtin"
            except Exception as e:
                print(f"[forecast] {company_id} {name} 실패: {e}")

    if not results:
        return None

    # Ensemble = 세 모델 예측의 단순 평균 (노트북과 동일)
    def _nmean(arrs):
        stack = np.vstack(arrs)
        out = np.full(stack.shape[1], np.nan)
        valid = ~np.all(np.isnan(stack), axis=0)
        if valid.any():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out[valid] = np.nanmean(stack[:, valid], axis=0)
        return out

    means = _nmean([r[0] for r in results.values()])
    los = _nmean([r[1] for r in results.values()])
    his = _nmean([r[2] for r in results.values()])
    results["ensemble"] = (means, los, his)
    engines["ensemble"] = "mean"

    out = []
    for model_name, (mean, lo, hi) in results.items():
        for i, ts in enumerate(future_idx):
            def _v(a):
                x = float(a[i])
                return None if np.isnan(x) else x
            out.append({
                "company_id": company_id,
                "model": model_name,
                "basis_year": basis_year,
                "basis_month": basis_month,
                "target_year": int(ts.year),
                "target_month": int(ts.month),
                "predicted": _v(mean),
                "lower_95": _v(lo),
                "upper_95": _v(hi),
                "engine": engines.get(model_name),
            })
    upsert_forecast(conn, out)
    name = COMPANIES.get(company_id, company_id)
    tag = ", ".join(f"{m}:{engines[m]}" for m in MODELS if m in results)
    print(f"[forecast] {name}({company_id}): basis={basis_year}-{basis_month:02d}, "
          f"{horizon}개월 저장 [{tag}]")
    return out


def forecast_all(conn, horizon: int = 6, companies=None):
    for cid in (companies or COMPANIES.keys()):
        try:
            forecast_company(conn, cid, horizon)
        except Exception as e:
            print(f"[forecast] {cid} 실패: {e}")


# ======================================================================
# === visualizer.py ===
# ======================================================================
"""시계열 시각화 (모델 선택 지원).

기업별로 세 종류의 차트를 PNG로 저장한다.
1) {code}_forecast_{model}.png : 실적 + 선택 모델의 최신 예측(95% CI)
2) {code}_history_{model}.png  : 선택 모델의 과거 basis별 예측 vs 실적
3) {code}_models.png           : 4개 모델의 최신 예측 비교
기본 모델은 'ensemble'.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


MODEL_COLORS = {"sarima": "#d62728", "theta": "#2ca02c",
                "ets": "#9467bd", "ensemble": "#ff7f0e"}
FC_COLS = ["by", "bm", "ty", "tm", "pred", "lo", "hi", "model"]


def _actual_frame(conn, company_id):
    rows = load_revenue_series(conn, company_id)
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df["revenue_b"] = df["revenue"] / 1e6  # NTD 천 → NTD 십억(billion)
    return df


def _fc_frame(fc):
    f = pd.DataFrame(fc, columns=FC_COLS)
    if f.empty:
        return f
    f["date"] = pd.to_datetime(dict(year=f.ty, month=f.tm, day=1))
    for c in ("pred", "lo", "hi"):
        f[c] = pd.to_numeric(f[c], errors="coerce") / 1e6  # CI 없으면 NaN
    return f


def plot_latest_forecast(conn, company_id, model="ensemble"):
    df = _actual_frame(conn, company_id)
    if df.empty:
        return None
    basis = latest_basis(conn, company_id)
    f = _fc_frame(load_forecasts(conn, company_id, basis=basis, model=model))

    name = COMPANIES.get(company_id, company_id)
    color = MODEL_COLORS.get(model, "#d62728")
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4", lw=1.6)

    if not f.empty:
        last = df.iloc[-1]
        ax.plot([last["date"], *f["date"]], [last["revenue_b"], *f["pred"]],
                label=f"Forecast ({model})", color=color, lw=1.6, ls="--",
                marker="o", ms=4)
        if f["lo"].notna().any() and f["hi"].notna().any():
            ax.fill_between(f["date"], f["lo"], f["hi"], color=color, alpha=0.15,
                            label="95% CI")

    ax.set_title(f"{name} ({company_id}) Monthly Revenue & {model.upper()} Forecast")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_forecast_{model}.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_forecast_history(conn, company_id, model="ensemble"):
    """선택 모델의 과거 basis별 예측을 실적 위에 겹쳐 예측 정확도 추적."""
    df = _actual_frame(conn, company_id)
    f = _fc_frame(load_forecasts(conn, company_id, model=model))
    if df.empty or f.empty:
        return None

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4",
            lw=2, zorder=5)

    cmap = plt.get_cmap("autumn")
    bases = sorted(f.groupby(["by", "bm"]).groups.keys())
    for i, (by, bm) in enumerate(bases):
        g = f[(f.by == by) & (f.bm == bm)].sort_values("date")
        c = cmap(i / max(len(bases) - 1, 1) * 0.8)
        ax.plot(g["date"], g["pred"], ls="--", lw=1.1, marker=".", ms=5,
                color=c, alpha=0.85, label=f"@ {by}-{bm:02d}")

    ax.set_title(f"{name} ({company_id}) {model.upper()} Forecast History vs Actual")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_history_{model}.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_model_comparison(conn, company_id):
    """4개 모델의 최신 예측을 한 차트에서 비교."""
    df = _actual_frame(conn, company_id)
    if df.empty:
        return None
    basis = latest_basis(conn, company_id)
    f = _fc_frame(load_forecasts(conn, company_id, basis=basis))
    if f.empty:
        return None

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    tail = df.tail(24)  # 최근 2년 실적 + 예측 비교가 잘 보이도록
    ax.plot(tail["date"], tail["revenue_b"], label="Actual", color="#1f77b4", lw=2)

    last = df.iloc[-1]
    for m, color in MODEL_COLORS.items():
        g = f[f.model == m].sort_values("date")
        if g.empty:
            continue
        lw = 2.2 if m == "ensemble" else 1.2
        ax.plot([last["date"], *g["date"]], [last["revenue_b"], *g["pred"]],
                ls="--", lw=lw, marker="o", ms=4, color=color, label=m)

    ax.set_title(f"{name} ({company_id}) Forecast Model Comparison")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_models.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_all(conn, companies=None, model="ensemble"):
    paths = []
    for cid in (companies or COMPANIES.keys()):
        for p in (plot_latest_forecast(conn, cid, model),
                  plot_forecast_history(conn, cid, model),
                  plot_model_comparison(conn, cid)):
            if p:
                paths.append(p)
                print(f"[plot] {p}")
    return paths


# ======================================================================
# === main.py ===
# ======================================================================
"""CLI 진입점.

사용 예
  python main.py collect --start 2019-01 --end 2026-06   # 과거 이력 일괄 수집
  python main.py latest                                  # 최신 월 증분 수집(OpenAPI→MOPS 폴백)
  python main.py forecast --horizon 6                    # SARIMA 예측 후 DB 저장
  python main.py plot                                    # 차트 PNG 생성 (output/)
  python main.py run --horizon 6                         # latest → forecast → plot 한번에
  python main.py migrate [--sqlite PATH]                 # 구 revenue.db → investar 이관 (최초 1회)
"""
import argparse
from datetime import date



def interactive():
    """인자 없이(더블클릭 등) 실행됐을 때의 대화형 메뉴."""
    from pathlib import Path
    here = Path(__file__).resolve().parent
    print("=" * 60)
    print(" 대만 대표기업 월 매출 수집·예측·시각화")
    print(f" 실행 위치 : {here}")
    print(f" DB        : MariaDB investar ({T_REV}, {T_FC})")
    print(f" 차트      : {OUTPUT_DIR}")
    print("=" * 60)
    conn = get_conn()
    print(f"대상 기업: {', '.join(f'{v}({k})' for k, v in COMPANIES.items())}")

    while True:
        print("""
[1] 접속 진단 (debug)
[2] 과거 데이터 수집 (collect)
[3] 최신 월 수집 (latest)
[4] 예측 (forecast)
[5] 시각화 (plot)
[6] 전체 실행: 최신수집→예측→시각화 (run)
[7] 구 revenue.db → investar 이관 (최초 1회)
[0] 종료""")
        choice = input("번호 선택 > ").strip()
        try:
            if choice == "1":
                _debug(conn, None)
            elif choice == "2":
                start = input("시작 연월 (예: 2019-01) > ").strip() or "2019-01"
                from datetime import date as _d
                t = _d.today()
                y, mo = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
                collect_range(conn, start, f"{y}-{mo:02d}")
            elif choice == "3":
                collect_latest(conn)
            elif choice == "4":
                h = input("예측 개월 수 (기본 6) > ").strip()
                forecast_all(conn, horizon=int(h) if h else 6)
            elif choice == "5":
                mm = input("모델 선택 1)sarima 2)theta 3)ets 4)ensemble 5)전체 (기본 4) > ").strip()
                model = {"1": "sarima", "2": "theta", "3": "ets",
                         "4": "ensemble", "5": "all"}.get(mm, "ensemble")
                if model == "all":
                    for md in ("sarima", "theta", "ets", "ensemble"):
                        plot_all(conn, model=md)
                else:
                    plot_all(conn, model=model)
            elif choice == "6":
                collect_latest(conn)
                forecast_all(conn, horizon=6)
                plot_all(conn)
            elif choice == "7":
                pth = input(f"revenue.db 경로 (Enter = {LEGACY_SQLITE_PATH}) > ").strip().strip('"')
                migrate_from_sqlite(conn, pth or None)
            elif choice == "0":
                break
            else:
                print("0~7 중에서 선택하세요.")
        except Exception as e:
            print(f"\n[오류] {e}\n")
    conn.dispose()
    input("\nEnter를 누르면 창이 닫힙니다...")


def _debug(conn, ym):
    import requests
    if ym is None:
        t = date.today()
        y, mo = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
        ym = f"{y}-{mo:02d}"
    y, mo = map(int, ym.split("-"))
    roc = y - 1911
    print(f"진단 대상: {ym} (민국 {roc}년 {mo}월)\n")
    for market in MARKETS:
        for tpl in MOPS_URL_TEMPLATES:
            url = tpl.format(market=market, roc_year=roc, month=mo)
            try:
                r = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
                head = r.content[:200].decode("big5", "ignore").replace("\n", " ")
                print(f"[{r.status_code}] {len(r.content):>8} bytes  {url}")
                print(f"        본문 앞부분: {head[:120]}\n")
            except Exception as e:
                print(f"[예외] {url}\n        {e}\n")
    for url in (TWSE_OPENAPI_MONTHLY, TPEX_OPENAPI_MONTHLY):
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            print(f"[{r.status_code}] {len(r.content):>8} bytes  {url}\n")
        except Exception as e:
            print(f"[예외] {url}\n        {e}\n")


def main():
    import sys
    if len(sys.argv) == 1:      # 인자 없이 실행(더블클릭 포함) → 대화형 메뉴
        interactive()
        return

    p = argparse.ArgumentParser(description="대만 대표기업 월 매출 수집/예측/시각화")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="MOPS에서 기간 일괄 수집")
    c.add_argument("--start", required=True, help="YYYY-MM")
    c.add_argument("--end", default=None, help="YYYY-MM (기본: 지난달)")

    sub.add_parser("latest", help="최신 월 증분 수집")

    f = sub.add_parser("forecast", help="SARIMA 예측")
    f.add_argument("--horizon", type=int, default=6, help="예측 개월 수")

    pl = sub.add_parser("plot", help="시각화 PNG 생성")
    pl.add_argument("--model", default="ensemble",
                    choices=["sarima", "theta", "ets", "ensemble", "all"],
                    help="시각화할 예측 모델 (기본: ensemble)")

    r = sub.add_parser("run", help="latest → forecast → plot")
    r.add_argument("--horizon", type=int, default=6)

    d = sub.add_parser("debug", help="특정 월 요청 상태 진단")
    d.add_argument("--ym", default=None, help="YYYY-MM (기본: 지난달)")

    mg = sub.add_parser("migrate", help="구 revenue.db(SQLite) → investar 이관")
    mg.add_argument("--sqlite", default=None, help="revenue.db 경로 (기본: 이 파일 폴더)")

    args = p.parse_args()
    conn = get_conn()
    print(f"대상 기업: {', '.join(f'{v}({k})' for k, v in COMPANIES.items())}\n")

    if args.cmd == "collect":
        end = args.end
        if end is None:
            t = date.today()
            y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
            end = f"{y}-{m:02d}"
        collect_range(conn, args.start, end)

    elif args.cmd == "latest":
        collect_latest(conn)

    elif args.cmd == "forecast":
        forecast_all(conn, horizon=args.horizon)

    elif args.cmd == "plot":
        if args.model == "all":
            for md in ("sarima", "theta", "ets", "ensemble"):
                plot_all(conn, model=md)
        else:
            plot_all(conn, model=args.model)

    elif args.cmd == "run":
        collect_latest(conn)
        forecast_all(conn, horizon=args.horizon)
        plot_all(conn)

    elif args.cmd == "debug":
        _debug(conn, args.ym)

    elif args.cmd == "migrate":
        migrate_from_sqlite(conn, args.sqlite)

    conn.dispose()


if __name__ == "__main__":
    main()
