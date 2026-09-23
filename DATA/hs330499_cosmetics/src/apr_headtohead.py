# -*- coding: utf-8 -*-
"""
STEP 9. 에이피알 — 동일 표본 정면비교 (head-to-head)

문제: 구글트렌드 지표는 표본이 2021Q4부터라 앞선 검증들과 평가분기가 다르다.
      또 에이피알 매출 증가율 자체가 추세를 가지므로, 추세를 가진 변수는 무엇이든
      상관이 높게 나온다. 따라서 다음 조건을 모두 맞춰 비교한다.

  (1) 동일 표본기간 : 각 지표가 이용 가능한 공통 구간으로 제한
  (2) 동일 평가분기 : 확장창 표본밖 예측을 같은 분기에서 비교
  (3) 동일 모형크기 : y ~ 1 + y_AR1 + X  (설명변수 1개만)  -- 표본이 짧아 SEAS 제외
  (4) 위약 동시투입 : 순수 시간추세 / 반도체수출 / ASIN패널크기

  -> 구글트렌드가 위약보다 확실히 나으면 '진짜 신호', 비슷하면 '추세 효과'.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import D_DATA, D_TAB, D_FIG, log, setup_matplotlib
from backtest import ols_fit, ols_predict, dm_test
from deepdive import load_monthly, cumulative_quarter_features

plt = setup_matplotlib()

TICKER = "278470"
MIN_TRAIN = 8

CANDIDATES = {
    # --- 구글트렌드 ---
    "gt_brand_level": ("구글트렌드", "메디큐브 검색수준"),
    "gt_sos": ("구글트렌드", "검색 점유율(SoS)"),
    "gt_rel_peer": ("구글트렌드", "경쟁사 대비 상대검색"),
    "gt_category_heat": ("구글트렌드", "카테고리 열기"),
    "gt_brand_level_yoy": ("구글트렌드", "메디큐브 검색 YoY"),
    "gt_rel_peer_yoy": ("구글트렌드", "상대검색 YoY"),
    # --- 아마존 ---
    "az_gmv_index_all": ("아마존", "GMV지수(전체)"),
    "az_gmv_index_fixed": ("아마존", "GMV지수(고정)"),
    "az_review_velocity_yoy": ("아마존", "리뷰증가속도 YoY"),
    "coh_gmv_yoy": ("아마존(고정코호트)", "코호트 GMV YoY"),
    "coh_rev_velocity_yoy": ("아마존(고정코호트)", "코호트 리뷰속도 YoY"),
    # --- 산업 수출지표 (비교군) ---
    "HS330499_yoy": ("산업수출", "HS330499 수출총액 YoY"),
    # --- 위약 ---
    "PLB_trend": ("위약", "순수 시간추세"),
    "PLB_semi_yoy": ("위약", "반도체 수출 YoY"),
    "PLB_n_asin": ("위약", "ASIN 패널크기"),
}


def bt_single(panel: pd.DataFrame, x: pd.Series, target: str, min_train: int):
    """표본을 X 가용구간으로 제한한 뒤 확장창 표본밖 예측 (모형: 1 + y_AR1 + X)."""
    d = panel.copy()
    d["y"] = d[target]
    d["y_AR1"] = d[target].shift(1)
    d["xv"] = x.reindex(d.index)
    d = d.dropna(subset=["y", "y_AR1", "xv"]).sort_index()
    if len(d) < min_train + 4:
        return pd.DataFrame()
    recs = []
    for t in range(min_train, len(d)):
        r = {"date": d.index[t], "actual": d["y"].iloc[t], "NAIVE": d["y_AR1"].iloc[t]}
        for nm, cols in (("AR1", ["y_AR1"]), ("EXP", ["y_AR1", "xv"])):
            tr = d.iloc[:t][cols + ["y"]].dropna()
            if len(tr) < min_train:
                r[nm] = np.nan
                continue
            beta, _ = ols_fit(tr[cols].values, tr["y"].values)
            r[nm] = ols_predict(beta, d.iloc[t][cols].values.astype(float))
        recs.append(r)
    return pd.DataFrame(recs).set_index("date")


def sc(fc: pd.DataFrame) -> dict:
    d = fc[["actual", "NAIVE", "AR1", "EXP"]].dropna()
    if len(d) < 5:
        return {}
    o = {"n_eval": len(d), "평가기간": "{:%Y-%m}~{:%Y-%m}".format(d.index.min(), d.index.max())}
    for c in ("NAIVE", "AR1", "EXP"):
        e = (d[c] - d["actual"]).values
        o["MAE_" + c] = np.abs(e).mean()
    bb = "NAIVE" if o["MAE_NAIVE"] <= o["MAE_AR1"] else "AR1"
    o["최선기준"] = bb
    o["MAE_기준"] = o["MAE_" + bb]
    o["MAE_개선_pct"] = (1 - o["MAE_EXP"] / o["MAE_" + bb]) * 100
    _, p = dm_test((d[bb] - d["actual"]).values, (d["EXP"] - d["actual"]).values)
    o["DM_p"] = p
    return o


def main():
    panel = pd.read_csv(D_DATA / "company_{}_features.csv".format(TICKER),
                        parse_dates=["date"]).set_index("date")
    feats = pd.read_csv(D_DATA / "apr_alt_features.csv", parse_dates=["date"]).set_index("date")
    hs = cumulative_quarter_features(load_monthly("330499"))
    feats["HS330499_yoy"] = hs["cum3_usd_yoy"].reindex(feats.index)

    # ---------- (A) 동일표본 상관 비교 ----------
    rows = []
    for tgt, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
        y = panel[tgt]
        # 공통표본 = 구글트렌드(핵심 후보)가 있는 구간
        base_idx = feats["gt_brand_level"].dropna().index.intersection(y.dropna().index)
        for c, (grp, lab) in CANDIDATES.items():
            if c not in feats.columns:
                continue
            m = pd.concat([y, feats[c]], axis=1).loc[
                feats.index.intersection(base_idx)].dropna()
            m.columns = ["y", "x"]
            if len(m) < 8 or m["x"].std() == 0:
                continue
            r, p = stats.pearsonr(m["y"], m["x"])
            # 시간추세를 통제한 편상관
            tt = np.arange(len(m), dtype=float)
            ry = stats.linregress(tt, m["y"]).slope * tt
            rx = stats.linregress(tt, m["x"]).slope * tt
            pr, pp = stats.pearsonr(m["y"] - ry, m["x"] - rx)
            rows.append({"목표변수": tl, "구분": grp, "지표": lab, "col": c, "n": len(m),
                         "상관_r": round(r, 3), "p값": round(p, 4),
                         "추세통제_편상관_r": round(pr, 3), "편상관_p값": round(pp, 4),
                         "표본": "{:%Y-%m}~{:%Y-%m}".format(m.index.min(), m.index.max())})
    corr = pd.DataFrame(rows)
    corr.to_csv(D_TAB / "step14_apr_headtohead_corr.csv", index=False, encoding="utf-8-sig")

    # ---------- (B) 동일표본 표본밖 비교 ----------
    res = []
    for tgt, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
        for c, (grp, lab) in CANDIDATES.items():
            if c not in feats.columns:
                continue
            fc = bt_single(panel, feats[c], tgt, MIN_TRAIN)
            if fc.empty:
                continue
            s = sc(fc)
            if not s:
                continue
            s.update({"목표변수": tl, "구분": grp, "지표": lab, "col": c})
            res.append(s)
    oos = pd.DataFrame(res)
    oos.to_csv(D_TAB / "step15_apr_headtohead_oos.csv", index=False, encoding="utf-8-sig")

    # ---------- 출력 ----------
    for tl in ("매출액 YoY(%)", "영업이익률(%)"):
        print("\n" + "=" * 112)
        print("■ {} — 동일표본 상관 (추세통제 편상관 포함)".format(tl))
        s = corr[corr["목표변수"] == tl].copy()
        s["k"] = s["구분"].map({"구글트렌드": 0, "아마존": 1, "아마존(고정코호트)": 2,
                              "산업수출": 3, "위약": 4})
        print(s.sort_values(["k", "상관_r"], ascending=[True, False])[
            ["구분", "지표", "n", "상관_r", "p값", "추세통제_편상관_r", "편상관_p값", "표본"]
        ].to_string(index=False))

        print("\n■ {} — 동일모형(1+AR1+X) 표본밖 성능".format(tl))
        t = oos[oos["목표변수"] == tl].sort_values("MAE_개선_pct", ascending=False)
        if t.empty:
            print("  (표본부족)")
            continue
        print(t[["구분", "지표", "n_eval", "평가기간", "최선기준", "MAE_기준", "MAE_EXP",
                 "MAE_개선_pct", "DM_p"]].round(3).to_string(index=False))

    # ---------- 그래프 ----------
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.5))
    for ax, tl in zip(axes, ("매출액 YoY(%)", "영업이익률(%)")):
        t = oos[oos["목표변수"] == tl].sort_values("MAE_개선_pct")
        if t.empty:
            continue
        cmap = {"구글트렌드": "#4c72b0", "아마존": "#dd8452",
                "아마존(고정코호트)": "#937860", "산업수출": "#55a868", "위약": "#c44e52"}
        ax.barh(t["지표"], t["MAE_개선_pct"], color=[cmap[g] for g in t["구분"]])
        ax.axvline(0, color="k", lw=1.2)
        ax.set_xlabel("MAE 개선율 vs 기준모형 (%)")
        ax.set_title("에이피알 — {}".format(tl))
        ax.tick_params(axis="y", labelsize=8)
    import matplotlib.patches as mp
    fig.legend(handles=[mp.Patch(color=v, label=k) for k, v in
                        {"구글트렌드": "#4c72b0", "아마존": "#dd8452",
                         "아마존(고정코호트)": "#937860", "산업수출": "#55a868",
                         "위약": "#c44e52"}.items()],
               loc="lower center", ncol=5, fontsize=9)
    fig.suptitle("동일 표본·동일 모형 정면비교 (빨강=위약: 이보다 확실히 나아야 진짜 신호)", y=1.02)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(D_FIG / "fig9_apr_headtohead.png")
    plt.close(fig)
    log("H2H", "상관 {}행 / 표본밖 {}행".format(len(corr), len(oos)))
    return corr, oos


if __name__ == "__main__":
    main()
