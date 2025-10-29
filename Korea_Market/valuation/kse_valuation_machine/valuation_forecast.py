import pandas as pd
from typing import Optional

def _default_value_start_date() -> str:
    """Return next month in 'YYYY-MM' format, based on current local time."""
    today = pd.Timestamp.today()
    next_month = (today.to_period('M') + 1).strftime('%Y-%m')
    return next_month

def _ensure_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure a 'date' column exists by resetting index if needed and normalizing common names."""
    if 'date' in df.columns:
        out = df.copy()
    else:
        out = df.reset_index()
        if 'date' not in out.columns:
            for cand in ['index', 'Date', 'ds', out.columns[0]]:
                if cand in out.columns:
                    out = out.rename(columns={cand: 'date'})
                    break
    return out

def _filter_from_value_start_date(df: pd.DataFrame, value_start_date: str) -> pd.DataFrame:
    if 'date' not in df.columns:
        raise KeyError("'date' column is required after merging.")
    df = df.copy()
    date_period = pd.to_datetime(df['date']).dt.to_period('M')
    start_period = pd.Period(value_start_date, freq='M')
    return df.loc[date_period >= start_period]

def _compute_mc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute mc_* columns from available psr_* and revenue_*_ttm columns. NaN propagates by default."""
    df = df.copy()

    pairs = [
        ('psr_SARIMA_noexog',    'revenue_sarima_ttm',        'mc_sarima_noexog'),
        ('psr_SARIMA_exog',      'revenue_sarima_exog_ttm',   'mc_sarima_exog'),
        ('psr_ETS',              'revenue_ets_ttm',           'mc_ets'),
        ('psr_Prophet',          'revenue_prophet_ttm',       'mc_prophet'),
        ('psr_LSTM',             'revenue_lstm_ttm',          'mc_lstm'),
        ('psr_Theta',            'revenue_theta_ttm',         'mc_theta'),
    ]

    for psr_col, rev_col, mc_col in pairs:
        if psr_col in df.columns and rev_col in df.columns:
            df.loc[:, mc_col] = df[psr_col] * df[rev_col]
        else:
            df.loc[:, mc_col] = pd.Series(pd.NA, index=df.index, dtype='float64')

    return df

def compute_valuation_forecast(
    fc_table: pd.DataFrame,
    rev_final: pd.DataFrame,
    value_start_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Build valuation_forecast_result from PSR forecasts (fc_table) and
    revenue TTM forecasts (rev_final).
    """
    if value_start_date is None:
        value_start_date = _default_value_start_date()

    fc_table_reset = _ensure_date_column(fc_table)
    rev_final_reset = _ensure_date_column(rev_final)

    ttm_cols = [c for c in rev_final_reset.columns if 'ttm' in c]
    rev_ttm = rev_final_reset[['date'] + ttm_cols]

    merged_df = pd.merge(fc_table_reset, rev_ttm, on='date', how='outer')
    merged_df = merged_df.sort_values('date').reset_index(drop=True)
    merged_df = merged_df.ffill(limit=2)

    clean_df = _filter_from_value_start_date(merged_df, value_start_date)
    clean_df = _compute_mc_columns(clean_df)

    mc_cols = [c for c in clean_df.columns if c.startswith('mc_')]
    valuation_forecast_result = clean_df[['date'] + mc_cols].copy()

    return valuation_forecast_result

def run(fc_table: pd.DataFrame, rev_final: pd.DataFrame, value_start_date: Optional[str] = None) -> pd.DataFrame:
    """Convenience alias."""
    return compute_valuation_forecast(fc_table, rev_final, value_start_date)
