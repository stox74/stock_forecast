# -*- coding: utf-8 -*-
"""
STEP 3-B. 표본 밖(out-of-sample) 예측력 검증

방식: 시간순서를 유지하는 확장창(expanding window) / rolling-origin
  - 원점 t 에서 [0, t) 자료만으로 계수 추정 -> t 시점 예측
  - 변수 선택(EXP_AUTO)도 훈련표본 BIC로만 수행 (미래자료 유입 차단)
  - 모든 모형을 '동일한 평가분기' 위에서 비교 (교집합)

모형
  NAIVE    : y_hat = 직전 공시 분기값 (정보집합별 earn_lag 반영)
  AR1      : y ~ 1 + y_AR1
  BASE_AR  : y ~ 1 + y_AR1 + y_SEAS            <- 기준모형(baseline)
  EXP_<v>  : y ~ 1 + y_AR1 + y_SEAS + <수출변수 1개>
  EXP_AUTO : 위 중 훈련표본 BIC 최소 수출변수 1개를 매 원점마다 선택

목표변수
  rev_yoy : 매출액 전년동기대비 증가율(%)
  opm     : 영업이익률(%)  <- 영업이익이 0/음수인 분기가 있어 op_yoy 대신 사용
파생지표
  매출액 수준(십억원), 영업이익 수준(십억원) 오차도 함께 산출
"""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from scipy import stats
from common import D_DATA, D_TAB, COMPANIES, log
from features import SCENARIOS

TARGETS = {"rev_yoy": "매출액 YoY(%)", "opm": "영업이익률(%)"}

# 표본이 짧으므로 훈련 최소길이를 기업별로 다르게 (문서화 대상)
MIN_TRAIN = {"003350": 20, "278470": 14}


# ---------------------------------------------------------------- OLS
def ols_fit(X: np.ndarray, y: np.ndarray):
    Xc = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta
    return beta, resid


def ols_predict(beta: np.ndarray, x: np.ndarray) -> float:
    return float(np.concatenate([[1.0], x]) @ beta)


def bic(resid: np.ndarray, k: int) -> float:
    n = len(resid)
    sse = float(resid @ resid)
    if sse <= 0 or n <= k + 1:
        return np.inf
    return n * np.log(sse / n) + (k + 1) * np.log(n)


# ---------------------------------------------------------------- 백테스트
def run_one(df: pd.DataFrame, target: str, ar_cols: list, exp_cols: list,
            min_train: int) -> pd.DataFrame:
    """단일 (기업, 정보집합, 목표변수) 조합의 확장창 표본밖 예측."""
    need = [target] + ar_cols
    d = df.dropna(subset=need).copy()
    d = d.sort_index()
    if len(d) < min_train + 3:
        return pd.DataFrame()

    specs = {"AR1": [ar_cols[0]], "BASE_AR": ar_cols}
    for c in exp_cols:
        specs["EXP_" + c] = ar_cols + [c]          # 기준모형(AR1+SEAS) + 수출 1개
    for c in exp_cols:
        specs["EXPs_" + c] = [ar_cols[0], c]       # 간결형: AR1 + 수출 1개

    recs = []
    y_all = d[target].values
    for t in range(min_train, len(d)):
        dt = d.index[t]
        actual = y_all[t]
        row = {"date": dt, "actual": actual}

        # NAIVE : 직전 공시값
        row["NAIVE"] = d[ar_cols[0]].iloc[t]

        auto_pool = {}
        for mname, cols in specs.items():
            tr = d.iloc[:t][cols + [target]].dropna()
            te = d.iloc[t][cols]
            if len(tr) < max(min_train, len(cols) * 4) or te.isna().any():
                row[mname] = np.nan
                continue
            beta, resid = ols_fit(tr[cols].values, tr[target].values)
            row[mname] = ols_predict(beta, te.values.astype(float))
            if mname.startswith("EXP_") or mname.startswith("EXPs_"):
                auto_pool[mname] = (bic(resid, len(cols)), row[mname])

        # EXP_AUTO : 훈련표본 BIC 최소 (미래정보 미사용)
        if auto_pool:
            best = min(auto_pool.items(), key=lambda kv: kv[1][0])
            row["EXP_AUTO"] = best[1][1]
            row["EXP_AUTO_pick"] = best[0]
        else:
            row["EXP_AUTO"] = np.nan
            row["EXP_AUTO_pick"] = None

        # 수준 환산용 보조값
        row["rev_L4"] = df["revenue_bn"].shift(4).get(dt, np.nan)
        row["rev_actual"] = df["revenue_bn"].get(dt, np.nan)
        row["op_actual"] = df["op_bn"].get(dt, np.nan)
        recs.append(row)

    return pd.DataFrame(recs).set_index("date")


