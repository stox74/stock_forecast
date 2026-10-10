# -*- coding: utf-8 -*-
"""호영님 확인용 (읽기만). 실행: python -m DATA.fdriver.radar.check_radar [--quarter 2026Q3]

1. 이벤트표: 39곳 행 수와 2026Q3 첫 공시가 나온 기업
2. fd_signal: variant 별 행 수 (level_evfix 추가 확인)
3. fd_nowcast: TS·E 행 수
4. 9월 수출 자료: korea_monthly_trade_data 의 최신 달 (2026-09 가 들어왔는지)
5. 실전 기록: fd_live_record 의 분기별 기록 수
"""
import argparse

import pandas as pd

from .. import db, events
from .universe import universe


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default="2026Q3")
    a = ap.parse_args(argv)
    pd.set_option("display.width", 200)
    u = universe()
    ids = list(u["entity_id"])
    ev = events.read_events(ids)
    print("== 1. 이벤트표 39곳 행 수", len(ev))
    fd = events.first_disclosure(ev, {e: 12 for e in ids})
    q = events.fiscal_quarter_end(a.quarter)
    done = fd[fd["period_end"] == q].merge(u[["entity_id", "name"]], on="entity_id")
    print(f"   {a.quarter} 첫 공시가 이미 나온 기업:", done[["name", "first_dt", "first_type"]].to_string(index=False))
    print("\n== 2. fd_signal variant")
    print(db.read_sql("SELECT variant, COUNT(*) n, COUNT(DISTINCT entity_id) k FROM fd_signal GROUP BY variant").to_string(index=False))
    print("\n== 3. fd_nowcast TS·E")
    print(db.read_sql("SELECT model_id, COUNT(*) n, COUNT(DISTINCT entity_id) k FROM fd_nowcast WHERE model_id IN ('TS','E') "
                      "GROUP BY model_id").to_string(index=False))
    print("\n== 4. 수출 자료 최신 달")
    print(db.read_sql("SELECT MAX(date) last_month, COUNT(DISTINCT root_hs_code) n_codes_at_last FROM korea_monthly_trade_data "
                      "WHERE indicator='expDlr' AND date = (SELECT MAX(date) FROM korea_monthly_trade_data "
                      "WHERE indicator='expDlr')").to_string(index=False))
    print("\n== 5. 실전 기록 (fd_live_record)")
    if db.table_exists("fd_live_record"):
        print(db.read_sql("SELECT fiscal_quarter, record_id, COUNT(*) n FROM fd_live_record GROUP BY fiscal_quarter, record_id")
              .to_string(index=False))
    else:
        print("   아직 기록 없음 (테이블 없음)")


if __name__ == "__main__":
    main()
