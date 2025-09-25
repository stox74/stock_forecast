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
from pandas.tseries.offsets import MonthEnd
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

# === us_est_forecast_v2.py 에 추가 ===
def _to_month_end(x):
    x = pd.to_datetime(x)
    return x + MonthEnd(0)

def _ensure_sorted_unique(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date_month_end"] = _to_month_end(d["date_month_end"])
    d = d.sort_values("date_month_end").drop_duplicates("date_month_end").reset_index(drop=True)
    return d

def _filter_to_quarter_phase(df: pd.DataFrame) -> pd.DataFrame:
    """
    입력 데이터에서 가장 일관된 분기 페이즈(01/04/07/10 or 02/05/08/11 등)를 자동 추정해 그 달만 남깁니다.
    """
    d = _ensure_sorted_unique(df)
    months = d["date_month_end"].dt.month
    # month%3 의 최빈값(0,1,2 중 하나)을 페이즈로 선택
    phase = (months % 3).mode().iloc[0]
    d = d[months % 3 == phase].reset_index(drop=True)
    return d

def run_es_revenue_quarterly(df: pd.DataFrame,
                                 ticker: str = "UNKNOWN",
                                 prediction_quarters: int = 4,
                                 start_date: Optional[Union[str, pd.Timestamp]] = None
                                   ) -> tuple[pd.DataFrame, dict]:
    """
    Exponential Smoothing으로 'revenue_billions'를 '분기 단위'로만 예측.
    - 결과 인덱스는 분기 월말에 해당하는 행만 존재(월별 행 미생성).
    - 과거=실제값, 미래=예측값을 'revenue_billions_esq_forecast'에 기록.
    """
    if "revenue_billions" not in df.columns:
        raise ValueError("df에 'revenue_billions' 컬럼이 필요합니다.")

    # 1) 분기 페이즈만 추출 (예: 01/31, 04/30, 07/31, 10/31)
    qdf = _filter_to_quarter_phase(df[["date_month_end", "revenue_billions"]])
    if qdf["revenue_billions"].notna().sum() < 6:
        raise ValueError("분기 예측에는 최소 6개 이상의 유효 분기 데이터가 필요합니다.")

    q_series = pd.Series(qdf["revenue_billions"].values,
                         index=pd.to_datetime(qdf["date_month_end"])).dropna()

    # 2) ES 모델 (분기 레벨, 계절성 없음)
    model = ExponentialSmoothing(q_series, trend="add", seasonal=None, initialization_method="estimated")
    fit = model.fit(optimized=True)

    # 3) 미래 분기 인덱스 생성 (마지막 관측 분기와 동일한 페이즈로 +3개월씩)
    last_q = _to_month_end(start_date) if start_date is not None else q_series.index.max()
    future_quarters = [last_q + pd.DateOffset(months=3 * i) for i in range(1, int(prediction_quarters) + 1)]

    # 4) 분기 예측
    q_forecast = fit.forecast(int(prediction_quarters))
    q_forecast.index = pd.to_datetime(future_quarters)

    # 5) 출력: 분기 행만 존재하는 DataFrame 구축
    out = qdf.copy()  # 이미 분기 페이즈만 남은 상태
    out = out.sort_values("date_month_end").reset_index(drop=True)
    col = "revenue_billions_esq_forecast"
    out[col] = out["revenue_billions"]  # 과거 = 실제

    # 미래 분기 행만 추가하여 예측값 기록
    add = pd.DataFrame({"date_month_end": q_forecast.index, col: q_forecast.values})
    out = pd.concat([out, add], ignore_index=True)
    out = out.sort_values("date_month_end").set_index("date_month_end")

    results = {
        "meta": {"ticker": ticker, "target": "revenue_billions", "model": "ES-quarterly-strict"},
        "forecast": q_forecast
    }
    return out, results

if __name__ == '__main__':
    print("us_es_forecast_v1.py loaded. Use run_es_prediction_v1(...)")
