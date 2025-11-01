# -*- coding: utf-8 -*-

import pandas as pd
import gc
from utils import log
from analysis import make_growth_summaries, to_long
from db_operations import (ensure_valuation_table, ensure_revenue_forecast_table,
                           upsert_long_to_db_on_ticker_created_at, upsert_revenue_forecast_to_db)

def calculate_valuation(revenue_forecast_ttm, psr_forecast_df, ticker):
    """밸류에이션 계산"""
    valuation_df = revenue_forecast_ttm.join(psr_forecast_df, how='inner')
    valuation_df['ticker'] = ticker

    valuation_filled = valuation_df.copy()
    cols_to_ffill = ['ticker'] + [c for c in valuation_filled.columns if 'revenue_billions' in c]
    valuation_filled[cols_to_ffill] = valuation_filled[cols_to_ffill].ffill(limit=2)

    valuation_filled['sarima_valuation'] = valuation_filled['revenue_billions_sarima_noexog_ttm'] * \
                                           valuation_filled['PSR_ttm_sarima_forecast']
    valuation_filled['lstm_valuation'] = valuation_filled['revenue_billions_lstm_forecast_ttm'] * \
                                         valuation_filled['PSR_ttm_lstm_forecast']
    valuation_filled['prophet_valuation'] = valuation_filled['revenue_billions_prophet_forecast_ttm'] * \
                                            valuation_filled['PSR_prophet_forecast_noexog']
    valuation_filled['es_valuation'] = valuation_filled['revenue_billions_esq_forecast_ttm'] * valuation_filled[
        'PSR_es_forecast']

    valuation_filled = valuation_filled.sort_index()

    valuation_result = (
        valuation_filled
        .groupby('ticker', group_keys=False)
        .apply(lambda d: d.tail(15))
        .reset_index()
        .rename(columns={'index': 'date_month_end'})
    )

    return valuation_result

def flush_batch_and_upload(batch_results, batch_revenue_results, db_info, total_upsert_rows, total_revenue_rows):
    """
    배치 데이터를 DB에 업로드
    Returns: (updated_total_upsert_rows, updated_total_revenue_rows)
    """
    if batch_results:
        ensure_valuation_table(db_info, table_name="us_valuation_result")

        try:
            final_df = pd.concat(batch_results, axis=0, ignore_index=True)
            rev_summary_batch, val_summary_batch = make_growth_summaries(final_df)

            long_val = to_long(val_summary_batch, category='valuation')

            long_rev = to_long(rev_summary_batch, category='revenue')
            model_map = {
                'revenue_billions_sarima_noexog_ttm': 'revenue_sarima',
                'revenue_billions_lstm_forecast_ttm': 'revenue_lstm',
                'revenue_billions_prophet_forecast_ttm': 'revenue_prophet',
                'revenue_billions_esq_forecast_ttm': 'revenue_es',
                'revenue_billions_avg_of_4_ttm': 'revenue_avg_of_4'
            }
            if 'model' in long_rev.columns:
                long_rev['model'] = long_rev['model'].replace(model_map)

            final_long = pd.concat([long_val, long_rev], axis=0, ignore_index=True)

            affected = upsert_long_to_db_on_ticker_created_at(final_long, db_info, table_name="us_valuation_result")
            total_upsert_rows += int(affected or 0)
            log("BATCH-UPLOADED", f"valuation rows={affected}, total={total_upsert_rows}")
        except Exception as e:
            log("BATCH-VAL-ERR", f"valuation upload failed: {e}")
        finally:
            del batch_results[:]
            gc.collect()

    if batch_revenue_results:
        ensure_revenue_forecast_table(db_info, table_name="us_revenue_forecast_result")

        try:
            tickers_in_batch = [df['ticker'].iloc[0] if 'ticker' in df.columns and len(df) > 0 else 'UNKNOWN'
                                for df in batch_revenue_results]
            log("BATCH-REV-CHECK", f"revenue batch contains tickers: {tickers_in_batch}")

            revenue_df = pd.concat(batch_revenue_results, axis=0, ignore_index=True)

            for col in revenue_df.columns:
                if pd.api.types.is_datetime64_any_dtype(revenue_df[col]):
                    if hasattr(revenue_df[col].dtype, 'tz') and revenue_df[col].dtype.tz is not None:
                        revenue_df[col] = revenue_df[col].dt.tz_localize(None)

            dup_check = revenue_df.groupby(['ticker', 'date_month_end']).size()
            if (dup_check > 1).any():
                log("BATCH-REV-DUP", f"Found duplicates in batch:\n{dup_check[dup_check > 1]}")

            affected = upsert_revenue_forecast_to_db(revenue_df, db_info, table_name="us_revenue_forecast_result")
            total_revenue_rows += int(affected or 0)
            log("BATCH-UPLOADED", f"revenue forecast rows={affected}, total={total_revenue_rows}")
        except Exception as e:
            log("BATCH-REV-ERR", f"revenue upload failed: {e}")
            import traceback
            log("BATCH-REV-TB", traceback.format_exc())
        finally:
            del batch_revenue_results[:]
            gc.collect()

    return total_upsert_rows, total_revenue_rows
