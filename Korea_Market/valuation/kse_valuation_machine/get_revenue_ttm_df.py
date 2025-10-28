# get_revenue_ttm_df.py

from __future__ import annotations
import pandas as pd
from typing import Optional, Dict


def _detect_value_col(df: pd.DataFrame, preferred: Optional[str] = None) -> str:
    """
    예측 DF에서 값(Column) 이름을 자동 식별한다.
    preferred가 주어지면 우선 사용하고, 없으면 후보 리스트/숫자형 첫 컬럼을 사용한다.
    """
    if preferred and preferred in df.columns:
        return preferred

    candidates = ["forecast", "yhat", "y_pred", "prediction", "pred", "value", "revenue"]
    for c in candidates:
        if c in df.columns:
            return c

    # 마지막 수단: 숫자형 첫 컬럼
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            return c

    raise ValueError("예측 데이터에서 숫자형 값 컬럼을 찾지 못했습니다.")


def _to_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    인덱스를 DatetimeIndex로 보장하고 정렬한다.
    """
    x = df.copy()
    if not isinstance(x.index, pd.DatetimeIndex):
        x.index = pd.to_datetime(x.index)
    x = x.sort_index()
    x = x[~x.index.duplicated(keep="last")]
    return x


def _combine_actual_and_forecast(
    df_actual: pd.DataFrame,
    df_forecast: pd.DataFrame,
    out_col_name: str,
    actual_col: str = "revenue",
    forecast_col: Optional[str] = None,
) -> pd.DataFrame:
    """
    실제(df_actual[actual_col])과 예측(df_forecast[*forecast_col*])을
    인덱스(date) 기준으로 상하 결합하여 1개 컬럼(out_col_name)의 단일 시계열을 생성.

    - df_actual, df_forecast 모두 인덱스가 날짜라고 가정.
    - 예측 구간이 실제 구간과 겹치면 실제 값이 우선되도록 예측 쪽 겹치는 인덱스를 제거한다.
    """
    a = _to_datetime_index(df_actual)[[actual_col]].rename(columns={actual_col: out_col_name})
    f = _to_datetime_index(df_forecast)

    f_value = _detect_value_col(f, preferred=forecast_col)
    f = f[[f_value]].rename(columns={f_value: out_col_name})

    # 실제 구간과 겹치는 날짜는 예측에서 제거 (실제 우선)
    f = f.loc[~f.index.isin(a.index)]

    # 상하 결합 후 정렬
    s = pd.concat([a, f], axis=0).sort_index()
    s = s[~s.index.duplicated(keep="first")]
    s.index.name = "date"
    return s


def get_revenue_ttm_df(
    df_rev: pd.DataFrame,
    rev_sarima_noexog: pd.DataFrame,
    rev_sarima_exog: pd.DataFrame,
    rev_ets_df: pd.DataFrame,
    rev_prophet_df: pd.DataFrame,
    rev_theta_df: pd.DataFrame,
    rev_lstm_df: Optional[pd.DataFrame] = None,
    ref_lstm_df: Optional[pd.DataFrame] = None,
    forecast_col_map: Optional[Dict[str, str]] = None,
    ttm_window: int = 4,
) -> pd.DataFrame:
    """
    실제 매출(df_rev['revenue'])과 여러 모델 예측치를 상하 결합한 단일 시계열들을
    가로로 결합한 뒤, 각 컬럼에 대해 4분기 롤링 합(TTM) 컬럼까지 포함한 DataFrame을 반환한다.

    Parameters
    ----------
    df_rev : DataFrame
        실제 매출. 인덱스는 date, 컬럼에 'revenue' 포함.
    rev_sarima_noexog, rev_sarima_exog, rev_ets_df, rev_prophet_df, rev_theta_df : DataFrame
        각 모델의 예측 결과 DF. 인덱스는 date.
    rev_lstm_df, ref_lstm_df : Optional[DataFrame]
        LSTM 예측 DF (두 변수명 중 하나만 제공되어도 됨).
    forecast_col_map : Optional[Dict[str, str]]
        모델 키 -> 예측 컬럼명 강제 지정용 맵.
        예: {"sarima": "forecast", "prophet": "yhat"}
    ttm_window : int
        롤링 합 계산 윈도우(기본 4: 분기 데이터의 TTM).

    Returns
    -------
    rev_final : DataFrame
        인덱스=date, 컬럼은 revenue_sarima, revenue_sarima_exog, revenue_ets, revenue_prophet,
        revenue_lstm, revenue_theta 와 각 *_ttm 컬럼이 포함됨.
    """
    fcmap = forecast_col_map or {}

    # LSTM 입력 정리 (둘 중 하나만 넘어와도 동작)
    lstm_df = rev_lstm_df if rev_lstm_df is not None else ref_lstm_df
    if lstm_df is None:
        raise ValueError("rev_lstm_df 또는 ref_lstm_df 중 하나는 제공되어야 합니다.")

    # 모델별 단일 시계열 생성 (실제+예측 상하 결합, 1개 컬럼)
    s_sarima_noexog = _combine_actual_and_forecast(
        df_rev, rev_sarima_noexog, "revenue_sarima", actual_col="revenue", forecast_col=fcmap.get("sarima")
    )
    s_sarima_exog = _combine_actual_and_forecast(
        df_rev, rev_sarima_exog, "revenue_sarima_exog", actual_col="revenue", forecast_col=fcmap.get("sarima_exog")
    )
    s_ets = _combine_actual_and_forecast(
        df_rev, rev_ets_df, "revenue_ets", actual_col="revenue", forecast_col=fcmap.get("ets")
    )
    s_prophet = _combine_actual_and_forecast(
        df_rev, rev_prophet_df, "revenue_prophet", actual_col="revenue", forecast_col=fcmap.get("prophet")
    )
    s_lstm = _combine_actual_and_forecast(
        df_rev, lstm_df, "revenue_lstm", actual_col="revenue", forecast_col=fcmap.get("lstm")
    )
    s_theta = _combine_actual_and_forecast(
        df_rev, rev_theta_df, "revenue_theta", actual_col="revenue", forecast_col=fcmap.get("theta")
    )

    # 가로 결합
    rev_panel = pd.concat(
        [s_sarima_noexog, s_sarima_exog, s_ets, s_prophet, s_lstm, s_theta],
        axis=1
    ).sort_index()

    # 4분기 롤링 합(TTM)
    rev_ttm = rev_panel.rolling(window=ttm_window, min_periods=ttm_window).sum()
    rev_ttm.columns = [f"{c}_ttm" for c in rev_ttm.columns]

    # 최종 합치기
    rev_final = pd.concat([rev_panel, rev_ttm], axis=1)
    rev_final.index.name = "date"
    return rev_final


# 예시 사용법(스크립트 직접 실행 시):
if __name__ == "__main__":
    # 실제 환경에서는 아래 부분을 삭제하거나 주석 처리하고,
    # 노트북/다른 스크립트에서 get_revenue_ttm_df를 import 하여 사용하세요.
    print("This module provides get_revenue_ttm_df(). Import and call it in your pipeline.")
