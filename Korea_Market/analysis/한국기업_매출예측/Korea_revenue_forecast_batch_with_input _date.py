"""
한국 전체 기업 매출 시계열 예측 배치 처리 스크립트 (개선 버전)

이 스크립트는 한국 상장 기업들의 재무 데이터를 바탕으로
SARIMA, ETS, Theta 모델을 사용하여 매출을 예측합니다.

[주요 수정 사항]
- 분기별 예측이 일별로 잘못 되는 문제 해결
- PeriodIndex를 사용하여 정확한 분기 예측 구현
- 예측 입력 날짜(input_date) 추적 기능 추가
- ticker 범위 조정 기능 추가 (시작~끝 인덱스 지정)
"""

import numpy as np
import pandas as pd
import pymysql
import sys
import os
import socket
import warnings
import gc
from pathlib import Path
from typing import Union, Optional
from tqdm import tqdm
from datetime import datetime

warnings.filterwarnings('ignore')


# ==================== 1. 경로 설정 ====================

def setup_universal_paths():
    """
    어떤 PC에서도 작동하는 범용 경로 설정
    DATA 폴더를 자동으로 찾아 경로 추가
    """
    current = Path.cwd()

    # 상위 폴더를 탐색하며 DATA 폴더 찾기
    for parent in [current, *current.parents]:
        data_folder = parent / "DATA"
        if data_folder.exists():
            # 프로젝트 루트와 DATA 폴더 모두 추가
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            if str(data_folder) not in sys.path:
                sys.path.insert(0, str(data_folder))

            print("=" * 70)
            print("📁 경로 설정 완료")
            print("=" * 70)
            print(f"✓ 프로젝트 루트: {parent}")
            print(f"✓ DATA 폴더:    {data_folder}")
            print(f"✓ 현재 위치:     {current}")
            print(f"✓ 운영체제:      {os.name}")
            print("=" * 70 + "\n")

            return {
                'project_root': parent,
                'data_folder': data_folder,
                'current': current
            }

    # 못 찾으면 경고
    print("⚠️ DATA 폴더를 찾을 수 없습니다. 현재 경로에서 실행합니다.")
    return {
        'project_root': current,
        'data_folder': current,
        'current': current
    }


# 경로 설정 실행
paths = setup_universal_paths()

# ==================== 2. 필요한 모듈 import ====================

try:
    # 예측 함수 import
    from universal_ts_forecast_function import (
        ensure_datetime_index_df,
        forecast_sarima,
        forecast_ets,
        forecast_theta,
        infer_freq_alias,
        seasonal_periods_from_freq
    )

    print("✅ 예측 함수 import 성공")
except ImportError as e:
    print(f"❌ 예측 함수 import 실패: {e}")
    print("universal_ts_forecast_function.py 파일을 확인하세요.")
    sys.exit(1)

try:
    # stock_invest_function에서 DB 관련 함수 import
    from DATA.stock_invest_function import fetch_table_data, get_db_host

    print("✅ DB 함수 import 성공")
except ImportError:
    try:
        # DATA 없이 시도
        from stock_invest_function import fetch_table_data, get_db_host

        print("✅ DB 함수 import 성공")
    except ImportError as e:
        print(f"⚠️ stock_invest_function import 실패: {e}")
        print("필요한 함수를 직접 정의합니다.")


        def fetch_table_data(db_info, table_name):
            """간단한 테이블 조회 함수"""
            conn = pymysql.connect(
                host=db_info["host"],
                port=db_info["port"],
                user=db_info["user"],
                password=db_info["password"],
                db=db_info.get("db", db_info.get("database")),
                charset="utf8mb4"
            )
            try:
                return pd.read_sql(f"SELECT * FROM {table_name}", conn)
            finally:
                conn.close()


        def get_db_host():
            """현재 머신에 따라 DB 호스트 자동 선택"""
            hostname = socket.gethostname()
            if 'desktop' in hostname.lower():
                return '192.168.0.8'
            else:
                return 'localhost'


# ==================== 3. DB 관련 함수 ====================

