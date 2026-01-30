"""
Theta 기반 미국 주식 월말 주가 예측 모듈
"""

import pandas as pd
import numpy as np
import pymysql
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import warnings

warnings.filterwarnings('ignore')

from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tools.sm_exceptions import ConvergenceWarning

warnings.simplefilter('ignore', ConvergenceWarning)

from DATA.stock_invest_function import get_db_host

# DB 연결 정보
DB_CONFIG = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}


def get_monthly_close_price(
        ticker: str,
        connection,
        include_current_month: bool = True
) -> Optional[pd.DataFrame]:
    """
    특정 티커의 월말 종가 데이터 추출

    Parameters:
    -----------
    ticker : str
        종목 티커
    connection : pymysql.Connection
        DB 연결 객체
    include_current_month : bool
        현재 진행 중인 월의 최신 데이터 포함 여부 (기본값: True)

    Returns:
    --------
    pd.DataFrame or None
        월말 종가 데이터 (인덱스: date, 컬럼: close_price)
    """
    try:
        query = """
                SELECT date, value as close_price
                FROM us_stock_daily_market_cap
                WHERE ticker = %s
                  AND indicator = 'close_price'
                ORDER BY date \
                """

        df = pd.read_sql(query, connection, params=(ticker,))

        if df.empty:
            return None

        # date를 datetime으로 변환
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)

        # 월말 데이터만 추출 (각 월의 마지막 거래일)
        df_monthly = df.resample('M').last()

        # 현재 진행 중인 월 포함 옵션
        if include_current_month:
            # 가장 최근 데이터의 날짜
            last_date = df.index[-1]
            last_month_end = df_monthly.index[-1]

            # 최근 데이터가 마지막 월말 이후라면 (현재 진행 중인 월)
            if last_date > last_month_end:
                # 현재 진행 중인 월의 최신 데이터를 해당 월말로 설정
                current_month_end = last_date + pd.offsets.MonthEnd(0)

                # 해당 월의 최신 데이터 추가
                current_month_price = df.loc[last_date, 'close_price']
                df_monthly.loc[current_month_end] = current_month_price

        # 결측치 제거
        df_monthly = df_monthly.dropna()

        # 최소 24개월 데이터 필요 (2년)
        if len(df_monthly) < 24:
            return None

        return df_monthly

    except Exception as e:
        print(f"Error getting monthly data for {ticker}: {str(e)}")
        return None


def find_best_theta_params(
        log_prices: pd.Series,
        period: int = 12
) -> Tuple[float, str, bool, float]:
    """
    AIC 최소화를 통한 최적 Theta 파라미터 탐색

    Theta 모델의 주요 파라미터:
    - theta: 세타 계수 (0~1 사이, 기본값=2)
    - deseasonalize: 계절성 제거 여부
    - use_test: 테스트 방법 ('additive' or 'multiplicative')

    Parameters:
    -----------
    log_prices : pd.Series
        로그 변환된 주가 데이터
    period : int
        계절성 주기 (기본값: 12)

    Returns:
    --------
    Tuple[float, str, bool, float]
        (theta, method, deseasonalize, best_aic)
    """
    best_aic = np.inf
    best_params = None

    # Theta 파라미터 조합
    # theta 값: 클수록 선형 추세에 가까움
    theta_values = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    # deseasonalize: 계절성 제거 여부
    deseasonalize_options = [True, False]

    # method: 계절성 처리 방법
    method_options = ['additive', 'multiplicative']

    for theta in theta_values:
        for deseasonalize in deseasonalize_options:
            for method in method_options:
                # 곱셈 방법은 양수 데이터만 가능
                if method == 'multiplicative' and (log_prices <= 0).any():
                    continue

                try:
                    model = ThetaModel(
                        log_prices,
                        period=period,
                        deseasonalize=deseasonalize,
                        method=method
                    )

                    fitted = model.fit()

                    # AIC 계산 (ThetaModel은 기본적으로 AIC를 제공하지 않으므로 추정)
                    # 잔차 기반으로 AIC 근사값 계산
                    residuals = fitted.fittedvalues - log_prices
                    n = len(log_prices)
                    k = 3  # 파라미터 개수 추정

                    mse = np.mean(residuals ** 2)
                    aic_approx = n * np.log(mse) + 2 * k

                    if aic_approx < best_aic:
                        best_aic = aic_approx
                        best_params = {
                            'theta': theta,
                            'method': method,
                            'deseasonalize': deseasonalize,
                            'aic': aic_approx
                        }

                except Exception as e:
                    continue

    if best_params is None:
        # 최적화 실패 시 기본값
        return 2.0, 'additive', True, np.inf

    return (
        best_params['theta'],
        best_params['method'],
        best_params['deseasonalize'],
        best_params['aic']
    )


