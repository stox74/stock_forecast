"""
앙상블 예측 모델 생성 스크립트
- SARIMA, ETS, Theta, LSTM 모델의 예측값을 평균하여 앙상블 예측 생성
- us_stock_price_forecast_result 테이블에 저장
"""

import pandas as pd
import numpy as np
import pymysql
from datetime import datetime
from typing import List, Optional, Tuple
import warnings

warnings.filterwarnings('ignore')

# ===========================
# DB 설정
# ===========================

from DATA.stock_invest_function import get_db_host

DB_CONFIG = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar',
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'read_timeout': 30,
    'write_timeout': 30
}


# ===========================
# 데이터 조회
# ===========================
def get_forecasts_by_date(forecast_at: str, include_recent: bool = True) -> pd.DataFrame:
    """
    특정 예측일의 모든 예측 데이터 조회

    Parameters:
    -----------
    forecast_at : str
        예측 생성일 (YYYY-MM-DD)
    include_recent : bool
        최근 7일 이내의 다른 예측도 포함할지 여부

    Returns:
    --------
    pd.DataFrame
        ticker, date, item, value 컬럼을 가진 DataFrame
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        if include_recent:
            # 최근 7일 이내의 예측 데이터도 포함
            query = """
                    SELECT ticker, date, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE forecast_at >= DATE_SUB(%s \
                        , INTERVAL 7 DAY)
                      AND forecast_at <= %s
                      AND item NOT LIKE '%%_params'
                      AND item NOT LIKE '%%ensemble%%'
                    ORDER BY ticker, date, item \
                    """
            df = pd.read_sql(query, connection, params=(forecast_at, forecast_at))
        else:
            # 특정 날짜만
            query = """
                    SELECT ticker, date, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE forecast_at = %s
                      AND item NOT LIKE '%%_params'
                      AND item NOT LIKE '%%ensemble%%'
                    ORDER BY ticker, date, item \
                    """
            df = pd.read_sql(query, connection, params=(forecast_at,))

        return df

    except Exception as e:
        print(f"예측 데이터 조회 오류: {e}")
        return pd.DataFrame()
    finally:
        if connection:
            connection.close()


def get_latest_forecast_date() -> Optional[str]:
    """
    가장 최근 예측일 조회

    Returns:
    --------
    str or None
        최근 예측일 (YYYY-MM-DD)
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()

        query = """
                SELECT MAX(forecast_at) as latest_date
                FROM us_stock_price_forecast_result
                WHERE item NOT LIKE '%%_params'
                  AND item NOT LIKE '%%ensemble%%' \
                """

        cursor.execute(query)
        result = cursor.fetchone()

        if result and result[0]:
            return result[0].strftime('%Y-%m-%d')

        return None

    except Exception as e:
        print(f"최근 예측일 조회 오류: {e}")
        return None
    finally:
        if connection:
            connection.close()


