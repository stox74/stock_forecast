# -*- coding: utf-8 -*-
"""2d 작업 2 B: 드라이버 후보 생성, 상관 순위, 매핑 엑셀 v1 (명세서 2d v2 4절 + 9절 3, 5). DB 쓰기 없음.

선정 구간 2019Q1~2023Q4 자료만 쓴다 (분기 매출 YoY 는 2018 값이 필요해 2018 자료를 읽지만 상관은 2019Q1~2023Q4).

1. 수출 후보 (source 별)
   - dart: k2_products 의 '주요 제품' 행 글자에 hs_keywords.KW4 키워드가 들어 있으면 그 4자리 호가 후보.
     수출 테이블에 그 4자리 코드가 있으면 4자리 그대로, 없으면 그 아래 6자리 코드들. 6자리 품목명(HS_CODE_MAP 등)에
     행 글자의 3글자 이상 낱말이 들어 있으면 그 6자리도 후보 (direct). 단 6자리 품목명 3개 넘게 나오는 낱말
     ('사용되는', '자동차' 등 흔한 말)은 direct 일치에 쓰지 않는다 (DF_MAX). 조사·어미로 끝나는 말(NONNOUN_END)과
     범주어(GENERIC: 반도체, 디스플레이, 시스템 등)도 쓰지 않는다. 주력 품목(행 비율 20% 이상)에서 direct 로 잡힌
     6자리는 direct6 = True (승인 기준 '주력 품목과 직접 맞는 6자리').
   - 위 표 추출이 'ok' 가 아니거나 dart 후보가 하나도 없으면 hs_map (kr_company_hs_map), 그것도 없으면 ksic
     (hs_keywords.KSIC_HS, 업종코드 앞자리 최장 일치) (9절 3).
   - 수출 테이블에서 2018-01 이전부터 2023-12 이후까지 자료가 있는 코드만 쓴다.
   - 분기 수출 = 월 수출액(달러) 합. 빈 달은 0 (2c 규칙).
2. 내수 후보: hs_keywords.KSIC_DOMESTIC (업종코드 앞자리가 맞는 항목 모두). 분기 값은 금액·인원·면적이면 합, 지수·가격이면 평균.
   - 소매 업태(store:)는 2020-01 부터라 2019Q1~2020Q4 YoY 는 한국은행 소매판매액지수 YoY 로 채운다 (9절 6).
   - 한국은행 지표 공표 시차는 bok_economic_indicators.available_at - 기간말 (lag_days) 로 기록만 한다.
3. 상관: 분기 매출 YoY(fd_rev_adj_2d) 와 후보 YoY 의 Pearson 상관.
   corr_full (2019Q1~2023Q4, 12분기 이상), corr_excovid (2020Q1~2021Q4 제외, 8분기 이상),
   corr_lead1 (후보 1분기 앞선 값, 보고만). 순위는 corr_full.
4. 기업당 상관 상위 3개 + direct6 후보를 제시. 승인 기준 통과 = corr_full >= 0.3 또는 direct6.
   통과 기업을 최고 corr_full 순으로 정렬해 상위 80곳은 매핑 엑셀 본 시트, 나머지 통과 기업은 '대기' 시트.
   통과 못 한 기업은 '드라이버 없음 (시계열만)'.
6. 위약검정 (메모리 규칙: 무관 품목 + 시간추세 + 경기 공통요인). 판정·선정 기준은 바꾸지 않고 열로만 붙인다.
   - placebo_best_mean: 그 기업 후보가 아닌 수출 코드에서 수출 후보 수만큼 무작위로 50번 뽑았을 때 최고 corr_full 평균
     (seed 0). placebo_pass_share: 그 50번 중 최고 상관이 0.3 이상인 비율.
   - corr_total_exports / corr_ip / corr_trend: 한국은행 수출금액지수·전산업생산지수 YoY, 시간 순번과의 상관.
   - 대안 순위 (참고 시트): 통과 기업 중 최고 후보의 corr_excovid >= 0.3 이고, 최고 상관 - placebo_best_mean 이 큰 순 80곳.
5. 기존 대조군(한국화장품제조 003350, 동원산업 006040)도 같은 방법으로 돌려 '재편입_후보' 시트에 둔다 (2절).
실행: python -m DATA.fdriver.eval2d.k2_candidates
"""
import json
import logging
import re
from typing import Dict, List

import numpy as np
import pandas as pd

from .. import db
from ..adapters import read_revenue, read_series
from ..config import output_path
from . import hs_keywords as H
from .k1_survey import SEL
from .k2_targets import read_adj

