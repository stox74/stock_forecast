#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
from tqdm import tqdm
from statsmodels.tsa.statespace.sarimax import SARIMAX
from datetime import datetime
from itertools import product
from sqlalchemy import create_engine, text
import sqlalchemy
import matplotlib.pyplot as plt
import warnings

# stock_invest_function에서 get_db_host만 import
try:
    from DATA.stock_invest_function import get_db_host
except:
    # get_db_host를 import할 수 없는 경우 기본값 사용
    def get_db_host():
        return '192.168.0.230'

plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings("ignore")

# 수입 데이터 설정
LOG_TRANSFORM_CODES = []  # 필요시 추가
FORECAST_STEPS = 24
MIN_PERIODS = 60

db_info = {
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'host': get_db_host(),
    'port': '3307',
    'database': 'investar'
}

input_date = datetime.now().date()

print(f"=== SARIMA 예측 시스템 - 수입 데이터 ({input_date}) ===")

engine = create_engine(
    f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
    f"{db_info['host']}:{db_info['port']}/{db_info['database']}"
)

# 테이블 생성 및 스키마 확인
print("테이블 생성 및 스키마 확인 중...")

with engine.connect() as conn:
    # 월별 테이블 생성
    conn.execute(text("""
                      CREATE TABLE IF NOT EXISTS us_trade_import_monthly_with_forecast
                      (
                          hs_code_6d
                          VARCHAR
                      (
                          10
                      ) NOT NULL,
                          date DATETIME NOT NULL,
                          impDlr DOUBLE DEFAULT NULL,
                          forecast_flag INT DEFAULT 0,
                          input_date DATE DEFAULT NULL,
                          quarter VARCHAR
                      (
                          10
                      ) DEFAULT NULL,
                          UNIQUE KEY uniq_monthly_tracking
                      (
                          hs_code_6d,
                          date,
                          forecast_flag,
                          input_date
                      )
                          ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                      """))
    print("[생성완료] us_trade_import_monthly_with_forecast")

    # 분기별 테이블 생성
    conn.execute(text("""
                      CREATE TABLE IF NOT EXISTS us_trade_import_quarter_with_forecast
                      (
                          hs_code_6d
                          VARCHAR
                      (
                          10
                      ) NOT NULL,
                          quarter VARCHAR
                      (
                          10
                      ) NOT NULL,
                          impDlr DOUBLE DEFAULT NULL,
                          date DATETIME DEFAULT NULL,
                          input_date DATE DEFAULT NULL,
                          forecast_flag INT DEFAULT 0,
                          UNIQUE KEY uniq_quarterly_tracking
                      (
                          hs_code_6d,
                          quarter,
                          forecast_flag,
                          input_date
                      )
                          ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                      """))
    print("[생성완료] us_trade_import_quarter_with_forecast")

    conn.commit()

print("무역 수입 데이터 로딩 중...")

# fetch_table_data 대신 직접 데이터 로드 (이모지 인코딩 오류 방지)
try:
    query = "SELECT * FROM us_import_data"
    trade_df = pd.read_sql(query, con=engine)
    print(f"[완료] {len(trade_df):,}개 레코드 로드 완료")
except Exception as e:
    print(f"[오류] 데이터 로드 실패: {e}")
    print("\n다음을 확인하세요:")
    print("  1. us_import_data 테이블이 존재하는지 확인")
    print("  2. python us_import_data_downloader_fast.py 실행")
    exit(1)

trade_df['hs_code_6d'] = trade_df['hs_code'].astype(str).str[:6]
trade_df['date'] = pd.to_datetime(trade_df['date'])

print(f"총 {len(trade_df):,}개 레코드 로드 완료")

valid_codes = trade_df['hs_code_6d'].unique().tolist()
print(f"분석 대상 HS Code: {len(valid_codes):,}개")


