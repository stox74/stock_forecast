#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simplified Enhanced US Stock Valuation Preprocessing
- Quarterly revenue data collection (API) with smart date conversion
- Monthly market cap data collection (API)
- Export data collection (DB)
- Data merge and PSR calculation
- Simple DB data supplementation (2013-2024)
- Preventive duplicate removal approach
"""

import requests
import pandas as pd
import numpy as np
import calendar
import time
from datetime import datetime
from tqdm import tqdm
import warnings
from sqlalchemy import create_engine
import gc

warnings.filterwarnings('ignore')

# stock_invest_function module import attempt (optional)
try:
    from stock_invest_function import get_db_host

    STOCK_FUNCTION_AVAILABLE = True
except ImportError:
    STOCK_FUNCTION_AVAILABLE = False


    def get_db_host():
        return 'localhost'


# ==============================================
# Utility Functions with Smart Date Conversion
# ==============================================

def convert_to_month_end(date_str):
    """Convert date to month end with smart 1-5 day rule"""
    try:
        # Safe type conversion
        date_obj = pd.to_datetime(date_str)
        if pd.isna(date_obj):
            return None

        y, m, d = date_obj.year, date_obj.month, date_obj.day

        # 1-5 days → previous month end
        if 1 <= d <= 5:
            if m == 1:
                prev_y, prev_m = y - 1, 12
            else:
                prev_y, prev_m = y, m - 1
            last_day_prev = calendar.monthrange(prev_y, prev_m)[1]
            return datetime(prev_y, prev_m, last_day_prev)

        # Others → current month end
        last_day_cur = calendar.monthrange(y, m)[1]
        return datetime(y, m, last_day_cur)

    except Exception:
        return None


def test_api_connection(api_key):
    """Test API connection"""
    test_url = "https://financialmodelingprep.com/api/v3/income-statement/AAPL"
    test_params = {'limit': 1, 'apikey': api_key, 'period': 'quarter'}

    try:
        response = requests.get(test_url, params=test_params, timeout=10)

        if response.status_code == 401:
            return False, "Invalid API key"
        elif response.status_code == 429:
            return False, "API rate limit exceeded"
        elif response.status_code != 200:
            return False, f"API error: {response.status_code}"

        data = response.json()
        if isinstance(data, dict) and 'Error Message' in data:
            return False, f"API error: {data['Error Message']}"
        elif not data:
            return False, "Empty response from API"

        return True, "API connection successful"

    except Exception as e:
        return False, f"API connection failed: {str(e)}"


# ==============================================
# Revenue Data Collection Functions
# ==============================================

def fetch_revenue_data(ticker, api_key):
    """Fetch quarterly revenue data"""
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
    params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"

        data = response.json()

        if isinstance(data, dict) and 'Error Message' in data:
            return None, f"API error: {data['Error Message']}"

        if not data:
            return None, "No data"

        return data, None

    except Exception as e:
        return None, f"Error: {str(e)}"


def add_revenue_ttm(df):
    """Add TTM (Trailing Twelve Months) column to quarterly revenue data"""
    df_copy = df.copy()
    df_copy = df_copy.sort_values(['ticker', 'date'])

    ttm_values = []

    for ticker in df_copy['ticker'].unique():
        ticker_data = df_copy[df_copy['ticker'] == ticker].copy()
        ticker_data = ticker_data.sort_values('date')
        ticker_data['revenue_ttm'] = ticker_data['revenue'].rolling(window=4, min_periods=1).sum()
        ttm_values.extend(ticker_data['revenue_ttm'].tolist())

    df_copy['revenue_ttm'] = ttm_values
    return df_copy


def collect_revenue_data(tickers, api_key, request_delay=0.3):
    """Collect quarterly revenue data with smart preprocessing (2013-2024)"""
    all_revenue_data = []

    for ticker in tqdm(tickers, desc="Revenue"):
        revenue_data, error = fetch_revenue_data(ticker, api_key)

        if revenue_data is None:
            continue

        for item in revenue_data:
            # Filter data: 2013-2024 only
            item_date = pd.to_datetime(item.get('date', ''))
            if item_date.year < 2013 or item_date.year > 2024:
                continue

            all_revenue_data.append({
                'ticker': ticker,
                'date': item.get('date', ''),
                'calendar_year': item.get('calendarYear', ''),
                'period': item.get('period', ''),
                'revenue': item.get('revenue', 0) if item.get('revenue') is not None else 0,
                'revenue_billions': round((item.get('revenue', 0) or 0) / 1_000_000_000, 2),
            })

        time.sleep(request_delay)

    # Create and process DataFrame with preventive duplicate removal
    revenue_df = pd.DataFrame(all_revenue_data) if all_revenue_data else pd.DataFrame()

    if not revenue_df.empty:
        revenue_df['date'] = pd.to_datetime(revenue_df['date'])
        revenue_df = revenue_df.sort_values(['ticker', 'date'])
        revenue_df['date_month_end'] = revenue_df['date'].apply(convert_to_month_end)

        # Preventive duplicate removal - keep first occurrence
        revenue_df = revenue_df.drop_duplicates(subset=['date_month_end'], keep='first').reset_index(drop=True)

        # Add TTM
        revenue_df_with_ttm = add_revenue_ttm(revenue_df)
        revenue_df_with_ttm['revenue_ttm_billions'] = revenue_df_with_ttm['revenue_ttm'] / 1_000_000_000
        revenue_df_with_ttm['date_month_end'] = revenue_df_with_ttm['date'].apply(convert_to_month_end)

        return revenue_df_with_ttm

    return pd.DataFrame()


# ==============================================
# Market Cap Data Collection Functions
# ==============================================

def fetch_market_data_yearly(ticker, api_key, start_year=2013, end_year=2024):
    """Fetch market cap data by year (2013-2024)"""
    all_data = []

    for year in range(start_year, end_year + 1):
        start_date_str = f"{year}-01-01"
        end_date_str = f"{year}-12-31"

        url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
        params = {'from': start_date_str, 'to': end_date_str, 'apikey': api_key}

        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    all_data.extend(data)
            time.sleep(0.3)
        except Exception as e:
            continue

    return all_data if all_data else None, None


def process_daily_to_monthly_market_data(daily_data, ticker):
    """Convert daily market cap data to monthly (month-end basis)"""
    if not daily_data:
        return pd.DataFrame()

    df = pd.DataFrame(daily_data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    df['year_month'] = df['date'].dt.to_period('M')

    monthly_data = []

    for year_month in df['year_month'].unique():
        month_data = df[df['year_month'] == year_month]
        last_day_data = month_data.loc[month_data['date'].idxmax()]

        monthly_data.append({
            'ticker': ticker,
            'date': last_day_data['date'],
            'market_cap': last_day_data['marketCap'],
            'market_cap_billions': round(last_day_data['marketCap'] / 1_000_000_000, 2),
        })

    return pd.DataFrame(monthly_data)


def collect_market_cap_data(tickers, api_key, start_year=2013):
    """Collect monthly market cap data for multiple tickers (2013-2024)"""
    all_market_data = []

    for ticker in tqdm(tickers, desc="Market Cap"):
        data, error = fetch_market_data_yearly(ticker, api_key, start_year, 2024)

        if not data:
            continue

        monthly_df = process_daily_to_monthly_market_data(data, ticker)

        if not monthly_df.empty:
            monthly_df['date_month_end'] = monthly_df['date'].apply(convert_to_month_end)
            all_market_data.append(monthly_df)

    if all_market_data:
        market_df = pd.concat(all_market_data, ignore_index=True)
        market_df = market_df.sort_values(['ticker', 'date_month_end'])
        return market_df

    return pd.DataFrame()


# ==============================================
# Export Data Collection Functions
# ==============================================

def get_hs_data(hs_code_6d, db_info):
    """Extract trade data by HS Code (2013-2024)"""
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT * FROM us_trade_monthly_data_with_forecast
        WHERE hs_code_6d = '{hs_code_6d}'
        AND date >= '2013-01-01'
        AND date <= '2024-12-31'
        ORDER BY date DESC
        """

        df = pd.read_sql(query, engine)
        engine.dispose()

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])

        return df

    except Exception as e:
        return pd.DataFrame()


