# -*- coding: utf-8 -*-
"""2c 작업 3 1-(b): 판정 코드(s3_lib)를 2a 시범 자료에 돌려 2a v6 기록값 재현 확인 (DB 쓰기 없음).

재현 대상 (명세서 2a v6 / 작업 4b·5~7 리포트, pre5)
- C1: 상대 RMSE (주 모델/B2, 전체 기간) 삼성전자 M4:combo 0.850, 하나투어 M4:combo 0.369, 현대건설기계 M4:842952 1.036,
  에이피알 M4:amazon 1.045
- C2: 주 대상 4개 기업 합산 corr(nsur_drv, SUR - nsur_mom) 0.533, 부호 적중 0.63, n 123 (level)
- C3: 매매 구간 b_mom -0.058 (0.553), b_drv +0.491 (0.959), n 113, 군집 50
신호는 새로 계산(build_signals, 쓰기 없음)해 DB 의 2a fd_signal 행과도 비교한다.
실행: python -m DATA.fdriver.stage2c.s3_b_verify_2a
"""
import numpy as np
import pandas as pd

from ..config import output_path
from ..signals import sur
from . import s3_lib as L

OUT = output_path("2c", "step3", "verify_2a")
MAIN4 = ["005930", "039130", "267270", "278470"]
EXPECT = {
    "rel_rmse": {"005930": 0.850, "039130": 0.369, "267270": 1.036, "278470": 1.045},
    "c2": {"corr": 0.533, "hit": 0.63, "n": 123},
    "c3": {"b_mom": -0.058, "se_mom": 0.553, "b_drv": 0.491, "se_drv": 0.959, "n": 113, "clusters": 50},
}


def main():
    main = {e: L.main_model_of(e) for e in MAIN4}
    rows = []
    # C1
    d = L.eval_rows(MAIN4, main)
    pe = L.paired_errors(d)
    ent = L.c1_by_entity(pe, main)
    for r in ent.itertuples():
        exp = EXPECT["rel_rmse"][r.entity_id]
        rows.append({"item": f"C1 rel_rmse {r.entity_id} {r.main_model}", "expected": exp, "got": round(r.rel_rmse, 3),
                     "n": r.n, "match": round(r.rel_rmse, 3) == exp})
    # C2: 신호 새로 계산 vs DB
    fresh = L.signal_rows(MAIN4)
    db_s = sur.read_signals(MAIN4)
    k = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "variant"]
    fresh["asof_date"] = pd.to_datetime(fresh["asof_date"])
    m = fresh.merge(db_s, on=k, suffixes=("_new", "_db"))
    diffs = {c: float((m[c + "_new"] - m[c + "_db"]).abs().max()) for c in ["nsur", "nsur_mom", "nsur_drv", "sur"]}
    rows.append({"item": "C2 signals fresh vs fd_signal (2a)", "expected": f"{len(db_s)} rows",
                 "got": f"{len(fresh)} fresh, {len(m)} matched, max|diff| {max(diffs.values()):.2e}",
                 "match": len(m) == len(db_s) == len(fresh) and max(diffs.values()) < 1e-9})
    sig = L.pick_main_signals(fresh, main)
    c2 = L.c2(sig)
    rows += [{"item": "C2 corr(drv, SUR-mom)", "expected": 0.533, "got": round(c2["estimate"], 3), "n": c2["N"],
              "match": round(c2["estimate"], 3) == 0.533},
             {"item": "C2 sign hit", "expected": 0.63, "got": round(c2["hit_rate"], 2), "n": c2["N_hit"],
              "match": round(c2["hit_rate"], 2) == 0.63},
             {"item": "C2 n", "expected": 123, "got": c2["N"], "match": c2["N"] == 123}]
    # C3
    ev = L.events_with_car(sig)
    c3 = L.c3(ev)
    for key, got in [("b_mom", c3["beta_mom"]), ("se_mom", c3["se_mom"]), ("b_drv", c3["estimate"]), ("se_drv", c3["se"])]:
        rows.append({"item": f"C3 {key}", "expected": EXPECT["c3"][key], "got": round(got, 3), "n": c3["N"],
                     "match": round(got, 3) == EXPECT["c3"][key]})
    rows += [{"item": "C3 n", "expected": 113, "got": c3["N"], "match": c3["N"] == 113},
             {"item": "C3 clusters", "expected": 50, "got": c3["n_clusters"], "match": c3["n_clusters"] == 50}]
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "verify_2a.csv", index=False, encoding="utf-8-sig")
    # 같은 코드의 2a 판정 통계 (참고)
    ref = pd.DataFrame([L.c1a(ent), L.c1b(pe), c2, {k2: v for k2, v in c3.items() if k2 != "_fit"}])
    ref.to_csv(OUT / "pilot_c1_c3_same_code.csv", index=False, encoding="utf-8-sig")
    ent.to_csv(OUT / "pilot_c1_by_entity.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))
    print("ALL MATCH:", bool(t["match"].all()))


if __name__ == "__main__":
    main()