def forecast_sarima(df, date_col='date', value_col='impDlr', steps=14, use_log=False):
    """
    SARIMA 모델을 사용한 시계열 예측

    Parameters:
    - df: 데이터프레임
    - date_col: 날짜 컬럼명
    - value_col: 예측할 값 컬럼명 (impDlr)
    - steps: 예측 기간 (개월)
    - use_log: 로그 변환 사용 여부

    Returns:
    - 예측 결과 Series
    """
    try:
        ts = df.groupby(date_col)[value_col].sum().asfreq('M')
        if ts.isnull().any() or len(ts.dropna()) < MIN_PERIODS:
            return pd.Series(dtype='float64')

        if use_log:
            ts = np.log(ts)

        # SARIMA 파라미터 그리드
        p = d = q = [0, 1]
        P = D = Q = [0, 1]
        s = 12

        param_combinations = list(product(p, d, q))
        seasonal_combinations = list(product(P, D, Q))
        total_combinations = list(product(param_combinations, seasonal_combinations))

        best_aic = np.inf
        best_model = None

        # 최적 파라미터 탐색
        for (order, seasonal) in total_combinations:
            seasonal_order = (*seasonal, s)
            try:
                model = SARIMAX(ts, order=order, seasonal_order=seasonal_order)
                result = model.fit(disp=False, maxiter=50)
                if result.aic < best_aic:
                    best_aic = result.aic
                    best_model = result
            except Exception:
                continue

        if best_model is None:
            return pd.Series(dtype='float64')

        # 예측 수행
        forecast = best_model.forecast(steps=steps)
        if use_log:
            forecast = np.exp(forecast)

        forecast.index = pd.date_range(
            start=ts.index[-1] + pd.offsets.MonthEnd(1),
            periods=steps,
            freq='M'
        )
        return forecast

    except Exception:
        return pd.Series(dtype='float64')


print(f"총 {len(valid_codes):,}개 HS Code에 대해 예측 시작...")

forecast_list = []
model_count = 0

for code in tqdm(valid_codes, desc="SARIMA 예측 진행"):
    sub_df = trade_df[trade_df['hs_code_6d'] == code].copy()
    use_log = code in LOG_TRANSFORM_CODES

    forecast = forecast_sarima(
        sub_df[['date', 'impDlr']],
        steps=FORECAST_STEPS,
        use_log=use_log
    )

    if not forecast.empty:
        temp_df = pd.DataFrame({
            'hs_code_6d': code,
            'date': forecast.index,
            'impDlr': forecast.values,
            'forecast_flag': 1,
            'input_date': input_date
        })
        forecast_list.append(temp_df)
        model_count += 1

print(f"예측 완료: {model_count}개")

# 실제 데이터 준비
historical_df = trade_df.groupby(['hs_code_6d', 'date'], as_index=False)['impDlr'].sum()
historical_df['forecast_flag'] = 0
historical_df['input_date'] = input_date

# 실제 + 예측 데이터 결합
if forecast_list:
    forecast_combined = pd.concat(forecast_list, ignore_index=True)
    monthly_combined = pd.concat([historical_df, forecast_combined], ignore_index=True)
else:
    monthly_combined = historical_df.copy()

monthly_combined['quarter'] = monthly_combined['date'].dt.to_period('Q').astype(str)

# 중복 제거
monthly_combined = (monthly_combined
                    .sort_values(['hs_code_6d', 'date', 'forecast_flag', 'input_date'])
                    .drop_duplicates(subset=['hs_code_6d', 'date', 'forecast_flag', 'input_date'], keep='last'))

print(f"월별 데이터 준비 완료: {len(monthly_combined):,}개")

# 분기별 데이터 집계
monthly_combined['quarter_period'] = monthly_combined['date'].dt.to_period('Q')
quarterly_grouped = (
    monthly_combined
    .groupby(['hs_code_6d', 'quarter_period', 'input_date'], as_index=False)
    .agg({
        'impDlr': 'sum',
        'forecast_flag': 'max'
    })
)

