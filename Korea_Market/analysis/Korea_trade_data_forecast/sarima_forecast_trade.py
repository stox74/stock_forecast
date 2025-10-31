
# -*- coding: utf-8 -*-
"""
sarima_forecast_trade.py

- 입력: (date, root_hs_code, indicator, value) 형태의 DataFrame
- 처리: indicator로 필터 → value 시계열을 SARIMA로 n-step 예측
- 출력: index=date, column='sarima_{indicator}' 형태의 DataFrame

주의: universal_ts_forecast_function.py 가 같은 환경에서 import 가능해야 합니다.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from DATA.universal_ts_forecast_function import forecast_sarima, infer_freq_alias, seasonal_periods_from_freq


# 기존 모듈에서 유틸/모델 함수 임포트
from DATA.universal_ts_forecast_function import (
    forecast_sarima,
    infer_freq_alias,
    seasonal_periods_from_freq,
)


def _infer_future_index(last_index: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """
    마지막 시점 이후의 미래 인덱스를 horizon만큼 생성
    """
    if not isinstance(last_index, pd.DatetimeIndex) or last_index.empty:
        raise ValueError("유효한 DatetimeIndex가 필요합니다.")
    freq = pd.infer_freq(last_index) or 'M'  # 기본 월말
    # 마지막 시점 이후부터 horizon개 생성
    future = pd.date_range(start=last_index[-1], periods=horizon + 1, freq=freq)[1:]
    return future


def sarima_forecast_trade_value(
    df: pd.DataFrame,
    indicator: str,
    horizon: int,
    sarima_kwargs: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """
    특정 indicator의 value를 SARIMA로 n-step 예측하여 반환.

    Parameters
    ----------
    df : pd.DataFrame
        columns: ['date', 'root_hs_code', 'indicator', 'value'] (대소문자 동일 권장)
    indicator : str
        예: 'expDlr', 'impDlr'
    horizon : int
        예측 스텝 수 (n-step)
    sarima_kwargs : dict, optional
        universal_ts_forecast_function.forecast_sarima 에 전달할 추가 인자
        (예: {'seasonal_period': 12, 'try_transforms': True, 'p_values': (0,1,2)} 등)

    Returns
    -------
    pd.DataFrame
        index: 미래 날짜
        columns: ['sarima_{indicator}']
    """
    if sarima_kwargs is None:
        sarima_kwargs = {}

    # 1) 입력 검증 및 필터
    required = {'date', 'indicator', 'value'}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"입력 df에 필요한 컬럼이 없습니다: {missing}")

    sub = df.loc[df['indicator'] == indicator, ['date', 'value']].copy()
    if sub.empty:
        raise ValueError(f"indicator='{indicator}' 데이터가 없습니다.")

    # 2) 시계열 정리
    sub['date'] = pd.to_datetime(sub['date'], errors='coerce')
    sub = sub.dropna(subset=['date']).sort_values('date')
    y = pd.Series(sub['value'].astype(float).values, index=sub['date'])
    y.index.name = 'date'

    # 3) 계절주기(m) 자동 추론 (월·분기·일 등)
    freq_alias = infer_freq_alias(y.index)
    m_auto = seasonal_periods_from_freq(freq_alias)

    # 사용자가 seasonal_period를 명시하지 않았다면 자동값 사용
    if 'seasonal_period' not in sarima_kwargs:
        sarima_kwargs['seasonal_period'] = m_auto

    # 4) SARIMA 예측 (외생변수 사용 안 함)
    out = forecast_sarima(
        y=y,
        forecast_horizon=horizon,
        exog=None,
        **sarima_kwargs
    )

    if 'error' in out:
        raise RuntimeError(out['error'])

    fc_vals = np.asarray(out['forecast']).reshape(-1)
    if len(fc_vals) != horizon:
        raise RuntimeError("예측 결과 길이가 horizon과 일치하지 않습니다.")

    # 5) 미래 날짜 인덱스 구성
    future_index = _infer_future_index(y.index, horizon)
    result = pd.DataFrame(
        data=fc_vals,
        index=future_index,
        columns=[f"sarima_{indicator}"]
    )
    result.index.name = 'date'
    return result


# 단독 실행 테스트 (선택)
# if __name__ == "__main__":
#     # 가짜 예제
#     dates = pd.date_range('2015-01-31', periods=120, freq='M')
#     vals = np.linspace(1e6, 2e6, 120) + np.random.normal(0, 5e4, 120)
#     tmp = pd.DataFrame({
#         'date': dates,
#         'root_hs_code': '854232',
#         'indicator': 'expDlr',
#         'value': vals
#     })
#     fc = sarima_forecast_trade_value(tmp, indicator='expDlr', horizon=6)
#     print(fc)
