# -*- coding: utf-8 -*-
"""
multi_model_trade_forecast.py

- 입력: (date, indicator, value) 컬럼을 가진 DataFrame (root_hs_code 등 다른 컬럼은 있어도 무방)
- 처리: indicator로 필터 → value 시계열을 SARIMA/ETS/Prophet/LSTM/Theta로 n-step 예측
- 출력: index = 미래 date, columns = ['sarima_{indicator}', 'ets_{indicator}', 'prophet_{indicator}', 'lstm_{indicator}', 'theta_{indicator}']
- 외생변수(exog)는 사용하지 않음

필수: 같은 환경에서 universal_ts_forecast_function.py 가 import 가능해야 함.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

from DATA.universal_ts_forecast_function import (
    infer_freq_alias,
    seasonal_periods_from_freq,
    forecast_sarima,   # 내부적으로 find_best_sarima_params 사용
    forecast_ets,
    forecast_prophet,
    forecast_lstm,
    forecast_theta,
)

# --------------------------- 유틸 ---------------------------

def _ensure_series(df: pd.DataFrame, indicator: str) -> pd.Series:
    """
    (date, indicator, value) 구조의 df에서 특정 indicator의 value 시계열을 반환
    """
    cols = {c.lower(): c for c in df.columns}
    for need in ("date", "indicator", "value"):
        if need not in cols:
            raise KeyError(f"입력 df에 '{need}' 컬럼이 필요합니다. 현재 컬럼: {list(df.columns)}")

    sub = df.loc[df[cols["indicator"]] == indicator, [cols["date"], cols["value"]]].copy()
    if sub.empty:
        raise ValueError(f"indicator='{indicator}' 데이터가 없습니다.")

    sub[cols["date"]] = pd.to_datetime(sub[cols["date"]], errors="coerce")
    sub = sub.dropna(subset=[cols["date"]]).sort_values(cols["date"])
    s = pd.Series(sub[cols["value"]].astype(float).values, index=sub[cols["date"]])
    s.index.name = "date"
    return s


def _future_index_like(index: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """
    기존 인덱스의 빈도를 추론해 미래 날짜 인덱스(horizon개) 생성
    """
    if not isinstance(index, pd.DatetimeIndex) or index.empty:
        raise ValueError("유효한 DatetimeIndex가 필요합니다.")
    freq = pd.infer_freq(index) or "M"   # 기본 월말
    future = pd.date_range(start=index[-1], periods=horizon + 1, freq=freq)[1:]
    return future


# --------------------- 메인 진입 함수 ----------------------

def forecast_trade_multi_models(
    trade_df: pd.DataFrame,
    indicator: str,
    horizon: int,
    use_models: Optional[List[str]] = None,
    model_kwargs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    특정 indicator의 value 시계열을 다중 모델로 n-step 예측 후 합친 DataFrame 반환.

    Parameters
    ----------
    trade_df : pd.DataFrame
        최소 컬럼: ['date','indicator','value'] (대소문자 무관)
    indicator : str
        예: 'expDlr', 'impDlr'
    horizon : int
        예측 스텝 수
    use_models : list[str], optional
        사용할 모델 리스트(대/소문자 무관). 기본: ['sarima','ets','prophet','lstm','theta']
    model_kwargs : dict, optional
        모델별 추가 파라미터. 예:
        {
          "SARIMA": {"seasonal_period": 12},  # 미지정 시 자동(m) 사용
          "LSTM": {"lookback": 12, "epochs": 50, "batch_size": 16},
        }

    Returns
    -------
    pd.DataFrame
        index=future dates,
        columns=['sarima_{indicator}', 'ets_{indicator}', 'prophet_{indicator}', 'lstm_{indicator}', 'theta_{indicator}']
        (성공한 모델만 컬럼 포함)
    """
    if use_models is None:
        use_models = ["sarima", "ets", "prophet", "lstm", "theta"]
    # 표준화
    use_models = [m.strip().upper() for m in use_models]
    model_kwargs = model_kwargs or {}

    # 1) 대상 시계열 만들기
    y = _ensure_series(trade_df, indicator)

    # 2) 주기/계절성 추론
    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)

    # 3) 각 모델 예측
    future_idx = _future_index_like(y.index, horizon)
    out_cols = {}
    errors = {}

    if "SARIMA" in use_models:
        res = forecast_sarima(y, horizon, seasonal_period=m, **model_kwargs.get("SARIMA", {}))
        if "error" in res:
            errors["SARIMA"] = res["error"]
        else:
            out_cols[f"sarima_{indicator}"] = np.asarray(res["forecast"]).reshape(-1)

    if "ETS" in use_models:
        res = forecast_ets(y, horizon, m=m, **model_kwargs.get("ETS", {}))
        if "error" in res:
            errors["ETS"] = res["error"]
        else:
            out_cols[f"ets_{indicator}"] = np.asarray(res["forecast"]).reshape(-1)

    if "PROPHET" in use_models:
        res = forecast_prophet(y, horizon, m=m, **model_kwargs.get("Prophet", {}))
        if "error" in res:
            errors["Prophet"] = res["error"]
        else:
            out_cols[f"prophet_{indicator}"] = np.asarray(res["forecast"]).reshape(-1)

    if "LSTM" in use_models:
        res = forecast_lstm(y, horizon, **model_kwargs.get("LSTM", {}))
        if "error" in res:
            errors["LSTM"] = res["error"]
        else:
            out_cols[f"lstm_{indicator}"] = np.asarray(res["forecast"]).reshape(-1)

    if "THETA" in use_models:
        res = forecast_theta(y, horizon, m=m, **model_kwargs.get("Theta", {}))
        if "error" in res:
            errors["Theta"] = res["error"]
        else:
            out_cols[f"theta_{indicator}"] = np.asarray(res["forecast"]).reshape(-1)

    # 4) 결과 합치기
    if not out_cols:
        raise RuntimeError(f"모든 모델 예측 실패: {errors}")

    result = pd.DataFrame(out_cols, index=future_idx)
    result.index.name = "date"

    # 실패한 모델이 있으면 경고 출력(필요시 주석 처리 가능)
    if errors:
        print("[WARN] 일부 모델 예측 실패:", errors)

    return result


# --------- 단독 테스트 (선택) ---------
# if __name__ == "__main__":
#     # 간단 자가 테스트용 (무작위 데이터)
#     idx = pd.date_range("2010-01-31", periods=120, freq="M")
#     val = np.linspace(1e6, 2e6, len(idx)) + np.random.normal(0, 5e4, len(idx))
#     df = pd.DataFrame({"date": idx, "indicator": "expDlr", "value": val})
#     out = forecast_trade_multi_models(df, indicator="expDlr", horizon=6)
#     print(out)
