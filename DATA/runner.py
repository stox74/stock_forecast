# -*- coding: utf-8 -*-
from typing import Optional, List, Dict
import pandas as pd
import gc
from .config import BATCH_SIZE_DEFAULT, MEASUREMENT_DATE, log, get_db_info
from .db_utils import (
    TABLE_VAL,
    upsert_long_to_db_on_ticker_created_at,
    ensure_valuation_table,
    upsert_enhanced_merged_df,
)
from .pipeline import process_one_ticker
from .adapters import build_fx

def _flush_batch_and_upload(batch_results: List[pd.DataFrame], db_info: Dict[str,str], created_at: str) -> int:
    """
    배치 → 성장요약 → long(valuation, revenue) → DB 업서트
    반환: 업서트된 행 수
    """
    if not batch_results:
        return 0

    ensure_valuation_table(db_info, table_name=TABLE_VAL)

    final_df = pd.concat(batch_results, axis=0, ignore_index=True)

    # summaries는 adapters 경유가 아닌, 기존 import 그대로 사용 중이면 여길 유지하세요.
    from .summaries import make_growth_summaries, to_long as _to_long

    rev_summary_batch, val_summary_batch = make_growth_summaries(final_df)
    long_val = _to_long(val_summary_batch, category='valuation', created_at=f"{created_at} 00:00:00")
    long_rev = _to_long(rev_summary_batch, category='revenue',  created_at=f"{created_at} 00:00:00")

    final_long = pd.concat([long_val, long_rev], axis=0, ignore_index=True)
    affected = upsert_long_to_db_on_ticker_created_at(final_long, db_info, table_name=TABLE_VAL)

    del batch_results[:]
    gc.collect()
    return int(affected or 0)


def run_valuation_for_tickers(
    tickers: List[str],
    batch_size: int = BATCH_SIZE_DEFAULT,
    api_key: Optional[str] = "hT0gAk87j9xZx4PlBApvBqfVL5IahvgV",
    measurement_date: Optional[str] = MEASUREMENT_DATE,
    db_info: Optional[Dict[str,str]] = None,
) -> Dict[str, int]:
    if db_info is None:
        db_info = get_db_info()

    fx = build_fx()  # ✅ 어댑터 빌드 (필수 함수/모듈 수집)

    # 커버리지 점검 (fx에서 가져옴)
    try:
        miss_q, miss_m = fx["audit_db_coverage"](db_info, tickers)
        if miss_m:
            fname = f"market_cap_missing_ticker_{pd.Timestamp.today().strftime('%Y%m%d')}.csv"
            pd.DataFrame({"ticker": miss_m}).to_csv(fname, index=False, encoding="utf-8-sig")
            log("AUDIT-SAVE", f"US_fundm missing {len(miss_m)} tickers saved -> {fname}")
        else:
            log("AUDIT", "US_fundm missing: 0 tickers")
    except Exception as e:
        log("AUDIT-SKIP", f"coverage check skipped: {e}")

    batch_results: List[pd.DataFrame] = []
    total_success_tickers = 0
    total_val_rows_upserted = 0
    total_rev_fc_rows_upserted = 0

    for idx, tk in enumerate(tickers, 1):
        log("TICKER", f"{idx}/{len(tickers)} {tk}")

        enhanced_merged_df, valuation_result, errs = process_one_ticker(
            ticker=tk, api_key=api_key, db_info=db_info, fx=fx
        )
        for e in errs:
            log("ERR", f"{e}")

        if valuation_result is None or valuation_result.empty:
            continue

        # ✅ 매 티커 처리 직후: us_revenue_forecast_result 업서트
        try:
            affected_fc = upsert_enhanced_merged_df(
                enhanced_merged_df=enhanced_merged_df,
                db_info=db_info,
                ticker=tk,
                created_at=f"{measurement_date} 00:00:00",
            )
            total_rev_fc_rows_upserted += int(affected_fc or 0)
            log("REV-FC", f"{tk} upsert={affected_fc}")
        except Exception as e:
            log("EXC-REV-FC", f"{tk} e={e}")

        batch_results.append(valuation_result)
        total_success_tickers += 1
        log("VAL-PACK", f"{tk} packed={len(valuation_result)} batch={len(batch_results)}")

        # 배치 업로드 (us_valuation_result)
        try:
            is_last = (idx == len(tickers))
            if (len(batch_results) >= batch_size) or is_last:
                log("BATCH-FLUSH", f"size={len(batch_results)} is_last={is_last}")
                upserted = _flush_batch_and_upload(batch_results, db_info, measurement_date)
                total_val_rows_upserted += upserted
                log("BATCH-UPLOADED", f"rows={upserted}, total={total_val_rows_upserted}")
        except Exception as e:
            log("EXC-BATCH-FLUSH", f"{tk} e={e}")

        # 메모리 정리
        try:
            del enhanced_merged_df, valuation_result
            gc.collect()
        except Exception:
            pass

    return {
        "success_tickers": total_success_tickers,
        "valuation_rows_upserted": total_val_rows_upserted,
        "rev_fc_rows_upserted": total_rev_fc_rows_upserted,
    }


def run_valuation_range(
    start: int,
    end: int,
    batch_size: int = BATCH_SIZE_DEFAULT,
    api_key: Optional[str] = "hT0gAk87j9xZx4PlBApvBqfVL5IahvgV",
    measurement_date: Optional[str] = MEASUREMENT_DATE,
    db_info: Optional[Dict[str,str]] = None,
) -> Dict[str, int]:
    from .summaries import ticker_list  # summaries가 없으면 adapters의 fx["ticker_list"]도 사용 가능
    tickers = ticker_list[start:end]
    return run_valuation_for_tickers(
        tickers=tickers,
        batch_size=batch_size,
        api_key=api_key,
        measurement_date=measurement_date,
        db_info=db_info,
    )
