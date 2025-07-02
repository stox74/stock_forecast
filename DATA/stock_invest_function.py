import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import socket
import requests
from datetime import timedelta

import matplotlib
import matplotlib.pyplot as plt
import os
import requests
from tqdm import tqdm
from statsmodels.tsa.statespace.sarimax import SARIMAX
from itertools import product
from prophet import Prophet
import datetime

from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from statsmodels.stats.diagnostic import acorr_ljungbox
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense

import warnings
warnings.filterwarnings("ignore")

def fetch_trade_data_multi_hscode(db_info: dict,
                                   hs_codes: list,
                                   indicator: str,
                                   table_name: str = 'korea_monthly_trade_data') -> pd.DataFrame:
    """
    여러 HS 코드와 하나의 indicator에 해당하는 무역 데이터를 MySQL/MariaDB에서 조회

    Parameters:
    - db_info (dict): DB 접속 정보 (user, password, host, port, database)
    - hs_codes (list): 조회할 HS 코드 리스트
    - indicator (str): 조회할 지표 이름 (예: 'expDlr', 'impDlr')
    - table_name (str): 조회할 테이블 이름 (기본값: 'korea_monthly_trade_data')

    Returns:
    - pd.DataFrame: 조회된 무역 데이터
    """

    try:
        # DB 엔진 생성
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        # HS 코드 리스트를 안전하게 SQL용 문자열로 변환
        hs_codes_str = ', '.join(f"'{code}'" for code in hs_codes)

        # 쿼리 작성
        query = f"""
            SELECT *
            FROM {table_name}
            WHERE root_hs_code IN ({hs_codes_str})
              AND indicator = '{indicator}'
            ORDER BY root_hs_code, date
        """

        # 쿼리 실행 및 결과 DataFrame으로 변환
        df = pd.read_sql(query, engine)

        # 날짜 컬럼 변환
        df['date'] = pd.to_datetime(df['date'])

        return df

    except Exception as e:
        print(f"\u274c \ub370\uc774\ud130 \uc870\ud68c \uc2e4\ud328: {e}")
        return pd.DataFrame()


def preprocess_quarterly_growth(df: pd.DataFrame) -> pd.DataFrame:
    """
    월별 데이터를 분기별로 집계하고, 전년 동분기 대비 성장률을 계산하는 함수
    """
    df['quarter'] = df['date'].dt.to_period('Q')
    df_quarterly = (
        df.groupby(['root_hs_code', 'quarter'])['value']
        .sum()
        .reset_index()
    )
    df_quarterly['date'] = df_quarterly['quarter'].dt.to_timestamp(how='end')
    df_quarterly.drop(columns=['quarter'], inplace=True)
    df_quarterly['date'] = pd.to_datetime(df_quarterly['date']).dt.date

    df_quarterly = df_quarterly.sort_values(['root_hs_code', 'date'])
    df_quarterly['yoy_value'] = df_quarterly.groupby('root_hs_code')['value'].shift(4)
    df_quarterly['yoy_growth'] = (
                                         (df_quarterly['value'] - df_quarterly['yoy_value']) / df_quarterly['yoy_value']
                                 ) * 100

    return df_quarterly


def create_yoy_growth_pivot(df_quarterly: pd.DataFrame,
                            start_date: str = None,
                            end_date: str = None) -> pd.DataFrame:
    """
    전년 동분기 대비 증가율을 pivot 형태로 변환하고 분석기간을 설정할 수 있는 함수

    Parameters:
    - df_quarterly (DataFrame): 'root_hs_code', 'date', 'yoy_growth' 포함된 데이터
    - start_date (str or None): 분석 시작일 (예: '2015-01-01')
    - end_date (str or None): 분석 종료일 (예: '2023-12-31')

    Returns:
    - pivot_df (DataFrame): 행: date, 열: root_hs_code, 값: yoy_growth
    """
    pivot_df = df_quarterly.pivot(
        index='date',
        columns='root_hs_code',
        values='yoy_growth'
    ).sort_index()

    pivot_df.replace([np.inf, -np.inf], np.nan, inplace=True)
    pivot_df.index = pd.to_datetime(pivot_df.index)

    if start_date:
        pivot_df = pivot_df[pivot_df.index >= pd.to_datetime(start_date)]
    if end_date:
        pivot_df = pivot_df[pivot_df.index <= pd.to_datetime(end_date)]

    return pivot_df