def get_db_config():
    """DB 연결 정보 반환"""
    return {
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'host': get_db_host(),
        'port': 3307,
        'database': 'investar'
    }


# ==================== 4. ticker 관련 함수 ====================

def get_all_tickers(db_info: dict) -> list:
    """
    korea_fs_data_from_DART 테이블에서 unique ticker 목록 조회
    """
    database_name = db_info.get("db") or db_info.get("database")

    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=database_name,
        charset="utf8mb4",
    )

    try:
        sql = """
              SELECT DISTINCT ticker
              FROM korea_fs_data_from_DART
              ORDER BY ticker
              """
        df = pd.read_sql(sql, conn)
        tickers = df['ticker'].tolist()
        print(f"✅ unique ticker {len(tickers)}개 조회 완료")
        return tickers
    finally:
        conn.close()


# ==================== 5. 매출 데이터 조회 함수 ====================

def get_quarterly_revenue_simple(
        db_info: dict,
        ticker: Union[str, int],
        adjust_q4: bool = True
) -> pd.DataFrame:
    """
    DART에서 매출 데이터 조회 + Q4 조정
    """
    database_name = db_info.get("db") or db_info.get("database")

    # ticker 정규화
    if isinstance(ticker, int):
        ticker_str = f"{ticker:06d}"
    else:
        ticker_str = str(ticker).zfill(6)

    # DB 연결
    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=database_name,
        charset="utf8mb4",
    )

    try:
        # 매출 데이터 조회
        sql = """
              SELECT *
              FROM korea_fs_data_from_DART
              WHERE ticker = %s
                AND account_id IN ('ifrs_Revenue', 'ifrs-full_Revenue')
              ORDER BY bsns_year, report_date
              """

        df = pd.read_sql(sql, conn, params=[ticker_str])

        if df.empty:
            return df

        # Q4 조정
        if adjust_q4:
            df = adjust_fy_to_q4(df)

        return df
    finally:
        conn.close()


def adjust_fy_to_q4(df: pd.DataFrame) -> pd.DataFrame:
    """
    FY(연간 누적) 데이터를 순수 Q4로 변환
    Q4 = FY - (Q1 + Q2 + Q3)
    """
    result_df = df.copy()

    # 연도별 처리
    for year in result_df['bsns_year'].unique():
        year_mask = result_df['bsns_year'] == year

        # FY 행 찾기
        fy_mask = year_mask & (result_df['quarter'] == 'FY')

        if fy_mask.any():
            # FY 금액
            fy_amount = result_df.loc[fy_mask, 'thstrm_amount'].iloc[0]

            # Q1, Q2, Q3 금액 합계
            q123_mask = year_mask & result_df['quarter'].isin(['Q1', 'Q2', 'Q3'])
            q123_sum = result_df.loc[q123_mask, 'thstrm_amount'].sum()

            # 순수 Q4 계산
            pure_q4 = fy_amount - q123_sum

            # FY를 Q4로 변경하고 금액 조정
            result_df.loc[fy_mask, 'quarter'] = 'Q4'
            result_df.loc[fy_mask, 'thstrm_amount'] = pure_q4

    return result_df


# ==================== 6. 분기 데이터 준비 함수 (핵심 수정) ====================

def prepare_quarterly_series(revenue_df: pd.DataFrame, ticker: str) -> pd.Series:
    """
    매출 DataFrame을 분기별 PeriodIndex Series로 변환

    Parameters:
    -----------
    revenue_df : pd.DataFrame
        report_date와 thstrm_amount 컬럼을 가진 DataFrame
    ticker : str
        종목 코드 (로깅용)

    Returns:
    --------
    pd.Series
        PeriodIndex를 가진 분기별 매출 Series
    """
    df = revenue_df.copy()

    # datetime으로 변환
    df['report_date'] = pd.to_datetime(df['report_date'])

    # 분기 정보 추출
    df['year'] = df['report_date'].dt.year
    df['quarter'] = df['report_date'].dt.quarter

    # 분기 PeriodIndex 생성
    df['period'] = df['year'].astype(str) + 'Q' + df['quarter'].astype(str)
    df['period'] = pd.PeriodIndex(df['period'], freq='Q')

    # 중복된 분기가 있는지 확인 및 처리
    duplicates = df[df.duplicated(subset=['period'], keep=False)]
    if not duplicates.empty:
        # 각 분기의 마지막 값 사용
        df = df.sort_values(['period', 'report_date']).groupby('period').last().reset_index()

    # PeriodIndex를 인덱스로 설정
    df = df.set_index('period').sort_index()

    # 매출 Series 반환
    series = df['thstrm_amount'].astype(float)

    return series


