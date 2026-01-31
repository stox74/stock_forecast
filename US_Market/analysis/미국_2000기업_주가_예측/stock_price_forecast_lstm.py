"""
LSTM 기반 미국 주식 월말 주가 예측 시스템
GPU 가속, 파라미터 최적화, 배치 처리 지원
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pymysql
import warnings
from sklearn.preprocessing import MinMaxScaler
import json
from typing import List, Tuple, Dict, Optional

warnings.filterwarnings('ignore')

# TensorFlow/Keras GPU 설정
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping

# GPU 메모리 증가 허용
gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
    except RuntimeError as e:
        print(f"GPU 설정 오류: {e}")


# ===========================
# DB 호스트 설정
# ===========================
from DATA.stock_invest_function import get_db_host


# ===========================
# 설정
# ===========================
# DB 연결 정보
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

# 기본 LSTM 파라미터
DEFAULT_LSTM_CONFIG = {
    'seq_length': 24,
    'lstm_units': [128, 64],
    'dropout': 0.2,
    'learning_rate': 0.001,
    'epochs': 50,
    'batch_size': 32
}

# 최적화 시 시도할 파라미터 조합
OPTIMIZATION_CONFIGS = [
    {'seq_length': 12, 'lstm_units': [64], 'dropout': 0.2, 'learning_rate': 0.001},
    {'seq_length': 24, 'lstm_units': [128, 64], 'dropout': 0.2, 'learning_rate': 0.001},
    {'seq_length': 24, 'lstm_units': [128, 64], 'dropout': 0.2, 'learning_rate': 0.0005},
    {'seq_length': 36, 'lstm_units': [128, 64, 32], 'dropout': 0.2, 'learning_rate': 0.001},
    {'seq_length': 12, 'lstm_units': [128, 64], 'dropout': 0.2, 'learning_rate': 0.001},
    {'seq_length': 36, 'lstm_units': [64], 'dropout': 0.2, 'learning_rate': 0.0005},
]


# ===========================
# DB에서 데이터 가져오기
# ===========================
def get_monthly_close_price(
        ticker: str,
        include_current_month: bool = True
) -> Optional[pd.DataFrame]:
    """
    특정 티커의 월말 종가 데이터 추출 (DB에서)

    Parameters:
    -----------
    ticker : str
        종목 티커
    include_current_month : bool
        현재 진행 중인 월의 최신 데이터 포함 여부 (기본값: True)

    Returns:
    --------
    pd.DataFrame or None
        월말 종가 데이터 (컬럼: date, price)
    """
    connection = None
    try:
        # DB 연결
        connection = pymysql.connect(**DB_CONFIG)

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

        # 최소 36개월 데이터 필요 (3년)
        if len(df_monthly) < 36:
            return None

        # DataFrame 형태 변환 (date를 컬럼으로)
        df_monthly = df_monthly.reset_index()
        df_monthly.columns = ['date', 'price']

        return df_monthly

    except pymysql.Error as e:
        print(f"DB 연결/조회 오류 ({ticker}): {e}")
        return None
    except Exception as e:
        print(f"데이터 처리 오류 ({ticker}): {e}")
        return None
    finally:
        if connection:
            connection.close()


def get_current_price(ticker: str) -> Optional[float]:
    """
    DB에서 현재 주가 가져오기 (가장 최근 종가)
    """
    try:
        connection = pymysql.connect(**DB_CONFIG)

        query = """
                SELECT value as close_price
                FROM us_stock_daily_market_cap
                WHERE ticker = %s
                  AND indicator = 'close_price'
                ORDER BY date DESC
                    LIMIT 1 \
                """

        df = pd.read_sql(query, connection, params=(ticker,))
        connection.close()

        if not df.empty:
            return float(df['close_price'].iloc[0])

    except Exception as e:
        print(f"현재가 조회 오류 ({ticker}): {e}")

    return None


# ===========================
# LSTM 데이터 준비
# ===========================
def prepare_lstm_data(data: pd.DataFrame, seq_length: int,
                      train_ratio: float = 0.8) -> Tuple:
    """
    LSTM 학습을 위한 데이터 준비

    Args:
        data: 가격 데이터 (컬럼: price)
        seq_length: 시퀀스 길이 (입력 시점 수)
        train_ratio: 학습 데이터 비율

    Returns:
        X_train, X_val, y_train, y_val, scaler, scaled_data
    """
    scaler = MinMaxScaler()
    scaled_data = scaler.fit_transform(data[['price']].values)

    X, y = [], []
    for i in range(len(scaled_data) - seq_length):
        X.append(scaled_data[i:i + seq_length])
        y.append(scaled_data[i + seq_length])

    X = np.array(X)
    y = np.array(y)

    split_idx = int(len(X) * train_ratio)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    return X_train, X_val, y_train, y_val, scaler, scaled_data


# ===========================
# LSTM 모델 생성
# ===========================
def create_lstm_model(seq_length: int, lstm_units: List[int],
                      dropout: float, learning_rate: float) -> Sequential:
    """
    LSTM 모델 생성

    Args:
        seq_length: 시퀀스 길이
        lstm_units: LSTM 레이어별 유닛 수 (예: [128, 64])
        dropout: 드롭아웃 비율
        learning_rate: 학습률

    Returns:
        컴파일된 LSTM 모델
    """
    model = Sequential()

    if len(lstm_units) == 1:
        model.add(LSTM(lstm_units[0], input_shape=(seq_length, 1)))
        model.add(Dropout(dropout))
    else:
        # 첫 번째 LSTM 레이어
        model.add(LSTM(lstm_units[0], return_sequences=True, input_shape=(seq_length, 1)))
        model.add(Dropout(dropout))

        # 중간 레이어들
        for units in lstm_units[1:-1]:
            model.add(LSTM(units, return_sequences=True))
            model.add(Dropout(dropout))

        # 마지막 LSTM 레이어
        model.add(LSTM(lstm_units[-1]))
        model.add(Dropout(dropout))

    model.add(Dense(1))

    optimizer = tf.keras.optimizers.Adam(learning_rate=learning_rate)
    model.compile(optimizer=optimizer, loss='mse')

    return model


# ===========================
# LSTM 예측
# ===========================
def forecast_lstm_multi_step(model: Sequential, scaler: MinMaxScaler,
                             last_sequence: np.ndarray,
                             n_months: int) -> List[float]:
    """
    다중 스텝 예측 (순차적 예측)

    Args:
        model: 학습된 LSTM 모델
        scaler: 스케일러
        last_sequence: 마지막 시퀀스 데이터
        n_months: 예측할 개월 수

    Returns:
        예측 가격 리스트
    """
    predictions = []
    current_seq = last_sequence.copy()

    for _ in range(n_months):
        pred = model.predict(current_seq.reshape(1, -1, 1), verbose=0)
        predictions.append(pred[0, 0])
        current_seq = np.append(current_seq[1:], pred[0, 0])

    predictions = scaler.inverse_transform(np.array(predictions).reshape(-1, 1))
    return predictions.flatten().tolist()


# ===========================
# 최적 모델 찾기
# ===========================
def find_best_lstm_model(ticker: str, data: pd.DataFrame,
                         forecast_months: int,
                         optimize: bool = True) -> Tuple[Optional[Dict], Optional[List[float]]]:
    """
    최적의 LSTM 모델 찾기

    Args:
        ticker: 티커 심볼
        data: 월말 가격 데이터
        forecast_months: 예측 개월 수
        optimize: 파라미터 최적화 여부

    Returns:
        (best_config, forecasts) - 최적 설정, 예측값 리스트
    """
    configs_to_try = OPTIMIZATION_CONFIGS if optimize else [DEFAULT_LSTM_CONFIG]

    best_config = None
    best_val_loss = float('inf')
    best_forecasts = None

    for config in configs_to_try:
        try:
            seq_length = config['seq_length']

            # 데이터 준비
            X_train, X_val, y_train, y_val, scaler, scaled_data = prepare_lstm_data(
                data, seq_length
            )

            # 데이터가 충분한지 확인
            if len(X_train) < 20:
                continue

            # 모델 생성
            model = create_lstm_model(
                seq_length=seq_length,
                lstm_units=config['lstm_units'],
                dropout=config['dropout'],
                learning_rate=config['learning_rate']
            )

            # 조기 종료 콜백
            early_stop = EarlyStopping(
                monitor='val_loss',
                patience=5,
                restore_best_weights=True
            )

            # 모델 학습
            model.fit(
                X_train, y_train,
                validation_data=(X_val, y_val),
                epochs=config.get('epochs', 50),
                batch_size=config.get('batch_size', 32),
                callbacks=[early_stop],
                verbose=0
            )

            # 검증 손실 평가
            val_loss = model.evaluate(X_val, y_val, verbose=0)

            # 최적 모델 업데이트
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_config = config.copy()
                best_config['val_loss'] = val_loss

                # 예측 수행
                last_seq = scaled_data[-seq_length:]
                forecasts = forecast_lstm_multi_step(
                    model, scaler, last_seq, forecast_months
                )
                best_forecasts = forecasts

            # 메모리 정리
            del model
            tf.keras.backend.clear_session()

        except Exception as e:
            continue

    return best_config, best_forecasts


# ===========================
# 데이터베이스 함수들
# ===========================
def save_forecasts_to_db(ticker: str, forecast_date: str,
                         forecasts: List[float],
                         model_params: Dict,
                         include_current_month: bool = True) -> int:
    """
    예측 결과를 데이터베이스에 저장

    Args:
        ticker: 티커 심볼
        forecast_date: 예측 생성일 (YYYY-MM-DD)
        forecasts: 월별 예측 가격 리스트
        model_params: 모델 파라미터
        include_current_month: 현재 월 포함 여부

    Returns:
        저장된 행 수
    """
    if not forecasts:
        return 0

    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()

        # 테이블 생성 (없는 경우)
        create_table_sql = """
                           CREATE TABLE IF NOT EXISTS us_stock_price_forecast_result \
                           ( \
                               id \
                               INT \
                               AUTO_INCREMENT \
                               PRIMARY \
                               KEY, \
                               date \
                               DATE \
                               NOT \
                               NULL \
                               COMMENT \
                               '예측 목표일 (월말)', \
                               ticker \
                               VARCHAR \
                           ( \
                               20 \
                           ) NOT NULL COMMENT '티커',
                               item VARCHAR \
                           ( \
                               100 \
                           ) NOT NULL COMMENT '예측 모델명',
                               value DECIMAL \
                           ( \
                               15, \
                               4 \
                           ) COMMENT '예측 주가',
                               forecast_at DATE NOT NULL COMMENT '예측 생성일',
                               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
                               UNIQUE KEY unique_forecast \
                           ( \
                               ticker, \
                               date, \
                               item, \
                               forecast_at \
                           ),
                               INDEX idx_ticker_date \
                           ( \
                               ticker, \
                               date \
                           ),
                               INDEX idx_forecast_at \
                           ( \
                               forecast_at \
                           )
                               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='미국 주식 LSTM 예측 결과' \
                           """
        cursor.execute(create_table_sql)

        # 데이터 준비
        base_date = datetime.strptime(forecast_date, '%Y-%m-%d')

        # 현재 월 포함 여부에 따라 시작 오프셋 결정
        if include_current_month:
            # 현재 진행 중인 월의 말일부터 시작
            start_offset = 0
        else:
            # 다음 달 말일부터 시작
            start_offset = 1

        # item 이름 생성 (LSTM 파라미터 포함)
        seq_length = model_params.get('seq_length', 24)
        lstm_units = model_params.get('lstm_units', [128, 64])
        units_str = '_'.join(map(str, lstm_units))
        item_name = f"lstm_seq{seq_length}_units{units_str}"

        records = []
        for i, price in enumerate(forecasts):
            # i개월 후의 월말일 계산
            months_ahead = start_offset + i
            future_date = base_date.replace(day=1)

            for _ in range(months_ahead + 1):
                future_date = future_date + timedelta(days=32)
                future_date = future_date.replace(day=1)

            future_date = future_date - timedelta(days=1)

            records.append({
                'date': future_date.strftime('%Y-%m-%d'),
                'ticker': ticker,
                'item': item_name,
                'value': float(price),
                'forecast_at': forecast_date
            })

        # 예측값 저장
        sql = """
              INSERT INTO us_stock_price_forecast_result
                  (date, ticker, item, value, forecast_at)
              VALUES (%(date)s, %(ticker)s, %(item)s, %(value)s, %(forecast_at)s) ON DUPLICATE KEY \
              UPDATE \
                  value = \
              VALUES (value), created_at = CURRENT_TIMESTAMP \
              """

        cursor.executemany(sql, records)
        rows_affected = cursor.rowcount

        # 파라미터 저장 (별도 레코드)
        param_item_name = f"{item_name}_params"
        param_record = {
            'date': base_date.strftime('%Y-%m-%d'),
            'ticker': ticker,
            'item': param_item_name,
            'value': model_params.get('val_loss', 0),  # validation loss를 value로 저장
            'forecast_at': forecast_date
        }

        param_sql = """
                    INSERT INTO us_stock_price_forecast_result
                        (date, ticker, item, value, forecast_at)
                    VALUES (%(date)s, %(ticker)s, %(item)s, %(value)s, %(forecast_at)s) ON DUPLICATE KEY \
                    UPDATE \
                        value = \
                    VALUES (value), created_at = CURRENT_TIMESTAMP \
                    """

        cursor.execute(param_sql, param_record)

        connection.commit()

        cursor.close()

        return rows_affected

    except Exception as e:
        print(f"DB 저장 오류 ({ticker}): {e}")
        if connection:
            connection.rollback()
        return 0
    finally:
        if connection:
            connection.close()


def get_forecast_summary(ticker: Optional[str] = None,
                         forecast_date: Optional[str] = None) -> pd.DataFrame:
    """
    예측 결과 조회

    Args:
        ticker: 티커 (None이면 전체)
        forecast_date: 예측일 (None이면 최근)

    Returns:
        예측 결과 DataFrame
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        sql = """
              SELECT date, ticker, item, value, forecast_at, created_at
              FROM us_stock_price_forecast_result
              WHERE 1=1 \
              """
        params = []

        if ticker:
            sql += " AND ticker = %s"
            params.append(ticker)

        if forecast_date:
            sql += " AND forecast_at = %s"
            params.append(forecast_date)

        # params가 아닌 예측값만 조회 (item에 'params'가 없는 것만)
        sql += " AND item NOT LIKE '%_params'"

        sql += " ORDER BY ticker, date"

        df = pd.read_sql(sql, connection, params=params if params else None)

        return df

    except Exception as e:
        print(f"예측 조회 오류: {e}")
        return pd.DataFrame()
    finally:
        if connection:
            connection.close()


def get_latest_forecasts_by_ticker() -> pd.DataFrame:
    """
    티커별 최신 예측 결과 조회

    Returns:
        DataFrame: ticker, latest_forecast_at, forecast_count
    """
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)

        sql = """
              SELECT ticker, \
                     MAX(forecast_at)     as latest_forecast_at, \
                     COUNT(DISTINCT date) as forecast_count
              FROM us_stock_price_forecast_result
              WHERE item NOT LIKE '%_params'
              GROUP BY ticker
              ORDER BY ticker \
              """

        df = pd.read_sql(sql, connection)

        return df

    except Exception as e:
        print(f"최신 예측 조회 오류: {e}")
        return pd.DataFrame()
    finally:
        if connection:
            connection.close()


