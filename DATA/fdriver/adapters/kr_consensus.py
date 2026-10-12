# -*- coding: utf-8 -*-
"""매출 컨센서스 읽기 (2b 명세서 v1 4절): kr_revenue_consensus (월별), kr_revenue_consensus_daily (2023 일별).

read_consensus(ticker, target_quarter, as_of, source="monthly"|"daily", strict=False)
  대상 분기(target_quarter: 'YYYYQn' 또는 분기말 날짜)의 컨센서스 중 관측일(asof_date)이 as_of 이하(strict=True 면 미만)인
  가장 최근 값과 그 관측일. 억원 -> 원 (이벤트표 실제 매출 단위). 없으면 {"value": None, "asof": None}.
  - C_pre5  = read_consensus(t, q, pre5 기준일)                 (관측일 <= pre5)
  - C_final = read_consensus(t, q, 발표일, strict=True)          (관측일 < 발표일)
history(tickers, source): 관측 이력 전체 (한 번 읽어 여러 번 쓰기용). read_consensus 는 내부 캐시를 쓴다.
테이블은 읽기만 한다 (복사본을 만들지 않음).
"""
from functools import lru_cache
from typing import Dict, Optional, Union

import pandas as pd

from .. import db

TABLES = {"monthly": "kr_revenue_consensus", "daily": "kr_revenue_consensus_daily"}
UNIT_TO_WON = {"억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}


def quarter_end(q: Union[str, pd.Timestamp]) -> pd.Timestamp:
    if isinstance(q, str) and "Q" in q:
        y, n = int(q[:4]), int(q[-1])
        return pd.Timestamp(y, 3 * n, 1) + pd.offsets.MonthEnd(0)
    return pd.Timestamp(q) + pd.offsets.MonthEnd(0)


@lru_cache(maxsize=4096)
def _hist(ticker: str, source: str) -> pd.DataFrame:
    d = db.read_sql(f"SELECT ticker, target_period_end, asof_date, consensus, unit FROM `{TABLES[source]}` "
                    f"WHERE ticker = :t", {"t": ticker})
    if d.empty:
        return d
    d["target_period_end"] = pd.to_datetime(d["target_period_end"])
    d["asof_date"] = pd.to_datetime(d["asof_date"])
    d["value"] = d["consensus"] * d["unit"].map(UNIT_TO_WON)
    return d.sort_values(["target_period_end", "asof_date"]).reset_index(drop=True)


def history(ticker: str, source: str = "monthly") -> pd.DataFrame:
    return _hist(str(ticker), source).copy()


def read_consensus(ticker: str, target_quarter, as_of, source: str = "monthly", strict: bool = False) -> Dict:
    if source not in TABLES:
        raise ValueError(source)
    h = _hist(str(ticker), source)
    out = {"value": None, "asof": None}
    if h.empty or as_of is None or pd.isna(as_of):
        return out
    q = quarter_end(target_quarter)
    t = pd.Timestamp(as_of)
    x = h[h["target_period_end"] == q]
    x = x[x["asof_date"] < t] if strict else x[x["asof_date"] <= t]
    if x.empty:
        return out
    r = x.iloc[-1]
    return {"value": float(r["value"]), "asof": r["asof_date"]}


def clear_cache() -> None:
    _hist.cache_clear()
