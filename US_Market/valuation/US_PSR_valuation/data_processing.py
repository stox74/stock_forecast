# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np

def clean_rev_data(rev_data: pd.DataFrame) -> pd.DataFrame:
    """매출 데이터 정제"""
    required = ['revenue', 'calendar_year', 'period']
    missing = [c for c in required if c not in rev_data.columns]
    if missing:
        raise ValueError(f"필수 컬럼이 없습니다: {missing}")

    d = rev_data.copy()
    d = d[~d['revenue'].isna()].copy()
    d = d.drop_duplicates(subset=['calendar_year', 'period'], keep='first').reset_index(drop=True)
    return d

def calculate_enhanced_ttm_and_psr(merged_data):
    """TTM 및 PSR 계산"""
    df = merged_data.copy()

    df['date_month_end'] = pd.to_datetime(df['date_month_end'], errors='coerce')
    df = df.sort_values(['date_month_end']).reset_index(drop=True)
    df = df.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    df['revenue_ttm'] = df.groupby('ticker')['revenue_billions'].rolling(window=4, min_periods=1).sum().reset_index(0, drop=True)
    df['revenue_ttm_billions'] = df['revenue_ttm']
    df['revenue_ttm_shift'] = df.groupby('ticker')['revenue_ttm_billions'].shift(2)
    df['PSR_ttm'] = df['market_cap_billions'] / df['revenue_ttm_shift']
    df['PSR_ttm'] = df['PSR_ttm'].replace([np.inf, -np.inf], np.nan)

    return df

def prepare_revenue_ttm(
        df: pd.DataFrame,
        revenue_key: str = "revenue_billions",
        min_periods: int = 1,
) -> pd.DataFrame:
    """매출 TTM 준비 함수"""
    d = df.copy()

    if 'date_month_end' not in d.columns:
        d = d.reset_index().rename(columns={'index': 'date_month_end'})
    d['date_month_end'] = pd.to_datetime(d['date_month_end'])

    if 'ticker' not in d.columns:
        raise ValueError("ticker 칼럼이 필요합니다.")

    rev_cols = [c for c in d.columns if revenue_key in c]
    if not rev_cols:
        raise ValueError(f"'{revenue_key}' 가 포함된 칼럼을 찾지 못했습니다.")

    uniq_tickers = d['ticker'].dropna().unique()
    if len(uniq_tickers) == 1:
        d['ticker'] = d['ticker'].ffill().bfill()
    else:
        d = d[~d['ticker'].isna()].copy()

    d = d.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    row_mean = d[rev_cols].mean(axis=1, skipna=True)
    for c in rev_cols:
        d[c] = d[c].fillna(row_mean)

    for c in rev_cols:
        ttm_col = f"{c}_ttm"
        d[ttm_col] = (
            d.groupby('ticker', group_keys=False)[c]
            .rolling(window=4, min_periods=min_periods)
            .sum()
            .reset_index(level=0, drop=True)
        )

    d = d.set_index('date_month_end')
    return d