quarterly_grouped['quarter'] = quarterly_grouped['quarter_period'].astype(str)
quarterly_grouped['date'] = quarterly_grouped['quarter_period'].dt.to_timestamp() + pd.offsets.QuarterEnd(0)
quarterly_grouped = quarterly_grouped[['hs_code_6d', 'quarter', 'impDlr', 'date', 'input_date', 'forecast_flag']]

# 중복 제거
quarterly_grouped = (quarterly_grouped
                     .sort_values(['hs_code_6d', 'quarter', 'forecast_flag', 'input_date'])
                     .drop_duplicates(subset=['hs_code_6d', 'quarter', 'forecast_flag', 'input_date'], keep='last'))

print(f"분기 데이터 준비 완료: {len(quarterly_grouped):,}개")

# 월별 데이터 업로드
print("월별 데이터 업로드 시작...")

try:
    temp_monthly = f"temp_import_monthly_{int(datetime.now().timestamp())}"

    monthly_combined.to_sql(
        name=temp_monthly,
        con=engine,
        if_exists='replace',
        index=False
    )

    with engine.connect() as conn:
        conn.execute(text(f"""
            INSERT INTO us_trade_import_monthly_with_forecast
            (hs_code_6d, date, impDlr, forecast_flag, input_date, quarter)
            SELECT hs_code_6d, date, impDlr, forecast_flag, input_date, quarter
            FROM {temp_monthly}
            ON DUPLICATE KEY UPDATE
                impDlr = VALUES(impDlr),
                quarter = VALUES(quarter);
        """))
        conn.execute(text(f"DROP TABLE {temp_monthly}"))
        conn.commit()

    print("[완료] 월별 데이터 업로드 완료")
    monthly_success = True
except Exception as e:
    print(f"[오류] 월별 업로드 실패: {e}")
    monthly_success = False

# 분기 데이터 업로드
print("분기 데이터 업로드 시작...")

try:
    temp_quarterly = f"temp_import_quarterly_{int(datetime.now().timestamp())}"

    quarterly_grouped.to_sql(
        name=temp_quarterly,
        con=engine,
        if_exists='replace',
        index=False
    )

    with engine.connect() as conn:
        conn.execute(text(f"""
            INSERT INTO us_trade_import_quarter_with_forecast
            (hs_code_6d, quarter, impDlr, date, input_date, forecast_flag)
            SELECT hs_code_6d, quarter, impDlr, date, input_date, forecast_flag
            FROM {temp_quarterly}
            ON DUPLICATE KEY UPDATE
                impDlr = VALUES(impDlr),
                date = VALUES(date);
        """))
        conn.execute(text(f"DROP TABLE {temp_quarterly}"))
        conn.commit()

    print("[완료] 분기 데이터 업로드 완료")
    quarterly_success = True
except Exception as e:
    print(f"[오류] 분기 업로드 실패: {e}")
    quarterly_success = False

# 최종 결과 출력
print("\n" + "=" * 60)
print("예측 시스템 실행 완료")
print("=" * 60)
print(f"예측 실행 날짜 (input_date): {input_date}")
print(f"예측 성공 HS Code: {model_count}개")
print(f"월별 레코드: {len(monthly_combined):,}개")
print(f"분기 레코드: {len(quarterly_grouped):,}개")

if monthly_success and quarterly_success:
    print("\n[완료] 모든 데이터 업로드 성공")
    print("\n생성된 테이블:")
    print("  1. us_trade_import_monthly_with_forecast (월별)")
    print("  2. us_trade_import_quarter_with_forecast (분기별)")
    print("\n예측 변화 추적 방법:")
    print("  - input_date를 예측 실행 날짜로 사용")
    print("  - 같은 (hs_code, date)에 대해 여러 input_date의 예측값 비교 가능")
    print("  - forecast_flag: 0=실제값, 1=예측값")
else:
    print("\n[경고] 일부 데이터 업로드 실패")

print("\n프로그램 종료")