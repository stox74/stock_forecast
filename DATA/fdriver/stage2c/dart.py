# -*- coding: utf-8 -*-
"""2c 작업 2: DART 공시 목록·사업보고서 본문 캐시 (2c/step2/cache). 받은 것은 다시 받지 않는다."""
import re
from pathlib import Path

import pandas as pd

from .. import entity, events
from ..config import output_path

ANNUAL_RE = re.compile(r"^(\[[^\]]+\])?사업보고서\((\d{4})\.(\d{2})\)$")


def cache_dir(*parts: str) -> Path:
    return output_path("2c", "step2", "cache", *parts)


def dart_list(entity_id: str, refresh: bool = False) -> pd.DataFrame:
    """공시 목록 (2010-01-01 ~ 오늘). refresh=True 면 다시 받는다."""
    f = cache_dir("lists") / f"{entity_id}.csv"
    if refresh or not f.exists():
        corp = entity.entity_info([entity_id])["dart_corp_code"].iloc[0]
        lst = events.fetch_list(corp, bgn=events.START)
        lst["entity_id"] = entity_id
        lst.to_csv(f, index=False, encoding="utf-8-sig")
    return pd.read_csv(f, dtype=str)


def annual_reports(entity_id: str, first_year: int = 2012) -> pd.DataFrame:
    """사업보고서 원공시 (사업연도별 가장 이른 접수). 컬럼: fiscal_year, rcept_no, rcept_dt, report_nm, n_corrections."""
    lst = dart_list(entity_id)
    rows = []
    for r in lst.itertuples():
        m = ANNUAL_RE.match(events.norm_title(r.report_nm))
        if m:
            rows.append({"fiscal_year": int(m.group(2)), "rcept_no": r.rcept_no, "rcept_dt": pd.Timestamp(r.rcept_dt),
                         "report_nm": r.report_nm, "is_correction": bool(m.group(1))})
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d = d[d["fiscal_year"] >= first_year]
    n_corr = d[d["is_correction"]].groupby("fiscal_year").size()
    orig = d[~d["is_correction"]].sort_values("rcept_dt").drop_duplicates("fiscal_year")
    orig["n_corrections"] = orig["fiscal_year"].map(n_corr).fillna(0).astype(int)
    return orig.sort_values("fiscal_year").reset_index(drop=True)


def annual_text(rcept_no: str) -> str:
    """사업보고서 본문 (수정한 events.doc_text: zip 에서 본문 파일을 골라 읽음)."""
    return events.doc_text(rcept_no, cache_dir("annual_docs"))
