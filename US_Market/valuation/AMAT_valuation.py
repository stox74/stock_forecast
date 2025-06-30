import pandas as pd
import requests
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine
from tqdm import tqdm
from datetime import datetime, timedelta
from prophet import Prophet
from DATA.stock_invest_function import (
    get_db_host,
    merge_endog_exog_data,
    forecast_future_4q_with_sarima,
    forecast_ratio_with_lstm,
    get_market_cap,
    get_FMP_data,
    reshape_FMP_data

)

def prepare_endog_data(fundq_df, tic_name, st_date, end_date):
    endog_df = fundq_df[fundq_df['ticker'] == tic_name][['date', 'saleq']].rename(columns={'saleq': tic_name})
    endog_df['date'] = pd.to_datetime(endog_df['date'])
    endog_df['quarter'] = endog_df['date'].dt.to_period('Q')
    endog_df = endog_df.sort_values('date').groupby('quarter').last().reset_index(drop=True)
    endog_df = endog_df[(endog_df['date'] >= st_date) & (endog_df['date'] <= end_date)]
    return endog_df

def prepare_exog_data(trade_df, hs_code):
    temp_df = trade_df[trade_df['hs_code_6d'] == hs_code][['date','expDlr']].drop_duplicates(subset=['date'])
    temp_df['date'] = pd.to_datetime(temp_df['date'])
    temp_df['quarter'] = temp_df['date'].dt.to_period('Q')
    quarterly_sum_df = temp_df.groupby('quarter')['expDlr'].sum().reset_index()
    quarterly_sum_df['quarter'] = quarterly_sum_df['quarter'].dt.to_timestamp()
    quarterly_sum_df['export_qoq_change'] = quarterly_sum_df['expDlr'].pct_change(periods=1)
    quarterly_sum_df['export_yoy_change'] = quarterly_sum_df['expDlr'].pct_change(periods=4)
    quarterly_sum_df['date'] = quarterly_sum_df['quarter'] + pd.offsets.QuarterEnd(0)
    quarterly_sum_df.drop(columns='quarter', inplace=True)
    quarterly_sum_df = quarterly_sum_df.iloc[8:].reset_index(drop=True)

    scaler = StandardScaler()
    quarterly_sum_df['exog_scaled'] = scaler.fit_transform(quarterly_sum_df[['export_yoy_change']])
    return quarterly_sum_df

def forecast_revenue_with_sarima(endog_df, exog_df, tic_name, st_date, end_date):
    merged = merge_endog_exog_data(
        endog_df=endog_df,
        exog_df=exog_df,
        endog_col=tic_name,
        exog_col='export_yoy_change',
        start_date=st_date,
        end_date=end_date
    ).drop_duplicates(subset=['date', 'endog_var']).reset_index(drop=True)

    forecast_with_exog = forecast_future_4q_with_sarima(merged, value_col='endog_var', exog_col='exog_var', fixed_variable=0)
    forecast_without_exog = forecast_future_4q_with_sarima(merged, value_col='endog_var', exog_col=None, fixed_variable=0)

    return forecast_with_exog.rename('revenue_with_exog'), forecast_without_exog.rename('revenue_without_exog')

def get_quarter_end(row):
    year = row['date'].year
    q = row['quarter']
    if q == 'Q1':
        return pd.Timestamp(year, 3, 31)
    elif q == 'Q2':
        return pd.Timestamp(year, 6, 30)
    elif q == 'Q3':
        return pd.Timestamp(year, 9, 30)
    elif q == 'Q4':
        return pd.Timestamp(year, 12, 31)

