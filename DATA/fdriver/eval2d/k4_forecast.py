# -*- coding: utf-8 -*-
"""2d 작업 4: 평가 구간 매출 nowcast·영업이익 예측 (명세서 2d v3 3·5·6·7절). 쓰기는 fd_forecast_2d 만.

대상: fd_driver_map_2d status driver (드라이버 1개, 가중치 1) + ts_only. 영업이익 규칙은 fd_op_rule (excluded 제외).
평가 구간 2024Q1~2026Q2. 예측 시점은 발표 5영업일 전 (pre5) 하나. 실적(매출·영업이익)은 붙이지 않는다 (작업 5).

발표일 (first_dt): fd_earnings_event 첫 공시일 (events.first_disclosure: 잠정실적·정기보고서·4분기 손익구조변경 중 가장 이른
  것). 없으면 법정 기한 (분기말 +45일, 4분기 +90일, 휴장일이면 다음 거래일, announce_basis 'legal').
  pre5 = 첫 공시일 5거래일 전 (nowcast.run.checkpoints 와 같음).
기준일 t 에 아는 매출: fd_rev_adj_2d 분기 매출 중 그 분기 발표일 <= t 인 것 (값은 보정본 최종값, vintage 없음).

매출 예측 (전년 동기 대비 로그 성장률 g, 수준 = 전년 동기 매출 x exp(g))
- B2: 가장 최근 아는 분기의 g (nowcast.benchmarks).
- TS: radar.ts.ts_forecast (로그 매출에 ETS + 자동 계절 ARIMA, 평균).
- M1 (driver 만): 드라이버 1개 OLS g = a + b x (nowcast.models.ols_predict, 확장창, 최소 12분기).
  x = 예측 분기에 t 까지 공표된 달만 합한 로그 YoY, 학습 분기도 같은 달 구성 (nowcast.features).
  공표 시점: 수출 월말 +15일, KOSIS·관광 기존 규칙 (+35일 또는 공표일정), 한국은행 available_at, FRED·KOMIS 일별.
- E = 구성 요소 g 의 평균 (= 매출 기하평균). driver: M1·B2·TS, ts_only: B2·TS. 빠진 요소는 남은 것의 평균.
- E_spread: radar.output.spread 와 같은 정의 (각 요소 - 나머지 평균의 최대 |exp(dev) - 1|, %).
- 보조 E_fill (판정에 쓰지 않음): 월간 드라이버의 아직 공표 안 된 달을 드라이버 월 시계열 예측(로그, 계절 12 ETS,
  36개월 미만이면 전년 같은 달 x 최근 3개월 평균 YoY)으로 채워 3개월 전부로 M1 을 다시 계산한 E. n_filled = 채운 달 수.
  일별 드라이버(FRED, KOMIS)는 채우지 않는다 (n_filled 0, E_fill = E).

영업이익 예측 (매출 입력 E, 규칙은 fd_op_rule 고정, 계수·비용 예측은 t 까지 공표된 분기로 다시 추정)
- R1: OP_hat = a_t + b_t E. a_t, b_t 는 2019Q1 ~ t 까지 아는 분기 OLS (확장창).
- R2: OP_hat = E (1 - 원가율_hat) - 판관비_hat.  R3: OP_hat = E (1 - c_hat).  R4: 예측 안 함.
  원가율·판관비·c 는 로그 계열에 ETS (감쇠 추세 + 계절 4), SARIMA (1,0,0)(0,1,1)4 + 상수, Theta (계절 4, 가법)를 각각 적합해
  로그 예측 평균 (= 기하평균). 계열은 2018Q1 부터 t 까지 아는 분기의 마지막 연속 구간, 최소 12분기.
- OP_naive = B2 매출 x 전년 동기 영업이익률 (t 에 아는 값).
실행: python -m DATA.fdriver.eval2d.k4_forecast
"""
import json
import logging
import warnings
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .. import db, entity, events
from ..adapters import read_series, read_trade
from ..config import output_path
from ..nowcast import benchmarks as bm
from ..nowcast import features as ft
from ..nowcast import models as md
from ..nowcast.run import _calendar
from ..radar import ts as rts
from ..radar.output import spread
from . import k3_oprule as K3
from . import k7_methods as k7
from .k2_targets import read_adj

