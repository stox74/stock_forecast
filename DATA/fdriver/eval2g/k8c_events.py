# -*- coding: utf-8 -*-
"""2g K8c 2(a): 기업 사건 수집. DART 공시목록에서 2023-01 이후 주요사항보고서 중
회사분할결정·회사분할합병결정·회사합병결정·영업양수결정·영업양도결정 (정정 포함, 원공시 날짜 기준). DB 쓰기 없음 (CSV).
공시목록 캐시: 2024-01 이후는 K10 캐시(2g/K10/cache/lists), 2023 년은 2g/K8c/cache/lists_2023 에 새로 받는다.
python -m DATA.fdriver.eval2g.k8c_events [--threads 4]
"""
import argparse
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import events
from ..config import output_path
from . import k10_region as K10

OUT = output_path("2g", "K8c")
L23 = output_path("2g", "K8c", "cache", "lists_2023")
EVENT_RE = re.compile(r"주요사항보고서\((회사분할합병결정|회사분할결정|회사합병결정|영업양수결정|영업양도결정)\)")
KIND = {"회사분할합병결정": "분할합병", "회사분할결정": "분할", "회사합병결정": "합병", "영업양수결정": "영업양수",
        "영업양도결정": "영업양도"}
log = logging.getLogger(__name__)
MISSING_2023: list = []


def lists(ticker: str, corp_code: str) -> pd.DataFrame:
    f23 = L23 / f"{ticker}.csv"
    if f23.exists():
        a = pd.read_csv(f23, dtype=str)
    else:
        try:
            a = events.fetch_list(corp_code, bgn="20230101", end="20231231")
            a.to_csv(f23, index=False, encoding="utf-8-sig")
        except Exception:      # noqa: BLE001  2023 목록을 못 받으면 2024 이후만 쓰고 표시
            MISSING_2023.append(ticker)
            a = pd.DataFrame(columns=["report_nm", "rcept_dt", "rcept_no"])
    f24 = K10.LISTS / f"{ticker}.csv"
    if f24.exists():
        b = pd.read_csv(f24, dtype=str)
    else:
        b = events.fetch_list(corp_code, bgn="20240101")
        b.to_csv(f24, index=False, encoding="utf-8-sig")
    return pd.concat([a, b], ignore_index=True)


def firm_events(ticker: str, corp_code: str) -> list:
    lst = lists(ticker, corp_code)
    out = []
    for r in lst.itertuples():
        title = re.sub(r"\s+", "", str(r.report_nm))
        m = EVENT_RE.search(title)
        if m:
            out.append({"entity_id": ticker, "kind": KIND[m.group(1)], "rcept_dt": str(r.rcept_dt),
                        "rcept_no": str(r.rcept_no), "title": str(r.report_nm), "corrected": title.startswith("[")})
    return out


def main(threads: int = 4) -> pd.DataFrame:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t0 = time.time()
    t = K10.targets()
    rows, errs = [], []

    def one(r):
        try:
            return firm_events(r.entity_id, r.corp_code), None
        except Exception as ex:      # noqa: BLE001
            return [], (r.entity_id, repr(ex)[:120])

    with ThreadPoolExecutor(max_workers=threads) as ex:
        for k, (ev, err) in enumerate(ex.map(one, list(t.itertuples()))):
            rows += ev
            if err:
                errs.append(err)
            if (k + 1) % 200 == 0:
                log.info("%d/%d (%.0fs)", k + 1, len(t), time.time() - t0)
    e = pd.DataFrame(rows, columns=["entity_id", "kind", "rcept_dt", "rcept_no", "title", "corrected"])
    e["rcept_dt"] = pd.to_datetime(e["rcept_dt"])
    # 사건 하나 = 같은 기업·종류의 원공시 (정정은 원공시가 목록에 없을 때만 그 날짜로)
    first = e.sort_values(["corrected", "rcept_dt"]).groupby(["entity_id", "kind"], as_index=False).first()
    allv = e.sort_values(["entity_id", "rcept_dt"])
    allv.to_csv(OUT / "k8c_events_all_filings.csv", index=False, encoding="utf-8-sig")
    # 같은 기업·종류라도 1년 넘게 떨어진 공시는 다른 사건으로 본다
    ev = []
    for (eid, kind), g in e[~e["corrected"]].sort_values("rcept_dt").groupby(["entity_id", "kind"]):
        last = None
        for r in g.itertuples():
            if last is None or (r.rcept_dt - last).days > 365:
                ev.append({"entity_id": eid, "kind": kind, "event_dt": r.rcept_dt.date(), "rcept_no": r.rcept_no})
            last = r.rcept_dt
    only_corr = first[first["corrected"]]
    for r in only_corr.itertuples():
        if not any(x["entity_id"] == r.entity_id and x["kind"] == r.kind for x in ev):
            ev.append({"entity_id": r.entity_id, "kind": r.kind, "event_dt": r.rcept_dt.date(), "rcept_no": r.rcept_no})
    ev = pd.DataFrame(ev).merge(t[["entity_id", "name", "group_2g"]], on="entity_id", how="left")
    ev.to_csv(OUT / "k8c_events.csv", index=False, encoding="utf-8-sig")
    pd.Series(sorted(set(MISSING_2023)), name="entity_id").to_csv(OUT / "k8c_events_missing_2023.csv", index=False,
                                                                   encoding="utf-8-sig")
    print({"firms": len(t), "missing_2023_lists": len(set(MISSING_2023)), "filings": len(e), "events": len(ev), "firms_with_event": int(ev["entity_id"].nunique()),
           "by_kind": ev["kind"].value_counts().to_dict(), "errors": errs, "seconds": round(time.time() - t0)})
    return ev


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    main(ap.parse_args().threads)
