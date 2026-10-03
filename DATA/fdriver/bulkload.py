# -*- coding: utf-8 -*-
"""일괄 적재일 판별 (config.BULK_LOAD).

테이블마다 "하루에 처음 수집된 행"을 날짜별로 모아 일괄 적재일인지 판정한다.
판정 결과는 프로세스 안에서 캐시한다 (clear_cache 로 비움).
"""
from typing import Dict, List, Tuple

import pandas as pd

from . import db
from .config import BULK_LOAD

_CACHE: Dict[Tuple[str, str, str], List[pd.Timestamp]] = {}


def classify_days(daily: pd.DataFrame, cfg: dict = BULK_LOAD) -> pd.DataFrame:
    """daily: 컬럼 d(수집일), n(행 수), n_periods(서로 다른 기간 수), p1, p2(기간 최소·최대).
    is_bulk 컬럼을 붙여 돌려준다."""
    out = daily.copy()
    if out.empty:
        out["is_bulk"] = pd.Series(dtype=bool)
        return out
    total = out["n"].sum()
    span = (pd.to_datetime(out["p2"]) - pd.to_datetime(out["p1"])).dt.days
    size = (out["n"] / total >= cfg["min_share"]) | (out["n"] >= cfg["min_rows"])
    by_size = size & (span >= cfg["min_span_days"])
    by_history = (out["n_periods"] >= cfg["history_periods"]) & (span >= cfg["history_span_days"])
    out["span_days"] = span
    out["is_bulk"] = by_size | by_history
    return out


def daily_first_collected(table: str, observed_col: str, period_col: str) -> pd.DataFrame:
    t, o, p = (db.check_ident(x) for x in (table, observed_col, period_col))
    df = db.read_sql(
        f"SELECT DATE(`{o}`) AS d, COUNT(*) AS n, COUNT(DISTINCT `{p}`) AS n_periods, "
        f"MIN(`{p}`) AS p1, MAX(`{p}`) AS p2 FROM `{t}` WHERE `{o}` IS NOT NULL GROUP BY DATE(`{o}`) ORDER BY d"
    )
    df["d"] = pd.to_datetime(df["d"])
    return df


def bulk_dates(table: str, observed_col: str, period_col: str = "period_end") -> List[pd.Timestamp]:
    """테이블의 일괄 적재일 목록. config 의 dates 에 있으면 그 값을 쓴다."""
    if observed_col not in BULK_LOAD["observed_columns"]:
        return []
    override = BULK_LOAD.get("dates", {})
    if table in override:
        return [pd.Timestamp(x).normalize() for x in override[table]]
    key = (table, observed_col, period_col)
    if key not in _CACHE:
        cls = classify_days(daily_first_collected(table, observed_col, period_col))
        _CACHE[key] = [pd.Timestamp(x).normalize() for x in cls.loc[cls["is_bulk"], "d"]]
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()
