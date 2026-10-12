# -*- coding: utf-8 -*-
"""2b K2 탐색 분석 (판정에 쓰지 않음). fd_eval_2b 최근 run 의 판정 표본만 쓴다. DB 쓰기 없음.

1. E 편향 분해: 편향 = (예측 - 실제) / 실제. E 와 구성 요소(드라이버 요소 n_drv = M1/M2/M3, B2, TS), 참고로 M4·C_pre5.
   전체와 연도별 중앙값.
2. 편향 제거 gap: gap_adj = gap_E - (그 기업의 앞선 분기 gap_E 평균). 앞선 분기 = pre5 기준일이 이번 pre5 보다 앞선
   4분기 아닌 분기 중 판정 제외 flag 가 없는 분기, 최소 MIN_PRIOR 개 (모자라면 gap_adj 없음).
   gap_adj 로 Q2 부호 적중률(이항검정)과 포괄 회귀 b (기업·분기 두 방향 군집). 같은 표본의 원래 gap_E 도 함께.
실행: python -m DATA.fdriver.eval2b.k4_explore
"""
import json

import numpy as np
import pandas as pd

from ..config import output_path
from . import frame as F
from . import k2_table
from .k3_analysis import _jsonable, hit_stats, twoway_ols

MIN_PRIOR = 4
PARTS = {"E": "n_E", "drv(M1/M2/M3)": "n_drv", "B2": "n_B2", "TS": "n_TS", "M4 (참고)": "n_M4", "C_pre5 (참고)": "c_pre5"}


def bias_table(j: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for grp, g in [("전체", j)] + list(j.groupby(j["quarter"].str[:4])):
        r = {"year": grp, "n": len(g)}
        for k, c in PARTS.items():
            b = ((g[c] - g["actual"]) / g["actual"]).dropna()
            r[f"{k} 중앙값%"] = float(b.median() * 100) if len(b) else np.nan
            r[f"{k} n"] = len(b)
        rows.append(r)
    return pd.DataFrame(rows)


def prior_gap_mean(d: pd.DataFrame, min_prior: int = MIN_PRIOR) -> pd.Series:
    """행마다 같은 기업의 앞선 (pre5 기준일이 더 이른) 적격 분기 gap_E 평균. 적격 = 4분기 아님, 제외 flag 없음, gap_E 있음."""
    ok = ~d["q4"] & d["gap_E"].notna() & ~d[F.EXCLUDE_FLAGS[1:]].any(axis=1) & d["pre5_date"].notna()
    out = pd.Series(np.nan, index=d.index)
    for _, g in d.groupby("ticker"):
        base = g[ok.loc[g.index]].sort_values("pre5_date")
        for i, r in g.iterrows():
            if pd.isna(r["pre5_date"]):
                continue
            prev = base[base["pre5_date"] < r["pre5_date"]]["gap_E"]
            if len(prev) >= min_prior:
                out[i] = prev.mean()
    return out


def debiased(d: pd.DataFrame) -> dict:
    d = d.copy()
    d["prior_gap_mean"] = prior_gap_mean(d)
    d["gap_adj"] = d["gap_E"] - d["prior_gap_mean"]
    j = d[d["in_judgement"] & d["gap_adj"].notna()]
    rows = []
    for sig in ("gap_adj", "gap_E"):
        h = hit_stats(j[sig], j["surprise"])
        r = twoway_ols(j, "surprise", sig)
        rows.append({"signal": sig, "n": h["n"], "hit_rate": h["hit_rate"], "p_one_binom": h["p_one"],
                     "b": r["b"], "se": r["se"], "p_one_reg": r["p_one"], "p_two_reg": r["p_two"],
                     "se_fallback": r["se_fallback"]})
    return {"table": pd.DataFrame(rows), "events": d, "n_firms": int(j["ticker"].nunique()),
            "quarters": sorted(j["quarter"].unique())}


def main(run_id=None):
    out = output_path("2b", "k2_explore")
    d = k2_table.load(run_id)
    j = d[d["in_judgement"]]
    bt = bias_table(j)
    db_ = debiased(d)
    bt.to_csv(out / "bias_by_year.csv", index=False, encoding="utf-8-sig")
    db_["table"].to_csv(out / "debiased_gap.csv", index=False, encoding="utf-8-sig")
    db_["events"][["ticker", "name", "quarter", "pre5_date", "gap_E", "prior_gap_mean", "gap_adj", "surprise",
                   "in_judgement"]].to_csv(out / "debiased_events.csv", index=False, encoding="utf-8-sig")
    summ = {"run_id": d["run_id"].iloc[0], "debiased_firms": db_["n_firms"], "debiased_quarters": db_["quarters"]}
    (out / "summary.json").write_text(json.dumps(_jsonable(summ), ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    pd.set_option("display.width", 250)
    print(json.dumps(_jsonable(summ), ensure_ascii=False, default=str))
    print(bt.round(2).to_string(index=False))
    print(db_["table"].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
