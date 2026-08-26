# -*- coding: utf-8 -*-
"""
fmp_fs_analyzer_v1.py
=====================
US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP (US_FMP_FS_1_RUN_UPDATE.py 가 적재하는 long 테이블) 기반
재무분석 유틸리티. dart_fs_analyzer_v4 와 같은 인터페이스.

테이블 스키마 (US_FMP_FS_2_DB_SAVE_LIB.py V4)
    id, ticker, date(YYYY-MM-DD, 분기말), date_month(YYYY-MM), period(Q1..Q4 / FY), item(FMP 필드명), value
    금액 단위: USD (원 단위)

DART 와 다른 점
    - FMP 분기 데이터는 이미 "분기 flow" (누적 아님) → 분기화 불필요
    - period 는 회계분기(Q1..Q4)라 회사마다 달력이 다름 → date 를 달력 분기 Period 로 변환해 정렬
    - 저장 라이브러리 V4 는 단순 INSERT 라 같은 (ticker, date, item) 이 여러 번 쌓임 → id 최대 행만 사용 (최신 수집분)
    - 기업명 컬럼 없음 → name_table 로 선택 매핑

파이프라인
    panel = build_panel(engine)                      # ticker × 분기 × concept (1회, 캐시 권장)
    get_ts(panel, 'AAPL', ['매출액', '영업이익'])
    yoy_screen / qoq_screen / turnaround_screen / ratio_screen(panel, ...)

Python 3.9 호환 (typing.Optional / Union)
"""

from typing import Optional, Union, List, Dict, Sequence
import re
import numpy as np
import pandas as pd
from sqlalchemy import text, bindparam
from sqlalchemy.engine import Engine

TABLES = {"IS": "US_IS_from_FMP", "BS": "US_BS_from_FMP", "CF": "US_CF_from_FMP"}
QUARTER_PERIODS = ("Q1", "Q2", "Q3", "Q4")

# ----------------------------------------------------------------------
# concept → FMP item (우선순위 순). 한글 별칭은 DART 분석기와 맞춤
# ----------------------------------------------------------------------
CONCEPTS: Dict[str, Dict] = {
    # IS
    "매출액":       dict(sj="IS", items=["revenue"]),
    "매출원가":     dict(sj="IS", items=["costOfRevenue"]),
    "매출총이익":   dict(sj="IS", items=["grossProfit"]),
    "영업비용":     dict(sj="IS", items=["operatingExpenses"]),
    "영업이익":     dict(sj="IS", items=["operatingIncome"]),
    "EBITDA":       dict(sj="IS", items=["ebitda"]),
    "세전이익":     dict(sj="IS", items=["incomeBeforeTax"]),
    "법인세":       dict(sj="IS", items=["incomeTaxExpense"]),
    "당기순이익":   dict(sj="IS", items=["netIncome"]),
    "이자비용":     dict(sj="IS", items=["interestExpense"]),
    "감가상각비":   dict(sj="IS", items=["depreciationAndAmortization"]),
    "연구개발비":   dict(sj="IS", items=["researchAndDevelopmentExpenses"]),
    "EPS":          dict(sj="IS", items=["epsdiluted", "eps"]),
    "평균주식수":   dict(sj="IS", items=["weightedAverageShsOutDil", "weightedAverageShsOut"]),
    # BS
    "자산":         dict(sj="BS", items=["totalAssets"]),
    "유동자산":     dict(sj="BS", items=["totalCurrentAssets"]),
    "현금":         dict(sj="BS", items=["cashAndCashEquivalents"]),
    "단기금융상품": dict(sj="BS", items=["shortTermInvestments"]),
    "현금성자산":   dict(sj="BS", items=["cashAndShortTermInvestments"]),
    "매출채권":     dict(sj="BS", items=["netReceivables"]),
    "재고자산":     dict(sj="BS", items=["inventory"]),
    "유형자산":     dict(sj="BS", items=["propertyPlantEquipmentNet"]),
    "부채":         dict(sj="BS", items=["totalLiabilities"]),
    "유동부채":     dict(sj="BS", items=["totalCurrentLiabilities"]),
    "매입채무":     dict(sj="BS", items=["accountPayables"]),
    "단기차입금":   dict(sj="BS", items=["shortTermDebt"]),
    "장기차입금":   dict(sj="BS", items=["longTermDebt"]),
    "총차입금":     dict(sj="BS", items=["totalDebt"]),
    "순차입금":     dict(sj="BS", items=["netDebt"]),
    "리스부채":     dict(sj="BS", items=["capitalLeaseObligations"]),
    "자본":         dict(sj="BS", items=["totalEquity", "totalStockholdersEquity"]),
    "지배주주지분": dict(sj="BS", items=["totalStockholdersEquity"]),
    "비지배지분":   dict(sj="BS", items=["minorityInterest"]),
    # CF
    "영업현금흐름": dict(sj="CF", items=["operatingCashFlow", "netCashProvidedByOperatingActivities"]),
    "투자현금흐름": dict(sj="CF", items=["netCashUsedForInvestingActivites", "netCashUsedForInvestingActivities"]),
    "재무현금흐름": dict(sj="CF", items=["netCashUsedProvidedByFinancingActivities"]),
    "CAPEX":        dict(sj="CF", items=["capitalExpenditure"]),          # FMP 는 음수
    "FCF":          dict(sj="CF", items=["freeCashFlow"]),
    "배당금지급":   dict(sj="CF", items=["dividendsPaid"]),               # 음수
    "자사주매입":   dict(sj="CF", items=["commonStockRepurchased"]),      # 음수
}


