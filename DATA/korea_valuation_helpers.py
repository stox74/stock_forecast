# -*- coding: utf-8 -*-
"""
korea_valuation_helpers.py
================================================================================
한국 주식 가치평가 공용 헬퍼 모듈
  - DataGuide DB 재무데이터 → wide-form DataFrame 변환
  - ticker 정규화 (A005930 ↔ 005930)
  - BOK ECOS API → 10년 국고채 금리 (Rf)
  - FinanceDataReader → KOSPI 시계열 (베타·E(Rm) 계산용)
  - 10년 베타 (Blume 조정) + E(Rm) (10년 기하평균 OR Damodaran ERP floor)
  - 6년+ 매출 universe 필터
  - 데이터 품질 진단 (사용된 fallback 추적)

설계 원칙:
  1. US v7 / Relative v4 와 동일한 long-format DB 스키마 가정
  2. 핵심 항목(매출, 영업이익) 결측 → 종목 제외
     보조 항목(D&A, CapEx, NWC) 결측 → median ratio fallback
  3. 모든 fallback 사용 내역은 DataQualityReport 로 추적 가능

작성일: 2026-04-21
================================================================================
"""

from __future__ import annotations
import os, sys, time, warnings
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union, Any
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pymysql
import requests

warnings.filterwarnings("ignore")


# ════════════════════════════════════════════════════════════════════════════
# 0. 경로 자동 감지 (노트북 / 데스크탑 모두 호환)
# ════════════════════════════════════════════════════════════════════════════

_CANDIDATE_ROOTS = [
    r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast",
    r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy",
]


