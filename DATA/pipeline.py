# -*- coding: utf-8 -*-
from typing import Dict, Tuple, List
import pandas as pd
import numpy as np
import traceback
from .config import START_DATE_MONTH, END_DATE_MONTH, log

def process_one_ticker(
    ticker: str,
    api_key: str,
    db_info: Dict[str,str],
    fx: Dict[str, object],
) -> Tuple[pd.DataFrame, pd.DataFrame, List[Dict]]:
    """
    fx: adapters.build_fx()로 받은 함수/모듈 dict
    """
    error_list: List[Dict] = []
    try:
        # 1) FMP 매출
        revenue_data, err = fx["fetch_revenue_data"](ticker, api_key)
        if revenue_data is None:
            raise RuntimeError(f"FMP revenue fetch failed: {err}")
        all_revenue_data = [{
            'ticker': ticker,
            'date': it.get('date', ''),
            'calendar_year': it.get('calendarYear', ''),
            'period': it.get('period', ''),
            'revenue': it.get('revenue', 0) if it.get('revenue') is not None else 0,
            'revenue_billions': round((it.get('revenue', 0) or 0) / 1_000_000_000, 2),
        } for it in revenue_data]
        fmp_rev = pd.DataFrame(all_revenue_data)
        fmp_rev['date'] = pd.to_datetime(fmp_rev['date'])
        fmp_rev['date_month_end'] = fx["to_month_end_safe"](fmp_rev['date'])
        fmp_rev = (fmp_rev.dropna(subset=['date_month_end'])
                           .drop_duplicates(subset=['date_month_end'])
                           .sort_values('date_month_end')
                           .reset_index(drop=True))

        # 2) DB 매출
        db_rev_raw = fx["fetch_db_revenue_data"](ticker, db_info)
        if db_rev_raw is not None and not db_rev_raw.empty:
            db_rev = db_rev_raw.loc[db_rev_raw['revenue_billions'] != db_rev_raw['revenue_billions'].shift()]
        else:
            db_rev = pd.DataFrame(columns=['ticker','date','date_month_end','revenue_billions'])

        merged_rev_raw = pd.merge(fmp_rev, db_rev, on=['ticker','date_month_end'], how='outer')
        rev = merged_rev_raw[merged_rev_raw['date_month_end'] >= START_DATE_MONTH]
        if 'revenue_billions_x' in rev.columns:
            rev['revenue_billions_x'] = rev['revenue_billions_x'].fillna(rev.get('revenue_billions_y'))
            rev = rev.rename(columns={'revenue_billions_x':'revenue_billions'})
        rev = fx["clean_rev_data"](rev)

        # 3) 매출 예측
        sarima_df, _ = fx["sarima"].run_sarima_prediction(rev, forecast_quarters=4, exog_col=None)
        sarima_df = sarima_df.sort_values('date_month_end').set_index('date_month_end')
        lstm_raw_df, _ = fx["lstm_v2"].run_lstm_revenue_prediction(rev, ticker=ticker, prediction_quarters=4)
        lstm_df = lstm_raw_df.drop_duplicates(subset=['revenue_billions_lstm_forecast'], keep='last')
        prophet_raw_df, _ = fx["prophet_v3"].run_prophet_revenue_only(rev, ticker=ticker, prediction_quarters=4)
        es_raw_df, _ = fx["esmod"].run_es_revenue_quarterly(rev, ticker=ticker, prediction_quarters=4)

        # 4) 시총
        market_data, _ = fx["fetch_market_data_yearly"](ticker, api_key, start_year=2010)
        fmp_mcap = fx["process_daily_to_monthly_market_data"](market_data, ticker).copy()
        fmp_mcap['date_month_end'] = fx["to_month_end_safe"](fmp_mcap['date'])
        fmp_mcap = (fmp_mcap.drop_duplicates(subset=['date_month_end'])
                             .sort_values('date_month_end')
                             .reset_index(drop=True))

        # 5) DB 시총 병합
        db_mcap = fx["_safe_get_db_market_df"](ticker, db_info)
        if (db_mcap is not None and not db_mcap.empty) and ('date_month_end' not in db_mcap.columns):
            if 'date' in db_mcap.columns:
                db_mcap['date_month_end'] = fx["to_month_end_safe"](db_mcap['date'])
            else:
                db_mcap = pd.DataFrame()
        if db_mcap is None or db_mcap.empty:
            merged_mcap = fmp_mcap.copy()
            merged_mcap['market_cap_billions_from_db'] = np.nan
        else:
            if 'market_cap_billions' in db_mcap.columns:
                db_mcap2 = db_mcap.rename(columns={'market_cap_billions':'market_cap_billions_from_db'})
            else:
                db_mcap2 = db_mcap[['date_month_end']].copy()
                db_mcap2['market_cap_billions_from_db'] = np.nan
            merged_mcap = fmp_mcap.merge(db_mcap2[['date_month_end','market_cap_billions_from_db']],
                                         on='date_month_end', how='left')
        if 'market_cap_billions' not in merged_mcap.columns:
            merged_mcap['market_cap_billions'] = np.nan
        if 'market_cap_billions_from_db' not in merged_mcap.columns:
            merged_mcap['market_cap_billions_from_db'] = np.nan
        merged_mcap['market_cap_billions'] = merged_mcap['market_cap_billions'].fillna(
            merged_mcap['market_cap_billions_from_db']
        )
        merged_mcap = (merged_mcap.drop_duplicates(subset=['date_month_end'])
                                 .sort_values('date_month_end')
                                 .reset_index(drop=True))

        # 6) PSR 계산
        enhanced = pd.merge(
            merged_mcap[['date_month_end','market_cap_billions']],
            rev, on='date_month_end', how='outer'
        )
        enhanced_resize = enhanced[['date_month_end','market_cap_billions','ticker','revenue_billions']].copy()
        enhanced_resize.dropna(subset=['market_cap_billions'], inplace=True)
        enhanced_resize.ffill(limit=2, inplace=True)
        enhanced_resize = enhanced_resize[
            (enhanced_resize['date_month_end'] >= START_DATE_MONTH) &
            (enhanced_resize['date_month_end'] <= END_DATE_MONTH)
        ].dropna(axis=0)

        enhanced_ttm = fx["calculate_enhanced_ttm_and_psr"](enhanced_resize)

        # 6-1) PSR 예측
        psr_sarima_df, _  = fx["sarima"].run_sarima_psr_only(enhanced_ttm, periods=12,
                           target_col='PSR_ttm', analysis_start='2012-06-01',
                           warmup_months=6, fill_method='interpolate', ic='aic')
        psr_lstm_df, _    = fx["lstm_v2"].run_lstm_psr_prediction(enhanced_ttm, ticker=ticker, prediction_months=12)
        psr_prophet_df, _ = fx["prophet_v3"].run_prophet_psr_only(enhanced_ttm, ticker=ticker, prediction_months=12)
        psr_es_df, _      = fx["esmod"].run_es_psr_only(enhanced_ttm, ticker=ticker, prediction_months=12, start_date=None)

        # 7) Valuation 종합
        def _pick(df, cols):
            d = df.copy()
            if 'date_month_end' not in d.columns:
                d = d.reset_index()
                if 'date_month_end' not in d.columns and 'index' in d.columns:
                    d = d.rename(columns={'index':'date_month_end'})
            d = d.drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')
            return d[['date_month_end'] + cols].set_index('date_month_end')

        rev_sarima  = _pick(sarima_df,      ['revenue_billions_sarima_noexog'])
        rev_lstm    = _pick(lstm_df,        ['revenue_billions_lstm_forecast'])
        rev_prophet = _pick(prophet_raw_df, ['revenue_billions_prophet_forecast'])
        rev_es      = _pick(es_raw_df,      ['revenue_billions_esq_forecast'])
        rev_wide = pd.concat([rev_sarima, rev_lstm, rev_prophet, rev_es], axis=1, join='outer').reset_index()
        if 'ticker' not in rev_wide.columns:
            rev_wide['ticker'] = ticker

        rev_ttm_all = fx["prepare_revenue_ttm"](rev_wide)
        rev_ttm = rev_ttm_all.filter(like='_ttm').copy()
        if 'date_month_end' not in rev_ttm_all.columns:
            rev_ttm['date_month_end'] = rev_wide['date_month_end'].values
        else:
            rev_ttm['date_month_end'] = rev_ttm_all['date_month_end'].values
        rev_ttm = rev_ttm.drop_duplicates(subset=['date_month_end']).set_index('date_month_end')
        r_cols = [
            'revenue_billions_sarima_noexog_ttm',
            'revenue_billions_lstm_forecast_ttm',
            'revenue_billions_prophet_forecast_ttm',
            'revenue_billions_esq_forecast_ttm'
        ]
        rev_ttm['revenue_billions_avg_of_4_ttm'] = rev_ttm[r_cols].mean(axis=1)

        psr_wide = pd.concat([
            _pick(psr_sarima_df,  ['PSR_ttm_sarima_forecast']),
            _pick(psr_lstm_df,    ['PSR_ttm_lstm_forecast']),
            _pick(psr_prophet_df, ['PSR_prophet_forecast_noexog']),
            _pick(psr_es_df,      ['PSR_es_forecast'])
        ], axis=1, join='outer')

        valuation = rev_ttm.join(psr_wide, how='inner')
        valuation['ticker'] = ticker
        cols_to_ffill = ['ticker'] + [c for c in valuation.columns if 'revenue_billions' in c]
        valuation[cols_to_ffill] = valuation[cols_to_ffill].ffill(limit=2)

        valuation['sarima_valuation']  = valuation['revenue_billions_sarima_noexog_ttm']  * valuation['PSR_ttm_sarima_forecast']
        valuation['lstm_valuation']    = valuation['revenue_billions_lstm_forecast_ttm']  * valuation['PSR_ttm_lstm_forecast']
        valuation['prophet_valuation'] = valuation['revenue_billions_prophet_forecast_ttm']* valuation['PSR_prophet_forecast_noexog']
        valuation['es_valuation']      = valuation['revenue_billions_esq_forecast_ttm']   * valuation['PSR_es_forecast']

        valuation = valuation.sort_index()
        valuation_result = (
            valuation.groupby('ticker', group_keys=False)
                     .apply(lambda d: d.tail(15))
                     .reset_index()
                     .rename(columns={'index':'date_month_end'})
        )

        enhanced_merged_df = enhanced_resize.copy()
        return enhanced_merged_df, valuation_result, error_list

    except Exception as e:
        log("EXC-TICKER", f"{ticker} e={e} tb={traceback.format_exc().splitlines()[-1]}")
        error_list.append({"ticker": ticker, "stage": "pipeline", "error": str(e)})
        return pd.DataFrame(), pd.DataFrame(), error_list
