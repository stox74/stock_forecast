# fs_pipeline.py - COMPLETE REWRITE
import pymysql
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple, Optional

# ✅ 직접 실행과 모듈 import 모두 지원
try:
    from .fs_core import safe_divide
    from .fs_dart_reader import fetch_dart_rows_by_ticker, build_dart_fs_tables
except ImportError:
    from fs_core import safe_divide
    from fs_dart_reader import fetch_dart_rows_by_ticker, build_dart_fs_tables

# ============================================================
# 컬럼명 매핑 및 표준화
# ============================================================

# ⭐ korea_fs_data의 컬럼명 → 중간 표준 컬럼명
KOREA_FS_RENAME = {
    '매출액(천원)': '매출액',
    '매출총이익(천원)': '매출총이익',
    '영업이익(천원)': '영업이익',
    '계속사업이익(천원)': '계속사업이익',
    '당기순이익(천원)': '당기순이익',
    '총자산(천원)': '자산총계',
    '유동자산(천원)': '유동자산',
    '재고자산(천원)': '재고자산',
    '총자본(천원)': '자본총계',
    '배당금지급(영업,투자,재무)(천원)': '배당금',
}

# ⭐ DART 컬럼명 → 중간 표준 컬럼명
DART_RENAME = {
    '매출액': '매출액',
    '매출이익': '매출총이익',
    '영업이익': '영업이익',
    '법인세비용차감전순이익': '계속사업이익',
    '당기순이익': '당기순이익',
    '자산이계': '자산총계',
    '유동자산': '유동자산',
    '재고자산': '재고자산',
    '자본이계': '자본총계',
    '배당금': '배당금',
}

# ⭐ 최종 사용할 컬럼 순서
FINAL_COLUMNS = [
    '매출액',
    '매출총이익',
    '영업이익',
    '계속사업이익',
    '당기순이익',
    '자산총계',
    '유동자산',
    '재고자산',
    '자본총계',
    '배당금',
]


def _connect(db_info: Dict[str, Any]):
    return pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        database=db_info["database"],
        charset="utf8mb4"
    )


# ============================================================
# korea_fs_data 관련 함수
# ============================================================

def fetch_korea_fs_data(
        db_info: Dict[str, Any],
        symbol: str,
        table_name: str = "korea_fs_data",
        symbol_variants: Optional[List[str]] = None,
        verbose: bool = False
) -> pd.DataFrame:
    """korea_fs_data 테이블에서 데이터 조회"""
    conn = _connect(db_info)
    try:
        if symbol_variants is None:
            base_ticker = symbol.replace("A", "") if symbol.startswith("A") else symbol
            symbol_variants = [
                base_ticker,
                f"A{base_ticker}",
                f"{base_ticker}.KS",
                f"{base_ticker}.KQ",
                symbol
            ]

        symbol_variants = list(set(symbol_variants))

        if verbose:
            print(f"[korea_fs_data] Trying symbols: {symbol_variants}")

        placeholders = " OR ".join(["symbol=%s"] * len(symbol_variants))
        sql = f"""
            SELECT symbol, company_name, date, indicator, value
            FROM {table_name}
            WHERE ({placeholders})
            ORDER BY date, indicator
        """

        df = pd.read_sql(sql, conn, params=symbol_variants)

        if verbose:
            if df.empty:
                print(f"[korea_fs_data] No data found")
            else:
                print(f"[korea_fs_data] Found {len(df)} rows")

        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])

        return df
    finally:
        conn.close()


