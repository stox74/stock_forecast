"""
================================================================================
[1단계 / 메인 실행 파일] US_FMP_FS_1_RUN_UPDATE.py
================================================================================
▶  매월말 재무제표 업데이트 시 "이 파일 하나만" 실행하면 됩니다.

파이프라인 구조 (실행 순서):
    [1] US_FMP_FS_1_RUN_UPDATE.py   ← 지금 이 파일 (직접 실행)
         │  FMP API에서 티커별 IS/BS/CF 수집
         │  배치 단위(기본 200티커)로 즉시 DB 저장 + 체크포인트 기록
         ▼
    [2] US_FMP_FS_2_DB_SAVE_LIB.py  ← 보조 모듈 (자동 import, 직접 실행 X)
            UPSERT 저장: 신규 데이터는 INSERT, 기존 키의 값 변경
            (8-K 속보치 → 10-Q/10-K 확정치, restatement)은 UPDATE
            → 기간별 재무데이터가 "항상 최신"으로 유지됨

구버전 대비 개선 사항
=====================
1. UPSERT 적용 (v2 라이브러리) — 매 실행마다 최근 N분기를 재수집해 덮어쓰므로
   확정치/정정치가 자동 반영됨 (구버전 INSERT IGNORE는 갱신 불가였음)
2. 배치 저장 + 체크포인트 (done_tickers_fmp_fs.txt)
   - 구버전: 2,000티커 전량 수집 후 일괄 저장 → 중간에 끊기면 전부 유실
   - 신버전: 배치(기본 200티커)마다 DB에 저장하고 완료 티커를 기록
     → --skip_done 옵션으로 중단 지점부터 안전 재시작 가능
3. --quarters 기본값 불일치 수정 (구버전: 기본 2인데 help는 8이라고 표기)
4. Rate limit 보호 — FMP Starter 250콜/분, 티커당 3콜 기준으로 딜레이 산정

실행 예시:
    # 매월말 정기 업데이트 (전체 티커, 최근 8분기, UPSERT)
    python US_FMP_FS_1_RUN_UPDATE.py

    # 최근 4분기만
    python US_FMP_FS_1_RUN_UPDATE.py --quarters 4

    # 연간 데이터 최근 5년
    python US_FMP_FS_1_RUN_UPDATE.py --period annual --quarters 5

    # 중단됐던 작업 이어서 재시작
    python US_FMP_FS_1_RUN_UPDATE.py --skip_done

    # 수집만 하고 저장 안 함 (디버깅)
    python US_FMP_FS_1_RUN_UPDATE.py --collect_only --tickers AAPL,MSFT,FIX
================================================================================
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
        _add_path(str(_p))
        break

# 보조 모듈(US_FMP_FS_2_DB_SAVE_LIB.py) 위치 탐색
for _candidate in [_here, _here / "FMP_data_pipeline"]:
    if (_candidate / "US_FMP_FS_2_DB_SAVE_LIB.py").exists():
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
    from US_FMP_FS_2_DB_SAVE_LIB import (
        save_financial_data_incremental,
        print_table_stats,
        close_engine,
    )
except ImportError as e:
    print(f"[ERROR] US_FMP_FS_2_DB_SAVE_LIB.py import 실패: {e}")
    print("  이 스크립트와 같은 폴더(또는 FMP_data_pipeline 폴더)에 있는지 확인하세요.")
    sys.exit(1)

# ================================================================================
# 설정
# ================================================================================

FMP_API_KEY  = "hT0gAk87j9xZx4PlBApvBqfVL5IahvgV"   # TODO: DATA/config.py로 이동 권장
API_BASE_URL = "https://financialmodelingprep.com/api/v3"

# FMP Starter 플랜: 250 calls/min. 티커당 3콜(IS/BS/CF)이므로
# 티커 사이클 최소 시간 = 3 * 60 / 250 = 0.72초. 여유를 두고 0.8초 적용.
REQUEST_DELAY = 0.8
MAX_RETRIES   = 3

# 체크포인트 파일 (완료 티커 기록 → --skip_done으로 재시작)
CHECKPOINT_FILE = _here / "done_tickers_fmp_fs.txt"
FAILED_FILE     = _here / "failed_tickers_fmp_fs.txt"


# ================================================================================
# 체크포인트 유틸리티
# ================================================================================

def load_done_tickers() -> set:
    if CHECKPOINT_FILE.exists():
        return set(CHECKPOINT_FILE.read_text(encoding="utf-8").split())
    return set()


def append_done_tickers(tickers: list) -> None:
    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        for t in tickers:
            f.write(t + "\n")


def reset_checkpoint() -> None:
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
    if FAILED_FILE.exists():
        FAILED_FILE.unlink()


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
                    tqdm.write(f"   [FMP ERROR] {data['Error Message'][:120]}")
                    return []
                return []
            elif resp.status_code == 429:
                # Rate limit 도달 → 대기 후 재시도
                tqdm.write(f"   [429 Rate Limit] {endpoint} — 20초 대기 후 재시도")
                time.sleep(20)
            else:
                tqdm.write(f"   [HTTP {resp.status_code}] {url}")
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(REQUEST_DELAY * 2)
            else:
                tqdm.write(f"   [EXCEPTION] {e}")
    return []


def fetch_income_statement(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"income-statement/{ticker}", {"period": period, "limit": limit})


def fetch_balance_sheet(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"balance-sheet-statement/{ticker}", {"period": period, "limit": limit})


def fetch_cash_flow(ticker: str, period: str, limit: int) -> list:
    return _fmp_get(f"cash-flow-statement/{ticker}", {"period": period, "limit": limit})


# ================================================================================
# JSON -> long-format DataFrame 변환
# ================================================================================

# FMP API JSON에서 항목(item)으로 저장하지 않을 메타 컬럼
# 참고: 비미국 기업으로 확장 시 reportedCurrency는 FX 변환에 필요하므로
#       이 set에서 제거해 item으로 함께 저장할 것
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
                "ticker": ticker,
                "date":   date_str,
                "period": period_val,
                "item":   key,
                "value":  val,
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
# 배치 단위 수집 + 즉시 저장
# ================================================================================

def collect_and_save_in_batches(
    tickers: list,
    period: str,
    quarters: int,
    batch_size: int = 200,
    collect_only: bool = False,
) -> dict:
    """
    티커를 배치로 나눠 [수집 → 즉시 DB 저장 → 체크포인트 기록]을 반복

    구버전과의 차이: 전량 수집 후 일괄 저장이 아니라 배치마다 저장하므로
    중간에 중단되어도 이미 저장된 배치는 유실되지 않음 (--skip_done으로 재시작)
    """
    n_total = len(tickers)
    n_batches = (n_total + batch_size - 1) // batch_size
    failed_all = []
    saved_tables_union = set()
    collected_frames = {"IS": [], "BS": [], "CF": []}  # collect_only 모드용

    for b in range(n_batches):
        batch = tickers[b * batch_size: (b + 1) * batch_size]
        print(f"\n{'=' * 70}")
        print(f"[배치 {b + 1}/{n_batches}] 티커 {len(batch)}개 "
              f"({b * batch_size + 1}~{min((b + 1) * batch_size, n_total)}/{n_total}, "
              f"{(b + 1) / n_batches * 100:.1f}%)")
        print("=" * 70)

        is_list, bs_list, cf_list = [], [], []
        batch_failed = []

        for ticker in tqdm(batch, desc=f"FMP 수집 (batch {b + 1})", ncols=100):
            try:
                is_raw = fetch_income_statement(ticker, period, quarters)
                bs_raw = fetch_balance_sheet(ticker, period, quarters)
                cf_raw = fetch_cash_flow(ticker, period, quarters)

                got_any = False
                if is_raw:
                    is_list.append(_to_long(is_raw, ticker))
                    got_any = True
                if bs_raw:
                    bs_list.append(_to_long(bs_raw, ticker))
                    got_any = True
                if cf_raw:
                    cf_list.append(_to_long(cf_raw, ticker))
                    got_any = True

                if not got_any:
                    batch_failed.append(ticker)

            except Exception as e:
                tqdm.write(f"[FAILED] {ticker}: {e}")
                batch_failed.append(ticker)

            time.sleep(REQUEST_DELAY)

        IS = pd.concat(is_list, ignore_index=True) if is_list else pd.DataFrame()
        BS = pd.concat(bs_list, ignore_index=True) if bs_list else pd.DataFrame()
        CF = pd.concat(cf_list, ignore_index=True) if cf_list else pd.DataFrame()

        print(f"   수집: IS {len(IS):,} / BS {len(BS):,} / CF {len(CF):,} rows"
              + (f"  |  실패 {len(batch_failed)}개" if batch_failed else ""))

        if collect_only:
            collected_frames["IS"].append(IS)
            collected_frames["BS"].append(BS)
            collected_frames["CF"].append(CF)
        else:
            # 배치 즉시 저장 (UPSERT → 기존 키의 값 변경도 자동 갱신)
            saved = save_financial_data_incremental(
                IS=IS if not IS.empty else None,
                BS=BS if not BS.empty else None,
                CF=CF if not CF.empty else None,
                mode="upsert",
                verify=False,      # 배치마다 풀 카운트 방지, 마지막에 일괄 검증
                verbose=True,
            )
            saved_tables_union.update(saved)

            # 저장 성공한 배치의 티커를 체크포인트에 기록
            done_in_batch = [t for t in batch if t not in batch_failed]
            append_done_tickers(done_in_batch)

        failed_all.extend(batch_failed)

    # 실패 티커 파일 기록
    if failed_all:
        FAILED_FILE.write_text("\n".join(failed_all), encoding="utf-8")
        print(f"\n⚠️  수집 실패 티커 {len(failed_all)}개 → {FAILED_FILE.name} 기록")

    result = {
        "failed": failed_all,
        "saved_tables": sorted(saved_tables_union),
    }

    if collect_only:
        result["IS"] = pd.concat(collected_frames["IS"], ignore_index=True) \
            if collected_frames["IS"] else pd.DataFrame()
        result["BS"] = pd.concat(collected_frames["BS"], ignore_index=True) \
            if collected_frames["BS"] else pd.DataFrame()
        result["CF"] = pd.concat(collected_frames["CF"], ignore_index=True) \
            if collected_frames["CF"] else pd.DataFrame()

    return result


# ================================================================================
# argparse
# ================================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FMP API에서 재무제표 수집 후 MySQL DB에 UPSERT 증분 저장"
    )
    parser.add_argument(
        "--quarters", type=int, default=8,
        help="수집할 최근 분기 수 (기본값: 8). --period annual일 때는 연도 수",
    )
    parser.add_argument(
        "--period", choices=["quarter", "annual"], default="quarter",
        help="수집 주기 (기본값: quarter)",
    )
    parser.add_argument(
        "--batch_size", type=int, default=200,
        help="배치당 티커 수 — 배치마다 DB 저장 및 체크포인트 기록 (기본값: 200)",
    )
    parser.add_argument(
        "--skip_done", action="store_true",
        help="체크포인트(done_tickers_fmp_fs.txt)에 기록된 티커를 건너뜀 (중단 후 재시작용). "
             "미지정 시 체크포인트를 초기화하고 전체 재수집 (정기 업데이트 기본 동작)",
    )
    parser.add_argument(
        "--collect_only", action="store_true",
        help="수집만 하고 DB 저장은 건너뜀 (디버깅 / 데이터 확인용)",
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="쉼표 구분 티커 지정 (예: AAPL,MSFT,FIX) — 미지정 시 전체 리스트 사용",
    )
    return parser.parse_args()


# ================================================================================
# 메인
# ================================================================================

def main() -> None:
    args = parse_args()

    # 티커 결정
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        ticker_source = f"--tickers 인자 ({len(tickers)}개)"
    else:
        tickers = list(ALL_TICKERS)
        ticker_source = f"DATA/us_target_ticker_list_2000.py ({len(tickers)}개)"

    # 체크포인트 처리
    # - 기본(정기 업데이트): 체크포인트 초기화 후 전체 재수집 (SKIP_DONE=False 원칙)
    # - --skip_done: 완료 티커 건너뛰고 이어서 진행
    if args.skip_done:
        done = load_done_tickers()
        before = len(tickers)
        tickers = [t for t in tickers if t not in done]
        print(f"체크포인트: 완료 {len(done)}개 건너뜀 → 남은 티커 {len(tickers)}/{before}개")
    else:
        reset_checkpoint()

    est_min = len(tickers) * REQUEST_DELAY / 60

    print("=" * 70)
    print("US FMP Financial Statement 수집 + DB UPSERT 파이프라인")
    print("=" * 70)
    print(f"  [1] 실행 파일 : US_FMP_FS_1_RUN_UPDATE.py (현재)")
    print(f"  [2] 보조 모듈 : US_FMP_FS_2_DB_SAVE_LIB.py (자동 import)")
    print("-" * 70)
    print(f"  티커 출처  : {ticker_source}")
    print(f"  수집 주기  : {args.period}")
    print(f"  수집 기간  : 최근 {args.quarters}{'분기' if args.period == 'quarter' else '년'}")
    print(f"  배치 크기  : {args.batch_size} 티커 (배치마다 저장+체크포인트)")
    print(f"  저장 방식  : {'건너뜀 (--collect_only)' if args.collect_only else 'UPSERT (기존 키 값 변경 시 갱신)'}")
    print(f"  예상 시간  : 약 {est_min:.0f}분 이상 (API 딜레이 기준)")
    print("=" * 70)

    if not tickers:
        print("\n처리할 티커가 없습니다. (--skip_done으로 전부 완료된 상태)")
        return

    result = collect_and_save_in_batches(
        tickers=tickers,
        period=args.period,
        quarters=args.quarters,
        batch_size=args.batch_size,
        collect_only=args.collect_only,
    )

    # 최종 검증
    if not args.collect_only and result["saved_tables"]:
        print_table_stats(result["saved_tables"])

    if args.collect_only:
        print("\n--collect_only 모드: DB 저장을 건너뛰었습니다.")
        for name in ["IS", "BS", "CF"]:
            df = result.get(name, pd.DataFrame())
            print(f"  {name}: {len(df):,} rows"
                  + (f" / {df['ticker'].nunique()} tickers" if not df.empty else ""))
        if not result["IS"].empty:
            print("\n[IS 샘플 (상위 5행)]")
            print(result["IS"].head(5).to_string(index=False))

    print("\n" + "=" * 70)
    print("파이프라인 완료.")
    if result["failed"]:
        print(f"⚠️  실패 티커 {len(result['failed'])}개 — {FAILED_FILE.name} 확인 후")
        print(f"    python US_FMP_FS_1_RUN_UPDATE.py --tickers "
              f"{','.join(result['failed'][:5])}{',...' if len(result['failed']) > 5 else ''}")
        print("    형태로 개별 재수집 가능")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        print("→ 이미 저장된 배치는 DB에 반영되어 있습니다.")
        print("→ 이어서 재시작: python US_FMP_FS_1_RUN_UPDATE.py --skip_done")
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f"\n치명적 오류: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            close_engine()
        except Exception:
            pass