def get_latest_input_date_data(df):
    """Extract data with the latest input_date only"""
    if 'input_date' not in df.columns:
        return pd.DataFrame()

    df_copy = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df_copy['input_date']):
        df_copy['input_date'] = pd.to_datetime(df_copy['input_date'])

    latest_date = df_copy['input_date'].max()
    latest_data = df_copy[df_copy['input_date'] == latest_date].copy()

    return latest_data


def collect_export_data(hs_code, db_info):
    """Collect export data (2013-2024)"""
    export_df = get_hs_data(hs_code, db_info)

    if export_df.empty:
        return pd.DataFrame()

    latest_export_data = get_latest_input_date_data(export_df)
    latest_export_data = latest_export_data.sort_values('date').reset_index(drop=True)

    if not latest_export_data.empty:
        latest_export_data['date'] = pd.to_datetime(latest_export_data['date'])
        latest_export_data['date_month_end'] = latest_export_data['date'].apply(convert_to_month_end)

    return latest_export_data


# ==============================================
# Simplified DB Data Supplementation Functions
# ==============================================

def fetch_db_revenue_data(ticker, db_info, start_date='2013-01-01', end_date='2024-12-31'):
    """Extract saleq data from DB (2013-2024)"""
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT date, ticker, saleq
        FROM US_fundq 
        WHERE ticker = '{ticker}' 
        AND saleq IS NOT NULL
        AND date >= '{start_date}'
        AND date <= '{end_date}'
        ORDER BY date ASC
        """

        df = pd.read_sql(query, con=engine)
        engine.dispose()

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df['revenue_billions'] = df['saleq'] / 1000  # Unit adjustment
            df['date_month_end'] = df['date'].apply(convert_to_month_end)

        return df[['ticker', 'date', 'date_month_end', 'revenue_billions']]

    except Exception as e:
        return pd.DataFrame()


def fetch_db_market_data(ticker, db_info, start_date='2013-01-01', end_date='2024-12-31'):
    """Extract me data from DB (2013-2024)"""
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT date, ticker, me
        FROM US_fundm 
        WHERE ticker = '{ticker}' 
        AND me IS NOT NULL
        AND date >= '{start_date}'
        AND date <= '{end_date}'
        ORDER BY date ASC
        """

        df = pd.read_sql(query, con=engine)
        engine.dispose()

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df['market_cap_billions'] = df['me'] / 1000  # Unit adjustment
            df['date_month_end'] = df['date'].apply(convert_to_month_end)

        return df[['ticker', 'date', 'date_month_end', 'market_cap_billions']]

    except Exception as e:
        return pd.DataFrame()


