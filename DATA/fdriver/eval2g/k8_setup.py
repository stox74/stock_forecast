# -*- coding: utf-8 -*-
"""2g K8·K8b 1~2: 대상 확정, 매출 보정(fd_rev_adj_2g), 판관비 항등식 수정, 영업이익 규칙(fd_op_rule_2g).
method_2g_v1.md (커밋 5dc9fdb) 1~2절. K8b: 대상에 (C) 커버리지 '없음'·'저커버리지' (같은 필터) 를 더한다. 쓰기는 fd_target_2g, fd_rev_adj_2g, fd_op_rule_2g (2g 작업 표, upsert) 와 CSV 만.

python -m DATA.fdriver.eval2g.k8_setup [--dry]
"""
import argparse
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..eval2d import k2_targets as K2
from ..eval2d import k3_oprule as K3
from ..eval2d.k1_survey import SEL
from ..eval2d.k4b_fix import C_PP, MIN_YEARS
from . import common as C

OUT = output_path("2g", "K8b")
GROUP_C = {"없음": "컨센서스 없음", "저커버리지": "저커버리지"}
FILTERS = {"f1_rev_history": "매출 이력", "f2_rev_size": "매출 300억", "f3_price": "주가 이벤트",
           "f5_not_fin_holding": "금융·지주"}

DDL_TARGET = f"""
CREATE TABLE IF NOT EXISTS `{C.T_TARGET}` (
  entity_id VARCHAR(20) NOT NULL,
  name VARCHAR(100) NULL,
  market VARCHAR(10) NULL,
  ksic VARCHAR(10) NULL,
  industry_group VARCHAR(40) NULL,
  group_2g VARCHAR(10) NOT NULL,
  coverage_class VARCHAR(10) NULL,
  filter_fail VARCHAR(80) NULL,
  basis VARCHAR(10) NULL,
  flag_jump TINYINT NULL,
  flag_basis TINYINT NULL,
  rule VARCHAR(4) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id)
) DEFAULT CHARSET=utf8mb4
"""
DDL_ADJ = K2.DDL.replace(f"`{K2.T_ADJ}`", f"`{C.T_ADJ}`")
DDL_RULE = (K3.DDL.replace(f"`{K3.T_RULE}`", f"`{C.T_RULE}`").replace("q4_measure VARCHAR(10)", "q4_measure VARCHAR(30)")
            .replace("q4_years VARCHAR(60)", "q4_years VARCHAR(120)"))
WIDEN = [f"ALTER TABLE `{C.T_RULE}` MODIFY q4_measure VARCHAR(30) NULL, MODIFY q4_years VARCHAR(120) NULL"]


def universe() -> (pd.DataFrame, list):
    u = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "corp_code": str, "ksic": str})
    steps = [("0 K1 상장 보통주", len(u))]
    a = u[(u["coverage_class"] == "정상") & u["pass_all_ex39"]].assign(group_2g="정상")
    steps.append(("1 (A) 정상 + 2d 필터 1~5 통과 (레이더 39 제외)", len(a)))
    b = u[u["in_radar39"]].assign(group_2g="레이더39")
    steps.append(("1 (B) 기존 레이더 39곳", len(b)))
    c = u[u["coverage_class"].isin(list(GROUP_C)) & u["pass_all_ex39"]].copy()
    c["group_2g"] = c["coverage_class"].map(GROUP_C)
    steps.append(("1 (C) 없음·저커버리지 + 2d 필터 1~5 통과 (K8b)", len(c)))
    t = pd.concat([a, b, c], ignore_index=True)
    t["filter_fail"] = t.apply(lambda r: ",".join(v for k, v in FILTERS.items() if not bool(r[k])), axis=1)
    m2d = set(db.read_sql("SELECT entity_id FROM fd_driver_map_2d WHERE status IN ('driver','ts_only')")["entity_id"])
    t["in_2d"] = t["ticker"].isin(m2d)
    t = t[~t["in_2d"]]
    steps.append(("2 2d 확대 73곳 제외", len(t)))
    t["excl_negative"] = t["ticker"].isin(K2.k1_negative())
    return t, steps