# ======================================================================
# 헬퍼
# ======================================================================
def _sj_of(item: str) -> Optional[str]:
    for c in CONCEPTS.values():
        if item in c["items"]:
            return c["sj"]
    return None


def _name_map(engine: Optional[Engine], name_table: Optional[str], ticker_col: str, name_col: str) -> pd.Series:
    if engine is None or not name_table:
        return pd.Series(dtype=object)
    try:
        with engine.connect() as con:
            df = pd.read_sql(text(f"SELECT DISTINCT {ticker_col} AS ticker, {name_col} AS name FROM {name_table}"), con)
        return df.drop_duplicates("ticker").set_index("ticker")["name"]
    except Exception:
        return pd.Series(dtype=object)


# ======================================================================
# 1. 항목 검색
# ======================================================================
def search_items(engine: Engine, keyword: Optional[str] = None, sj_div: Optional[str] = None) -> pd.DataFrame:
    """
    세 테이블에 적재된 FMP item 목록. keyword 는 item 명 부분일치(대소문자 무시).
    반환: sj_div, item, n_ticker, n_rows, min_date, max_date, mapped(concept)
    """
    parts = []
    for sj, tbl in TABLES.items():
        if sj_div and sj != sj_div:
            continue
        where = "WHERE item LIKE :kw" if keyword else ""
        sql = text(f"""
            SELECT '{sj}' AS sj_div, item, COUNT(DISTINCT ticker) AS n_ticker, COUNT(*) AS n_rows,
                   MIN(date) AS min_date, MAX(date) AS max_date
            FROM {tbl} {where} GROUP BY item ORDER BY n_ticker DESC
        """)
        with engine.connect() as con:
            parts.append(pd.read_sql(sql, con, params={"kw": f"%{keyword}%"} if keyword else None))
    df = pd.concat(parts, ignore_index=True)
    rev = {}
    for cname, c in CONCEPTS.items():
        for it in c["items"]:
            rev.setdefault((c["sj"], it), cname)
    df.insert(0, "mapped", [rev.get((s, i), "") for s, i in zip(df["sj_div"], df["item"])])
    return df