def simple_supplement_revenue_with_db(fmp_revenue_df, ticker, db_info):
    """Simple revenue data supplementation with exact matching"""
    db_revenue_df = fetch_db_revenue_data(ticker, db_info)

    if db_revenue_df.empty:
        return fmp_revenue_df, {'db_records': 0, 'overwritten': 0, 'added': 0}

    enhanced_revenue_df = fmp_revenue_df.copy()
    enhanced_revenue_df['year_month'] = enhanced_revenue_df['date_month_end'].dt.to_period('M')

    overwritten_count = 0
    added_count = 0

    # Direct monthly mapping with DB data
    for _, db_row in db_revenue_df.iterrows():
        if db_row['date_month_end'] > pd.Timestamp('2024-12-31'):
            continue

        year_month = db_row['date_month_end'].to_period('M')
        revenue_value = db_row['revenue_billions']

        mask = enhanced_revenue_df['year_month'] == year_month
        if mask.any():
            # Overwrite existing data
            enhanced_revenue_df.loc[mask, 'revenue_billions'] = revenue_value
            enhanced_revenue_df.loc[mask, 'revenue'] = revenue_value * 1_000_000_000
            overwritten_count += 1
        else:
            # Add new data
            new_row = {
                'ticker': ticker,
                'date': db_row['date_month_end'],
                'date_month_end': db_row['date_month_end'],
                'revenue_billions': revenue_value,
                'revenue': revenue_value * 1_000_000_000,
                'year_month': year_month,
                'calendar_year': db_row['date_month_end'].year,
                'period': f"Q{((db_row['date_month_end'].month - 1) // 3) + 1}"
            }
            enhanced_revenue_df = pd.concat([enhanced_revenue_df, pd.DataFrame([new_row])], ignore_index=True)
            added_count += 1

    enhanced_revenue_df = enhanced_revenue_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(
        drop=True)

    stats = {
        'db_records': len(db_revenue_df),
        'overwritten': overwritten_count,
        'added': added_count
    }

    return enhanced_revenue_df, stats