def pivot_korea_fs_data(
        df: pd.DataFrame,
        verbose: bool = False
) -> pd.DataFrame:
    """
    korea_fs_data를 pivot하고 표준 컬럼명으로 변환
    """
    if df.empty:
        if verbose:
            print("[korea_fs_data] Input empty")
        return pd.DataFrame()

    # 매핑 테이블에 있는 컬럼만 필터링
    target_indicators = list(KOREA_FS_RENAME.keys())
    available = df["indicator"].unique().tolist()
    matched = [ind for ind in available if ind in target_indicators]

    if verbose:
        print(f"[korea_fs_data] Matched indicators: {len(matched)}/{len(available)}")

    if not matched:
        if verbose:
            print("[korea_fs_data] No matched indicators")
        return pd.DataFrame()

    # 필터링 후 pivot
    tmp = df[df["indicator"].isin(matched)].copy()
    pv = tmp.pivot_table(
        index="date",
        columns="indicator",
        values="value",
        aggfunc="first"
    ).sort_index()

    if verbose:
        print(f"[korea_fs_data] Before rename: {pv.columns.tolist()}")

    # ⭐ 표준 컬럼명으로 rename
    pv = pv.rename(columns=KOREA_FS_RENAME)

    if verbose:
        print(f"[korea_fs_data] After rename: {pv.columns.tolist()}")

    return pv


# ============================================================
# DART 관련 함수
# ============================================================

def process_dart_data(
        db_info: Dict[str, Any],
        ticker: str,
        table_name: str = "korea_fs_data_from_DART",
        verbose: bool = False
) -> pd.DataFrame:
    """DART 데이터를 조회하고 표준 컬럼명으로 변환"""

    if verbose:
        print(f"[DART] Fetching data for {ticker}...")

    # 원본 데이터 조회
    raw = fetch_dart_rows_by_ticker(db_info, ticker, table_name=table_name)

    if raw.empty:
        raise ValueError(f"[DART] No data for ticker={ticker}")

    # 재무제표 테이블 생성
    fs_dart = build_dart_fs_tables(raw)

    if fs_dart.empty:
        raise ValueError(f"[DART] build_dart_fs_tables returned empty")

    if verbose:
        print(f"[DART] Shape: {fs_dart.shape}")
        print(f"[DART] Columns: {fs_dart.columns.tolist()}")

    # 천원 단위로 변환 (원 → 천원)
    fs_dart = fs_dart / 1000.0

    if verbose:
        print(f"[DART] Scaled to thousands")

    # ⭐ 표준 컬럼명으로 rename
    # DART_RENAME에 있는 컬럼만 선택
    available_cols = [col for col in fs_dart.columns if col in DART_RENAME]
    fs_dart_filtered = fs_dart[available_cols].copy()
    fs_dart_renamed = fs_dart_filtered.rename(columns=DART_RENAME)

    if verbose:
        print(f"[DART] After rename: {fs_dart_renamed.columns.tolist()}")

    return fs_dart_renamed


# ============================================================
# 데이터 결합 및 지표 계산
# ============================================================

def merge_fs_data(
        korea_fs: pd.DataFrame,
        dart_fs: pd.DataFrame,
        verbose: bool = False
) -> pd.DataFrame:
    """
    korea_fs_data와 DART 데이터를 결합
    korea_fs_data 우선, DART는 보조
    """

    if verbose:
        print(f"\n[MERGE] Starting merge...")
        print(f"[MERGE] korea_fs shape: {korea_fs.shape if not korea_fs.empty else 'empty'}")
        print(f"[MERGE] dart shape: {dart_fs.shape if not dart_fs.empty else 'empty'}")

    if korea_fs.empty and dart_fs.empty:
        raise ValueError("Both korea_fs_data and DART are empty")

    if korea_fs.empty:
        merged = dart_fs.copy()
        if verbose:
            print(f"[MERGE] Using DART only")
    elif dart_fs.empty:
        merged = korea_fs.copy()
        if verbose:
            print(f"[MERGE] Using korea_fs_data only")
    else:
        # 날짜 union
        all_dates = korea_fs.index.union(dart_fs.index)

        # reindex
        korea_reindexed = korea_fs.reindex(all_dates)
        dart_reindexed = dart_fs.reindex(all_dates)

        # korea_fs_data 우선으로 결합
        merged = korea_reindexed.combine_first(dart_reindexed)

        if verbose:
            print(f"[MERGE] Combined shape: {merged.shape}")

            # 각 컬럼별 데이터 소스 추적
            for col in FINAL_COLUMNS[:5]:
                if col in merged.columns:
                    korea_count = korea_fs[col].notna().sum() if col in korea_fs.columns else 0
                    dart_count = dart_fs[col].notna().sum() if col in dart_fs.columns else 0
                    merged_count = merged[col].notna().sum()
                    print(f"[MERGE]   {col}: korea={korea_count}, dart={dart_count}, total={merged_count}")

    # 최종 컬럼 정리
    for col in FINAL_COLUMNS:
        if col not in merged.columns:
            merged[col] = np.nan

    merged = merged[FINAL_COLUMNS].sort_index()
    merged.index.name = "date"

    if verbose:
        print(f"[MERGE] Final shape: {merged.shape}")
        print(f"[MERGE] Final columns: {merged.columns.tolist()}")

    return merged


