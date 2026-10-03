# -*- coding: utf-8 -*-
"""수출입: korea_monthly_trade_data.

- root_hs_code 는 utf16 컬럼이라 utf8mb4 테이블(kr_company_hs_map 등)과 SQL JOIN 하면 collation 오류가 난다.
  JOIN 하지 말고 코드 목록을 바인딩해서 조회한다.
- 코드는 문자열만 받는다 (030341 보존). 5자리·9자리 예전 코드는 제외한다.
"""
import logging
from typing import Iterable

import pandas as pd

from .. import asof, db
from ._common import as_list, date_range_params, empty_frame, finalize, require_str_codes

log = logging.getLogger(__name__)

SOURCE = "korea_monthly_trade_data"
ITEMS = {
    # item: (unit, currency)
    "expDlr": ("USD", "USD"), "impDlr": ("USD", "USD"), "balPayments": ("USD", "USD"),
    "expWgt": ("kg", None), "impWgt": ("kg", None),
    "expDlr_yoy": ("yoy_raw", None), "impDlr_yoy": ("yoy_raw", None),
}
LEGACY_LENGTHS = (5, 9)


def _clean_codes(hs_codes) -> list:
    codes = require_str_codes(as_list(hs_codes), "hs_codes")
    out = []
    for c in codes:
        if not c.isdigit():
            raise ValueError(f"hs code must be digits: {c!r}")
        if len(c) in LEGACY_LENGTHS:
            log.warning("legacy %d-digit hs code ignored: %s", len(c), c)
            continue
        out.append(c)
    return sorted(set(out))


def read_trade(hs_codes: Iterable[str], items: Iterable[str] = ("expDlr",), start=None, end=None) -> pd.DataFrame:
    codes = _clean_codes(hs_codes)
    items = as_list(items)
    bad = [i for i in items if i not in ITEMS]
    if bad:
        raise ValueError(f"unknown trade item(s): {bad}; allowed {list(ITEMS)}")
    if not codes or not items:
        return empty_frame()
    s, e = date_range_params(start, end)
    fc, pc = db.in_clause("hs", codes)
    fi, pi = db.in_clause("it", items)
    raw = db.read_sql(
        f"SELECT `date`, `root_hs_code`, `indicator`, `value` FROM `korea_monthly_trade_data` "
        f"WHERE `root_hs_code` IN {fc} AND `indicator` IN {fi} AND `date` BETWEEN :s AND :e",
        {**pc, **pi, "s": s, "e": e},
    )
    if raw.empty:
        return empty_frame()
    df = pd.DataFrame({
        "key": raw["root_hs_code"].astype(str),
        "period_end": pd.to_datetime(raw["date"]) + pd.offsets.MonthEnd(0),
        "freq": "M",
        "item": raw["indicator"].astype(str),
        "value": raw["value"].astype("float64"),
    })
    df["unit"] = df["item"].map(lambda i: ITEMS[i][0])
    df["currency"] = df["item"].map(lambda i: ITEMS[i][1])
    df["source"] = SOURCE
    return finalize(asof.stamp(df, "trade"))