def get_available_forecast_dates() -> List[str]:
    """
    사용 가능한 모든 예측일 목록 조회

    Returns:
    --------
    List[str]
        예측일 목록 (YYYY-MM-DD)
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        query = """
                SELECT DISTINCT forecast_at
                FROM us_stock_price_forecast_result
                WHERE item NOT LIKE '%%_params'
                  AND item NOT LIKE '%%ensemble%%'
                ORDER BY forecast_at DESC \
                """

        df = pd.read_sql(query, connection)

        if not df.empty:
            return [d.strftime('%Y-%m-%d') for d in df['forecast_at']]

        return []

    except Exception as e:
        print(f"예측일 목록 조회 오류: {e}")
        return []
    finally:
        if connection:
            connection.close()


# ===========================
# 앙상블 예측 생성
# ===========================
def normalize_model_name(item: str) -> str:
    """
    모델 이름 정규화
    - lstm_seq*_units* 형태는 모두 'lstm'으로 통합
    - 다른 모델은 그대로 유지

    Parameters:
    -----------
    item : str
        원본 모델 이름

    Returns:
    --------
    str
        정규화된 모델 이름
    """
    if item.startswith('lstm'):
        return 'lstm'
    else:
        return item.lower()


def create_ensemble_predictions(df_forecasts: pd.DataFrame) -> pd.DataFrame:
    """
    여러 모델의 예측값을 평균하여 앙상블 예측 생성
    LSTM의 다양한 파라미터 조합은 하나의 모델로 간주하고 평균 계산
    같은 모델의 여러 forecast_at가 있으면 최신 것만 사용

    Parameters:
    -----------
    df_forecasts : pd.DataFrame
        ticker, date, item, value, forecast_at 컬럼을 가진 예측 데이터

    Returns:
    --------
    pd.DataFrame
        ticker, date, ensemble_value, model_count, models 컬럼을 가진 앙상블 예측
    """
    if df_forecasts.empty:
        return pd.DataFrame()

    # 모델 이름 정규화
    df_forecasts = df_forecasts.copy()
    df_forecasts['normalized_model'] = df_forecasts['item'].apply(normalize_model_name)

    # 각 ticker, date, normalized_model 조합에서 가장 최근 forecast_at만 사용
    df_forecasts['forecast_at'] = pd.to_datetime(df_forecasts['forecast_at'])
    df_latest = df_forecasts.sort_values('forecast_at', ascending=False).groupby(
        ['ticker', 'date', 'normalized_model']
    ).first().reset_index()

    # 같은 normalized_model의 평균 계산 (LSTM 여러 파라미터 통합)
    df_model_avg = df_latest.groupby(['ticker', 'date', 'normalized_model']).agg({
        'value': 'mean'
    }).reset_index()

    # ticker와 date별로 그룹화하여 최종 앙상블 평균 계산
    ensemble = df_model_avg.groupby(['ticker', 'date']).agg({
        'value': ['mean', 'count'],
        'normalized_model': lambda x: ','.join(sorted(x.unique()))
    }).reset_index()

    # 컬럼명 정리
    ensemble.columns = ['ticker', 'date', 'ensemble_value', 'model_count', 'models']

    return ensemble


def analyze_model_coverage(df_forecasts: pd.DataFrame) -> pd.DataFrame:
    """
    티커별 모델 커버리지 분석
    LSTM의 다양한 파라미터는 하나의 모델로 통합하여 계산

    Parameters:
    -----------
    df_forecasts : pd.DataFrame
        예측 데이터

    Returns:
    --------
    pd.DataFrame
        티커별 사용된 모델 수 및 모델 목록
    """
    if df_forecasts.empty:
        return pd.DataFrame()

    # 모델 이름 정규화
    df_forecasts = df_forecasts.copy()
    df_forecasts['normalized_model'] = df_forecasts['item'].apply(normalize_model_name)

    # 각 티커의 첫 번째 예측 날짜 기준으로 모델 분석
    first_dates = df_forecasts.groupby('ticker')['date'].min().reset_index()

    coverage = []
    for _, row in first_dates.iterrows():
        ticker = row['ticker']
        first_date = row['date']

        ticker_models = df_forecasts[
            (df_forecasts['ticker'] == ticker) &
            (df_forecasts['date'] == first_date)
            ]['normalized_model'].unique()

        coverage.append({
            'ticker': ticker,
            'model_count': len(ticker_models),
            'models': ', '.join(sorted(ticker_models))
        })

    return pd.DataFrame(coverage)


# ===========================
# DB 저장
# ===========================
def save_ensemble_to_db(df_ensemble: pd.DataFrame, forecast_at: str) -> int:
    """
    앙상블 예측 결과를 데이터베이스에 저장

    Parameters:
    -----------
    df_ensemble : pd.DataFrame
        앙상블 예측 데이터 (ticker, date, ensemble_value)
    forecast_at : str
        예측 생성일 (YYYY-MM-DD)

    Returns:
    --------
    int
        저장된 행 수
    """
    if df_ensemble.empty:
        return 0

    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()

        # 레코드 준비
        records = []
        for _, row in df_ensemble.iterrows():
            records.append({
                'date': row['date'].strftime('%Y-%m-%d') if isinstance(row['date'], pd.Timestamp) else row['date'],
                'ticker': row['ticker'],
                'item': 'ensemble',
                'value': float(row['ensemble_value']),
                'forecast_at': forecast_at
            })

        # 저장
        sql = """
              INSERT INTO us_stock_price_forecast_result
                  (date, ticker, item, value, forecast_at)
              VALUES (%(date)s, %(ticker)s, %(item)s, %(value)s, %(forecast_at)s) ON DUPLICATE KEY \
              UPDATE \
                  value = \
              VALUES (value), created_at = CURRENT_TIMESTAMP \
              """

        cursor.executemany(sql, records)
        connection.commit()

        rows_affected = cursor.rowcount

        return rows_affected

    except Exception as e:
        print(f"앙상블 저장 오류: {e}")
        if connection:
            connection.rollback()
        return 0
    finally:
        if connection:
            connection.close()


def get_ensemble_summary(forecast_at: str) -> pd.DataFrame:
    """
    저장된 앙상블 예측 조회

    Parameters:
    -----------
    forecast_at : str
        예측일

    Returns:
    --------
    pd.DataFrame
        앙상블 예측 결과
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        query = """
                SELECT ticker, date, value, forecast_at
                FROM us_stock_price_forecast_result
                WHERE forecast_at = %s
                  AND item = 'ensemble'
                ORDER BY ticker, date \
                """

        df = pd.read_sql(query, connection, params=(forecast_at,))

        return df

    except Exception as e:
        print(f"앙상블 조회 오류: {e}")
        return pd.DataFrame()
    finally:
        if connection:
            connection.close()


