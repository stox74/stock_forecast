# -*- coding: utf-8 -*-
"""v2 사후 계산: 이미 저장된 실전 기록(v2 열이 없는 회차)으로 출력 v2 값(dir_*, spread_*, flag_*, decision)을 계산해
별도 CSV 로 남긴다. 기록 테이블은 읽기만 한다 (고쳐 쓰지 않음).

- 저장된 성장률(g_E, g_M4, g_M1, g_B2, g_TS)과 rev_base 로 금액·단순 성장률을 다시 만든다.
- 컨센서스 열이 비어 있는 회차(1차, fd_consensus 기준)는 kr_revenue_consensus 를 기록 기준일로 다시 읽는다
  (build.consensus_cols 와 같은 규칙). 이 값은 기록 당시 저장된 값이 아니므로 consensus_source 에 '사후'로 적는다.
- 결과: OUTPUT_ROOT/radar/<분기>/records/record_postcalc_<분기>_<record_id>.csv (모든 행 postcalc = '사후 계산').
실행: python -m DATA.fdriver.radar.postcalc_v2 --quarter 2026Q3
"""
import argparse

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from . import build as B
from . import output
from .record import T_RECORD


def internal_from_record(rec: pd.DataFrame) -> pd.DataFrame:
    t = rec.copy()
    base = pd.to_numeric(t["rev_base"], errors="coerce")
    for m in ("E", "M4", "M1", "B2", "TS"):
        g = pd.to_numeric(t[f"g_{m}"], errors="coerce")
        t[f"g_{m}_pct"] = (np.exp(g) - 1) * 100
        if m in ("M1", "B2", "TS") and (f"rev_{m}" not in t or t[f"rev_{m}"].isna().all()):
            t[f"rev_{m}"] = base * np.exp(g)
    src = []
    rows = []
    for r in t.to_dict("records"):
        stored = r.get("consensus") is not None and pd.notna(r.get("consensus"))
        c = B.consensus_cols(r["entity_id"], r["fiscal_quarter"], pd.Timestamp(r["asof_date"]), r.get("rev_E"),
                             r.get("rev_M4"), r.get("rev_base"))
        if stored:                       # 저장된 값이 있으면 그 값으로 차이를 다시 계산 (같은 규칙)
            v = float(r["consensus"])
            c.update({"consensus": v, "consensus_asof": r.get("consensus_asof"),
                      "gap_E": (r["rev_E"] / v - 1) if r.get("rev_E") is not None and pd.notna(r.get("rev_E")) else np.nan})
        rows.append(c)
        src.append("기록 값" if stored else "사후 (kr_revenue_consensus, 기록 기준일)")
    cc = pd.DataFrame(rows, index=t.index)
    for k in cc.columns:
        t[k] = cc[k]
    t["consensus_source"] = src
    return t


def postcalc(quarter: str, record_id: str) -> pd.DataFrame:
    rec = db.read_sql(f"SELECT * FROM `{T_RECORD}` WHERE fiscal_quarter = :q AND record_id = :r",
                      {"q": quarter, "r": record_id})
    t = internal_from_record(rec)
    out = output.to_output(t)
    out.insert(0, "record_id", record_id)
    out.insert(1, "postcalc", "사후 계산")
    out["consensus_source"] = t["consensus_source"].values
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    a = ap.parse_args(argv)
    has_v2 = "decision" in db.table_columns(T_RECORD)          # v2 열은 다음 기록 때 생김
    cond = " AND decision IS NULL" if has_v2 else ""
    ids = db.read_sql(f"SELECT DISTINCT record_id FROM `{T_RECORD}` WHERE fiscal_quarter = :q{cond}",
                      {"q": a.quarter})["record_id"].tolist()
    d = output_path("radar", a.quarter, "records")
    for rid in ids:
        f = d / f"record_postcalc_{a.quarter}_{rid}.csv"
        if f.exists():
            raise FileExistsError(f)
        out = postcalc(a.quarter, rid)
        out.to_csv(f, index=False, encoding="utf-8-sig")
        print(rid, len(out), f)
        print(output.decision_counts(output.judgement(out)).to_string())


if __name__ == "__main__":
    main()
