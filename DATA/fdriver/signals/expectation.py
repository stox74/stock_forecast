# -*- coding: utf-8 -*-
"""기대값과 분모 (2a 명세서 6절, 8절 기대값 교체 구조).

분모 (delta, sigma)
  기준일 t 에 알던 매출(vintage -> as_of(t) -> latest_known)로, 분기 q 직전 8분기 j = q-8 .. q-1 의
  D_j = Rev_j - Rev_{j-4} (log 변형은 g_j = ln Rev_j - ln Rev_{j-4}) 평균과 표준편차(ddof=1).
  8분기 중 6분기 이상 계산될 때만 쓴다. 추정·평가 제외 분기(4분기 확인 사례, 기업 사건)는 D_j 에서 뺀다.
  j 와 j-4 가 모두 t 까지 발표돼 있어야 한다 (q 이전 발표 분기만 사용).

expectation(entity_id, quarter, t, kind)
  kind="ts":        level 기대 매출 = Rev_{q-4} + delta, log 기대 성장률 = delta (g 평균)
  kind="consensus": t 시점 컨센서스 평균 (read_consensus). log 는 ln(평균 / Rev_{q-4}).
                    TODO(2b): 컨센서스 분모(범위 또는 과거 컨센서스 오차의 표준편차)는 2b 에서 정한다.
                    지금은 ts 분모(sigma)를 그대로 쓴다.
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..adapters.consensus import read_consensus
from ..events import fiscal_quarter_label
from ..nowcast import features as ft

WINDOW = 8
MIN_HIST = 6
VARIANTS = ("level", "log")


class Context:
    """기업 단위 캐시: 매출 vintage, 제외 분기, 결산월."""

    def __init__(self, entity_id: str, rev_v: Optional[pd.DataFrame] = None, fye: int = 12):
        self.entity_id = entity_id
        self.rev_v = ft.revenue_frame(entity_id) if rev_v is None else rev_v
        self.excl = ft.excluded_quarters(entity_id, self.rev_v)
        self.fye = fye
        self._known: Dict[pd.Timestamp, pd.Series] = {}

    def known(self, t) -> pd.Series:
        t = pd.Timestamp(t)
        if t not in self._known:
            self._known[t] = ft.revenue_known(self.rev_v, t)
        return self._known[t]


def denominators(ctx: Context, q: pd.Timestamp, t, variant: str = "level",
                 window: int = WINDOW, min_hist: int = MIN_HIST) -> Dict:
    """delta, sigma, n_hist, hist_quarters, max_available (사용한 매출 값의 최대 available_at)."""
    R = ctx.known(t)
    vals, used = {}, []
    for k in range(window, 0, -1):
        j = ft.quarter_shift(q, k)
        b = ft.quarter_shift(j, 4)
        if j in ctx.excl or j not in R.index or b not in R.index:
            continue
        if variant == "level":
            vals[j] = float(R[j] - R[b])
        else:
            if not (R[j] > 0 and R[b] > 0):
                continue
            vals[j] = float(np.log(R[j]) - np.log(R[b]))
        used += [j, b]
    s = pd.Series(vals, dtype="float64")
    out = {"delta": np.nan, "sigma": np.nan, "n_hist": len(s), "hist_quarters": list(s.index),
           "max_available": None}
    if used:
        k = ft.known_at(ctx.rev_v, t)
        out["max_available"] = k.loc[k["period_end"].isin(used), "available_at"].max()
    if len(s) >= min_hist:
        sd = float(s.std(ddof=1))
        out["delta"] = float(s.mean())
        out["sigma"] = sd if sd > 0 else np.nan
    return out


def expectation(entity_id: str, quarter: pd.Timestamp, t, kind: str = "ts", variant: str = "level",
                ctx: Optional[Context] = None) -> Dict:
    """기대값. 반환: expected (level: 매출, log: 성장률), base (t 시점 Rev_{q-4}), delta, sigma, n_hist, kind."""
    ctx = ctx or Context(entity_id)
    q = pd.Timestamp(quarter)
    den = denominators(ctx, q, t, variant)
    R = ctx.known(t)
    base = R.get(ft.quarter_shift(q, 4))
    out = {**den, "base": None if base is None else float(base), "kind": kind, "expected": np.nan}
    if base is None or not base > 0:
        return out
    if kind == "ts":
        out["expected"] = base + den["delta"] if variant == "level" else den["delta"]
    elif kind == "consensus":
        c = read_consensus([entity_id], "revenue", asof=t)
        c = c[c["fiscal_quarter"] == fiscal_quarter_label(q, ctx.fye)]
        if c.empty or not c["mean"].iloc[0] > 0:
            return out
        m = float(c["mean"].iloc[0])
        out["expected"] = m if variant == "level" else float(np.log(m) - np.log(base))
        out["consensus_asof"] = c["asof_date"].iloc[0]
        # TODO(2b): 컨센서스 분모. 지금은 ts 분모(sigma)를 그대로 쓴다.
    else:
        raise ValueError(f"unknown expectation kind: {kind}")
    return out


def surprise(value: float, exp: Dict, variant: str = "level") -> float:
    """(값 - 기대) / sigma. level: value 는 매출, log: value 는 매출 (내부에서 ln(value/base))."""
    if value is None or pd.isna(value) or pd.isna(exp.get("expected")) or pd.isna(exp.get("sigma")):
        return np.nan
    if variant == "level":
        return float((value - exp["expected"]) / exp["sigma"])
    if not value > 0:
        return np.nan
    return float((np.log(value) - np.log(exp["base"]) - exp["expected"]) / exp["sigma"])
