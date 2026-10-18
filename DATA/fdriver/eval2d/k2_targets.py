# -*- coding: utf-8 -*-
"""2d 작업 2 A: 대상 확정과 매출 기준 보정 (명세서 2d v2 9절 1, 2, 4). 쓰기는 fd_rev_adj_2d 만.

입력: K1 산출물 k1_universe.csv (target_2d 899곳), k1_revenue_basis_check.csv.
1. 음수 매출 제외: K1 점검표에서 2019Q1~2026Q2 에 음수 분기가 있는 기업을 뺀다 (9절 1, K1 read_revenue 기준).
2. 기준 보정 (9절 2): 기업마다 하나의 기준으로 2009Q1~2026Q2 매출을 만든다.
   - DataGuide(DG) 매출이 있는 기업: 기준 'DG' (연결, 컨센서스와 같은 기준). DG 가 빈 분기는 DART 값으로 채우되,
     그 기업의 DART/DG 비율 중앙값(보정 대상 분기 제외)이 0.95~1.05 일 때만 (같은 기준). 아니면 비워 둔다.
   - DG 가 없는 기업: 기준 'DART' (V3 연결 우선 -> V2). V2 누적값 의심 연도는 누적 차분으로 고친다 (dart_cum_fix).
   - DART 와 DG 가 5% 넘게 다른 분기의 원인 진단 (cause):
       unit          비율이 10^k (k = +-1, +-3, +-6) 에서 2% 안
       v2_cumulative 2·3분기 DART 값이 DG 1~해당 분기 합과 1% 안 (반기·3분기 칸에 누적값)
       q4_derivation 4분기이고, 같은 해에 v2_cumulative 가 있거나 DART 연간 합이 DG 연간 합과 1% 안
       quarter_shift DART 값이 DG 의 앞뒤 분기 값과 1% 안 (결산월이 12월이 아니거나 결산월을 바꾼 기업에서 DART 분기
                     라벨이 한 분기 밀림)
       dg_separate   DART V3 별도(OFS) 값이 DG 와 1% 안 (DG 가 별도 기준)
       basis         그 기업의 불일치 분기 비율이 중앙값의 +-10% 안에 4분기 이상 모임 (연결·별도 차이 추정)
       other         그 밖
   - flag_basis = 1: 선정 구간(2019Q1~2023Q4)에서 원인이 basis / dg_separate / other 인 5% 초과 불일치가 3분기 이상
     (보정 기준과 DART 가 계속 다른 기업). unit / v2_cumulative / q4_derivation / quarter_shift 는 DART 쪽 산출
     오류라 세지 않는다.
3. 급변 표시 (9절 4): flag_jump = K1 rev_jump_q >= 4. 참고로 보정 매출로 다시 센 rev_jump_q_adj 도 둔다.
4. 보정 매출로 선정 구간 20분기가 다 차지 않는 기업은 대상에서 뺀다 (상관 계산 불가).
실행: python -m DATA.fdriver.eval2d.k2_targets
"""
import json
import logging
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

from .. import db
from ..adapters import revenue as R
from ..config import output_path
from .k1_survey import ALLQ, SEL