def fetch_and_prepare_psr_data(tickers, tic_name, apikey):
    ratios_timeseries = []
    for ticker in tqdm(tickers):
        try:
            url = f"https://financialmodelingprep.com/api/v3/ratios/{ticker}?period=quarter&limit=1000&apikey={apikey}"
            response = requests.get(url)
            data = response.json()

            if isinstance(data, list):
                for entry in data:
                    ratios_timeseries.append({
                        'ticker': ticker,
                        'date': entry.get('date'),
                        'PSR': entry.get('priceToSalesRatio')
                    })
        except Exception as e:
            print(f"[❌ 오류]git  {ticker}: {e}")

    df_ratios = pd.DataFrame(ratios_timeseries)
    df_ratios.sort_values(by=['ticker', 'date'], inplace=True)

    endog_ratio_df = df_ratios[df_ratios['ticker'] == tic_name][['date', 'PSR']].copy()
    endog_ratio_df['date'] = pd.to_datetime(endog_ratio_df['date'])
    endog_ratio_df['quarter'] = endog_ratio_df['date'].dt.month.map(
        lambda m: 'Q1' if m<=3 else 'Q2' if m<=6 else 'Q3' if m<=9 else 'Q4')

    endog_ratio_df['aligned_date'] = endog_ratio_df.apply(get_quarter_end, axis=1)

    endog_ratio_df.drop(columns=['date'], inplace=True)
    endog_ratio_df.rename(columns={'aligned_date':'date'}, inplace=True)
    return endog_ratio_df

def forecast_psr_with_sarima(endog_ratio_df, exog_df):
    merged_ratio_exog = pd.merge(endog_ratio_df, exog_df, on='date', how='left')[['date','PSR','exog_scaled']]
    merged_ratio_exog.rename(columns={'PSR':'endog_var','exog_scaled':'exog_var'}, inplace=True)
    merged_ratio_exog = merged_ratio_exog.dropna()

    psr_forecast_with_exog = forecast_future_4q_with_sarima(merged_ratio_exog, value_col='endog_var', exog_col='exog_var', use_log=False, fixed_variable=0)
    psr_forecast_without_exog = forecast_future_4q_with_sarima(merged_ratio_exog, value_col='endog_var', exog_col=None, use_log=False, fixed_variable=0)
    return psr_forecast_with_exog.rename('PSR_quarter_with_exog'), psr_forecast_without_exog.rename('PSR_quarter_without_exog')


def save_forecast_to_db(shortterm_df, longterm_df, db_info):
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )
    if not shortterm_df.empty:
        shortterm_df.to_sql('US_short_term_valuaion_result', con=engine, if_exists='replace', index=False)
    else:
        print("⚠️ shortterm_df 가 비어 있어 DB에 저장하지 않음.")

    if not longterm_df.empty:
        longterm_df.to_sql('US_long_term_valuaion_result', con=engine, if_exists='replace', index=False)
    else:
        print("⚠️ longterm_df 가 비어 있어 DB에 저장하지 않음.")


