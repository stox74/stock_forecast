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
from valuation import calculate_valuation
from db_batch_save import save_batch_results_to_db

# 새로운 모듈 임포트
from get_fmp_revenue import get_fmp_revenue
from get_fmp_marketcap_processor import get_fmp_market_cap
from get_marketcap_fundamental_dataframe import get_marketcap_fundamental_dataframe

warnings.filterwarnings('ignore')


def save_revenue_forecast_to_db(batch_revenue_results, db_info, forecast_date, table_name="us_revenue_forecast_result"):
    """
    Revenue forecast 데이터를 Long format으로 변환하여 DB에 저장

    Args:
        batch_revenue_results: List of revenue forecast DataFrames
        db_info: Database connection information
        forecast_date: Date when forecast was made (YYYY-MM-DD format)
        table_name: Target table name

    Returns:
        Number of rows upserted
    """
    if not batch_revenue_results:
        log("WARN-REV-SAVE", "No revenue forecast data to save")
        return 0

    try:
        from sqlalchemy import create_engine, text

        # DB 연결
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
        )

        # 배치의 모든 DataFrame을 Long format으로 변환
        long_format_list = []

        for df in batch_revenue_results:
            if df is None or df.empty:
                continue

            # DataFrame 복사
            df_copy = df.copy()

            # date_month_end가 index인 경우 컬럼으로 변환
            if 'date_month_end' not in df_copy.columns:
                df_copy = df_copy.reset_index()

            # ticker 컬럼 확인
            if 'ticker' not in df_copy.columns:
                log("WARN-REV-SAVE", "ticker column missing in revenue forecast data")
                continue

            ticker = df_copy['ticker'].iloc[0] if not df_copy['ticker'].isna().all() else None
            if ticker is None:
                continue

            # 예측 컬럼만 선택 (revenue_billions로 시작하는 컬럼들)
            forecast_cols = [col for col in df_copy.columns if col.startswith('revenue_billions_')]

            if not forecast_cols:
                log("WARN-REV-SAVE", f"{ticker} No forecast columns found")
                continue

            # Long format으로 변환
            for col in forecast_cols:
                for idx, row in df_copy.iterrows():
                    date_val = row['date_month_end']
                    value = row[col]

                    # NaN 값은 건너뛰기
                    if pd.isna(value):
                        continue

                    long_format_list.append({
                        'date': date_val,
                        'ticker': ticker,
                        'indicator': col,
                        'value': float(value),
                        'forecast_date': forecast_date
                    })

        if not long_format_list:
            log("WARN-REV-SAVE", "No valid revenue forecast data after conversion")
            return 0

        # Long format DataFrame 생성
        long_df = pd.DataFrame(long_format_list)

        # 날짜 형식 통일
        long_df['date'] = pd.to_datetime(long_df['date']).dt.strftime('%Y-%m-%d')
        long_df['forecast_date'] = pd.to_datetime(long_df['forecast_date']).dt.strftime('%Y-%m-%d')

        log("REV-LONG-FORMAT", f"Converted to long format: {len(long_df)} rows")

        # 테이블 생성 (없는 경우)
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            date DATE NOT NULL,
            ticker VARCHAR(20) NOT NULL,
            indicator VARCHAR(100) NOT NULL,
            value DOUBLE,
            forecast_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_forecast (ticker, date, indicator, forecast_date),
            KEY idx_ticker (ticker),
            KEY idx_date (date),
            KEY idx_forecast_date (forecast_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """

        with engine.connect() as conn:
            conn.execute(text(create_table_sql))
            conn.commit()

        log("REV-TABLE-CHECK", f"Table {table_name} ready")

        # UPSERT 수행 (ON DUPLICATE KEY UPDATE)
        # forecast_date가 같으면 UPDATE, 다르면 INSERT
        upsert_sql = f"""
        INSERT INTO {table_name} (date, ticker, indicator, value, forecast_date)
        VALUES (:date, :ticker, :indicator, :value, :forecast_date)
        ON DUPLICATE KEY UPDATE
            value = VALUES(value),
            updated_at = CURRENT_TIMESTAMP
        """

        with engine.connect() as conn:
            for _, row in long_df.iterrows():
                conn.execute(
                    text(upsert_sql),
                    {
                        'date': row['date'],
                        'ticker': row['ticker'],
                        'indicator': row['indicator'],
                        'value': row['value'],
                        'forecast_date': row['forecast_date']
                    }
                )
            conn.commit()

        log("REV-DB-SAVE", f"Saved {len(long_df)} rows to {table_name}")

        return len(long_df)

    except Exception as e:
        log("EXC-REV-SAVE", f"Failed to save revenue forecast: {e}")
        traceback.print_exc()
        return 0


def process_ticker(ticker, idx, total_tickers, api_key, db_info, start_date_month, end_date_month,
                   batch_results, batch_revenue_results, error_ticker_list):
    """개별 ticker 처리"""
    log("TICKER", f"{idx}/{total_tickers} {ticker}")

    # 1. FMP에서 매출 데이터 가져오기
    rev_data, error = get_fmp_revenue(
        ticker=ticker,
        api_key=api_key,
        fetch_revenue_data_func=fetch_revenue_data,
        fetch_db_revenue_func=fetch_db_revenue_data,
        db_info=db_info,
        start_date='2015-01-01',
        log_func=log,
        error_list=error_ticker_list
    )

    if rev_data is None:
        log("ERR-REV", f"{ticker} Failed to get revenue data")
        return False

    # 2. 매출 예측
    sarima_df, lstm_df, prophet_raw_df, es_raw_df, error = run_revenue_forecasts(
        rev_data, ticker, sarima, lstm_v2, prophet_v3, esmod
    )
    if error:
        error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_forecast', 'error': error})
        return False

    # 3. 시가총액 데이터 가져오기
    merged_market_df, error = get_fmp_market_cap(
        ticker=ticker,
        api_key=api_key,
        fetch_market_data_func=fetch_market_data_yearly,
        process_daily_to_monthly_func=process_daily_to_monthly_market_data,
        fetch_db_market_func=safe_get_db_market_df,
        db_info=db_info,
        start_year=2010,
        log_func=log,
        error_list=error_ticker_list
    )

    if merged_market_df is None:
        log("ERR-MCAP", f"{ticker} Failed to get market cap data")
        return False

    # 4. 시가총액과 매출 데이터 병합
    market_cap_resize = get_marketcap_fundamental_dataframe(
        merged_market_df=merged_market_df,
        rev_data=rev_data,
        start_date=start_date_month,
        end_date=end_date_month,
        log_func=log
    )

    if market_cap_resize is None:
        log("ERR-MERGE", f"{ticker} Failed to merge data")
        return False

    # 5. TTM 및 PSR 계산
    try:
        enhanced_merged_df_with_ttm = calculate_enhanced_ttm_and_psr(market_cap_resize)
        psr_ok = enhanced_merged_df_with_ttm[['date_month_end', 'PSR_ttm']].dropna()

        if psr_ok.empty:
            log("WARN-PSR", f"{ticker} No valid PSR data")
            return False
    except Exception as e:
        log("EXC-TTM-PSR", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'ttm_psr_calc', 'error': str(e)})
        return False

    # 6. PSR 예측
    psr_sarima_df, psr_lstm_df, psr_prophet_df, psr_es_df, error = run_psr_forecasts(
        enhanced_merged_df_with_ttm, ticker, sarima, lstm_v2, prophet_v3, esmod
    )
    if error:
        error_ticker_list.append({'ticker': ticker, 'stage': 'psr_forecast', 'error': error})
        return False

    # 7. Revenue forecast DataFrame 준비
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
    except Exception as e:
        log("EXC-REV-FORECAST", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_forecast_prep', 'error': str(e)})
        return False

    # 8. Revenue TTM 계산
    try:
        revenue_forecast_df_reset = revenue_forecast_df.reset_index()
        if 'ticker' not in revenue_forecast_df_reset.columns:
            revenue_forecast_df_reset['ticker'] = ticker

        revenue_forecast_ = prepare_revenue_ttm(revenue_forecast_df_reset)
        revenue_forecast_ttm = revenue_forecast_.filter(like='_ttm').copy()

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
    except Exception as e:
        log("EXC-REV-TTM", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'revenue_ttm_calc', 'error': str(e)})
        return False

    # 9. PSR forecast DataFrame 준비
    try:
        psr_sarima = pick_columns(psr_sarima_df, ['PSR_ttm_sarima_forecast'])
        psr_lstm = pick_columns(psr_lstm_df, ['PSR_ttm_lstm_forecast'])
        psr_prophet = pick_columns(psr_prophet_df, ['PSR_prophet_forecast_noexog'])
        psr_es = pick_columns(psr_es_df, ['PSR_es_forecast'])
        psr_forecast_df = pd.concat([psr_sarima, psr_lstm, psr_prophet, psr_es], axis=1, join='outer')
    except Exception as e:
        log("EXC-PSR-FORECAST", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'psr_forecast_prep', 'error': str(e)})
        return False

    # 10. Valuation 계산
    try:
        valuation_result = calculate_valuation(revenue_forecast_ttm, psr_forecast_df, ticker)
        batch_results.append(valuation_result)
        log("OK-VALUATION", f"{ticker} valuation calculated and added to batch")
    except Exception as e:
        log("EXC-VALUATION", f"{ticker} e={e}")
        error_ticker_list.append({'ticker': ticker, 'stage': 'valuation_calc', 'error': str(e)})
        return False

    return True


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description='US PSR Valuation Run')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='Batch size for processing')
    parser.add_argument('--ticker-file', type=str, help='Path to ticker list file (CSV)')
    parser.add_argument('--ticker-range', type=str, help='Ticker range, e.g., 0:100')
    args = parser.parse_args()

    # DB 설정
    db_config['host'] = get_db_host()
    db_info = db_config

    log("DB-CONFIG", f"Host={db_info['host']}, Port={db_info['port']}, DB={db_info['database']}")

    # 날짜 설정
    end_date_month = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
    forecast_date = pd.Timestamp.today().strftime('%Y-%m-%d')

    log("DATE-CONFIG", f"Start={start_date_month}, End={end_date_month}, Forecast={forecast_date}")

    # Ticker 리스트 설정
    if args.ticker_file:
        try:
            ticker_df = pd.read_csv(args.ticker_file)
            target_tickers = ticker_df['ticker'].tolist() if 'ticker' in ticker_df.columns else ticker_df.iloc[:,
                                                                                                0].tolist()
            log("TICKER-LOAD", f"Loaded {len(target_tickers)} tickers from {args.ticker_file}")
        except Exception as e:
            log("ERR-TICKER-LOAD", f"Failed to load ticker file: {e}")
            return
    else:
        from DATA.us_target_ticker_list import ticker_list

        if args.ticker_range:
            try:
                parts = args.ticker_range.split(':')
                start_idx = int(parts[0])

                # 끝 인덱스가 없거나 빈 문자열이면 끝까지
                if len(parts) == 1 or (len(parts) == 2 and parts[1].strip() == ''):
                    target_tickers = ticker_list[start_idx:]
                    log("TICKER-RANGE", f"Using ticker range {start_idx}:end, count={len(target_tickers)}")
                else:
                    end_idx = int(parts[1])
                    target_tickers = ticker_list[start_idx:end_idx]
                    log("TICKER-RANGE", f"Using ticker range {start_idx}:{end_idx}, count={len(target_tickers)}")
            except Exception as e:
                log("ERR-TICKER-RANGE", f"Invalid ticker range: {e}")
                return
        else:
            target_tickers = ticker_list
            log("TICKER-ALL", f"Using all tickers, count={len(target_tickers)}")

    # 전역 변수 초기화
    error_ticker_list = []
    total_success_tickers = 0
    total_upsert_rows = 0
    total_revenue_rows = 0

    batch_results = []
    batch_revenue_results = []

    log("INIT", "Variables initialized")

    # DB에서 데이터 존재 여부 확인
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
                log("BATCH-FLUSH",
                    f"valuation={len(batch_results)}, revenue={len(batch_revenue_results)}, is_last={is_last}")

                # 1. batch_results를 Long format으로 변환하여 DB 저장
                if batch_results:
                    psr_rows = save_batch_results_to_db(
                        batch_results,
                        db_info,
                        forecast_date=forecast_date,
                        table_name="us_psr_valuation_result"
                    )
                    total_upsert_rows += psr_rows
                    log("PSR-VALUATION-SAVE", f"Saved {psr_rows} rows to us_psr_valuation_result")
                    batch_results.clear()

                # 2. revenue_forecast_df를 Long format으로 변환하여 DB 저장
                if batch_revenue_results:
                    rev_rows = save_revenue_forecast_to_db(
                        batch_revenue_results,
                        db_info,
                        forecast_date=forecast_date,
                        table_name="us_revenue_forecast_result"
                    )
                    total_revenue_rows += rev_rows
                    log("REV-FORECAST-SAVE", f"Saved {rev_rows} rows to us_revenue_forecast_result")
                    batch_revenue_results.clear()

        except Exception as e:
            log("EXC-BATCH-FLUSH", f"{ticker} e={e}")
            traceback.print_exc()
            error_ticker_list.append({'ticker': ticker, 'stage': 'batch_flush', 'error': str(e)})

        # 메모리 정리
        gc.collect()

    # 결과 출력
    print("\n" + "=" * 80)
    print("처리 완료")
    print("=" * 80)
    print(f"성공 ticker: {total_success_tickers}")
    print(f"PSR Valuation 업서트 rows: {total_upsert_rows}")
    print(f"Revenue forecast 업서트 rows: {total_revenue_rows}")
    print(f"오류 ticker: {len(error_ticker_list)}")

    # 오류 리스트 저장
    if error_ticker_list:
        try:
            error_df = pd.DataFrame(error_ticker_list)
            error_df.to_csv("valuation_error_list.csv", index=False, encoding="utf-8-sig")
            print(f"\n오류 리스트 저장: valuation_error_list.csv (총 {len(error_ticker_list)}개)")
            print("\n오류 발생 단계별 통계:")
            print(error_df['stage'].value_counts())
        except Exception as e:
            print(f"오류 리스트 저장 실패: {e}")
    else:
        print("\n처리 완료: 오류 없음")


if __name__ == "__main__":
    main()