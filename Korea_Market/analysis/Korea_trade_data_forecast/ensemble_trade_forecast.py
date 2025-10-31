# -*- coding: utf-8 -*-
"""
ensemble_trade_forecast.py

- 입력: fc_table (index=date, columns like 'sarima_{indicator}', 'ets_{indicator}', ...)
- 처리:
  (1) 사용 가능한 모델 컬럼들의 행별 평균 -> 'avg5_{indicator}'
  (2) 각 행에서 최고/최저를 제외한 나머지의 평균 -> '앙상블_{indicator}'
      - 5개 모델이면 '중간 3개'의 평균
      - 4개면 중간 2개 평균, 3개면 3개 그대로 평균
      - 유효값이 2개 미만이면 NaN
- 출력: 원본 + 위 두 컬럼이 추가된 DataFrame
"""

from __future__ import annotations
from typing import List
import numpy as np
import pandas as pd


MODEL_PREFIXES = ("sarima", "ets", "prophet", "lstm", "theta")


def _detect_model_cols(df: pd.DataFrame, indicator: str) -> List[str]:
    """
    fc_table 안에서 특정 indicator에 해당하는 모델 컬럼명을 찾아 반환.
    예: indicator='expDlr' -> ['sarima_expDlr','ets_expDlr',...]
    """
    target_cols = []
    suffix = f"_{indicator}"
    for pref in MODEL_PREFIXES:
        col = f"{pref}{suffix}"
        if col in df.columns:
            target_cols.append(col)
    if not target_cols:
        raise KeyError(f"'{indicator}'에 해당하는 모델 컬럼을 찾지 못했습니다. "
                       f"(예상: {', '.join([p+suffix for p in MODEL_PREFIXES])})")
    return target_cols


def _trimmed_row_mean(values: np.ndarray) -> float:
    """
    한 행(1D array)의 값들에서 최고/최저를 제외한 평균을 계산.
    - 유효값(비-NaN) 개수가 5개 이상: sum - max - min / (n-2)
    - 4개: 중간 2개 평균
    - 3개: 3개 평균 (제외할 값이 부족)
    - 그 외(n<3): NaN
    """
    vals = values[~np.isnan(values)]
    n = vals.size
    if n < 3:
        return np.nan
    if n == 3:
        return float(vals.mean())
    # n >= 4
    return float((vals.sum() - vals.max() - vals.min()) / (n - 2))


def build_ensemble_columns(fc_table: pd.DataFrame, indicator: str) -> pd.DataFrame:
    """
    fc_table에 평균 컬럼과 '앙상블_{indicator}' 컬럼을 추가하여 반환.

    Parameters
    ----------
    fc_table : pd.DataFrame
        index: DatetimeIndex (date)
        columns: subset of { 'sarima_{indicator}', 'ets_{indicator}', 'prophet_{indicator}',
                             'lstm_{indicator}', 'theta_{indicator}' }
    indicator : str
        예: 'expDlr', 'impDlr'

    Returns
    -------
    pd.DataFrame
        원본 + ['avg5_{indicator}', '앙상블_{indicator}']
    """
    df = fc_table.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        # date 컬럼이 있으면 인덱스로 올림
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.set_index("date")
        else:
            raise ValueError("fc_table의 인덱스가 DatetimeIndex가 아니고 'date' 컬럼도 없습니다.")

    model_cols = _detect_model_cols(df, indicator)

    # (1) 5개(또는 사용 가능한) 모델의 행별 평균
    df[f"avg5_{indicator}"] = df[model_cols].mean(axis=1, skipna=True)

    # (2) 최고/최저 제외 평균 (보통 5개 중 중간 3개)
    vals = df[model_cols].to_numpy(dtype=float)
    trimmed_means = np.apply_along_axis(_trimmed_row_mean, 1, vals)
    df[f"ensemble_{indicator}"] = trimmed_means

    return df
