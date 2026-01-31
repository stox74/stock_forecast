import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pymysql
import configparser
import warnings
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping
import requests

warnings.filterwarnings('ignore')

# ===========================
# 설정 로드
# ===========================
config = configparser.ConfigParser()
config.read('config.ini')

DB_CONFIG = {
    'host': config.get('database', 'host'),
    'user': config.get('database', 'user'),
    'password': config.get('database', 'password'),
    'database': config.get('database', 'database'),
    'charset': 'utf8mb4'
}

FMP_API_KEY = config.get('api', 'fmp_key')

# LSTM 하이퍼파라미터 조합
LSTM_CONFIGS = [
    {'seq_length': 12, 'units': [128, 64], 'dropout': 0.2, 'epochs': 50},
    {'seq_length': 24, 'units': [64], 'dropout': 0.2, 'epochs': 50},
    {'seq_length': 36, 'units': [128, 64, 32], 'dropout': 0.2, 'epochs': 50}
]

FORECAST_MONTHS = 6
BATCH_SIZE = 10


# ===========================
# FMP API: 티커 리스트 가져오기
# ===========================
def get_ticker_list(limit=2000):
    url = f"https://financialmodelingprep.com/api/v3/stock/list?apikey={FMP_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        tickers = [item['symbol'] for item in data[:limit] if item['exchangeShortName'] in ['NASDAQ', 'NYSE']]
        return tickers
    else:
        print(f"FMP API Error: {response.status_code}")
        return []


# ===========================
# FMP API: 일별 주가 데이터 가져오기
# ===========================
def get_stock_data_fmp(ticker, years=3):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)

    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}?from={start_date.strftime('%Y-%m-%d')}&to={end_date.strftime('%Y-%m-%d')}&apikey={FMP_API_KEY}"

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if 'historical' in data and len(data['historical']) > 0:
            df = pd.DataFrame(data['historical'])
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            df = df[['date', 'close']].rename(columns={'close': 'price'})
            return df
    return None


# ===========================
# LSTM 데이터 준비
# ===========================
def prepare_lstm_data(data, seq_length):
    scaler = MinMaxScaler()
    scaled_data = scaler.fit_transform(data[['price']].values)

    X, y = [], []
    for i in range(len(scaled_data) - seq_length):
        X.append(scaled_data[i:i + seq_length])
        y.append(scaled_data[i + seq_length])

    X = np.array(X)
    y = np.array(y)

    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    return X_train, X_val, y_train, y_val, scaler, scaled_data


# ===========================
# LSTM 모델 생성
# ===========================
def create_lstm_model(seq_length, units, dropout):
    model = Sequential()

    if len(units) == 1:
        model.add(LSTM(units[0], input_shape=(seq_length, 1)))
        model.add(Dropout(dropout))
    else:
        model.add(LSTM(units[0], return_sequences=True, input_shape=(seq_length, 1)))
        model.add(Dropout(dropout))

        for unit in units[1:-1]:
            model.add(LSTM(unit, return_sequences=True))
            model.add(Dropout(dropout))

        model.add(LSTM(units[-1]))
        model.add(Dropout(dropout))

    model.add(Dense(1))
    model.compile(optimizer='adam', loss='mse')

    return model


# ===========================
# LSTM 예측 (6개월 선행)
# ===========================
def forecast_lstm(model, scaler, last_sequence, forecast_days):
    predictions = []
    current_seq = last_sequence.copy()

    for _ in range(forecast_days):
        pred = model.predict(current_seq.reshape(1, -1, 1), verbose=0)
        predictions.append(pred[0, 0])
        current_seq = np.append(current_seq[1:], pred[0, 0])

    predictions = scaler.inverse_transform(np.array(predictions).reshape(-1, 1))
    return predictions[-1][0]