# ======================================================================
# 2. 패널 구축
# ======================================================================
def load_long(engine: Engine, concepts: Optional[Sequence[str]] = None,
              tickers: Optional[Sequence[str]] = None, start: Optional[str] = None,
              periods: Sequence[str] = QUARTER_PERIODS) -> pd.DataFrame:
    """
    세 테이블에서 concept 에 해당하는 item 만 로드. 같은 (ticker, date, item) 이 여러 행이면 id 최대(최신 수집) 만 사용.
    반환: ticker, date, q(Period), period, sj_div, item, value
    """
    concepts = list(concepts) if concepts else list(CONCEPTS)
    by_sj: Dict[str, List[str]] = {}
    for c in concepts:
        by_sj.setdefault(CONCEPTS[c]["sj"], []).extend(CONCEPTS[c]["items"])
    parts = []
    for sj, items in by_sj.items():
        where = ["item IN :items", "period IN :periods"]
        params: Dict[str, object] = {"items": tuple(sorted(set(items))), "periods": tuple(periods)}
        if tickers:
            where.append("ticker IN :tks"); params["tks"] = tuple(t.upper() for t in tickers)
        if start:
            where.append("date >= :start"); params["start"] = start
        # 중복 행은 id 최대만 (MySQL 8 / MariaDB 10.2+ 윈도우 함수)
        sql = text(f"""
            SELECT ticker, date, period, item, value FROM (
                SELECT ticker, date, period, item, value,
                       ROW_NUMBER() OVER (PARTITION BY ticker, date, item ORDER BY id DESC) AS rn
                FROM {TABLES[sj]}
                WHERE {' AND '.join(where)}
            ) t WHERE rn = 1
        """).bindparams(bindparam("items", expanding=True), bindparam("periods", expanding=True))
        if tickers:
            sql = sql.bindparams(bindparam("tks", expanding=True))
        with engine.connect() as con:
            d = pd.read_sql(sql, con, params=params)
        d["sj_div"] = sj
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df["q"] = df["date"].dt.to_period("Q")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"])


def build_panel(engine: Engine, concepts: Optional[Sequence[str]] = None, tickers: Optional[Sequence[str]] = None,
                start: Optional[str] = None, name_table: Optional[str] = None,
                name_ticker_col: str = "ticker", name_col: str = "company_name") -> pd.DataFrame:
    """
    long 패널: ticker, company_name, q(달력 분기), date(회계분기말), concept, sj_div, item, value(USD)
    concept 별 items 우선순위: 앞 item 이 있으면 그것, 없으면 다음.
    """
    concepts = list(concepts) if concepts else list(CONCEPTS)
    df = load_long(engine, concepts, tickers, start)
    if df.empty:
        raise ValueError("FMP 데이터 없음")
    out = []
    for cname in concepts:
        c = CONCEPTS[cname]
        sub = df[(df["sj_div"] == c["sj"]) & df["item"].isin(c["items"])].copy()
        if sub.empty:
            continue
        sub["pri"] = sub["item"].map({it: i for i, it in enumerate(c["items"])})
        sub = sub.sort_values(["ticker", "q", "pri", "date"]).drop_duplicates(["ticker", "q"], keep="first")
        sub["concept"] = cname
        out.append(sub)
    p = pd.concat(out, ignore_index=True)
    names = _name_map(engine, name_table, name_ticker_col, name_col)
    p.insert(1, "company_name", p["ticker"].map(names).fillna(""))
    return p[["ticker", "company_name", "q", "date", "concept", "sj_div", "item", "value"]].reset_index(drop=True)


def coverage_report(panel: pd.DataFrame) -> pd.DataFrame:
    g = panel.groupby("concept")
    return pd.DataFrame({"n_ticker": g["ticker"].nunique(), "n_rows": g.size(),
                         "min_q": g["q"].min().astype(str), "max_q": g["q"].max().astype(str)}
                        ).sort_values("n_ticker", ascending=False)


# ======================================================================
# 패널 → 매트릭스
# ======================================================================
def _mat(panel: pd.DataFrame, concept: str) -> pd.DataFrame:
    sub = panel[panel["concept"] == concept]
    return sub.pivot_table(index="ticker", columns="q", values="value", aggfunc="last")


def _names(panel: pd.DataFrame) -> pd.Series:
    return panel.drop_duplicates("ticker").set_index("ticker")["company_name"]


def pick_asof_quarter(mat: pd.DataFrame, min_frac: float = 0.5) -> pd.Period:
    cnt = mat.notna().sum(axis=0)
    return cnt[cnt >= cnt.max() * min_frac].index.max()


