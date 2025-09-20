#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SARIMA ì˜ˆì¸¡ ëª¨ë“ˆ
ì™¸ë¶€ì—ì„œ import ê°€ëŠ¥í•œ ë…ë¦½ì ì¸ ì˜ˆì¸¡ í•¨ìˆ˜ë“¤
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import warnings

# í•„ìˆ˜ ë¼ì´ë¸ŒëŸ¬ë¦¬ import ì‹œë„
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.stattools import adfuller

    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    print("Warning: statsmodelsê°€ ì„¤ì¹˜ë˜ì§€ ì•Šì•˜ìŠµë‹ˆë‹¤.")
    print("pip install statsmodels ë¡œ ì„¤ì¹˜í•´ì£¼ì„¸ìš”.")

from itertools import product

from prophet import Prophet
from statsmodels.tsa.holtwinters import ExponentialSmoothing

warnings.filterwarnings('ignore')


def create_forecast_dates(start_date_str, months=12):
    """ì˜ˆì¸¡ ê¸°ê°„ ë‚ ì§œ ìƒì„±"""
    start_date = pd.to_datetime(start_date_str + "-01")

    forecast_dates = []
    for i in range(months):
        current_date = start_date + relativedelta(months=i)
        month_end = current_date.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
        forecast_dates.append(month_end)

    return forecast_dates


def prepare_export_data(final_data, export_forecast_start_date):
    """ìˆ˜ì¶œ ë°ì´í„° ì¤€ë¹„ ë° ë¶„ë¦¬"""
    forecast_start = pd.to_datetime(export_forecast_start_date + "-01")
    forecast_start_month_end = forecast_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)

    data_sorted = final_data.sort_values('date_month_end').copy()
    export_data = data_sorted[data_sorted['expDlr'].notna()].copy()

    if export_data.empty:
        print("ìˆ˜ì¶œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")
        return None, None, None

    historical_export = export_data[export_data['date_month_end'] < forecast_start_month_end]
    future_export = export_data[export_data['date_month_end'] >= forecast_start_month_end]

    print(f"ê³¼ê±° ìˆ˜ì¶œ ë°ì´í„°: {len(historical_export)}ê°œ")
    print(f"ë¯¸ëž˜ ìˆ˜ì¶œ ì˜ˆì¸¡ì¹˜: {len(future_export)}ê°œ")

    return historical_export, future_export, forecast_start_month_end


def prepare_target_data(final_data, forecast_start_month_end):
    """ì˜ˆì¸¡ ëŒ€ìƒ ë°ì´í„° ì¤€ë¹„"""
    target_data = final_data[final_data['PSR_ttm'].notna()].copy()
    target_data = target_data.sort_values('date_month_end')
    historical_target = target_data[target_data['date_month_end'] < forecast_start_month_end]

    print(f"ê³¼ê±° PSR ë°ì´í„°: {len(historical_target)}ê°œ")
    return historical_target


def check_stationarity(series, name="Series"):
    """ì •ìƒì„± ê²€ì •"""
    if not STATSMODELS_AVAILABLE:
        print(f"{name}: ì •ìƒì„± ê²€ì • ê±´ë„ˆëœ€ (statsmodels ì—†ìŒ)")
        return True

    result = adfuller(series.dropna())

    print(f"\n{name} ì •ìƒì„± ê²€ì •:")
    print(f"ADF Statistic: {result[0]:.4f}")
    print(f"p-value: {result[1]:.4f}")

    if result[1] <= 0.05:
        print("ì‹œê³„ì—´ì´ ì •ìƒì ìž…ë‹ˆë‹¤.")
        return True
    else:
        print("ì‹œê³„ì—´ì´ ë¹„ì •ìƒì ìž…ë‹ˆë‹¤. ì°¨ë¶„ì´ í•„ìš”í•  ìˆ˜ ìžˆìŠµë‹ˆë‹¤.")
        return False


def find_best_sarima_params(y_train, exog_train=None, seasonal_period=12):
    """ìµœì  SARIMA íŒŒë¼ë¯¸í„° ì°¾ê¸°"""
    if not STATSMODELS_AVAILABLE:
        print("statsmodelsê°€ ì—†ì–´ ê¸°ë³¸ íŒŒë¼ë¯¸í„°ë¥¼ ì‚¬ìš©í•©ë‹ˆë‹¤.")
        return (1, 1, 1), (1, 1, 1, seasonal_period)

    print("ìµœì  SARIMA íŒŒë¼ë¯¸í„° íƒìƒ‰ ì¤‘...")

    p_values = [0, 1, 2]
    d_values = [0, 1]
    q_values = [0, 1, 2]

    P_values = [0, 1]
    D_values = [0, 1]
    Q_values = [0, 1]

    best_aic = np.inf
    best_params = None
    best_seasonal_params = None

    total_combinations = len(p_values) * len(d_values) * len(q_values) * len(P_values) * len(D_values) * len(Q_values)
    print(f"ì´ {total_combinations}ê°œ ì¡°í•© í…ŒìŠ¤íŠ¸")

    tested = 0
    for p, d, q in product(p_values, d_values, q_values):
        for P, D, Q in product(P_values, D_values, Q_values):
            try:
                model = SARIMAX(
                    y_train,
                    exog=exog_train,
                    order=(p, d, q),
                    seasonal_order=(P, D, Q, seasonal_period),
                    enforce_stationarity=False,
                    enforce_invertibility=False
                )

                fitted_model = model.fit(disp=False, maxiter=100)

                if fitted_model.aic < best_aic:
                    best_aic = fitted_model.aic
                    best_params = (p, d, q)
                    best_seasonal_params = (P, D, Q, seasonal_period)

                tested += 1
                if tested % 10 == 0:
                    print(f"ì§„í–‰ë¥ : {tested}/{total_combinations}")

            except:
                continue

    if best_params is None:
        print("ìµœì  íŒŒë¼ë¯¸í„°ë¥¼ ì°¾ì„ ìˆ˜ ì—†ì–´ ê¸°ë³¸ê°’ì„ ì‚¬ìš©í•©ë‹ˆë‹¤.")
        best_params = (1, 1, 1)
        best_seasonal_params = (1, 1, 1, seasonal_period)
        best_aic = 0

    print(f"\nìµœì  íŒŒë¼ë¯¸í„°:")
    print(f"ARIMA Order: {best_params}")
    print(f"Seasonal Order: {best_seasonal_params}")
    print(f"Best AIC: {best_aic:.4f}")

    return best_params, best_seasonal_params


def sarima_forecast_with_export(final_data, export_forecast_start_date="2025-10",
                                USE_EXOGENOUS=True, forecast_months=12):
    """
    SARIMA ëª¨ë¸ì„ ì‚¬ìš©í•œ PSR ì˜ˆì¸¡

    Parameters:
    - final_data (pd.DataFrame): ì „ì²˜ë¦¬ëœ ë°ì´í„°
    - export_forecast_start_date (str): ìˆ˜ì¶œ ì˜ˆì¸¡ ì‹œìž‘ì¼ (YYYY-MM í˜•ì‹)
    - USE_EXOGENOUS (bool): ì™¸ìƒë³€ìˆ˜(ìˆ˜ì¶œ ë°ì´í„°) ì‚¬ìš© ì—¬ë¶€
    - forecast_months (int): ì˜ˆì¸¡ ê°œì›” ìˆ˜

    Returns:
    - pd.DataFrame: ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    """

    if not STATSMODELS_AVAILABLE:
        print("Error: statsmodelsê°€ ì„¤ì¹˜ë˜ì§€ ì•Šì•˜ìŠµë‹ˆë‹¤.")
        print("pip install statsmodels ëª…ë ¹ìœ¼ë¡œ ì„¤ì¹˜í•´ì£¼ì„¸ìš”.")
        return None

    print("=" * 70)
    print("SARIMA ì˜ˆì¸¡ ì‹œìž‘")
    print(f"ì˜ˆì¸¡ ì‹œìž‘ì¼: {export_forecast_start_date}")
    print(f"ì™¸ìƒë³€ìˆ˜ ì‚¬ìš©: {USE_EXOGENOUS}")
    print(f"ì˜ˆì¸¡ ê¸°ê°„: {forecast_months}ê°œì›”")
    print("=" * 70)

    # ì˜ˆì¸¡ ì¢…ë£Œì¼ ê³„ì‚°
    export_forecast_end_date = (pd.to_datetime(export_forecast_start_date + "-01") +
                                relativedelta(months=forecast_months - 1)).strftime('%Y-%m')
    print(f"ì˜ˆì¸¡ ì¢…ë£Œì¼: {export_forecast_end_date}")

    # 1. ë°ì´í„° ì¤€ë¹„
    historical_export, future_export, forecast_start_month_end = prepare_export_data(
        final_data, export_forecast_start_date)

    historical_target = prepare_target_data(final_data, forecast_start_month_end)

    if historical_target.empty:
        print("ì˜ˆì¸¡ ëŒ€ìƒ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")
        return None

    # 2. ì‹œê³„ì—´ ë°ì´í„° ì¤€ë¹„
    target_ts = historical_target.set_index('date_month_end')['PSR_ttm'].astype(float)

    # ì™¸ìƒë³€ìˆ˜ ì¤€ë¹„ (YoY)
    exog_train = None
    exog_forecast = None

    if USE_EXOGENOUS and historical_export is not None and not historical_export.empty:
        print("\nì™¸ìƒë³€ìˆ˜(ìˆ˜ì¶œ ë°ì´í„°) ì¤€ë¹„ ì¤‘... (YoY ë³€í™˜)")

        # í•™ìŠµê³¼ ë¯¸ëž˜ë¥¼ í•©ì³ ì—°ì† ì‹œê³„ì—´ êµ¬ì„±
        export_all = pd.concat(
            [
                historical_export[['date_month_end', 'expDlr']],
                future_export[['date_month_end', 'expDlr']] if future_export is not None else pd.DataFrame(
                    columns=['date_month_end', 'expDlr'])
            ],
            ignore_index=True
        ).drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')

        export_all = export_all.set_index('date_month_end')['expDlr'].astype(float)

        # YoY(12ê°œì›” ì „ ëŒ€ë¹„) ê³„ì‚°: ë¹„ìœ¨(ì˜ˆ: 0.08 = +8%)
        export_yoy = export_all.pct_change(12)

        # í•™ìŠµìš© YoY ì‹œë¦¬ì¦ˆë¥¼ íƒ€ê¹ƒê³¼ ì •ë ¬
        target_ts = historical_target.set_index('date_month_end')['PSR_ttm'].astype(float)

        # ìˆ˜ì •ëœ ì½”ë“œ
        # ê³µí†µ ì¸ë±ìŠ¤ë§Œ ì‚¬ìš©í•˜ì—¬ ì•ˆì „í•˜ê²Œ ì •ë ¬
        common_index = target_ts.index.intersection(export_yoy.index)
        if len(common_index) == 0:
            raise ValueError("íƒ€ê²Ÿ ë°ì´í„°ì™€ ì™¸ìƒë³€ìˆ˜ ë°ì´í„°ì˜ ë‚ ì§œê°€ ì „í˜€ ê²¹ì¹˜ì§€ ì•ŠìŠµë‹ˆë‹¤.")

        target_ts = target_ts.loc[common_index]
        export_yoy_train = export_yoy.loc[common_index]



        # ê³µí†µ ë‚ ì§œë§Œ ì‚¬ìš© (NaN ì œê±°)
        common_dates = target_ts.index.intersection(export_yoy_train.dropna().index)

        if len(common_dates) > 0:
            target_ts = target_ts.loc[common_dates]
            exog_train = export_yoy_train.loc[common_dates].values.reshape(-1, 1)

            # ì˜ˆì¸¡ êµ¬ê°„ì˜ ì›”ë§ ë‚ ì§œ ìƒì„± í›„ YoYë¥¼ ë§¤ì¹­
            forecast_dates = pd.to_datetime(create_forecast_dates(export_forecast_start_date, forecast_months))
            exog_forecast_series = export_yoy.reindex(forecast_dates)

            # ì‹œìž‘ ì§í›„ NaN ë°©ì§€ìš© ë³´ê°„/ì±„ì›€ (ì„ íƒ: ffillâ†’bfill)
            exog_forecast = exog_forecast_series.fillna(method='ffill').fillna(method='bfill').values.reshape(-1, 1)

            print(f"ì™¸ìƒë³€ìˆ˜(YoY) ë§¤ì¹­ëœ í•™ìŠµ ê°œì›”: {len(common_dates)}")
            print(f"ë¯¸ëž˜ ìˆ˜ì¶œ YoY ì˜ˆì¸¡ì¹˜ ê°œì›”: {len(exog_forecast)}")
        else:
            print("ìˆ˜ì¶œ YoYì™€ PSR ë‚ ì§œê°€ ë§¤ì¹­ë˜ì§€ ì•Šì•„ ì™¸ìƒë³€ìˆ˜ë¥¼ ì‚¬ìš©í•˜ì§€ ì•ŠìŠµë‹ˆë‹¤.")
            USE_EXOGENOUS = False
            exog_train = None
    else:
        print("ì™¸ìƒë³€ìˆ˜ ë¯¸ì‚¬ìš© ë˜ëŠ” ìˆ˜ì¶œ ë°ì´í„° ë¶€ìž¬ë¡œ ê±´ë„ˆëœ€.")
        USE_EXOGENOUS = False

    # 3. ì •ìƒì„± ê²€ì •
    check_stationarity(target_ts, "PSR")

    if USE_EXOGENOUS and exog_train is not None:
        check_stationarity(pd.Series(exog_train.flatten()), "ìˆ˜ì¶œ ë°ì´í„°")

    # 4. ìµœì  íŒŒë¼ë¯¸í„° ì°¾ê¸°
    best_order, best_seasonal_order = find_best_sarima_params(
        target_ts, exog_train if USE_EXOGENOUS else None)

    if best_order is None:
        print("ìµœì  íŒŒë¼ë¯¸í„°ë¥¼ ì°¾ì„ ìˆ˜ ì—†ìŠµë‹ˆë‹¤.")
        return None

    # 5. ìµœì¢… ëª¨ë¸ í•™ìŠµ
    print(f"\nìµœì¢… SARIMA ëª¨ë¸ í•™ìŠµ ì¤‘...")

    try:
        model = SARIMAX(
            target_ts,
            exog=exog_train if USE_EXOGENOUS else None,
            order=best_order,
            seasonal_order=best_seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        fitted_model = model.fit(disp=False, maxiter=200)

        print("ëª¨ë¸ í•™ìŠµ ì™„ë£Œ!")
        print(f"AIC: {fitted_model.aic:.4f}")

    except Exception as e:
        print(f"ëª¨ë¸ í•™ìŠµ ì‹¤íŒ¨: {e}")
        return None

    # 6. ì˜ˆì¸¡ ìˆ˜í–‰
    print(f"\n{forecast_months}ê°œì›” ì˜ˆì¸¡ ìˆ˜í–‰ ì¤‘...")

    forecast_dates = create_forecast_dates(export_forecast_start_date, forecast_months)

    try:
        if USE_EXOGENOUS and exog_forecast is not None:
            # ì™¸ìƒë³€ìˆ˜ê°€ ë¶€ì¡±í•œ ê²½ìš° ë§ˆì§€ë§‰ ê°’ìœ¼ë¡œ ì±„ì›€
            if len(exog_forecast) < forecast_months:
                last_value = exog_forecast[-1] if len(exog_forecast) > 0 else np.array([[0]])
                missing_count = forecast_months - len(exog_forecast)
                additional_values = np.repeat(last_value, missing_count, axis=0)
                exog_forecast = np.vstack([exog_forecast, additional_values])

            forecast_result = fitted_model.forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            )
            conf_int = fitted_model.get_forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            ).conf_int()
        else:
            forecast_result = fitted_model.forecast(steps=forecast_months)
            conf_int = fitted_model.get_forecast(steps=forecast_months).conf_int()

    except Exception as e:
        print(f"ì˜ˆì¸¡ ì‹¤í–‰ ì‹¤íŒ¨: {e}")
        return None

    # 7. ê²°ê³¼ ì •ë¦¬
    forecast_df = pd.DataFrame({
        'date_month_end': forecast_dates,
        'PSR_forecast': forecast_result.values,
        'PSR_lower': conf_int.iloc[:, 0].values,
        'PSR_upper': conf_int.iloc[:, 1].values,
        'forecast_type': 'SARIMA',
        'use_exogenous': USE_EXOGENOUS
    })

    # ì™¸ìƒë³€ìˆ˜ ì •ë³´ ì¶”ê°€
    if USE_EXOGENOUS and exog_forecast is not None:
        forecast_df['exog_value'] = exog_forecast[:forecast_months].flatten()
    else:
        forecast_df['exog_value'] = np.nan

    print("=" * 70)
    print("SARIMA ì˜ˆì¸¡ ì™„ë£Œ!")
    print(f"ì˜ˆì¸¡ ê²°ê³¼: {len(forecast_df)} ë ˆì½”ë“œ")
    print("=" * 70)

    return forecast_df


