# -*- coding: utf-8 -*-
"""
STEP 12. Korea_revenue_forecast_v4 노트북이 DB에 저장한 '실제 예측값'을 실적과 대조

  korea_revenue_forecast_result 에는 실행시점(created_at)별 vintage 가 남아 있다.
  각 vintage 는 그 시점에 공시돼 있던 분기까지만 보고 앞으로를 예측한 것이므로,
  **진짜 표본밖(out-of-sample) 예측**이다. 이제 실적이 나온 분기와 대조하면
  이 방법이 실제로 얼마나 맞는지 사후 가정 없이 측정할 수 있다.

기준모형 (같은 시점 정보만 사용)
  naive_last : 직전 공시분기 매출 그대로
  naive_seas : 전년 동기 매출 그대로
  naive_yoy  : 전년 동기 매출 x (직전 공시분기의 전년동기 증가율)   <- 앞선 분석의 NAIVE
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from common import q, D_TAB, D_FIG, log, setup_matplotlib

plt = setup_matplotlib()
MODELS = ["Ensemble", "SARIMA", "ETS", "Theta"]
NAIVES = ["naive_yoy", "naive_seas", "naive_last"]


def norm_tk(s: pd.Series) -> pd.Series:
    b = s.astype(str).str.upper().str.replace("^A", "", regex=True).str.zfill(6)
    return "A" + b


def load_forecasts() -> pd.DataFrame:
    f = q("SELECT date, ticker, indicator, value, DATE(created_at) AS vintage "
          "FROM korea_revenue_forecast_result")
    f["date"] = pd.to_datetime(f["date"]).dt.to_period("Q")
    f["vintage"] = pd.to_datetime(f["vintage"])
    f["ticker"] = norm_tk(f["ticker"])
    f = f[f["indicator"].isin(MODELS)]
    f = f.pivot_table(index=["ticker", "vintage", "date"], columns="indicator",
                      values="value", aggfunc="last").reset_index()
    return f


def load_actuals() -> pd.DataFrame:
    a = q("SELECT ticker, date, value FROM korea_fs_data_from_DG "
          "WHERE item_code='M000904001' AND sj_div='IS'")
    a["ticker"] = norm_tk(a["ticker"])
    a["q"] = (pd.to_datetime(a["date"]) + pd.offsets.QuarterEnd(0)).dt.to_period("Q")
    a = (a.sort_values("date").groupby(["ticker", "q"], as_index=False)["value"].last()
           .rename(columns={"value": "actual"}))
    return a


def main():
    f = load_forecasts()
    a = load_actuals()
    log("EVAL", "예측 {:,}행 / 실적 {:,}행".format(len(f), len(a)))

    # vintage 별 '마지막 관측분기' = 그 vintage 예측의 첫 분기 - 1
    first_q = f.groupby(["ticker", "vintage"])["date"].min().rename("first_fc_q").reset_index()
    first_q["last_obs_q"] = first_q["first_fc_q"] - 1
    f = f.merge(first_q, on=["ticker", "vintage"])
    f["horizon"] = (f["date"] - f["last_obs_q"]).apply(lambda x: x.n)

    # 실적 결합
    d = f.merge(a.rename(columns={"q": "date"}), on=["ticker", "date"], how="inner")

    # 기준모형 (vintage 시점 정보만)
    av = a.set_index(["ticker", "q"])["actual"]
    def lk(tk, qq):
        try:
            return av.loc[(tk, qq)]
        except KeyError:
            return np.nan
    d["last_obs_val"] = [lk(t, p) for t, p in zip(d["ticker"], d["last_obs_q"])]
    d["seas_val"] = [lk(t, p - 4) for t, p in zip(d["ticker"], d["date"])]
    d["last_obs_seas"] = [lk(t, p - 4) for t, p in zip(d["ticker"], d["last_obs_q"])]

    # ---- 단위 불일치 보정 (DB 버그) ----
    # 2026-02-02 이전 vintage 는 매출을 '원' 단위로 저장(이후는 '천원'). 1000배 차이.
    # 실적을 쓰지 않고, vintage 시점에 이미 알려진 '직전 공시분기 매출'과의 배율로 판정한다.
    d["_scale_ref"] = d["Ensemble"] / d["last_obs_val"]
    vs = d.groupby("vintage")["_scale_ref"].median()
    factor = np.where(vs > 100, 1000.0, 1.0)
    fmap = pd.Series(factor, index=vs.index, name="unit_factor")
    log("EVAL", "vintage 단위보정: " + ", ".join(
        "{:%Y-%m-%d}={:.0f}x".format(k, v) for k, v in fmap.items()))
    d = d.merge(fmap, left_on="vintage", right_index=True)
    for c in MODELS:
        d[c] = d[c] / d["unit_factor"]

    d["naive_last"] = d["last_obs_val"]
    d["naive_seas"] = d["seas_val"]
    g = d["last_obs_val"] / d["last_obs_seas"]
    g = g.where(d["last_obs_seas"] > 0)
    d["naive_yoy"] = d["seas_val"] * g

    d = d[d["actual"] > 0].copy()
    for c in MODELS + NAIVES:
        d["ape_" + c] = (d[c] - d["actual"]).abs() / d["actual"] * 100

    d.to_csv(D_TAB / "step19_notebook_forecast_eval_raw.csv", index=False, encoding="utf-8-sig")

    # ---------------- 전체 종목 평가 ----------------
    rows = []
    for h in sorted(d["horizon"].unique()):
        s = d[d["horizon"] == h]
        s = s.dropna(subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(s) < 30:
            continue
        r = {"예측시계_분기": h, "n": len(s), "종목수": s["ticker"].nunique()}
        for c in MODELS + NAIVES:
            r["MdAPE_" + c] = s["ape_" + c].median()
        r["앙상블_승률_vs_naive_yoy"] = (s["ape_Ensemble"] < s["ape_naive_yoy"]).mean() * 100
        rows.append(r)
    byh = pd.DataFrame(rows)
    byh.to_csv(D_TAB / "step20_notebook_eval_by_horizon.csv", index=False, encoding="utf-8-sig")

    print("\n=== 전체 종목 : 예측시계별 중위절대오차율 MdAPE (%) ===")
    cols = ["예측시계_분기", "n", "종목수"] + ["MdAPE_" + c for c in MODELS + NAIVES] + \
           ["앙상블_승률_vs_naive_yoy"]
    print(byh[cols].round(2).to_string(index=False))

    # 성장률 구간별 (에이피알 같은 고성장주에서 어떤가)
    d["yoy_at_vintage"] = (g - 1) * 100
    bins = [-1e9, 0, 20, 50, 1e9]
    labs = ["역성장(<0%)", "저성장(0~20%)", "중성장(20~50%)", "고성장(>50%)"]
    d["성장구간"] = pd.cut(d["yoy_at_vintage"], bins=bins, labels=labs)
    rows = []
    for lab in labs:
        s = d[(d["성장구간"] == lab) & (d["horizon"] <= 4)].dropna(
            subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(s) < 20:
            continue
        rows.append({"성장구간": lab, "n": len(s), "종목수": s["ticker"].nunique(),
                     "MdAPE_앙상블": s["ape_Ensemble"].median(),
                     "MdAPE_naive_yoy": s["ape_naive_yoy"].median(),
                     "MdAPE_naive_seas": s["ape_naive_seas"].median(),
                     "앙상블_승률_pct": (s["ape_Ensemble"] < s["ape_naive_yoy"]).mean() * 100})
    byg = pd.DataFrame(rows)
    byg.to_csv(D_TAB / "step21_notebook_eval_by_growth.csv", index=False, encoding="utf-8-sig")
    print("\n=== 성장구간별 (예측시계 1~4분기) ===")
    print(byg.round(2).to_string(index=False))

    # ---------------- 에이피알 ----------------
    for tk, nm in (("A278470", "에이피알"), ("A003350", "한국화장품제조")):
        s = d[d["ticker"] == tk].sort_values(["vintage", "date"])
        if s.empty:
            print("\n=== {} : 대조 가능한 예측 없음 ===".format(nm))
            continue
        print("\n=== {} ({}) : 저장된 예측 vs 실제 (단위 십억원) ===".format(nm, tk))
        o = pd.DataFrame({
            "예측시점": s["vintage"].dt.strftime("%Y-%m-%d"),
            "대상분기": s["date"].astype(str),
            "시계": s["horizon"],
            "실제": s["actual"] / 1e6,
            "앙상블": s["Ensemble"] / 1e6,
            "오차%": (s["Ensemble"] - s["actual"]) / s["actual"] * 100,
            "SARIMA": s["SARIMA"] / 1e6, "ETS": s["ETS"] / 1e6, "Theta": s["Theta"] / 1e6,
            "naive_yoy": s["naive_yoy"] / 1e6,
            "naive오차%": (s["naive_yoy"] - s["actual"]) / s["actual"] * 100,
        })
        print(o.round(1).to_string(index=False))
        v = s.dropna(subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(v):
            print("  평균절대오차율 : 앙상블 {:.1f}%  vs  naive_yoy {:.1f}%  (n={})".format(
                v["ape_Ensemble"].mean(), v["ape_naive_yoy"].mean(), len(v)))
    return d, byh, byg


if __name__ == "__main__":
    main()
