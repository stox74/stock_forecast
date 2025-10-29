# get_revenue_ttm_df.py

from __future__ import annotations
import pandas as pd
from typing import Optional, Dict, Union

DFLike = Optional[Union[pd.DataFrame, pd.Series]]

# ──────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────────────────────

def _is_none_or_empty(x: DFLike) -> bool:
    """None 이거나 빈 객체인지 판별"""
    if x is None:
        return True
    if isinstance(x, (pd.Series, pd.DataFrame)) and x.empty:
        return True
    return False


def _to_dataframe(x: DFLike) -> Optional[pd.DataFrame]:
    """Series/None/DF 를 모두 DF 또는 None 으로 정규화"""
    if x is None:
        return None
    if isinstance(x, pd.Series):
        return x.to_frame()
    if isinstance(x, pd.DataFrame):
        return x
    raise TypeError("지원하지 않는 타입입니다. DataFrame/Series/None 만 허용됩니다.")


def _to_datetime_index(df_like: DFLike) -> Optional[pd.DataFrame]:
    """
    인덱스를 DatetimeIndex로 보장하고 정렬하여 DF 반환.
    None 또는 빈 객체면 None 반환.
    """
    if _is_none_or_empty(df_like):
        return None

    x = _to_dataframe(df_like).copy()

    # 'date' 컬럼이 있고 인덱스가 날짜형이 아니면 date를 인덱스로 승격
    if "date" in x.columns and not isinstance(x.index, pd.DatetimeIndex):
        try:
            x = x.set_index("date")
        except Exception:
            pass

    # 인덱스를 DatetimeIndex로
    if not isinstance(x.index, pd.DatetimeIndex):
        x.index = pd.to_datetime(x.index, errors="coerce")

    # NaT(변환 실패) 드롭 & 정렬 & 중복 제거
    x = x[~x.index.isna()]
    if x.empty:
        return None

    x = x.sort_index()
    x = x[~x.index.duplicated(keep="last")]
    return x


def _detect_value_col(df_like: DFLike, preferred: Optional[str] = None) -> str:
    """
    예측 DF/Series에서 값(Column) 이름을 자동 식별.
    preferred가 있으면 우선 사용, 없으면 후보/숫자형 첫 컬럼을 사용.
    """
    if _is_none_or_empty(df_like):
        raise ValueError("예측 데이터가 비어 있습니다(None/empty).")

    df = _to_dataframe(df_like)

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


# ──────────────────────────────────────────────────────────────────────────────
# 결합 로직
# ──────────────────────────────────────────────────────────────────────────────

