# -*- coding: utf-8 -*-


import pandas as pd
from typing import Tuple, Optional, Dict, List, Any


def to_month_end_safe(date_series: pd.Series) -> pd.Series:
    """
    Convert dates to month-end format safely.

    Args:
        date_series: Pandas Series of dates

    Returns:
        Series with dates converted to month-end
    """
    try:
        return pd.to_datetime(date_series).dt.to_period('M').dt.to_timestamp('M')
    except Exception:
        return pd.Series([pd.NaT] * len(date_series))


def clean_rev_data(rev_data: pd.DataFrame) -> pd.DataFrame:
    """
    Clean revenue data by removing duplicates and sorting.

    Args:
        rev_data: DataFrame with revenue data

    Returns:
        Cleaned DataFrame
    """
    if rev_data.empty:
        return rev_data

    # Fix data types for common columns
    if 'calendar_year' in rev_data.columns:
        # Convert calendar_year to numeric, handling mixed types
        rev_data['calendar_year'] = pd.to_numeric(rev_data['calendar_year'], errors='coerce')

    if 'revenue' in rev_data.columns:
        rev_data['revenue'] = pd.to_numeric(rev_data['revenue'], errors='coerce').fillna(0)

    if 'revenue_billions' in rev_data.columns:
        rev_data['revenue_billions'] = pd.to_numeric(rev_data['revenue_billions'], errors='coerce').fillna(0)

    # Remove duplicates based on date_month_end
    rev_data = rev_data.drop_duplicates(subset=['date_month_end'], keep='first')

    # Sort by date
    rev_data = rev_data.sort_values('date_month_end').reset_index(drop=True)

    # Remove unnecessary columns if they exist
    cols_to_drop = [col for col in rev_data.columns if col.endswith('_y')]
    if cols_to_drop:
        rev_data = rev_data.drop(columns=cols_to_drop)

    return rev_data


