# -*- coding: utf-8 -*-

"""
Market Cap and Fundamental Data Merger Module

This module handles merging market cap data with revenue data and preparing
the final dataframe for valuation analysis.
"""

import pandas as pd
from typing import Optional, Callable


def get_marketcap_fundamental_dataframe(
        merged_market_df: pd.DataFrame,
        rev_data: pd.DataFrame,
        start_date: str,
        end_date: str,
        log_func: Optional[Callable] = None
) -> Optional[pd.DataFrame]:
    """
    Merge market cap and revenue data, apply forward fill, and filter by date range.

    Args:
        merged_market_df: DataFrame with market cap data (must have 'date_month_end', 'market_cap_billions')
        rev_data: DataFrame with revenue data (must have 'date_month_end', 'revenue_billions', 'ticker')
        start_date: Start date for filtering (e.g., '2015-01-01')
        end_date: End date for filtering (e.g., '2024-12-31')
        log_func: Optional logging function

    Returns:
        DataFrame with merged and processed data, or None if error occurs

    Output columns:
        - date_month_end
        - market_cap_billions
        - ticker
        - revenue_billions
    """

    def log(tag: str, msg: str):
        """Internal logging helper"""
        if log_func:
            log_func(tag, msg)

    try:
        # Convert date strings to month-end timestamps
        start_date_month = pd.to_datetime(start_date).to_period('M').to_timestamp('M')
        end_date_month = pd.to_datetime(end_date).to_period('M').to_timestamp('M')

        # Merge market cap and revenue data
        enhanced_merged_df = pd.merge(
            merged_market_df[['date_month_end', 'market_cap_billions']],
            rev_data,
            on='date_month_end',
            how='outer'
        )

        # Select required columns
        market_cap_resize = enhanced_merged_df[
            ['date_month_end', 'market_cap_billions', 'ticker', 'revenue_billions']
        ].copy()

        # Forward fill missing values (limit=2 months)
        market_cap_resize.ffill(limit=2, inplace=True)

        # Filter by date range
        market_cap_resize = market_cap_resize[
            (market_cap_resize['date_month_end'] >= start_date_month) &
            (market_cap_resize['date_month_end'] <= end_date_month)
            ]

        # Remove rows with any missing values
        market_cap_resize = market_cap_resize.dropna(axis=0)

        # Sort by date
        market_cap_resize = market_cap_resize.sort_values('date_month_end').reset_index(drop=True)

        log("OK-MERGE", f"Final rows={len(market_cap_resize)} range={start_date} to {end_date}")

        return market_cap_resize

    except Exception as e:
        log("EXC-MERGE", f"Failed to merge data: {e}")
        return None


# Example usage
if __name__ == "__main__":
    print("\n[*] Running marketcap_fundamental_merger.py directly...\n")

    # Mock data for testing
    mock_market_df = pd.DataFrame({
        'date_month_end': pd.to_datetime(['2023-09-30', '2023-10-31', '2023-11-30', '2023-12-31']),
        'market_cap_billions': [2800, 2900, 3000, 3100]
    })

    mock_revenue_df = pd.DataFrame({
        'date_month_end': pd.to_datetime(['2023-09-30', '2023-12-31']),
        'ticker': ['AAPL', 'AAPL'],
        'revenue_billions': [89.5, 119.6]
    })


    def mock_log(tag, msg):
        print(f"[{tag}] {msg}")


    print("=" * 70)
    print("Testing Market Cap & Fundamental Data Merger")
    print("=" * 70)

    print("\nInput Market Cap Data:")
    print("-" * 70)
    print(mock_market_df.to_string(index=False))

    print("\nInput Revenue Data:")
    print("-" * 70)
    print(mock_revenue_df.to_string(index=False))

    result_df = get_marketcap_fundamental_dataframe(
        merged_market_df=mock_market_df,
        rev_data=mock_revenue_df,
        start_date='2023-01-01',
        end_date='2024-12-31',
        log_func=mock_log
    )

    print("\nResult After Merge & Processing:")
    print("-" * 70)
    if result_df is not None:
        print(result_df.to_string(index=False))
        print(f"\n[SUCCESS] Processed {len(result_df)} records")
    else:
        print("[ERROR] Failed to process data")