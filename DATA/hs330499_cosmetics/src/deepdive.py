# -*- coding: utf-8 -*-
"""
STEP 6. 심화검증
  Q1. 수출자료는 월별로 먼저 나온다. 분기 안에서 월이 쌓일수록 당분기 매출 예측오차가 줄어드는가?
      -> '정보 누적 곡선' : 당분기 수출 0/1/2/3개월 이용 시 MAE 비교 (동일 평가분기)
  Q2. HS330499 말고 도움이 되는 수출지표가 정말 없는가?
      -> DB에 있는 화장품·미용기기 관련 HS 전 계열을 동일 프로토콜로 비교

추가 검정: 예측결합(forecast encompassing) 회귀
      (y - f_base) = a + λ (f_exp - f_base) + u   ->  λ>0 이면 수출예측이 추가정보를 가짐
      표본이 짧을 때 DM보다 민감하다.

시점 가정 (모두 분기실적 공시 전 = 분기종료 후 45일 이전)
  k=1 : 당분기 m1 수출 공표  = 분기 45일째
  k=2 : 당분기 m2 수출 공표  = 분기 75일째  (분기 종료 이전!)
  k=3 : 당분기 m3 수출 공표  = 분기 종료 +15일
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import q, D_DATA, D_TAB, D_FIG, COMPANIES, log, yoy, setup_matplotlib
from backtest import ols_fit, ols_predict, dm_test, MIN_TRAIN

plt = setup_matplotlib()

# DB에서 확인된 화장품/미용기기 관련 HS 계열
HS_CANDIDATES = {
    "330499": "기초·색조 화장품 기타(주 분석대상)",
    "330410": "입술화장용 제품류",
    "330420": "눈화장용 제품류",
    "330510": "샴푸",
    "330590": "두발용 기타",
    "330790": "면도·목욕 등 기타",
    "340130": "피부세정제(클렌저)",
    "854370": "미용기기 - 그 밖의 기기",
    "854390": "미용기기 - 부분품",
    "3304991000": "330499 세분류 A",
    "3304992000": "330499 세분류 B",
    "3304999000": "330499 세분류 D",
}
HS_COMBO = {
    "3304_SUM": ["330410", "330420", "330499"],          # 3304 전체 합성
    "COSMETIC_ALL": ["330410", "330420", "330499", "330510", "330590", "330790", "340130"],
    "DEVICE_SUM": ["854370", "854390"],                   # 미용기기 합성
}


# ---------------------------------------------------------------- 수출 계열
def load_monthly(hs: str) -> pd.DataFrame:
    d = q("SELECT date, indicator, value FROM korea_monthly_trade_data "
          "WHERE root_hs_code=:c AND indicator IN ('expDlr','expWgt')", c=hs)
    if d.empty:
        return pd.DataFrame()
    d["date"] = pd.to_datetime(d["date"])
    w = d.pivot_table(index="date", columns="indicator", values="value", aggfunc="first").sort_index()
    return w.rename(columns={"expDlr": "usd", "expWgt": "kg"})


def cumulative_quarter_features(m: pd.DataFrame) -> pd.DataFrame:
    """당분기 k개월 누적 수출과 그 전년동기(동일 k개월) 대비 증가율."""
    x = m.copy()
    x["qp"] = pd.PeriodIndex(x.index, freq="Q")
    x["k"] = (x.index.month - 1) % 3 + 1          # 분기 내 1,2,3번째 달
    out = {}
    for k in (1, 2, 3):
        sub = x[x["k"] <= k].groupby("qp")[["usd", "kg"]].sum(min_count=k)
        sub.index = sub.index.to_timestamp(how="end").normalize()
        out["cum{}_usd".format(k)] = sub["usd"]
        out["cum{}_kg".format(k)] = sub["kg"]
    df = pd.DataFrame(out)
    for k in (1, 2, 3):
        df["cum{}_usd_yoy".format(k)] = yoy(df["cum{}_usd".format(k)], 4)
        df["cum{}_kg_yoy".format(k)] = yoy(df["cum{}_kg".format(k)], 4)
        df["cum{}_log_usd".format(k)] = np.log(df["cum{}_usd".format(k)])
    return df


# ---------------------------------------------------------------- 백테스트 핵심
def backtest_variable(panel: pd.DataFrame, xvar: pd.Series, target: str,
                      min_train: int, earn_lag: int = 1) -> pd.DataFrame:
    """BASE_AR(+수출변수) 확장창 표본밖 예측. 동일 인덱스에서 baseline도 함께 산출."""
    d = panel.copy()
    d["y"] = d[target]
    d["y_AR1"] = d[target].shift(earn_lag)
    d["y_SEAS"] = d[target].shift(4)
    d["xv"] = xvar.reindex(d.index)
    d = d.dropna(subset=["y", "y_AR1", "y_SEAS"]).sort_index()
    if len(d) < min_train + 3:
        return pd.DataFrame()

    recs = []
    for t in range(min_train, len(d)):
        dt = d.index[t]
        r = {"date": dt, "actual": d["y"].iloc[t], "NAIVE": d["y_AR1"].iloc[t]}
        for name, cols in (("BASE_AR", ["y_AR1", "y_SEAS"]),
                           ("EXP", ["y_AR1", "y_SEAS", "xv"])):
            tr = d.iloc[:t][cols + ["y"]].dropna()
            te = d.iloc[t][cols]
            if len(tr) < max(min_train, len(cols) * 4) or te.isna().any():
                r[name] = np.nan
                continue
            beta, _ = ols_fit(tr[cols].values, tr["y"].values)
            r[name] = ols_predict(beta, te.values.astype(float))
        recs.append(r)
    return pd.DataFrame(recs).set_index("date")


def encompassing(fc: pd.DataFrame):
    """(y - f_base) = a + λ(f_exp - f_base) + u  ;  λ>0 이면 수출예측에 추가정보."""
    d = fc[["actual", "BASE_AR", "EXP"]].dropna()
    if len(d) < 8:
        return np.nan, np.nan, 0
    yv = (d["actual"] - d["BASE_AR"]).values
    xv = (d["EXP"] - d["BASE_AR"]).values
    if np.allclose(xv, 0):
        return np.nan, np.nan, len(d)
    X = np.column_stack([np.ones(len(xv)), xv])
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    n, k = len(yv), 2
    s2 = resid @ resid / (n - k)
    XtXi = np.linalg.pinv(X.T @ X)
    se = np.sqrt(s2 * XtXi[1, 1])
    lam = beta[1]
    tstat = lam / se if se > 0 else np.nan
    pv = 2 * (1 - stats.t.cdf(abs(tstat), df=n - k)) if np.isfinite(tstat) else np.nan
    return lam, pv, len(d)


def score(fc: pd.DataFrame) -> dict:
    d = fc[["actual", "NAIVE", "BASE_AR", "EXP"]].dropna()
    if d.empty:
        return {}
    out = {"n_eval": len(d),
           "기간": "{:%Y-%m}~{:%Y-%m}".format(d.index.min(), d.index.max())}
    for c in ("NAIVE", "BASE_AR", "EXP"):
        e = (d[c] - d["actual"]).values
        out["MAE_" + c] = np.abs(e).mean()
        out["RMSE_" + c] = np.sqrt((e ** 2).mean())
    bb = "NAIVE" if out["MAE_NAIVE"] < out["MAE_BASE_AR"] else "BASE_AR"
    out["최선기준"] = bb
    out["MAE_개선_vs_최선기준_pct"] = (1 - out["MAE_EXP"] / out["MAE_" + bb]) * 100
    out["RMSE_개선_vs_최선기준_pct"] = (1 - out["RMSE_EXP"] / out["RMSE_" + bb]) * 100
    _, p = dm_test((d[bb] - d["actual"]).values, (d["EXP"] - d["actual"]).values)
    out["DM_p"] = p
    lam, plam, _ = encompassing(d)
    out["encomp_lambda"] = lam
    out["encomp_p"] = plam
    return out


# ---------------------------------------------------------------- Q1 정보누적
def q1_accumulation(panels: dict) -> pd.DataFrame:
    m = load_monthly("330499")
    cf = cumulative_quarter_features(m)
    rows = []
    for code, p in panels.items():
        for target, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
            for k in (1, 2, 3):
                for vv, vl in (("cum{}_usd_yoy".format(k), "수출총액 누적{}개월 YoY".format(k)),
                               ("cum{}_kg_yoy".format(k), "수출중량 누적{}개월 YoY".format(k))):
                    fc = backtest_variable(p, cf[vv], target, MIN_TRAIN[code])
                    if fc.empty:
                        continue
                    s = score(fc)
                    if not s:
                        continue
                    s.update({"ticker": code, "기업명": COMPANIES[code]["name"],
                              "목표변수": tl, "누적개월": k, "지표": vl})
                    rows.append(s)
    df = pd.DataFrame(rows)
    df.to_csv(D_TAB / "step7_monthly_accumulation.csv", index=False, encoding="utf-8-sig")
    return df


# ---------------------------------------------------------------- Q2 대안지표
def q2_alternatives(panels: dict) -> pd.DataFrame:
    series = {}
    for hs, nm in HS_CANDIDATES.items():
        m = load_monthly(hs)
        if m.empty:
            continue
        series[(hs, nm)] = cumulative_quarter_features(m)
    for nm, parts in HS_COMBO.items():
        acc = None
        for hs in parts:
            mm = load_monthly(hs)
            if mm.empty:
                continue
            acc = mm if acc is None else acc.add(mm, fill_value=np.nan)
        if acc is not None:
            series[(nm, "합성: " + "+".join(parts))] = cumulative_quarter_features(acc)

    rows = []
    for code, p in panels.items():
        for target, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
            for (hs, nm), cf in series.items():
                for vv, vl in (("cum3_usd_yoy", "분기 수출총액 YoY"),
                               ("cum3_kg_yoy", "분기 수출중량 YoY"),
                               ("cum3_log_usd", "분기 수출총액 로그수준")):
                    fc = backtest_variable(p, cf[vv], target, MIN_TRAIN[code])
                    if fc.empty:
                        continue
                    s = score(fc)
                    if not s:
                        continue
                    s.update({"ticker": code, "기업명": COMPANIES[code]["name"],
                              "목표변수": tl, "HS": hs, "HS설명": nm, "지표": vl})
                    rows.append(s)
    df = pd.DataFrame(rows)
    df.to_csv(D_TAB / "step8_alternative_hs.csv", index=False, encoding="utf-8-sig")
    return df


# ---------------------------------------------------------------- 그래프
def fig_accum(df: pd.DataFrame):
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    for i, code in enumerate(COMPANIES):
        for j, tl in enumerate(["매출액 YoY(%)", "영업이익률(%)"]):
            ax = axes[i][j]
            s = df[(df["ticker"] == code) & (df["목표변수"] == tl)]
            if s.empty:
                continue
            base = s["MAE_" + s["최선기준"].iloc[0]].iloc[0]
            s = s.copy()
            s["종류"] = np.where(s["지표"].str.contains("총액"), "수출총액 YoY", "수출중량 YoY")
            for lbl, g in s.groupby("종류"):
                g = g.sort_values("누적개월")
                ax.plot(g["누적개월"], g["MAE_EXP"], "-o", lw=1.8, ms=7, label=lbl)
            ax.axhline(base, color="k", ls="--", lw=1.4,
                       label="기준모형({})".format(s["최선기준"].iloc[0]))
            ax.set_xticks([1, 2, 3])
            ax.set_xlabel("당분기 이용가능 수출 개월수")
            ax.set_ylabel("표본밖 MAE")
            ax.set_title("{} / {}".format(COMPANIES[code]["name"], tl), fontsize=10)
            ax.legend(fontsize=8)
    fig.suptitle("당분기 수출자료가 쌓일수록 예측오차가 줄어드는가 (HS330499)", y=1.01)
    fig.tight_layout()
    fig.savefig(D_FIG / "fig6_monthly_accumulation.png")
    plt.close(fig)


def fig_alt(df: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    for ax, code in zip(axes, COMPANIES):
        s = df[(df["ticker"] == code) & (df["목표변수"] == "매출액 YoY(%)")].copy()
        if s.empty:
            continue
        s["lab"] = s["HS"] + " " + s["지표"].str.replace("분기 수출", "")
        s = s.sort_values("MAE_개선_vs_최선기준_pct")
        colors = np.where(s["MAE_개선_vs_최선기준_pct"] > 0, "#55a868", "#c44e52")
        ax.barh(s["lab"], s["MAE_개선_vs_최선기준_pct"], color=colors)
        ax.axvline(0, color="k", lw=1.2)
        ax.set_xlabel("MAE 개선율 vs 최선 무수출 기준모형 (%)")
        ax.set_title("{} — 매출액 YoY".format(COMPANIES[code]["name"]), fontsize=11)
        ax.tick_params(axis="y", labelsize=7)
    fig.suptitle("대안 수출지표 전수 비교 (다중비교 보정 없음 — 상위값은 과대평가)", y=1.01)
    fig.tight_layout()
    fig.savefig(D_FIG / "fig7_alternative_hs.png")
    plt.close(fig)


def main():
    panels = {c: pd.read_csv(D_DATA / "company_{}_features.csv".format(c),
                             parse_dates=["date"]).set_index("date") for c in COMPANIES}

    log("DEEP", "Q1 월별 정보누적 곡선")
    acc = q1_accumulation(panels)
    fig_accum(acc)
    for code in COMPANIES:
        print("\n=== {} : 당분기 수출 누적개월별 표본밖 MAE ===".format(COMPANIES[code]["name"]))
        s = acc[acc["ticker"] == code]
        for tl, g in s.groupby("목표변수"):
            bb = g["최선기준"].iloc[0]
            print(" [{}] 기준모형({}) MAE={:.3f}, 평가 {}분기".format(
                tl, bb, g["MAE_" + bb].iloc[0], g["n_eval"].iloc[0]))
            print(g.sort_values(["지표", "누적개월"])[
                ["지표", "누적개월", "MAE_EXP", "MAE_개선_vs_최선기준_pct",
                 "DM_p", "encomp_lambda", "encomp_p"]].round(3).to_string(index=False))

    log("DEEP", "Q2 대안 수출지표 전수비교")
    alt = q2_alternatives(panels)
    fig_alt(alt)
    for code in COMPANIES:
        print("\n=== {} : 대안지표 상위 10 (매출액 YoY) ===".format(COMPANIES[code]["name"]))
        s = alt[(alt["ticker"] == code) & (alt["목표변수"] == "매출액 YoY(%)")]
        print(s.sort_values("MAE_개선_vs_최선기준_pct", ascending=False).head(10)[
            ["HS", "HS설명", "지표", "n_eval", "MAE_EXP", "MAE_개선_vs_최선기준_pct",
             "DM_p", "encomp_lambda", "encomp_p"]].round(3).to_string(index=False))
        print("\n=== {} : 대안지표 상위 6 (영업이익률) ===".format(COMPANIES[code]["name"]))
        s2 = alt[(alt["ticker"] == code) & (alt["목표변수"] == "영업이익률(%)")]
        print(s2.sort_values("MAE_개선_vs_최선기준_pct", ascending=False).head(6)[
            ["HS", "HS설명", "지표", "n_eval", "MAE_EXP", "MAE_개선_vs_최선기준_pct",
             "DM_p", "encomp_p"]].round(3).to_string(index=False))
    return acc, alt


if __name__ == "__main__":
    main()