def get_fmp_revenue(
        ticker: str,
        api_key: str,
        fetch_revenue_data_func,
        fetch_db_revenue_func,
        db_info: Dict[str, Any],
        start_date: str = '2015-01-01',
        log_func=None,
        error_list: Optional[List[Dict]] = None
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Fetch and process revenue data from FMP API and merge with database data.

    Args:
        ticker: Stock ticker symbol
        api_key: FMP API key
        fetch_revenue_data_func: Function to fetch revenue data from FMP
        fetch_db_revenue_func: Function to fetch revenue data from database
        db_info: Database connection information
        start_date: Start date for filtering data (default: '2015-01-01')
        log_func: Optional logging function
        error_list: Optional list to append error information

    Returns:
        Tuple of (processed DataFrame, error message)
        Returns (None, error_msg) if critical error occurs
    """

    def log(tag: str, msg: str):
        """Internal logging helper"""
        if log_func:
            log_func(tag, msg)
        else:
            print(f"[{tag}] {msg}")

    if error_list is None:
        error_list = []

    start_date_month = pd.to_datetime(start_date).to_period('M').to_timestamp('M')

    # ========================================
    # 1. FMP에서 매출 데이터 가져오기
    # ========================================
    revenue_data, error = fetch_revenue_data_func(ticker, api_key)
    if revenue_data is None:
        msg = f"FMP revenue fetch failed: {error}"
        log("ERR-FMP-REV", f"{ticker} {msg}")
        error_list.append({
            'ticker': ticker,
            'stage': 'fetch_revenue',
            'error': msg
        })
        return None, msg

    rows = len(revenue_data) if isinstance(revenue_data, list) else 0
    log("OK-FMP-REV", f"{ticker} raw_rows={rows}")

    # ========================================
    # 2. 매출 데이터 전처리
    # ========================================
    try:
        all_revenue_data = [{
            'ticker': ticker,
            'date': it.get('date', ''),
            'calendar_year': it.get('calendarYear', ''),
            'period': it.get('period', ''),
            'revenue': it.get('revenue', 0) if it.get('revenue') is not None else 0,
            'revenue_billions': round((it.get('revenue', 0) or 0) / 1_000_000_000, 2),
        } for it in revenue_data]

        fmp_revenue_df = pd.DataFrame(all_revenue_data)

        # Convert data types explicitly
        fmp_revenue_df['date'] = pd.to_datetime(fmp_revenue_df['date'])
        fmp_revenue_df['calendar_year'] = pd.to_numeric(fmp_revenue_df['calendar_year'], errors='coerce')
        fmp_revenue_df['revenue'] = pd.to_numeric(fmp_revenue_df['revenue'], errors='coerce').fillna(0)
        fmp_revenue_df['revenue_billions'] = pd.to_numeric(fmp_revenue_df['revenue_billions'], errors='coerce').fillna(
            0)

        fmp_revenue_df = fmp_revenue_df.sort_values(['ticker', 'date'])
        fmp_revenue_df['date_month_end'] = to_month_end_safe(fmp_revenue_df['date'])

        bad = fmp_revenue_df['date_month_end'].isna().sum()
        log("CHK-FMP-MEND", f"{ticker} nan_mend={bad} / {len(fmp_revenue_df)}")

        fmp_revenue_df = fmp_revenue_df.dropna(subset=['date_month_end'])
        fmp_revenue_df = fmp_revenue_df.drop_duplicates(
            subset=['date_month_end']
        ).sort_values('date_month_end').reset_index(drop=True)

    except Exception as e:
        msg = f"FMP revenue preprocessing failed: {str(e)}"
        log("EXC-FMP-REV", f"{ticker} e={e}")
        error_list.append({
            'ticker': ticker,
            'stage': 'fmp_revenue_preproc',
            'error': str(e)
        })
        return None, msg

    # ========================================
    # 3. DB에서 매출 데이터 가져오기 및 병합
    # ========================================
    try:
        db_revenue_raw = fetch_db_revenue_func(ticker, db_info)
        rows_db = 0 if db_revenue_raw is None or db_revenue_raw.empty else len(db_revenue_raw)
        log("OK-DB-REV", f"{ticker} rows={rows_db}")

        if rows_db and not db_revenue_raw.empty:
            # Ensure proper data types for DB data
            if 'calendar_year' in db_revenue_raw.columns:
                db_revenue_raw['calendar_year'] = pd.to_numeric(db_revenue_raw['calendar_year'], errors='coerce')
            if 'revenue' in db_revenue_raw.columns:
                db_revenue_raw['revenue'] = pd.to_numeric(db_revenue_raw['revenue'], errors='coerce').fillna(0)
            if 'revenue_billions' in db_revenue_raw.columns:
                db_revenue_raw['revenue_billions'] = pd.to_numeric(db_revenue_raw['revenue_billions'],
                                                                   errors='coerce').fillna(0)

            # Remove consecutive duplicate revenue values
            db_revenue_df = db_revenue_raw.loc[
                db_revenue_raw['revenue_billions'] != db_revenue_raw['revenue_billions'].shift()
                ]
        else:
            db_revenue_df = pd.DataFrame(
                columns=['ticker', 'date', 'date_month_end', 'revenue_billions']
            )

        # Merge FMP and DB data
        merged_rev_data = pd.merge(
            fmp_revenue_df,
            db_revenue_df,
            on=['ticker', 'date_month_end'],
            how='outer',
            suffixes=('_fmp', '_db')
        )

        # Filter by start date
        rev_data = merged_rev_data[merged_rev_data['date_month_end'] >= start_date_month].copy()

        # Combine revenue_billions columns (prefer FMP data)
        if 'revenue_billions_fmp' in rev_data.columns:
            rev_data['revenue_billions'] = rev_data['revenue_billions_fmp'].fillna(
                rev_data.get('revenue_billions_db', 0)
            )
            # Drop the suffixed columns
            rev_data = rev_data.drop(
                columns=[col for col in rev_data.columns if col.endswith(('_fmp', '_db'))],
                errors='ignore'
            )
        elif 'revenue_billions_x' in rev_data.columns:
            # Fallback for different merge suffix
            rev_data['revenue_billions'] = rev_data['revenue_billions_x'].fillna(
                rev_data.get('revenue_billions_y', 0)
            )
            rev_data = rev_data.drop(
                columns=[col for col in rev_data.columns if col.endswith(('_x', '_y'))],
                errors='ignore'
            )

        log("REV-MERGE", f"{ticker} merged_rows={len(rev_data)}")

        # Clean the merged data
        rev_data = clean_rev_data(rev_data)

        # Safely calculate year range
        yr_min, yr_max = None, None
        if 'calendar_year' in rev_data.columns:
            valid_years = rev_data['calendar_year'].dropna()
            if not valid_years.empty:
                try:
                    yr_min = int(valid_years.min())
                    yr_max = int(valid_years.max())
                except (ValueError, TypeError):
                    yr_min, yr_max = None, None

        log("OK-REV-CLEAN", f"{ticker} rows={len(rev_data)} yrmin={yr_min} yrmax={yr_max}")

        return rev_data, None

    except Exception as e:
        msg = f"DB revenue merge/clean failed: {str(e)}"
        log("EXC-DB-REV", f"{ticker} e={e}")
        error_list.append({
            'ticker': ticker,
            'stage': 'db_revenue_merge_clean',
            'error': str(e)
        })
        return None, msg


# Example usage function with mock data for testing
def example_usage():
    """
    Example of how to use get_fmp_revenue function with mock data.
    This is for demonstration purposes only.
    """
    print("=" * 70)
    print("Revenue Processor Module - Example Usage")
    print("=" * 70)
    print("\nThis is a mock example. To use this module in your project:")
    print("1. Import: from revenue_processor import get_fmp_revenue")
    print("2. Pass your actual fetch_revenue_data and fetch_db_revenue_data functions")
    print("3. See the usage example file for detailed integration instructions")
    print("=" * 70)

    # Mock functions that return proper format
    def mock_fetch_revenue_data(ticker, api_key):
        """Mock FMP API call - returns sample data"""
        print(f"\n[MOCK] Fetching revenue data for {ticker} from FMP API...")
        mock_data = [
            {
                'date': '2023-12-31',
                'calendarYear': 2023,
                'period': 'FY',
                'revenue': 383285000000
            },
            {
                'date': '2023-09-30',
                'calendarYear': 2023,
                'period': 'Q4',
                'revenue': 89498000000
            },
            {
                'date': '2022-12-31',
                'calendarYear': 2022,
                'period': 'FY',
                'revenue': 394328000000
            },
        ]
        return mock_data, None  # Returns (data, error)

    def mock_fetch_db_revenue_data(ticker, db_info):
        """Mock database query - returns empty DataFrame"""
        print(f"[MOCK] Fetching revenue data for {ticker} from database...")
        return pd.DataFrame()  # Empty DataFrame for this example

    def mock_log(tag, msg):
        """Mock logging function"""
        print(f"[{tag}] {msg}")

    # Configuration
    ticker = "AAPL"
    api_key = "mock_api_key"
    db_info = {
        'host': 'localhost',
        'database': 'mock_db',
        'user': 'mock_user',
        'password': 'mock_password'
    }

    error_list = []

    print("\n" + "=" * 70)
    print("Running mock example...")
    print("=" * 70)

    # Call the function with mock data
    revenue_df, error = get_fmp_revenue(
        ticker=ticker,
        api_key=api_key,
        fetch_revenue_data_func=mock_fetch_revenue_data,
        fetch_db_revenue_func=mock_fetch_db_revenue_data,
        db_info=db_info,
        start_date='2015-01-01',
        log_func=mock_log,
        error_list=error_list
    )

    print("\n" + "=" * 70)
    print("Results:")
    print("=" * 70)

    if revenue_df is not None:
        print(f"\n[SUCCESS] Successfully processed {len(revenue_df)} revenue records")
        print(f"\nDataFrame shape: {revenue_df.shape}")
        print(f"Columns: {list(revenue_df.columns)}")
        print("\nSample data:")
        print(revenue_df.to_string())
    else:
        print(f"\n[ERROR] Error occurred: {error}")

    if error_list:
        print(f"\nErrors collected: {len(error_list)}")
        for err in error_list:
            print(f"  - {err}")

    print("\n" + "=" * 70)
    print("Example completed!")
    print("=" * 70)


if __name__ == "__main__":
    print("\n[*] Running revenue_processor.py directly...\n")
    example_usage()
    print("\n[NOTE] To use this module in your project, import it:")
    print("   from revenue_processor import get_fmp_revenue")
    print("\n[INFO] See 'revenue_processor_usage_example.py' for detailed examples.\n")