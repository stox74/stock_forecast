import pandas as pd
import numpy as np
import sys
from pathlib import Path
import warnings

warnings.filterwarnings('ignore')

import matplotlib
import matplotlib.pyplot as plt

matplotlib.rc('font', family='Malgun Gothic')
plt.rcParams['axes.unicode_minus'] = False

try:
    current_path = Path(__file__).resolve()
except NameError:
    current_path = Path().resolve()

for parent in current_path.parents:
    if (parent / "stock_forecast" / "DATA").is_dir():
        stock_forecast_path = parent / "stock_forecast"
        break
else:
    raise ImportError("stock_forecast/DATA 폴더를 찾을 수 없습니다.")

if str(stock_forecast_path) not in sys.path:
    sys.path.insert(0, str(stock_forecast_path))

from sklearn.preprocessing import StandardScaler, RobustScaler
from DATA.stock_invest_function import *
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
from tqdm import tqdm
import gc


# ==================== 유틸리티 함수 ====================

def clean_numeric_data(series, method='drop'):
    """숫자 데이터 정제"""
    series = series.replace([np.inf, -np.inf], np.nan)
    if method == 'drop':
        return series.dropna()
    elif method == 'fill_median':
        return series.fillna(series.median())
    elif method == 'fill_mean':
        return series.fillna(series.mean())
    elif method == 'fill_zero':
        return series.fillna(0)
    else:
        return series


def create_db_engine(db_info: dict):
    """데이터베이스 엔진 생성"""
    return create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )


def get_market_cap_by_ticker(db_info: dict, ticker: str) -> pd.DataFrame:
    """티커별 시가총액 데이터 조회"""
    try:
        engine = create_db_engine(db_info)
        query = f"""
        SELECT date, value FROM ks_listed_company_daily_marketcap
        WHERE ticker = '{ticker}' AND indicator = '시가총액'
        ORDER BY date
        """
        df = pd.read_sql(query, con=engine)
        engine.dispose()
        return df
    except Exception as e:
        print(f"시가총액 데이터 조회 실패 ({ticker}): {e}")
        return pd.DataFrame()


def save_valuation_to_db(db_info: dict, table_name: str, df: pd.DataFrame):
    """가치평가 결과를 DB에 저장"""
    try:
        engine = create_db_engine(db_info)
        new_ticker = df['ticker'].iloc[0]

        # 기존 데이터 삭제
        delete_query = f"DELETE FROM {table_name} WHERE ticker = '{new_ticker}'"
        with engine.connect() as conn:
            conn.execute(text(delete_query))
            conn.commit()

        # 새 데이터 추가
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists='append',
            index=False,
            method='multi'
        )
        engine.dispose()

    except Exception as e:
        print(f"DB 저장 실패 ({df['ticker'].iloc[0] if len(df) > 0 else 'Unknown'}): {e}")


# ==================== 데이터 추출 함수 ====================

def extract_revenue_data(db_info: dict, ticker: str, target_indicator: str = '매출액(천원)') -> pd.DataFrame:
    """매출 데이터 추출 및 전처리"""
    try:
        fs_df = fetch_table_data(db_info, "korea_fs_data")
        fs_df.rename(columns={'Date': 'date'}, inplace=True)

        revenue_raw = fs_df[fs_df['indicator'] == target_indicator].copy()
        revenue_company = revenue_raw[revenue_raw['symbol'] == ticker].copy()

        if len(revenue_company) == 0:
            return pd.DataFrame()

        revenue_company['date'] = pd.to_datetime(revenue_company['date'])
        revenue_company['value'] = pd.to_numeric(revenue_company['value'], errors='coerce')
        revenue_company = revenue_company.dropna(subset=['value']).sort_values('date')

        revenue_company['year'] = revenue_company['date'].dt.year
        revenue_company['quarter'] = revenue_company['date'].dt.quarter
        revenue_company['year_quarter'] = revenue_company['year'].astype(str) + 'Q' + revenue_company['quarter'].astype(
            str)

        revenue_quarterly = revenue_company.groupby(['year', 'quarter']).agg({
            'date': 'last',
            'value': 'last',
            'year_quarter': 'last',
            'symbol': 'last'
        }).reset_index()

        revenue_quarterly = revenue_quarterly.sort_values(['year', 'quarter']).reset_index(drop=True)
        revenue_quarterly['revenue'] = clean_numeric_data(revenue_quarterly['value'], method='fill_median')

        del fs_df, revenue_raw, revenue_company
        gc.collect()

        return revenue_quarterly

    except Exception as e:
        print(f"매출 데이터 추출 실패 ({ticker}): {e}")
        return pd.DataFrame()


