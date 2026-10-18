# -*- coding: utf-8 -*-
"""2d 작업 2 C: 사전 점검 P1 - B2 가 시장 기대를 대신할 수 있는가 (명세서 2d v2 5절). DB 쓰기 없음.

표본: fd_eval_2b 의 최신 run (2b K2, 20261012_222348) 판정 표본 488건 (레이더 39곳, 2022Q1~2026Q2, 4분기·기준 불일치·
기업 사건 제외). 2b Q3 와 같은 표본이라 결과를 바로 비교할 수 있다. 참고로 4분기 포함 전체(값이 다 있는 행)도 낸다.
- SUR_B2 = ln(실제) - ln(B2 pre5), SUR_cons = ln(실제) - ln(C_pre5 = 발표 5영업일 전 컨센서스)
- 부호 일치율: 둘 다 0 이 아닌 행에서 sign 같음 비율. 상관: Pearson, Spearman.
- 주가 반응 스프레드: 2b Q3 (c) 와 같은 방식. 분기마다 신호 상위 1/3 - 하위 1/3 의 코스피 대비 CAR(발표 -3 ~ +3 거래일)
  차이의 분기 평균 (evaluate.event_study). 신호를 SUR_B2, SUR_cons, 2b surprise(C_final 기준) 셋으로 낸다.
  참고로 D2 창 CAR[0,+1] 도 같은 방식으로 낸다.
- 해석 기준 (고정): 부호 일치율 >= 65% 이고 SUR_B2 스프레드(-3~+3) > 0 이면 '기대 대용 무리 없음', 아니면 '기대 대용 약함'.
실행: python -m DATA.fdriver.eval2d.k2_p1
"""
import json

import numpy as np
import pandas as pd
from scipy import stats

from ..config import output_path
from ..eval2b import k2_table
from ..eval2b.k3_analysis import tercile_by_quarter
from ..evaluate import event_study as es

OUT = output_path("2d", "K2")
AGREE_MIN = 0.65
WINDOWS = {"m3p3": (-3, 3), "d0p1": (0, 1)}


def add_cars(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    ok = d["announce_date"].notna()
    xr = es.excess_returns(sorted(d.loc[ok, "ticker"].unique()))
    cal = xr["cal"].values
    for w in WINDOWS:
        d[f"car_{w}"] = np.nan
    for i, r in d[ok].iterrows():
        e = r["ticker"]
        if e not in xr["ex"]:
            continue
        d0 = int(np.searchsorted(cal, np.datetime64(pd.Timestamp(r["announce_date"])), side="left"))
        for w, (a, b) in WINDOWS.items():
            c = es.car(xr["ex"][e], xr["cal"], d0 + a, d0 + b, xr["last"][e], xr["first"].get(e))
            d.loc[i, f"car_{w}"] = c["car"] * 100 if pd.notna(c["car"]) else np.nan
    return d


def agreement(d: pd.DataFrame) -> dict:
    x = d.dropna(subset=["sur_b2", "sur_cons"])
    x = x[(x["sur_b2"] != 0) & (x["sur_cons"] != 0)]
    same = np.sign(x["sur_b2"]) == np.sign(x["sur_cons"])
    bt = stats.binomtest(int(same.sum()), len(x), 0.5, alternative="greater") if len(x) else None
    return {"n": len(x), "sign_agree": float(same.mean()) if len(x) else np.nan,
            "binom_p_vs_50": float(bt.pvalue) if bt else np.nan,
            "pearson": float(x["sur_b2"].corr(x["sur_cons"])), "spearman": float(x["sur_b2"].corr(x["sur_cons"],
                                                                                                method="spearman")),
            "sur_b2_median": float(x["sur_b2"].median()), "sur_cons_median": float(x["sur_cons"].median()),
            "sur_b2_abs_median": float(x["sur_b2"].abs().median()),
            "sur_cons_abs_median": float(x["sur_cons"].abs().median())}


def spreads(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sig in ("sur_b2", "sur_cons", "surprise"):
        for w in WINDOWS:
            r = tercile_by_quarter(d, sig, f"car_{w}")
            rows.append({"signal": sig, "window": w, **{k: v for k, v in r.items() if k != "table"}})
    return pd.DataFrame(rows)


def main():
    d = k2_table.load()
    run_id = str(d["run_id"].iloc[0]) if "run_id" in d else ""
    d = d[d["quarter"] >= "2022Q1"].copy()
    d["sur_b2"] = np.log(d["actual"] / d["n_B2"])
    d["sur_cons"] = np.log(d["actual"] / d["c_pre5"])
    d = add_cars(d)
    j = d[d["in_judgement"].astype(bool)]
    full = d.dropna(subset=["actual", "n_B2", "c_pre5"])
    res = {"run_id": run_id, "judgement": agreement(j), "all_with_values": agreement(full)}
    sp = spreads(j)
    sp_all = spreads(full)
    b2 = sp[(sp["signal"] == "sur_b2") & (sp["window"] == "m3p3")].iloc[0]
    ok = bool(res["judgement"]["sign_agree"] >= AGREE_MIN and b2["spread_mean"] > 0)
    res["decision"] = {
        "sign_agree": res["judgement"]["sign_agree"], "sur_b2_spread_m3p3": float(b2["spread_mean"]),
        "pass": ok,
        "text": ("B2 를 기대 대용으로 쓰는 데 무리가 없음 (부호 일치율 {:.1%} >= 65%, SUR_B2 스프레드 {:+.2f}%p > 0)"
                 if ok else "기대 대용 약함 (부호 일치율 {:.1%}, SUR_B2 스프레드 {:+.2f}%p; 기준 65% 이상 + 양수). "
                            "D2 판정은 유지하고 결과 해석에 '기대 대용 약함'을 적는다")
        .format(res["judgement"]["sign_agree"], b2["spread_mean"])}
    by_firm = j.dropna(subset=["sur_b2", "sur_cons"]).groupby(["ticker", "name"]).apply(
        lambda g: pd.Series({"n": len(g), "sign_agree": float((np.sign(g["sur_b2"]) == np.sign(g["sur_cons"])).mean()),
                             "corr": g["sur_b2"].corr(g["sur_cons"])})).reset_index()
    j[["ticker", "name", "quarter", "announce_date", "pre5_date", "actual", "n_B2", "c_pre5", "c_final", "sur_b2",
       "sur_cons", "surprise", "car_m3p3", "car_d0p1"]].to_csv(OUT / "k2_p1_events.csv", index=False,
                                                                encoding="utf-8-sig")
    sp.assign(sample="judgement").pipe(lambda x: pd.concat([x, sp_all.assign(sample="all_with_values")])).to_csv(
        OUT / "k2_p1_spreads.csv", index=False, encoding="utf-8-sig")
    by_firm.to_csv(OUT / "k2_p1_by_firm.csv", index=False, encoding="utf-8-sig")
    (OUT / "k2_p1_summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float),
                                           encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(sp.round(3).to_string())
    print(sp_all.round(3).to_string())
    return res


if __name__ == "__main__":
    main()
