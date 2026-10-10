# -*- coding: utf-8 -*-
"""R1 (a): 고친 events.parse_prelim(월간 공시 제외)으로 39곳 fd_earnings_event 재적재 + fd_signal 'level_evfix' 추가.

1. 기존 fd_earnings_event 전체를 CSV 로 백업 (radar/a_reload/backup_fd_earnings_event_<시각>.csv).
2. 39곳 공시 목록을 다시 받아(본문은 캐시 우선) 이벤트표를 만들고, 39곳 행을 지운 뒤 다시 넣는다
   (upsert 만 하면 이제 빠져야 할 월간 공시 행이 남으므로).
3. 확인: 기업별로 첫 공시일이 바뀐 분기 수 (백업에 있던 분기 기준) 를 작업 3 리포트 5-1 과 비교.
4. fd_signal: 2c·2a 기존 행은 그대로 두고, 고친 이벤트 날짜로 만든 level 신호를 variant 'level_evfix' 로 추가
   (덮어쓰지 않는 INSERT).
실행: python -m DATA.fdriver.radar.a_reload_events                 (재적재 + 신호)
      python -m DATA.fdriver.radar.a_reload_events --signals-only  (신호만: 첫 실행 때 variant 길이 오류로 신호 INSERT
                                                                    가 통째로 취소돼 이 옵션으로 다시 넣음)
"""
import time

import pandas as pd

from .. import db, entity, events
from ..config import output_path
from ..signals import sur
from .universe import universe

OUT = output_path("radar", "a_reload")
EXPECTED = {"000270": 63, "005380": 63, "009540": 53, "034230": 47, "114090": 25, "298040": 1}   # 작업 3 리포트 5-1
SIGNAL_KEY = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "variant"]


def fetch_lists(info: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for r in info.itertuples():
        for attempt in range(4):
            try:
                df = events.fetch_list(r.dart_corp_code, bgn=events.START)
                break
            except Exception:              # noqa: BLE001  (DART 접속 일시 오류: 쉬고 다시)
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        df["entity_id"] = r.entity_id
        parts.append(df)
    return pd.concat(parts, ignore_index=True)


def first_dates(ev: pd.DataFrame, fye) -> pd.DataFrame:
    return events.first_disclosure(ev, fye)[["entity_id", "period_end", "first_dt", "first_type"]]


def add_evfix_signals(ids) -> int:
    """fd_signal 에 variant 'level_evfix' 추가 (기존 행 그대로, 덮어쓰지 않는 INSERT).
    variant 컬럼이 VARCHAR(10) 이라 'level_evfix'(11자)가 들어가도록 VARCHAR(20) 으로 넓힌다 (기존 값은 바뀌지 않음)."""
    db.execute_fd_ddl(f"ALTER TABLE `{sur.T_SIGNAL}` MODIFY `variant` VARCHAR(20) NOT NULL")
    parts = []
    for e in ids:
        s = sur.build_signals(e, variants=("level",))["rows"]
        s = s[s["nsur"].notna() | s["sur"].notna()].copy()
        s["variant"] = "level_evfix"
        parts.append(s)
    sig = pd.concat(parts, ignore_index=True)
    sig["asof_date"] = pd.to_datetime(sig["asof_date"]).dt.date
    return db.insert_fd_strict(sig, sur.T_SIGNAL, SIGNAL_KEY)


def main():
    if "--signals-only" in __import__("sys").argv[1:]:
        print("level_evfix written", add_evfix_signals(list(universe()["entity_id"])))
        return
    u = universe()
    ids = list(u["entity_id"])
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    old_all = db.read_sql(f"SELECT * FROM `{events.T_EVENT}`")
    old_all.to_csv(OUT / f"backup_fd_earnings_event_{stamp}.csv", index=False, encoding="utf-8-sig")
    old = events.read_events(ids)

    info = entity.entity_info(ids)
    fye = dict(zip(info["entity_id"], info["fiscal_year_end"].astype(int)))
    lst = fetch_lists(info)
    parsed = events.parse_filings(lst, fye)
    new = events.build_events(parsed)
    parsed.to_csv(OUT / f"parsed_filings_{stamp}.csv", index=False, encoding="utf-8-sig")

    rows = new[events.EVENT_COLUMNS].copy()
    rows["rcept_dt"] = rows["rcept_dt"].dt.date
    events.create_table()
    deleted = db.delete_fd(events.T_EVENT, {"entity_id": ids})
    written = db.write_fd(rows, events.T_EVENT, ["entity_id", "fiscal_quarter", "event_type"])

    after = events.read_events(ids)
    a = first_dates(old, fye).rename(columns={"first_dt": "first_dt_before", "first_type": "type_before"})
    b = first_dates(after, fye).rename(columns={"first_dt": "first_dt_after", "first_type": "type_after"})
    m = a.merge(b, on=["entity_id", "period_end"], how="left")
    m["changed"] = m["first_dt_before"] != m["first_dt_after"]
    m.to_csv(OUT / "first_dt_before_after.csv", index=False, encoding="utf-8-sig")
    new_q = b.merge(a[["entity_id", "period_end"]], on=["entity_id", "period_end"], how="left", indicator=True)
    new_q = new_q[new_q["_merge"] == "left_only"].drop(columns="_merge")
    new_q.to_csv(OUT / "new_quarters_after_reload.csv", index=False, encoding="utf-8-sig")
    chk = m.groupby("entity_id").agg(quarters=("period_end", "size"), changed=("changed", "sum")).reset_index()
    chk["expected_5_1"] = chk["entity_id"].map(EXPECTED).fillna(0).astype(int)
    chk["match"] = chk["changed"] == chk["expected_5_1"]
    chk.insert(1, "name", chk["entity_id"].map(dict(zip(u["entity_id"], u["name"]))))
    chk.to_csv(OUT / "changed_vs_report_5_1.csv", index=False, encoding="utf-8-sig")
    cnt = (old.groupby("entity_id").size().rename("rows_before").to_frame()
           .join(after.groupby("entity_id").size().rename("rows_after")).reset_index())
    cnt.to_csv(OUT / "event_rows_before_after.csv", index=False, encoding="utf-8-sig")

    # fd_signal level_evfix (39곳)
    n_sig = add_evfix_signals(ids)
    pd.set_option("display.width", 200)
    print(f"events: deleted {deleted}, written {written}; signals level_evfix written {n_sig}")
    print(chk[(chk["changed"] > 0) | (chk["expected_5_1"] > 0) | ~chk["match"]].to_string(index=False))
    print("all match:", bool(chk["match"].all()), "| new quarters:", len(new_q))
    print(new_q.to_string(index=False))


if __name__ == "__main__":
    main()
