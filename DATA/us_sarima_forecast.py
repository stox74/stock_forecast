
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SARIMA 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈

특징:
1) 외생변수(exog_col) 사용 여부 구분
   - exog_col=None → 외생변수 없이 예측
   - exog_col="expDlr_yoy" → 해당 컬럼을 외생변수로 사용
2) 매출(revenue): 분기 단위 예측 (forecast_quarters)
   - 미래는 분기별 1개 값, 과거 NaN은 revenue_billions로 채움
3) PSR: 월별 12개월 예측
   - 미래는 12개월, 과거 NaN은 PSR_ttm으로 채움
4) 매출과 PSR의 예측 시작일 분리
   - start_date_revenue, start_date_psr
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from typing import Optional, Union
from statsmodels.tsa.statespace.sarimax import SARIMAX
from pandas.tseries.offsets import MonthEnd
from itertools import product
from typing import Optional, Union, Tuple
# =========================================================
# 유틸 함수
# =========================================================

# (이미 있으면 생략)
def to_month_end(s):
    ts = pd.to_datetime(s)
    if isinstance(ts, pd.Timestamp):
        return ts + MonthEnd(0)
    return ts + MonthEnd(0)

def ensure_sorted_unique_dates(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date_month_end"] = to_month_end(d["date_month_end"])
    return d.sort_values("date_month_end").drop_duplicates(["date_month_end"]).reset_index(drop=True)

def _filter_quarter_phase(df: pd.DataFrame) -> pd.DataFrame:
    """월 자료에서도 분기 페이즈(월%3 최빈값)만 남김: 01/04/07/10 등."""
    d = ensure_sorted_unique_dates(df)
    phase = (d["date_month_end"].dt.month % 3).mode().iloc[0]
    return d[d["date_month_end"].dt.month % 3 == phase].reset_index(drop=True)

def find_best_sarima_params(
    y_train: pd.Series,
    exog_train: pd.Series | None = None,
    seasonal_period: int = 12,
    p_values=(0,1,2), d_values=(0,1), q_values=(0,1,2),
    P_values=(0,1),   D_values=(0,1), Q_values=(0,1),
    ic: str = "aic",
    max_order_sum: int = 8,
):
    best_ic = np.inf
    best_order = (1,1,1)
    best_sorder = (1,1,0, seasonal_period)
    for p,d,q in product(p_values, d_values, q_values):
        for P,D,Q in product(P_values, D_values, Q_values):
            if (p+q+P+Q) > max_order_sum:
                continue
            order = (p,d,q)
            sorder = (P,D,Q, seasonal_period)
            try:
                m = SARIMAX(
                    y_train.astype(float),
                    exog=None if exog_train is None else exog_train.astype(float),
                    order=order, seasonal_order=sorder,
                    enforce_stationarity=False, enforce_invertibility=False
                )
                fit = m.fit(disp=False)
                val = fit.aic if ic.lower()=="aic" else fit.bic
                if np.isfinite(val) and val < best_ic:
                    best_ic, best_order, best_sorder = val, order, sorder
            except Exception:
                continue
    return best_order, best_sorder


def run_sarima_prediction(
        df: pd.DataFrame,
        ticker: str = "UNKNOWN",
        forecast_quarters: int = 4,
        psr_periods: int = 12,  # 월간 예측 길이(12/24 등)
        start_date_revenue: Optional[Union[str, pd.Timestamp]] = None,
        start_date_psr: Optional[Union[str, pd.Timestamp]] = None,
        exog_col: Optional[str] = None,
        ic: str = "aic",
) -> Tuple[pd.DataFrame, dict]:
    """
    반환을 항상 보장: (out_df, results)
    - revenue_billions → 분기 예측(S=4)
    - PSR_ttm(또는 월간 타깃) → 월 예측(S=12, psr_periods)
    """
    out_df = ensure_sorted_unique_dates(df)
    results = {"meta": {"ticker": ticker}, "revenue": {}, "psr": {}}

    # ---------------- Revenue: Quarterly ----------------
    try:
        if "revenue_billions" in out_df.columns:
            qdf = _filter_quarter_phase(out_df[["date_month_end","revenue_billions"]])
            y = pd.Series(qdf["revenue_billions"].values, index=qdf["date_month_end"]).dropna()
            if len(y) >= 8:
                exog_hist = None
                if exog_col and exog_col in out_df.columns:
                    exog_hist = out_df.set_index("date_month_end")[exog_col].reindex(y.index).ffill().bfill()

                ord_ne, sord_ne = find_best_sarima_params(y, None, 4, ic=ic)
                fit_ne = SARIMAX(y, order=ord_ne, seasonal_order=sord_ne,
                                 enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                fc_ne = fit_ne.forecast(steps=forecast_quarters)

                fc_ex = None
                if exog_hist is not None:
                    ord_ex, sord_ex = find_best_sarima_params(y, exog_hist, 4, ic=ic)
                    fit_ex = SARIMAX(y, exog=exog_hist, order=ord_ex, seasonal_order=sord_ex,
                                     enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                    exog_future = np.repeat(exog_hist.iloc[-1], forecast_quarters).reshape(-1,1)
                    fc_ex = fit_ex.forecast(steps=forecast_quarters, exog=exog_future)

                last_q = y.index.max() if not start_date_revenue else to_month_end(start_date_revenue)
                future_q = [last_q + pd.DateOffset(months=3*i) for i in range(1, forecast_quarters+1)]

                out_df["revenue_billions_sarima_noexog"] = out_df.get("revenue_billions")
                if exog_hist is not None:
                    out_df["revenue_billions_sarima_exog"] = out_df.get("revenue_billions")

                for i, d in enumerate(future_q):
                    if d not in out_df["date_month_end"].values:
                        out_df.loc[len(out_df), "date_month_end"] = d
                    out_df.loc[out_df["date_month_end"] == d, "revenue_billions_sarima_noexog"] = fc_ne.iloc[i]
                    if fc_ex is not None:
                        out_df.loc[out_df["date_month_end"] == d, "revenue_billions_sarima_exog"] = fc_ex.iloc[i]

                out_df["revenue_billions_sarima_noexog"] = \
                    out_df["revenue_billions_sarima_noexog"].combine_first(out_df["revenue_billions"])
                if "revenue_billions_sarima_exog" in out_df.columns:
                    out_df["revenue_billions_sarima_exog"] = \
                        out_df["revenue_billions_sarima_exog"].combine_first(out_df["revenue_billions"])

                results["revenue"] = {
                    "order": ord_ne, "seasonal_order": sord_ne,
                    "forecast_noexog": fc_ne, "forecast_exog": fc_ex
                }
    except Exception as e:
        results["revenue"]["error"] = str(e)

    # ---------------- PSR(or monthly target): Monthly ----------------
    try:
        target_col = "PSR_ttm" if "PSR_ttm" in out_df.columns else None
        if target_col:
            d = ensure_sorted_unique_dates(out_df[["date_month_end", target_col]])
            full_idx = pd.date_range(d["date_month_end"].min(), d["date_month_end"].max(), freq="M")
            y = pd.Series(d[target_col].values, index=d["date_month_end"]).reindex(full_idx).interpolate("time").ffill().bfill()
            if y.notna().sum() >= 24:
                exog_hist = None
                if exog_col and exog_col in out_df.columns:
                    exog_hist = out_df.set_index("date_month_end")[exog_col].reindex(y.index).ffill().bfill()

                ord_ne, sord_ne = find_best_sarima_params(y, None, 12, ic=ic)
                fit_ne = SARIMAX(y, order=ord_ne, seasonal_order=sord_ne,
                                 enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                fc_ne = fit_ne.forecast(steps=int(psr_periods))

                fc_ex = None
                if exog_hist is not None:
                    ord_ex, sord_ex = find_best_sarima_params(y, exog_hist, 12, ic=ic)
                    fit_ex = SARIMAX(y, exog=exog_hist, order=ord_ex, seasonal_order=sord_ex,
                                     enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                    exog_future = np.repeat(exog_hist.iloc[-1], int(psr_periods)).reshape(-1,1)
                    fc_ex = fit_ex.forecast(steps=int(psr_periods), exog=exog_future)

                last_m = y.index.max() if not start_date_psr else to_month_end(start_date_psr)
                future_m = pd.date_range(last_m + MonthEnd(1), periods=int(psr_periods), freq="M")

                out_df[f"{target_col}_sarima_forecast_noexog"] = out_df.get(target_col)
                if exog_hist is not None:
                    out_df[f"{target_col}_sarima_forecast_exog"] = out_df.get(target_col)

                for i, d_ in enumerate(future_m):
                    if d_ not in out_df["date_month_end"].values:
                        out_df.loc[len(out_df), "date_month_end"] = d_
                    out_df.loc[out_df["date_month_end"] == d_, f"{target_col}_sarima_forecast_noexog"] = fc_ne.iloc[i]
                    if fc_ex is not None:
                        out_df.loc[out_df["date_month_end"] == d_, f"{target_col}_sarima_forecast_exog"] = fc_ex.iloc[i]

                out_df[f"{target_col}_sarima_forecast_noexog"] = \
                    out_df[f"{target_col}_sarima_forecast_noexog"].combine_first(out_df[target_col])
                if f"{target_col}_sarima_forecast_exog" in out_df.columns:
                    out_df[f"{target_col}_sarima_forecast_exog"] = \
                        out_df[f"{target_col}_sarima_forecast_exog"].combine_first(out_df[target_col])

                results["psr"] = {
                    "order": ord_ne, "seasonal_order": sord_ne,
                    "forecast_noexog": fc_ne, "forecast_exog": fc_ex
                }
    except Exception as e:
        results["psr"]["error"] = str(e)

    out_df = out_df.sort_values("date_month_end").reset_index(drop=True)
    return out_df, results

def _seasonal_naive(y: pd.Series, steps: int, season: int = 12) -> pd.Series:
    """계절 나이브: t+h 예측 = t-(season-h%season) 관측."""
    if len(y) < season:
        return pd.Series(np.repeat(y.iloc[-1], steps))
    last_cycle = y.iloc[-season:]
    reps = int(np.ceil(steps / season))
    vals = np.tile(last_cycle.values, reps)[:steps]
    return pd.Series(vals)

def run_sarima_psr_only(
    df: pd.DataFrame,
    periods: int = 12,                                  # 예측 개월 수 (12/24 등)
    target_col: str = "PSR_ttm",
    out_col: Optional[str] = None,
    analysis_start: Union[str, pd.Timestamp] = "2012-01-01",
    warmup_months: int = 6,                             # 최초 유효값 + 6개월부터 학습
    fill_method: str = "interpolate",                   # 'ffill'|'bfill'|'interpolate'
    ic: str = "aic",
    transform: str = "log",                             # 'log' or 'none'
    max_forecast_multiplier: float = 10.0,              # 폭주 감지 임계(마지막 수준의 10배)
) -> Tuple[pd.DataFrame, dict]:
    """
    월간 SARIMA - PSR 전용(다른 양의 월간 변수에도 사용 가능)
    - 2012-01 이후 데이터만 사용
    - 최초 유효값 + 6개월 이후부터 학습
    - 기본 로그 변환으로 양수 보장 및 폭주 완화
    - 불안정 예측(음수/과도한 폭주) 시 계절 나이브로 자동 대체
    """
    results = {"meta": {"target": target_col, "model": "SARIMA-monthly"}, "fit": {}, "fallback": None, "error": None}

    try:
        if "date_month_end" not in df.columns:
            raise ValueError("df에 'date_month_end' 컬럼이 필요합니다.")
        if target_col not in df.columns:
            raise ValueError("df에 '{}' 컬럼이 필요합니다.".format(target_col))

        d0 = ensure_sorted_unique_dates(df[["date_month_end", target_col]])
        d = d0[d0["date_month_end"] >= to_month_end(analysis_start)].copy()
        if d.empty:
            raise ValueError("2012-01 이후 데이터가 없습니다.")

        # 연속 월말 인덱스 생성
        full_idx = pd.date_range(d["date_month_end"].min(), d["date_month_end"].max(), freq="M")
        s_raw = pd.Series(d[target_col].values, index=d["date_month_end"]).reindex(full_idx)

        # 최초 유효값 + warmup
        if s_raw.dropna().empty:
            raise ValueError("2012-01 이후 '{}'의 유효값이 없습니다.".format(target_col))
        first_valid = s_raw.dropna().index.min()
        start_cutoff = first_valid + MonthEnd(warmup_months)

        # 결측 보정
        if fill_method == "ffill":
            s_train = s_raw.ffill()
        elif fill_method == "bfill":
            s_train = s_raw.bfill()
        elif fill_method == "interpolate":
            s_train = s_raw.interpolate("time").ffill().bfill()
        else:
            raise ValueError("fill_method는 'ffill'|'bfill'|'interpolate' 중 하나여야 합니다.")

        # 학습구간
        s_train = s_train[s_train.index >= start_cutoff]
        if s_train.notna().sum() < 12:
            s_train = s_raw.interpolate("time").ffill().bfill()
            s_train = s_train[s_train.index >= first_valid]
        if s_train.notna().sum() < 12:
            raise ValueError("학습데이터가 부족합니다(최소 12개월 필요).")

        # 변환(로그)
        eps = 1e-6
        if transform == "log":
            if (s_train <= 0).any():
                s_train = s_train.clip(lower=eps)
            y_fit = np.log(s_train)
        else:
            y_fit = s_train.copy()

        # 최적 파라미터 탐색 및 적합(안정성 강제)
        order, sorder = find_best_sarima_params(y_fit, None, seasonal_period=12, ic=ic)
        model = SARIMAX(
            y_fit.astype(float),
            order=order, seasonal_order=sorder,
            enforce_stationarity=True,
            enforce_invertibility=True,
            trend="n",
        )
        fit = model.fit(disp=False)

        # 미래 인덱스 & 예측
        last_hist = y_fit.index.max()
        future_idx = pd.date_range(last_hist + MonthEnd(1), periods=int(periods), freq="M")
        fc = fit.get_forecast(steps=int(periods))
        fc_mean = pd.Series(fc.predicted_mean, index=future_idx)

        # 역변환
        if transform == "log":
            fc_level = np.exp(fc_mean)
            last_level = float(s_train.iloc[-1])
        else:
            fc_level = fc_mean.copy()
            last_level = float(s_train.iloc[-1])

        # 폭주/음수 감지 → 계절 나이브 백업
        if (fc_level < 0).any() or (fc_level > last_level * max_forecast_multiplier).any():
            results["fallback"] = "seasonal_naive"
            sn = _seasonal_naive(s_train, steps=int(periods), season=12)
            fc_level = pd.Series(sn.values, index=future_idx)

        # 출력 DF
        out = d0.set_index("date_month_end")[[target_col]].copy()
        add = pd.DataFrame(index=future_idx, data={target_col: np.nan})
        out = pd.concat([out, add], axis=0)

        if out_col is None:
            out_col = "{}_sarima_forecast".format(target_col)
        out[out_col] = out[target_col]
        out.loc[future_idx, out_col] = fc_level.values
        out = out.sort_index()

        results["meta"]["order"] = order
        results["meta"]["seasonal_order"] = sorder
        results["meta"]["transform"] = transform
        results["meta"]["analysis_start_used"] = str(analysis_start)
        results["meta"]["warmup_months"] = warmup_months
        results["fit"]["aic"] = float(fit.aic)
        results["fit"]["bic"] = float(fit.bic)
        return out, results

    except Exception as e:
        results["error"] = str(e)
        safe = ensure_sorted_unique_dates(df[["date_month_end", target_col]])
        return safe.set_index("date_month_end").sort_index(), results



if __name__ == "__main__":
    print("us_sarima_forecast.py loaded. Use run_sarima_prediction(...)")


