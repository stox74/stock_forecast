#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
WRDSDataValidator (MySQL, integrated)
- EDGAR 분기 데이터(정규화 DF)를 WRDS(Compustat) MySQL 데이터로 보완
- 기업별 분기 캘린더 자동 추정 → 분기말(eom)로 스냅
- WRDS 단위를 EDGAR 스케일에 자동 정렬(천/백만/십억 등)
- EDGAR 우선 결합 + ±days_tolerance 근접치 asof 보조
"""

from typing import Dict, Optional, List, Tuple
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pandas.tseries.offsets import MonthEnd

try:
    import pymysql
except ImportError:
    print("=" * 80)
    print("pymysql 모듈이 설치되어 있지 않습니다.")
    print("=" * 80)
    print("\n다음 명령어로 설치해주세요:")
    print("  pip install pymysql")
    print("=" * 80)
    raise

warnings.filterwarnings("ignore")


# -----------------------------------------------------------------------------
# 분기 캘린더 추정 & 표준화 유틸
# -----------------------------------------------------------------------------
QUARTER_PATTERNS = [
    (3, 6, 9, 12),   # 3-6-9-12
    (1, 4, 7, 10),   # 1-4-7-10
    (2, 5, 8, 11),   # 2-5-8-11
    (4, 7, 10, 1),   # 4-7-10-1 (회계연도 3월말 기업)
]


def _most_likely_pattern(dates: pd.Index) -> tuple:
    """날짜 인덱스에서 가장 가능성 높은 분기 패턴(분기말 달 튜플) 추정"""
    if len(dates) == 0:
        return (3, 6, 9, 12)
    months = pd.Index(dates).month
    scores = []
    for pat in QUARTER_PATTERNS:
        mask = months.isin(pat)
        score = mask.mean() - (months[mask].value_counts(normalize=True).std() if mask.any() else 0)
        scores.append((score, pat))
    scores.sort(reverse=True, key=lambda x: x[0])
    return scores[0][1]


def _to_quarter_eom_index(dates: pd.Index, pattern: tuple, tolerance_days: int = 20) -> pd.Index:
    """
    임의의 날짜들을 주어진 패턴의 '표준 분기말(월말)'로 스냅.
    tolerance_days 안의 가장 가까운 월말로 매핑.
    """
    if not isinstance(dates, pd.DatetimeIndex):
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


def standardize_quarter_dates(edgar: pd.DataFrame,
                              wrds: pd.DataFrame,
                              tolerance_days: int = 20) -> Tuple[pd.DataFrame, pd.DataFrame, tuple]:
    """
    EDGAR/WRDS 각각에 표준 분기말(eom) 인덱스를 부여하여 정렬/중복제거 후 반환.
    두 소스 중 캘린더 일치도가 높은 쪽의 패턴을 채택(동률이면 EDGAR 우선).
    """
    if not isinstance(edgar.index, pd.DatetimeIndex):
        edgar = edgar.copy()
        edgar.index = pd.to_datetime(edgar.index)

    if not isinstance(wrds.index, pd.DatetimeIndex):
        wrds = wrds.copy()
        wrds.index = pd.to_datetime(wrds.index)

    pat_e = _most_likely_pattern(edgar.index.dropna())
    pat_w = _most_likely_pattern(wrds.index.dropna())

    def _score(pattern, idx):  # 일치도 점수
        return (pd.Index(idx.month).isin(pattern)).mean()

    pattern = pat_e if _score(pat_e, edgar.index) >= _score(pat_w, wrds.index) else pat_w

    e_idx = _to_quarter_eom_index(edgar.index, pattern, tolerance_days)
    w_idx = _to_quarter_eom_index(wrds.index, pattern, tolerance_days)

    e2 = edgar.copy(); e2.index = e_idx
    w2 = wrds.copy();  w2.index = w_idx

    e2 = e2[~e2.index.isna()].sort_index()
    w2 = w2[~w2.index.isna()].sort_index()

    # 동일 분기말 중복 시 최신값 하나
    e2 = e2[~e2.index.duplicated(keep="last")]
    w2 = w2[~w2.index.duplicated(keep="last")]

    return e2, w2, pattern


# -----------------------------------------------------------------------------
# 스케일(단위) 정렬 유틸 (WRDS → EDGAR 기준으로 보정)
# -----------------------------------------------------------------------------
_CURRENCY_COL_HINTS = [
    "revenue", "net_income", "operating_income", "gross_profit",
    "total_assets", "total_liabilities", "stockholders_equity",
    "current_assets", "current_liabilities", "cost_of_revenue", "cash",
]

_SCALE_CANDIDATES = [1, 1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9]


def _overlap_scale_factor(edgar_s: pd.Series, wrds_s: pd.Series) -> float:
    """겹치는 구간에서 WRDS 값에 곱할 최적 스케일(EDGAR에 가장 근접) 탐색"""
    x = edgar_s.dropna()
    y = wrds_s.dropna()
    idx = x.index.intersection(y.index)
    if len(idx) < 3:
        return 1.0
    x = x.loc[idx].astype(float)
    y = y.loc[idx].astype(float)
    errs = []
    for f in _SCALE_CANDIDATES:
        den = np.maximum(np.abs(x.values), 1.0)  # 0 나눗셈 방지
        err = np.nanmedian(np.abs(x.values - f * y.values) / den)
        errs.append((err, f))
    errs.sort(key=lambda t: t[0])
    return errs[0][1]


def harmonize_wrds_scale_to_edgar(edgar_df: pd.DataFrame, wrds_df: pd.DataFrame) -> pd.DataFrame:
    """
    통화 단위(천/백만/십억 등) 차이를 EDGAR 기준으로 자동 보정.
    revenue 컬럼으로 우선 추정, 없으면 다른 금액 컬럼으로 추정.
    """
    w = wrds_df.copy()
    candidates = [c for c in _CURRENCY_COL_HINTS if (c in edgar_df.columns and c in wrds_df.columns)]
    if not candidates:
        return w
    key = "revenue" if "revenue" in candidates else candidates[0]
    factor = _overlap_scale_factor(edgar_df[key], wrds_df[key])
    if factor == 1.0:
        return w
    for c in candidates:
        w[c] = w[c] * factor
    return w


# -----------------------------------------------------------------------------
# WRDSDataValidator
# -----------------------------------------------------------------------------
class WRDSDataValidator:
    """
    EDGAR 데이터를 WRDS 데이터로 검증하고 보완 (MySQL)
    """

    # WRDS Compustat → EDGAR 표준 컬럼 매핑
    WRDS_TO_EDGAR_MAPPING = {
        # Income Statement
        "saleq": "revenue",
        "revtq": "revenue",
        "cogsq": "cost_of_revenue",
        "xsgaq": "operating_expenses",
        "xrdq": "research_development",
        "ibq": "operating_income",
        "niq": "net_income",
        "piq": "pretax_income",
        "epspiq": "earnings_per_share",
        "txtq": "income_tax_expense",
        "txpq": "income_tax_payable",
        "txdiq": "deferred_tax_income",
        "xintq": "interest_expense",
        "xidoq": "extraordinary_items",
        "mibtq": "minority_interest_income",

        # Balance Sheet - Assets
        "atq": "total_assets",
        "actq": "current_assets",
        "cheq": "cash",
        "ivstq": "short_term_investments",
        "rectq": "accounts_receivable",
        "recdq": "accounts_receivable_net",
        "rectrq": "accounts_receivable_trade",
        "invtq": "inventory",
        "ppentq": "net_ppe",
        "ppegtq": "gross_ppe",
        "aoq": "other_assets",
        "intanq": "intangible_assets",
        "ivaoq": "investment_advances",

        # Balance Sheet - Liabilities
        "ltq": "total_liabilities",
        "lctq": "current_liabilities",
        "dlcq": "short_term_debt",
        "apq": "accounts_payable",
        "loq": "other_liabilities",

        # Balance Sheet - Equity
        "seqq": "stockholders_equity",
        "ceqq": "common_equity",
        "pstkrq": "preferred_stock_redemption",

        # Cash Flow Statement
        "oancfy": "operating_cash_flow",
        "fincfy": "financing_cash_flow",
        "capxy": "capital_expenditures",
        "dltisy": "long_term_debt_issuance",
        "prstkcy": "purchase_common_stock",
        "sstky": "sale_of_stock",
        "chechy": "cash_change",
        "dlcchy": "short_term_debt_change",
        "dpq": "depreciation_amortization",

        # Other
        "icaptq": "invested_capital",
    }

    def __init__(self, db_info: Dict[str, any]):
        self.db_info = db_info
        self.conn: Optional[pymysql.connections.Connection] = None

    # --- DB 연결/해제 ---
    def connect(self):
        if self.conn:
            return
        self.conn = pymysql.connect(
            host=self.db_info["host"],
            port=int(self.db_info.get("port", 3306)),
            user=self.db_info["user"],
            password=self.db_info["password"],
            database=self.db_info["database"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        print(f"✓ MySQL 연결 성공: {self.db_info['host']}:{self.db_info.get('port', 3306)}/{self.db_info['database']}")

    def disconnect(self):
        if self.conn:
            try:
                self.conn.close()
            finally:
                self.conn = None
                print("✓ MySQL 연결 해제")

    # === 디버그 프린터 ===
    def _dbg_df(name: str, df: pd.DataFrame, rows: int = 6):
        try:
            print(f"\n[DEBUG] {name}: shape={tuple(df.shape)}")
            if isinstance(df.index, pd.DatetimeIndex):
                print(f"        date-range: {df.index.min()} ~ {df.index.max()}  (unique={df.index.nunique()})")
            print(
                f"        columns({len(df.columns)}): {list(df.columns)[:12]}{' ...' if len(df.columns) > 12 else ''}")
            print(f"        head({rows}):");
            print(df.head(rows))
            print(f"        tail({rows}):");
            print(df.tail(rows))
        except Exception as e:
            print(f"[DEBUG] {name}: print failed -> {e}")

    # --- WRDS 읽기 ---
    def get_wrds_data(
            self,
            ticker: str,
            table_name: str = "us_fundq",  # ✅ 당신 DB 테이블
            *,
            ticker_col: str = "ticker",  # ✅ 당신 DB 컬럼
            date_col: str = "edate"  # ✅ 당신 DB 컬럼
    ) -> pd.DataFrame:
        """
        MySQL(WRDS 적재본)에서 특정 종목의 분기 재무데이터 로드.
        'us_fundq'에서 ticker/edate 기준으로 읽어와 인덱스를 DatetimeIndex로 표준화.
        """
        if not self.conn:
            self.connect()

        sql = f"SELECT * FROM {table_name} WHERE {ticker_col}=%s"
        try:
            df = pd.read_sql(sql, self.conn, params=[ticker])
        except Exception as e:
            print(f"⚠ WRDS 쿼리 실패: {e}")
            return pd.DataFrame()

        if df.empty:
            print(f"⚠ WRDS 조회 0 rows (table={table_name}, ticker={ticker})")
            return pd.DataFrame()

        if date_col not in df.columns:
            print(f"⚠ WRDS: 날짜 컬럼 '{date_col}' 이(가) 없습니다. columns={list(df.columns)[:12]}")
            return pd.DataFrame()

        df = df.copy()
        df["date"] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.set_index("date")

        # 필요한 컬럼만 남겨도 좋지만(선택), 여기선 전체 들고가서 이후 매핑에서 필터
        print(f"✓ WRDS {ticker}: {len(df)} rows  (table={table_name}, ticker_col={ticker_col}, date_col={date_col})")
        # 간단 디버그
        try:
            print("[DEBUG] WRDS columns (subset):",
                  [c for c in df.columns if c in self.WRDS_TO_EDGAR_MAPPING or c in ("ticker", "cusip", "gvkey")][:15])
            print(df[[c for c in ("saleq", "revtq", "niq", "atq", "ltq", "seqq") if c in df.columns]].head(3))
        except Exception:
            pass

        return df

    # --- WRDS → EDGAR 표준 컬럼으로 변환 ---
    def convert_wrds_to_edgar_format(self, wrds_df: pd.DataFrame) -> pd.DataFrame:
        if wrds_df is None or wrds_df.empty:
            return pd.DataFrame()
        if "date" not in wrds_df.columns:
            print("⚠ WRDS 변환 실패: date 컬럼 없음")
            return pd.DataFrame()

        df = wrds_df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).set_index("date").sort_index()

        out = {}
        for wrds_col, edgar_col in self.WRDS_TO_EDGAR_MAPPING.items():
            if wrds_col in df.columns:
                out[edgar_col] = pd.to_numeric(df[wrds_col], errors="coerce")

        converted = pd.DataFrame(out)
        converted.index.name = "date"
        print(f"✓ WRDS → EDGAR 형식 변환 완료: {converted.shape[0]} rows × {converted.shape[1]} cols")
        return converted

    # --- YTD → 분기 플로우 변환 유틸(필요 시 확장) ---
    @staticmethod
    def _to_quarter_flow(ytd_series: pd.Series) -> pd.Series:
        s = ytd_series.sort_index()
        prev_fy, prev_val = None, 0.0
        out = []
        for dt, val in s.items():
            fy = dt.year
            cur = float(val) if pd.notna(val) else np.nan
            if fy != prev_fy:
                prev_val = 0.0
                prev_fy = fy
            out.append(cur - prev_val if pd.notna(cur) else np.nan)
            prev_val = cur if pd.notna(cur) else prev_val
        q = s.copy()
        q[:] = out
        return q

    # --- 핵심: 보완 파이프라인 ---
    def validate_and_fill_improved(
            self,
            edgar_df: pd.DataFrame,
            ticker: str,
            table_name: str = "us_fundq",
            days_tolerance: int = 15,
            verbose: bool = True,
            *,
            ticker_col: str = "ticker",  #  추가
            date_col: str = "edate"  #  추가
    ) -> pd.DataFrame:
        """
        1) WRDS 로드 → EDGAR 표준 컬럼으로 변환
        2) 분기말 캘린더 자동 추정 → 양쪽 모두 표준 분기말로 스냅
        3) WRDS 스케일(단위)을 EDGAR 기준으로 자동 보정
        4) 분기말 키로 병합: EDGAR 우선, 결측만 WRDS로 보충
        5) 남은 결측은 ±days_tolerance 근접치 asof로 보조
        """
        if edgar_df is None or edgar_df.empty:
            print("⚠ EDGAR 입력 DF가 비어있습니다. WRDS만으로 구성합니다.")
            edgar_df = pd.DataFrame()

        # WRDS 로드 (이 줄 교체)
        wrds_raw = self.get_wrds_data(
            ticker=ticker,
            table_name=table_name,
            ticker_col=ticker_col,
            date_col=date_col
        )

        if wrds_raw is None or wrds_raw.empty:
            print("WRDS 데이터 없음 → EDGAR 그대로 반환")
            return edgar_df

        wrds_e = self.convert_wrds_to_edgar_format(wrds_raw)
        if wrds_e is None or wrds_e.empty:
            print("WRDS 변환 결과 없음 → EDGAR 그대로 반환")
            return edgar_df

        # 2) 분기말 표준 인덱스 부여
        e_std, w_std, pattern = standardize_quarter_dates(edgar_df, wrds_e, tolerance_days=days_tolerance)

        # 3) 스케일 보정
        w_std = harmonize_wrds_scale_to_edgar(e_std, w_std)

        # 4) 병합: EDGAR 우선
        out = e_std.reindex(e_std.index.union(w_std.index)).sort_index()
        w_grid = w_std.reindex(out.index)
        all_cols = sorted(set(out.columns) | set(w_grid.columns))

        for c in all_cols:
            if c in out.columns and c in w_grid.columns:
                out[c] = out[c].fillna(w_grid[c])
            elif c in w_grid.columns:
                out[c] = w_grid[c]

        # 5) asof 보조
        tol = pd.Timedelta(days=days_tolerance)
        for c in all_cols:
            need = out[c].isna()
            if not need.any():
                continue
            sub = w_std[[c]].dropna()
            if sub.empty:
                continue
            m1 = pd.merge_asof(out[need].sort_index(), sub.sort_index(),
                               left_index=True, right_index=True,
                               direction="backward", tolerance=tol)
            m2 = pd.merge_asof(out[need].sort_index(), sub.sort_index(),
                               left_index=True, right_index=True,
                               direction="forward", tolerance=tol)
            filled = m1[c].combine_first(m2[c])
            out.loc[need, c] = out.loc[need, c].fillna(filled)

        return out



if __name__ == "__main__":
    print("=" * 80)
    print("WRDS Data Validator (MySQL) - Integrated Version")
    print("=" * 80)
    print("\n이 모듈은 EDGAR 데이터를 WRDS MySQL 데이터로 보완합니다.")
    print(" - 분기말 스냅 + 스케일 보정 + EDGAR 우선 병합 + asof 보조")