def run_psr_forecast(final_data, export_forecast_start_date="2025-10", USE_EXOGENOUS=True):
    """PSR ì˜ˆì¸¡ ì‹¤í–‰ í•¨ìˆ˜ (ê°„íŽ¸ ë²„ì „)"""

    forecast_result = sarima_forecast_with_export(
        final_data=final_data,
        export_forecast_start_date=export_forecast_start_date,
        USE_EXOGENOUS=USE_EXOGENOUS,
        forecast_months=12
    )

    if forecast_result is not None:
        print("\nì˜ˆì¸¡ ê²°ê³¼ ìƒ˜í”Œ:")
        print(forecast_result.head().to_string(index=False))

        print(f"\nì˜ˆì¸¡ í†µê³„:")
        print(f"í‰ê·  PSR: {forecast_result['PSR_forecast'].mean():.2f}")
        print(f"ìµœì†Œ PSR: {forecast_result['PSR_forecast'].min():.2f}")
        print(f"ìµœëŒ€ PSR: {forecast_result['PSR_forecast'].max():.2f}")

    return forecast_result


# ëª¨ë“ˆ í…ŒìŠ¤íŠ¸ í•¨ìˆ˜
def test_sarima_module():
    """ëª¨ë“ˆì´ ì œëŒ€ë¡œ ìž‘ë™í•˜ëŠ”ì§€ í…ŒìŠ¤íŠ¸"""
    print("SARIMA ëª¨ë“ˆ í…ŒìŠ¤íŠ¸")
    print("=" * 30)

    if STATSMODELS_AVAILABLE:
        print("âœ… statsmodels ì‚¬ìš© ê°€ëŠ¥")
    else:
        print("âŒ statsmodels ì„¤ì¹˜ í•„ìš”")
        print("pip install statsmodels")

    print("âœ… pandas, numpy ì‚¬ìš© ê°€ëŠ¥")
    print("âœ… ë‚ ì§œ ì²˜ë¦¬ í•¨ìˆ˜ ì‚¬ìš© ê°€ëŠ¥")

    # ê°„ë‹¨í•œ ë‚ ì§œ í…ŒìŠ¤íŠ¸
    test_dates = create_forecast_dates("2025-10", 3)
    print(f"âœ… ë‚ ì§œ ìƒì„± í…ŒìŠ¤íŠ¸: {len(test_dates)}ê°œ ë‚ ì§œ ìƒì„±")

    print("\nëª¨ë“ˆ import ì„±ê³µ!")
    print("ì‚¬ìš©ë²•:")
    print("from sarima_forecast_module import sarima_forecast_with_export")
    print("result = sarima_forecast_with_export(final_data, '2025-10', True)")


