"""
한국 전체 기업 매출 시계열 예측 배치 처리 스크립트

이 스크립트는 한국 상장 기업들의 재무 데이터를 바탕으로
SARIMA, ETS, Theta 모델을 사용하여 매출을 예측합니다.
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
from typing import Union
from tqdm import tqdm

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
              ORDER BY ticker \
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
              ORDER BY bsns_year, report_date \
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


# ==================== 6. DB 저장 함수 ====================

def save_to_db_batch(
        df: pd.DataFrame,
        db_info: dict,
        table_name: str = "korea_revenue_forecast_result",
        batch_size: int = 50
):
    """
    DataFrame을 DB에 배치로 저장
    중복 데이터는 ON DUPLICATE KEY UPDATE로 처리

    Parameters:
    -----------
    df : pd.DataFrame
        저장할 데이터 (date, ticker, indicator, value 컬럼 필요)
    db_info : dict
        DB 연결 정보
    table_name : str
        테이블 이름
    batch_size : int
        배치 크기 (기본값: 50)
    """
    if df.empty:
        print("⚠️ 저장할 데이터가 없습니다.")
        return

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
        cursor = conn.cursor()

        # 1. 테이블이 없으면 생성
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            date DATE NOT NULL,
            ticker VARCHAR(6) NOT NULL,
            indicator VARCHAR(50) NOT NULL,
            value DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_date_ticker_indicator (date, ticker, indicator)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        cursor.execute(create_table_sql)
        conn.commit()

        # 2. INSERT ... ON DUPLICATE KEY UPDATE 쿼리
        insert_sql = f"""
        INSERT INTO {table_name} (date, ticker, indicator, value)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            value = VALUES(value),
            updated_at = CURRENT_TIMESTAMP
        """

        # 3. 배치 단위로 저장
        total_rows = len(df)
        saved_count = 0

        for i in range(0, total_rows, batch_size):
            batch_df = df.iloc[i:i + batch_size]

            # 데이터 준비
            data_to_insert = [
                (
                    row['date'],
                    row['ticker'],
                    row['indicator'],
                    row['value']
                )
                for _, row in batch_df.iterrows()
            ]

            # 배치 삽입
            cursor.executemany(insert_sql, data_to_insert)
            conn.commit()

            saved_count += len(batch_df)

            if saved_count % (batch_size * 10) == 0:  # 500개마다 로그
                print(f"  💾 {saved_count}/{total_rows}행 저장 완료")

        print(f"✅ DB 저장 완료: {saved_count}행 ({table_name})")

    except Exception as e:
        conn.rollback()
        print(f"❌ DB 저장 실패: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def convert_to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    wide format을 long format으로 변환

    Input:
        date, SARIMA, ETS, Theta, Ensemble, ticker

    Output:
        date, ticker, indicator, value
    """
    if df.empty:
        return df

    # 날짜 컬럼 이름 통일
    if 'date' not in df.columns and df.index.name == 'date':
        df = df.reset_index()

    # date를 날짜 형식으로 변환
    df['date'] = pd.to_datetime(df['date']).dt.date

    # melt를 사용하여 long format으로 변환
    long_df = df.melt(
        id_vars=['date', 'ticker'],
        value_vars=['SARIMA', 'ETS', 'Theta', 'Ensemble'],
        var_name='indicator',
        value_name='value'
    )

    # 정렬
    long_df = long_df.sort_values(['ticker', 'date', 'indicator']).reset_index(drop=True)

    return long_df


# ==================== 7. 배치 예측 함수 ====================

