# -*- coding: utf-8 -*-
"""
trade_data_import (v2, 2026-10-17)

v1 대비 변경
  1. DB 엔진을 접속 정보별로 한 번만 만들고 재사용 (v1 은 호출마다 create_engine -> 연결 풀 누적)
  2. get_trade_data_by_hscode 의 반환 형식은 v1 과 같음 (date, root_hs_code, indicator, value 긴 형식)
  3. 새 함수 check_trade_series(df): 빈 달, 0인 달, 마지막 달을 점검해 dict 로 돌려줌 (예측 전 품질 판단용)

HS 코드는 문자열 그대로 VARCHAR 열과 비교하므로 앞자리 0('0207', '030341')이 보존된다.
"""
from typing import Dict, Optional

import pandas as pd
from sqlalchemy import create_engine, text

_ENGINES: Dict[tuple, object] = {}


def _get_engine(db_info: dict):
    key = (db_info["host"], int(db_info["port"]), db_info["user"], db_info["database"])
    eng = _ENGINES.get(key)
    if eng is None:
        conn_str = (f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
                    f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4")
        eng = create_engine(conn_str, pool_pre_ping=True, pool_recycle=1800)
        _ENGINES[key] = eng
    return eng


def get_trade_data_by_hscode(db_info: dict, hs_code: str, indicator: Optional[str] = None,
                             table: str = "korea_monthly_trade_data", verbose: bool = True) -> pd.DataFrame:
    """HS 코드(문자열)와 지표로 월별 무역 자료를 읽는다. 반환 열: date, root_hs_code, indicator, value"""
    q = "SELECT date, root_hs_code, indicator, value FROM `" + table + "` WHERE root_hs_code = :hs_code"
    params = {"hs_code": str(hs_code).strip()}
    if indicator:
        q += " AND indicator = :indicator"
        params["indicator"] = indicator
    q += " ORDER BY date ASC"
    with _get_engine(db_info).connect() as conn:
        df = pd.read_sql(text(q), conn, params=params)
    if df.empty:
        if verbose:
            print(f"[WARN] HS Code {hs_code}, Indicator '{indicator}' 데이터 없음.")
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if verbose:
        print(f"[INFO] HS Code={hs_code}, Indicator={indicator or 'ALL'} -> {len(df)}행 불러옴")
    return df


def check_trade_series(df: pd.DataFrame, ref_month: Optional[pd.Timestamp] = None) -> Dict:
    """긴 형식 단일 지표 자료의 품질 점검.
    반환: n_rows, first, last, span_months, missing_months, zero_share, all_zero, lag_months(ref 대비)"""
    out = {"n_rows": 0, "first": None, "last": None, "span_months": 0, "missing_months": 0,
           "zero_share": None, "all_zero": True, "lag_months": None}
    if df is None or df.empty:
        return out
    d = pd.to_datetime(df["date"]).dt.to_period("M")
    v = pd.to_numeric(df["value"], errors="coerce")
    first, last = d.min(), d.max()
    span = (last - first).n + 1
    out.update(n_rows=int(len(df)), first=first.to_timestamp(how="end").normalize(),
               last=last.to_timestamp(how="end").normalize(), span_months=int(span),
               missing_months=int(span - d.nunique()), zero_share=float((v <= 0).mean()),
               all_zero=bool((v <= 0).all()))
    if ref_month is not None:
        r = pd.Timestamp(ref_month).to_period("M")
        out["lag_months"] = int((r - last).n)
    return out


def get_latest_month(db_info: dict, indicator: str = "expDlr",
                     table: str = "korea_monthly_trade_data") -> Optional[pd.Timestamp]:
    """테이블 전체 최신월 (끊긴 계열 판단 기준)"""
    with _get_engine(db_info).connect() as conn:
        d = pd.read_sql(text(f"SELECT MAX(date) AS d FROM `{table}` WHERE indicator = :i"),
                        conn, params={"i": indicator})["d"].iloc[0]
    return pd.Timestamp(d) if pd.notna(d) else None


def get_unique_hscode_list(db_info: dict, table: str = "korea_monthly_trade_data") -> list:
    """테이블의 root_hs_code 고유값 목록"""
    q = text(f"SELECT DISTINCT root_hs_code FROM `{table}` WHERE root_hs_code IS NOT NULL ORDER BY root_hs_code")
    with _get_engine(db_info).connect() as conn:
        df = pd.read_sql(q, conn)
    hs_list = df["root_hs_code"].dropna().astype(str).unique().tolist()
    print(f"[INFO] 총 {len(hs_list)}개의 고유 HS Code를 불러왔습니다.")
    return hs_list