def setup_project_path() -> str:
    """
    프로젝트 루트 경로를 자동 감지하여 sys.path에 추가.

    탐색 순서:
      1. 현재 파일 / cwd 의 부모 중 'DATA' 디렉토리가 있는 곳
      2. _CANDIDATE_ROOTS 의 후보 경로
    """
    try:
        start = Path(__file__).resolve().parent
    except NameError:
        start = Path.cwd()

    for p in [start] + list(start.parents):
        if (p / "DATA").is_dir():
            root = str(p)
            if root not in sys.path:
                sys.path.insert(0, root)
            return root

    for cand in _CANDIDATE_ROOTS:
        if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, "DATA")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return cand

    raise EnvironmentError(
        "DATA 폴더를 찾을 수 없습니다. _CANDIDATE_ROOTS를 환경에 맞게 수정하세요."
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. Ticker 정규화 (A005930 ↔ 005930 ↔ 5930)
# ════════════════════════════════════════════════════════════════════════════

def to_dg_ticker(ticker: Union[str, int]) -> str:
    """
    DataGuide / 시가총액 / 매출예측 테이블용: 'A005930' 형태로 통일.
    """
    if isinstance(ticker, int):
        body = f"{ticker:06d}"
    else:
        raw = str(ticker).strip().upper()
        body = raw[1:] if raw.startswith("A") else raw
    return "A" + body.zfill(6)


def to_price_ticker(ticker: Union[str, int]) -> str:
    """
    KSE_Price 테이블용: '005930' 형태로 통일 (A 접두사 제거).
    """
    if isinstance(ticker, int):
        return f"{ticker:06d}"
    raw = str(ticker).strip().upper()
    body = raw[1:] if raw.startswith("A") else raw
    return body.zfill(6)


# ════════════════════════════════════════════════════════════════════════════
# 2. DataGuide 항목코드 매핑 (FCFF_RIM_DATA.xlsx 헤더 기반)
# ════════════════════════════════════════════════════════════════════════════
#
# 모든 값 단위: 천원 (1e3 KRW). 가치평가 시 1e8로 나누면 조원 단위.
# 출처: FCFF_RIM_DATA.xlsx 의 11행(아이템코드) ~ 12행(아이템명)

DG_ITEM_CODES: Dict[str, Dict[str, str]] = {
    # ── IS (손익계산서) ────────────────────────────────────────────────
    "revenue":            {"code": "M000904001", "name": "매출액"},
    "cogs":               {"code": "M000905001", "name": "매출원가"},
    "gross_profit":       {"code": "M000904007", "name": "매출총이익"},
    "operating_income":   {"code": "M000906001", "name": "영업이익"},
    "pretax_income":      {"code": "M001212380", "name": "법인세비용차감전계속사업이익"},
    "tax_expense":        {"code": "M001212390", "name": "법인세비용"},
    "net_income":         {"code": "M001212450", "name": "당기순이익(손실)"},
    "interest_expense":   {"code": "M001290770", "name": "이자비용"},
    "da_is":              {"code": "M001211210", "name": "감가상각비(IS)"},
    "intangible_amort_is":{"code": "M001290030", "name": "무형자산상각비(IS)"},
    "rnd":                {"code": "M001211300", "name": "연구개발비"},

    # ── BS (재무상태표) ────────────────────────────────────────────────
    "total_assets":       {"code": "M001190010", "name": "자산"},
    "current_assets":     {"code": "M001190240", "name": "유동자산"},
    "cash":               {"code": "M001190370", "name": "현금및현금성자산"},
    "short_term_invest":  {"code": "M001113350", "name": "단기금융상품"},
    "receivables":        {"code": "M001180890", "name": "매출채권"},
    "inventories":        {"code": "M001190250", "name": "재고자산"},
    "short_term_debt":    {"code": "M001121700", "name": "단기차입금"},
    "current_lt_debt":    {"code": "M001190620", "name": "유동성장기부채"},
    "bonds":              {"code": "M001190460", "name": "사채"},
    "long_term_debt":     {"code": "M001190470", "name": "장기차입금"},
    "lease_liab":         {"code": "M001122020", "name": "(금융)리스부채"},
    "non_current_liab":   {"code": "M001190450", "name": "비유동부채"},
    "total_equity":       {"code": "M001190380", "name": "자본"},
    "minority_interest":  {"code": "M001130640", "name": "비지배주주지분"},

    # ── CF (현금흐름표) ────────────────────────────────────────────────
    "cfo":                {"code": "M001390000", "name": "영업활동현금흐름"},
    "da_cf":              {"code": "M001310330", "name": "감가상각비(CF)"},
    "intangible_amort_cf":{"code": "M001390020", "name": "무형자산상각비(CF)"},
    "cfi":                {"code": "M001390160", "name": "투자활동현금흐름"},
    "capex_tangible":     {"code": "M001390290", "name": "유형자산의증가"},
    "capex_intangible":   {"code": "M001390320", "name": "무형자산의증가"},
    "cff":                {"code": "M001390370", "name": "재무활동현금흐름"},
    "dividends_paid":     {"code": "M001330710", "name": "배당금지급"},

    # ── 주식수 (Stock 시트) ────────────────────────────────────────────
    "shares_treasury_adj": {"code": "S420004400", "name": "평균발행주식수(자사주차감)"},
    "shares_common":       {"code": "S420004510", "name": "평균발행주식수(보통주)"},
}


def get_item_code(key: str) -> str:
    """key (예: 'revenue') → DataGuide item_code (예: 'M000904001')."""
    if key not in DG_ITEM_CODES:
        raise KeyError(f"Unknown DG item key: {key}. Available: {list(DG_ITEM_CODES)}")
    return DG_ITEM_CODES[key]["code"]


# ════════════════════════════════════════════════════════════════════════════
# 3. DB 연결 헬퍼
# ════════════════════════════════════════════════════════════════════════════

def get_pymysql_conn(db_info: Dict[str, Any], dict_cursor: bool = True):
    """pymysql 연결 (DictCursor 기본)."""
    cls = pymysql.cursors.DictCursor if dict_cursor else pymysql.cursors.Cursor
    return pymysql.connect(
        host       = db_info["host"],
        port       = int(db_info.get("port", 3307)),
        user       = db_info["user"],
        password   = db_info["password"],
        db         = db_info.get("database", "investar"),
        charset    = "utf8mb4",
        autocommit = False,
        cursorclass= cls,
    )


# ════════════════════════════════════════════════════════════════════════════
# 4. 한국 재무데이터 wide-form 로더 (long → wide)
# ════════════════════════════════════════════════════════════════════════════

def load_korea_financials_wide(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "korea_fs_data_from_DG",
    item_keys: Optional[List[str]] = None,
    fillna_zero: bool = False,
) -> pd.DataFrame:
    """
    한국 기업 재무데이터를 분기별 wide-form DataFrame으로 반환.

    Parameters
    ----------
    ticker : str | int  ('005930', 5930, 'A005930' 모두 허용)
    db_info : DB 접속 정보
    table_name : 'korea_fs_data_from_DG' 기본값
    item_keys : 가져올 항목 (None → DG_ITEM_CODES 전체)
                예: ['revenue', 'operating_income', 'tax_expense']
    fillna_zero : True → 결측을 0으로 대체 (※ FCFF 모델에서는 False 권장,
                  결측은 별도로 진단용으로 보존하고 모델 내부에서 fallback 처리)

    Returns
    -------
    DataFrame: index=date(분기말), columns=item_keys, 단위=천원 원본 그대로
               (1e8로 나누면 조원, 1e5로 나누면 억원)

    예시:
        >>> df = load_korea_financials_wide('A005930', db_info)
        >>> df[['revenue', 'operating_income']].tail(4)
                    revenue        operating_income
        2025-09-30  86,420,000     12,300,000
        ...
    """
    tk = to_dg_ticker(ticker)
    keys = item_keys or list(DG_ITEM_CODES.keys())
    codes = [DG_ITEM_CODES[k]["code"] for k in keys]
    code_to_key = {DG_ITEM_CODES[k]["code"]: k for k in keys}

    placeholders = ",".join(["%s"] * len(codes))
    sql = f"""
        SELECT date, item_code, value
        FROM `{table_name}`
        WHERE ticker = %s
          AND item_code IN ({placeholders})
        ORDER BY date, item_code
    """
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, [tk] + codes)
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=keys)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["key"] = df["item_code"].map(code_to_key)

    # 분기말 정규화 (예: 2025-09-29 → 2025-09-30)
    df["date"] = df["date"].dt.to_period("Q").dt.to_timestamp("Q").dt.normalize()

    wide = (df.pivot_table(index="date", columns="key",
                           values="value", aggfunc="last")
              .sort_index())
    # 누락 컬럼 추가 (요청한 키지만 데이터 없는 경우)
    for k in keys:
        if k not in wide.columns:
            wide[k] = np.nan
    wide = wide[keys]  # 컬럼 순서 통일

    if fillna_zero:
        wide = wide.fillna(0.0)

    return wide