# ==================== 7. DB 저장 함수 (input_date 추가) ====================

def save_to_db_batch(
        df: pd.DataFrame,
        db_info: dict,
        input_date: str,
        table_name: str = "korea_revenue_forecast_result",
        batch_size: int = 50
):
    """
    DataFrame을 DB에 배치로 저장 (input_date 컬럼 추가)

    Parameters:
    -----------
    df : pd.DataFrame
        저장할 데이터 (date, ticker, indicator, value 컬럼 필요)
    db_info : dict
        DB 연결 정보
    input_date : str
        예측 입력 날짜 (YYYY-MM-DD 형식)
    table_name : str
        테이블 이름
    batch_size : int
        배치 크기
    """
    if df.empty:
        print("⚠️ 저장할 데이터가 없습니다")
        return

    database_name = db_info.get("db") or db_info.get("database")

    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=database_name,
        charset="utf8mb4",
        autocommit=False
    )

    try:
        with conn.cursor() as cursor:
            # 테이블 생성 (input_date 컬럼 추가)
            create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                input_date DATE NOT NULL COMMENT '예측 입력 날짜',
                forecast_date DATE NOT NULL COMMENT '예측 대상 날짜',
                ticker VARCHAR(20) NOT NULL,
                indicator VARCHAR(50) NOT NULL,
                value DOUBLE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (input_date, forecast_date, ticker, indicator),
                INDEX idx_ticker (ticker),
                INDEX idx_forecast_date (forecast_date),
                INDEX idx_input_date (input_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 
            COMMENT='한국 기업 매출 예측 결과 (입력날짜 추적)'
            """
            cursor.execute(create_table_sql)

            # INSERT ... ON DUPLICATE KEY UPDATE 쿼리
            insert_sql = f"""
            INSERT INTO {table_name} 
                (input_date, forecast_date, ticker, indicator, value)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                created_at = CURRENT_TIMESTAMP
            """

            # 배치로 저장
            total_rows = len(df)
            for i in range(0, total_rows, batch_size):
                batch_df = df.iloc[i:i + batch_size]
                batch_data = [
                    (input_date, row['date'], row['ticker'], row['indicator'], row['value'])
                    for _, row in batch_df.iterrows()
                ]

                cursor.executemany(insert_sql, batch_data)
                conn.commit()

                if (i + len(batch_df)) % 500 == 0 or (i + len(batch_df)) == total_rows:
                    print(f"  💾 저장: {i + len(batch_df)}/{total_rows} 행")

            print(f"✅ DB 저장 완료: {total_rows}행 (input_date={input_date})")

    except Exception as e:
        conn.rollback()
        print(f"❌ DB 저장 실패: {e}")
        raise
    finally:
        conn.close()


def convert_to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wide format을 Long format으로 변환

    Wide format:
        date, ticker, SARIMA, ETS, Theta, Ensemble

    Long format:
        date, ticker, indicator, value
    """
    if df.empty:
        return df

    # date 컬럼이 문자열이면 datetime으로 변환
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])

    # 모델 컬럼들
    model_columns = ['SARIMA', 'ETS', 'Theta', 'Ensemble']

    # 존재하는 모델 컬럼만 선택
    existing_models = [col for col in model_columns if col in df.columns]

    if not existing_models:
        raise ValueError("변환할 모델 컬럼이 없습니다")

    # melt로 long format 변환
    id_vars = ['date', 'ticker']
    long_df = df.melt(
        id_vars=id_vars,
        value_vars=existing_models,
        var_name='indicator',
        value_name='value'
    )

    # 정렬
    long_df = long_df.sort_values(['ticker', 'date', 'indicator']).reset_index(drop=True)

    return long_df


