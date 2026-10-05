# -*- coding: utf-8 -*-
"""코스피 일별 지수 수집 (2a 명세서 7절 시장 수익률).

FinanceDataReader 로 코스피 지수를 받아 kr_stock_index_daily(+_vintage)에 STD-1 규칙으로 적재한다.
- 처음 실행(테이블에 해당 idx_name 이 없음): START 부터 전체
- 이후: 저장된 마지막 날짜 - REFETCH_DAYS 부터 다시 받아, 바뀐 값은 _vintage 에 'revised' 로 남긴다
- 원천: FDR 의 KRX 경로('KS11')는 KRX 데이터 포털이 로그인을 요구해 실패한다(2026-10, 응답 "LOGOUT").
  그래서 FDR 의 야후 경로 'YAHOO:^KS11' 을 쓴다. 야후는 거래량 단위가 불분명해 종가만 저장한다.
  종가가 빈 날(2011-10-04, 2015-08-14)은 버린다. 야후에 없는 거래일은 결측으로 남는다.
- idx_name 은 FDR 심볼 그대로('YAHOO:^KS11') 둬서 원천이 이름에 드러나게 한다. 같은 테이블에 쓰는
  금융위원회 지수시세 노트북은 '코스피' 등 API 지수명을 쓰므로 키가 겹치지 않는다
  (원천 구분 컬럼은 없음, 세션 리포트에 제안).
쓰기는 db.write_std1_external 로만 한다 (config.EXTERNAL_WRITE_TABLES 에 등록된 테이블).
"""
import logging
from typing import Dict, Optional

import pandas as pd

from . import db

log = logging.getLogger(__name__)

TABLE = "kr_stock_index_daily"
START = "2010-01-01"
REFETCH_DAYS = 10
KOSPI = "YAHOO:^KS11"
SYMBOLS = {KOSPI: "코스피 (FinanceDataReader, Yahoo ^KS11)"}

# FDR 컬럼 -> 테이블 컬럼 (종가만)
FDR_MAP = {"Close": "close"}
VALUE_COLS = ["close"]


def fetch_fdr(symbol: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
    import FinanceDataReader as fdr
    raw = fdr.DataReader(symbol, start, end)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["idx_name", "period_end"] + VALUE_COLS)
    df = raw.reset_index().rename(columns={raw.index.name or "index": "Date"})
    if "Date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "Date"})
    out = pd.DataFrame({"idx_name": symbol, "period_end": pd.to_datetime(df["Date"]).dt.normalize()})
    for src, dst in FDR_MAP.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce") if src in df.columns else None
    out = out[out["close"].notna() & (out["close"] > 0)]
    return out.drop_duplicates("period_end", keep="last").reset_index(drop=True)


def last_date(symbol: str):
    df = db.read_sql(f"SELECT MAX(`period_end`) AS d FROM `{TABLE}` WHERE `idx_name` = :n", {"n": symbol})
    d = df["d"].iloc[0]
    return None if d is None or pd.isna(d) else pd.Timestamp(d)


def update(symbol: str = KOSPI, full: bool = False) -> Dict[str, object]:
    """처음이면 전체, 아니면 증분. 반환: 기간, 행 수, new/revised 수."""
    ld = None if full else last_date(symbol)
    start = START if ld is None else (ld - pd.Timedelta(days=REFETCH_DAYS)).strftime("%Y-%m-%d")
    df = fetch_fdr(symbol, start)
    if df.empty:
        return {"symbol": symbol, "mode": "full" if ld is None else "incremental", "start": start, "rows": 0}
    res = db.write_std1_external(df, TABLE, VALUE_COLS,
                                 static_cols={"freq": "D", "unit": None, "currency": "KRW"})
    res.update({"symbol": symbol, "mode": "full" if ld is None else "incremental", "start": start,
                "first": df["period_end"].min().date(), "last": df["period_end"].max().date()})
    log.info("index %s updated: %s", symbol, res)
    return res


def read_index(symbol: str = KOSPI, start=None, end=None) -> pd.DataFrame:
    """period_end, close, ret (일간 수익률)."""
    s = pd.Timestamp(start).date() if start is not None else pd.Timestamp("1900-01-01").date()
    e = pd.Timestamp(end).date() if end is not None else pd.Timestamp("2999-12-31").date()
    df = db.read_sql(f"SELECT `period_end`, `close` FROM `{TABLE}` WHERE `idx_name` = :n "
                     f"AND `period_end` BETWEEN :s AND :e ORDER BY `period_end`", {"n": symbol, "s": s, "e": e})
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["ret"] = df["close"].pct_change()
    return df