# ===========================
# 메인 실행
# ===========================
def process_ensemble_for_date(forecast_at: str,
                              min_models: int = 2,
                              include_recent: bool = True) -> Tuple[int, int]:
    """
    특정 예측일의 앙상블 예측 생성 및 저장

    Parameters:
    -----------
    forecast_at : str
        예측일 (YYYY-MM-DD)
    min_models : int
        앙상블 생성에 필요한 최소 모델 수 (기본값: 2)
    include_recent : bool
        최근 7일 이내 다른 예측도 포함할지 여부 (기본값: True)

    Returns:
    --------
    Tuple[int, int]
        (저장된 티커 수, 저장된 총 레코드 수)
    """
    print(f"\n{'=' * 80}")
    print(f"앙상블 예측 생성: {forecast_at}")
    if include_recent:
        print(f"(최근 7일 이내 예측 데이터 포함)")
    print(f"{'=' * 80}")

    # 1. 예측 데이터 조회
    print("\n[1단계] 예측 데이터 조회 중...")
    df_forecasts = get_forecasts_by_date(forecast_at, include_recent=include_recent)

    if df_forecasts.empty:
        print(f"  예측 데이터 없음")
        return 0, 0

    total_records = len(df_forecasts)
    unique_tickers = df_forecasts['ticker'].nunique()
    unique_items = df_forecasts['item'].nunique()
    unique_forecast_dates = df_forecasts['forecast_at'].nunique()

    # 정규화된 모델 목록
    df_forecasts_temp = df_forecasts.copy()
    df_forecasts_temp['normalized_model'] = df_forecasts_temp['item'].apply(normalize_model_name)
    unique_models = df_forecasts_temp['normalized_model'].nunique()
    normalized_models = sorted(df_forecasts_temp['normalized_model'].unique())

    print(f"  총 {total_records}개 레코드")
    print(f"  티커 수: {unique_tickers}개")
    print(f"  예측일 수: {unique_forecast_dates}개")
    if unique_forecast_dates > 1:
        forecast_dates = sorted(df_forecasts['forecast_at'].unique())
        print(f"  예측일 범위: {forecast_dates[0]} ~ {forecast_dates[-1]}")
    print(f"  원본 item 수: {unique_items}개")
    print(f"  정규화된 모델 수: {unique_models}개")
    print(f"  모델 목록: {', '.join(normalized_models)}")

    # 2. 모델 커버리지 분석
    print("\n[2단계] 모델 커버리지 분석 중...")
    df_coverage = analyze_model_coverage(df_forecasts)

    if not df_coverage.empty:
        print(f"\n모델 커버리지 분포:")
        coverage_dist = df_coverage['model_count'].value_counts().sort_index()
        for count, num_tickers in coverage_dist.items():
            print(f"  {count}개 모델: {num_tickers}개 티커")

        # 모델이 적은 티커 출력
        few_models = df_coverage[df_coverage['model_count'] < min_models]
        if not few_models.empty:
            print(f"\n주의: {len(few_models)}개 티커는 {min_models}개 미만의 모델 사용")
            print(f"샘플 (상위 5개):")
            for _, row in few_models.head(5).iterrows():
                print(f"  {row['ticker']}: {row['model_count']}개 - {row['models']}")

    # 3. 앙상블 예측 생성
    print(f"\n[3단계] 앙상블 예측 생성 중...")
    df_ensemble = create_ensemble_predictions(df_forecasts)

    if df_ensemble.empty:
        print(f"  앙상블 생성 실패")
        return 0, 0

    # 최소 모델 수 필터링
    df_ensemble_filtered = df_ensemble[df_ensemble['model_count'] >= min_models].copy()

    excluded = len(df_ensemble) - len(df_ensemble_filtered)
    if excluded > 0:
        print(f"  {excluded}개 티커 제외 (모델 수 < {min_models})")

        # 제외된 티커 상세 정보 (샘플)
        excluded_tickers = df_ensemble[df_ensemble['model_count'] < min_models]
        if len(excluded_tickers) <= 10:
            print(f"\n  제외된 티커 목록:")
            for _, row in excluded_tickers.iterrows():
                print(f"    {row['ticker']}: {row['model_count']}개 모델 ({row['models']})")
        else:
            print(f"\n  제외된 티커 샘플 (상위 5개):")
            for _, row in excluded_tickers.head(5).iterrows():
                print(f"    {row['ticker']}: {row['model_count']}개 모델 ({row['models']})")

    print(f"  앙상블 생성 완료: {len(df_ensemble_filtered)}개 티커")

    # 4. DB 저장
    print(f"\n[4단계] 데이터베이스 저장 중...")
    rows_saved = save_ensemble_to_db(df_ensemble_filtered, forecast_at)

    if rows_saved > 0:
        print(f"  {rows_saved}개 레코드 저장 완료")
    else:
        print(f"  저장 실패")
        return 0, 0

    # 5. 저장 결과 확인
    print(f"\n[5단계] 저장 결과 확인...")
    df_saved = get_ensemble_summary(forecast_at)

    if not df_saved.empty:
        unique_tickers_saved = df_saved['ticker'].nunique()
        print(f"  저장된 티커 수: {unique_tickers_saved}개")

        # 샘플 출력
        print(f"\n샘플 앙상블 예측 (상위 3개 티커):")
        for ticker in df_saved['ticker'].unique()[:3]:
            ticker_data = df_saved[df_saved['ticker'] == ticker].head(3)
            print(f"\n  {ticker}:")
            for _, row in ticker_data.iterrows():
                print(f"    {row['date'].strftime('%Y-%m-%d')}: ${row['value']:.2f}")

    return len(df_ensemble_filtered), rows_saved


