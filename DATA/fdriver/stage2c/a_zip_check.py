# -*- coding: utf-8 -*-
"""2c 작업 2 (a): zip 본문 읽기 수정 전후 2a 결과 동일 확인 (DB 쓰기 없음).

1. 시범 6개 기업의 DART 공시 목록을 다시 받고, 본문이 필요한 공시(잠정실적·손익구조변경)를 수정한 doc_text 로
   새 캐시 폴더(2c/step2/cache/dart_docs_recheck)에 다시 받는다. 예전 캐시(stage2a/cache/dart_docs, 첫 파일 읽기)
   본문과 글자 단위로 비교한다.
2. 새 본문으로 이벤트표를 다시 만들어 DB 의 fd_earnings_event 와 비교한다 (2a 적재 이후 새로 나온 공시는 따로 표시).
3. M4 평가: DB 의 fd_nowcast 로 metrics_table 을 다시 계산해 명세서 2a v6 값과 비교하고, 현재 코드로 시범 기업
   nowcast 를 다시 계산해 DB 행과 비교한다.
실행: 저장소 루트(investment_strategy)에서 python -m DATA.fdriver.stage2c.a_zip_check
"""
import sys

import numpy as np
import pandas as pd

from .. import db, entity, events
from ..config import output_path
from ..evaluate import forecast_eval as fe
from ..nowcast import run

OUT = output_path("2c", "step2", "a_zip")
PILOTS = list(entity.PILOTS)
V6 = {  # 명세서 2a v6 / 작업 4b 리포트: pre5, 전체 기간, B2 대비 상대 RMSE
    ("005930", "M4:combo"): 0.850, ("039130", "M4:combo"): 0.369, ("267270", "M4:842952"): 1.036,
}
KEYS = ["entity_id", "fiscal_quarter", "event_type"]


def recheck_docs():
    info = entity.entity_info(PILOTS)
    fye = dict(zip(info["entity_id"], info["fiscal_year_end"].astype(int)))
    lists = []
    for r in info.itertuples():
        df = events.fetch_list(r.dart_corp_code, bgn=events.START)
        df["entity_id"] = r.entity_id
        lists.append(df)
    lst = pd.concat(lists, ignore_index=True)
    new_cache = output_path("2c", "step2", "cache", "dart_docs_recheck")
    parsed = events.parse_filings(lst, fye, cache=new_cache)
    old_cache = events.doc_cache_dir()
    need = parsed[parsed["event_type"] != "periodic"]
    rows = []
    for rno in need["rcept_no"].unique():
        fo, fn = old_cache / f"{rno}.txt", new_cache / f"{rno}.txt"
        to = fo.read_text(encoding="utf-8") if fo.exists() else None
        tn = fn.read_text(encoding="utf-8") if fn.exists() else None
        rows.append({"rcept_no": rno, "old_cached": to is not None, "new_fetched": tn is not None,
                     "same_text": (to == tn) if (to is not None and tn is not None) else None,
                     "old_len": len(to) if to else None, "new_len": len(tn) if tn else None})
    cmp_docs = pd.DataFrame(rows)
    choice = pd.read_csv(new_cache / events.ZIP_LOG, dtype=str) if (new_cache / events.ZIP_LOG).exists() else pd.DataFrame()
    return lst, parsed, cmp_docs, choice


def compare_events(parsed: pd.DataFrame):
    new = events.build_events(parsed)[events.EVENT_COLUMNS].copy()
    old = events.read_events(PILOTS)[events.EVENT_COLUMNS].copy()
    cutoff = old["rcept_dt"].max()
    new_after = new[new["rcept_dt"] > cutoff]
    new_cmp = new[new["rcept_dt"] <= cutoff]
    m = old.merge(new_cmp, on=KEYS, how="outer", suffixes=("_old", "_new"), indicator=True)
    diffs = []
    for r in m.to_dict("records"):
        if r["_merge"] != "both":
            diffs.append({**{k: r[k] for k in KEYS}, "field": "row", "old": r["_merge"], "new": ""})
            continue
        for c in ["rcept_dt", "rcept_no", "report_nm", "revenue_won", "unit", "fs_type"]:
            a, b = r[f"{c}_old"], r[f"{c}_new"]
            same = (pd.isna(a) and pd.isna(b)) or (a == b) or (
                isinstance(a, float) and isinstance(b, float) and np.isclose(a, b, rtol=1e-12))
            if not same:
                diffs.append({**{k: r[k] for k in KEYS}, "field": c, "old": a, "new": b})
    return old, new, new_after, pd.DataFrame(diffs, columns=KEYS + ["field", "old", "new"]), cutoff