def simple_supplement_market_with_db(fmp_market_df, ticker, db_info):
    """Simple market cap data supplementation with exact matching"""
    db_market_df = fetch_db_market_data(ticker, db_info)

    if db_market_df.empty:
        return fmp_market_df, {'db_records': 0, 'filled': 0, 'added': 0}

    enhanced_market_df = fmp_market_df.copy()
    enhanced_market_df['year_month'] = enhanced_market_df['date_month_end'].dt.to_period('M')

    filled_count = 0
    added_count = 0

    # Direct monthly mapping with DB data
    for _, db_row in db_market_df.iterrows():
        if db_row['date_month_end'] > pd.Timestamp('2024-12-31'):
            continue

        year_month = db_row['date_month_end'].to_period('M')
        market_value = db_row['market_cap_billions']

        mask = (enhanced_market_df['year_month'] == year_month)

        if mask.any():
            # Fill only if existing data is NaN
            nan_mask = mask & enhanced_market_df['market_cap_billions'].isna()
            if nan_mask.any():
                enhanced_market_df.loc[nan_mask, 'market_cap_billions'] = market_value
                enhanced_market_df.loc[nan_mask, 'market_cap'] = market_value * 1_000_000_000
                filled_count += 1
        else:
            # Add new data
            new_row = {
                'ticker': ticker,
                'date': db_row['date_month_end'],
                'date_month_end': db_row['date_month_end'],
                'market_cap_billions': market_value,
                'market_cap': market_value * 1_000_000_000,
                'year_month': year_month
            }
            enhanced_market_df = pd.concat([enhanced_market_df, pd.DataFrame([new_row])], ignore_index=True)
            added_count += 1

    enhanced_market_df = enhanced_market_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(
        drop=True)

    stats = {
        'db_records': len(db_market_df),
        'filled': filled_count,
        'added': added_count
    }

    return enhanced_market_df, stats


def simple_forward_fill(merged_data, max_fill_months=2):
    """Simple forward fill with basic duplicate removal"""
    df = merged_data.copy()

    # Remove any remaining duplicates
    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)
    df = df.drop_duplicates(subset=['ticker', 'date_month_end'], keep='last')

    # Count NaN before forward fill
    before_revenue_nan = df['revenue_billions'].isna().sum()
    before_market_nan = df['market_cap_billions'].isna().sum()

    # Apply forward fill
    df['revenue_billions'] = df.groupby('ticker')['revenue_billions'].ffill(limit=max_fill_months)
    df['market_cap_billions'] = df.groupby('ticker')['market_cap_billions'].ffill(limit=3)

    # Count NaN after forward fill
    after_revenue_nan = df['revenue_billions'].isna().sum()
    after_market_nan = df['market_cap_billions'].isna().sum()

    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    stats = {
        'before_revenue_nan': before_revenue_nan,
        'before_market_nan': before_market_nan,
        'after_revenue_nan': after_revenue_nan,
        'after_market_nan': after_market_nan,
        'revenue_filled': before_revenue_nan - after_revenue_nan,
        'market_filled': before_market_nan - after_market_nan,
        'final_records': len(df)
    }

    return df, stats


# ==============================================
# Data Merge and PSR Calculation Functions
# ==============================================

def calculate_enhanced_ttm_and_psr(merged_data):
    """Calculate enhanced TTM and PSR"""
    df = merged_data.copy()
    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    # Calculate TTM from quarterly revenue
    df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0,
                                                                                                                    drop=True)
    df['revenue_ttm_billions'] = df['revenue_ttm']

    # Apply 2-month shift
    df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)

    # Calculate PSR
    df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']

    # Handle infinite values
    df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)

    return df


def calculate_export_yoy_growth(df):
    """Calculate YoY growth rate for export data"""
    if 'expDlr' not in df.columns or df['expDlr'].isna().all():
        df['expDlr_yoy'] = pd.NA
        return df

    df = df.sort_values('date_month_end').reset_index(drop=True)
    df['expDlr_yoy'] = df['expDlr'].pct_change(periods=12) * 100  # YoY growth rate (%)

    return df


