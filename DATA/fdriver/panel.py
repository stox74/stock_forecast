# -*- coding: utf-8 -*-
"""분기 패널.

to_fiscal_quarter: 월·주·일 시계열을 기업 결산 분기로 묶는다.
  - 금액·물량은 합계, 지수·가격·비율은 평균 (AGG_* 기본값 표, how 로 덮어쓰기)
  - 분기 값의 available_at = 구성 원천 값 중 가장 늦은 날짜, asof_quality = 가장 낮은 등급
  - 구성 값이 MIN_OBS 보다 적은 분기(진행 중인 분기 등)는 버린다

panel(entity_id): 매출과 fd_entity_link 로 연결된 시계열을 분기 단위 긴 표 하나로.
  as_of(panel, d) 가 그대로 작동하도록 긴 표를 유지한다. 분석용 넓은 표는 panel_wide().
"""
import logging
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from . import entity
from .adapters import read_revenue, read_series, read_trade
from .adapters._common import finalize
from .config import COMMON_COLUMNS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 집계 기본값
AGG_BY_ITEM: Dict[str, str] = {
    # 금액·물량: 합계
    "revenue": "sum", "expDlr": "sum", "impDlr": "sum", "balPayments": "sum",
    "expWgt": "sum", "impWgt": "sum", "visitors": "sum", "volume": "sum",
    # 가격·지수·비율: 평균
    "close": "mean", "open": "mean", "high": "mean", "low": "mean", "prc_change": "mean",
    "price": "mean", "price_buybox": "mean", "rating": "mean", "is_oos": "mean",
    "bsr_root": "mean", "bsr_sub": "mean", "bsr_rank_median": "mean",
    "trend_raw": "mean", "trend_anchored": "mean",
    "expDlr_yoy": "mean", "impDlr_yoy": "mean", "forced_sale_ratio_pct": "mean",
    # 잔고(저량): 평균
    "credit_loan_total": "mean", "credit_loan_kospi": "mean", "credit_loan_kosdaq": "mean",
    "stock_lending_total": "mean", "stock_lending_kospi": "mean", "stock_lending_kosdaq": "mean",
    "subscription_loan": "mean", "securities_collateral_loan": "mean",
    "investor_deposit": "mean", "derivatives_deposit": "mean", "retail_rp_balance": "mean",
    "margin_receivable": "mean", "forced_sale_amount": "sum",
    # 누적 개수: 분기 마지막 값
    "review_count": "last",
}
# STD-1 처럼 item 이 'value' 하나인 시계열은 키 -> 원천 테이블 -> 단위 순으로 정한다
AGG_BY_KEY: Dict[str, str] = {
    "HOUST": "mean", "PERMIT": "mean", "HOUST1F": "mean", "HSN1F": "mean",   # 연율 환산(SAAR) 수준
    "RSHPCS": "sum",                                                        # 월간 금액
}
AGG_BY_SOURCE: Dict[str, str] = {"kr_airline_traffic": "sum", "tw_revenue_monthly": "sum"}
AGG_BY_UNIT: Dict[str, str] = {
    "백만원": "sum", "천주10억원": "sum", "persons": "sum", "tons": "sum", "Mil. of $": "sum",
    "2020＝100": "mean", "Index": "mean", "Percent": "mean", "pct": "mean",
}
DEFAULT_AGG = "mean"
MIN_OBS: Dict[str, int] = {"D": 40, "W": 10, "M": 3, "Q": 1}

GRADE_ORDER = {"A": 0, "B": 1, "C": 2}
PANEL_COLUMNS = ["entity_id", "driver", "link_type", "role"] + COMMON_COLUMNS + ["n_obs", "agg"]


def agg_rule(key: str, item: str, source: str, unit, how: Optional[Dict[str, str]] = None) -> str:
    how = how or {}
    for k in (f"{key}:{item}", key, item):
        if k in how:
            return how[k]
    if key in AGG_BY_KEY:
        return AGG_BY_KEY[key]
    if item != "value" and item in AGG_BY_ITEM:
        return AGG_BY_ITEM[item]
    if source in AGG_BY_SOURCE:
        return AGG_BY_SOURCE[source]
    if unit in AGG_BY_UNIT:
        return AGG_BY_UNIT[unit]
    log.warning("no aggregation default for key=%s item=%s source=%s unit=%s -> %s", key, item, source, unit,
                DEFAULT_AGG)
    return DEFAULT_AGG


