# -*- coding: utf-8 -*-
"""2d 작업 K4b-1: K4 결정 사항 반영 (평가 전 고정). 쓰기는 fd_op_rule (flag_q4 관련 3열 upsert) 만.

flag_q4 (명세서 6절 '4분기' 주의 표시), K4 보조 기준(5%p, 1년)을 아래로 바꾼다.
- 판관비 기준 (K3 그대로): 어느 해든 4분기 판관비 > 그해 분기 평균 x 1.5 (판관비 16분기 미만이면 영업비용)
- 영업비용률 기준 (K4b): 4분기 c = (매출 - 영업이익) / 매출 이 그해 1~3분기 c 평균보다 10%p 이상 높은 해가
  선정 구간에 2개 이상
- flag_q4 = 둘 중 하나라도 해당. q4_measure = 'sga' / 'opex' / 'opex_ratio' / '+' 로 이음, q4_years = 해당 해
  (영업비용률 기준은 'YYYY(c)').
판관비 기준 값은 K3 결과(k3_op_rule.csv)에서 다시 읽으므로 몇 번 실행해도 결과가 같다. 리드코프(rule excluded)는 그대로 둔다.
실행: python -m DATA.fdriver.eval2d.k4b_fix
"""
import json

import pandas as pd

from .. import db
from ..config import output_path

OUT = output_path("2d", "K5")
K3 = output_path("2d", "K3")
C_PP = 0.10
MIN_YEARS = 2


def opex_ratio_years() -> pd.DataFrame:
    q = pd.read_csv(K3 / "k3_quarterly_inputs.csv", dtype={"entity_id": str}, parse_dates=["period_end"])
    q["c"] = (q["revenue_adj"] - q["op"]) / q["revenue_adj"]
    rows = []
    for e, g in q.groupby("entity_id"):
        yrs = []
        for y, gy in g.groupby(g["period_end"].dt.year):
            c4 = gy.loc[gy["period_end"].dt.month == 12, "c"]
            c13 = gy.loc[gy["period_end"].dt.month != 12, "c"]
            if len(c4) == 1 and len(c13) == 3 and c4.iloc[0] - c13.mean() >= C_PP:
                yrs.append((int(y), float(c4.iloc[0]), float(c13.mean())))
        rows.append({"entity_id": e, "c_years": [y for y, _, _ in yrs],
                     "c_detail": "; ".join(f"{y}: {a:.3f} vs {b:.3f}" for y, a, b in yrs)})
    return pd.DataFrame(rows)


def main():
    k3 = pd.read_csv(K3 / "k3_op_rule.csv", dtype={"entity_id": str})
    base = k3.set_index("entity_id")[["flag_q4", "q4_years", "q4_measure"]]
    cy = opex_ratio_years().set_index("entity_id")
    r = db.read_sql("SELECT * FROM fd_op_rule").drop(columns=["first_collected_at", "last_collected_at"],
                                                      errors="ignore")
    before = int(r["flag_q4"].sum())
    out = []
    for row in r.itertuples(index=False):
        d = row._asdict()
        e = d["entity_id"]
        sga = int(base["flag_q4"].get(e, 0) or 0)
        sga_years = "" if pd.isna(base["q4_years"].get(e)) else str(base["q4_years"].get(e))
        meas = str(base["q4_measure"].get(e) or "sga")
        c_years = cy["c_years"].get(e, []) if e in cy.index else []
        c_hit = len(c_years) >= MIN_YEARS
        parts = ([meas] if sga else []) + (["opex_ratio"] if c_hit else [])
        d["flag_q4"] = int(sga or c_hit)
        d["q4_measure"] = "+".join(parts) if parts else meas
        d["q4_years"] = ",".join(x for x in ([sga_years] if sga else []) +
                                 ([",".join(f"{y}(c)" for y in c_years)] if c_hit else []) if x)
        out.append(d)
    o = pd.DataFrame(out)
    n = db.write_fd(o, "fd_op_rule", ["entity_id"])
    act = o[o["rule"] != "excluded"]
    det = act[["entity_id", "name", "rule", "flag_q4", "q4_measure", "q4_years"]].merge(
        cy["c_detail"].reset_index(), on="entity_id", how="left")
    det.to_csv(OUT / "k4b_flag_q4.csv", index=False, encoding="utf-8-sig")
    s = {"rows_written": n, "flag_q4_before (K4, 5%p 1년)": before,
         "flag_q4_after": int(act["flag_q4"].sum()),
         "by_measure": act.loc[act["flag_q4"] == 1, "q4_measure"].value_counts().to_dict(),
         "by_rule": act.groupby("rule")["flag_q4"].sum().to_dict(),
         "firms": act.loc[act["flag_q4"] == 1, "name"].tolist()}
    (OUT / "k4b_fix_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