def extract_quarterly_revenue(data, revenue_col='revenue_billions', date_col='date_month_end',
                              data_end_date=None):
    """
    ì›”ë³„ ë°ì´í„°ì—ì„œ ë¶„ê¸°ë³„ ë§¤ì¶œ ì¶”ì¶œ

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - revenue_col: ë§¤ì¶œ ì»¬ëŸ¼ëª…
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼ (ì˜ˆ: '2025-08', '2025-08-31')

    Returns:
    - quarterly_data: ë¶„ê¸°ë³„ ë§¤ì¶œ DataFrame
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ë°ì´í„° ì¢…ë£Œì¼ ì„¤ì •
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"ë°ì´í„°ë¥¼ {end_date.strftime('%Y-%m')}ê¹Œì§€ë¡œ ì œí•œí–ˆìŠµë‹ˆë‹¤.")

    # NaNì´ ì•„ë‹Œ ë§¤ì¶œ ë°ì´í„°ë§Œ ì‚¬ìš©
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")

    print(f"ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°: {len(revenue_data)}ê°œì›”")
    print(
        f"ë°ì´í„° ê¸°ê°„: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # ë¶„ê¸° ì •ë³´ ì¶”ê°€
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # ë¶„ê¸°ë³„ ê·¸ë£¹í™” (ë¶„ê¸° ë§ˆì§€ë§‰ ì›”ì˜ ë°ì´í„° ì‚¬ìš©)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # í•´ë‹¹ ë¶„ê¸°ì˜ ë§ˆì§€ë§‰ ì›” ë°ì´í„° ì‚¬ìš©
        last_month_data = group.loc[group[date_col].idxmax()]

        # ë¶„ê¸°ë§ ë‚ ì§œ ê³„ì‚°
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"ì¶”ì¶œëœ ë¶„ê¸° ë°ì´í„°: {len(quarterly_data)}ë¶„ê¸°")
    print("ë¶„ê¸°ë³„ ë°ì´í„°:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}ê°œì›” ë°ì´í„°)")

    return quarterly_data


def sarima_quarterly_forecast(quarterly_data, forecast_quarters=4):
    """
    ë¶„ê¸°ë³„ ë§¤ì¶œ SARIMA ì˜ˆì¸¡

    Parameters:
    - quarterly_data: ë¶„ê¸°ë³„ ë§¤ì¶œ DataFrame
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜

    Returns:
    - forecast_result: ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - model_info: ëª¨ë¸ ì •ë³´
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values

        if len(revenue_series) < 8:
            raise ValueError(f"SARIMA ëª¨ë¸ë§ì„ ìœ„í•´ ìµœì†Œ 8ë¶„ê¸° ë°ì´í„°ê°€ í•„ìš”í•©ë‹ˆë‹¤. í˜„ìž¬: {len(revenue_series)}ë¶„ê¸°")

        print(f"SARIMA ëª¨ë¸ë§ ì‹œìž‘: {len(revenue_series)}ë¶„ê¸° ë°ì´í„° ì‚¬ìš©")

        # SARIMA íŒŒë¼ë¯¸í„° ê·¸ë¦¬ë“œ ì„œì¹˜
        p_values = [0, 1, 2]
        d_values = [0, 1]
        q_values = [0, 1, 2]
        P_values = [0, 1]
        D_values = [0, 1]
        Q_values = [0, 1]
        s_value = 4  # ë¶„ê¸°ë³„ ê³„ì ˆì„±

        best_aic = float('inf')
        best_params = None
        best_model = None

        print("SARIMA íŒŒë¼ë¯¸í„° ìµœì í™” ì¤‘...")
        tested_models = 0

        for p, d, q, P, D, Q in product(p_values, d_values, q_values, P_values, D_values, Q_values):
            try:
                tested_models += 1

                # íŒŒë¼ë¯¸í„° ìˆ˜ ì œí•œ
                total_params = p + q + P + Q + 1
                if total_params >= len(revenue_series) * 0.4:
                    continue

                model = SARIMAX(
                    revenue_series,
                    order=(p, d, q),
                    seasonal_order=(P, D, Q, s_value),
                    enforce_stationarity=False,
                    enforce_invertibility=False
                )

                fitted_model = model.fit(disp=False, maxiter=100)

                if fitted_model.aic < best_aic:
                    best_aic = fitted_model.aic
                    best_params = (p, d, q, P, D, Q, s_value)
                    best_model = fitted_model

            except Exception:
                continue

        print(f"ì´ {tested_models}ê°œ ëª¨ë¸ í…ŒìŠ¤íŠ¸ ì™„ë£Œ")

        # ìµœì  ëª¨ë¸ì„ ì°¾ì§€ ëª»í•œ ê²½ìš° ê¸°ë³¸ ëª¨ë¸ ì‚¬ìš©
        if best_model is None:
            print("ìµœì  ëª¨ë¸ì„ ì°¾ì§€ ëª»í•´ ê¸°ë³¸ SARIMA(1,1,1) ëª¨ë¸ ì‚¬ìš©")
            model = SARIMAX(
                revenue_series,
                order=(1, 1, 1),
                seasonal_order=(0, 0, 0, 0),
                enforce_stationarity=False,
                enforce_invertibility=False
            )
            best_model = model.fit(disp=False)
            best_params = (1, 1, 1, 0, 0, 0, 0)
            best_aic = best_model.aic

        print(f"ìµœì  ëª¨ë¸: SARIMA{best_params[:3]} x {best_params[3:]} (AIC: {best_aic:.2f})")

        # ì˜ˆì¸¡ ìˆ˜í–‰
        forecast = best_model.forecast(steps=forecast_quarters)
        forecast_values = forecast.values if hasattr(forecast, 'values') else forecast

        # ì‹ ë¢°êµ¬ê°„ ê³„ì‚°
        try:
            prediction_results = best_model.get_prediction(
                start=len(revenue_series),
                end=len(revenue_series) + forecast_quarters - 1
            )
            forecast_ci = prediction_results.conf_int()
            forecast_lower = forecast_ci.iloc[:, 0].values
            forecast_upper = forecast_ci.iloc[:, 1].values
        except:
            print("ì‹ ë¢°êµ¬ê°„ ê³„ì‚° ì‹¤íŒ¨, ê¸°ë³¸ê°’ ì‚¬ìš©")
            forecast_std = np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std

        # ì˜ˆì¸¡ ë‚ ì§œ ìƒì„±
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # ë¶„ê¸°ë§ë¡œ ì¡°ì •
            quarter_end = pd.Timestamp(
                year=next_quarter_date.year,
                month=next_quarter_date.month,
                day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
            )
            forecast_dates.append(quarter_end)

        # ê²°ê³¼ DataFrame ìƒì„±
        forecast_result = pd.DataFrame({
            'date_quarter_end': forecast_dates,
            'year': [d.year for d in forecast_dates],
            'quarter': [d.quarter for d in forecast_dates],
            'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
            'revenue_billions_forecast': forecast_values,
            'forecast_lower': forecast_lower,
            'forecast_upper': forecast_upper
        })

        # ëª¨ë¸ ì •ë³´
        model_info = {
            'params': best_params,
            'aic': best_aic,
            'model': best_model,
            'historical_data_points': len(revenue_series)
        }

        print("ë¶„ê¸°ë³„ SARIMA ì˜ˆì¸¡ ì™„ë£Œ:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"SARIMA ì˜ˆì¸¡ ì‹¤íŒ¨: {e}")
        return None, None


def distribute_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼ë¥¼ ì›”ë³„ë¡œ ë¶„ë°°

    Parameters:
    - quarterly_forecast: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - original_data: ì›ë³¸ ì›”ë³„ ë°ì´í„° DataFrame
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…

    Returns:
    - updated_data: ì›”ë³„ ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_forecast ì»¬ëŸ¼ ì´ˆê¸°í™”
    df['revenue_billions_forecast'] = df['revenue_billions'].copy()

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("ë¶„ê¸°ë³„ ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°° ì¤‘...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_forecast']

        # í•´ë‹¹ ë¶„ê¸°ì˜ ì›”ë“¤
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} ì˜ˆì¸¡ê°’ {quarterly_value:.2f}Bë¥¼ {months_in_quarter}ì›”ì— ë¶„ë°°")

        for month in months_in_quarter:
            # í•´ë‹¹ ë…„ì›”ì˜ ë§ˆì§€ë§‰ ë‚  ê³„ì‚°
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # í•´ë‹¹ ë‚ ì§œì˜ í–‰ ì°¾ê¸°
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: í•´ë‹¹ ë‚ ì§œ ì—†ìŒ")

    return df


def revenue_sarima_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                     data_end_date=None, forecast_quarters=4):
    """
    ë§¤ì¶œ SARIMA ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - revenue_col: ë§¤ì¶œ ì»¬ëŸ¼ëª…
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼ (ì˜ˆ: '2025-08-31')
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜

    Returns:
    - result_data: ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ ë°ì´í„°
    - quarterly_data: ë¶„ê¸°ë³„ ë°ì´í„°
    - forecast_result: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼
    - model_info: ëª¨ë¸ ì •ë³´
    """
    print("=== ë§¤ì¶œ SARIMA ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì‹œìž‘ ===")
    print(f"ë°ì´í„° ì¢…ë£Œì¼: {data_end_date if data_end_date else 'ì „ì²´ ë°ì´í„° ì‚¬ìš©'}")
    print(f"ì˜ˆì¸¡ ë¶„ê¸° ìˆ˜: {forecast_quarters}")

    try:
        # 1. ë¶„ê¸°ë³„ ë°ì´í„° ì¶”ì¶œ
        print("\n1. ë¶„ê¸°ë³„ ë§¤ì¶œ ë°ì´í„° ì¶”ì¶œ")
        quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)

        # 2. SARIMA ì˜ˆì¸¡
        print("\n2. ë¶„ê¸°ë³„ SARIMA ì˜ˆì¸¡")
        forecast_result, model_info = sarima_quarterly_forecast(quarterly_data, forecast_quarters)

        if forecast_result is None:
            print("SARIMA ì˜ˆì¸¡ ì‹¤íŒ¨")
            return None, quarterly_data, None, None

        # 3. ì›”ë³„ ë¶„ë°°
        print("\n3. ë¶„ê¸°ë³„ ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°°")
        result_data = distribute_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== ë§¤ì¶œ SARIMA ì˜ˆì¸¡ ì™„ë£Œ ===")
        print(f"ì˜ˆì¸¡ëœ ë¶„ê¸°: {len(forecast_result)}ê°œ")
        print(f"ì—…ë°ì´íŠ¸ëœ ì›”ë³„ ë°ì´í„°: {len(result_data)}ê°œì›”")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì˜¤ë¥˜: {e}")
        return None, None, None, None


# LSTM ë¶„ê¸°ë³„ ë§¤ì¶œ ì˜ˆì¸¡ í•¨ìˆ˜
import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
import warnings

warnings.filterwarnings('ignore')


