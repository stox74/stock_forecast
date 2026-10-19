# -*- coding: utf-8 -*-
"""2d 작업 7: 매출 예측 방법 v2 (사전 등록 v2 1절, 커밋 51c6991). 분기 매출 시계열 세 모형.

입력 R: 기준일에 아는 분기 매출 (index 분기말). 예측 대상 분기 q, 전년 동기 매출 base.
- 학습 구간: radar.ts.training_series (q 이전 마지막 연속·양수 구간, 로그). 12분기 미만이면 그 모형은 결측.
- SARIMA: 계절 주기 4, 후보 8개 중 AICc 최소 (CANDIDATES). 적합 실패한 후보는 건너뛴다.
- ETS: 감쇠 가법 추세 + 가법 계절 4 (radar.ts.ets_forecast 와 같음).
- Theta: statsmodels ThetaModel, 주기 4 계절 조정 후 가법 (로그 값이 음수일 수 있어 가법).
반환 g = 로그 예측 - ln(base) (전년 동기 대비 로그 성장률).
조합 (E5, E4, E5_noM1) 은 combine() 에서 로그 g 평균 = 매출 기하평균.
"""
import warnings
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..radar import ts as rts

MIN_TRAIN = 12
SEASON = 4
# (order, seasonal_order(P,D,Q), trend)
CANDIDATES = [
    ((0, 1, 1), (0, 1, 1), None),   # airline
    ((1, 1, 0), (0, 1, 1), None),
    ((1, 1, 1), (0, 1, 1), None),
    ((0, 1, 0), (0, 1, 1), None),
    ((1, 0, 0), (0, 1, 1), "c"),
    ((2, 0, 0), (0, 1, 1), "c"),
    ((1, 0, 1), (0, 1, 1), "c"),
    ((0, 1, 1), (1, 1, 0), None),
]


def sarima_forecast(y: np.ndarray, h: int) -> (Optional[float], Optional[str]):
    best = None
    for order, so, trend in CANDIDATES:
        try:
            m, aicc = rts._fit(y, order, so, trend)
        except Exception:      # noqa: BLE001  적합 실패 후보는 건너뜀
            continue
        if np.isfinite(aicc) and (best is None or aicc < best[0]):
            best = (aicc, m, order, so, trend)
    if best is None:
        return None, None
    try:
        f = float(np.asarray(best[1].forecast(h))[-1])
    except Exception:          # noqa: BLE001
        return None, None
    o, s, tr = best[2], best[3], best[4]
    return (f if np.isfinite(f) else None), f"{o}{s}{'+c' if tr else ''}"


def theta_forecast(y: np.ndarray, h: int) -> Optional[float]:
    from statsmodels.tsa.forecasting.theta import ThetaModel
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = ThetaModel(y, period=SEASON, deseasonalize=True, use_test=False, method="additive").fit()
            f = float(np.asarray(m.forecast(h))[-1])
        return f if np.isfinite(f) else None
    except Exception:          # noqa: BLE001
        return None


def ts3(R: pd.Series, q: pd.Timestamp, base: float) -> Dict:
    """g_SARIMA, g_ETS, g_Theta, sarima_order, n_train."""
    out = {"g_SARIMA": None, "g_ETS": None, "g_Theta": None, "sarima_order": None, "ts3_n_train": 0}
    y = rts.training_series(R, q)
    out["ts3_n_train"] = len(y)
    if len(y) < MIN_TRAIN or base is None or not base > 0:
        return out
    h = rts.horizon(y.index.max(), q)
    v = y.values
    lb = float(np.log(base))
    fs, order = sarima_forecast(v, h)
    fe = rts.ets_forecast(v, h)
    ft_ = theta_forecast(v, h)
    out.update({"g_SARIMA": None if fs is None else fs - lb, "g_ETS": None if fe is None else fe - lb,
                "g_Theta": None if ft_ is None else ft_ - lb, "sarima_order": order})
    return out


def _mean(vals) -> Optional[float]:
    v = [x for x in vals if x is not None and pd.notna(x)]
    return float(np.mean(v)) if v else None


def combine(g_sarima, g_ets, g_theta, g_m1, g_b2, driver: bool) -> Dict:
    ts = [g_sarima, g_ets, g_theta]
    m1 = [g_m1] if driver else []
    return {"g_E5": _mean(ts + m1 + [g_b2]), "g_E4": _mean(ts + m1), "g_E5_noM1": _mean(ts + [g_b2]),
            "e5_n": sum(1 for x in ts + m1 + [g_b2] if x is not None and pd.notna(x))}
