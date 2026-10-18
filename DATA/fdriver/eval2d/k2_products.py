# -*- coding: utf-8 -*-
"""2d 작업 2 B1-1: DART 사업보고서 '주요 제품' 표 추출 (명세서 2d v2 4.1 타당성 필터 입력). DB 쓰기 없음.

- 대상: k2_targets.csv 의 confirmed 기업
- 보고서: 선정 구간 마지막 사업연도 2023 사업보고서 원공시 (없거나 받기 실패면 2022). 2024 이후 보고서는 쓰지 않는다.
- 표 읽기: 2c 의 stage2c.segments.find_section / parse_rows (K1 시험과 같음). 합계·소계 행과 금액 0 이하 행은 뺀다.
- 판정: 표를 찾고 남은 행의 비율 합이 90~110% 면 'ok', 표는 찾았지만 비율 합이 밖이면 'pct_mismatch', 못 찾으면 'no_table'.
- 캐시: 2d/K2/cache/lists (공시목록), 2d/K1/cache/annual_docs (원문, K1 과 공유)
결과: 2d/K2/k2_products.csv (기업 x 행), k2_products_status.csv (기업별 판정). 이미 처리한 기업은 다시 받지 않는다.
실행: python -m DATA.fdriver.eval2d.k2_products
"""
import logging
import re
import time

import pandas as pd

from .. import events
from ..config import output_path
from ..stage2c import segments as S

log = logging.getLogger(__name__)
OUT = output_path("2d", "K2")
LISTS = output_path("2d", "K2", "cache", "lists")
DOCS = output_path("2d", "K1", "cache", "annual_docs")
YEARS = (2023, 2022)
ANNUAL = re.compile(r"^(\[[^\]]+\])?사업보고서\((\d{4})\.(\d{2})\)$")


def annual_list(ticker: str, corp_code: str) -> pd.DataFrame:
    f = LISTS / f"{ticker}.csv"
    if f.exists():
        lst = pd.read_csv(f, dtype=str)
    else:
        lst = events.fetch_list(corp_code, bgn="20220101", end="20241231")
        lst.to_csv(f, index=False, encoding="utf-8-sig")
    rows = []
    for r in lst.itertuples():
        m = ANNUAL.match(events.norm_title(str(r.report_nm)))
        if m and not m.group(1):
            rows.append({"fy": int(m.group(2)), "rcept_no": r.rcept_no, "rcept_dt": r.rcept_dt})
    if not rows:
        return pd.DataFrame(columns=["fy", "rcept_no", "rcept_dt"])
    return pd.DataFrame(rows).sort_values("rcept_dt").drop_duplicates("fy")


def extract(ticker: str, corp_code: str):
    ann = annual_list(ticker, corp_code)
    last = {"status": "no_report", "fy": None, "rcept_no": None}
    for fy in YEARS:
        a = ann[ann["fy"] == fy]
        if a.empty:
            continue
        rno = a["rcept_no"].iloc[0]
        t = events.doc_text(rno, DOCS)
        if t.startswith("__ERROR__"):
            last = {"status": "doc_error", "fy": fy, "rcept_no": rno}
            continue
        pat, s = S.find_section(t)
        if not pat:
            last = {"status": "no_table", "fy": fy, "rcept_no": rno}
            continue
        keep = [r for r in S.parse_rows(s) if not S.SKIP.search(r["label"][-25:]) and r["amount"] > 0]
        pct = sum(r["pct"] for r in keep)
        status = "ok" if keep and 90 <= pct <= 110 else "pct_mismatch"
        rows = [{"ticker": ticker, "fy": fy, "row": i, "label": re.sub(r"\s+", " ", r["label"].replace("&cr;", " "))[-200:],
                 "amount": r["amount"], "pct": r["pct"]} for i, r in enumerate(keep)]
        res = {"status": status, "fy": fy, "rcept_no": rno, "section": pat, "n_rows": len(keep), "pct_sum": round(pct, 1),
               "section_text": re.sub(r"\s+", " ", s)[:1500]}
        if status == "ok":
            return res, rows
        last, last_rows = res, rows
    if last.get("status") == "pct_mismatch":
        return last, last_rows
    return last, []


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = pd.read_csv(OUT / "k2_targets.csv", dtype={"ticker": str})
    t = t[t["confirmed"]]
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "corp_code": str})
    t = t.merge(k1[["ticker", "corp_code"]], on="ticker", how="left")
    fs, fr = OUT / "k2_products_status.csv", OUT / "k2_products.csv"
    done = pd.read_csv(fs, dtype={"ticker": str}) if fs.exists() else pd.DataFrame(columns=["ticker"])
    rows_old = pd.read_csv(fr, dtype={"ticker": str}) if fr.exists() else pd.DataFrame()
    st, rows = [], []
    todo = t[~t["ticker"].isin(set(done["ticker"]))]
    for i, r in enumerate(todo.itertuples()):
        try:
            res, rr = extract(r.ticker, r.corp_code)
        except Exception as e:      # 한 기업 실패가 전체를 멈추지 않게 기록만
            res, rr = {"status": "error", "error": repr(e)[:200]}, []
            time.sleep(3)
        st.append({"ticker": r.ticker, "name": r.name, **res})
        rows += rr
        if (i + 1) % 25 == 0 or i + 1 == len(todo):
            pd.concat([done, pd.DataFrame(st)], ignore_index=True).to_csv(fs, index=False, encoding="utf-8-sig")
            pd.concat([rows_old, pd.DataFrame(rows)], ignore_index=True).to_csv(fr, index=False, encoding="utf-8-sig")
            log.info("%d/%d", i + 1, len(todo))
    s = pd.read_csv(fs, dtype={"ticker": str})
    print(s["status"].value_counts().to_dict())


if __name__ == "__main__":
    main()
