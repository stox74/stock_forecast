# psr_forecast_runner.py

# psr_forecast_runner.py
import numpy as np
import pandas as pd
from typing import Optional, Tuple

from DATA.universal_ts_forecast_function import (
    ensure_datetime_index_df,
    infer_freq_alias,
    seasonal_periods_from_freq,
    forecast_sarima,
    forecast_ets,
    forecast_prophet,
    forecast_lstm,
    forecast_theta,
)

from statsmodels.tsa.statespace.sarimax import SARIMAX


# ──────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────
def _make_future_index(last_idx: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """
    관측 시계열의 마지막 인덱스를 기준으로 horizon 길이의 미래 인덱스 생성.
    infer_freq 실패 시 월말/일간을 추론.
    """
    freq = pd.infer_freq(last_idx)
    if not freq:
        last = last_idx[-1]
        if last == (last + pd.offsets.MonthEnd(0)):
            freq = "M"
        else:
            freq = "D"
    return pd.date_range(start=last_idx[-1], periods=horizon + 1, freq=freq, inclusive="neither")


def _extend_exog_for_forecast(
    exog_df: pd.DataFrame,
    future_index: pd.DatetimeIndex,
    strategy: str = "repeat_last",
) -> pd.DataFrame:
    """
    미래 구간의 exog 프레임 생성.
    - "repeat_last": 마지막 행을 horizon 길이만큼 복제
    - "ffill": 과거+미래 인덱스 합집합으로 reindex 후 앞으로 채움
    """
    if exog_df is None or exog_df.empty:
        return pd.DataFrame(index=future_index)

    if strategy == "repeat_last":
        last_row = exog_df.iloc[-1]
        last_vals = pd.DataFrame(
            np.tile(last_row.values, (len(future_index), 1)),
            index=future_index,
            columns=exog_df.columns,
        )
        return last_vals

    elif strategy == "ffill":
        tmp = exog_df.copy()
        tmp = tmp.reindex(exog_df.index.union(future_index)).ffill()
        return tmp.loc[future_index]

    else:
        raise ValueError("지원하지 않는 exog 확장 전략입니다. ('repeat_last' 또는 'ffill')")


def _ensure_finite(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """
    fit/forecast 직전 유한성 보장. inf → NaN 변환 후 ffill/bfill로 치환.
    """
    if df is None or df.empty:
        return df
    arr = df.to_numpy()
    if not np.isfinite(arr).all():
        print(f"[DEBUG] {name}에 NaN/Inf 존재 → ffill/bfill로 정화")
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.ffill().bfill()
    return df


def _to_numeric_strict(df: pd.DataFrame) -> pd.DataFrame:
    """
    콤마/공백 제거 후 숫자형으로 강제 변환(errors='coerce').
    """
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = out[c].astype(str).str.replace(",", "").str.strip()
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _align_index_period_end(df: pd.DataFrame, ref_index: pd.DatetimeIndex) -> pd.DataFrame:
    """
    y의 빈도에 맞춰 exog 인덱스를 period end로 정렬(월/분기 등)해 날짜 미스매치 완화.
    ref_index: y의 인덱스(빈도 추정을 위한 참조)
    """
    if df is None or df.empty:
        return df
    try:
        freq_alias = infer_freq_alias(ref_index)
        if freq_alias:  # 'M', 'Q', ...
            idx_aligned = df.index.to_period(freq_alias).to_timestamp(how="end")
            out = df.copy()
            out.index = idx_aligned
            return out.sort_index()
        return df.sort_index()
    except Exception:
        return df.sort_index()


# ──────────────────────────────────────────────────────────────────────
# Preprocess: 앞 절반 NaN 가로 제거 / 뒤 절반 유지
# ──────────────────────────────────────────────────────────────────────
def _preprocess_fronthalf_dropna_keep_backhalf(
    df_psr: pd.DataFrame, exog_df: Optional[pd.DataFrame]
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """
    1) psr_df와 exog_df를 outer-join
    2) 앞 절반 구간: 가로(any) NaN 포함 행 제거
       뒤 절반 구간: 원본 유지
    3) 다시 psr / exog로 분리하여 반환
    """
    psr_df = ensure_datetime_index_df(df_psr).copy()
    if "psr" not in psr_df.columns:
        raise KeyError("입력 df_psr에 'psr' 컬럼이 있어야 합니다.")
    psr_df["psr"] = pd.to_numeric(psr_df["psr"], errors="coerce")

    if exog_df is not None and not exog_df.empty:
        exog_df = ensure_datetime_index_df(exog_df).copy()
        exog_df = _to_numeric_strict(exog_df)
        joined = psr_df.join(exog_df, how="outer")
    else:
        joined = psr_df.copy()

    joined = joined.sort_index()
    n = len(joined)
    if n == 0:
        return psr_df.iloc[0:0], (exog_df.iloc[0:0] if exog_df is not None else None)

    split = n // 2
    first_half = joined.iloc[:split].copy()
    second_half = joined.iloc[split:].copy()

    # 앞 절반: 가로(any) NaN 제거
    first_half_clean = first_half.dropna(axis=0, how="any")

    cleaned = pd.concat([first_half_clean, second_half], axis=0).sort_index()

    # 다시 분리
    psr_clean = cleaned[["psr"]].copy()
    if exog_df is not None and not exog_df.empty:
        exog_cols = [c for c in cleaned.columns if c != "psr"]
        exog_clean = cleaned[exog_cols].copy()
    else:
        exog_clean = None

    return psr_clean, exog_clean


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
def forecast_psr_all_models(
    df_psr: pd.DataFrame,
    horizon: int = 5,
    exog_df: Optional[pd.DataFrame] = None,
    exog_future_strategy: str = "repeat_last",
    sarima_grid_kwargs: Optional[dict] = None,
    lstm_kwargs: Optional[dict] = None,
) -> pd.DataFrame:

    if sarima_grid_kwargs is None:
        sarima_grid_kwargs = {}
    if lstm_kwargs is None:
        lstm_kwargs = {}

    # (A) 전처리: 앞 절반 NaN 제거 / 뒤 절반 유지
    psr_clean, exog_clean = _preprocess_fronthalf_dropna_keep_backhalf(df_psr, exog_df)

    # (A-1) 디버깅: 결합 상태 확인
    print("\n" + "=" * 70)
    print("[DEBUG] 전처리 완료 후 psr_clean / exog_clean 결합 상태 확인")
    print("=" * 70)
    try:
        if exog_clean is not None and not exog_clean.empty:
            combined_debug_df = psr_clean.join(exog_clean, how="outer")
        else:
            combined_debug_df = psr_clean.copy()
        print("\n[head(10)]")
        print(combined_debug_df.head(10))
        print("\n[tail(10)]")
        print(combined_debug_df.tail(10))
        print("\n[info()]")
        combined_debug_df.info()
    except Exception as e:
        print(f"[DEBUG ERROR] 데이터 확인 중 오류 발생: {e}")
    print("=" * 70 + "\n")

    # (B) y 학습 대상
    y = psr_clean["psr"].astype(float).dropna()
    if y.empty:
        raise ValueError("psr 시계열이 비어 있습니다. (전처리 후)")

    # 빈도/계절주기
    freq_alias = infer_freq_alias(y.index)
    m = seasonal_periods_from_freq(freq_alias)

    # 미래 인덱스
    future_idx = _make_future_index(y.index, horizon)

    # (C) 단변량 모델들
    sarima_noexog_res = forecast_sarima(
        y=y, forecast_horizon=horizon, seasonal_period=m, **sarima_grid_kwargs
    )
    sarima_noexog = sarima_noexog_res.get("forecast", np.full(horizon, np.nan))

    ets_res = forecast_ets(y=y, forecast_horizon=horizon, m=m)
    ets_fc = ets_res.get("forecast", np.full(horizon, np.nan))

    prophet_res = forecast_prophet(y=y, forecast_horizon=horizon, m=m)
    prophet_fc = prophet_res.get("forecast", np.full(horizon, np.nan))

    lstm_res = forecast_lstm(y=y, forecast_horizon=horizon, **lstm_kwargs)
    lstm_fc = lstm_res.get("forecast", np.full(horizon, np.nan))

    theta_res = forecast_theta(y=y, forecast_horizon=horizon, m=m)
    theta_fc = theta_res.get("forecast", np.full(horizon, np.nan))

    # (D) SARIMA with exog
    sarima_exog = np.full(horizon, np.nan)

    if exog_clean is not None and not exog_clean.empty:
        # 1) 학습에 사용할 유효 구간(타깃+exog 모두 notna)
        used_cols = ["psr"] + list(exog_clean.columns)
        joined = psr_clean.join(exog_clean, how="inner")[used_cols]
        valid_mask = joined.notna().all(axis=1)
        valid_idx = valid_mask[valid_mask].index

        y_train = y.loc[y.index.intersection(valid_idx)]
        X_train = exog_clean.loc[y_train.index]

        # 2) 디버깅: 학습용 구조 확인
        print("\n[DEBUG] SARIMA(exog) 학습용 데이터 확인")
        print("-" * 70)
        print("y_train shape:", y_train.shape)
        print("X_train shape:", X_train.shape)
        print("공통 인덱스 개수:", len(valid_idx))
        print("\nX_train.head(5):\n", X_train.head())
        print("-" * 70 + "\n")

        # 3) 미래 exog 우선 사용(이미 제공된 경우)
        #    y 빈도에 맞춰 exog 인덱스 정렬(월/분기 등 period end)
        exog_clean_aligned = _align_index_period_end(exog_clean, y.index)

        if set(future_idx).issubset(set(exog_clean_aligned.index)):
            # 미래 인덱스가 전부 존재하면 그대로 사용
            X_future = exog_clean_aligned.reindex(future_idx)[X_train.columns]
        else:
            # 일부만 있거나 없으면 확장 전략 사용
            base = exog_clean_aligned[X_train.columns]
            X_future = _extend_exog_for_forecast(base, future_idx, exog_future_strategy)

        # 4) 유한성 보장 및 인덱스 동기화
        X_train = _ensure_finite(X_train, "X_train").dropna()
        y_train = y_train.loc[X_train.index]  # 인덱스 동기화
        X_future = _ensure_finite(X_future, "X_future")

        # 5) 적합/예측
        if not X_train.empty:
            order = sarima_grid_kwargs.get("order", (0, 1, 1))
            seasonal_order = sarima_grid_kwargs.get("seasonal_order", (0, 1, 1, max(1, m)))

            try:
                model = SARIMAX(
                    y_train, exog=X_train,
                    order=order, seasonal_order=seasonal_order,
                    enforce_stationarity=False, enforce_invertibility=False
                )
                res = model.fit(disp=False)
                sarima_exog = res.forecast(steps=horizon, exog=X_future).values
            except Exception as e:
                print(f"[경고] SARIMA exog 예측 실패: {e}")

    # (E) 결과 집계
    out = pd.DataFrame({
        "SARIMA_noexog": sarima_noexog,
        "SARIMA_exog": sarima_exog,
        "ETS": ets_fc,
        "Prophet": prophet_fc,
        "LSTM": lstm_fc,
        "Theta": theta_fc,
    }, index=future_idx)

    out.columns = [f"psr_{col}" for col in out.columns]
    out.index.name = "date"
    return out