def fetch_table_data(db_info: dict, table_name: str) -> pd.DataFrame:
    """
    investar DB에서 테이블 이름만 입력하면 전체 데이터를 가져오는 함수

    Parameters:
    - db_info (dict): DB 접속 정보 (user, password, host, port, database)
    - table_name (str): 조회할 테이블 이름

    Returns:
    - pd.DataFrame: 전체 테이블 데이터
    """
    try:
        # SQLAlchemy 엔진 생성
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
            f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        # 데이터 조회
        query = f"SELECT * FROM {table_name}"
        df = pd.read_sql(query, con=engine)
        print(f"✅ '{table_name}' 테이블에서 {len(df)}건의 데이터를 가져왔습니다.")
        return df

    except Exception as e:
        print(f"❌ 데이터 조회 실패: {e}")
        return pd.DataFrame()

def get_db_host():
    try:
        # 현재 로컬 IP 주소 확인
        local_ip = socket.gethostbyname(socket.gethostname())

        # 내부 네트워크면 192로 시작 (또는 10. / 172.16~31 도 가능)
        if local_ip.startswith("192.168."):
            return '192.168.0.230'  # 집 내부 IP로 수정
        else:
            return 'hystox74.synology.me'  # 외부 접속용 DDNS 주소
    except Exception as e:
        print("⚠️ IP 확인 실패:", e)
        return 'hystox74.synology.me'  # 예외 발생 시 외부 주소 사용


# 날짜를 회계분기 말일로 정규화
def normalize_dates_to_quarter_end(df):
    df = df.copy()
    df = df.reset_index()
    df['date'] = pd.to_datetime(df['date']) + pd.offsets.QuarterEnd(0)
    df = df.set_index('date')
    return df

def get_db_host():
    try:
        # 현재 로컬 IP 주소 확인
        local_ip = socket.gethostbyname(socket.gethostname())

        # 내부 네트워크면 192로 시작 (또는 10. / 172.16~31 도 가능)
        if local_ip.startswith("192.168."):
            return '192.168.0.230'  # 집 내부 IP로 수정
        else:
            return 'hystox74.synology.me'  # 외부 접속용 DDNS 주소
    except Exception as e:
        print("⚠️ IP 확인 실패:", e)
        return 'hystox74.synology.me'  # 예외 발생 시 외부 주소 사용

# long-format 변환 함수 (ticker는 컬럼에서 제거하고 별도 컬럼으로 분리)
def reshape_FMP_data(margin_df):
    melted_df = margin_df.reset_index().melt(id_vars=['date', 'ticker'], var_name='accounting_item', value_name='value')
    final_df = melted_df.dropna().sort_values(['accounting_item', 'ticker', 'date'])
    return final_df

