# -*- coding: utf-8 -*-
"""실전 사전 기록 확인: fd_live_forecast 의 기록과 실제 첫 공시 값을 비교 (DB 읽기만).

사용 (실적 발표 후):
  1. 이벤트표 갱신: from DATA.fdriver import events; events.build_and_load([...])   (fd_earnings_event 쓰기)
  2. python -m DATA.fdriver.evaluate.live_check 2026Q3        (저장소 루트에서)
실제 첫 공시 값
  read_revenue(vintage) 의 첫 vintage (2a 평가와 같은 값). 아직 매출 표에 없으면 fd_earnings_event 의
  잠정 매출(prelim, 4분기는 pl_change). 둘 다 없으면 '대기'.
출력 (기업 x 모델)
  g_actual = ln(실제) - ln(rev_base), err = g_hat - g_actual, |err|, B2 의 |err| (같은 코로나 변형),
  B2 대비 개선 여부, SUR = (실제 - rev_base - delta) / sigma (기록된 t 시점 분모),
  nsur 와 SUR 부호 일치, nsur_drv 와 (SUR - nsur_mom) 부호 일치 (드라이버 성분이 맞는 방향이었는지).
"""
import sys
from typing import Optional

import numpy as np
import pandas as pd

from .. import db, entity
from ..events import first_disclosure, fiscal_quarter_end, read_events
from ..nowcast import features as ft

T_LIVE = "fd_live_forecast"


def load_records(fiscal_quarter: str) -> pd.DataFrame:
    return db.read_sql(f"SELECT * FROM `{T_LIVE}` WHERE fiscal_quarter = :q", {"q": fiscal_quarter})


def actual_first(entity_id: str, fiscal_quarter: str) -> dict:
    """{'value', 'first_dt', 'source'} 또는 value None (대기)."""
    fye = int(entity.entity_info([entity_id])["fiscal_year_end"].iloc[0])
    q = fiscal_quarter_end(fiscal_quarter, fye)
    act = ft.actuals(ft.revenue_frame(entity_id))
    if q in act.index and pd.notna(act.loc[q, "rev_actual_first"]):
        return {"value": float(act.loc[q, "rev_actual_first"]), "first_dt": act.loc[q, "first_dt"], "source": "read_revenue"}
    fd = first_disclosure(read_events([entity_id]), {entity_id: fye})
    r = fd[fd["period_end"] == q]
    if len(r):
        r = r.iloc[0]
        v = r["prelim_value"] if pd.notna(r["prelim_value"]) else r["pl_change_value"]
        if pd.notna(v):
            return {"value": float(v), "first_dt": r["first_dt"], "source": f"fd_earnings_event:{r['first_type']}"}
    return {"value": None, "first_dt": None, "source": None}


def compare(rec: pd.DataFrame, actual: Optional[float]) -> pd.DataFrame:
    """한 기업의 기록 행(rec)과 실제 첫 공시 값 비교. actual 이 None 이면 status '대기'."""
    d = rec.copy()
    d["variant"] = np.where(d["model_id"].str.endswith(":excovid"), "excovid", "all")
    if actual is None or not actual > 0:
        d["status"] = "대기"
        return d[["entity_id", "fiscal_quarter", "model_id", "status"]]
    d["status"] = "비교"
    d["rev_actual_first"] = actual
    d["g_actual"] = np.log(actual) - np.log(d["rev_base"])
    d["err"] = d["g_hat"] - d["g_actual"]
    d["abs_err"] = d["err"].abs()
    b2 = d[d["model_id"].isin(["B2", "B2:excovid"])].set_index("variant")["abs_err"]
    d["abs_err_B2"] = d["variant"].map(b2)
    d["better_than_B2"] = np.where(d["model_id"].str.startswith("B2"), None, d["abs_err"] < d["abs_err_B2"])
    d["sur"] = (actual - d["rev_base"] - d["delta"]) / d["sigma"]
    d["sign_nsur_eq_sur"] = np.sign(d["nsur"]) == np.sign(d["sur"])
    d["sign_drv_eq_sur_minus_mom"] = np.where(d["nsur_drv"].fillna(0) == 0, None,
                                              np.sign(d["nsur_drv"]) == np.sign(d["sur"] - d["nsur_mom"]))
    return d[["entity_id", "fiscal_quarter", "model_id", "status", "g_hat", "g_actual", "err", "abs_err", "abs_err_B2",
              "better_than_B2", "rev_hat", "rev_actual_first", "nsur", "nsur_mom", "nsur_drv", "sur",
              "sign_nsur_eq_sur", "sign_drv_eq_sur_minus_mom"]]


def check(fiscal_quarter: str) -> pd.DataFrame:
    rec = load_records(fiscal_quarter)
    out = []
    for e, g in rec.groupby("entity_id"):
        a = actual_first(e, fiscal_quarter)
        c = compare(g, a["value"])
        c["actual_source"], c["first_dt"] = a["source"], a["first_dt"]
        out.append(c)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main(argv=None) -> None:
    argv = argv or sys.argv[1:]
    fq = argv[0] if argv else "2026Q3"
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_rows", 200)
    r = check(fq)
    if r.empty:
        print(f"no records for {fq}")
        return
    for e, g in r.groupby("entity_id"):
        print(f"\n== {e} {entity.PILOTS.get(e, '')} {fq}: {g['status'].iloc[0]}"
              + (f" (실제 {g['rev_actual_first'].iloc[0] / 1e12:.3f}조원, {g['actual_source'].iloc[0]}, {g['first_dt'].iloc[0]})"
                 if g["status"].iloc[0] == "비교" else ""))
        if g["status"].iloc[0] == "비교":
            print(g.drop(columns=["entity_id", "fiscal_quarter", "status", "actual_source", "first_dt"]).round(4)
                  .to_string(index=False))
    if len(argv) > 1:
        r.to_csv(argv[1], index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