def dm_test(e1: np.ndarray, e2: np.ndarray, h: int = 1):
    """Diebold-Mariano (제곱오차, HLN 소표본 보정). 표본이 짧아 참고용."""
    dd = e1 ** 2 - e2 ** 2
    n = len(dd)
    if n < 6 or np.allclose(dd, 0):
        return np.nan, np.nan
    dbar = dd.mean()
    v = dd.var(ddof=1) / n
    if v <= 0:
        return np.nan, np.nan
    stat = dbar / np.sqrt(v)
    corr = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat *= corr
    p = 2 * (1 - stats.t.cdf(abs(stat), df=n - 1))
    return stat, p


BASELINES = ("NAIVE", "AR1", "BASE_AR")


def evaluate(fc: pd.DataFrame, target: str) -> pd.DataFrame:
    """동일 평가분기 위에서 모형별 MAE/RMSE 비교.

    기준모형 2가지로 개선율을 함께 제시한다.
      (1) BASE_AR       : 사전에 지정한 기준모형
      (2) 최선기준모형   : 수출 미사용 기준모형(NAIVE/AR1/BASE_AR) 중 MAE 최소
                          -> 사후적으로 고른 보수적(엄격한) 비교 기준
    """
    mcols = [c for c in fc.columns
             if c in BASELINES or c.startswith("EXP_") or c.startswith("EXPs_")]
    mcols = [c for c in mcols if c != "EXP_AUTO_pick"]
    common = fc[["actual"] + mcols].dropna()
    if common.empty or "BASE_AR" not in common.columns:
        return pd.DataFrame()

    err = {c: (common[c] - common["actual"]).values for c in mcols}
    mae = {c: np.abs(e).mean() for c, e in err.items()}
    base_err = err["BASE_AR"]
    bb = min([b for b in BASELINES if b in mae], key=lambda b: mae[b])
    bb_err = err[bb]

    rows = []
    for c in mcols:
        e = err[c]
        rmse = np.sqrt((e ** 2).mean())
        st1, p1 = dm_test(base_err, e)
        st2, p2 = dm_test(bb_err, e)
        rows.append({
            "모형": c,
            "평가분기수": len(common),
            "평가기간": "{:%Y-%m}~{:%Y-%m}".format(common.index.min(), common.index.max()),
            "MAE": mae[c], "RMSE": rmse,
            "MAE_개선율_vs_BASE_AR_pct": (1 - mae[c] / mae["BASE_AR"]) * 100,
            "RMSE_개선율_vs_BASE_AR_pct": (1 - rmse / np.sqrt((base_err ** 2).mean())) * 100,
            "최선기준모형": bb,
            "MAE_개선율_vs_최선기준_pct": (1 - mae[c] / mae[bb]) * 100,
            "RMSE_개선율_vs_최선기준_pct": (1 - rmse / np.sqrt((bb_err ** 2).mean())) * 100,
            "DM_p값_vs_BASE_AR": p1,
            "DM_p값_vs_최선기준": p2,
        })
    out = pd.DataFrame(rows)
    out["목표변수"] = TARGETS.get(target, target)
    return out


def level_metrics(fc_rev: pd.DataFrame, fc_opm: pd.DataFrame) -> pd.DataFrame:
    """YoY/이익률 예측 -> 매출액·영업이익 수준(십억원) 오차로 환산."""
    mcols = [c for c in fc_rev.columns
             if (c in BASELINES or c.startswith("EXP_") or c.startswith("EXPs_"))
             and c != "EXP_AUTO_pick"]
    mcols = [c for c in mcols if c in fc_opm.columns]
    idx = fc_rev.index.intersection(fc_opm.index)
    a = fc_rev.loc[idx]
    b = fc_opm.loc[idx]
    keep = a[["actual"] + mcols].dropna().index.intersection(b[["actual"] + mcols].dropna().index)
    if len(keep) == 0:
        return pd.DataFrame()
    a, b = a.loc[keep], b.loc[keep]
    rows = []
    for c in mcols:
        rev_hat = a["rev_L4"] * (1 + a[c] / 100.0)
        op_hat = rev_hat * b[c] / 100.0
        rows.append({
            "모형": c, "평가분기수": len(keep),
            "매출액_MAE_십억": (rev_hat - a["rev_actual"]).abs().mean(),
            "매출액_RMSE_십억": np.sqrt(((rev_hat - a["rev_actual"]) ** 2).mean()),
            "영업이익_MAE_십억": (op_hat - b["op_actual"]).abs().mean(),
            "영업이익_RMSE_십억": np.sqrt(((op_hat - b["op_actual"]) ** 2).mean()),
        })
    out = pd.DataFrame(rows)
    for m in ("매출액_MAE_십억", "매출액_RMSE_십억", "영업이익_MAE_십억", "영업이익_RMSE_십억"):
        base = out.loc[out["모형"] == "BASE_AR", m].iloc[0]
        out[m.replace("_십억", "_개선율_pct")] = (1 - out[m] / base) * 100
    return out


