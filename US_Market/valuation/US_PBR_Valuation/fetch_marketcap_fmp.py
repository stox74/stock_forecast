# -*- coding: utf-8 -*-

import datetime as dt
import requests
import time

# DEBUG 플래그 및 로그 함수 (필요에 따라 수정)
DEBUG = True


def log(category, message):
    """간단한 로깅 함수"""
    if DEBUG:
        print(f"[{category}] {message}")


def fetch_market_data_yearly(ticker, api_key, start_year=2010):
    """
    주어진 ticker의 연도별 시가총액 데이터를 가져옵니다.

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼 (예: 'AAPL')
    api_key : str
        Financial Modeling Prep API 키
    start_year : int, optional
        데이터 수집 시작 연도 (기본값: 2010)

    Returns:
    --------
    list or None
        시가총액 데이터 리스트, 데이터가 없으면 None
    """
    all_data = []
    current_year = dt.datetime.now().year

    for year in range(start_year, current_year + 1):
        url = f"https://financialmodelingprep.com/api/v3/historical-market-capitalization/{ticker}"
        params = {
            'from': f"{year}-01-01",
            'to': f"{year}-12-31",
            'apikey': api_key
        }

        try:
            # 디버그 모드: 첫 해, 두 번째 해, 현재 연도만 로깅
            if DEBUG and year in (start_year, start_year + 1, current_year):
                log("FMP-MCAP-REQ", f"{ticker} year={year} url={url} apikey=***")

            response = requests.get(url, params=params, timeout=30)

            if DEBUG and year in (start_year, start_year + 1, current_year):
                log("FMP-MCAP-RESP", f"{ticker} year={year} status={response.status_code}")

            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list):
                    all_data.extend(data)

            # API 호출 제한을 위한 대기
            time.sleep(0.3)

        except Exception as e:
            if DEBUG:
                log("FMP-MCAP-EXC", f"{ticker} year={year} exc={e}")
            continue

    if DEBUG:
        log("FMP-MCAP-DONE", f"{ticker} total_records={len(all_data)}")

    return all_data if all_data else None