def extract_export_data(db_info: dict, hs_code: str) -> pd.DataFrame:
    """수출 데이터 추출 및 전처리"""
    if hs_code is None:
        return pd.DataFrame()

    try:
        export_df = fetch_table_data(db_info, "korea_monthly_trade_data_forecast")
        export_company = export_df[export_df['root_hs_code'] == hs_code].copy()

        if len(export_company) == 0:
            return pd.DataFrame()

        export_company['date'] = pd.to_datetime(export_company['date'])
        export_company['expDlr_forecast_12m'] = pd.to_numeric(export_company['expDlr_forecast_12m'], errors='coerce')
        export_company = export_company.dropna(subset=['expDlr_forecast_12m']).sort_values('date')

        export_company['year'] = export_company['date'].dt.year
        export_company['quarter'] = export_company['date'].dt.quarter
        export_company['year_quarter'] = export_company['year'].astype(str) + 'Q' + export_company['quarter'].astype(
            str)
        export_company['month'] = export_company['date'].dt.month

        quarter_end_months = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_check = export_company.groupby(['year', 'quarter']).agg({
            'month': 'max',
            'date': 'count'
        }).reset_index()

        complete_quarters = []
        for _, row in quarter_check.iterrows():
            expected_end_month = quarter_end_months[row['quarter']]
            if row['month'] == expected_end_month:
                complete_quarters.append((row['year'], row['quarter']))

        complete_quarter_filter = export_company.apply(
            lambda x: (x['year'], x['quarter']) in complete_quarters, axis=1
        )
        export_company_filtered = export_company[complete_quarter_filter].copy()

        export_quarterly = export_company_filtered.groupby(['year', 'quarter']).agg({
            'expDlr_forecast_12m': 'sum',
            'date': 'last',
            'year_quarter': 'last',
            'root_hs_code': 'last'
        }).reset_index()

        export_quarterly = export_quarterly.sort_values(['year', 'quarter']).reset_index(drop=True)

        del export_df, export_company, export_company_filtered
        gc.collect()

        return export_quarterly

    except Exception as e:
        print(f"수출 데이터 추출 실패 (HS Code: {hs_code}): {e}")
        return pd.DataFrame()


def calculate_yoy_growth(export_data: pd.DataFrame) -> pd.DataFrame:
    """YoY 성장률 계산"""
    if len(export_data) == 0:
        return pd.DataFrame()

    try:
        export_yoy_df = export_data.copy()
        export_yoy_df['exog_var'] = np.nan

        for i in range(4, len(export_yoy_df)):
            if export_yoy_df.iloc[i - 4]['expDlr_forecast_12m'] != 0:
                yoy_rate = (export_yoy_df.iloc[i]['expDlr_forecast_12m'] /
                            export_yoy_df.iloc[i - 4]['expDlr_forecast_12m'] - 1) * 100
                export_yoy_df.loc[export_yoy_df.index[i], 'exog_var'] = yoy_rate

        return export_yoy_df[['date', 'exog_var']].dropna().copy()

    except Exception as e:
        print(f"YoY 성장률 계산 실패: {e}")
        return pd.DataFrame()


# ==================== 예측 모델 함수 ====================

