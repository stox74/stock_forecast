from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from bok_client import BOKClient, SeriesSpec


def build_specs_from_index_map(
    index_map: Dict[str, dict],
    names: Optional[Sequence[str]] = None,
    *,
    default_cycle: str = "M",
    default_start: str = "200001",
    default_end: str = "202512",
    lang: str = "kr",
    overrides: Optional[Dict[str, dict]] = None,
) -> List[SeriesSpec]:
    """Convert mapping dict into a list of SeriesSpec.

    index_map[name] should contain:
      - stat_code (str)
      - items (list/tuple; may be empty)

    overrides (optional) can specify per-series:
      - cycle, start, end, items, stat_code, lang
    """
    overrides = overrides or {}
    chosen = list(names) if names is not None else list(index_map.keys())

    specs: List[SeriesSpec] = []
    for name in chosen:
        base = index_map[name]
        ov = overrides.get(name, {})
        specs.append(
            SeriesSpec(
                name=name,
                stat_code=str(ov.get("stat_code", base.get("stat_code"))),
                items=tuple(ov.get("items", base.get("items", [])) or ()),
                cycle=str(ov.get("cycle", default_cycle)),
                start=str(ov.get("start", default_start)),
                end=str(ov.get("end", default_end)),
                lang=str(ov.get("lang", lang)),
            )
        )
    return specs


def fetch_economic_indicators(
    api_key: str,
    specs: Iterable[SeriesSpec],
    *,
    fail_fast: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Download multiple ECOS series and return dict(name -> DataFrame)."""
    client = BOKClient(api_key=api_key)
    return client.fetch_many(specs, fail_fast=fail_fast)


def to_wide(results: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Convert results dict to a single wide DataFrame.

    Returns a DataFrame with column 'Date' and one column per series name.
    Accepts both 'Date' and 'date' in per-series frames.
    """
    frames = []
    for name, df in results.items():
        if df is None or df.empty:
            continue

        date_col = "Date" if "Date" in df.columns else ("date" if "date" in df.columns else None)
        if date_col is None:
            raise ValueError(f"Expected a date column in '{name}'. columns={list(df.columns)}")

        tmp = df[[date_col, "value"]].copy()
        tmp = tmp.rename(columns={date_col: "Date", "value": name})
        frames.append(tmp)

    if not frames:
        return pd.DataFrame()

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="Date", how="outer")

    out = out.sort_values("Date").reset_index(drop=True)
    return out
