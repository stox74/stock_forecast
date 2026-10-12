# -*- coding: utf-8 -*-
"""2b 이벤트 단위 자료 (기업 x 분기). 2b 명세서 v1 3·5절 + K2 수정.

대상: 레이더 39곳 x 2022Q1~2026Q2 (4분기 포함, 4분기는 flags 로 표시만).
- pre5_date: fd_nowcast pre5 기준일. n_E, n_M4, n_B2, n_TS, n_drv(E 의 드라이버 요소, radar.ts.e_component): 그 기준일 예상 매출 (원).
  주 모델 M4 = 드라이버 2개 이상 M4:combo, 1개면 M4:<드라이버> (레이더와 같음).
- actual: 첫 공시 매출 (fd_nowcast rev_actual_first, 2c 와 같음).
  K2: 4분기 첫 공시값이 연간 값인 행 (첫 공시 / 확정 4분기 > 2) 은 확정 4분기 값 (연간 - 1~3분기, rev_actual_final) 으로
  바꾸고 flags 'q4_annual_fix', 원래 값은 actual_first_raw.
  fd_nowcast 행이 없는 분기(기업 사건, 4분기 점검 제외)는 actual 이 비고 '비교 불가'가 된다.
- announce_date: 이벤트표(2c, R1 재적재) 첫 공시일.
- c_pre5: 관측일 <= pre5 인 최근 컨센서스.
- c_final (K2): 발표 다음 달 마지막 관측값 (read_consensus_after, 발표 후 고정값 = 발표 직전 값). 없으면 NULL.
  K1 정의(관측일 < 발표일 인 최근 값 = 전월 말 값)는 c_final_prev 로 보존.
  2023 일별 원천: c_pre5_daily (관측일 <= pre5), c_final_daily (관측일 < 발표일, 진짜 발표 직전 값).
- gap_E, gap_M4 = (n - c_pre5) / c_pre5, surprise = (actual - c_final) / c_final, rev_c = (c_final - c_pre5) / c_pre5.
- nsur_drv: 2c M4 서프라이즈 신호 (fd_signal variant 'level_evfix' = R1 에서 고친 발표일 기준, 주 모델 pre5 행).
- flags (판정 제외): q4, basis_mismatch (기업별 c_final/actual 중앙값 0.8~1.25 밖, 또는 BASIS_MISMATCH_EXTRA),
  corp_event (2c 기업 사건 분기), excluded_firm (EXCLUDED_FIRMS), control (등급 'B2만').
  flags (표시만): daily (2023 일별 원천 값이 있음), basis_check (이벤트 actual/c_final 0.67~1.5 밖), q4_annual_fix.
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from .. import db, events
from ..adapters import kr_consensus as kc
from ..nowcast import features as ft
from ..radar.build import main_driver_model
from ..radar.ts import e_component
from ..radar.universe import universe

QUARTERS = [f"{y}Q{q}" for y in range(2022, 2027) for q in range(1, 5) if (y, q) <= (2026, 2)]
BASIS_LO, BASIS_HI = 0.8, 1.25
EVENT_LO, EVENT_HI = 0.67, 1.5
Q4_ANNUAL_RATIO = 2.0
# K2: 실제 매출이 별도 기준이라 컨센서스(연결)와 맞지 않는 분기
BASIS_MISMATCH_EXTRA = {"009540": ["2022Q1", "2022Q2", "2022Q3"]}
EXCLUDED_FIRMS = {"267270": "현대건설기계 (2026 합병, 대상에서 제외 유지)"}
CONTROL_GRADE = "B2만"
EXCLUDE_FLAGS = ["q4", "basis_mismatch", "corp_event", "excluded_firm", "control"]
SHOW_FLAGS = ["daily", "basis_check", "q4_annual_fix"]
NOWCAST_COLS = ("n_E", "n_M4", "n_B2", "n_TS", "n_drv")


def _register():
    from ..stage2c import registry
    registry.register(rules="2b2")


def _nowcast_pre5(e: str, main: str, drv: str) -> pd.DataFrame:
    ids = {"B2": "n_B2", "E": "n_E", "TS": "n_TS"}
    if main:
        ids[main] = "n_M4"
    if drv:
        ids[drv] = "n_drv"
    frag, p = db.in_clause("m", list(ids))
    nc = db.read_sql("SELECT fiscal_quarter, asof_date, model_id, rev_hat, rev_actual_first, rev_actual_final "
                     f"FROM fd_nowcast WHERE entity_id = :e AND checkpoint LIKE '%%pre5%%' AND model_id IN {frag}",
                     {"e": e, **p})
    if nc.empty:
        return pd.DataFrame(columns=["fiscal_quarter", "pre5_date", "actual_first", "actual_final", *NOWCAST_COLS])
    pv = nc.pivot_table(index="fiscal_quarter", columns="model_id", values="rev_hat", aggfunc="first")
    out = pd.DataFrame(index=pv.index)
    for c in NOWCAST_COLS:
        out[c] = np.nan
    for m, c in ids.items():
        if m in pv:
            out[c] = pv[m]
    first = nc.drop_duplicates("fiscal_quarter").set_index("fiscal_quarter")
    out["pre5_date"] = pd.to_datetime(first["asof_date"])
    out["actual_first"] = first["rev_actual_first"]
    out["actual_final"] = first["rev_actual_final"]
    return out.reset_index()


def _nsur_drv(e: str, main: str) -> pd.Series:
    if not main:
        return pd.Series(dtype=float)
    s = db.read_sql("SELECT fiscal_quarter, nsur_drv FROM fd_signal WHERE entity_id = :e AND model_id = :m "
                    "AND variant = 'level_evfix' AND checkpoint LIKE '%%pre5%%'", {"e": e, "m": main})
    return s.drop_duplicates("fiscal_quarter").set_index("fiscal_quarter")["nsur_drv"]


def fix_q4_actual(fq: str, first: float, final: float):
    """4분기 첫 공시값이 연간 값이면 확정 4분기 값(연간 - 1~3분기)으로. 반환 (actual, fixed)."""
    if fq.endswith("Q4") and pd.notna(first) and pd.notna(final) and final > 0 and first / final > Q4_ANNUAL_RATIO:
        return float(final), True
    return first, False


def build_events() -> pd.DataFrame:
    _register()
    u = universe()
    rows: List[Dict] = []
    none = {"value": None, "asof": None}
    for r in u.itertuples():
        e = r.entity_id
        main, drv = main_driver_model(e), e_component(e)
        nc = _nowcast_pre5(e, main, drv).set_index("fiscal_quarter")
        nd = _nsur_drv(e, main)
        fd = events.first_disclosure(events.read_events([e]), {e: 12})
        ann = dict(zip(fd["period_end"], fd["first_dt"]))
        hm = kc.history(e, "monthly")
        for fq in QUARTERS:
            q = kc.quarter_end(fq)
            a = nc.loc[fq] if fq in nc.index else None
            pre5 = a["pre5_date"] if a is not None else pd.NaT
            ad = ann.get(q, pd.NaT)
            cp = kc.read_consensus(e, fq, pre5) if pd.notna(pre5) else none
            cprev = kc.read_consensus(e, fq, ad, strict=True) if pd.notna(ad) else none
            caft = kc.read_consensus_after(e, fq, ad)
            cpd = kc.read_consensus(e, fq, pre5, source="daily") if pd.notna(pre5) else none
            cfd = kc.read_consensus(e, fq, ad, source="daily", strict=True) if pd.notna(ad) else none
            first = a["actual_first"] if a is not None else np.nan
            actual, fixed = fix_q4_actual(fq, first, a["actual_final"] if a is not None else np.nan)
            rows.append({"ticker": e, "name": r.name, "quarter": fq, "grade": r.grade, "group": r.group,
                         "main_model": main, "drv_model": drv, "pre5_date": pre5, "announce_date": ad,
                         "actual": actual, "actual_first_raw": first, "q4_annual_fix": fixed,
                         **{c: (a[c] if a is not None else np.nan) for c in NOWCAST_COLS},
                         "c_pre5": cp["value"], "c_pre5_asof": cp["asof"],
                         "c_final": caft["value"], "c_final_asof": caft["asof"], "c_after": caft["value"],
                         "c_final_prev": cprev["value"], "c_final_prev_asof": cprev["asof"],
                         "c_pre5_daily": cpd["value"], "c_final_daily": cfd["value"],
                         "nsur_drv": nd.get(fq, np.nan),
                         "q4": fq.endswith("Q4"), "corp_event": bool(ft.corporate_event_quarters(e, [q])),
                         "excluded_firm": e in EXCLUDED_FIRMS, "control": r.grade == CONTROL_GRADE,
                         "basis_extra": fq in BASIS_MISMATCH_EXTRA.get(e, []),
                         "has_consensus": not hm.empty,
                         "has_hist_consensus": bool((hm["target_period_end"] <= q).any()) if not hm.empty else False})
    return derive(pd.DataFrame(rows))


def derive(d: pd.DataFrame) -> pd.DataFrame:
    """비율·flags·판정 표본."""
    d = d.copy()
    for c in ["actual", "actual_first_raw", *NOWCAST_COLS, "c_pre5", "c_final", "c_after", "c_final_prev",
              "c_pre5_daily", "c_final_daily", "nsur_drv"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["gap_E"] = (d["n_E"] - d["c_pre5"]) / d["c_pre5"]
    d["gap_M4"] = (d["n_M4"] - d["c_pre5"]) / d["c_pre5"]
    d["surprise"] = (d["actual"] - d["c_final"]) / d["c_final"]
    d["rev_c"] = (d["c_final"] - d["c_pre5"]) / d["c_pre5"]
    d["surprise_prev"] = (d["actual"] - d["c_final_prev"]) / d["c_final_prev"]
    d["gap_E_daily"] = (d["n_E"] - d["c_pre5_daily"]) / d["c_pre5_daily"]
    d["surprise_daily"] = (d["actual"] - d["c_final_daily"]) / d["c_final_daily"]
    d["ratio_final_actual"] = d["c_final"] / d["actual"]
    med = d[~d["q4"] & ~d["basis_extra"]].groupby("ticker")["ratio_final_actual"].median()
    d["basis_ratio_median"] = d["ticker"].map(med)
    firm_mis = d["basis_ratio_median"].notna() & ~d["basis_ratio_median"].between(BASIS_LO, BASIS_HI)
    d["basis_mismatch"] = firm_mis | d["basis_extra"]
    ev_ratio = d["actual"] / d["c_final"]
    d["basis_check"] = ev_ratio.notna() & ~ev_ratio.between(EVENT_LO, EVENT_HI)
    d["daily"] = d["c_pre5_daily"].notna()
    d["comparable"] = (~d["q4"]) & d[["c_pre5", "c_final", "n_E", "actual", "announce_date"]].notna().all(axis=1) \
        & (d["actual"] > 0)
    d["in_judgement"] = d["comparable"] & ~d[EXCLUDE_FLAGS[1:]].any(axis=1)
    return d


def flags_text(d: pd.DataFrame) -> pd.Series:
    parts = []
    for _, r in d.iterrows():
        f = [k for k in EXCLUDE_FLAGS + SHOW_FLAGS if bool(r[k])]
        if not r["comparable"]:
            f.append("not_comparable")
        parts.append(";".join(f))
    return pd.Series(parts, index=d.index)
