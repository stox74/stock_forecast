# -*- coding: utf-8 -*-
"""2d 작업 7-4: 과거 구간 탐색 (판정 아님). 사전 등록 v2 (커밋 51c6991) 뒤에만 실행한다. 쓰기 없음 (CSV 만).

K5 평가 구간 2024Q1~2026Q2, 예측 시점 pre5 (K4b 와 같은 발표일·기준일) 에서 v2 방법(E5, E4)을 다시 계산해
K5 결과와 나란히 적는다. '탐색적, 판정에 쓰지 않음'.
- 예측: k4_forecast.run_entity(method_v2=True) (기존 E 도 같은 실행에서 다시 나옴)
- 실적·표본·CAR: fd_eval_2d run 20261018_013015 의 rev_actual, in_d1, in_d2, car_0_1 을 그대로 쓴다.
- D1 형: |ln(실적/E5)| vs |ln(실적/B2)| (driver, flag_jump=0). 참고: E5 vs E (H6 형), E5 vs E5_noM1 (H1 형),
  ts_only 의 E5 vs B2.
- D2 형: CAR[0,+1] ~ gap_E4_vs_B2 (driver, 컨센서스 없음). 참고: 같은 표본의 gap_E_vs_B2 (K5 D2 재현) 와 3분위 스프레드.
실행: python -m DATA.fdriver.eval2d.k7_explore
"""
import json
import logging

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..eval2b.k3_analysis import tercile_by_quarter
from ..nowcast.run import _calendar
from . import k3_oprule as K3
from . import k4_forecast as K4
from .k2_targets import read_adj
from .k5_judge import paired_test, slope_test

OUT = output_path("2d", "K7")
EVAL_RUN = "20261018_013015"


def forecasts() -> pd.DataFrame:
    cal = _calendar()
    m = K4.inputs()["map"]
    ids = m["entity_id"].tolist()
    adj = read_adj(ids)
    it = K3.items(ids, adj, quarters=None)
    ann = K4.announce_dates(ids, cal)
    rows = []
    for i, r in enumerate(m.itertuples()):
        rows += K4.run_entity(r, adj, it, ann, cal, method_v2=True)
        logging.info("%d/%d %s", i + 1, len(m), r.entity_id)
    return pd.DataFrame(rows)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fc = forecasts()
    fc.to_csv(OUT / "k7_eval_forecasts_v2.csv", index=False, encoding="utf-8-sig")
    ev = db.read_sql("SELECT entity_id, quarter, status, rev_actual, in_d1, in_d2, car_0_1, flag_jump, "
                     "E AS E_k5, B2 AS B2_k5 FROM fd_eval_2d WHERE run_id = :r", {"r": EVAL_RUN})
    d = ev.merge(fc[["entity_id", "quarter", "E", "B2", "E5", "E4", "E5_noM1", "SARIMA", "ETS", "Theta",
                     "gap_E4_vs_B2", "gap_E_vs_B2", "e5_n", "sarima_order"]], on=["entity_id", "quarter"], how="left")
    lg = lambda a, f: np.abs(np.log(a / f)).where((a > 0) & (f > 0))
    for k in ("E", "B2", "E5", "E4", "E5_noM1", "SARIMA", "ETS", "Theta"):
        d[f"err_{k}"] = lg(d["rev_actual"], d[k])
    d1 = d[d["in_d1"] == 1]
    tso = d[(d["status"] == "ts_only") & (d["flag_jump"] == 0)]
    d2 = d[d["in_d2"] == 1]
    keep = ("n", "n_firms", "mean_diff", "se", "p_one", "p_two", "share_a_better", "success", "b", "r2")
    pick = lambda r: {k: v for k, v in r.items() if k in keep or k.startswith("median_")}
    res = {"note": "탐색적, 판정에 쓰지 않음 (사전 등록 v2 커밋 51c6991 뒤 계산)",
           "check_E_reproduces_K5": float((d["E"] / d["E_k5"] - 1).abs().max()),
           "D1_E5_vs_B2": pick(paired_test(d1, "err_E5", "err_B2")),
           "D1_K5_E_vs_B2": pick(paired_test(d1, "err_E", "err_B2")),
           "H6_E5_vs_E": pick(paired_test(d1, "err_E5", "err_E")),
           "H1_E5_vs_E5_noM1": pick(paired_test(d1, "err_E5", "err_E5_noM1")),
           "ts_only_E5_vs_B2": pick(paired_test(tso, "err_E5", "err_B2")),
           "each_vs_B2": {k: pick(paired_test(d1, f"err_{k}", "err_B2")) for k in ("SARIMA", "ETS", "Theta", "E4")},
           "D2_gap_E4_vs_B2": pick(slope_test(d2, "car_0_1", "gap_E4_vs_B2")),
           "D2_K5_gap_E_vs_B2": pick(slope_test(d2, "car_0_1", "gap_E_vs_B2"))}
    t4 = tercile_by_quarter(d2.rename(columns={"entity_id": "ticker"}), "gap_E4_vs_B2", "car_0_1")
    t1 = tercile_by_quarter(d2.rename(columns={"entity_id": "ticker"}), "gap_E_vs_B2", "car_0_1")
    res["D2_tercile_E4"] = {k: v for k, v in t4.items() if k != "table"}
    res["D2_tercile_E_K5"] = {k: v for k, v in t1.items() if k != "table"}
    res["corr_gap_E4_vs_gap_E"] = float(d2[["gap_E4_vs_B2", "gap_E_vs_B2"]].corr().iloc[0, 1])
    res["e5_n"] = d["e5_n"].value_counts(dropna=False).to_dict()
    d.to_csv(OUT / "k7_explore_rows.csv", index=False, encoding="utf-8-sig")
    t4["table"].to_csv(OUT / "k7_d2_tercile_E4.csv", index=False, encoding="utf-8-sig")
    (OUT / "k7_explore.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
