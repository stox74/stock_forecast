# -*- coding: cp949 -*-

from typing import Optional, Dict
import numpy as np
import pandas as pd

# 유틸/모델 import (이미 /mnt/data 에 있음)
from DATA.universal_ts_forecast_function import (
    ensure_datetime_index_df,
    infer_freq_alias,
    seasonal_periods_from_freq,
    forecast_ets,
    forecast_prophet,
    forecast_lstm,
    forecast_theta,
)

# ===== 공통 유틸 =====

import pandas as pd
import numpy as np

def _future_index_from_last(idx: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """마지막 관측 이후 horizon개 인덱스(분기/월 말일) 생성"""
    if not isinstance(idx, pd.DatetimeIndex) or len(idx) == 0:
        raise ValueError("유효한 DatetimeIndex가 필요합니다.")
    last_idx = idx.max()
    freq_alias = infer_freq_alias(idx)
    h = int(horizon)
    if "Q" in str(freq_alias).upper():
        return pd.period_range(last_idx.to_period("Q") + 1, periods=h, freq="Q").to_timestamp("Q")
    else:
        return pd.period_range(last_idx.to_period("M") + 1, periods=h, freq="M").to_timestamp("M")


def _prepare_series(final_combined_data: pd.DataFrame) -> tuple[pd.Series, int, str]:
    """endog_var Series / 계절주기 m / 빈도 alias 추출"""
    df = ensure_datetime_index_df(final_combined_data)
    if "endog_var" not in df.columns:
        raise KeyError("'endog_var' 컬럼이 없습니다.")
    y = df["endog_var"].astype(float).dropna()
    if y.empty:
        raise ValueError("endog_var에 유효한 데이터가 없습니다.")
    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)
    return y, int(m), freq_alias


def _to_df(forecast: np.ndarray, index: pd.DatetimeIndex, colname: str) -> pd.DataFrame:
    return pd.DataFrame(np.asarray(forecast, dtype=float), index=index, columns=[colname])


# ===== 각 모델별 래퍼 =====

def forecast_revenue_ets(final_combined_data: pd.DataFrame,
                         horizon: int = 4,
                         try_transforms: bool = True) -> pd.DataFrame:
    """
    ETS로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_ets')
    """
    y, m, _ = _prepare_series(final_combined_data)
    out = forecast_ets(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])
    fc_index = _future_index_from_last(y.index, horizon)
    return _to_df(out["forecast"], fc_index, "revenue_ets")


def forecast_revenue_prophet(final_combined_data: pd.DataFrame,
                             horizon: int = 4,
                             try_transforms: bool = True) -> pd.DataFrame:
    """
    Prophet으로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_prophet')
    (미설치 시 RuntimeError)
    """
    y, m, _ = _prepare_series(final_combined_data)
    out = forecast_prophet(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])
    fc_index = _future_index_from_last(y.index, horizon)
    return _to_df(out["forecast"], fc_index, "revenue_prophet")


def forecast_revenue_lstm(final_combined_data: pd.DataFrame,
                          horizon: int = 4,
                          lookback: int = 12,
                          epochs: int = 50,
                          batch_size: int = 16,
                          try_transforms: bool = True) -> pd.DataFrame:
    """
    LSTM으로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_lstm')
    (TensorFlow 미설치 시 RuntimeError)
    """
    y, _, _ = _prepare_series(final_combined_data)
    out = forecast_lstm(
        y=y, forecast_horizon=horizon,
        lookback=lookback, epochs=epochs, batch_size=batch_size,
        try_transforms=try_transforms
    )
    if "error" in out:
        raise RuntimeError(out["error"])
    fc_index = _future_index_from_last(y.index, horizon)
    return _to_df(out["forecast"], fc_index, "revenue_lstm")


def forecast_revenue_theta(final_combined_data: pd.DataFrame,
                           horizon: int = 4,
                           try_transforms: bool = True) -> pd.DataFrame:
    """
    Theta로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_theta')
    (statsmodels>=0.13 필요, 미설치 시 RuntimeError)
    """
    y, m, _ = _prepare_series(final_combined_data)
    out = forecast_theta(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])
    fc_index = _future_index_from_last(y.index, horizon)
    return _to_df(out["forecast"], fc_index, "revenue_theta")


# ===== 선택: 한 번에 네 모델 돌리고 합치기 =====

def forecast_revenue_all_models(final_combined_data: pd.DataFrame,
                                horizon: int = 4) -> Dict[str, pd.DataFrame]:
    """
    네 모델(ETS/Prophet/LSTM/Theta)을 돌려 결과 DataFrame 사전으로 반환.
    설치/환경 문제로 실패한 모델은 Key만 제외.
    """
    results: Dict[str, pd.DataFrame] = {}

    # ETS
    try:
        results["ETS"] = forecast_revenue_ets(final_combined_data, horizon=horizon)
    except Exception as e:
        print(f"[ETS] skip: {e}")

    # Prophet
    try:
        results["Prophet"] = forecast_revenue_prophet(final_combined_data, horizon=horizon)
    except Exception as e:
        print(f"[Prophet] skip: {e}")

    # LSTM
    try:
        results["LSTM"] = forecast_revenue_lstm(final_combined_data, horizon=horizon)
    except Exception as e:
        print(f"[LSTM] skip: {e}")

    # Theta
    try:
        results["Theta"] = forecast_revenue_theta(final_combined_data, horizon=horizon)
    except Exception as e:
        print(f"[Theta] skip: {e}")

    return results
