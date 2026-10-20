# -*- coding: utf-8 -*-
"""2g 참고 예측 (method_2g_ref_v1 2절, 커밋 72209e4): M1_ref, SARIMAX_ref, 미래 드라이버 값, E5_ref, E6_ref.
2g 주 예측(E5)과 2d 는 건드리지 않는다. 이 모듈의 값은 모두 참고 열이다.
"""
import warnings
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import db
from ..eval2d import k4_forecast as K4
from ..eval2d import k7_methods as k7
from ..nowcast import features as ft
from ..nowcast import models as md
from ..radar import ts as rts

MIN_TRAIN = 12
FC_TABLE = "korea_monthly_trade_forecast_v2"
FC_IND = "ensemble_expDlr"


class Driver:
    """드라이버 하나의 원자료 (작업 프로세스 안에서 읽는다)."""

    def __init__(self, code: str):
        raw, kind = K4.driver_raw(code)
        self.code, self.kind = code, kind
        self.drv = ft.prepare_driver(raw)
        self.spec = ft.DriverSpec(model_id="M1:" + code, driver_key=code, kind=kind, lag=0, loader=lambda: raw)
        self.is_export = ":" not in code
        self._fc = None

    def stored_fc(self, t: pd.Timestamp) -> pd.Series:
        """저장된 전국 수출 예측: forecast_date <= t 중 가장 늦은 것의 ensemble 월 값 (index 월말)."""
        if not self.is_export:
            return pd.Series(dtype=float)
        if self._fc is None:
            self._fc = db.read_sql(f"SELECT date, value, forecast_date FROM `{FC_TABLE}` WHERE hs_code = :c AND "
                                   f"indicator = :i", {"c": self.code, "i": FC_IND})
            self._fc["date"] = pd.to_datetime(self._fc["date"])
            self._fc["forecast_date"] = pd.to_datetime(self._fc["forecast_date"])
        f = self._fc[self._fc["forecast_date"] <= t]
        if f.empty:
            return pd.Series(dtype=float)
        f = f[f["forecast_date"] == f["forecast_date"].max()]
        return f.set_index("date")["value"].astype(float)


# ------------------------------------------------------------------ M1_ref
def m1_ref(d: Driver, R: pd.Series, q: pd.Timestamp, t: pd.Timestamp) -> Dict:
    """2d k4_forecast 의 M1 과 같은 계산 (기준일에 공표된 달만)."""
    g_hist = ft.growth(R)
    kn = ft.known_at(d.drv, t)
    f = ft.target_feature(d.spec, d.drv, q, t, known=kn)
    out = {"g_M1_ref": None, "m1_ref_cov": f["coverage"]}
    if f["x"] is None:
        return out
    xtr = ft.training_features(d.spec, d.drv, list(g_hist.index), t, f["positions"], known=kn)
    res = md.ols_predict(xtr, g_hist, f["x"])
    if "g_hat" in res:
        out["g_M1_ref"] = float(res["g_hat"])
    return out


# ------------------------------------------------------------------ 드라이버 분기 값
def _months(q: pd.Timestamp) -> List[pd.Timestamp]:
    return list(pd.date_range(q - pd.offsets.MonthEnd(2), q, freq="M"))


def hist_x(kn: pd.DataFrame, kind: str, quarters) -> Dict[pd.Timestamp, float]:
    out = {}
    for j in quarters:
        v = ft._level(kn, j, kind, [0, 1, 2])
        if v is not None and v > 0:
            out[j] = float(np.log(v))
    return out


def future_x(d: Driver, kn: pd.DataFrame, q: pd.Timestamp, t: pd.Timestamp) -> (Optional[float], str):
    """예측 분기 q 의 드라이버 로그 수준과 출처."""
    if d.kind == "D":
        g = kn[kn["qend"] == q]
        if len(g):
            return float(np.log(g["value"].mean())), "known_mean"
        last = kn[pd.to_datetime(kn["period_end"]) > t - pd.Timedelta(days=30)]
        if last.empty or not last["value"].mean() > 0:
            return None, "none"
        return float(np.log(last["value"].mean())), "last30d"
    s = kn.groupby("period_end")["value"].sum()
    months = _months(q)
    have = {m: float(s[m]) for m in months if m in s.index}
    miss = [m for m in months if m not in have]
    if not miss:
        return (float(np.log(sum(have.values()))) if sum(have.values()) > 0 else None), "known"
    fc = d.stored_fc(t)
    if len(fc) and all(m in fc.index for m in miss):
        v = sum(have.values()) + float(sum(fc[m] for m in miss))
        return (float(np.log(v)) if v > 0 else None), ("known+stored_fc" if have else "stored_fc")
    kf, n = K4.fill_missing_months(kn, q, t)
    if not n:
        return None, "fill_fail"
    s2 = kf.groupby("period_end")["value"].sum()
    if not all(m in s2.index for m in months):
        return None, "fill_fail"
    v = float(sum(s2[m] for m in months))
    return (float(np.log(v)) if v > 0 else None), ("known+fill" if have else "fill")


# ------------------------------------------------------------------ SARIMAX_ref
def _fit_x(y, x, order, so, trend):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = SARIMAX(y, exog=x, order=order, seasonal_order=(*so, k7.SEASON), trend=trend,
                    enforce_stationarity=True, enforce_invertibility=True).fit(disp=False, maxiter=200)
    k = len(m.params)
    aicc = m.aic + (2 * k * (k + 1)) / max(m.nobs - k - 1, 1)
    return m, aicc


def order_from_label(label: str):
    for o, so, tr in k7.CANDIDATES:
        if f"{o}{so}{'+c' if tr else ''}" == label:
            return [(o, so, tr)]
    return None


