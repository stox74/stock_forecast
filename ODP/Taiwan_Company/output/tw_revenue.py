"""tw_revenue.py — 대만 대표기업 월 매출 수집·예측·시각화 (단일 파일 버전 v2)

v2 변경사항: MOPS 사이트 개편 대응
 - 데이터가 남아있는 구버전 아카이브(mopsov.twse.com.tw)를 우선 조회
 - 브라우저형 요청 헤더 추가 (봇 차단 회피)
 - 실패 시 HTTP 상태를 출력, `debug` 명령으로 상세 진단 가능

사용법:
  python tw_revenue.py debug                     # 접속 진단 (지난달 기준)
  python tw_revenue.py collect --start 2019-01   # 과거 이력 수집
  python tw_revenue.py forecast --horizon 6      # SARIMA 예측
  python tw_revenue.py plot                      # 차트 생성 (output 폴더)
  python tw_revenue.py run                       # 매월: 증분수집 -> 예측 -> 시각화
"""
# ======================================================================
# === config.py ===
# ======================================================================
"""프로젝트 전역 설정."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "revenue.db"
OUTPUT_DIR = BASE_DIR / "output"

# 수집 대상 기업 (종목코드: 표시용 이름)
# 필요 시 자유롭게 추가/삭제하면 됩니다. 코드는 TWSE 상장(sii) 기준.
COMPANIES = {
    "2330": "TSMC",
    "2317": "Hon Hai (Foxconn)",
    "2454": "MediaTek",
    "2308": "Delta Electronics",
    "2382": "Quanta Computer",
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
    basis_year   INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 연
    basis_month  INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 월
    target_year  INTEGER NOT NULL,
    target_month INTEGER NOT NULL,
    predicted    REAL,
    lower_95     REAL,
    upper_95     REAL,
    model        TEXT,
    created_at   TEXT,
    PRIMARY KEY (company_id, basis_year, basis_month, target_year, target_month)
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
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
            (company_id, basis_year, basis_month, target_year, target_month,
             predicted, lower_95, upper_95, model, created_at)
        VALUES
            (:company_id, :basis_year, :basis_month, :target_year, :target_month,
             :predicted, :lower_95, :upper_95, :model, :created_at)
        ON CONFLICT (company_id, basis_year, basis_month, target_year, target_month)
        DO UPDATE SET
            predicted  = excluded.predicted,
            lower_95   = excluded.lower_95,
            upper_95   = excluded.upper_95,
            model      = excluded.model,
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


def load_forecasts(conn, company_id, basis=None):
    """예측 이력 조회. basis=(year, month) 지정 시 해당 시점 예측만."""
    q = ("SELECT basis_year, basis_month, target_year, target_month, "
         "predicted, lower_95, upper_95 FROM forecast WHERE company_id = ?")
    args = [company_id]
    if basis:
        q += " AND basis_year = ? AND basis_month = ?"
        args += list(basis)
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
            r.encoding = "big5"
            html = r.text
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
    return rows or None


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
"""SARIMA 기반 월 매출 예측.

- 매출은 로그 변환 후 SARIMA(1,1,1)(1,1,1,12) 적합 (config에서 조정 가능)
- 예측 결과는 basis(예측에 사용한 마지막 실적 연월)와 함께 forecast 테이블에 저장
  → 매월 예측을 다시 돌려도 과거 예측 이력이 보존되어 실적과 비교 가능
"""
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX



def _series_to_frame(rows):
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df = df.set_index("date").asfreq("MS")  # 결측월은 NaN으로
    return df


def forecast_company(conn, company_id: str, horizon: int = 6):
    rows = load_revenue_series(conn, company_id)
    if len(rows) < MIN_OBS_FOR_FORECAST:
        print(f"[forecast] {company_id}: 관측치 {len(rows)}개 "
              f"(최소 {MIN_OBS_FOR_FORECAST}개 필요) → 건너뜀")
        return None

    df = _series_to_frame(rows)
    y = np.log(df["revenue"].astype(float))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            y,
            order=SARIMA_ORDER,
            seasonal_order=SARIMA_SEASONAL_ORDER,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        result = model.fit(disp=False)

    fc = result.get_forecast(steps=horizon)
    mean = np.exp(fc.predicted_mean)
    ci = np.exp(fc.conf_int(alpha=0.05))

    basis_year, basis_month = int(rows[-1][0]), int(rows[-1][1])
    model_tag = f"SARIMA{SARIMA_ORDER}x{SARIMA_SEASONAL_ORDER}-log"

    out = []
    for ts, pred in mean.items():
        lo, hi = ci.loc[ts].iloc[0], ci.loc[ts].iloc[1]
        out.append({
            "company_id": company_id,
            "basis_year": basis_year,
            "basis_month": basis_month,
            "target_year": int(ts.year),
            "target_month": int(ts.month),
            "predicted": float(pred),
            "lower_95": float(lo),
            "upper_95": float(hi),
            "model": model_tag,
        })
    upsert_forecast(conn, out)
    name = COMPANIES.get(company_id, company_id)
    print(f"[forecast] {name}({company_id}): basis={basis_year}-{basis_month:02d}, "
          f"{horizon}개월 예측 저장")
    return out


def forecast_all(conn, horizon: int = 6, companies=None):
    for cid in (companies or COMPANIES.keys()):
        try:
            forecast_company(conn, cid, horizon)
        except Exception as e:  # 한 종목 실패가 전체를 막지 않도록
            print(f"[forecast] {cid} 실패: {e}")


# ======================================================================
# === visualizer.py ===
# ======================================================================
"""시계열 시각화.