def forecast_theta_optimized(
        ticker: str,
        df_monthly: pd.DataFrame,
        forecast_months: int = 6,
        optimize: bool = True,
        period: int = 12
) -> Optional[Dict]:
    """
    Theta 모델을 사용한 주가 예측 (최적화 포함)

    Parameters:
    -----------
    ticker : str
        종목 티커
    df_monthly : pd.DataFrame
        월말 종가 데이터
    forecast_months : int
        예측 개월 수
    optimize : bool
        파라미터 최적화 여부
    period : int
        계절성 주기

    Returns:
    --------
    Dict or None
        예측 결과
    """
    try:
        # 1. 로그 변환
        log_prices = np.log(df_monthly['close_price'])

        # 2. 최적 파라미터 탐색
        if optimize:
            theta, method, deseasonalize, best_aic = find_best_theta_params(
                log_prices,
                period=period
            )
        else:
            # 기본 파라미터
            theta = 2.0
            method = 'additive'
            deseasonalize = True
            best_aic = None

        # 3. Theta 모델 학습
        model = ThetaModel(
            log_prices,
            period=period,
            deseasonalize=deseasonalize,
            method=method
        )

        fitted_model = model.fit()

        # 4. 예측 (로그 스케일)
        forecast_log = fitted_model.forecast(steps=forecast_months)

        # 5. 로그 역변환 (원래 가격으로)
        forecast_prices = np.exp(forecast_log)

        # 6. 예측 날짜 생성
        last_date = df_monthly.index[-1]
        forecast_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=1),
            periods=forecast_months,
            freq='M'
        )

        # 7. 결과 정리
        result = {
            'ticker': ticker,
            'current_price': df_monthly['close_price'].iloc[-1],
            'current_date': last_date,
            'forecast_dates': forecast_dates,
            'forecast_prices': forecast_prices.values,
            'theta': theta,
            'method': method,
            'deseasonalize': deseasonalize,
            'aic': best_aic,
            'n_months': len(df_monthly)
        }

        return result

    except Exception as e:
        print(f"Error in Theta forecast for {ticker}: {str(e)}")
        return None