log = logging.getLogger(__name__)
OUT = output_path("2d", "K2")
MAP_FILE = "fdriver_2d_driver_mapping_v1.xlsx"
COVID = pd.period_range("2020Q1", "2021Q4", freq="Q").to_timestamp(how="end").normalize()
READ_START = pd.Timestamp("2017-01-01")
SEL_END = SEL[-1]
CORR_MIN = 0.30
MAIN_PCT = 20.0
N_TOP = 3
N_MAIN = 80
DF_MAX = 3
NONNOUN_END = ("는", "며", "의", "을", "를", "에", "로", "와", "과", "한", "된", "고", "등", "용", "서", "여", "다")
GENERIC = {"반도체", "디스플레이", "시스템", "자동차", "전자부품", "부분품", "부속품", "화학제품", "플라스틱", "알루미늄",
           "스테인리스", "합성수지", "의약품", "화장품", "기계류", "소프트웨어", "솔루션", "서비스", "전자제품", "전기기기",
           "산업기계", "건설기계", "금속제품", "섬유제품", "식료품", "식품류", "원재료", "완제품", "반제품",
           "임가공", "있도록", "비중이", "분리되어", "사용되어", "이루어진", "만들어진", "포함되어"}
CONTROLS = {"003350": "한국화장품제조", "006040": "동원산업"}


# ------------------------------------------------------------------ 수출 코드 목록·이름
def trade_inventory() -> pd.DataFrame:
    d = db.read_sql("SELECT root_hs_code code, MIN(date) first, MAX(date) last FROM korea_monthly_trade_data "
                    "WHERE indicator = 'expDlr' GROUP BY root_hs_code")
    d["code"] = d["code"].astype(str)
    d = d[(pd.to_datetime(d["first"]) <= "2018-01-31") & (pd.to_datetime(d["last"]) >= "2023-12-31")]
    d = d[d["code"].str.len().isin([4, 6])].copy()
    d["level"] = d["code"].str.len()
    return d.set_index("code")


def hs_names() -> Dict[str, str]:
    from DATA.hs_code_map_v2 import HS_CODE_MAP
    names = {str(k): str(v) for k, v in HS_CODE_MAP.items()}
    m = db.read_sql("SELECT hs_code, hs6, hs_name FROM kr_company_hs_map WHERE hs_name IS NOT NULL")
    for r in m.itertuples():
        names.setdefault(str(r.hs6), str(r.hs_name))
    for k, kws in H.KW4.items():
        names.setdefault(k, "/".join(kws[:4]) + " (키워드)")
    return names


def export_yoy(codes: List[str]) -> pd.DataFrame:
    """quarter x code 의 분기 수출 YoY (달러)."""
    parts = []
    for i in range(0, len(codes), 200):
        f, p = db.in_clause("c", codes[i:i + 200])
        parts.append(db.read_sql(f"SELECT root_hs_code code, date, value FROM korea_monthly_trade_data "
                                 f"WHERE indicator = 'expDlr' AND root_hs_code IN {f} AND date >= :s AND date <= :e",
                                 {**p, "s": str(READ_START.date()), "e": str(SEL_END.date())}))
    d = pd.concat(parts, ignore_index=True)
    d["q"] = pd.to_datetime(d["date"]) + pd.offsets.QuarterEnd(0)
    w = d.pivot_table(index="q", columns="code", values="value", aggfunc="sum").fillna(0.0)
    w = w.reindex(pd.period_range(READ_START, SEL_END, freq="Q").to_timestamp(how="end").normalize()).fillna(0.0)
    y = w / w.shift(4) - 1
    return y.replace([np.inf, -np.inf], np.nan)


# ------------------------------------------------------------------ 내수 시계열
FLOW_BOK = {"건설기성액_불변_계절조정", "건설수주액", "건축착공면적", "건축허가면적"}


def _q(df: pd.DataFrame, how: str) -> pd.Series:
    s = df.set_index("period_end")["value"].sort_index()
    s.index = pd.to_datetime(s.index)
    g = s.groupby(s.index + pd.offsets.QuarterEnd(0))
    q = g.sum(min_count=1) if how == "sum" else g.mean()
    n = g.size()
    full = n >= (3 if how == "sum" else 1)
    return q[full]


