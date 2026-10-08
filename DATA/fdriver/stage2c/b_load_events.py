# -*- coding: utf-8 -*-
"""2c 작업 2 (b): 선정 33개 기업 DART 이벤트 적재 (2a 와 같은 events.build_and_load, fd_earnings_event 쓰기).

확인 기준: 기업별 이벤트 수, 첫·마지막 분기, 공시일 결측 분기 수.
공시일 결측 분기 = 매출(read_revenue)이 있는 2010Q1 이후 분기 중 첫 공시(잠정·정기보고서) 이벤트가 없는 분기.
실행: python -m DATA.fdriver.stage2c.b_load_events
"""
import pandas as pd

from .. import entity, events
from ..adapters import read_revenue
from ..config import output_path
from .mapping import read_mapping

OUT = output_path("2c", "step2", "b_events")


def main():
    m = read_mapping()
    ids = [e for e in m["companies"].loc[m["companies"]["included"], "entity_id"] if e not in entity.PILOTS]
    res = events.build_and_load(ids)
    res["parsed"].to_csv(OUT / "parsed_filings.csv", index=False, encoding="utf-8-sig")
    res["events"].to_csv(OUT / "events_built.csv", index=False, encoding="utf-8-sig")
    summarize(ids, m)
    print("rows_written", res["rows_written"])


def summarize(ids, m=None):
    m = m or read_mapping()
    names = dict(zip(m["companies"]["entity_id"], m["companies"]["name"]))
    ev = events.read_events(ids)
    fye = {i: 12 for i in ids}
    fd = events.first_disclosure(ev, fye)
    rev = read_revenue(ids)
    rev = rev[rev["period_end"] >= "2010-03-31"]
    rows = []
    for e in ids:
        g = ev[ev["entity_id"] == e]
        rq = set(rev.loc[rev["key"] == e, "period_end"])
        fq = set(fd.loc[fd["entity_id"] == e, "period_end"])
        miss = sorted(q for q in rq if q not in fq)
        cnt = g["event_type"].value_counts()
        rows.append({"entity_id": e, "name": names.get(e), "n_events": len(g),
                     "prelim": int(cnt.get("prelim", 0)), "prelim_sep": int(cnt.get("prelim_sep", 0)),
                     "pl_change": int(cnt.get("pl_change", 0)), "periodic": int(cnt.get("periodic", 0)),
                     "first_q": g["fiscal_quarter"].min(), "last_q": g["fiscal_quarter"].max(),
                     "revenue_quarters": len(rq), "quarters_with_first_dt": len(fq & rq),
                     "missing_first_dt": len(miss),
                     "missing_list": ",".join(events.fiscal_quarter_label(q) for q in miss)})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "b_event_summary.csv", index=False, encoding="utf-8-sig")
    print(t.to_string(index=False))
    return t


if __name__ == "__main__":
    main()
