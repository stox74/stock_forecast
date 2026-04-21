"""
한국 기업 분기별 매출 시계열 예측 시스템 (DataGuide 버전)
- 데이터 소스: korea_fs_data_from_DG (DataGuide wide → long 변환본)
- 매출 식별: item_code='M000904001' (매출액(천원))
- ticker 형식: 'A005930' (A 접두사 포함)
- Q4 처리: DataGuide는 각 분기가 독립값이므로 FY 역산 불필요 ★
- 모델: SARIMA, ETS, Theta 앙상블
- 결과 저장: korea_revenue_forecast_result
"""

import sys
import os
import numpy as np
import pandas as pd
import pymysql
from datetime import datetime
from pathlib import Path
from typing import Union, List, Tuple, Dict, Optional
from tqdm import tqdm
import warnings

warnings.filterwarnings('ignore')


# ==================== 데이터 소스 설정 (필요 시 여기만 수정) ====================
# 매출 데이터를 조회할 원천 테이블
FS_TABLE = "korea_fs_data_from_DG"

# DataGuide에서 매출액을 가리키는 item_code
#   M000904001 = '매출액(천원)' (IS 시트 원본)
REVENUE_ITEM_CODE = "M000904001"

# ticker 형식: True면 'A005930' (A 접두사 포함), False면 '005930'
TICKER_HAS_A_PREFIX = True


def normalize_ticker_for_dg(ticker) -> str:
    """
    DG 테이블 조회용 ticker 정규화.
    입력이 '005930', 5930, 'A005930' 어떤 형태든
    DG 스키마에 맞게 'A005930' 형태로 통일한다.
    """
    if isinstance(ticker, int):
        raw = f"{ticker:06d}"
    else:
        raw = str(ticker).strip()

    # 'A' 접두사 제거 (이미 있으면) 후 6자리 zfill
    body = raw[1:] if raw.upper().startswith('A') else raw
    body = body.zfill(6)

    return f"A{body}" if TICKER_HAS_A_PREFIX else body


# ==================== 경로 설정 ====================
def setup_universal_paths():
    """
    어떤 PC에서도 작동하는 범용 경로 설정
    DATA 폴더를 자동으로 찾아 경로 추가
    """
    current = Path.cwd()

    for parent in [current, *current.parents]:
        data_folder = parent / "DATA"
        if data_folder.exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            if str(data_folder) not in sys.path:
                sys.path.insert(0, str(data_folder))

            print("=" * 80)
            print("경로 설정 완료")
            print("=" * 80)
            print(f"프로젝트 루트: {parent}")
            print(f"DATA 폴더:    {data_folder}")
            print(f"현재 위치:     {current}")
            print("=" * 80 + "\n")

            return {
                'project_root': parent,
                'data_folder': data_folder,
                'current': current
            }

    raise FileNotFoundError(
        f"DATA 폴더를 찾을 수 없습니다.\n"
        f"현재 위치: {current}\n"
        f"상위 폴더에 DATA 폴더가 있는지 확인하세요."
    )


# 경로 설정 실행
try:
    paths = setup_universal_paths()
except FileNotFoundError as e:
    print(e)
    print("\n대안: 수동으로 경로를 설정하세요.")
    sys.exit(1)

# ==================== 필요 모듈 import ====================
try:
    from universal_ts_forecast_function import (
        forecast_sarima,
        forecast_ets,
        forecast_theta,
    )
    from stock_invest_function import get_db_host

    print("예측 모듈 import 성공\n")

except ImportError as e:
    print(f"모듈 import 실패: {e}")
    print("\n확인 사항:")
    print("1. DATA 폴더에 'universal_ts_forecast_function.py' 파일이 있는가?")
    print("2. DATA 폴더에 'stock_invest_function.py' 파일이 있는가?")
    sys.exit(1)


# ==================== DB 설정 ====================
def get_db_info() -> dict:
    """
    DB 연결 정보 반환

    Returns:
        dict: DB 연결 정보
    """
    return {
        "host": get_db_host(),
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }


