import pandas as pd
from typing import Tuple, Optional

def _ensure_qdate_index(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """Date 인덱스 정리(이미 Date 인덱스면 유지), 정렬/중복 제거."""
    if df is None or df.empty:
        out = pd.DataFrame()
        out.index = pd.DatetimeIndex([], name="Date")
        return out

    out = df.copy()
    if date_col in out.columns:
        out["Date"] = pd.to_datetime(out[date_col], errors="coerce")
        out = out.drop(columns=[date_col]).set_index("Date")
    else:
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index, errors="coerce")
        out.index.name = "Date"

    # 정렬 + 중복 인덱스가 있으면 마지막 값 유지
    out = out.sort_index()
    if not out.index.is_unique:
        out = out[~out.index.duplicated(keep="last")]
    return out


def get_revenue_export_joined_table(
    df_rev: pd.DataFrame,                # columns: ['revenue','year','quarter','year_quarter','symbol']
    df_exog: pd.DataFrame,               # columns: ['expDlr_forecast_12m','year','quarter','year_quarter','root_hs_code']
    join_how: str = "outer",
    fill_exog: Optional[str] = None,     # 'ffill' | 'bfill' | None
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    df_rev의 'revenue'를 endog_var로, df_exog의 'expDlr_forecast_12m'을 exog_var로 맵핑하여
    Date 인덱스 기준으로 결합한 DataFrame을 반환합니다.

    Returns
    -------
    combined_df         : Date index, columns=['endog_var','exog_var']
    final_combined_data : combined_df의 복사본
    forecast_df         : endog_var가 존재하는 구간만 필터링
    """
    # 1) 인덱스 정리
    rev = _ensure_qdate_index(df_rev)
    exo = _ensure_qdate_index(df_exog)

    # 2) 숫자형 변환 + 필요한 컬럼만
    if "revenue" not in rev.columns:
        raise KeyError("df_rev에 'revenue' 컬럼이 없습니다.")
    rev = rev.copy()
    rev["revenue"] = pd.to_numeric(rev["revenue"], errors="coerce")
    rev_endog = rev[["revenue"]].rename(columns={"revenue": "endog_var"})

    # exog: 우선 'exog_var'가 이미 있으면 사용, 없으면 'expDlr_forecast_12m'를 사용
    exo = exo.copy()
    if "exog_var" in exo.columns:
        exo_only = exo[["exog_var"]]
        exo_only["exog_var"] = pd.to_numeric(exo_only["exog_var"], errors="coerce")
    elif "expDlr_forecast_12m" in exo.columns:
        exo["expDlr_forecast_12m"] = pd.to_numeric(exo["expDlr_forecast_12m"], errors="coerce")
        exo_only = exo[["expDlr_forecast_12m"]].rename(columns={"expDlr_forecast_12m": "exog_var"})
    else:
        # exogenous 입력이 없는 경우 빈 exog 컬럼 생성
        exo_only = pd.DataFrame(index=exo.index, columns=["exog_var"])

    # 3) 병합
    combined_df = rev_endog.join(exo_only, how=join_how)

    # 4) exog 보간(옵션)
    if fill_exog in {"ffill", "bfill"}:
        combined_df["exog_var"] = combined_df["exog_var"].fillna(method=fill_exog)

    # 5) 후속 파이프라인 호환 반환
    final_combined_data = combined_df.copy()
    forecast_df = combined_df[combined_df["endog_var"].notna()].copy()

    return combined_df, final_combined_data, forecast_df
