# -*- coding: utf-8 -*-
"""
날짜 표준화 유틸리티
- 분기말/월말 날짜를 철저하게 통일
"""
import pandas as pd
from typing import List


def get_standard_quarter_dates(start_date: str, num_quarters: int, forecast_start_date: str = None) -> pd.DatetimeIndex:
    """
    표준 분기말 날짜 생성 (3/31, 6/30, 9/30, 12/31)

    Parameters:
    -----------
    start_date : str
        마지막 실제 데이터 날짜 (YYYY-MM-DD 형식)
    num_quarters : int
        생성할 분기 수
    forecast_start_date : str, optional
        예측 시작 날짜를 수동 지정 (YYYY-MM-DD 형식)
        None이면 start_date 다음 분기부터 자동 생성

    Returns:
    --------
    pd.DatetimeIndex : 표준화된 분기말 날짜

    Example:
    --------
    >>> # 자동 생성 (다음 분기부터)
    >>> get_standard_quarter_dates('2025-09-30', 4)
    DatetimeIndex(['2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'])

    >>> # 수동 지정
    >>> get_standard_quarter_dates('2025-09-30', 4, forecast_start_date='2025-12-31')
    DatetimeIndex(['2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'])
    """
    if forecast_start_date is not None:
        # 수동 지정된 시작일 사용
        start_fc = pd.Timestamp(forecast_start_date)
        start_quarter_end = (start_fc.to_period('Q')).to_timestamp('Q')
    else:
        # 자동 생성: 마지막 데이터의 다음 분기
        start = pd.Timestamp(start_date)
        start_quarter_end = (start.to_period('Q')).to_timestamp('Q')
        start_quarter_end = start_quarter_end + pd.offsets.QuarterEnd(1)

    # 분기말 날짜 생성
    quarter_dates = pd.date_range(
        start=start_quarter_end,
        periods=num_quarters,
        freq='QE'
    )

    return quarter_dates


def get_standard_month_dates(start_date: str, num_months: int, forecast_start_date: str = None) -> pd.DatetimeIndex:
    """
    표준 월말 날짜 생성

    Parameters:
    -----------
    start_date : str
        마지막 실제 데이터 날짜 (YYYY-MM-DD 형식)
    num_months : int
        생성할 월 수
    forecast_start_date : str, optional
        예측 시작 날짜를 수동 지정 (YYYY-MM-DD 형식)
        None이면 start_date 다음 월부터 자동 생성

    Returns:
    --------
    pd.DatetimeIndex : 표준화된 월말 날짜

    Example:
    --------
    >>> # 자동 생성 (다음 월부터)
    >>> get_standard_month_dates('2025-12-31', 6)
    DatetimeIndex(['2026-01-31', '2026-02-28', '2026-03-31', ...])

    >>> # 수동 지정
    >>> get_standard_month_dates('2025-12-31', 6, forecast_start_date='2026-01-31')
    DatetimeIndex(['2026-01-31', '2026-02-28', '2026-03-31', ...])
    """
    if forecast_start_date is not None:
        # 수동 지정된 시작일 사용
        start_fc = pd.Timestamp(forecast_start_date)
        start_month_end = (start_fc.to_period('M')).to_timestamp('M')
    else:
        # 자동 생성: 마지막 데이터의 다음 월
        start = pd.Timestamp(start_date)
        start_month_end = (start.to_period('M')).to_timestamp('M')
        start_month_end = start_month_end + pd.offsets.MonthEnd(1)

    # 월말 날짜 생성
    month_dates = pd.date_range(
        start=start_month_end,
        periods=num_months,
        freq='ME'
    )

    return month_dates


def normalize_to_quarter_end(date_series: pd.Series) -> pd.Series:
    """
    날짜 시리즈를 분기말로 정규화

    Parameters:
    -----------
    date_series : pd.Series
        날짜 시리즈

    Returns:
    --------
    pd.Series : 분기말로 정규화된 날짜
    """
    return pd.to_datetime(date_series).dt.to_period('Q').dt.to_timestamp('Q')