def merge_with_export_data(merged_data, export_data):
    """Merge final data with export data - preserve export forecasts"""
    if export_data.empty:
        merged_data['hs_code_6d'] = None
        merged_data['expDlr'] = pd.NA
        merged_data['expDlr_yoy'] = pd.NA
        return merged_data

    # Extract required columns from export data
    export_subset = export_data[['date_month_end', 'hs_code_6d', 'expDlr']].copy()

    # Calculate YoY growth rate
    export_subset = calculate_export_yoy_growth(export_subset)

    # Merge data (outer join to preserve all data)
    final_data = pd.merge(merged_data, export_subset, on='date_month_end', how='outer')

    # Sort by date
    final_data = final_data.sort_values('date_month_end').reset_index(drop=True)

    # Fill ticker NaN values with ffill
    if 'ticker' in final_data.columns:
        final_data['ticker'] = final_data['ticker'].ffill()
        # Apply bfill for cases where first row is NaN
        final_data['ticker'] = final_data['ticker'].bfill()

    return final_data


# ==============================================
# Simplified Missing Value Analysis Functions
# ==============================================

def analyze_missing_values(df, name="DataFrame"):
    """Basic missing value analysis"""
    stats = {}

    if df.empty:
        return stats

    total_records = len(df)
    stats['total_records'] = total_records

    # Analyze key columns
    key_columns = ['revenue_billions', 'market_cap_billions', 'expDlr']

    for col in key_columns:
        if col in df.columns:
            nan_count = df[col].isna().sum()
            valid_count = total_records - nan_count
            nan_ratio = (nan_count / total_records * 100) if total_records > 0 else 0

            stats[f'{col}_total'] = total_records
            stats[f'{col}_nan'] = nan_count
            stats[f'{col}_valid'] = valid_count
            stats[f'{col}_nan_ratio'] = round(nan_ratio, 2)

    return stats


# ==============================================
# Main Execution Functions
# ==============================================

def run_enhanced_preprocessing(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=False):
    """
    Simplified preprocessing pipeline with preventive approach (2013-2024)

    Parameters:
    - ticker (str): Stock ticker to analyze
    - api_key (str): FMP API key
    - db_info (dict): DB connection info
    - hs_code (str, optional): HS code
    - start_year (int): Data collection start year (default: 2013)
    - verbose (bool): Print progress reports

    Returns:
    - pd.DataFrame: Final processed data
    """

    if verbose:
        print(f"Starting simplified preprocessing for {ticker}")
        print("=" * 60)

    # Memory management
    gc.collect()

    # 1. Collect FMP data with smart date conversion
    if verbose:
        print("1. Collecting FMP data with smart date conversion...")

    fmp_revenue_df = collect_revenue_data([ticker], api_key)
    fmp_market_df = collect_market_cap_data([ticker], api_key, start_year)

    if fmp_revenue_df.empty or fmp_market_df.empty:
        return None

    if verbose:
        print(f"FMP Revenue: {len(fmp_revenue_df)} records")
        print(f"FMP Market: {len(fmp_market_df)} records")

    # 2. Simple DB supplementation
    if verbose:
        print("2. Applying simple DB supplementation...")

    enhanced_revenue_df, revenue_stats = simple_supplement_revenue_with_db(fmp_revenue_df, ticker, db_info)
    enhanced_market_df, market_stats = simple_supplement_market_with_db(fmp_market_df, ticker, db_info)

    if verbose:
        print(f"DB Revenue supplementation: {revenue_stats}")
        print(f"DB Market supplementation: {market_stats}")

    # Memory cleanup
    del fmp_revenue_df, fmp_market_df
    gc.collect()

    # 3. Data merging
    if verbose:
        print("3. Merging data...")

    revenue_subset = enhanced_revenue_df[['ticker', 'date_month_end', 'revenue_billions']].copy()
    market_subset = enhanced_market_df[['ticker', 'date_month_end', 'market_cap_billions']].copy()

    merged_data = pd.merge(market_subset, revenue_subset, on=['ticker', 'date_month_end'], how='outer')
    merged_data = merged_data.sort_values('date_month_end').reset_index(drop=True)

    # 4. Simple forward fill
    if verbose:
        print("4. Applying simple forward fill...")

    merged_data_filled, fill_stats = simple_forward_fill(merged_data)

    if verbose:
        print(f"Forward fill results: {fill_stats}")

    # Memory cleanup
    del enhanced_revenue_df, enhanced_market_df, revenue_subset, market_subset, merged_data
    gc.collect()

    # 5. Calculate TTM and PSR
    if verbose:
        print("5. Calculating TTM and PSR...")

    final_data = calculate_enhanced_ttm_and_psr(merged_data_filled)

    # 6. Merge export data (optional)
    if hs_code:
        if verbose:
            print("6. Merging export data...")
        export_data = collect_export_data(hs_code, db_info)
        final_data = merge_with_export_data(final_data, export_data)
    else:
        # Add empty export columns if no export data
        final_data['hs_code_6d'] = None
        final_data['expDlr'] = pd.NA
        final_data['expDlr_yoy'] = pd.NA

        # Apply ticker ffill
        if 'ticker' in final_data.columns:
            final_data['ticker'] = final_data['ticker'].ffill().bfill()

    # Final analysis
    if verbose:
        final_stats = analyze_missing_values(final_data, "Final Data")
        print(f"7. Final data: {len(final_data)} records")
        print(f"Final Revenue NaN: {final_stats.get('revenue_billions_nan', 0)}")
        print(f"Final Market NaN: {final_stats.get('market_cap_billions_nan', 0)}")
        print("=" * 60)
        print("Simplified preprocessing completed successfully")

    # Final memory cleanup
    gc.collect()

    return final_data