기업별로 두 종류의 차트를 PNG로 저장한다.
1) {code}_forecast.png : 실적 + 최신 예측(95% 신뢰구간 밴드)
2) {code}_history.png  : 과거 각 basis 시점의 예측들을 실적 위에 겹쳐
                         '매월 예측이 실적을 얼마나 맞췄는지' 추적
"""
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd



def _actual_frame(conn, company_id):
    rows = load_revenue_series(conn, company_id)
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df["revenue_b"] = df["revenue"] / 1e6  # NTD 천 → NTD 십억(billion)
    return df


def plot_latest_forecast(conn, company_id):
    df = _actual_frame(conn, company_id)
    if df.empty:
        return None
    basis = latest_basis(conn, company_id)
    fc = load_forecasts(conn, company_id, basis=basis)

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4", lw=1.6)

    if fc:
        f = pd.DataFrame(fc, columns=["by", "bm", "ty", "tm", "pred", "lo", "hi"])
        f["date"] = pd.to_datetime(dict(year=f.ty, month=f.tm, day=1))
        for c in ("pred", "lo", "hi"):
            f[c] = f[c] / 1e6
        # 실적 마지막 점과 예측 첫 점을 이어 그리기
        last = df.iloc[-1]
        xs = [last["date"]] + list(f["date"])
        ys = [last["revenue_b"]] + list(f["pred"])
        ax.plot(xs, ys, label="Forecast", color="#d62728", lw=1.6, ls="--", marker="o", ms=4)
        ax.fill_between(f["date"], f["lo"], f["hi"], color="#d62728", alpha=0.15,
                        label="95% CI")

    ax.set_title(f"{name} ({company_id}) Monthly Revenue & SARIMA Forecast")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_forecast.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_forecast_history(conn, company_id):
    """과거 basis별 예측을 실적 위에 겹쳐 예측 정확도를 시각적으로 추적."""
    df = _actual_frame(conn, company_id)
    fc = load_forecasts(conn, company_id)
    if df.empty or not fc:
        return None

    f = pd.DataFrame(fc, columns=["by", "bm", "ty", "tm", "pred", "lo", "hi"])
    f["date"] = pd.to_datetime(dict(year=f.ty, month=f.tm, day=1))
    f["pred_b"] = f["pred"] / 1e6

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4", lw=2, zorder=5)

    cmap = plt.get_cmap("autumn")
    bases = sorted(f.groupby(["by", "bm"]).groups.keys())
    for i, (by, bm) in enumerate(bases):
        g = f[(f.by == by) & (f.bm == bm)].sort_values("date")
        color = cmap(i / max(len(bases) - 1, 1) * 0.8)
        ax.plot(g["date"], g["pred_b"], ls="--", lw=1.1, marker=".", ms=5,
                color=color, alpha=0.85, label=f"Forecast @ {by}-{bm:02d}")

    ax.set_title(f"{name} ({company_id}) Forecast History vs Actual")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_history.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_all(conn, companies=None):
    paths = []
    for cid in (companies or COMPANIES.keys()):
        for fn in (plot_latest_forecast, plot_forecast_history):
            p = fn(conn, cid)
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



def main():
    p = argparse.ArgumentParser(description="대만 대표기업 월 매출 수집/예측/시각화")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="MOPS에서 기간 일괄 수집")
    c.add_argument("--start", required=True, help="YYYY-MM")
    c.add_argument("--end", default=None, help="YYYY-MM (기본: 지난달)")

    sub.add_parser("latest", help="최신 월 증분 수집")

    f = sub.add_parser("forecast", help="SARIMA 예측")
    f.add_argument("--horizon", type=int, default=6, help="예측 개월 수")

    sub.add_parser("plot", help="시각화 PNG 생성")

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
        plot_all(conn)

    elif args.cmd == "run":
        collect_latest(conn)
        forecast_all(conn, horizon=args.horizon)
        plot_all(conn)

    elif args.cmd == "debug":
        import requests
        ym = args.ym
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

    conn.close()


if __name__ == "__main__":
    main()
