#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
edgar_wrds_filler.py
- EDGAR 분기 재무(특히 revenue)의 결측을 WRDS(us_fundq)의 saleq로 보완
- 서로 다른 분기말 패턴(3-6-9-12 / 1-4-7-10 등) 스냅
- 단위(스케일) 자동 정합
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import pymysql
import sqlalchemy as sa


# ===== 설정 =====
QUARTER_PATTERNS = [(3, 6, 9, 12), (1, 4, 7, 10), (2, 5, 8, 11), (4, 7, 10, 1)]
SCALE_CANDIDATES = [1, 1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9]

def fetch_wrds_fundq_sample(
    db_info: dict,
    ticker: str,
    columns=None,
    table_name: str = "US_fundq",
    date_col: str = "edate",
):
    if columns is None:
        columns = ["edate", "ticker", "saleq"]

    # 1) 컬럼 join (← 핵심 수정)
    #    MySQL/SQLite 안전 위해 컬럼명은 백틱으로 감쌈
    col_str = ", ".join([f"`{c}`" for c in columns])

    # 2) 커넥션
    engine = sa.create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )

    # 3) 안전한 파라미터 바인딩
    sql = f"""
        SELECT {col_str}
        FROM `{table_name}`
        WHERE `ticker` = %(ticker)s
        ORDER BY `{date_col}`
    """

    df = pd.read_sql(sql, engine, params={"ticker": ticker})

    # 4) 날짜 파싱(있으면) + 정렬/인덱스
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(date_col).reset_index(drop=True)

    return df



# ===== 날짜 스냅 & 스케일 추정 =====
def _most_likely_pattern(dates: pd.Index) -> tuple:
    if len(dates) == 0:
        return (3, 6, 9, 12)
    months = pd.Index(pd.to_datetime(dates)).month
    scores = []
    for pat in QUARTER_PATTERNS:
        mask = months.isin(pat)
        score = mask.mean() - (months[mask].value_counts(normalize=True).std() if mask.any() else 0.0)
        scores.append((score, pat))
    scores.sort(reverse=True, key=lambda x: x[0])
    return scores[0][1]


def _to_quarter_eom_index(dates: pd.Index, pattern: tuple, tolerance_days: int = 20) -> pd.Index:
    dates = pd.to_datetime(dates)
    start = dates.min() - pd.DateOffset(years=3)
    end = dates.max() + pd.DateOffset(years=3)
    q_eoms = pd.date_range(start=start, end=end, freq="M")
    q_eoms = q_eoms[q_eoms.month.isin(pattern)]
    tol = pd.Timedelta(days=tolerance_days)

    snapped = []
    for d in dates:
        i = np.argmin(np.abs(q_eoms - d))
        cand = q_eoms[i]
        snapped.append(cand if abs(cand - d) <= tol else pd.NaT)
    return pd.DatetimeIndex(snapped, name="date")


def _overlap_scale_factor(x: pd.Series, y: pd.Series) -> float:
    """
    겹치는 구간에서 y(WRDS)에 곱할 스케일을 탐색 → x(EDGAR)와 가장 유사하게.
    지표: Median Absolute Percentage Error (MAPE) 최소.
    """
    x = pd.to_numeric(x, errors="coerce").dropna()
    y = pd.to_numeric(y, errors="coerce").dropna()
    idx = x.index.intersection(y.index)
    if len(idx) < 3:
        return 1.0
    x = x.loc[idx].astype(float)
    y = y.loc[idx].astype(float)

    best_f, best_err = 1.0, np.inf
    for f in SCALE_CANDIDATES:
        yf = y * f
        denom = np.where(np.abs(x.values) < 1e-12, 1.0, np.abs(x.values))
        mape = np.median(np.abs((yf.values - x.values) / denom))
        if mape < best_err:
            best_err, best_f = mape, f
    return best_f