def sarimax_ref(d: Driver, R: pd.Series, targets: List[pd.Timestamp], t: pd.Timestamp, candidates=None) -> Dict:
    """R: 기준일에 아는 분기 매출. targets: 예측 분기들 (오름차순). 반환: {q: {g_log_level, x_src}}, order, n_train."""
    y = rts.training_series(R, targets[0])
    kn = ft.known_at(d.drv, t)
    xh = hist_x(kn, d.kind, list(y.index))
    # x 가 있는 마지막 연속 구간
    keep = []
    for j in reversed(list(y.index)):
        if j in xh:
            keep.append(j)
        else:
            break
    keep = sorted(keep)
    out = {"order": None, "n_train": len(keep), "per_q": {q: {"lvl": None, "x_src": None} for q in targets}}
    if len(keep) < MIN_TRAIN:
        return out
    last = keep[-1]
    path, srcs = [], {}
    qq = ft.quarter_shift(last, -1)
    while qq <= targets[-1]:
        if qq in xh and qq <= last:
            v, src = xh[qq], "known"
        else:
            v, src = future_x(d, kn, qq, t)
        path.append(v)
        srcs[qq] = src
        qq = ft.quarter_shift(qq, -1)
    for q in targets:
        out["per_q"][q]["x_src"] = srcs.get(q)
    if any(v is None for v in path):
        return out
    yv = y[keep].to_numpy(float)
    xv = np.array([xh[j] for j in keep], dtype=float).reshape(-1, 1)
    best = None
    for o, so, tr in (candidates or k7.CANDIDATES):
        try:
            m, aicc = _fit_x(yv, xv, o, so, tr)
        except Exception:      # noqa: BLE001
            continue
        if np.isfinite(aicc) and (best is None or aicc < best[0]):
            best = (aicc, m, f"{o}{so}{'+c' if tr else ''}")
    if best is None:
        return out
    try:
        fc = np.asarray(best[1].forecast(len(path), exog=np.array(path, dtype=float).reshape(-1, 1)))
    except Exception:          # noqa: BLE001
        return out
    out["order"] = best[2]
    for q in targets:
        h = rts.horizon(last, q)
        v = float(fc[h - 1]) if 1 <= h <= len(fc) else None
        out["per_q"][q]["lvl"] = v if v is not None and np.isfinite(v) else None
    return out


# ------------------------------------------------------------------ 조합
def combine_ref(rec: dict, g_m1, g_sx) -> Dict:
    base = rec.get("rev_base")
    lvl = (lambda g: float(base * np.exp(g)) if g is not None and pd.notna(g) and base else None)
    core = [rec.get(f"g_{k}") for k in ("SARIMA", "ETS", "Theta", "B2")]
    core = [g for g in core if g is not None and pd.notna(g)]
    out = {"g_M1_ref": g_m1, "M1_ref": lvl(g_m1), "g_SARIMAX_ref": g_sx, "SARIMAX_ref": lvl(g_sx),
           "g_E5_ref": None, "E5_ref": None, "g_E6_ref": None, "E6_ref": None}
    if g_m1 is not None and core:
        out["g_E5_ref"] = float(np.mean(core + [g_m1]))
    if g_sx is not None and core:
        out["g_E6_ref"] = float(np.mean(core + ([g_m1] if g_m1 is not None else []) + [g_sx]))
    out["E5_ref"], out["E6_ref"] = lvl(out["g_E5_ref"]), lvl(out["g_E6_ref"])
    return out


def known_revenue(R_all: pd.Series, ann_e: pd.Series, cal, q: pd.Timestamp, t: pd.Timestamp) -> pd.Series:
    def ann_dt(j):
        v = ann_e.get(j)
        return pd.Timestamp(v) if v is not None and pd.notna(v) else K4.legal_date(j, cal)
    return R_all.reindex([j for j in R_all.index if j < q and ann_dt(j) <= t])


def firm_ref(code: str, recs: List[dict], R_all: pd.Series, ann_e: pd.Series, cal, t: pd.Timestamp,
             fixed_order: bool = False, d: Driver = None) -> List[dict]:
    """recs: 같은 기업의 예측 행 (분기 오름차순, rev_base·g_* 가 있는 것). 각 행에 참고 열을 더해 돌려준다.
    d: 이미 읽은 Driver (과거 구간 탐색에서 분기마다 다시 읽지 않도록)."""
    d = d or Driver(code)
    qs = [pd.Timestamp(r["period_end"]) for r in recs]
    R = known_revenue(R_all, ann_e, cal, qs[0], t)
    cands = order_from_label(recs[0].get("sarima_order")) if fixed_order else None
    sx = sarimax_ref(d, R, qs, t, candidates=cands)
    kn = ft.known_at(d.drv, t)
    last = pd.to_datetime(kn["period_end"]).max() if len(kn) else None
    stale = last is None or last < ft.quarter_shift(qs[0], 1) - pd.offsets.MonthEnd(2)
    note = ("drv_data_end " + (last.strftime("%Y-%m") if last is not None else "none")) if stale else None
    out = []
    for r, q in zip(recs, qs):
        m1 = m1_ref(d, R, q, t)
        base = r.get("rev_base")
        lv = sx["per_q"][q]["lvl"]
        g_sx = (lv - float(np.log(base))) if lv is not None and base else None
        r.update(combine_ref(r, m1["g_M1_ref"], g_sx))
        r.update({"sarimax_order_ref": sx["order"], "sarimax_n_train": sx["n_train"], "x_src": sx["per_q"][q]["x_src"],
                  "m1_ref_cov": m1["m1_ref_cov"], "ref_note": note})
        out.append(r)
    return out
