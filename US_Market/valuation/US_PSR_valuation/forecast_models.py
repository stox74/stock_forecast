# -*- coding: utf-8 -*-

import pandas as pd
from utils import log
import traceback

def run_revenue_forecasts(rev_data, ticker, sarima, lstm_v2, prophet_v3, esmod):
    """4개 모델로 매출 예측 실행"""
    try:
        periods = 4
        sarima_df, _ = sarima.run_sarima_prediction(rev_data, forecast_quarters=periods, exog_col=None)
        sarima_df = sarima_df.sort_values("date_month_end").set_index("date_month_end")
        lstm_raw_df, _ = lstm_v2.run_lstm_revenue_prediction(rev_data, ticker=ticker, prediction_quarters=4)
        lstm_df = lstm_raw_df.drop_duplicates(subset=['revenue_billions_lstm_forecast'], keep='last')
        prophet_raw_df, _ = prophet_v3.run_prophet_revenue_only(rev_data, ticker=ticker, prediction_quarters=4)
        es_raw_df, _ = esmod.run_es_revenue_quarterly(rev_data, ticker=ticker, prediction_quarters=4)
        log("OK-REV-FORECAST",
            f"{ticker} sarima={sarima_df.shape} lstm={lstm_df.shape} prophet={prophet_raw_df.shape} es={es_raw_df.shape}")
        return sarima_df, lstm_df, prophet_raw_df, es_raw_df, None
    except Exception as e:
        log("EXC-REV-FORECAST", f"{ticker} e={e}")
        return None, None, None, None, str(e)

def run_psr_forecasts(enhanced_merged_df_with_ttm, ticker, sarima, lstm_v2, prophet_v3, esmod):
    """4개 모델로 PSR 예측 실행"""
    try:
        psr_sarima_df, _ = sarima.run_sarima_psr_only(
            df=enhanced_merged_df_with_ttm,
            periods=12,
            target_col="PSR_ttm",
            analysis_start="2012-06-01",
            warmup_months=6,
            fill_method="interpolate",
            ic="aic"
        )
        psr_lstm_df, _ = lstm_v2.run_lstm_psr_prediction(enhanced_merged_df_with_ttm, ticker=ticker,
                                                         prediction_months=12)
        psr_prophet_df, _ = prophet_v3.run_prophet_psr_only(enhanced_merged_df_with_ttm, ticker=ticker,
                                                            prediction_months=12)
        psr_es_df, _ = esmod.run_es_psr_only(df=enhanced_merged_df_with_ttm, ticker=ticker, prediction_months=12,
                                             start_date=None)
        log("OK-PSR-FORECAST",
            f"{ticker} sarima={psr_sarima_df.shape} lstm={psr_lstm_df.shape} prophet={psr_prophet_df.shape} es={psr_es_df.shape}")
        return psr_sarima_df, psr_lstm_df, psr_prophet_df, psr_es_df, None
    except Exception as e:
        log("EXC-PSR-FORECAST", f"{ticker} e={e}")
        return None, None, None, None, str(e)

def pick_columns(df, cols):
    """데이터프레임에서 특정 컬럼 추출"""
    d = df.copy()
    if 'date_month_end' not in d.columns:
        d = d.reset_index()
        if 'date_month_end' not in d.columns and 'index' in d.columns:
            d = d.rename(columns={'index': 'date_month_end'})
    d = d.drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')
    return d[['date_month_end'] + cols].set_index('date_month_end')
