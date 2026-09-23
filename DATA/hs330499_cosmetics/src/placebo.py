# -*- coding: utf-8 -*-
"""
STEP 7. 위약(placebo) 검정

문제의식:
  에이피알에서 '분기 수출총액 로그수준'을 넣으면 매출액 YoY 예측오차가 25~30% 줄고
  예측결합 회귀의 λ도 p<0.01 로 유의하게 나온다. 그런데 **샴푸(330510),
  피부세정제(340130), 면도·목욕용(330790)** 같이 에이피알 매출과 직접 관련이 없는
  품목에서도 똑같이 유의하게 나온다.

  -> 이것이 '화장품 수출 정보' 때문인지, 아니면 단순히 **두 계열이 함께 우상향하는
     추세(trend) 효과**인지 판별해야 한다.

방법:
  화장품과 전혀 무관한 수출계열(반도체 854231/854232/854239, 석유제품 271012,
  승용차 870323)과 **순수 선형 시간추세**를 같은 자리에 넣고 동일 프로토콜로 돌린다.
  위약 변수에서도 같은 크기의 '개선'이 나오면, 그 개선은 수출정보가 아니라 추세다.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from common import q, D_DATA, D_TAB, D_FIG, COMPANIES, log, setup_matplotlib
from backtest import MIN_TRAIN
from deepdive import load_monthly, cumulative_quarter_features, backtest_variable, score

plt = setup_matplotlib()

PLACEBO_HS = {
    "854231": "반도체 - 메모리",
    "854232": "반도체 - 기타 메모리",
    "854239": "반도체 - 기타 집적회로",
    "271012": "석유제품(경질유)",
    "870323": "승용차(1500~3000cc)",
}
REAL_HS = {
    "330499": "화장품 기타 (주 분석대상)",
    "330510": "샴푸",
    "340130": "피부세정제",
}


def main():
    panels = {c: pd.read_csv(D_DATA / "company_{}_features.csv".format(c),
                             parse_dates=["date"]).set_index("date") for c in COMPANIES}

    series = {}
    for hs, nm in {**REAL_HS, **PLACEBO_HS}.items():
        m = load_monthly(hs)
        if not m.empty:
            series[hs] = (nm, cumulative_quarter_features(m))

    rows = []
    for code, p in panels.items():
        # 순수 선형 시간추세 (위약의 극단)
        trend = pd.Series(np.arange(len(p), dtype=float), index=p.index, name="trend")
        for target, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
            fc = backtest_variable(p, trend, target, MIN_TRAIN[code])
            if not fc.empty:
                s = score(fc)
                if s:
                    s.update({"ticker": code, "기업명": COMPANIES[code]["name"],
                              "목표변수": tl, "구분": "위약(순수 시간추세)",
                              "HS": "TREND", "HS설명": "선형 시간추세", "지표": "수준"})
                    rows.append(s)
            for hs, (nm, cf) in series.items():
                grp = "실제(화장품)" if hs in REAL_HS else "위약(무관품목)"
                for vv, vl in (("cum3_log_usd", "분기 수출총액 로그수준"),
                               ("cum3_usd_yoy", "분기 수출총액 YoY")):
                    fc = backtest_variable(p, cf[vv], target, MIN_TRAIN[code])
                    if fc.empty:
                        continue
                    s = score(fc)
                    if not s:
                        continue
                    s.update({"ticker": code, "기업명": COMPANIES[code]["name"],
                              "목표변수": tl, "구분": grp, "HS": hs,
                              "HS설명": nm, "지표": vl})
                    rows.append(s)

    df = pd.DataFrame(rows)
    df.to_csv(D_TAB / "step9_placebo.csv", index=False, encoding="utf-8-sig")

    # ---- 출력 ----
    for code in COMPANIES:
        for tl in ("매출액 YoY(%)", "영업이익률(%)"):
            s = df[(df["ticker"] == code) & (df["목표변수"] == tl)]
            if s.empty:
                continue
            print("\n" + "=" * 100)
            print("■ {} / {}  (기준모형 {}, MAE {:.3f})".format(
                COMPANIES[code]["name"], tl, s["최선기준"].iloc[0],
                s["MAE_" + s["최선기준"].iloc[0]].iloc[0]))
            for vl in ("분기 수출총액 로그수준", "분기 수출총액 YoY", "수준"):
                t = s[s["지표"] == vl]
                if t.empty:
                    continue
                print("\n  [{}]".format(vl))
                print(t.sort_values("MAE_개선_vs_최선기준_pct", ascending=False)[
                    ["구분", "HS", "HS설명", "n_eval", "MAE_EXP",
                     "MAE_개선_vs_최선기준_pct", "encomp_lambda", "encomp_p"]
                ].round(3).to_string(index=False))

    # ---- 그래프 ----
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    for ax, code in zip(axes, COMPANIES):
        s = df[(df["ticker"] == code) & (df["목표변수"] == "매출액 YoY(%)") &
               (df["지표"].isin(["분기 수출총액 로그수준", "수준"]))].copy()
        if s.empty:
            continue
        s["lab"] = s["HS설명"]
        s = s.sort_values("MAE_개선_vs_최선기준_pct")
        col = np.where(s["구분"].str.startswith("실제"), "#4c72b0", "#c44e52")
        ax.barh(s["lab"], s["MAE_개선_vs_최선기준_pct"], color=col)
        ax.axvline(0, color="k", lw=1.2)
        ax.set_xlabel("MAE 개선율 vs 기준모형 (%)")
        ax.set_title("{} — 매출액 YoY / 수출총액 '수준' 투입".format(COMPANIES[code]["name"]))
        ax.tick_params(axis="y", labelsize=8)
    fig.suptitle("위약검정: 파랑=화장품(실제), 빨강=무관품목·시간추세(위약)\n"
                 "위약에서도 비슷하게 개선되면 그 개선은 '수출정보'가 아니라 '추세'다", y=1.06)
    fig.tight_layout()
    fig.savefig(D_FIG / "fig8_placebo.png")
    plt.close(fig)
    log("PLACEBO", "{}행 저장".format(len(df)))
    return df


if __name__ == "__main__":
    main()