def fiscal_quarter_end(period_end: pd.Series, fiscal_year_end: int = 12) -> pd.Series:
    """기간말이 속한 결산 분기의 마지막 날. 결산월 F 면 분기말 월은 F, F-3, F-6, F-9."""
    pe = pd.to_datetime(period_end)
    m = pe.dt.month
    offset = (fiscal_year_end - m) % 3
    ym = pe.dt.year * 12 + (m - 1) + offset
    out = pd.to_datetime(dict(year=ym // 12, month=ym % 12 + 1, day=1)) + pd.offsets.MonthEnd(0)
    return pd.Series(out.values, index=period_end.index)


def _worst(grades: pd.Series) -> str:
    return max(grades, key=lambda g: GRADE_ORDER.get(g, 9))


def _notes(notes: pd.Series) -> str:
    parts = sorted({p for n in notes if n for p in str(n).split(";") if p})
    return ";".join(parts)


def to_fiscal_quarter(df: pd.DataFrame, fiscal_year_end: int = 12, how: Optional[Dict[str, str]] = None,
                      min_obs: Optional[Dict[str, int]] = None) -> pd.DataFrame:
    """COMMON_COLUMNS 형식의 긴 표를 결산 분기로 묶는다. 반환: COMMON_COLUMNS + n_obs, agg."""
    cols = COMMON_COLUMNS + ["n_obs", "agg"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    mo = {**MIN_OBS, **(min_obs or {})}
    d = df.dropna(subset=["value"]).copy()
    d["q_end"] = fiscal_quarter_end(d["period_end"], fiscal_year_end)
    rows = []
    for (key, item, q), g in d.groupby(["key", "item", "q_end"], sort=True):
        first = g.iloc[0]
        rule = agg_rule(key, item, first["source"], first["unit"], how)
        f = first["freq"]
        if rule == "sum":
            v = g["value"].sum()
        elif rule == "mean":
            v = g["value"].mean()
        elif rule == "last":
            v = g.sort_values("period_end")["value"].iloc[-1]
        else:
            raise ValueError(f"unknown aggregation {rule!r}")
        rows.append({
            "key": key, "period_end": q, "freq": "Q", "item": item, "value": float(v),
            "unit": first["unit"], "currency": first["currency"],
            "available_at": g["available_at"].max(), "asof_quality": _worst(g["asof_quality"]),
            "asof_note": _notes(g["asof_note"]), "source": first["source"],
            "n_obs": int(len(g)), "agg": rule, "_freq_in": f,
        })
    out = pd.DataFrame(rows)
    need = out["_freq_in"].map(lambda f: mo.get(f, 1))
    out = out[out["n_obs"] >= need].drop(columns=["_freq_in"])
    if out.empty:
        return pd.DataFrame(columns=cols)
    fin = finalize(out[COMMON_COLUMNS])
    # finalize 가 정렬하므로 같은 정렬로 extra 를 맞춘다
    out = out.sort_values(["key", "item", "period_end", "available_at"]).reset_index(drop=True)
    fin["n_obs"] = out["n_obs"].values
    fin["agg"] = out["agg"].values
    return fin


def combine_across_keys(df: pd.DataFrame, new_key: str, item: str, how: str = "sum",
                        require_all: bool = True) -> pd.DataFrame:
    """같은 기간의 여러 키를 하나로 (예: 항공사별 여객 합계, ASIN 별 순위 중앙값).
    available_at 은 가장 늦은 날짜, 등급은 가장 낮은 등급."""
    if df.empty:
        return df
    n_keys = df["key"].nunique()
    rows = []
    for pe, g in df.dropna(subset=["value"]).groupby("period_end"):
        if require_all and g["key"].nunique() < n_keys:
            continue
        v = g["value"].sum() if how == "sum" else g["value"].median()
        rows.append({"key": new_key, "period_end": pe, "freq": g["freq"].iloc[0], "item": item, "value": v,
                     "unit": g["unit"].iloc[0], "currency": g["currency"].iloc[0],
                     "available_at": g["available_at"].max(), "asof_quality": _worst(g["asof_quality"]),
                     "asof_note": _notes(g["asof_note"]), "source": g["source"].iloc[0]})
    if not rows:
        return finalize(pd.DataFrame(columns=COMMON_COLUMNS))
    return finalize(pd.DataFrame(rows))


def unit_price(q: pd.DataFrame, value_item: str = "expDlr", weight_item: str = "expWgt") -> pd.DataFrame:
    """분기 금액 / 분기 중량 = 단가 (USD/kg). 두 값 중 늦은 available_at, 낮은 등급."""
    a = q[q["item"] == value_item]
    b = q[q["item"] == weight_item]
    m = a.merge(b, on=["key", "period_end"], suffixes=("", "_w"))
    m = m[m["value_w"] > 0]
    if m.empty:
        return q.iloc[0:0]
    out = pd.DataFrame({
        "key": m["key"], "period_end": m["period_end"], "freq": "Q", "item": "unit_price",
        "value": m["value"] / m["value_w"], "unit": "USD/kg", "currency": "USD",
        "available_at": np.maximum(m["available_at"].values, m["available_at_w"].values),
        "asof_quality": [_worst(pd.Series([x, y])) for x, y in zip(m["asof_quality"], m["asof_quality_w"])],
        "asof_note": [_notes(pd.Series([x, y])) for x, y in zip(m["asof_note"], m["asof_note_w"])],
        "source": m["source"], "n_obs": np.minimum(m["n_obs"].values, m["n_obs_w"].values), "agg": "ratio",
    })
    fin = finalize(out[COMMON_COLUMNS])
    out = out.sort_values(["key", "item", "period_end", "available_at"]).reset_index(drop=True)
    fin["n_obs"] = out["n_obs"].values
    fin["agg"] = out["agg"].values
    return fin


def _label(df: pd.DataFrame, entity_id: str, driver_fn, link_type: str, role: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=PANEL_COLUMNS)
    out = df.copy()
    out["entity_id"] = entity_id
    out["driver"] = [driver_fn(k, i) for k, i in zip(out["key"], out["item"])]
    out["link_type"] = link_type
    out["role"] = role
    if "n_obs" not in out:
        out["n_obs"] = 1
        out["agg"] = "none"
    return out[PANEL_COLUMNS]


def panel(entity_id: str, start=None, end=None, trade_items: Iterable[str] = ("expDlr", "expWgt"),
          expand_alt: bool = False) -> pd.DataFrame:
    """매출 + 연결 시계열의 분기 긴 표 (PANEL_COLUMNS)."""
    info = entity.entity_info([entity_id])
    if info.empty:
        raise KeyError(f"{entity_id} not in fd_entity_master")
    fye = int(info["fiscal_year_end"].iloc[0])
    links = entity.entity_links(entity_id)
    parts = []

    rev = read_revenue([entity_id])
    parts.append(_label(rev, entity_id, lambda k, i: "revenue", "self", "target"))

    def role_of(lt):
        r = links.loc[links["link_type"] == lt, "role"]
        return r.iloc[0] if len(r) else None

    for lt, g in links.groupby("link_type"):
        keys = sorted(g["series_key"].unique())
        fam = g["family"].iloc[0]
        if lt == "hs_code":
            tr = read_trade(keys, items=list(trade_items))
            q = to_fiscal_quarter(tr, fye)
            if {"expDlr", "expWgt"} <= set(trade_items):
                q = pd.concat([q, unit_price(q)], ignore_index=True)
            parts.append(_label(q, entity_id, lambda k, i: f"trade:{k}:{i}", lt, role_of(lt)))
        elif lt == "kr_peer":
            r = read_revenue(keys)
            parts.append(_label(r, entity_id, lambda k, i: f"kr_peer:{k}:revenue", lt, role_of(lt)))
        elif lt == "airline_series":
            m = read_series("airline", keys)
            s = combine_across_keys(m, "intl_dep_sum", "value", how="sum", require_all=True)
            q = to_fiscal_quarter(s, fye, how={"intl_dep_sum": "sum"})
            parts.append(_label(q, entity_id, lambda k, i: "airline:intl_dep_sum", lt, role_of(lt)))
        elif lt == "alt_data":
            for fam2, g2 in g.groupby("family"):
                k2 = sorted(g2["series_key"].unique())
                d = read_series(fam2, k2)
                if fam2 == "amazon_bsr":
                    med = combine_across_keys(d, "median", "bsr_rank_median", how="median", require_all=False)
                    q = to_fiscal_quarter(med, fye)
                    parts.append(_label(q, entity_id, lambda k, i: "amazon_bsr:median_rank", lt, "product"))
                    if expand_alt:
                        q2 = to_fiscal_quarter(d, fye)
                        parts.append(_label(q2, entity_id, lambda k, i: f"amazon_bsr:{k}", lt, "product"))
                else:
                    q = to_fiscal_quarter(d, fye)
                    rmap = dict(zip(g2["series_key"], g2["role"]))
                    lab = _label(q, entity_id, lambda k, i, f=fam2: f"{f}:{k}", lt, None)
                    lab["role"] = lab["key"].map(rmap)
                    parts.append(lab)
        else:
            d = read_series(fam, keys)
            q = to_fiscal_quarter(d, fye)
            parts.append(_label(q, entity_id, lambda k, i, f=fam: f"{f}:{k}", lt, role_of(lt)))

    parts = [p for p in parts if not p.empty]
    out = pd.concat(parts, ignore_index=True)
    if start is not None:
        out = out[out["period_end"] >= pd.Timestamp(start)]
    if end is not None:
        out = out[out["period_end"] <= pd.Timestamp(end)]
    return out.sort_values(["driver", "period_end"]).reset_index(drop=True)


def panel_wide(p: pd.DataFrame) -> pd.DataFrame:
    """분석용: 행 = 분기말, 열 = driver, 값 = value."""
    return p.pivot_table(index="period_end", columns="driver", values="value", aggfunc="first").sort_index()
