from typing import Optional
import pandas as pd
from DATA.universal_ts_forecast_function import infer_freq_alias


def _future_index_from_df(combined_df: pd.DataFrame, horizon: int) -> pd.DatetimeIndex:
    """combined_df의 주기를 보고 horizon개 미래 인덱스 생성(분기/월 지원)."""
    df = combined_df.copy()

    # 인덱스 정리
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df.index = pd.to_datetime(df["date"], errors="coerce")
            df = df.drop(columns=["date"])
        else:
            df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[~df.index.isna()].sort_index()

    # 마지막 관측 시점(가능하면 endog_var 기준) 찾기
    if "endog_var" in df.columns and df["endog_var"].notna().any():
        last_idx = df.loc[df["endog_var"].notna()].index.max()
    else:
        last_idx = df.index.max()

    freq_alias = infer_freq_alias(df.index)
    h = int(horizon)

    # 분기/월 인덱스 생성
    if "Q" in str(freq_alias).upper():  # 분기
        return pd.period_range(last_idx.to_period("Q") + 1, periods=h, freq="Q").to_timestamp("Q")
    else:  # 기본: 월
        return pd.period_range(last_idx.to_period("M") + 1, periods=h, freq="M").to_timestamp("M")


def build_forecast_df_from_out(out_result: dict,
                               combined_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    forecast_endog_* 결과(out_result)로부터 예측 DF 생성.
    - out_result['forecast_index']가 없으면 combined_df로 미래 인덱스 복원.
    - 외생변수 여부(out_result['used_exog'])에 따라 컬럼명 자동 설정.
    """
    # 값/인덱스 꺼내기
    yhat = out_result.get("forecast", None)
    fc_index = out_result.get("forecast_index", None)
    used_exog = bool(out_result.get("used_exog", False))

    if yhat is None:
        raise ValueError("out_result에 'forecast'가 없습니다.")
    horizon = len(yhat)

    # 인덱스가 없으면 combined_df로 생성
    if fc_index is None:
        if combined_df is None:
            raise ValueError("forecast_index가 없으므로 combined_df를 함께 전달해야 합니다.")
        fc_index = _future_index_from_df(combined_df, horizon)

    col_name = "revenue_with_exog" if used_exog else "revenue_with_noexog"
    return pd.DataFrame(yhat, index=fc_index, columns=[col_name])