def save_forecast_to_db(
        forecast_results: List[Dict],
        connection,
        table_name: str = 'us_stock_price_forecast_result'
) -> int:
    """
    예측 결과를 DB에 저장
    - 같은 날짜(forecast_at 기준)에 예측한 데이터는 덮어쓰기
    - 다른 날짜에 예측한 데이터는 추가

    Parameters:
    -----------
    forecast_results : List[Dict]
        예측 결과 리스트
    connection : pymysql.Connection
        DB 연결 객체
    table_name : str
        저장할 테이블명

    Returns:
    --------
    int
        저장된 레코드 수
    """
    try:
        # 테이블 생성 (없는 경우)
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            date DATE NOT NULL COMMENT '예측 대상 날짜',
            ticker VARCHAR(20) NOT NULL COMMENT '종목 티커',
            item VARCHAR(50) NOT NULL COMMENT '예측 모델명',
            value DOUBLE COMMENT '예측 주가',
            forecast_at DATE NOT NULL COMMENT '예측 수행 날짜',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_forecast (date, ticker, item, forecast_at),
            INDEX idx_ticker (ticker),
            INDEX idx_date (date),
            INDEX idx_item (item),
            INDEX idx_forecast_at (forecast_at)
        ) COMMENT='주가 예측 결과 테이블'
        """

        with connection.cursor() as cursor:
            cursor.execute(create_table_query)

        # 현재 예측 수행 날짜 (오늘 날짜)
        forecast_at = datetime.now().date()

        # 기존 같은 날 예측 데이터 삭제 (덮어쓰기 위해)
        delete_query = f"""
        DELETE FROM {table_name}
        WHERE ticker = %s 
          AND item = 'theta'
          AND forecast_at = %s
        """

        # 데이터 삽입
        insert_query = f"""
        INSERT INTO {table_name}
        (date, ticker, item, value, forecast_at)
        VALUES (%s, %s, %s, %s, %s)
        """

        records_inserted = 0

        with connection.cursor() as cursor:
            for result in forecast_results:
                ticker = result['ticker']

                # 해당 티커의 오늘 날짜 Theta 예측 데이터 삭제 (덮어쓰기)
                cursor.execute(delete_query, (ticker, forecast_at))

                # 새로운 예측 데이터 삽입
                for date, price in zip(result['forecast_dates'], result['forecast_prices']):
                    cursor.execute(insert_query, (
                        date.date(),
                        ticker,
                        'theta',
                        float(price),
                        forecast_at
                    ))
                    records_inserted += 1

        connection.commit()
        return records_inserted

    except Exception as e:
        connection.rollback()
        print(f"Error saving to DB: {str(e)}")
        return 0


def process_tickers_batch(
        tickers: List[str],
        forecast_months: int = 6,
        batch_size: int = 20,
        optimize_params: bool = True,
        include_current_month: bool = True
) -> Tuple[int, int, List[str]]:
    """
    배치 단위로 티커들의 주가 예측 및 저장

    Parameters:
    -----------
    tickers : List[str]
        예측할 티커 리스트
    forecast_months : int
        예측 개월 수
    batch_size : int
        배치 크기
    optimize_params : bool
        파라미터 최적화 여부
    include_current_month : bool
        현재 진행 중인 월의 최신 데이터 포함 여부

    Returns:
    --------
    Tuple[int, int, List[str]]
        (성공 개수, 실패 개수, 실패 티커 리스트)
    """
    connection = pymysql.connect(**DB_CONFIG, charset='utf8mb4')

    success_count = 0
    fail_count = 0
    failed_tickers = []

    forecast_batch = []

    try:
        for idx, ticker in enumerate(tickers, 1):
            print(f"[{idx}/{len(tickers)}] Processing {ticker}...", end=' ')

            try:
                # 월말 종가 데이터 추출 (현재월 포함)
                df_monthly = get_monthly_close_price(
                    ticker,
                    connection,
                    include_current_month=include_current_month
                )

                if df_monthly is None:
                    print("Insufficient data")
                    fail_count += 1
                    failed_tickers.append(ticker)
                    continue

                # Theta 예측
                forecast_result = forecast_theta_optimized(
                    ticker,
                    df_monthly,
                    forecast_months,
                    optimize=optimize_params
                )

                if forecast_result is None:
                    print("Forecast failed")
                    fail_count += 1
                    failed_tickers.append(ticker)
                    continue

                # 배치에 추가
                forecast_batch.append(forecast_result)

                # 파라미터 정보 출력
                deseason_str = 'DS' if forecast_result['deseasonalize'] else 'NoDS'
                method_str = 'Add' if forecast_result['method'] == 'additive' else 'Mul'

                print(f"OK (Current: ${forecast_result['current_price']:.2f}, " +
                      f"6M Forecast: ${forecast_result['forecast_prices'][-1]:.2f}, " +
                      f"Theta={forecast_result['theta']}, {method_str}/{deseason_str})")

                success_count += 1

                # 배치 크기에 도달하면 DB에 저장
                if len(forecast_batch) >= batch_size:
                    records = save_forecast_to_db(forecast_batch, connection)
                    print(f"  --> Saved {records} records to DB (batch of {len(forecast_batch)} tickers)")
                    forecast_batch = []

            except Exception as e:
                print(f"Error: {str(e)}")
                fail_count += 1
                failed_tickers.append(ticker)
                continue

        # 남은 배치 저장
        if forecast_batch:
            records = save_forecast_to_db(forecast_batch, connection)
            print(f"  --> Saved {records} records to DB (final batch of {len(forecast_batch)} tickers)")

        return success_count, fail_count, failed_tickers

    finally:
        connection.close()


def get_forecast_summary(ticker: str = None, forecast_date: str = None) -> pd.DataFrame:
    """
    저장된 예측 결과 조회

    Parameters:
    -----------
    ticker : str, optional
        특정 티커의 결과만 조회
    forecast_date : str, optional
        특정 예측 날짜의 결과만 조회 (YYYY-MM-DD 형식)

    Returns:
    --------
    pd.DataFrame
        예측 결과
    """
    try:
        connection = pymysql.connect(**DB_CONFIG, charset='utf8mb4')

        if ticker and forecast_date:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE ticker = %s \
                      AND item = 'theta' \
                      AND forecast_at = %s
                    ORDER BY date \
                    """
            df = pd.read_sql(query, connection, params=(ticker, forecast_date))
        elif ticker:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE ticker = %s AND item = 'theta'
                    ORDER BY forecast_at DESC, date \
                    """
            df = pd.read_sql(query, connection, params=(ticker,))
        elif forecast_date:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE item = 'theta' AND forecast_at = %s
                    ORDER BY ticker, date \
                    """
            df = pd.read_sql(query, connection, params=(forecast_date,))
        else:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE item = 'theta'
                    ORDER BY forecast_at DESC, ticker, date
                        LIMIT 100 \
                    """
            df = pd.read_sql(query, connection)

        connection.close()
        return df

    except Exception as e:
        print(f"Error getting forecast summary: {str(e)}")
        return pd.DataFrame()


def get_latest_forecasts_by_ticker() -> pd.DataFrame:
    """
    각 티커별 가장 최근 예측 결과 조회

    Returns:
    --------
    pd.DataFrame
        티커별 최근 예측 결과
    """
    try:
        connection = pymysql.connect(**DB_CONFIG, charset='utf8mb4')

        query = """
                SELECT ticker, \
                       MAX(forecast_at)     as latest_forecast_date, \
                       COUNT(DISTINCT date) as forecast_months
                FROM us_stock_price_forecast_result
                WHERE item = 'theta'
                GROUP BY ticker
                ORDER BY latest_forecast_date DESC, ticker \
                """

        df = pd.read_sql(query, connection)
        connection.close()

        return df

    except Exception as e:
        print(f"Error getting latest forecasts: {str(e)}")
        return pd.DataFrame()