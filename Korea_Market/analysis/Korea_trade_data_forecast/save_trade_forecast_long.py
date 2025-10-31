# -*- coding: utf-8 -*-
"""
build_long_trade_forecast.py

예측 결과 out DataFrame을 long-format으로 변환합니다.
요구 형식:
1) index는 date
2) hs_code 컬럼은 입력 인자 hs_code
3) indicator 컬럼은 out의 각 컬럼명
4) value 컬럼은 예측값
5) forecast_date 컬럼은 예측 실행일(기본: 오늘) 또는 인자로 지정한 날짜
"""

from __future__ import annotations
from typing import Optional, Iterable
from datetime import date as dt_date
import pandas as pd


def to_long_format(
    out_df: pd.DataFrame,
    hs_code: str,
    forecast_date: Optional[pd.Timestamp | dt_date] = None,
    use_columns: Optional[Iterable[str]] = None,
    dropna_value: bool = False,
) -> pd.DataFrame:
    """
    out_df를 long 포맷으로 변환.

    Parameters
    ----------
    out_df : pd.DataFrame
        index=DatetimeIndex(date), columns=모델별 예측치 (예: sarima_expDlr, ets_expDlr, 앙상블_expDlr 등)
    hs_code : str
        long 포맷의 hs_code 값으로 채움
    forecast_date : pd.Timestamp | datetime.date, optional
        저장용 예측일(기본: 오늘)
    use_columns : Iterable[str], optional
        out_df에서 특정 컬럼만 선택해 변환하고 싶을 때 지정
    dropna_value : bool, default False
        value가 NaN인 행을 제거할지 여부

    Returns
    -------
    pd.DataFrame
        index: date
        columns: ['hs_code', 'indicator', 'value', 'forecast_date']
    """
    # --- 인덱스 검증/정리 ---
    if not isinstance(out_df.index, pd.DatetimeIndex):
        if "date" in out_df.columns:
            df = out_df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.set_index("date")
        else:
            raise ValueError("out_df.index는 DatetimeIndex 여야 하며, 없으면 'date' 컬럼이 필요합니다.")
    else:
        df = out_df.copy()
        df.index = pd.to_datetime(df.index)

    df = df.sort_index()

    # --- 사용할 컬럼 선택 (옵션) ---
    if use_columns is not None:
        missing = set(use_columns) - set(df.columns)
        if missing:
            raise KeyError(f"use_columns에 존재하지 않는 컬럼: {missing}")
        df = df[list(use_columns)]

    # --- long 변환 ---
    long_df = (
        df.reset_index()
          .melt(id_vars=["date"], var_name="indicator", value_name="value")
          .sort_values(["date", "indicator"], kind="mergesort")
          .reset_index(drop=True)
    )

    # --- 부가 컬럼 채우기 ---
    long_df["hs_code"] = str(hs_code)
    if forecast_date is None:
        forecast_date = pd.Timestamp(dt_date.today())
    long_df["forecast_date"] = pd.to_datetime(forecast_date).date()

    # --- NaN 처리 (옵션) ---
    if dropna_value:
        long_df = long_df.dropna(subset=["value"])

    # --- 요구 형식 & 인덱스 정리 ---
    long_df = long_df[["date", "hs_code", "indicator", "value", "forecast_date"]]
    long_df = long_df.set_index("date")

    return long_df