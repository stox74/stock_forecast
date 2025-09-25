#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prophet 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈 (v3)

변경 사항:
1) 매출과 PSR의 예측 시작일을 분리 (start_date_revenue, start_date_psr)
2) 매출은 분기별 마지막 달만 예측, 두 달은 ffill(limit=2)로 채움
3) PSR은 월별 그대로 예측
4) PSR 관련 exog/noexog 컬럼을 상하 결합하여 NaN 최소화
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from typing import Optional, Union
from prophet import Prophet


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
# Prophet Predictor
# =========================================================

class ProphetPredictor:
    def __init__(self, use_exogenous: bool = False):
        self.use_exogenous = use_exogenous
        self.model = None
        self.forecast = None

    def prepare_data(self, df: pd.DataFrame, target_col: str, exog_col: Optional[str] = None) -> pd.DataFrame:
        data = df[['date_month_end', target_col]].dropna().copy()
        data.columns = ['ds', 'y']
        if self.use_exogenous and exog_col and exog_col in df.columns:
            exog = df[['date_month_end', exog_col]].dropna().copy()
            exog.columns = ['ds', exog_col]
            data = pd.merge(data, exog, on='ds', how='left')
        return data

    def fit_and_predict(self, df: pd.DataFrame, target_col: str,
                        periods: int, start_date: Optional[Union[str, pd.Timestamp]],
                        exog_col: Optional[str] = None) -> Optional[pd.DataFrame]:
        data = self.prepare_data(df, target_col, exog_col)
        if len(data) < 6:
            return None

        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode='multiplicative',
            changepoint_prior_scale=0.05
        )
        if self.use_exogenous and exog_col and exog_col in data.columns:
            model.add_regressor(exog_col)

        model.fit(data)

        last_train = pd.to_datetime(data['ds'].max())
        start_ts = last_train if start_date is None else to_month_end(start_date)
        gap = month_offset(start_ts, last_train) if start_ts > last_train else 0
        total_periods = gap + periods

        future = model.make_future_dataframe(periods=total_periods, freq='M')
        if self.use_exogenous and exog_col and exog_col in data.columns:
            hist_exog = data[['ds', exog_col]]
            future = pd.merge(future, hist_exog, on='ds', how='left')
            recent_mean = data[exog_col].tail(12).mean()
            mask_future = future['ds'] > data['ds'].max()
            future.loc[mask_future, exog_col] = future.loc[mask_future, exog_col].fillna(recent_mean)
            future[exog_col] = future[exog_col].ffill().bfill()

        forecast = model.predict(future)
        forecast_slice = forecast[forecast['ds'] > start_ts].head(periods).copy()

        self.model = model
        self.forecast = forecast
        return forecast_slice


# =========================================================
# 메인 실행 함수
# =========================================================

