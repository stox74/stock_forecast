# -*- coding: utf-8 -*-
"""
Korea valuation batch runner
"""

import gc
import traceback
import pandas as pd
from datetime import datetime
import sys, os
from typing import Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from DATA.stock_invest_function import *
from get_market_cap_by_ticker import get_market_cap_by_ticker
from get_fs_data_by_ticker import extract_quarterly_fs_data
from get_hscode_processed_data import get_hscode_processed_data
from get_monthly_export_data import extract_monthly_exog_var
from get_revenue_export_joined_table import get_revenue_export_joined_table
from sarima_endog_forecast import forecast_endog_with_optional_exog
from sarima_endog_forecast import forecast_endog_fill_tail
from get_forecasted_revenue_df import build_forecast_df_from_out
from revenue_forecast_all_package import *
from get_revenue_ttm_df import get_revenue_ttm_df
from get_psr_from_mc_and_rev import build_psr_series
from psr_forecast_runner import forecast_psr_all_models
from ticker_list import get_ticker_list
from upload_valuation_longform import upload_valuation_longform
from valuation_forecast import compute_valuation_forecast

db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

EXOG_Q_TRANSFORM = "pct"
ERROR_RECORDS = []


def _safe_prophet_forecast(final_combined_data, horizon: int = 5):
    try:
        return forecast_revenue_prophet(final_combined_data, horizon=horizon)
    except Exception:
        return None


def _get_exog_from_export(df_export: Optional[dict]) -> Optional[pd.DataFrame]:
    if isinstance(df_export, dict) and 'quarterly' in df_export:
        return df_export['quarterly']
    return None


def _transform_exog_col(df: pd.DataFrame, col: str, periods: int, method: str = "pct") -> pd.DataFrame:
    if df is None or col not in df.columns:
        return df
    out = df.copy()
    out = out.sort_index()
    if method == "pct":
        out[col] = out[col].pct_change(periods)
    elif method == "diff":
        out[col] = out[col].diff(periods)
    else:
        raise ValueError("method must be 'pct' or 'diff'")
    return out.dropna()


def _cleanup(*args):
    """메모리 즉시 정리"""
    for obj in args:
        try:
            del obj
        except:
            pass
    gc.collect()