def domestic_series(names: List[str]) -> Dict[str, Dict]:
    """이름 -> {'yoy': 분기 YoY Series, 'note', 'lag_days'}."""
    out = {}
    info = db.read_sql("SELECT series_id, table_alias, c1_name, itm_name FROM kr_kosis_series_info "
                       "WHERE table_alias IN ('retail_sales_by_store_type','retail_sales_index')")
    bok_names = sorted({n.split(":", 1)[1] for n in names if n.startswith("bok:")} | {"소매판매액지수_계절조정"})
    f, p = db.in_clause("b", bok_names)
    bok = db.read_sql(f"SELECT Index_Name, Date AS period_end, Value AS value, lag_days FROM bok_economic_indicators "
                      f"WHERE Index_Name IN {f} AND Date >= :s", {**p, "s": str(READ_START.date())})

    def yoy(q):
        r = q / q.shift(4) - 1
        return r.replace([np.inf, -np.inf], np.nan)

    def bok_yoy(name):
        b = bok[bok["Index_Name"] == name]
        if b.empty:
            return None, None
        how = "sum" if name in FLOW_BOK else "mean"
        return yoy(_q(b[["period_end", "value"]], how)), int(b["lag_days"].max())

    retail_bok, _ = bok_yoy("소매판매액지수_계절조정")
    for n in sorted(set(names)):
        kind, key = n.split(":", 1)
        note, lag = "", None
        if kind == "store":
            sid = info[(info["table_alias"] == "retail_sales_by_store_type") & (info["c1_name"] == key)]["series_id"]
            s = read_series("kosis", sid.tolist(), start=READ_START)
            y = yoy(_q(s[["period_end", "value"]], "sum"))
            fill = y.isna() & y.index.isin(retail_bok.index)
            y = y.reindex(y.index.union(retail_bok.index))
            miss = y.isna()
            y[miss] = retail_bok.reindex(y.index)[miss]
            note, lag = "2021Q1 전 YoY 는 한국은행 소매판매액지수_계절조정 YoY 로 채움", 35
        elif kind == "kosis_idx":
            sid = info[(info["table_alias"] == "retail_sales_index") & (info["c1_name"] == key)
                       & (info["itm_name"] == "경상지수")]["series_id"]
            s = read_series("kosis", sid.tolist(), start=READ_START)
            y, lag = yoy(_q(s[["period_end", "value"]], "mean")), 35
            note = "KOSIS 상품군별 소매판매액지수 경상지수"
        elif kind == "bok":
            y, lag = bok_yoy(key)
            note = "bok_economic_indicators (available_at 근사, 신뢰도 C)"
        elif kind == "tourism":
            s = read_series("tourism", [key], start=READ_START)
            y, lag = yoy(_q(s[["period_end", "value"]], "sum")), 35
            note = "방한객 합계" if key.startswith("E") else "출국자 합계"
        elif kind == "fred":
            s = read_series("fred", [key], start=READ_START)
            y, lag = yoy(_q(s[["period_end", "value"]], "mean")), 1
            note = "FRED 일간 분기 평균"
        elif kind == "komis":
            sid = db.read_sql("SELECT series_id FROM kr_komis_mineral_price_info WHERE mineral_name = :m "
                              "AND price_criteria_name = 'LME CASH'", {"m": key})["series_id"].tolist()
            s = read_series("komis", sid, start=READ_START)
            y, lag = yoy(_q(s[["period_end", "value"]], "mean")), 1
            note = "KOMIS LME CASH 분기 평균"
        else:
            continue
        if y is not None:
            out[n] = {"yoy": y[y.index <= SEL_END], "note": note, "lag_days": lag}
    return out


# ------------------------------------------------------------------ 후보 만들기
TOKEN = re.compile(r"[가-힣A-Za-z0-9]{3,}")


def _tokens(label: str) -> set:
    return {t for t in TOKEN.findall(label) if t not in H.STOPWORDS and not t.isdigit()}


def dart_candidates(rows: pd.DataFrame, inv: pd.DataFrame, names: Dict[str, str], kw4: Dict = None,
                    nospace: bool = False) -> pd.DataFrame:
    """kw4: 키워드 사전 (기본 H.KW4). nospace=True 면 행 글자와 키워드의 띄어쓰기를 지우고 맞춘다 (K2b)."""
    kw4 = kw4 or H.KW4
    inv4 = set(inv.index[inv["level"] == 4])
    inv6 = inv.index[inv["level"] == 6]
    kids = {c4: [c for c in inv6 if c.startswith(c4)] for c4 in kw4}
    name6 = {c: names.get(c, "") for c in inv6}
    def specific(t):     # 품목명에서 흔하지 않은 명사만 (부분 문자열 일치라 낱말이 품목명 몇 개에 들어 있는지로 센다)
        if t in GENERIC or t.endswith(NONNOUN_END):
            return False
        return sum(t in nm for nm in name6.values()) <= DF_MAX

    spec_cache = {}
    out = []
    for r in rows.itertuples():
        lab = str(r.label)
        up = re.sub(r"\s+", "", lab).upper() if nospace else lab.upper()
        toks = set()
        for t in _tokens(lab):
            if t not in spec_cache:
                spec_cache[t] = specific(t)
            if spec_cache[t]:
                toks.add(t)
        pct = float(r.pct) if pd.notna(r.pct) else 0.0
        hits = set()
        for c4, kws in kw4.items():
            kw = next((k for k in kws if (re.sub(r"\s+", "", k) if nospace else k).upper() in up), None)
            if kw is None:
                continue
            if c4 in inv4:
                out.append({"ticker": r.ticker, "code": c4, "kw": kw, "pct": pct, "direct": False})
            for c in kids.get(c4, []):
                direct = any(t in name6[c] for t in toks)
                if direct or c4 not in inv4:
                    out.append({"ticker": r.ticker, "code": c, "kw": kw, "pct": pct, "direct": direct})
                    hits.add(c)
        if pct >= 5:
            for c in inv6:
                if c in hits or not name6[c]:
                    continue
                t = next((t for t in toks if len(t) >= 3 and t in name6[c]), None)
                if t:
                    out.append({"ticker": r.ticker, "code": c, "kw": t, "pct": pct, "direct": True})
    d = pd.DataFrame(out)
    if d.empty:
        return d
    d["direct6"] = d["direct"] & (d["code"].str.len() == 6) & (d["pct"] >= MAIN_PCT)
    g = d.groupby(["ticker", "code"]).agg(product_share=("pct", "sum"), kw=("kw", lambda s: ",".join(sorted(set(s)))),
                                          direct6=("direct6", "any")).reset_index()
    g["product_share"] = g["product_share"].clip(upper=100)
    g["source"] = "dart"
    return g