def m4_check():
    d = fe.load_nowcast(PILOTS)
    d = d[d["checkpoint"] != "live"]
    mt = fe.metrics_table(d)
    sel = mt[(mt["variant"] == "all") & (mt["checkpoint"] == "pre5") & mt["model"].str.startswith("M4")]
    sel = sel[["entity_id", "model", "n_vs_B2", "rel_B2", "dm_p_B2"]].copy()
    sel["v6_value"] = [V6.get((e, m)) for e, m in zip(sel["entity_id"], sel["model"])]
    # 현재 코드로 다시 계산한 nowcast 와 DB 행 비교
    db_nc = db.read_sql("SELECT entity_id, fiscal_quarter, asof_date, model_id, checkpoint, g_hat, rev_hat FROM fd_nowcast "
                        "WHERE entity_id IN ('005930','006040','267270','278470','003350','039130')")
    db_nc["asof_date"] = pd.to_datetime(db_nc["asof_date"])
    rows = []
    for e in PILOTS:
        r = run.nowcast_entity(e)["rows"]
        r["asof_date"] = pd.to_datetime(r["asof_date"])
        rows.append(r[["entity_id", "fiscal_quarter", "asof_date", "model_id", "checkpoint", "g_hat", "rev_hat"]])
    new_nc = pd.concat(rows, ignore_index=True)
    k = ["entity_id", "fiscal_quarter", "asof_date", "model_id"]
    mm = db_nc.merge(new_nc, on=k, how="outer", suffixes=("_db", "_new"), indicator=True)
    both = mm[mm["_merge"] == "both"]
    gdiff = (both["g_hat_db"] - both["g_hat_new"]).abs()
    summ = {"db_rows": len(db_nc), "new_rows": len(new_nc), "only_db": int((mm["_merge"] == "left_only").sum()),
            "only_new": int((mm["_merge"] == "right_only").sum()), "both": len(both),
            "max_abs_g_diff": float(gdiff.max()) if len(both) else None,
            "n_g_diff_gt_1e-9": int((gdiff > 1e-9).sum())}
    return sel, summ, mm[mm["_merge"] != "both"]


def main():
    lst, parsed, cmp_docs, choice = recheck_docs()
    cmp_docs.to_csv(OUT / "doc_text_old_vs_new.csv", index=False, encoding="utf-8-sig")
    if not choice.empty:
        choice.to_csv(OUT / "zip_choice_pilots.csv", index=False, encoding="utf-8-sig")
    old, new, new_after, diffs, cutoff = compare_events(parsed)
    diffs.to_csv(OUT / "event_diffs.csv", index=False, encoding="utf-8-sig")
    new_after.to_csv(OUT / "events_after_2a_load.csv", index=False, encoding="utf-8-sig")
    sel, summ, nc_unmatched = m4_check()
    sel.to_csv(OUT / "m4_pre5_rel_b2.csv", index=False, encoding="utf-8-sig")
    nc_unmatched.to_csv(OUT / "nowcast_unmatched_rows.csv", index=False, encoding="utf-8-sig")
    lines = [
        f"공시 목록 {len(lst)}건, 본문 필요 공시 {cmp_docs.shape[0]}건",
        f"  예전 캐시 있음 {int(cmp_docs['old_cached'].sum())}, 새로 받음 {int(cmp_docs['new_fetched'].sum())}, "
        f"본문 동일 {int(cmp_docs['same_text'].fillna(False).sum())}, 다름 {int((cmp_docs['same_text'] == False).sum())}",
        "  zip 판별 규칙: " + (choice["rule"].value_counts().to_dict().__repr__() if not choice.empty else "로그 없음"),
        "  zip 파일 수: " + (choice["n_files"].value_counts().to_dict().__repr__() if not choice.empty else "-"),
        f"이벤트표: DB {len(old)}행 (마지막 접수일 {cutoff.date()}), 새로 만든 표 {len(new)}행 "
        f"(그중 2a 적재 이후 공시 {len(new_after)}행), 차이 {len(diffs)}건",
        f"nowcast 재계산: {summ}",
        "M4 pre5 (전체 기간) B2 대비 상대 RMSE:",
        sel.round(3).to_string(index=False),
    ]
    txt = "\n".join(lines)
    (OUT / "a_summary.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    sys.exit(main())