def main():
    # === 사용자 입력 파트 ===
    tic_name = 'AMAT'
    hs_code = '848690'
    st_date = '2010-01-01'
    end_date = '2025-03-31'
    tickers = [tic_name]
    apikey = 'hT0gAk87j9xZx4PlBApvBqfVL5IahvgV'

    today_str = datetime.today().strftime('%Y-%m-%d')

    db_info = {
        'host': get_db_host(),
        'port': 3307,
        'user' : 'stox7412',
        'password' : 'Apt106503!~',
        'database': 'investar'
    }

    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
    )
    fundq_df = pd.read_sql("SELECT * FROM US_fundq", engine)
    trade_df = pd.read_sql("SELECT * FROM us_trade_monthly_data_with_forecast", engine)

    wide_df = get_FMP_data(tickers, apikey)
    long_df = reshape_FMP_data(wide_df)

    accounting_name = 'revenue'
    FMP_df = long_df[(long_df['ticker'] == tic_name) & (long_df['accounting_item'] == accounting_name)][
        ['date', 'value']].copy()
    FMP_df.rename(columns={'value': tic_name}, inplace=True)

    endog_df = prepare_endog_data(fundq_df, tic_name, st_date, end_date)
    exog_df = prepare_exog_data(trade_df, hs_code)

    # === 매출 forecast ===
    revenue_with_exog, revenue_without_exog = forecast_revenue_with_sarima(endog_df, exog_df, tic_name, st_date, end_date)

    # === PSR forecast ===
    endog_ratio_df = fetch_and_prepare_psr_data(tickers, tic_name, apikey)
    psr_with_exog, psr_without_exog = forecast_psr_with_sarima(endog_ratio_df, exog_df)

    # === 결과 테이블 조립 ===
    longterm_value_forecasts = pd.concat([psr_with_exog, psr_without_exog, revenue_with_exog, revenue_without_exog], axis=1)
    longterm_value_forecasts['value_with_exog'] = longterm_value_forecasts['PSR_quarter_with_exog'] * longterm_value_forecasts['revenue_with_exog']
    longterm_value_forecasts['value_without_exog'] = longterm_value_forecasts['PSR_quarter_with_exog'] * longterm_value_forecasts['revenue_with_exog']

    today_str = datetime.today().strftime('%Y-%m-%d')
    longterm_df = longterm_value_forecasts.reset_index().melt(
        id_vars=['index'], var_name='indicator', value_name='value'
    ).rename(columns={'index':'date'})
    longterm_df['ticker'] = tic_name
    longterm_df['forecast_date'] = today_str

    # === 단기 예측 (LSTM + Prophet) ===

    df_market = get_market_cap(tic_name, apikey, st_date, end_date)

    # 잘못된 to_timestamp 인자를 수정하고 다시 실행

    # 1. 분기별 매출 데이터 재정렬 및 rolling TTM 계산
    df_revenue = FMP_df.sort_values("date").reset_index(drop=True)
    df_revenue["TTM_Revenue"] = df_revenue[tic_name].rolling(window=4).sum()

    # 전제: df_market (일일 시가총액), df_revenue (분기별 TTM 매출)
    df_revenue = df_revenue.sort_values('date')
    df_market = df_market.sort_values('date')

    # LSTM 예측
    lstm_forecast = forecast_ratio_with_lstm(df_market, endog_df)  # endog_df 는 revenue_df 임
    lstm_forecast = lstm_forecast.rename(columns={'forecasted_PSR': 'PSR_daily_lstm'})

    # Prophet 예측
    df_daily_psr = pd.merge_asof(
        df_market,
        endog_df[['date', tic_name]].rename(columns={tic_name: 'TTM_Revenue'}),
        left_on='date',
        right_on='date',
        direction='backward'
    )
    df_daily_psr['PSR'] = df_daily_psr['marketCap'] / df_daily_psr['TTM_Revenue']
    df_daily_psr = df_daily_psr.dropna(subset=['PSR'])

    df_prophet = df_daily_psr.rename(columns={'date': 'ds', 'PSR': 'y'})
    model = Prophet(daily_seasonality=True, yearly_seasonality=True)
    model.fit(df_prophet)
    future = model.make_future_dataframe(periods=90)
    prophet_forecast = model.predict(future)[['ds', 'trend']].rename(
        columns={'ds': 'date', 'trend': 'PSR_daily_prophet'})

    # === 합치기 및 가치 계산 ===
    lstm_forecast_indexed = lstm_forecast.set_index('date')
    prophet_forecast_indexed = prophet_forecast.set_index('date')
    shortterm_value_forecasts = pd.concat([lstm_forecast_indexed, prophet_forecast_indexed], axis=1, join='inner')

    total_revenue_with_exog = revenue_with_exog.sum()
    total_revenue_without_exog = revenue_without_exog.sum()

    shortterm_value_forecasts['revenue_with_exog'] = total_revenue_with_exog
    shortterm_value_forecasts['revenue_without_exog'] = total_revenue_without_exog

    shortterm_value_forecasts['lstm_value_with_exog'] = shortterm_value_forecasts[
                                                            'PSR_daily_lstm'] * total_revenue_with_exog
    shortterm_value_forecasts['lstm_value_without_exog'] = shortterm_value_forecasts[
                                                               'PSR_daily_lstm'] * total_revenue_without_exog
    shortterm_value_forecasts['prophet_value_with_exog'] = shortterm_value_forecasts[
                                                               'PSR_daily_prophet'] * total_revenue_with_exog
    shortterm_value_forecasts['prophet_value_without_exog'] = shortterm_value_forecasts[
                                                                  'PSR_daily_prophet'] * total_revenue_without_exog

    # === Long format 변환
    shortterm_df = shortterm_value_forecasts.reset_index().melt(
        id_vars=['date'],
        var_name='indicator',
        value_name='value'
    )
    shortterm_df['ticker'] = tic_name
    shortterm_df['forecast_date'] = today_str

    # === DB 저장 ===
    save_forecast_to_db(shortterm_df, longterm_df, db_info)

if __name__ == "__main__":
    main()
