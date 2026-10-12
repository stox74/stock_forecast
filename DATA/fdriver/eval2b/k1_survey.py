# -*- coding: utf-8 -*-
"""2b 작업 1 사전 조사 (명세서 4절) + 멈춤 조건 (2절). DB 쓰기 없음.

1. 커버리지: 39곳 x 2022Q1~2026Q2 (4분기 제외 14개 분기) 중 C_pre5, C_final, pre5 E, 실제 매출, 발표일이 모두 있는 이벤트 수
   (기업별·연도별), 컨센서스가 아예 없는 기업.
2. 매출 기준·단위: 이벤트별 C_final / 실제 매출 비율의 기업별 중앙값. 0.8~1.25 밖 기업 = 기준 불일치 후보.
3. 고정값 점검: 발표 다음 달 말 값(c_after)이 C_final 과 같은 비율, 실제 매출과 같은 비율 (차이 0.1% 이내).
멈춤 조건: 비교 가능 이벤트 < 150 / c_after = 실제 비율 >= 30% / 전체 C_final/실제 비율 중앙값이 0.9~1.1 밖.
실행: python -m DATA.fdriver.eval2b.k1_survey
"""
import json

import pandas as pd

from ..config import output_path
from . import frame as F

OUT = output_path("2b", "k1_survey")
TOL = 0.001


def survey(d: pd.DataFrame) -> dict:
    nq4 = d[~d["q4"]].copy()
    nq4["year"] = nq4["quarter"].str[:4]
    cov = nq4.pivot_table(index=["ticker", "name", "grade"], columns="year", values="comparable", aggfunc="sum",
                          fill_value=0)
    cov["total"] = cov.sum(axis=1)
    hist = d[~d["q4"]].groupby("name")["has_hist_consensus"].any()
    no_cons = sorted(hist[~hist].index)                     # 2026Q2 이전 대상 분기 컨센서스가 하나도 없는 기업
    ratio = nq4.groupby(["ticker", "name"])["ratio_final_actual"].median().rename("ratio_median").reset_index()
    ratio["out_of_0.8_1.25"] = ratio["ratio_median"].notna() & ~ratio["ratio_median"].between(F.BASIS_LO, F.BASIS_HI)
    fx = nq4.dropna(subset=["c_after", "c_final", "actual"])
    eq_final = ((fx["c_after"] / fx["c_final"] - 1).abs() <= TOL).mean() if len(fx) else float("nan")
    eq_actual = ((fx["c_after"] / fx["actual"] - 1).abs() <= TOL).mean() if len(fx) else float("nan")
    n_comp = int(nq4["comparable"].sum())
    overall_ratio = float(nq4.loc[nq4["comparable"], "ratio_final_actual"].median())
    stops = {"events_lt_150": n_comp < 150, "after_eq_actual_ge_30pct": bool(eq_actual >= 0.30),
             "ratio_median_out_0.9_1.1": not (0.9 <= overall_ratio <= 1.1)}
    return {"coverage": cov, "no_consensus": no_cons, "ratio": ratio,
            "summary": {"events_max_non_q4": len(nq4), "events_comparable": n_comp,
                        "events_in_judgement": int(d["in_judgement"].sum()),
                        "fixed_check_n": int(len(fx)), "c_after_eq_c_final": float(eq_final),
                        "c_after_eq_actual": float(eq_actual), "ratio_median_overall": overall_ratio,
                        "c_pre5_asof_eq_c_final_asof": float((nq4.loc[nq4["comparable"], "c_pre5_asof"]
                                                              == nq4.loc[nq4["comparable"], "c_final_asof"]).mean()),
                        "daily_events": int((nq4["comparable"] & nq4["daily"]).sum()),
                        "basis_mismatch_firms": ratio.loc[ratio["out_of_0.8_1.25"], "name"].tolist(),
                        "stop": stops, "stop_any": any(stops.values())}}


def main():
    d = F.build_events()
    d.to_csv(OUT / "events_frame.csv", index=False, encoding="utf-8-sig")
    s = survey(d)
    s["coverage"].to_csv(OUT / "coverage.csv", encoding="utf-8-sig")
    s["ratio"].to_csv(OUT / "basis_ratio.csv", index=False, encoding="utf-8-sig")
    (OUT / "summary.json").write_text(json.dumps({**s["summary"], "no_consensus": s["no_consensus"]},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    pd.set_option("display.width", 220)
    print(json.dumps(s["summary"], ensure_ascii=False, indent=1))
    print("컨센서스 없음:", s["no_consensus"])
    print(s["coverage"].to_string())
    print(s["ratio"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
