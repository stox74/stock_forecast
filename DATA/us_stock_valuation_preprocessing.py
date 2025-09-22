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
# Utility Functions
# ==============================================

def convert_to_month_end(date_str):
    try:
        # 문자열/타입 혼용 안전 변환
        date_obj = pd.to_datetime(date_str)
        if pd.isna(date_obj):
            return None

        y, m, d = date_obj.year, date_obj.month, date_obj.day

        # 1~5일 → 전달 말일
        if 1 <= d <= 5:
            if m == 1:
                prev_y, prev_m = y - 1, 12
            else:
                prev_y, prev_m = y, m - 1
            last_day_prev = calendar.monthrange(prev_y, prev_m)[1]
            return datetime(prev_y, prev_m, last_day_prev)

        # 그 외 → 해당월 말일
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


# ---------- NEW: revenue alias sync helper ----------
def _sync_revenue_alias(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep revenue_billion (singular) and revenue_billions (plural) in sync.
    - If only one exists, create the other as a copy.
    - If both exist, mutually fill NaNs.
    """
    if df is None or df.empty:
        return df
    df = df.copy()

    has_plural = 'revenue_billions' in df.columns
    has_singular = 'revenue_billion' in df.columns

    if not has_plural and not has_singular:
        return df

    if has_plural and not has_singular:
        df['revenue_billion'] = df['revenue_billions']
        return df

    if has_singular and not has_plural:
        df['revenue_billions'] = df['revenue_billion']
        return df

    # both exist -> cross fill NaNs
    plural_nan = df['revenue_billions'].isna()
    if plural_nan.any():
        df.loc[plural_nan, 'revenue_billions'] = df.loc[plural_nan, 'revenue_billion']

    singular_nan = df['revenue_billion'].isna()
    if singular_nan.any():
        df.loc[singular_nan, 'revenue_billion'] = df.loc[singular_nan, 'revenue_billions']

    return df
# ----------------------------------------------------


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
    """Collect quarterly revenue data for multiple tickers (2013-2024)"""
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

    revenue_df = pd.DataFrame(all_revenue_data) if all_revenue_data else pd.DataFrame()
    if not revenue_df.empty:
        revenue_df['date'] = pd.to_datetime(revenue_df['date'])
        revenue_df = revenue_df.sort_values(['ticker', 'date'])
        revenue_df['date_month_end'] = revenue_df['date'].apply(convert_to_month_end)
        revenue_df = revenue_df.drop_duplicates(subset=['date_month_end'], keep='first').reset_index(drop=True)
        # Add TTM
        revenue_df_with_ttm = add_revenue_ttm(revenue_df)
        revenue_df_with_ttm['revenue_ttm_billions'] = revenue_df_with_ttm['revenue_ttm'] / 1_000_000_000
        revenue_df_with_ttm['date_month_end'] = revenue_df_with_ttm['date'].apply(convert_to_month_end)

        # ensure alias presence
        revenue_df_with_ttm = _sync_revenue_alias(revenue_df_with_ttm)

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
        except Exception:
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
    except Exception:
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
# DB Data Supplementation Functions (Enhanced)
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
    except Exception:
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
    except Exception:
        return pd.DataFrame()


def comprehensive_supplement_revenue_with_db(fmp_revenue_df, ticker, db_info):
    """Comprehensive revenue data supplementation with aggressive gap filling"""
    db_revenue_df = fetch_db_revenue_data(ticker, db_info)
    if db_revenue_df.empty:
        return _sync_revenue_alias(fmp_revenue_df), {'db_records': 0, 'overwritten': 0, 'added': 0}

    enhanced_revenue_df = fmp_revenue_df.copy()
    enhanced_revenue_df['year_month'] = enhanced_revenue_df['date_month_end'].dt.to_period('M')

    overwritten_count = 0
    added_count = 0
    filled_count = 0

    for _, db_row in db_revenue_df.iterrows():
        year_month = db_row['date_month_end'].to_period('M')
        revenue_value = db_row['revenue_billions']
        mask = enhanced_revenue_df['year_month'] == year_month
        if mask.any():
            enhanced_revenue_df.loc[mask, 'revenue_billions'] = revenue_value
            enhanced_revenue_df.loc[mask, 'revenue'] = revenue_value * 1_000_000_000
            overwritten_count += 1
        else:
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

    enhanced_revenue_df = enhanced_revenue_df.sort_values('date_month_end').reset_index(drop=True)
    nan_mask = enhanced_revenue_df['revenue_billions'].isna()
    if nan_mask.any():
        db_revenue_df_sorted = db_revenue_df.sort_values('date_month_end').reset_index(drop=True)
        for idx in enhanced_revenue_df[nan_mask].index:
            target_date = enhanced_revenue_df.loc[idx, 'date_month_end']
            if not db_revenue_df_sorted.empty:
                db_revenue_df_sorted['date_diff'] = abs(db_revenue_df_sorted['date_month_end'] - target_date)
                nearest_idx = db_revenue_df_sorted['date_diff'].idxmin()
                nearest_value = db_revenue_df_sorted.loc[nearest_idx, 'revenue_billions']
                enhanced_revenue_df.loc[idx, 'revenue_billions'] = nearest_value
                enhanced_revenue_df.loc[idx, 'revenue'] = nearest_value * 1_000_000_000
                filled_count += 1

    enhanced_revenue_df = enhanced_revenue_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(drop=True)
    enhanced_revenue_df = _sync_revenue_alias(enhanced_revenue_df)

    stats = {
        'db_records': len(db_revenue_df),
        'overwritten': overwritten_count,
        'added': added_count,
        'gap_filled': filled_count
    }
    return enhanced_revenue_df, stats


def comprehensive_supplement_market_with_db(fmp_market_df, ticker, db_info):
    """Comprehensive market cap data supplementation with aggressive gap filling"""
    db_market_df = fetch_db_market_data(ticker, db_info)
    if db_market_df.empty:
        return fmp_market_df, {'db_records': 0, 'filled': 0, 'added': 0}

    enhanced_market_df = fmp_market_df.copy()
    enhanced_market_df['year_month'] = enhanced_market_df['date_month_end'].dt.to_period('M')

    filled_count = 0
    added_count = 0
    gap_filled_count = 0

    for _, db_row in db_market_df.iterrows():
        year_month = db_row['date_month_end'].to_period('M')
        market_value = db_row['market_cap_billions']
        mask = (enhanced_market_df['year_month'] == year_month)
        if mask.any():
            nan_mask = mask & enhanced_market_df['market_cap_billions'].isna()
            if nan_mask.any():
                enhanced_market_df.loc[nan_mask, 'market_cap_billions'] = market_value
                enhanced_market_df.loc[nan_mask, 'market_cap'] = market_value * 1_000_000_000
                filled_count += 1
        else:
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

    enhanced_market_df = enhanced_market_df.sort_values('date_month_end').reset_index(drop=True)
    nan_mask = enhanced_market_df['market_cap_billions'].isna()
    if nan_mask.any():
        db_market_df_sorted = db_market_df.sort_values('date_month_end').reset_index(drop=True)
        for idx in enhanced_market_df[nan_mask].index:
            target_date = enhanced_market_df.loc[idx, 'date_month_end']
            if not db_market_df_sorted.empty:
                db_market_df_sorted['date_diff'] = abs(db_market_df_sorted['date_month_end'] - target_date)
                nearest_idx = db_market_df_sorted['date_diff'].idxmin()
                nearest_value = db_market_df_sorted.loc[nearest_idx, 'market_cap_billions']
                enhanced_market_df.loc[idx, 'market_cap_billions'] = nearest_value
                enhanced_market_df.loc[idx, 'market_cap'] = nearest_value * 1_000_000_000
                gap_filled_count += 1

    enhanced_market_df = enhanced_market_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(drop=True)

    stats = {
        'db_records': len(db_market_df),
        'filled': filled_count,
        'added': added_count,
        'gap_filled': gap_filled_count
    }
    return enhanced_market_df, stats


def comprehensive_forward_fill(merged_data, max_fill_months=2):
    """Apply comprehensive forward fill with detailed tracking (limit=2 유지)"""
    df = merged_data.copy()

    # Ensure datetime & sorting
    if 'date_month_end' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['date_month_end']):
        df['date_month_end'] = pd.to_datetime(df['date_month_end'])
    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    # Sync aliases first
    df = _sync_revenue_alias(df)

    before_revenue_nan = df['revenue_billions'].isna().sum() if 'revenue_billions' in df.columns else 0
    before_market_nan  = df['market_cap_billions'].isna().sum() if 'market_cap_billions' in df.columns else 0

    # ffill(limit=2) 유지
    if 'revenue_billions' in df.columns:
        df['revenue_billions'] = df.groupby('ticker', dropna=False)['revenue_billions'].ffill(limit=max_fill_months)

        # 시작부 리딩 NaN만 최대 1개월 bfill (꼬리 왜곡 방지)
        leading_mask = df.groupby('ticker', dropna=False)['revenue_billions'].cumcount() == 0
        needs_bfill  = df['revenue_billions'].isna() & leading_mask
        if needs_bfill.any():
            tmp = df.copy()
            tmp['revenue_billions'] = tmp.groupby('ticker', dropna=False)['revenue_billions'].bfill(limit=1)
            df.loc[needs_bfill, 'revenue_billions'] = tmp.loc[needs_bfill, 'revenue_billions']

        # re-sync after fill
        df = _sync_revenue_alias(df)

    if 'market_cap_billions' in df.columns:
        df['market_cap_billions'] = df.groupby('ticker', dropna=False)['market_cap_billions'].ffill(limit=3)

    after_revenue_nan = df['revenue_billions'].isna().sum() if 'revenue_billions' in df.columns else 0
    after_market_nan  = df['market_cap_billions'].isna().sum() if 'market_cap_billions' in df.columns else 0

    stats = {
        'before_revenue_nan': before_revenue_nan,
        'before_market_nan':  before_market_nan,
        'after_revenue_nan':  after_revenue_nan,
        'after_market_nan':   after_market_nan,
        'revenue_filled':     before_revenue_nan - after_revenue_nan,
        'market_filled':      before_market_nan  - after_market_nan
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
    df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0, drop=True)
    df['revenue_ttm_billions'] = df['revenue_ttm']

    # Apply 2-month shift
    df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)

    # Calculate PSR
    df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']

    # Handle infinite values
    df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)

    # alias sync for safety
    df = _sync_revenue_alias(df)

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
    export_subset = export_data[['date_month_end', 'hs_code_6d', 'expDlr']].copy()
    export_subset = calculate_export_yoy_growth(export_subset)
    final_data = pd.merge(merged_data, export_subset, on='date_month_end', how='outer')
    final_data = final_data.sort_values('date_month_end').reset_index(drop=True)
    if 'ticker' in final_data.columns:
        final_data['ticker'] = final_data['ticker'].ffill()
        final_data['ticker'] = final_data['ticker'].bfill()
    final_data = _sync_revenue_alias(final_data)
    return final_data


# ==============================================
# Comprehensive Missing Value Analysis Functions
# ==============================================

def analyze_missing_values(df, name="DataFrame"):
    """Comprehensive missing value analysis"""
    stats = {}
    if df.empty:
        return stats
    total_records = len(df)
    stats['total_records'] = total_records
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


def print_missing_value_report(before_stats, after_stats, stage_name):
    """Print concise missing value improvement report"""
    print(f"\n{stage_name} Results:")
    print("-" * 40)
    for col in ['revenue_billions', 'market_cap_billions']:
        if f'{col}_nan' in before_stats and f'{col}_nan' in after_stats:
            before_nan = before_stats[f'{col}_nan']
            after_nan = after_stats[f'{col}_nan']
            improvement = before_nan - after_nan
            col_name = col.replace('_billions', '').replace('_', ' ').title()
            print(f"{col_name}: {before_nan} → {after_nan} NaN (improved: {improvement})")


# ==============================================
# Main Execution Functions
# ==============================================

def run_comprehensive_enhanced_preprocessing(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=False):
    """
    Comprehensive preprocessing pipeline with strong missing value treatment and verification
    """
    if verbose:
        print(f"Starting comprehensive preprocessing for {ticker}")
        print("=" * 60)

    gc.collect()

    # 1. Collect FMP data
    if verbose:
        print("1. Collecting FMP data...")

    fmp_revenue_df = collect_revenue_data([ticker], api_key)
    fmp_market_df = collect_market_cap_data([ticker], api_key, start_year)

    if fmp_revenue_df.empty or fmp_market_df.empty:
        return None, {'error': 'FMP data collection failed'}

    # Analyze original FMP data
    fmp_revenue_stats = analyze_missing_values(fmp_revenue_df, "FMP Revenue")
    fmp_market_stats = analyze_missing_values(fmp_market_df, "FMP Market")

    if verbose:
        print(f"FMP Revenue: {len(fmp_revenue_df)} records, {fmp_revenue_stats.get('revenue_billions_nan', 0)} NaN")
        print(f"FMP Market: {len(fmp_market_df)} records, {fmp_market_stats.get('market_cap_billions_nan', 0)} NaN")

    # 2. Comprehensive DB supplementation
    if verbose:
        print("2. Applying comprehensive DB supplementation...")

    enhanced_revenue_df, revenue_supplement_stats = comprehensive_supplement_revenue_with_db(fmp_revenue_df, ticker, db_info)
    enhanced_market_df, market_supplement_stats   = comprehensive_supplement_market_with_db(fmp_market_df, ticker, db_info)

    # ✅ alias sync after DB supplementation
    enhanced_revenue_df = _sync_revenue_alias(enhanced_revenue_df)

    # Analyze after DB supplementation
    enhanced_revenue_stats = analyze_missing_values(enhanced_revenue_df, "Enhanced Revenue")
    enhanced_market_stats = analyze_missing_values(enhanced_market_df, "Enhanced Market")

    if verbose:
        print(f"DB Revenue supplementation: {revenue_supplement_stats}")
        print(f"DB Market supplementation: {market_supplement_stats}")
        print_missing_value_report(fmp_revenue_stats, enhanced_revenue_stats, "DB Supplementation - Revenue")
        print_missing_value_report(fmp_market_stats, enhanced_market_stats, "DB Supplementation - Market")

    # Memory cleanup
    del fmp_revenue_df, fmp_market_df
    gc.collect()

    # 3. Data merging
    if verbose:
        print("3. Merging data...")

    revenue_subset = enhanced_revenue_df[['ticker', 'date_month_end', 'revenue_billions']].copy()
    market_subset  = enhanced_market_df[['ticker', 'date_month_end', 'market_cap_billions']].copy()

    merged_data = pd.merge(market_subset, revenue_subset, on=['ticker', 'date_month_end'], how='outer')
    merged_data = merged_data.sort_values('date_month_end').reset_index(drop=True)

    # ✅ alias sync just after merge
    merged_data = _sync_revenue_alias(merged_data)

    # Analyze before forward fill
    before_fill_stats = analyze_missing_values(merged_data, "Before Forward Fill")

    # 4. Comprehensive forward fill (limit=2 유지)
    if verbose:
        print("4. Applying comprehensive forward fill...")

    merged_data_filled, fill_stats = comprehensive_forward_fill(merged_data)

    # Analyze after forward fill
    after_fill_stats = analyze_missing_values(merged_data_filled, "After Forward Fill")

    if verbose:
        print_missing_value_report(before_fill_stats, after_fill_stats, "Forward Fill")

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
        final_data['hs_code_6d'] = None
        final_data['expDlr'] = pd.NA
        final_data['expDlr_yoy'] = pd.NA
        if 'ticker' in final_data.columns:
            final_data['ticker'] = final_data['ticker'].ffill().bfill()

    # ✅ expose singular alias always for compatibility
    final_data = _sync_revenue_alias(final_data)
    final_data['revenue_billion'] = final_data['revenue_billions']

    # Final analysis
    final_stats = analyze_missing_values(final_data, "Final Data")

    if verbose:
        print(f"7. Final data: {len(final_data)} records")
        print(f"Final Revenue NaN: {final_stats.get('revenue_billions_nan', 0)}")
        print(f"Final Market NaN: {final_stats.get('market_cap_billions_nan', 0)}")
        print("=" * 60)
        print("Preprocessing completed successfully")

    comprehensive_stats = {
        'ticker': ticker,
        'total_records': len(final_data),
        'fmp_original': {
            'revenue_nan': fmp_revenue_stats.get('revenue_billions_nan', 0),
            'market_nan':  fmp_market_stats.get('market_cap_billions_nan', 0)
        },
        'db_supplementation': {
            'revenue': revenue_supplement_stats,
            'market':  market_supplement_stats
        },
        'forward_fill': fill_stats,
        'final_result': {
            'revenue_nan': final_stats.get('revenue_billions_nan', 0),
            'market_nan':  final_stats.get('market_cap_billions_nan', 0),
            'revenue_improvement': fmp_revenue_stats.get('revenue_billions_nan', 0) - final_stats.get('revenue_billions_nan', 0),
            'market_improvement':  fmp_market_stats.get('market_cap_billions_nan', 0)  - final_stats.get('market_cap_billions_nan', 0)
        }
    }
    gc.collect()
    return final_data, comprehensive_stats


def run_enhanced_preprocessing(ticker, api_key, db_info, hs_code=None, start_year=2013):
    """Simple version without verbose output (backward compatibility)"""
    result, stats = run_comprehensive_enhanced_preprocessing(
        ticker, api_key, db_info, hs_code, start_year, verbose=False
    )
    return result


def run_enhanced_preprocessing_with_stats(ticker, api_key, db_info, hs_code=None, start_year=2013):
    """Version with comprehensive statistics (backward compatibility)"""
    result, comprehensive_stats = run_comprehensive_enhanced_preprocessing(
        ticker, api_key, db_info, hs_code, start_year, verbose=False
    )
    if result is not None and 'final_result' in comprehensive_stats:
        simple_stats = {
            'total_records': comprehensive_stats['total_records'],
            'revenue_improvement': comprehensive_stats['final_result']['revenue_improvement'],
            'market_improvement':  comprehensive_stats['final_result']['market_improvement'],
            'final_revenue_nan':   comprehensive_stats['final_result']['revenue_nan'],
            'final_market_nan':    comprehensive_stats['final_result']['market_nan']
        }
        return result, simple_stats
    return result, {}


def run_simple_enhanced_preprocessing(ticker, api_key, db_info, start_year=2013):
    """Simple version (excluding export data)"""
    return run_enhanced_preprocessing(ticker, api_key, db_info, start_year=start_year)


# ==============================================
# Verification and Testing Functions
# ==============================================

def verify_preprocessing_quality(final_data, comprehensive_stats, ticker):
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

    # Check missing value improvement
    if 'final_result' in comprehensive_stats:
        final_result = comprehensive_stats['final_result']
        revenue_improvement = final_result.get('revenue_improvement', 0)
        market_improvement  = final_result.get('market_improvement', 0)
        verification_report['summary'] = {
            'total_records': len(final_data),
            'revenue_improvement': revenue_improvement,
            'market_improvement':  market_improvement,
            'final_revenue_nan':   final_result.get('revenue_nan', 0),
            'final_market_nan':    final_result.get('market_nan', 0),
            'date_range':          f"{min_date.strftime('%Y-%m')} to {max_date.strftime('%Y-%m')}"
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
        print(f"Revenue Improvement: {summary['revenue_improvement']} NaN values fixed")
        print(f"Market Improvement: {summary['market_improvement']} NaN values fixed")
        print(f"Final Revenue NaN: {summary['final_revenue_nan']}")
        print(f"Final Market NaN: {summary['final_market_nan']}")
    if verification_report['issues']:
        print(f"\nIssues Found ({len(verification_report['issues'])}):")
        for issue in verification_report['issues']:
            print(f"  - {issue}")
    print("=" * 50)


# ==============================================
# Main Execution Function with Full Verification
# ==============================================

def run_complete_preprocessing_with_verification(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=True):
    """
    Complete preprocessing pipeline with comprehensive verification

    Returns:
    - tuple: (final_data, verification_report, comprehensive_stats)
    """
    final_data, comprehensive_stats = run_comprehensive_enhanced_preprocessing(
        ticker, api_key, db_info, hs_code, start_year, verbose
    )
    verification_report = verify_preprocessing_quality(final_data, comprehensive_stats, ticker)
    if verbose:
        print_verification_report(verification_report)
    return final_data, verification_report, comprehensive_stats


# ==============================================
# Additional Utility Functions
# ==============================================

def fetch_ticker_and_item(db_info: dict, ticker: str, table_name: str = "fundq_df") -> pd.DataFrame:
    """
    Efficiently fetch saleq data for specific ticker
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
    except Exception:
        return pd.DataFrame()


def fetch_ticker_and_me(db_info: dict, ticker: str, table_name: str = "fundm_df") -> pd.DataFrame:
    """
    Efficiently fetch me data for specific ticker
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
    except Exception:
        return pd.DataFrame()






# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# Enhanced US Stock Valuation Preprocessing with Comprehensive Missing Value Treatment
# - Quarterly revenue data collection (API)
# - Monthly market cap data collection (API)
# - Export data collection (DB)
# - Data merge and PSR calculation
# - Strong DB data supplementation for FMP missing values (2013-2024)
# - Missing value verification and reporting
# """
#
# import requests
# import pandas as pd
# import numpy as np
# import calendar
# import time
# from datetime import datetime
# from tqdm import tqdm
# import warnings
# from sqlalchemy import create_engine
# import gc
#
# warnings.filterwarnings('ignore')
#
# # stock_invest_function module import attempt (optional)
# try:
#     from stock_invest_function import get_db_host
#
#     STOCK_FUNCTION_AVAILABLE = True
# except ImportError:
#     STOCK_FUNCTION_AVAILABLE = False
#
#
#     def get_db_host():
#         return 'localhost'
#
#
# # ==============================================
# # Utility Functions
# # ==============================================
#
# def convert_to_month_end(date_str):
#     """Convert date to month end"""
#     try:
#         if isinstance(date_str, str):
#             date_obj = pd.to_datetime(date_str)
#         else:
#             date_obj = date_str
#
#         year = date_obj.year
#         month = date_obj.month
#         last_day = calendar.monthrange(year, month)[1]
#         month_end = datetime(year, month, last_day)
#         return month_end
#     except Exception as e:
#         return None
#
#
# def test_api_connection(api_key):
#     """Test API connection"""
#     test_url = "https://financialmodelingprep.com/api/v3/income-statement/AAPL"
#     test_params = {'limit': 1, 'apikey': api_key, 'period': 'quarter'}
#
#     try:
#         response = requests.get(test_url, params=test_params, timeout=10)
#
#         if response.status_code == 401:
#             return False, "Invalid API key"
#         elif response.status_code == 429:
#             return False, "API rate limit exceeded"
#         elif response.status_code != 200:
#             return False, f"API error: {response.status_code}"
#
#         data = response.json()
#         if isinstance(data, dict) and 'Error Message' in data:
#             return False, f"API error: {data['Error Message']}"
#         elif not data:
#             return False, "Empty response from API"
#
#         return True, "API connection successful"
#
#     except Exception as e:
#         return False, f"API connection failed: {str(e)}"
#
#
# # ==============================================
# # Revenue Data Collection Functions
# # ==============================================
#
# def fetch_revenue_data(ticker, api_key):
#     """Fetch quarterly revenue data"""
#     url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
#     params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}
#
#     try:
#         response = requests.get(url, params=params, timeout=30)
#
#         if response.status_code != 200:
#             return None, f"HTTP {response.status_code}"
#
#         data = response.json()
#
#         if isinstance(data, dict) and 'Error Message' in data:
#             return None, f"API error: {data['Error Message']}"
#
#         if not data:
#             return None, "No data"
#
#         return data, None
#
#     except Exception as e:
#         return None, f"Error: {str(e)}"
#
#
# def add_revenue_ttm(df):
#     """Add TTM (Trailing Twelve Months) column to quarterly revenue data"""
#     df_copy = df.copy()
#     df_copy = df_copy.sort_values(['ticker', 'date'])
#
#     ttm_values = []
#
#     for ticker in df_copy['ticker'].unique():
#         ticker_data = df_copy[df_copy['ticker'] == ticker].copy()
#         ticker_data = ticker_data.sort_values('date')
#         ticker_data['revenue_ttm'] = ticker_data['revenue'].rolling(window=4, min_periods=1).sum()
#         ttm_values.extend(ticker_data['revenue_ttm'].tolist())
#
#     df_copy['revenue_ttm'] = ttm_values
#     return df_copy
#
#
# def collect_revenue_data(tickers, api_key, request_delay=0.3):
#     """Collect quarterly revenue data for multiple tickers (2013-2024)"""
#     all_revenue_data = []
#
#     for ticker in tqdm(tickers, desc="Revenue"):
#         revenue_data, error = fetch_revenue_data(ticker, api_key)
#
#         if revenue_data is None:
#             continue
#
#         for item in revenue_data:
#             # Filter data: 2013-2024 only
#             item_date = pd.to_datetime(item.get('date', ''))
#             if item_date.year < 2013 or item_date.year > 2024:
#                 continue
#
#             all_revenue_data.append({
#                 'ticker': ticker,
#                 'date': item.get('date', ''),
#                 'calendar_year': item.get('calendarYear', ''),
#                 'period': item.get('period', ''),
#                 'revenue': item.get('revenue', 0) if item.get('revenue') is not None else 0,
#                 'revenue_billions': round((item.get('revenue', 0) or 0) / 1_000_000_000, 2),
#             })
#
#         time.sleep(request_delay)
#
#     # Create and process DataFrame
#     revenue_df = pd.DataFrame(all_revenue_data) if all_revenue_data else pd.DataFrame()
#
#     if not revenue_df.empty:
#         revenue_df['date'] = pd.to_datetime(revenue_df['date'])
#         revenue_df = revenue_df.sort_values(['ticker', 'date'])
#         revenue_df['date_month_end'] = revenue_df['date'].apply(convert_to_month_end)
#
#         # Add TTM
#         revenue_df_with_ttm = add_revenue_ttm(revenue_df)
#         revenue_df_with_ttm['revenue_ttm_billions'] = revenue_df_with_ttm['revenue_ttm'] / 1_000_000_000
#         revenue_df_with_ttm['date_month_end'] = revenue_df_with_ttm['date'].apply(convert_to_month_end)
#
#         return revenue_df_with_ttm
#
#     return pd.DataFrame()
#
#
# # ==============================================
# # Market Cap Data Collection Functions
# # ==============================================
#
# def fetch_market_data_yearly(ticker, api_key, start_year=2013, end_year=2024):
#     """Fetch market cap data by year (2013-2024)"""
#     all_data = []
#
#     for year in range(start_year, end_year + 1):
#         start_date_str = f"{year}-01-01"
#         end_date_str = f"{year}-12-31"
#
#         url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
#         params = {'from': start_date_str, 'to': end_date_str, 'apikey': api_key}
#
#         try:
#             response = requests.get(url, params=params, timeout=30)
#             if response.status_code == 200:
#                 data = response.json()
#                 if data and isinstance(data, list):
#                     all_data.extend(data)
#             time.sleep(0.3)
#         except Exception as e:
#             continue
#
#     return all_data if all_data else None, None
#
#
# def process_daily_to_monthly_market_data(daily_data, ticker):
#     """Convert daily market cap data to monthly (month-end basis)"""
#     if not daily_data:
#         return pd.DataFrame()
#
#     df = pd.DataFrame(daily_data)
#     df['date'] = pd.to_datetime(df['date'])
#     df = df.sort_values('date')
#     df['year_month'] = df['date'].dt.to_period('M')
#
#     monthly_data = []
#
#     for year_month in df['year_month'].unique():
#         month_data = df[df['year_month'] == year_month]
#         last_day_data = month_data.loc[month_data['date'].idxmax()]
#
#         monthly_data.append({
#             'ticker': ticker,
#             'date': last_day_data['date'],
#             'market_cap': last_day_data['marketCap'],
#             'market_cap_billions': round(last_day_data['marketCap'] / 1_000_000_000, 2),
#         })
#
#     return pd.DataFrame(monthly_data)
#
#
# def collect_market_cap_data(tickers, api_key, start_year=2013):
#     """Collect monthly market cap data for multiple tickers (2013-2024)"""
#     all_market_data = []
#
#     for ticker in tqdm(tickers, desc="Market Cap"):
#         data, error = fetch_market_data_yearly(ticker, api_key, start_year, 2024)
#
#         if not data:
#             continue
#
#         monthly_df = process_daily_to_monthly_market_data(data, ticker)
#
#         if not monthly_df.empty:
#             monthly_df['date_month_end'] = monthly_df['date'].apply(convert_to_month_end)
#             all_market_data.append(monthly_df)
#
#     if all_market_data:
#         market_df = pd.concat(all_market_data, ignore_index=True)
#         market_df = market_df.sort_values(['ticker', 'date_month_end'])
#         return market_df
#
#     return pd.DataFrame()
#
#
# # ==============================================
# # Export Data Collection Functions
# # ==============================================
#
# def get_hs_data(hs_code_6d, db_info):
#     """Extract trade data by HS Code (2013-2024)"""
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
#         )
#
#         query = f"""
#         SELECT * FROM us_trade_monthly_data_with_forecast
#         WHERE hs_code_6d = '{hs_code_6d}'
#         AND date >= '2013-01-01'
#         AND date <= '2024-12-31'
#         ORDER BY date DESC
#         """
#
#         df = pd.read_sql(query, engine)
#         engine.dispose()
#
#         if not df.empty:
#             df['date'] = pd.to_datetime(df['date'])
#
#         return df
#
#     except Exception as e:
#         return pd.DataFrame()
#
#
# def get_latest_input_date_data(df):
#     """Extract data with the latest input_date only"""
#     if 'input_date' not in df.columns:
#         return pd.DataFrame()
#
#     df_copy = df.copy()
#     if not pd.api.types.is_datetime64_any_dtype(df_copy['input_date']):
#         df_copy['input_date'] = pd.to_datetime(df_copy['input_date'])
#
#     latest_date = df_copy['input_date'].max()
#     latest_data = df_copy[df_copy['input_date'] == latest_date].copy()
#
#     return latest_data
#
#
# def collect_export_data(hs_code, db_info):
#     """Collect export data (2013-2024)"""
#     export_df = get_hs_data(hs_code, db_info)
#
#     if export_df.empty:
#         return pd.DataFrame()
#
#     latest_export_data = get_latest_input_date_data(export_df)
#     latest_export_data = latest_export_data.sort_values('date').reset_index(drop=True)
#
#     if not latest_export_data.empty:
#         latest_export_data['date'] = pd.to_datetime(latest_export_data['date'])
#         latest_export_data['date_month_end'] = latest_export_data['date'].apply(convert_to_month_end)
#
#     return latest_export_data
#
#
# # ==============================================
# # DB Data Supplementation Functions (Enhanced)
# # ==============================================
#
# def fetch_db_revenue_data(ticker, db_info, start_date='2013-01-01', end_date='2024-12-31'):
#     """Extract saleq data from DB (2013-2024)"""
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
#         )
#
#         query = f"""
#         SELECT date, ticker, saleq
#         FROM fundq_df
#         WHERE ticker = '{ticker}'
#         AND saleq IS NOT NULL
#         AND date >= '{start_date}'
#         AND date <= '{end_date}'
#         ORDER BY date ASC
#         """
#
#         df = pd.read_sql(query, con=engine)
#         engine.dispose()
#
#         if not df.empty:
#             df['date'] = pd.to_datetime(df['date'])
#             df['revenue_billions'] = df['saleq'] / 1000  # Unit adjustment
#             df['date_month_end'] = df['date'].apply(convert_to_month_end)
#
#         return df[['ticker', 'date', 'date_month_end', 'revenue_billions']]
#
#     except Exception as e:
#         return pd.DataFrame()
#
#
# def fetch_db_market_data(ticker, db_info, start_date='2013-01-01', end_date='2024-12-31'):
#     """Extract me data from DB (2013-2024)"""
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
#         )
#
#         query = f"""
#         SELECT date, ticker, me
#         FROM fundm_df
#         WHERE ticker = '{ticker}'
#         AND me IS NOT NULL
#         AND date >= '{start_date}'
#         AND date <= '{end_date}'
#         ORDER BY date ASC
#         """
#
#         df = pd.read_sql(query, con=engine)
#         engine.dispose()
#
#         if not df.empty:
#             df['date'] = pd.to_datetime(df['date'])
#             df['market_cap_billions'] = df['me'] / 1000  # Unit adjustment
#             df['date_month_end'] = df['date'].apply(convert_to_month_end)
#
#         return df[['ticker', 'date', 'date_month_end', 'market_cap_billions']]
#
#     except Exception as e:
#         return pd.DataFrame()
#
#
# def comprehensive_supplement_revenue_with_db(fmp_revenue_df, ticker, db_info):
#     """Comprehensive revenue data supplementation with aggressive gap filling"""
#     db_revenue_df = fetch_db_revenue_data(ticker, db_info)
#
#     if db_revenue_df.empty:
#         return fmp_revenue_df, {'db_records': 0, 'overwritten': 0, 'added': 0}
#
#     enhanced_revenue_df = fmp_revenue_df.copy()
#     enhanced_revenue_df['year_month'] = enhanced_revenue_df['date_month_end'].dt.to_period('M')
#
#     overwritten_count = 0
#     added_count = 0
#     filled_count = 0
#
#     # Step 1: Direct monthly mapping with DB data
#     for _, db_row in db_revenue_df.iterrows():
#         year_month = db_row['date_month_end'].to_period('M')
#         revenue_value = db_row['revenue_billions']
#
#         mask = enhanced_revenue_df['year_month'] == year_month
#         if mask.any():
#             # Overwrite existing data
#             enhanced_revenue_df.loc[mask, 'revenue_billions'] = revenue_value
#             enhanced_revenue_df.loc[mask, 'revenue'] = revenue_value * 1_000_000_000
#             overwritten_count += 1
#         else:
#             # Add new data
#             new_row = {
#                 'ticker': ticker,
#                 'date': db_row['date_month_end'],
#                 'date_month_end': db_row['date_month_end'],
#                 'revenue_billions': revenue_value,
#                 'revenue': revenue_value * 1_000_000_000,
#                 'year_month': year_month,
#                 'calendar_year': db_row['date_month_end'].year,
#                 'period': f"Q{((db_row['date_month_end'].month - 1) // 3) + 1}"
#             }
#             enhanced_revenue_df = pd.concat([enhanced_revenue_df, pd.DataFrame([new_row])], ignore_index=True)
#             added_count += 1
#
#     # Step 2: Aggressive gap filling - fill any remaining NaN with nearest available data
#     enhanced_revenue_df = enhanced_revenue_df.sort_values('date_month_end').reset_index(drop=True)
#
#     # Find rows with NaN revenue
#     nan_mask = enhanced_revenue_df['revenue_billions'].isna()
#     if nan_mask.any():
#         # Get all available DB data for reference
#         db_revenue_df_sorted = db_revenue_df.sort_values('date_month_end').reset_index(drop=True)
#
#         for idx in enhanced_revenue_df[nan_mask].index:
#             target_date = enhanced_revenue_df.loc[idx, 'date_month_end']
#
#             # Find nearest date in DB data
#             if not db_revenue_df_sorted.empty:
#                 db_revenue_df_sorted['date_diff'] = abs(db_revenue_df_sorted['date_month_end'] - target_date)
#                 nearest_idx = db_revenue_df_sorted['date_diff'].idxmin()
#                 nearest_value = db_revenue_df_sorted.loc[nearest_idx, 'revenue_billions']
#
#                 # Fill with nearest value
#                 enhanced_revenue_df.loc[idx, 'revenue_billions'] = nearest_value
#                 enhanced_revenue_df.loc[idx, 'revenue'] = nearest_value * 1_000_000_000
#                 filled_count += 1
#
#     enhanced_revenue_df = enhanced_revenue_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(
#         drop=True)
#
#     stats = {
#         'db_records': len(db_revenue_df),
#         'overwritten': overwritten_count,
#         'added': added_count,
#         'gap_filled': filled_count
#     }
#
#     return enhanced_revenue_df, stats
#
#
# def comprehensive_supplement_market_with_db(fmp_market_df, ticker, db_info):
#     """Comprehensive market cap data supplementation with aggressive gap filling"""
#     db_market_df = fetch_db_market_data(ticker, db_info)
#
#     if db_market_df.empty:
#         return fmp_market_df, {'db_records': 0, 'filled': 0, 'added': 0}
#
#     enhanced_market_df = fmp_market_df.copy()
#     enhanced_market_df['year_month'] = enhanced_market_df['date_month_end'].dt.to_period('M')
#
#     filled_count = 0
#     added_count = 0
#     gap_filled_count = 0
#
#     # Step 1: Direct monthly mapping with DB data
#     for _, db_row in db_market_df.iterrows():
#         year_month = db_row['date_month_end'].to_period('M')
#         market_value = db_row['market_cap_billions']
#
#         mask = (enhanced_market_df['year_month'] == year_month)
#
#         if mask.any():
#             # Fill only if existing data is NaN
#             nan_mask = mask & enhanced_market_df['market_cap_billions'].isna()
#             if nan_mask.any():
#                 enhanced_market_df.loc[nan_mask, 'market_cap_billions'] = market_value
#                 enhanced_market_df.loc[nan_mask, 'market_cap'] = market_value * 1_000_000_000
#                 filled_count += 1
#         else:
#             # Add new data
#             new_row = {
#                 'ticker': ticker,
#                 'date': db_row['date_month_end'],
#                 'date_month_end': db_row['date_month_end'],
#                 'market_cap_billions': market_value,
#                 'market_cap': market_value * 1_000_000_000,
#                 'year_month': year_month
#             }
#             enhanced_market_df = pd.concat([enhanced_market_df, pd.DataFrame([new_row])], ignore_index=True)
#             added_count += 1
#
#     # Step 2: Aggressive gap filling for market cap
#     enhanced_market_df = enhanced_market_df.sort_values('date_month_end').reset_index(drop=True)
#
#     # Find rows with NaN market cap
#     nan_mask = enhanced_market_df['market_cap_billions'].isna()
#     if nan_mask.any():
#         # Get all available DB data for reference
#         db_market_df_sorted = db_market_df.sort_values('date_month_end').reset_index(drop=True)
#
#         for idx in enhanced_market_df[nan_mask].index:
#             target_date = enhanced_market_df.loc[idx, 'date_month_end']
#
#             # Find nearest date in DB data
#             if not db_market_df_sorted.empty:
#                 db_market_df_sorted['date_diff'] = abs(db_market_df_sorted['date_month_end'] - target_date)
#                 nearest_idx = db_market_df_sorted['date_diff'].idxmin()
#                 nearest_value = db_market_df_sorted.loc[nearest_idx, 'market_cap_billions']
#
#                 # Fill with nearest value
#                 enhanced_market_df.loc[idx, 'market_cap_billions'] = nearest_value
#                 enhanced_market_df.loc[idx, 'market_cap'] = nearest_value * 1_000_000_000
#                 gap_filled_count += 1
#
#     enhanced_market_df = enhanced_market_df.drop('year_month', axis=1).sort_values('date_month_end').reset_index(
#         drop=True)
#
#     stats = {
#         'db_records': len(db_market_df),
#         'filled': filled_count,
#         'added': added_count,
#         'gap_filled': gap_filled_count
#     }
#
#     return enhanced_market_df, stats
#
#
# def comprehensive_forward_fill(merged_data, max_fill_months=2):
#     """Apply comprehensive forward fill with detailed tracking"""
#     df = merged_data.copy()
#
#     # Count NaN before forward fill
#     before_revenue_nan = df['revenue_billions'].isna().sum()
#     before_market_nan = df['market_cap_billions'].isna().sum()
#
#     # Apply forward fill
#     df['revenue_billions'] = df.groupby('ticker')['revenue_billions'].ffill(limit=max_fill_months)
#     df['market_cap_billions'] = df.groupby('ticker')['market_cap_billions'].ffill(limit=3)
#
#     # Count NaN after forward fill
#     after_revenue_nan = df['revenue_billions'].isna().sum()
#     after_market_nan = df['market_cap_billions'].isna().sum()
#
#     stats = {
#         'before_revenue_nan': before_revenue_nan,
#         'before_market_nan': before_market_nan,
#         'after_revenue_nan': after_revenue_nan,
#         'after_market_nan': after_market_nan,
#         'revenue_filled': before_revenue_nan - after_revenue_nan,
#         'market_filled': before_market_nan - after_market_nan
#     }
#
#     return df, stats
#
#
# # ==============================================
# # Data Merge and PSR Calculation Functions
# # ==============================================
#
# def calculate_enhanced_ttm_and_psr(merged_data):
#     """Calculate enhanced TTM and PSR"""
#     df = merged_data.copy()
#     df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)
#
#     # Calculate TTM from quarterly revenue
#     df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0,
#                                                                                                                     drop=True)
#     df['revenue_ttm_billions'] = df['revenue_ttm']
#
#     # Apply 2-month shift
#     df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)
#
#     # Calculate PSR
#     df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']
#
#     # Handle infinite values
#     df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)
#
#     return df
#
#
# def calculate_export_yoy_growth(df):
#     """Calculate YoY growth rate for export data"""
#     if 'expDlr' not in df.columns or df['expDlr'].isna().all():
#         df['expDlr_yoy'] = pd.NA
#         return df
#
#     df = df.sort_values('date_month_end').reset_index(drop=True)
#     df['expDlr_yoy'] = df['expDlr'].pct_change(periods=12) * 100  # YoY growth rate (%)
#
#     return df
#
#
# def merge_with_export_data(merged_data, export_data):
#     """Merge final data with export data - preserve export forecasts"""
#     if export_data.empty:
#         merged_data['hs_code_6d'] = None
#         merged_data['expDlr'] = pd.NA
#         merged_data['expDlr_yoy'] = pd.NA
#         return merged_data
#
#     # Extract required columns from export data
#     export_subset = export_data[['date_month_end', 'hs_code_6d', 'expDlr']].copy()
#
#     # Calculate YoY growth rate
#     export_subset = calculate_export_yoy_growth(export_subset)
#
#     # Merge data (outer join to preserve all data)
#     final_data = pd.merge(merged_data, export_subset, on='date_month_end', how='outer')
#
#     # Sort by date
#     final_data = final_data.sort_values('date_month_end').reset_index(drop=True)
#
#     # Fill ticker NaN values with ffill
#     if 'ticker' in final_data.columns:
#         final_data['ticker'] = final_data['ticker'].ffill()
#         # Apply bfill for cases where first row is NaN
#         final_data['ticker'] = final_data['ticker'].bfill()
#
#     return final_data
#
#
# # ==============================================
# # Comprehensive Missing Value Analysis Functions
# # ==============================================
#
# def analyze_missing_values(df, name="DataFrame"):
#     """Comprehensive missing value analysis"""
#     stats = {}
#
#     if df.empty:
#         return stats
#
#     total_records = len(df)
#     stats['total_records'] = total_records
#
#     # Analyze key columns
#     key_columns = ['revenue_billions', 'market_cap_billions', 'expDlr']
#
#     for col in key_columns:
#         if col in df.columns:
#             nan_count = df[col].isna().sum()
#             valid_count = total_records - nan_count
#             nan_ratio = (nan_count / total_records * 100) if total_records > 0 else 0
#
#             stats[f'{col}_total'] = total_records
#             stats[f'{col}_nan'] = nan_count
#             stats[f'{col}_valid'] = valid_count
#             stats[f'{col}_nan_ratio'] = round(nan_ratio, 2)
#
#     return stats
#
#
# def print_missing_value_report(before_stats, after_stats, stage_name):
#     """Print concise missing value improvement report"""
#     print(f"\n{stage_name} Results:")
#     print("-" * 40)
#
#     for col in ['revenue_billions', 'market_cap_billions']:
#         if f'{col}_nan' in before_stats and f'{col}_nan' in after_stats:
#             before_nan = before_stats[f'{col}_nan']
#             after_nan = after_stats[f'{col}_nan']
#             improvement = before_nan - after_nan
#
#             col_name = col.replace('_billions', '').replace('_', ' ').title()
#             print(f"{col_name}: {before_nan} → {after_nan} NaN (improved: {improvement})")
#
#
# # ==============================================
# # Main Execution Functions
# # ==============================================
#
# def run_comprehensive_enhanced_preprocessing(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=False):
#     """
#     Comprehensive preprocessing pipeline with strong missing value treatment and verification
#
#     Parameters:
#     - ticker (str): Stock ticker to analyze
#     - api_key (str): FMP API key
#     - db_info (dict): DB connection info
#     - hs_code (str, optional): HS code
#     - start_year (int): Data collection start year (default: 2013)
#     - verbose (bool): Print detailed progress reports
#
#     Returns:
#     - tuple: (final_data, comprehensive_stats)
#     """
#
#     if verbose:
#         print(f"Starting comprehensive preprocessing for {ticker}")
#         print("=" * 60)
#
#     # Memory management
#     gc.collect()
#
#     # 1. Collect FMP data
#     if verbose:
#         print("1. Collecting FMP data...")
#
#     fmp_revenue_df = collect_revenue_data([ticker], api_key)
#     fmp_market_df = collect_market_cap_data([ticker], api_key, start_year)
#
#     if fmp_revenue_df.empty or fmp_market_df.empty:
#         return None, {'error': 'FMP data collection failed'}
#
#     # Analyze original FMP data
#     fmp_revenue_stats = analyze_missing_values(fmp_revenue_df, "FMP Revenue")
#     fmp_market_stats = analyze_missing_values(fmp_market_df, "FMP Market")
#
#     if verbose:
#         print(f"FMP Revenue: {len(fmp_revenue_df)} records, {fmp_revenue_stats.get('revenue_billions_nan', 0)} NaN")
#         print(f"FMP Market: {len(fmp_market_df)} records, {fmp_market_stats.get('market_cap_billions_nan', 0)} NaN")
#
#     # 2. Comprehensive DB supplementation
#     if verbose:
#         print("2. Applying comprehensive DB supplementation...")
#
#     enhanced_revenue_df, revenue_supplement_stats = comprehensive_supplement_revenue_with_db(fmp_revenue_df, ticker,
#                                                                                              db_info)
#     enhanced_market_df, market_supplement_stats = comprehensive_supplement_market_with_db(fmp_market_df, ticker,
#                                                                                           db_info)
#
#     # Analyze after DB supplementation
#     enhanced_revenue_stats = analyze_missing_values(enhanced_revenue_df, "Enhanced Revenue")
#     enhanced_market_stats = analyze_missing_values(enhanced_market_df, "Enhanced Market")
#
#     if verbose:
#         print(f"DB Revenue supplementation: {revenue_supplement_stats}")
#         print(f"DB Market supplementation: {market_supplement_stats}")
#         print_missing_value_report(fmp_revenue_stats, enhanced_revenue_stats, "DB Supplementation - Revenue")
#         print_missing_value_report(fmp_market_stats, enhanced_market_stats, "DB Supplementation - Market")
#
#     # Memory cleanup
#     del fmp_revenue_df, fmp_market_df
#     gc.collect()
#
#     # 3. Data merging
#     if verbose:
#         print("3. Merging data...")
#
#     revenue_subset = enhanced_revenue_df[['ticker', 'date_month_end', 'revenue_billions']].copy()
#     market_subset = enhanced_market_df[['ticker', 'date_month_end', 'market_cap_billions']].copy()
#
#     merged_data = pd.merge(market_subset, revenue_subset, on=['ticker', 'date_month_end'], how='outer')
#     merged_data = merged_data.sort_values('date_month_end').reset_index(drop=True)
#
#     # Analyze before forward fill
#     before_fill_stats = analyze_missing_values(merged_data, "Before Forward Fill")
#
#     # 4. Comprehensive forward fill
#     if verbose:
#         print("4. Applying comprehensive forward fill...")
#
#     merged_data_filled, fill_stats = comprehensive_forward_fill(merged_data)
#
#     # Analyze after forward fill
#     after_fill_stats = analyze_missing_values(merged_data_filled, "After Forward Fill")
#
#     if verbose:
#         print_missing_value_report(before_fill_stats, after_fill_stats, "Forward Fill")
#
#     # Memory cleanup
#     del enhanced_revenue_df, enhanced_market_df, revenue_subset, market_subset, merged_data
#     gc.collect()
#
#     # 5. Calculate TTM and PSR
#     if verbose:
#         print("5. Calculating TTM and PSR...")
#
#     final_data = calculate_enhanced_ttm_and_psr(merged_data_filled)
#
#     # 6. Merge export data (optional)
#     if hs_code:
#         if verbose:
#             print("6. Merging export data...")
#         export_data = collect_export_data(hs_code, db_info)
#         final_data = merge_with_export_data(final_data, export_data)
#     else:
#         # Add empty export columns if no export data
#         final_data['hs_code_6d'] = None
#         final_data['expDlr'] = pd.NA
#         final_data['expDlr_yoy'] = pd.NA
#
#         # Apply ticker ffill
#         if 'ticker' in final_data.columns:
#             final_data['ticker'] = final_data['ticker'].ffill().bfill()
#
#     # Final analysis
#     final_stats = analyze_missing_values(final_data, "Final Data")
#
#     if verbose:
#         print(f"7. Final data: {len(final_data)} records")
#         print(f"Final Revenue NaN: {final_stats.get('revenue_billions_nan', 0)}")
#         print(f"Final Market NaN: {final_stats.get('market_cap_billions_nan', 0)}")
#         print("=" * 60)
#         print("Preprocessing completed successfully")
#
#     # Comprehensive statistics
#     comprehensive_stats = {
#         'ticker': ticker,
#         'total_records': len(final_data),
#         'fmp_original': {
#             'revenue_nan': fmp_revenue_stats.get('revenue_billions_nan', 0),
#             'market_nan': fmp_market_stats.get('market_cap_billions_nan', 0)
#         },
#         'db_supplementation': {
#             'revenue': revenue_supplement_stats,
#             'market': market_supplement_stats
#         },
#         'forward_fill': fill_stats,
#         'final_result': {
#             'revenue_nan': final_stats.get('revenue_billions_nan', 0),
#             'market_nan': final_stats.get('market_cap_billions_nan', 0),
#             'revenue_improvement': fmp_revenue_stats.get('revenue_billions_nan', 0) - final_stats.get(
#                 'revenue_billions_nan', 0),
#             'market_improvement': fmp_market_stats.get('market_cap_billions_nan', 0) - final_stats.get(
#                 'market_cap_billions_nan', 0)
#         }
#     }
#
#     # Final memory cleanup
#     gc.collect()
#
#     return final_data, comprehensive_stats
#
#
# def run_enhanced_preprocessing(ticker, api_key, db_info, hs_code=None, start_year=2013):
#     """
#     Simple version without verbose output (backward compatibility)
#     """
#     result, stats = run_comprehensive_enhanced_preprocessing(
#         ticker, api_key, db_info, hs_code, start_year, verbose=False
#     )
#     return result
#
#
# def run_enhanced_preprocessing_with_stats(ticker, api_key, db_info, hs_code=None, start_year=2013):
#     """
#     Version with comprehensive statistics (backward compatibility)
#     """
#     result, comprehensive_stats = run_comprehensive_enhanced_preprocessing(
#         ticker, api_key, db_info, hs_code, start_year, verbose=False
#     )
#
#     # Convert to simple stats format for backward compatibility
#     if result is not None and 'final_result' in comprehensive_stats:
#         simple_stats = {
#             'total_records': comprehensive_stats['total_records'],
#             'revenue_improvement': comprehensive_stats['final_result']['revenue_improvement'],
#             'market_improvement': comprehensive_stats['final_result']['market_improvement'],
#             'final_revenue_nan': comprehensive_stats['final_result']['revenue_nan'],
#             'final_market_nan': comprehensive_stats['final_result']['market_nan']
#         }
#         return result, simple_stats
#
#     return result, {}
#
#
# def run_simple_enhanced_preprocessing(ticker, api_key, db_info, start_year=2013):
#     """
#     Simple version (excluding export data)
#     """
#     return run_enhanced_preprocessing(ticker, api_key, db_info, start_year=start_year)
#
#
# # ==============================================
# # Verification and Testing Functions
# # ==============================================
#
# def verify_preprocessing_quality(final_data, comprehensive_stats, ticker):
#     """
#     Verify the quality of preprocessing results
#     """
#     verification_report = {
#         'ticker': ticker,
#         'status': 'SUCCESS',
#         'issues': [],
#         'summary': {}
#     }
#
#     if final_data is None or final_data.empty:
#         verification_report['status'] = 'FAILED'
#         verification_report['issues'].append('No final data generated')
#         return verification_report
#
#     # Check data range (should be 2013-2024)
#     date_range = final_data['date_month_end']
#     min_date = date_range.min()
#     max_date = date_range.max()
#
#     if min_date.year < 2013 or max_date.year > 2024:
#         verification_report['issues'].append(f'Date range outside 2013-2024: {min_date} to {max_date}')
#
#     # Check critical columns
#     required_columns = ['ticker', 'date_month_end', 'market_cap_billions', 'revenue_billions', 'PSR_ttm']
#     missing_columns = [col for col in required_columns if col not in final_data.columns]
#     if missing_columns:
#         verification_report['issues'].append(f'Missing required columns: {missing_columns}')
#
#     # Check missing value improvement
#     if 'final_result' in comprehensive_stats:
#         final_result = comprehensive_stats['final_result']
#         revenue_improvement = final_result.get('revenue_improvement', 0)
#         market_improvement = final_result.get('market_improvement', 0)
#
#         verification_report['summary'] = {
#             'total_records': len(final_data),
#             'revenue_improvement': revenue_improvement,
#             'market_improvement': market_improvement,
#             'final_revenue_nan': final_result.get('revenue_nan', 0),
#             'final_market_nan': final_result.get('market_nan', 0),
#             'date_range': f"{min_date.strftime('%Y-%m')} to {max_date.strftime('%Y-%m')}"
#         }
#
#     # Determine overall status
#     if len(verification_report['issues']) == 0:
#         verification_report['status'] = 'SUCCESS'
#     elif len(verification_report['issues']) <= 2:
#         verification_report['status'] = 'WARNING'
#     else:
#         verification_report['status'] = 'FAILED'
#
#     return verification_report
#
#
# def print_verification_report(verification_report):
#     """Print a concise verification report"""
#     print(f"\nPreprocessing Verification Report - {verification_report['ticker']}")
#     print("=" * 50)
#     print(f"Status: {verification_report['status']}")
#
#     if verification_report['summary']:
#         summary = verification_report['summary']
#         print(f"Total Records: {summary['total_records']}")
#         print(f"Date Range: {summary['date_range']}")
#         print(f"Revenue Improvement: {summary['revenue_improvement']} NaN values fixed")
#         print(f"Market Improvement: {summary['market_improvement']} NaN values fixed")
#         print(f"Final Revenue NaN: {summary['final_revenue_nan']}")
#         print(f"Final Market NaN: {summary['final_market_nan']}")
#
#     if verification_report['issues']:
#         print(f"\nIssues Found ({len(verification_report['issues'])}):")
#         for issue in verification_report['issues']:
#             print(f"  - {issue}")
#
#     print("=" * 50)
#
#
# # ==============================================
# # Main Execution Function with Full Verification
# # ==============================================
#
# def run_complete_preprocessing_with_verification(ticker, api_key, db_info, hs_code=None, start_year=2013, verbose=True):
#     """
#     Complete preprocessing pipeline with comprehensive verification
#
#     Returns:
#     - tuple: (final_data, verification_report, comprehensive_stats)
#     """
#
#     # Run comprehensive preprocessing
#     final_data, comprehensive_stats = run_comprehensive_enhanced_preprocessing(
#         ticker, api_key, db_info, hs_code, start_year, verbose
#     )
#
#     # Verify results
#     verification_report = verify_preprocessing_quality(final_data, comprehensive_stats, ticker)
#
#     # Print verification report if verbose
#     if verbose:
#         print_verification_report(verification_report)
#
#     return final_data, verification_report, comprehensive_stats
#
#
# # ==============================================
# # Additional Utility Functions
# # ==============================================
#
# def fetch_ticker_and_item(db_info: dict, ticker: str, table_name: str = "fundq_df") -> pd.DataFrame:
#     """
#     Efficiently fetch saleq data for specific ticker
#
#     Parameters:
#     - db_info (dict): DB connection info (user, password, host, port, database)
#     - ticker (str): Ticker to query (e.g. 'AMAT')
#     - table_name (str): Table name to query
#
#     Returns:
#     - pd.DataFrame: saleq data for the ticker
#     """
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
#         )
#
#         query = f"""
#         SELECT permno, edate, date, ticker, saleq
#         FROM {table_name}
#         WHERE ticker = '{ticker}'
#         AND saleq IS NOT NULL
#         AND date >= '2013-01-01'
#         AND date <= '2024-12-31'
#         ORDER BY date ASC
#         """
#
#         df = pd.read_sql(query, con=engine)
#         engine.dispose()
#         return df
#
#     except Exception as e:
#         return pd.DataFrame()
#
#
# def fetch_ticker_and_me(db_info: dict, ticker: str, table_name: str = "fundm_df") -> pd.DataFrame:
#     """
#     Efficiently fetch me data for specific ticker
#
#     Parameters:
#     - db_info (dict): DB connection info
#     - ticker (str): Ticker to query
#     - table_name (str): Table name to query
#
#     Returns:
#     - pd.DataFrame: me data for the ticker
#     """
#     try:
#         engine = create_engine(
#             f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
#             f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
#         )
#
#         query = f"""
#         SELECT permno, edate, date, ticker, me
#         FROM {table_name}
#         WHERE ticker = '{ticker}'
#         AND me IS NOT NULL
#         AND date >= '2013-01-01'
#         AND date <= '2024-12-31'
#         ORDER BY date ASC
#         """
#
#         df = pd.read_sql(query, con=engine)
#         engine.dispose()
#         return df
#
#     except Exception as e:
#         return pd.DataFrame()
#
#
# # ==============================================
# # Example Usage
# # ==============================================
#
# """
# # Basic usage (quiet mode)
# result = run_enhanced_preprocessing("AAPL", api_key, db_info)
#
# # With verification and detailed reporting
# result, verification, stats = run_complete_preprocessing_with_verification(
#     "AAPL", api_key, db_info, hs_code="854231", verbose=True
# )
#
# # Backward compatible usage with simple stats
# result, simple_stats = run_enhanced_preprocessing_with_stats("AAPL", api_key, db_info)
# """
#
#
#
#
# #
# # # Enhanced US Stock Valuation Preprocessing - 사용법 예시
# #
# # # 1. 필요한 라이브러리 import
# # from enhanced_preprocessing import run_enhanced_preprocessing, run_enhanced_preprocessing_with_stats
# #
# # # 2. 설정 정보 준비
# # api_key = "your_fmp_api_key_here"
# # ticker = "AAPL"  # 분석할 종목
# # hs_code = "854231"  # 수출 데이터용 HS 코드 (선택사항)
# #
# # # DB 연결 정보
# # db_info = {
# #     'user': 'your_username',
# #     'password': 'your_password',
# #     'host': 'localhost',
# #     'port': 3306,
# #     'database': 'your_database_name'
# # }
# #
# # # 3-1. 기본 사용법 (통계 출력 없음)
# # print("기본 전처리 실행...")
# # result = run_enhanced_preprocessing(
# #     ticker=ticker,
# #     api_key=api_key,
# #     db_info=db_info,
# #     hs_code=hs_code  # 수출 데이터가 필요한 경우만
# # )
# #
# # if result is not None:
# #     print(f"전처리 완료: {len(result)}건의 데이터")
# #     print("주요 컬럼:", list(result.columns))
# # else:
# #     print("전처리 실패")
# #
# # # 3-2. 통계 포함 사용법 (간단한 결과 확인)
# # print("\n통계 포함 전처리 실행...")
# # result, stats = run_enhanced_preprocessing_with_stats(
# #     ticker=ticker,
# #     api_key=api_key,
# #     db_info=db_info,
# #     hs_code=hs_code
# # )
# #
# # if result is not None:
# #     print(f"총 데이터: {stats['total_records']}건")
# #     print(f"매출 결측치 개선: {stats['revenue_improvement']}건")
# #     print(f"시가총액 결측치 개선: {stats['market_improvement']}건")
# #     print(f"최종 매출 결측치: {stats['final_revenue_nan']}건")
# #     print(f"최종 시가총액 결측치: {stats['final_market_nan']}건")
# #
# # # 4. 수출 데이터 없이 사용하는 경우
# # print("\n수출 데이터 없이 실행...")
# # result_simple = run_enhanced_preprocessing(
# #     ticker="MSFT",
# #     api_key=api_key,
# #     db_info=db_info
# #     # hs_code 생략
# # )
# #
# # # 5. 결과 데이터 확인
# # if result is not None:
# #     print("\n결과 데이터 샘플:")
# #     print(result.head())
# #
# #     print("\n주요 컬럼 정보:")
# #     print("- ticker: 종목 코드")
# #     print("- date_month_end: 월말 기준 날짜")
# #     print("- market_cap_billions: 시가총액 (십억달러)")
# #     print("- revenue_billions: 분기 매출 (십억달러)")
# #     print("- revenue_ttm_billions: TTM 매출 (십억달러)")
# #     print("- revenue_ttm_shift: 2개월 지연 TTM")
# #     print("- PSR_ttm: 주가매출비율")
# #     print("- expDlr: 수출액 (달러, 선택)")
# #     print("- expDlr_yoy: 수출 YoY 성장률 (%, 선택)")
# #
# # # 6. 간단한 전처리만 필요한 경우
# # from enhanced_preprocessing import run_simple_enhanced_preprocessing
# #
# # simple_result = run_simple_enhanced_preprocessing(
# #     ticker="GOOGL",
# #     api_key=api_key,
# #     db_info=db_info
# # )