def hsmap_candidates(tickers: List[str], inv: pd.DataFrame) -> pd.DataFrame:
    f, p = db.in_clause("t", tickers)
    m = db.read_sql(f"SELECT ticker, hs_code, hs6 FROM kr_company_hs_map WHERE is_active = 1 AND ticker IN {f}", p)
    out = []
    for r in m.itertuples():
        for c in (str(r.hs6), str(r.hs_code)[:4]):
            if c in inv.index:
                out.append({"ticker": r.ticker, "code": c})
                break
    d = pd.DataFrame(out, columns=["ticker", "code"]).drop_duplicates()
    return d.assign(product_share=np.nan, kw="", direct6=False, source="hs_map")


def ksic_candidates(firms: pd.DataFrame, inv: pd.DataFrame) -> pd.DataFrame:
    inv4 = set(inv.index[inv["level"] == 4])
    inv6 = inv.index[inv["level"] == 6]
    out = []
    for r in firms.itertuples():
        k = str(r.ksic or "")
        key = next((k[:n] for n in range(len(k), 1, -1) if k[:n] in H.KSIC_HS), None)
        if key is None:
            continue
        for c4 in H.KSIC_HS[key]:
            codes = [c4] if c4 in inv4 else [c for c in inv6 if c.startswith(c4)]
            out += [{"ticker": r.ticker, "code": c, "kw": f"KSIC {key}"} for c in codes]
    d = pd.DataFrame(out, columns=["ticker", "code", "kw"]).drop_duplicates(["ticker", "code"])
    return d.assign(product_share=np.nan, direct6=False, source="ksic")


def domestic_candidates(firms: pd.DataFrame) -> pd.DataFrame:
    out = []
    for r in firms.itertuples():
        k = str(r.ksic or "")
        for pre, ns in H.KSIC_DOMESTIC.items():
            if k.startswith(pre):
                out += [{"ticker": r.ticker, "code": n, "kw": f"KSIC {pre}"} for n in ns]
    d = pd.DataFrame(out, columns=["ticker", "code", "kw"]).drop_duplicates(["ticker", "code"])
    return d.assign(product_share=np.nan, direct6=False, source="domestic")


# ------------------------------------------------------------------ 상관
def rev_yoy_frame(tickers: List[str]) -> pd.DataFrame:
    a = read_adj(tickers)
    w = a.pivot_table(index="period_end", columns="entity_id", values="revenue_adj")
    w = w[(w.index >= "2018-01-01") & (w.index <= SEL_END)]
    return (w / w.shift(4) - 1).replace([np.inf, -np.inf], np.nan)


def _corr(a: pd.Series, b: pd.Series, n_min: int) -> (float, int):
    m = pd.concat([a, b], axis=1).dropna()
    if len(m) < n_min or m.iloc[:, 0].std() == 0 or m.iloc[:, 1].std() == 0:
        return np.nan, len(m)
    return float(m.iloc[:, 0].corr(m.iloc[:, 1])), len(m)


def correlations(cands: pd.DataFrame, rev: pd.DataFrame, xq: pd.DataFrame, dom: Dict[str, Dict]) -> pd.DataFrame:
    rows = []
    for r in cands.itertuples():
        if r.ticker not in rev.columns:
            continue
        y = rev[r.ticker]
        x = dom[r.code]["yoy"] if r.source == "domestic" else (xq[r.code] if r.code in xq.columns else None)
        if x is None:
            continue
        x = x.reindex(x.index.union(y.index))
        ys, xs = y[y.index.isin(SEL)], x[x.index.isin(SEL)]
        cf, nf = _corr(ys, xs, 12)
        ex = ~ys.index.isin(COVID)
        ce, ne = _corr(ys[ex], xs.reindex(ys.index)[ex], 8)
        cl, nl = _corr(ys, x.shift(1).reindex(ys.index), 12)
        rows.append({**r._asdict(), "corr_full": cf, "n_full": nf, "corr_excovid": ce, "n_excovid": ne,
                     "corr_lead1": cl})
    d = pd.DataFrame(rows).drop(columns=["Index"], errors="ignore")
    return d


