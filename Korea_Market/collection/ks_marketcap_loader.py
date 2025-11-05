# ks_marketcap_loader.py

import pandas as pd
from datetime import datetime, timedelta
from pandas.tseries.offsets import BDay
from pykrx import stock
from dateutil.relativedelta import *
from sqlalchemy import create_engine
import warnings
from DATA.stock_invest_function import get_db_host  # 사용자 정의 함수
from sqlalchemy.types import String, Float, Date
import pymysql
import FinanceDataReader as fdr

warnings.simplefilter(action='ignore', category=FutureWarning)


# ==============================
# DB 설정
# ==============================
def get_db_engine():
    db_info = {
        'host': get_db_host(),
        'port': 3307,
        'user' : 'stox7412',
        'password' : 'Apt106503!~',
        'database': 'investar'
    }
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return engine


# ==============================
# 시가총액 데이터 수집 함수
# ==============================
def get_cap(base_day):
    date = str(base_day.year) + str(base_day.month).zfill(2) + str(base_day.day).zfill(2)
    limit_num = 1
    while limit_num < 10:
        market_info = stock.get_market_cap(date)
        if market_info['시가총액'].sum() != 0:
            break
        else:
            prev_date = base_day + relativedelta(days=-limit_num)
            date = str(prev_date.year) + str(prev_date.month).zfill(2) + str(prev_date.day).zfill(2)
            limit_num += 1

    market_info.reset_index(inplace=True)
    market_info.rename(columns={'단축코드': '종목코드', '상장주식수': 'Stocks'}, inplace=True)
    return market_info


# ==============================
# 전체 기간 시가총액 데이터프레임 생성
# ==============================
def collect_marketcap(start_date: str, end_date: str) -> pd.DataFrame:
    biz_days = pd.date_range(start=start_date, end=end_date, freq=BDay())
    all_data = []
    for day in biz_days:
        print(f" {day.date()} 시가총액 데이터 수집중...")
        cap_df = get_cap(day)
        cap_df['date'] = day.strftime('%Y-%m-%d')
        all_data.append(cap_df)
    result_df = pd.concat(all_data, ignore_index=True)
    return result_df


# ==============================
# 데이터 전처리 및 long format 변환
# ==============================
def transform_to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    df.rename(columns={'티커': 'ticker', 'Stocks': '유통주식수'}, inplace=True)
    df['ticker'] = 'A' + df['ticker'].astype(str)
    long_df = df.melt(
        id_vars=['date', 'ticker'],
        var_name='indicator',
        value_name='value'
    )
    return long_df


# ==============================
# DB 업로드 함수
# ==============================

def upload_to_db(df: pd.DataFrame, table_name: str, engine):
    # 데이터 타입 정의
    dtype_dict = {
        'date': Date(),
        'ticker': String(10),
        'indicator': String(50),
        'value': Float()
    }

    # date 컬럼을 datetime.date 로 변환
    df['date'] = pd.to_datetime(df['date']).dt.date

    # DB 커넥션 + 트랜잭션 직접 관리
    conn = engine.connect()
    trans = conn.begin()
    try:
        # chunksize 로 나누어 insert (예: 5000 행씩)
        df.to_sql(
            name=table_name,
            con=conn,
            if_exists='append',
            index=False,
            dtype=dtype_dict,
            chunksize=5000
        )
        trans.commit()
        print(f"DB 테이블 [{table_name}] 업로드 완료!")
    except Exception as e:
        print(f"DB 업로드 실패: {e}")
        trans.rollback()
    finally:
        conn.close()



# ==============================
# 📌 메인 실행 함수
# ==============================
def main():
    start_date = "2025-10-01"
    end_date = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    table_name = "ks_listed_company_daily_marketcap"

    # 1) 데이터 수집
    result_df = collect_marketcap(start_date, end_date)

    # 2) long format 변환
    long_df = transform_to_long_format(result_df)

    # 3) DB 업로드
    engine = get_db_engine()
    upload_to_db(long_df, table_name, engine)


if __name__ == "__main__":
    main()
