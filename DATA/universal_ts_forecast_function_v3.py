"""
통합 시계열 예측 함수 모듈
SARIMA, ETS, Prophet, LSTM, Theta 모델 지원
메모리 관리 기능 포함
"""

import sys
import os
import gc
import psutil
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
from typing import Optional, Iterable, Tuple, Dict, Any
from itertools import product

# 모델 임포트
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# Prophet 모델 (선택적)
try:
    from prophet import Prophet

    _HAS_PROPHET = True
except Exception:
    _HAS_PROPHET = False

# LSTM 모델 (선택적)
try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense
    from tensorflow.keras.callbacks import EarlyStopping
    from sklearn.preprocessing import MinMaxScaler

    _HAS_TF = True
except Exception:
    _HAS_TF = False

# Theta 모델 (선택적)
try:
    from statsmodels.tsa.forecasting.theta import ThetaModel

    _HAS_THETA = True
except Exception:
    _HAS_THETA = False

warnings.filterwarnings("ignore")

# 전역 변수
data_miss_list = []


# ==================== 메모리 관리 함수 ====================

def get_memory_usage():
    """현재 프로세스의 메모리 사용량 반환 (MB 단위)"""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return mem_info.rss / 1024 / 1024  # MB 단위


def clear_memory():
    """메모리 정리 수행"""
    gc.collect()
    if _HAS_TF:
        try:
            tf.keras.backend.clear_session()
        except:
            pass


def memory_check_decorator(func):
    """메모리 사용량을 체크하는 데코레이터"""

    def wrapper(*args, **kwargs):
        mem_before = get_memory_usage()
        print(f"[메모리] {func.__name__} 실행 전: {mem_before:.2f} MB")

        result = func(*args, **kwargs)

        mem_after = get_memory_usage()
        mem_diff = mem_after - mem_before
        print(f"[메모리] {func.__name__} 실행 후: {mem_after:.2f} MB (변화: {mem_diff:+.2f} MB)")

        # 메모리 사용량이 500MB 이상 증가했으면 경고
        if mem_diff > 500:
            print(f"[경고] 메모리 사용량이 크게 증가했습니다. 메모리 정리를 권장합니다.")

        return result

    return wrapper


def monitor_memory_usage(threshold_mb=2000):
    """
    메모리 사용량 모니터링
    threshold_mb: 경고를 발생시킬 메모리 임계값 (MB)
    """
    current_mem = get_memory_usage()
    if current_mem > threshold_mb:
        print(f"[경고] 메모리 사용량이 {current_mem:.2f} MB로 임계값 {threshold_mb} MB를 초과했습니다.")
        print("[조치] 메모리 정리를 수행합니다...")
        clear_memory()
        after_mem = get_memory_usage()
        print(f"[완료] 메모리 정리 후: {after_mem:.2f} MB (절약: {current_mem - after_mem:.2f} MB)")
    return current_mem


# ==================== 경로 설정 함수 ====================

def add_repo_path():
    """
    stock_forecast 프로젝트 루트를 자동 탐색하고,
    해당 경로를 sys.path에 추가하여 import 오류를 방지합니다.
    """
    try:
        current = Path(__file__).resolve()
    except NameError:
        current = Path.cwd()

    for parent in current.parents:
        if (parent / "DATA").exists():
            sys.path.insert(0, str(parent))
            print(f"[INFO] Project root added to sys.path: {parent}")
            return str(parent)

    fallback = r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\stock_forecast"
    if os.path.isdir(fallback):
        sys.path.insert(0, fallback)
        print(f"[WARNING] Using fallback path: {fallback}")
        return fallback

    raise FileNotFoundError("? DATA 폴더를 찾을 수 없습니다.")


# ==================== 데이터 전처리 함수 ====================

