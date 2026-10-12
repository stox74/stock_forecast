# -*- coding: utf-8 -*-
"""2b 작업 3~5: Q1 정보 보탬, Q2 방향 적중, Q3 주가 반응 (명세서 6~8절). fd_eval_2b 만 읽는다. DB 쓰기 없음.

판정 표본: in_judgement (4분기·기준 불일치·기업 사건 제외, 비교 가능 행).
- Q1: surprise = a + b * gap_E, 기업·분기 두 방향 군집 표준오차 (statsmodels cluster, groups 2열). 성공 = b > 0 이고 p < 0.10.
  p 는 2c 판정과 같이 예측 방향 단측 (two-sided 도 함께 냄). M4 는 참고.
- Q2: sign(gap_E) = sign(surprise) 적중률 >= 55% 이고 이항검정(귀무 50%, 단측 greater) p < 0.10. gap 또는 surprise 가 0 인 행 제외.
- Q3: 판정 없음. 분기마다 신호 상위 1/3 - 하위 1/3 의 코스피 대비 CAR 차이를 구해 분기 평균.
  (c) 발표일 -3 ~ +3 거래일, 신호 = 실제 surprise. (a) pre5 다음 거래일 ~ 발표 다음 거래일, (b) 발표 +2 ~ +20 거래일, 신호 = gap_E.
  CAR 계산은 2c C3 와 같은 evaluate.event_study (excess_returns, car), 참고로 2c C3 형 고정효과 회귀(분기 군집)도 낸다.
K2: fd_eval_2b 의 가장 최근 run 을 읽고 결과는 2b/k2_results (K1 결과는 2b/k1_results 그대로).
실행: python -m DATA.fdriver.eval2b.k3_analysis
"""
import json
from typing import Dict, Optional

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

from ..config import output_path
from ..evaluate import event_study as es
from ..stage2c.s3_lib import ALPHA, one_sided
from . import k2_table

HIT_MIN = 0.55
WINDOWS_Q3 = {"c": (-3, 3), "a": ("s+1", 1), "b": (2, 20)}


# ---------------------------------------------------------------- 공통
def grade_split(d: pd.DataFrame) -> pd.Series:
    return np.where(d["group"].eq("pilot"), "시범", d["grade"]) if "group" in d else d["grade"]


def judgement_sample(d: pd.DataFrame) -> pd.DataFrame:
    x = d[d["in_judgement"]].copy()
    x["year"] = x["quarter"].str[:4]
    return x


# ---------------------------------------------------------------- Q1
def twoway_ols(d: pd.DataFrame, y: str, x: str) -> Dict:
    z = d.dropna(subset=[y, x])
    if len(z) < 5 or z["ticker"].nunique() < 2 or z["quarter"].nunique() < 2:
        return {"n": len(z), "b": np.nan, "se": np.nan, "p_two": np.nan, "p_one": np.nan, "success": False}
    g = np.column_stack([pd.factorize(z["ticker"])[0], pd.factorize(z["quarter"])[0]])
    m = smf.ols(f"{y} ~ {x}", data=z)
    f = m.fit(cov_type="cluster", cov_kwds={"groups": g}, use_t=True)
    b, se, p2, fallback = float(f.params[x]), float(f.bse[x]), float(f.pvalues[x]), ""
    if not np.isfinite(se):
        # 두 방향 분산(기업 + 분기 - 교차)이 음수면 (분기 군집이 적을 때) 한 방향 군집 분산 중 큰 값 (Cameron-Gelbach-Miller)
        one = [m.fit(cov_type="cluster", cov_kwds={"groups": g[:, k]}, use_t=True) for k in (0, 1)]
        k = int(np.argmax([float(o.bse[x]) for o in one]))
        se, p2, fallback = float(one[k].bse[x]), float(one[k].pvalues[x]), ("firm", "quarter")[k] + "_only"
    p1 = one_sided(p2, b, "greater")
    return {"n": len(z), "n_firms": z["ticker"].nunique(), "n_quarters": z["quarter"].nunique(), "a": float(f.params["Intercept"]),
            "b": b, "se": se, "p_two": p2, "p_one": p1, "r2": float(f.rsquared), "se_fallback": fallback,
            "success": bool(b > 0 and p1 < ALPHA)}


FORECASTS = {"E": "n_E", "M4": "n_M4", "B2": "n_B2", "C_pre5": "c_pre5", "C_final": "c_final"}


def pct_errors(d: pd.DataFrame, cpre: str = "c_pre5", cfin: str = "c_final") -> pd.DataFrame:
    cols = {**FORECASTS, "C_pre5": cpre, "C_final": cfin}
    e = pd.DataFrame({k: (d[v] - d["actual"]) / d["actual"] for k, v in cols.items()}, index=d.index)
    e["comb"] = ((d["n_E"] + d[cpre]) / 2 - d["actual"]) / d["actual"]
    return e


