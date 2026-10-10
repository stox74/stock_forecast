# -*- coding: utf-8 -*-
"""R2 참고 계산 (한 번만, 결과로 아무것도 바꾸지 않음): 2c 판정 공통 분기 1,369개에서 E·M4·B2 상대 RMSE.

표본: 2c/step3/judgement/c1_pairs.csv 의 (기업, 분기) 1,369개 = 판정의 주 모델(M4)과 B2 공통 분기.
오차: fd_nowcast pre5, 전체 기간 변형의 err_first (forecast_eval 정의). E 는 R1 에서 추가한 model_id 'E'.
비교: 기업별 RMSE(E)/RMSE(B2), RMSE(M4)/RMSE(B2), RMSE(E)/RMSE(M4); 중앙값, 1 미만 비율. 전체와 코로나 2020Q1~2021Q4 제외.
결과 파일이 있으면 다시 계산하지 않는다.
실행: python -m DATA.fdriver.radar.r2_reference
"""
import numpy as np
import pandas as pd

from ..config import output_path
from ..evaluate import forecast_eval as fe

OUT = output_path("radar", "r2_reference")
PAIRS = output_path("2c", "step3", "judgement") / "c1_pairs.csv"
COVID = ("2020Q1", "2021Q4")


def rmse(s):
    return float(np.sqrt((s ** 2).mean())) if len(s) else np.nan


def main():
    if (OUT / "r2_reference_by_entity.csv").exists():
        print("참고 계산은 이미 한 번 했음 (다시 계산하지 않음)")
        return
    pe = pd.read_csv(PAIRS, dtype={"entity_id": str})
    ids = sorted(pe["entity_id"].unique())
    d = fe.load_nowcast(ids)
    d = d[(d["cp"] == "pre5") & (d["variant"] == "all") & (d["model"] == "E")]
    e = d.drop_duplicates(["entity_id", "fiscal_quarter"])[["entity_id", "fiscal_quarter", "err_first"]]
    x = pe.merge(e.rename(columns={"err_first": "err_E"}), on=["entity_id", "fiscal_quarter"], how="left")
    rows = []
    for label, keep in [("전체 기간", lambda q: True), ("코로나 2020Q1~2021Q4 제외", lambda q: not (COVID[0] <= q <= COVID[1]))]:
        xx = x[x["fiscal_quarter"].map(keep)]
        for ent, g in xx.groupby("entity_id"):
            g = g.dropna(subset=["err_E", "err_main", "err_b2"])
            rows.append({"sample": label, "entity_id": ent, "n": len(g),
                         "rel_E_vs_B2": rmse(g["err_E"]) / rmse(g["err_b2"]),
                         "rel_M4_vs_B2": rmse(g["err_main"]) / rmse(g["err_b2"]),
                         "rel_E_vs_M4": rmse(g["err_E"]) / rmse(g["err_main"])})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "r2_reference_by_entity.csv", index=False, encoding="utf-8-sig")
    summ = []
    for label, g in t.groupby("sample", sort=False):
        for c in ["rel_E_vs_B2", "rel_M4_vs_B2", "rel_E_vs_M4"]:
            summ.append({"sample": label, "ratio": c, "n_firms": int(g[c].notna().sum()), "quarters": int(g["n"].sum()),
                         "median": g[c].median(), "share_below_1": float((g[c] < 1).mean())})
    s = pd.DataFrame(summ)
    s.to_csv(OUT / "r2_reference_summary.csv", index=False, encoding="utf-8-sig")
    miss = int(x["err_E"].isna().sum())
    pd.set_option("display.width", 200)
    print(f"pairs {len(pe)}, E missing {miss}")
    print(s.round(4).to_string(index=False))
    print(t[t["sample"] == "전체 기간"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
