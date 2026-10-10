# -*- coding: utf-8 -*-
"""R1 (b): TS 예측 (분기 매출만)과 앙상블 E.

TS
- 입력: 기준일 t 에 공개돼 있던 분기 매출 (features.revenue_known: vintage -> as_of(t) -> latest_known, nowcast 와 같은 규칙).
- 예측 대상 분기 q 이전의 가장 최근 연속(빈 분기 없음)·양수 구간만 쓴다. 12분기 미만이면 결측.
- 로그 매출에 계절 ETS (가법 계절, 감쇠 가법 추세)와 계절 ARIMA (자동 차수) 를 각각 적합해 q 를 예측하고,
  두 로그 예측을 평균한다 (하나만 되면 그 하나, 표시). 성장률 g = 평균 로그 예측 - ln(Rev_{q-4}) (rev_base).
- 자동 ARIMA (pmdarima 가 없어 statsmodels 로 구현, Hyndman-Khandakar 단계적 탐색):
  D = 1 (분기 계절 차분) 고정, d 는 계절 차분한 계열의 KPSS 검정(5%)으로 0/1. p,q in 0..2, P,Q in 0..1,
  초기 4개 모형에서 출발해 한 차수씩 바꾸며 AICc 가 줄지 않을 때까지 탐색.
E
- E = (M1 + B2 + TS) / 3 (전년 대비 로그 성장률). 드라이버 2개 이상 기업은 M1 대신 M2, 선행 드라이버 1개 기업은
  그 단일 드라이버 모델(M3:x). 대조군(드라이버 없음)은 (B2 + TS) / 2.
- 구성 요소가 결측이면 남은 것들의 평균, drivers_used 에 'missing:<요소>' 표시. 가중치는 1/n 고정.
"""
import warnings
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..nowcast import features as ft

MIN_TRAIN = 12
SEASON = 4


# ---------------------------------------------------------------- 학습 구간
def training_series(R: pd.Series, q: pd.Timestamp) -> pd.Series:
    """q 이전 분기 중 마지막 공개 분기에서 거꾸로 이어지는 연속·양수 구간 (로그)."""
    r = R[R.index < q].sort_index()
    if r.empty:
        return pd.Series(dtype="float64")
    out = []
    cur = r.index.max()
    while cur in r.index and r[cur] > 0:
        out.append((cur, float(np.log(r[cur]))))
        cur = ft.quarter_shift(cur, 1)
    out.reverse()
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([k for k, _ in out]), dtype="float64")


def horizon(last: pd.Timestamp, q: pd.Timestamp) -> int:
    return int(round((q.year * 12 + q.month - last.year * 12 - last.month) / 3))


# ---------------------------------------------------------------- ETS
def ets_forecast(y: np.ndarray, h: int) -> Optional[float]:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = ExponentialSmoothing(y, trend="add", damped_trend=True, seasonal="add", seasonal_periods=SEASON,
                                     initialization_method="estimated").fit()
            f = float(m.forecast(h)[-1])
        return f if np.isfinite(f) else None
    except Exception:            # noqa: BLE001
        return None


# ---------------------------------------------------------------- 자동 ARIMA
def _kpss_d(y: np.ndarray) -> int:
    from statsmodels.tsa.stattools import kpss
    z = y[SEASON:] - y[:-SEASON]
    if len(z) < 8:
        return 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            p = kpss(z, regression="c", nlags="auto")[1]
        except Exception:        # noqa: BLE001
            return 0
    return 1 if p < 0.05 else 0