def ensure_datetime_index_df(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame의 인덱스를 DatetimeIndex로 변환"""
    out = df.copy()
    out.index.name = 'date'
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors='coerce')
    out = out[~out.index.isna()].sort_index()
    out.index.name = 'date'
    return out


def infer_freq_alias(index: pd.DatetimeIndex) -> str:
    """시계열 빈도 추론"""
    freq = pd.infer_freq(index)
    if freq:
        if freq.startswith("Q"): return "Q"
        if freq.startswith("M"): return "M"
        if freq.startswith("D"): return "D"
        if freq.startswith("W"): return "W"

    if len(index) < 2: return "D"
    gap = (index[1] - index[0]).days
    if 80 <= gap <= 100: return "Q"
    if 25 <= gap <= 35: return "M"
    return "D"


def seasonal_periods_from_freq(freq_alias: str) -> int:
    """주기별 계절성 기간 반환"""
    if freq_alias == "Q": return 4
    if freq_alias == "M": return 12
    if freq_alias == "D": return 7
    return 12


def normalize_period_end_index(index: pd.DatetimeIndex, freq_alias: str) -> pd.DatetimeIndex:
    """
    기간말 '영업일' 인덱스를 기간말 '달력일'로 정규화한다.

    DataGuide/FnGuide 분기 데이터는 2025-12-30 처럼 분기말 영업일로 기록되는 경우가
    있다. 반면 _prev_date() 는 QuarterEnd 오프셋으로 2025-12-31 을 만들기 때문에
    매칭이 실패하고, extract_recent_contiguous_block() 이 연속구간을 1개로 잘못
    판정해 종목이 통째로 SKIP 된다.

        ValueError: [SKIP] 최근 연속 무결측 구간 길이 부족: 1 < 20 (Q)

    이를 막기 위해 Q/M 주기에 한해 기간말 달력일로 스냅한다.
    D/W 는 정규화가 오히려 데이터를 훼손하므로 원본을 그대로 반환한다.
    이미 정규화된 인덱스에 다시 적용해도 결과가 같다(멱등).
    """
    f = (freq_alias or "").upper()
    if not f.startswith(("Q", "M")):
        return index
    return index.to_period(f[0]).to_timestamp(how="end").normalize()


def data_length_gate(index: pd.DatetimeIndex, n_obs: int):
    """데이터 길이 검증"""
    freq = infer_freq_alias(index)
    if freq == "Q" and n_obs < 20:
        return False, "분기 데이터 길이 부족(<20)"
    if freq == "M" and n_obs < 60:
        return False, "월간 데이터 길이 부족(<60)"
    if freq == "D" and n_obs < 1250:
        return False, "일간 데이터 길이 부족(<1250)"
    return True, freq


def _min_required_len(freq_alias: str) -> int:
    """주기별 최소 요구 길이"""
    f = freq_alias.upper()
    if f.startswith("Q"): return 20
    if f.startswith("M"): return 60
    if f.startswith("D"): return 1250
    return 60


def _prev_date(d: pd.Timestamp, freq_alias: str) -> pd.Timestamp:
    """주기에 따른 이전 날짜 계산"""
    f = freq_alias.upper()
    if f.startswith("Q"):
        return d - pd.offsets.QuarterEnd(1)
    if f.startswith("M"):
        return d - pd.offsets.MonthEnd(1)
    if f.startswith("W"):
        return d - pd.offsets.Week(weekday=6)
    if f.startswith("D"):
        return d - pd.Timedelta(days=1)
    return d - pd.offsets.MonthEnd(1)


def extract_recent_contiguous_block(s: pd.Series, freq_alias: str) -> pd.Series:
    """
    최근 연속된 무결측 구간 추출
    """
    if s.dropna().empty:
        return s.iloc[0:0]

    idx_set = set(s.index)
    cur = s.last_valid_index()
    if pd.isna(cur):
        return s.iloc[0:0]

    collected = [cur]
    while True:
        nxt = _prev_date(cur, freq_alias)
        if nxt not in idx_set:
            break
        if pd.isna(s.loc[nxt]):
            break
        collected.append(nxt)
        cur = nxt

    collected = sorted(collected)
    return s.loc[collected]


def coerce_pivot_to_target_series_strict(
        pivot_df: pd.DataFrame,
        target_col: str,
        strict_no_nan: bool = True
) -> pd.DataFrame:
    """
    피벗 테이블에서 특정 컬럼 추출 및 검증
    최근 연속 무결측 구간만 사용
    """
    global data_miss_list

    df = pivot_df.copy()
    df.index.name = 'date'
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors='coerce')
    df = df[~df.index.isna()].sort_index()

    if target_col not in df.columns:
        raise KeyError(f"'{target_col}' 컬럼이 없습니다.")

    s = df[target_col].astype(float)

    freq_alias = infer_freq_alias(df.index)
    min_len = _min_required_len(freq_alias)

    # 기간말 영업일 -> 기간말 달력일 정규화 (Q/M 만 적용)
    s.index = normalize_period_end_index(s.index, freq_alias)
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep='last')]

    block = extract_recent_contiguous_block(s, freq_alias=freq_alias)

    if strict_no_nan and block.isna().any():
        data_miss_list.append({
            "ticker": target_col,
            "reason": "block contains NaN (unexpected)",
            "latest": str(s.last_valid_index()),
            "block_len": int(block.notna().sum()),
            "required_min": min_len,
            "freq": freq_alias
        })
        raise ValueError(f"[SKIP] '{target_col}' 연속 구간 내 NaN 존재")

    if len(block) < min_len:
        data_miss_list.append({
            "ticker": target_col,
            "reason": "insufficient_recent_contiguous_length",
            "latest": str(s.last_valid_index()),
            "block_len": int(len(block)),
            "required_min": min_len,
            "freq": freq_alias
        })
        raise ValueError(f"[SKIP] '{target_col}' 최근 연속 무결측 구간 길이 부족: {len(block)} < {min_len} ({freq_alias})")

    out = pd.DataFrame({"y": block})
    out.index.name = 'date'
    return out


# ==================== SARIMA 모델 ====================

@memory_check_decorator
def find_best_sarima_params(
        y_train: pd.Series,
        exog_train: Optional[pd.DataFrame] = None,
        seasonal_period: int = 12,
        p_values: Iterable[int] = (0, 1, 2),
        d_values: Iterable[int] = (0, 1),
        q_values: Iterable[int] = (0, 1, 2),
        P_values: Iterable[int] = (0, 1),
        D_values: Iterable[int] = (0, 1),
        Q_values: Iterable[int] = (0, 1),
        ic: str = "aic",
        max_order_sum: Optional[int] = 8,
        n_jobs: int = 1,
        return_leaderboard: bool = False,
        refit_best: bool = False,
        verbose: bool = False,
) -> Dict[str, Any]:
    """SARIMA 하이퍼파라미터 최적화"""

    y = y_train.astype(float).dropna()
    X = None if exog_train is None else exog_train.loc[y.index].astype(float)
    m = int(max(1, seasonal_period))

    ic = ic.lower()
    if ic not in {"aic", "aicc", "bic", "hqic"}:
        raise ValueError("ic must be one of {'aic','aicc','bic','hqic'}")

    def _compute_ic(res, which: str) -> float:
        if which == "aic":   return float(res.aic)
        if which == "bic":   return float(res.bic)
        if which == "hqic":  return float(res.hqic)
        n = len(y)
        k = len(res.params)
        aicc = float(res.aic) + (2 * k * (k + 1)) / max(n - k - 1, 1)
        return aicc

    candidates: list[Tuple[Tuple[int, int, int], Tuple[int, int, int, int]]] = []
    for p, d, q in product(p_values, d_values, q_values):
        for P, D, Q in product(P_values, D_values, Q_values):
            if (max_order_sum is not None) and ((p + q + P + Q) > max_order_sum):
                continue
            candidates.append(((p, d, q), (P, D, Q, m)))

    if verbose:
        print(f"[SARIMA] candidates: {len(candidates)} (m={m}, ic={ic})")

    def _fit_one(spec) -> Optional[Tuple[float, Tuple[int, int, int], Tuple[int, int, int, int]]]:
        (order, sorder) = spec
        try:
            model = SARIMAX(
                y, exog=X,
                order=order, seasonal_order=sorder,
                enforce_stationarity=False, enforce_invertibility=False,
            )
            res = model.fit(disp=False)
            val = _compute_ic(res, ic)
            if np.isfinite(val):
                return (val, order, sorder)
        except Exception:
            return None
        return None

    results: list[Tuple[float, Tuple[int, int, int], Tuple[int, int, int, int]]] = []

    if n_jobs and n_jobs > 1:
        try:
            from joblib import Parallel, delayed
            results = [
                r for r in Parallel(n_jobs=n_jobs, prefer="processes")(
                    delayed(_fit_one)(spec) for spec in candidates
                ) if r is not None
            ]
        except Exception:
            if verbose: print("[SARIMA] joblib 병렬 실패, 순차 탐색으로 전환합니다.")
            for spec in candidates:
                r = _fit_one(spec)
                if r is not None:
                    results.append(r)
    else:
        for spec in candidates:
            r = _fit_one(spec)
            if r is not None:
                results.append(r)

    if not results:
        order = (0, 1, 1)
        sorder = (0, 1, 1, m)
        fallback_ic = np.inf
    else:
        results.sort(key=lambda x: x[0])
        fallback_ic, order, sorder = results[0]

    out = {
        "order": order,
        "seasonal_order": sorder,
        "ic_value": fallback_ic,
        "model": None,
        "leaderboard": None,
    }

    if return_leaderboard and results:
        df_lb = pd.DataFrame(results, columns=[ic.upper(), "order", "seasonal_order"])
        df_lb.sort_values(by=ic.upper(), inplace=True)
        out["leaderboard"] = df_lb

    if refit_best:
        try:
            model_best = SARIMAX(y, exog=X, order=order, seasonal_order=sorder,
                                 enforce_stationarity=False, enforce_invertibility=False)
            res_best = model_best.fit(disp=False)
            out["model"] = res_best
        except Exception as e:
            if verbose: print(f"[SARIMA] refit 실패: {e}")

    # 메모리 정리
    clear_memory()

    return out


@memory_check_decorator
def forecast_sarima(
        y: pd.Series,
        forecast_horizon: int,
        exog: Optional[pd.DataFrame] = None,
        seasonal_period: int = 12,
        try_transforms: bool = True,
        **grid_kwargs
) -> dict:
    """SARIMA 모델 예측
    - 양수 시계열 보호: log1p 변환 우선 적용 (음수/발산 방지)
    - 발산 감지: 예측값이 학습 데이터 범위의 100배 초과 시 실패 처리
    - 음수 클리핑: 시가총액 등 양수 시계열에서 음수 예측값 0으로 대체
    """

    y = y.astype(float).dropna()
    y_max = float(y.max())
    y_min = float(y[y > 0].min()) if (y > 0).any() else 0.0
    all_positive = bool((y > 0).all())

    def _is_diverged(fc_arr: np.ndarray) -> bool:
        """예측값이 학습 범위 100배 초과하거나 NaN/inf 포함 시 발산으로 판단"""
        if not np.all(np.isfinite(fc_arr)):
            return True
        if y_max > 0 and np.any(np.abs(fc_arr) > y_max * 100):
            return True
        return False

    def _clip_forecast(fc_arr: np.ndarray) -> np.ndarray:
        """양수 시계열이면 음수값을 y_min 으로 클리핑"""
        if all_positive:
            fc_arr = np.clip(fc_arr, a_min=max(y_min * 0.01, 0.0), a_max=None)
        return fc_arr

    def _fit_and_forecast(series, X_train=None):
        params = find_best_sarima_params(
            series, exog_train=X_train,
            seasonal_period=seasonal_period,
            refit_best=True, verbose=False,
            **grid_kwargs
        )
        res = params["model"]
        if res is None:
            raise ValueError("SARIMA fit failed")
        fc = res.forecast(steps=forecast_horizon, exog=None)
        return np.asarray(fc), params

    # 1순위: log1p 변환 적용 (양수 시계열 발산 방지에 가장 효과적)
    if all_positive:
        try:
            z = np.log1p(y)
            z_fc, params = _fit_and_forecast(pd.Series(z, index=y.index), exog)
            fc = np.expm1(z_fc)
            if not _is_diverged(fc):
                fc = _clip_forecast(fc)
                clear_memory()
                return {
                    "forecast": fc,
                    "spec": {
                        "order": params["order"],
                        "seasonal_order": params["seasonal_order"],
                        "ic_value": params["ic_value"]
                    },
                    "used_transform": ["log1p"]
                }
        except Exception:
            pass

    # 2순위: 원본 스케일 시도
    try:
        fc, params = _fit_and_forecast(y, exog)
        if not _is_diverged(fc):
            fc = _clip_forecast(fc)
            clear_memory()
            return {
                "forecast": fc,
                "spec": {
                    "order": params["order"],
                    "seasonal_order": params["seasonal_order"],
                    "ic_value": params["ic_value"]
                }
            }
    except Exception:
        pass

    clear_memory()
    return {"error": "SARIMA 적합 실패 (발산 포함)"}


# ==================== ETS 모델 ====================

@memory_check_decorator
def forecast_ets(
        y: pd.Series,
        forecast_horizon: int,
        m: int,
        try_transforms: bool = True
) -> dict:
    """ETS (Exponential Smoothing) 모델 예측
    - 양수 시계열 보호: log1p 변환 우선 적용
    - 발산 감지 및 음수 클리핑
    - mul 계절성 자동 시도 (add 실패 시)
    """

    y = y.dropna().astype(float)
    y_max = float(y.max())
    all_positive = bool((y > 0).all())
    y_min_pos = float(y[y > 0].min()) if all_positive else 0.0

    def _is_diverged(fc_arr: np.ndarray) -> bool:
        if not np.all(np.isfinite(fc_arr)):
            return True
        if y_max > 0 and np.any(np.abs(fc_arr) > y_max * 100):
            return True
        return False

    def _clip_forecast(fc_arr: np.ndarray) -> np.ndarray:
        if all_positive:
            fc_arr = np.clip(fc_arr, a_min=max(y_min_pos * 0.01, 0.0), a_max=None)
        return fc_arr

    def _fit_and_forecast(series: pd.Series) -> np.ndarray:
        # add 계절성 먼저 시도
        for seasonal in (['add', 'mul'] if m > 1 else [None]):
            try:
                model = ExponentialSmoothing(
                    series,
                    seasonal_periods=max(1, int(m)),
                    trend='add',
                    seasonal=seasonal,
                    initialization_method='estimated'
                )
                res = model.fit(optimized=True)
                fc = np.asarray(res.forecast(forecast_horizon))
                if np.all(np.isfinite(fc)):
                    return fc
            except Exception:
                continue
        raise ValueError("ETS 모든 seasonal 옵션 실패")

    # 1순위: log1p 변환 (양수 시계열)
    if all_positive:
        try:
            z = np.log1p(y)
            z_fc = _fit_and_forecast(pd.Series(z, index=y.index))
            fc = np.expm1(z_fc)
            if not _is_diverged(fc):
                fc = _clip_forecast(fc)
                clear_memory()
                return {
                    "forecast": fc,
                    "spec": {"seasonal_periods": int(m), "trend": "add"},
                    "used_transform": ["log1p"]
                }
        except Exception:
            pass

    # 2순위: 원본 스케일
    try:
        fc = _fit_and_forecast(y)
        if not _is_diverged(fc):
            fc = _clip_forecast(fc)
            clear_memory()
            return {
                "forecast": fc,
                "spec": {"seasonal_periods": int(m), "trend": "add"}
            }
    except Exception as e:
        clear_memory()
        return {"error": f"ETS 적합 실패: {e}"}

    clear_memory()
    return {"error": "ETS 적합 실패 (발산 포함)"}


# ==================== Prophet 모델 ====================

@memory_check_decorator
def forecast_prophet(
        y: pd.Series,
        forecast_horizon: int,
        m: int,
        try_transforms: bool = True
) -> dict:
    """Prophet 모델 예측"""

    if not _HAS_PROPHET:
        return {"error": "Prophet 미설치"}

    y = y.dropna().astype(float)

    def _fit_and_forecast(series: pd.Series) -> np.ndarray:
        df_train = pd.DataFrame({
            "ds": series.index,
            "y": series.values
        })
        model = Prophet(
            seasonality_mode='multiplicative' if m > 1 else 'additive',
            daily_seasonality=False,
            weekly_seasonality=False,
            yearly_seasonality=(m == 12)
        )
        model.fit(df_train)

        future = model.make_future_dataframe(periods=forecast_horizon, freq=pd.infer_freq(series.index) or 'M')
        forecast = model.predict(future)
        fc = forecast.tail(forecast_horizon)["yhat"].values
        return np.asarray(fc)

    try:
        fc = _fit_and_forecast(y)
        clear_memory()
        return {
            "forecast": fc,
            "spec": {"seasonality_mode": "multiplicative" if m > 1 else "additive"}
        }
    except Exception:
        pass

    if try_transforms:
        try:
            z = np.log1p(y)
            z_fc = _fit_and_forecast(pd.Series(z, index=y.index))
            fc = np.expm1(z_fc)
            clear_memory()
            return {
                "forecast": fc,
                "spec": {"seasonality_mode": "multiplicative" if m > 1 else "additive"},
                "used_transform": ["log1p"]
            }
        except Exception as e:
            clear_memory()
            return {"error": f"Prophet 적합 실패: {e}"}

    clear_memory()
    return {"error": "Prophet 적합 실패"}


# ==================== LSTM 모델 ====================

@memory_check_decorator
def forecast_lstm(
        y: pd.Series,
        forecast_horizon: int,
        lookback: int = 12,
        epochs: int = 50,
        batch_size: int = 16,
        try_transforms: bool = True
) -> dict:
    """LSTM 모델 예측"""

    if not _HAS_TF:
        return {"error": "TensorFlow 미설치"}

    y = y.dropna().astype(float).values.reshape(-1, 1)

    def _fit_and_forecast(data: np.ndarray) -> np.ndarray:
        scaler = MinMaxScaler()
        scaled = scaler.fit_transform(data)

        X, Y = [], []
        for i in range(lookback, len(scaled)):
            X.append(scaled[i - lookback:i, 0])
            Y.append(scaled[i, 0])
        X, Y = np.array(X), np.array(Y)
        X = X.reshape((X.shape[0], X.shape[1], 1))

        model = Sequential([
            LSTM(50, activation='relu', return_sequences=True, input_shape=(lookback, 1)),
            LSTM(50, activation='relu'),
            Dense(1)
        ])
        model.compile(optimizer='adam', loss='mse')

        early_stop = EarlyStopping(monitor='loss', patience=5, restore_best_weights=True)
        model.fit(X, Y, epochs=epochs, batch_size=batch_size, verbose=0, callbacks=[early_stop])

        fc_list = []
        last_seq = scaled[-lookback:].reshape(1, lookback, 1)
        for _ in range(forecast_horizon):
            pred = model.predict(last_seq, verbose=0)
            fc_list.append(pred[0, 0])
            last_seq = np.append(last_seq[:, 1:, :], pred.reshape(1, 1, 1), axis=1)

        fc_scaled = np.array(fc_list).reshape(-1, 1)
        fc = scaler.inverse_transform(fc_scaled).flatten()

        # 모델 메모리 해제
        del model
        tf.keras.backend.clear_session()

        return fc

    try:
        fc = _fit_and_forecast(y)
        clear_memory()
        return {
            "forecast": fc,
            "spec": {"lookback": lookback, "epochs": epochs, "batch_size": batch_size}
        }
    except Exception:
        pass

    if try_transforms:
        try:
            z = np.log1p(y)
            z_fc = _fit_and_forecast(z)
            fc = np.expm1(z_fc)
            clear_memory()
            return {
                "forecast": fc,
                "spec": {"lookback": lookback, "epochs": epochs, "batch_size": batch_size},
                "used_transform": ["log1p"]
            }
        except Exception as e:
            clear_memory()
            return {"error": f"LSTM 적합 실패: {e}"}

    clear_memory()
    return {"error": "LSTM 적합 실패"}


# ==================== Theta 모델 ====================

@memory_check_decorator
def forecast_theta(
        y: pd.Series,
        forecast_horizon: int,
        m: int,
        try_transforms: bool = True,
) -> dict:
    """Theta 모델 예측
    - 양수 시계열 보호: log1p 변환 우선 적용
    - 발산 감지 및 음수 클리핑
    """

    if not _HAS_THETA:
        return {"error": "ThetaModel 미설치 (statsmodels>=0.13 필요)"}

    y = y.dropna().astype(float)
    y_max = float(y.max())
    all_positive = bool((y > 0).all())
    y_min_pos = float(y[y > 0].min()) if all_positive else 0.0

    def _is_diverged(fc_arr: np.ndarray) -> bool:
        if not np.all(np.isfinite(fc_arr)):
            return True
        if y_max > 0 and np.any(np.abs(fc_arr) > y_max * 100):
            return True
        return False

    def _clip_forecast(fc_arr: np.ndarray) -> np.ndarray:
        if all_positive:
            fc_arr = np.clip(fc_arr, a_min=max(y_min_pos * 0.01, 0.0), a_max=None)
        return fc_arr

    def _fit_and_forecast(series: pd.Series) -> np.ndarray:
        tm = ThetaModel(
            series,
            period=max(1, int(m)),
            deseasonalize=(m > 1)
        )
        res = tm.fit()
        fc = res.forecast(forecast_horizon)
        return np.asarray(fc)

    # 1순위: log1p 변환 (양수 시계열)
    if all_positive:
        try:
            z = np.log1p(y)
            z_fc = _fit_and_forecast(pd.Series(z, index=y.index))
            fc = np.expm1(z_fc)
            if not _is_diverged(fc):
                fc = _clip_forecast(fc)
                clear_memory()
                return {
                    "forecast": fc,
                    "spec": {"period": int(m), "deseasonalize": bool(m > 1)},
                    "used_transform": ["log1p"],
                }
        except Exception:
            pass

    # 2순위: 원본 스케일
    try:
        fc = _fit_and_forecast(y)
        if not _is_diverged(fc):
            fc = _clip_forecast(fc)
            clear_memory()
            return {
                "forecast": fc,
                "spec": {"period": int(m), "deseasonalize": bool(m > 1)},
            }
    except Exception as e:
        clear_memory()
        return {"error": f"Theta 적합 실패: {e}"}

    clear_memory()
    return {"error": "Theta 적합 실패 (발산 포함)"}


# ==================== 통합 예측 함수 ====================

@memory_check_decorator
def forecast_one_from_pivot_inline(
        pivot_df: pd.DataFrame,
        target_col: str,
        horizon: int,
        models: list = None,
        strict_no_nan: bool = True,
        **model_kwargs
) -> dict:
    """
    통합 예측 함수 - 여러 모델을 사용하여 예측 수행

    Parameters:
    -----------
    pivot_df : pd.DataFrame
        피벗 테이블 (index=date, columns=tickers)
    target_col : str
        예측할 대상 컬럼명 (티커)
    horizon : int
        예측 기간
    models : list
        사용할 모델 리스트 (기본값: ["SARIMA", "ETS", "Prophet", "LSTM", "Theta"])
    strict_no_nan : bool
        결측값 엄격 검증 여부
    **model_kwargs
        각 모델에 전달할 추가 파라미터

    Returns:
    --------
    dict : 각 모델별 예측 결과
    """

    if models is None:
        models = ["SARIMA", "ETS", "Prophet", "LSTM", "Theta"]

    # 메모리 모니터링
    monitor_memory_usage(threshold_mb=2000)

    try:
        df_target = coerce_pivot_to_target_series_strict(pivot_df, target_col, strict_no_nan)
    except Exception as e:
        return {"error": str(e)}

    freq = infer_freq_alias(df_target.index)
    m = seasonal_periods_from_freq(freq)
    y = df_target["y"]

    result = {}

    for model_name in models:
        print(f"\n[예측 중] {model_name} 모델...")

        if model_name == "SARIMA":
            result[model_name] = forecast_sarima(
                y, horizon, seasonal_period=m, **model_kwargs.get("SARIMA", {})
            )
        elif model_name == "ETS":
            result[model_name] = forecast_ets(
                y, horizon, m=m, **model_kwargs.get("ETS", {})
            )
        elif model_name == "Prophet":
            result[model_name] = forecast_prophet(
                y, horizon, m=m, **model_kwargs.get("Prophet", {})
            )
        elif model_name == "LSTM":
            result[model_name] = forecast_lstm(
                y, horizon, **model_kwargs.get("LSTM", {})
            )
        elif model_name == "Theta":
            result[model_name] = forecast_theta(
                y, horizon, m=m, **model_kwargs.get("Theta", {})
            )

        # 각 모델 실행 후 메모리 정리
        clear_memory()
        monitor_memory_usage(threshold_mb=2000)

    return result


if __name__ == "__main__":
    print("예측 함수 모듈이 로드되었습니다.")
    print(f"사용 가능한 모델: SARIMA, ETS, Prophet={'?' if _HAS_PROPHET else '?'}, "
          f"LSTM={'?' if _HAS_TF else '?'}, Theta={'?' if _HAS_THETA else '?'}")