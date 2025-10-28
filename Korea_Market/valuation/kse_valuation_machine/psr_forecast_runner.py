# psr_forecast_runner.py
import numpy as np
import pandas as pd
from typing import Optional

from DATA.universal_ts_forecast_function import (
    ensure_datetime_index_df,
    infer_freq_alias,
    seasonal_periods_from_freq,
    forecast_sarima,
    forecast_ets,
    forecast_prophet,
    forecast_lstm,
    forecast_theta,
)

from statsmodels.tsa.statespace.sarimax import SARIMAX


def _make_future_index(last_idx: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    freq = pd.infer_freq(last_idx)
    if not freq:
        last = last_idx[-1]
        if last == (last + pd.offsets.MonthEnd(0)):
            freq = 'M'
        else:
            freq = 'D'
    return pd.date_range(start=last_idx[-1], periods=horizon+1, freq=freq, inclusive='neither')


def _extend_exog_for_forecast(
    exog_df: pd.DataFrame,
    future_index: pd.DatetimeIndex,
    strategy: str = "repeat_last",
) -> pd.DataFrame:
    if strategy == "repeat_last":
        last_vals = exog_df.iloc[[-1]].reindex(future_index).ffill()
        return last_vals
    elif strategy == "ffill":
        tmp = exog_df.copy()
        tmp = tmp.reindex(exog_df.index.union(future_index)).ffill()
        return tmp.loc[future_index]
    else:
        raise ValueError("지원하지 않는 exog 확장 전략입니다.")


def forecast_psr_all_models(
    df_psr: pd.DataFrame,
    horizon: int = 5,
    exog_df: Optional[pd.DataFrame] = None,
    exog_future_strategy: str = "repeat_last",
    sarima_grid_kwargs: Optional[dict] = None,
    lstm_kwargs: Optional[dict] = None,
) -> pd.DataFrame:

    if sarima_grid_kwargs is None:
        sarima_grid_kwargs = {}
    if lstm_kwargs is None:
        lstm_kwargs = {}

    # 1) 인덱스/컬럼 정리
    df_psr = ensure_datetime_index_df(df_psr)
    if 'psr' not in df_psr.columns:
        raise KeyError("입력 df_psr에 'psr' 컬럼이 있어야 합니다.")

    y = df_psr['psr'].astype(float).dropna()
    if y.empty:
        raise ValueError("psr 시계열이 비어 있습니다.")

    # 2) 빈도/계절주기
    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)

    # 3) 미래 인덱스
    future_idx = _make_future_index(y.index, horizon)

    # 4) SARIMA(no exog)
    sarima_noexog_res = forecast_sarima(
        y=y, forecast_horizon=horizon, seasonal_period=m, **sarima_grid_kwargs
    )
    sarima_noexog = sarima_noexog_res.get("forecast", np.full(horizon, np.nan))

    # 5) ETS
    ets_res = forecast_ets(y=y, forecast_horizon=horizon, m=m)
    ets_fc = ets_res.get("forecast", np.full(horizon, np.nan))

    # 6) Prophet
    prophet_res = forecast_prophet(y=y, forecast_horizon=horizon, m=m)
    prophet_fc = prophet_res.get("forecast", np.full(horizon, np.nan))

    # 7) LSTM
    lstm_res = forecast_lstm(y=y, forecast_horizon=horizon, **lstm_kwargs)
    lstm_fc = lstm_res.get("forecast", np.full(horizon, np.nan))

    # 8) Theta
    theta_res = forecast_theta(y=y, forecast_horizon=horizon, m=m)
    theta_fc = theta_res.get("forecast", np.full(horizon, np.nan))

    # 9) SARIMA(exog)
    sarima_exog = np.full(horizon, np.nan)
    if exog_df is not None and not exog_df.empty:
        exog_df = ensure_datetime_index_df(exog_df).astype(float)
        common_idx = y.index.intersection(exog_df.index)
        y_train = y.loc[common_idx]
        X_train = exog_df.loc[common_idx]
        X_future = _extend_exog_for_forecast(exog_df.loc[common_idx], future_idx, exog_future_strategy)

        order = sarima_grid_kwargs.get("order", (0, 1, 1))
        seasonal_order = sarima_grid_kwargs.get("seasonal_order", (0, 1, 1, max(1, m)))

        try:
            model = SARIMAX(
                y_train, exog=X_train,
                order=order, seasonal_order=seasonal_order,
                enforce_stationarity=False, enforce_invertibility=False
            )
            res = model.fit(disp=False)
            sarima_exog = res.forecast(steps=horizon, exog=X_future).values
        except Exception as e:
            print(f"[경고] SARIMA exog 예측 실패: {e}")

    # 10) 결과 집계 + psr_ prefix 추가
    out = pd.DataFrame({
        "SARIMA_noexog": sarima_noexog,
        "SARIMA_exog": sarima_exog,
        "ETS": ets_fc,
        "Prophet": prophet_fc,
        "LSTM": lstm_fc,
        "Theta": theta_fc,
    }, index=future_idx)

    # 모든 칼럼명 앞에 psr_ 접두어 추가
    out.columns = [f"psr_{col}" for col in out.columns]

    out.index.name = "date"
    return out