def batch_forecast_all_tickers(
        db_info: dict,
        fs_df: pd.DataFrame,
        H: int = 9,
        test_mode: bool = False,
        test_limit: int = 10,
        save_checkpoint: bool = True,
        checkpoint_interval: int = 50,
        save_to_db: bool = True,
        db_batch_size: int = 50
) -> tuple[pd.DataFrame, list]:
    """
    모든 ticker에 대해 매출 예측 수행

    Parameters:
    -----------
    db_info : dict
        DB 연결 정보
    fs_df : pd.DataFrame
        DataGuide 데이터 (symbol, indicator, date, value 컬럼 필요)
    H : int
        예측 기간 (기본값: 9)
    test_mode : bool
        테스트 모드 (처음 N개만 처리)
    test_limit : int
        테스트 모드에서 처리할 개수
    save_checkpoint : bool
        중간 저장 여부
    checkpoint_interval : int
        몇 개마다 체크포인트 저장할지
    save_to_db : bool
        DB 저장 여부 (기본값: True)
    db_batch_size : int
        DB 저장 배치 크기 (기본값: 50)

    Returns:
    --------
    tuple[pd.DataFrame, list]
        (결합된 예측 결과, 에러 리스트)
    """
    # ticker 목록 조회
    print("📊 ticker 목록 조회 중...")
    all_tickers = get_all_tickers(db_info)

    if test_mode:
        all_tickers = all_tickers[:test_limit]
        print(f"🧪 테스트 모드: 처음 {test_limit}개만 처리")

    print(f"✅ 처리할 ticker: {len(all_tickers)}개\n")

    # 결과 저장 리스트
    all_results = []
    error_list = []
    success_count = 0

    # 진행바와 함께 반복
    for idx, ticker in enumerate(tqdm(all_tickers, desc="🔄 예측 진행 중"), 1):
        try:
            # ---- DataGuide 데이터 ----
            ticker_dg = 'A' + ticker
            revenue_dg = fs_df[
                (fs_df['symbol'] == ticker_dg) &
                (fs_df['indicator'] == '매출액(천원)')
                ]

            if revenue_dg.empty:
                continue

            revenue_from_dg = revenue_dg[['date', 'value']].copy()
            revenue_from_dg['value'] = revenue_from_dg['value'] * 1000

            # ---- DART 데이터 ----
            revenue_df = get_quarterly_revenue_simple(db_info, ticker=ticker)

            if revenue_df.empty:
                continue

            # ---- 데이터 결합 ----
            revenue_from_dart = revenue_df[['report_date', 'thstrm_amount']].copy()
            revenue_from_dg.columns = ['date', 'revenue']
            revenue_from_dart.columns = ['date', 'revenue']

            revenue_concated_df = pd.concat(
                [revenue_from_dg, revenue_from_dart],
                axis=0
            ).drop_duplicates(
                subset=['date'],
                keep='first'
            ).set_index('date')

            # ---- 데이터 준비 ----
            df = revenue_concated_df.copy()
            df = ensure_datetime_index_df(df)
            series = df["revenue"].astype(float)

            # 최소 데이터 길이 검사
            n = len(series)
            if n < 48:
                error_list.append((ticker, f"데이터 부족: {n}개"))
                continue

            # ---- 빈도 추론 ----
            freq = infer_freq_alias(series.index)
            m = seasonal_periods_from_freq(freq)

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

            # ---- 결과 DataFrame 생성 ----
            forecast_index = pd.date_range(
                start=series.index[-1],
                periods=H + 1,
                freq=freq
            )[1:]

            result_df = pd.DataFrame({
                "SARIMA": sarima_result.get("forecast"),
                "ETS": ets_result.get("forecast"),
                "Theta": theta_result.get("forecast"),
            }, index=forecast_index)

            # 앙상블
            result_df["Ensemble"] = result_df[["SARIMA", "ETS", "Theta"]].mean(axis=1)
            result_df['ticker'] = ticker
            result_df['date'] = result_df.index

            # 결과 저장
            all_results.append(result_df)
            success_count += 1

            # 체크포인트 저장
            if save_checkpoint and success_count % checkpoint_interval == 0:
                checkpoint_df = pd.concat(all_results, axis=0, ignore_index=True)
                checkpoint_df.to_csv(
                    'revenue_forecast_checkpoint.csv',
                    index=False,
                    encoding='utf-8-sig'
                )
                tqdm.write(f"💾 체크포인트 저장: {success_count}개 완료")

                # DB에도 저장
                if save_to_db:
                    try:
                        # long format으로 변환
                        long_checkpoint_df = convert_to_long_format(checkpoint_df)
                        # DB에 저장
                        save_to_db_batch(
                            long_checkpoint_df,
                            db_info,
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
                import gc
                gc.collect()

                tqdm.write(f"✅ 메모리 정리 완료")

        except Exception as e:
            # 에러 발생 시 리스트에 추가하고 계속 진행
            error_list.append((ticker, str(e)))
            continue

    # ---- 최종 결과 결합 ----
    # all_results가 비어있을 수 있으므로 체크포인트 파일에서 읽어옴
    if save_checkpoint and os.path.exists('revenue_forecast_checkpoint.csv'):
        print("\n📂 체크포인트 파일에서 최종 데이터 로딩 중...")
        final_df = pd.read_csv('revenue_forecast_checkpoint.csv')

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
        import gc
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

            # DB에 저장
            save_to_db_batch(
                long_final_df,
                db_info,
                table_name="korea_revenue_forecast_result",
                batch_size=db_batch_size
            )

            # 메모리 정리
            del long_final_df
            import gc
            gc.collect()

        except Exception as db_error:
            print(f"⚠️ 최종 DB 저장 실패: {db_error}")
            print("CSV 파일은 정상적으로 저장됩니다.")
    else:
        final_df = pd.DataFrame()
        print(f"\n⚠️ 예측 성공한 ticker가 없습니다")

    # ---- 에러 요약 ----
    if error_list:
        print(f"❌ 에러 발생: {len(error_list)}개 ticker")

        # 에러 타입별 집계
        error_types = {}
        for ticker, error in error_list:
            error_key = error.split(':')[0] if ':' in error else error
            error_types[error_key] = error_types.get(error_key, 0) + 1

        print("\n에러 타입별 요약:")
        for error_type, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"  - {error_type}: {count}개")

    return final_df, error_list


