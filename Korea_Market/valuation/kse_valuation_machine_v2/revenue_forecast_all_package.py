# -*- coding: utf-8 -*-
"""
Revenue 예측 패키지 (날짜 표준화 적용)
- 모든 예측 결과를 표준 분기말 날짜로 통일
- 개별 모델 함수 + 통합 함수 제공
"""
import pandas as pd
import numpy as np
from typing import Optional, Tuple, Dict
from date_standardization import get_standard_quarter_dates, standardize_dataframe_dates

from DATA.universal_ts_forecast_function import (
    ensure_datetime_index_df,
    infer_freq_alias,
    seasonal_periods_from_freq,
    forecast_sarima,
    forecast_ets,
    forecast_theta,
    forecast_lstm,
    forecast_prophet,
)


# ===== 공통 유틸 =====

def _prepare_series(final_combined_data: pd.DataFrame) -> tuple:
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


# ===== 개별 모델별 래퍼 함수 =====

def forecast_revenue_ets(final_combined_data: pd.DataFrame,
                         horizon: int = 4,
                         try_transforms: bool = True) -> pd.DataFrame:
    """
    ETS로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_ets')
    표준 분기말 날짜 적용
    """
    y, m, _ = _prepare_series(final_combined_data)

    # 마지막 실제 데이터 날짜
    last_actual_date = y.index[-1].strftime('%Y-%m-%d')

    # 표준 분기말 날짜 생성
    fc_index = get_standard_quarter_dates(last_actual_date, horizon)

    out = forecast_ets(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])

    return _to_df(out["forecast"], fc_index, "revenue_ets")


def forecast_revenue_prophet(final_combined_data: pd.DataFrame,
                             horizon: int = 4,
                             try_transforms: bool = True) -> pd.DataFrame:
    """
    Prophet으로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_prophet')
    표준 분기말 날짜 적용
    """
    y, m, _ = _prepare_series(final_combined_data)

    last_actual_date = y.index[-1].strftime('%Y-%m-%d')
    fc_index = get_standard_quarter_dates(last_actual_date, horizon)

    out = forecast_prophet(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])

    return _to_df(out["forecast"], fc_index, "revenue_prophet")


def forecast_revenue_lstm(final_combined_data: pd.DataFrame,
                          horizon: int = 4,
                          lookback: int = 12,
                          epochs: int = 50,
                          batch_size: int = 16,
                          try_transforms: bool = True) -> pd.DataFrame:
    """
    LSTM으로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_lstm')
    표준 분기말 날짜 적용
    """
    y, _, _ = _prepare_series(final_combined_data)

    last_actual_date = y.index[-1].strftime('%Y-%m-%d')
    fc_index = get_standard_quarter_dates(last_actual_date, horizon)

    out = forecast_lstm(
        y=y, forecast_horizon=horizon,
        lookback=lookback, epochs=epochs, batch_size=batch_size,
        try_transforms=try_transforms
    )
    if "error" in out:
        raise RuntimeError(out["error"])

    return _to_df(out["forecast"], fc_index, "revenue_lstm")


def forecast_revenue_theta(final_combined_data: pd.DataFrame,
                           horizon: int = 4,
                           try_transforms: bool = True) -> pd.DataFrame:
    """
    Theta로 매출 예측 → DataFrame 반환 (컬럼: 'revenue_theta')
    표준 분기말 날짜 적용
    """
    y, m, _ = _prepare_series(final_combined_data)

    last_actual_date = y.index[-1].strftime('%Y-%m-%d')
    fc_index = get_standard_quarter_dates(last_actual_date, horizon)

    out = forecast_theta(y=y, forecast_horizon=horizon, m=m, try_transforms=try_transforms)
    if "error" in out:
        raise RuntimeError(out["error"])

    return _to_df(out["forecast"], fc_index, "revenue_theta")


# ===== 선택: 한 번에 네 모델 돌리고 합치기 (기존 호환성) =====