def rank_and_select(c: pd.DataFrame, firms: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    c = c.sort_values(["ticker", "corr_full"], ascending=[True, False], na_position="last").copy()
    c["rank"] = c.groupby("ticker").cumcount() + 1
    c["n_candidates"] = c.groupby("ticker")["code"].transform("size")
    c["pass"] = (c["corr_full"] >= CORR_MIN) | c["direct6"].astype(bool)
    shown = c[(c["rank"] <= N_TOP) | c["direct6"].astype(bool)].copy()
    best = shown[shown["pass"]].groupby("ticker")["corr_full"].max()
    firm = firms.copy()
    firm["n_candidates"] = firm["ticker"].map(c.groupby("ticker").size()).fillna(0).astype(int)
    firm["best_corr"] = firm["ticker"].map(c.groupby("ticker")["corr_full"].max())
    firm["best_pass_corr"] = firm["ticker"].map(best)
    firm["passed"] = firm["ticker"].isin(best.index)
    firm["has_direct6"] = firm["ticker"].isin(set(c.loc[c["direct6"].astype(bool), "ticker"]))
    firm["sources"] = firm["ticker"].map(c.groupby("ticker")["source"].agg(lambda s: ",".join(sorted(set(s)))))
    p = firm[firm["passed"]].sort_values("best_pass_corr", ascending=False, na_position="last")
    firm["tier"] = np.where(firm["ticker"].isin(p["ticker"].head(N_MAIN)), "승인 대상",
                            np.where(firm["passed"], "대기", "드라이버 없음"))
    return {"cands": c, "shown": shown, "firm": firm}


# ------------------------------------------------------------------ 위약검정
N_PLACEBO = 50


def placebo(firm: pd.DataFrame, c: pd.DataFrame, rev: pd.DataFrame, xq_all: pd.DataFrame,
            macro: Dict[str, Dict], per_firm_seed: bool = False, n_reps: int = N_PLACEBO) -> pd.DataFrame:
    """per_firm_seed=False (작업 2): 난수열 하나를 기업 순서대로 이어 씀. True (K2b): 기업마다 seed [0, 종목코드]
    라서 다른 기업의 후보 수가 바뀌어도 그 기업의 위약 값은 그대로다."""
    rng = np.random.default_rng(0)
    ys = rev[rev.index.isin(SEL)]
    xs = xq_all[xq_all.index.isin(SEL)]
    rows = []
    for t in firm["ticker"]:
        out = {"ticker": t}
        if t not in ys.columns:
            rows.append(out)
            continue
        y = ys[t]
        if per_firm_seed:
            rng = np.random.default_rng([0, int(t) if str(t).isdigit() else abs(hash(t)) % 10 ** 8])
        own = set(c.loc[(c["ticker"] == t) & (c["source"] != "domestic"), "code"])
        n = max(len(own), 1)
        cm = xs[[k for k in xs.columns if k not in own]].corrwith(y).dropna()
        reps = [cm.iloc[rng.choice(len(cm), size=min(n, len(cm)), replace=False)].max() for _ in range(n_reps)]
        out["placebo_best_mean"] = float(np.mean(reps))
        out["placebo_pass_share"] = float(np.mean(np.array(reps) >= CORR_MIN))
        yy = y.dropna()
        out["corr_trend"] = float(np.corrcoef(yy, np.arange(len(yy)))[0, 1]) if len(yy) > 5 else np.nan
        for k, col in (("bok:수출금액지수", "corr_total_exports"), ("bok:전산업생산지수", "corr_ip")):
            out[col] = _corr(y, macro[k]["yoy"].reindex(y.index), 12)[0]
        rows.append(out)
    return pd.DataFrame(rows)


def alt_rank(firm: pd.DataFrame, shown: pd.DataFrame) -> pd.DataFrame:
    best = shown[shown["pass"]].sort_values("corr_full", ascending=False).drop_duplicates("ticker")
    f = firm.merge(best[["ticker", "corr_excovid"]].rename(columns={"corr_excovid": "best_corr_excovid"}),
                   on="ticker", how="left")
    f["excess_over_placebo"] = f["best_pass_corr"] - f["placebo_best_mean"]
    ok = f["passed"] & (f["best_corr_excovid"] >= CORR_MIN)
    top = f[ok].sort_values("excess_over_placebo", ascending=False).head(N_MAIN)
    f["alt_rank"] = f["ticker"].map({t: i + 1 for i, t in enumerate(top["ticker"])})
    return f


# ------------------------------------------------------------------ 엑셀
def write_excel(res: Dict, names: Dict[str, str], dom: Dict[str, Dict], products: pd.DataFrame,
                pstatus: pd.DataFrame, controls: Dict, targets: pd.DataFrame) -> None:
    firm, shown = res["firm"], res["shown"]

    def code_rows(f):
        s = shown[shown["ticker"].isin(f["ticker"])].merge(f[["ticker", "name"]], on="ticker", how="left")
        s["item"] = [dom[c]["note"] if src == "domestic" else names.get(c, "") for c, src in zip(s["code"], s["source"])]
        s["lag"] = [dom[c]["lag_days"] if src == "domestic" else "" for c, src in zip(s["code"], s["source"])]
        out = pd.DataFrame({
            "종목코드": s["ticker"], "기업": s["name"], "출처": s["source"], "HS 코드 / 내수 계열": s["code"],
            "품목 (참고)": s["item"], "일치 키워드": s["kw"], "DART 매출 비중 합(%)": s["product_share"],
            "순위": s["rank"], "corr_full (2019~2023)": s["corr_full"].round(3), "n": s["n_full"],
            "corr_excovid": s["corr_excovid"].round(3), "corr_lead1 (보고만)": s["corr_lead1"].round(3),
            "주력 6자리 직접 일치": np.where(s["direct6"], "Y", ""), "승인 기준 통과": np.where(s["pass"], "Y", ""),
            "공표 시차(일)": s["lag"], "사용 (Y/N)": "", "세그먼트": "", "6자리 전개 (Y/공란)": "", "메모": ""})
        return out.sort_values(["종목코드", "순위"])

    def firm_rows(f):
        t = f.merge(pstatus[["ticker", "status", "fy", "pct_sum"]], on="ticker", how="left")
        return pd.DataFrame({
            "종목코드": t["ticker"], "기업": t["name"], "시장": t["market"], "업종": t["ksic_div_name"],
            "KSIC": t["ksic"], "coverage_class": t["coverage_class"], "최근4분기 매출(억)": t["rev_last4_sum_eok"],
            "매출 기준": t["basis"], "flag_basis (기준불일치)": t["flag_basis"], "flag_jump (급변)": t["flag_jump"],
            "급변 분기 수": t["rev_jump_q"], "DART 표 추출": t["status"], "DART 사업연도": t["fy"],
            "후보 출처": t["sources"], "후보 수": t["n_candidates"], "최고 corr_full": t["best_corr"].round(3),
            "주력 6자리 직접 일치": np.where(t["has_direct6"], "Y", ""),
            "최고 후보 corr_excovid": t["best_corr_excovid"].round(3),
            "위약 최고상관 평균": t["placebo_best_mean"].round(3), "위약 0.3 통과율": t["placebo_pass_share"].round(2),
            "수출총액지수 상관": t["corr_total_exports"].round(3), "생산지수 상관": t["corr_ip"].round(3),
            "위약 대비 초과": t["excess_over_placebo"].round(3), "대안 순위": t["alt_rank"], "드라이버 유형": "",
            "포함 여부 (Y/N/검토)": "", "메모": ""})

    main = firm[firm["tier"] == "승인 대상"]
    wait = firm[firm["tier"] == "대기"]
    seg = products[products["ticker"].isin(main["ticker"])].merge(main[["ticker", "name"]], on="ticker")
    seg_rows = pd.DataFrame({"종목코드": seg["ticker"], "기업": seg["name"], "세그먼트": "",
                             "DART 행 (참고)": seg["label"].str[-120:], "DART 비율(%)": seg["pct"],
                             "가중치 (0~1)": "", "적용 시작 연도": "", "드라이버 있음 (Y/N)": "",
                             "근거": "사업보고서 " + seg["fy"].astype(str)})
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str})
    ev = targets[targets["ticker"].isin(main["ticker"]) & (targets["flag_jump"] == 1)].merge(
        k1[["ticker", "rev_jump_quarters"]], on="ticker", how="left")
    ev_rows = pd.DataFrame({"종목코드": ev["ticker"], "기업": ev["name"], "분기": ev["rev_jump_quarters"],
                            "내용": "매출 전년 동기 대비 2배 초과 또는 절반 미만 분기 4개 이상 (급변)",
                            "기본안": "D1 판정에서 따로 보고 (9절 4)", "판단 (제외/유지/공시 확인)": "", "메모": ""})
    guide = pd.DataFrame({"fdriver 2d 드라이버 매핑 입력표 v1": [
        f"작성: {pd.Timestamp.today().date()} (2d 작업 2). 명세서 2d v2 4절 + 9절.",
        "기업: 승인 기준(상관 0.3 이상 또는 주력 6자리 직접 일치)을 넘는 기업 중 최고 상관 순 상위 80곳.",
        "코드: 기업당 상관 상위 3개 + 주력 6자리 직접 일치 후보. '사용 (Y/N)'과 '세그먼트'를 채워 주세요.",
        "출처: dart (사업보고서 주요 제품 -> 키워드), hs_map (kr_company_hs_map), ksic (업종 기반), domestic (내수 4.2).",
        "상관: 분기 매출 YoY(fd_rev_adj_2d) vs 후보 YoY, 2019Q1~2023Q4. excovid 는 2020Q1~2021Q4 제외. lead1 은 보고만.",
        "후보 수가 많은 기업(ksic 출처 등)은 최고 상관이 우연히 높게 나올 수 있습니다 ('후보 수' 열).",
        "세그먼트_가중치: DART 주요 제품 행과 비율을 미리 넣었습니다. 사업부 가중평균이 필요하면 세그먼트·가중치를 채워 주세요.",
        "기업사건: 급변 표시 기업. 대기: 기준은 넘었지만 80곳 밖. 재편입_후보: 기존 대조군.",
        "위약검정: 무관 수출 코드를 같은 수만큼 무작위로 뽑은 최고 상관(평균), 수출총액지수·생산지수 상관. 높을수록 우연·경기 공통요인.",
        "대안순위_80 (참고): corr_excovid 0.3 이상이면서 '최고 상관 - 위약 평균'이 큰 순. 명세서 기준(기업 시트)과 다를 수 있습니다.",
        "이 파일의 HS 코드·드라이버명·가중치는 어떤 공개물에도 넣지 않습니다 (7절).",
    ]})
    ctl_rows = None
    if controls:
        cf = controls["firm"]
        ctl_rows = pd.concat([firm_rows(cf), code_rows(cf).assign(구분="코드")], ignore_index=True) \
            if False else code_rows(controls["firm"]) if not controls["firm"].empty else None
    with pd.ExcelWriter(OUT / MAP_FILE, engine="openpyxl") as xw:
        guide.to_excel(xw, sheet_name="안내", index=False)
        firm_rows(main).to_excel(xw, sheet_name="기업", index=False)
        code_rows(main).to_excel(xw, sheet_name="코드", index=False)
        seg_rows.to_excel(xw, sheet_name="세그먼트_가중치", index=False)
        ev_rows.to_excel(xw, sheet_name="기업사건", index=False)
        firm_rows(wait).to_excel(xw, sheet_name="대기_기업", index=False)
        alt = firm[firm["alt_rank"].notna()].sort_values("alt_rank")
        firm_rows(alt).to_excel(xw, sheet_name="대안순위_80", index=False)
        code_rows(wait).to_excel(xw, sheet_name="대기_코드", index=False)
        if ctl_rows is not None:
            ctl_rows.to_excel(xw, sheet_name="재편입_후보", index=False)