# 마진 및 성장률 시계열 데이터 수집 및 long-format 변환
def get_FMP_data(ticker_list, apikey):
    result_df_list = []

    for ticker in tqdm(ticker_list, desc="Fetching income statements"):
        url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}?period=quarter&limit=100&apikey={apikey}"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"❌ {ticker} API 요청 실패: {response.status_code}")
            continue

        data = response.json()
        df = pd.DataFrame(data)
        if df.empty:
            print(f"❌ {ticker} 수신 데이터 없음.")
            continue

        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        # 기본 컬럼 정의
        df['revenue'] = df['revenue']
        df['grossProfit'] = df['grossProfit']
        df['operatingIncome'] = df['operatingIncome']
        df['netIncome'] = df['netIncome']

        # 마진율 계산
        df['GPM'] = df['grossProfit'] / df['revenue']
        df['OPM'] = df['operatingIncome'] / df['revenue']
        df['NPM'] = df['netIncome'] / df['revenue']

        # 성장률 계산
        for col in ['revenue', 'grossProfit', 'operatingIncome', 'netIncome']:
            df[f'{col}_qoq'] = df[col].pct_change(periods=1)
            df[f'{col}_yoy'] = df[col].pct_change(periods=4)

        # 필요한 항목만 추출
        keep_cols = ['revenue', 'grossProfit', 'operatingIncome', 'netIncome',
                     'GPM', 'OPM', 'NPM',
                     'revenue_qoq', 'grossProfit_qoq', 'operatingIncome_qoq', 'netIncome_qoq',
                     'revenue_yoy', 'grossProfit_yoy', 'operatingIncome_yoy', 'netIncome_yoy']

        sub_df = df[keep_cols]
        sub_df = normalize_dates_to_quarter_end(sub_df)
        sub_df['ticker'] = ticker

        result_df_list.append(sub_df)

    # 전체 병합 및 정렬
    if not result_df_list:
        return pd.DataFrame()

    full_df = pd.concat(result_df_list)
    full_df = full_df.replace([np.inf, -np.inf], np.nan)
    full_df = full_df.sort_index()

    return full_df

def merge_wrds_and_fmp(endog_df, fmp_df, tic_name):
    """
    endog_df를 우선으로 사용하고, 값이 없는 경우에만 fmp_df의 값을 채워넣습니다.

    Parameters:
        - endog_df: 주된 데이터프레임
        - fmp_df: 보조 데이터프레임
        - tic_name: 병합 대상인 칼럼 이름 (ex: 'COHR')

    Returns:
        - 병합된 DataFrame (date, tic_name)
    """
    # 1. 날짜를 분기 종료일로 통일
    for df in [endog_df, fmp_df]:
        df['date'] = pd.to_datetime(df['date']).dt.to_period("Q").dt.to_timestamp(how='end').dt.normalize()

    # 2. 날짜 기준으로 outer merge
    merged = pd.merge(
        endog_df[['date', tic_name]],
        fmp_df[['date', tic_name]].rename(columns={tic_name: 'fmp_value'}),
        on='date',
        how='outer'
    ).sort_values('date')

    # 3. 메인 데이터 우선, 보조 데이터 보완
    merged['final'] = merged[tic_name].combine_first(merged['fmp_value'])

    # 4. 정리: date, 최종값만 반환
    result = merged[['date', 'final']].rename(columns={'final': tic_name})
    return result

def merge_endog_exog_data(endog_df, exog_df,
                          endog_col, exog_col,
                          start_date=None, end_date=None):
    """
    두 데이터프레임에서 지정한 칼럼을 추출하고 날짜 기준으로 병합합니다.
    """

    # 1. 복사 및 날짜 처리
    endog_df = endog_df.copy()
    exog_df = exog_df.copy()

    # 날짜 → 분기 종료일 → 시간 정보 제거
    # 날짜 → 분기 종료일 → 시간 제거
    endog_df['date'] = pd.to_datetime(endog_df['date']).dt.to_period('Q').dt.to_timestamp(how='end').dt.normalize()
    exog_df['date'] = pd.to_datetime(exog_df['date']).dt.to_period('Q').dt.to_timestamp(how='end').dt.normalize()

    # 2. 외생변수는 중복 제거
    exog_df = exog_df.groupby('date')[exog_col].last().reset_index()

    # 3. 날짜 필터링
    if start_date:
        start = pd.to_datetime(start_date)
        endog_df = endog_df[endog_df['date'] >= start]
        exog_df = exog_df[exog_df['date'] >= start]
    if end_date:
        end = pd.to_datetime(end_date)
        endog_df = endog_df[endog_df['date'] <= end]
        exog_df = exog_df[exog_df['date'] <= end]

    # 4. 필요한 칼럼 추출
    endog_df = endog_df[['date', endog_col]]
    exog_df = exog_df[['date', exog_col]]

    # 5. 병합
    merged_df = pd.merge(endog_df, exog_df, on='date', how='inner')

    # 6. 열 이름 변경
    merged_df.rename(columns={
        endog_col: 'endog_var',
        exog_col: 'exog_var'
    }, inplace=True)

    return merged_df

