# -*- coding: utf-8 -*-
"""adapter 공통: 반환 형식 맞추기, 기간 인자 처리."""
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import COMMON_COLUMNS

MIN_DATE = "1900-01-01"
MAX_DATE = "2999-12-31"


def date_range_params(start, end) -> Tuple[str, str]:
    s = pd.Timestamp(start).strftime("%Y-%m-%d") if start is not None else MIN_DATE
    e = pd.Timestamp(end).strftime("%Y-%m-%d") if end is not None else MAX_DATE
    return s, e


def as_list(x) -> List:
    if x is None:
        return []
    if isinstance(x, (str, bytes)) or np.isscalar(x):
        return [x]
    return list(x)


def require_str_codes(codes: Iterable, what: str) -> List[str]:
    """코드는 문자열만 받는다. 숫자를 받으면 앞자리 0이 이미 사라졌을 수 있으므로 거부."""
    out = []
    for c in codes:
        if not isinstance(c, str):
            raise TypeError(f"{what} must be str (got {type(c).__name__}: {c!r}); leading zeros may be lost")
        out.append(c.strip())
    return out


def empty_frame() -> pd.DataFrame:
    return finalize(pd.DataFrame(columns=COMMON_COLUMNS))


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """COMMON_COLUMNS 순서·형식으로 맞춘다."""
    out = df.copy()
    for c in COMMON_COLUMNS:
        if c not in out.columns:
            out[c] = None
    out = out[COMMON_COLUMNS]
    out["key"] = out["key"].astype(str)
    out["period_end"] = pd.to_datetime(out["period_end"]).dt.normalize()
    out["available_at"] = pd.to_datetime(out["available_at"]).dt.normalize()
    out["value"] = pd.to_numeric(out["value"], errors="coerce").astype("float64")
    for c in ["freq", "item", "asof_quality", "source"]:
        out[c] = out[c].astype(str)
    out["asof_note"] = out["asof_note"].fillna("").astype(str)
    for c in ["unit", "currency"]:
        out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out.sort_values(["key", "item", "period_end", "available_at"]).reset_index(drop=True)
