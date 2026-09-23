# -*- coding: utf-8 -*-
"""
STEP 10. 에이피알 — 마지막 두 가지 점검

(A) 방향성 정확도 : 수준 예측이 아니라 '매출 증가율이 전분기 대비 오를지/내릴지'를
                    맞히는가. 실무에서는 방향만 맞아도 쓸모가 있다.
(B) 부문별 매출   : 에이피알 연결매출 증가는 사실상 '수출(해외)'이 만든다.
                    미국 중심 지표(구글트렌드/아마존)가 겨냥해야 할 목표는
                    연결 전체가 아니라 뷰티 수출 매출이다. 겹치는 구간에서 확인.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import D_DATA, D_TAB, D_FIG, log, setup_matplotlib

plt = setup_matplotlib()
TICKER = "278470"

DIR_VARS = {
    "gt_brand_level": "메디큐브 검색수준",
    "gt_rel_peer": "경쟁사 대비 상대검색",
    "gt_rel_peer_yoy": "상대검색 YoY",
    "gt_sos": "검색 점유율(SoS)",
    "coh_rev_velocity_yoy": "코호트 리뷰속도 YoY",
    "az_gmv_index_fixed": "아마존 GMV지수(고정)",
    "HS330499_yoy": "HS330499 수출 YoY",
    "PLB_trend": "[위약] 순수 시간추세",
    "PLB_n_asin": "[위약] ASIN 패널크기",
}


def main():
    panel = pd.read_csv(D_DATA / "company_{}_features.csv".format(TICKER),
                        parse_dates=["date"]).set_index("date")
    feats = pd.read_csv(D_DATA / "apr_alt_features.csv", parse_dates=["date"]).set_index("date")
    from deepdive import load_monthly, cumulative_quarter_features
    hs = cumulative_quarter_features(load_monthly("330499"))
    feats["HS330499_yoy"] = hs["cum3_usd_yoy"].reindex(feats.index)

    # ---------- (A) 방향성 ----------
    y = panel["rev_yoy"]
    dy = np.sign(y.diff())                      # 매출 증가율이 전분기 대비 상승(+1)/하락(-1)
    rows = []
    for c, lab in DIR_VARS.items():
        if c not in feats.columns:
            continue
        for L in (0, 1):
            dx = np.sign(feats[c].diff()).shift(L)
            m = pd.concat([dy, dx], axis=1).dropna()
            m.columns = ["dy", "dx"]
            m = m[(m["dy"] != 0) & (m["dx"] != 0)]
            if len(m) < 8:
                continue
            hit = (m["dy"] == m["dx"]).mean()
            # 이항검정 (귀무: 0.5)
            k = int((m["dy"] == m["dx"]).sum())
            pv = stats.binomtest(k, len(m), 0.5).pvalue
            rows.append({"지표": lab, "col": c, "시차": L, "n": len(m),
                         "방향적중률": round(hit, 3), "적중수": k, "이항검정_p": round(pv, 4),
                         "표본": "{:%Y-%m}~{:%Y-%m}".format(m.index.min(), m.index.max())})
    dirs = pd.DataFrame(rows).sort_values("방향적중률", ascending=False)
    dirs.to_csv(D_TAB / "step16_apr_direction.csv", index=False, encoding="utf-8-sig")

    print("■ 매출액 YoY 방향(전분기 대비 상승/하락) 적중률")
    print(dirs[["지표", "시차", "n", "방향적중률", "적중수", "이항검정_p", "표본"]].to_string(index=False))

    # ---------- (B) 부문별 ----------
    segp = D_DATA / "apr_segment_revenue.csv"
    if segp.exists():
        seg = pd.read_csv(segp, parse_dates=["date"]).set_index("date")
        print("\n■ 에이피알 뷰티부문 내수 vs 수출 (십억원, DART 매출실적)")
        print(seg[["beauty_domestic", "beauty_export", "beauty_total",
                   "beauty_domestic_yoy", "beauty_export_yoy"]].round(1).to_string())

        both = seg.join(panel[["revenue_bn", "rev_yoy"]], how="left")
        both["수출비중_pct"] = both["beauty_export"] / both["beauty_total"] * 100
        print("\n■ 수출 비중 추이")
        print(both[["beauty_total", "수출비중_pct", "revenue_bn"]].round(1).to_string())

        # 미국 지표 vs 뷰티수출 YoY (표본 매우 짧음 -> 기술통계)
        rows = []
        for c, lab in DIR_VARS.items():
            if c not in feats.columns:
                continue
            m = pd.concat([seg["beauty_export_yoy"], feats[c]], axis=1).dropna()
            m.columns = ["y", "x"]
            if len(m) < 4:
                continue
            r, p = stats.pearsonr(m["y"], m["x"])
            rows.append({"지표": lab, "n": len(m), "상관_r": round(r, 3), "p값": round(p, 4)})
        ex = pd.DataFrame(rows).sort_values("상관_r", ascending=False)
        ex.to_csv(D_TAB / "step17_apr_export_seg_corr.csv", index=False, encoding="utf-8-sig")
        print("\n■ 뷰티 '수출' 매출 YoY 와의 상관 (표본 {}분기 — 기술통계일 뿐, 검정 불가)".format(
            int(ex["n"].max()) if not ex.empty else 0))
        print(ex.to_string(index=False))

        # 그래프
        fig, ax = plt.subplots(1, 2, figsize=(14, 5))
        a = ax[0]
        a.bar(seg.index, seg["beauty_domestic"], width=70, label="내수", color="#4c72b0")
        a.bar(seg.index, seg["beauty_export"], width=70, bottom=seg["beauty_domestic"],
              label="수출", color="#c44e52")
        a.set_ylabel("십억원")
        a.set_title("에이피알 뷰티부문 매출 구성 (내수는 정체, 수출이 성장 전부)")
        a.legend()
        a2 = ax[1]
        # 구글 0-floor 검열 구간(2023-06 이전) 제외 후 정규화
        g = feats[["gt_brand_level", "gt_rel_peer"]].dropna().loc["2023-07-01":]
        for col, lab in (("gt_brand_level", "메디큐브 검색수준"),
                         ("gt_rel_peer", "경쟁사 대비 상대검색")):
            base = g[col].replace(0, np.nan).dropna()
            if len(base):
                a2.plot(g.index, g[col] / base.iloc[0], lw=1.8, label=lab + "(정규화)")
        yy = panel["rev_yoy"].reindex(g.index)
        a3 = a2.twinx()
        a3.plot(g.index, yy, "k--o", ms=3, lw=1.5, label="매출액 YoY(우)")
        a3.grid(False); a3.set_ylabel("%")
        a2.set_title("구글트렌드 지표 vs 매출액 YoY (0-floor 검열구간 제외)")
        h1, l1 = a2.get_legend_handles_labels(); h3, l3 = a3.get_legend_handles_labels()
        a2.legend(h1 + h3, l1 + l3, fontsize=8, loc="upper left")
        fig.tight_layout()
        fig.savefig(D_FIG / "fig10_apr_segment_and_trends.png")
        plt.close(fig)

    log("FINAL-CHK", "완료")
    return dirs


if __name__ == "__main__":
    main()
