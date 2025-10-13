# -*- coding: utf-8 -*-
from typing import Dict
import pandas as pd
from sqlalchemy import create_engine
import sys

# ===== 기본 설정 =====
BATCH_SIZE_DEFAULT = 20
START_DATE_MONTH = '2011-03-01'
END_DATE_MONTH = (pd.Timestamp.today().normalize() - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
MEASUREMENT_DATE = pd.Timestamp.today().strftime('%Y-%m-%d')  # created_at 일자

def get_db_info() -> Dict[str, str]:
    # 기존 코드의 get_db_host(), 계정 등은 그대로 사용
    from DATA.stock_invest_function import get_db_host  # 기존 프로젝트 함수
    return {
        "host": get_db_host(),
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }

def get_engine(db_info: Dict[str, str]):
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url)

def log(tag: str, msg: str):
    print(f"[{tag}] {msg}", file=sys.stdout, flush=True)
