"""tw_revenue.py — 대만 대표기업 월 매출 수집·예측·시각화 (단일 파일 v6)

★ 사용법: 더블클릭 → 메뉴 선택. DB/차트는 이 파일과 같은 폴더에 생성.

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
            "statsmodels": "statsmodels", "matplotlib": "matplotlib"}
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
DB_PATH = BASE_DIR / "revenue.db"
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

MARKET = "sii"  # sii=상장, otc=상궤(店頭), rotc=흥궤

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
# === db.py ===
# ======================================================================
"""SQLite 저장 계층.

테이블 구조
- revenue  : 실제 발표된 월 매출 (종목, 연, 월 기준 UPSERT)
- forecast : 예측 이력. '어느 시점(basis)의 데이터로 어느 달(target)을 예측했는가'를
             함께 저장해 매월 예측 기록을 추적할 수 있게 한다.
"""
import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue (
    company_id   TEXT NOT NULL,
    company_name TEXT,
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    revenue      INTEGER,           -- 단위: NTD 천 (MOPS 원본 단위)
    mom_pct      REAL,              -- 전월 대비 %
    yoy_pct      REAL,              -- 전년 동월 대비 %
    source       TEXT,              -- 'mops' | 'openapi'
    collected_at TEXT,
    PRIMARY KEY (company_id, year, month)
);

CREATE TABLE IF NOT EXISTS forecast (
    company_id   TEXT NOT NULL,
    model        TEXT NOT NULL,     -- sarima | theta | ets | ensemble
    basis_year   INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 연
    basis_month  INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 월
    target_year  INTEGER NOT NULL,
    target_month INTEGER NOT NULL,
    predicted    REAL,
    lower_95     REAL,
    upper_95     REAL,
    created_at   TEXT,
    PRIMARY KEY (company_id, model, basis_year, basis_month, target_year, target_month)
);
"""