# ------------------------------------------------------------------ main
def control_frame() -> Dict:
    """대조군 2곳: 매출은 read_revenue (fd_rev_adj_2d 밖), 제품 표는 k2_products.extract."""
    from . import k2_products as P
    ids = list(CONTROLS)
    cm = db.read_sql("SELECT corp_code, stock_code FROM korea_dart_corp_master WHERE stock_code IN ('003350','006040')")
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "ksic": str})
    f = k1[k1["ticker"].isin(ids)].copy()
    rows, st = [], []
    for t in ids:
        cc = cm.loc[cm["stock_code"] == t, "corp_code"].iloc[0]
        res, rr = P.extract(t, cc)
        st.append({"ticker": t, **res})
        rows += rr
    rev = read_revenue(ids, start="2017-01-01")
    w = rev.pivot_table(index="period_end", columns="key", values="value")
    w = w[(w.index >= "2018-01-01") & (w.index <= SEL_END)]
    return {"firm": f, "rows": pd.DataFrame(rows), "status": pd.DataFrame(st), "rev_yoy": w / w.shift(4) - 1}


def build(firms: pd.DataFrame, rows: pd.DataFrame, pstatus: pd.DataFrame, inv, names, xq_cache=None):
    ok = set(pstatus.loc[pstatus["status"] == "ok", "ticker"])
    dc = dart_candidates(rows[rows["ticker"].isin(ok)], inv, names) if len(rows) else pd.DataFrame()
    have_dart = set(dc["ticker"]) if len(dc) else set()
    rest = [t for t in firms["ticker"] if t not in have_dart]
    hm = hsmap_candidates(rest, inv) if rest else pd.DataFrame()
    have_map = set(hm["ticker"]) if len(hm) else set()
    rest2 = firms[firms["ticker"].isin([t for t in rest if t not in have_map])]
    kc = ksic_candidates(rest2, inv)
    dm = domestic_candidates(firms)
    cands = pd.concat([x for x in (dc, hm, kc, dm) if len(x)], ignore_index=True)
    cols = ["ticker", "code", "source", "kw", "product_share", "direct6"]
    return cands[cols], {"dart": len(have_dart), "hs_map": len(have_map), "ksic": rest2["ticker"].nunique(),
                         "ksic_with_candidates": kc["ticker"].nunique() if len(kc) else 0,
                         "domestic": dm["ticker"].nunique() if len(dm) else 0}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    targets = pd.read_csv(OUT / "k2_targets.csv", dtype={"ticker": str, "ksic": str})
    firms = targets[targets["confirmed"]].copy()
    rows = pd.read_csv(OUT / "k2_products.csv", dtype={"ticker": str})
    pstatus = pd.read_csv(OUT / "k2_products_status.csv", dtype={"ticker": str})
    inv = trade_inventory()
    names = hs_names()
    cands, src_counts = build(firms, rows, pstatus, inv, names)
    ctl = control_frame()
    ccands, _ = build(ctl["firm"], ctl["rows"], ctl["status"], inv, names)
    codes = sorted(set(cands.loc[cands["source"] != "domestic", "code"]) |
                   set(ccands.loc[ccands["source"] != "domestic", "code"]))
    xq = export_yoy(codes)
    dom = domestic_series(sorted(set(cands.loc[cands["source"] == "domestic", "code"]) |
                                 set(ccands.loc[ccands["source"] == "domestic", "code"])))
    rev = rev_yoy_frame(firms["ticker"].tolist())
    corr = correlations(cands, rev, xq, dom)
    res = rank_and_select(corr, firms)
    inv_codes = inv.index.tolist()
    xq_all = export_yoy(inv_codes)
    macro = domestic_series(["bok:수출금액지수", "bok:전산업생산지수"])
    pl = placebo(res["firm"], res["cands"], rev, xq_all, macro)
    res["firm"] = alt_rank(res["firm"].merge(pl, on="ticker", how="left"), res["shown"])
    ccorr = correlations(ccands, ctl["rev_yoy"], xq, dom)
    cres = rank_and_select(ccorr, ctl["firm"]) if len(ccorr) else None
    if cres:
        cpl = placebo(cres["firm"], cres["cands"], ctl["rev_yoy"], xq_all, macro)
        cres["firm"] = alt_rank(cres["firm"].merge(cpl, on="ticker", how="left"), cres["shown"])
    controls = {"firm": cres["firm"]} if cres else {}
    shown_all = pd.concat([res["shown"], cres["shown"]], ignore_index=True) if cres else res["shown"]
    write_excel({**res, "shown": shown_all}, names, dom, rows, pstatus, controls, targets)

    corr.to_csv(OUT / "k2_candidates_all.csv", index=False, encoding="utf-8-sig")
    res["firm"].to_csv(OUT / "k2_firm_tiers.csv", index=False, encoding="utf-8-sig")
    if cres:
        ccorr.to_csv(OUT / "k2_controls_candidates.csv", index=False, encoding="utf-8-sig")
    f = res["firm"]
    summary = {
        "firms": len(f), "candidate_source_firms": src_counts,
        "candidates_by_source": corr["source"].value_counts().to_dict(),
        "tiers": f["tier"].value_counts().to_dict(),
        "tier_by_coverage": f.groupby(["tier", "coverage_class"]).size().unstack(fill_value=0).to_dict(),
        "passed_by_best_source": res["shown"][res["shown"]["pass"]].sort_values("corr_full", ascending=False)
        .drop_duplicates("ticker")["source"].value_counts().to_dict(),
        "direct6_firms": int(f["has_direct6"].sum()),
        "main_corr_min": float(f.loc[f["tier"] == "승인 대상", "best_pass_corr"].min()),
        "main_corr_median": float(f.loc[f["tier"] == "승인 대상", "best_pass_corr"].median()),
        "main_flag_basis": int(f.loc[f["tier"] == "승인 대상", "flag_basis"].sum()),
        "main_flag_jump": int(f.loc[f["tier"] == "승인 대상", "flag_jump"].sum()),
        "controls": cres["firm"][["ticker", "name", "best_corr", "passed"]].to_dict(orient="records") if cres else [],
        "n_export_codes": len(codes), "n_domestic_series": len(dom),
        "placebo_by_tier": f.groupby("tier")[["best_corr", "placebo_best_mean", "placebo_pass_share",
                                              "corr_total_exports", "corr_ip", "corr_trend"]].median().round(3)
        .to_dict(orient="index"),
        "alt_80": int(f["alt_rank"].notna().sum()),
        "alt_overlap_with_main": int((f["alt_rank"].notna() & (f["tier"] == "승인 대상")).sum()),
        "main_excovid_ge_0.3": int(((f["tier"] == "승인 대상") & (f["best_corr_excovid"] >= CORR_MIN)).sum()),
    }
    (OUT / "k2_candidates_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return res


if __name__ == "__main__":
    main()