def load_korea_revenue_actual(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "korea_fs_data_from_DG",
) -> pd.Series:
    """편의 함수: 매출 actual 시계열만 반환 (분기말 인덱스, 단위=천원)."""
    df = load_korea_financials_wide(ticker, db_info, table_name,
                                     item_keys=["revenue"])
    return df["revenue"].dropna() if not df.empty else pd.Series(dtype=float)


def load_korea_revenue_forecast(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "korea_revenue_forecast_result",
    model_priority: Tuple[str, ...] = ("Ensemble", "SARIMA", "ETS", "Theta"),
    horizon: int = 8,
) -> Tuple[pd.Series, str, Optional[date]]:
    """
    매출 forecast 시계열 반환.

    Returns
    -------
    forecast : pd.Series (분기말 인덱스, 단위=천원)
    used_model : str  실제 사용된 모델명
    created_at : date | None  예측 실행일

    Logic:
      1. 최신 created_at 조회
      2. priority 순으로 사용 가능한 model 선택
    """
    tk = to_dg_ticker(ticker)
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(created_at) AS m FROM `{table_name}` WHERE ticker=%s",
                (tk,)
            )
            row = cur.fetchone()
            max_ca = row["m"] if row and row.get("m") else None

        if max_ca is None:
            return pd.Series(dtype=float), "", None

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT date, indicator, value FROM `{table_name}` "
                f"WHERE ticker=%s AND created_at=%s "
                f"ORDER BY indicator, date",
                (tk, max_ca)
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.Series(dtype=float), "", max_ca

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = df["date"].dt.to_period("Q").dt.to_timestamp("Q").dt.normalize()

    available = df["indicator"].unique().tolist()
    ordered = [m for m in model_priority if m in available] + \
              [m for m in available if m not in model_priority]

    for m in ordered:
        s = (df[df["indicator"] == m]
             .drop_duplicates("date", keep="last")
             .set_index("date")["value"]
             .dropna()
             .sort_index())
        if len(s) >= horizon:
            return s.iloc[:horizon], m, max_ca
        elif len(s) > 0:
            # horizon 부족 → 마지막 값 forward-fill
            extra = pd.date_range(
                start=s.index[-1] + pd.offsets.QuarterEnd(1),
                periods=horizon - len(s), freq="QE"
            )
            s2 = pd.concat([s, pd.Series([float(s.iloc[-1])] * len(extra), index=extra)])
            return s2.iloc[:horizon], m, max_ca

    return pd.Series(dtype=float), "", max_ca


# ════════════════════════════════════════════════════════════════════════════
# 5. 시가총액 / 주가 로더
# ════════════════════════════════════════════════════════════════════════════