def sarima_forecast(endog, exog, forecast_periods):
    """SARIMA 모델 예측"""
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        from itertools import product

        p_values = [0, 1, 2]
        d_values = [0, 1]
        q_values = [0, 1, 2]
        P_values = [0, 1]
        D_values = [0, 1]
        Q_values = [0, 1]
        s = 4

        best_aic = np.inf
        best_params = None
        best_model = None

        for p, d, q in product(p_values, d_values, q_values):
            for P, D, Q in product(P_values, D_values, Q_values):
                try:
                    model = SARIMAX(
                        endog,
                        exog=exog,
                        order=(p, d, q),
                        seasonal_order=(P, D, Q, s),
                        enforce_stationarity=False,
                        enforce_invertibility=False
                    )
                    fitted = model.fit(disp=False, maxiter=200)

                    if fitted.aic < best_aic:
                        best_aic = fitted.aic
                        best_params = ((p, d, q), (P, D, Q, s))
                        best_model = fitted
                except:
                    continue

        if best_model is not None:
            forecast_result = best_model.forecast(steps=forecast_periods, exog=exog)
            return forecast_result
        else:
            return None

    except Exception as e:
        print(f"SARIMA 예측 실패: {e}")
        return None


def lstm_forecast(data, forecast_periods, sequence_length=8):
    """LSTM 모델 예측"""
    try:
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout
        from tensorflow.keras.optimizers import Adam
        from tensorflow.keras.callbacks import EarlyStopping

        scaler = StandardScaler()
        data_scaled = scaler.fit_transform(data.reshape(-1, 1)).flatten()

        X, y = [], []
        for i in range(len(data_scaled) - sequence_length):
            X.append(data_scaled[i:i + sequence_length])
            y.append(data_scaled[i + sequence_length])

        X = np.array(X).reshape(-1, sequence_length, 1)
        y = np.array(y)

        model = Sequential([
            LSTM(50, activation='relu', return_sequences=True, input_shape=(sequence_length, 1)),
            Dropout(0.2),
            LSTM(50, activation='relu'),
            Dropout(0.2),
            Dense(1)
        ])

        model.compile(optimizer=Adam(learning_rate=0.001), loss='mse')
        early_stopping = EarlyStopping(monitor='loss', patience=10, restore_best_weights=True)
        model.fit(X, y, epochs=100, batch_size=4, verbose=0, callbacks=[early_stopping])

        predictions = []
        current_sequence = data_scaled[-sequence_length:].tolist()

        for _ in range(forecast_periods):
            current_input = np.array(current_sequence[-sequence_length:]).reshape(1, sequence_length, 1)
            next_pred = model.predict(current_input, verbose=0)[0, 0]
            predictions.append(next_pred)
            current_sequence.append(next_pred)

        predictions_rescaled = scaler.inverse_transform(np.array(predictions).reshape(-1, 1)).flatten()

        del model
        gc.collect()

        return predictions_rescaled

    except Exception as e:
        print(f"LSTM 예측 실패: {e}")
        return None


def prophet_forecast(df, forecast_periods):
    """Prophet 모델 예측"""
    try:
        from prophet import Prophet

        prophet_df = pd.DataFrame({
            'ds': df['date'],
            'y': df['endog_var']
        })

        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            changepoint_prior_scale=0.05
        )
        model.fit(prophet_df)

        last_date = prophet_df['ds'].max()
        future_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=3),
            periods=forecast_periods,
            freq='Q'
        )
        future_df = pd.DataFrame({'ds': future_dates})

        forecast = model.predict(future_df)

        del model
        gc.collect()

        return forecast['yhat'].values

    except Exception as e:
        print(f"Prophet 예측 실패: {e}")
        return None


def exponential_smoothing_forecast(data, forecast_periods):
    """Exponential Smoothing 모델 예측 (추가)"""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        model = ExponentialSmoothing(
            data,
            seasonal_periods=4,
            trend='add',
            seasonal='add',
            initialization_method="estimated"
        )
        fitted = model.fit()
        forecast_result = fitted.forecast(steps=forecast_periods)

        del model, fitted
        gc.collect()

        return forecast_result

    except Exception as e:
        print(f"Exponential Smoothing 예측 실패: {e}")
        return None