# ======================================================================
# 3. 종목별 시계열
# ======================================================================
def get_ts(panel: pd.DataFrame, ticker: str, concepts: Sequence[str],
           start: Optional[str] = None, unit: float = 1e6) -> pd.DataFrame:
    """행=달력 분기, 열=concepts. unit=1e6 → $M.  EPS·주식수는 단위 변환 안 함."""
    tk = ticker.upper()
    sub = panel[panel["ticker"] == tk]
    if sub.empty:
        raise ValueError(f"데이터 없음: {tk}")
    wide = sub.pivot_table(index="q", columns="concept", values="value", aggfunc="last").reindex(columns=list(concepts))
    for c in concepts:
        if c not in ("EPS", "평균주식수"):
            wide[c] = wide[c] / unit
    if start:
        wide = wide[wide.index >= pd.Period(start, freq="Q")]
    wide.attrs["ticker"], wide.attrs["company_name"] = tk, sub["company_name"].iloc[0]
    wide.attrs["fiscal_dates"] = sub.drop_duplicates("q").set_index("q")["date"].dt.strftime("%Y-%m-%d").to_dict()
    return wide.sort_index()


# ======================================================================
# 4~5. YoY / QoQ
# ======================================================================
def _growth_screen(panel, item, lag, n, asof, min_base, unit, ascending):
    mat = _mat(panel, item)
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    if q_t not in mat.columns or q_b not in mat.columns:
        raise ValueError(f"분기 데이터 부족: t={q_t}, base={q_b}")
    cur, base = mat[q_t], mat[q_b]
    out = pd.DataFrame({"ticker": mat.index, "company_name": _names(panel).reindex(mat.index).values,
                        "base_q": str(q_b), "t_q": str(q_t),
                        f"{item}(t-{lag})": base.values / unit, f"{item}(t)": cur.values / unit})
    valid = (base > 0) & (base.abs() >= min_base) & cur.notna()
    out["growth_%"] = np.where(valid, (cur - base) / base.abs() * 100, np.nan)
    return out.dropna(subset=["growth_%"]).sort_values("growth_%", ascending=ascending).head(n).reset_index(drop=True)


def yoy_screen(panel: pd.DataFrame, item: str = "매출액", n: int = 30, asof: Optional[str] = None,
               min_base: float = 1e7, unit: float = 1e6, ascending: bool = False) -> pd.DataFrame:
    """YoY 상위 N (t vs t-4). min_base USD (1e7 = $10M)."""
    return _growth_screen(panel, item, 4, n, asof, min_base, unit, ascending)


def qoq_screen(panel: pd.DataFrame, item: str = "매출액", n: int = 30, asof: Optional[str] = None,
               min_base: float = 1e7, unit: float = 1e6, ascending: bool = False) -> pd.DataFrame:
    """QoQ 상위 N (t vs t-1)."""
    return _growth_screen(panel, item, 1, n, asof, min_base, unit, ascending)


# ======================================================================
# 6. 흑자전환
# ======================================================================
def turnaround_screen(panel: pd.DataFrame, basis: str = "yoy", item: str = "영업이익",
                      asof: Optional[str] = None, unit: float = 1e6, min_profit: float = 0.0,
                      sort_by: str = "swing") -> pd.DataFrame:
    lag = 4 if basis.lower() == "yoy" else 1
    mat, rev = _mat(panel, item), _mat(panel, "매출액")
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    cur, base = mat[q_t], mat[q_b]
    sel = mat.index[((base < 0) & (cur > min_profit)).fillna(False)]
    rev_t = rev.reindex(sel)[q_t] if q_t in rev.columns else pd.Series(np.nan, index=sel)
    out = pd.DataFrame({"ticker": sel, "company_name": _names(panel).reindex(sel).values,
                        "base_q": str(q_b), "t_q": str(q_t),
                        f"{item}(t-{lag})": base[sel].values / unit, f"{item}(t)": cur[sel].values / unit,
                        "swing": (cur[sel] - base[sel]).values / unit, "매출액(t)": rev_t.values / unit})
    out["swing_%rev"] = out["swing"] / out["매출액(t)"] * 100
    key = "swing" if sort_by == "swing" else f"{item}(t)"
    return out.sort_values(key, ascending=False).reset_index(drop=True)