def main():
    """
    메인 실행 함수
    """
    print("=" * 80)
    print("앙상블 예측 모델 생성 시스템")
    print("=" * 80)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 사용 가능한 예측일 목록 조회
    print("\n사용 가능한 예측일 조회 중...")
    available_dates = get_available_forecast_dates()

    if not available_dates:
        print("예측 데이터가 없습니다.")
        return

    print(f"총 {len(available_dates)}개의 예측일 발견:")
    for i, date in enumerate(available_dates[:10], 1):
        print(f"  {i}. {date}")

    if len(available_dates) > 10:
        print(f"  ... (외 {len(available_dates) - 10}개)")

    # 최근 예측일 확인
    latest_date = available_dates[0]
    print(f"\n최근 예측일: {latest_date}")

    # 실행 모드 선택
    print("\n실행 모드 선택:")
    print("1. 최근 예측일만 처리")
    print("2. 특정 예측일 처리")
    print("3. 모든 예측일 처리")

    mode = input("\n선택 (1-3): ").strip()

    if mode == '1':
        # 최근 예측일만
        forecast_dates = [latest_date]
        print(f"\n처리 대상: {latest_date}")

    elif mode == '2':
        # 특정 예측일
        print(f"\n예측일 입력 (YYYY-MM-DD, 예: {latest_date}): ")
        selected_date = input().strip()

        if selected_date not in available_dates:
            print(f"오류: {selected_date}는 존재하지 않는 예측일입니다.")
            return

        forecast_dates = [selected_date]
        print(f"\n처리 대상: {selected_date}")

    elif mode == '3':
        # 모든 예측일
        forecast_dates = available_dates
        print(f"\n처리 대상: 총 {len(forecast_dates)}개 예측일")

    else:
        print("잘못된 선택입니다.")
        return

    # 최소 모델 수 설정
    print("\n앙상블 생성에 필요한 최소 모델 수 (기본값: 2): ")
    min_models_input = input().strip()
    min_models = int(min_models_input) if min_models_input.isdigit() else 2
    print(f"최소 모델 수: {min_models}개")

    # 최근 데이터 포함 여부
    print("\n최근 7일 이내 다른 예측 데이터도 포함하시겠습니까? (y/n, 기본값: y): ")
    include_recent_input = input().strip().lower()
    include_recent = include_recent_input != 'n'
    if include_recent:
        print("최근 7일 이내 예측 데이터 포함")
    else:
        print("선택한 날짜만 사용")

    # 실행 확인
    confirm = input("\n앙상블 생성을 시작하시겠습니까? (y/n): ").strip().lower()
    if confirm != 'y':
        print("실행을 취소했습니다.")
        return

    # 처리 시작
    print("\n" + "=" * 80)
    print("앙상블 생성 시작")
    print("=" * 80)

    total_tickers = 0
    total_records = 0
    success_dates = 0

    start_time = datetime.now()

    for i, forecast_date in enumerate(forecast_dates, 1):
        print(f"\n[{i}/{len(forecast_dates)}] 처리 중: {forecast_date}")

        tickers, records = process_ensemble_for_date(
            forecast_date,
            min_models,
            include_recent=include_recent
        )

        if tickers > 0:
            total_tickers += tickers
            total_records += records
            success_dates += 1
            print(f"  완료: {tickers}개 티커, {records}개 레코드")
        else:
            print(f"  실패 또는 데이터 없음")

    end_time = datetime.now()
    elapsed_time = (end_time - start_time).total_seconds()

    # 최종 결과
    print("\n" + "=" * 80)
    print("앙상블 생성 완료")
    print("=" * 80)
    print(f"총 처리 시간: {elapsed_time:.1f}초 ({elapsed_time / 60:.1f}분)")
    print(f"처리한 예측일: {success_dates}/{len(forecast_dates)}개")
    print(f"생성된 총 티커 수: {total_tickers}개")
    print(f"저장된 총 레코드 수: {total_records}개")

    # 최근 결과 확인
    if success_dates > 0:
        print("\n" + "=" * 80)
        print("최근 앙상블 예측 확인")
        print("=" * 80)

        df_recent = get_ensemble_summary(forecast_dates[0])

        if not df_recent.empty:
            print(f"\n{forecast_dates[0]} 앙상블 예측:")
            print(f"총 {df_recent['ticker'].nunique()}개 티커")
            print(f"\n상위 10개 티커 샘플:")

            sample_tickers = df_recent['ticker'].unique()[:10]
            for ticker in sample_tickers:
                ticker_data = df_recent[df_recent['ticker'] == ticker].head(3)
                values = ticker_data['value'].tolist()
                dates = [d.strftime('%Y-%m') for d in ticker_data['date']]
                print(
                    f"  {ticker}: {dates[0]}=${values[0]:.2f}, {dates[1]}=${values[1]:.2f}, {dates[2]}=${values[2]:.2f}...")

    print("\n" + "=" * 80)
    print("모든 작업 완료")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n오류 발생: {str(e)}")
        import traceback

        traceback.print_exc()