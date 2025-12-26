# -*- coding: utf-8 -*-
"""
Valuation Forecast 계산 (날짜 표준화 적용)
- PSR(월말) + Revenue TTM(분기말) 병합 시 날짜 정렬 강화
"""
import pandas as pd
from typing import Optional
from date_standardization import standardize_dataframe_dates


def _default_value_start_date() -> str:
    """다음 달 'YYYY-MM' 형식 반환"""
    today = pd.Timestamp.today()
    next_month = (today.to_period('M') + 1).strftime('%Y-%m')
    return next_month


def _ensure_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """date 컬럼 확보"""
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
    """value_start_date 이후 데이터만 필터링"""
    if 'date' not in df.columns:
        raise KeyError("'date' column is required after merging.")
    df = df.copy()
    date_period = pd.to_datetime(df['date']).dt.to_period('M')
    start_period = pd.Period(value_start_date, freq='M')
    return df.loc[date_period >= start_period]


def _compute_mc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """mc_* 컬럼 계산 (psr * revenue_ttm)"""
    df = df.copy()

    pairs = [
        ('psr_SARIMA_noexog', 'revenue_sarima_ttm', 'mc_sarima_noexog'),
        ('psr_SARIMA_exog', 'revenue_sarima_exog_ttm', 'mc_sarima_exog'),
        ('psr_ETS', 'revenue_ets_ttm', 'mc_ets'),
        ('psr_Prophet', 'revenue_prophet_ttm', 'mc_prophet'),
        ('psr_LSTM', 'revenue_lstm_ttm', 'mc_lstm'),
        ('psr_Theta', 'revenue_theta_ttm', 'mc_theta'),
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
    Valuation Forecast 계산 (날짜 정렬 강화)

    Parameters:
    -----------
    fc_table : pd.DataFrame
        PSR 예측값 (월말 날짜)
    rev_final : pd.DataFrame
        Revenue + TTM (분기말 날짜)
    value_start_date : str, optional
        평가 시작 날짜

    Returns:
    --------
    pd.DataFrame : Market Cap 예측 결과
    """
    if value_start_date is None:
        value_start_date = _default_value_start_date()

    # date 컬럼 확보
    fc_table_reset = _ensure_date_column(fc_table)
    rev_final_reset = _ensure_date_column(rev_final)

    # 날짜 표준화 (PSR: 월말, Revenue: 분기말 유지)
    fc_table_reset['date'] = pd.to_datetime(fc_table_reset['date'])
    rev_final_reset['date'] = pd.to_datetime(rev_final_reset['date'])

    # Revenue TTM 컬럼만 추출
    rev_cols = [c for c in rev_final_reset.columns if c.startswith('revenue_')]
    rev_data = rev_final_reset[['date'] + rev_cols]

    # Outer join으로 병합
    merged_df = pd.merge(fc_table_reset, rev_data, on='date', how='outer')

    # 날짜순 정렬
    merged_df = merged_df.sort_values('date').reset_index(drop=True)

    # Forward fill (최대 2행)
    # PSR은 월말, Revenue TTM은 분기말이므로 중간 월에 ffill 필요
    merged_df = merged_df.ffill(limit=2)

    # 필터링
    clean_df = _filter_from_value_start_date(merged_df, value_start_date)

    # Market Cap 계산
    clean_df = _compute_mc_columns(clean_df)

    # 결과 추출
    mc_cols = [c for c in clean_df.columns if c.startswith('mc_')]
    valuation_forecast_result = clean_df[['date'] + mc_cols].copy()

    # 최종 날짜 정렬
    valuation_forecast_result = valuation_forecast_result.sort_values('date').reset_index(drop=True)

    return valuation_forecast_result


def run(fc_table: pd.DataFrame, rev_final: pd.DataFrame, value_start_date: Optional[str] = None) -> pd.DataFrame:
    """Convenience alias."""
    return compute_valuation_forecast(fc_table, rev_final, value_start_date)