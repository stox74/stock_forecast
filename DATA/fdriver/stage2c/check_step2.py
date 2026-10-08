# -*- coding: utf-8 -*-
"""호영님 확인용: 2c 작업 2 결과를 DB 에서 읽어 요약 (읽기만).

실행 (저장소 루트 investment_strategy 에서):
    python -m DATA.fdriver.stage2c.check_step2
보여 주는 것
1. fd_earnings_event: 선정 33개 기업의 기업별 이벤트 수, 첫·마지막 분기
2. fd_nowcast: 선정 기업의 기업별 행 수, 기준일 종류, 모델 종류 수
3. 삼성전자 2026Q3: 잠정실적 이벤트, 실전 기록(M4:combo, B2), 컨센서스 두 건
"""
import pandas as pd

from .. import db
from .mapping import read_mapping


def main():
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    m = read_mapping()
    c = m["companies"][m["companies"]["included"]]
    ids = list(c["entity_id"])
    names = dict(zip(c["entity_id"], c["name"]))
    f, p = db.in_clause("e", ids)

    ev = db.read_sql(f"SELECT entity_id, COUNT(*) n_events, MIN(fiscal_quarter) first_q, MAX(fiscal_quarter) last_q "
                     f"FROM fd_earnings_event WHERE entity_id IN {f} GROUP BY entity_id", p)
    ev.insert(1, "name", ev["entity_id"].map(names))
    print("== 1. 이벤트표 (fd_earnings_event)")
    print(ev.to_string(index=False))

    nc = db.read_sql(f"SELECT entity_id, COUNT(*) n_rows, COUNT(DISTINCT fiscal_quarter) n_quarters, "
                     f"COUNT(DISTINCT model_id) n_models, MIN(fiscal_quarter) first_q, MAX(fiscal_quarter) last_q "
                     f"FROM fd_nowcast WHERE entity_id IN {f} GROUP BY entity_id", p)
    nc.insert(1, "name", nc["entity_id"].map(names))
    print("\n== 2. nowcast (fd_nowcast)")
    print(nc.to_string(index=False))

    print("\n== 3. 삼성전자 2026Q3")
    print(db.read_sql("SELECT fiscal_quarter, event_type, rcept_dt, rcept_no, revenue_won FROM fd_earnings_event "
                      "WHERE entity_id='005930' AND fiscal_quarter='2026Q3'").to_string(index=False))
    print(db.read_sql("SELECT model_id, rev_hat, g_hat, nsur, nsur_drv FROM fd_live_forecast WHERE entity_id='005930' "
                      "AND fiscal_quarter='2026Q3' AND model_id IN ('M4:combo','B2')").to_string(index=False))
    print(db.read_sql("SELECT asof_date, mean, source FROM fd_consensus WHERE entity_id='005930' "
                      "AND fiscal_quarter='2026Q3'").to_string(index=False))


if __name__ == "__main__":
    main()
