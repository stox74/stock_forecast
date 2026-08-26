# -*- coding: utf-8 -*-
"""
dataguide_fs_analyzer_v1.py
===========================
korea_fs_data_from_DG (DataGuide long-format 테이블) 분석 유틸리티

테이블 스키마 (dataguide_fs_loader 기준)
    date, ticker, company_name, item_code, indicator, value, market, sj_div
    PK = (date, ticker, item_code)
    value 단위: 천원 (주식수 항목은 '주')

제공 기능
    1. search_items()      : 키워드로 수집된 재무항목 목록 조회
    2. get_ts()            : 특정 종목의 재무항목 시계열 (행=분기, 열=항목)
    3. yoy_screen()        : YoY 성장률 상위 N개 기업
    4. qoq_screen()        : QoQ 성장률 상위 N개 기업
    5. turnaround_screen() : 영업이익 흑자전환 기업 (YoY / QoQ 기준)
    6. ratio_screen()      : 재무비율(영업이익률/순이익률/ROE/ROA/ROIC 등) 상위 N개 기업

주의
    - DataGuide 분기 날짜는 회계분기말 영업일(예: 2025-12-30)이므로
      모든 시계열 연산은 date → 분기(Period 'Q')로 정규화한 뒤 수행한다.
    - BS 항목은 2023Q1부터, IS/CF 항목은 2020Q1부터 적재되어 있다.
    - Python 3.9 호환 (typing.Optional / Union 사용)
"""

from typing import Optional, Union, List, Dict, Sequence
import re
import numpy as np
import pandas as pd
from sqlalchemy import text, bindparam
from sqlalchemy.engine import Engine

TABLE = "korea_fs_data_from_DG"

# ----------------------------------------------------------------------
# 항목 별칭 (자주 쓰는 항목을 짧은 키로 지정)  키 → item_code
# ----------------------------------------------------------------------
ITEM_ALIAS: Dict[str, str] = {
    "매출액":        "M000904001",
    "매출원가":      "M000905001",
    "매출총이익":    "M000904007",
    "영업이익":      "M000906001",
    "세전이익":      "M001212380",
    "법인세":        "M001212390",
    "당기순이익":    "M001212450",
    "이자비용":      "M001290770",
    "감가상각비":    "M001310330",   # CF 기준 (IS 기준은 M001211210)
    "무형자산상각비": "M001390020",  # CF 기준 (IS 기준은 M001290030)
    "연구개발비":    "M001211300",
    "자산":          "M001190010",
    "유동자산":      "M001190240",
    "현금":          "M001190370",
    "단기금융상품":  "M001113350",
    "매출채권":      "M001180890",
    "재고자산":      "M001190250",
    "매입채무":      "M000902006",
    "단기차입금":    "M001121700",
    "유동성장기부채": "M001190620",
    "사채":          "M001190460",
    "장기차입금":    "M001190470",
    "리스부채":      "M001122020",
    "비유동부채":    "M001190450",
    "자본":          "M001190380",
    "비지배지분":    "M001130640",
    "영업현금흐름":  "M001390000",
    "투자현금흐름":  "M001390160",
    "재무현금흐름":  "M001390370",
    "유형자산증가":  "M001390290",
    "무형자산증가":  "M001390320",
    "배당금지급":    "M001330710",
    "평균주식수":    "S420004400",   # 자사주 차감
    "평균주식수_총": "S420004510",
}


# ======================================================================
# 내부 헬퍼
# ======================================================================
def _norm_tk(tk: str) -> str:
    tk = str(tk).strip().upper()
    if re.fullmatch(r"\d{6}", tk):
        return "A" + tk
    return tk


def _to_q(s: pd.Series) -> pd.PeriodIndex:
    """회계분기말 영업일 → 분기 Period (2025-12-30 → 2025Q4)"""
    return pd.to_datetime(s).dt.to_period("Q")


def _item_master(engine: Engine) -> pd.DataFrame:
    """item_code ↔ indicator 마스터 (캐시)"""
    sql = text(f"""
        SELECT item_code, indicator, sj_div,
               COUNT(DISTINCT ticker) AS n_ticker,
               MIN(date) AS min_date, MAX(date) AS max_date
        FROM {TABLE}
        GROUP BY item_code, indicator, sj_div
        ORDER BY sj_div, item_code
    """)
    with engine.connect() as con:
        return pd.read_sql(sql, con)