def theta_forecast(data, forecast_periods):
    """Theta 모델 예측 (추가)"""
    try:
        from statsmodels.tsa.forecasting.theta import ThetaModel

        model = ThetaModel(data, period=4)
        fitted = model.fit()
        forecast_result = fitted.forecast(steps=forecast_periods)

        del model, fitted
        gc.collect()

        return forecast_result

    except Exception as e:
        print(f"Theta 예측 실패: {e}")
        return None


# ==================== 데이터 결합 및 예측 실행 ====================

def combine_data(revenue_data, export_exog_data):
    """매출 데이터와 외생변수 결합"""
    revenue_endog_df = revenue_data[['date', 'revenue']].copy()
    revenue_endog_df.rename(columns={'revenue': 'endog_var'}, inplace=True)

    if len(export_exog_data) > 0:
        combined_df = pd.merge(revenue_endog_df, export_exog_data, on='date', how='outer').sort_values('date')
    else:
        combined_df = revenue_endog_df.copy()
        combined_df['exog_var'] = np.nan

    return combined_df


def run_all_forecasts(forecast_df, hs_code, forecast_periods=8):
    """모든 예측 모델 실행 (SARIMA, LSTM, Prophet, Exponential Smoothing, Theta)"""
    results = {}

    endog = forecast_df['endog_var'].values
    exog = forecast_df['exog_var'].values if (hs_code is not None and forecast_df['exog_var'].notna().any()) else None

    # SARIMA
    results['sarima'] = sarima_forecast(endog, exog, forecast_periods)

    # LSTM
    results['lstm'] = lstm_forecast(endog, forecast_periods)

    # Prophet
    results['prophet'] = prophet_forecast(forecast_df, forecast_periods)

    # Exponential Smoothing (신규 추가)
    results['exp_smoothing'] = exponential_smoothing_forecast(endog, forecast_periods)

    # Theta (신규 추가)
    results['theta'] = theta_forecast(endog, forecast_periods)

    return results


def create_ensemble_forecast(forecast_results):
    """앙상블 예측 생성"""
    valid_forecasts = []

    for model_name, forecast in forecast_results.items():
        if forecast is not None and not np.all(np.isnan(forecast)):
            valid_forecasts.append(forecast)

    if valid_forecasts:
        return np.mean(valid_forecasts, axis=0)
    else:
        return None


# ==================== 메인 처리 함수 ====================

def process_single_ticker(ticker, hs_code, db_info, table_name):
    """단일 티커 처리"""
    try:
        # 1. 매출 데이터 추출
        revenue_data = extract_revenue_data(db_info, ticker)
        if len(revenue_data) == 0:
            print(f"[{ticker}] 매출 데이터 없음")
            return False

        # 2. 수출 데이터 추출
        export_data = extract_export_data(db_info, hs_code)
        export_exog_data = calculate_yoy_growth(export_data)

        # 3. 데이터 결합
        combined_df = combine_data(revenue_data, export_exog_data)
        forecast_df = combined_df[combined_df['endog_var'].notna()].copy()

        if len(forecast_df) < 8:
            print(f"[{ticker}] 데이터 부족 (최소 8개 필요, 현재 {len(forecast_df)}개)")
            return False

        # 4. 예측 실행
        forecast_results = run_all_forecasts(forecast_df, hs_code, forecast_periods=8)

        # 5. 앙상블 예측
        ensemble = create_ensemble_forecast(forecast_results)

        # 6. 결과 데이터프레임 생성
        last_date = forecast_df['date'].max()
        future_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=3),
            periods=8,
            freq='Q'
        )

        forecast_comparison_df = pd.DataFrame({
            'date': future_dates,
            'year_month': future_dates.strftime('%Y-%m')
        })

        # 모든 모델 결과 추가
        for model_name, forecast in forecast_results.items():
            if forecast is not None:
                forecast_comparison_df[f'{model_name}_forecast'] = forecast
            else:
                forecast_comparison_df[f'{model_name}_forecast'] = np.nan

        # 앙상블 결과 추가
        if ensemble is not None:
            forecast_comparison_df['ensemble_forecast'] = ensemble
        else:
            forecast_comparison_df['ensemble_forecast'] = np.nan

        # 7. Long format으로 변환하여 DB 저장
        forecast_date = pd.Timestamp.now().strftime('%Y-%m-%d')
        long_format_list = []

        for _, row in forecast_comparison_df.iterrows():
            date = row['date']
            for col in forecast_comparison_df.columns:
                if col not in ['date', 'year_month'] and pd.notna(row[col]):
                    long_format_list.append({
                        'date': date,
                        'ticker': ticker,
                        'indicator': col,
                        'value': float(row[col])
                    })

        if len(long_format_list) > 0:
            long_format_df = pd.DataFrame(long_format_list)
            long_format_df['date'] = pd.to_datetime(long_format_df['date'])
            save_valuation_to_db(db_info, table_name, long_format_df)

            print(f"[{ticker}] 완료 - {len(long_format_df):,}개 레코드 저장")

            # 메모리 정리
            del revenue_data, export_data, combined_df, forecast_df
            del forecast_results, forecast_comparison_df, long_format_df
            gc.collect()

            return True
        else:
            print(f"[{ticker}] 저장할 데이터 없음")
            return False

    except Exception as e:
        print(f"[{ticker}] 처리 중 오류 발생: {e}")
        return False


