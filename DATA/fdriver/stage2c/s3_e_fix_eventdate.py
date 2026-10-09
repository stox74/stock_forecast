# -*- coding: utf-8 -*-
"""2c 작업 3: 판정 실행 뒤 발견한 버그(월간 판매·매출 공시가 분기 잠정실적 이벤트로 잡힘)의 영향 계산. DB 쓰기 없음.

버그: 예전 서식 '영업(잠정)실적(공정공시)' 월간 공시(현대차·기아 판매 대수, GKL 월 매출, 현대중공업 월간 IR 등)는 본문에
  날짜 범위가 없어 '접수일 직전 분기'의 prelim_sep 이벤트로 잡혔다. 그 결과 분기 첫 공시일(day 0)이 월초 공시일이 되어
  신호일(pre5) 보다 앞서고, C3 매매 구간이 비어 이벤트가 빠졌다. (nowcast 의 첫 공시일·실제 값은 vintage 경로라 영향 없음)
수정: events.parse_prelim 이 월 표기 '(2023년1월)' 등을 보면 월간으로 보고 뺀다 (events.MONTHLY_LABEL).
이 스크립트: 공시 목록(작업 2 캐시, 시범 기업은 다시 조회)과 캐시된 본문으로 이벤트표를 메모리에서 다시 만들고
  (fd_earnings_event 는 다시 적재하지 않음, 이번 작업의 DB 쓰기는 fd_signal 만 허용) 고친 첫 공시일로 C3 를 다시 계산한다.
실행: python -m DATA.fdriver.stage2c.s3_e_fix_eventdate
산출 (2c/step3/fix_eventdate): event_diff_by_entity.csv, c3_events_fixed.csv, c3_fixed.csv, pilot_event_check.csv
"""
import pandas as pd

from .. import entity, events
from ..config import output_path
from . import dart
from . import s3_lib as L

J = output_path("2c", "step3", "judgement")
OUT = output_path("2c", "step3", "fix_eventdate")


def _cached_doc_text(rcept_no, cache):
    """캐시에 있는 본문만 읽는다 (새로 받지 않음). 캐시에 없는 것은 원래 적재 때도 본문 오류였던 공시."""
    f = cache / f"{rcept_no}.txt"
    return f.read_text(encoding="utf-8") if f.exists() else "__ERROR__ not_cached"


def rebuild(entity_ids, use_cache_lists=True) -> pd.DataFrame:
    info = entity.entity_info(entity_ids)
    fye = dict(zip(info["entity_id"], info["fiscal_year_end"].astype(int)))
    lists = []
    for r in info.itertuples():
        if use_cache_lists:
            lst = dart.dart_list(r.entity_id)
        else:
            lst = events.fetch_list(r.dart_corp_code, bgn=events.START)
            lst["entity_id"] = r.entity_id
        lists.append(lst)
    lst = pd.concat(lists, ignore_index=True)
    lst["rcept_dt"] = lst["rcept_dt"].astype(str)
    orig = events.doc_text
    events.doc_text = _cached_doc_text
    try:
        parsed = events.parse_filings(lst, fye)
    finally:
        events.doc_text = orig
    ev = events.build_events(parsed)[events.EVENT_COLUMNS]
    return ev, fye


def first_dates(ev: pd.DataFrame, fye) -> pd.DataFrame:
    fd = events.first_disclosure(ev, fye)
    return fd[["entity_id", "period_end", "first_dt", "first_type"]]


def compare(ids, ev_new, fye, cutoff=None):
    old = events.read_events(ids)
    if cutoff is not None:
        ev_new = ev_new[ev_new["rcept_dt"] <= cutoff]
    a = first_dates(old, fye).rename(columns={"first_dt": "first_dt_db", "first_type": "type_db"})
    b = first_dates(ev_new, fye).rename(columns={"first_dt": "first_dt_fixed", "first_type": "type_fixed"})
    m = a.merge(b, on=["entity_id", "period_end"], how="outer")
    m["changed"] = m["first_dt_db"] != m["first_dt_fixed"]
    return m


def main():
    sig = pd.read_csv(J / "c2_signals_main.csv", dtype={"entity_id": str})
    ids = sorted(sig["entity_id"].unique())
    ev_new, fye = rebuild(ids)
    cutoff = events.read_events(ids)["rcept_dt"].max()
    m = compare(ids, ev_new, fye, cutoff)
    m.to_csv(OUT / "first_dt_db_vs_fixed.csv", index=False, encoding="utf-8-sig")
    by = (m.groupby("entity_id").agg(quarters=("period_end", "size"), changed=("changed", "sum")).reset_index())
    by.to_csv(OUT / "event_diff_by_entity.csv", index=False, encoding="utf-8-sig")

    # 시범 기업: 고친 규칙으로도 2a 이벤트 첫 공시일이 같은지
    p_ids = list(entity.PILOTS)
    ev_p, fye_p = rebuild(p_ids, use_cache_lists=False)
    mp = compare(p_ids, ev_p, fye_p, events.read_events(p_ids)["rcept_dt"].max())
    mp.groupby("entity_id").agg(quarters=("period_end", "size"), changed=("changed", "sum")).reset_index() \
      .to_csv(OUT / "pilot_event_check.csv", index=False, encoding="utf-8-sig")

    # 고친 첫 공시일로 C3 다시 계산 (신호 값은 그대로, event_date 만 바꿈)
    fd = first_dates(ev_new, fye)
    fd["fiscal_quarter"] = [events.fiscal_quarter_label(q, fye[e]) for e, q in zip(fd["entity_id"], fd["period_end"])]
    s2 = sig.drop(columns=["event_date"]).merge(fd[["entity_id", "fiscal_quarter", "first_dt"]], on=["entity_id", "fiscal_quarter"],
                                                how="left").rename(columns={"first_dt": "event_date"})
    s2["asof_date"] = pd.to_datetime(s2["asof_date"])
    ev = L.events_with_car(s2)
    ev.to_csv(OUT / "c3_events_fixed.csv", index=False, encoding="utf-8-sig")
    r = L.c3(ev)
    t = L.verdict([{k: v for k, v in r.items() if k != "_fit"}])
    t.to_csv(OUT / "c3_fixed.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    print(by[by["changed"] > 0].to_string(index=False))
    print(pd.read_csv(OUT / "pilot_event_check.csv").to_string(index=False))
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