def resolve_item(engine: Engine, item: str) -> str:
    """
    별칭 / item_code / 항목명(부분일치) → item_code 로 변환.
    부분일치가 여러 개면 ValueError (후보 목록 출력).
    """
    if item in ITEM_ALIAS:
        return ITEM_ALIAS[item]
    if re.fullmatch(r"[MS]\d{9}", item):
        return item
    m = _item_master(engine)
    hit = m[m["indicator"].str.contains(item, regex=False)]
    if len(hit) == 0:
        raise ValueError(f"항목을 찾을 수 없습니다: {item}")
    if hit["item_code"].nunique() > 1:
        raise ValueError(
            f"항목 '{item}' 이(가) 여러 개 매칭됩니다. item_code를 지정하세요.\n"
            f"{hit[['item_code', 'indicator', 'sj_div']].to_string(index=False)}"
        )
    return hit["item_code"].iloc[0]


def load_long(
    engine: Engine,
    item_codes: Sequence[str],
    tickers: Optional[Sequence[str]] = None,
    start: Optional[str] = None,
    market: Optional[str] = None,
) -> pd.DataFrame:
    """long 포맷 원본 로드 (+ 분기 컬럼 q 추가)"""
    params: Dict[str, object] = {}
    where = ["item_code IN :codes"]
    params["codes"] = tuple(item_codes)
    if tickers:
        where.append("ticker IN :tks")
        params["tks"] = tuple(_norm_tk(t) for t in tickers)
    if start:
        where.append("date >= :start")
        params["start"] = start
    if market:
        where.append("market = :mkt")
        params["mkt"] = market
    sql = text(f"""
        SELECT date, ticker, company_name, item_code, indicator, value, market, sj_div
        FROM {TABLE}
        WHERE {' AND '.join(where)}
    """)
    for k in ("codes", "tks"):          # IN (...) 은 expanding bindparam 으로 처리
        if k in params:
            sql = sql.bindparams(bindparam(k, expanding=True))
    with engine.connect() as con:
        df = pd.read_sql(sql, con, params=params)
    df["date"] = pd.to_datetime(df["date"])
    df["q"] = df["date"].dt.to_period("Q")
    return df


def _pivot_qt(df: pd.DataFrame, item_code: str) -> pd.DataFrame:
    """ticker × 분기 wide 매트릭스 (값 단위 그대로)"""
    sub = df[df["item_code"] == item_code]
    return sub.pivot_table(index="ticker", columns="q", values="value", aggfunc="last")


def _names(df: pd.DataFrame) -> pd.Series:
    return df.drop_duplicates("ticker").set_index("ticker")["company_name"]


def pick_asof_quarter(mat: pd.DataFrame, min_frac: float = 0.5) -> pd.Period:
    """
    기준 분기 자동 선택: 가장 최근 분기 중 유효 종목 수가
    역대 최대 종목 수의 min_frac 이상인 분기 (부분 적재 분기 제외).
    """
    cnt = mat.notna().sum(axis=0)
    ok = cnt[cnt >= cnt.max() * min_frac]
    return ok.index.max()


# ======================================================================
# 1. 항목 검색
# ======================================================================
def search_items(engine: Engine, keyword: Optional[str] = None) -> pd.DataFrame:
    """
    키워드(부분일치, 대소문자 무시)로 수집된 재무항목 목록 조회.
    keyword=None 이면 전체 목록.
    """
    m = _item_master(engine)
    if keyword:
        m = m[m["indicator"].str.contains(keyword, case=False, regex=False)]
    alias = {v: k for k, v in ITEM_ALIAS.items()}
    m = m.copy()
    m.insert(0, "alias", m["item_code"].map(alias).fillna(""))
    return m.reset_index(drop=True)


# ======================================================================
# 2. 종목별 시계열
# ======================================================================
def get_ts(
    engine: Engine,
    ticker: str,
    items: Sequence[str],
    start: Optional[str] = None,
    unit: float = 1e5,          # 천원 → 억원 (1e5). 원단위 그대로면 1.0
    use_period_index: bool = True,
) -> pd.DataFrame:
    """
    종목 하나의 재무항목 시계열. 행=분기, 열=items (입력 순서 유지).
    items: 별칭('매출액'), item_code('M000904001'), 항목명 부분일치 모두 허용.
    """
    codes = [resolve_item(engine, it) for it in items]
    df = load_long(engine, codes, tickers=[ticker], start=start)
    if df.empty:
        raise ValueError(f"데이터 없음: {ticker}")
    idx = "q" if use_period_index else "date"
    wide = df.pivot_table(index=idx, columns="item_code", values="value", aggfunc="last")
    wide = wide.reindex(columns=codes)
    wide.columns = list(items)
    # 주식수 항목은 단위 변환 제외
    for it, c in zip(items, codes):
        if not c.startswith("S"):
            wide[it] = wide[it] / unit
    wide.attrs["ticker"] = _norm_tk(ticker)
    wide.attrs["company_name"] = df["company_name"].iloc[0]
    return wide.sort_index()