def error_table(d: pd.DataFrame, **kw) -> pd.DataFrame:
    e = pct_errors(d, **kw)
    rows = []
    for k in list(FORECASTS) + ["comb"]:
        v = e[k].dropna()
        rows.append({"forecast": k, "n": len(v), "median_abs_pct": float(v.abs().median() * 100) if len(v) else np.nan,
                     "rmse_pct": float(np.sqrt((v ** 2).mean()) * 100) if len(v) else np.nan})
    return pd.DataFrame(rows)


def firm_rmse(d: pd.DataFrame, **kw) -> pd.DataFrame:
    e = pct_errors(d, **kw).assign(ticker=d["ticker"], name=d["name"])
    e = e.dropna(subset=["E", "C_pre5"])
    rm = lambda s: float(np.sqrt((s ** 2).mean()))
    t = e.groupby(["ticker", "name"]).agg(n=("E", "size"), rmse_E=("E", rm), rmse_C_pre5=("C_pre5", rm),
                                          rmse_comb=("comb", rm)).reset_index()
    t["rel_E_C"] = t["rmse_E"] / t["rmse_C_pre5"]
    t["comb_beats_C"] = t["rmse_comb"] < t["rmse_C_pre5"]
    return t


def firm_summary(t: pd.DataFrame) -> Dict:
    return {"firms": len(t), "rel_rmse_median": float(t["rel_E_C"].median()) if len(t) else np.nan,
            "rel_lt1_share": float((t["rel_E_C"] < 1).mean()) if len(t) else np.nan,
            "comb_beats_C_share": float(t["comb_beats_C"].mean()) if len(t) else np.nan}


def q1(d: pd.DataFrame) -> Dict:
    j = judgement_sample(d)
    main = twoway_ols(j, "surprise", "gap_E")
    m4 = twoway_ols(j, "surprise", "gap_M4")
    ft = firm_rmse(j)
    splits = []
    for col, lab in [("grade_split", "등급"), ("year", "연도")]:
        for k, g in j.groupby(col):
            r = twoway_ols(g, "surprise", "gap_E")
            et = error_table(g).set_index("forecast")
            splits.append({"split": lab, "group": k, "n": len(g), "b": r["b"], "p_one": r["p_one"], "se_fallback": r["se_fallback"],
                           "mape_E": et.loc["E", "median_abs_pct"], "mape_C_pre5": et.loc["C_pre5", "median_abs_pct"],
                           "mape_C_final": et.loc["C_final", "median_abs_pct"], **firm_summary(firm_rmse(g))})
    # 2023 일별 원천: 같은 이벤트에서 월별과 일별
    j23 = j[(j["year"] == "2023") & j["c_pre5_daily"].notna() & j["c_final_daily"].notna()].copy()
    daily = []
    if len(j23):
        jd = j23.assign(surprise=j23["surprise_daily"], gap_E=j23["gap_E_daily"])
        for lab, x, kw in [("2023 월별", j23, {}), ("2023 일별", jd, {"cpre": "c_pre5_daily", "cfin": "c_final_daily"})]:
            r = twoway_ols(x, "surprise", "gap_E")
            et = error_table(x, **kw).set_index("forecast")
            daily.append({"split": lab, "n": len(x), "b": r["b"], "p_one": r["p_one"],
                          "mape_E": et.loc["E", "median_abs_pct"], "mape_C_pre5": et.loc["C_pre5", "median_abs_pct"],
                          "mape_C_final": et.loc["C_final", "median_abs_pct"], **firm_summary(firm_rmse(x, **kw))})
    return {"reg_E": main, "reg_M4": m4, "errors": error_table(j), "firm_rmse": ft, "firm_summary": firm_summary(ft),
            "splits": pd.DataFrame(splits), "daily_2023": pd.DataFrame(daily)}


# ---------------------------------------------------------------- Q2
def hit_stats(sig: pd.Series, target: pd.Series) -> Dict:
    ok = sig.notna() & target.notna() & (sig != 0) & (target != 0)
    s, t = np.sign(sig[ok]), np.sign(target[ok])
    n, k = int(ok.sum()), int((s == t).sum())
    p = float(stats.binomtest(k, n, 0.5, alternative="greater").pvalue) if n else np.nan
    p2 = float(stats.binomtest(k, n, 0.5).pvalue) if n else np.nan
    rate = k / n if n else np.nan
    return {"n": n, "hits": k, "hit_rate": rate, "p_one": p, "p_two": p2,
            "success": bool(n and rate >= HIT_MIN and p < ALPHA)}