# ==================== 7. 메인 실행 함수 ====================

def main():
    """메인 실행 함수"""

    print("=" * 80)
    print("한국 전체 기업 매출 시계열 예측")
    print("=" * 80)
    print()

    # 1. DB 연결 정보
    db_info = get_db_config()
    print(f"✅ DB 연결 정보 설정 완료 (host: {db_info['host']})")

    # 2. DataGuide 데이터 로드
    print("\n📊 DataGuide 데이터 로딩 중...")
    try:
        fs_df = fetch_table_data(db_info, "korea_fs_data")
        print(f"✅ DataGuide 데이터 로드 완료: {len(fs_df):,}행")
    except Exception as e:
        print(f"❌ DataGuide 데이터 로드 실패: {e}")
        print("테스트 모드로 진행합니다.")
        fs_df = pd.DataFrame()

    # 3. 테스트 모드 선택
    print("\n" + "=" * 80)
    print("실행 모드 선택")
    print("=" * 80)
    print("1. 테스트 모드 (처음 10개 ticker만)")
    print("2. 전체 실행 (모든 ticker)")

    choice = input("\n선택 (1 또는 2, 기본값=1): ").strip() or "1"

    if choice == "1":
        test_mode = True
        test_limit = 10
        print(f"\n🧪 테스트 모드 선택: 처음 {test_limit}개 ticker 처리")
    else:
        test_mode = False
        test_limit = None
        print("\n🚀 전체 실행 모드 선택: 모든 ticker 처리")

    # 4. 배치 예측 실행
    print("\n" + "=" * 80)
    print("예측 시작")
    print("=" * 80)
    print()

    result_df, error_list = batch_forecast_all_tickers(
        db_info=db_info,
        fs_df=fs_df,
        H=9,
        test_mode=test_mode,
        test_limit=test_limit,
        save_checkpoint=True,
        checkpoint_interval=50,
        save_to_db=True,  # DB 저장 활성화
        db_batch_size=50  # 배치 크기 50
    )

    # 5. 결과 저장
    if not result_df.empty:
        output_file = 'revenue_forecast_final.csv'
        result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n✅ 최종 결과 저장: {output_file}")
        print(f"   - 총 {len(result_df)}행")
        print(f"   - ticker 개수: {result_df['ticker'].nunique()}개")

        # 결과 샘플 출력
        print("\n📊 결과 샘플 (처음 5행):")
        print(result_df.head())

    # 6. 에러 로그 저장
    if error_list:
        error_file = 'revenue_forecast_errors.csv'
        error_df = pd.DataFrame(error_list, columns=['ticker', 'error'])
        error_df.to_csv(error_file, index=False, encoding='utf-8-sig')
        print(f"\n❌ 에러 로그 저장: {error_file}")
        print(f"   - 총 {len(error_list)}개 ticker 실패")

    print("\n" + "=" * 80)
    print("✅ 모든 작업 완료!")
    print("=" * 80)


# ==================== 8. 스크립트 실행 ====================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n\n❌ 예상치 못한 오류 발생: {e}")
        import traceback

        traceback.print_exc()