def compute_financial_ratios(df: pd.DataFrame, verbose: bool = False) -> pd.DataFrame:
    """TTM 및 재무비율 계산"""

    if verbose:
        print(f"\n[RATIOS] Computing TTM and financial ratios...")

    out = df.copy()

    # TTM (Trailing Twelve Months)
    out["매출액_ttm"] = out["매출액"].rolling(4).sum()
    out["매출총이익_ttm"] = out["매출총이익"].rolling(4).sum()
    out["영업이익_ttm"] = out["영업이익"].rolling(4).sum()
    out["당기순이익_ttm"] = out["당기순이익"].rolling(4).sum()

    # 평균 자산/자본 (4분기 전 값과 현재 값의 평균)
    out["자본총계_lag4"] = out["자본총계"].shift(4)
    out["자산총계_lag4"] = out["자산총계"].shift(4)
    out["자본총계_평균"] = (out["자본총계"] + out["자본총계_lag4"]) / 2
    out["자산총계_평균"] = (out["자산총계"] + out["자산총계_lag4"]) / 2

    # 수익성 지표
    out["GPM_ttm"] = safe_divide(out["매출총이익_ttm"], out["매출액_ttm"])  # 매출총이익률
    out["OPM_ttm"] = safe_divide(out["영업이익_ttm"], out["매출액_ttm"])  # 영업이익률
    out["NIM_ttm"] = safe_divide(out["당기순이익_ttm"], out["매출액_ttm"])  # 순이익률

    # 효율성 지표
    out["ROA"] = safe_divide(out["당기순이익_ttm"], out["자산총계_평균"])  # 총자산이익률
    out["ROE"] = safe_divide(out["당기순이익_ttm"], out["자본총계_평균"])  # 자기자본이익률

    # 배당 지표
    out["payout_ratio"] = safe_divide(out["배당금"], out["당기순이익_ttm"])  # 배당성향

    if verbose:
        print(f"[RATIOS] Computed TTM and ratios")
        print(f"[RATIOS] Final shape: {out.shape}")

    return out


# ============================================================
# 메인 함수
# ============================================================

def build_merged_df_for_ticker(
        db_info: Dict[str, Any],
        ticker: str,
        table_name_dart: str = "korea_fs_data_from_DART",
        table_name_fn: str = "korea_fs_data",
        verbose: bool = False,
) -> pd.DataFrame:
    """
    단일 ticker에 대해 DART + korea_fs_data 결합 후 지표 계산
    """

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Processing ticker: {ticker}")
        print(f"{'=' * 60}")

    # 1) DART 데이터 처리
    try:
        dart_fs = process_dart_data(db_info, ticker, table_name_dart, verbose)
    except Exception as e:
        if verbose:
            print(f"[ERROR] DART processing failed: {e}")
        dart_fs = pd.DataFrame()

    # 2) korea_fs_data 처리
    symbol = "A" + ticker if not ticker.startswith("A") else ticker
    korea_raw = fetch_korea_fs_data(db_info, symbol, table_name_fn, verbose=verbose)

    if not korea_raw.empty:
        korea_fs = pivot_korea_fs_data(korea_raw, verbose)
    else:
        if verbose:
            print(f"[korea_fs_data] No data found")
        korea_fs = pd.DataFrame()

    # 3) 결합
    merged = merge_fs_data(korea_fs, dart_fs, verbose)

    # 4) 재무비율 계산
    result = compute_financial_ratios(merged, verbose)

    if verbose:
        print(f"{'=' * 60}\n")

    return result