def _validate_df_mc(df_mc: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """df_mc 컬럼명 검증 및 표준화"""
    if df_mc is None or df_mc.empty:
        raise ValueError(f"[{ticker}] df_mc is empty or None")

    # 'value' 컬럼이 없으면 다른 가능한 컬럼명 확인 (대소문자 무시)
    if 'value' not in df_mc.columns:
        col_mapping = {col.lower(): col for col in df_mc.columns}
        possible_names = ['marketcap', 'market_cap', 'cap', 'mc', 'value']

        for name in possible_names:
            if name in col_mapping:
                df_mc = df_mc.rename(columns={col_mapping[name]: 'value'})
                return df_mc

        raise KeyError(f"[{ticker}] df_mc columns={df_mc.columns.tolist()}, 'value' column not found")

    return df_mc


def process_single_ticker(
        ticker: str,
        hs_code: Optional[str],
        db_info: dict,
        table_name: str = "Korea_company_valuation_ver2",
        horizon_quarter: int = 5,
        psr_horizon_months: int = 13,
        forecast_date: Optional[str] = None,
        value_start_date: Optional[str] = None,
) -> bool:
    print(f"\n{'=' * 70}\n[{ticker}] 시작 (hs_code={hs_code})\n{'=' * 70}")

    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()

    try:
        # 1) 기본 데이터
        df_mc = get_market_cap_by_ticker(db_info, ticker)
        df_mc = _validate_df_mc(df_mc, ticker)

        df_rev = extract_quarterly_fs_data(db_info=db_info, table_name="korea_fs_data",
                                           target_indicator="매출액(천원)", ticker=ticker)

        # 2) 수출 exog (hs_code 있을 때만)
        exog_df = None
        df_export = None
        if hs_code:
            df_export = get_hscode_processed_data(db_info, hs_code=hs_code)
            exog_df = _get_exog_from_export(df_export)
            if isinstance(exog_df, pd.DataFrame) and 'exog_var' in exog_df.columns:
                exog_df = _transform_exog_col(df=exog_df, col='exog_var', periods=4, method=EXOG_Q_TRANSFORM)

        # 3) 결합 테이블
        combined_df, final_combined_data, forecast_df = get_revenue_export_joined_table(
            df_rev=df_rev, df_exog=exog_df, join_how="outer", fill_exog="ffill")
        final_combined_data = final_combined_data.loc['2015-01-01':]
        _cleanup(combined_df, forecast_df)

        # 4) Revenue forecasts
        out_noexog = forecast_endog_with_optional_exog(combined_df=final_combined_data, horizon=horizon_quarter,
                                                       hs_code=None)
        rev_sarima_noexog = build_forecast_df_from_out(out_noexog, combined_df=final_combined_data)
        _cleanup(out_noexog)

        rev_sarima_exog = None
        if hs_code:
            out_exog = forecast_endog_fill_tail(final_combined_data, hs_code=hs_code)
            rev_sarima_exog = build_forecast_df_from_out(out_exog, combined_df=final_combined_data)
            _cleanup(out_exog)

        rev_ets_df = forecast_revenue_ets(final_combined_data, horizon=horizon_quarter)
        rev_theta_df = forecast_revenue_theta(final_combined_data, horizon=horizon_quarter)
        rev_lstm_df = forecast_revenue_lstm(final_combined_data, horizon=horizon_quarter, lookback=12)
        rev_prophet_df = _safe_prophet_forecast(final_combined_data, horizon=horizon_quarter)
        _cleanup(final_combined_data)

        # 5) Revenue TTM 집계
        rev_final = get_revenue_ttm_df(df_rev=df_rev, rev_sarima_noexog=rev_sarima_noexog,
                                       rev_sarima_exog=rev_sarima_exog, rev_ets_df=rev_ets_df,
                                       rev_prophet_df=rev_prophet_df, rev_theta_df=rev_theta_df,
                                       rev_lstm_df=rev_lstm_df)
        _cleanup(rev_sarima_noexog, rev_sarima_exog, rev_ets_df, rev_theta_df, rev_lstm_df, rev_prophet_df)

        # 6) PSR 시계열 및 예측
        psr_df = build_psr_series(df_mc=df_mc, df_rev=df_rev)
        _cleanup(df_mc)

        exog_monthly_df = None
        if hs_code and df_export is not None and isinstance(df_export, dict):
            try:
                df_exog_monthly = extract_monthly_exog_var(df_export)
                if isinstance(df_exog_monthly, pd.DataFrame) and 'exog_var' in df_exog_monthly.columns:
                    exog_monthly_df = _transform_exog_col(df=df_exog_monthly[['exog_var']],
                                                          col='exog_var', periods=12, method="pct")
                _cleanup(df_exog_monthly)
            except Exception:
                pass
        _cleanup(df_export, exog_df)

        fc_table = forecast_psr_all_models(psr_df, horizon=psr_horizon_months, exog_df=exog_monthly_df)
        _cleanup(exog_monthly_df)

        # 7) Valuation 계산
        valuation_forecast_result = compute_valuation_forecast(fc_table=fc_table, rev_final=rev_final,
                                                               value_start_date=value_start_date)

        # 8) DB 업로드
        upload_valuation_longform(valuation_forecast_result=valuation_forecast_result, fc_table=fc_table,
                                  rev_final=rev_final, psr_df=psr_df, ticker=ticker, db_info=db_info,
                                  table_name=table_name, forecast_date=forecast_date)

        _cleanup(valuation_forecast_result, fc_table, rev_final, psr_df, df_rev)
        print(f"[OK] 완료: {ticker}")
        return True

    except Exception as e:
        ERROR_RECORDS.append({"ticker": ticker, "hs_code": hs_code, "error": str(e),
                              "traceback": traceback.format_exc()})
        print(f"[ERROR] {ticker}: {e}")
        return False


def _save_error_records_to_excel(prefix: str = "error_tickers") -> Optional[str]:
    if not ERROR_RECORDS:
        return None
    from datetime import datetime as dt  # 명시적으로 재import
    fname = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    try:
        pd.DataFrame(ERROR_RECORDS).to_excel(fname, index=False, sheet_name="errors", engine="xlsxwriter")
        print(f"[INFO] 에러 기록 저장: {fname}")
        return fname
    except Exception:
        return None


def run_batch(group: str = "all", value_start_date: Optional[str] = None,
              forecast_date: Optional[str] = None,
              table_name: str = "Korea_company_valuation_ver2"):
    ok, fail = 0, 0
    tickers = get_ticker_list(group=group)
    for item in tickers:
        res = process_single_ticker(ticker=item.get('ticker'), hs_code=item.get('hs_code'),
                                    db_info=db_info, table_name=table_name, horizon_quarter=6,
                                    psr_horizon_months=24, forecast_date=forecast_date,
                                    value_start_date=value_start_date)
        ok += int(res)
        fail += int(not res)

    print(f"\n{'-' * 60}\nBatch done. success={ok}, fail={fail}, group='{group}'\n{'-' * 60}")
    _save_error_records_to_excel(prefix=f"error_tickers_{group}")


if __name__ == "__main__":
    run_batch(group="all", value_start_date=None, forecast_date=None)