# ======================================================================
# 3~4. YoY / QoQ 스크리너 (공통 엔진)
# ======================================================================
def _growth_screen(
    engine: Engine,
    item: str,
    lag: int,
    n: int,
    asof: Optional[str],
    min_base: float,
    market: Optional[str],
    unit: float,
    ascending: bool,
) -> pd.DataFrame:
    code = resolve_item(engine, item)
    df = load_long(engine, [code], market=market)
    mat = _pivot_qt(df, code)
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    if q_t not in mat.columns or q_b not in mat.columns:
        raise ValueError(f"분기 데이터 부족: t={q_t}, base={q_b}")

    cur = mat[q_t]
    base = mat[q_b]
    out = pd.DataFrame({
        "ticker": mat.index,
        "company_name": _names(df).reindex(mat.index).values,
        f"{item}(t-{lag})": base.values / unit,
        f"{item}(t)": cur.values / unit,
    })
    valid = (base > 0) & (base.abs() >= min_base) & cur.notna()
    out["growth_%"] = np.where(valid, (cur - base) / base.abs() * 100, np.nan)
    out = out.dropna(subset=["growth_%"]).sort_values("growth_%", ascending=ascending)
    out.insert(2, "base_q", str(q_b))
    out.insert(3, "t_q", str(q_t))
    return out.head(n).reset_index(drop=True)


def yoy_screen(
    engine: Engine,
    item: str = "매출액",
    n: int = 30,
    asof: Optional[str] = None,       # 예: '2026Q2'. None → 자동(최근 완전 적재 분기)
    min_base: float = 1e6,            # 기준값 최소 (천원 단위, 1e6=10억원) → 소규모 베이스 배제
    market: Optional[str] = None,     # 'KS' / 'KQ' / None(전체)
    unit: float = 1e5,                # 표시 단위: 천원→억원
    ascending: bool = False,          # True 면 역성장 하위 N개
) -> pd.DataFrame:
    """YoY 성장률 상위 N개 기업 (t vs t-4). 기준값(t-4)이 양수인 기업만."""
    return _growth_screen(engine, item, 4, n, asof, min_base, market, unit, ascending)


def qoq_screen(
    engine: Engine,
    item: str = "매출액",
    n: int = 30,
    asof: Optional[str] = None,
    min_base: float = 1e6,
    market: Optional[str] = None,
    unit: float = 1e5,
    ascending: bool = False,
) -> pd.DataFrame:
    """QoQ 성장률 상위 N개 기업 (t vs t-1). 기준값(t-1)이 양수인 기업만."""
    return _growth_screen(engine, item, 1, n, asof, min_base, market, unit, ascending)


# ======================================================================
# 5. 흑자전환 스크리너
# ======================================================================
def turnaround_screen(
    engine: Engine,
    basis: str = "yoy",               # 'yoy' (t-4 적자 → t 흑자) / 'qoq' (t-1 적자 → t 흑자)
    item: str = "영업이익",
    asof: Optional[str] = None,
    market: Optional[str] = None,
    unit: float = 1e5,
    min_profit: float = 0.0,          # t 시점 최소 이익 (천원)
    sort_by: str = "swing",           # 'swing'(개선폭) / 'profit'(t 이익)
) -> pd.DataFrame:
    """
    적자 → 흑자 전환 기업. 컬럼: ticker, name, 기준분기, t분기, 이익(t-lag), 이익(t), 개선폭.
    매출 대비 개선폭(swing_%rev)도 함께 제공.
    """
    lag = 4 if basis.lower() == "yoy" else 1
    code = resolve_item(engine, item)
    rev_code = ITEM_ALIAS["매출액"]
    df = load_long(engine, [code, rev_code], market=market)
    mat = _pivot_qt(df, code)
    rev = _pivot_qt(df, rev_code)
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag

    cur, base = mat[q_t], mat[q_b]
    mask = (base < 0) & (cur > min_profit)
    sel = mat.index[mask.fillna(False)]
    rev_t = rev.reindex(sel)[q_t] if q_t in rev.columns else pd.Series(np.nan, index=sel)

    out = pd.DataFrame({
        "ticker": sel,
        "company_name": _names(df).reindex(sel).values,
        "base_q": str(q_b),
        "t_q": str(q_t),
        f"{item}(t-{lag})": base[sel].values / unit,
        f"{item}(t)": cur[sel].values / unit,
        "swing": (cur[sel] - base[sel]).values / unit,
        "매출액(t)": rev_t.values / unit,
    })
    out["swing_%rev"] = out["swing"] / out["매출액(t)"] * 100
    key = "swing" if sort_by == "swing" else f"{item}(t)"
    return out.sort_values(key, ascending=False).reset_index(drop=True)