# ===== 메인: EDGAR revenue 결측 보완 =====
def fill_revenue_with_wrds(
    edgar_df: pd.DataFrame,
    wrds_df: pd.DataFrame,
    wrds_value_col: str = "saleq",
    days_tolerance: int = 20,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    EDGAR 분기 DF의 'revenue' NaN을 WRDS(us_fundq)의 saleq로 보완 (단위 자동 정합).
    어떤 경우에도 DataFrame을 반환(실패 시 원본 정렬 후 반환).
    """
    try:
        if edgar_df is None or len(edgar_df) == 0:
            raise ValueError("edgar_df is empty")

        # --- EDGAR 정리
        e = edgar_df.copy()
        if not isinstance(e.index, pd.DatetimeIndex):
            e.index = pd.to_datetime(e.index, errors="coerce")
        e = e[~e.index.isna()].sort_index()
        if "revenue" not in e.columns:
            e["revenue"] = np.nan

        # --- WRDS 정리
        if wrds_df is None or len(wrds_df) == 0:
            if verbose:
                print("WRDS df is empty → return edgar_df as-is")
            e["revenue_source"] = np.where(e["revenue"].notna(), "EDGAR", "MISSING")
            return e

        w = wrds_df.copy()
        if "edate" not in w.columns:
            raise ValueError("wrds_df must have 'edate' column")
        if wrds_value_col not in w.columns:
            raise ValueError(f"wrds_df must have '{wrds_value_col}' column")
        w["edate"] = pd.to_datetime(w["edate"], errors="coerce")
        w = w.dropna(subset=["edate"]).sort_values("edate")

        # --- 패턴 추정 & 월말 스냅
        pat_e = _most_likely_pattern(e.index)
        pat_w = _most_likely_pattern(w["edate"])
        pattern = pat_e if len(e) >= 3 else pat_w

        e_idx = _to_quarter_eom_index(e.index, pattern, days_tolerance)
        w_idx = _to_quarter_eom_index(w["edate"], pattern, days_tolerance)

        e2 = e.copy(); e2.index = e_idx; e2 = e2[~e2.index.isna()].sort_index()
        w2 = w[[wrds_value_col]].copy(); w2.index = w_idx; w2 = w2[~w2.index.isna()].sort_index()

        e2 = e2[~e2.index.duplicated(keep="last")]
        w2 = w2[~w2.index.duplicated(keep="last")]

        # --- 스케일 추정(겹치는 구간 기준)
        scale = _overlap_scale_factor(e2["revenue"], w2[wrds_value_col]) if e2["revenue"].notna().any() else 1.0
        w2_scaled = (w2[wrds_value_col] * scale).rename("wrds_revenue_scaled")

        # --- 결측 보완
        out = e2.copy()
        need = out["revenue"].isna()
        wrds_aligned = w2_scaled.reindex(out.index)
        out.loc[need, "revenue"] = wrds_aligned[need]
        out["revenue_source"] = np.where(
            need & wrds_aligned.notna(), "WRDS_scaled",
            np.where(out["revenue"].notna(), "EDGAR", "MISSING")
        )

        if verbose:
            filled_cells = int((need & wrds_aligned.notna()).sum())
            print(f"[fill] pattern={pattern}, scale_factor={scale}, filled={filled_cells}")
        return out

    except Exception as ex:
        # 어떤 오류가 나도 DataFrame 반환
        print(f"[fill] ERROR: {ex} → return edgar_df as-is")
        e0 = edgar_df.copy()
        if not isinstance(e0.index, pd.DatetimeIndex):
            e0.index = pd.to_datetime(e0.index, errors="coerce")
            e0 = e0[~e0.index.isna()].sort_index()
        if "revenue" not in e0.columns:
            e0["revenue"] = np.nan
        e0["revenue_source"] = np.where(e0["revenue"].notna(), "EDGAR", "MISSING")
        return e0


__all__ = [
    "fetch_wrds_fundq_sample",
    "fill_revenue_with_wrds",
    "_most_likely_pattern",
    "_to_quarter_eom_index",
    "_overlap_scale_factor",
    "QUARTER_PATTERNS",
    "SCALE_CANDIDATES",
]

