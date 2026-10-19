# -*- coding: utf-8 -*-
"""2d K7 후속: 10/19 v2 스냅샷 자료 품질 점검. 판정 아님, 2026Q3 실적 쓰지 않음. 쓰기는 K7 CSV 만 (DB 쓰기 없음).
(a) gap_E4_vs_B2 상위·하위 5곳: fd_rev_adj_2d 최근 8분기(2024Q3~2026Q2), flag_jump, flag_basis, 모형별 예측
(b) OP_hat_E5 와 OP_naive 부호가 다른 기업: 규칙, 원가율·판관비(또는 c, R1 계수) 예측, 최근 실적
python -m DATA.fdriver.eval2d.k7_quality
"""
import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from . import k3_oprule as K3
from .k2_targets import read_adj

OUT = output_path("2d", "K7")
RUN = "20261019_201817"
EOK = 1e8     # 원 -> 억원
Q8 = pd.period_range("2024Q3", "2026Q2", freq="Q")


def pe(p):
    return p.to_timestamp(how="end").normalize()


def main():
    s = db.read_sql("SELECT * FROM fd_live_2d WHERE run_id = :r", {"r": RUN})
    names = db.read_sql("SELECT entity_id, name FROM fd_op_rule").drop_duplicates("entity_id").set_index("entity_id")["name"]
    s["name"] = s["entity_id"].map(names)
    ids = s["entity_id"].tolist()
    adj = read_adj(ids)
    rev = adj.pivot_table(index="entity_id", columns="period_end", values="revenue_adj")

    # (a)
    s = s.sort_values("gap_E4_vs_B2")
    pick = pd.concat([s.tail(5).iloc[::-1].assign(side="상위"), s.head(5).assign(side="하위")])
    pct = lambda g: (np.exp(g) - 1) * 100
    a = pick[["side", "entity_id", "name", "status", "rule", "flag_jump", "flag_basis", "sarima_order", "ts3_n_train"]].copy()
    a["gap_E4_vs_B2_pct"] = pct(pick["gap_E4_vs_B2"])
    for k in ("SARIMA", "ETS", "Theta", "M1", "B2", "E4", "E5"):
        a[f"yoy_{k}_pct"] = pct(pick[f"g_{k}"])
    a["rev_base_2025Q3_eok"] = pick["rev_base"] / EOK
    a["E5_eok"] = pick["E5"] / EOK
    src = []
    for e in a["entity_id"]:
        r = {"entity_id": e}
        for p in Q8:
            v = rev.at[e, pe(p)] if pe(p) in rev.columns and e in rev.index else np.nan
            v4 = rev.at[e, pe(p - 4)] if pe(p - 4) in rev.columns and e in rev.index else np.nan
            r[f"{p}_eok"] = v / EOK
            r[f"{p}_yoy_pct"] = (v / v4 - 1) * 100 if v4 and v4 > 0 else np.nan
        x = adj[(adj["entity_id"] == e) & adj["period_end"].isin([pe(p) for p in Q8])]
        r["basis"] = ",".join(sorted(x["basis"].dropna().unique()))
        r["source"] = ",".join(sorted(x["source"].dropna().astype(str).unique()))
        r["adj_reason"] = " | ".join(f"{d.date()}:{t}" for d, t in zip(x["period_end"], x["adj_reason"]) if pd.notna(t) and t)
        src.append(r)
    a = a.merge(pd.DataFrame(src), on="entity_id")
    a.to_csv(OUT / "k7_quality_a_gap_extremes.csv", index=False, encoding="utf-8-sig")

    # (b)
    b = s[np.sign(s["OP_hat_E5"]) != np.sign(s["OP_naive"])]
    b = b[b["OP_hat_E5"].notna() & b["OP_naive"].notna()].copy()
    it = K3.items(b["entity_id"].tolist(), adj[adj["entity_id"].isin(b["entity_id"])], quarters=None)
    iv = it.pivot_table(index=["entity_id", "period_end"], columns="item", values="value")
    rows = []
    for r in b.itertuples():
        o = {"entity_id": r.entity_id, "name": r.name, "rule": r.rule, "op_note": r.op_note, "op_n_models": r.op_n_models,
             "E5_eok": r.E5 / EOK, "B2_eok": r.B2 / EOK, "OP_hat_E5_eok": r.OP_hat_E5 / EOK, "OP_naive_eok": r.OP_naive / EOK,
             "OP_hat_E_eok": r.OP_hat / EOK, "op_a_eok": None if pd.isna(r.op_a) else r.op_a / EOK, "op_b": r.op_b,
             "cost_ratio_hat_pct": None if pd.isna(r.cost_ratio_hat) else r.cost_ratio_hat * 100,
             "sga_hat_eok": None if pd.isna(r.sga_hat) else r.sga_hat / EOK,
             "c_hat_pct": None if pd.isna(r.c_hat) else r.c_hat * 100}
        for p in list(pd.period_range("2025Q3", "2026Q2", freq="Q")):
            d = pe(p)
            R = rev.at[r.entity_id, d] if d in rev.columns else np.nan
            g = iv.loc[(r.entity_id, d)] if (r.entity_id, d) in iv.index else pd.Series(dtype=float)
            o[f"{p}_rev_eok"] = R / EOK
            o[f"{p}_op_eok"] = g.get("op", np.nan) / EOK
            o[f"{p}_op_margin_pct"] = g.get("op", np.nan) / R * 100
            o[f"{p}_cost_ratio_pct"] = g.get("cogs", np.nan) / R * 100
            o[f"{p}_sga_eok"] = g.get("sga", np.nan) / EOK
        rows.append(o)
    bt = pd.DataFrame(rows)
    bt.to_csv(OUT / "k7_quality_b_op_sign.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250, "display.max_columns", 80)
    print(a.round(1).T.to_string())
    print(len(b), "sign-differ firms")
    print(bt.round(1).T.to_string())


if __name__ == "__main__":
    main()
