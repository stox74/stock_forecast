# -*- coding: utf-8 -*-
"""
kr_us_data.py — 한국/미국 기업 분기 매출 로더 (Python 3.9)

한국: MariaDB investar.korea_fs_data_from_DG  (dataguide_fs_loader 적재분)
      - PK (date, ticker, item_code), 매출액 item_code = 'M000904001'
      - value 단위: 천원
미국: MySQL investar.US_IS_from_FMP           (US_FMP_FS_2_DB_SAVE_LIB 적재분)
      - long format (ticker, period, date, date_month, item, value)
      - 분기 행만 사용 (period in Q1..Q4), 매출 item = 'revenue'

두 시장 모두 최종 형태는 동일:
      wide DataFrame (index=PeriodIndex('Q'), columns=ticker, 값=분기 매출)
→ 캘린더 분기 기준으로 대만 분기 데이터와 정렬해 상관계수를 계산한다.
"""
from typing import Iterable, Optional, Tuple

import pandas as pd

from analysis_config import (make_engine, KR_REVENUE_ITEM_CODE,
                             US_REVENUE_ITEM)


def _to_quarter_wide(df: pd.DataFrame, date_col: str, ticker_col: str,
                     value_col: str) -> pd.DataFrame:
    """(date, ticker, value) long → 분기 wide. 같은 분기 중복은 최신 date 우선."""
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    d = d.dropna(subset=[date_col, value_col])
    d["quarter"] = pd.PeriodIndex(d[date_col], freq="Q")
    d = d.sort_values(date_col)
    wide = d.pivot_table(index="quarter", columns=ticker_col,
                         values=value_col, aggfunc="last")
    return wide.sort_index()


# ----------------------------------------------------------------------
# 한국
# ----------------------------------------------------------------------
def load_kr_quarterly_revenue(engine=None,
                              tickers: Optional[Iterable[str]] = None,
                              item_code: str = KR_REVENUE_ITEM_CODE,
                              start: Optional[str] = None,
                              cumulative: bool = False,
                              ) -> Tuple[pd.DataFrame, dict]:
    """
    한국 분기 매출 wide + {ticker: 회사명}.

    Parameters
    ----------
    tickers : None 이면 전 종목. 'A005930' 형식.
    start   : 'YYYY-MM-DD' 시작일 필터.
    cumulative : 기본 False — DataGuide 분기 매출액은 '단일 분기 값'으로 확인됨.
                 (만약 누적 항목을 쓰는 경우에만 True → 회계연도 내 차분 변환)
    """
    eng = engine or make_engine()
    q = ("SELECT date, ticker, company_name, value "
         "FROM korea_fs_data_from_DG WHERE item_code = %(item)s")
    params = {"item": item_code}
    if start:
        q += " AND date >= %(start)s"
        params["start"] = start
    if tickers:
        tk = list(tickers)
        ph = ", ".join(f"%(t{i})s" for i in range(len(tk)))
        q += f" AND ticker IN ({ph})"
        params.update({f"t{i}": t for i, t in enumerate(tk)})
    df = pd.read_sql(q, eng, params=params)
    if df.empty:
        return pd.DataFrame(), {}

    names = (df.sort_values("date").groupby("ticker")["company_name"]
             .last().to_dict())

    if cumulative:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["ticker", "date"])
        df["fy"] = df["date"].dt.year
        df["value"] = (df.groupby(["ticker", "fy"])["value"]
                       .diff().fillna(df["value"]))

    wide = _to_quarter_wide(df, "date", "ticker", "value")
    return wide, names


def list_kr_revenue_items(engine=None, keyword: str = "매출") -> pd.DataFrame:
    """매출 관련 item_code 후보 확인용 (indicator 명에 keyword 포함)."""
    eng = engine or make_engine()
    q = ("SELECT item_code, indicator, COUNT(*) AS n_rows, "
         "COUNT(DISTINCT ticker) AS n_tickers "
         "FROM korea_fs_data_from_DG "
         "WHERE indicator LIKE %(kw)s GROUP BY item_code, indicator "
         "ORDER BY n_rows DESC")
    return pd.read_sql(q, eng, params={"kw": f"%{keyword}%"})


# ----------------------------------------------------------------------
# 미국
# ----------------------------------------------------------------------
def load_us_quarterly_revenue(engine=None,
                              tickers: Optional[Iterable[str]] = None,
                              item: str = US_REVENUE_ITEM,
                              start: Optional[str] = None,
                              table: str = "US_IS_from_FMP",
                              ) -> Tuple[pd.DataFrame, dict]:
    """
    미국 분기 매출 wide + {ticker: ticker}(FMP 는 별도 회사명 컬럼이 없어 ticker 사용).
    period가 Q1~Q4 인 행만 사용 (FY 연간행 제외).
    """
    eng = engine or make_engine()
    q = (f"SELECT ticker, period, date, value FROM `{table}` "
         "WHERE item = %(item)s AND period IN ('Q1','Q2','Q3','Q4')")
    params = {"item": item}
    if start:
        q += " AND date >= %(start)s"
        params["start"] = start
    if tickers:
        tk = list(tickers)
        ph = ", ".join(f"%(t{i})s" for i in range(len(tk)))
        q += f" AND ticker IN ({ph})"
        params.update({f"t{i}": t for i, t in enumerate(tk)})
    df = pd.read_sql(q, eng, params=params)
    if df.empty:
        return pd.DataFrame(), {}
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    wide = _to_quarter_wide(df, "date", "ticker", "value")
    names = {t: t for t in wide.columns}
    return wide, names


def list_us_items(engine=None, keyword: str = "revenue",
                  table: str = "US_IS_from_FMP") -> pd.DataFrame:
    """미국 IS 테이블의 매출 관련 item 명 후보 확인용."""
    eng = engine or make_engine()
    q = (f"SELECT item, COUNT(*) AS n_rows, COUNT(DISTINCT ticker) AS n_tickers "
         f"FROM `{table}` WHERE item LIKE %(kw)s "
         "GROUP BY item ORDER BY n_rows DESC")
    return pd.read_sql(q, eng, params={"kw": f"%{keyword}%"})
