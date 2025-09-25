
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SARIMA 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈

특징:
1) 외생변수(exog_col) 사용 여부 구분
   - exog_col=None → 외생변수 없이 예측
   - exog_col="expDlr_yoy" → 해당 컬럼을 외생변수로 사용
2) 매출(revenue): 분기 단위 예측 (forecast_quarters)
   - 미래는 분기별 1개 값, 과거 NaN은 revenue_billions로 채움
3) PSR: 월별 12개월 예측
   - 미래는 12개월, 과거 NaN은 PSR_ttm으로 채움
4) 매출과 PSR의 예측 시작일 분리
   - start_date_revenue, start_date_psr
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from typing import Optional, Union
from statsmodels.tsa.statespace.sarimax import SARIMAX
from pandas.tseries.offsets import MonthEnd
from itertools import product
from typing import Optional, Union, Tuple
# =========================================================
# 유틸 함수
# =========================================================

# (이미 있으면 생략)
def to_month_end(s):
    ts = pd.to_datetime(s)
    if isinstance(ts, pd.Timestamp):
        return ts + MonthEnd(0)
    return ts + MonthEnd(0)

def ensure_sorted_unique_dates(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date_month_end"] = to_month_end(d["date_month_end"])
    return d.sort_values("date_month_end").drop_duplicates(["date_month_end"]).reset_index(drop=True)

def _filter_quarter_phase(df: pd.DataFrame) -> pd.DataFrame:
    """월 자료에서도 분기 페이즈(월%3 최빈값)만 남김: 01/04/07/10 등."""
    d = ensure_sorted_unique_dates(df)
    phase = (d["date_month_end"].dt.month % 3).mode().iloc[0]
    return d[d["date_month_end"].dt.month % 3 == phase].reset_index(drop=True)

def find_best_sarima_params(
    y_train: pd.Series,
    exog_train: pd.Series | None = None,
    seasonal_period: int = 12,
    p_values=(0,1,2), d_values=(0,1), q_values=(0,1,2),
    P_values=(0,1),   D_values=(0,1), Q_values=(0,1),
    ic: str = "aic",
    max_order_sum: int = 8,
):
    best_ic = np.inf
    best_order = (1,1,1)
    best_sorder = (1,1,0, seasonal_period)
    for p,d,q in product(p_values, d_values, q_values):
        for P,D,Q in product(P_values, D_values, Q_values):
            if (p+q+P+Q) > max_order_sum:
                continue
            order = (p,d,q)
            sorder = (P,D,Q, seasonal_period)
            try:
                m = SARIMAX(
                    y_train.astype(float),
                    exog=None if exog_train is None else exog_train.astype(float),
                    order=order, seasonal_order=sorder,
                    enforce_stationarity=False, enforce_invertibility=False
                )
                fit = m.fit(disp=False)
                val = fit.aic if ic.lower()=="aic" else fit.bic
                if np.isfinite(val) and val < best_ic:
                    best_ic, best_order, best_sorder = val, order, sorder
            except Exception:
                continue
    return best_order, best_sorder


def run_sarima_prediction(
        df: pd.DataFrame,
        ticker: str = "UNKNOWN",
        forecast_quarters: int = 4,
        psr_periods: int = 12,  # 월간 예측 길이(12/24 등)
        start_date_revenue: Optional[Union[str, pd.Timestamp]] = None,
        start_date_psr: Optional[Union[str, pd.Timestamp]] = None,
        exog_col: Optional[str] = None,
        ic: str = "aic",
) -> Tuple[pd.DataFrame, dict]:
    """
    반환을 항상 보장: (out_df, results)
    - revenue_billions → 분기 예측(S=4)
    - PSR_ttm(또는 월간 타깃) → 월 예측(S=12, psr_periods)
    """
    out_df = ensure_sorted_unique_dates(df)
    results = {"meta": {"ticker": ticker}, "revenue": {}, "psr": {}}

    # ---------------- Revenue: Quarterly ----------------
    try:
        if "revenue_billions" in out_df.columns:
            qdf = _filter_quarter_phase(out_df[["date_month_end","revenue_billions"]])
            y = pd.Series(qdf["revenue_billions"].values, index=qdf["date_month_end"]).dropna()
            if len(y) >= 8:
                exog_hist = None
                if exog_col and exog_col in out_df.columns:
                    exog_hist = out_df.set_index("date_month_end")[exog_col].reindex(y.index).ffill().bfill()

                ord_ne, sord_ne = find_best_sarima_params(y, None, 4, ic=ic)
                fit_ne = SARIMAX(y, order=ord_ne, seasonal_order=sord_ne,
                                 enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                fc_ne = fit_ne.forecast(steps=forecast_quarters)

                fc_ex = None
                if exog_hist is not None:
                    ord_ex, sord_ex = find_best_sarima_params(y, exog_hist, 4, ic=ic)
                    fit_ex = SARIMAX(y, exog=exog_hist, order=ord_ex, seasonal_order=sord_ex,
                                     enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                    exog_future = np.repeat(exog_hist.iloc[-1], forecast_quarters).reshape(-1,1)
                    fc_ex = fit_ex.forecast(steps=forecast_quarters, exog=exog_future)

                last_q = y.index.max() if not start_date_revenue else to_month_end(start_date_revenue)
                future_q = [last_q + pd.DateOffset(months=3*i) for i in range(1, forecast_quarters+1)]

                out_df["revenue_billions_sarima_noexog"] = out_df.get("revenue_billions")
                if exog_hist is not None:
                    out_df["revenue_billions_sarima_exog"] = out_df.get("revenue_billions")

                for i, d in enumerate(future_q):
                    if d not in out_df["date_month_end"].values:
                        out_df.loc[len(out_df), "date_month_end"] = d
                    out_df.loc[out_df["date_month_end"] == d, "revenue_billions_sarima_noexog"] = fc_ne.iloc[i]
                    if fc_ex is not None:
                        out_df.loc[out_df["date_month_end"] == d, "revenue_billions_sarima_exog"] = fc_ex.iloc[i]

                out_df["revenue_billions_sarima_noexog"] = \
                    out_df["revenue_billions_sarima_noexog"].combine_first(out_df["revenue_billions"])
                if "revenue_billions_sarima_exog" in out_df.columns:
                    out_df["revenue_billions_sarima_exog"] = \
                        out_df["revenue_billions_sarima_exog"].combine_first(out_df["revenue_billions"])

                results["revenue"] = {
                    "order": ord_ne, "seasonal_order": sord_ne,
                    "forecast_noexog": fc_ne, "forecast_exog": fc_ex
                }
    except Exception as e:
        results["revenue"]["error"] = str(e)

    # ---------------- PSR(or monthly target): Monthly ----------------
    try:
        target_col = "PSR_ttm" if "PSR_ttm" in out_df.columns else None
        if target_col:
            d = ensure_sorted_unique_dates(out_df[["date_month_end", target_col]])
            full_idx = pd.date_range(d["date_month_end"].min(), d["date_month_end"].max(), freq="M")
            y = pd.Series(d[target_col].values, index=d["date_month_end"]).reindex(full_idx).interpolate("time").ffill().bfill()
            if y.notna().sum() >= 24:
                exog_hist = None
                if exog_col and exog_col in out_df.columns:
                    exog_hist = out_df.set_index("date_month_end")[exog_col].reindex(y.index).ffill().bfill()

                ord_ne, sord_ne = find_best_sarima_params(y, None, 12, ic=ic)
                fit_ne = SARIMAX(y, order=ord_ne, seasonal_order=sord_ne,
                                 enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                fc_ne = fit_ne.forecast(steps=int(psr_periods))

                fc_ex = None
                if exog_hist is not None:
                    ord_ex, sord_ex = find_best_sarima_params(y, exog_hist, 12, ic=ic)
                    fit_ex = SARIMAX(y, exog=exog_hist, order=ord_ex, seasonal_order=sord_ex,
                                     enforce_stationarity=False, enforce_invertibility=False).fit(disp=False)
                    exog_future = np.repeat(exog_hist.iloc[-1], int(psr_periods)).reshape(-1,1)
                    fc_ex = fit_ex.forecast(steps=int(psr_periods), exog=exog_future)

                last_m = y.index.max() if not start_date_psr else to_month_end(start_date_psr)
                future_m = pd.date_range(last_m + MonthEnd(1), periods=int(psr_periods), freq="M")

                out_df[f"{target_col}_sarima_forecast_noexog"] = out_df.get(target_col)
                if exog_hist is not None:
                    out_df[f"{target_col}_sarima_forecast_exog"] = out_df.get(target_col)

                for i, d_ in enumerate(future_m):
                    if d_ not in out_df["date_month_end"].values:
                        out_df.loc[len(out_df), "date_month_end"] = d_
                    out_df.loc[out_df["date_month_end"] == d_, f"{target_col}_sarima_forecast_noexog"] = fc_ne.iloc[i]
                    if fc_ex is not None:
                        out_df.loc[out_df["date_month_end"] == d_, f"{target_col}_sarima_forecast_exog"] = fc_ex.iloc[i]

                out_df[f"{target_col}_sarima_forecast_noexog"] = \
                    out_df[f"{target_col}_sarima_forecast_noexog"].combine_first(out_df[target_col])
                if f"{target_col}_sarima_forecast_exog" in out_df.columns:
                    out_df[f"{target_col}_sarima_forecast_exog"] = \
                        out_df[f"{target_col}_sarima_forecast_exog"].combine_first(out_df[target_col])

                results["psr"] = {
                    "order": ord_ne, "seasonal_order": sord_ne,
                    "forecast_noexog": fc_ne, "forecast_exog": fc_ex
                }
    except Exception as e:
        results["psr"]["error"] = str(e)

    out_df = out_df.sort_values("date_month_end").reset_index(drop=True)
    return out_df, results

if __name__ == "__main__":
    print("us_sarima_forecast.py loaded. Use run_sarima_prediction(...)")


# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# SARIMA Forecast Module
# Independent forecasting functions for external import
# """
#
# import pandas as pd
# import numpy as np
# from datetime import datetime, timedelta
# from dateutil.relativedelta import relativedelta
# import warnings
#
# # Required library imports
# try:
#     from statsmodels.tsa.statespace.sarimax import SARIMAX
#     from statsmodels.tsa.stattools import adfuller
#
#     STATSMODELS_AVAILABLE = True
# except ImportError:
#     STATSMODELS_AVAILABLE = False
#
# from itertools import product
# from prophet import Prophet
# from statsmodels.tsa.holtwinters import ExponentialSmoothing
# import tensorflow as tf
# from tensorflow.keras.models import Sequential
# from tensorflow.keras.layers import LSTM, Dense
# from sklearn.preprocessing import MinMaxScaler
#
# warnings.filterwarnings('ignore')
#
#
# def create_forecast_dates(start_date_str, months=12):
#     """Create forecast period dates"""
#     start_date = pd.to_datetime(start_date_str + "-01")
#     forecast_dates = []
#
#     for i in range(months):
#         current_date = start_date + relativedelta(months=i)
#         month_end = current_date.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
#         forecast_dates.append(month_end)
#
#     return forecast_dates
#
#
# def prepare_export_data(final_data, export_forecast_start_date):
#     """Prepare and split export data"""
#     forecast_start = pd.to_datetime(export_forecast_start_date + "-01")
#     forecast_start_month_end = forecast_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
#
#     data_sorted = final_data.sort_values('date_month_end').copy()
#     export_data = data_sorted[data_sorted['expDlr'].notna()].copy()
#
#     if export_data.empty:
#         return None, None, None
#
#     historical_export = export_data[export_data['date_month_end'] < forecast_start_month_end]
#     future_export = export_data[export_data['date_month_end'] >= forecast_start_month_end]
#
#     return historical_export, future_export, forecast_start_month_end
#
#
# def prepare_target_data(final_data, forecast_start_month_end):
#     """Prepare target data for forecasting"""
#     target_data = final_data[final_data['PSR_ttm'].notna()].copy()
#     target_data = target_data.sort_values('date_month_end')
#     historical_target = target_data[target_data['date_month_end'] < forecast_start_month_end]
#
#     return historical_target
#
#
# def check_stationarity(series, name="Series"):
#     """Check stationarity using ADF test"""
#     if not STATSMODELS_AVAILABLE:
#         return True
#
#     result = adfuller(series.dropna())
#     return result[1] <= 0.05
#
#
# def find_best_sarima_params(y_train, exog_train=None, seasonal_period=12):
#     """Find optimal SARIMA parameters"""
#     if not STATSMODELS_AVAILABLE:
#         return (1, 1, 1), (1, 1, 1, seasonal_period)
#
#     p_values = [0, 1, 2]
#     d_values = [0, 1]
#     q_values = [0, 1, 2]
#     P_values = [0, 1]
#     D_values = [0, 1]
#     Q_values = [0, 1]
#
#     best_aic = np.inf
#     best_params = None
#     best_seasonal_params = None
#
#     for p, d, q in product(p_values, d_values, q_values):
#         for P, D, Q in product(P_values, D_values, Q_values):
#             try:
#                 model = SARIMAX(
#                     y_train,
#                     exog=exog_train,
#                     order=(p, d, q),
#                     seasonal_order=(P, D, Q, seasonal_period),
#                     enforce_stationarity=False,
#                     enforce_invertibility=False
#                 )
#
#                 fitted_model = model.fit(disp=False, maxiter=100)
#
#                 if fitted_model.aic < best_aic:
#                     best_aic = fitted_model.aic
#                     best_params = (p, d, q)
#                     best_seasonal_params = (P, D, Q, seasonal_period)
#             except:
#                 continue
#
#     if best_params is None:
#         best_params = (1, 1, 1)
#         best_seasonal_params = (1, 1, 1, seasonal_period)
#
#     return best_params, best_seasonal_params
#
#
# def sarima_forecast_with_export(final_data, export_forecast_start_date="2025-10",
#                                 USE_EXOGENOUS=True, forecast_months=12):
#     """
#     SARIMA model PSR forecasting
#
#     Parameters:
#     - final_data: Preprocessed data DataFrame
#     - export_forecast_start_date: Export forecast start date (YYYY-MM format)
#     - USE_EXOGENOUS: Whether to use exogenous variables (export data)
#     - forecast_months: Number of months to forecast
#
#     Returns:
#     - pd.DataFrame: Forecast results DataFrame
#     """
#
#     if not STATSMODELS_AVAILABLE:
#         return None
#
#     # Data preparation
#     historical_export, future_export, forecast_start_month_end = prepare_export_data(
#         final_data, export_forecast_start_date)
#
#     historical_target = prepare_target_data(final_data, forecast_start_month_end)
#
#     if historical_target.empty:
#         return None
#
#     # Time series data preparation
#     target_ts = historical_target.set_index('date_month_end')['PSR_ttm'].astype(float)
#
#     # Exogenous variable preparation (YoY)
#     exog_train = None
#     exog_forecast = None
#
#     if USE_EXOGENOUS and historical_export is not None and not historical_export.empty:
#         # Combine historical and future export data
#         export_all = pd.concat([
#             historical_export[['date_month_end', 'expDlr']],
#             future_export[['date_month_end', 'expDlr']] if future_export is not None else pd.DataFrame(
#                 columns=['date_month_end', 'expDlr'])
#         ], ignore_index=True).drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')
#
#         export_all = export_all.set_index('date_month_end')['expDlr'].astype(float)
#
#         # Calculate YoY (12-month percentage change)
#         export_yoy = export_all.pct_change(12)
#
#         # Align target series with YoY data
#         common_index = target_ts.index.intersection(export_yoy.index)
#         if len(common_index) == 0:
#             USE_EXOGENOUS = False
#         else:
#             target_ts = target_ts.loc[common_index]
#             export_yoy_train = export_yoy.loc[common_index]
#
#             # Use common dates (remove NaN)
#             common_dates = target_ts.index.intersection(export_yoy_train.dropna().index)
#
#             if len(common_dates) > 0:
#                 target_ts = target_ts.loc[common_dates]
#                 exog_train = export_yoy_train.loc[common_dates].values.reshape(-1, 1)
#
#                 # Generate forecast dates and match YoY
#                 forecast_dates = pd.to_datetime(create_forecast_dates(export_forecast_start_date, forecast_months))
#                 exog_forecast_series = export_yoy.reindex(forecast_dates)
#
#                 # Fill NaN values
#                 exog_forecast = exog_forecast_series.fillna(method='ffill').fillna(method='bfill').values.reshape(-1, 1)
#             else:
#                 USE_EXOGENOUS = False
#                 exog_train = None
#     else:
#         USE_EXOGENOUS = False
#
#     # Find optimal parameters
#     best_order, best_seasonal_order = find_best_sarima_params(
#         target_ts, exog_train if USE_EXOGENOUS else None)
#
#     if best_order is None:
#         return None
#
#     # Final model training
#     try:
#         model = SARIMAX(
#             target_ts,
#             exog=exog_train if USE_EXOGENOUS else None,
#             order=best_order,
#             seasonal_order=best_seasonal_order,
#             enforce_stationarity=False,
#             enforce_invertibility=False
#         )
#
#         fitted_model = model.fit(disp=False, maxiter=200)
#     except Exception:
#         return None
#
#     # Perform forecasting
#     forecast_dates = create_forecast_dates(export_forecast_start_date, forecast_months)
#
#     try:
#         if USE_EXOGENOUS and exog_forecast is not None:
#             if len(exog_forecast) < forecast_months:
#                 last_value = exog_forecast[-1] if len(exog_forecast) > 0 else np.array([[0]])
#                 missing_count = forecast_months - len(exog_forecast)
#                 additional_values = np.repeat(last_value, missing_count, axis=0)
#                 exog_forecast = np.vstack([exog_forecast, additional_values])
#
#             forecast_result = fitted_model.forecast(
#                 steps=forecast_months,
#                 exog=exog_forecast[:forecast_months]
#             )
#             conf_int = fitted_model.get_forecast(
#                 steps=forecast_months,
#                 exog=exog_forecast[:forecast_months]
#             ).conf_int()
#         else:
#             forecast_result = fitted_model.forecast(steps=forecast_months)
#             conf_int = fitted_model.get_forecast(steps=forecast_months).conf_int()
#     except Exception:
#         return None
#
#     # Organize results
#     forecast_df = pd.DataFrame({
#         'date_month_end': forecast_dates,
#         'PSR_forecast': forecast_result.values,
#         'PSR_lower': conf_int.iloc[:, 0].values,
#         'PSR_upper': conf_int.iloc[:, 1].values,
#         'forecast_type': 'SARIMA',
#         'use_exogenous': USE_EXOGENOUS
#     })
#
#     # Add exogenous variable information
#     if USE_EXOGENOUS and exog_forecast is not None:
#         forecast_df['exog_value'] = exog_forecast[:forecast_months].flatten()
#     else:
#         forecast_df['exog_value'] = np.nan
#
#     return forecast_df
#
#
# def extract_quarterly_revenue(data, revenue_col='revenue_billions', date_col='date_month_end', data_end_date=None):
#     """Extract quarterly revenue from monthly data"""
#     df = data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     if data_end_date:
#         end_date = pd.to_datetime(data_end_date)
#         df = df[df[date_col] <= end_date]
#
#     revenue_data = df[df[revenue_col].notna()].copy()
#
#     if len(revenue_data) == 0:
#         raise ValueError("No valid revenue data found")
#
#     revenue_data['year'] = revenue_data[date_col].dt.year
#     revenue_data['quarter'] = revenue_data[date_col].dt.quarter
#     revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)
#
#     quarterly_list = []
#
#     for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
#         last_month_data = group.loc[group[date_col].idxmax()]
#
#         quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
#         quarter_end_month = quarter_month_map[quarter]
#         quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
#                                         day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)
#
#         quarterly_list.append({
#             'date_quarter_end': quarter_end_date,
#             'year': year,
#             'quarter': quarter,
#             'year_quarter': f"{year}Q{quarter}",
#             'revenue_billions': last_month_data[revenue_col],
#             'data_months_in_quarter': len(group)
#         })
#
#     quarterly_data = pd.DataFrame(quarterly_list)
#     quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)
#
#     return quarterly_data
#
#
# def sarima_quarterly_forecast(quarterly_data, forecast_quarters=4):
#     """Quarterly revenue SARIMA forecasting"""
#     try:
#         revenue_series = quarterly_data['revenue_billions'].values
#
#         if len(revenue_series) < 8:
#             raise ValueError(
#                 f"Minimum 8 quarters required for SARIMA modeling. Current: {len(revenue_series)} quarters")
#
#         p_values = [0, 1, 2]
#         d_values = [0, 1]
#         q_values = [0, 1, 2]
#         P_values = [0, 1]
#         D_values = [0, 1]
#         Q_values = [0, 1]
#         s_value = 4  # Quarterly seasonality
#
#         best_aic = float('inf')
#         best_params = None
#         best_model = None
#
#         for p, d, q, P, D, Q in product(p_values, d_values, q_values, P_values, D_values, Q_values):
#             try:
#                 total_params = p + q + P + Q + 1
#                 if total_params >= len(revenue_series) * 0.4:
#                     continue
#
#                 model = SARIMAX(
#                     revenue_series,
#                     order=(p, d, q),
#                     seasonal_order=(P, D, Q, s_value),
#                     enforce_stationarity=False,
#                     enforce_invertibility=False
#                 )
#
#                 fitted_model = model.fit(disp=False, maxiter=100)
#
#                 if fitted_model.aic < best_aic:
#                     best_aic = fitted_model.aic
#                     best_params = (p, d, q, P, D, Q, s_value)
#                     best_model = fitted_model
#             except Exception:
#                 continue
#
#         if best_model is None:
#             model = SARIMAX(
#                 revenue_series,
#                 order=(1, 1, 1),
#                 seasonal_order=(0, 0, 0, 0),
#                 enforce_stationarity=False,
#                 enforce_invertibility=False
#             )
#             best_model = model.fit(disp=False)
#             best_params = (1, 1, 1, 0, 0, 0, 0)
#             best_aic = best_model.aic
#
#         # Perform forecasting
#         forecast = best_model.forecast(steps=forecast_quarters)
#         forecast_values = forecast.values if hasattr(forecast, 'values') else forecast
#
#         # Calculate confidence intervals
#         try:
#             prediction_results = best_model.get_prediction(
#                 start=len(revenue_series),
#                 end=len(revenue_series) + forecast_quarters - 1
#             )
#             forecast_ci = prediction_results.conf_int()
#             forecast_lower = forecast_ci.iloc[:, 0].values
#             forecast_upper = forecast_ci.iloc[:, 1].values
#         except:
#             forecast_std = np.std(revenue_series) * 0.1
#             forecast_lower = forecast_values - 1.96 * forecast_std
#             forecast_upper = forecast_values + 1.96 * forecast_std
#
#         # Generate forecast dates
#         last_date = quarterly_data['date_quarter_end'].iloc[-1]
#         forecast_dates = []
#
#         for i in range(1, forecast_quarters + 1):
#             next_quarter_date = last_date + pd.DateOffset(months=3 * i)
#             quarter_end = pd.Timestamp(
#                 year=next_quarter_date.year,
#                 month=next_quarter_date.month,
#                 day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
#             )
#             forecast_dates.append(quarter_end)
#
#         # Create result DataFrame
#         forecast_result = pd.DataFrame({
#             'date_quarter_end': forecast_dates,
#             'year': [d.year for d in forecast_dates],
#             'quarter': [d.quarter for d in forecast_dates],
#             'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
#             'revenue_billions_forecast': forecast_values,
#             'forecast_lower': forecast_lower,
#             'forecast_upper': forecast_upper
#         })
#
#         model_info = {
#             'params': best_params,
#             'aic': best_aic,
#             'model': best_model,
#             'historical_data_points': len(revenue_series)
#         }
#
#         return forecast_result, model_info
#
#     except Exception:
#         return None, None
#
#
# def distribute_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
#     """Distribute quarterly forecast results to monthly"""
#     df = original_data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     df['revenue_billions_forecast'] = df['revenue_billions'].copy()
#
#     quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}
#
#     for _, forecast_row in quarterly_forecast.iterrows():
#         year = forecast_row['year']
#         quarter = forecast_row['quarter']
#         quarterly_value = forecast_row['revenue_billions_forecast']
#
#         months_in_quarter = quarter_month_map[quarter]
#
#         for month in months_in_quarter:
#             month_end = pd.Timestamp(year=year, month=month,
#                                      day=pd.Timestamp(year, month, 1).days_in_month)
#
#             mask = df[date_col] == month_end
#             if mask.any():
#                 df.loc[mask, 'revenue_billions_forecast'] = quarterly_value
#
#     return df
#
#
# def revenue_sarima_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
#                                      data_end_date=None, forecast_quarters=4):
#     """Revenue SARIMA forecast pipeline"""
#     try:
#         quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)
#         forecast_result, model_info = sarima_quarterly_forecast(quarterly_data, forecast_quarters)
#
#         if forecast_result is None:
#             return None, quarterly_data, None, None
#
#         result_data = distribute_quarterly_to_monthly(forecast_result, data, date_col)
#
#         return result_data, quarterly_data, forecast_result, model_info
#
#     except Exception:
#         return None, None, None, None
#
#
# # LSTM Functions
# def create_lstm_sequences(data, lookback_window=8):
#     """Create sequence data for LSTM"""
#     X, y = [], []
#     for i in range(lookback_window, len(data)):
#         X.append(data[i - lookback_window:i])
#         y.append(data[i])
#     return np.array(X), np.array(y)
#
#
# def lstm_quarterly_forecast(quarterly_data, forecast_quarters=4, lookback_window=8, epochs=100):
#     """Quarterly revenue LSTM forecasting"""
#     try:
#         revenue_series = quarterly_data['revenue_billions'].values.astype(np.float64)
#
#         if len(revenue_series) < lookback_window + 4:
#             raise ValueError(f"Minimum {lookback_window + 4} quarters required for LSTM modeling")
#
#         # Handle NaN values
#         if np.isnan(revenue_series).any():
#             mask = np.isnan(revenue_series)
#             indices = np.where(~mask)[0]
#             revenue_series = np.interp(np.arange(len(revenue_series)), indices, revenue_series[indices])
#
#         # Data normalization
#         scaler = MinMaxScaler(feature_range=(0.1, 0.9))
#         scaled_data = scaler.fit_transform(revenue_series.reshape(-1, 1)).flatten()
#
#         # Create sequence data
#         X, y = create_lstm_sequences(scaled_data, lookback_window)
#
#         if len(X) == 0:
#             raise ValueError("Failed to create sequences: insufficient data")
#
#         # Reshape for LSTM input
#         X = X.reshape((X.shape[0], X.shape[1], 1))
#
#         # Build LSTM model
#         model = Sequential([
#             LSTM(50, return_sequences=True, input_shape=(lookback_window, 1)),
#             LSTM(50, return_sequences=False),
#             Dense(25, activation='relu'),
#             Dense(1)
#         ])
#
#         # Compile model
#         model.compile(
#             optimizer=tf.keras.optimizers.Adam(learning_rate=0.001, clipnorm=1.0),
#             loss='mse',
#             metrics=['mae']
#         )
#
#         # Early stopping
#         early_stopping = tf.keras.callbacks.EarlyStopping(
#             monitor='loss',
#             patience=20,
#             restore_best_weights=True,
#             verbose=0
#         )
#
#         # Train model
#         history = model.fit(
#             X, y,
#             epochs=epochs,
#             batch_size=min(8, len(X)),
#             verbose=0,
#             callbacks=[early_stopping]
#         )
#
#         final_loss = history.history['loss'][-1]
#         trained_epochs = len(history.history['loss'])
#
#         # Perform forecasting
#         last_sequence = scaled_data[-lookback_window:].copy()
#         predictions = []
#
#         for step in range(forecast_quarters):
#             input_seq = last_sequence.reshape(1, lookback_window, 1)
#             pred = model.predict(input_seq, verbose=0)
#             pred_value = pred[0, 0]
#
#             if np.isnan(pred_value) or np.isinf(pred_value):
#                 if predictions:
#                     pred_value = np.mean(predictions)
#                 else:
#                     pred_value = last_sequence[-1]
#
#             pred_value = np.clip(pred_value, 0.1, 0.9)
#             predictions.append(pred_value)
#
#             last_sequence = np.roll(last_sequence, -1)
#             last_sequence[-1] = pred_value
#
#         # Inverse transform
#         predictions_array = np.array(predictions).reshape(-1, 1)
#         forecast_values = scaler.inverse_transform(predictions_array).flatten()
#
#         # Final NaN check
#         if np.isnan(forecast_values).any() or np.isinf(forecast_values).any():
#             last_actual = revenue_series[-1]
#             forecast_values = np.full(forecast_quarters, last_actual)
#
#         # Generate forecast dates
#         last_date = quarterly_data['date_quarter_end'].iloc[-1]
#         forecast_dates = []
#
#         for i in range(1, forecast_quarters + 1):
#             next_quarter_date = last_date + pd.DateOffset(months=3 * i)
#             quarter_end = pd.Timestamp(
#                 year=next_quarter_date.year,
#                 month=next_quarter_date.month,
#                 day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
#             )
#             forecast_dates.append(quarter_end)
#
#         # Create result DataFrame
#         forecast_result = pd.DataFrame({
#             'date_quarter_end': forecast_dates,
#             'year': [d.year for d in forecast_dates],
#             'quarter': [d.quarter for d in forecast_dates],
#             'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
#             'revenue_billions_lstm_forecast': forecast_values
#         })
#
#         model_info = {
#             'model_type': 'LSTM',
#             'lookback_window': lookback_window,
#             'trained_epochs': trained_epochs,
#             'final_loss': final_loss,
#             'scaler': scaler,
#             'model': model,
#             'historical_data_points': len(revenue_series)
#         }
#
#         return forecast_result, model_info
#
#     except Exception:
#         return None, None
#
#
# def distribute_lstm_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
#     """Distribute quarterly LSTM forecast results to monthly"""
#     df = original_data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     if 'revenue_billions' in df.columns:
#         df['revenue_billions_lstm_forecast'] = df['revenue_billions'].copy()
#     else:
#         df['revenue_billions_lstm_forecast'] = np.nan
#
#     quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}
#
#     for _, forecast_row in quarterly_forecast.iterrows():
#         year = forecast_row['year']
#         quarter = forecast_row['quarter']
#         quarterly_value = forecast_row['revenue_billions_lstm_forecast']
#
#         months_in_quarter = quarter_month_map[quarter]
#
#         for month in months_in_quarter:
#             month_end = pd.Timestamp(year=year, month=month,
#                                      day=pd.Timestamp(year, month, 1).days_in_month)
#
#             mask = df[date_col] == month_end
#             if mask.any():
#                 df.loc[mask, 'revenue_billions_lstm_forecast'] = quarterly_value
#
#     return df
#
#
# def revenue_lstm_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
#                                    data_end_date=None, forecast_quarters=4, lookback_window=8, epochs=100):
#     """Revenue LSTM forecast pipeline"""
#     try:
#         quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)
#         forecast_result, model_info = lstm_quarterly_forecast(
#             quarterly_data, forecast_quarters, lookback_window, epochs
#         )
#
#         if forecast_result is None:
#             return None, quarterly_data, None, None
#
#         result_data = distribute_lstm_quarterly_to_monthly(forecast_result, data, date_col)
#
#         return result_data, quarterly_data, forecast_result, model_info
#
#     except Exception:
#         return None, None, None, None
#
#
# # Prophet Functions
# def prepare_prophet_data(quarterly_data, exog_data=None):
#     """Prepare data for Prophet"""
#     prophet_df = pd.DataFrame({
#         'ds': quarterly_data['date_quarter_end'],
#         'y': quarterly_data['revenue_billions']
#     })
#
#     if prophet_df['y'].isna().any():
#         prophet_df = prophet_df.dropna(subset=['y'])
#
#     if exog_data is not None:
#         exog_cols = [col for col in exog_data.columns
#                      if col not in ['date_quarter_end', 'year', 'quarter', 'year_quarter']]
#
#         for _, exog_row in exog_data.iterrows():
#             exog_date = exog_row['date_quarter_end']
#             mask = prophet_df['ds'] == exog_date
#
#             if mask.any():
#                 for col in exog_cols:
#                     prophet_df.loc[mask, col] = exog_row[col]
#
#         for col in exog_cols:
#             if col in prophet_df.columns:
#                 prophet_df = prophet_df.dropna(subset=[col])
#
#     return prophet_df
#
#
# def extract_exogenous_variables(data, date_col='date_month_end', data_end_date=None, exog_cols=['expDlr']):
#     """Extract exogenous variables and convert to quarterly"""
#     df = data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     if data_end_date:
#         end_date = pd.to_datetime(data_end_date)
#         df = df[df[date_col] <= end_date]
#
#     available_exog_cols = [col for col in exog_cols if col in df.columns]
#     if not available_exog_cols:
#         return None
#
#     df_clean = df.dropna(subset=available_exog_cols)
#
#     if len(df_clean) == 0:
#         return None
#
#     exog_data = df_clean.copy()
#     exog_data['year'] = exog_data[date_col].dt.year
#     exog_data['quarter'] = exog_data[date_col].dt.quarter
#
#     quarterly_exog_list = []
#
#     for (year, quarter), group in exog_data.groupby(['year', 'quarter']):
#         last_month_data = group.loc[group[date_col].idxmax()]
#
#         quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
#         quarter_end_month = quarter_month_map[quarter]
#         quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
#                                         day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)
#
#         exog_dict = {
#             'date_quarter_end': quarter_end_date,
#             'year': year,
#             'quarter': quarter,
#             'year_quarter': f"{year}Q{quarter}"
#         }
#
#         for col in available_exog_cols:
#             exog_dict[col] = last_month_data[col]
#
#         quarterly_exog_list.append(exog_dict)
#
#     quarterly_exog = pd.DataFrame(quarterly_exog_list)
#     quarterly_exog = quarterly_exog.sort_values('date_quarter_end').reset_index(drop=True)
#
#     return quarterly_exog
#
#
# def prophet_quarterly_forecast(prophet_df, forecast_quarters=4, use_exog=False, exog_cols=None):
#     """Quarterly revenue Prophet forecasting"""
#     try:
#         model = Prophet(
#             yearly_seasonality=True,
#             weekly_seasonality=False,
#             daily_seasonality=False,
#             seasonality_mode='additive',
#             changepoint_prior_scale=0.05
#         )
#
#         model.add_seasonality(name='quarterly', period=365.25 / 4, fourier_order=4)
#
#         if use_exog and exog_cols:
#             for col in exog_cols:
#                 if col in prophet_df.columns:
#                     model.add_regressor(col)
#
#         model.fit(prophet_df)
#
#         last_date = prophet_df['ds'].iloc[-1]
#         future_dates = []
#
#         for i in range(1, forecast_quarters + 1):
#             next_quarter_date = last_date + pd.DateOffset(months=3 * i)
#             quarter_end = pd.Timestamp(
#                 year=next_quarter_date.year,
#                 month=next_quarter_date.month,
#                 day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
#             )
#             future_dates.append(quarter_end)
#
#         future_df = model.make_future_dataframe(periods=forecast_quarters, freq='QS')
#
#         if use_exog and exog_cols:
#             for col in exog_cols:
#                 if col in prophet_df.columns:
#                     last_value = prophet_df[col].iloc[-1]
#
#                     for i, future_date in enumerate(future_dates):
#                         mask = future_df['ds'] == future_date
#                         if mask.any():
#                             future_df.loc[mask, col] = last_value
#
#         forecast = model.predict(future_df)
#         forecast_only = forecast.tail(forecast_quarters).copy()
#
#         forecast_result = pd.DataFrame({
#             'date_quarter_end': future_dates,
#             'year': [d.year for d in future_dates],
#             'quarter': [d.quarter for d in future_dates],
#             'year_quarter': [f"{d.year}Q{d.quarter}" for d in future_dates],
#             'revenue_billions_prophet_forecast' + ('_exog' if use_exog else ''): forecast_only['yhat'].values,
#             'forecast_lower': forecast_only['yhat_lower'].values,
#             'forecast_upper': forecast_only['yhat_upper'].values
#         })
#
#         model_info = {
#             'model_type': 'Prophet',
#             'use_exogenous': use_exog,
#             'exogenous_variables': exog_cols if use_exog else None,
#             'model': model,
#             'historical_data_points': len(prophet_df)
#         }
#
#         return forecast_result, model_info
#
#     except Exception:
#         return None, None
#
#
# def distribute_prophet_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end',
#                                             use_exog=False):
#     """Distribute quarterly Prophet forecast results to monthly"""
#     df = original_data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     forecast_col = 'revenue_billions_prophet_forecast' + ('_exog' if use_exog else '')
#
#     if 'revenue_billions' in df.columns:
#         df[forecast_col] = df['revenue_billions'].copy()
#     else:
#         df[forecast_col] = np.nan
#
#     quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}
#
#     for _, forecast_row in quarterly_forecast.iterrows():
#         year = forecast_row['year']
#         quarter = forecast_row['quarter']
#         quarterly_value = forecast_row[forecast_col]
#
#         months_in_quarter = quarter_month_map[quarter]
#
#         for month in months_in_quarter:
#             month_end = pd.Timestamp(year=year, month=month,
#                                      day=pd.Timestamp(year, month, 1).days_in_month)
#
#             mask = df[date_col] == month_end
#             if mask.any():
#                 df.loc[mask, forecast_col] = quarterly_value
#
#     return df
#
#
# def revenue_prophet_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
#                                       data_end_date=None, forecast_quarters=4,
#                                       use_exogenous=True, exog_cols=['expDlr']):
#     """Revenue Prophet forecast pipeline"""
#     try:
#         quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)
#
#         result_data = data.copy()
#         forecast_results = {}
#         model_infos = {}
#
#         # Prophet without exogenous variables
#         prophet_df_no_exog = prepare_prophet_data(quarterly_data)
#
#         forecast_no_exog, model_info_no_exog = prophet_quarterly_forecast(
#             prophet_df_no_exog, forecast_quarters, use_exog=False
#         )
#
#         if forecast_no_exog is not None:
#             result_data = distribute_prophet_quarterly_to_monthly(
#                 forecast_no_exog, result_data, date_col, use_exog=False
#             )
#             forecast_results['no_exog'] = forecast_no_exog
#             model_infos['no_exog'] = model_info_no_exog
#         else:
#             forecast_results['no_exog'] = None
#             model_infos['no_exog'] = None
#
#         # Prophet with exogenous variables
#         if use_exogenous:
#             quarterly_exog = extract_exogenous_variables(data, date_col, data_end_date, exog_cols)
#
#             if quarterly_exog is not None:
#                 prophet_df_with_exog = prepare_prophet_data(quarterly_data, quarterly_exog)
#
#                 forecast_with_exog, model_info_with_exog = prophet_quarterly_forecast(
#                     prophet_df_with_exog, forecast_quarters, use_exog=True, exog_cols=exog_cols
#                 )
#
#                 if forecast_with_exog is not None:
#                     result_data = distribute_prophet_quarterly_to_monthly(
#                         forecast_with_exog, result_data, date_col, use_exog=True
#                     )
#                     forecast_results['with_exog'] = forecast_with_exog
#                     model_infos['with_exog'] = model_info_with_exog
#                 else:
#                     forecast_results['with_exog'] = None
#                     model_infos['with_exog'] = None
#             else:
#                 forecast_results['with_exog'] = None
#                 model_infos['with_exog'] = None
#         else:
#             forecast_results['with_exog'] = None
#             model_infos['with_exog'] = None
#
#         return result_data, quarterly_data, forecast_results, model_infos
#
#     except Exception:
#         return None, None, None, None
#
#
# # Exponential Smoothing Functions
# def exponential_smoothing_quarterly_forecast(quarterly_data, forecast_quarters=4):
#     """Quarterly revenue Exponential Smoothing forecasting"""
#     try:
#         revenue_series = quarterly_data['revenue_billions'].values
#
#         if len(revenue_series) < 8:
#             raise ValueError(f"Minimum 8 quarters required for Exponential Smoothing modeling")
#
#         ts_data = pd.Series(
#             revenue_series,
#             index=pd.date_range(
#                 start=quarterly_data['date_quarter_end'].iloc[0],
#                 periods=len(revenue_series),
#                 freq='QS'
#             )
#         )
#
#         models_to_try = [
#             ('add', 'add', False, 4),
#             ('add', 'mul', False, 4),
#             ('add', 'add', True, 4),
#             ('add', 'mul', True, 4),
#             ('add', None, False, None),
#             ('add', None, True, None),
#             (None, None, False, None)
#         ]
#
#         best_aic = float('inf')
#         best_model = None
#         best_config = None
#
#         for trend, seasonal, damped, seasonal_periods in models_to_try:
#             try:
#                 if seasonal is not None and seasonal_periods is not None:
#                     if len(revenue_series) < seasonal_periods * 2:
#                         continue
#
#                     model = ExponentialSmoothing(
#                         ts_data,
#                         trend=trend,
#                         seasonal=seasonal,
#                         damped_trend=damped,
#                         seasonal_periods=seasonal_periods
#                     )
#                     model_name = f"Holt-Winters ({seasonal})"
#                 else:
#                     model = ExponentialSmoothing(
#                         ts_data,
#                         trend=trend,
#                         damped_trend=damped
#                     )
#                     if trend is not None:
#                         model_name = f"Holt ({'Damped' if damped else 'Linear'})"
#                     else:
#                         model_name = "Simple ES"
#
#                 fitted_model = model.fit(optimized=True, use_brute=False)
#
#                 if fitted_model.aic < best_aic:
#                     best_aic = fitted_model.aic
#                     best_model = fitted_model
#                     best_config = (trend, seasonal, damped, seasonal_periods, model_name)
#
#             except Exception:
#                 continue
#
#         if best_model is None:
#             model = ExponentialSmoothing(ts_data, trend=None)
#             best_model = model.fit(optimized=True)
#             best_config = (None, None, False, None, "Simple ES (Fallback)")
#             best_aic = best_model.aic
#
#         forecast = best_model.forecast(steps=forecast_quarters)
#
#         if isinstance(forecast, pd.Series):
#             forecast_values = forecast.values
#         else:
#             forecast_values = np.array(forecast)
#
#         if np.isnan(forecast_values).any():
#             last_value = revenue_series[-1]
#             forecast_values = np.nan_to_num(forecast_values, nan=last_value)
#
#         if np.isinf(forecast_values).any():
#             last_value = revenue_series[-1]
#             forecast_values = np.where(np.isinf(forecast_values), last_value, forecast_values)
#
#         try:
#             residuals = best_model.resid
#             forecast_std = np.std(residuals) if residuals is not None else np.std(revenue_series) * 0.1
#             forecast_lower = forecast_values - 1.96 * forecast_std
#             forecast_upper = forecast_values + 1.96 * forecast_std
#         except:
#             forecast_std = np.std(revenue_series) * 0.1
#             forecast_lower = forecast_values - 1.96 * forecast_std
#             forecast_upper = forecast_values + 1.96 * forecast_std
#
#         last_date = quarterly_data['date_quarter_end'].iloc[-1]
#         forecast_dates = []
#
#         for i in range(1, forecast_quarters + 1):
#             next_quarter_date = last_date + pd.DateOffset(months=3 * i)
#             quarter_end = pd.Timestamp(
#                 year=next_quarter_date.year,
#                 month=next_quarter_date.month,
#                 day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
#             )
#             forecast_dates.append(quarter_end)
#
#         forecast_result = pd.DataFrame({
#             'date_quarter_end': forecast_dates,
#             'year': [d.year for d in forecast_dates],
#             'quarter': [d.quarter for d in forecast_dates],
#             'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
#             'revenue_billions_es_forecast': forecast_values,
#             'forecast_lower': forecast_lower,
#             'forecast_upper': forecast_upper
#         })
#
#         model_info = {
#             'model_type': 'ExponentialSmoothing',
#             'best_config': best_config,
#             'aic': best_aic,
#             'model': best_model,
#             'historical_data_points': len(revenue_series)
#         }
#
#         return forecast_result, model_info
#
#     except Exception:
#         return None, None
#
#
# def distribute_es_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
#     """Distribute quarterly Exponential Smoothing forecast results to monthly"""
#     df = original_data.copy()
#     df[date_col] = pd.to_datetime(df[date_col])
#
#     if 'revenue_billions' in df.columns:
#         df['revenue_billions_es_forecast'] = df['revenue_billions'].copy()
#     else:
#         df['revenue_billions_es_forecast'] = np.nan
#
#     quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}
#
#     for _, forecast_row in quarterly_forecast.iterrows():
#         year = forecast_row['year']
#         quarter = forecast_row['quarter']
#         quarterly_value = forecast_row['revenue_billions_es_forecast']
#
#         months_in_quarter = quarter_month_map[quarter]
#
#         for month in months_in_quarter:
#             month_end = pd.Timestamp(year=year, month=month,
#                                      day=pd.Timestamp(year, month, 1).days_in_month)
#
#             mask = df[date_col] == month_end
#             if mask.any():
#                 df.loc[mask, 'revenue_billions_es_forecast'] = quarterly_value
#
#     return df
#
#
# def revenue_es_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
#                                  data_end_date=None, forecast_quarters=4):
#     """Revenue Exponential Smoothing forecast pipeline"""
#     try:
#         quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)
#         forecast_result, model_info = exponential_smoothing_quarterly_forecast(quarterly_data, forecast_quarters)
#
#         if forecast_result is None:
#             return None, quarterly_data, None, None
#
#         result_data = distribute_es_quarterly_to_monthly(forecast_result, data, date_col)
#
#         return result_data, quarterly_data, forecast_result, model_info
#
#     except Exception:
#         return None, None, None, None
#
#
# # Integration Functions
# def create_revenue_forecast_result(sarima_data, lstm_data, prophet_data, es_data):
#     """Create integrated forecast result from 4 models"""
#     base_columns = ['ticker', 'date_month_end'] if 'ticker' in sarima_data.columns else ['date_month_end']
#     revenue_forecast_result = sarima_data[base_columns + ['revenue_billions_forecast']].copy()
#
#     lstm_forecast = lstm_data[['date_month_end', 'revenue_billions_lstm_forecast']].copy()
#     revenue_forecast_result = pd.merge(revenue_forecast_result, lstm_forecast, on='date_month_end', how='outer')
#
#     prophet_forecast = prophet_data[['date_month_end', 'revenue_billions_prophet_forecast']].copy()
#     revenue_forecast_result = pd.merge(revenue_forecast_result, prophet_forecast, on='date_month_end', how='outer')
#
#     es_forecast = es_data[['date_month_end', 'revenue_billions_es_forecast']].copy()
#     revenue_forecast_result = pd.merge(revenue_forecast_result, es_forecast, on='date_month_end', how='outer')
#
#     revenue_forecast_result = revenue_forecast_result.sort_values('date_month_end').reset_index(drop=True)
#     if 'ticker' in revenue_forecast_result.columns:
#         revenue_forecast_result['ticker'] = revenue_forecast_result['ticker'].fillna('AMAT')
#
#     return revenue_forecast_result
#
#
# def compare_forecast_models(forecast_df):
#     """Compare forecast values by model"""
#     forecast_columns = [
#         'revenue_billions_forecast',
#         'revenue_billions_lstm_forecast',
#         'revenue_billions_prophet_forecast',
#         'revenue_billions_es_forecast'
#     ]
#
#     complete_forecasts = forecast_df.dropna(subset=forecast_columns)
#     if len(complete_forecasts) == 0:
#         return None, None
#
#     comparison_stats = {}
#     for col in forecast_columns:
#         model_name = col.replace('revenue_billions_', '').replace('_forecast', '')
#         comparison_stats[model_name] = {
#             'mean': complete_forecasts[col].mean(),
#             'std': complete_forecasts[col].std(),
#             'min': complete_forecasts[col].min(),
#             'max': complete_forecasts[col].max()
#         }
#
#     comparison_df = pd.DataFrame(comparison_stats).T
#     correlation_matrix = complete_forecasts[forecast_columns].corr()
#
#     return comparison_df, correlation_matrix
#
#
# def calculate_ttm_with_shift(revenue_forecast_result, shift_months=2):
#     """Calculate TTM with shift handling quarterly duplicates"""
#     df = revenue_forecast_result.copy()
#     df['date_month_end'] = pd.to_datetime(df['date_month_end'])
#     df = df.sort_values('date_month_end').reset_index(drop=True)
#
#     result_dfs = []
#
#     for ticker in df['ticker'].unique():
#         ticker_df = df[df['ticker'] == ticker].copy()
#
#         forecast_columns = [
#             'revenue_billions_forecast',
#             'revenue_billions_lstm_forecast',
#             'revenue_billions_prophet_forecast',
#             'revenue_billions_es_forecast'
#         ]
#
#         existing_forecast_columns = [col for col in forecast_columns if col in ticker_df.columns]
#
#         quarterly_data = extract_quarterly_data(ticker_df, existing_forecast_columns)
#         quarterly_ttm = calculate_quarterly_ttm(quarterly_data, existing_forecast_columns)
#         monthly_ttm = expand_quarterly_to_monthly(quarterly_ttm, ticker_df, existing_forecast_columns)
#         ticker_result = merge_ttm_data(ticker_df, monthly_ttm, existing_forecast_columns, shift_months)
#
#         result_dfs.append(ticker_result)
#
#     return pd.concat(result_dfs, ignore_index=True)
#
#
# def extract_quarterly_data(ticker_df, forecast_columns):
#     """Extract quarterly data from monthly duplicates"""
#     quarterly_data = []
#
#     for col in forecast_columns:
#         if col not in ticker_df.columns:
#             continue
#
#         values = ticker_df[col].values
#         quarterly_indices = [0]
#
#         for i in range(1, len(values)):
#             if values[i] != values[i - 1]:
#                 quarterly_indices.append(i)
#
#         quarterly_subset = ticker_df.iloc[quarterly_indices].copy()
#         quarterly_subset['quarter'] = pd.PeriodIndex(quarterly_subset['date_month_end'], freq='Q')
#
#         if len(quarterly_data) == 0:
#             quarterly_data = quarterly_subset[['date_month_end', 'quarter'] + [col]].copy()
#         else:
#             quarterly_data = quarterly_data.merge(
#                 quarterly_subset[['date_month_end', col]],
#                 on='date_month_end',
#                 how='outer'
#             )
#
#     return quarterly_data.sort_values('date_month_end').reset_index(drop=True)
#
#
# def calculate_quarterly_ttm(quarterly_data, forecast_columns):
#     """Calculate TTM using quarterly data"""
#     quarterly_ttm = quarterly_data.copy()
#
#     for col in forecast_columns:
#         if col in quarterly_ttm.columns:
#             ttm_col = col.replace('revenue_billions_', 'revenue_ttm_')
#             quarterly_ttm[ttm_col] = quarterly_ttm[col].rolling(window=4, min_periods=1).sum()
#
#     return quarterly_ttm
#
#
# def expand_quarterly_to_monthly(quarterly_ttm, original_monthly_df, forecast_columns):
#     """Expand quarterly TTM data to monthly"""
#     monthly_dates = original_monthly_df[['date_month_end']].copy()
#     monthly_dates['quarter'] = pd.PeriodIndex(monthly_dates['date_month_end'], freq='Q')
#
#     ttm_columns = [col.replace('revenue_billions_', 'revenue_ttm_')
#                    for col in forecast_columns if col in quarterly_ttm.columns]
#
#     quarterly_ttm_subset = quarterly_ttm[['quarter'] + ttm_columns].copy()
#     monthly_ttm = monthly_dates.merge(quarterly_ttm_subset, on='quarter', how='left')
#
#     for col in ttm_columns:
#         if col in monthly_ttm.columns:
#             monthly_ttm[col] = monthly_ttm[col].fillna(method='ffill', limit=2)
#
#     return monthly_ttm[['date_month_end'] + ttm_columns]
#
#
# def merge_ttm_data(original_df, monthly_ttm, forecast_columns, shift_months):
#     """Merge original data with TTM data and apply shift"""
#     result_df = original_df.merge(monthly_ttm, on='date_month_end', how='left')
#
#     ttm_columns = [col.replace('revenue_billions_', 'revenue_ttm_')
#                    for col in forecast_columns if col in original_df.columns]
#
#     for ttm_col in ttm_columns:
#         if ttm_col in result_df.columns:
#             shifted_col = ttm_col + f'_shift{shift_months}m'
#             result_df[shifted_col] = result_df[ttm_col].shift(shift_months)
#
#     return result_df
#
#
# def run_psr_forecast(final_data, export_forecast_start_date="2025-10", USE_EXOGENOUS=True):
#     """Run PSR forecast (simplified version)"""
#     forecast_result = sarima_forecast_with_export(
#         final_data=final_data,
#         export_forecast_start_date=export_forecast_start_date,
#         USE_EXOGENOUS=USE_EXOGENOUS,
#         forecast_months=12
#     )
#
#     return forecast_result