# ======================================================================
# 7. 재무비율
# ======================================================================
def _ttm(mat: pd.DataFrame) -> pd.DataFrame:
    return mat.T.rolling(4, min_periods=4).sum().T


def _avg2(mat: pd.DataFrame, lag: int = 4) -> pd.DataFrame:
    prev = mat.shift(lag, axis=1)
    return mat.where(prev.isna(), (mat + prev) / 2)


RATIO_COMPONENTS = {
    "OPM": ["매출액", "영업이익"], "NPM": ["매출액", "당기순이익"], "GPM": ["매출액", "매출총이익"],
    "EBITDA_margin": ["매출액", "EBITDA"],
    "ROE": ["당기순이익", "자본(평균)"], "ROE_지배": ["당기순이익", "지배주주지분(평균)"],
    "ROA": ["당기순이익", "자산(평균)"], "ROIC": ["영업이익", "유효세율_%", "투하자본(평균)"],
    "부채비율": ["부채", "자본"], "순차입금비율": ["순차입금", "자본"],
    "OCF_margin": ["매출액", "영업현금흐름"], "FCF_margin": ["매출액", "FCF"],
    "FCF_conversion": ["당기순이익", "FCF"],
}


def compute_ratios(panel: pd.DataFrame, asof: Optional[str] = None, ttm: bool = True,
                   default_tax: float = 0.21, unit: float = 1e6) -> pd.DataFrame:
    """ticker 별 1행: 관련 재무데이터($M) + 비율(%). 손익/CF 는 TTM 합, BS 는 기초·기말 평균."""
    need = ["매출액", "매출총이익", "영업이익", "EBITDA", "세전이익", "법인세", "당기순이익",
            "자산", "자본", "지배주주지분", "부채", "총차입금", "순차입금", "현금성자산", "현금", "단기금융상품",
            "영업현금흐름", "CAPEX", "FCF"]
    have = set(panel["concept"])
    mats = {c: _mat(panel, c) for c in need if c in have}
    all_q = sorted(set().union(*[m.columns for m in mats.values()]))
    all_tk = sorted(set().union(*[m.index for m in mats.values()]))
    empty = pd.DataFrame(np.nan, index=all_tk, columns=all_q)
    mats = {c: (mats[c].reindex(index=all_tk, columns=all_q) if c in mats else empty.copy()) for c in need}
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mats["매출액"])

    def flow(c): return _ttm(mats[c])[q_t] if ttm else mats[c][q_t] * 4
    def end(c):  return mats[c][q_t]
    def avg(c):  return _avg2(mats[c])[q_t]

    rev, gp, op, ebitda = flow("매출액"), flow("매출총이익"), flow("영업이익"), flow("EBITDA")
    ni, pretax, tax = flow("당기순이익"), flow("세전이익"), flow("법인세")
    ocf, capex = flow("영업현금흐름"), flow("CAPEX").abs()
    fcf = flow("FCF")
    fcf = fcf.where(fcf.notna(), ocf - capex)
    cash_m = mats["현금성자산"].where(mats["현금성자산"].notna(), mats["현금"].fillna(0) + mats["단기금융상품"].fillna(0))
    nd_m = mats["순차입금"].where(mats["순차입금"].notna(), mats["총차입금"].fillna(0) - cash_m)
    ic_m = mats["자본"] + nd_m
    eff_tax = (tax / pretax).where((pretax > 0) & (tax >= 0)).clip(0, 0.35).fillna(default_tax)
    nopat = op * (1 - eff_tax)

    o = pd.DataFrame(index=all_tk)
    o["company_name"] = _names(panel).reindex(all_tk)
    o["t_q"] = str(q_t)
    for k, v in [("매출액", rev), ("매출총이익", gp), ("영업이익", op), ("EBITDA", ebitda), ("당기순이익", ni),
                 ("영업현금흐름", ocf), ("CAPEX", capex), ("FCF", fcf),
                 ("자산(평균)", avg("자산")), ("자본(평균)", avg("자본")), ("지배주주지분(평균)", avg("지배주주지분")),
                 ("자산", end("자산")), ("자본", end("자본")), ("부채", end("부채")),
                 ("순차입금", nd_m[q_t]), ("투하자본(평균)", _avg2(ic_m)[q_t])]:
        o[k] = v / unit
    o["유효세율_%"] = eff_tax * 100
    o["GPM"], o["OPM"], o["NPM"] = gp / rev * 100, op / rev * 100, ni / rev * 100
    o["EBITDA_margin"] = ebitda / rev * 100
    o["ROE"] = ni / avg("자본") * 100
    o["ROE_지배"] = ni / avg("지배주주지분") * 100
    o["ROA"] = ni / avg("자산") * 100
    o["ROIC"] = nopat / _avg2(ic_m)[q_t] * 100
    o["부채비율"] = end("부채") / end("자본") * 100
    o["순차입금비율"] = nd_m[q_t] / end("자본") * 100
    o["OCF_margin"] = ocf / rev * 100
    o["FCF_margin"] = fcf / rev * 100
    o["FCF_conversion"] = fcf / ni * 100
    o.loc[(avg("자본") <= 0) | (end("자본") <= 0), ["ROE", "ROIC", "부채비율", "순차입금비율"]] = np.nan
    o.loc[rev <= 0, ["GPM", "OPM", "NPM", "EBITDA_margin", "OCF_margin", "FCF_margin"]] = np.nan
    o.loc[_avg2(ic_m)[q_t] <= 0, "ROIC"] = np.nan
    o.loc[ni <= 0, "FCF_conversion"] = np.nan
    o.index.name = "ticker"
    return o.reset_index()