def run_prophet_prediction_v3(df: pd.DataFrame,
                              ticker: str = 'UNKNOWN',
                              prediction_quarters: int = 4,
                              start_date_revenue: Optional[Union[str, pd.Timestamp]] = None,
                              start_date_psr: Optional[Union[str, pd.Timestamp]] = None,
                              exog_col: Optional[str] = 'expDlr_yoy') -> tuple[pd.DataFrame, dict]:

    df = ensure_sorted_unique_dates(df)
    out_df = df.copy()
    results = {'meta': {'ticker': ticker}, 'revenue': {}, 'psr': {}}

    # ------------------------
    # Revenue (분기별)
    # ------------------------
    predictor_noex = ProphetPredictor(use_exogenous=False)
    fc_rev_noex = predictor_noex.fit_and_predict(df, 'revenue_billions',
                                                 periods=prediction_quarters * 3,
                                                 start_date=start_date_revenue)
    if fc_rev_noex is not None:
        results['revenue']['noexog'] = fc_rev_noex[['ds', 'yhat']]

    if exog_col and exog_col in df.columns:
        predictor_ex = ProphetPredictor(use_exogenous=True)
        fc_rev_ex = predictor_ex.fit_and_predict(df, 'revenue_billions',
                                                 periods=prediction_quarters * 3,
                                                 start_date=start_date_revenue,
                                                 exog_col=exog_col)
        if fc_rev_ex is not None:
            results['revenue']['exog'] = fc_rev_ex[['ds', 'yhat']]

    # ------------------------
    # PSR (월별)
    # ------------------------
    predictor_noex = ProphetPredictor(use_exogenous=False)
    fc_psr_noex = predictor_noex.fit_and_predict(df, 'PSR_ttm',
                                                 periods=13,
                                                 start_date=start_date_psr)
    if fc_psr_noex is not None:
        results['psr']['noexog'] = fc_psr_noex[['ds', 'yhat']]

    if exog_col and exog_col in df.columns:
        predictor_ex = ProphetPredictor(use_exogenous=True)
        fc_psr_ex = predictor_ex.fit_and_predict(df, 'PSR_ttm',
                                                 periods=13,
                                                 start_date=start_date_psr,
                                                 exog_col=exog_col)
        if fc_psr_ex is not None:
            results['psr']['exog'] = fc_psr_ex[['ds', 'yhat']]

    # ------------------------
    # DataFrame에 예측 반영
    # ------------------------
    def ensure_rows_for(index_like):
        nonlocal out_df
        add = pd.DataFrame({'date_month_end': pd.to_datetime(index_like)})
        out_df = (pd.merge(out_df, add, on='date_month_end', how='outer')
                    .sort_values('date_month_end').reset_index(drop=True))

    # --- Revenue 반영 ---
    if 'noexog' in results['revenue']:
        s = results['revenue']['noexog']
        s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
        keep_idx = [dt for i, dt in enumerate(s.index) if i % 3 == 2]
        quarterly_series = s.loc[keep_idx]
        ensure_rows_for(quarterly_series.index)
        col = "revenue_billions_prophet_forecast_noexog"
        out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), 'date_month_end'].map(quarterly_series)
        out_df[col] = out_df[col].ffill(limit=2)

    if 'exog' in results['revenue']:
        s = results['revenue']['exog']
        s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
        keep_idx = [dt for i, dt in enumerate(s.index) if i % 3 == 2]
        quarterly_series = s.loc[keep_idx]
        ensure_rows_for(quarterly_series.index)
        col = "revenue_billions_prophet_forecast_exog"
        out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), 'date_month_end'].map(quarterly_series)
        out_df[col] = out_df[col].ffill(limit=2)

    # --- PSR 반영 ---
    if 'noexog' in results['psr']:
        s = results['psr']['noexog']
        s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
        ensure_rows_for(s.index)
        col = "PSR_prophet_forecast_noexog"
        out_df.loc[out_df['date_month_end'].isin(s.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(s.index), 'date_month_end'].map(s)

    if 'exog' in results['psr']:
        s = results['psr']['exog']
        s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
        ensure_rows_for(s.index)
        col = "PSR_prophet_forecast_exog"
        out_df.loc[out_df['date_month_end'].isin(s.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(s.index), 'date_month_end'].map(s)

    # ------------------------
    # PSR 컬럼 병합 (NaN 최소화)
    # ------------------------
    if 'PSR_prophet_forecast_exog' in out_df.columns and 'PSR_ttm_prophet_forecast_exog' in out_df.columns:
        out_df['PSR_prophet_forecast_exog'] = \
            out_df['PSR_prophet_forecast_exog'].combine_first(out_df['PSR_ttm_prophet_forecast_exog'])
        out_df.drop(columns=['PSR_ttm_prophet_forecast_exog'], inplace=True)

    if 'PSR_prophet_forecast_noexog' in out_df.columns and 'PSR_ttm_prophet_forecast_noexog' in out_df.columns:
        out_df['PSR_prophet_forecast_noexog'] = \
            out_df['PSR_prophet_forecast_noexog'].combine_first(out_df['PSR_ttm_prophet_forecast_noexog'])
        out_df.drop(columns=['PSR_ttm_prophet_forecast_noexog'], inplace=True)

    # ------------------------
    # 과거 구간 NaN → 실제값으로 보정
    # ------------------------
    # revenue: 예측 칼럼의 NaN을 과거 구간에서는 revenue_billions로 채움
    if 'revenue_billions' in out_df.columns:
        for col in ['revenue_billions_prophet_forecast_noexog',
                    'revenue_billions_prophet_forecast_exog']:
            if col in out_df.columns:
                out_df[col] = out_df[col].combine_first(out_df['revenue_billions'])

    # PSR: 예측 칼럼의 NaN을 과거 구간에서는 PSR_ttm으로 채움
    if 'PSR_ttm' in out_df.columns:
        for col in ['PSR_prophet_forecast_noexog',
                    'PSR_prophet_forecast_exog']:
            if col in out_df.columns:
                out_df[col] = out_df[col].combine_first(out_df['PSR_ttm'])

    return out_df, results

def run_prophet_revenue_only(df: pd.DataFrame,
                             ticker: str = 'UNKNOWN',
                             prediction_quarters: int = 4,
                             start_date: Optional[Union[str, pd.Timestamp]] = None
                             ) -> tuple[pd.DataFrame, dict]:
    """
    Prophet으로 revenue_billions만 예측(PSR/외생변수 제외).
    - 월별 예측 후 '각 분기 마지막 달'만 채우고 나머지 두 달은 ffill(limit=2)로 보간.
    - prediction_quarters = 4 (4분기) / 8 (8분기) 등.
    반환: (out_df, results)  / out_df는 date_month_end를 index로 정렬해서 반환.
    """
    df = ensure_sorted_unique_dates(df)
    out_df = df.copy()

    predictor = ProphetPredictor(use_exogenous=False)
    fc = predictor.fit_and_predict(
        df=df,
        target_col='revenue_billions',
        periods=prediction_quarters * 3,  # 분기 수 × 3개월
        start_date=start_date,
        exog_col=None
    )

    results = {'meta': {'ticker': ticker, 'target': 'revenue_billions'}, 'revenue': {}}

    if fc is not None and len(fc) > 0:
        # 월별(yhat) → 분기 마지막 달만 남기기 (i%3==2)
        s = pd.Series(fc['yhat'].values, index=pd.to_datetime(fc['ds']))
        keep_idx = [dt for i, dt in enumerate(s.index) if i % 3 == 2]
        quarterly_series = s.loc[keep_idx]

        # out_df에 예측 날짜 행 보강
        add = pd.DataFrame({'date_month_end': pd.to_datetime(quarterly_series.index)})
        out_df = (pd.merge(out_df, add, on='date_month_end', how='outer')
                    .sort_values('date_month_end')
                    .reset_index(drop=True))

        col = "revenue_billions_prophet_forecast"
        out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(quarterly_series.index), 'date_month_end'].map(quarterly_series)

        # 분기 마지막 달만 값이 있으므로 같은 분기의 앞 2개월을 ffill(limit=2)로 채움
        out_df[col] = out_df[col].ffill(limit=2)

        # 과거 구간은 실제값으로 채움
        if 'revenue_billions' in out_df.columns:
            out_df[col] = out_df[col].combine_first(out_df['revenue_billions'])

        results['revenue']['forecast'] = fc[['ds', 'yhat']]

    # 인덱스를 date_month_end로 설정하여 반환
    out_df = out_df.sort_values('date_month_end').set_index('date_month_end')
    return out_df, results


if __name__ == '__main__':
    print("us_prophet_forecast_v3.py loaded. Use run_prophet_prediction_v3(...)")