# ===========================
# 배치 처리 함수
# ===========================
def process_tickers_batch(tickers: List[str],
                          forecast_months: int = 6,
                          batch_size: int = 10,
                          optimize_params: bool = True,
                          include_current_month: bool = True) -> Tuple[int, int, List[str]]:
    """
    여러 티커를 배치로 처리

    Args:
        tickers: 티커 리스트
        forecast_months: 예측 개월 수
        batch_size: 배치 크기 (DB 저장 주기)
        optimize_params: 파라미터 최적화 여부
        include_current_month: 현재 월 포함 여부

    Returns:
        (성공 개수, 실패 개수, 실패 티커 리스트)
    """
    total = len(tickers)
    success_count = 0
    fail_count = 0
    failed_tickers = []

    forecast_date = datetime.now().strftime('%Y-%m-%d')

    for idx, ticker in enumerate(tickers, 1):
        try:
            # DB에서 데이터 가져오기
            df = get_monthly_close_price(ticker, include_current_month=include_current_month)

            if df is None or len(df) < 36:
                print(f"[{idx}/{total}] {ticker}: SKIP (데이터 부족 - {len(df) if df is not None else 0}개월)")
                fail_count += 1
                failed_tickers.append(ticker)
                continue

            # 최적 모델 찾기 및 예측
            best_config, forecasts = find_best_lstm_model(
                ticker, df, forecast_months, optimize=optimize_params
            )

            if best_config is None or forecasts is None:
                print(f"[{idx}/{total}] {ticker}: FAIL (모델 학습 실패)")
                fail_count += 1
                failed_tickers.append(ticker)
                continue

            # DB 저장
            rows = save_forecasts_to_db(
                ticker, forecast_date, forecasts, best_config,
                include_current_month=include_current_month
            )

            if rows > 0:
                current_price = df['price'].iloc[-1]
                forecast_6m = forecasts[-1] if len(forecasts) >= 6 else forecasts[-1]

                print(f"[{idx}/{total}] {ticker}: OK "
                      f"(현재가: ${current_price:.2f}, "
                      f"6개월 예측: ${forecast_6m:.2f}, "
                      f"val_loss: {best_config.get('val_loss', 0):.6f})")
                success_count += 1
            else:
                print(f"[{idx}/{total}] {ticker}: FAIL (DB 저장 실패)")
                fail_count += 1
                failed_tickers.append(ticker)

        except Exception as e:
            print(f"[{idx}/{total}] {ticker}: ERROR ({str(e)})")
            fail_count += 1
            failed_tickers.append(ticker)
            continue

    return success_count, fail_count, failed_tickers


# ===========================
# 메인 실행 (독립 실행용)
# ===========================
def main():
    """
    독립 실행 시 사용 (테스트용)
    """
    print("=" * 80)
    print("LSTM 주가 예측 모듈 - 독립 실행 모드")
    print("=" * 80)

    # 테스트 티커
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']

    print(f"\n테스트 티커: {test_tickers}")
    print(f"예측 개월: 6개월")

    confirm = input("\n실행하시겠습니까? (y/n): ").strip().lower()
    if confirm != 'y':
        print("취소되었습니다.")
        return

    success, fail, failed = process_tickers_batch(
        tickers=test_tickers,
        forecast_months=6,
        batch_size=5,
        optimize_params=True,
        include_current_month=True
    )

    print("\n" + "=" * 80)
    print("처리 완료")
    print("=" * 80)
    print(f"성공: {success}개")
    print(f"실패: {fail}개")

    if failed:
        print(f"\n실패한 티커: {', '.join(failed)}")


if __name__ == "__main__":
    main()