# ===========================
# 최적 LSTM 모델 찾기
# ===========================
def find_best_lstm_model(ticker, data):
    best_config = None
    best_loss = float('inf')
    best_forecast = None

    for config in LSTM_CONFIGS:
        try:
            X_train, X_val, y_train, y_val, scaler, scaled_data = prepare_lstm_data(data, config['seq_length'])

            if len(X_train) < 20:
                continue

            model = create_lstm_model(config['seq_length'], config['units'], config['dropout'])

            early_stop = EarlyStopping(monitor='val_loss', patience=5, restore_best_weights=True)

            model.fit(X_train, y_train,
                      validation_data=(X_val, y_val),
                      epochs=config['epochs'],
                      batch_size=32,
                      callbacks=[early_stop],
                      verbose=0)

            val_loss = model.evaluate(X_val, y_val, verbose=0)

            if val_loss < best_loss:
                best_loss = val_loss
                best_config = config

                last_seq = scaled_data[-config['seq_length']:]
                forecast_price = forecast_lstm(model, scaler, last_seq, FORECAST_MONTHS * 30)
                best_forecast = forecast_price

        except Exception as e:
            continue

    return best_config, best_forecast


# ===========================
# DB 저장
# ===========================
def save_to_db(records):
    if not records:
        return 0

    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

        sql = """
              INSERT INTO forecast_data (code, date, item, value)
              VALUES (%(code)s, %(date)s, %(item)s, %(value)s) ON DUPLICATE KEY \
              UPDATE value= \
              VALUES (value) \
              """

        cursor.executemany(sql, records)
        conn.commit()

        rows_affected = cursor.rowcount
        cursor.close()
        conn.close()

        return rows_affected

    except Exception as e:
        print(f"Error saving to DB: {e}")
        return 0


# ===========================
# 메인 실행
# ===========================
def main():
    print("=" * 80)
    print("LSTM 주가 예측 시스템 (FMP API)")
    print("=" * 80)

    confirm = input("예측을 시작하시겠습니까? (y/n): ").strip().lower()
    if confirm != 'y':
        print("예측이 취소되었습니다.")
        return

    print("=" * 80)
    print("예측 시작...")
    print("=" * 80)

    tickers = get_ticker_list(limit=2000)
    if not tickers:
        print("티커 리스트를 가져올 수 없습니다.")
        return

    total = len(tickers)
    insert_data = []
    success_count = 0
    fail_count = 0

    target_date = datetime.now() + timedelta(days=FORECAST_MONTHS * 30)
    target_date_str = target_date.strftime('%Y-%m-%d')

    for idx, ticker in enumerate(tickers, 1):
        try:
            df = get_stock_data_fmp(ticker, years=3)

            if df is None or len(df) < 100:
                print(f"[{idx}/{total}] Processing {ticker}... SKIP (insufficient data)")
                fail_count += 1
                continue

            current_price = df['price'].iloc[-1]

            best_config, forecast_price = find_best_lstm_model(ticker, df)

            if best_config is None or forecast_price is None:
                print(f"[{idx}/{total}] Processing {ticker}... FAIL (no valid model)")
                fail_count += 1
                continue

            # item 필드에 파라미터 통합 (수정된 부분)
            units_str = '_'.join(map(str, best_config['units']))
            item_name = f"lstm_seq{best_config['seq_length']}_units{units_str}"

            insert_data.append({
                'code': ticker,
                'date': target_date_str,
                'item': item_name,
                'value': forecast_price
            })

            print(
                f"[{idx}/{total}] Processing {ticker}... OK (Current: ${current_price:.2f}, 6M Forecast: ${forecast_price:.2f}, seq={best_config['seq_length']}, units={best_config['units']})")
            success_count += 1

            if len(insert_data) >= BATCH_SIZE:
                saved = save_to_db(insert_data)
                print(f"  --> Saved {saved} records to DB (batch of {len(insert_data)} tickers)")
                insert_data = []

        except Exception as e:
            print(f"[{idx}/{total}] Processing {ticker}... ERROR ({str(e)})")
            fail_count += 1
            continue

    if insert_data:
        saved = save_to_db(insert_data)
        print(f"  --> Saved {saved} records to DB (final batch of {len(insert_data)} tickers)")

    print("\n" + "=" * 80)
    print("예측 완료")
    print("=" * 80)
    print(f"총 처리: {total}개")
    print(f"성공: {success_count}개")
    print(f"실패: {fail_count}개")
    print("=" * 80)


if __name__ == "__main__":
    main()