def forecast_revenue_all_models_dict(final_combined_data: pd.DataFrame,
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


# ===== 새로운 통합 함수 (파이프라인용) =====

def forecast_revenue_all_models(
        revenue_df: pd.DataFrame,
        horizon: int = 6,
        forecast_start_date: str = None,
        exog_df: Optional[pd.DataFrame] = None,
        exog_future_strategy: str = "repeat_last",
        sarima_grid_kwargs: Optional[dict] = None,
        lstm_kwargs: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Revenue 예측 (모든 모델) - 표준 분기말 날짜 적용

    Parameters:
    -----------
    revenue_df : pd.DataFrame
        실제 매출 데이터
    horizon : int
        예측 분기 수
    forecast_start_date : str, optional
        예측 시작 날짜 수동 지정 (YYYY-MM-DD, 예: '2025-12-31')
        None이면 마지막 실제 데이터의 다음 분기부터 자동 생성

    Returns:
    --------
    pd.DataFrame: 표준 분기말 날짜가 적용된 예측 결과
        - date: 분기말 (3/31, 6/30, 9/30, 12/31)
        - revenue_sarima, revenue_ets, revenue_theta, revenue_lstm, revenue_prophet
    """
    if sarima_grid_kwargs is None:
        sarima_grid_kwargs = {}
    if lstm_kwargs is None:
        lstm_kwargs = {}

    # 입력 데이터 표준화
    revenue_df = ensure_datetime_index_df(revenue_df)
    revenue_df = standardize_dataframe_dates(revenue_df, freq='Q')

    y = revenue_df.iloc[:, 0].astype(float).dropna()
    if y.empty:
        raise ValueError("매출 시계열이 비어 있습니다")

    # 마지막 실제 데이터 날짜
    last_actual_date = y.index[-1].strftime('%Y-%m-%d')

    # 표준 분기말 날짜 생성 (수동 지정 또는 자동)
    standard_dates = get_standard_quarter_dates(
        last_actual_date,
        horizon,
        forecast_start_date=forecast_start_date
    )

    if forecast_start_date:
        print(f"  └─ 예측 시작일 (수동 지정): {forecast_start_date}")

    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)

    # 각 모델 예측
    print(f"  - SARIMA 예측 중...")
    sarima_res = forecast_sarima(
        y=y, forecast_horizon=horizon, seasonal_period=m, **sarima_grid_kwargs
    )
    sarima_fc = sarima_res.get("forecast", np.full(horizon, np.nan))

    print(f"  - ETS 예측 중... (freq={freq_alias}, m={m})")
    ets_res = forecast_ets(y=y, forecast_horizon=horizon, m=m)
    ets_fc = ets_res.get("forecast", np.full(horizon, np.nan))
    print(f"  └─ ETS 예측 범위: {standard_dates[0].strftime('%Y-%m-%d')} ~ {standard_dates[-1].strftime('%Y-%m-%d')}")

    print(f"  - Theta 예측 중... (freq={freq_alias}, m={m})")
    theta_res = forecast_theta(y=y, forecast_horizon=horizon, m=m)
    theta_fc = theta_res.get("forecast", np.full(horizon, np.nan))

    lstm_res = forecast_lstm(y=y, forecast_horizon=horizon, **lstm_kwargs)
    lstm_fc = lstm_res.get("forecast", np.full(horizon, np.nan))

    prophet_res = forecast_prophet(y=y, forecast_horizon=horizon, m=m)
    prophet_fc = prophet_res.get("forecast", np.full(horizon, np.nan))

    # 표준 날짜로 결과 집계
    result = pd.DataFrame({
        "revenue_sarima": sarima_fc,
        "revenue_ets": ets_fc,
        "revenue_theta": theta_fc,
        "revenue_lstm": lstm_fc,
        "revenue_prophet": prophet_fc,
    }, index=standard_dates)

    result.index.name = "date"

    return result


def compute_ttm(quarterly_df: pd.DataFrame, value_col: str) -> pd.Series:
    """
    분기 데이터 → TTM(Trailing Twelve Months) 변환
    표준 분기말 날짜 유지
    """
    df = quarterly_df.copy()
    df = standardize_dataframe_dates(df, freq='Q')

    if value_col not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            series = df.iloc[:, 0]
        else:
            raise KeyError(f"'{value_col}' 컬럼을 찾을 수 없습니다")
    else:
        series = df[value_col]

    ttm = series.rolling(window=4, min_periods=1).sum()
    ttm.name = f"{value_col}_ttm"

    return ttm


def merge_actual_and_forecast(
        actual_df: pd.DataFrame,
        forecast_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    실제 데이터 + 예측 데이터 병합 (표준 분기말 날짜)
    """
    # 두 데이터 모두 분기말로 표준화
    actual_df = standardize_dataframe_dates(actual_df, freq='Q')
    forecast_df = standardize_dataframe_dates(forecast_df, freq='Q')

    # 인덱스 기반 병합
    if isinstance(actual_df.index, pd.DatetimeIndex):
        actual_reset = actual_df.reset_index()
    else:
        actual_reset = actual_df.copy()
        if 'date' not in actual_reset.columns:
            actual_reset = actual_reset.reset_index()
            actual_reset.columns = ['date'] + list(actual_reset.columns[1:])

    if isinstance(forecast_df.index, pd.DatetimeIndex):
        forecast_reset = forecast_df.reset_index()
    else:
        forecast_reset = forecast_df.copy()
        if 'date' not in forecast_reset.columns:
            forecast_reset = forecast_reset.reset_index()
            forecast_reset.columns = ['date'] + list(forecast_reset.columns[1:])

    # 날짜 표준화
    actual_reset['date'] = pd.to_datetime(actual_reset['date']).dt.to_period('Q').dt.to_timestamp('Q')
    forecast_reset['date'] = pd.to_datetime(forecast_reset['date']).dt.to_period('Q').dt.to_timestamp('Q')

    merged = pd.concat([actual_reset, forecast_reset], axis=0, ignore_index=True)
    merged = merged.drop_duplicates(subset=['date'], keep='last')
    merged = merged.sort_values('date').reset_index(drop=True)

    return merged


def run_revenue_forecast_pipeline(
        actual_revenue_df: pd.DataFrame,
        horizon: int = 6,
        forecast_start_date: str = None,
        exog_df: Optional[pd.DataFrame] = None,
        sarima_grid_kwargs: Optional[dict] = None,
        lstm_kwargs: Optional[dict] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Revenue 예측 파이프라인 (표준 날짜 적용)

    Parameters:
    -----------
    forecast_start_date : str, optional
        예측 시작 날짜 수동 지정 (YYYY-MM-DD)

    Returns:
    --------
    fc_table : pd.DataFrame
        예측값만 (표준 분기말 날짜)
    rev_final : pd.DataFrame
        실제값 + 예측값 + TTM (표준 분기말 날짜)
    """
    # 실제 데이터 표준화
    actual_revenue_df = standardize_dataframe_dates(actual_revenue_df, freq='Q')

    # 예측 수행 (표준 날짜로 반환)
    fc_table = forecast_revenue_all_models(
        revenue_df=actual_revenue_df,
        horizon=horizon,
        forecast_start_date=forecast_start_date,
        exog_df=exog_df,
        sarima_grid_kwargs=sarima_grid_kwargs,
        lstm_kwargs=lstm_kwargs,
    )

    # 실제 + 예측 병합
    rev_combined = merge_actual_and_forecast(actual_revenue_df, fc_table)

    # 각 예측 모델에 대해 TTM 계산
    rev_final = rev_combined.copy()

    for col in fc_table.columns:
        if col in rev_final.columns:
            ttm_series = compute_ttm(rev_final, col)
            rev_final[f"{col}_ttm"] = ttm_series

    # 원본 매출 데이터도 있으면 TTM 계산
    original_col = actual_revenue_df.columns[0] if len(actual_revenue_df.columns) > 0 else None
    if original_col and original_col in rev_final.columns:
        ttm_series = compute_ttm(rev_final, original_col)
        rev_final[f"{original_col}_ttm"] = ttm_series

    # 최종 날짜 표준화 확인
    rev_final = standardize_dataframe_dates(rev_final, freq='Q')
    fc_table = standardize_dataframe_dates(fc_table, freq='Q')

    return fc_table, rev_final