# ==================== 8. 배치 예측 함수 (ticker 범위 지정 추가) ====================

def batch_forecast_all_tickers(
        db_info: dict,
        fs_df: pd.DataFrame,
        input_date: str,
        H: int = 9,
        ticker_start_idx: Optional[int] = None,
        ticker_end_idx: Optional[int] = None,
        ticker_list: Optional[list] = None,
        save_checkpoint: bool = True,
        checkpoint_interval: int = 50,
        save_to_db: bool = True,
        db_batch_size: int = 50
):
    """
    모든 ticker에 대해 배치로 매출 예측 수행 (분기별 예측 개선)

    Parameters:
    -----------
    db_info : dict
        DB 연결 정보
    fs_df : pd.DataFrame
        DataGuide 재무 데이터
    input_date : str
        예측 입력 날짜 (YYYY-MM-DD)
    H : int
        예측 horizon (분기 수)
    ticker_start_idx : int, optional
        처리할 ticker 시작 인덱스 (0-based)
    ticker_end_idx : int, optional
        처리할 ticker 종료 인덱스 (exclusive)
    ticker_list : list, optional
        직접 지정한 ticker 리스트 (이게 있으면 start/end_idx 무시)
    save_checkpoint : bool
        체크포인트 저장 여부
    checkpoint_interval : int
        체크포인트 저장 간격
    save_to_db : bool
        DB 저장 여부
    db_batch_size : int
        DB 배치 크기
    """

    # 1. ticker 목록 결정
    if ticker_list is not None:
        # 직접 지정한 리스트 사용
        all_tickers = ticker_list
        print(f"\n📝 사용자 지정 ticker 리스트: {len(all_tickers)}개")
    else:
        # DB에서 조회
        all_tickers = get_all_tickers(db_info)

        # 범위 지정
        if ticker_start_idx is not None or ticker_end_idx is not None:
            start = ticker_start_idx if ticker_start_idx is not None else 0
            end = ticker_end_idx if ticker_end_idx is not None else len(all_tickers)

            print(f"\n📊 전체 ticker: {len(all_tickers)}개")
            print(f"📌 처리 범위: 인덱스 {start} ~ {end - 1}")

            all_tickers = all_tickers[start:end]
            print(f"✅ 선택된 ticker: {len(all_tickers)}개")

    print(f"\n처리 대상: {len(all_tickers)}개 ticker")
    print(f"입력 날짜: {input_date}")

    # 2. 예측 결과 저장용 리스트
    all_results = []
    error_list = []
    success_count = 0

    # 3. 진행 상황 표시
    pbar = tqdm(all_tickers, desc="매출 예측 진행")

    for ticker in pbar:
        pbar.set_postfix_str(f"현재: {ticker}")

        try:
            # ---- DataGuide 데이터 ----
            ticker_dg = 'A' + ticker
            revenue_dg = fs_df[
                (fs_df['symbol'] == ticker_dg) &
                (fs_df['indicator'] == '매출액(천원)')
                ][['date', 'value']].copy()

            if not revenue_dg.empty:
                revenue_dg['value'] = revenue_dg['value'] * 1000
                revenue_dg.columns = ['date', 'revenue']

            # ---- DART 데이터 ----
            revenue_dart_df = get_quarterly_revenue_simple(db_info, ticker=ticker)

            if revenue_dart_df.empty:
                error_list.append((ticker, "DART 데이터 없음"))
                continue

            revenue_dart = revenue_dart_df[['report_date', 'thstrm_amount']].copy()
            revenue_dart.columns = ['date', 'revenue']

            # ---- 데이터 결합 ----
            if not revenue_dg.empty:
                revenue_combined = pd.concat([revenue_dg, revenue_dart], axis=0) \
                    .drop_duplicates(subset=['date'], keep='first') \
                    .sort_values('date') \
                    .reset_index(drop=True)
            else:
                revenue_combined = revenue_dart

            # ---- 분기 Series로 변환 (핵심 수정) ----
            series = prepare_quarterly_series(revenue_combined, ticker)

            # 최소 데이터 길이 검사
            n = len(series)
            if n < 16:  # 분기 데이터는 최소 4년(16분기) 권장
                error_list.append((ticker, f"데이터 부족: {n}개 분기"))
                continue

            # ---- 계절성 파라미터 ----
            m = 4  # 분기 데이터의 계절성은 항상 4

            # ---- 모델별 예측 ----
            sarima_result = forecast_sarima(
                y=series,
                forecast_horizon=H,
                seasonal_period=m,
                try_transforms=True
            )

            ets_result = forecast_ets(
                y=series,
                forecast_horizon=H,
                m=m,
                try_transforms=True
            )

            theta_result = forecast_theta(
                y=series,
                forecast_horizon=H,
                m=m,
                try_transforms=True
            )

            # ---- 결과 DataFrame 생성 (PeriodIndex 사용) ----
            last_period = series.index[-1]

            # 미래 분기 생성
            forecast_periods = pd.period_range(
                start=last_period + 1,
                periods=H,
                freq='Q'
            )

            result_df = pd.DataFrame({
                "SARIMA": sarima_result.get("forecast"),
                "ETS": ets_result.get("forecast"),
                "Theta": theta_result.get("forecast"),
            }, index=forecast_periods)

            # 앙상블
            result_df["Ensemble"] = result_df[["SARIMA", "ETS", "Theta"]].mean(axis=1)
            result_df['ticker'] = ticker

            # DatetimeIndex로 변환 (DB 저장용)
            result_df['date'] = result_df.index.to_timestamp()
            result_df.reset_index(drop=True, inplace=True)

            # 결과 저장
            all_results.append(result_df)
            success_count += 1

            # 체크포인트 저장
            if save_checkpoint and success_count % checkpoint_interval == 0:
                checkpoint_df = pd.concat(all_results, axis=0, ignore_index=True)
                checkpoint_file = f'revenue_forecast_checkpoint_{input_date}.csv'
                checkpoint_df.to_csv(
                    checkpoint_file,
                    index=False,
                    encoding='utf-8-sig'
                )
                tqdm.write(f"💾 체크포인트 저장: {success_count}개 완료 ({checkpoint_file})")

                # DB에도 저장
                if save_to_db:
                    try:
                        # long format으로 변환
                        long_checkpoint_df = convert_to_long_format(checkpoint_df)
                        # DB에 저장 (input_date 포함)
                        save_to_db_batch(
                            long_checkpoint_df,
                            db_info,
                            input_date=input_date,
                            batch_size=db_batch_size
                        )
                        tqdm.write(f"💾 DB 저장 완료: {len(long_checkpoint_df)}행")

                        # 메모리 정리
                        del long_checkpoint_df

                    except Exception as db_error:
                        tqdm.write(f"⚠️ DB 저장 실패: {db_error}")

                # 체크포인트 저장 후 all_results 메모리 정리
                tqdm.write(f"🧹 메모리 정리 중... (저장된 {success_count}개 데이터)")

                # all_results 리스트 비우기 (DB와 CSV에 이미 저장됨)
                all_results.clear()

                # 가비지 컬렉션 강제 실행
                gc.collect()

                tqdm.write(f"✅ 메모리 정리 완료")

        except Exception as e:
            # 에러 발생 시 리스트에 추가하고 계속 진행
            error_list.append((ticker, str(e)))
            continue

    # ---- 최종 결과 결합 ----
    checkpoint_file = f'revenue_forecast_checkpoint_{input_date}.csv'

    if save_checkpoint and os.path.exists(checkpoint_file):
        print(f"\n📂 체크포인트 파일에서 최종 데이터 로딩 중... ({checkpoint_file})")
        final_df = pd.read_csv(checkpoint_file)

        # all_results에 남은 데이터가 있으면 추가
        if all_results:
            remaining_df = pd.concat(all_results, axis=0, ignore_index=True)
            final_df = pd.concat([final_df, remaining_df], axis=0, ignore_index=True)
            final_df = final_df.drop_duplicates(subset=['date', 'ticker'], keep='last').reset_index(drop=True)

        print(f"✅ 완료: {success_count}개 ticker 예측 성공")
        print(f"📊 총 {len(final_df)}행 생성")

    elif all_results:
        final_df = pd.concat(all_results, axis=0, ignore_index=True)
        print(f"\n✅ 완료: {success_count}개 ticker 예측 성공")
        print(f"📊 총 {len(final_df)}행 생성")
    else:
        final_df = pd.DataFrame()
        print(f"\n⚠️ 예측 성공한 ticker가 없습니다")

    # 메모리 정리
    if all_results:
        all_results.clear()
        gc.collect()

    # ---- 최종 DB 저장 ----
    if not final_df.empty and save_to_db:
        try:
            print("\n" + "=" * 70)
            print("📊 최종 결과를 DB에 저장 중...")
            print("=" * 70)

            # long format으로 변환
            long_final_df = convert_to_long_format(final_df)
            print(f"✅ Long format 변환 완료: {len(long_final_df)}행")
            print(f"   - 원본(Wide): {len(final_df)}행")
            print(f"   - 변환(Long): {len(long_final_df)}행")
            print(f"   - 컬럼: {long_final_df.columns.tolist()}")

            # DB에 저장 (input_date 포함)
            save_to_db_batch(
                long_final_df,
                db_info,
                input_date=input_date,
                table_name="korea_revenue_forecast_result",
                batch_size=db_batch_size
            )

            # 메모리 정리
            del long_final_df
            gc.collect()

        except Exception as db_error:
            print(f"⚠️ 최종 DB 저장 실패: {db_error}")
            print("CSV 파일은 정상적으로 저장됩니다.")

    # ---- 에러 요약 ----
    if error_list:
        print(f"\n❌ 에러 발생: {len(error_list)}개 ticker")

        # 에러 타입별 집계
        error_types = {}
        for ticker, error in error_list:
            error_key = error.split(':')[0] if ':' in error else error
            error_types[error_key] = error_types.get(error_key, 0) + 1

        print("\n에러 타입별 요약:")
        for error_type, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"  - {error_type}: {count}개")

    return final_df, error_list


