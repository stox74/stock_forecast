"""
SARIMA 기반 미국 주식 월말 주가 예측 모듈
"""

import pandas as pd
import numpy as np
import pymysql
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import warnings

warnings.filterwarnings('ignore')

from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from statsmodels.tsa.stattools import adfuller
import itertools

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

                print(f"  [현재월 포함] {last_date.strftime('%Y-%m-%d')} 주가를 " +
                      f"{current_month_end.strftime('%Y-%m-%d')} 월말로 사용")

        # 결측치 제거
        df_monthly = df_monthly.dropna()

        # 최소 36개월 데이터 필요 (3년)
        if len(df_monthly) < 36:
            return None

        return df_monthly

    except Exception as e:
        print(f"Error getting monthly data for {ticker}: {str(e)}")
        return None


def check_stationarity(series: pd.Series) -> Dict:
    """
    시계열 정상성 검정 (ADF Test)

    Parameters:
    -----------
    series : pd.Series
        검정할 시계열 데이터

    Returns:
    --------
    Dict
        검정 결과
    """
    try:
        result = adfuller(series.dropna())
        return {
            'adf_statistic': result[0],
            'p_value': result[1],
            'is_stationary': result[1] < 0.05
        }
    except:
        return None


def find_best_sarima_params(
        log_prices: pd.Series,
        p_range: range = range(0, 3),
        d_range: range = range(0, 2),
        q_range: range = range(0, 3),
        P_range: range = range(0, 3),
        D_range: range = range(0, 2),
        Q_range: range = range(0, 3),
        m: int = 12
) -> Tuple[Tuple, Tuple, float]:
    """
    AIC 최소화를 통한 최적 SARIMA 파라미터 탐색

    Parameters:
    -----------
    log_prices : pd.Series
        로그 변환된 주가 데이터
    p_range, d_range, q_range : range
        ARIMA 파라미터 범위
    P_range, D_range, Q_range : range
        Seasonal ARIMA 파라미터 범위
    m : int
        계절성 주기 (기본값: 12)

    Returns:
    --------
    Tuple[Tuple, Tuple, float]
        ((p,d,q), (P,D,Q,m), best_aic)
    """
    best_aic = np.inf
    best_order = None
    best_seasonal_order = None

    # 파라미터 조합 생성
    pdq = list(itertools.product(p_range, d_range, q_range))
    seasonal_pdq = list(itertools.product(P_range, D_range, Q_range, [m]))

    # 최대 조합 수 제한 (계산 시간 단축)
    max_combinations = 50
    total_combinations = len(pdq) * len(seasonal_pdq)

    if total_combinations > max_combinations:
        # 랜덤 샘플링
        import random
        pdq = random.sample(pdq, min(len(pdq), 10))
        seasonal_pdq = random.sample(seasonal_pdq, min(len(seasonal_pdq), 5))

    for param in pdq:
        for param_seasonal in seasonal_pdq:
            try:
                model = SARIMAX(
                    log_prices,
                    order=param,
                    seasonal_order=param_seasonal,
                    enforce_stationarity=False,
                    enforce_invertibility=False
                )

                results = model.fit(disp=False, maxiter=100)

                if results.aic < best_aic:
                    best_aic = results.aic
                    best_order = param
                    best_seasonal_order = param_seasonal

            except:
                continue

    return best_order, best_seasonal_order, best_aic


def forecast_sarima_optimized(
        ticker: str,
        df_monthly: pd.DataFrame,
        forecast_months: int = 6,
        optimize: bool = True
) -> Optional[Dict]:
    """
    SARIMA 모델을 사용한 주가 예측 (최적화 포함)

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

    Returns:
    --------
    Dict or None
        예측 결과
    """
    try:
        # 1. 로그 변환
        log_prices = np.log(df_monthly['close_price'])

        # 2. 계절성 차분 (12개월)
        log_prices_diff = log_prices.diff(12).dropna()

        # 3. 정상성 검정
        stationarity = check_stationarity(log_prices_diff)

        # 4. 최적 파라미터 탐색
        if optimize:
            best_order, best_seasonal_order, best_aic = find_best_sarima_params(
                log_prices,
                p_range=range(0, 3),
                d_range=range(0, 2),
                q_range=range(0, 3),
                P_range=range(0, 2),
                D_range=range(0, 2),
                Q_range=range(0, 2),
                m=12
            )

            if best_order is None:
                # 최적화 실패 시 기본값 사용
                best_order = (1, 1, 1)
                best_seasonal_order = (1, 1, 1, 12)
        else:
            # 기본 파라미터
            best_order = (1, 1, 1)
            best_seasonal_order = (1, 1, 1, 12)

        # 5. SARIMA 모델 학습
        model = SARIMAX(
            log_prices,
            order=best_order,
            seasonal_order=best_seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        fitted_model = model.fit(disp=False, maxiter=200)

        # 6. 예측 (로그 스케일)
        forecast_log = fitted_model.forecast(steps=forecast_months)

        # 7. 로그 역변환 (원래 가격으로)
        forecast_prices = np.exp(forecast_log)

        # 8. 예측 날짜 생성
        last_date = df_monthly.index[-1]
        forecast_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=1),
            periods=forecast_months,
            freq='M'
        )

        # 9. 결과 정리
        result = {
            'ticker': ticker,
            'current_price': df_monthly['close_price'].iloc[-1],
            'current_date': last_date,
            'forecast_dates': forecast_dates,
            'forecast_prices': forecast_prices.values,
            'order': best_order,
            'seasonal_order': best_seasonal_order,
            'aic': fitted_model.aic if hasattr(fitted_model, 'aic') else None,
            'is_stationary': stationarity['is_stationary'] if stationarity else None,
            'n_months': len(df_monthly)
        }

        return result

    except Exception as e:
        print(f"Error in SARIMA forecast for {ticker}: {str(e)}")
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
          AND item = 'sarima'
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

                # 해당 티커의 오늘 날짜 예측 데이터 삭제 (덮어쓰기)
                cursor.execute(delete_query, (ticker, forecast_at))

                # 새로운 예측 데이터 삽입
                for date, price in zip(result['forecast_dates'], result['forecast_prices']):
                    cursor.execute(insert_query, (
                        date.date(),
                        ticker,
                        'sarima',
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

                # SARIMA 예측
                forecast_result = forecast_sarima_optimized(
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

                print(f"OK (Current: ${forecast_result['current_price']:.2f}, " +
                      f"6M Forecast: ${forecast_result['forecast_prices'][-1]:.2f}, " +
                      f"Order: {forecast_result['order']})")

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
                      AND item = 'sarima' \
                      AND forecast_at = %s
                    ORDER BY date \
                    """
            df = pd.read_sql(query, connection, params=(ticker, forecast_date))
        elif ticker:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE ticker = %s AND item = 'sarima'
                    ORDER BY forecast_at DESC, date \
                    """
            df = pd.read_sql(query, connection, params=(ticker,))
        elif forecast_date:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE item = 'sarima' AND forecast_at = %s
                    ORDER BY ticker, date \
                    """
            df = pd.read_sql(query, connection, params=(forecast_date,))
        else:
            query = """
                    SELECT date, ticker, item, value, forecast_at
                    FROM us_stock_price_forecast_result
                    WHERE item = 'sarima'
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
                WHERE item = 'sarima'
                GROUP BY ticker
                ORDER BY latest_forecast_date DESC, ticker \
                """

        df = pd.read_sql(query, connection)
        connection.close()

        return df

    except Exception as e:
        print(f"Error getting latest forecasts: {str(e)}")
        return pd.DataFrame()