def forecast_future_4q_with_sarima(df, date_col='date', value_col='endog_var', exog_col=None,
                                   use_log=True, fixed_variable=0):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
    import matplotlib.pyplot as plt
    from itertools import product
    import numpy as np
    from tqdm import tqdm
    import pandas as pd

    # 시계열 설정
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).asfreq('Q')
    ts = df[value_col]

    # 로그 변환을 먼저 적용
    if use_log:
        ts_transformed = np.log(ts)
        print("✅ 로그 변환 적용")
    else:
        ts_transformed = ts

    # 외생변수 설정 (ts_transformed 이후에 실행되어야 함)
    if exog_col:
        exog = df[exog_col]
        exog_train = exog.loc[ts_transformed.index]   # ✅ 이제 안전하게 접근 가능
        exog_forecast = exog.iloc[-4:]
        print(f"📎 외생변수 '{exog_col}' 포함")
    else:
        exog_train = exog_forecast = None

    # 로그 변환
    if use_log:
        ts_transformed = np.log(ts)
        print("✅ 로그 변환 적용")
    else:
        ts_transformed = ts

    # SARIMA 학습
    if fixed_variable == 1:
        order = (1, 1, 1)
        seasonal_order = (1, 1, 1, 4)
        print(f"🔧 고정 SARIMA 구조 사용: order={order}, seasonal_order={seasonal_order}")
        model = SARIMAX(ts_transformed, order=order, seasonal_order=seasonal_order,
                        exog=exog if exog_col else None)
        result = model.fit(disp=False)
    else:
        p = d = q = P = D = Q = [0, 1]
        s = 4
        best_aic = np.inf
        best_model = None
        print(f"🔍 Grid Search 진행 중...")

        for order in product(p, d, q):
            for seasonal in product(P, D, Q):
                seasonal_order = (*seasonal, s)
                try:
                    model = SARIMAX(ts_transformed, order=order,
                                    seasonal_order=seasonal_order,
                                    exog=exog_train if exog_col else None)
                    temp_result = model.fit(disp=False)
                    if temp_result.aic < best_aic:
                        best_aic = temp_result.aic
                        best_model = temp_result
                        best_order = order
                        best_seasonal = seasonal_order
                except Exception as e:
                    print(f"⚠️ 실패: order={order}, seasonal={seasonal_order}, error={e}")
                    continue

        if best_model is None:
            print("❌ Grid Search 실패: 유효한 SARIMA 모형이 하나도 학습되지 않았습니다.")
            return None

        result = best_model
        print(f"✅ 최적 모형: order={best_order}, seasonal_order={best_seasonal}, AIC={best_aic:.2f}")

    # 예측
    forecast_log = result.forecast(steps=4, exog=exog_forecast if exog_col else None)
    forecast = np.exp(forecast_log) if use_log else forecast_log

    # fittedvalues → 원 단위로 변환
    fitted = result.fittedvalues
    fitted = np.exp(fitted) if use_log else fitted

    # 실제값과 fitted값 align
    ts_valid = ts[fitted.index]  # 잘린 부분 제외
    residuals_original = ts_valid - fitted

    # 예측 시각화
    plt.figure(figsize=(12, 5))
    plt.plot(ts.index, ts.values, label='실제 시계열', color='skyblue')
    plt.plot(forecast.index, forecast.values, label='예측값 (미래 4분기)', linestyle='--', marker='o', color='orange')
    plt.xlim(ts.index.min(), forecast.index.max())
    y_min = min(ts.min(), forecast.min()) * 0.95
    y_max = max(ts.max(), forecast.max()) * 1.05
    plt.ylim(y_min, y_max)
    plt.title("전체 데이터 및 SARIMA 예측 결과", fontsize=14)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

    print("\n📈 미래 4분기 예측 결과:")
    print(forecast)

    # 잔차 진단 (원 단위 residual)
    print("\n📊 Ljung-Box Test (잔차 자기상관):")
    print(acorr_ljungbox(residuals_original.dropna(), lags=[4, 8, 12], return_df=True))

    plt.figure(figsize=(12, 6))
    plt.subplot(2, 2, 1)
    plt.plot(residuals_original)
    plt.title("잔차 시계열 (원 단위)")

    plt.subplot(2, 2, 2)
    plot_acf(residuals_original.dropna(), ax=plt.gca(), lags=20)
    plt.title("잔차 ACF")

    plt.subplot(2, 2, 3)
    plot_pacf(residuals_original.dropna(), ax=plt.gca(), lags=20)
    plt.title("잔차 PACF")

    plt.subplot(2, 2, 4)
    plt.hist(residuals_original.dropna(), bins=20)
    plt.title("잔차 분포")

    plt.tight_layout()
    plt.show()

    return forecast

