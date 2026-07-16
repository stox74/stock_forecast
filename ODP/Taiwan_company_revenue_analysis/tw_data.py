# -*- coding: utf-8 -*-
"""
tw_data.py — 대만 50개사 월별 매출 전처리 (Python 3.9)

revenue.db(SQLite, tw_revenue_V4.py 가 생성) 를 읽어
  1) 월별 매출 wide DataFrame  (index=날짜, columns=종목코드)
  2) 월별 MoM 변화율 DataFrame
  3) 월별 YoY 변화율 DataFrame
  4) 분기(3개월 groupby) 매출 및 분기 YoY growth
  5) 예측치(forecast 테이블)로 연장한 분기 YoY  ← 미래 분기 예측용
을 제공한다. 단위: NTD 천 (MOPS 원본 단위).
"""
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union

import numpy as np
import pandas as pd

from analysis_config import find_revenue_db


# ----------------------------------------------------------------------
# 로딩
# ----------------------------------------------------------------------
def get_tw_conn(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else find_revenue_db(quiet=True)
    if path is None or not Path(path).exists():
        raise FileNotFoundError(
            "revenue.db 를 찾을 수 없습니다. tw_revenue_V4.py 로 먼저 수집하거나 "
            "환경변수 TW_REVENUE_DB 로 경로를 지정하세요.")
    return sqlite3.connect(str(path))


def load_tw_companies(conn) -> dict:
    """DB에 실제 저장된 기업 목록 {종목코드: 이름}."""
    df = pd.read_sql(
        "SELECT company_id, MAX(company_name) AS name "
        "FROM revenue GROUP BY company_id ORDER BY company_id", conn)
    return dict(zip(df["company_id"], df["name"]))


def load_tw_monthly_long(conn) -> pd.DataFrame:
    """월별 매출 long format: [date, company_id, company_name, revenue]."""
    df = pd.read_sql(
        "SELECT company_id, company_name, year, month, revenue "
        "FROM revenue WHERE revenue IS NOT NULL ORDER BY year, month", conn)
    df["date"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=1))
    return df[["date", "company_id", "company_name", "revenue"]]


# ----------------------------------------------------------------------
# 1) 월별 wide / 2) MoM / 3) YoY
# ----------------------------------------------------------------------
def monthly_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """index=월초 날짜(DatetimeIndex), columns=종목코드, 값=월 매출."""
    wide = long_df.pivot_table(index="date", columns="company_id",
                               values="revenue", aggfunc="last")
    return wide.sort_index()


def monthly_mom(wide: pd.DataFrame) -> pd.DataFrame:
    """월별 실적 변화 (전월 대비 %, MoM)."""
    return wide.pct_change(1) * 100.0


def monthly_yoy(wide: pd.DataFrame) -> pd.DataFrame:
    """월별 실적의 YoY 변화 (전년 동월 대비 %)."""
    return wide.pct_change(12) * 100.0


# ----------------------------------------------------------------------
# 4) 분기 집계 + 분기 YoY growth
# ----------------------------------------------------------------------
def quarterly_revenue(wide: pd.DataFrame, require_full: bool = True) -> pd.DataFrame:
    """
    월별 → 캘린더 분기(3개월) 합산. index=PeriodIndex('Q').
    require_full=True 면 3개월이 모두 있는 분기만 남긴다(부분 분기 왜곡 방지).
    """
    q = wide.copy()
    q.index = pd.PeriodIndex(q.index, freq="Q")
    counts = q.groupby(level=0).count()
    sums = q.groupby(level=0).sum(min_count=1)
    if require_full:
        sums = sums.where(counts >= 3)
    return sums


def quarterly_yoy(q_rev: pd.DataFrame) -> pd.DataFrame:
    """분기 매출의 YoY growth (%, 전년 동분기 대비)."""
    return q_rev.pct_change(4) * 100.0


# ----------------------------------------------------------------------
# 5) 예측치로 연장한 분기 시계열 (미래 분기 YoY 산출용)
# ----------------------------------------------------------------------
def load_tw_forecast_monthly(conn, model: str = "ensemble") -> pd.DataFrame:
    """
    forecast 테이블에서 각 기업의 '최신 basis' 예측만 추출.
    반환: wide (index=월초 날짜, columns=종목코드, 값=예측 매출)
    """
    df = pd.read_sql(
        """
        SELECT f.company_id, f.target_year, f.target_month, f.predicted
        FROM forecast f
        JOIN (
            SELECT company_id, MAX(basis_year*100 + basis_month) AS b
            FROM forecast WHERE model = ? GROUP BY company_id
        ) m ON f.company_id = m.company_id
           AND f.basis_year*100 + f.basis_month = m.b
        WHERE f.model = ?
        ORDER BY f.company_id, f.target_year, f.target_month
        """, conn, params=(model, model))
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(dict(year=df["target_year"],
                                     month=df["target_month"], day=1))
    return df.pivot_table(index="date", columns="company_id",
                          values="predicted", aggfunc="last").sort_index()


def tw_quarterly_extended(conn, model: str = "ensemble"
                          ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """
    실적 + 예측을 이어붙여 분기 매출/분기 YoY 를 만든다.

    Returns
    -------
    q_rev : 분기 매출 (실적+예측 혼합, 3개월 완성 분기만)
    q_yoy : 분기 YoY growth (%)
    is_forecast : 각 분기가 예측치를 포함하는지 (company 무관, 분기 단위 Bool)
    """
    actual = monthly_wide(load_tw_monthly_long(conn))
    fc = load_tw_forecast_monthly(conn, model=model)

    combined = actual.copy()
    if not fc.empty:
        # 실적이 없는 (미래) 월만 예측으로 채움
        combined = actual.combine_first(fc)
        # 단, 각 기업별 마지막 실적 이전의 예측치는 무시(combine_first가 이미 처리)

    q_rev = quarterly_revenue(combined, require_full=True)
    q_yoy = quarterly_yoy(q_rev)

    last_actual_q = pd.Period(actual.index.max(), freq="Q")
    is_forecast = pd.Series(q_rev.index > last_actual_q, index=q_rev.index,
                            name="contains_forecast")
    # 마지막 실적 분기가 미완성(예측으로 보충)인 경우도 표시
    act_q_counts = actual.copy()
    act_q_counts.index = pd.PeriodIndex(act_q_counts.index, freq="Q")
    n_act = act_q_counts.groupby(level=0).count().max(axis=1)
    partial = n_act.reindex(q_rev.index).fillna(0) < 3
    is_forecast = is_forecast | partial
    return q_rev, q_yoy, is_forecast