def run_enhanced_preprocessing_with_stats(ticker, api_key, db_info, hs_code=None, start_year=2013):
    """
    Version with basic statistics (backward compatibility)
    """
    # Analyze original data
    fmp_revenue_df = collect_revenue_data([ticker], api_key)
    fmp_market_df = collect_market_cap_data([ticker], api_key, start_year)

    if fmp_revenue_df.empty or fmp_market_df.empty:
        return None, {}

    original_revenue_stats = analyze_missing_values(fmp_revenue_df, "Original Revenue")
    original_market_stats = analyze_missing_values(fmp_market_df, "Original Market")

    # Run preprocessing
    result = run_enhanced_preprocessing(ticker, api_key, db_info, hs_code, start_year, verbose=False)

    if result is not None:
        final_stats = analyze_missing_values(result, "Final Data")

        # Convert to simple stats format for backward compatibility
        simple_stats = {
            'total_records': final_stats.get('total_records', 0),
            'revenue_improvement': original_revenue_stats.get('revenue_billions_nan', 0) - final_stats.get(
                'revenue_billions_nan', 0),
            'market_improvement': original_market_stats.get('market_cap_billions_nan', 0) - final_stats.get(
                'market_cap_billions_nan', 0),
            'final_revenue_nan': final_stats.get('revenue_billions_nan', 0),
            'final_market_nan': final_stats.get('market_cap_billions_nan', 0)
        }
        return result, simple_stats

    return result, {}


def run_simple_enhanced_preprocessing(ticker, api_key, db_info, start_year=2013):
    """
    Simple version (excluding export data)
    """
    return run_enhanced_preprocessing(ticker, api_key, db_info, start_year=start_year)


# ==============================================
# Verification Functions
# ==============================================

def verify_preprocessing_quality(final_data, ticker):
    """
    Verify the quality of preprocessing results
    """
    verification_report = {
        'ticker': ticker,
        'status': 'SUCCESS',
        'issues': [],
        'summary': {}
    }

    if final_data is None or final_data.empty:
        verification_report['status'] = 'FAILED'
        verification_report['issues'].append('No final data generated')
        return verification_report

    # Check data range (should be 2013-2024)
    date_range = final_data['date_month_end']
    min_date = date_range.min()
    max_date = date_range.max()

    if min_date.year < 2013 or max_date.year > 2024:
        verification_report['issues'].append(f'Date range outside 2013-2024: {min_date} to {max_date}')

    # Check critical columns
    required_columns = ['ticker', 'date_month_end', 'market_cap_billions', 'revenue_billions', 'PSR_ttm']
    missing_columns = [col for col in required_columns if col not in final_data.columns]
    if missing_columns:
        verification_report['issues'].append(f'Missing required columns: {missing_columns}')

    # Check for duplicates
    duplicate_count = final_data.duplicated(subset=['ticker', 'date_month_end']).sum()
    if duplicate_count > 0:
        verification_report['issues'].append(f'Found {duplicate_count} duplicate date entries')

    # Check missing values
    final_stats = analyze_missing_values(final_data)
    revenue_nan = final_stats.get('revenue_billions_nan', 0)
    market_nan = final_stats.get('market_cap_billions_nan', 0)

    verification_report['summary'] = {
        'total_records': len(final_data),
        'final_revenue_nan': revenue_nan,
        'final_market_nan': market_nan,
        'duplicate_entries': duplicate_count,
        'date_range': f"{min_date.strftime('%Y-%m')} to {max_date.strftime('%Y-%m')}"
    }

    # Determine overall status
    if len(verification_report['issues']) == 0:
        verification_report['status'] = 'SUCCESS'
    elif len(verification_report['issues']) <= 2:
        verification_report['status'] = 'WARNING'
    else:
        verification_report['status'] = 'FAILED'

    return verification_report


