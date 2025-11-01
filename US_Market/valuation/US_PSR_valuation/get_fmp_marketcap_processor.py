"""
FMP Market Cap Data Processor Module

This module handles fetching, processing, and merging market cap data from FMP API and database.
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional, Dict, List, Any, Callable


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


def get_fmp_market_cap(
        ticker: str,
        api_key: str,
        fetch_market_data_func: Callable,
        process_daily_to_monthly_func: Callable,
        fetch_db_market_func: Callable,
        db_info: Dict[str, Any],
        start_year: int = 2010,
        log_func: Optional[Callable] = None,
        error_list: Optional[List[Dict]] = None
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Fetch and process market cap data from FMP API and merge with database data.

    Args:
        ticker: Stock ticker symbol
        api_key: FMP API key
        fetch_market_data_func: Function to fetch market data from FMP (returns tuple: data, error)
        process_daily_to_monthly_func: Function to process daily data to monthly
        fetch_db_market_func: Function to fetch market data from database
        db_info: Database connection information
        start_year: Start year for fetching data (default: 2010)
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

    if error_list is None:
        error_list = []

    # ========================================
    # 1. FMP에서 시가총액 데이터 가져오기
    # ========================================
    try:
        market_data, _ = fetch_market_data_func(ticker, api_key, start_year=start_year)
        if not market_data:
            msg = "FMP market data fetch failed"
            log("ERR-FMP-MCAP", f"{ticker} {msg}")
            error_list.append({
                'ticker': ticker,
                'stage': 'fetch_market',
                'error': msg
            })
            return None, msg

        # 일별 데이터를 월별로 처리
        fmp_market_df = process_daily_to_monthly_func(market_data, ticker).copy()
        fmp_market_df['date_month_end'] = to_month_end_safe(fmp_market_df['date'])
        fmp_market_df = fmp_market_df.drop_duplicates(
            subset=['date_month_end']
        ).sort_values('date_month_end').reset_index(drop=True)

        log("OK-FMP-MCAP", f"{ticker} rows={len(fmp_market_df)}")

    except Exception as e:
        msg = f"FMP market data preprocessing failed: {str(e)}"
        log("EXC-FMP-MCAP", f"{ticker} e={e}")
        error_list.append({
            'ticker': ticker,
            'stage': 'fmp_market_preproc',
            'error': str(e)
        })
        return None, msg

    # ========================================
    # 2. DB 시가총액 데이터와 병합
    # ========================================
    try:
        db_market_df = fetch_db_market_func(ticker, db_info)

        # DB 데이터가 비어있거나 None인 경우 처리
        if db_market_df is None or db_market_df.empty:
            db_market_df = pd.DataFrame()

        # date_month_end 컬럼 확인 및 생성
        if not db_market_df.empty and 'date_month_end' not in db_market_df.columns:
            if 'date' in db_market_df.columns:
                db_market_df['date_month_end'] = to_month_end_safe(db_market_df['date'])
            else:
                db_market_df = pd.DataFrame()

        # DB 데이터가 없는 경우
        if db_market_df.empty:
            merged_market_df = fmp_market_df.copy()
            merged_market_df['market_cap_billions_from_db'] = np.nan
        else:
            # market_cap_billions 컬럼 처리
            if 'market_cap_billions' in db_market_df.columns:
                db_market_df_renamed = db_market_df.rename(
                    columns={'market_cap_billions': 'market_cap_billions_from_db'}
                )
            else:
                db_market_df_renamed = db_market_df[['date_month_end']].copy()
                db_market_df_renamed['market_cap_billions_from_db'] = np.nan

            # FMP 데이터와 DB 데이터 병합
            merged_market_df = fmp_market_df.merge(
                db_market_df_renamed[['date_month_end', 'market_cap_billions_from_db']],
                on='date_month_end',
                how='left'
            )

        # market_cap_billions 컬럼 확인 및 생성
        if 'market_cap_billions' not in merged_market_df.columns:
            merged_market_df['market_cap_billions'] = np.nan
        if 'market_cap_billions_from_db' not in merged_market_df.columns:
            merged_market_df['market_cap_billions_from_db'] = np.nan

        # FMP 데이터 우선, 없으면 DB 데이터 사용
        merged_market_df['market_cap_billions'] = merged_market_df['market_cap_billions'].fillna(
            merged_market_df['market_cap_billions_from_db']
        )

        # 중복 제거 및 정렬
        merged_market_df = merged_market_df.drop_duplicates(
            subset=['date_month_end']
        ).sort_values('date_month_end').reset_index(drop=True)

        nan_count = merged_market_df['market_cap_billions'].isna().sum()
        log("OK-MCAP-MERGE", f"{ticker} rows={len(merged_market_df)} nan_mcap={nan_count}")

        return merged_market_df, None

    except Exception as e:
        msg = f"Market cap merge failed: {str(e)}"
        log("EXC-MCAP-MERGE", f"{ticker} e={e}")
        error_list.append({
            'ticker': ticker,
            'stage': 'market_merge',
            'error': str(e)
        })
        return None, msg


def safe_get_db_market_df(
        ticker: str,
        db_info: Dict[str, Any],
        fetch_db_market_func: Callable
) -> pd.DataFrame:
    """
    Safely fetch market data from database with error handling.

    Args:
        ticker: Stock ticker symbol
        db_info: Database connection information
        fetch_db_market_func: Function to fetch market data from database

    Returns:
        DataFrame with market data or empty DataFrame on error
    """
    try:
        db_market_df = fetch_db_market_func(ticker, db_info)
        if db_market_df is None or db_market_df.empty:
            return pd.DataFrame()
        return db_market_df
    except Exception:
        return pd.DataFrame()


# Example usage
def example_usage():
    """
    Example of how to use get_fmp_market_cap function.
    """
    print("=" * 70)
    print("Market Cap Processor Module - Example Usage")
    print("=" * 70)
    print("\nThis is a mock example. To use this module in your project:")
    print("1. Import: from market_cap_processor import get_fmp_market_cap")
    print("2. Pass your actual fetch and process functions")
    print("3. See below for integration example")
    print("=" * 70)

    # Mock functions
    def mock_fetch_market_data(ticker, api_key, start_year=2010):
        """Mock FMP API call"""
        print(f"\n[MOCK] Fetching market data for {ticker} from year {start_year}...")
        mock_data = [
            {'date': '2023-12-29', 'marketCap': 3000000000000},
            {'date': '2023-11-30', 'marketCap': 2950000000000},
            {'date': '2023-10-31', 'marketCap': 2900000000000},
        ]
        return mock_data, None

    def mock_process_daily_to_monthly(market_data, ticker):
        """Mock processing function"""
        print(f"[MOCK] Processing daily to monthly data for {ticker}...")
        df = pd.DataFrame(market_data)
        df['date'] = pd.to_datetime(df['date'])
        df['market_cap_billions'] = df['marketCap'] / 1_000_000_000
        return df[['date', 'market_cap_billions']]

    def mock_fetch_db_market(ticker, db_info):
        """Mock database fetch"""
        print(f"[MOCK] Fetching market data for {ticker} from database...")
        return pd.DataFrame()

    def mock_log(tag, msg):
        """Mock logging function"""
        print(f"[{tag}] {msg}")

    # Test execution
    ticker = "AAPL"
    api_key = "mock_api_key"
    db_info = {'host': 'localhost', 'database': 'mock_db'}
    error_list = []

    print("\n" + "=" * 70)
    print("Running mock example...")
    print("=" * 70)

    market_df, error = get_fmp_market_cap(
        ticker=ticker,
        api_key=api_key,
        fetch_market_data_func=mock_fetch_market_data,
        process_daily_to_monthly_func=mock_process_daily_to_monthly,
        fetch_db_market_func=mock_fetch_db_market,
        db_info=db_info,
        start_year=2010,
        log_func=mock_log,
        error_list=error_list
    )

    print("\n" + "=" * 70)
    print("Results:")
    print("=" * 70)

    if market_df is not None:
        print(f"\n[SUCCESS] Successfully processed {len(market_df)} market cap records")
        print(f"\nDataFrame shape: {market_df.shape}")
        print(f"Columns: {list(market_df.columns)}")
        print("\nSample data:")
        print(market_df.to_string())
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
    print("\n[*] Running market_cap_processor.py directly...\n")
    example_usage()
    print("\n[NOTE] To use this module in your project, import it:")
    print("   from market_cap_processor import get_fmp_market_cap")