def extract_quarterly_revenue_lstm(data, revenue_col='revenue_billions', date_col='date_month_end',
                                   data_end_date=None):
    """
    ì›”ë³„ ë°ì´í„°ì—ì„œ ë¶„ê¸°ë³„ ë§¤ì¶œ ì¶”ì¶œ (LSTMìš©)
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ë°ì´í„° ì¢…ë£Œì¼ ì„¤ì •
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"ë°ì´í„°ë¥¼ {end_date.strftime('%Y-%m')}ê¹Œì§€ë¡œ ì œí•œí–ˆìŠµë‹ˆë‹¤.")

    # NaNì´ ì•„ë‹Œ ë§¤ì¶œ ë°ì´í„°ë§Œ ì‚¬ìš©
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")

    print(f"ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°: {len(revenue_data)}ê°œì›”")
    print(
        f"ë°ì´í„° ê¸°ê°„: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # ë¶„ê¸° ì •ë³´ ì¶”ê°€
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # ë¶„ê¸°ë³„ ê·¸ë£¹í™” (ë¶„ê¸° ë§ˆì§€ë§‰ ì›”ì˜ ë°ì´í„° ì‚¬ìš©)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # í•´ë‹¹ ë¶„ê¸°ì˜ ë§ˆì§€ë§‰ ì›” ë°ì´í„° ì‚¬ìš©
        last_month_data = group.loc[group[date_col].idxmax()]

        # ë¶„ê¸°ë§ ë‚ ì§œ ê³„ì‚°
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"ì¶”ì¶œëœ ë¶„ê¸° ë°ì´í„°: {len(quarterly_data)}ë¶„ê¸°")
    print("ë¶„ê¸°ë³„ ë°ì´í„°:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}ê°œì›” ë°ì´í„°)")

    return quarterly_data


def create_lstm_sequences(data, lookback_window=8):
    """
    LSTMì„ ìœ„í•œ ì‹œí€€ìŠ¤ ë°ì´í„° ìƒì„±
    """
    X, y = [], []
    for i in range(lookback_window, len(data)):
        X.append(data[i - lookback_window:i])
        y.append(data[i])
    return np.array(X), np.array(y)


def lstm_quarterly_forecast(quarterly_data, forecast_quarters=4, lookback_window=8, epochs=100):
    """
    ë¶„ê¸°ë³„ ë§¤ì¶œ LSTM ì˜ˆì¸¡

    Parameters:
    - quarterly_data: ë¶„ê¸°ë³„ ë§¤ì¶œ DataFrame
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜
    - lookback_window: LSTM ìž…ë ¥ ì‹œí€€ìŠ¤ ê¸¸ì´
    - epochs: í›ˆë ¨ ì—í¬í¬ ìˆ˜

    Returns:
    - forecast_result: ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - model_info: ëª¨ë¸ ì •ë³´
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values.astype(np.float64)

        if len(revenue_series) < lookback_window + 4:
            raise ValueError(f"LSTM ëª¨ë¸ë§ì„ ìœ„í•´ ìµœì†Œ {lookback_window + 4}ë¶„ê¸° ë°ì´í„°ê°€ í•„ìš”í•©ë‹ˆë‹¤. í˜„ìž¬: {len(revenue_series)}ë¶„ê¸°")

        print(f"LSTM ëª¨ë¸ë§ ì‹œìž‘: {len(revenue_series)}ë¶„ê¸° ë°ì´í„° ì‚¬ìš©")
        print(f"Lookback window: {lookback_window}ë¶„ê¸°")

        # NaN ì²˜ë¦¬
        if np.isnan(revenue_series).any():
            print("NaN ê°’ ë°œê²¬, ë³´ê°„ ì²˜ë¦¬")
            mask = np.isnan(revenue_series)
            indices = np.where(~mask)[0]
            revenue_series = np.interp(np.arange(len(revenue_series)), indices, revenue_series[indices])

        print(f"ë§¤ì¶œ ë²”ìœ„: {revenue_series.min():.2f}B ~ {revenue_series.max():.2f}B")

        # ë°ì´í„° ì •ê·œí™”
        scaler = MinMaxScaler(feature_range=(0.1, 0.9))  # ì•ˆì •ì ì¸ ë²”ìœ„
        scaled_data = scaler.fit_transform(revenue_series.reshape(-1, 1)).flatten()

        print(f"ì •ê·œí™” í›„ ë²”ìœ„: {scaled_data.min():.4f} ~ {scaled_data.max():.4f}")

        # ì‹œí€€ìŠ¤ ë°ì´í„° ìƒì„±
        X, y = create_lstm_sequences(scaled_data, lookback_window)

        if len(X) == 0:
            raise ValueError("ì‹œí€€ìŠ¤ ìƒì„± ì‹¤íŒ¨: ë°ì´í„°ê°€ ë¶€ì¡±í•©ë‹ˆë‹¤.")

        print(f"ì‹œí€€ìŠ¤ ë°ì´í„°: X={X.shape}, y={y.shape}")

        # LSTM ìž…ë ¥ì„ ìœ„í•œ reshape
        X = X.reshape((X.shape[0], X.shape[1], 1))

        # LSTM ëª¨ë¸ êµ¬ì„±
        model = Sequential([
            LSTM(50, return_sequences=True, input_shape=(lookback_window, 1)),
            LSTM(50, return_sequences=False),
            Dense(25, activation='relu'),
            Dense(1)
        ])

        # ëª¨ë¸ ì»´íŒŒì¼
        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001, clipnorm=1.0),
            loss='mse',
            metrics=['mae']
        )

        print("LSTM ëª¨ë¸ êµ¬ì¡°:")
        print(f"  - LSTM ë ˆì´ì–´ 1: 50 units (return_sequences=True)")
        print(f"  - LSTM ë ˆì´ì–´ 2: 50 units")
        print(f"  - Dense ë ˆì´ì–´: 25 units (ReLU)")
        print(f"  - ì¶œë ¥ ë ˆì´ì–´: 1 unit")

        # ì¡°ê¸° ì¢…ë£Œ ì„¤ì •
        early_stopping = tf.keras.callbacks.EarlyStopping(
            monitor='loss',
            patience=20,
            restore_best_weights=True,
            verbose=0
        )

        # ëª¨ë¸ í›ˆë ¨
        print(f"LSTM ëª¨ë¸ í›ˆë ¨ ì¤‘ (ìµœëŒ€ {epochs} epochs)...")
        history = model.fit(
            X, y,
            epochs=epochs,
            batch_size=min(8, len(X)),
            verbose=0,
            callbacks=[early_stopping]
        )

        final_loss = history.history['loss'][-1]
        trained_epochs = len(history.history['loss'])
        print(f"í›ˆë ¨ ì™„ë£Œ: {trained_epochs} epochs, ìµœì¢… ì†ì‹¤: {final_loss:.6f}")

        # ì˜ˆì¸¡ ìˆ˜í–‰
        print(f"LSTMìœ¼ë¡œ {forecast_quarters}ë¶„ê¸° ì˜ˆì¸¡ ì¤‘...")

        # ë§ˆì§€ë§‰ ì‹œí€€ìŠ¤ë¡œ ì‹œìž‘
        last_sequence = scaled_data[-lookback_window:].copy()
        predictions = []

        for step in range(forecast_quarters):
            # í˜„ìž¬ ì‹œí€€ìŠ¤ë¡œ ì˜ˆì¸¡
            input_seq = last_sequence.reshape(1, lookback_window, 1)
            pred = model.predict(input_seq, verbose=0)
            pred_value = pred[0, 0]

            # NaN ì²´í¬
            if np.isnan(pred_value) or np.isinf(pred_value):
                print(f"ë¹„ì •ìƒ ì˜ˆì¸¡ê°’ ê°ì§€ at step {step + 1}: {pred_value}")
                if predictions:
                    pred_value = np.mean(predictions)
                else:
                    pred_value = last_sequence[-1]
                print(f"ëŒ€ì²´ê°’ ì‚¬ìš©: {pred_value:.4f}")

            # ê°’ ë²”ìœ„ ì œí•œ
            pred_value = np.clip(pred_value, 0.1, 0.9)

            predictions.append(pred_value)

            # ë‹¤ìŒ ì˜ˆì¸¡ì„ ìœ„í•´ ì‹œí€€ìŠ¤ ì—…ë°ì´íŠ¸
            last_sequence = np.roll(last_sequence, -1)
            last_sequence[-1] = pred_value

            print(f"  ì˜ˆì¸¡ {step + 1}: ì •ê·œí™”ê°’={pred_value:.4f}")

        # ì—­ì •ê·œí™”
        predictions_array = np.array(predictions).reshape(-1, 1)
        forecast_values = scaler.inverse_transform(predictions_array).flatten()

        # ìµœì¢… NaN ì²´í¬
        if np.isnan(forecast_values).any() or np.isinf(forecast_values).any():
            print("ìµœì¢… ì˜ˆì¸¡ê°’ì— ë¹„ì •ìƒê°’ ë°œê²¬, ë§ˆì§€ë§‰ ì‹¤ì œê°’ìœ¼ë¡œ ëŒ€ì²´")
            last_actual = revenue_series[-1]
            forecast_values = np.full(forecast_quarters, last_actual)

        print(f"ì—­ì •ê·œí™” ì™„ë£Œ: {forecast_values}")

        # ì˜ˆì¸¡ ë‚ ì§œ ìƒì„±
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # ë¶„ê¸°ë§ë¡œ ì¡°ì •
            quarter_end = pd.Timestamp(
                year=next_quarter_date.year,
                month=next_quarter_date.month,
                day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
            )
            forecast_dates.append(quarter_end)

        # ê²°ê³¼ DataFrame ìƒì„±
        forecast_result = pd.DataFrame({
            'date_quarter_end': forecast_dates,
            'year': [d.year for d in forecast_dates],
            'quarter': [d.quarter for d in forecast_dates],
            'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
            'revenue_billions_lstm_forecast': forecast_values
        })

        # ëª¨ë¸ ì •ë³´
        model_info = {
            'model_type': 'LSTM',
            'lookback_window': lookback_window,
            'trained_epochs': trained_epochs,
            'final_loss': final_loss,
            'scaler': scaler,
            'model': model,
            'historical_data_points': len(revenue_series)
        }

        print("ë¶„ê¸°ë³„ LSTM ì˜ˆì¸¡ ì™„ë£Œ:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_lstm_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"LSTM ì˜ˆì¸¡ ì‹¤íŒ¨: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def distribute_lstm_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    ë¶„ê¸°ë³„ LSTM ì˜ˆì¸¡ ê²°ê³¼ë¥¼ ì›”ë³„ë¡œ ë¶„ë°°

    Parameters:
    - quarterly_forecast: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - original_data: ì›ë³¸ ì›”ë³„ ë°ì´í„° DataFrame
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…

    Returns:
    - updated_data: ì›”ë³„ ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_lstm_forecast ì»¬ëŸ¼ ì´ˆê¸°í™”
    if 'revenue_billions' in df.columns:
        df['revenue_billions_lstm_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_lstm_forecast'] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("ë¶„ê¸°ë³„ LSTM ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°° ì¤‘...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_lstm_forecast']

        # í•´ë‹¹ ë¶„ê¸°ì˜ ì›”ë“¤
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} LSTM ì˜ˆì¸¡ê°’ {quarterly_value:.2f}Bë¥¼ {months_in_quarter}ì›”ì— ë™ì¼í•˜ê²Œ ì ìš©")

        for month in months_in_quarter:
            # í•´ë‹¹ ë…„ì›”ì˜ ë§ˆì§€ë§‰ ë‚  ê³„ì‚°
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # í•´ë‹¹ ë‚ ì§œì˜ í–‰ ì°¾ê¸°
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_lstm_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: í•´ë‹¹ ë‚ ì§œ ì—†ìŒ")

    return df


def revenue_lstm_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                   data_end_date=None, forecast_quarters=4, lookback_window=8, epochs=100):
    """
    ë§¤ì¶œ LSTM ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - revenue_col: ë§¤ì¶œ ì»¬ëŸ¼ëª…
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼ (ì˜ˆ: '2025-08-31')
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜
    - lookback_window: LSTM ìž…ë ¥ ì‹œí€€ìŠ¤ ê¸¸ì´
    - epochs: í›ˆë ¨ ì—í¬í¬ ìˆ˜

    Returns:
    - result_data: ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ ë°ì´í„°
    - quarterly_data: ë¶„ê¸°ë³„ ë°ì´í„°
    - forecast_result: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼
    - model_info: ëª¨ë¸ ì •ë³´
    """
    print("=== ë§¤ì¶œ LSTM ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì‹œìž‘ ===")
    print(f"ë°ì´í„° ì¢…ë£Œì¼: {data_end_date if data_end_date else 'ì „ì²´ ë°ì´í„° ì‚¬ìš©'}")
    print(f"ì˜ˆì¸¡ ë¶„ê¸° ìˆ˜: {forecast_quarters}")
    print(f"Lookback window: {lookback_window}ë¶„ê¸°")
    print(f"í›ˆë ¨ epochs: {epochs}")

    try:
        # 1. ë¶„ê¸°ë³„ ë°ì´í„° ì¶”ì¶œ
        print("\n1. ë¶„ê¸°ë³„ ë§¤ì¶œ ë°ì´í„° ì¶”ì¶œ")
        quarterly_data = extract_quarterly_revenue_lstm(data, revenue_col, date_col, data_end_date)

        # 2. LSTM ì˜ˆì¸¡
        print("\n2. ë¶„ê¸°ë³„ LSTM ì˜ˆì¸¡")
        forecast_result, model_info = lstm_quarterly_forecast(
            quarterly_data, forecast_quarters, lookback_window, epochs
        )

        if forecast_result is None:
            print("LSTM ì˜ˆì¸¡ ì‹¤íŒ¨")
            return None, quarterly_data, None, None

        # 3. ì›”ë³„ ë¶„ë°°
        print("\n3. ë¶„ê¸°ë³„ LSTM ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°°")
        result_data = distribute_lstm_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== ë§¤ì¶œ LSTM ì˜ˆì¸¡ ì™„ë£Œ ===")
        print(f"ì˜ˆì¸¡ëœ ë¶„ê¸°: {len(forecast_result)}ê°œ")
        print(f"ì—…ë°ì´íŠ¸ëœ ì›”ë³„ ë°ì´í„°: {len(result_data)}ê°œì›”")
        print("ì¶”ê°€ëœ ì»¬ëŸ¼: revenue_billions_lstm_forecast")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"LSTM ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì˜¤ë¥˜: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None

