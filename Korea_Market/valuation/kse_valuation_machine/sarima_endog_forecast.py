# -*- coding: utf-8 -*-
"""
sarima_endog_forecast.py
... (docstring trimmed for brevity in this cell)
"""
from typing import Optional, Dict, Any
import os
import sys
import numpy as np
import pandas as pd

try:
    from DATA.universal_ts_forecast_function import (
        infer_freq_alias, seasonal_periods_from_freq,
        forecast_sarima, ensure_datetime_index_df
    )
except Exception:
    sys.path.insert(0, "/mnt/data")
    from DATA.universal_ts_forecast_function import (
        infer_freq_alias, seasonal_periods_from_freq,
        forecast_sarima, ensure_datetime_index_df
    )

def _ensure_quarter_or_month_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out.index = pd.to_datetime(out["date"], errors="coerce")
        out = out.drop(columns=["date"])
    elif not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    out.index = out.index.tz_localize(None) if getattr(out.index, "tz", None) else out.index
    out.index.name = "date"
    return out

def forecast_endog_with_optional_exog(
    combined_df: pd.DataFrame,
    horizon: int = 4,
    hs_code: Optional[str] = None,
    seasonal_period: Optional[int] = None,
    try_transforms: bool = True,
    sarima_grid_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    개선 버전:
    - endog_var는 건드리지 않음
    - exog_var의 초반부 NaN만 제거 (뒤쪽 결측은 그대로 둠)
    """

    if not isinstance(combined_df, pd.DataFrame) or "endog_var" not in combined_df.columns:
        raise ValueError("combined_df must be a DataFrame containing 'endog_var' column.")

    df = combined_df.copy()
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[~df.index.isna()].sort_index()

    y = df["endog_var"].astype(float)

    # 외생변수 처리
    use_exog = (hs_code is not None) and ("exog_var" in df.columns)
    X = None
    if use_exog:
        X = df[["exog_var"]].astype(float)

        # ▶ exog_var 전처리: 앞부분 NaN 제거 (뒤쪽 NaN은 그대로 둠)
        first_valid = X["exog_var"].first_valid_index()
        if first_valid is not None:
            X = X.loc[first_valid:]
            y = y.loc[first_valid:]  # 인덱스 정렬 맞추기

        # 필요하면 선형 보간 옵션 추가 (선택적)
        # X["exog_var"] = X["exog_var"].interpolate(method='linear', limit_direction='forward')

    # 계절 주기 추론
    if seasonal_period is None:
        from DATA.universal_ts_forecast_function import infer_freq_alias, seasonal_periods_from_freq
        freq_alias = infer_freq_alias(y.index)
        seasonal_period = seasonal_periods_from_freq(freq_alias)

    grid_kwargs = dict(sarima_grid_kwargs or {})
    grid_kwargs.setdefault("p_values", (0, 1, 2))
    grid_kwargs.setdefault("d_values", (0, 1))
    grid_kwargs.setdefault("q_values", (0, 1, 2))
    grid_kwargs.setdefault("P_values", (0, 1))
    grid_kwargs.setdefault("D_values", (0, 1))
    grid_kwargs.setdefault("Q_values", (0, 1))
    grid_kwargs.setdefault("ic", "aic")
    grid_kwargs.setdefault("max_order_sum", 8)
    grid_kwargs.setdefault("n_jobs", 1)

    from DATA.universal_ts_forecast_function import forecast_sarima
    result = forecast_sarima(
        y=y,
        forecast_horizon=int(horizon),
        exog=X if use_exog else None,
        seasonal_period=int(seasonal_period),
        try_transforms=try_transforms,
        **grid_kwargs
    )

    result["used_exog"] = bool(use_exog)
    result["seasonal_period"] = int(seasonal_period)
    return result

from DATA.universal_ts_forecast_function import (
    find_best_sarima_params, infer_freq_alias, seasonal_periods_from_freq
)

def _ensure_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out.index = pd.to_datetime(out["date"], errors="coerce")
        out = out.drop(columns=["date"])
    elif not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    out.index = out.index.tz_localize(None) if getattr(out.index, "tz", None) else out.index
    out.index.name = "Date"
    return out

def _future_q_index(last_idx: pd.Timestamp, horizon: int) -> pd.DatetimeIndex:
    """마지막 분기 이후 horizon 개 분기 말일 인덱스 생성."""
    return pd.period_range(last_idx.to_period("Q") + 1, periods=int(horizon), freq="Q").to_timestamp("Q")

def _build_exog_future(
    exog_hist: pd.Series,
    future_index: pd.DatetimeIndex,
    seasonal_period: int,
    strategy: str = "seasonal"  # 'seasonal' | 'carry' | 'mean' | 'zero'
) -> pd.DataFrame:
    s = exog_hist.dropna().astype(float)
    if s.empty:
        vals = np.zeros(len(future_index))
        return pd.DataFrame({"exog_var": vals}, index=future_index)

    if strategy == "carry":
        last_val = float(s.iloc[-1])
        vals = np.repeat(last_val, len(future_index))
    elif strategy == "seasonal":
        m = max(1, int(seasonal_period))
        pat = s.iloc[-m:].to_numpy()
        rep = int(np.ceil(len(future_index) / m))
        vals = np.tile(pat, rep)[:len(future_index)]
    elif strategy == "mean":
        vals = np.repeat(float(s.mean()), len(future_index))
    elif strategy == "zero":
        vals = np.zeros(len(future_index))
    else:
        raise ValueError("exog future strategy must be one of {'seasonal','carry','mean','zero'}")
    return pd.DataFrame({"exog_var": vals}, index=future_index)

def forecast_endog_fill_tail(
    combined_df: pd.DataFrame,
    horizon: Optional[int] = None,            # None이면 endog_var 꼬리쪽 NaN 갯수로 자동 추정
    hs_code: Optional[str] = None,            # None이면 exog 미사용
    seasonal_period: Optional[int] = None,    # 미지정 시 자동 추론
    exog_strategy: Optional[str] = None,      # None일 경우 자동 결정
    sarima_grid_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    final_combined_data 같은 DF를 받아 SARIMA로 꼬리(endog_var NaN)만 예측해 채웁니다.
    """

    df = _ensure_dt_index(combined_df)
    if "endog_var" not in df.columns:
        return {"error": "combined_df에 'endog_var'가 없습니다."}

    y_all = df["endog_var"].astype(float)

    # 1) horizon 자동 추정 (endog_var 꼬리쪽 NaN 길이)
    if horizon is None:
        mask_notna = y_all.notna()
        if mask_notna.any():
            last_obs_loc = np.where(mask_notna.values)[0][-1]
            horizon = len(y_all) - (last_obs_loc + 1)
        else:
            horizon = 4
    horizon = int(max(1, horizon))

    # 2) 학습 구간
    y_train = y_all.dropna()

    # 3) 계절 주기 추론
    if seasonal_period is None:
        freq_alias = infer_freq_alias(y_train.index)
        seasonal_period = seasonal_periods_from_freq(freq_alias)

    # 4) exog 사용 여부
    use_exog = (hs_code is not None) and ("exog_var" in df.columns)

    # exog_strategy 자동 설정 로직 추가
    if use_exog and exog_strategy is None:
        exog_strategy = "seasonal"

    X_train = None
    if use_exog:
        X_full = df[["exog_var"]].astype(float)
        first_exog = X_full["exog_var"].first_valid_index()
        if first_exog is not None:
            X_full = X_full.loc[first_exog:]
            y_train = y_train.loc[first_exog:]

        idx = y_train.index.intersection(X_full.index)
        y_train = y_train.loc[idx]
        X_train = X_full.loc[idx]

    # 나머지 원래 코드 동일
    grid = dict(sarima_grid_kwargs or {})
    grid.setdefault("p_values", (0, 1, 2))
    grid.setdefault("d_values", (0, 1))
    grid.setdefault("q_values", (0, 1, 2))
    grid.setdefault("P_values", (0, 1))
    grid.setdefault("D_values", (0, 1))
    grid.setdefault("Q_values", (0, 1))
    grid.setdefault("ic", "aic")
    grid.setdefault("max_order_sum", 8)
    grid.setdefault("n_jobs", 1)

    params = find_best_sarima_params(
        y_train=y_train,
        exog_train=X_train,
        seasonal_period=int(seasonal_period),
        refit_best=True,
        verbose=False,
        **grid
    )
    res = params.get("model", None)
    if res is None:
        return {"error": "SARIMA fit failed", "used_exog": use_exog, "seasonal_period": int(seasonal_period)}

    last_obs_ts = y_train.index[-1]
    fc_index = _future_q_index(last_obs_ts, horizon)

    exog_future = None
    if use_exog:
        exog_future = _build_exog_future(
            exog_hist=X_train["exog_var"],
            future_index=fc_index,
            seasonal_period=int(seasonal_period),
            strategy=exog_strategy
        )

    try:
        fc = res.forecast(steps=horizon, exog=exog_future if use_exog else None)
    except Exception as e:
        return {"error": f"forecast failed: {e}", "used_exog": use_exog, "seasonal_period": int(seasonal_period)}

    fc = np.asarray(fc, dtype=float)

    filled = df.copy()
    filled.loc[fc_index, "endog_var"] = fc

    return {
        "forecast_index": fc_index,
        "forecast": fc,
        "spec": {
            "order": params["order"],
            "seasonal_order": params["seasonal_order"],
            "ic_value": params["ic_value"],
        },
        "used_exog": use_exog,
        "seasonal_period": int(seasonal_period),
        "filled": filled,
        "exog_future": exog_future,
        "exog_strategy_used": exog_strategy  # 확인용 필드 추가
    }
