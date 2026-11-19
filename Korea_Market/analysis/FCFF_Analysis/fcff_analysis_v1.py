# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════
 FCFF DCF 결과 분석 모듈 v1  (fcff_analysis_v1.py)
 ───────────────────────────────────────────────────────────────────────────
 목적 : FCFF_DCF_Batch 노트북을 다시 돌리지 않고, DB(us_fcff_dcf_valuation)에
        이미 저장된 결과만 '읽기 전용'으로 조회 → 분석 → 엑셀 추출.

 위치 : 프로젝트 루트(DATA 폴더가 있는 곳) 또는 그 하위 아무 폴더.
        DATA.config 의 get_db_info() 를 그대로 사용합니다.

 사용법 1) 노트북 / 파이썬
     import fcff_analysis_v1 as fa
     fa.list_dates()
     fa.report()                              # 최신 배치 종합 리포트 → 엑셀
     fa.ranking("2026-11-17", top_n=50)       # DataFrame 반환
     fa.ticker_report(["NVDA", "AAPL"])       # 종목 상세 → 엑셀
     fa.q("SELECT ... FROM us_fcff_dcf_valuation WHERE ...")   # 자유 SQL

 사용법 2) 명령줄 (PyCharm 터미널 / Claude Code)
     python fcff_analysis_v1.py dates
     python fcff_analysis_v1.py report  --date 2026-11-17 --prev 2026-11-05
     python fcff_analysis_v1.py rank    --date 2026-11-17 --top 50 [--asc] [--raw] [--xlsx]
     python fcff_analysis_v1.py ticker  NVDA AAPL [--date 2026-11-17]
     python fcff_analysis_v1.py compare 2026-11-05 2026-11-17
     python fcff_analysis_v1.py sql "SELECT ..." [--xlsx 파일이름]

 원칙 : SELECT 만 허용 (q() 가 INSERT/UPDATE/DELETE/DROP 등을 차단).
        DB 조회는 cursor.execute → fetchall → DataFrame 패턴 (pd.read_sql 미사용).
