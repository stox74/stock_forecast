# -*- coding: utf-8 -*-
"""R1 (c) 확인: 2026Q2 시험 레이더 (39행) 와 fd_nowcast 대조 (표본 5곳). DB 쓰기 없음 (레이더 CSV·HTML 만 저장).

대조: 레이더의 pre5 기준일·g_M4·g_B2·g_M1(M2)·g_TS·g_E 가 fd_nowcast 의 같은 기업·분기 pre5 행(전체 기간 변형)과 같은지.
표본 5곳: 업종·드라이버 유형이 다른 기업 (가중 세그먼트, 관광, 드라이버 2개, 시범, 대조군).
실행: python -m DATA.fdriver.radar.c_check_2026q2
"""
import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from . import build as B
from .universe import CONTROLS

SAMPLE = ["009150", "114090", "000660", "005930", "006040"]   # 삼성전기, GKL, SK하이닉스, 삼성전자, 동원산업(대조군)
OUT = output_path("radar", "c_check")


def main():
    res = B.build("2026Q2")
    paths = B.save(res)
    tab = res["table"]
    rows = []
    for e in SAMPLE:
        r = tab[tab["entity_id"] == e].iloc[0]
        nc = db.read_sql("SELECT model_id, asof_date, checkpoint, g_hat FROM fd_nowcast WHERE entity_id = :e AND "
                         "fiscal_quarter = '2026Q2' AND checkpoint LIKE '%%pre5%%'", {"e": e})
        g = dict(zip(nc["model_id"], nc["g_hat"]))
        t_nc = pd.to_datetime(nc["asof_date"]).iloc[0].date() if len(nc) else None
        pairs = [("asof_date", r["asof_date"], t_nc), ("g_B2", r["g_B2"], g.get("B2")),
                 ("g_TS", r["g_TS"], g.get("TS")), ("g_E", r["g_E"], g.get("E"))]
        if e != "006040":
            pairs += [("g_M4", r["g_M4"], g.get(r["main_model"])), ("g_M1", r["g_M1"], g.get(r["M1_model"]))]
        for col, a, b in pairs:
            if col == "asof_date":
                same = a == b
                diff = None
            else:
                same = (pd.isna(a) and (b is None or pd.isna(b))) or (b is not None and pd.notna(a) and abs(a - b) < 1e-9)
                diff = None if (b is None or pd.isna(a)) else float(a - b)
            note = ""
            if e in CONTROLS and col in ("g_TS", "g_E") and pd.isna(a) and b is not None:
                same, note = True, "대조군은 레이더에 B2 만 표시 (등급표 규칙): 비운 것이 맞음"
            rows.append({"entity_id": e, "name": r["name"], "column": col, "radar": a, "fd_nowcast": b, "diff": diff,
                         "match": bool(same), "note": note})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "radar_vs_nowcast_2026Q2.csv", index=False, encoding="utf-8-sig")
    n_rows = len(tab)
    n_with = int(tab["g_B2"].notna().sum())
    pd.set_option("display.width", 220)
    print(paths)
    print(f"radar rows {n_rows}, with B2 {n_with}, with M4 {int(tab['g_M4'].notna().sum())}, with E {int(tab['g_E'].notna().sum())}")
    print(t.to_string(index=False))
    print("ALL MATCH:", bool(t["match"].all()))


if __name__ == "__main__":
    main()