def _fit(y, order, sorder, trend):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = SARIMAX(y, order=order, seasonal_order=(*sorder, SEASON), trend=trend,
                    enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
    k = len(m.params)
    n = m.nobs
    aicc = m.aic + (2 * k * (k + 1)) / max(n - k - 1, 1)
    return m, aicc


def auto_arima_forecast(y: np.ndarray, h: int) -> Tuple[Optional[float], Optional[str]]:
    d, D = _kpss_d(y), 1
    trend = "c" if d == 0 else None          # d = 0, D = 1 이면 계절 차분 뒤 상수(추세) 허용
    cache: Dict[Tuple, Tuple] = {}

    def score(p, q, P, Q):
        key = (p, q, P, Q)
        if key not in cache:
            try:
                cache[key] = _fit(y, (p, d, q), (P, D, Q), trend)
            except Exception:    # noqa: BLE001
                cache[key] = (None, np.inf)
        return cache[key][1]

    starts = [(2, 2, 1, 1), (0, 0, 0, 0), (1, 0, 1, 0), (0, 1, 0, 1)]
    best = min(starts, key=lambda s: score(*s))
    improved = True
    while improved:
        improved = False
        p, q, P, Q = best
        for nb in [(p + a, q + b, P + c, Q + e) for a, b, c, e in
                   [(1, 0, 0, 0), (-1, 0, 0, 0), (0, 1, 0, 0), (0, -1, 0, 0), (0, 0, 1, 0), (0, 0, -1, 0),
                    (0, 0, 0, 1), (0, 0, 0, -1), (1, 1, 0, 0), (-1, -1, 0, 0)]]:
            if not (0 <= nb[0] <= 2 and 0 <= nb[1] <= 2 and 0 <= nb[2] <= 1 and 0 <= nb[3] <= 1):
                continue
            if score(*nb) < score(*best) - 1e-9:
                best, improved = nb, True
    m, s = cache[best]
    if m is None or not np.isfinite(s):
        return None, None
    try:
        f = float(np.asarray(m.forecast(h))[-1])
    except Exception:            # noqa: BLE001
        return None, None
    p, q, P, Q = best
    return (f if np.isfinite(f) else None), f"({p},{d},{q})({P},{D},{Q})"


# ---------------------------------------------------------------- TS 한 건
def ts_forecast(R: pd.Series, q: pd.Timestamp, base: float) -> Dict:
    """반환 g_hat (None 이면 결측), n_train, label, reason."""
    y = training_series(R, q)
    if len(y) < MIN_TRAIN:
        return {"g_hat": None, "n_train": len(y), "label": None, "reason": "lt_min_train"}
    if base is None or not base > 0:
        return {"g_hat": None, "n_train": len(y), "label": None, "reason": "no_base"}
    h = horizon(y.index.max(), q)
    v = y.values
    fe = ets_forecast(v, h)
    fa, order = auto_arima_forecast(v, h)
    parts = [x for x in (fe, fa) if x is not None]
    if not parts:
        return {"g_hat": None, "n_train": len(y), "label": None, "reason": "fit_failed"}
    label = "TS:" + "+".join(([f"ets"] if fe is not None else []) + ([f"arima{order}"] if fa is not None else []))
    if len(parts) == 1:
        label += ";one_model_only"
    return {"g_hat": float(np.mean(parts) - np.log(base)), "n_train": len(y), "label": label, "reason": None,
            "h": h}


# ---------------------------------------------------------------- E
def e_component(entity_id: str) -> Optional[str]:
    """E 의 드라이버 요소 model_id: 드라이버 2개 이상 M2, 1개면 그 모델(M1:x / M3:x), 없으면 None."""
    comps = [s.model_id for s in ft.DRIVER_SPECS.get(entity_id, []) if not s.aux and s.alt is None]
    if len(comps) >= 2:
        return "M2"
    return comps[0] if comps else None


def ensemble(g_drv: Optional[float], g_b2: Optional[float], g_ts: Optional[float], drv_model: Optional[str]) -> Dict:
    comps = {"B2": g_b2, "TS": g_ts}
    if drv_model is not None:
        comps = {drv_model: g_drv, **comps}
    have = {k: v for k, v in comps.items() if v is not None and pd.notna(v)}
    if not have:
        return {"g_hat": None, "label": None}
    miss = [k for k in comps if k not in have]
    label = "E:" + "+".join(have) + (f";missing:{','.join(miss)}" if miss else "")
    return {"g_hat": float(np.mean(list(have.values()))), "label": label, "n_components": len(have)}