def forecast_ratio_with_lstm(df_market, df_revenue, window_size=60, forecast_days=31):
    """
    일일 시가총액과 분기별 매출 데이터를 기반으로 LSTM으로 PSR 예측

    Parameters
    ----------
    df_market : pd.DataFrame
        'date' (datetime) 와 'marketCap' 컬럼 포함. 일별 시가총액 데이터.
    df_revenue : pd.DataFrame
        'date' (datetime) 와 매출 컬럼(예: 'ANET') 포함. 분기별 데이터.
    window_size : int
        LSTM 입력 시퀀스 길이 (default: 60)
    forecast_days : int
        예측할 미래 영업일 수 (default: 30)

    Returns
    -------
    forecast_df : pd.DataFrame
        'date' 와 'forecasted_PSR' 포함된 데이터프레임
    """

    # 1. TTM 계산
    df_revenue = df_revenue.sort_values("date").reset_index(drop=True)
    df_revenue["TTM_Revenue"] = df_revenue.iloc[:, 1].rolling(window=4).sum()  # 두번째 컬럼 사용

    # 2. 일일 시가총액과 TTM 병합
    df_revenue = df_revenue.sort_values('date')
    df_market = df_market.sort_values('date')

    df_daily_psr = pd.merge_asof(
        df_market,
        df_revenue[['date', 'TTM_Revenue']],
        left_on='date',
        right_on='date',
        direction='backward'
    )

    # PSR 계산
    df_daily_psr['PSR'] = df_daily_psr['marketCap'] / df_daily_psr['TTM_Revenue']
    df_daily_psr = df_daily_psr.dropna(subset=['PSR'])

    # 3. 스케일링
    series = df_daily_psr[['PSR']].copy()
    scaler = MinMaxScaler()
    scaled_series = scaler.fit_transform(series)

    # 4. 시퀀스 데이터 준비
    def create_sequences(data, window_size=60):
        X, y = [], []
        for i in range(window_size, len(data)):
            X.append(data[i-window_size:i])
            y.append(data[i])
        return np.array(X), np.array(y)

    X_train, y_train = create_sequences(scaled_series, window_size)

    # 5. LSTM 모델 학습
    model = Sequential([
        LSTM(50, activation='relu', input_shape=(window_size, 1)),
        Dense(1)
    ])
    model.compile(optimizer='adam', loss='mse')
    model.fit(X_train, y_train, epochs=20, batch_size=32, verbose=0)

    # 6. 미래 예측
    future = list(scaled_series[-window_size:].reshape(-1))
    predictions = []

    for _ in range(forecast_days):
        x_input = np.array(future[-window_size:]).reshape(1, window_size, 1)
        next_pred = model.predict(x_input, verbose=0)
        predictions.append(next_pred[0][0])
        future.append(next_pred[0][0])

    forecasted_psr = scaler.inverse_transform(np.array(predictions).reshape(-1, 1))

    # 7. 미래 날짜 생성
    last_date = df_daily_psr['date'].max()
    forecast_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=forecast_days)

    forecast_df = pd.DataFrame({'date': forecast_dates, 'forecasted_PSR': forecasted_psr.flatten()})

    return forecast_df


