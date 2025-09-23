#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exponential Smoothing 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈

- 외생변수 없음
- 예측 시작일(start_date) 지정 가능
- 매출: 분기마다 1번만 예측, 두 달은 ffill(limit=2)
- PSR: 월별 그대로 13개월 예측
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from typing import Optional, Union
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# =========================================================
# 유틸
# =========================================================

def to_month_end(s: Union[pd.Series, pd.DatetimeIndex, str, pd.Timestamp]) -> Union[pd.Series, pd.Timestamp]:
    ts = pd.to_datetime(s)
    if isinstance(ts, pd.Timestamp):
        return ts + pd.offsets.MonthEnd(0)
    return ts + pd.offsets.MonthEnd(0)

def ensure_sorted_unique_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['date_month_end'] = to_month_end(df['date_month_end'])
    df = df.sort_values('date_month_end').drop_duplicates(subset=['date_month_end']).reset_index(drop=True)
    return df

def month_offset(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return (a.year - b.year) * 12 + (a.month - b.month)

# =========================================================
# 메인 실행 함수
# =========================================================

def run_es_prediction_v1(df: pd.DataFrame,
                         ticker: str = 'UNKNOWN',
                         prediction_quarters: int = 4,
                         start_date_revenue: Optional[Union[str, pd.Timestamp]] = None,
                         start_date_psr: Optional[Union[str, pd.Timestamp]] = None
                         ) -> tuple[pd.DataFrame, dict]:
    """
    Exponential Smoothing 기반 예측 실행 (Revenue/PSR 시작일 분리)

    Parameters
    ----------
    df : pd.DataFrame
        'date_month_end', 'revenue_billions', 'PSR_ttm' 포함
    ticker : str
        종목명
    prediction_quarters : int
        매출 예측할 분기 수 (기본 4 → 12개월)
    start_date_revenue : str or pd.Timestamp, optional
        매출 예측 시작 기준 날짜 (월말로 정규화됨). None이면 데이터 마지막 이후부터 시작
    start_date_psr : str or pd.Timestamp, optional
        PSR 예측 시작 기준 날짜 (월말로 정규화됨). None이면 데이터 마지막 이후부터 시작
    """

    df = ensure_sorted_unique_dates(df)
    out_df = df.copy()
    results = {'meta': {'ticker': ticker}, 'revenue': None, 'psr': None}

    # --------------------------
    # 매출 (분기별 예측)
    # --------------------------
    if 'revenue_billions' in df.columns and df['revenue_billions'].notna().sum() > 6:
        series = pd.Series(df['revenue_billions'].values,
                           index=pd.to_datetime(df['date_month_end'])).dropna()

        model = ExponentialSmoothing(series, trend="add", seasonal=None)
        fit = model.fit()

        start_ts_rev = to_month_end(start_date_revenue) if start_date_revenue is not None else series.index.max()
        total_periods = prediction_quarters * 3

        future_dates_rev = pd.date_range(start=start_ts_rev + pd.offsets.MonthEnd(1),
                                         periods=total_periods, freq='M')
        forecast_rev = fit.forecast(total_periods)
        forecast_rev.index = future_dates_rev

        # 블록(3개월)별 마지막 달만 사용
        keep_idx = [dt for i, dt in enumerate(forecast_rev.index) if i % 3 == 2]
        quarterly_series = forecast_rev.loc[keep_idx]

        col_rev = "revenue_billions_es_forecast"
        # 미래 행 추가(없으면)
        out_df = (pd.merge(out_df, pd.DataFrame({'date_month_end': forecast_rev.index}),
                           on='date_month_end', how='outer')
                    .sort_values('date_month_end').reset_index(drop=True))

        mask = out_df['date_month_end'].isin(quarterly_series.index)
        out_df.loc[mask, col_rev] = out_df.loc[mask, 'date_month_end'].map(quarterly_series)

        # 분기 내 2개월은 앞 값으로 채움
        out_df[col_rev] = out_df[col_rev].ffill(limit=2)

        results['revenue'] = quarterly_series

    # --------------------------
    # PSR (월별 13개월 예측)
    # --------------------------
    if 'PSR_ttm' in df.columns and df['PSR_ttm'].notna().sum() > 6:
        series = pd.Series(df['PSR_ttm'].values,
                           index=pd.to_datetime(df['date_month_end'])).dropna()

        model = ExponentialSmoothing(series, trend="add", seasonal=None)
        fit = model.fit()

        total_periods_psr = 13
        start_ts_psr = to_month_end(start_date_psr) if start_date_psr is not None else series.index.max()

        future_dates_psr = pd.date_range(start=start_ts_psr + pd.offsets.MonthEnd(1),
                                         periods=total_periods_psr, freq='M')
        forecast_psr = fit.forecast(total_periods_psr)
        forecast_psr.index = future_dates_psr

        col_psr = "PSR_es_forecast"
        # 미래 행 추가(없으면)
        out_df = (pd.merge(out_df, pd.DataFrame({'date_month_end': forecast_psr.index}),
                           on='date_month_end', how='outer')
                    .sort_values('date_month_end').reset_index(drop=True))

        mask = out_df['date_month_end'].isin(forecast_psr.index)
        out_df.loc[mask, col_psr] = out_df.loc[mask, 'date_month_end'].map(forecast_psr)

        results['psr'] = forecast_psr

    # ====================================================
    # 과거 구간 NaN → 실제값으로 보정 (날짜 기준 결합)
    # ====================================================
    if 'revenue_billions' in out_df.columns and 'revenue_billions_es_forecast' in out_df.columns:
        # 예측 칼럼의 NaN을 같은 날짜의 실제 revenue_billions로 메움
        out_df['revenue_billions_es_forecast'] = out_df['revenue_billions_es_forecast']\
            .combine_first(out_df['revenue_billions'])

    if 'PSR_ttm' in out_df.columns and 'PSR_es_forecast' in out_df.columns:
        # 예측 칼럼의 NaN을 같은 날짜의 실제 PSR_ttm으로 메움
        out_df['PSR_es_forecast'] = out_df['PSR_es_forecast']\
            .combine_first(out_df['PSR_ttm'])

    return out_df, results



if __name__ == '__main__':
    print("us_es_forecast_v1.py loaded. Use run_es_prediction_v1(...)")
