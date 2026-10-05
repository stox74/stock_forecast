# -*- coding: utf-8 -*-
"""nowcast 모델 M1, M2, M3 (2a 명세서 v3 4절).

- M1: 드라이버 하나, g = a + b * x + e, 확장 창 OLS (기준일 t 에 실적이 발표된 분기만, 최소 12분기)
- M3: M1 과 같은 식에 선행 드라이버(직전 분기 x)를 넣은 것 (x 를 만드는 쪽은 features.DriverSpec.lag)
- M2: M1·M3 예측들의 단순 평균 (보조 드라이버 제외). 기준일에 쓸 수 있는 드라이버 예측이 하나도 없으면
       벤치마크 B3 로 대체하고 drivers_used 에 'fallback:B3' 로 적는다 (명세서 4절 "벤치마크로 대체").
- M4 (v5): g_hat = g_lag + b * (x_q - x_lag). g_lag 는 B2 와 같은 값(benchmarks.b2_lag), x_lag 는 g_lag 와 같은
       분기의 드라이버 값(예측 분기와 같은 월 구성). b 는 원점 통과 확장 창 OLS, 종속 (g_j - g_lag(j)),
       설명 (x_j - x_lag(j)), 최소 12쌍. g_lag(j) 는 기준일 t 에 알던 성장률 중 j 보다 앞선 것에
       b2_lag 를 적용한 분기 (= j 를 예측할 때 B2 가 썼을 분기). b = 0 이면 정확히 B2.
예측 표준오차: 회귀 잔차 분산으로 계산한 예측 오차 sqrt(s^2 * (1 + x0 (X'X)^-1 x0')).
M2 의 표준오차는 구성 예측 표준오차 제곱의 평균의 제곱근 (참고용).
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .benchmarks import b2_lag

MIN_TRAIN = 12


def ols_predict(x_train: pd.Series, y_train: pd.Series, x0: Optional[float],
                min_train: int = MIN_TRAIN) -> Dict:
    """x_train, y_train 은 분기 인덱스. 둘 다 있는 분기로 추정.
    예측할 수 없으면 {'skipped': 'no_x' | 'lt_min_train', 'n_train': n} 를 돌려준다."""
    d = pd.concat([x_train.rename("x"), y_train.rename("y")], axis=1).dropna()
    n = len(d)
    if x0 is None or np.isnan(x0):
        return {"skipped": "no_x", "n_train": n, "train_quarters": list(d.index)}
    if n < min_train:
        return {"skipped": "lt_min_train", "n_train": n, "train_quarters": list(d.index)}
    X = np.column_stack([np.ones(n), d["x"].to_numpy()])
    y = d["y"].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = float(resid @ resid) / max(n - 2, 1)
    x0v = np.array([1.0, x0])
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
        se = float(np.sqrt(s2 * (1.0 + x0v @ xtx_inv @ x0v)))
    except np.linalg.LinAlgError:
        se = float(np.sqrt(s2))
    return {"g_hat": float(x0v @ beta), "g_se": se, "n_train": n, "a": float(beta[0]), "b": float(beta[1]),
            "train_quarters": list(d.index)}


def combine(preds: List[Dict]) -> Optional[Dict]:
    """M2: 예측 단순 평균."""
    ok = [p for p in preds if p and "g_hat" in p]
    if not ok:
        return None
    ses = [p["g_se"] for p in ok if p.get("g_se") is not None]
    return {"g_hat": float(np.mean([p["g_hat"] for p in ok])),
            "g_se": float(np.sqrt(np.mean(np.square(ses)))) if ses else None,
            "n_train": int(min(p["n_train"] for p in ok)), "n_models": len(ok)}


def ols_origin(x: np.ndarray, y: np.ndarray) -> Dict:
    """원점 통과 OLS y = b x + e. 반환 b, s2 (잔차 분산, 자유도 n-1), sxx, n."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    sxx = float(x @ x)
    if n == 0 or sxx <= 0:
        return {"b": np.nan, "s2": np.nan, "sxx": sxx, "n": n}
    b = float(x @ y) / sxx
    resid = y - b * x
    return {"b": b, "s2": float(resid @ resid) / max(n - 1, 1), "sxx": sxx, "n": n}


def m4_pairs(g_hist: pd.Series, x_hist: pd.Series) -> pd.DataFrame:
    """학습 쌍: j 마다 lag(j) = b2_lag(g_hist[< j]). dy = g_j - g_lag(j), dx = x_j - x_lag(j).
    x 가 둘 중 하나라도 없으면 그 쌍은 빠진다."""
    h = g_hist.dropna().sort_index()
    rows = []
    for j in h.index:
        lag = b2_lag(h[h.index < j])
        if lag is None:
            continue
        xj, xl = x_hist.get(j), x_hist.get(lag[0])
        if xj is None or xl is None or pd.isna(xj) or pd.isna(xl):
            continue
        rows.append({"q": j, "lag_q": lag[0], "dy": float(h[j] - lag[1]), "dx": float(xj - xl)})
    return pd.DataFrame(rows, columns=["q", "lag_q", "dy", "dx"]).set_index("q")


def m4_predict(g_hist: pd.Series, x_hist: pd.Series, x0: Optional[float],
               min_train: int = MIN_TRAIN, b_fixed: Optional[float] = None) -> Dict:
    """M4 예측. x_hist 는 과거 분기 드라이버 값(예측 분기와 같은 월 구성), x0 는 예측 분기 값.
    b_fixed 를 주면 추정하지 않고 그 값을 쓴다 (테스트용)."""
    lag = b2_lag(g_hist)
    pairs = m4_pairs(g_hist, x_hist)
    n = len(pairs)
    base = {"n_train": n, "train_quarters": sorted(set(pairs.index) | set(pairs["lag_q"]))}
    if lag is None:
        return {"skipped": "no_lag", **base}
    xl = x_hist.get(lag[0])
    if x0 is None or pd.isna(x0) or xl is None or pd.isna(xl):
        return {"skipped": "no_x", "lag_q": lag[0], **base}
    dx0 = float(x0 - xl)
    if b_fixed is not None:
        return {"g_hat": lag[1] + b_fixed * dx0, "g_se": None, "b": b_fixed, "lag_q": lag[0], "g_lag": lag[1],
                "x_lag": float(xl), "dx0": dx0, **base}
    if n < min_train:
        return {"skipped": "lt_min_train", "lag_q": lag[0], **base}
    r = ols_origin(pairs["dx"].to_numpy(), pairs["dy"].to_numpy())
    if np.isnan(r["b"]):
        return {"skipped": "degenerate", "lag_q": lag[0], **base}
    se = float(np.sqrt(r["s2"] * (1.0 + dx0 * dx0 / r["sxx"])))
    return {"g_hat": lag[1] + r["b"] * dx0, "g_se": se, "b": r["b"], "lag_q": lag[0], "g_lag": lag[1],
            "x_lag": float(xl), "dx0": dx0, **base}