log = logging.getLogger(__name__)
K1 = output_path("2d", "K1")
OUT = output_path("2d", "K2")
T_ADJ = "fd_rev_adj_2d"
START = pd.Timestamp("2009-03-31")
END = ALLQ[-1]
TOL = 0.05
JUMP_MIN = 4
DART_ARTIFACTS = ("unit", "v2_cumulative", "q4_derivation", "quarter_shift")

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_ADJ}` (
  entity_id VARCHAR(20) NOT NULL,
  period_end DATE NOT NULL,
  revenue_adj DOUBLE NULL,
  basis VARCHAR(10) NOT NULL,
  source VARCHAR(40) NULL,
  adj_reason VARCHAR(100) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, period_end)
) DEFAULT CHARSET=utf8mb4
"""


# ------------------------------------------------------------------ 입력
def k1_targets() -> pd.DataFrame:
    u = pd.read_csv(K1 / "k1_universe.csv", dtype={"ticker": str, "corp_code": str, "ksic": str})
    return u[u["target_2d"]].reset_index(drop=True)


def k1_negative() -> set:
    b = pd.read_csv(K1 / "k1_revenue_basis_check.csv", dtype={"entity_id": str})
    return set(b.loc[b["kind"] == "negative_revenue", "entity_id"])


def read_sources(ids: List[str]) -> pd.DataFrame:
    """long: entity_id, period_end, src (DG / V3 / V3S / V2), value (원)."""
    fye = R._fiscal_year_ends(ids)
    dec = [i for i in ids if fye.get(i, 12) == 12]
    parts = []
    dg = R._read_dg(ids)
    parts.append(dg.assign(src="DG")[["entity_id", "period_end", "value", "src"]])
    for table, basis, tag in ((R.SRC_V3, "consolidated", "V3"), (R.SRC_V3, "separate", "V3S"),
                              (R.SRC_V2, "consolidated", "V2")):
        d = R._read_dart(table, dec, basis)
        if d.empty:
            continue
        d["period_end"] = R._quarter_end(d["fiscal_year"], d["fq"])
        parts.append(d.assign(src=tag)[["entity_id", "period_end", "value", "src"]])
    s = pd.concat(parts, ignore_index=True)
    s = s[(s["period_end"] >= START) & (s["period_end"] <= END)]
    return s.drop_duplicates(["entity_id", "period_end", "src"])


# ------------------------------------------------------------------ 진단
def _is_unit(r: float) -> bool:
    return any(abs(r / k - 1) < 0.02 for k in (10, 0.1, 1e3, 1e-3, 1e6, 1e-6))


def diagnose(w: pd.DataFrame) -> pd.DataFrame:
    """w: entity_id 한 기업의 분기 wide (index period_end, 열 DG, V3, V3S, V2, DART). 5% 초과 분기마다 cause."""
    d = w.dropna(subset=["DART", "DG"]).copy()
    d = d[d["DG"] != 0]
    d["ratio"] = d["DART"] / d["DG"]
    d = d[(d["ratio"] - 1).abs() > TOL].copy()
    if d.empty:
        return d.assign(cause=pd.Series(dtype=str))
    dg = w["DG"]
    causes = {}
    cum_years = set()
    for pe, r in d.iterrows():
        q, y = pe.quarter, pe.year
        if _is_unit(r["ratio"]):
            causes[pe] = "unit"
            continue
        if q in (2, 3):
            prev = [pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) for m in range(3, q * 3 + 1, 3)]
            if all(p in dg.index and pd.notna(dg[p]) for p in prev):
                cum = dg[prev].sum()
                if cum and abs(r["DART"] / cum - 1) < 0.01:
                    causes[pe] = "v2_cumulative"
                    cum_years.add(y)
                    continue
        nb = [pe - pd.offsets.QuarterEnd(1), pe + pd.offsets.QuarterEnd(1)]
        if any(n in dg.index and pd.notna(dg[n]) and dg[n] and abs(r["DART"] / dg[n] - 1) < 0.01 for n in nb):
            causes[pe] = "quarter_shift"
            continue
        if pd.notna(r.get("V3S")) and abs(r["V3S"] / r["DG"] - 1) < 0.01:
            causes[pe] = "dg_separate"
            continue
        causes[pe] = None
    for pe, r in d.iterrows():
        if causes[pe] is not None or pe.quarter != 4:
            continue
        y = pe.year
        yq = [pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) for m in (3, 6, 9, 12)]
        sub = w.reindex(yq)
        same_annual = sub["DART"].notna().all() and sub["DG"].notna().all() and \
            abs(sub["DART"].sum() / sub["DG"].sum() - 1) < 0.01
        if y in cum_years or same_annual:
            causes[pe] = "q4_derivation"
    d["cause"] = pd.Series(causes)
    rest = d["cause"].isna()
    if rest.sum() >= 4:
        med = d.loc[rest, "ratio"].median()
        near = rest & ((d["ratio"] / med - 1).abs() <= 0.10)
        if near.sum() >= 4:
            d.loc[near, "cause"] = "basis"
    d["cause"] = d["cause"].fillna("other")
    return d


def dart_cum_fix(s: pd.Series) -> (pd.Series, List[pd.Timestamp]):
    """DG 가 없는 기업의 DART 분기값 (index period_end). 4분기가 음수이거나 1~3분기 평균의 40% 미만이고
    2·3분기가 커지는 해에, 2·3분기 칸을 누적으로 본 세 가지 고침(2·3 / 3만 / 2만) 중 분기 변동계수가 가장 작고
    모두 양수인 것을 고른다. 변동계수가 원래의 절반 미만일 때만 고친다."""
    s = s.copy()
    fixed = []
    for y in sorted(set(s.index.year)):
        qs = [pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) for m in (3, 6, 9, 12)]
        if not all(q in s.index and pd.notna(s[q]) for q in qs):
            continue
        v = s[qs].to_numpy(float)
        if not (v[3] < 0 or v[3] < 0.4 * v[:3].mean()):
            continue
        if not (v[1] > 1.3 * v[0] or v[2] > 1.3 * v[1]):
            continue
        annual = v.sum()
        cands = []
        # 2·3 누적: 원 칸 c2 = v1, c3 = v2 (to_standalone 이 그대로 넣음)
        cands.append([v[0], v[1] - v[0], v[2] - v[1], annual - v[2]])
        cands.append([v[0], v[1], v[2] - v[0] - v[1], annual - v[2]])        # 3만 누적
        cands.append([v[0], v[1] - v[0], v[2], annual - v[1] - v[2]])        # 2만 누적
        cv0 = np.std(v) / abs(np.mean(v)) if np.mean(v) else np.inf
        best = None
        for c in cands:
            c = np.array(c)
            if (c <= 0).any():
                continue
            cv = np.std(c) / np.mean(c)
            if best is None or cv < best[0]:
                best = (cv, c)
        if best is not None and best[0] < 0.5 * cv0:
            s[qs] = best[1]
            fixed += qs
    return s, fixed


# ------------------------------------------------------------------ 보정
def adjust(src: pd.DataFrame, ids: List[str]) -> Dict[str, pd.DataFrame]:
    w = src.pivot_table(index=["entity_id", "period_end"], columns="src", values="value").reset_index()
    for c in ("DG", "V3", "V3S", "V2"):
        if c not in w:
            w[c] = np.nan
    w["DART"] = w["V3"].combine_first(w["V2"])
    w["DART_src"] = np.where(w["V3"].notna(), "DART_V3", np.where(w["V2"].notna(), "DART_V2", None))
    grid = pd.MultiIndex.from_product([ids, pd.period_range(START, END, freq="Q").to_timestamp(how="end").normalize()],
                                      names=["entity_id", "period_end"])
    w = w.set_index(["entity_id", "period_end"]).reindex(grid).reset_index()
    rows, diag, firm = [], [], []
    for eid, g in w.groupby("entity_id", sort=True):
        g = g.set_index("period_end").sort_index()
        dgn = diagnose(g)
        if len(dgn):
            diag.append(dgn.reset_index().assign(entity_id=eid))
        has_dg = g["DG"].notna().any()
        cause_q = dgn["cause"] if len(dgn) else pd.Series(dtype=str)
        if has_dg:
            ok = g.dropna(subset=["DART", "DG"])
            ok = ok[~ok.index.isin(cause_q.index[cause_q.isin(DART_ARTIFACTS)])]
            ok = ok[ok["DG"] != 0]
            med = float((ok["DART"] / ok["DG"]).median()) if len(ok) else np.nan
            same = bool(pd.notna(med) and 0.95 <= med <= 1.05)
            val = g["DG"].copy()
            source = pd.Series(np.where(g["DG"].notna(), "DG", None), index=g.index, dtype=object)
            reason = pd.Series(np.where(g["DG"].notna(), "dg", None), index=g.index, dtype=object)
            for pe, c in cause_q.items():
                reason[pe] = f"dg;dart_{c}"
            miss = g["DG"].isna() & g["DART"].notna()
            if same:
                val[miss] = g.loc[miss, "DART"]
                source[miss] = g.loc[miss, "DART_src"]
                reason[miss] = "dg_missing_fill_dart"
            else:
                reason[miss] = "dg_missing_dart_other_basis"
            basis, fixed = "DG", []
        else:
            med, same = np.nan, None
            val, fixed = dart_cum_fix(g["DART"])
            source = g["DART_src"].astype(object)
            reason = pd.Series(np.where(g["DART"].notna(), "dart", None), index=g.index, dtype=object)
            reason[fixed] = "dart_cum_fix"
            basis = "DART"
        sel_bad = dgn[dgn.index.isin(SEL) & ~dgn["cause"].isin(DART_ARTIFACTS)] if len(dgn) else dgn
        firm.append({"entity_id": eid, "basis": basis, "dart_dg_median_ratio": med, "dart_same_basis": same,
                     "n_mismatch_all": len(dgn), "n_mismatch_sel_unexplained": len(sel_bad),
                     "main_cause": (cause_q.value_counts().idxmax() if len(cause_q) else ""),
                     "causes": ";".join(f"{k}:{v}" for k, v in cause_q.value_counts().items()),
                     "n_dart_cum_fix": len(fixed), "flag_basis": int(len(sel_bad) >= 3)})
        rows.append(pd.DataFrame({"entity_id": eid, "period_end": g.index, "revenue_adj": val.to_numpy(),
                                  "basis": basis, "source": source.to_numpy(), "adj_reason": reason.to_numpy()}))
    adj = pd.concat(rows, ignore_index=True)
    adj = adj[adj["revenue_adj"].notna() | adj["adj_reason"].notna()]
    return {"adj": adj, "diag": pd.concat(diag, ignore_index=True) if diag else pd.DataFrame(),
            "firm": pd.DataFrame(firm)}


def jump_count(adj: pd.DataFrame) -> pd.Series:
    p = adj.pivot_table(index="period_end", columns="entity_id", values="revenue_adj")
    r = (p / p.shift(4)).loc[p.index.isin(ALLQ)]
    return ((r > 2) | (r < 0.5)).sum()


def read_adj(entity_ids=None) -> pd.DataFrame:
    """fd_rev_adj_2d 읽기 (다른 2d 모듈용)."""
    if entity_ids is None:
        d = db.read_sql(f"SELECT entity_id, period_end, revenue_adj, basis, source, adj_reason FROM `{T_ADJ}`")
    else:
        f, p = db.in_clause("e", list(entity_ids))
        d = db.read_sql(f"SELECT entity_id, period_end, revenue_adj, basis, source, adj_reason FROM `{T_ADJ}` "
                        f"WHERE entity_id IN {f}", p)
    d["period_end"] = pd.to_datetime(d["period_end"])
    return d


# ------------------------------------------------------------------ main
def main(write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("DATA.fdriver.adapters.revenue").setLevel(logging.ERROR)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    t = k1_targets()
    steps = [("K1 확대 대상 (target_2d)", len(t))]
    neg = k1_negative()
    t["excl_negative"] = t["ticker"].isin(neg)
    t1 = t[~t["excl_negative"]].copy()
    steps.append(("1 음수 매출 분기 기업 제외", len(t1)))
    ids = t1["ticker"].tolist()
    src = read_sources(ids)
    res = adjust(src, ids)
    adj, firm, diag = res["adj"], res["firm"], res["diag"]
    sel_n = adj[adj["period_end"].isin(SEL) & adj["revenue_adj"].notna()].groupby("entity_id").size()
    neg_adj = adj[adj["period_end"].isin(ALLQ) & (adj["revenue_adj"] < 0)].groupby("entity_id").size()
    firm["adj_sel_quarters"] = firm["entity_id"].map(sel_n).fillna(0).astype(int)
    firm["adj_negative_q"] = firm["entity_id"].map(neg_adj).fillna(0).astype(int)
    firm["rev_jump_q_adj"] = firm["entity_id"].map(jump_count(adj)).fillna(0).astype(int)
    t1 = t1.merge(firm, left_on="ticker", right_on="entity_id", how="left")
    t1["flag_jump"] = (t1["rev_jump_q"] >= JUMP_MIN).astype(int)
    t1["flag_jump_adj"] = (t1["rev_jump_q_adj"] >= JUMP_MIN).astype(int)
    t1["excl_sel_incomplete"] = t1["adj_sel_quarters"] < len(SEL)
    t1["excl_adj_negative"] = t1["adj_negative_q"] > 0
    t2 = t1[~t1["excl_sel_incomplete"]]
    steps.append(("2 보정 매출로 선정 구간 20분기 미충족 제외", len(t2)))
    t3 = t2[~t2["excl_adj_negative"]]
    steps.append(("3 보정 뒤에도 음수 분기 남은 기업 제외", len(t3)))
    t1["confirmed"] = t1["ticker"].isin(set(t3["ticker"]))

    keep = adj[adj["entity_id"].isin(set(t3["ticker"]))].copy()
    keep["run_id"] = run_id
    n_written = 0
    if write:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{T_ADJ}`")["entity_id"].tolist()
        if old:
            db.delete_fd(T_ADJ, {"entity_id": old})
        n_written = db.write_fd(keep, T_ADJ, ["entity_id", "period_end"])

    cols = ["ticker", "name", "market", "ksic", "ksic_div_name", "industry_group", "coverage_class", "cons_q_8",
            "rev_last4_sum_eok", "basis", "dart_dg_median_ratio", "dart_same_basis", "main_cause", "causes",
            "n_mismatch_all", "n_mismatch_sel_unexplained", "n_dart_cum_fix", "flag_basis", "rev_jump_q",
            "rev_jump_q_adj", "flag_jump", "flag_jump_adj", "adj_sel_quarters", "adj_negative_q",
            "excl_sel_incomplete", "excl_adj_negative", "confirmed"]
    t1[cols].to_csv(OUT / "k2_targets.csv", index=False, encoding="utf-8-sig")
    t[t["excl_negative"]][["ticker", "name", "industry_group", "coverage_class"]].to_csv(
        OUT / "k2_excluded_negative.csv", index=False, encoding="utf-8-sig")
    if len(diag):
        diag.to_csv(OUT / "k2_basis_diagnosis_quarters.csv", index=False, encoding="utf-8-sig")
    c = t1[t1["confirmed"]]
    by_cause = diag["cause"].value_counts().to_dict() if len(diag) else {}
    by_cause_firms = diag.groupby("cause")["entity_id"].nunique().to_dict() if len(diag) else {}
    reason_cnt = keep["adj_reason"].str.replace(r"^dg;dart_.*", "dg (DART 불일치 분기)", regex=True).value_counts()
    summary = {
        "run_id": run_id, "steps": steps, "rows_written": n_written,
        "confirmed": len(c), "basis": c["basis"].value_counts().to_dict(),
        "flag_basis": int(c["flag_basis"].sum()), "flag_jump": int(c["flag_jump"].sum()),
        "flag_jump_adj": int(c["flag_jump_adj"].sum()),
        "mismatch_quarters_by_cause": by_cause, "mismatch_firms_by_cause": by_cause_firms,
        "firms_with_mismatch": int((c["n_mismatch_all"] > 0).sum()),
        "dart_cum_fix_firms": int((c["n_dart_cum_fix"] > 0).sum()),
        "dart_cum_fix_quarters": int(c["n_dart_cum_fix"].sum()),
        "adj_reason_rows": reason_cnt.to_dict(),
        "coverage_class": c["coverage_class"].value_counts().to_dict(),
    }
    (OUT / "k2_targets_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return t1, adj, diag


if __name__ == "__main__":
    main()
