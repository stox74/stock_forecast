from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd
import requests


BASE_URL = "https://ecos.bok.or.kr/api"


class EcosAPIError(RuntimeError):
    """Raised when ECOS returns an error payload."""

    def __init__(self, code: str, message: str, raw: Any = None):
        super().__init__(f"ECOS API error: {code} | {message}")
        self.code = code
        self.message = message
        self.raw = raw


def _coerce_items(items: Optional[Sequence[Union[str, int]]]) -> Tuple[str, str, str, str]:
    """items -> (item1,item2,item3,item4). Missing positions are filled with '?'"""
    if items is None:
        return "?", "?", "?", "?"
    if not isinstance(items, (list, tuple)):
        raise TypeError("items must be a list/tuple, e.g. items=['10','T23']")
    if len(items) > 4:
        raise ValueError("items supports up to 4 codes (item1~item4).")
    padded: List[str] = []
    for x in list(items) + ["?"] * (4 - len(items)):
        if x is None:
            padded.append("?")
        else:
            s = str(x).strip()
            padded.append(s if s else "?")
    return padded[0], padded[1], padded[2], padded[3]


@dataclass(frozen=True)
class SeriesSpec:
    """Manual ECOS series specification (stat_code + items + cycle + date range)."""

    name: str
    stat_code: str
    items: Sequence[Union[str, int]] = ()
    cycle: str = "M"  # M, Q, D, A (etc.)
    start: str = "200001"   # cycle-dependent format, e.g. 200001 / 2000Q1 / 20000101
    end: str = "202512"
    lang: str = "kr"


class BOKClient:
    """Small, robust ECOS client for StatisticSearch."""

    def __init__(self, api_key: Optional[str] = None, session: Optional[requests.Session] = None, timeout: int = 30):
        self.api_key = api_key or os.getenv("ECOS_API_KEY", "")
        if not self.api_key:
            raise ValueError("ECOS API key is empty. Pass api_key=... or set env ECOS_API_KEY.")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get_json(self, service: str, *parts: str) -> Dict[str, Any]:
        url = f"{BASE_URL}/{service}/" + "/".join(parts)
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        # ECOS error payload often looks like {"RESULT":{"CODE":"...","MESSAGE":"..."}}
        if isinstance(data, dict) and "RESULT" in data and isinstance(data["RESULT"], dict):
            code = str(data["RESULT"].get("CODE", ""))
            msg = str(data["RESULT"].get("MESSAGE", ""))
            # Some successful responses also include RESULT; treat non-00 as error.
            if code and code != "00":
                raise EcosAPIError(code, msg, raw=data)

        return data

    def statistic_search(
        self,
        stat_code: str,
        cycle: str,
        start: str,
        end: str,
        items: Optional[Sequence[Union[str, int]]] = None,
        lang: str = "kr",
        start_row: int = 1,
        end_row: int = 100000,
    ) -> pd.DataFrame:
        """Call StatisticSearch and return a normalized DataFrame with date/value."""
        item1, item2, item3, item4 = _coerce_items(items)

        data = self._get_json(
            "StatisticSearch",
            self.api_key, "json", lang,
            str(start_row), str(end_row),
            str(stat_code), str(cycle), str(start), str(end),
            str(item1), str(item2), str(item3), str(item4),
        )

        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            # Return empty DF (caller can decide)
            return pd.DataFrame(columns=["date", "value", "TIME", "DATA_VALUE"])

        df = pd.DataFrame(rows)

        if "TIME" not in df.columns or "DATA_VALUE" not in df.columns:
            # Keep raw; still return something
            df["date"] = pd.NaT
            df["value"] = pd.NA
            return df

        df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
        df["TIME"] = df["TIME"].astype(str)

        # Parse TIME by cycle when possible
        if cycle == "M":
            df["date"] = pd.to_datetime(df["TIME"], format="%Y%m", errors="coerce") + pd.offsets.MonthEnd(0)
        elif cycle == "D":
            df["date"] = pd.to_datetime(df["TIME"], format="%Y%m%d", errors="coerce")
        else:
            # Q/A etc: keep as string
            df["date"] = df["TIME"]

        return df

    def fetch_series(self, spec: SeriesSpec) -> pd.DataFrame:
        df = self.statistic_search(
            stat_code=spec.stat_code,
            cycle=spec.cycle,
            start=spec.start,
            end=spec.end,
            items=list(spec.items) if spec.items else None,
            lang=spec.lang,
        )
        # Normalize output
        if df.empty:
            return pd.DataFrame(columns=["Date", "value", "name", "stat_code", "cycle", "item1", "item2", "item3", "item4"])

        item1, item2, item3, item4 = _coerce_items(spec.items if spec.items else None)

        out = pd.DataFrame()
        out["Date"] = df["date"]
        out["value"] = pd.to_numeric(df["value"], errors="coerce")
        out["name"] = spec.name
        out["stat_code"] = spec.stat_code
        out["cycle"] = spec.cycle
        out["item1"] = item1
        out["item2"] = item2
        out["item3"] = item3
        out["item4"] = item4

        # Drop NA dates when parsed
        if spec.cycle in ("M", "D"):
            out = out.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

        return out

    def fetch_many(self, specs: Iterable[SeriesSpec], fail_fast: bool = False) -> Dict[str, pd.DataFrame]:
        results: Dict[str, pd.DataFrame] = {}
        for spec in specs:
            try:
                df = self.fetch_series(spec)
                results[spec.name] = df
            except Exception as e:
                if fail_fast:
                    raise
                results[spec.name] = pd.DataFrame()  # placeholder
                print(f"[FAIL] {spec.name}: {e}")
        return results
