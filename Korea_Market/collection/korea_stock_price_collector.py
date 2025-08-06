import pandas as pd
import pymysql
from stock_forecast.DATA.stock_invest_function import *
from datetime import datetime
from tqdm import tqdm
from pykrx import stock
import requests
from bs4 import BeautifulSoup
import time

today_dt = datetime.today().strftime("%Y%m%d")

def read_krx_code():
    url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
    krx = pd.read_html(url, header=0, encoding='euc-kr')[0]
    krx = krx[['종목코드', '회사명']].rename(columns={'종목코드': 'code', '회사명': 'company'})
    krx['code'] = krx['code'].astype(str).str.zfill(6)
    return krx

def fetch_stock_price_data(code_list):
    price_list = []
    for cd in tqdm(code_list, desc="Fetching stock data"):
        try:
            price_data = stock.get_market_ohlcv("20130101", "20141231", cd)
            price_data['코드'] = cd
            price_list.append(price_data)
        except Exception as e:
            print(f"⚠️ 데이터 수집 실패: {cd}, 이유: {e}")
    return pd.concat(price_list)

def setup_database_connection():
    host_num = 'hystox74.synology.me'
    return pymysql.connect(
        host=host_num,
        port=3307,
        db='investar',
        user='stox7412',
        passwd='Apt106503!~',
        charset='utf8mb4',
        autocommit=True,
        connect_timeout=30,
        read_timeout=60,
        write_timeout=60
    )

def create_table(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS KSE_Price(
            date DATE,
            open INT,
            high INT,
            low INT,
            close INT,
            volume BIGINT,
            prc_change FLOAT,
            code VARCHAR(255),
            PRIMARY KEY (date, code)
        )
    ''')

def insert_stock_data(cursor, connection, data, batch_size=1000):
    data_tuples = [
        (row['date'], row['open'], row['high'], row['low'], row['close'],
         row['volume'], row['prc_change'], row['code'])
        for _, row in data.iterrows()
    ]

    for i in tqdm(range(0, len(data_tuples), batch_size), desc="Inserting data (batch)"):
        batch = data_tuples[i:i+batch_size]
        try:
            cursor.executemany('''
                INSERT IGNORE INTO KSE_Price (date, open, high, low, close, volume, prc_change, code)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', batch)
        except pymysql.err.OperationalError as e:
            print(f"⚠️ DB 연결 오류 발생, 재시도: {e}")
            try:
                connection.ping(reconnect=True)
                cursor.executemany('''
                    INSERT IGNORE INTO KSE_Price (date, open, high, low, close, volume, prc_change, code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', batch)
            except Exception as retry_e:
                print(f"❌ 재시도 실패. 중단된 배치 범위: {i}-{i+batch_size}, 오류: {retry_e}")
                continue

def main():
    krx_df = read_krx_code()
    code_list = krx_df['code'].tolist()

    price_df = fetch_stock_price_data(code_list)
    price_df_re = price_df.reset_index()
    price_df_re.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'prc_change', 'code']
    raw_data = price_df_re.fillna('')

    cnx = setup_database_connection()
    cursor = cnx.cursor()

    create_table(cursor)
    insert_stock_data(cursor, cnx, raw_data, batch_size=1000)

    cnx.commit()
    cnx.close()

if __name__ == "__main__":
    main()
