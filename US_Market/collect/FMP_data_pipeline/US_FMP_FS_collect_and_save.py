"""
US FMP Financial Statement Data - 수집 + DB 저장 통합 스크립트

- 티커 출처 : DATA/us_target_ticker_list_2000.py 의 ticker_list
- FMP API   : IS / BS / CF 수집 (최근 N분기)
- DB 저장   : US_FMP_FS_DATA_API_FIXED.save_financial_data_incremental() 증분 저장
- 저장 테이블: US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP

실행 예시:
    python US_FMP_FS_collect_and_save.py
    python US_FMP_FS_collect_and_save.py --quarters 8
    python US_FMP_FS_collect_and_save.py --quarters 4 --period annual
    python US_FMP_FS_collect_and_save.py --quarters 4 --collect_only
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

warnings.filterwarnings("ignore")

# ================================================================================
# sys.path 설정
# ================================================================================

def _add_path(p: str) -> None:
    if p not in sys.path:
        sys.path.insert(0, p)


# 스크립트 위치에서 상위로 올라가며 DATA 폴더 탐색
_here = Path(__file__).resolve().parent
for _p in [_here, *_here.parents]:
    if (_p / "DATA").exists():
        _add_path(str(_p))          # investment_strategy  ->  DATA.config, DATA.us_target_ticker_list_2000
        break

# US_FMP_FS_DATA_API_FIXED.py 위치 탐색 (같은 폴더 또는 FMP_data_pipeline 하위)
for _candidate in [_here, _here / "FMP_data_pipeline"]:
    if (_candidate / "US_FMP_FS_DATA_API_FIXED.py").exists():
        _add_path(str(_candidate))
        break

# ================================================================================
# 모듈 import
# ================================================================================

try:
    from DATA.us_target_ticker_list_2000 import ticker_list as ALL_TICKERS
except ImportError as e:
    print(f"[ERROR] us_target_ticker_list_2000.py import 실패: {e}")
    print("  DATA 폴더 안에 us_target_ticker_list_2000.py 가 있는지 확인하세요.")
    sys.exit(1)

try:
    from US_FMP_FS_DATA_API_FIXED import save_financial_data_incremental
except ImportError as e:
    print(f"[ERROR] US_FMP_FS_DATA_API_FIXED.py import 실패: {e}")
    print("  이 스크립트와 같은 폴더(또는 FMP_data_pipeline 폴더)에 있는지 확인하세요.")
    sys.exit(1)

# ================================================================================
# 설정
# ================================================================================

FMP_API_KEY   = "hT0gAk87j9xZx4PlBApvBqfVL5IahvgV"   # <-- 본인 FMP API Key 입력
API_BASE_URL  = "https://financialmodelingprep.com/api/v3"
REQUEST_DELAY = 0.3     # API 호출 간 대기 시간 (초)
MAX_RETRIES   = 3       # 실패 시 재시도 횟수

# ================================================================================
# FMP API 호출 유틸리티
# ================================================================================

def _fmp_get(endpoint: str, params: dict) -> list:
    """FMP REST API 단일 호출 (재시도 포함)"""
    params["apikey"] = FMP_API_KEY
    url = f"{API_BASE_URL}/{endpoint}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "Error Message" in data:
                    print(f"   [FMP ERROR] {data['Error Message']}")
                    return []
                return []
            else:
                print(f"   [HTTP {resp.status_code}] {url}")
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(REQUEST_DELAY * 2)
            else:
                print(f"   [EXCEPTION] {e}")
    return []


# ================================================================================
# FMP 재무제표 수집 함수
# ================================================================================

def fetch_income_statement(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"income-statement/{ticker}", {"period": period, "limit": limit})

def fetch_balance_sheet(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"balance-sheet-statement/{ticker}", {"period": period, "limit": limit})

def fetch_cash_flow(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"cash-flow-statement/{ticker}", {"period": period, "limit": limit})


# ================================================================================
# JSON -> long-format DataFrame 변환
# ================================================================================

# FMP API JSON 에서 제외할 메타 컬럼
_META_COLS = {
    "date", "symbol", "reportedCurrency", "cik",
    "fillingDate", "acceptedDate", "calendarYear",
    "period", "link", "finalLink",
}

def _to_long(records: list, ticker: str) -> pd.DataFrame:
    """
    FMP API 응답 레코드 리스트를 long-format DataFrame으로 변환

    출력 컬럼: ticker | date | date_month | period | item | value
    """
    if not records:
        return pd.DataFrame()

    rows = []
    for rec in records:
        date_str   = rec.get("date", "")
        period_val = rec.get("period", "")      # Q1 / Q2 / Q3 / Q4 / FY

        for key, val in rec.items():
            if key in _META_COLS:
                continue
            rows.append({
                "ticker" : ticker,
                "date"   : date_str,
                "period" : period_val,
                "item"   : key,
                "value"  : val,
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"]       = pd.to_datetime(df["date"], errors="coerce")
    df["date_month"] = df["date"].dt.to_period("M").astype(str)
    df["date"]       = df["date"].dt.strftime("%Y-%m-%d")
    df["value"]      = pd.to_numeric(df["value"], errors="coerce")

    return df


# ================================================================================
# 전체 수집 파이프라인
# ================================================================================

def collect_all(
    tickers : list,
    period  : str,
    quarters: int,
) -> tuple:
    """
    티커 리스트 전체에 대해 IS / BS / CF 수집

    Parameters
    ----------
    tickers  : 수집 대상 티커 리스트
    period   : 'quarter' 또는 'annual'
    quarters : 수집할 최근 분기(또는 연도) 수  ->  FMP limit 파라미터로 전달

    Returns
    -------
    IS, BS, CF : pd.DataFrame (long-format)
    """
    is_list, bs_list, cf_list = [], [], []
    failed = []

    for ticker in tqdm(tickers, desc="FMP 데이터 수집", ncols=100):
        try:
            is_raw = fetch_income_statement(ticker, period, quarters)
            bs_raw = fetch_balance_sheet(ticker, period, quarters)
            cf_raw = fetch_cash_flow(ticker, period, quarters)

            if is_raw:
                is_list.append(_to_long(is_raw, ticker))
            if bs_raw:
                bs_list.append(_to_long(bs_raw, ticker))
            if cf_raw:
                cf_list.append(_to_long(cf_raw, ticker))

        except Exception as e:
            tqdm.write(f"[FAILED] {ticker}: {e}")
            failed.append(ticker)

        time.sleep(REQUEST_DELAY)

    IS = pd.concat(is_list, ignore_index=True) if is_list else pd.DataFrame()
    BS = pd.concat(bs_list, ignore_index=True) if bs_list else pd.DataFrame()
    CF = pd.concat(cf_list, ignore_index=True) if cf_list else pd.DataFrame()

    print("\n" + "=" * 50)
    print("수집 완료 요약")
    print("=" * 50)
    print(f"  IS : {len(IS):>10,} rows  |  {IS['ticker'].nunique() if not IS.empty else 0} tickers")
    print(f"  BS : {len(BS):>10,} rows  |  {BS['ticker'].nunique() if not BS.empty else 0} tickers")
    print(f"  CF : {len(CF):>10,} rows  |  {CF['ticker'].nunique() if not CF.empty else 0} tickers")

    if failed:
        print(f"\n  수집 실패 티커 ({len(failed)}개): {failed}")

    return IS, BS, CF


# ================================================================================
# argparse
# ================================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FMP API에서 재무제표 수집 후 MySQL DB에 증분 저장"
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=8,
        help="수집할 최근 분기 수 (기본값: 8). annual 모드일 때는 연도 수로 동작",
    )
    parser.add_argument(
        "--period",
        choices=["quarter", "annual"],
        default="quarter",
        help="수집 주기 (기본값: quarter)",
    )
    parser.add_argument(
        "--collect_only",
        action="store_true",
        help="수집만 하고 DB 저장은 건너뜀 (디버깅 / 데이터 확인용)",
    )
    return parser.parse_args()


# ================================================================================
# 메인
# ================================================================================

def main() -> None:
    args = parse_args()

    print("=" * 70)
    print("US FMP Financial Statement 수집 + DB 저장 파이프라인")
    print("=" * 70)
    print(f"  티커 출처 : DATA/us_target_ticker_list_2000.py")
    print(f"  총 티커 수: {len(ALL_TICKERS)}개")
    print(f"  수집 주기 : {args.period}")
    print(f"  수집 기간 : 최근 {args.quarters}{'분기' if args.period == 'quarter' else '년'}")
    print(f"  DB 저장   : {'건너뜀 (--collect_only)' if args.collect_only else '증분 저장'}")
    print("=" * 70)

    # 1. 수집
    IS, BS, CF = collect_all(
        tickers  = ALL_TICKERS,
        period   = args.period,
        quarters = args.quarters,
    )

    if IS.empty and BS.empty and CF.empty:
        print("\n수집된 데이터가 없습니다. 종료합니다.")
        return

    # 2. 샘플 확인
    if not IS.empty:
        print("\n[IS 샘플 (상위 5행)]")
        print(IS.head(5).to_string(index=False))

    # 3. DB 저장
    if not args.collect_only:
        print("\nDB 저장을 시작합니다...")
        save_financial_data_incremental(
            IS = IS if not IS.empty else None,
            BS = BS if not BS.empty else None,
            CF = CF if not CF.empty else None,
            # 날짜 필터 없음 - 최근 N분기 데이터 전체 저장
            # 필요 시 아래 주석 해제 후 날짜 지정
            # start_date = "2024-01",
            # end_date   = "2024-12",
        )
    else:
        print("\n--collect_only 모드: DB 저장을 건너뜁니다.")

    print("\n파이프라인 완료.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n치명적 오류: {e}")
        traceback.print_exc()
        sys.exit(1)

# # 기본 실행 (200개 티커, 최근 8분기)
# python US_FMP_FS_collect_and_save.py
#
# # 최근 4분기만 수집
# python US_FMP_FS_collect_and_save.py --quarters 4
#
# # 연간 데이터, 최근 5년
# python US_FMP_FS_collect_and_save.py --period annual --quarters 5
#
# # 수집만 하고 DB 저장 안 함 (데이터 확인용)
# python US_FMP_FS_collect_and_save.py --quarters 4 --collect_only