def load_korea_marketcap_latest(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "ks_listed_company_daily_marketcap",
) -> Tuple[Optional[float], Optional[date]]:
    """
    최신 시가총액 (단위는 DB 적재 시 결정됨 — 보통 백만원 또는 억원).
    ※ 단위 확인 필요: 삼성전자 시총이 약 500조원이라면
       value=500,000,000 → 백만원 / value=5,000,000 → 억원

    Returns
    -------
    (value, date) : 둘 다 None 가능
    """
    tk = to_dg_ticker(ticker)
    sql = f"""
        SELECT date, value FROM `{table_name}`
        WHERE ticker=%s AND indicator='시가총액'
        ORDER BY date DESC LIMIT 1
    """
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (tk,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    return float(row["value"]), row["date"]


def load_korea_price_series(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "KSE_Price",
    start_date: Optional[str] = None,
) -> pd.Series:
    """
    KSE_Price 에서 일별 종가 시계열 반환 (단위=원).
    """
    tk = to_price_ticker(ticker)
    sql = f"SELECT date, close FROM `{table_name}` WHERE code=%s"
    params: List[Any] = [tk]
    if start_date:
        sql += " AND date >= %s"
        params.append(start_date)
    sql += " ORDER BY date"
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return pd.Series(dtype=float, name="close")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.set_index("date")["close"].dropna().sort_index()


def load_current_price(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "KSE_Price",
) -> Optional[float]:
    """가장 최근 종가 (단일 값)."""
    s = load_korea_price_series(ticker, db_info, table_name)
    return float(s.iloc[-1]) if not s.empty else None


# ════════════════════════════════════════════════════════════════════════════
# 6. KOSPI 시계열 — 3단 fallback chain (FDR LOGOUT 대응)
# ════════════════════════════════════════════════════════════════════════════

def _fetch_kospi_fdr(start_date, end_date, max_retry=3):
    """Source 1: FinanceDataReader. LOGOUT/타임아웃 시 재시도."""
    import FinanceDataReader as fdr
    last_err = None
    for attempt in range(max_retry):
        try:
            df = fdr.DataReader("KS11", start_date, end_date)
            if df is not None and not df.empty and "Close" in df.columns:
                s = df["Close"].dropna().sort_index()
                if len(s) > 100:  # 최소 100거래일은 있어야 성공으로 간주
                    s.name = "kospi"
                    return s
        except Exception as e:
            last_err = str(e)[:150]
            # LOGOUT 에러는 긴 대기가 도움됨
            wait = 3 + attempt * 2  # 3, 5, 7초
            if attempt < max_retry - 1:
                time.sleep(wait)
    raise RuntimeError(f"FDR 실패 (재시도 {max_retry}회): {last_err}")


def _fetch_kospi_pykrx(start_date, end_date):
    """Source 2: pykrx (KOSPI 종합지수 = '1001')."""
    from pykrx import stock as pykrx_stock
    # pykrx는 'YYYYMMDD' 형식
    s = pd.to_datetime(start_date).strftime("%Y%m%d")
    e = pd.to_datetime(end_date).strftime("%Y%m%d") if end_date \
        else datetime.today().strftime("%Y%m%d")
    df = pykrx_stock.get_index_ohlcv(s, e, "1001")
    if df is None or df.empty or "종가" not in df.columns:
        raise RuntimeError("pykrx get_index_ohlcv 반환 비어있음")
    s_series = df["종가"].astype(float)
    s_series.index = pd.to_datetime(s_series.index)
    s_series = s_series.sort_index()
    s_series.name = "kospi"
    return s_series


def _fetch_kospi_yfinance(start_date, end_date):
    """Source 3: yfinance ('^KS11')."""
    import yfinance as yf
    df = yf.download("^KS11", start=start_date, end=end_date,
                     progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty or "Close" not in df.columns:
        raise RuntimeError("yfinance 반환 비어있음")
    # yfinance는 MultiIndex columns인 경우 있음
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.dropna().sort_index()
    s.name = "kospi"
    return s


def load_kospi_series(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    prefer: str = "fdr",     # 'fdr' | 'pykrx' | 'yfinance'
    verbose: bool = True,
) -> pd.Series:
    """
    FinanceDataReader 'KS11' (KOSPI 종합지수) 일별 종가 반환.

    3단 fallback chain (FDR LOGOUT 에러 대응):
      1) FinanceDataReader (재시도 최대 3회, 3/5/7초 간격)
      2) pykrx (KOSPI 종합지수 '1001')
      3) yfinance ('^KS11')

    Parameters
    ----------
    prefer : 우선 사용할 소스. 실패 시 다른 소스로 자동 전환.
    """
    if start_date is None:
        start_date = (datetime.today() - timedelta(days=365 * 12)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = datetime.today().strftime("%Y-%m-%d")

    # 선호 소스를 맨 앞에 두고 나머지를 뒤에
    sources = ["fdr", "pykrx", "yfinance"]
    if prefer in sources:
        sources.remove(prefer)
        sources.insert(0, prefer)

    fetchers = {
        "fdr":      _fetch_kospi_fdr,
        "pykrx":    _fetch_kospi_pykrx,
        "yfinance": _fetch_kospi_yfinance,
    }

    errors = []
    for src in sources:
        try:
            if verbose:
                print(f"[KOSPI] try source={src} ({start_date} ~ {end_date}) ...",
                      end=" ", flush=True)
            s = fetchers[src](start_date, end_date)
            if verbose:
                print(f"OK  {len(s):,}거래일")
            return s
        except ImportError as e:
            if verbose:
                print(f"SKIP ({e})")
            errors.append(f"{src}: 미설치 ({e})")
        except Exception as e:
            if verbose:
                print(f"FAIL ({str(e)[:80]})")
            errors.append(f"{src}: {str(e)[:100]}")

    raise ValueError(
        "KOSPI 데이터 로드 실패 — 모든 소스에서 실패\n"
        + "\n".join(f"  [{i+1}] {err}" for i, err in enumerate(errors))
    )


# ════════════════════════════════════════════════════════════════════════════
# 7. BOK ECOS API → 10년 국고채 금리 (Rf)
# ════════════════════════════════════════════════════════════════════════════
#
# 통계표 코드:
#   817Y002  : 시장금리(일별)
# 통계항목 코드 (817Y002 하위):
#   010210000 : 국고채(10년)  ← Rf 기본 사용
#   010200000 : 국고채(5년)
#   010195000 : 국고채(3년)
#
# API 형식 (StatisticSearch):
#   https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/1/100/{STAT_CODE}/{CYCLE}/{START}/{END}/{ITEM_CODE}
# 응답: data.StatisticSearch.row[]  (각 row 에 TIME, DATA_VALUE 포함)

BOK_API_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
BOK_STAT_CODE_RATES   = "817Y002"
BOK_ITEM_GOV_BOND_10Y = "010210000"


def fetch_bok_treasury_yield(
    api_key: str,
    item_code: str = BOK_ITEM_GOV_BOND_10Y,
    cycle: str = "D",  # D=일, M=월
    days_back: int = 30,
    end_date: Optional[str] = None,
) -> pd.Series:
    """
    BOK ECOS API → 국고채 금리 시계열 (단위=연 %).

    Parameters
    ----------
    api_key : KEYS['BOK']
    item_code : 010210000 (국고채 10년) 기본
    cycle : 'D' (일) / 'M' (월)
    days_back : 종료일로부터 며칠 전까지 조회
    end_date : 'YYYYMMDD' (기본: 오늘)

    Returns
    -------
    pd.Series : index=date, value=금리(%)
    """
    if end_date is None:
        end_date = datetime.today().strftime("%Y%m%d")
    if cycle == "D":
        start_date = (pd.to_datetime(end_date) - pd.Timedelta(days=days_back)).strftime("%Y%m%d")
    elif cycle == "M":
        start_date = (pd.to_datetime(end_date) - pd.DateOffset(months=days_back // 30 + 1)).strftime("%Y%m")
        end_date = pd.to_datetime(end_date).strftime("%Y%m")
    else:
        raise ValueError(f"Unsupported cycle: {cycle}")

    url = (
        f"{BOK_API_URL}/{api_key}/json/kr/1/1000/"
        f"{BOK_STAT_CODE_RATES}/{cycle}/{start_date}/{end_date}/{item_code}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"BOK API 호출 실패: {e}\n  url={url}")

    # 정상 응답: data['StatisticSearch']['row']
    if "StatisticSearch" not in data:
        raise RuntimeError(f"BOK API 응답 비정상: {data}")
    rows = data["StatisticSearch"].get("row", [])
    if not rows:
        return pd.Series(dtype=float, name="treasury_yield")

    df = pd.DataFrame(rows)
    if cycle == "D":
        df["date"] = pd.to_datetime(df["TIME"], format="%Y%m%d", errors="coerce")
    else:
        df["date"] = pd.to_datetime(df["TIME"], format="%Y%m", errors="coerce")
    df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    return (df.dropna(subset=["date", "value"])
              .set_index("date")["value"]
              .sort_index()
              .rename("treasury_yield"))


def get_risk_free_rate(
    api_key: str,
    fallback_rate: float = 0.035,
    item_code: str = BOK_ITEM_GOV_BOND_10Y,
    days_back: int = 60,
) -> Tuple[float, str]:
    """
    최근 10년 국고채 금리 가져와서 소수(%/100)로 반환.

    Returns
    -------
    (rate, source) : (0.0335, 'BOK_2026-04-15') 또는 (0.035, 'fallback')
    """
    try:
        s = fetch_bok_treasury_yield(api_key, item_code=item_code,
                                     cycle="D", days_back=days_back)
        if s.empty:
            return fallback_rate, "fallback"
        latest_date = s.index[-1].strftime("%Y-%m-%d")
        return float(s.iloc[-1]) / 100.0, f"BOK_{latest_date}"
    except Exception as e:
        print(f"[WARN] BOK API 실패, fallback 사용: {e}")
        return fallback_rate, "fallback"


# ════════════════════════════════════════════════════════════════════════════
# 8. Beta (10년 일별 회귀) + Blume 조정
# ════════════════════════════════════════════════════════════════════════════

def compute_beta_10y(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    kospi_series: Optional[pd.Series] = None,
    years: int = 10,
    min_obs: int = 750,  # 최소 3년 (750 영업일)
) -> Dict[str, float]:
    """
    종목별 베타 계산:
      1. KSE_Price 에서 일별 종가 → 일별 수익률
      2. KOSPI 일별 수익률과 inner-join (날짜 정렬)
      3. β_raw = Cov(stock, kospi) / Var(kospi)  (최근 10년 데이터)
      4. β_blume = 0.67 × |β_raw| + 0.33  ← 호영님 요청 수식

    Returns
    -------
    {
      "beta_raw": float,       # OLS 추정 베타
      "beta_blume": float,     # 0.67×|β| + 0.33  (시장 민감도 둔화)
      "n_obs": int,            # 회귀 표본 수
      "r_squared": float,      # 회귀 R² (적합도)
      "n_years_used": float,   # 실제 사용 기간(년)
    }
    """
    # 1. 주가 시계열
    start = (datetime.today() - timedelta(days=int(365.25 * (years + 1)))).strftime("%Y-%m-%d")
    px = load_korea_price_series(ticker, db_info, start_date=start)
    if px.empty or len(px) < min_obs:
        return {"beta_raw": np.nan, "beta_blume": 1.0,
                "n_obs": len(px), "r_squared": np.nan, "n_years_used": np.nan}

    # 2. KOSPI
    if kospi_series is None:
        kospi_series = load_kospi_series(start_date=start)
    if kospi_series.empty:
        return {"beta_raw": np.nan, "beta_blume": 1.0,
                "n_obs": 0, "r_squared": np.nan, "n_years_used": np.nan}

    # 3. 수익률 + inner join
    px_ret = px.pct_change().dropna()
    mk_ret = kospi_series.pct_change().dropna()
    df = pd.concat([px_ret.rename("s"), mk_ret.rename("m")], axis=1, join="inner").dropna()

    # 최근 N년만 사용
    cutoff = df.index.max() - pd.DateOffset(years=years)
    df = df.loc[df.index >= cutoff]

    if len(df) < min_obs:
        return {"beta_raw": np.nan, "beta_blume": 1.0,
                "n_obs": len(df), "r_squared": np.nan, "n_years_used": np.nan}

    var_m = float(df["m"].var())
    if var_m <= 1e-12:
        return {"beta_raw": np.nan, "beta_blume": 1.0,
                "n_obs": len(df), "r_squared": np.nan, "n_years_used": np.nan}

    cov_sm = float(df["s"].cov(df["m"]))
    beta_raw = cov_sm / var_m
    # R² (단변량 OLS)
    corr = df["s"].corr(df["m"])
    r2 = float(corr ** 2) if not np.isnan(corr) else np.nan
    # Blume 조정 (호영님 요청: 0.67×β + 0.33×1)
    beta_blume = 0.67 * abs(beta_raw) + 0.33

    n_years = (df.index.max() - df.index.min()).days / 365.25

    return {
        "beta_raw":     float(beta_raw),
        "beta_blume":   float(beta_blume),
        "n_obs":        int(len(df)),
        "r_squared":    r2,
        "n_years_used": float(n_years),
    }


# ════════════════════════════════════════════════════════════════════════════
# 9. E(Rm) — 두 가지 옵션
# ════════════════════════════════════════════════════════════════════════════

def estimate_market_return(
    method: str = "damodaran_floor",
    rf: float = 0.035,
    kospi_series: Optional[pd.Series] = None,
    years: int = 10,
    damodaran_erp_kr: float = 0.07,  # Damodaran 한국 ERP 기본 7%
    geo_floor: float = 0.07,         # 기하평균 사용 시 floor
    geo_cap: float = 0.15,
) -> Dict[str, Any]:
    """
    E(Rm) 추정. 호영님 결정 #4 반영:
      - 기본: damodaran_floor (Rf + Damodaran ERP, 한국 ~7%)
      - 옵션: kospi_geo_10y (KOSPI 10년 기하평균, floor 7%)

    Parameters
    ----------
    method : 'damodaran_floor' | 'kospi_geo_10y' | 'kospi_geo_no_floor'
    rf : 무위험수익률 (소수)
    kospi_series : 미리 로드한 KOSPI 시계열
    years : 기하평균 산출 기간
    damodaran_erp_kr : Damodaran 발표 한국 ERP (보통 6~8%)
    geo_floor / geo_cap : 기하평균 사용 시 안전장치

    Returns
    -------
    {
      "e_rm": float,         # E(Rm) 최종값
      "erp": float,          # E(Rm) - Rf
      "method": str,
      "kospi_geo": float,    # 참고: KOSPI 실제 10년 기하평균 (있으면)
      "note": str,
    }
    """
    kospi_geo = np.nan
    note_lines = []

    # KOSPI 기하평균 계산 (참고용 또는 method별 사용)
    if kospi_series is not None and not kospi_series.empty:
        try:
            cutoff = kospi_series.index.max() - pd.DateOffset(years=years)
            seg = kospi_series.loc[kospi_series.index >= cutoff].dropna()
            if len(seg) > 100:
                p_start, p_end = float(seg.iloc[0]), float(seg.iloc[-1])
                n_years = (seg.index.max() - seg.index.min()).days / 365.25
                if p_start > 0 and n_years > 0:
                    kospi_geo = (p_end / p_start) ** (1.0 / n_years) - 1.0
        except Exception as e:
            note_lines.append(f"KOSPI geo 계산 실패: {e}")

    if method == "damodaran_floor":
        e_rm = rf + damodaran_erp_kr
        note_lines.append(f"Damodaran 한국 ERP {damodaran_erp_kr:.1%} 적용")

    elif method == "kospi_geo_10y":
        if np.isnan(kospi_geo):
            e_rm = rf + damodaran_erp_kr
            note_lines.append("KOSPI geo 계산 불가 → Damodaran fallback")
        else:
            erp_geo = max(kospi_geo - rf, 0.0)
            erp_clipped = float(np.clip(erp_geo, geo_floor - rf, geo_cap - rf))
            # 즉, E(Rm) ∈ [geo_floor, geo_cap]
            e_rm = float(np.clip(rf + erp_clipped, geo_floor, geo_cap))
            note_lines.append(
                f"KOSPI {years}년 기하평균={kospi_geo:.2%}, "
                f"floor {geo_floor:.0%} 적용 → E(Rm)={e_rm:.2%}"
            )

    elif method == "kospi_geo_no_floor":
        if np.isnan(kospi_geo):
            e_rm = rf + damodaran_erp_kr
            note_lines.append("KOSPI geo 계산 불가 → Damodaran fallback")
        else:
            e_rm = float(kospi_geo)
            note_lines.append(f"KOSPI {years}년 기하평균={kospi_geo:.2%} (floor 없음)")
    else:
        raise ValueError(f"Unknown method: {method}")

    return {
        "e_rm":   float(e_rm),
        "erp":    float(e_rm - rf),
        "method": method,
        "kospi_geo": float(kospi_geo) if not np.isnan(kospi_geo) else None,
        "note":   " | ".join(note_lines),
    }


# ════════════════════════════════════════════════════════════════════════════
# 10. Universe Filter — 6년+ 매출 보유 종목
# ════════════════════════════════════════════════════════════════════════════

def get_universe_with_min_history(
    db_info: Dict[str, Any],
    min_quarters: int = 24,         # 6년 = 24분기
    require_consecutive: bool = False,  # True면 결측 분기 없이 연속이어야 함
    fs_table: str = "korea_fs_data_from_DG",
    revenue_code: str = "M000904001",
) -> pd.DataFrame:
    """
    매출 데이터가 N분기 이상인 ticker universe 반환.

    Returns
    -------
    DataFrame with columns: [ticker, n_quarters, first_date, last_date, gap_count]
      gap_count : 첫 ~ 마지막 사이의 결측 분기 수 (0이면 완전 연속)
    """
    sql = f"""
        SELECT ticker,
               COUNT(*)        AS n_quarters,
               MIN(date)       AS first_date,
               MAX(date)       AS last_date
        FROM `{fs_table}`
        WHERE item_code = %s
          AND value IS NOT NULL
        GROUP BY ticker
        HAVING n_quarters >= %s
        ORDER BY n_quarters DESC
    """
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (revenue_code, min_quarters))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=["ticker", "n_quarters", "first_date",
                                     "last_date", "gap_count"])

    df = pd.DataFrame(rows)
    df["first_date"] = pd.to_datetime(df["first_date"])
    df["last_date"]  = pd.to_datetime(df["last_date"])
    # 예상 분기 수 = (last - first) / 90일 + 1
    df["expected_q"] = ((df["last_date"] - df["first_date"]).dt.days // 90) + 1
    df["gap_count"]  = (df["expected_q"] - df["n_quarters"]).clip(lower=0)

    if require_consecutive:
        df = df[df["gap_count"] == 0].copy()

    return df.drop(columns=["expected_q"]).reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
# 11. 데이터 품질 진단 (호영님 결정 #5 반영)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class DataQualityReport:
    """
    가치평가 시 사용된 데이터의 품질 / fallback 사용 내역 추적용.

    valuation 모델 내부에서 결측·OLS 부적합·fallback 발생 시 .add() 호출,
    최종적으로 .summary() 또는 .to_dict() 로 점검 가능.

    예시:
        rep = DataQualityReport(ticker='A005930')
        rep.add('revenue', 'ok', n_obs=44)
        rep.add('da', 'fallback_median', n_obs=12, value=0.025)
        rep.summary()
        # → A005930  ok=1  fallback=1  missing=0  최저 OLS R²=N/A
    """
    ticker: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add(self, field_name: str, status: str,
            n_obs: int = 0, value: Optional[float] = None,
            r2: Optional[float] = None, note: str = ""):
        """
        status : 'ok'           — OLS 정상 적합
                 'fallback_median' — OLS 부적합 → median ratio 사용
                 'fallback_zero'   — 컬럼 자체 없음 → 0 사용
                 'missing'         — 결측 (모델 제외 사유)
                 'warning'         — 진단 경고
        """
        self.items.append({
            "field": field_name, "status": status, "n_obs": n_obs,
            "value": value, "r2": r2, "note": note,
        })

    def warn(self, msg: str):
        self.warnings.append(msg)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.items:
            return pd.DataFrame()
        return pd.DataFrame(self.items)

    def summary(self) -> str:
        df = self.to_dataframe()
        if df.empty:
            return f"[{self.ticker}] 진단 항목 없음"
        cnts = df["status"].value_counts().to_dict()
        parts = [f"[{self.ticker}]"]
        for st in ["ok", "fallback_median", "fallback_zero", "missing", "warning"]:
            parts.append(f"{st}={cnts.get(st, 0)}")
        if "r2" in df.columns and df["r2"].notna().any():
            parts.append(f"min_R²={df['r2'].min():.2f}")
        if self.warnings:
            parts.append(f"warns={len(self.warnings)}")
        return "  ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker":    self.ticker,
            "items":     self.items,
            "warnings":  self.warnings,
            "n_total":   len(self.items),
            "n_ok":      sum(1 for it in self.items if it["status"] == "ok"),
            "n_fallback":sum(1 for it in self.items
                             if it["status"].startswith("fallback")),
            "n_missing": sum(1 for it in self.items if it["status"] == "missing"),
        }


def save_quality_report_to_db(
    reports: List[DataQualityReport],
    db_info: Dict[str, Any],
    table_name: str = "korea_valuation_quality_log",
    run_date: Optional[str] = None,
    model_name: str = "FCFF_DCF",
):
    """
    여러 종목의 DataQualityReport 들을 한 번에 DB 저장.
    배치 실행 후 `SELECT * FROM korea_valuation_quality_log WHERE date='...'`
    로 어떤 종목이 어떤 fallback을 썼는지 검증 가능.
    """
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")
    create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{table_name}` (
          `id` BIGINT NOT NULL AUTO_INCREMENT,
          `date`       DATE        NOT NULL,
          `model`      VARCHAR(30) NOT NULL COMMENT 'FCFF_DCF / Relative',
          `ticker`     VARCHAR(20) NOT NULL,
          `field`      VARCHAR(50) NOT NULL,
          `status`     VARCHAR(20) NOT NULL,
          `n_obs`      INT         DEFAULT 0,
          `value`      DOUBLE,
          `r_squared`  DOUBLE,
          `note`       VARCHAR(255),
          `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (`id`),
          INDEX idx_date_ticker (`date`, `ticker`),
          INDEX idx_status (`status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    insert_sql = f"""
        INSERT INTO `{table_name}` (date, model, ticker, field, status,
                                     n_obs, value, r_squared, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows: List[Tuple] = []
    for rep in reports:
        for it in rep.items:
            rows.append((
                run_date, model_name, rep.ticker,
                it.get("field"), it.get("status"),
                int(it.get("n_obs", 0) or 0),
                _none_or_float(it.get("value")),
                _none_or_float(it.get("r2")),
                (it.get("note") or "")[:255],
            ))

    conn = get_pymysql_conn(db_info, dict_cursor=False)
    try:
        with conn.cursor() as cur:
            cur.execute(create_sql)
            if rows:
                cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _none_or_float(x):
    if x is None: return None
    try:
        f = float(x)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return None


# ════════════════════════════════════════════════════════════════════════════
# 12. 가치평가 결과 조회 헬퍼
# ════════════════════════════════════════════════════════════════════════════

def get_evaluation_history(
    ticker: Union[str, int],
    db_info: Dict[str, Any],
    table_name: str = "korea_fcff_dcf_valuation",
    date_col: str = "date",
) -> pd.DataFrame:
    """
    호영님 요청 #12: 종목별로 평가 날짜별 평가 결과 추이 조회.

    Returns
    -------
    DataFrame: 평가일자별 한 줄 요약
    """
    tk = to_dg_ticker(ticker)
    sql = f"""
        SELECT {date_col} AS run_date,
               COUNT(*) AS n_quarters,
               MAX(target_price) AS target_price,
               MAX(current_price) AS current_price,
               MAX(upside_pct) AS upside_pct,
               MAX(discount_rate) AS discount_rate,
               MAX(g_terminal) AS g_terminal,
               MAX(enterprise_value) AS enterprise_value
        FROM `{table_name}`
        WHERE ticker = %s
        GROUP BY {date_col}
        ORDER BY {date_col} DESC
    """
    conn = get_pymysql_conn(db_info)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (tk,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════
# 13. 자가 진단 (모듈 직접 실행 시)
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("korea_valuation_helpers — 자가 진단")
    print("=" * 70)
    try:
        root = setup_project_path()
        print(f"[OK] Project root  : {root}")
    except Exception as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

    # ticker 변환 테스트
    for t in ["005930", 5930, "A005930", "a005930"]:
        print(f"  {str(t):<10} → DG: {to_dg_ticker(t)}  Price: {to_price_ticker(t)}")

    print(f"\n[OK] DG_ITEM_CODES: {len(DG_ITEM_CODES)}개 항목 매핑 정의됨")
    print(f"  IS: {sum(1 for k in DG_ITEM_CODES if DG_ITEM_CODES[k]['code'].startswith('M00090') or DG_ITEM_CODES[k]['code'].startswith('M001212') or DG_ITEM_CODES[k]['code'].startswith('M001211') or DG_ITEM_CODES[k]['code'].startswith('M001290'))}개")
    print(f"  BS: {sum(1 for k in DG_ITEM_CODES if DG_ITEM_CODES[k]['code'].startswith('M00119') or DG_ITEM_CODES[k]['code'].startswith('M00112') or DG_ITEM_CODES[k]['code'].startswith('M00118') or DG_ITEM_CODES[k]['code'].startswith('M00113'))}개")
    print(f"  CF: {sum(1 for k in DG_ITEM_CODES if DG_ITEM_CODES[k]['code'].startswith('M00139') or DG_ITEM_CODES[k]['code'].startswith('M00131') or DG_ITEM_CODES[k]['code'].startswith('M00133'))}개")
    print(f"  Stock: {sum(1 for k in DG_ITEM_CODES if DG_ITEM_CODES[k]['code'].startswith('S'))}개")
