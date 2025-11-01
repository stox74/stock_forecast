# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from pandas.tseries.offsets import MonthEnd

def identify_revenue_columns(columns):
    """매출 관련 컬럼 식별"""
    rev_cols = [c for c in columns if c.startswith("revenue_billions")]
    model_map = {"sarima": [], "lstm": [], "prophet": [], "es": []}
    for c in rev_cols:
        low = c.lower()
        if "sarima" in low:
            model_map["sarima"].append(c)
        elif "lstm" in low:
            model_map["lstm"].append(c)
        elif "prophet" in low:
            model_map["prophet"].append(c)
        elif "es" in low:
            model_map["es"].append(c)
    return rev_cols, model_map

def identify_valuation_columns(columns):
    """밸류에이션 관련 컬럼 식별"""
    val_cols = [c for c in columns if c.endswith("_valuation")]
    model_map = {"sarima": None, "lstm": None, "prophet": None, "es": None}
    for c in val_cols:
        low = c.lower()
        if "sarima" in low:
            model_map["sarima"] = c
        elif "lstm" in low:
            model_map["lstm"] = c
        elif "prophet" in low:
            model_map["prophet"] = c
        elif "es" in low:
            model_map["es"] = c
    return val_cols, model_map

def compute_growth(series: pd.Series, start_dt: pd.Timestamp) -> dict:
    """성장률 계산"""
    s = series.dropna()
    s = s.loc[s.index >= start_dt]
    if s.empty:
        return {"start_value": np.nan, "end_value": np.nan, "growth": np.nan}
    start_value = s.iloc[0]
    end_value = s.iloc[-1]
    if pd.isna(start_value) or start_value == 0:
        growth = np.nan
    else:
        growth = (end_value / start_value) - 1.0
    return {"start_value": start_value, "end_value": end_value, "growth": growth}

def make_growth_summaries(df: pd.DataFrame):
    """성장 요약 생성"""
    d = df.copy()

    if "date_month_end" not in d.columns:
        if "index" in d.columns:
            d["date_month_end"] = pd.to_datetime(d["index"], errors="coerce")
        else:
            idx_dt = pd.to_datetime(d.index, errors="coerce")
            if idx_dt.notna().any():
                d = d.reset_index().rename(columns={"index": "date_month_end"})
                d["date_month_end"] = pd.to_datetime(d["date_month_end"], errors="coerce")
            else:
                raise KeyError("날짜가 들어있는 'index' 컬럼(또는 date_month_end)을 찾을 수 없습니다.")
    d["date_month_end"] = (d["date_month_end"] + MonthEnd(0))
    d = d.dropna(subset=["date_month_end"])

    if "ticker" not in d.columns:
        d["ticker"] = d.get("Ticker", d.get("symbol", "UNKNOWN"))

    d = d.sort_values(["ticker", "date_month_end"]).reset_index(drop=True)

    rev_cols, rev_model_map = identify_revenue_columns(d.columns)
    val_cols, val_model_map = identify_valuation_columns(d.columns)

    start_dt = (pd.Timestamp.today() + MonthEnd(0)).normalize()

    revenue_growth_rows = []
    valuation_growth_rows = []

    for ticker, g in d.groupby("ticker"):
        g = g.set_index("date_month_end").copy()

        rev_model_cols = {
            "sarima": rev_model_map["sarima"][0] if rev_model_map["sarima"] else None,
            "lstm": rev_model_map["lstm"][0] if rev_model_map["lstm"] else None,
            "prophet": rev_model_map["prophet"][0] if rev_model_map["prophet"] else None,
            "es": rev_model_map["es"][0] if rev_model_map["es"] else None,
        }
        for model, col in rev_model_cols.items():
            if col is None or col not in g.columns:
                continue
            m = compute_growth(g[col], start_dt)
            revenue_growth_rows.append({
                "ticker": ticker,
                "series": f"revenue_{model}",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        present_rev_cols = [c for c in rev_model_cols.values() if c and c in g.columns]
        if present_rev_cols:
            g["revenue_avg_of_4"] = g[present_rev_cols].mean(axis=1, skipna=True)
            m = compute_growth(g["revenue_avg_of_4"], start_dt)
            revenue_growth_rows.append({
                "ticker": ticker,
                "series": "revenue_avg_of_4",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        val_model_cols = {k: v for k, v in val_model_map.items() if v is not None and v in g.columns}
        for model, col in val_model_cols.items():
            m = compute_growth(g[col], start_dt)
            valuation_growth_rows.append({
                "ticker": ticker,
                "series": f"valuation_{model}",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

        present_val_cols = list(val_model_cols.values())
        if present_val_cols:
            vals = g[present_val_cols].copy()
            row_min = vals.min(axis=1)
            top3_avg = (vals.sum(axis=1) - row_min) / np.maximum(vals.count(axis=1) - 1, 1)
            g["valuation_avg_top3"] = top3_avg
            m = compute_growth(g["valuation_avg_top3"], start_dt)
            valuation_growth_rows.append({
                "ticker": ticker,
                "series": "valuation_avg_top3",
                "start_date": start_dt.date(),
                "start_value": m["start_value"],
                "end_value": m["end_value"],
                "growth": m["growth"],
            })

    revenue_growth_summary = pd.DataFrame(revenue_growth_rows)
    valuation_growth_summary = pd.DataFrame(valuation_growth_rows)
    return revenue_growth_summary, valuation_growth_summary

def to_long(df: pd.DataFrame, category: str) -> pd.DataFrame:
    """Wide 형식을 Long 형식으로 변환"""
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ticker", "category", "model", "start_month_end", "start_value", "end_value", "growth", "created_at"
        ])

    out = df.copy()

    if "start_date" in out.columns:
        out = out.rename(columns={"start_date": "start_month_end"})

    out["category"] = category
    prefix = f"{category}_"
    out["model"] = out["series"].str.replace(prefix, "", regex=False)

    out["start_month_end"] = pd.to_datetime(out["start_month_end"], errors="coerce")
    out["created_at"] = pd.Timestamp.utcnow()

    cols = ["ticker", "category", "model", "start_month_end", "start_value", "end_value", "growth", "created_at"]
    return out[cols]