═══════════════════════════════════════════════════════════════════════════
"""
import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════
#  ★ Control Panel — 설정은 여기서만 수정
# ═══════════════════════════════════════════════════════════════════════
TABLE_RESULT = "us_fcff_dcf_valuation"    # FCFF DCF 결과 테이블
DEFAULT_PORT = 3307                       # MySQL investar 포트

# 프로젝트 루트 후보 (노트북 / 데스크탑) — DATA 폴더 자동 탐색 실패 시 사용
_CANDIDATE_ROOTS = [
    r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast",           # 노트북
    r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy",  # 데스크탑
]

# 엑셀 저장 폴더 — None 이면 {프로젝트 루트}/reports/fcff_analysis
OUT_DIR_OVERRIDE: Optional[str] = None

# '정상 배치'로 인정할 최소 종목 수 (latest/prev 자동 선택 시 소규모 테스트 런 제외)
MIN_BATCH_TICKERS = 500

# 결과 품질 플래그 기준
UPSIDE_MAX     = 300.0    # upside_pct(%) 상한 — 초과 시 극단값
UPSIDE_MIN     = -90.0    # upside_pct(%) 하한 — 미만 시 극단값
G_CAP          = 0.0399   # g_terminal 이 이 값 이상이면 GDP 상한(4%)에 걸린 것
WACC_FLOOR_TOL = 0.0556   # WACC 가 이 값 이하이면 하한(Rf+1%)에 걸린 것으로 간주
TV_SPREAD_MIN  = 0.0201   # WACC - g 가 이 값 이하이면 TV 스프레드 하한에 걸린 것

# 체크포인트(failed_tickers_*.txt) 탐색 폴더명
CHECKPOINT_DIRNAME = "_fcff_checkpoint"

# ═══════════════════════════════════════════════════════════════════════
#  0. 경로 & DB 연결
# ═══════════════════════════════════════════════════════════════════════
def _find_root() -> Path:
    """현재 파일/작업폴더에서 상위로 올라가며 DATA 폴더 탐색 → 실패 시 후보 경로."""
    starts = []
    try:
        starts.append(Path(__file__).resolve().parent)
    except NameError:
        pass
    starts.append(Path.cwd())
    for s in starts:
        for p in [s] + list(s.parents):
            if (p / "DATA").is_dir():
                return p
    for c in _CANDIDATE_ROOTS:
        if os.path.isdir(os.path.join(c, "DATA")):
            return Path(c)
    return Path.cwd()


ROOT = _find_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = Path(OUT_DIR_OVERRIDE) if OUT_DIR_OVERRIDE else ROOT / "reports" / "fcff_analysis"

_db_info_cache: Optional[dict] = None


def _get_db_info() -> dict:
    global _db_info_cache
    if _db_info_cache is None:
        from DATA.config import get_db_info   # 기존 설정 파일 재사용
        _db_info_cache = get_db_info()
    return _db_info_cache


def _connect():
    """pymysql 연결 (DictCursor). 테스트 시 이 함수를 바꿔 끼울 수 있음."""
    import pymysql
    info = _get_db_info()
    return pymysql.connect(
        host=info["host"],
        port=int(info.get("port", DEFAULT_PORT)),
        user=info["user"],
        password=info["password"],
        db=info.get("database", "investar"),
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|rename|call|outfile|dumpfile)\b",
    re.IGNORECASE,
)


def q(sql: str, params: Optional[Sequence] = None) -> pd.DataFrame:
    """
    읽기 전용 SQL 실행 → DataFrame.
    SELECT / WITH / SHOW / DESCRIBE / EXPLAIN 만 허용. 파라미터는 %s 자리표시자.
    """
    body = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip()
    first = body.split(None, 1)[0].lower() if body else ""
    if first not in ("select", "with", "show", "describe", "desc", "explain"):
        raise PermissionError(f"읽기 전용 모듈: '{first}' 문은 허용되지 않습니다 (SELECT 만 가능).")
    if ";" in body.rstrip(";"):
        raise PermissionError("여러 문장을 한 번에 실행할 수 없습니다.")
    # 문자열 리터럴을 지운 뒤 금지어 검사 (컬럼 값에 'update' 같은 단어가 있어도 통과)
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "''", body)
    if first in ("select", "with") and _FORBIDDEN.search(stripped):
        raise PermissionError("SELECT 문 안에 쓰기 키워드가 포함되어 있습니다.")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params) if params else None)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        cur.close()
    finally:
        conn.close()
    if rows and isinstance(rows[0], dict):
        return pd.DataFrame(rows, columns=cols or None)
    return pd.DataFrame(list(rows), columns=cols)


# ═══════════════════════════════════════════════════════════════════════
#  1. 평가일자
# ═══════════════════════════════════════════════════════════════════════
def list_dates(verbose: bool = True) -> pd.DataFrame:
    """저장된 평가일자 + 일자별 종목수/행수/저장시각 범위."""
    df = q(f"""
        SELECT `date`, COUNT(DISTINCT ticker) AS n_tickers, COUNT(*) AS n_rows,
               MIN(created_at) AS first_saved, MAX(created_at) AS last_saved
        FROM `{TABLE_RESULT}`
        GROUP BY `date`
        ORDER BY `date` DESC
    """)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["n_tickers"] = df["n_tickers"].astype(int)
        df["n_rows"] = df["n_rows"].astype(int)
        df["full_batch"] = df["n_tickers"] >= MIN_BATCH_TICKERS
    if verbose:
        if df.empty:
            print("저장된 평가 결과가 없습니다.")
        else:
            print(f"평가일자 {len(df)}건 (full_batch = 종목 {MIN_BATCH_TICKERS}개 이상)")
            print(df.to_string(index=False))
    return df


def latest_date(before: Optional[str] = None) -> str:
    """가장 최근 정상 배치 일자. before 를 주면 그 날짜보다 이전 배치 중 최신."""
    d = list_dates(verbose=False)
    d = d[d["full_batch"]]
    if before:
        d = d[d["date"] < str(before)]
    if d.empty:
        raise ValueError("조건에 맞는 정상 배치 일자가 없습니다.")
    return d["date"].iloc[0]


def _norm_dates(dates) -> List[str]:
    if dates is None or (isinstance(dates, str) and dates.lower() == "latest"):
        return [latest_date()]
    if isinstance(dates, str):
        return [s.strip() for s in dates.split(",") if s.strip()]
    return [str(x) for x in dates]


# ═══════════════════════════════════════════════════════════════════════
#  2. 배치 결과 로드 (종목 단위 1행)
# ═══════════════════════════════════════════════════════════════════════
_NUM_COLS = ["current_price", "target_price", "upside_pct", "wacc", "g_terminal",
             "reinvestment_rate", "enterprise_value", "net_debt", "equity_value", "shares"]


def load_run(dates=None, dedup: str = "recent") -> pd.DataFrame:
    """
    지정 일자(들)의 결과를 종목 단위로 로드하고 품질 플래그를 붙임.
    dates : "2026-11-17" | "2026-11-17,2026-11-18" | 리스트 | None/"latest"
    dedup : "recent" → 같은 종목이 여러 날짜에 있으면 최신 결과만 / "all" → 모두 유지
    """
    ds = _norm_dates(dates)
    ph = ",".join(["%s"] * len(ds))
    df = q(f"""
        SELECT ticker, `date` AS eval_date,
               MAX(current_price) AS current_price, MAX(target_price) AS target_price,
               MAX(upside_pct) AS upside_pct, MAX(discount_rate) AS wacc,
               MAX(g_terminal) AS g_terminal, MAX(reinvestment_rate) AS reinvestment_rate,
               MAX(enterprise_value) AS enterprise_value, MAX(net_debt) AS net_debt,
               MAX(equity_value) AS equity_value, MAX(shares) AS shares,
               COUNT(*) AS n_quarters, MAX(created_at) AS created_at
        FROM `{TABLE_RESULT}`
        WHERE `date` IN ({ph})
        GROUP BY ticker, `date`
    """, ds)
    if df.empty:
        print(f"[load_run] {ds} 에 해당하는 결과 없음 — list_dates() 로 일자 확인")
        return df

    df["eval_date"] = pd.to_datetime(df["eval_date"]).dt.strftime("%Y-%m-%d")
    for c in _NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if dedup == "recent":
        df = (df.sort_values(["eval_date", "created_at"], ascending=[False, False])
                .drop_duplicates("ticker", keep="first"))

    # ── 파생 지표 & 품질 플래그 ───────────────────────────────────────
    mcap = df["current_price"] * df["shares"]
    df["market_cap"]      = mcap
    df["equity_to_mcap"]  = np.where(mcap > 0, df["equity_value"] / mcap, np.nan)
    df["tv_spread"]       = df["wacc"] - df["g_terminal"]
    df["flag_tp_neg"]     = df["target_price"] <= 0
    df["flag_extreme"]    = (df["upside_pct"] > UPSIDE_MAX) | (df["upside_pct"] < UPSIDE_MIN)
    df["flag_g_cap"]      = df["g_terminal"] >= G_CAP
    df["flag_wacc_floor"] = df["wacc"] <= WACC_FLOOR_TOL
    df["flag_thin_spread"] = df["tv_spread"] <= TV_SPREAD_MIN
    df["clean"]           = ~df["flag_tp_neg"] & ~df["flag_extreme"] & df["upside_pct"].notna()
    return df.reset_index(drop=True)


_RANK_COLS = ["rank", "ticker", "eval_date", "current_price", "target_price", "upside_pct",
              "wacc", "g_terminal", "tv_spread", "reinvestment_rate", "market_cap",
              "equity_value", "enterprise_value", "net_debt", "shares", "equity_to_mcap",
              "flag_tp_neg", "flag_extreme", "flag_g_cap", "flag_wacc_floor", "flag_thin_spread"]


def ranking(dates=None, top_n: Optional[int] = 50, ascending: bool = False,
            clean: bool = True, extra_filter: Optional[str] = None) -> pd.DataFrame:
    """
    Upside 랭킹.
    clean=True        → TP<=0, upside 극단값(UPSIDE_MIN~MAX 밖) 제외
    extra_filter      → pandas query 문자열 예) "market_cap > 1e10 and wacc > 0.06"
    top_n=None        → 전체
    """
    df = load_run(dates)
    if df.empty:
        return df
    if clean:
        df = df[df["clean"]]
    if extra_filter:
        df = df.query(extra_filter)
    df = df[df["upside_pct"].notna()].sort_values("upside_pct", ascending=ascending)
    df = df.reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    if top_n:
        df = df.head(top_n)
    return df[[c for c in _RANK_COLS if c in df.columns]]


# ═══════════════════════════════════════════════════════════════════════
#  3. 종목 상세
# ═══════════════════════════════════════════════════════════════════════
def ticker_detail(ticker: str, date: Optional[str] = None) -> pd.DataFrame:
    """종목의 분기별 FCFF 경로 (date=None → 그 종목의 가장 최근 평가일)."""
    t = ticker.upper()
    if date is None:
        d = q(f"SELECT MAX(`date`) AS d FROM `{TABLE_RESULT}` WHERE ticker=%s", [t])
        if d.empty or pd.isna(d["d"].iloc[0]):
            return pd.DataFrame()
        date = pd.to_datetime(d["d"].iloc[0]).strftime("%Y-%m-%d")
    df = q(f"""
        SELECT ticker, `date` AS eval_date, quarter, sales_forecast, opm_forecast, ebit,
               tax_rate, nopat, da, capex, nwc, delta_nwc, fcff, roic,
               discount_rate AS wacc, g_terminal, target_price, current_price, upside_pct
        FROM `{TABLE_RESULT}`
        WHERE ticker=%s AND `date`=%s
        ORDER BY quarter
    """, [t, str(date)])
    if not df.empty:
        df["eval_date"] = pd.to_datetime(df["eval_date"]).dt.strftime("%Y-%m-%d")
        for c in df.columns[3:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def ticker_history(ticker: str) -> pd.DataFrame:
    """종목의 평가일자별 TP/주가/Upside/WACC/g 추이."""
    df = q(f"""
        SELECT `date` AS eval_date, MAX(current_price) AS current_price,
               MAX(target_price) AS target_price, MAX(upside_pct) AS upside_pct,
               MAX(discount_rate) AS wacc, MAX(g_terminal) AS g_terminal,
               MAX(equity_value) AS equity_value, MAX(shares) AS shares
        FROM `{TABLE_RESULT}`
        WHERE ticker=%s
        GROUP BY `date`
        ORDER BY `date`
    """, [ticker.upper()])
    if df.empty:
        return df
    df["eval_date"] = pd.to_datetime(df["eval_date"]).dt.strftime("%Y-%m-%d")
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.insert(0, "ticker", ticker.upper())
    df["tp_chg_pct"] = df["target_price"].pct_change()
    return df


# ═══════════════════════════════════════════════════════════════════════
#  4. 회차 비교
# ═══════════════════════════════════════════════════════════════════════
def compare_runs(date_old: Optional[str] = None, date_new: Optional[str] = None) -> pd.DataFrame:
    """두 배치 비교 (기본: 최신 정상 배치 vs 직전 정상 배치)."""
    date_new = date_new or latest_date()
    date_old = date_old or latest_date(before=date_new)
    keep = ["ticker", "current_price", "target_price", "upside_pct", "wacc", "g_terminal", "clean"]
    a = load_run(date_old)[keep]
    b = load_run(date_new)[keep]
    m = a.merge(b, on="ticker", how="outer", suffixes=("_old", "_new"), indicator=True)
    m["status"] = m["_merge"].map({"both": "both", "left_only": "dropped", "right_only": "new"})
    m = m.drop(columns="_merge")
    m["price_chg_pct"] = m["current_price_new"] / m["current_price_old"] - 1
    m["tp_chg_pct"] = np.where(m["target_price_old"] > 0,
                               m["target_price_new"] / m["target_price_old"] - 1, np.nan)
    m["d_upside"] = m["upside_pct_new"] - m["upside_pct_old"]
    m["d_wacc"] = m["wacc_new"] - m["wacc_old"]
    m["tp_sign_flip"] = (np.sign(m["target_price_old"]) != np.sign(m["target_price_new"])) & \
                        (m["status"] == "both")
    m.insert(1, "date_old", date_old)
    m.insert(2, "date_new", date_new)
    order = ["ticker", "date_old", "date_new", "status",
             "current_price_old", "current_price_new", "price_chg_pct",
             "target_price_old", "target_price_new", "tp_chg_pct", "tp_sign_flip",
             "upside_pct_old", "upside_pct_new", "d_upside",
             "wacc_old", "wacc_new", "d_wacc", "g_terminal_old", "g_terminal_new",
             "clean_old", "clean_new"]
    m["_o"] = m["status"].map({"both": 0, "new": 1, "dropped": 2})
    m = m.sort_values(["_o", "d_upside"], ascending=[True, False]).drop(columns="_o")
    return m[order].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════
#  5. 실패 종목 (체크포인트 파일)
# ═══════════════════════════════════════════════════════════════════════
def failures(date: Optional[str] = None) -> pd.DataFrame:
    """failed_tickers_{date}.txt 를 찾아 읽음. 같은 날 재실행 후 성공한 종목은 표시."""
    date = date or latest_date()
    fname = f"failed_tickers_{date}.txt"
    cands = [Path.cwd() / CHECKPOINT_DIRNAME, ROOT / CHECKPOINT_DIRNAME]
    cands += [p / CHECKPOINT_DIRNAME for p in ROOT.glob("*") if p.is_dir()]
    cands += [p / CHECKPOINT_DIRNAME for p in ROOT.glob("*/*") if p.is_dir()]
    found = [c / fname for c in cands if (c / fname).exists()]
    if not found:
        print(f"[failures] {fname} 를 찾지 못함 (탐색: 작업폴더, 루트, 루트 하위 2단계)")
        return pd.DataFrame(columns=["ticker", "source_file", "saved_in_db"])
    rows = []
    for f in dict.fromkeys(found):
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                rows.append({"ticker": line.strip().split("|")[0], "source_file": str(f)})
    df = pd.DataFrame(rows).drop_duplicates("ticker")
    ok = set(load_run(date)["ticker"]) if not df.empty else set()
    df["saved_in_db"] = df["ticker"].isin(ok)
    return df.reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════
#  6. 요약 통계
# ═══════════════════════════════════════════════════════════════════════
def summary(dates=None, prev: Optional[str] = None) -> pd.DataFrame:
    """배치 품질/분포 요약 (항목, 값) 2열 표."""
    ds = _norm_dates(dates)
    df = load_run(ds)
    out = [("평가일자", ", ".join(ds)), ("종목 수", len(df))]
    if df.empty:
        return pd.DataFrame(out, columns=["item", "value"])
    c = df[df["clean"]]
    out += [
        ("TP <= 0 (음수 목표가)", int(df["flag_tp_neg"].sum())),
        (f"Upside 극단값 (<{UPSIDE_MIN:.0f}% 또는 >{UPSIDE_MAX:.0f}%)", int(df["flag_extreme"].sum())),
        ("g = GDP 상한(4%)", int(df["flag_g_cap"].sum())),
        ("WACC = 하한(Rf+1%)", int(df["flag_wacc_floor"].sum())),
        ("WACC-g 스프레드 = 하한(2%)", int(df["flag_thin_spread"].sum())),
        ("clean 종목 수", len(c)),
        ("clean 중 Upside > 0", int((c["upside_pct"] > 0).sum())),
        ("clean 중 Upside > 30%", int((c["upside_pct"] > 30).sum())),
    ]
    for p in (0.1, 0.25, 0.5, 0.75, 0.9):
        out.append((f"clean Upside {int(p*100)}% 분위 (%)", round(float(c["upside_pct"].quantile(p)), 1)))
    for p in (0.1, 0.5, 0.9):
        out.append((f"WACC {int(p*100)}% 분위", round(float(df["wacc"].quantile(p)), 4)))
    out.append(("g_terminal 중앙값", round(float(df["g_terminal"].median()), 4)))

    try:
        prev = prev or latest_date(before=min(ds))
        cmp_ = compare_runs(prev, max(ds))
        both = cmp_[(cmp_["status"] == "both") & cmp_["clean_old"].fillna(False).astype(bool)
                    & cmp_["clean_new"].fillna(False).astype(bool)]
        rho = both[["upside_pct_old", "upside_pct_new"]].corr(method="spearman").iloc[0, 1]
        out += [
            ("비교 대상 직전 배치", prev),
            ("신규 / 탈락 종목", f"{int((cmp_['status']=='new').sum())} / {int((cmp_['status']=='dropped').sum())}"),
            ("TP 부호 반전 종목", int(cmp_["tp_sign_flip"].sum())),
            ("TP 변화율 중앙값 (clean 공통)", round(float(both["tp_chg_pct"].median()), 4)),
            ("Upside 순위 상관 Spearman (clean 공통)", round(float(rho), 3)),
        ]
    except Exception as e:
        out.append(("직전 배치 비교", f"생략 ({str(e)[:60]})"))
    return pd.DataFrame(out, columns=["item", "value"])


# ═══════════════════════════════════════════════════════════════════════
#  7. 엑셀 export (파일명 버전 자동 증가)
# ═══════════════════════════════════════════════════════════════════════
_PCT_DEC = {"wacc", "g_terminal", "tv_spread", "reinvestment_rate", "opm_forecast", "tax_rate",
            "roic", "tp_chg_pct", "price_chg_pct", "d_wacc", "wacc_old", "wacc_new",
            "g_terminal_old", "g_terminal_new"}
_PCT_PT = {"upside_pct", "upside_pct_old", "upside_pct_new", "d_upside"}
_PRICE = {"current_price", "target_price", "current_price_old", "current_price_new",
          "target_price_old", "target_price_new", "equity_to_mcap"}
_BIG = {"market_cap", "equity_value", "enterprise_value", "net_debt", "shares", "sales_forecast",
        "ebit", "nopat", "da", "capex", "nwc", "delta_nwc", "fcff"}


def _versioned_path(name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("_")
    v = 1
    while (OUT_DIR / f"{base}_v{v}.xlsx").exists():
        v += 1
    return OUT_DIR / f"{base}_v{v}.xlsx"


def export_excel(sheets: Dict[str, pd.DataFrame], name: str) -> Path:
    """{시트명: DataFrame} → OUT_DIR/{name}_v{n}.xlsx (기존 파일은 덮어쓰지 않고 버전 증가)."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import ColorScaleRule

    path = _versioned_path(name)
    navy = PatternFill("solid", fgColor="003876")
    hfont = Font(bold=True, color="FFFFFF")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for sname, df in sheets.items():
            sname = sname[:31]
            df = df.copy() if df is not None else pd.DataFrame()
            for c in df.columns:
                if df[c].dtype == bool:
                    df[c] = df[c].map({True: "Y", False: ""})
            df.to_excel(xw, sheet_name=sname, index=False)
            ws = xw.sheets[sname]
            if df.empty:
                continue
            for j, col in enumerate(df.columns, 1):
                cell = ws.cell(row=1, column=j)
                cell.fill, cell.font = navy, hfont
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                fmt = ("0.00%" if col in _PCT_DEC else "0.0" if col in _PCT_PT else
                       "#,##0.00" if col in _PRICE else "#,##0" if col in _BIG else None)
                if fmt:
                    for r in range(2, len(df) + 2):
                        ws.cell(row=r, column=j).number_format = fmt
                width = max(len(str(col)), *(len(str(v)) for v in df[col].head(200))) + 2
                ws.column_dimensions[get_column_letter(j)].width = min(max(width, 8), 45)
                if col in ("upside_pct", "d_upside", "tp_chg_pct") and len(df) > 1:
                    rng = f"{get_column_letter(j)}2:{get_column_letter(j)}{len(df)+1}"
                    ws.conditional_formatting.add(rng, ColorScaleRule(
                        start_type="percentile", start_value=5, start_color="F8696B",
                        mid_type="num", mid_value=0, mid_color="FFFFFF",
                        end_type="percentile", end_value=95, end_color="63BE7B"))
            ws.freeze_panes = "B2" if df.columns[0] in ("rank", "ticker") else "A2"
            ws.auto_filter.ref = ws.dimensions
    print(f"[export] {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════
#  8. 원클릭 리포트
# ═══════════════════════════════════════════════════════════════════════
def report(dates=None, prev: Optional[str] = None, top_n: int = 50) -> Path:
    """
    배치 종합 리포트 (시트: Summary, Top, Bottom, All, Flagged, Compare, Failures).
    dates=None → 최신 정상 배치, prev=None → 그 직전 정상 배치와 비교.
    """
    ds = _norm_dates(dates)
    allr = ranking(ds, top_n=None, clean=False)
    sheets = {
        "Summary":  summary(ds, prev),
        "Top":      ranking(ds, top_n=top_n),
        "Bottom":   ranking(ds, top_n=top_n, ascending=True),
        "All":      allr,
        "Flagged":  allr[allr["flag_tp_neg"] | allr["flag_extreme"]].reset_index(drop=True),
    }
    try:
        sheets["Compare"] = compare_runs(prev, max(ds))
    except Exception as e:
        print(f"[report] 회차 비교 생략: {e}")
    try:
        sheets["Failures"] = failures(max(ds))
    except Exception as e:
        print(f"[report] 실패목록 생략: {e}")
    return export_excel(sheets, f"FCFF_Report_{max(ds).replace('-', '')}")


def ticker_report(tickers: Union[str, List[str]], date: Optional[str] = None) -> Path:
    """종목별 분기 상세 + 평가 이력 → 엑셀 (종목당 시트 2개)."""
    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]
    sheets = {}
    for t in tickers:
        sheets[f"{t.upper()}_detail"] = ticker_detail(t, date)
        sheets[f"{t.upper()}_history"] = ticker_history(t)
    tag = "_".join(t.upper() for t in tickers[:5]) + ("_etc" if len(tickers) > 5 else "")
    return export_excel(sheets, f"FCFF_Ticker_{tag}")