def _migrate(conn):
    """구버전 forecast 테이블(PK에 model 미포함)을 새 스키마로 이전."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='forecast'"
    ).fetchone()
    if row and "PRIMARY KEY (company_id, model" not in row[0]:
        conn.execute("ALTER TABLE forecast RENAME TO forecast_v1")
        conn.executescript(SCHEMA)
        conn.execute("""
            INSERT OR IGNORE INTO forecast
                (company_id, model, basis_year, basis_month,
                 target_year, target_month, predicted, lower_95, upper_95, created_at)
            SELECT company_id, 'sarima', basis_year, basis_month,
                   target_year, target_month, predicted, lower_95, upper_95, created_at
            FROM forecast_v1
        """)
        conn.execute("DROP TABLE forecast_v1")
        conn.commit()
        print("[db] 기존 예측 데이터를 새 스키마로 이전했습니다 (구 SARIMA 예측 유지).")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_revenue(conn, rows):
    """rows: dict 리스트 (company_id, company_name, year, month, revenue, mom_pct, yoy_pct, source)"""
    now = _now()
    conn.executemany(
        """
        INSERT INTO revenue
            (company_id, company_name, year, month, revenue, mom_pct, yoy_pct, source, collected_at)
        VALUES
            (:company_id, :company_name, :year, :month, :revenue, :mom_pct, :yoy_pct, :source, :collected_at)
        ON CONFLICT (company_id, year, month) DO UPDATE SET
            company_name = excluded.company_name,
            revenue      = excluded.revenue,
            mom_pct      = excluded.mom_pct,
            yoy_pct      = excluded.yoy_pct,
            source       = excluded.source,
            collected_at = excluded.collected_at
        """,
        [{**r, "collected_at": now} for r in rows],
    )
    conn.commit()


def upsert_forecast(conn, rows):
    now = _now()
    conn.executemany(
        """
        INSERT INTO forecast
            (company_id, model, basis_year, basis_month, target_year, target_month,
             predicted, lower_95, upper_95, created_at)
        VALUES
            (:company_id, :model, :basis_year, :basis_month, :target_year, :target_month,
             :predicted, :lower_95, :upper_95, :created_at)
        ON CONFLICT (company_id, model, basis_year, basis_month, target_year, target_month)
        DO UPDATE SET
            predicted  = excluded.predicted,
            lower_95   = excluded.lower_95,
            upper_95   = excluded.upper_95,
            created_at = excluded.created_at
        """,
        [{**r, "created_at": now} for r in rows],
    )
    conn.commit()


def load_revenue_series(conn, company_id):
    """특정 기업의 월 매출을 (year, month, revenue) 오름차순으로 반환."""
    cur = conn.execute(
        "SELECT year, month, revenue FROM revenue "
        "WHERE company_id = ? AND revenue IS NOT NULL ORDER BY year, month",
        (company_id,),
    )
    return cur.fetchall()


def load_forecasts(conn, company_id, basis=None, model=None):
    """예측 이력 조회. basis=(year, month), model('sarima'|'theta'|'ets'|'ensemble') 필터 가능."""
    q = ("SELECT basis_year, basis_month, target_year, target_month, "
         "predicted, lower_95, upper_95, model FROM forecast WHERE company_id = ?")
    args = [company_id]
    if basis:
        q += " AND basis_year = ? AND basis_month = ?"
        args += list(basis)
    if model:
        q += " AND model = ?"
        args.append(model)
    q += " ORDER BY basis_year, basis_month, target_year, target_month"
    return conn.execute(q, args).fetchall()


def latest_basis(conn, company_id):
    """가장 최근 실적의 (year, month)."""
    row = conn.execute(
        "SELECT year, month FROM revenue WHERE company_id = ? "
        "ORDER BY year DESC, month DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row


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
            })
    if rows:
        return rows

    # 둘 다 실패 → 원인 파악을 위한 진단 출력
    cols = list(map(str, tables[0].columns))[:8] if tables else []
    print(f"\n  [parse] 표 {len(tables)}개 발견, 파싱 매칭 실패. "
          f"첫 표 컬럼 예시: {cols}")
    return None


def fetch_openapi_latest():
    """TWSE OpenAPI에서 최신 월 매출(JSON)을 가져온다. 실패 시 None."""
    try:
        r = requests.get(TWSE_OPENAPI_MONTHLY, headers=REQUEST_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [warn] OpenAPI 실패: {e}")
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
            "source": "openapi",
        })
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
        rows = fetch_mops_month(y, m)
        if not rows:
            print("데이터 없음(미발표이거나 페이지 없음)")
        else:
            picked = [r for r in rows if r["company_id"] in targets]
            # 표시용 이름을 영문으로 통일
            for r in picked:
                r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
            upsert_revenue(conn, picked)
            print(f"{len(picked)}개 기업 저장")
        time.sleep(REQUEST_DELAY_SEC)


def collect_latest(conn, companies=None):
    """OpenAPI로 최신 월 증분 수집. 실패 시 MOPS로 폴백."""
    from datetime import date
    targets = set(companies or COMPANIES.keys())

    rows = fetch_openapi_latest()
    if rows:
        picked = [r for r in rows if r["company_id"] in targets]
        for r in picked:
            r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
        upsert_revenue(conn, picked)
        got = {(r['year'], r['month']) for r in picked}
        print(f"[collect-latest] OpenAPI에서 {len(picked)}건 저장 (연월: {sorted(got)})")
        return

    # 폴백: 지난달 MOPS 페이지 시도
    today = date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    print("[collect-latest] OpenAPI 실패 → MOPS 폴백")
    collect_range(conn, f"{y}-{m:02d}", f"{y}-{m:02d}", companies)


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
    print(f" DB/차트   : 이 폴더 안에 자동 생성됩니다")
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
            elif choice == "0":
                break
            else:
                print("0~6 중에서 선택하세요.")
        except Exception as e:
            print(f"\n[오류] {e}\n")
    conn.close()
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
    for tpl in MOPS_URL_TEMPLATES:
        url = tpl.format(market="sii", roc_year=roc, month=mo)
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            head = r.content[:200].decode("big5", "ignore").replace("\n", " ")
            print(f"[{r.status_code}] {len(r.content):>8} bytes  {url}")
            print(f"        본문 앞부분: {head[:120]}\n")
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

    conn.close()


if __name__ == "__main__":
    main()
