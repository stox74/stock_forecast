# -*- coding: utf-8 -*-
"""벤치마크 B1~B3 (2a 명세서 v3 5절).

입력 g_hist 는 기준일 t 에 알던 분기 성장률(g = ln R_q - ln R_{q-4})로, 예측 분기 q 이전이고
제외 분기(4분기 확인 사례, 합병 영향, excovid 변형이면 코로나 기간)를 뺀 것이다.
- B1: g = 0 (전년 동기 매출 그대로, 계절 랜덤워크)
- B2: g = 가장 최근 분기의 g (직전 성장률 지속)
- B3: g = 최근 4분기 g 평균 (드리프트 있는 계절 랜덤워크)
g_se (참고용): B1 은 과거 g 의 표준편차, B2 는 연속 분기 g 차이의 표준편차, B3 는 g 와 그 직전 4분기 평균 차이의 표준편차.
n_train 은 예측에 쓴 과거 분기 수 (B1 0, B2 1, B3 4).
"""
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd


def _std(s: pd.Series) -> Optional[float]:
    s = s.dropna()
    return float(s.std(ddof=1)) if len(s) >= 2 else None


def b2_lag(g_hist: pd.Series) -> Optional[Tuple[pd.Timestamp, float]]:
    """B2 가 쓰는 (분기, 성장률): g_hist 중 가장 최근 분기. M4 의 g_lag 도 이 함수로 정한다."""
    h = g_hist.dropna().sort_index()
    if h.empty:
        return None
    return h.index[-1], float(h.iloc[-1])


def benchmarks(g_hist: pd.Series) -> Dict[str, Dict]:
    """반환: {"B1": {...}, "B2": {...}, "B3": {...}} (계산할 수 없는 벤치마크는 빠짐).
    각 값: g_hat, g_se, n_train, train_quarters"""
    h = g_hist.dropna().sort_index()
    out = {"B1": {"g_hat": 0.0, "g_se": _std(h), "n_train": 0, "train_quarters": []}}
    lag = b2_lag(h)
    if lag is not None:
        out["B2"] = {"g_hat": lag[1], "g_se": _std(h.diff()), "n_train": 1,
                     "train_quarters": [lag[0]]}
    if len(h) >= 4:
        dev = h - h.rolling(4).mean().shift(1)
        out["B3"] = {"g_hat": float(h.iloc[-4:].mean()), "g_se": _std(dev), "n_train": 4,
                     "train_quarters": list(h.index[-4:])}
    return out
