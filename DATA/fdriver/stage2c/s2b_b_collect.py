# -*- coding: utf-8 -*-
"""2c 작업 2b (b): 무역 통계 수집기로 진단 A 코드 수집 (korea_monthly_trade_data 쓰기는 수집기만 함).

수집기 = Korea_Market/collection/Korea_Export_Data/한국수출입데이터_수집_V6.ipynb 첫 셀 (코드 목록 DATA/hs_code_map_v2.py).
이 스크립트는 그 셀의 코드를 그대로 실행하되, 설정 네 줄만 바꾼다:
  TARGET_CODES = (a) 의 A 코드, START_YYYYMM = '200701', END_YYYYMM = '202608', AUTO_UPLOAD = True
실행 전후로 대상 코드의 DB 기간을 기록한다 (step2b/b_collect/coverage_before.csv, coverage_after.csv).
실행: python -m DATA.fdriver.stage2c.s2b_b_collect
"""
import json
import os
import re
from pathlib import Path

import pandas as pd

from .. import db
from ..config import output_path

OUT = output_path("2c", "step2b", "b_collect")
REPO = Path(__file__).resolve().parents[3]
NOTEBOOK = REPO / "Korea_Market" / "collection" / "Korea_Export_Data" / "한국수출입데이터_수집_V6.ipynb"
START, END = "200701", "202608"


def target_codes() -> list:
    t = pd.read_csv(output_path("2c", "step2b", "a_diagnose") / "a_cause_table.csv", dtype=str)
    codes = set(t.loc[t["class"] == "A", "code"])
    ch = pd.read_csv(output_path("2c", "step2b", "a_diagnose") / "a_children_from_api.csv", dtype=str)
    codes |= set(ch.loc[ch["in_db"] == "False", "child6"])
    return sorted(codes)


def coverage(codes) -> pd.DataFrame:
    f, p = db.in_clause("c", codes)
    d = db.read_sql(f"SELECT root_hs_code code, MIN(date) first, MAX(date) last, COUNT(DISTINCT date) n_months "
                    f"FROM korea_monthly_trade_data WHERE indicator='expDlr' AND root_hs_code IN {f} GROUP BY root_hs_code", p)
    d["code"] = d["code"].astype(str)
    return pd.DataFrame({"code": codes}).merge(d, on="code", how="left")


def collector_source(codes) -> str:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    src = "".join(nb["cells"][0]["source"])
    subs = {r"^START_YYYYMM\s*=.*$": f'START_YYYYMM = "{START}"', r"^END_YYYYMM\s*=.*$": f'END_YYYYMM   = "{END}"',
            r"^TARGET_CODES\s*=.*$": f"TARGET_CODES = {codes!r}", r"^AUTO_UPLOAD\s*=.*$": "AUTO_UPLOAD = True"}
    for pat, rep in subs.items():
        src, n = re.subn(pat, rep, src, count=1, flags=re.M)
        if n != 1:
            raise RuntimeError(f"수집기 설정 줄을 찾지 못함: {pat}")
    return src


def main():
    codes = target_codes()
    before = coverage(codes)
    before.to_csv(OUT / "coverage_before.csv", index=False, encoding="utf-8-sig")
    (OUT / "target_codes.txt").write_text("\n".join(codes), encoding="utf-8")
    src = collector_source(codes)
    (OUT / "collector_cell_as_run.py").write_text(re.sub(r"serviceKey=[^\"&]+", "serviceKey=***", src), encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(REPO)
    ns = {"__name__": "collector"}
    try:
        exec(compile(src, str(NOTEBOOK), "exec"), ns)
    finally:
        os.chdir(cwd)
    out = ns.get("out") or {}
    after = coverage(codes)
    after.to_csv(OUT / "coverage_after.csv", index=False, encoding="utf-8-sig")
    log = {"n_codes": len(codes), "errors": out.get("errors"), "empty_hs_count": out.get("empty_hs_count"),
           "monthly_long_rows": len(out.get("monthly_long", [])), "quarterly_long_rows": len(out.get("quarterly_long", []))}
    (OUT / "collect_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.set_option("display.width", 200)
    print(log)
    print(before.merge(after, on="code", suffixes=("_before", "_after")).to_string(index=False))


if __name__ == "__main__":
    main()