def q2(d: pd.DataFrame) -> Dict:
    j = judgement_sample(d)
    main = hit_stats(j["gap_E"], j["surprise"])
    v = j[(j["gap_E"] != 0) & (j["surprise"] != 0)].dropna(subset=["gap_E", "surprise"]).copy()
    v["size"] = pd.qcut(v["gap_E"].abs(), 3, labels=["하위", "중간", "상위"])
    v["signed_surprise"] = np.sign(v["gap_E"]) * v["surprise"]
    size = v.groupby("size", observed=True).apply(lambda g: pd.Series({
        **hit_stats(g["gap_E"], g["surprise"]), "abs_gap_median": g["gap_E"].abs().median(),
        "mean_surprise": g["surprise"].mean(), "mean_surprise_in_gap_dir": g["signed_surprise"].mean()})).reset_index()
    grade = pd.DataFrame([{"group": k, **hit_stats(g["gap_E"], g["surprise"])} for k, g in j.groupby("grade_split")])
    j["agree"] = np.where(j["gap_M4"].isna(), "-", np.where(np.sign(j["gap_M4"]) == np.sign(j["gap_E"]), "일치", "불일치"))
    agree = pd.DataFrame([{"group": k, **hit_stats(g["gap_E"], g["surprise"])} for k, g in j.groupby("agree")])
    same = j.dropna(subset=["nsur_drv", "gap_E", "surprise"])
    same = same[(same["nsur_drv"] != 0) & (same["gap_E"] != 0) & (same["surprise"] != 0)]
    sigcmp = pd.DataFrame([{"signal": "gap_E", **hit_stats(same["gap_E"], same["surprise"])},
                           {"signal": "nsur_drv", **hit_stats(same["nsur_drv"], same["surprise"])},
                           {"signal": "gap_M4", **hit_stats(same["gap_M4"], same["surprise"])}])
    revc = hit_stats(j["gap_E"], j["rev_c"])
    revc["rev_c_zero"] = int((j["rev_c"] == 0).sum())
    return {"main": main, "size": size, "grade": grade, "agree": agree, "signal_compare": sigcmp, "rev_c": revc}


# ---------------------------------------------------------------- Q3
def add_cars(d: pd.DataFrame, xr: Optional[Dict] = None) -> pd.DataFrame:
    d = d.copy()
    for w in WINDOWS_Q3:
        d[f"car_{w}"] = np.nan
    ok = d["announce_date"].notna()
    xr = xr or es.excess_returns(sorted(d.loc[ok, "ticker"].unique()))
    cal = xr["cal"].values
    for i, r in d[ok].iterrows():
        e = r["ticker"]
        if e not in xr["ex"]:
            continue
        d0 = int(np.searchsorted(cal, np.datetime64(pd.Timestamp(r["announce_date"])), side="left"))
        s1 = int(np.searchsorted(cal, np.datetime64(pd.Timestamp(r["pre5_date"])), side="right")) \
            if pd.notna(r["pre5_date"]) else None
        for w, (a, b) in WINDOWS_Q3.items():
            start = s1 if a == "s+1" else d0 + a
            if start is None:
                continue
            c = es.car(xr["ex"][e], xr["cal"], start, d0 + b, xr["last"][e], xr["first"].get(e))
            d.loc[i, f"car_{w}"] = c["car"] * 100 if pd.notna(c["car"]) else np.nan
    return d


def tercile_by_quarter(d: pd.DataFrame, sig: str, car: str) -> Dict:
    rows = []
    for q, g in d.dropna(subset=[sig, car]).groupby("quarter"):
        g = g.sort_values(sig)
        k = len(g) // 3
        if k < 1:
            continue
        rows.append({"quarter": q, "n": len(g), "n_each": k, "top": g[car].iloc[-k:].mean(), "bottom": g[car].iloc[:k].mean()})
    t = pd.DataFrame(rows)
    if t.empty:
        return {"quarters": 0, "spread_mean": np.nan, "t": np.nan, "p_two": np.nan, "table": t}
    t["spread"] = t["top"] - t["bottom"]
    s = t["spread"]
    tt = stats.ttest_1samp(s, 0.0) if len(s) > 1 else None
    return {"quarters": len(t), "events": int(t["n"].sum()), "spread_mean": float(s.mean()),
            "spread_pos_share": float((s > 0).mean()), "t": float(tt.statistic) if tt else np.nan,
            "p_two": float(tt.pvalue) if tt else np.nan, "table": t}


