# simple_run.py
from ticker_list import ticker_list
from improved_forecast_system_v2 import process_multiple_tickers
from DATA.stock_invest_function import get_db_host

db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# 한 줄로 실행!
results = process_multiple_tickers(ticker_list, db_info)