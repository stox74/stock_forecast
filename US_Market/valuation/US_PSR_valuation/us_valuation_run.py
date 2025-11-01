# -*- coding: utf-8 -*-

import sys
import os
import warnings
import argparse
import gc
import traceback
import pandas as pd
import numpy as np
from pathlib import Path

# 프로젝트 경로 설정
from utils import add_repo_path, log, to_month_end_safe

project_root = add_repo_path()

# 모듈 임포트
import importlib
import DATA.us_sarima_forecast as sarima

importlib.reload(sarima)
import DATA.us_lstm_forecast_v2 as lstm_v2

importlib.reload(lstm_v2)
import DATA.us_prophet_forecast_v3 as prophet_v3

importlib.reload(prophet_v3)
import DATA.us_est_forecast_v2 as esmod

importlib.reload(esmod)

from DATA.stock_invest_function import get_db_host

# 커스텀 모듈
from config import api_key, db_config, BATCH_SIZE, start_date_month, get_default_month_end_str
from db_audit import audit_db_coverage
from data_fetch import (fetch_revenue_data, fetch_market_data_yearly, fetch_db_revenue_data,
                        safe_get_db_market_df, process_daily_to_monthly_market_data)
from data_processing import clean_rev_data, calculate_enhanced_ttm_and_psr, prepare_revenue_ttm
from forecast_models import run_revenue_forecasts, run_psr_forecasts, pick_columns
from valuation import calculate_valuation, flush_batch_and_upload
from db_batch_save import save_batch_results_to_db

warnings.filterwarnings('ignore')