def sql_to_excel(sql: str, name: str = "FCFF_Query", params: Optional[Sequence] = None) -> Path:
    """자유 SELECT 결과를 바로 엑셀로."""
    return export_excel({"Query": q(sql, params)}, name)


# ═══════════════════════════════════════════════════════════════════════
#  9. 명령줄 실행
# ═══════════════════════════════════════════════════════════════════════
def _cli(argv=None):
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    p = argparse.ArgumentParser(description="FCFF DCF 결과 분석 (읽기 전용)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("dates", help="저장된 평가일자 목록")

    r = sub.add_parser("report", help="종합 리포트 엑셀")
    r.add_argument("--date", default=None, help="평가일자 (쉼표로 여러 개, 기본 latest)")
    r.add_argument("--prev", default=None, help="비교할 이전 배치 일자")
    r.add_argument("--top", type=int, default=50)

    k = sub.add_parser("rank", help="Upside 랭킹")
    k.add_argument("--date", default=None)
    k.add_argument("--top", type=int, default=30)
    k.add_argument("--asc", action="store_true", help="하위(과대평가) 순")
    k.add_argument("--raw", action="store_true", help="극단값/음수 TP 포함")
    k.add_argument("--filter", default=None, help='pandas query 예) "market_cap > 1e10"')
    k.add_argument("--xlsx", action="store_true", help="엑셀로도 저장")

    t = sub.add_parser("ticker", help="종목 상세 + 이력")
    t.add_argument("tickers", nargs="+")
    t.add_argument("--date", default=None)
    t.add_argument("--xlsx", action="store_true")

    c = sub.add_parser("compare", help="두 배치 비교")
    c.add_argument("date_old", nargs="?", default=None)
    c.add_argument("date_new", nargs="?", default=None)
    c.add_argument("--xlsx", action="store_true")

    s = sub.add_parser("summary", help="배치 요약 통계")
    s.add_argument("--date", default=None)
    s.add_argument("--prev", default=None)

    f = sub.add_parser("failures", help="실패 종목 목록")
    f.add_argument("--date", default=None)

    qq = sub.add_parser("sql", help="자유 SELECT")
    qq.add_argument("query")
    qq.add_argument("--xlsx", default=None, help="엑셀 파일 이름 (주면 저장)")

    a = p.parse_args(argv)
    if a.cmd == "dates":
        list_dates()
    elif a.cmd == "report":
        report(a.date, a.prev, a.top)
    elif a.cmd == "rank":
        df = ranking(a.date, a.top, a.asc, clean=not a.raw, extra_filter=a.filter)
        show = ["rank", "ticker", "eval_date", "current_price", "target_price", "upside_pct",
                "wacc", "g_terminal", "market_cap", "flag_tp_neg", "flag_extreme"]
        print(df[[c for c in show if c in df.columns]].to_string(index=False))
        if a.xlsx:
            d = _norm_dates(a.date)
            export_excel({"Rank": df}, f"FCFF_Rank_{max(d).replace('-', '')}")
    elif a.cmd == "ticker":
        for tk in a.tickers:
            print(f"\n== {tk.upper()} 분기 상세 ==")
            print(ticker_detail(tk, a.date).to_string(index=False))
            print(f"\n== {tk.upper()} 평가 이력 ==")
            print(ticker_history(tk).to_string(index=False))
        if a.xlsx:
            ticker_report(a.tickers, a.date)
    elif a.cmd == "compare":
        df = compare_runs(a.date_old, a.date_new)
        print(df.head(40).to_string(index=False))
        print(f"... 총 {len(df)}행 (status 별: {df['status'].value_counts().to_dict()})")
        if a.xlsx:
            export_excel({"Compare": df}, f"FCFF_Compare_{df['date_new'].iloc[0].replace('-', '')}")
    elif a.cmd == "summary":
        print(summary(a.date, a.prev).to_string(index=False))
    elif a.cmd == "failures":
        print(failures(a.date).to_string(index=False))
    elif a.cmd == "sql":
        df = q(a.query)
        print(df.to_string(index=False) if len(df) <= 200 else df.head(200).to_string(index=False))
        if a.xlsx:
            export_excel({"Query": df}, a.xlsx)


if __name__ == "__main__":
    _cli()
