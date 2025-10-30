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


def _preprocess_fronthalf_dropna_keep_backhalf(
    df_psr: pd.DataFrame, exog_df: Optional[pd.DataFrame]
) -> tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """
    앞 절반 구간에서 가로 방향(any) NaN 행 제거, 뒤 절반 구간은 NaN 유지.
    psr_df와 exog_df를 인덱스 기준으로 outer-join 후 처리 -> 다시 psr/exog로 분리.
    """
    psr_df = ensure_datetime_index_df(df_psr).copy()
    if 'psr' not in psr_df.columns:
        raise KeyError("입력 df_psr에 'psr' 컬럼이 있어야 합니다.")
    psr_df['psr'] = pd.to_numeric(psr_df['psr'], errors='coerce')

    if exog_df is not None and not exog_df.empty:
        exog_df = ensure_datetime_index_df(exog_df).copy()
        # 숫자형으로 강제 변환
        for c in exog_df.columns:
            exog_df[c] = pd.to_numeric(exog_df[c], errors='coerce')
        # 결합
        joined = psr_df.join(exog_df, how='outer', lsuffix='', rsuffix='')
    else:
        joined = psr_df.copy()

    joined = joined.sort_index()
    n = len(joined)
    if n == 0:
        return psr_df.iloc[0:0], (exog_df.iloc[0:0] if exog_df is not None else None)

    split = n // 2
    first_half = joined.iloc[:split].copy()
    second_half = joined.iloc[split:].copy()

    # 앞 절반: 가로방향(any) NaN 포함 행 제거
    first_half_clean = first_half.dropna(axis=0, how='any')

    cleaned = pd.concat([first_half_clean, second_half], axis=0)
    cleaned = cleaned.sort_index()

    # 다시 psr / exog로 분리
    psr_clean = cleaned[['psr']].copy()

    if exog_df is not None and not exog_df.empty:
        exog_cols = [c for c in cleaned.columns if c != 'psr']
        exog_clean = cleaned[exog_cols].copy()
    else:
        exog_clean = None

    return psr_clean, exog_clean


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

    # === (A) 전처리: 앞 절반 NaN 가로 제거 / 뒤 절반 NaN 유지 ===
    psr_clean, exog_clean = _preprocess_fronthalf_dropna_keep_backhalf(df_psr, exog_df)

    # y 학습 대상 시계열
    y = psr_clean['psr'].astype(float).dropna()
    if y.empty:
        raise ValueError("psr 시계열이 비어 있습니다. (전처리 후)")

    # 빈도/계절주기
    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)

    # 미래 인덱스
    future_idx = _make_future_index(y.index, horizon)

    # === (B) 단변량 모델들: 전처리된 y 사용 ===
    sarima_noexog_res = forecast_sarima(
        y=y, forecast_horizon=horizon, seasonal_period=m, **sarima_grid_kwargs
    )
    sarima_noexog = sarima_noexog_res.get("forecast", np.full(horizon, np.nan))

    ets_res = forecast_ets(y=y, forecast_horizon=horizon, m=m)
    ets_fc = ets_res.get("forecast", np.full(horizon, np.nan))

    prophet_res = forecast_prophet(y=y, forecast_horizon=horizon, m=m)
    prophet_fc = prophet_res.get("forecast", np.full(horizon, np.nan))

    lstm_res = forecast_lstm(y=y, forecast_horizon=horizon, **lstm_kwargs)
    lstm_fc = lstm_res.get("forecast", np.full(horizon, np.nan))

    theta_res = forecast_theta(y=y, forecast_horizon=horizon, m=m)
    theta_fc = theta_res.get("forecast", np.full(horizon, np.nan))

    # === (C) SARIMA with exog: 전처리된 psr/exog 공통 유효구간만 사용 ===
    sarima_exog = np.full(horizon, np.nan)
    if exog_clean is not None and not exog_clean.empty:
        # 학습에 쓸 유효 행(타겟과 모든 exog가 notna)
        used_cols = ['psr'] + list(exog_clean.columns)
        valid_mask = psr_clean.join(exog_clean, how='inner')[used_cols].notna().all(axis=1)
        valid_idx = valid_mask[valid_mask].index

        y_train = y.loc[y.index.intersection(valid_idx)]
        X_train = exog_clean.loc[y_train.index]

        # 미래 exog 확장: 학습에 실제 사용한 X_train 기반
        if not X_train.empty:
            X_future = _extend_exog_for_forecast(X_train, future_idx, exog_future_strategy)

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

    # 결과 집계 + psr_ prefix
    out = pd.DataFrame({
        "SARIMA_noexog": sarima_noexog,
        "SARIMA_exog": sarima_exog,
        "ETS": ets_fc,
        "Prophet": prophet_fc,
        "LSTM": lstm_fc,
        "Theta": theta_fc,
    }, index=future_idx)

    out.columns = [f"psr_{col}" for col in out.columns]
    out.index.name = "date"
    return out