def normalize_to_month_end(date_series: pd.Series) -> pd.Series:
    """
    날짜 시리즈를 월말로 정규화

    Parameters:
    -----------
    date_series : pd.Series
        날짜 시리즈

    Returns:
    --------
    pd.Series : 월말로 정규화된 날짜
    """
    return pd.to_datetime(date_series).dt.to_period('M').dt.to_timestamp('M')


def standardize_dataframe_dates(df: pd.DataFrame, freq: str = 'Q') -> pd.DataFrame:
    """
    데이터프레임의 date 컬럼/인덱스를 표준 날짜로 변환

    Parameters:
    -----------
    df : pd.DataFrame
        날짜가 포함된 데이터프레임
    freq : str
        'Q' (분기말) 또는 'M' (월말)

    Returns:
    --------
    pd.DataFrame : 표준화된 날짜의 데이터프레임
    """
    df = df.copy()

    # date 컬럼이 있는 경우
    if 'date' in df.columns:
        if freq == 'Q':
            df['date'] = normalize_to_quarter_end(df['date'])
        elif freq == 'M':
            df['date'] = normalize_to_month_end(df['date'])
        else:
            raise ValueError("freq는 'Q' 또는 'M'이어야 합니다")

    # 인덱스가 DatetimeIndex인 경우
    elif isinstance(df.index, pd.DatetimeIndex):
        if freq == 'Q':
            df.index = df.index.to_period('Q').to_timestamp('Q')
        elif freq == 'M':
            df.index = df.index.to_period('M').to_timestamp('M')
        else:
            raise ValueError("freq는 'Q' 또는 'M'이어야 합니다")
        df.index.name = 'date'

    return df


def align_forecast_to_standard_dates(
        forecast_values: List[float],
        last_actual_date: str,
        freq: str = 'Q'
) -> pd.DataFrame:
    """
    예측값을 표준 날짜에 정렬

    Parameters:
    -----------
    forecast_values : List[float]
        예측값 리스트
    last_actual_date : str
        마지막 실제 데이터 날짜
    freq : str
        'Q' (분기말) 또는 'M' (월말)

    Returns:
    --------
    pd.DataFrame : 표준 날짜가 적용된 예측 데이터
    """
    num_periods = len(forecast_values)

    if freq == 'Q':
        dates = get_standard_quarter_dates(last_actual_date, num_periods)
    elif freq == 'M':
        dates = get_standard_month_dates(last_actual_date, num_periods)
    else:
        raise ValueError("freq는 'Q' 또는 'M'이어야 합니다")

    df = pd.DataFrame({
        'date': dates,
        'value': forecast_values
    })

    return df


if __name__ == "__main__":
    print("=" * 70)
    print("날짜 표준화 유틸리티 테스트")
    print("=" * 70)

    # 분기말 날짜 생성 테스트
    print("\n1. 분기말 날짜 생성 (마지막 실제 데이터: 2025-09-30, 예측 4분기)")
    q_dates = get_standard_quarter_dates('2025-09-30', 4)
    print(q_dates)
    print("예상: 2025-12-31, 2026-03-31, 2026-06-30, 2026-09-30")

    # 월말 날짜 생성 테스트
    print("\n2. 월말 날짜 생성 (마지막 실제 데이터: 2025-12-31, 예측 6개월)")
    m_dates = get_standard_month_dates('2025-12-31', 6)
    print(m_dates)
    print("예상: 2026-01-31, 2026-02-28, ..., 2026-06-30")

    # 예측값 정렬 테스트
    print("\n3. 예측값을 표준 분기말 날짜에 정렬")
    forecast = [100, 105, 110, 115]
    df = align_forecast_to_standard_dates(forecast, '2025-09-30', freq='Q')
    print(df)

    # 데이터프레임 날짜 표준화 테스트
    print("\n4. 데이터프레임 날짜 표준화")
    test_df = pd.DataFrame({
        'date': ['2025-12-15', '2026-03-20', '2026-06-25'],
        'value': [100, 105, 110]
    })
    print("원본:")
    print(test_df)
    print("\n표준화 후:")
    standardized = standardize_dataframe_dates(test_df, freq='Q')
    print(standardized)