def extract_quarterly_revenue_prophet(data, revenue_col='revenue_billions', date_col='date_month_end',
                                      data_end_date=None):
    """
    ì›”ë³„ ë°ì´í„°ì—ì„œ ë¶„ê¸°ë³„ ë§¤ì¶œ ì¶”ì¶œ (Prophetìš©)
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ë°ì´í„° ì¢…ë£Œì¼ ì„¤ì •
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"ë°ì´í„°ë¥¼ {end_date.strftime('%Y-%m')}ê¹Œì§€ë¡œ ì œí•œí–ˆìŠµë‹ˆë‹¤.")

    # NaNì´ ì•„ë‹Œ ë§¤ì¶œ ë°ì´í„°ë§Œ ì‚¬ìš©
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")

    print(f"ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°: {len(revenue_data)}ê°œì›”")
    print(
        f"ë°ì´í„° ê¸°ê°„: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # ë¶„ê¸° ì •ë³´ ì¶”ê°€
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # ë¶„ê¸°ë³„ ê·¸ë£¹í™” (ë¶„ê¸° ë§ˆì§€ë§‰ ì›”ì˜ ë°ì´í„° ì‚¬ìš©)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # í•´ë‹¹ ë¶„ê¸°ì˜ ë§ˆì§€ë§‰ ì›” ë°ì´í„° ì‚¬ìš©
        last_month_data = group.loc[group[date_col].idxmax()]

        # ë¶„ê¸°ë§ ë‚ ì§œ ê³„ì‚°
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"ì¶”ì¶œëœ ë¶„ê¸° ë°ì´í„°: {len(quarterly_data)}ë¶„ê¸°")
    print("ë¶„ê¸°ë³„ ë°ì´í„°:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}ê°œì›” ë°ì´í„°)")

    return quarterly_data

def prepare_prophet_data(quarterly_data, exog_data=None):
    """
    Prophetì„ ìœ„í•œ ë°ì´í„° ì¤€ë¹„ (ê¹”ë”í•œ ë²„ì „)

    Parameters:
    - quarterly_data: ë¶„ê¸°ë³„ ë§¤ì¶œ ë°ì´í„°
    - exog_data: ì™¸ìƒë³€ìˆ˜ ë°ì´í„° (ì˜µì…˜)

    Returns:
    - prophet_df: Prophet í˜•ì‹ DataFrame
    """
    # Prophet í˜•ì‹ìœ¼ë¡œ ë³€í™˜ (ds, y ì»¬ëŸ¼ í•„ìš”)
    prophet_df = pd.DataFrame({
        'ds': quarterly_data['date_quarter_end'],
        'y': quarterly_data['revenue_billions']
    })

    # y ì»¬ëŸ¼ NaNì´ ìžˆëŠ” í–‰ ì œê±°
    if prophet_df['y'].isna().any():
        original_len = len(prophet_df)
        prophet_df = prophet_df.dropna(subset=['y'])
        removed = original_len - len(prophet_df)
        print(f"ë§¤ì¶œ ë°ì´í„° NaN ì œê±°: {removed}ê°œ í–‰ ì œê±°ë¨")

    # ì™¸ìƒë³€ìˆ˜ ì¶”ê°€ (ìžˆëŠ” ê²½ìš°)
    if exog_data is not None:
        print("ì™¸ìƒë³€ìˆ˜ ë°ì´í„° ì¶”ê°€ ì¤‘...")

        # ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼ë“¤ í™•ì¸
        exog_cols = [col for col in exog_data.columns
                     if col not in ['date_quarter_end', 'year', 'quarter', 'year_quarter']]

        print(f"ì¶”ê°€í•  ì™¸ìƒë³€ìˆ˜: {exog_cols}")

        # ë‚ ì§œë¥¼ ê¸°ì¤€ìœ¼ë¡œ ë§¤í•‘
        for _, exog_row in exog_data.iterrows():
            exog_date = exog_row['date_quarter_end']
            mask = prophet_df['ds'] == exog_date

            if mask.any():
                for col in exog_cols:
                    prophet_df.loc[mask, col] = exog_row[col]

        # ì™¸ìƒë³€ìˆ˜ê°€ ë§¤í•‘ë˜ì§€ ì•Šì€ í–‰ ì œê±° (inner join íš¨ê³¼)
        for col in exog_cols:
            if col in prophet_df.columns:
                original_len = len(prophet_df)
                prophet_df = prophet_df.dropna(subset=[col])
                removed = original_len - len(prophet_df)
                if removed > 0:
                    print(f"ì™¸ìƒë³€ìˆ˜ {col} ë§¤í•‘ ì‹¤íŒ¨ë¡œ {removed}ê°œ í–‰ ì œê±°ë¨")

        print(f"ì¶”ê°€ëœ ì™¸ìƒë³€ìˆ˜: {exog_cols}")

    print(f"Prophet ë°ì´í„° ì¤€ë¹„ ì™„ë£Œ: {len(prophet_df)}ê°œ ë¶„ê¸°")

    return prophet_df

def extract_exogenous_variables(data, date_col='date_month_end', data_end_date=None,
                                exog_cols=['expDlr']):
    """
    ì™¸ìƒë³€ìˆ˜ ì¶”ì¶œ ë° ë¶„ê¸°ë³„ ë³€í™˜ (NaN ê°’ ì œê±°)

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼
    - exog_cols: ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼ëª… ë¦¬ìŠ¤íŠ¸

    Returns:
    - quarterly_exog: ë¶„ê¸°ë³„ ì™¸ìƒë³€ìˆ˜ DataFrame
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ë°ì´í„° ì¢…ë£Œì¼ ì„¤ì •
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]

    # ì™¸ìƒë³€ìˆ˜ ë°ì´í„° í™•ì¸
    available_exog_cols = [col for col in exog_cols if col in df.columns]
    if not available_exog_cols:
        print(f"ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼ì„ ì°¾ì„ ìˆ˜ ì—†ìŠµë‹ˆë‹¤: {exog_cols}")
        return None

    print(f"ì‚¬ìš© ê°€ëŠ¥í•œ ì™¸ìƒë³€ìˆ˜: {available_exog_cols}")

    # NaNì´ ìžˆëŠ” í–‰ ì œê±° (dropna ì‚¬ìš©)
    original_len = len(df)
    df_clean = df.dropna(subset=available_exog_cols)
    removed_rows = original_len - len(df_clean)

    print(f"ì™¸ìƒë³€ìˆ˜ NaN ì œê±°: {removed_rows}ê°œ í–‰ ì œê±°ë¨ (ì „ì²´ {original_len}ê°œ ì¤‘)")

    if len(df_clean) == 0:
        print("ì™¸ìƒë³€ìˆ˜ NaN ì œê±° í›„ ìœ íš¨í•œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")
        return None

    exog_data = df_clean.copy()

    # ë¶„ê¸° ì •ë³´ ì¶”ê°€
    exog_data['year'] = exog_data[date_col].dt.year
    exog_data['quarter'] = exog_data[date_col].dt.quarter

    # ë¶„ê¸°ë³„ ê·¸ë£¹í™” (ë¶„ê¸° ë§ˆì§€ë§‰ ì›”ì˜ ë°ì´í„° ì‚¬ìš©)
    quarterly_exog_list = []

    for (year, quarter), group in exog_data.groupby(['year', 'quarter']):
        # í•´ë‹¹ ë¶„ê¸°ì˜ ë§ˆì§€ë§‰ ì›” ë°ì´í„° ì‚¬ìš©
        last_month_data = group.loc[group[date_col].idxmax()]

        # ë¶„ê¸°ë§ ë‚ ì§œ ê³„ì‚°
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        exog_dict = {
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}"
        }

        # ì™¸ìƒë³€ìˆ˜ ê°’ ì¶”ê°€
        for col in available_exog_cols:
            exog_dict[col] = last_month_data[col]

        quarterly_exog_list.append(exog_dict)

    quarterly_exog = pd.DataFrame(quarterly_exog_list)
    quarterly_exog = quarterly_exog.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"ì¶”ì¶œëœ ë¶„ê¸°ë³„ ì™¸ìƒë³€ìˆ˜ ë°ì´í„°: {len(quarterly_exog)}ë¶„ê¸°")

    return quarterly_exog