# ======================================================================
# 6. 재무비율 스크리너
# ======================================================================
_RATIO_NEEDS = {
    "OPM":   ["매출액", "영업이익"],
    "NPM":   ["매출액", "당기순이익"],
    "GPM":   ["매출액", "매출총이익"],
    "ROE":   ["당기순이익", "자본"],
    "ROA":   ["당기순이익", "자산"],
    "ROIC":  ["영업이익", "세전이익", "법인세", "자본", "단기차입금", "유동성장기부채",
              "사채", "장기차입금", "리스부채", "현금", "단기금융상품"],
    "부채비율": ["자산", "자본"],
    "순차입금비율": ["자본", "단기차입금", "유동성장기부채", "사채", "장기차입금",
                  "리스부채", "현금", "단기금융상품"],
    "OCF_margin": ["매출액", "영업현금흐름"],
    "FCF_margin": ["매출액", "영업현금흐름", "유형자산증가", "무형자산증가"],
}


def _ttm(mat: pd.DataFrame) -> pd.DataFrame:
    """분기 매트릭스 → TTM (최근 4분기 합, 4개 모두 있어야 유효)"""
    return mat.T.rolling(4, min_periods=4).sum().T


def _avg2(mat: pd.DataFrame, lag: int = 4) -> pd.DataFrame:
    """기말/기초(4분기 전) 평균. 기초 없으면 기말 사용."""
    prev = mat.shift(lag, axis=1)
    return mat.where(prev.isna(), (mat + prev) / 2)


def compute_ratios(
    engine: Engine,
    asof: Optional[str] = None,
    market: Optional[str] = None,
    ttm: bool = True,                 # True: 손익 TTM 합 / False: 단일분기 연율화(×4)
    default_tax: float = 0.22,
    unit: float = 1e5,
) -> pd.DataFrame:
    """
    모든 비율을 한 번에 계산해 ticker별 1행으로 반환 (관련 재무데이터 포함, 억원 단위).
    """
    needed = sorted({a for v in _RATIO_NEEDS.values() for a in v})
    codes = [ITEM_ALIAS[a] for a in needed]
    df = load_long(engine, codes, market=market)
    mats = {a: _pivot_qt(df, ITEM_ALIAS[a]) for a in needed}
    # 컬럼(분기) 정렬 통일
    all_q = sorted(set().union(*[m.columns for m in mats.values()]))
    all_tk = sorted(set().union(*[m.index for m in mats.values()]))
    mats = {a: m.reindex(index=all_tk, columns=all_q) for a, m in mats.items()}

    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mats["매출액"])

    def flow(a: str) -> pd.Series:
        m = mats[a]
        return _ttm(m)[q_t] if ttm else m[q_t] * 4

    def stock_avg(a: str) -> pd.Series:
        return _avg2(mats[a])[q_t]

    def stock_end(a: str) -> pd.Series:
        return mats[a][q_t]

    rev, op, ni = flow("매출액"), flow("영업이익"), flow("당기순이익")
    gp, ocf = flow("매출총이익"), flow("영업현금흐름")
    capex = flow("유형자산증가").fillna(0) + flow("무형자산증가").fillna(0)
    pretax, tax = flow("세전이익"), flow("법인세")

    eq_avg, ta_avg = stock_avg("자본"), stock_avg("자산")
    eq_end, ta_end = stock_end("자본"), stock_end("자산")
    debt_end = sum(stock_end(a).fillna(0) for a in
                   ["단기차입금", "유동성장기부채", "사채", "장기차입금", "리스부채"])
    cash_end = stock_end("현금").fillna(0) + stock_end("단기금융상품").fillna(0)
    net_debt = debt_end - cash_end
    # 투하자본 = 자본 + 순차입금 (전 분기 매트릭스로 계산 후 기초/기말 평균)
    debt_m = sum(mats[a].fillna(0) for a in
                 ["단기차입금", "유동성장기부채", "사채", "장기차입금", "리스부채"])
    cash_m = mats["현금"].fillna(0) + mats["단기금융상품"].fillna(0)
    ic_m = mats["자본"] + debt_m - cash_m
    ic_avg = _avg2(ic_m)[q_t]

    # 유효세율 (0~30% 클립, 산출 불가시 default)
    eff_tax = (tax / pretax).where((pretax > 0) & (tax >= 0)).clip(0, 0.30).fillna(default_tax)
    nopat = op * (1 - eff_tax)

    out = pd.DataFrame(index=all_tk)
    out["company_name"] = _names(df).reindex(all_tk)
    out["t_q"] = str(q_t)
    out["매출액"] = rev / unit
    out["매출총이익"] = gp / unit
    out["영업이익"] = op / unit
    out["당기순이익"] = ni / unit
    out["영업현금흐름"] = ocf / unit
    out["CAPEX"] = capex / unit
    out["자산(평균)"] = ta_avg / unit
    out["자본(평균)"] = eq_avg / unit
    out["순차입금"] = net_debt / unit
    out["투하자본(평균)"] = ic_avg / unit
    out["유효세율_%"] = eff_tax * 100

    out["GPM"] = gp / rev * 100
    out["OPM"] = op / rev * 100
    out["NPM"] = ni / rev * 100
    out["ROE"] = ni / eq_avg * 100
    out["ROA"] = ni / ta_avg * 100
    out["ROIC"] = nopat / ic_avg * 100
    out["부채비율"] = (ta_end - eq_end) / eq_end * 100
    out["순차입금비율"] = net_debt / eq_end * 100
    out["OCF_margin"] = ocf / rev * 100
    out["FCF_margin"] = (ocf - capex) / rev * 100

    # 비율 계산 불가(자본 ≤ 0, 매출 ≤ 0) 제거
    out.loc[(eq_avg <= 0) | (eq_end <= 0), ["ROE", "ROIC", "부채비율", "순차입금비율"]] = np.nan
    out.loc[rev <= 0, ["GPM", "OPM", "NPM", "OCF_margin", "FCF_margin"]] = np.nan
    out.loc[ic_avg <= 0, "ROIC"] = np.nan
    out.index.name = "ticker"
    return out.reset_index()


