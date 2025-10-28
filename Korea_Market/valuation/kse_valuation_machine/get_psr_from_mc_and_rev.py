# -*- coding: utf-8 -*-

import pandas as pd
from typing import Optional


def _ensure_datetime_index(df: pd.DataFrame,
                           date_col_candidates: Optional[list] = None) -> pd.DataFrame:
    """
    Ensure DatetimeIndex for a DataFrame. If an explicit date column exists,
    set it as index; otherwise convert current index to datetime.
    """
    x = df.copy()
    date_cols = date_col_candidates or ["date", "Date"]
    for c in date_cols:
        if c in x.columns:
            x[c] = pd.to_datetime(x[c])
            x = x.set_index(c)
            break
    if not isinstance(x.index, pd.DatetimeIndex):
        x.index = pd.to_datetime(x.index)
    return x.sort_index()


def build_psr_series(
    df_mc: pd.DataFrame,
    df_rev: pd.DataFrame,
    roll_window: int = 3,
    ffill_limit: int = 3,
    shift_months: int = 2,
    mc_divisor: float = 1000.0,
    mc_value_col: str = "value",
    rev_value_col: str = "revenue",
) -> pd.DataFrame:
    """
    Compute PSR time series:
      1) Month-end resample of market cap (df_mc).
      2) Rolling-sum (roll_window) of revenue (df_rev) -> rev_ttm.
      3) Join month-end MC with rev_ttm; ffill(limit=ffill_limit).
      4) Scale market_cap by mc_divisor.
      5) Shift rev_ttm forward by `shift_months` to create alignment.
      6) PSR = market_cap / rev_ttm_shifted.
      7) Return DataFrame with one column 'psr' and no NaNs.
    Assumptions:
      - df_mc has a numeric column `mc_value_col` (default 'value').
      - df_rev has a numeric column `rev_value_col` (default 'revenue').
      - Both frames have a date column named 'date'/'Date' or a datetime index.
    """
    # 1) Month-end market cap
    mc = _ensure_datetime_index(df_mc)
    if mc_value_col not in mc.columns:
        raise KeyError(f"df_mc must have column '{mc_value_col}'.")
    mc_me = mc.resample("M").last()

    # 2) Revenue rolling sum (TTM-like with `roll_window`)
    rev = _ensure_datetime_index(df_rev)
    if rev_value_col not in rev.columns:
        raise KeyError(f"df_rev must have column '{rev_value_col}'.")
    rev_ttm = rev[[rev_value_col]].rolling(window=roll_window, min_periods=roll_window).sum()
    rev_ttm.columns = ["rev_ttm"]

    # 3) Join on month-end index and forward-fill gaps
    combined = mc_me.join(rev_ttm, how="left")
    combined = combined.ffill(limit=ffill_limit)

    # 4) Rename and scale market cap
    combined = combined.rename(columns={mc_value_col: "market_cap"})
    combined["market_cap"] = combined["market_cap"] / mc_divisor

    # 5) Shift TTM forward by `shift_months`
    combined["rev_ttm_shifted"] = combined["rev_ttm"].shift(shift_months)

    # 6) PSR
    combined["psr"] = combined["market_cap"] / combined["rev_ttm_shifted"]

    # 7) Output: only 'psr', no NaNs
    out = combined[["psr"]].dropna().copy()
    out.index.name = "date"
    return out

# if __name__ == "__main__":
#     print("Import build_psr_series(df_mc, df_rev, ...) from this module.")