def fe_reg(d: pd.DataFrame, y: str, x: str) -> Dict:
    z = d.rename(columns={"ticker": "entity_id", "quarter": "fiscal_quarter"})
    r = es.fe_cluster_ols(z, y, [x])
    if r["coef"].empty:
        return {"n": r["n"], "b": np.nan, "p_two": np.nan}
    c = r["coef"].loc[x]
    return {"n": r["n"], "b": float(c["coef"]), "se": float(c["se"]), "p_two": float(c["p"])}


def q3(d: pd.DataFrame, xr: Optional[Dict] = None) -> Dict:
    j = add_cars(judgement_sample(d), xr)
    out = {"events": j}
    for w, sig in [("c", "surprise"), ("a", "gap_E"), ("b", "gap_E")]:
        r = tercile_by_quarter(j, sig, f"car_{w}")
        out[w] = {**{k: v for k, v in r.items() if k != "table"}, "signal": sig,
                  "fe_reg": fe_reg(j, f"car_{w}", sig), "table": r["table"]}
        if w == "a":
            out[w]["candidate"] = bool(pd.notna(r["spread_mean"]) and r["spread_mean"] > 0)
    # 등급별 (a)(b)
    g_rows = []
    for k, g in j.groupby("grade_split"):
        for w in ("a", "b"):
            r = tercile_by_quarter(g, "gap_E", f"car_{w}")
            g_rows.append({"group": k, "window": w, **{kk: v for kk, v in r.items() if kk != "table"}})
    out["by_grade"] = pd.DataFrame(g_rows)
    return out


# ---------------------------------------------------------------- 실행
def decision(r1: Dict, r2: Dict) -> Dict:
    q1_ok, q2_ok = r1["reg_E"]["success"], r2["main"]["success"]
    sc = r2["signal_compare"].set_index("signal")
    gap_better = bool(sc.loc["gap_E", "hit_rate"] > sc.loc["nsur_drv", "hit_rate"])
    switch = bool(q1_ok and q2_ok and gap_better)
    return {"Q1_success": q1_ok, "Q2_success": q2_ok, "gap_E_hit_gt_nsur_drv": gap_better,
            "radar_main_signal": "gap_E (컨센서스 있는 종목), nsur_drv (없는 종목)" if switch else "nsur_drv 유지",
            "switch": switch}


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items() if not isinstance(v, pd.DataFrame)}
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def main(run_id=None, out_name="k2_results"):
    OUT = output_path("2b", out_name)
    d = k2_table.load(run_id)
    from ..radar.universe import universe
    u = universe().set_index("entity_id")
    d["group"] = d["ticker"].map(u["group"])
    d["grade_split"] = grade_split(d)
    r1, r2, r3 = q1(d), q2(d), q3(d)
    dec = decision(r1, r2)
    r1["errors"].to_csv(OUT / "q1_errors.csv", index=False, encoding="utf-8-sig")
    r1["firm_rmse"].to_csv(OUT / "q1_firm_rmse.csv", index=False, encoding="utf-8-sig")
    r1["splits"].to_csv(OUT / "q1_splits.csv", index=False, encoding="utf-8-sig")
    r1["daily_2023"].to_csv(OUT / "q1_daily_2023.csv", index=False, encoding="utf-8-sig")
    for k in ["size", "grade", "agree", "signal_compare"]:
        r2[k].to_csv(OUT / f"q2_{k}.csv", index=False, encoding="utf-8-sig")
    r3["events"].to_csv(OUT / "q3_events_cars.csv", index=False, encoding="utf-8-sig")
    for w in ("a", "b", "c"):
        r3[w]["table"].to_csv(OUT / f"q3_{w}_by_quarter.csv", index=False, encoding="utf-8-sig")
    r3["by_grade"].to_csv(OUT / "q3_by_grade.csv", index=False, encoding="utf-8-sig")
    summ = {"Q1": {"reg_E": r1["reg_E"], "reg_M4": r1["reg_M4"], "firm": r1["firm_summary"]},
            "Q2": {"main": r2["main"], "rev_c": r2["rev_c"]},
            "Q3": {w: {k: v for k, v in r3[w].items() if k != "table"} for w in ("c", "a", "b")},
            "decision": dec, "run_id": d["run_id"].iloc[0]}
    (OUT / "summary.json").write_text(json.dumps(_jsonable(summ), ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    pd.set_option("display.width", 220)
    print(json.dumps(_jsonable(summ), ensure_ascii=False, indent=1, default=str))
    for name, t in [("errors", r1["errors"]), ("splits", r1["splits"]), ("daily", r1["daily_2023"]),
                    ("size", r2["size"]), ("grade", r2["grade"]), ("agree", r2["agree"]),
                    ("signal", r2["signal_compare"]), ("q3 grade", r3["by_grade"])]:
        print(f"--- {name}\n{t.round(4).to_string(index=False)}")
    return r1, r2, r3, dec


if __name__ == "__main__":
    main()
