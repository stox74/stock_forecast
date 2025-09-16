#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
간소화된 데이터 전처리 스크립트
- 분기별 매출 데이터 수집 (API)
- 월별 시가총액 데이터 수집 (API)
- 수출 데이터 수집 (DB)
- 데이터 결합 및 PSR 계산
"""

import requests
import pandas as pd
import calendar
import time
from datetime import datetime
from tqdm import tqdm
import warnings
from sqlalchemy import create_engine

warnings.filterwarnings('ignore')

# stock_invest_function 모듈 import 시도 (선택적)
try:
    from stock_invest_function import get_db_host

    STOCK_FUNCTION_AVAILABLE = True
except ImportError:
    STOCK_FUNCTION_AVAILABLE = False


    def get_db_host():
        return 'localhost'


# ==============================================
# 유틸리티 함수들
# ==============================================

def convert_to_month_end(date_str):
    """날짜를 해당 월의 월말로 변환"""
    try:
        if isinstance(date_str, str):
            date_obj = pd.to_datetime(date_str)
        else:
            date_obj = date_str

        year = date_obj.year
        month = date_obj.month
        last_day = calendar.monthrange(year, month)[1]
        month_end = datetime(year, month, last_day)
        return month_end
    except Exception as e:
        print(f"날짜 변환 오류: {date_str} -> {e}")
        return None


def test_api_connection(api_key):
    """API 연결 테스트"""
    test_url = "https://financialmodelingprep.com/api/v3/income-statement/AAPL"
    test_params = {'limit': 1, 'apikey': api_key, 'period': 'quarter'}

    try:
        response = requests.get(test_url, params=test_params, timeout=10)

        if response.status_code == 401:
            return False, "API 키가 유효하지 않습니다."
        elif response.status_code == 429:
            return False, "API 요청 한도를 초과했습니다."
        elif response.status_code != 200:
            return False, f"API 오류: {response.status_code}"

        data = response.json()
        if isinstance(data, dict) and 'Error Message' in data:
            return False, f"API 오류: {data['Error Message']}"
        elif not data:
            return False, "API에서 빈 응답을 받았습니다."

        return True, "API 연결 성공"

    except Exception as e:
        return False, f"API 연결 실패: {str(e)}"


# ==============================================
# 매출 데이터 수집 함수들
# ==============================================

def fetch_revenue_data(ticker, api_key):
    """분기별 매출 데이터 수집"""
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
    params = {'limit': 200, 'apikey': api_key, 'period': 'quarter'}

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"

        data = response.json()

        if isinstance(data, dict) and 'Error Message' in data:
            return None, f"API 오류: {data['Error Message']}"

        if not data:
            return None, "데이터 없음"

        return data, None

    except Exception as e:
        return None, f"오류: {str(e)}"


def add_revenue_ttm(df):
    """분기별 매출 데이터에 TTM(최근 4분기 합계) 컬럼 추가"""
    df_copy = df.copy()
    df_copy = df_copy.sort_values(['ticker', 'date'])

    ttm_values = []

    for ticker in df_copy['ticker'].unique():
        ticker_data = df_copy[df_copy['ticker'] == ticker].copy()
        ticker_data = ticker_data.sort_values('date')
        ticker_data['revenue_ttm'] = ticker_data['revenue'].rolling(window=4, min_periods=1).sum()
        ttm_values.extend(ticker_data['revenue_ttm'].tolist())

    df_copy['revenue_ttm'] = ttm_values
    return df_copy


def collect_revenue_data(tickers, api_key, request_delay=0.3):
    """여러 종목의 분기별 매출 데이터 수집"""
    print("매출 데이터 수집 중...")
    all_revenue_data = []

    for ticker in tqdm(tickers, desc="Revenue"):
        revenue_data, error = fetch_revenue_data(ticker, api_key)

        if revenue_data is None:
            print(f"   {ticker}: {error}")
            continue

        for item in revenue_data:
            all_revenue_data.append({
                'ticker': ticker,
                'date': item.get('date', ''),
                'calendar_year': item.get('calendarYear', ''),
                'period': item.get('period', ''),
                'revenue': item.get('revenue', 0) if item.get('revenue') is not None else 0,
                'revenue_billions': round((item.get('revenue', 0) or 0) / 1_000_000_000, 2),
            })

        print(f"   {ticker}: {len([d for d in all_revenue_data if d['ticker'] == ticker])}개 분기")
        time.sleep(request_delay)

    # DataFrame 생성 및 처리
    revenue_df = pd.DataFrame(all_revenue_data) if all_revenue_data else pd.DataFrame()

    if not revenue_df.empty:
        revenue_df['date'] = pd.to_datetime(revenue_df['date'])
        revenue_df = revenue_df.sort_values(['ticker', 'date'])
        revenue_df['date_month_end'] = revenue_df['date'].apply(convert_to_month_end)

        # TTM 추가
        revenue_df_with_ttm = add_revenue_ttm(revenue_df)
        revenue_df_with_ttm['revenue_ttm_billions'] = revenue_df_with_ttm['revenue_ttm'] / 1_000_000_000
        revenue_df_with_ttm['date_month_end'] = revenue_df_with_ttm['date'].apply(convert_to_month_end)

        print(f"매출 데이터 수집 완료: {len(revenue_df_with_ttm)} 레코드")
        return revenue_df_with_ttm

    return pd.DataFrame()


# ==============================================
# 시가총액 데이터 수집 함수들
# ==============================================

def fetch_market_data_yearly(ticker, api_key, start_year=2010):
    """연도별로 세분화해서 시가총액 데이터 수집"""
    all_data = []
    current_year = datetime.now().year

    for year in range(start_year, current_year + 1):
        start_date_str = f"{year}-01-01"
        end_date_str = f"{year}-12-31"

        url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
        params = {'from': start_date_str, 'to': end_date_str, 'apikey': api_key}

        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    all_data.extend(data)
            time.sleep(0.3)
        except Exception as e:
            print(f"  {year}년 오류: {str(e)}")

    return all_data if all_data else None, None


def process_daily_to_monthly_market_data(daily_data, ticker):
    """일별 시가총액 데이터를 월말 기준으로 변환"""
    if not daily_data:
        return pd.DataFrame()

    df = pd.DataFrame(daily_data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    df['year_month'] = df['date'].dt.to_period('M')

    monthly_data = []

    for year_month in df['year_month'].unique():
        month_data = df[df['year_month'] == year_month]
        last_day_data = month_data.loc[month_data['date'].idxmax()]

        monthly_data.append({
            'ticker': ticker,
            'date': last_day_data['date'],
            'market_cap': last_day_data['marketCap'],
            'market_cap_billions': round(last_day_data['marketCap'] / 1_000_000_000, 2),
        })

    return pd.DataFrame(monthly_data)


def collect_market_cap_data(tickers, api_key, start_year=2010):
    """여러 종목의 월별 시가총액 데이터 수집"""
    print("시가총액 데이터 수집 중...")
    all_market_data = []

    for ticker in tqdm(tickers, desc="Market Cap"):
        # 연도별 데이터 수집
        data, error = fetch_market_data_yearly(ticker, api_key, start_year)

        if not data:
            print(f"   {ticker}: 데이터 수집 실패")
            continue

        # 월별 데이터로 변환
        monthly_df = process_daily_to_monthly_market_data(data, ticker)

        if not monthly_df.empty:
            monthly_df['date_month_end'] = monthly_df['date'].apply(convert_to_month_end)
            all_market_data.append(monthly_df)
            print(f"   {ticker}: {len(monthly_df)}개 월")

    if all_market_data:
        market_df = pd.concat(all_market_data, ignore_index=True)
        market_df = market_df.sort_values(['ticker', 'date_month_end'])
        print(f"시가총액 데이터 수집 완료: {len(market_df)} 레코드")
        return market_df

    return pd.DataFrame()


# ==============================================
# 수출 데이터 수집 함수들
# ==============================================

def get_hs_data(hs_code_6d, db_info):
    """HS Code로 무역 데이터 추출"""
    try:
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        query = f"""
        SELECT * FROM us_trade_monthly_data_with_forecast
        WHERE hs_code_6d = '{hs_code_6d}'
        ORDER BY date DESC
        """

        df = pd.read_sql(query, engine)
        engine.dispose()

        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])

        return df

    except Exception as e:
        print(f"DB 조회 오류: {str(e)}")
        return pd.DataFrame()


def get_latest_input_date_data(df):
    """DataFrame에서 input_date가 가장 최근인 데이터만 추출"""
    if 'input_date' not in df.columns:
        print("Error: 'input_date' 컬럼이 존재하지 않습니다.")
        return pd.DataFrame()

    df_copy = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df_copy['input_date']):
        df_copy['input_date'] = pd.to_datetime(df_copy['input_date'])

    latest_date = df_copy['input_date'].max()
    latest_data = df_copy[df_copy['input_date'] == latest_date].copy()

    print(f"가장 최근 input_date: {latest_date.strftime('%Y-%m-%d')}")
    print(f"해당 날짜의 데이터: {len(latest_data):,}개")

    return latest_data


def collect_export_data(hs_code, db_info):
    """수출 데이터 수집"""
    print(f"수출 데이터 수집 중... (HS Code: {hs_code})")

    export_df = get_hs_data(hs_code, db_info)

    if export_df.empty:
        print("수출 데이터 수집 실패")
        return pd.DataFrame()

    latest_export_data = get_latest_input_date_data(export_df)
    latest_export_data = latest_export_data.sort_values('date').reset_index(drop=True)

    if not latest_export_data.empty:
        latest_export_data['date'] = pd.to_datetime(latest_export_data['date'])
        latest_export_data['date_month_end'] = latest_export_data['date'].apply(convert_to_month_end)
        print(f"수출 데이터 수집 완료: {len(latest_export_data)} 레코드")

    return latest_export_data


# ==============================================
# 데이터 결합 및 PSR 계산 함수들
# ==============================================

def merge_revenue_market_data(revenue_df, market_df):
    """매출과 시가총액 데이터 결합"""
    print("매출 및 시가총액 데이터 결합 중...")

    # 필요한 컬럼만 추출
    ttm_data = revenue_df[['ticker', 'date_month_end', 'revenue_billions', 'revenue_ttm_billions']].copy()
    market_data = market_df[['ticker', 'date_month_end', 'market_cap_billions']].copy()

    # 데이터 병합
    merged_data = pd.merge(market_data, ttm_data, on=['ticker', 'date_month_end'], how='left')
    merged_data = merged_data.sort_values(['ticker', 'date_month_end']).reset_index(drop=True)

    # Forward fill 적용
    merged_data['revenue_ttm_billions'] = merged_data.groupby('ticker')['revenue_ttm_billions'].ffill(limit=3)
    merged_data['revenue_billions'] = merged_data['revenue_billions'].ffill(limit=3)

    # 결측치 제거
    merged_data = merged_data.dropna(subset=['revenue_ttm_billions']).reset_index(drop=True)

    print(f"데이터 결합 완료: {len(merged_data)} 레코드")
    return merged_data


def calculate_psr_with_shift(merged_data):
    """TTM shift 및 PSR 계산"""
    print("PSR 계산 중...")

    # revenue_ttm_billions를 2개월 뒤로 shift
    merged_data['revenue_ttm_shift'] = merged_data.groupby('ticker')['revenue_ttm_billions'].shift(2)

    # PSR 계산 (0으로 나누기 방지)
    merged_data['PSR_ttm'] = merged_data['market_cap_billions'] / merged_data['revenue_ttm_shift']

    # NaN 값 제거 - PSR 계산이 불가능한 경우만 제거 (수출 데이터만 있는 경우는 유지)
    before_count = len(merged_data)
    # market_cap과 revenue가 모두 있는데 PSR이 계산 안 된 경우만 제거
    merged_data = merged_data.dropna(subset=['revenue_ttm_shift', 'PSR_ttm'], how='all').reset_index(drop=True)
    after_count = len(merged_data)

    print(f"PSR 계산 완료: {after_count} 레코드 (제거된 레코드: {before_count - after_count}개)")
    return merged_data


def merge_with_export_data(merged_data, export_data):
    """최종 데이터와 수출 데이터 결합"""
    print("수출 데이터와 결합 중...")

    if export_data.empty:
        print("수출 데이터가 없어 수출 컬럼에 NaN을 추가합니다.")
        merged_data['hs_code_6d'] = None
        merged_data['expDlr'] = pd.NA
        return merged_data

    # 수출 데이터에서 필요한 컬럼만 추출
    export_subset = export_data[['date_month_end', 'hs_code_6d', 'expDlr']].copy()

    # 데이터 결합 (outer join으로 변경하여 모든 데이터 유지)
    final_data = pd.merge(merged_data, export_subset, on='date_month_end', how='outer')

    # 날짜순 정렬
    final_data = final_data.sort_values('date_month_end').reset_index(drop=True)

    # 매출/시가총액 데이터가 없는 경우를 위해 ticker 정보 채우기
    if 'ticker' in final_data.columns:
        # 가장 많이 나타나는 ticker로 NaN 값 채우기
        most_common_ticker = final_data['ticker'].mode().iloc[0] if not final_data['ticker'].mode().empty else None
        if most_common_ticker:
            final_data['ticker'] = final_data['ticker'].fillna(most_common_ticker)

    # 통계 출력
    export_matched = final_data['expDlr'].notna().sum()
    market_matched = final_data['market_cap_billions'].notna().sum()

    print(f"최종 데이터 결합 완료: {len(final_data)} 레코드")
    print(f"   수출 데이터: {export_matched}개")
    print(f"   시가총액 데이터: {market_matched}개")
    print(f"   수출 예측치 포함: {export_matched - market_matched}개")

    return final_data


# ==============================================
# 메인 실행 함수
# ==============================================

def run_data_preprocessing(tickers, api_key, hs_code=None, db_info=None, start_year=2010):
    """
    데이터 전처리 메인 실행 함수

    Parameters:
    - tickers (list): 분석할 종목 리스트 (예: ['AAPL', 'MSFT'])
    - api_key (str): Financial Modeling Prep API 키
    - hs_code (str, optional): HS 코드 (수출 데이터용)
    - db_info (dict, optional): 데이터베이스 연결 정보
    - start_year (int): 데이터 수집 시작 연도 (기본값: 2010)

    Returns:
    - pd.DataFrame: 최종 결합된 데이터
    """
    print("=" * 70)
    print("데이터 전처리 시작")
    print(f"대상 종목: {', '.join(tickers)}")
    if hs_code:
        print(f"HS Code: {hs_code}")
    print("=" * 70)

    # API 연결 테스트
    print("API 연결 테스트 중...")
    api_ok, api_message = test_api_connection(api_key)
    print(api_message)

    if not api_ok:
        print("API 연결에 실패했지만 계속 진행합니다.")

    # 수출 데이터 먼저 수집 (예측치 확인을 위해)
    export_data = pd.DataFrame()
    if hs_code and db_info:
        export_data = collect_export_data(hs_code, db_info)
        if export_data.empty:
            print("수출 데이터 수집에 실패했습니다.")
    elif hs_code and not db_info:
        print("HS Code는 제공되었지만 DB 정보가 없어 수출 데이터를 수집하지 않습니다.")
    elif not hs_code:
        print("HS Code가 제공되지 않아 수출 데이터를 수집하지 않습니다.")

    # 1. 매출 데이터 수집
    revenue_df = collect_revenue_data(tickers, api_key)
    if revenue_df.empty:
        print("매출 데이터 수집 실패")
        # 수출 데이터만 있는 경우 처리
        if not export_data.empty:
            print("수출 데이터만으로 결과 반환")
            export_data['ticker'] = tickers[0] if tickers else 'UNKNOWN'
            export_data['market_cap_billions'] = pd.NA
            export_data['revenue_billions'] = pd.NA
            export_data['revenue_ttm_billions'] = pd.NA
            export_data['revenue_ttm_shift'] = pd.NA
            export_data['PSR_ttm'] = pd.NA
            return export_data
        return None

    # 2. 시가총액 데이터 수집
    market_df = collect_market_cap_data(tickers, api_key, start_year)
    if market_df.empty:
        print("시가총액 데이터 수집 실패")
        # 수출 데이터만 있는 경우 처리
        if not export_data.empty:
            print("수출 데이터만으로 결과 반환")
            export_data['ticker'] = tickers[0] if tickers else 'UNKNOWN'
            export_data['market_cap_billions'] = pd.NA
            export_data['revenue_billions'] = pd.NA
            export_data['revenue_ttm_billions'] = pd.NA
            export_data['revenue_ttm_shift'] = pd.NA
            export_data['PSR_ttm'] = pd.NA
            return export_data
        return None

    # 3. 매출 + 시가총액 데이터 결합
    merged_data = merge_revenue_market_data(revenue_df, market_df)

    # 4. PSR 계산
    merged_data = calculate_psr_with_shift(merged_data)

    # 5. 최종 데이터 결합 (수출 데이터 포함, outer join으로 예측치 보존)
    final_data = merge_with_export_data(merged_data, export_data)

    print("=" * 70)
    print("데이터 전처리 완료!")
    print(f"최종 데이터: {len(final_data)} 레코드")
    if not export_data.empty:
        future_predictions = final_data[final_data['market_cap_billions'].isna() & final_data['expDlr'].notna()]
        if len(future_predictions) > 0:
            print(f"수출 예측치만 있는 미래 데이터: {len(future_predictions)} 레코드")
    print("=" * 70)

    return final_data


def run_simple_preprocessing(ticker, api_key, start_year=2010):
    """
    단일 종목 간단 전처리 함수 (수출 데이터 제외)

    Parameters:
    - ticker (str): 분석할 종목 (예: 'AAPL')
    - api_key (str): Financial Modeling Prep API 키
    - start_year (int): 데이터 수집 시작 연도 (기본값: 2010)

    Returns:
    - pd.DataFrame: 매출 + 시가총액 + PSR 데이터
    """
    return run_data_preprocessing([ticker], api_key, start_year=start_year)