# ==================== 9. 메인 실행 함수 ====================

def main():
    """메인 실행 함수"""

    print("=" * 80)
    print("한국 전체 기업 매출 시계열 예측 (입력날짜 추적 버전)")
    print("=" * 80)
    print()

    # 1. DB 연결 정보
    db_info = get_db_config()
    print(f"✅ DB 연결 정보 설정 완료 (host: {db_info['host']})")

    # 2. 입력 날짜 설정
    default_input_date = datetime.now().strftime('%Y-%m-%d')
    print(f"\n📅 예측 입력 날짜 설정")
    print(f"   기본값: {default_input_date} (오늘)")
    input_date_input = input(f"   입력 날짜 (YYYY-MM-DD, Enter=기본값): ").strip()

    if input_date_input:
        try:
            # 날짜 형식 검증
            datetime.strptime(input_date_input, '%Y-%m-%d')
            input_date = input_date_input
        except ValueError:
            print(f"⚠️ 잘못된 날짜 형식입니다. 기본값 사용: {default_input_date}")
            input_date = default_input_date
    else:
        input_date = default_input_date

    print(f"✅ 입력 날짜: {input_date}")

    # 3. DataGuide 데이터 로드
    print("\n📊 DataGuide 데이터 로딩 중...")
    try:
        fs_df = fetch_table_data(db_info, "korea_fs_data")
        print(f"✅ DataGuide 데이터 로드 완료: {len(fs_df):,}행")
    except Exception as e:
        print(f"❌ DataGuide 데이터 로드 실패: {e}")
        print("DART 데이터만으로 진행합니다.")
        fs_df = pd.DataFrame()

    # 4. 실행 모드 선택
    print("\n" + "=" * 80)
    print("실행 모드 선택")
    print("=" * 80)
    print("1. 테스트 모드 (처음 10개 ticker)")
    print("2. 범위 지정 (시작~끝 인덱스)")
    print("3. 전체 실행 (모든 ticker)")

    choice = input("\n선택 (1, 2 또는 3, 기본값=1): ").strip() or "1"

    ticker_start_idx = None
    ticker_end_idx = None
    ticker_list = None

    if choice == "1":
        # 테스트 모드
        ticker_start_idx = 0
        ticker_end_idx = 10
        print(f"\n🧪 테스트 모드 선택: 처음 10개 ticker 처리")

    elif choice == "2":
        # 범위 지정
        print("\n📌 ticker 범위 지정")

        # 전체 ticker 수 확인
        all_tickers_temp = get_all_tickers(db_info)
        total_count = len(all_tickers_temp)
        print(f"   전체 ticker 수: {total_count}개")

        start_input = input(f"   시작 인덱스 (0 ~ {total_count - 1}, 기본값=0): ").strip()
        end_input = input(f"   종료 인덱스 (1 ~ {total_count}, 기본값={total_count}): ").strip()

        try:
            ticker_start_idx = int(start_input) if start_input else 0
            ticker_end_idx = int(end_input) if end_input else total_count

            # 범위 검증
            if ticker_start_idx < 0 or ticker_start_idx >= total_count:
                print(f"⚠️ 시작 인덱스가 범위를 벗어났습니다. 0으로 설정합니다.")
                ticker_start_idx = 0

            if ticker_end_idx <= ticker_start_idx or ticker_end_idx > total_count:
                print(f"⚠️ 종료 인덱스가 잘못되었습니다. {total_count}으로 설정합니다.")
                ticker_end_idx = total_count

            print(f"\n✅ 처리 범위: {ticker_start_idx} ~ {ticker_end_idx - 1} ({ticker_end_idx - ticker_start_idx}개)")

        except ValueError:
            print("⚠️ 잘못된 입력입니다. 전체 실행으로 진행합니다.")
            ticker_start_idx = None
            ticker_end_idx = None

    else:
        # 전체 실행
        print("\n🚀 전체 실행 모드 선택: 모든 ticker 처리")

    # 5. 배치 예측 실행
    print("\n" + "=" * 80)
    print("예측 시작")
    print("=" * 80)
    print()

    result_df, error_list = batch_forecast_all_tickers(
        db_info=db_info,
        fs_df=fs_df,
        input_date=input_date,
        H=9,
        ticker_start_idx=ticker_start_idx,
        ticker_end_idx=ticker_end_idx,
        ticker_list=ticker_list,
        save_checkpoint=True,
        checkpoint_interval=50,
        save_to_db=True,
        db_batch_size=50
    )

    # 6. 결과 저장
    if not result_df.empty:
        output_file = f'revenue_forecast_{input_date}.csv'
        result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n✅ 최종 결과 저장: {output_file}")
        print(f"   - 총 {len(result_df)}행")
        print(f"   - ticker 개수: {result_df['ticker'].nunique()}개")

        # 결과 샘플 출력
        print("\n📊 결과 샘플 (처음 5행):")
        print(result_df.head())

        # 예측 기간 확인
        print("\n📅 예측 기간:")
        print(f"   - 시작: {result_df['date'].min()}")
        print(f"   - 종료: {result_df['date'].max()}")

    # 7. 에러 로그 저장
    if error_list:
        error_file = f'revenue_forecast_errors_{input_date}.csv'
        error_df = pd.DataFrame(error_list, columns=['ticker', 'error'])
        error_df.to_csv(error_file, index=False, encoding='utf-8-sig')
        print(f"\n❌ 에러 로그 저장: {error_file}")
        print(f"   - 총 {len(error_list)}개 ticker 실패")

    print("\n" + "=" * 80)
    print("✅ 모든 작업 완료!")
    print("=" * 80)


# ==================== 10. 스크립트 실행 ====================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n\n❌ 예상치 못한 오류 발생: {e}")
        import traceback

        traceback.print_exc()