# ==================== DB 연결 함수 ====================
def get_connection(db_info: dict):
    """
    DB 연결 생성 (config.py의 db_info 사용)

    Parameters:
        db_info: DB 연결 정보 딕셔너리

    Returns:
        pymysql.Connection: DB 연결 객체
    """
    conn = pymysql.connect(
        host=db_info["host"],
        port=int(db_info["port"]),
        user=db_info["user"],
        password=db_info["password"],
        db=db_info["database"],
        charset="utf8mb4",
    )
    return conn


# ==================== 데이터 조회 함수 ====================
def get_all_tickers(db_info: dict) -> List[str]:
    """
    DataGuide 테이블에서 매출 데이터가 존재하는 모든 ticker 조회.

    매출액(item_code='M000904001') 레코드가 하나라도 있는 기업만 대상.
    → 매출 데이터가 없어서 예측이 불가능한 기업은 처음부터 제외된다.

    Returns:
        list: ticker 리스트 (예: ['A000020', 'A000040', ...], 정렬됨)
    """
    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=db_info["database"],
        charset="utf8mb4",
    )

    try:
        sql = f"""
              SELECT DISTINCT ticker
              FROM {FS_TABLE}
              WHERE item_code = %s
              ORDER BY ticker
              """

        df = pd.read_sql(sql, conn, params=[REVENUE_ITEM_CODE])
        tickers = df['ticker'].tolist()

        print(f"매출 데이터 보유 ticker {len(tickers)}개 조회 완료 (소스: {FS_TABLE})\n")

        return tickers

    finally:
        conn.close()


def adjust_fy_to_q4(df: pd.DataFrame) -> pd.DataFrame:
    """
    ⚠️ [LEGACY] 이 함수는 DART 데이터용으로 만들어진 것이다.

    DART 데이터는 누적 공시 방식이라 (Q1, H1, Q3, FY) 구조였고,
    FY에서 Q1+H1+Q3를 빼서 Q4를 역산해야 했다.

    반면 DataGuide(korea_fs_data_from_DG) 데이터는 이미 각 분기가
    독립값으로 제공되므로 이 함수를 호출하면 오히려 잘못된 결과가 나온다.

    현재 파이프라인에서는 사용하지 않으며, 과거 DART 경로로 돌아갈 경우를
    대비해 함수 자체는 보존만 해둔다.

    Logic (DART 데이터 기준):
        각 연도별로 FY - (Q1 + Q2 + Q3) = 순수 Q4

    Parameters:
        df: 매출 데이터 (quarter, bsns_year, thstrm_amount 컬럼 필수)

    Returns:
        'quarter' 컬럼이 'Q4'로 변경되고
        'thstrm_amount'가 순수 분기 실적으로 조정된 DataFrame
    """
    result_df = df.copy()

    for year in result_df['bsns_year'].unique():
        year_mask = result_df['bsns_year'] == year
        fy_mask = year_mask & (result_df['quarter'] == 'FY')

        if fy_mask.any():
            fy_amount = result_df.loc[fy_mask, 'thstrm_amount'].iloc[0]

            q123_mask = year_mask & result_df['quarter'].isin(['Q1', 'H1', 'Q3'])
            q123_sum = result_df.loc[q123_mask, 'thstrm_amount'].sum()

            q4_amount = fy_amount - q123_sum

            result_df.loc[fy_mask, 'thstrm_amount'] = q4_amount
            result_df.loc[fy_mask, 'quarter'] = 'Q4'

    return result_df