def ratio_screen(panel: Optional[pd.DataFrame], ratio: str = "ROE", n: int = 30, asof: Optional[str] = None,
                 ttm: bool = True, ascending: bool = False, min_revenue: float = 1e8,
                 ratios_df: Optional[pd.DataFrame] = None, unit: float = 1e6) -> pd.DataFrame:
    """비율 상위 N. min_revenue USD (TTM, 1e8 = $100M). ratios_df 를 넘기면 정렬만."""
    ratio = ratio.upper() if ratio.lower() in ("opm", "npm", "gpm", "roe", "roa", "roic") else ratio
    if ratio not in RATIO_COMPONENTS:
        raise ValueError(f"지원 비율: {list(RATIO_COMPONENTS)}")
    r = ratios_df if ratios_df is not None else compute_ratios(panel, asof, ttm, unit=unit)
    r = r[r["매출액"] * unit >= min_revenue].dropna(subset=[ratio]).sort_values(ratio, ascending=ascending)
    others = [c for c in ["OPM", "NPM", "ROE", "ROA", "ROIC", "부채비율"] if c != ratio]
    return r[["ticker", "company_name", "t_q"] + RATIO_COMPONENTS[ratio] + [ratio] + others].head(n).reset_index(drop=True)


# ======================================================================
# 8. 적재 상태 점검 (V4 저장 라이브러리는 INSERT 만 하므로 중복 누적 확인)
# ======================================================================
def duplicate_report(engine: Engine) -> pd.DataFrame:
    """테이블별 전체 행 / 고유 (ticker,date,item) 수 / 중복 배수. 배수가 커지면 --truncate 후 재적재 권장."""
    rows = []
    for sj, tbl in TABLES.items():
        sql = text(f"""SELECT SUM(c) AS total_rows, COUNT(*) AS unique_keys,
                              COUNT(DISTINCT ticker) AS n_ticker, MAX(date) AS max_date
                       FROM (SELECT ticker, date, item, COUNT(*) AS c FROM {tbl} GROUP BY ticker, date, item) g""")
        with engine.connect() as con:
            r = pd.read_sql(sql, con).iloc[0]
        rows.append(dict(table=tbl, total_rows=int(r["total_rows"]), unique_keys=int(r["unique_keys"]),
                         dup_factor=round(r["total_rows"] / max(r["unique_keys"], 1), 2),
                         n_ticker=int(r["n_ticker"]), max_date=r["max_date"]))
    return pd.DataFrame(rows)
