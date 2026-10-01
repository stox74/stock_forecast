# fs_core.py
import re
import numpy as np
import pandas as pd
from typing import Optional, List
from difflib import SequenceMatcher


def safe_divide(num, den):
    result = num / den
    if isinstance(result, (pd.Series, pd.DataFrame)):
        return result.replace([np.inf, -np.inf], np.nan)
    return np.nan if (den == 0) else result


def cumulative_to_quarterly(
    df: pd.DataFrame,
    value_cols: List[str],
    exclude_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    연도별 누적값(1Q,2Q,3Q,4Q)을 순수 분기값으로 변환.
    df.index: 분기말 날짜
    """
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()

    if exclude_date is not None:
        out = out.loc[out.index != pd.to_datetime(exclude_date)].copy()

    years = out.index.year

    def _to_quarterly(s: pd.Series) -> pd.Series:
        s = s.sort_index()
        q = s.diff()
        if len(s) > 0:
            q.iloc[0] = s.iloc[0]
        return q

    for col in value_cols:
        out[col] = (
            out[col]
            .groupby(years)
            .apply(_to_quarterly)
            .reset_index(level=0, drop=True)
        )

    return out


def adjust_quarterly_q4_only(df: pd.DataFrame, value_cols: List[str]) -> pd.DataFrame:
    """
    4Q(12월) 값이 FY(연간 누적)로 들어온 경우:
    Q4 = FY - (Q1+Q2+Q3)
    """
    out = df.copy()
    out.index = pd.to_datetime(out.index)

    for year in sorted(out.index.year.unique()):
        sub = out.loc[out.index.year == year].sort_index()
        q1_idx = sub.index[sub.index.month == 3]
        q2_idx = sub.index[sub.index.month == 6]
        q3_idx = sub.index[sub.index.month == 9]
        q4_idx = sub.index[sub.index.month == 12]
        if len(q4_idx) == 0:
            continue

        q4_i = q4_idx[0]
        for col in value_cols:
            if col not in out.columns:
                continue
            fy = out.loc[q4_i, col]
            if pd.isna(fy):
                continue

            prev_sum = (
                out.loc[q1_idx, col].fillna(0).sum()
                + out.loc[q2_idx, col].fillna(0).sum()
                + out.loc[q3_idx, col].fillna(0).sum()
            )
            out.loc[q4_i, col] = fy - prev_sum

    return out


def merge_similar_columns_smart(df: pd.DataFrame, threshold: float = 0.75) -> pd.DataFrame:
    """
    pivot table에서 유사 컬럼을 자동 병합
    """
    out = df.copy()

    def normalize(col):
        col = str(col)
        col = re.sub(r"\(.*?\)", "", col)
        col = col.replace(" ", "")
        col = re.sub(r"[^가-힣A-Za-z]", "", col)
        return col

    columns = list(out.columns)
    norm_cols = [normalize(c) for c in columns]

    groups = {}
    used = set()

    for i, base in enumerate(norm_cols):
        if i in used:
            continue
        groups[columns[i]] = [columns[i]]
        used.add(i)

        for j in range(i + 1, len(columns)):
            if j in used:
                continue
            ratio = SequenceMatcher(None, base, norm_cols[j]).ratio()
            if ratio >= threshold:
                groups[columns[i]].append(columns[j])
                used.add(j)

    for base_col, cols in groups.items():
        for col in cols:
            if col == base_col:
                continue
            out[base_col] = out[base_col].combine_first(out[col])
            out = out.drop(columns=[col])

    return out


def make_pivot(df: pd.DataFrame, target_name: str) -> pd.DataFrame:
    """
    report_date index × thstrm_amount values로 pivot 후,
    여러 account_nm이 생기면 자동으로 1개로 통합.
    """
    if df.empty:
        return pd.DataFrame()

    tmp = df.copy()
    tmp["report_date"] = pd.to_datetime(tmp["report_date"])

    def norm(x):
        x = str(x)
        x = re.sub(r"\s+", "", x)
        x = re.sub(r"\(.*?\)", "", x)
        x = re.sub(r"[^가-힣A-Za-z]", "", x)
        return x

    tmp["account_nm_rep"] = tmp["account_nm"].apply(norm)

    pivot_df = tmp.pivot_table(
        index="report_date",
        columns="account_nm_rep",
        values="thstrm_amount",
        aggfunc="sum"
    ).sort_index()

    cols = pivot_df.columns.tolist()
    if len(cols) == 0:
        return pd.DataFrame()
    if len(cols) == 1:
        pivot_df.columns = [target_name]
        return pivot_df

    merged = pd.Series(index=pivot_df.index, dtype="float64")
    for c in cols:
        merged = merged.combine_first(pivot_df[c])

    sum_col = pivot_df.sum(axis=1)
    merged_final = sum_col if sum_col.notna().sum() >= merged.notna().sum() else merged

    return pd.DataFrame({target_name: merged_final})