def get_quarterly_revenue_simple(
        db_info: dict,
        ticker: Union[str, int],
        adjust_q4: bool = False,
        verbose: bool = False
) -> pd.DataFrame:
    """
    특정 ticker의 분기별 매출 시계열을 DataGuide 테이블에서 조회한다.

    DataGuide는 각 분기가 독립값으로 제공되므로 FY 역산(adjust_fy_to_q4)이
    불필요하다. 파라미터 `adjust_q4`는 기본값 False로 두고, downstream
    호환성 때문에 시그니처만 남겨둠.

    Parameters:
        db_info:    DB 연결 정보
        ticker:     종목 코드 ('005930', 5930, 'A005930' 어느 형태든 허용)
        adjust_q4:  [LEGACY, DG에서는 무시됨] True로 설정해도 경고만 표시하고 skip
        verbose:    진행 상황 출력 여부

    Returns:
        DataFrame with columns:
            report_date      DATE      (DG의 date 컬럼을 그대로 alias, downstream 호환)
            thstrm_amount    DOUBLE    (DG의 value를 alias, downstream 호환)
            bsns_year        INT       (date에서 추출, downstream 호환)
            quarter          str       (date에서 추출: 'Q1'/'Q2'/'Q3'/'Q4')
            ticker           str       (입력 ticker 정규화본)
    """
    # ticker 정규화 (A 접두사 보장)
    ticker_str = normalize_ticker_for_dg(ticker)

    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=db_info["database"],
        charset="utf8mb4",
    )

    try:
        # DG 스키마 → 기존 downstream 코드가 기대하는 컬럼명으로 alias
        # (forecast_quarterly_revenue() 이하가 report_date, thstrm_amount를
        #  그대로 사용하므로 여기서 이름만 맞춰준다)
        sql = f"""
              SELECT
                  date                  AS report_date,
                  value                 AS thstrm_amount,
                  YEAR(date)            AS bsns_year,
                  ticker                AS ticker
              FROM {FS_TABLE}
              WHERE ticker = %s
                AND item_code = %s
                AND value IS NOT NULL
              ORDER BY date
              """

        df = pd.read_sql(sql, conn, params=[ticker_str, REVENUE_ITEM_CODE])

        if df.empty:
            if verbose:
                print(f"  {ticker_str}: 데이터 없음")
            return df

        # quarter 컬럼 유도 (downstream 호환)
        df['quarter'] = 'Q' + ((pd.to_datetime(df['report_date']).dt.month + 2) // 3).astype(str)

        if verbose:
            print(f"  {ticker_str}: 매출 데이터 {len(df)}행 조회")

        # adjust_q4는 DG 데이터에서 불필요 — 호출되더라도 skip하고 경고만
        if adjust_q4:
            if verbose:
                print(f"  [INFO] DG 데이터는 분기 독립값이므로 adjust_fy_to_q4 skip")

        return df

    finally:
        conn.close()


# ==================== 예측 함수 ====================
def forecast_quarterly_revenue(
        ticker: str,
        db_info: dict,
        forecast_quarters: int = 8,
        min_data_periods: int = 24,
        verbose: bool = False
) -> Tuple[bool, pd.DataFrame, str]:
    """
    단일 ticker에 대한 분기별 매출 예측

    Parameters:
        ticker: 종목 코드
        db_info: DB 연결 정보
        forecast_quarters: 예측할 분기 수
        min_data_periods: 최소 필요 데이터 기간 (분기 수)
        verbose: 진행 상황 출력 여부

    Returns:
        (성공여부, 예측결과 DataFrame, 에러메시지)
    """
    try:
        # 1. 데이터 조회 (DG 데이터는 이미 분기 독립값이므로 adjust_q4=False)
        revenue_df = get_quarterly_revenue_simple(db_info, ticker, adjust_q4=False, verbose=verbose)

        if revenue_df.empty:
            return False, pd.DataFrame(), "데이터 없음"

        # 2. 최소 데이터 기간 확인
        if len(revenue_df) < min_data_periods:
            return False, pd.DataFrame(), f"데이터 부족 ({len(revenue_df)}개 < {min_data_periods}개)"

        # 3. 시계열 데이터 준비
        df = revenue_df[['report_date', 'thstrm_amount']].copy()
        df.columns = ['date', 'revenue']
        df['date'] = pd.to_datetime(df['date'])

        # 분기 정보 추출
        df['year'] = df['date'].dt.year
        df['quarter'] = df['date'].dt.quarter

        # 분기 PeriodIndex 생성
        df['period'] = df['year'].astype(str) + 'Q' + df['quarter'].astype(str)
        df['period'] = pd.PeriodIndex(df['period'], freq='Q')

        # 중복 제거 (각 분기의 마지막 값 사용)
        duplicates = df[df.duplicated(subset=['period'], keep=False)]
        if not duplicates.empty:
            df = df.sort_values(['period', 'date']).groupby('period').last().reset_index()

        # PeriodIndex를 인덱스로 설정
        df = df.set_index('period').sort_index()
        series = df['revenue'].astype(float)

        # 4. 모델별 예측
        m = 4  # 분기 데이터의 계절성

        sarima_result = forecast_sarima(
            y=series,
            forecast_horizon=forecast_quarters,
            seasonal_period=m,
            try_transforms=True
        )

        ets_result = forecast_ets(
            y=series,
            forecast_horizon=forecast_quarters,
            m=m,
            try_transforms=True
        )

        theta_result = forecast_theta(
            y=series,
            forecast_horizon=forecast_quarters,
            m=m,
            try_transforms=True
        )

        # 5. 결과 합치기
        last_period = series.index[-1]
        forecast_periods = pd.period_range(
            start=last_period + 1,
            periods=forecast_quarters,
            freq='Q'
        )

        result_df = pd.DataFrame({
            "SARIMA": sarima_result.get("forecast"),
            "ETS": ets_result.get("forecast"),
            "Theta": theta_result.get("forecast"),
        }, index=forecast_periods)

        # 앙상블 (세 모델 평균)
        result_df["Ensemble"] = result_df[["SARIMA", "ETS", "Theta"]].mean(axis=1)
        result_df['ticker'] = ticker

        # DatetimeIndex로 변환 (분기 말일)
        result_df.index = result_df.index.to_timestamp(how='end')
        result_df = result_df.reset_index()
        result_df.columns = ['date'] + list(result_df.columns[1:])

        return True, result_df, ""

    except Exception as e:
        error_msg = str(e)
        if verbose:
            print(f"  {ticker}: 예측 실패 - {error_msg}")
        return False, pd.DataFrame(), error_msg


# ==================== Long Format 변환 함수 ====================
def convert_to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wide format을 Long format으로 변환

    Wide format:
        date, ticker, SARIMA, ETS, Theta, Ensemble

    Long format:
        date, ticker, indicator, value

    Parameters:
        df: Wide format DataFrame

    Returns:
        Long format DataFrame
    """
    if df.empty:
        return pd.DataFrame(columns=['date', 'ticker', 'indicator', 'value'])

    # 모델 컬럼들
    model_cols = ['SARIMA', 'ETS', 'Theta', 'Ensemble']

    # melt를 사용하여 변환
    long_df = df.melt(
        id_vars=['date', 'ticker'],
        value_vars=model_cols,
        var_name='indicator',
        value_name='value'
    )

    # 정렬
    long_df = long_df.sort_values(['ticker', 'date', 'indicator']).reset_index(drop=True)

    return long_df


# ==================== 배치 저장 함수 (수정 버전) ====================
def save_forecasts_batch(
        forecasts_long: pd.DataFrame,
        db_info: dict,
        table_name: str = "korea_revenue_forecast_result",
        batch_size: int = 30
) -> Tuple[int, int, int]:
    """
    예측 결과를 배치로 DB에 저장 (실행 시점별로 누적 저장)

    DB 스키마:
        - id: 자동증가 PRIMARY KEY
        - date: 예측 날짜
        - ticker: 종목 코드
        - indicator: 모델명 (SARIMA, ETS, Theta, Ensemble)
        - value: 예측값
        - created_at: 예측 실행 시점 (자동 기록)

    특징:
        - UNIQUE KEY 없음 → 실행할 때마다 새로운 레코드 추가
        - created_at으로 예측 버전 구분
        - 과거 예측 이력 모두 보관

    Parameters:
        forecasts_long: Long format 예측 결과 DataFrame
        db_info: DB 연결 정보
        table_name: 저장할 테이블 이름
        batch_size: 배치 저장 크기

    Returns:
        tuple: (신규 삽입 수, 0, 총 시도 수)
    """
    if forecasts_long.empty:
        print("저장할 데이터가 없습니다")
        return 0, 0, 0

    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=db_info["database"],
        charset="utf8mb4",
        autocommit=False
    )

    try:
        cursor = conn.cursor()

        # 테이블 생성 (날짜 기준 UNIQUE KEY)
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            date DATE NOT NULL COMMENT '예측 대상 날짜',
            ticker VARCHAR(20) NOT NULL,
            indicator VARCHAR(50) NOT NULL,
            value DOUBLE,
            created_at DATE NOT NULL COMMENT '예측 실행 날짜 (DATE만)',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_date_ticker_indicator_created (date, ticker, indicator, created_at),
            INDEX idx_ticker (ticker),
            INDEX idx_date (date),
            INDEX idx_indicator (indicator),
            INDEX idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        COMMENT='한국 기업 매출 예측 결과 (날짜별 버전 관리)'
        """
        cursor.execute(create_table_sql)

        # 오늘 날짜 (DATE 형식)
        today_date = datetime.now().date()

        # 배치 insert with ON DUPLICATE KEY UPDATE
        insert_sql = f"""
        INSERT INTO {table_name} 
        (date, ticker, indicator, value, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            value = VALUES(value),
            updated_at = CURRENT_TIMESTAMP
        """

        # 통계
        total_new = 0
        total_updated = 0
        total_attempted = 0

        # 배치 단위로 저장
        total_rows = len(forecasts_long)

        for i in range(0, total_rows, batch_size):
            batch_df = forecasts_long.iloc[i:i + batch_size]
            batch_data = [
                (
                    row['date'],
                    row['ticker'],
                    row['indicator'],
                    float(row['value']) if pd.notna(row['value']) else None,
                    today_date  # created_at에 오늘 날짜 저장
                )
                for _, row in batch_df.iterrows()
            ]

            cursor.executemany(insert_sql, batch_data)
            affected = cursor.rowcount

            # 통계 계산
            if affected == len(batch_data):
                # 모두 신규 삽입
                total_new += len(batch_data)
            elif affected == len(batch_data) * 2:
                # 모두 업데이트
                total_updated += len(batch_data)
            else:
                # 혼합
                updated = max(0, (affected - len(batch_data)))
                new = len(batch_data) - updated
                total_new += new
                total_updated += updated

            total_attempted += len(batch_data)

            conn.commit()

            if (i + len(batch_df)) % 1000 == 0 or (i + len(batch_df)) == total_rows:
                print(f"  저장: {i + len(batch_df)}/{total_rows} 행")

        print(f"\n저장 결과:")
        print(f"  총 시도: {total_attempted:,}개")
        print(f"  신규 삽입: {total_new:,}개")
        print(f"  기존 업데이트: {total_updated:,}개 (같은 날 재실행)")
        print(f"  예측 실행 날짜: {today_date}")

        return total_new, total_updated, total_attempted

    except Exception as e:
        conn.rollback()
        print(f"DB 저장 실패: {e}")
        raise
    finally:
        conn.close()


# ==================== 메인 실행 함수 ====================
def process_all_tickers(
        tickers: List[str],
        db_info: dict,
        forecast_quarters: int = 8,
        min_data_periods: int = 24,
        batch_size: int = 30,
        verbose: bool = True
) -> Dict:
    """
    모든 ticker에 대해 예측 수행 및 배치 저장

    Parameters:
        tickers: 처리할 ticker 리스트
        db_info: DB 연결 정보
        forecast_quarters: 예측할 분기 수
        min_data_periods: 최소 필요 데이터 기간
        batch_size: 배치 저장 크기
        verbose: 진행 상황 출력 여부

    Returns:
        결과 통계 딕셔너리
    """
    total = len(tickers)
    success_count = 0
    fail_count = 0
    failed_tickers = []
    batch_buffer = []

    print("=" * 80)
    print("예측 시작")
    print("=" * 80)
    print(f"총 ticker 수: {total}개")
    print(f"예측 분기 수: {forecast_quarters}개")
    print(f"최소 데이터 기간: {min_data_periods}개 분기 ({min_data_periods / 4:.1f}년)")
    print(f"배치 저장 크기: {batch_size}개")
    print("=" * 80 + "\n")

    start_time = datetime.now()

    for i, ticker in enumerate(tqdm(tickers, desc="예측 진행"), 1):
        # 예측 수행
        success, result_df, error_msg = forecast_quarterly_revenue(
            ticker=ticker,
            db_info=db_info,
            forecast_quarters=forecast_quarters,
            min_data_periods=min_data_periods,
            verbose=False
        )

        if success:
            success_count += 1
            batch_buffer.append(result_df)

            # 배치 크기에 도달하면 저장
            if len(batch_buffer) >= batch_size:
                # Wide to Long 변환
                combined_wide = pd.concat(batch_buffer, ignore_index=True)
                combined_long = convert_to_long_format(combined_wide)

                # DB 저장
                new, updated, attempted = save_forecasts_batch(
                    combined_long,
                    db_info,
                    batch_size=batch_size
                )

                if verbose:
                    print(f"  배치 저장 완료: {new}개 레코드 추가")

                batch_buffer = []
        else:
            fail_count += 1
            failed_tickers.append((ticker, error_msg))

    # 남은 데이터 저장
    if batch_buffer:
        combined_wide = pd.concat(batch_buffer, ignore_index=True)
        combined_long = convert_to_long_format(combined_wide)

        new, updated, attempted = save_forecasts_batch(
            combined_long,
            db_info,
            batch_size=batch_size
        )

        if verbose:
            print(f"  최종 배치 저장 완료: {new}개 레코드 추가")

    end_time = datetime.now()
    elapsed_time = (end_time - start_time).total_seconds()

    # 결과 요약
    print("\n" + "=" * 80)
    print("예측 완료")
    print("=" * 80)
    print(f"총 처리 시간: {elapsed_time:.1f}초 ({elapsed_time / 60:.1f}분)")
    print(f"성공: {success_count}개 ({success_count / total * 100:.1f}%)")
    print(f"실패: {fail_count}개 ({fail_count / total * 100:.1f}%)")

    if success_count > 0:
        print(f"평균 처리 시간: {elapsed_time / total:.2f}초/ticker")

    if failed_tickers:
        print(f"\n실패한 ticker 분석:")
        error_summary = {}
        for ticker, error in failed_tickers:
            error_summary[error] = error_summary.get(error, 0) + 1

        for error, count in sorted(error_summary.items(), key=lambda x: -x[1]):
            print(f"  {error}: {count}개")

        # 실패 ticker 샘플 출력 (최대 20개)
        if len(failed_tickers) <= 20:
            print(f"\n실패 ticker 목록:")
            for ticker, error in failed_tickers:
                print(f"  {ticker}: {error}")
        else:
            print(f"\n실패 ticker 샘플 (처음 20개):")
            for ticker, error in failed_tickers[:20]:
                print(f"  {ticker}: {error}")

    return {
        'total': total,
        'success': success_count,
        'fail': fail_count,
        'failed_tickers': failed_tickers,
        'elapsed_time': elapsed_time
    }


# ==================== 메인 실행 ====================
def main():
    """메인 실행 함수"""
    print("=" * 80)
    print("한국 기업 분기별 매출 시계열 예측 시스템")
    print("=" * 80)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ========================================================================
    # 설정 (여기를 수정하세요)
    # ========================================================================
    FORECAST_QUARTERS = 8  # 예측할 분기 수
    MIN_DATA_PERIODS = 24  # 최소 데이터 기간 (24분기 = 6년)
    BATCH_SIZE = 30  # 배치 저장 크기

    # 테스트 모드 설정
    # TEST_MODE = None   : 대화형 모드 (실행 시 선택)
    # TEST_MODE = True   : 자동 테스트 모드 (TEST_SIZE 개수만큼 실행)
    # TEST_MODE = False  : 전체 모드 (전체 ticker 실행)
    TEST_MODE = False
    TEST_SIZE = 30  # 테스트 모드 시 ticker 개수
    # ========================================================================

    # DB 연결 정보
    db_info = get_db_info()

    # 전체 ticker 조회
    print("DB에서 ticker 목록 조회 중...\n")
    all_tickers = get_all_tickers(db_info)

    # 실행 모드 선택
    if TEST_MODE:
        # 자동 테스트 모드
        test_tickers = all_tickers[:TEST_SIZE]
        mode_name = f"테스트 모드 - {TEST_SIZE}개"
        print(f"[{mode_name}] 처리 대상: {len(test_tickers)}개 ticker")
        print(f"ticker 목록: {test_tickers}\n")
    elif TEST_MODE is False:
        # 전체 모드
        test_tickers = all_tickers
        mode_name = f"전체 모드 - {len(all_tickers)}개"
        print(f"[{mode_name}] 처리 대상: {len(test_tickers)}개 ticker")
        print(f"샘플 ticker: {test_tickers[:10]}\n")
    else:
        # 대화형 모드
        print("\n실행 모드 선택:")
        print("1. 테스트 모드 (10개 ticker) - 빠른 테스트")
        print("2. 테스트 모드 (20개 ticker)")
        print("3. 소규모 (50개 ticker)")
        print("4. 중간 규모 (100개 ticker)")
        print("5. 대규모 (500개 ticker)")
        print("6. 전체 실행 (전체 ticker) - 장시간 소요")

        mode = input("\n선택 (1-6): ").strip()

        mode_config = {
            '1': (10, "테스트 모드 - 10개"),
            '2': (20, "테스트 모드 - 20개"),
            '3': (50, "소규모 - 50개"),
            '4': (100, "중간 규모 - 100개"),
            '5': (500, "대규모 - 500개"),
            '6': (len(all_tickers), f"전체 모드 - {len(all_tickers)}개")
        }

        if mode not in mode_config:
            print("잘못된 선택입니다. 종료합니다.")
            return

        num_tickers, mode_name = mode_config[mode]
        test_tickers = all_tickers[:num_tickers]

        print(f"\n[{mode_name}] 처리 대상: {len(test_tickers)}개 ticker")
        print(f"샘플 ticker: {test_tickers[:min(10, len(test_tickers))]}\n")

    # 설정 확인
    print("=" * 80)
    print("예측 설정")
    print("=" * 80)
    print(f"예측 분기 수: {FORECAST_QUARTERS}개")
    print(f"최소 데이터 기간: {MIN_DATA_PERIODS}개 분기 ({MIN_DATA_PERIODS / 4:.1f}년)")
    print(f"배치 저장 크기: {BATCH_SIZE}개")
    print(f"사용 모델: SARIMA, ETS, Theta + Ensemble")
    print("=" * 80 + "\n")

    # 예상 소요 시간
    estimated_seconds_per_ticker = 2
    estimated_total_time = len(test_tickers) * estimated_seconds_per_ticker
    print(f"예상 소요 시간: 약 {estimated_total_time / 60:.1f}분")
    print(f"(ticker당 평균 {estimated_seconds_per_ticker}초 기준)\n")

    # 실행 확인 (대화형 모드만)
    if TEST_MODE is None:
        confirm = input("예측을 시작하시겠습니까? (y/n): ").strip().lower()
        if confirm != 'y':
            print("실행을 취소했습니다.")
            return
        print()

    # 예측 실행
    result = process_all_tickers(
        tickers=test_tickers,
        db_info=db_info,
        forecast_quarters=FORECAST_QUARTERS,
        min_data_periods=MIN_DATA_PERIODS,
        batch_size=BATCH_SIZE,
        verbose=True
    )

    # 재시도 옵션
    if result['failed_tickers'] and len(result['failed_tickers']) <= 20:
        retry = input(f"\n실패한 {len(result['failed_tickers'])}개 ticker를 재시도하시겠습니까? (y/n): ").strip().lower()
        if retry == 'y':
            print("\n재시도 중...\n")
            retry_tickers = [t[0] for t in result['failed_tickers']]
            retry_result = process_all_tickers(
                tickers=retry_tickers,
                db_info=db_info,
                forecast_quarters=FORECAST_QUARTERS,
                min_data_periods=MIN_DATA_PERIODS,
                batch_size=BATCH_SIZE,
                verbose=True
            )

            print("\n재시도 결과:")
            print(f"성공: {retry_result['success']}개")
            print(f"실패: {retry_result['fail']}개")

    print("\n" + "=" * 80)
    print("모든 작업 완료")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n" + "=" * 80)
        print("사용자에 의해 중단되었습니다")
        print("=" * 80)
        print("정리 작업 중...")

        # TensorFlow 세션 정리
        try:
            import tensorflow as tf
            import keras.backend as K

            K.clear_session()
            print("TensorFlow 세션 정리 완료")
        except:
            pass

        # 강제 종료 방지를 위한 짧은 대기
        import time

        time.sleep(0.5)

        print("프로그램 종료")
        print("=" * 80)
        sys.exit(0)

    except Exception as e:
        print("\n" + "=" * 80)
        print("오류 발생")
        print("=" * 80)
        print(f"오류: {str(e)}")
        import traceback

        traceback.print_exc()

        # TensorFlow 정리
        try:
            import tensorflow as tf
            import keras.backend as K

            K.clear_session()
        except:
            pass

        sys.exit(1)