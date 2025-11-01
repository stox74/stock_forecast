# -*- coding: utf-8 -*-

import pandas as pd
from pandas.tseries.offsets import MonthEnd

DEBUG = True

def get_default_month_end_str(offset_months: int = 0) -> str:
    return (pd.Timestamp.today().normalize() + MonthEnd(offset_months)).strftime('%Y-%m-%d')

# 전역 변수
api_key = 'hT0gAk87j9xZx4PlBApvBqfVL5IahvgV'

db_config = {
    'host': None,  # 실행 시 get_db_host()로 설정
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

BATCH_SIZE = 20
start_date_month = '2011-03-01'