def prophet_quarterly_forecast(prophet_df, forecast_quarters=4, use_exog=False, exog_cols=None):
    """
    ë¶„ê¸°ë³„ ë§¤ì¶œ Prophet ì˜ˆì¸¡

    Parameters:
    - prophet_df: Prophet í˜•ì‹ ë°ì´í„°
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜
    - use_exog: ì™¸ìƒë³€ìˆ˜ ì‚¬ìš© ì—¬ë¶€
    - exog_cols: ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼ ë¦¬ìŠ¤íŠ¸

    Returns:
    - forecast_result: ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - model_info: ëª¨ë¸ ì •ë³´
    """
    try:
        print(f"Prophet ëª¨ë¸ë§ ì‹œìž‘ ({'ì™¸ìƒë³€ìˆ˜ í¬í•¨' if use_exog else 'ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨'})")

        # Prophet ëª¨ë¸ ìƒì„±
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode='additive',
            changepoint_prior_scale=0.05
        )

        # ë¶„ê¸° ê³„ì ˆì„± ì¶”ê°€
        model.add_seasonality(name='quarterly', period=365.25 / 4, fourier_order=4)

        # ì™¸ìƒë³€ìˆ˜ ì¶”ê°€ (ìžˆëŠ” ê²½ìš°)
        if use_exog and exog_cols:
            for col in exog_cols:
                if col in prophet_df.columns:
                    model.add_regressor(col)
                    print(f"ì™¸ìƒë³€ìˆ˜ ì¶”ê°€: {col}")

        print("Prophet ëª¨ë¸ í›ˆë ¨ ì¤‘...")
        model.fit(prophet_df)

        # ë¯¸ëž˜ ë‚ ì§œ ìƒì„±
        last_date = prophet_df['ds'].iloc[-1]
        future_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # ë¶„ê¸°ë§ë¡œ ì¡°ì •
            quarter_end = pd.Timestamp(
                year=next_quarter_date.year,
                month=next_quarter_date.month,
                day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
            )
            future_dates.append(quarter_end)

        # Future DataFrame ìƒì„±
        future_df = model.make_future_dataframe(periods=forecast_quarters, freq='QS')

        # ì™¸ìƒë³€ìˆ˜ì˜ ë¯¸ëž˜ ê°’ ì„¤ì • (ìžˆëŠ” ê²½ìš°)
        if use_exog and exog_cols:
            print("ì™¸ìƒë³€ìˆ˜ì˜ ë¯¸ëž˜ ê°’ ì„¤ì • ì¤‘...")
            for col in exog_cols:
                if col in prophet_df.columns:
                    # ë§ˆì§€ë§‰ ê°’ ì‚¬ìš© (ë˜ëŠ” íŠ¸ë Œë“œ ì—°ìž¥)
                    last_value = prophet_df[col].iloc[-1]

                    # ìµœê·¼ íŠ¸ë Œë“œ ê³„ì‚° (ìµœê·¼ 4ë¶„ê¸°)
                    recent_data = prophet_df[col].tail(4)
                    if len(recent_data) >= 2:
                        trend = np.mean(np.diff(recent_data))
                    else:
                        trend = 0

                    # ë¯¸ëž˜ ê°’ ìƒì„±
                    for i, future_date in enumerate(future_dates):
                        mask = future_df['ds'] == future_date
                        if mask.any():
                            # ì˜µì…˜ 1: ë§ˆì§€ë§‰ ê°’ ìœ ì§€
                            future_value = last_value
                            # ì˜µì…˜ 2: íŠ¸ë Œë“œ ì—°ìž¥ (ì£¼ì„ í•´ì œí•˜ì—¬ ì‚¬ìš©)
                            # future_value = last_value + trend * (i + 1)

                            future_df.loc[mask, col] = future_value
                            print(f"  {future_date.strftime('%Y-Q%m')} {col}: {future_value:.2f}")

        # ì˜ˆì¸¡ ìˆ˜í–‰
        forecast = model.predict(future_df)

        # ì˜ˆì¸¡ ë¶€ë¶„ë§Œ ì¶”ì¶œ
        forecast_only = forecast.tail(forecast_quarters).copy()

        # ê²°ê³¼ DataFrame ìƒì„±
        forecast_result = pd.DataFrame({
            'date_quarter_end': future_dates,
            'year': [d.year for d in future_dates],
            'quarter': [d.quarter for d in future_dates],
            'year_quarter': [f"{d.year}Q{d.quarter}" for d in future_dates],
            'revenue_billions_prophet_forecast' + ('_exog' if use_exog else ''): forecast_only['yhat'].values,
            'forecast_lower': forecast_only['yhat_lower'].values,
            'forecast_upper': forecast_only['yhat_upper'].values
        })

        # ëª¨ë¸ ì •ë³´
        model_info = {
            'model_type': 'Prophet',
            'use_exogenous': use_exog,
            'exogenous_variables': exog_cols if use_exog else None,
            'model': model,
            'historical_data_points': len(prophet_df)
        }

        forecast_col = 'revenue_billions_prophet_forecast' + ('_exog' if use_exog else '')
        print(f"ë¶„ê¸°ë³„ Prophet ì˜ˆì¸¡ ì™„ë£Œ ({'ì™¸ìƒë³€ìˆ˜ í¬í•¨' if use_exog else 'ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨'}):")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row[forecast_col]:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"Prophet ì˜ˆì¸¡ ì‹¤íŒ¨: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def distribute_prophet_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end',
                                            use_exog=False):
    """
    ë¶„ê¸°ë³„ Prophet ì˜ˆì¸¡ ê²°ê³¼ë¥¼ ì›”ë³„ë¡œ ë¶„ë°°

    Parameters:
    - quarterly_forecast: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - original_data: ì›ë³¸ ì›”ë³„ ë°ì´í„° DataFrame
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - use_exog: ì™¸ìƒë³€ìˆ˜ ì‚¬ìš© ì—¬ë¶€

    Returns:
    - updated_data: ì›”ë³„ ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ì»¬ëŸ¼ëª… ì„¤ì •
    forecast_col = 'revenue_billions_prophet_forecast' + ('_exog' if use_exog else '')

    # ì˜ˆì¸¡ ì»¬ëŸ¼ ì´ˆê¸°í™”
    if 'revenue_billions' in df.columns:
        df[forecast_col] = df['revenue_billions'].copy()
    else:
        df[forecast_col] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    exog_text = 'ì™¸ìƒë³€ìˆ˜ í¬í•¨ ' if use_exog else ''
    print(f"ë¶„ê¸°ë³„ Prophet {exog_text}ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°° ì¤‘...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row[forecast_col]

        # í•´ë‹¹ ë¶„ê¸°ì˜ ì›”ë“¤
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} Prophet {exog_text}ì˜ˆì¸¡ê°’ {quarterly_value:.2f}Bë¥¼ {months_in_quarter}ì›”ì— ë™ì¼í•˜ê²Œ ì ìš©")

        for month in months_in_quarter:
            # í•´ë‹¹ ë…„ì›”ì˜ ë§ˆì§€ë§‰ ë‚  ê³„ì‚°
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # í•´ë‹¹ ë‚ ì§œì˜ í–‰ ì°¾ê¸°
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, forecast_col] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: í•´ë‹¹ ë‚ ì§œ ì—†ìŒ")

    return df

def revenue_prophet_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                      data_end_date=None, forecast_quarters=4,
                                      use_exogenous=True, exog_cols=['expDlr']):
    """
    ë§¤ì¶œ Prophet ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ (ì™¸ìƒë³€ìˆ˜ í¬í•¨/ë¯¸í¬í•¨ ë‘˜ ë‹¤)

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - revenue_col: ë§¤ì¶œ ì»¬ëŸ¼ëª…
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼ (ì˜ˆ: '2025-08-31')
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜
    - use_exogenous: ì™¸ìƒë³€ìˆ˜ ì‚¬ìš© ì—¬ë¶€
    - exog_cols: ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼ ë¦¬ìŠ¤íŠ¸

    Returns:
    - result_data: ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ ë°ì´í„°
    - quarterly_data: ë¶„ê¸°ë³„ ë°ì´í„°
    - forecast_results: ì˜ˆì¸¡ ê²°ê³¼ ë”•ì…”ë„ˆë¦¬ {'no_exog': ..., 'with_exog': ...}
    - model_infos: ëª¨ë¸ ì •ë³´ ë”•ì…”ë„ˆë¦¬
    """
    print("=== ë§¤ì¶œ Prophet ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì‹œìž‘ ===")
    print(f"ë°ì´í„° ì¢…ë£Œì¼: {data_end_date if data_end_date else 'ì „ì²´ ë°ì´í„° ì‚¬ìš©'}")
    print(f"ì˜ˆì¸¡ ë¶„ê¸° ìˆ˜: {forecast_quarters}")
    print(f"ì™¸ìƒë³€ìˆ˜ ì‚¬ìš©: {use_exogenous}")
    if use_exogenous:
        print(f"ì™¸ìƒë³€ìˆ˜ ì»¬ëŸ¼: {exog_cols}")

    try:
        # 1. ë¶„ê¸°ë³„ ë°ì´í„° ì¶”ì¶œ
        print("\n1. ë¶„ê¸°ë³„ ë§¤ì¶œ ë°ì´í„° ì¶”ì¶œ")
        quarterly_data = extract_quarterly_revenue_prophet(data, revenue_col, date_col, data_end_date)

        result_data = data.copy()
        forecast_results = {}
        model_infos = {}

        # 2. ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨ Prophet ì˜ˆì¸¡
        print("\n2. Prophet ì˜ˆì¸¡ (ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨)")
        prophet_df_no_exog = prepare_prophet_data(quarterly_data)

        forecast_no_exog, model_info_no_exog = prophet_quarterly_forecast(
            prophet_df_no_exog, forecast_quarters, use_exog=False
        )

        if forecast_no_exog is not None:
            print("ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨ Prophet ì˜ˆì¸¡ ì„±ê³µ")
            result_data = distribute_prophet_quarterly_to_monthly(
                forecast_no_exog, result_data, date_col, use_exog=False
            )
            forecast_results['no_exog'] = forecast_no_exog
            model_infos['no_exog'] = model_info_no_exog
        else:
            print("ì™¸ìƒë³€ìˆ˜ ë¯¸í¬í•¨ Prophet ì˜ˆì¸¡ ì‹¤íŒ¨")
            forecast_results['no_exog'] = None
            model_infos['no_exog'] = None

        # 3. ì™¸ìƒë³€ìˆ˜ í¬í•¨ Prophet ì˜ˆì¸¡
        if use_exogenous:
            print("\n3. Prophet ì˜ˆì¸¡ (ì™¸ìƒë³€ìˆ˜ í¬í•¨)")

            # ì™¸ìƒë³€ìˆ˜ ë°ì´í„° ì¶”ì¶œ
            quarterly_exog = extract_exogenous_variables(data, date_col, data_end_date, exog_cols)

            if quarterly_exog is not None:
                prophet_df_with_exog = prepare_prophet_data(quarterly_data, quarterly_exog)

                forecast_with_exog, model_info_with_exog = prophet_quarterly_forecast(
                    prophet_df_with_exog, forecast_quarters, use_exog=True, exog_cols=exog_cols
                )

                if forecast_with_exog is not None:
                    print("ì™¸ìƒë³€ìˆ˜ í¬í•¨ Prophet ì˜ˆì¸¡ ì„±ê³µ")
                    result_data = distribute_prophet_quarterly_to_monthly(
                        forecast_with_exog, result_data, date_col, use_exog=True
                    )
                    forecast_results['with_exog'] = forecast_with_exog
                    model_infos['with_exog'] = model_info_with_exog
                else:
                    print("ì™¸ìƒë³€ìˆ˜ í¬í•¨ Prophet ì˜ˆì¸¡ ì‹¤íŒ¨")
                    forecast_results['with_exog'] = None
                    model_infos['with_exog'] = None
            else:
                print("ì™¸ìƒë³€ìˆ˜ ë°ì´í„° ì—†ìŒ - ì™¸ìƒë³€ìˆ˜ í¬í•¨ ì˜ˆì¸¡ ê±´ë„ˆëœ€")
                forecast_results['with_exog'] = None
                model_infos['with_exog'] = None
        else:
            forecast_results['with_exog'] = None
            model_infos['with_exog'] = None

        print("\n=== ë§¤ì¶œ Prophet ì˜ˆì¸¡ ì™„ë£Œ ===")

        # ì„±ê³µì ìœ¼ë¡œ ì™„ë£Œëœ ì˜ˆì¸¡ ìˆ˜ í™•ì¸
        successful_predictions = sum(1 for result in forecast_results.values() if result is not None)
        print(f"ì™„ë£Œëœ ì˜ˆì¸¡: {successful_predictions}/{'2' if use_exogenous else '1'}ê°œ")

        if successful_predictions > 0:
            print("ì¶”ê°€ëœ ì»¬ëŸ¼:")
            if forecast_results['no_exog'] is not None:
                print("  - revenue_billions_prophet_forecast")
            if forecast_results['with_exog'] is not None:
                print("  - revenue_billions_prophet_forecast_exog")

        return result_data, quarterly_data, forecast_results, model_infos

    except Exception as e:
        print(f"Prophet ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì˜¤ë¥˜: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None

def extract_quarterly_revenue_es(data, revenue_col='revenue_billions', date_col='date_month_end',
                                 data_end_date=None):

    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # ë°ì´í„° ì¢…ë£Œì¼ ì„¤ì •
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"ë°ì´í„°ë¥¼ {end_date.strftime('%Y-%m')}ê¹Œì§€ë¡œ ì œí•œí–ˆìŠµë‹ˆë‹¤.")

    # NaNì´ ì•„ë‹Œ ë§¤ì¶œ ë°ì´í„°ë§Œ ì‚¬ìš©
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°ê°€ ì—†ìŠµë‹ˆë‹¤.")

    print(f"ìœ íš¨í•œ ë§¤ì¶œ ë°ì´í„°: {len(revenue_data)}ê°œì›”")
    print(
        f"ë°ì´í„° ê¸°ê°„: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # ë¶„ê¸° ì •ë³´ ì¶”ê°€
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # ë¶„ê¸°ë³„ ê·¸ë£¹í™” (ë¶„ê¸° ë§ˆì§€ë§‰ ì›”ì˜ ë°ì´í„° ì‚¬ìš©)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # í•´ë‹¹ ë¶„ê¸°ì˜ ë§ˆì§€ë§‰ ì›” ë°ì´í„° ì‚¬ìš©
        last_month_data = group.loc[group[date_col].idxmax()]

        # ë¶„ê¸°ë§ ë‚ ì§œ ê³„ì‚°
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"ì¶”ì¶œëœ ë¶„ê¸° ë°ì´í„°: {len(quarterly_data)}ë¶„ê¸°")
    print("ë¶„ê¸°ë³„ ë°ì´í„°:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}ê°œì›” ë°ì´í„°)")

    return quarterly_data

def exponential_smoothing_quarterly_forecast(quarterly_data, forecast_quarters=4):
    """
    ë¶„ê¸°ë³„ ë§¤ì¶œ Exponential Smoothing ì˜ˆì¸¡

    Parameters:
    - quarterly_data: ë¶„ê¸°ë³„ ë§¤ì¶œ DataFrame
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜

    Returns:
    - forecast_result: ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - model_info: ëª¨ë¸ ì •ë³´
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values

        if len(revenue_series) < 8:
            raise ValueError(f"Exponential Smoothing ëª¨ë¸ë§ì„ ìœ„í•´ ìµœì†Œ 8ë¶„ê¸° ë°ì´í„°ê°€ í•„ìš”í•©ë‹ˆë‹¤. í˜„ìž¬: {len(revenue_series)}ë¶„ê¸°")

        print(f"Exponential Smoothing ëª¨ë¸ë§ ì‹œìž‘: {len(revenue_series)}ë¶„ê¸° ë°ì´í„° ì‚¬ìš©")

        # ì‹œê³„ì—´ ë°ì´í„°ë¡œ ë³€í™˜ (ë‚ ì§œ ì¸ë±ìŠ¤ ì‚¬ìš©)
        ts_data = pd.Series(
            revenue_series,
            index=pd.date_range(
                start=quarterly_data['date_quarter_end'].iloc[0],
                periods=len(revenue_series),
                freq='QS'
            )
        )

        print(f"ë§¤ì¶œ ë²”ìœ„: {revenue_series.min():.2f}B ~ {revenue_series.max():.2f}B")

        # ë‹¤ì–‘í•œ Exponential Smoothing ëª¨ë¸ ì‹œë„
        models_to_try = [
            # (trend, seasonal, damped_trend, seasonal_periods)
            ('add', 'add', False, 4),  # Holt-Winters Additive
            ('add', 'mul', False, 4),  # Holt-Winters Multiplicative
            ('add', 'add', True, 4),  # Damped Holt-Winters Additive
            ('add', 'mul', True, 4),  # Damped Holt-Winters Multiplicative
            ('add', None, False, None),  # Holt's Linear Trend
            ('add', None, True, None),  # Damped Holt's Linear Trend
            (None, None, False, None)  # Simple Exponential Smoothing
        ]

        best_aic = float('inf')
        best_model = None
        best_config = None

        print("ë‹¤ì–‘í•œ Exponential Smoothing ëª¨ë¸ í…ŒìŠ¤íŠ¸ ì¤‘...")

        for i, (trend, seasonal, damped, seasonal_periods) in enumerate(models_to_try):
            try:
                # ê³„ì ˆì„± ì‚¬ìš© ì‹œ ë°ì´í„° ê¸¸ì´ í™•ì¸
                if seasonal is not None and seasonal_periods is not None:
                    if len(revenue_series) < seasonal_periods * 2:
                        print(f"  ëª¨ë¸ {i + 1}: ê³„ì ˆì„± ëª¨ë¸ì„ ìœ„í•œ ë°ì´í„° ë¶€ì¡± (ê±´ë„ˆëœ€)")
                        continue

                # ëª¨ë¸ ìƒì„±
                if seasonal is not None and seasonal_periods is not None:
                    model = ExponentialSmoothing(
                        ts_data,
                        trend=trend,
                        seasonal=seasonal,
                        damped_trend=damped,
                        seasonal_periods=seasonal_periods
                    )
                    model_name = f"Holt-Winters ({seasonal})"
                else:
                    model = ExponentialSmoothing(
                        ts_data,
                        trend=trend,
                        damped_trend=damped
                    )
                    if trend is not None:
                        model_name = f"Holt ({'Damped' if damped else 'Linear'})"
                    else:
                        model_name = "Simple ES"

                # ëª¨ë¸ í”¼íŒ…
                fitted_model = model.fit(optimized=True, use_brute=False)

                # AIC ë¹„êµ
                if fitted_model.aic < best_aic:
                    best_aic = fitted_model.aic
                    best_model = fitted_model
                    best_config = (trend, seasonal, damped, seasonal_periods, model_name)

                print(f"  ëª¨ë¸ {i + 1} ({model_name}): AIC = {fitted_model.aic:.2f}")

            except Exception as e:
                print(f"  ëª¨ë¸ {i + 1}: ì‹¤íŒ¨ ({str(e)[:50]}...)")
                continue

        # ìµœì  ëª¨ë¸ì„ ì°¾ì§€ ëª»í•œ ê²½ìš° Simple ES ì‚¬ìš©
        if best_model is None:
            print("ìµœì  ëª¨ë¸ì„ ì°¾ì§€ ëª»í•´ Simple Exponential Smoothing ì‚¬ìš©")
            model = ExponentialSmoothing(ts_data, trend=None)
            best_model = model.fit(optimized=True)
            best_config = (None, None, False, None, "Simple ES (Fallback)")
            best_aic = best_model.aic

        trend, seasonal, damped, seasonal_periods, model_name = best_config
        print(f"\nìµœì  ëª¨ë¸: {model_name} (AIC: {best_aic:.2f})")

        # ëª¨ë¸ íŒŒë¼ë¯¸í„° ì¶œë ¥ (ì•ˆì „í•˜ê²Œ)
        try:
            print("ëª¨ë¸ íŒŒë¼ë¯¸í„°:")
            if hasattr(best_model, 'params'):
                for param_name, param_value in best_model.params.items():
                    if param_value is not None:
                        try:
                            if isinstance(param_value, (list, np.ndarray)):
                                if len(param_value) == 1:
                                    print(f"  - {param_name}: {float(param_value[0]):.4f}")
                                else:
                                    print(f"  - {param_name}: {[float(x) for x in param_value[:3]]}")
                            else:
                                print(f"  - {param_name}: {float(param_value):.4f}")
                        except (ValueError, TypeError):
                            print(f"  - {param_name}: {str(param_value)}")
        except Exception:
            print("ëª¨ë¸ íŒŒë¼ë¯¸í„° ì¶œë ¥ ìƒëžµ")

        # ì˜ˆì¸¡ ìˆ˜í–‰
        print(f"Exponential Smoothingìœ¼ë¡œ {forecast_quarters}ë¶„ê¸° ì˜ˆì¸¡ ì¤‘...")
        forecast = best_model.forecast(steps=forecast_quarters)

        # ì˜ˆì¸¡ê°’ ê²€ì¦
        if isinstance(forecast, pd.Series):
            forecast_values = forecast.values
        else:
            forecast_values = np.array(forecast)

        # NaN ì²´í¬
        if np.isnan(forecast_values).any():
            print("Warning: NaN values in forecast, using last known value")
            last_value = revenue_series[-1]
            forecast_values = np.nan_to_num(forecast_values, nan=last_value)

        # ë¬´í•œê°’ ì²´í¬
        if np.isinf(forecast_values).any():
            print("Warning: Infinite values in forecast, using last known value")
            last_value = revenue_series[-1]
            forecast_values = np.where(np.isinf(forecast_values), last_value, forecast_values)

        # ì‹ ë¢°êµ¬ê°„ ê³„ì‚° (ê¸°ë³¸ê°’ ì‚¬ìš©)
        try:
            # ìž”ì°¨ì˜ í‘œì¤€íŽ¸ì°¨ ì‚¬ìš©
            residuals = best_model.resid
            forecast_std = np.std(residuals) if residuals is not None else np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std
        except:
            print("ì‹ ë¢°êµ¬ê°„ ê³„ì‚° ì‹¤íŒ¨, ê¸°ë³¸ê°’ ì‚¬ìš©")
            forecast_std = np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std

        # ì˜ˆì¸¡ ë‚ ì§œ ìƒì„±
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # ë¶„ê¸°ë§ë¡œ ì¡°ì •
            quarter_end = pd.Timestamp(
                year=next_quarter_date.year,
                month=next_quarter_date.month,
                day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
            )
            forecast_dates.append(quarter_end)

        # ê²°ê³¼ DataFrame ìƒì„±
        forecast_result = pd.DataFrame({
            'date_quarter_end': forecast_dates,
            'year': [d.year for d in forecast_dates],
            'quarter': [d.quarter for d in forecast_dates],
            'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
            'revenue_billions_es_forecast': forecast_values,
            'forecast_lower': forecast_lower,
            'forecast_upper': forecast_upper
        })

        # ëª¨ë¸ ì •ë³´
        model_info = {
            'model_type': 'ExponentialSmoothing',
            'best_config': best_config,
            'aic': best_aic,
            'model': best_model,
            'historical_data_points': len(revenue_series)
        }

        print("ë¶„ê¸°ë³„ Exponential Smoothing ì˜ˆì¸¡ ì™„ë£Œ:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_es_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"Exponential Smoothing ì˜ˆì¸¡ ì‹¤íŒ¨: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def distribute_es_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    ë¶„ê¸°ë³„ Exponential Smoothing ì˜ˆì¸¡ ê²°ê³¼ë¥¼ ì›”ë³„ë¡œ ë¶„ë°°

    Parameters:
    - quarterly_forecast: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼ DataFrame
    - original_data: ì›ë³¸ ì›”ë³„ ë°ì´í„° DataFrame
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…

    Returns:
    - updated_data: ì›”ë³„ ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_es_forecast ì»¬ëŸ¼ ì´ˆê¸°í™”
    if 'revenue_billions' in df.columns:
        df['revenue_billions_es_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_es_forecast'] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("ë¶„ê¸°ë³„ Exponential Smoothing ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°° ì¤‘...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_es_forecast']

        # í•´ë‹¹ ë¶„ê¸°ì˜ ì›”ë“¤
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} ES ì˜ˆì¸¡ê°’ {quarterly_value:.2f}Bë¥¼ {months_in_quarter}ì›”ì— ë™ì¼í•˜ê²Œ ì ìš©")

        for month in months_in_quarter:
            # í•´ë‹¹ ë…„ì›”ì˜ ë§ˆì§€ë§‰ ë‚  ê³„ì‚°
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # í•´ë‹¹ ë‚ ì§œì˜ í–‰ ì°¾ê¸°
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_es_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: í•´ë‹¹ ë‚ ì§œ ì—†ìŒ")

    return df

def revenue_es_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                 data_end_date=None, forecast_quarters=4):
    """
    ë§¤ì¶œ Exponential Smoothing ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸

    Parameters:
    - data: ì›”ë³„ ë°ì´í„° DataFrame
    - revenue_col: ë§¤ì¶œ ì»¬ëŸ¼ëª…
    - date_col: ë‚ ì§œ ì»¬ëŸ¼ëª…
    - data_end_date: ë°ì´í„° ì¢…ë£Œì¼ (ì˜ˆ: '2025-08-31')
    - forecast_quarters: ì˜ˆì¸¡í•  ë¶„ê¸° ìˆ˜

    Returns:
    - result_data: ì˜ˆì¸¡ê°’ì´ ì¶”ê°€ëœ ë°ì´í„°
    - quarterly_data: ë¶„ê¸°ë³„ ë°ì´í„°
    - forecast_result: ë¶„ê¸°ë³„ ì˜ˆì¸¡ ê²°ê³¼
    - model_info: ëª¨ë¸ ì •ë³´
    """
    print("=== ë§¤ì¶œ Exponential Smoothing ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì‹œìž‘ ===")
    print(f"ë°ì´í„° ì¢…ë£Œì¼: {data_end_date if data_end_date else 'ì „ì²´ ë°ì´í„° ì‚¬ìš©'}")
    print(f"ì˜ˆì¸¡ ë¶„ê¸° ìˆ˜: {forecast_quarters}")

    try:
        # 1. ë¶„ê¸°ë³„ ë°ì´í„° ì¶”ì¶œ
        print("\n1. ë¶„ê¸°ë³„ ë§¤ì¶œ ë°ì´í„° ì¶”ì¶œ")
        quarterly_data = extract_quarterly_revenue_es(data, revenue_col, date_col, data_end_date)

        # 2. Exponential Smoothing ì˜ˆì¸¡
        print("\n2. ë¶„ê¸°ë³„ Exponential Smoothing ì˜ˆì¸¡")
        forecast_result, model_info = exponential_smoothing_quarterly_forecast(
            quarterly_data, forecast_quarters
        )

        if forecast_result is None:
            print("Exponential Smoothing ì˜ˆì¸¡ ì‹¤íŒ¨")
            return None, quarterly_data, None, None

        # 3. ì›”ë³„ ë¶„ë°°
        print("\n3. ë¶„ê¸°ë³„ ES ì˜ˆì¸¡ê°’ì„ ì›”ë³„ë¡œ ë¶„ë°°")
        result_data = distribute_es_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== ë§¤ì¶œ Exponential Smoothing ì˜ˆì¸¡ ì™„ë£Œ ===")
        print(f"ì˜ˆì¸¡ëœ ë¶„ê¸°: {len(forecast_result)}ê°œ")
        print(f"ì—…ë°ì´íŠ¸ëœ ì›”ë³„ ë°ì´í„°: {len(result_data)}ê°œì›”")
        print("ì¶”ê°€ëœ ì»¬ëŸ¼: revenue_billions_es_forecast")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"Exponential Smoothing ì˜ˆì¸¡ íŒŒì´í”„ë¼ì¸ ì˜¤ë¥˜: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None


