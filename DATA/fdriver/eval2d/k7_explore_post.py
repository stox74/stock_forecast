# -*- coding: utf-8 -*-
"""2d 작업 7-4 보조: k7_explore 결과에 중앙값과 영업이익(D3 형) 비교를 더한다. 탐색적, 판정에 쓰지 않음. 쓰기는 K7 CSV/JSON 만.
python -m DATA.fdriver.eval2d.k7_explore_post (k7_explore 다음에 실행)
"""
import json
import numpy as np
import pandas as pd
from DATA.fdriver import db
from DATA.fdriver.config import output_path
from DATA.fdriver.eval2d.k5_judge import paired_test

OUT = output_path("2d", "K7")
d = pd.read_csv(OUT / "k7_explore_rows.csv", dtype={"entity_id": str})
fc = pd.read_csv(OUT / "k7_eval_forecasts_v2.csv", dtype={"entity_id": str})
ev = db.read_sql("SELECT entity_id, quarter, rule, op_actual, rev_avg4, OP_naive, OP_hat AS OP_hat_k5, in_d3 "
                 "FROM fd_eval_2d WHERE run_id='20261018_013015'")
res = {}
med = lambda s, cols: {c: round(float(s[f"err_{c}"].median() * 100), 2) for c in cols}
cols = ["E", "B2", "E5", "E4", "E5_noM1", "SARIMA", "ETS", "Theta"]
res["median_err_pct_D1"] = med(d[d.in_d1 == 1], cols)
res["median_err_pct_ts_only"] = med(d[(d.status == "ts_only") & (d.flag_jump == 0)], cols)
res["n_D1"] = int((d.in_d1 == 1).sum())
o = ev.merge(fc[["entity_id", "quarter", "OP_hat_E5", "OP_hat"]], on=["entity_id", "quarter"], how="left")
o = o[o.in_d3 == 1].copy()
for c in ("OP_hat_E5", "OP_hat", "OP_naive"):
    o[f"err_{c}"] = (o[c] - o.op_actual).abs() / o.rev_avg4 * 100
o = o.dropna(subset=["err_OP_hat_E5", "err_OP_naive"])
res["D3_median"] = {c: round(float(o[f"err_{c}"].median()), 2) for c in ("OP_hat_E5", "OP_hat", "OP_naive")}
res["D3_OP_hat_reproduces_K5"] = float((o.OP_hat / o.OP_hat_k5 - 1).abs().max())
keep = ("n", "n_firms", "mean_diff", "se", "p_one", "p_two", "share_a_better", "success")
res["D3_E5_vs_naive"] = {k: v for k, v in paired_test(o, "err_OP_hat_E5", "err_OP_naive").items() if k in keep}
res["D3_E5_vs_OP_hat"] = {k: v for k, v in paired_test(o, "err_OP_hat_E5", "err_OP_hat").items() if k in keep}
d.groupby("quarter").apply(lambda g: pd.Series(med(g[g.in_d1 == 1], ["E", "B2", "E5"]))).to_csv(
    OUT / "k7_d1_by_quarter.csv", encoding="utf-8-sig")
(OUT / "k7_explore_post.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