def df_to_long_format(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Wide format → Long format 변환"""
    if df.empty:
        return pd.DataFrame(columns=["date", "ticker", "indicator", "value"])

    out = df.copy().reset_index()
    long_df = pd.melt(out, id_vars=["date"], var_name="indicator", value_name="value")
    long_df["ticker"] = ticker

    return long_df[["date", "ticker", "indicator", "value"]]


def build_long_for_tickers(
        db_info: Dict[str, Any],
        tickers: List[str],
        table_name_dart: str = "korea_fs_data_from_DART",
        table_name_fn: str = "korea_fs_data",
        verbose: bool = False,
        **kwargs  # item_list_fn 등 하위 호환성 유지
) -> Tuple[pd.DataFrame, List[Tuple[str, str]]]:
    """
    여러 ticker 처리하여 long format으로 반환
    """
    all_long = []
    errors: List[Tuple[str, str]] = []

    for i, tkr in enumerate(tickers, 1):
        if verbose:
            print(f"\n[{i}/{len(tickers)}] Processing {tkr}...")

        try:
            merged = build_merged_df_for_ticker(
                db_info=db_info,
                ticker=tkr,
                table_name_dart=table_name_dart,
                table_name_fn=table_name_fn,
                verbose=verbose,
            )
            long_df = df_to_long_format(merged, tkr)
            all_long.append(long_df)

            if verbose:
                print(f"   ✓ Success: {len(long_df)} rows")

        except Exception as e:
            errors.append((tkr, str(e)))
            if verbose:
                print(f"   ✗ Error: {str(e)}")

    if all_long:
        result = pd.concat(all_long, ignore_index=True)
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"Summary: {len(all_long)}/{len(tickers)} successful")
            print(f"Total rows: {len(result)}")
            print(f"{'=' * 60}")
        return result, errors

    return pd.DataFrame(columns=["date", "ticker", "indicator", "value"]), errors


# ============================================================
# 유틸리티 함수
# ============================================================

def get_available_indicators(
        db_info: Dict[str, Any],
        table_name: str = "korea_fs_data"
) -> List[str]:
    """korea_fs_data 테이블의 모든 indicator 조회"""
    conn = _connect(db_info)
    try:
        sql = f"SELECT DISTINCT indicator FROM {table_name} ORDER BY indicator"
        df = pd.read_sql(sql, conn)
        return df["indicator"].tolist()
    finally:
        conn.close()


def inspect_korea_fs_data_for_ticker(
        db_info: Dict[str, Any],
        ticker: str,
        table_name: str = "korea_fs_data"
) -> Dict[str, Any]:
    """특정 ticker의 korea_fs_data 상태 점검"""
    result = {
        "ticker": ticker,
        "found": False,
        "symbol_used": None,
        "row_count": 0,
        "date_range": None,
        "indicators": [],
        "sample_data": None
    }

    symbol_variants = [ticker, f"A{ticker}", f"{ticker}.KS", f"{ticker}.KQ"]

    for sym in symbol_variants:
        df = fetch_korea_fs_data(db_info, sym, table_name=table_name)
        if not df.empty:
            result["found"] = True
            result["symbol_used"] = sym
            result["row_count"] = len(df)
            result["date_range"] = (df["date"].min(), df["date"].max())
            result["indicators"] = df["indicator"].unique().tolist()
            result["sample_data"] = df.head(10)
            break

    return result


if __name__ == "__main__":
    print("fs_pipeline.py - Complete rewrite")
    print(f"Standard columns: {FINAL_COLUMNS}")