# 1. 시가총액 데이터 (일별 기준, 날짜 범위 포함)
def get_market_cap(symbol, api_key, from_date, to_date):
    url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{symbol}?from={from_date}&to={to_date}&apikey={api_key}"
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    return df[['date', 'marketCap']]


def load_forecast_by_hscode(db_info, root_hs_code, table_name):
    """
    특정 root_hs_code에 해당하는 예측 데이터를 데이터베이스에서 불러오는 함수

    Parameters:
    - db_info (dict): DB 접속 정보 (host, port, user, password, database)
    - root_hs_code (str or int): 조회할 HS 코드
    - table_name (str): 테이블 이름 (기본값: 'trade_forecast_by_month')

    Returns:
    - pd.DataFrame: 조회된 데이터프레임
    """
    try:
        # ✅ DB 엔진 생성
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        # ✅ SQL 쿼리 작성 및 실행
        query = f"""
            SELECT *
            FROM {table_name}
            WHERE root_hs_code = '{root_hs_code}'
            ORDER BY date
        """
        df = pd.read_sql(query, con=engine)

        print(f"✅ root_hs_code={root_hs_code}에 해당하는 {len(df)}개 행을 불러왔습니다.")
        return df

    except Exception as e:
        print(f"❌ 데이터 불러오기 실패: {e}")
        return pd.DataFrame()

def get_quarterly_export_forecast(db_info: dict, hs_code: str) -> pd.DataFrame:
    """
    특정 HS 코드를 기준으로 수출 예측 데이터를 불러와 분기별로 정리하는 함수

    Parameters:
    - db_info: dict, MariaDB 접속 정보
    - hs_code: str, 예: '854232' (HS 코드)

    Returns:
    - quarterly_sum_df: DataFrame, 분기별 수출합계 및 qoq/yoy 증가율 포함
    """
    # DB에서 해당 HS코드의 예측 데이터 로드
    # target_export_df = load_forecast_by_hscode(db_info, hs_code)
    temp_df = load_forecast_by_hscode(db_info, hs_code, table_name='korea_monthly_trade_data_forecast')

    # 중복 제거
    target_export_df = temp_df.drop_duplicates(subset=['date'])

    # date를 datetime으로 변환
    target_export_df['date'] = pd.to_datetime(target_export_df['date'])

    # 분기 추출 및 그룹화
    target_export_df['quarter'] = target_export_df['date'].dt.to_period('Q')
    quarterly_sum_df = target_export_df.groupby('quarter')['expDlr_forecast_12m'].sum().reset_index()

    # datetime 변환 후 추가 계산
    quarterly_sum_df['quarter'] = quarterly_sum_df['quarter'].dt.to_timestamp()
    quarterly_sum_df['export_qoq_change'] = quarterly_sum_df['expDlr_forecast_12m'].pct_change(periods=1)
    quarterly_sum_df['export_yoy_change'] = quarterly_sum_df['expDlr_forecast_12m'].pct_change(periods=4)
    quarterly_sum_df['date_month'] = quarterly_sum_df['quarter'] + pd.offsets.QuarterEnd(0)

    return quarterly_sum_df

