# -*- coding: utf-8 -*-
"""
STEP 13. Korea_revenue_forecast_v4 재평가 — vintage 오염 보정판

[왜 다시 하는가]
  korea_revenue_forecast_result 의 실제 UNIQUE KEY 는 (date, ticker, indicator) 로,
  created_at 이 빠져 있다. 노트북의 CREATE TABLE IF NOT EXISTS 는 created_at 을 포함한
  키를 선언하지만 테이블이 이미 존재하므로 아무 일도 하지 않는다.
  결과적으로 ON DUPLICATE KEY UPDATE 가 기존 행의 value 만 덮어쓰고 created_at 은
  원래 값을 유지한다 -> **created_at 은 신뢰할 수 없는 vintage 라벨**이다.
  실제로 전체 131,220행 중 71,072행(54.2%)이 나중에 덮어쓰였다.

[보정 방법]
  실제 예측 시점 = updated_at (값이 실제로 쓰인 시각)
  실적 공표 시점 = 대상분기말 + 45일 (분기보고서 법정기한)
  -> updated_at < 실적공표시점 인 행만 '진짜 표본밖 예측'으로 인정한다.
  -> 그 시점에 공시돼 있던 마지막 분기를 기준으로 예측시계(horizon)를 다시 계산한다.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import q, D_TAB, log

MODELS = ["Ensemble", "SARIMA", "ETS", "Theta"]
NAIVES = ["naive_yoy", "naive_seas", "naive_last"]
PUB_LAG_DAYS = 45          # 분기보고서 법정 제출기한


def norm_tk(s: pd.Series) -> pd.Series:
    b = s.astype(str).str.upper().str.replace("^A", "", regex=True).str.zfill(6)
    return "A" + b


def main():
    f = q("SELECT date, ticker, indicator, value, created_at, updated_at "
          "FROM korea_revenue_forecast_result")
    f["ticker"] = norm_tk(f["ticker"])
    f["target_q"] = (pd.to_datetime(f["date"]) + pd.offsets.QuarterEnd(0)).dt.to_period("Q")
    f["fc_time"] = pd.to_datetime(f["updated_at"])          # 실제 기록 시점
    f["label_time"] = pd.to_datetime(f["created_at"])       # DB 라벨(신뢰 불가)
    f["overwritten"] = f["fc_time"].dt.date != f["label_time"].dt.date
    f = f[f["indicator"].isin(MODELS)]

    w = f.pivot_table(index=["ticker", "target_q", "fc_time"], columns="indicator",
                      values="value", aggfunc="last").reset_index()

    a = q("SELECT ticker, date, value FROM korea_fs_data_from_DG "
          "WHERE item_code='M000904001' AND sj_div='IS'")
    a["ticker"] = norm_tk(a["ticker"])
    a["q"] = (pd.to_datetime(a["date"]) + pd.offsets.QuarterEnd(0)).dt.to_period("Q")
    a = (a.sort_values("date").groupby(["ticker", "q"], as_index=False)["value"].last()
           .rename(columns={"value": "actual"}))

    d = w.merge(a.rename(columns={"q": "target_q"}), on=["ticker", "target_q"], how="inner")

    # --- 진짜 표본밖인지 판정 : 예측시점이 실적 공표 전이어야 한다 ---
    d["actual_pub"] = d["target_q"].dt.end_time.dt.normalize() + pd.Timedelta(days=PUB_LAG_DAYS)
    d["is_oos"] = d["fc_time"] < d["actual_pub"]

    # --- 예측시점에 공시돼 있던 마지막 분기 ---
    def last_published(t):
        cand = pd.period_range(end=pd.Period(t, freq="Q"), periods=8, freq="Q")
        ok = [p for p in cand
              if p.end_time.normalize() + pd.Timedelta(days=PUB_LAG_DAYS) <= t]
        return ok[-1] if ok else pd.NaT
    uniq = {t: last_published(t) for t in d["fc_time"].unique()}
    d["last_obs_q"] = d["fc_time"].map(uniq)
    d["horizon"] = [(tq - lq).n if pd.notna(lq) else np.nan
                    for tq, lq in zip(d["target_q"], d["last_obs_q"])]

    # --- 단위 보정 ---
    av = a.set_index(["ticker", "q"])["actual"]
    lk = lambda tk, qq: av.get((tk, qq), np.nan)
    d["last_obs_val"] = [lk(t, p) for t, p in zip(d["ticker"], d["last_obs_q"])]
    d["_ref"] = d["Ensemble"] / d["last_obs_val"]
    vs = d.groupby(d["fc_time"].dt.date)["_ref"].median()
    fmap = pd.Series(np.where(vs > 100, 1000.0, 1.0), index=vs.index, name="unit_factor")
    d = d.merge(fmap, left_on=d["fc_time"].dt.date, right_index=True)
    for c in MODELS:
        d[c] = d[c] / d["unit_factor"]

    # --- 기준모형 ---
    d["seas_val"] = [lk(t, p - 4) for t, p in zip(d["ticker"], d["target_q"])]
    d["last_obs_seas"] = [lk(t, p - 4) for t, p in zip(d["ticker"], d["last_obs_q"])]
    d["naive_last"] = d["last_obs_val"]
    d["naive_seas"] = d["seas_val"]
    g = (d["last_obs_val"] / d["last_obs_seas"]).where(d["last_obs_seas"] > 0)
    d["naive_yoy"] = d["seas_val"] * g
    d["yoy_at_fc"] = (g - 1) * 100

    d = d[(d["actual"] > 0) & d["horizon"].notna()].copy()
    for c in MODELS + NAIVES:
        d["ape_" + c] = (d[c] - d["actual"]).abs() / d["actual"] * 100
    d.to_csv(D_TAB / "step22_eval_v2_raw.csv", index=False, encoding="utf-8-sig")

    log("EVAL2", "대조 {:,}건 / 그 중 진짜 표본밖 {:,}건 ({:.1f}%)".format(
        len(d), int(d["is_oos"].sum()), d["is_oos"].mean() * 100))

    # ---------- 오염 여부별 비교 ----------
    print("\n=== [핵심] 사후 기록(실적 공표 후 덮어쓴) 행을 빼면 결과가 달라지는가 ===")
    rows = []
    for lab, s in (("전체 (이전 평가 방식)", d),
                   ("진짜 표본밖만 (보정 후)", d[d["is_oos"]]),
                   ("실적 공표 후 기록 = 사후", d[~d["is_oos"]])):
        s = s.dropna(subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(s) < 20:
            continue
        rows.append({"구분": lab, "n": len(s), "종목수": s["ticker"].nunique(),
                     "MdAPE_앙상블": s["ape_Ensemble"].median(),
                     "MdAPE_naive_yoy": s["ape_naive_yoy"].median(),
                     "앙상블_승률_pct": (s["ape_Ensemble"] < s["ape_naive_yoy"]).mean() * 100})
    cmp = pd.DataFrame(rows)
    cmp.to_csv(D_TAB / "step23_eval_v2_contamination.csv", index=False, encoding="utf-8-sig")
    print(cmp.round(2).to_string(index=False))

    # ---------- 진짜 표본밖 기준 성능 ----------
    oos = d[d["is_oos"]]
    rows = []
    for h in sorted(oos["horizon"].dropna().unique()):
        s = oos[oos["horizon"] == h].dropna(subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(s) < 30:
            continue
        k = int((s["ape_Ensemble"] < s["ape_naive_yoy"]).sum())
        r = {"시계_분기": int(h), "n": len(s), "종목수": s["ticker"].nunique()}
        for c in MODELS + NAIVES:
            r["MdAPE_" + c] = s["ape_" + c].median()
        r["앙상블_승률_pct"] = k / len(s) * 100
        r["승률_p값"] = stats.binomtest(k, len(s), 0.5).pvalue
        rows.append(r)
    byh = pd.DataFrame(rows)
    byh.to_csv(D_TAB / "step24_eval_v2_by_horizon.csv", index=False, encoding="utf-8-sig")
    print("\n=== 진짜 표본밖 기준 : 예측시계별 MdAPE (%) ===")
    print(byh[["시계_분기", "n", "종목수", "MdAPE_Ensemble", "MdAPE_naive_yoy",
               "MdAPE_naive_seas", "앙상블_승률_pct", "승률_p값"]].round(3).to_string(index=False))

    # ---------- 성장구간별 ----------
    bins, labs = [-1e9, 0, 20, 50, 1e9], ["역성장(<0%)", "저성장(0~20%)", "중성장(20~50%)", "고성장(>50%)"]
    oos = oos.copy(); oos["성장구간"] = pd.cut(oos["yoy_at_fc"], bins=bins, labels=labs)
    rows = []
    for lab in labs:
        s = oos[(oos["성장구간"] == lab) & (oos["horizon"] <= 4)].dropna(
            subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(s) < 20:
            continue
        rows.append({"성장구간": lab, "n": len(s), "종목수": s["ticker"].nunique(),
                     "MdAPE_앙상블": s["ape_Ensemble"].median(),
                     "MdAPE_naive_yoy": s["ape_naive_yoy"].median(),
                     "앙상블_승률_pct": (s["ape_Ensemble"] < s["ape_naive_yoy"]).mean() * 100})
    byg = pd.DataFrame(rows)
    byg.to_csv(D_TAB / "step25_eval_v2_by_growth.csv", index=False, encoding="utf-8-sig")
    print("\n=== 진짜 표본밖 기준 : 성장구간별 (시계 1~4분기) ===")
    print(byg.round(2).to_string(index=False))

    # ---------- 종목별 ----------
    for tk, nm in (("A278470", "에이피알"), ("A003350", "한국화장품제조")):
        s = d[d["ticker"] == tk].sort_values(["fc_time", "target_q"])
        if s.empty:
            continue
        print("\n=== {} ({}) 단위:십억원 ===".format(nm, tk))
        o = pd.DataFrame({
            "기록시점": s["fc_time"].dt.strftime("%Y-%m-%d"),
            "대상": s["target_q"].astype(str),
            "시계": s["horizon"].astype("Int64"),
            "표본밖": np.where(s["is_oos"], "O", "X(사후)"),
            "실제": (s["actual"] / 1e6).round(1),
            "앙상블": (s["Ensemble"] / 1e6).round(1),
            "오차%": ((s["Ensemble"] - s["actual"]) / s["actual"] * 100).round(1),
            "naive오차%": ((s["naive_yoy"] - s["actual"]) / s["actual"] * 100).round(1),
        })
        print(o.to_string(index=False))
        v = s[s["is_oos"]].dropna(subset=["ape_Ensemble", "ape_naive_yoy"])
        if len(v):
            print("  진짜 표본밖 {}건 평균절대오차 : 앙상블 {:.1f}% vs naive_yoy {:.1f}%".format(
                len(v), v["ape_Ensemble"].mean(), v["ape_naive_yoy"].mean()))
    return d


if __name__ == "__main__":
    main()