def create_revenue_forecast_result(sarima_data, lstm_data, prophet_data, es_data):
    """
    4ê°œ ëª¨ë¸ì˜ ì˜ˆì¸¡ ê²°ê³¼ì—ì„œ í•„ìš”í•œ ì»¬ëŸ¼ì„ ì¶”ì¶œí•˜ì—¬ í†µí•©ëœ forecast result ìƒì„±

    Parameters:
    - sarima_data: SARIMA ì˜ˆì¸¡ ê²°ê³¼ ë°ì´í„°í”„ë ˆìž„
    - lstm_data: LSTM ì˜ˆì¸¡ ê²°ê³¼ ë°ì´í„°í”„ë ˆìž„
    - prophet_data: Prophet ì˜ˆì¸¡ ê²°ê³¼ ë°ì´í„°í”„ë ˆìž„
    - es_data: Exponential Smoothing ì˜ˆì¸¡ ê²°ê³¼ ë°ì´í„°í”„ë ˆìž„

    Returns:
    - revenue_forecast_result: í†µí•©ëœ ì˜ˆì¸¡ ê²°ê³¼ ë°ì´í„°í”„ë ˆìž„
    """

    # 1. SARIMA ë°ì´í„°ì—ì„œ ê¸°ë³¸ êµ¬ì¡° ê°€ì ¸ì˜¤ê¸°
    base_columns = ['ticker', 'date_month_end'] if 'ticker' in sarima_data.columns else ['date_month_end']
    revenue_forecast_result = sarima_data[base_columns + ['revenue_billions_forecast']].copy()

    # 2. LSTM ì˜ˆì¸¡ ê²°ê³¼ ë³‘í•©
    lstm_forecast = lstm_data[['date_month_end', 'revenue_billions_lstm_forecast']].copy()
    revenue_forecast_result = pd.merge(revenue_forecast_result, lstm_forecast, on='date_month_end', how='outer')

    # 3. Prophet ì˜ˆì¸¡ ê²°ê³¼ ë³‘í•©
    prophet_forecast = prophet_data[['date_month_end', 'revenue_billions_prophet_forecast']].copy()
    revenue_forecast_result = pd.merge(revenue_forecast_result, prophet_forecast, on='date_month_end', how='outer')

    # 4. Exponential Smoothing ì˜ˆì¸¡ ê²°ê³¼ ë³‘í•©
    es_forecast = es_data[['date_month_end', 'revenue_billions_es_forecast']].copy()
    revenue_forecast_result = pd.merge(revenue_forecast_result, es_forecast, on='date_month_end', how='outer')

    # 5. ë‚ ì§œìˆœìœ¼ë¡œ ì •ë ¬ ë° ticker ì •ë³´ ì²˜ë¦¬
    revenue_forecast_result = revenue_forecast_result.sort_values('date_month_end').reset_index(drop=True)
    if 'ticker' in revenue_forecast_result.columns:
        revenue_forecast_result['ticker'] = revenue_forecast_result['ticker'].fillna('AMAT')

    return revenue_forecast_result