def forecast_future_with_sarima(df, date_col='date', value_col='endog_var', exog_col=None,
                                freq='Q', forecast_steps=4, seasonal_period=None,
                                use_log=True, fixed_variable=0):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
    import matplotlib.pyplot as plt
    from itertools import product
    import numpy as np
    from tqdm import tqdm
    import pandas as pd

    # 시계열 설정
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).asfreq(freq)
    ts = df[value_col]

    # 주기 자동 결정
    if seasonal_period is None:
        if freq == 'Q':
            seasonal_period = 4
        elif freq == 'M':
            seasonal_period = 12
        else:
            raise ValueError(f"지원되지 않는 freq '{freq}'. 'Q' 또는 'M' 사용하세요.")

    print(f"🔍 주기 설정: seasonal_period = {seasonal_period}")

    # 로그 변환
    if use_log:
        ts_transformed = np.log(ts)
        print("✅ 로그 변환 적용")
    else:
        ts_transformed = ts

    # 외생변수
    if exog_col:
        exog = df[exog_col]
        exog_train = exog.loc[ts_transformed.index]
        exog_forecast = exog.iloc[-forecast_steps:]
        print(f"📎 외생변수 '{exog_col}' 포함")
    else:
        exog_train = exog_forecast = None

    # SARIMA 학습
    if fixed_variable == 1:
        order = (1, 1, 1)
        seasonal_order = (1, 1, 1, seasonal_period)
        print(f"🔧 고정 SARIMA 구조 사용: order={order}, seasonal_order={seasonal_order}")
        model = SARIMAX(ts_transformed, order=order, seasonal_order=seasonal_order,
                        exog=exog_train)
        result = model.fit(disp=False)
    else:
        p = d = q = P = D = Q = [0, 1]
        best_aic = np.inf
        best_model = None
        print(f"🔍 Grid Search 진행 중...")

        for order in product(p, d, q):
            for seasonal in product(P, D, Q):
                seasonal_order = (*seasonal, seasonal_period)
                try:
                    model = SARIMAX(ts_transformed, order=order,
                                    seasonal_order=seasonal_order,
                                    exog=exog_train)
                    temp_result = model.fit(disp=False)
                    if temp_result.aic < best_aic:
                        best_aic = temp_result.aic
                        best_model = temp_result
                        best_order = order
                        best_seasonal = seasonal_order
                except Exception:
                    continue

        if best_model is None:
            print("❌ Grid Search 실패: 유효한 SARIMA 모형이 하나도 학습되지 않았습니다.")
            return None

        result = best_model
        print(f"✅ 최적 모형: order={best_order}, seasonal_order={best_seasonal}, AIC={best_aic:.2f}")

    # 예측
    forecast_log = result.forecast(steps=forecast_steps, exog=exog_forecast)
    forecast = np.exp(forecast_log) if use_log else forecast_log

    # fittedvalues → 원 단위
    fitted = np.exp(result.fittedvalues) if use_log else result.fittedvalues
    ts_valid = ts[fitted.index]
    residuals_original = ts_valid - fitted

    # 시각화
    plt.figure(figsize=(12, 5))
    plt.plot(ts.index, ts.values, label='실제 시계열', color='skyblue')
    plt.plot(forecast.index, forecast.values, label=f'예측값 (향후 {forecast_steps} 기간)', linestyle='--', marker='o', color='orange')
    plt.xlim(ts.index.min(), forecast.index.max())
    y_min = min(ts.min(), forecast.min()) * 0.95
    y_max = max(ts.max(), forecast.max()) * 1.05
    plt.ylim(y_min, y_max)
    plt.title(f"전체 데이터 및 SARIMA 예측 결과", fontsize=14)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()

    print("\n📈 미래 예측 결과:")
    print(forecast)

    # 잔차 진단
    print("\n📊 Ljung-Box Test (잔차 자기상관):")
    print(acorr_ljungbox(residuals_original.dropna(), lags=[4, 8, 12], return_df=True))

    plt.figure(figsize=(12, 6))
    plt.subplot(2, 2, 1)
    plt.plot(residuals_original)
    plt.title("잔차 시계열 (원 단위)")

    plt.subplot(2, 2, 2)
    plot_acf(residuals_original.dropna(), ax=plt.gca(), lags=20)
    plt.title("잔차 ACF")

    plt.subplot(2, 2, 3)
    plot_pacf(residuals_original.dropna(), ax=plt.gca(), lags=20)
    plt.title("잔차 PACF")

    plt.subplot(2, 2, 4)
    plt.hist(residuals_original.dropna(), bins=20)
    plt.title("잔차 분포")

    plt.tight_layout()
    plt.show()

    return forecast