def process_ticker(ticker, idx, total_tickers, api_key, db_info, start_date_month, end_date_month,
                   batch_results, batch_revenue_results, error_ticker_list):
    """개별 ticker 처리"""
    log("TICKER", f"{idx}/{total_tickers} {ticker}")

    # 1. FMP에서 매출 데이터 가져오기
    revenue_data, error = fetch_revenue_data(ticker, api_key)
    if revenue_data is None:
        msg = f"FMP revenue fetch failed: {error}"
        log("ERR-FMP-REV", f"{ticker} {msg}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_revenue', 'error': msg})
        return False

    rows = len(revenue_data) if isinstance(revenue_data, list) else 0
    log("OK-FMP-REV", f"{ticker} raw_rows={rows}")

    # 2. 매출 데이터 전처리
    try:
        all_revenue_data = [{
            'ticker': ticker,
            'date': it.get('date', ''),
            'calendar_year': it.get('calendarYear', ''),
            'period': it.get('period', ''),
            'revenue': it.get('revenue', 0) if it.get('revenue') is not None else 0,
            'revenue_billions': round((it.get('revenue', 0) or 0) / 1_000_000_000, 2),
        } for it in revenue_data]

        fmp_revenue_df = pd.DataFrame(all_revenue_data)
        fmp_revenue_df['date'] = pd.to_datetime(fmp_revenue_df['date'])
        fmp_revenue_df = fmp_revenue_df.sort_values(['ticker', 'date'])
        fmp_revenue_df['date_month_end'] = to_month_end_safe(fmp_revenue_df['date'])
        bad = fmp_revenue_df['date_month_end'].isna().sum()
        log("CHK-FMP-MEND", f"{ticker} nan_mend={bad} / {len(fmp_revenue_df)}")
        fmp_revenue_df = fmp_revenue_df.dropna(subset=['date_month_end'])
        fmp_revenue_df = fmp_revenue_df.drop_duplicates(subset=['date_month_end']).sort_values(
            'date_month_end').reset_index(drop=True)
    except Exception as e:
        log("EXC-FMP-REV", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_revenue_preproc', 'error': str(e)})
        return False

    # 3. DB에서 매출 데이터 가져오기 및 병합
    try:
        db_revenue_raw = fetch_db_revenue_data(ticker, db_info)
        rows_db = 0 if db_revenue_raw is None else len(db_revenue_raw)
        log("OK-DB-REV", f"{ticker} rows={rows_db}")

        if rows_db:
            db_revenue_df = db_revenue_raw.loc[
                db_revenue_raw['revenue_billions'] != db_revenue_raw['revenue_billions'].shift()
                ]
        else:
            db_revenue_df = pd.DataFrame(columns=['ticker', 'date', 'date_month_end', 'revenue_billions'])

        merged_rev_data = pd.merge(fmp_revenue_df, db_revenue_df, on=['ticker', 'date_month_end'], how='outer')
        rev_data = merged_rev_data[merged_rev_data['date_month_end'] >= start_date_month]

        if 'revenue_billions_x' in rev_data.columns:
            rev_data['revenue_billions_x'] = rev_data['revenue_billions_x'].fillna(
                rev_data.get('revenue_billions_y'))
            rev_data = rev_data.rename(columns={'revenue_billions_x': 'revenue_billions'})

        log("REV-MERGE", f"{ticker} merged_rows={len(rev_data)}")

        rev_data = clean_rev_data(rev_data)
        yr_min = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].min()
        yr_max = None if 'calendar_year' not in rev_data else rev_data['calendar_year'].max()
        log("OK-REV-CLEAN", f"{ticker} rows={len(rev_data)} yrmin={yr_min} yrmax={yr_max}")
    except Exception as e:
        log("EXC-DB-REV", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'db_revenue_merge_clean', 'error': str(e)})
        return False

    # 4. 매출 예측
    sarima_df, lstm_df, prophet_raw_df, es_raw_df, error = run_revenue_forecasts(
        rev_data, ticker, sarima, lstm_v2, prophet_v3, esmod
    )
    if error:
        error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_forecast', 'error': error})
        return False

    # 5. 시가총액 데이터 가져오기
    try:
        market_data, _ = fetch_market_data_yearly(ticker, api_key, start_year=2010)
        if not market_data:
            msg = "FMP market data fetch failed"
            log("ERR-FMP-MCAP", f"{ticker} {msg}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'fetch_market', 'error': msg})
            return False

        fmp_market_df = process_daily_to_monthly_market_data(market_data, ticker).copy()
        fmp_market_df['date_month_end'] = to_month_end_safe(fmp_market_df['date'])
        fmp_market_df = fmp_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
            'date_month_end').reset_index(drop=True)
        log("OK-FMP-MCAP",
            f"{ticker} rows={len(fmp_market_df)}")
    except Exception as e:
        log("EXC-FMP-MCAP", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'fmp_market_preproc', 'error': str(e)})
        return False

    # 6. DB 시가총액 데이터와 병합
    try:
        db_market_df = safe_get_db_market_df(ticker, db_info)
        if (not db_market_df.empty) and ('date_month_end' not in db_market_df.columns):
            if 'date' in db_market_df.columns:
                db_market_df['date_month_end'] = to_month_end_safe(db_market_df['date'])
            else:
                db_market_df = pd.DataFrame()

        if db_market_df.empty:
            merged_market_df = fmp_market_df.copy()
            merged_market_df['market_cap_billions_from_db'] = np.nan
        else:
            if 'market_cap_billions' in db_market_df.columns:
                db_market_df_renamed = db_market_df.rename(
                    columns={'market_cap_billions': 'market_cap_billions_from_db'})
            else:
                db_market_df_renamed = db_market_df[['date_month_end']].copy()
                db_market_df_renamed['market_cap_billions_from_db'] = np.nan
            merged_market_df = fmp_market_df.merge(
                db_market_df_renamed[['date_month_end', 'market_cap_billions_from_db']],
                on='date_month_end', how='left'
            )

        if 'market_cap_billions' not in merged_market_df.columns:
            merged_market_df['market_cap_billions'] = np.nan
        if 'market_cap_billions_from_db' not in merged_market_df.columns:
            merged_market_df['market_cap_billions_from_db'] = np.nan

        merged_market_df['market_cap_billions'] = merged_market_df['market_cap_billions'].fillna(
            merged_market_df['market_cap_billions_from_db']
        )
        merged_market_df = merged_market_df.drop_duplicates(subset=['date_month_end']).sort_values(
            'date_month_end').reset_index(drop=True)
        log("OK-MCAP-MERGE",
            f"{ticker} rows={len(merged_market_df)} nan_mcap={merged_market_df['market_cap_billions'].isna().sum()}")
    except Exception as e:
        log("EXC-MCAP-MERGE", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'market_merge', 'error': str(e)})
        return False

    # 7. PSR 계산을 위한 데이터 준비
    try:
        enhanced_merged_df = pd.merge(
            merged_market_df[['date_month_end', 'market_cap_billions']],
            rev_data, on='date_month_end', how='outer'
        )
        market_cap_resize = enhanced_merged_df[
            ['date_month_end', 'market_cap_billions', 'ticker', 'revenue_billions']].copy()
        market_cap_resize.dropna(subset=['market_cap_billions'], inplace=True)
        market_cap_resize.ffill(limit=2, inplace=True)
        market_cap_resize = market_cap_resize[(market_cap_resize['date_month_end'] >= start_date_month) & (
                market_cap_resize['date_month_end'] <= end_date_month)]
        market_cap_resize = market_cap_resize.dropna(axis=0)
        log("OK-PSR-PREP", f"{ticker} rows={len(market_cap_resize)}")

        enhanced_merged_df_with_ttm = calculate_enhanced_ttm_and_psr(market_cap_resize)

        psr_ok = enhanced_merged_df_with_ttm[['date_month_end', 'PSR_ttm']].dropna()
        if psr_ok.empty or psr_ok['PSR_ttm'].count() < 6:
            msg = "PSR series too short after TTM shift"
            log("ERR-PSR-SHORT", f"{ticker} {msg}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'psr_prepare', 'error': msg})
            return False
    except Exception as e:
        log("EXC-PSR-PREP", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'psr_prepare', 'error': str(e)})
        return False

    # 8. PSR 예측
    psr_sarima_df, psr_lstm_df, psr_prophet_df, psr_es_df, error = run_psr_forecasts(
        enhanced_merged_df_with_ttm, ticker, sarima, lstm_v2, prophet_v3, esmod
    )
    if error:
        error_ticker_list.append({'ticker': ticker, 'stage': 'psr_forecast', 'error': error})
        return False

    # 9. 밸류에이션 계산
    try:
        rev_sarima = pick_columns(sarima_df, ['revenue_billions_sarima_noexog'])
        rev_lstm = pick_columns(lstm_df, ['revenue_billions_lstm_forecast'])
        rev_prophet = pick_columns(prophet_raw_df, ['revenue_billions_prophet_forecast'])
        rev_es = pick_columns(es_raw_df, ['revenue_billions_esq_forecast'])

        revenue_forecast_df = pd.concat([rev_sarima, rev_lstm, rev_prophet, rev_es], axis=1, join='outer')

        # Revenue forecast 데이터를 배치에 추가
        revenue_forecast_for_db = revenue_forecast_df.reset_index()
        revenue_forecast_for_db['ticker'] = ticker
        batch_revenue_results.append(revenue_forecast_for_db)
        log("OK-REV-BATCH", f"{ticker} added to revenue batch, batch_size={len(batch_revenue_results)}")

        # TTM 계산
        revenue_forecast_df_reset = revenue_forecast_df.reset_index()
        if 'ticker' not in revenue_forecast_df_reset.columns:
            revenue_forecast_df_reset['ticker'] = ticker

        revenue_forecast_ = prepare_revenue_ttm(revenue_forecast_df_reset)
        revenue_forecast_ttm = revenue_forecast_.filter(like='_ttm').copy()

        if 'ticker' not in revenue_forecast_df_reset.columns or revenue_forecast_df_reset['ticker'].isna().all():
            raise ValueError("ticker 칼럼이 필요합니다.")

        if 'date_month_end' not in revenue_forecast_.columns:
            revenue_forecast_ttm['date_month_end'] = revenue_forecast_df_reset['date_month_end'].values
        else:
            revenue_forecast_ttm['date_month_end'] = revenue_forecast_['date_month_end'].values
        revenue_forecast_ttm = revenue_forecast_ttm.drop_duplicates(subset=['date_month_end']).set_index(
            'date_month_end')

        revenue_cols_ttm = [
            'revenue_billions_sarima_noexog_ttm',
            'revenue_billions_lstm_forecast_ttm',
            'revenue_billions_prophet_forecast_ttm',
            'revenue_billions_esq_forecast_ttm'
        ]
        revenue_forecast_ttm['revenue_billions_avg_of_4_ttm'] = revenue_forecast_ttm[revenue_cols_ttm].mean(axis=1)

        psr_sarima = pick_columns(psr_sarima_df, ['PSR_ttm_sarima_forecast'])
        psr_lstm = pick_columns(psr_lstm_df, ['PSR_ttm_lstm_forecast'])
        psr_prophet = pick_columns(psr_prophet_df, ['PSR_prophet_forecast_noexog'])
        psr_es = pick_columns(psr_es_df, ['PSR_es_forecast'])
        psr_forecast_df = pd.concat([psr_sarima, psr_lstm, psr_prophet, psr_es], axis=1, join='outer')

        valuation_result = calculate_valuation(revenue_forecast_ttm, psr_forecast_df, ticker)

        # ★★★ batch_results 형태 파악을 위한 디버그 프린트 ★★★
        print("\n" + "=" * 80)
        print(f"[DEBUG] valuation_result 형태 분석 (ticker: {ticker})")
        print("=" * 80)
        print(f"Type: {type(valuation_result)}")
        print(f"Shape: {valuation_result.shape if hasattr(valuation_result, 'shape') else 'N/A'}")
        print(f"\nColumns:\n{valuation_result.columns.tolist() if hasattr(valuation_result, 'columns') else 'N/A'}")
        print(f"\nFirst 3 rows:\n{valuation_result.head(3)}")
        print(f"\nData types:\n{valuation_result.dtypes if hasattr(valuation_result, 'dtypes') else 'N/A'}")
        print(f"\nSample data (last row):\n{valuation_result.tail(1)}")
        print("=" * 80 + "\n")

        batch_results.append(valuation_result)
        log("OK-VAL-PACK", f"{ticker} packed={len(valuation_result)} batch={len(batch_results)}")

    except Exception as e:
        log("EXC-VAL-PACK", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'valuation_pack', 'error': str(e)})
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Run valuation pipeline in batches")
    parser.add_argument("--start", type=int, default=0, help="시작 인덱스(포함)")
    parser.add_argument("--end", type=int, default=None, help="끝 인덱스(미포함)")
    parser.add_argument("--batch-size", type=int, default=20, help="배치 크기 (기본 20)")
    args = parser.parse_args()

    # DB 설정
    db_config['host'] = get_db_host()
    db_info = db_config

    end_date_month = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')

    # Ticker 리스트 가져오기
    from DATA.us_target_ticker_list import ticker_list

    # ★★★ 테스트용: AAPL만 실행 ★★★
    target_tickers = ['AAPL']

    # 전체 실행할 때는 아래 주석 해제하고 위 라인 주석 처리
    # end_idx = args.end if args.end is not None else len(ticker_list)
    # target_tickers = ticker_list[args.start:end_idx]

    # 전역 변수
    error_ticker_list = []
    total_success_tickers = 0
    total_upsert_rows = 0
    total_revenue_rows = 0

    batch_results = []
    batch_revenue_results = []

    # DB 커버리지 감사
    miss_q, miss_m = audit_db_coverage(db_info, target_tickers)

    if miss_m:
        fname = f"market_cap_missing_ticker_{pd.Timestamp.today().strftime('%Y%m%d')}.csv"
        pd.DataFrame({"ticker": miss_m}).to_csv(fname, index=False, encoding="utf-8-sig")
        log("AUDIT-SAVE", f"US_fundm missing {len(miss_m)} tickers saved -> {fname}")
    else:
        log("AUDIT-SAVE", "US_fundm missing tickers: 0")

    # Ticker별 처리
    for idx, ticker in enumerate(target_tickers, 1):
        success = process_ticker(
            ticker, idx, len(target_tickers), api_key, db_info, start_date_month, end_date_month,
            batch_results, batch_revenue_results, error_ticker_list
        )

        if success:
            total_success_tickers += 1

        # 배치 플러시
        try:
            is_last = (idx == len(target_tickers))
            if (len(batch_results) >= args.batch_size) or is_last:
                # ★★★ batch_results 전체 형태 파악 ★★★
                print("\n" + "=" * 80)
                print(f"[DEBUG] batch_results 전체 분석 (flush 직전)")
                print("=" * 80)
                print(f"batch_results 타입: {type(batch_results)}")
                print(f"batch_results 길이: {len(batch_results)}")

                if batch_results:
                    for i, result in enumerate(batch_results):
                        print(f"\n--- batch_results[{i}] ---")
                        print(f"  Type: {type(result)}")
                        if hasattr(result, 'shape'):
                            print(f"  Shape: {result.shape}")
                        if hasattr(result, 'columns'):
                            print(f"  Columns: {result.columns.tolist()}")
                        if hasattr(result, 'head'):
                            print(f"  First row:\n{result.head(1)}")
                        if hasattr(result, 'dtypes'):
                            print(f"  Data types:\n{result.dtypes}")

                print("\n" + "=" * 80 + "\n")

                log("BATCH-FLUSH",
                    f"valuation={len(batch_results)}, revenue={len(batch_revenue_results)}, is_last={is_last}")

                # ★★★ batch_results를 Long format으로 변환하여 DB 저장 ★★★
                if batch_results:
                    psr_rows = save_batch_results_to_db(
                        batch_results,
                        db_info,
                        forecast_date=pd.Timestamp.today().strftime('%Y-%m-%d'),
                        table_name="us_psr_valuation_result"
                    )
                    log("PSR-VALUATION-SAVE", f"Saved {psr_rows} rows to us_psr_valuation_result")

                total_upsert_rows, total_revenue_rows = flush_batch_and_upload(
                    batch_results, batch_revenue_results, db_info, total_upsert_rows, total_revenue_rows
                )
        except Exception as e:
            log("EXC-BATCH-FLUSH", f"{ticker} e={e}")
            error_ticker_list.append({'ticker': ticker, 'stage': 'batch_flush', 'error': str(e)})

        # 메모리 정리
        gc.collect()

    # 결과 출력
    print(f"[DONE] 성공 ticker: {total_success_tickers}")
    print(f"[DONE] Valuation 업서트 rows: {total_upsert_rows}")
    print(f"[DONE] Revenue forecast 업서트 rows: {total_revenue_rows}")

    if error_ticker_list:
        try:
            pd.DataFrame(error_ticker_list).to_csv("valuation_error_list.csv", index=False, encoding="utf-8-sig")
            print(f"[INFO] 오류 리스트 저장: valuation_error_list.csv (총 {len(error_ticker_list)}개)")
        except Exception:
            print(f"[WARN] 오류 리스트 저장 실패 (총 {len(error_ticker_list)}개)")
    else:
        print("[INFO] 오류 없이 완료")


if __name__ == "__main__":
    main()