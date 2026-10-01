#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prophet 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈 (v2)

- 외생변수 사용 / 미사용 두 갈래 예측
- 외생변수 None → exog 예측 생략
- 예측 시작일(start_date) 지정 가능
- 최대 4개 결과 컬럼 생성:
  revenue_billions_prophet_forecast_noexog
  PSR_prophet_forecast_noexog
  revenue_billions_prophet_forecast_exog
  PSR_prophet_forecast_exog
"""

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from prophet import Prophet
# 상단 import에 추가 (Python 3.9 호환)
from typing import Optional, Union
# =========================================================
# 유틸
# =========================================================

def month_diff(a: pd.Timestamp, b: pd.Timestamp) -> int:
    """a(나중) - b(이전) 의 월 차이"""
    a = pd.to_datetime(a); b = pd.to_datetime(b)
    return (a.year - b.year) * 12 + (a.month - b.month)

def to_month_end(s: Union[pd.Series, pd.DatetimeIndex, str, pd.Timestamp]) -> Union[pd.Series, pd.Timestamp]:
    ts = pd.to_datetime(s)
    if isinstance(ts, pd.Timestamp):
        return ts + pd.offsets.MonthEnd(0)
    return ts + pd.offsets.MonthEnd(0)

# =========================================================
# Prophet 헬퍼
# =========================================================

class ProphetPredictor:
    """Prophet 학습 및 예측을 간단히 수행하는 헬퍼"""

    def __init__(self, use_exogenous: bool = False):
        self.use_exogenous = use_exogenous
        self.model: Optional[Prophet] = None
        self.forecast: Optional[pd.DataFrame] = None

    def prepare_data(self, df: pd.DataFrame, target_col: str, exog_col: Optional[str] = None) -> pd.DataFrame:
        """Prophet 입력 형식으로 변환"""
        data = df[['date_month_end', target_col]].dropna().copy()
        data.columns = ['ds', 'y']

        if self.use_exogenous and exog_col and exog_col in df.columns:
            exog = df[['date_month_end', exog_col]].dropna().copy()
            exog.columns = ['ds', exog_col]
            data = pd.merge(data, exog, on='ds', how='left')

        return data

    def create_future_exog(self, history: pd.DataFrame, exog_col: str, periods: int) -> pd.DataFrame:
        """외생변수 미래값 생성 (최근 12개월 평균 채움)"""
        if not self.use_exogenous or exog_col not in history.columns:
            return None

        recent_mean = history[exog_col].tail(12).mean()
        last_date = history['ds'].max()
        future_dates = pd.date_range(start=last_date + pd.offsets.MonthEnd(1), periods=periods, freq='M')

        return pd.DataFrame({'ds': future_dates, exog_col: [recent_mean] * periods})

    def fit_and_predict(self,
                        df: pd.DataFrame,
                        target_col: str,
                        exog_col: Optional[str] = None,
                        periods: int = 13,
                        start_date: Optional[Union[str, pd.Timestamp]] = None
                        ) -> tuple[Optional[Prophet], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """모델 학습 및 예측 수행 (start_date 반영)"""
        data = self.prepare_data(df, target_col, exog_col)
        if len(data) < 12:
            return None, None, None

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

        # ---- start_date 처리 ----
        last_train = pd.to_datetime(data['ds'].max())
        start_ts = last_train if start_date is None else to_month_end(start_date)
        gap = month_diff(start_ts, last_train) if start_ts > last_train else 0
        total_periods = gap + periods

        future = model.make_future_dataframe(periods=total_periods, freq='M')

        if self.use_exogenous and exog_col and exog_col in data.columns:
            # 과거 exog 병합
            hist_exog = data[['ds', exog_col]]
            future = pd.merge(future, hist_exog, on='ds', how='left')
            # 미래 exog 채움(최근 12개월 평균)
            recent_mean = data[exog_col].tail(12).mean()
            mask_future = future['ds'] > data['ds'].max()
            future.loc[mask_future, exog_col] = future.loc[mask_future, exog_col].fillna(recent_mean)
            future[exog_col] = future[exog_col].ffill().bfill()

        forecast = model.predict(future)
        # start_date 이후 '정확히 periods개월'만 슬라이스
        forecast_slice = forecast[forecast['ds'] > start_ts].head(periods).copy()

        self.model = model
        self.forecast = forecast
        return model, forecast_slice, data


# =========================================================
# 메인 실행 함수
# =========================================================

# =========================================================
# 메인 실행 함수 (업그레이드 버전)
# =========================================================

def run_prophet_prediction_v2(df: pd.DataFrame,
                              ticker: str = 'UNKNOWN',
                              prediction_quarters: int = 4,
                              start_date: Optional[Union[str, pd.Timestamp]] = None,
                              exog_col: Optional[str] = 'expDlr_yoy') -> tuple[pd.DataFrame, dict]:

    df = ensure_sorted_unique_dates(df)
    periods = prediction_quarters * 3
    targets = ['revenue_billions', 'PSR_ttm']
    results = {'without_exog': {}, 'with_exog': {}, 'meta': {'ticker': ticker}}

    # --- 외생변수 미사용 ---
    for tgt in targets:
        predictor = ProphetPredictor(use_exogenous=False)
        _, fc, _ = predictor.fit_and_predict(df, tgt, exog_col=None, periods=periods, start_date=start_date)
        if fc is not None:
            results['without_exog'][tgt] = fc[['ds', 'yhat']].copy()

    # --- 외생변수 사용 ---
    if exog_col is not None and exog_col in df.columns:
        for tgt in targets:
            predictor = ProphetPredictor(use_exogenous=True)
            _, fc, _ = predictor.fit_and_predict(df, tgt, exog_col=exog_col, periods=periods, start_date=start_date)
            if fc is not None:
                results['with_exog'][tgt] = fc[['ds', 'yhat']].copy()

    out_df = df.copy()

    # 기본 컬럼 (과거=실제)
    if 'revenue_billions' in out_df.columns:
        out_df['revenue_billions_prophet_forecast_noexog'] = out_df['revenue_billions']
        if exog_col is not None:
            out_df['revenue_billions_prophet_forecast_exog'] = out_df['revenue_billions']
    if 'PSR_ttm' in out_df.columns:
        out_df['PSR_prophet_forecast_noexog'] = out_df['PSR_ttm']
        if exog_col is not None:
            out_df['PSR_prophet_forecast_exog'] = out_df['PSR_ttm']

    # start_date 기준 월 오프셋 계산 함수
    def month_offset(a: pd.Timestamp, b: pd.Timestamp) -> int:
        return (a.year - b.year) * 12 + (a.month - b.month)

    # 미래 예측값 반영
    def apply_series(series: pd.Series, colname: str, is_revenue: bool):
        nonlocal out_df
        # 미래 날짜 행 추가
        add = pd.DataFrame({'date_month_end': series.index})
        out_df = (
            pd.merge(out_df, add, on='date_month_end', how='outer')
              .sort_values('date_month_end')
              .reset_index(drop=True)
        )

        if is_revenue:
            # ---- 분기별 처리 ----
            base_start = to_month_end(start_date) if start_date is not None else out_df['date_month_end'].max()
            keep_idx = []
            for dt in series.index:
                off = month_offset(dt, base_start)
                if off >= 0 and (off % 3 == 2):  # 블록의 3번째 달만 선택
                    keep_idx.append(dt)
            quarterly_series = series.loc[keep_idx]

            mask = out_df['date_month_end'].isin(quarterly_series.index)
            out_df.loc[mask, colname] = out_df.loc[mask, 'date_month_end'].map(quarterly_series)

            # ffill(limit=2) 적용 → 두 달까지 복사
            out_df[colname] = out_df[colname].ffill(limit=2)

        else:
            # ---- PSR: 월별 그대로 ----
            mask = out_df['date_month_end'].isin(series.index)
            out_df.loc[mask, colname] = out_df.loc[mask, 'date_month_end'].map(series)

    # no-exog 예측 반영
    for tgt in targets:
        if tgt in results['without_exog']:
            s = results['without_exog'][tgt]
            s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
            col = f"{tgt}_prophet_forecast_noexog"
            apply_series(s, col, is_revenue=(tgt == 'revenue_billions'))

    # with-exog 예측 반영
    if exog_col is not None:
        for tgt in targets:
            if tgt in results['with_exog']:
                s = results['with_exog'][tgt]
                s = pd.Series(s['yhat'].values, index=pd.to_datetime(s['ds']))
                col = f"{tgt}_prophet_forecast_exog"
                apply_series(s, col, is_revenue=(tgt == 'revenue_billions'))

    return out_df, results




if __name__ == '__main__':
    print("us_prophet_forecast_v2.py loaded. Use run_prophet_prediction_v2(...)")