# ---------------------------------------------------------------- 실행
def main():
    all_eval, all_level, all_fc, all_pick = [], [], [], []

    for code in COMPANIES:
        for scen, spec in SCENARIOS.items():
            exp_cols = list(spec["export"].keys())
            df = pd.read_csv(D_DATA / "dataset_{}_{}.csv".format(code, scen),
                             parse_dates=["date"]).set_index("date")
            fcs = {}
            for tgt in TARGETS:
                ar_cols = ["{}_AR1".format(tgt), "{}_SEAS".format(tgt)]
                fc = run_one(df, tgt, ar_cols, exp_cols, MIN_TRAIN[code])
                if fc.empty:
                    log("BT", "SKIP {} {} {} (표본부족)".format(code, scen, tgt))
                    continue
                fcs[tgt] = fc
                ev = evaluate(fc, tgt)
                if not ev.empty:
                    ev.insert(0, "정보집합", scen)
                    ev.insert(0, "기업명", COMPANIES[code]["name"])
                    ev.insert(0, "ticker", code)
                    all_eval.append(ev)
                f = fc.copy()
                f["ticker"] = code; f["정보집합"] = scen; f["목표변수"] = tgt
                all_fc.append(f.reset_index())
                if "EXP_AUTO_pick" in fc.columns:
                    pk = fc["EXP_AUTO_pick"].value_counts().rename("선택횟수").to_frame()
                    pk["ticker"] = code; pk["정보집합"] = scen; pk["목표변수"] = TARGETS[tgt]
                    all_pick.append(pk.reset_index(names="선택변수"))

            if {"rev_yoy", "opm"} <= set(fcs):
                lv = level_metrics(fcs["rev_yoy"], fcs["opm"])
                if not lv.empty:
                    lv.insert(0, "정보집합", scen)
                    lv.insert(0, "기업명", COMPANIES[code]["name"])
                    lv.insert(0, "ticker", code)
                    all_level.append(lv)

    ev = pd.concat(all_eval, ignore_index=True)
    ev.to_csv(D_TAB / "step4_oos_performance.csv", index=False, encoding="utf-8-sig")
    lv = pd.concat(all_level, ignore_index=True)
    lv.to_csv(D_TAB / "step4_oos_level_performance.csv", index=False, encoding="utf-8-sig")
    pd.concat(all_fc, ignore_index=True).to_csv(
        D_TAB / "step4_oos_forecasts.csv", index=False, encoding="utf-8-sig")
    if all_pick:
        pd.concat(all_pick, ignore_index=True).to_csv(
            D_TAB / "step4_auto_selection.csv", index=False, encoding="utf-8-sig")

    log("BT", "표본밖 성능표 {}행 / 수준환산표 {}행 저장".format(len(ev), len(lv)))

    # ---- 화면 출력 ----
    for code in COMPANIES:
        print("\n" + "=" * 104)
        print("■ {} ({})".format(COMPANIES[code]["name"], code))
        for scen in SCENARIOS:
            s = ev[(ev["ticker"] == code) & (ev["정보집합"] == scen)]
            if s.empty:
                continue
            print("\n  [{}] {}".format(scen, SCENARIOS[scen]["origin"]))
            for tgt_lab in s["목표변수"].unique():
                t = s[s["목표변수"] == tgt_lab].sort_values("MAE")
                print("   - {} (평가 {}분기, {})".format(
                    tgt_lab, t["평가분기수"].iloc[0], t["평가기간"].iloc[0]))
                cols = ["모형", "MAE", "RMSE", "MAE_개선율_vs_BASE_AR_pct",
                        "MAE_개선율_vs_최선기준_pct", "DM_p값_vs_최선기준"]
                print("     (최선 무수출 기준모형 = {})".format(t["최선기준모형"].iloc[0]))
                print(t[cols].round(3).head(8).to_string(index=False))
    return ev, lv


if __name__ == "__main__":
    main()