def compare_forecast_models(forecast_df):
    """ëª¨ë¸ë³„ ì˜ˆì¸¡ê°’ ë¹„êµ ë¶„ì„"""
    forecast_columns = [
        'revenue_billions_forecast',  # SARIMA
        'revenue_billions_lstm_forecast',  # LSTM
        'revenue_billions_prophet_forecast',  # Prophet
        'revenue_billions_es_forecast'  # Exponential Smoothing
    ]

    complete_forecasts = forecast_df.dropna(subset=forecast_columns)
    if len(complete_forecasts) == 0:
        return None, None

    # ëª¨ë¸ë³„ í†µê³„
    comparison_stats = {}
    for col in forecast_columns:
        model_name = col.replace('revenue_billions_', '').replace('_forecast', '')
        comparison_stats[model_name] = {
            'mean': complete_forecasts[col].mean(),
            'std': complete_forecasts[col].std(),
            'min': complete_forecasts[col].min(),
            'max': complete_forecasts[col].max()
        }

    comparison_df = pd.DataFrame(comparison_stats).T
    correlation_matrix = complete_forecasts[forecast_columns].corr()

    return comparison_df, correlation_matrix


# í•¨ìˆ˜ ì‚¬ìš© ì˜ˆì‹œ (ì‹¤ì œ ë°ì´í„°ê°€ ìžˆì„ ë•Œë§Œ ì‹¤í–‰)
# def example_usage():
#     """
#     ì‹¤ì œ ì‚¬ìš© ì˜ˆì‹œ - ë°ì´í„°ê°€ ì¤€ë¹„ëœ í›„ì— í˜¸ì¶œí•˜ì„¸ìš”
#     """
#     print("=== ì˜ˆì¸¡ ê²°ê³¼ í†µí•© ì˜ˆì‹œ ===")
#
#     # ì˜ˆì‹œ: ê° íŒŒì´í”„ë¼ì¸ ì‹¤í–‰ í›„ ê²°ê³¼ í†µí•©
#     # sarima_result_data, _, _, _ = revenue_sarima_forecast_pipeline(data, ...)
#     # lstm_result_data, _, _, _ = revenue_lstm_forecast_pipeline(data, ...)
#     # prophet_result_data, _, _, _ = revenue_prophet_forecast_pipeline(data, ...)
#     # es_result_data, _, _, _ = revenue_es_forecast_pipeline(data, ...)
#
#     # í†µí•©ëœ ì˜ˆì¸¡ ê²°ê³¼ ìƒì„±
#     # revenue_forecast_result = create_revenue_forecast_result(
#     #     sarima_data=sarima_result_data,
#     #     lstm_data=lstm_result_data,
#     #     prophet_data=prophet_result_data,
#     #     es_data=es_result_data
#     # )
#
#     # ëª¨ë¸ ë¹„êµ ì‹¤í–‰
#     # comparison_stats, correlation_matrix = compare_forecast_models(revenue_forecast_result)
#
#     print("í†µí•© í•¨ìˆ˜ë“¤ì´ ì¤€ë¹„ë˜ì—ˆìŠµë‹ˆë‹¤.")
#     print("ì‹¤ì œ ë°ì´í„°ë¡œ íŒŒì´í”„ë¼ì¸ì„ ì‹¤í–‰í•œ í›„ create_revenue_forecast_result() í•¨ìˆ˜ë¥¼ ì‚¬ìš©í•˜ì„¸ìš”.")
def calculate_ttm_with_shift(revenue_forecast_result, shift_months=2):
    """
    ë¶„ê¸°ë³„ ì¤‘ë³µ ë°ì´í„°ë¥¼ ì²˜ë¦¬í•˜ì—¬ ì •í™•í•œ TTM ê³„ì‚° ë° shift ì ìš©

    Process:
    1. ë¶„ê¸°ë³„ ì¤‘ë³µ ë°ì´í„°ì—ì„œ ì²« ë²ˆì§¸ ë°ì´í„°ë§Œ ì¶”ì¶œ (ë¶„ê¸° ë°ì´í„°)
    2. ë¶„ê¸° ë°ì´í„°ë¥¼ rolling(window=4)ë¡œ TTM ê³„ì‚°
    3. TTM ë°ì´í„°ë¥¼ ë‹¤ì‹œ ì›”ë³„ë¡œ í™•ìž¥
    4. ê²°ì¸¡ì¹˜ë¥¼ ffill(limit=2)ë¡œ ë³´ì™„
    """
    df = revenue_forecast_result.copy()
    df['date_month_end'] = pd.to_datetime(df['date_month_end'])
    df = df.sort_values('date_month_end').reset_index(drop=True)

    # í‹°ì»¤ë³„ë¡œ ì²˜ë¦¬
    result_dfs = []

    for ticker in df['ticker'].unique():
        ticker_df = df[df['ticker'] == ticker].copy()

        forecast_columns = [
            'revenue_billions_forecast',
            'revenue_billions_lstm_forecast',
            'revenue_billions_prophet_forecast',
            'revenue_billions_es_forecast'
        ]

        existing_forecast_columns = [col for col in forecast_columns if col in ticker_df.columns]

        # 1. ë¶„ê¸°ë³„ ì²« ë²ˆì§¸ ë°ì´í„°ë§Œ ì¶”ì¶œ (ì¤‘ë³µ ì œê±°)
        quarterly_data = extract_quarterly_data(ticker_df, existing_forecast_columns)

        # 2. ë¶„ê¸° ë°ì´í„°ë¡œ TTM ê³„ì‚° (4ë¶„ê¸° rolling)
        quarterly_ttm = calculate_quarterly_ttm(quarterly_data, existing_forecast_columns)

        # 3. TTM ë°ì´í„°ë¥¼ ì›”ë³„ë¡œ í™•ìž¥
        monthly_ttm = expand_quarterly_to_monthly(quarterly_ttm, ticker_df, existing_forecast_columns)

        # 4. ì›ë³¸ ë°ì´í„°ì™€ ë³‘í•©
        ticker_result = merge_ttm_data(ticker_df, monthly_ttm, existing_forecast_columns, shift_months)

        result_dfs.append(ticker_result)

    return pd.concat(result_dfs, ignore_index=True)


def extract_quarterly_data(ticker_df, forecast_columns):
    """
    ë¶„ê¸°ë³„ ì¤‘ë³µ ë°ì´í„°ì—ì„œ ì²« ë²ˆì§¸ ë°ì´í„°ë§Œ ì¶”ì¶œ
    ë¶„ê¸° íŒ¨í„´ ê°ì§€: 3ê°œì›” ì—°ì† ë™ì¼í•œ ê°’ì´ ë‚˜íƒ€ë‚˜ëŠ” íŒ¨í„´
    """
    quarterly_data = []

    for col in forecast_columns:
        if col not in ticker_df.columns:
            continue

        # ê°’ì˜ ë³€í™”ì  ì°¾ê¸° (ë¶„ê¸°ë³„ ì²« ë°ì´í„° ì¶”ì¶œ)
        values = ticker_df[col].values
        dates = ticker_df['date_month_end'].values

        # ì²« ë²ˆì§¸ ë°ì´í„°ëŠ” í•­ìƒ í¬í•¨
        quarterly_indices = [0]

        # ê°’ì´ ë³€ê²½ë˜ëŠ” ì§€ì  ì°¾ê¸°
        for i in range(1, len(values)):
            if values[i] != values[i - 1]:
                quarterly_indices.append(i)

        # ë¶„ê¸°ë³„ ë°ì´í„° ì¶”ì¶œ
        quarterly_subset = ticker_df.iloc[quarterly_indices].copy()
        quarterly_subset['quarter'] = pd.PeriodIndex(quarterly_subset['date_month_end'], freq='Q')

        if len(quarterly_data) == 0:
            quarterly_data = quarterly_subset[['date_month_end', 'quarter'] + [col]].copy()
        else:
            quarterly_data = quarterly_data.merge(
                quarterly_subset[['date_month_end', col]],
                on='date_month_end',
                how='outer'
            )

    return quarterly_data.sort_values('date_month_end').reset_index(drop=True)


def calculate_quarterly_ttm(quarterly_data, forecast_columns):
    """
    ë¶„ê¸° ë°ì´í„°ë¥¼ ì‚¬ìš©í•˜ì—¬ TTM ê³„ì‚° (4ë¶„ê¸° rolling)
    """
    quarterly_ttm = quarterly_data.copy()

    for col in forecast_columns:
        if col in quarterly_ttm.columns:
            ttm_col = col.replace('revenue_billions_', 'revenue_ttm_')
            # 4ë¶„ê¸° rolling sumìœ¼ë¡œ TTM ê³„ì‚°
            quarterly_ttm[ttm_col] = quarterly_ttm[col].rolling(window=4, min_periods=1).sum()

    return quarterly_ttm


def expand_quarterly_to_monthly(quarterly_ttm, original_monthly_df, forecast_columns):
    """
    ë¶„ê¸°ë³„ TTM ë°ì´í„°ë¥¼ ì›”ë³„ë¡œ í™•ìž¥
    """
    # ì›ë³¸ ì›”ë³„ ë‚ ì§œ í”„ë ˆìž„ ìƒì„±
    monthly_dates = original_monthly_df[['date_month_end']].copy()
    monthly_dates['quarter'] = pd.PeriodIndex(monthly_dates['date_month_end'], freq='Q')

    # TTM ì»¬ëŸ¼ë§Œ ì¶”ì¶œ
    ttm_columns = [col.replace('revenue_billions_', 'revenue_ttm_')
                   for col in forecast_columns if col in quarterly_ttm.columns]

    quarterly_ttm_subset = quarterly_ttm[['quarter'] + ttm_columns].copy()

    # ë¶„ê¸°ë³„ TTM ë°ì´í„°ë¥¼ ì›”ë³„ë¡œ ë§¤í•‘
    monthly_ttm = monthly_dates.merge(quarterly_ttm_subset, on='quarter', how='left')

    # ê²°ì¸¡ì¹˜ë¥¼ forward fillë¡œ ë³´ì™„ (limit=2ë¡œ ìµœëŒ€ 2ê°œì›”ê¹Œì§€)
    for col in ttm_columns:
        if col in monthly_ttm.columns:
            monthly_ttm[col] = monthly_ttm[col].fillna(method='ffill', limit=2)

    return monthly_ttm[['date_month_end'] + ttm_columns]


def merge_ttm_data(original_df, monthly_ttm, forecast_columns, shift_months):
    """
    ì›ë³¸ ë°ì´í„°ì™€ TTM ë°ì´í„° ë³‘í•© ë° shift ì ìš©
    """
    result_df = original_df.merge(monthly_ttm, on='date_month_end', how='left')

    # Shift ì ìš©
    ttm_columns = [col.replace('revenue_billions_', 'revenue_ttm_')
                   for col in forecast_columns if col in original_df.columns]

    for ttm_col in ttm_columns:
        if ttm_col in result_df.columns:
            shifted_col = ttm_col + f'_shift{shift_months}m'
            result_df[shifted_col] = result_df[ttm_col].shift(shift_months)

    return result_df


# ì‚¬ìš© ì˜ˆì‹œ ë° ê²€ì¦ í•¨ìˆ˜
def validate_ttm_calculation(result_df, ticker_sample='AMAT'):
    """
    TTM ê³„ì‚° ê²°ê³¼ ê²€ì¦
    """
    sample_data = result_df[result_df['ticker'] == ticker_sample].copy()

    print(f"=== {ticker_sample} TTM ê³„ì‚° ê²°ê³¼ ê²€ì¦ ===")
    print("\nì›ë³¸ ë¶„ê¸°ë³„ ë°ì´í„° (ì¤‘ë³µ í™•ì¸):")
    print(sample_data[['date_month_end', 'revenue_billions_forecast']].head(12))

    print(f"\nTTM ê³„ì‚° ê²°ê³¼:")
    print(sample_data[['date_month_end', 'revenue_ttm_forecast', 'revenue_ttm_forecast_shift2m']].head(12))

    # ì¤‘ë³µ ì œê±° í™•ì¸: ë¶„ê¸°ë³„ ì²« ë°ì´í„°ë§Œ ë‹¤ë¥¸ì§€ í™•ì¸
    quarterly_check = sample_data.groupby(sample_data['date_month_end'].dt.to_period('Q'))[
        'revenue_billions_forecast'].nunique()
    print(f"\në¶„ê¸°ë³„ ê³ ìœ ê°’ ê°œìˆ˜ (ëª¨ë‘ 1ì´ì–´ì•¼ í•¨): \n{quarterly_check.head()}")

    return sample_data

if __name__ == "__main__":
    test_sarima_module()