log = logging.getLogger(__name__)
OUT = output_path("2d", "K4")
T_FC = "fd_forecast_2d"
EVAL = pd.period_range("2024Q1", "2026Q2", freq="Q").to_timestamp(how="end").normalize()
PRE_DAYS = 5
R1_START = pd.Timestamp("2019-03-31")
COST_MIN = 12

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_FC}` (
  entity_id VARCHAR(20) NOT NULL,
  quarter CHAR(6) NOT NULL,
  period_end DATE NOT NULL,
  asof_date DATE NOT NULL,
  announce_date DATE NULL,
  announce_basis VARCHAR(10) NULL,
  status VARCHAR(10) NOT NULL,
  rule VARCHAR(10) NULL,
  drv_code VARCHAR(60) NULL,
  drv_coverage DOUBLE NULL,
  drv_months VARCHAR(20) NULL,
  rev_base DOUBLE NULL,
  g_M1 DOUBLE NULL, g_B2 DOUBLE NULL, g_TS DOUBLE NULL, g_E DOUBLE NULL, g_E_fill DOUBLE NULL,
  M1 DOUBLE NULL, B2 DOUBLE NULL, TS DOUBLE NULL, E DOUBLE NULL,
  E_spread DOUBLE NULL,
  E_spread_from VARCHAR(10) NULL,
  E_fill DOUBLE NULL,
  n_filled INT NULL,
  e_label VARCHAR(80) NULL,
  ts_label VARCHAR(80) NULL,
  m1_n_train INT NULL,
  OP_hat DOUBLE NULL,
  OP_naive DOUBLE NULL,
  op_a DOUBLE NULL, op_b DOUBLE NULL,
  cost_ratio_hat DOUBLE NULL, sga_hat DOUBLE NULL, c_hat DOUBLE NULL,
  op_n_models VARCHAR(20) NULL,
  op_note VARCHAR(120) NULL,
  coverage_class VARCHAR(10) NULL,
  flag_jump TINYINT NULL,
  flag_basis TINYINT NULL,
  flag_spread TINYINT NULL,
  flag_q4 TINYINT NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, quarter)
) DEFAULT CHARSET=utf8mb4
"""


# ------------------------------------------------------------------ 입력
def inputs() -> Dict:
    m = db.read_sql("SELECT entity_id, name, status, source, code, coverage_class, flag_jump, flag_basis "
                    "FROM fd_driver_map_2d WHERE status IN ('driver', 'ts_only')")
    r = db.read_sql("SELECT entity_id, rule, flag_spread, flag_q4 FROM fd_op_rule")
    m = m.merge(r, on="entity_id", how="left")
    return {"map": m}


def announce_dates(ids, cal) -> pd.DataFrame:
    ev = events.read_events(ids)
    fd = events.first_disclosure(ev, {i: 12 for i in ids})
    return fd[["entity_id", "period_end", "first_dt", "first_type"]]


def legal_date(q: pd.Timestamp, cal) -> pd.Timestamp:
    d = q + pd.Timedelta(days=90 if q.month == 12 else 45)
    i = int(np.searchsorted(cal, np.datetime64(d), side="left"))
    return pd.Timestamp(cal[min(i, len(cal) - 1)])


def pre5(first_dt: pd.Timestamp, cal) -> pd.Timestamp:
    pos = int(np.searchsorted(cal, np.datetime64(first_dt), side="left")) - PRE_DAYS
    return pd.Timestamp(cal[pos])


# ------------------------------------------------------------------ 드라이버
def driver_raw(code: str) -> (pd.DataFrame, str):
    """드라이버 긴 표 (key, item, period_end, value, available_at, ...) 와 kind (M 월간 합계, D 일별 평균)."""
    if ":" not in code:
        return read_trade([code], items=["expDlr"]), "M"
    kind, key = code.split(":", 1)
    if kind == "bok":
        b = db.read_sql("SELECT Date AS period_end, Value AS value, available_at FROM bok_economic_indicators "
                        "WHERE Index_Name = :n", {"n": key})
        b["period_end"] = pd.to_datetime(b["period_end"]) + pd.offsets.MonthEnd(0)
        b["available_at"] = pd.to_datetime(b["available_at"])
        b["key"], b["item"], b["asof_quality"], b["asof_note"] = code, "value", "C", ""
        return b, "M"
    if kind in ("kosis_idx", "store"):
        alias = "retail_sales_index" if kind == "kosis_idx" else "retail_sales_by_store_type"
        q = ("SELECT series_id FROM kr_kosis_series_info WHERE table_alias = :a AND c1_name = :c" +
             (" AND itm_name = '경상지수'" if kind == "kosis_idx" else ""))
        sid = db.read_sql(q, {"a": alias, "c": key})["series_id"].tolist()
        return read_series("kosis", sid), "M"
    if kind == "tourism":
        return read_series("tourism", [key]), "M"
    if kind == "fred":
        return read_series("fred", [key]), "D"
    if kind == "komis":
        sid = db.read_sql("SELECT series_id FROM kr_komis_mineral_price_info WHERE mineral_name = :m "
                          "AND price_criteria_name = 'LME CASH'", {"m": key})["series_id"].tolist()
        return read_series("komis", sid), "D"
    raise ValueError(code)


def fill_missing_months(kn: pd.DataFrame, q: pd.Timestamp, t: pd.Timestamp) -> (pd.DataFrame, int):
    """kn: 기준일 t 에 아는 월간 드라이버 (prepare_driver 형식). 분기 q 의 빠진 달을 예측해 붙인다."""
    s = kn.groupby("period_end")["value"].sum().sort_index()
    months = pd.date_range(q - pd.offsets.MonthEnd(2), q, freq="M")
    missing = [m for m in months if m not in s.index]
    if not missing or s.empty or (s <= 0).any():
        return kn, 0
    last = s.index.max()
    full = s.reindex(pd.date_range(s.index.min(), last, freq="M"))
    y = np.log(full.dropna())
    h_max = max(1, (missing[-1].year - last.year) * 12 + missing[-1].month - last.month)
    fc = None
    if len(y) >= 36 and not full.isna().any():
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mfit = ExponentialSmoothing(y.values, trend="add", damped_trend=True, seasonal="add",
                                            seasonal_periods=12, initialization_method="estimated").fit()
                fc = pd.Series(mfit.forecast(h_max), index=pd.date_range(last + pd.offsets.MonthEnd(1), periods=h_max,
                                                                          freq="M"))
        except Exception:      # noqa: BLE001
            fc = None
    rows = []
    for m in missing:
        if fc is not None and m in fc.index:
            v = float(np.exp(fc[m]))
        else:
            prev = s.get(m - pd.offsets.MonthEnd(12))
            recent = [np.log(s[k]) - np.log(s[k - pd.offsets.MonthEnd(12)]) for k in s.index[-3:]
                      if (k - pd.offsets.MonthEnd(12)) in s.index]
            if prev is None or not recent:
                return kn, 0
            v = float(prev * np.exp(np.mean(recent)))
        rows.append({"period_end": m, "value": v, "available_at": t, "key": kn["key"].iloc[0] if "key" in kn else "",
                     "item": kn["item"].iloc[0] if "item" in kn else "", "asof_note": "filled"})
    add = ft.prepare_driver(pd.DataFrame(rows))
    return pd.concat([kn, add], ignore_index=True), len(missing)


# ------------------------------------------------------------------ 비용 예측
def _log_models(y: np.ndarray, h: int) -> (Optional[float], int):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.forecasting.theta import ThetaModel
    out = []
    f = rts.ets_forecast(y, h)
    if f is not None:
        out.append(f)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            m = SARIMAX(y, order=(1, 0, 0), seasonal_order=(0, 1, 1, 4), trend="c").fit(disp=False, maxiter=200)
            v = float(np.asarray(m.forecast(h))[-1])
            if np.isfinite(v):
                out.append(v)
        except Exception:      # noqa: BLE001
            pass
        try:
            m = ThetaModel(y, period=4, deseasonalize=True, use_test=False, method="additive").fit()
            v = float(np.asarray(m.forecast(h))[-1])
            if np.isfinite(v):
                out.append(v)
        except Exception:      # noqa: BLE001
            pass
    if not out:
        return None, 0
    return float(np.exp(np.mean(out))), len(out)


def cost_forecast(s: pd.Series, q: pd.Timestamp) -> (Optional[float], int, str):
    """s: 분기 계열 (t 까지 아는 분기). 마지막 연속·양수 구간으로 q 를 예측."""
    s = s.dropna().sort_index()
    if s.empty:
        return None, 0, "empty"
    seg, cur = [], s.index.max()
    while cur in s.index and s[cur] > 0:
        seg.append(cur)
        cur = ft.quarter_shift(cur, 1)
    seg = sorted(seg)
    if len(seg) < COST_MIN:
        return None, 0, f"lt_{COST_MIN}"
    y = np.log(s[seg].to_numpy(float))
    h = rts.horizon(seg[-1], q)
    v, n = _log_models(y, h)
    return v, n, ""


# ------------------------------------------------------------------ 한 기업
def run_entity(row, adj: pd.DataFrame, it: pd.DataFrame, ann: pd.DataFrame, cal, quarters=None,
               live_t: pd.Timestamp = None, method_v2: bool = False) -> list:
    """quarters 기본 EVAL. live_t 를 주면 실전 모드: 발표일 대신 기준일 live_t 하나로 예측 (announce_basis 'live').
    method_v2=True 면 사전 등록 v2 (k7_methods) 의 SARIMA·ETS·Theta, E5·E4·E5_noM1, gap_E4_vs_B2, OP_hat_E5 를 더한다.
    기존 열(E, TS, OP_hat 등)의 계산은 바뀌지 않는다."""
    e = row.entity_id
    R_all = adj[adj["entity_id"] == e].set_index("period_end")["revenue_adj"].sort_index()
    a = ann[ann["entity_id"] == e].set_index("period_end")["first_dt"]
    items = it[it["entity_id"] == e].pivot_table(index="period_end", columns="item", values="value")
    for c in ("op", "cogs", "sga"):
        if c not in items:
            items[c] = np.nan

    def ann_dt(j):
        v = a.get(j)
        return (pd.Timestamp(v), "event") if v is not None and pd.notna(v) else (legal_date(j, cal), "legal")

    spec, drv, kind = None, None, None
    if row.status == "driver":
        raw, kind = driver_raw(row.code)
        drv = ft.prepare_driver(raw)
        spec = ft.DriverSpec(model_id="M1:" + row.code, driver_key=row.code, kind=kind, lag=0, loader=lambda: raw)
    out = []
    for q in (EVAL if quarters is None else quarters):
        if live_t is None:
            first_dt, basis = ann_dt(q)
            t = pre5(first_dt, cal)
        else:
            first_dt, basis, t = None, "live", pd.Timestamp(live_t)
        known_q = [j for j in R_all.index if j < q and ann_dt(j)[0] <= t]
        R = R_all.reindex(known_q)
        base = R.get(ft.quarter_shift(q, 4))
        rec = {"entity_id": e, "quarter": f"{q.year}Q{q.quarter}", "period_end": q.date(), "asof_date": t.date(),
               "announce_date": None if first_dt is None else first_dt.date(), "announce_basis": basis, "status": row.status, "rule": row.rule,
               "drv_code": row.code or None, "coverage_class": row.coverage_class, "flag_jump": row.flag_jump,
               "flag_basis": row.flag_basis, "flag_spread": row.flag_spread, "flag_q4": row.flag_q4}
        if base is None or not base > 0:
            out.append({**rec, "op_note": "no_base"})
            continue
        g_hist = ft.growth(R)
        b2 = bm.benchmarks(g_hist).get("B2")
        g_b2 = b2["g_hat"] if b2 else None
        tsr = rts.ts_forecast(R, q, base)
        g_ts = tsr["g_hat"]
        noM1 = [v for v in (g_b2, g_ts) if v is not None and pd.notna(v)]
        rec["g_E_noM1"] = float(np.mean(noM1)) if noM1 else None
        rec["E_noM1"] = float(base * np.exp(rec["g_E_noM1"])) if rec["g_E_noM1"] is not None else None
        g_m1, g_m1f, n_filled, cov, months, n_tr = None, None, None, None, None, None
        if spec is not None:
            kn = ft.known_at(drv, t)
            f = ft.target_feature(spec, drv, q, t, known=kn)
            cov, months = f["coverage"], ("" if f["positions"] is None else ",".join(str(p + 1) for p in f["positions"]))
            if f["x"] is not None:
                xtr = ft.training_features(spec, drv, list(g_hist.index), t, f["positions"], known=kn)
                res = md.ols_predict(xtr, g_hist, f["x"])
                if "g_hat" in res:
                    g_m1, n_tr = res["g_hat"], res["n_train"]
            if kind == "M":
                kf, n_filled = fill_missing_months(kn, q, t)
                if n_filled:
                    ff = ft.target_feature(spec, drv, q, t, known=kf)
                    if ff["x"] is not None:
                        xtr = ft.training_features(spec, drv, list(g_hist.index), t, [0, 1, 2], known=kn)
                        res = md.ols_predict(xtr, g_hist, ff["x"])
                        g_m1f = res.get("g_hat")
                else:
                    g_m1f = g_m1
            else:
                n_filled, g_m1f = 0, g_m1
        comps = {"B2": g_b2, "TS": g_ts}
        if spec is not None:
            comps = {"M1": g_m1, **comps}
        have = [v for v in comps.values() if v is not None and pd.notna(v)]
        g_e = float(np.mean(have)) if have else None
        miss = [k for k, v in comps.items() if v is None or pd.isna(v)]
        e_label = "E:" + "+".join(k for k in comps if k not in miss) + (f";missing:{','.join(miss)}" if miss else "")
        if spec is not None:
            sp = spread(g_m1, g_b2, g_ts, "M1", control=False)
            fill_comps = [v for v in (g_m1f, g_b2, g_ts) if v is not None and pd.notna(v)]
            g_ef = float(np.mean(fill_comps)) if fill_comps else None
        else:
            sp = {"spread_E_pct": np.nan, "spread_E_from": ""}
            g_ef = g_e
        lvl = (lambda g: float(base * np.exp(g)) if g is not None and pd.notna(g) else None)
        rec.update({"drv_coverage": cov, "drv_months": months, "rev_base": float(base), "g_M1": g_m1, "g_B2": g_b2,
                    "g_TS": g_ts, "g_E": g_e, "g_E_fill": g_ef, "M1": lvl(g_m1), "B2": lvl(g_b2), "TS": lvl(g_ts),
                    "E": lvl(g_e), "E_fill": lvl(g_ef), "n_filled": n_filled, "E_spread": sp["spread_E_pct"],
                    "E_spread_from": sp["spread_E_from"] or None, "e_label": e_label[:80],
                    "ts_label": (tsr["label"] or tsr["reason"] or "")[:80], "m1_n_train": n_tr})
        if method_v2:
            t3 = k7.ts3(R, q, base)
            cb = k7.combine(t3["g_SARIMA"], t3["g_ETS"], t3["g_Theta"], g_m1, g_b2, spec is not None)
            rec.update({**t3, **cb, "method_version": "v2"})
            for k in ("SARIMA", "ETS", "Theta", "E5", "E4", "E5_noM1"):
                rec[k] = lvl(rec[f"g_{k}"])
            rec["gap_E4_vs_B2"] = (cb["g_E4"] - g_b2) if cb["g_E4"] is not None and g_b2 is not None else None
            rec["gap_E_vs_B2"] = (g_e - g_b2) if g_e is not None and g_b2 is not None else None
        # 영업이익
        E = rec["E"]
        kq = [j for j in items.index if j < q and ann_dt(j)[0] <= t]
        I = items.reindex(kq)
        Rk = R_all.reindex(kq)
        b4 = ft.quarter_shift(q, 4)
        if rec["B2"] is not None and b4 in I.index and pd.notna(I.at[b4, "op"]) and Rk.get(b4, 0) > 0:
            rec["OP_naive"] = float(rec["B2"] * I.at[b4, "op"] / Rk[b4])
        rule = row.rule
        if E is None or rule in (None, "R4", "excluded") or pd.isna(rule):
            rec["op_note"] = "R4: 예측 안 함" if rule == "R4" else ("no_E" if E is None else "no_rule")
        elif rule == "R1":
            d = pd.DataFrame({"op": I["op"], "rev": Rk}).dropna()
            d = d[d.index >= R1_START]
            X = np.column_stack([np.ones(len(d)), d["rev"].to_numpy(float)])
            beta, *_ = np.linalg.lstsq(X, d["op"].to_numpy(float), rcond=None)
            rec.update({"op_a": float(beta[0]), "op_b": float(beta[1]), "OP_hat": float(beta[0] + beta[1] * E),
                        "op_n_models": f"ols n={len(d)}"})
        elif rule == "R2":
            cr, n1, w1 = cost_forecast(I["cogs"] / Rk, q)
            sg, n2, w2 = cost_forecast(I["sga"], q)
            if cr is not None and sg is not None:
                rec.update({"cost_ratio_hat": cr, "sga_hat": sg, "OP_hat": float(E * (1 - cr) - sg),
                            "op_n_models": f"cr{n1},sga{n2}"})
            else:
                # K4b 결정: 비용 계열 예측이 안 되면 R3 (영업비용률 c) 로 대체. fd_op_rule 의 rule 은 R2 그대로.
                c, n3, w3 = cost_forecast((Rk - I["op"]) / Rk, q)
                if c is not None:
                    rec.update({"c_hat": c, "OP_hat": float(E * (1 - c)), "op_n_models": f"c{n3}",
                                "op_note": "R2 판관비 계열 0 이하로 R3 대체"})
                else:
                    rec["op_note"] = f"R2 cost fail cr:{w1} sga:{w2}; R3 c fail {w3}"
        elif rule == "R3":
            c, n3, w3 = cost_forecast((Rk - I["op"]) / Rk, q)
            if c is not None:
                rec.update({"c_hat": c, "OP_hat": float(E * (1 - c)), "op_n_models": f"c{n3}"})
            else:
                rec["op_note"] = f"R3 c fail {w3}"
        if method_v2:
            # OP_hat_E5: 같은 규칙·같은 계수/비용 예측에 매출 E5 를 넣는다
            E5 = rec.get("E5")
            if E5 is not None and rec.get("OP_hat") is not None and pd.notna(rec.get("OP_hat")):
                if rec.get("op_a") is not None and pd.notna(rec.get("op_a")):
                    rec["OP_hat_E5"] = float(rec["op_a"] + rec["op_b"] * E5)
                elif rec.get("cost_ratio_hat") is not None and pd.notna(rec.get("cost_ratio_hat")):
                    rec["OP_hat_E5"] = float(E5 * (1 - rec["cost_ratio_hat"]) - rec["sga_hat"])
                elif rec.get("c_hat") is not None and pd.notna(rec.get("c_hat")):
                    rec["OP_hat_E5"] = float(E5 * (1 - rec["c_hat"]))
        out.append(rec)
    return out


def main(write: bool = True, only=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    cal = _calendar()
    m = inputs()["map"]
    if only:
        m = m[m["entity_id"].isin(only)]
    ids = m["entity_id"].tolist()
    adj = read_adj(ids)
    it = K3.items(ids, adj, quarters=None)
    ann = announce_dates(ids, cal)
    rows = []
    for i, r in enumerate(m.itertuples()):
        try:
            rows += run_entity(r, adj, it, ann, cal)
        except Exception as ex:     # 한 기업 실패를 기록하고 계속
            log.exception("failed %s", r.entity_id)
            rows.append({"entity_id": r.entity_id, "quarter": "ERR", "op_note": repr(ex)[:120]})
        log.info("%d/%d %s", i + 1, len(m), r.entity_id)
    fc = pd.DataFrame(rows)
    for c in ("op_note", "OP_hat", "OP_naive", "E_spread", "n_filled", "g_M1", "g_TS", "g_B2", "E", "announce_basis"):
        if c not in fc:
            fc[c] = np.nan
    fc["run_id"] = run_id
    fc = fc.drop(columns=["g_E_noM1", "E_noM1"], errors="ignore")   # 실전 기록용 열 (fd_forecast_2d 에는 없음)
    err = fc[fc["quarter"] == "ERR"]
    fc = fc[fc["quarter"] != "ERR"]
    n = 0
    if write and not only:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{T_FC}`")["entity_id"].tolist()
        if old:
            db.delete_fd(T_FC, {"entity_id": old})
        n = db.write_fd(fc, T_FC, ["entity_id", "quarter"])
    fc.to_csv(OUT / ("k4_forecast.csv" if not only else "k4_forecast_test.csv"), index=False, encoding="utf-8-sig")
    s = {"run_id": run_id, "rows_written": n, "rows": len(fc), "errors": err.to_dict(orient="records"),
         "firms": int(fc["entity_id"].nunique()), "status": fc.groupby("status")["entity_id"].nunique().to_dict(),
         "announce_basis": fc["announce_basis"].value_counts().to_dict(),
         "E_missing": int(fc["E"].isna().sum()), "M1_missing_driver": int((fc["status"].eq("driver") & fc["g_M1"].isna()).sum()),
         "TS_missing": int(fc["g_TS"].isna().sum()), "B2_missing": int(fc["g_B2"].isna().sum()),
         "e_label": fc["e_label"].value_counts().to_dict(),
         "n_filled": fc["n_filled"].value_counts(dropna=False).to_dict(),
         "E_spread_ge15": int((fc["E_spread"] >= 15).sum()), "E_spread_median": float(fc["E_spread"].median()),
         "OP_hat_rows": int(fc["OP_hat"].notna().sum()), "OP_naive_rows": int(fc["OP_naive"].notna().sum()),
         "op_by_rule": fc.groupby("rule")["OP_hat"].apply(lambda s: int(s.notna().sum())).to_dict(),
         "op_note": fc["op_note"].value_counts().to_dict()}
    (OUT / "k4_forecast_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return fc


if __name__ == "__main__":
    main()
