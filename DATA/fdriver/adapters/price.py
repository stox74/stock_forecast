# -*- coding: utf-8 -*-
"""주가: KSE_Price (수정주가, code 6자리 문자열, utf16 컬럼)."""
from typing import Iterable, Optional, Union

import pandas as pd

from .. import asof, db
from ._common import as_list, date_range_params, empty_frame, finalize, require_str_codes

SOURCE = "KSE_Price"
FIELDS = {
    "open": "KRW", "high": "KRW", "low": "KRW", "close": "KRW",
    "volume": "shares", "prc_change": "pct",
}
# 하루에 이 수 이상 종목이 있어야 거래일로 본다 (부분 적재일 제외)
MIN_CODES_PER_DAY = 100
# 거래일 달력 보완용 지수 (market_index.KOSPI)
INDEX_TABLE = "kr_stock_index_daily"
INDEX_FOR_CALENDAR = "YAHOO:^KS11"


def read_price(entity_ids: Iterable[str], start=None, end=None,
               field: Union[str, Iterable[str]] = "close") -> pd.DataFrame:
    ids = require_str_codes(as_list(entity_ids), "entity_ids")
    fields = as_list(field)
    bad = [f for f in fields if f not in FIELDS]
    if bad:
        raise ValueError(f"unknown price field(s): {bad}; allowed {list(FIELDS)}")
    if not ids:
        return empty_frame()
    s, e = date_range_params(start, end)
    frag, params = db.in_clause("code", ids)
    cols = ", ".join(f"`{f}`" for f in fields)
    raw = db.read_sql(
        f"SELECT `date`, `code`, {cols} FROM `KSE_Price` WHERE `code` IN {frag} AND `date` BETWEEN :s AND :e",
        {**params, "s": s, "e": e},
    )
    if raw.empty:
        return empty_frame()
    long = raw.melt(id_vars=["date", "code"], value_vars=fields, var_name="item", value_name="value")
    long = long.rename(columns={"code": "key", "date": "period_end"})
    long["key"] = long["key"].astype(str)
    long["freq"] = "D"
    long["unit"] = long["item"].map(FIELDS)
    long["currency"] = long["item"].map(lambda f: "KRW" if FIELDS[f] == "KRW" else None)
    long["source"] = SOURCE
    return finalize(asof.stamp(long, "price"))


def trading_calendar(start=None, end=None, register: bool = True) -> pd.DatetimeIndex:
    """KSE_Price 와 코스피 지수 날짜로 거래일 목록을 만든다. register=True 면 asof 의 영업일 계산에 등록."""
    s, e = date_range_params(start, end)
    df = db.read_sql(
        "SELECT `date` FROM `KSE_Price` WHERE `date` BETWEEN :s AND :e GROUP BY `date` HAVING COUNT(*) >= :m",
        {"s": s, "e": e, "m": MIN_CODES_PER_DAY},
    )
    days = pd.DatetimeIndex(pd.to_datetime(df["date"]))
    # KSE_Price 는 2013-05-21 부터라, 그 이전과 빠진 날은 코스피 지수 날짜로 보탠다 (2010-01-04 ~)
    if db.table_exists(INDEX_TABLE):
        ix = db.read_sql(f"SELECT `period_end` FROM `{INDEX_TABLE}` WHERE `idx_name` = :n AND `period_end` BETWEEN :s AND :e",
                         {"n": INDEX_FOR_CALENDAR, "s": s, "e": e})
        days = days.union(pd.DatetimeIndex(pd.to_datetime(ix["period_end"])))
    days = days.sort_values()
    if register:
        asof.set_trading_calendar(days)
    return days