def _combine_actual_and_forecast(
    df_actual: DFLike,
    df_forecast: DFLike,
    out_col_name: str,
    actual_col: str = "revenue",
    forecast_col: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    실제(df_actual[actual_col])과 예측(df_forecast[*forecast_col*])을
    인덱스(date) 기준으로 상하 결합하여 1개 컬럼(out_col_name)의 단일 시계열 생성.

    - df_actual, df_forecast 모두 인덱스가 날짜(또는 'date' 컬럼)이어야 함.
    - 예측 구간이 실제 구간과 겹치면 실제 값 우선(겹치는 예측 인덱스 제거).
    - 예측이 None/빈 경우에는 '실제만'으로 구성된 시계열을 반환.
    - 실제가 None/빈 경우에는 '예측만'으로 구성된 시계열을 반환.
    - 둘 다 None/빈이면 None 반환.
    """
    a = _to_datetime_index(df_actual)
    f = _to_datetime_index(df_forecast)

    # 둘 다 비어있으면 None
    if a is None and f is None:
        return None

    # 실제쪽 구성
    if a is not None:
        if actual_col not in a.columns:
            # Series였다가 to_frame 된 케이스 등: 컬럼명 자동 탐색 시도
            if actual_col not in a.columns and a.shape[1] == 1:
                a = a.rename(columns={a.columns[0]: actual_col})
            else:
                raise KeyError(f"실제 데이터에 '{actual_col}' 컬럼이 없습니다.")
        a = a[[actual_col]].rename(columns={actual_col: out_col_name})

    # 예측쪽 구성
    if f is not None:
        f_value = _detect_value_col(f, preferred=forecast_col)
        f = f[[f_value]].rename(columns={f_value: out_col_name})
        # 실제와 겹치는 날짜 제거(실제 우선)
        if a is not None:
            f = f.loc[~f.index.isin(a.index)]

    # 상하 결합
    if a is not None and f is not None:
        s = pd.concat([a, f], axis=0).sort_index()
    elif a is not None:
        s = a.sort_index()
    else:
        s = f.sort_index()

    s = s[~s.index.duplicated(keep="first")]
    s.index.name = "date"
    return s


# ──────────────────────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────────────────────

def get_revenue_ttm_df(
    df_rev: Union[pd.DataFrame, pd.Series],
    rev_sarima_noexog: DFLike,
    rev_sarima_exog: DFLike,
    rev_ets_df: DFLike,
    rev_prophet_df: DFLike,
    rev_theta_df: DFLike,
    rev_lstm_df: DFLike = None,
    ref_lstm_df: DFLike = None,
    forecast_col_map: Optional[Dict[str, str]] = None,
    ttm_window: int = 4,
) -> pd.DataFrame:
    """
    실제 매출(df_rev['revenue'])과 여러 모델 예측치를 상하 결합한 단일 시계열들을
    가로로 결합한 뒤, 각 컬럼에 대해 4분기 롤링 합(TTM) 컬럼까지 포함한 DataFrame 반환.

    Parameters
    ----------
    df_rev : DataFrame or Series
        실제 매출. 인덱스는 date(또는 'date' 컬럼), 컬럼에 'revenue' 포함(Series면 단일 컬럼으로 간주).
    rev_sarima_noexog, rev_sarima_exog, rev_ets_df, rev_prophet_df, rev_theta_df : DFLike
        각 모델의 예측 결과(Series/DF/None 허용). 인덱스는 date(또는 'date' 컬럼).
    rev_lstm_df, ref_lstm_df : DFLike
        LSTM 예측(둘 중 하나만 넘어와도 됨). 둘 다 None이어도 동작.
    forecast_col_map : Optional[Dict[str, str]]
        모델 키 -> 예측 컬럼명 강제 지정용 맵.
        예: {"sarima": "forecast", "prophet": "yhat"}
    ttm_window : int
        롤링 합 계산 윈도우(기본 4: 분기 데이터의 TTM).

    Returns
    -------
    rev_final : DataFrame
        인덱스=date, 컬럼은 사용 가능한 모델만 포함:
        revenue_sarima, revenue_sarima_exog, revenue_ets, revenue_prophet, revenue_lstm, revenue_theta
        및 각 *_ttm 컬럼.
    """
    fcmap = forecast_col_map or {}

    # df_rev 정규화(Series 허용)
    df_rev_norm = _to_datetime_index(df_rev)
    if df_rev_norm is None:
        raise ValueError("df_rev 가 비어 있습니다. (None/empty)")
    # df_rev 컬럼명 보정: Series 혹은 다른 단일 컬럼 이름인 경우 'revenue'로 통일
    if "revenue" not in df_rev_norm.columns:
        if df_rev_norm.shape[1] == 1:
            df_rev_norm = df_rev_norm.rename(columns={df_rev_norm.columns[0]: "revenue"})
        else:
            raise KeyError("df_rev 에 'revenue' 컬럼이 없습니다.")

    # LSTM 입력 정리 (둘 중 하나만 넘어와도 되고, 둘 다 None 가능)
    lstm_df = rev_lstm_df if not _is_none_or_empty(rev_lstm_df) else ref_lstm_df

    # 모델별 단일 시계열 생성
    series_map: Dict[str, Optional[pd.DataFrame]] = {
        "revenue_sarima": _combine_actual_and_forecast(
            df_rev_norm, rev_sarima_noexog, "revenue_sarima",
            actual_col="revenue", forecast_col=fcmap.get("sarima")
        ),
        "revenue_sarima_exog": _combine_actual_and_forecast(
            df_rev_norm, rev_sarima_exog, "revenue_sarima_exog",
            actual_col="revenue", forecast_col=fcmap.get("sarima_exog")
        ),
        "revenue_ets": _combine_actual_and_forecast(
            df_rev_norm, rev_ets_df, "revenue_ets",
            actual_col="revenue", forecast_col=fcmap.get("ets")
        ),
        "revenue_prophet": _combine_actual_and_forecast(
            df_rev_norm, rev_prophet_df, "revenue_prophet",
            actual_col="revenue", forecast_col=fcmap.get("prophet")
        ),
        "revenue_lstm": _combine_actual_and_forecast(
            df_rev_norm, lstm_df, "revenue_lstm",
            actual_col="revenue", forecast_col=fcmap.get("lstm")
        ),
        "revenue_theta": _combine_actual_and_forecast(
            df_rev_norm, rev_theta_df, "revenue_theta",
            actual_col="revenue", forecast_col=fcmap.get("theta")
        ),
    }

    # 사용 가능한 시계열만 수집
    available_series = [s for s in series_map.values() if s is not None]
    if not available_series:
        raise ValueError("결합 가능한 시계열이 없습니다. (모든 모델이 None/empty)")

    # 가로 결합
    rev_panel = pd.concat(available_series, axis=1).sort_index()
    rev_panel.index.name = "date"

    # 4분기 롤링 합(TTM) - 숫자형 컬럼만
    num_cols = [c for c in rev_panel.columns if pd.api.types.is_numeric_dtype(rev_panel[c])]
    rev_ttm = rev_panel[num_cols].rolling(window=ttm_window, min_periods=ttm_window).sum()
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
