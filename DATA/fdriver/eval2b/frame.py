# -*- coding: utf-8 -*-
"""2b 이벤트 단위 자료 (기업 x 분기). 2b 명세서 v1 3·5절.

대상: 레이더 39곳 x 2022Q1~2026Q2 (4분기 포함, 4분기는 flags 로 표시만).
- pre5_date: fd_nowcast pre5 기준일 (전체 기간 변형 B2 행). n_E, n_M4, n_B2: 그 기준일의 예상 매출 rev_hat (원).
  주 모델 M4 = 드라이버 2개 이상 M4:combo, 1개면 M4:<드라이버> (레이더와 같음).
- actual: 첫 공시 매출 (fd_nowcast rev_actual_first, 2c 와 같음).
- announce_date: 이벤트표(2c, R1 재적재) 첫 공시일.
- c_pre5: 관측일 <= pre5 인 최근 컨센서스, c_final: 관측일 < 발표일 인 최근 값, c_after: 발표 다음 달의 마지막 관측값.
  같은 값을 2023 일별 원천으로도 계산 (c_pre5_daily, c_final_daily).
- gap_E, gap_M4 = (n - c_pre5) / c_pre5, surprise = (actual - c_final) / c_final, rev_c = (c_final - c_pre5) / c_pre5.
- nsur_drv: 2c M4 서프라이즈 신호 (fd_signal variant 'level_evfix' = R1 에서 고친 발표일 기준, 주 모델 pre5 행).
- flags: q4 (회계 4분기), basis_mismatch (기업별 c_final/actual 중앙값 0.8~1.25 밖), corp_event (2c 기업 사건 분기),
  daily (2023 일별 원천 값이 있음).
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from .. import db, events
from ..adapters import kr_consensus as kc
from ..nowcast import features as ft
from ..radar.build import main_driver_model
from ..radar.universe import universe

QUARTERS = [f"{y}Q{q}" for y in range(2022, 2027) for q in range(1, 5) if (y, q) <= (2026, 2)]
BASIS_LO, BASIS_HI = 0.8, 1.25


def _register():
    from ..stage2c import registry
    registry.register(rules="2b2")


def _nowcast_pre5(e: str, main: str) -> pd.DataFrame:
    nc = db.read_sql("SELECT fiscal_quarter, asof_date, model_id, checkpoint, rev_hat, rev_actual_first FROM fd_nowcast "
                     "WHERE entity_id = :e AND checkpoint LIKE '%%pre5%%' AND model_id IN ('B2', 'E', :m)",
                     {"e": e, "m": main or "B2"})
    if nc.empty:
        return pd.DataFrame(columns=["fiscal_quarter"])
    p = nc.pivot_table(index="fiscal_quarter", columns="model_id", values="rev_hat", aggfunc="first")
    out = pd.DataFrame({"n_B2": p.get("B2"), "n_E": p.get("E"), "n_M4": p.get(main) if main else np.nan})
    first = nc.drop_duplicates("fiscal_quarter").set_index("fiscal_quarter")
    out["pre5_date"] = pd.to_datetime(first["asof_date"])
    out["actual"] = first["rev_actual_first"]
    return out.reset_index()


def _nsur_drv(e: str, main: str) -> pd.Series:
    if not main:
        return pd.Series(dtype=float)
    s = db.read_sql("SELECT fiscal_quarter, nsur_drv FROM fd_signal WHERE entity_id = :e AND model_id = :m "
                    "AND variant = 'level_evfix' AND checkpoint LIKE '%%pre5%%'", {"e": e, "m": main})
    return s.drop_duplicates("fiscal_quarter").set_index("fiscal_quarter")["nsur_drv"]


def _after_value(h: pd.DataFrame, q: pd.Timestamp, ann: pd.Timestamp):
    if h.empty or pd.isna(ann):
        return None
    m0 = (ann + pd.offsets.MonthBegin(1)).normalize()
    m1 = m0 + pd.offsets.MonthEnd(0)
    x = h[(h["target_period_end"] == q) & (h["asof_date"] >= m0) & (h["asof_date"] <= m1)]
    return float(x["value"].iloc[-1]) if len(x) else None


def build_events() -> pd.DataFrame:
    _register()
    u = universe()
    rows: List[Dict] = []
    for r in u.itertuples():
        e = r.entity_id
        main = main_driver_model(e)
        nc = _nowcast_pre5(e, main).set_index("fiscal_quarter")
        nd = _nsur_drv(e, main)
        fd = events.first_disclosure(events.read_events([e]), {e: 12})
        ann = dict(zip(fd["period_end"], fd["first_dt"]))
        hm = kc.history(e, "monthly")
        for fq in QUARTERS:
            q = kc.quarter_end(fq)
            a = nc.loc[fq] if fq in nc.index else None
            pre5 = a["pre5_date"] if a is not None else pd.NaT
            ad = ann.get(q, pd.NaT)
            cp = kc.read_consensus(e, fq, pre5) if pd.notna(pre5) else {"value": None, "asof": None}
            cf = kc.read_consensus(e, fq, ad, strict=True) if pd.notna(ad) else {"value": None, "asof": None}
            cpd = kc.read_consensus(e, fq, pre5, source="daily") if pd.notna(pre5) else {"value": None, "asof": None}
            cfd = kc.read_consensus(e, fq, ad, source="daily", strict=True) if pd.notna(ad) else {"value": None, "asof": None}
            row = {"ticker": e, "name": r.name, "quarter": fq, "grade": r.grade, "group": r.group, "main_model": main,
                   "pre5_date": pre5, "announce_date": ad,
                   "actual": a["actual"] if a is not None else np.nan,
                   "n_E": a["n_E"] if a is not None else np.nan, "n_M4": a["n_M4"] if a is not None else np.nan,
                   "n_B2": a["n_B2"] if a is not None else np.nan,
                   "c_pre5": cp["value"], "c_pre5_asof": cp["asof"], "c_final": cf["value"], "c_final_asof": cf["asof"],
                   "c_after": _after_value(hm, q, ad),
                   "c_pre5_daily": cpd["value"], "c_final_daily": cfd["value"],
                   "nsur_drv": nd.get(fq, np.nan),
                   "q4": fq.endswith("Q4"), "corp_event": bool(ft.corporate_event_quarters(e, [q])),
                   "has_consensus": not hm.empty,
                   "has_hist_consensus": bool((hm["target_period_end"] <= q).any()) if not hm.empty else False}
            rows.append(row)
    d = pd.DataFrame(rows)
    for c in ["actual", "n_E", "n_M4", "n_B2", "c_pre5", "c_final", "c_after", "c_pre5_daily", "c_final_daily", "nsur_drv"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["gap_E"] = (d["n_E"] - d["c_pre5"]) / d["c_pre5"]
    d["gap_M4"] = (d["n_M4"] - d["c_pre5"]) / d["c_pre5"]
    d["surprise"] = (d["actual"] - d["c_final"]) / d["c_final"]
    d["rev_c"] = (d["c_final"] - d["c_pre5"]) / d["c_pre5"]
    d["gap_E_daily"] = (d["n_E"] - d["c_pre5_daily"]) / d["c_pre5_daily"]
    d["surprise_daily"] = (d["actual"] - d["c_final_daily"]) / d["c_final_daily"]
    d["ratio_final_actual"] = d["c_final"] / d["actual"]
    med = d[~d["q4"]].groupby("ticker")["ratio_final_actual"].median()
    d["basis_ratio_median"] = d["ticker"].map(med)
    d["basis_mismatch"] = ~d["basis_ratio_median"].between(BASIS_LO, BASIS_HI)
    d.loc[d["basis_ratio_median"].isna(), "basis_mismatch"] = False
    d["daily"] = d["c_pre5_daily"].notna()
    d["comparable"] = (~d["q4"]) & d[["c_pre5", "c_final", "n_E", "actual", "announce_date"]].notna().all(axis=1) \
        & (d["actual"] > 0)
    d["in_judgement"] = d["comparable"] & ~d["basis_mismatch"] & ~d["corp_event"]
    return d


def flags_text(d: pd.DataFrame) -> pd.Series:
    parts = []
    for r in d.itertuples():
        f = [k for k, v in (("q4", r.q4), ("basis_mismatch", r.basis_mismatch), ("corp_event", r.corp_event),
                            ("daily", r.daily)) if v]
        if not r.comparable:
            f.append("not_comparable")
        parts.append(";".join(f))
    return pd.Series(parts, index=d.index)