def print_verification_report(verification_report):
    """Print a concise verification report"""
    print(f"\nPreprocessing Verification Report - {verification_report['ticker']}")
    print("=" * 50)
    print(f"Status: {verification_report['status']}")

    if verification_report['summary']:
        summary = verification_report['summary']
        print(f"Total Records: {summary['total_records']}")
        print(f"Date Range: {summary['date_range']}")
        print(f"Final Revenue NaN: {summary['final_revenue_nan']}")
        print(f"Final Market NaN: {summary['final_market_nan']}")
        print(f"Duplicate Entries: {summary['duplicate_entries']}")

    if verification_report['issues']:
        print(f"\nIssues Found ({len(verification_report['issues'])}):")
        for issue in verification_report['issues']:
            print(f"  - {issue}")

    print("=" * 50)


# ==============================================
# Complete Pipeline with Verification
# ==============================================

def run_complete_preprocessing_with_verification(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=True):
    """
    Complete simplified preprocessing pipeline with verification

    Returns:
    - tuple: (final_data, verification_report)
    """

    # Run simplified preprocessing
    final_data = run_enhanced_preprocessing(
        ticker, api_key, db_info, hs_code, start_year, verbose
    )

    # Verify results
    verification_report = verify_preprocessing_quality(final_data, ticker)

    # Print verification report if verbose
    if verbose:
        print_verification_report(verification_report)

    return final_data, verification_report


# ==============================================
# Additional Utility Functions
# ==============================================

def fetch_ticker_and_item(db_info: dict, ticker: str, table_name: str = "US_fundq") -> pd.DataFrame:
    """
    Efficiently fetch saleq data for specific ticker

    Parameters:
    - db_info (dict): DB connection info (user, password, host, port, database)
    - ticker (str): Ticker to query (e.g. 'AMAT')
    - table_name (str): Table name to query

    Returns:
    - pd.DataFrame: saleq data for the ticker
    """
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT permno, edate, date, ticker, saleq
        FROM {table_name} 
        WHERE ticker = '{ticker}' 
        AND saleq IS NOT NULL
        AND date >= '2013-01-01'
        AND date <= '2024-12-31'
        ORDER BY date ASC
        """

        df = pd.read_sql(query, con=engine)
        engine.dispose()
        return df

    except Exception as e:
        return pd.DataFrame()


def fetch_ticker_and_me(db_info: dict, ticker: str, table_name: str = "US_fundm") -> pd.DataFrame:
    """
    Efficiently fetch me data for specific ticker

    Parameters:
    - db_info (dict): DB connection info
    - ticker (str): Ticker to query
    - table_name (str): Table name to query

    Returns:
    - pd.DataFrame: me data for the ticker
    """
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT permno, edate, date, ticker, me
        FROM {table_name} 
        WHERE ticker = '{ticker}' 
        AND me IS NOT NULL
        AND date >= '2013-01-01'
        AND date <= '2024-12-31'
        ORDER BY date ASC
        """

        df = pd.read_sql(query, con=engine)
        engine.dispose()
        return df

    except Exception as e:
        return pd.DataFrame()


# ==============================================
# Example Usage
# ==============================================

"""
# Basic usage (quiet mode)
result = run_enhanced_preprocessing("AAPL", api_key, db_info)

# With verification and detailed reporting
result, verification = run_complete_preprocessing_with_verification(
    "AAPL", api_key, db_info, hs_code="854231", verbose=True
)

# Backward compatible usage with simple stats
result, simple_stats = run_enhanced_preprocessing_with_stats("AAPL", api_key, db_info)

print("Stats:", simple_stats)
# Expected output:
# Stats: {
#     'total_records': 144, 
#     'revenue_improvement': 0,  # No missing values from start
#     'market_improvement': 0,   # No missing values from start
#     'final_revenue_nan': 0,    # All filled
#     'final_market_nan': 0      # All filled
# }
"""