def q4_flags(q: pd.DataFrame, k3: pd.DataFrame) -> pd.DataFrame:
    """K4b 기준 flag_q4: K3 판관비 기준 또는 영업비용률 10%p 이상 해 2개 이상 (선정 구간)."""
    q = q[q["period_end"].isin(SEL)].copy()
    q["c"] = (q["revenue_adj"] - q["op"]) / q["revenue_adj"]
    cy = {}
    for e, g in q.groupby("entity_id"):
        yrs = []
        for y, gy in g.groupby(g["period_end"].dt.year):
            c4 = gy.loc[gy["period_end"].dt.month == 12, "c"].dropna()
            c13 = gy.loc[gy["period_end"].dt.month != 12, "c"].dropna()
            if len(c4) == 1 and len(c13) == 3 and c4.iloc[0] - c13.mean() >= C_PP:
                yrs.append(int(y))
        cy[e] = yrs
    out = k3.copy()
    for i, r in out.iterrows():
        c_years = cy.get(r["entity_id"], [])
        c_hit = len(c_years) >= MIN_YEARS
        sga = int(r["flag_q4"] or 0)
        meas = str(r["q4_measure"] or "sga")
        parts = ([meas] if sga else []) + (["opex_ratio"] if c_hit else [])
        out.at[i, "flag_q4"] = int(sga or c_hit)
        out.at[i, "q4_measure"] = "+".join(parts) if parts else meas
        sy = str(r["q4_years"]) if sga and pd.notna(r["q4_years"]) else ""
        out.at[i, "q4_years"] = ",".join(x for x in [sy, ",".join(f"{y}(c)" for y in c_years) if c_hit else ""] if x)
    return out