def ratio_screen(
    engine: Engine,
    ratio: str = "ROE",               # OPM/NPM/GPM/ROE/ROA/ROIC/부채비율/순차입금비율/OCF_margin/FCF_margin
    n: int = 30,
    asof: Optional[str] = None,
    market: Optional[str] = None,
    ttm: bool = True,
    ascending: bool = False,          # 부채비율 등은 True 로 낮은 순
    min_revenue: float = 1e6,         # 최소 매출(천원, TTM). 1e6 = 10억원
    ratios_df: Optional[pd.DataFrame] = None,   # compute_ratios() 결과 재사용 시
) -> pd.DataFrame:
    """
    입력 비율 기준 상위 N개 기업. 컬럼: ticker, name, 관련 재무데이터, 해당 비율(+다른 비율).
    """
    ratio = ratio.upper() if ratio.lower() in ("opm", "npm", "gpm", "roe", "roa", "roic") else ratio
    if ratio not in _RATIO_NEEDS:
        raise ValueError(f"지원 비율: {list(_RATIO_NEEDS)}")
    r = ratios_df if ratios_df is not None else compute_ratios(engine, asof, market, ttm)
    r = r[r["매출액"] * 1e5 >= min_revenue]
    r = r.dropna(subset=[ratio]).sort_values(ratio, ascending=ascending)

    base_cols = ["ticker", "company_name", "t_q"]
    comp_cols = {
        "OPM": ["매출액", "영업이익"], "NPM": ["매출액", "당기순이익"], "GPM": ["매출액", "매출총이익"],
        "ROE": ["당기순이익", "자본(평균)"], "ROA": ["당기순이익", "자산(평균)"],
        "ROIC": ["영업이익", "유효세율_%", "투하자본(평균)"],
        "부채비율": ["자산(평균)", "자본(평균)"], "순차입금비율": ["순차입금", "자본(평균)"],
        "OCF_margin": ["매출액", "영업현금흐름"], "FCF_margin": ["매출액", "영업현금흐름", "CAPEX"],
    }[ratio]
    others = [c for c in ["OPM", "NPM", "ROE", "ROA", "ROIC", "부채비율"] if c != ratio]
    return r[base_cols + comp_cols + [ratio] + others].head(n).reset_index(drop=True)