def process_multiple_tickers(ticker_list, db_info, table_name='Korea_company_valuation_ver2',
                             error_log_file='error_log.txt'):
    """여러 티커를 순차적으로 처리"""
    success_count = 0
    error_tickers = []

    # 에러 로그 파일 초기화
    with open(error_log_file, 'w', encoding='utf-8') as f:
        f.write(f"에러 로그 - {datetime.now()}\n")
        f.write("=" * 50 + "\n\n")

    # tqdm을 사용한 진행 표시
    for ticker_info in tqdm(ticker_list, desc="티커 처리 진행", unit="ticker"):
        ticker = ticker_info['ticker']
        hs_code = ticker_info.get('hs_code', None)

        try:
            success = process_single_ticker(ticker, hs_code, db_info, table_name)
            if success:
                success_count += 1
            else:
                error_tickers.append(ticker)
                with open(error_log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{ticker}: 처리 실패 (데이터 부족 또는 저장 실패)\n")
        except Exception as e:
            error_tickers.append(ticker)
            with open(error_log_file, 'a', encoding='utf-8') as f:
                f.write(f"{ticker}: {str(e)}\n")
            continue

    # 최종 결과 출력
    print("\n" + "=" * 50)
    print(f"전체 처리 완료!")
    print(f"총 티커 수: {len(ticker_list)}")
    print(f"성공: {success_count}")
    print(f"실패: {len(error_tickers)}")

    if error_tickers:
        print(f"\n실패한 티커 목록: {', '.join(error_tickers)}")
        print(f"상세 에러 로그: {error_log_file}")

    return {
        'total': len(ticker_list),
        'success': success_count,
        'failed': len(error_tickers),
        'error_tickers': error_tickers
    }


# ==================== 실행 예제 ====================
#
# if __name__ == "__main__":
#     # DB 설정
#     db_info = {
#         'host': get_db_host(),
#         'port': 3307,
#         'user': 'stox7412',
#         'password': 'Apt106503!~',
#         'database': 'investar'
#     }
#
#     # 처리할 티커 리스트 (여러 개 입력 가능)
#     ticker_list = [
#         {'ticker': 'A084370', 'hs_code': None},
#         {'ticker': 'A005930', 'hs_code': '8542'},
#         {'ticker': 'A000660', 'hs_code': None},
#         # 필요한 만큼 추가...
#     ]
#
#     # 단일 티커 처리 예제
#     # process_single_ticker('A084370', None, db_info, 'Korea_company_valuation_ver2')
#
#     # 여러 티커 일괄 처리
#     results = process_multiple_tickers(
#         ticker_list=ticker_list,
#         db_info=db_info,
#         table_name='Korea_company_valuation_ver2',
#         error_log_file='forecast_error_log.txt'
#     )