def main(write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("DATA.fdriver.adapters.revenue").setLevel(logging.ERROR)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    end = C.last_completed_quarter(pd.Timestamp.today())
    t, steps = universe()
    t1 = t[~t["excl_negative"]].copy()
    steps.append(("3 매출 음수 분기 기업 제외 (K1 점검표)", len(t1)))
    ids = t1["ticker"].tolist()
    res = C.build_adj(ids, end)
    adj, firm, diag = res["adj"], res["firm"], res["diag"]
    sel_n = adj[adj["period_end"].isin(SEL) & adj["revenue_adj"].notna()].groupby("entity_id").size()
    neg = adj[(adj["period_end"] >= SEL[0]) & (adj["revenue_adj"] < 0)].groupby("entity_id").size()
    firm["adj_sel_quarters"] = firm["entity_id"].map(sel_n).fillna(0).astype(int)
    firm["adj_negative_q"] = firm["entity_id"].map(neg).fillna(0).astype(int)
    t1 = t1.merge(firm, left_on="ticker", right_on="entity_id", how="left").drop(columns="entity_id")
    t1["flag_jump"] = (t1["rev_jump_q"] >= K2.JUMP_MIN).astype(int)
    t2 = t1[t1["adj_sel_quarters"] >= len(SEL)]
    steps.append(("4 보정 매출 선정 구간 20분기 미충족 제외", len(t2)))
    t3 = t2[t2["adj_negative_q"] == 0].copy()
    steps.append(("5 보정 뒤 음수 분기 남은 기업 제외", len(t3)))
    ids = t3["ticker"].tolist()
    adj = adj[adj["entity_id"].isin(ids)].copy()

    # 항목 + 판관비 수정, 규칙
    it, fixlog = C.items_2g(ids, adj)
    tt = t3.rename(columns={"ticker": "entity_id"})[["entity_id", "name", "ksic", "industry_group"]].assign(
        status="2g")
    rule = K3.assign(tt, adj, it[it["period_end"].isin(SEL)])
    qin = it[it["period_end"].isin(SEL)].pivot_table(index=["entity_id", "period_end"], columns="item",
                                                     values="value").reset_index()
    qin = qin.merge(adj[["entity_id", "period_end", "revenue_adj"]], on=["entity_id", "period_end"], how="left")
    rule = q4_flags(qin, rule)
    rule["run_id"] = run_id
    t3["rule"] = t3["ticker"].map(rule.set_index("entity_id")["rule"])
    tgt = t3.rename(columns={"ticker": "entity_id"})[
        ["entity_id", "name", "market", "ksic", "industry_group", "group_2g", "coverage_class", "filter_fail", "basis",
         "flag_jump", "flag_basis", "rule"]].assign(run_id=run_id)
    adj["run_id"] = run_id
    rcols = ["entity_id", "name", "status", "rule", "rule_reason", "a", "b", "p_hac", "r2", "c_mean", "c_sd", "n_q",
             "n_rev_lt50", "n_cogs", "n_sga", "item_basis", "flag_spread", "flag_q4", "q4_years", "q4_measure", "run_id"]
    n = {}
    if write:
        for ddl in (DDL_TARGET, DDL_ADJ, DDL_RULE, *WIDEN):
            db.execute_fd_ddl(ddl)
        for tab, df, key in ((C.T_TARGET, tgt, ["entity_id"]), (C.T_ADJ, adj, ["entity_id", "period_end"]),
                             (C.T_RULE, rule[rcols], ["entity_id"])):
            old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{tab}`")["entity_id"].tolist()
            stale = sorted(set(old) - set(df["entity_id"]))
            if stale:
                db.delete_fd(tab, {"entity_id": stale})
            n[tab] = db.write_fd(df, tab, key)
    # CSV
    t1.to_csv(OUT / "k8_targets_all.csv", index=False, encoding="utf-8-sig")
    tgt.to_csv(OUT / "k8_targets.csv", index=False, encoding="utf-8-sig")
    rule.to_csv(OUT / "k8_op_rule_2g.csv", index=False, encoding="utf-8-sig")
    fixlog.to_csv(OUT / "k8_sga_fixed.csv", index=False, encoding="utf-8-sig")
    if len(diag):
        diag.to_csv(OUT / "k8_basis_diagnosis_quarters.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(steps, columns=["step", "firms"]).to_csv(OUT / "k8_funnel.csv", index=False, encoding="utf-8-sig")
    dart_firms = int((tgt["basis"] == "DART").sum())
    s = {"run_id": run_id, "end": str(end.date()), "steps": steps, "rows_written": n,
         "group_2g": tgt["group_2g"].value_counts().to_dict(), "basis": tgt["basis"].value_counts().to_dict(),
         "coverage_class": tgt["coverage_class"].value_counts().to_dict(),
         "flag_jump": int(tgt["flag_jump"].sum()), "flag_basis": int(tgt["flag_basis"].sum()),
         "radar39_filter_fail": tgt.loc[tgt["filter_fail"] != "", ["entity_id", "name", "filter_fail"]].to_dict(
             orient="records"),
         "radar39_dropped": t.loc[(t["group_2g"] == "레이더39") & ~t["ticker"].isin(tgt["entity_id"]),
                                  ["ticker", "name", "excl_negative"]].to_dict(orient="records"),
         "sga_fixed": {"dart_basis_firms": dart_firms, "firms": int(fixlog["entity_id"].nunique()),
                       "quarters": len(fixlog), "by_kind": fixlog["sga_fixed"].value_counts().to_dict(),
                       "firms_sel": int(fixlog.loc[fixlog["period_end"].isin(SEL), "entity_id"].nunique()),
                       "quarters_sel": int(fixlog["period_end"].isin(SEL).sum())},
         "rule": rule["rule"].value_counts().to_dict(),
         "rule_by_group": {f"{a}/{b}": int(v) for (a, b), v in
                           tgt.groupby(["group_2g", "rule"]).size().items()},
         "flag_spread": int(rule["flag_spread"].sum()), "flag_q4": int(rule["flag_q4"].sum()),
         "identity_gap_gt1pp": int((rule["identity_gap_max_pp"] > 1).sum())}
    (OUT / "k8_setup_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                               encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    main(write=not ap.parse_args().dry)
