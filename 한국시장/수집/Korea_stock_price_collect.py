import pandas as pd
import pymysql
from datetime import datetime
from tqdm import tqdm
from pykrx import stock

# 오늘 날짜 가져오기
today_dt = datetime.today().strftime("%Y%m%d")

def read_krx_code():
    """
    KRX로부터 상장기업 목록을 읽어와 데이터프레임으로 반환
    """
    url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
    krx = pd.read_html(url, header=0)[0]
    krx = krx[['종목코드', '회사명']]
    krx = krx.rename(columns={'종목코드': 'code', '회사명': 'company'})
    krx['code'] = krx['code'].map('{:06d}'.format)
    return krx

def fetch_stock_price_data(code_list):
    """
    종목 코드 리스트를 사용하여 시가, 종가, 거래량 등 주식 가격 데이터를 가져온다.
    """
    price_list = []
    for cd in tqdm(code_list, desc="Fetching stock data"):
        price_data = stock.get_market_ohlcv("20240301", today_dt, cd)
        price_data['코드'] = cd
        price_list.append(price_data)
    return pd.concat(price_list)

def setup_database_connection():
    """
    데이터베이스 연결 설정
    """
    host_num = 'hystox74.synology.me'
    return pymysql.connect(host=host_num, port=3307, db='investar',
                           user='stox7412', passwd='Apt106503!~', autocommit=True)

def create_table(cursor):
    """
    데이터베이스에 KSE_Price 테이블 생성
    """
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS KSE_Price(
            date DATE,
            open INT,
            high INT,
            low INT,
            close INT,
            volume INT,
            prc_change FLOAT,
            code VARCHAR(255),
            PRIMARY KEY (date, code)
        )
    ''')

def insert_stock_data(cursor, data):
    """
    중복 데이터를 방지하며 데이터베이스에 주식 데이터를 삽입
    """
    for _, row in tqdm(data.iterrows(), total=len(data), desc="Inserting data"):
        cursor.execute('''
            INSERT IGNORE INTO KSE_Price VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (row['date'], row['open'], row['high'], row['low'], row['close'],
              row['volume'], row['prc_change'], row['code']))

def main():
    # KRX 상장 기업 목록 가져오기
    krx_df = read_krx_code()
    code_list = krx_df['code'].tolist()

    # 주식 데이터 가져오기
    price_df = fetch_stock_price_data(code_list)
    price_df_re = price_df.reset_index()
    price_df_re.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'prc_change', 'code']
    raw_data = price_df_re.fillna('')

    # 데이터베이스 연결 및 작업
    cnx = setup_database_connection()
    cursor = cnx.cursor()

    # 테이블 생성 및 데이터 삽입
    create_table(cursor)
    insert_stock_data(cursor, raw_data)

    # 변경사항 저장 및 연결 종료
    cnx.commit()
    cnx.close()

if __name__ == "__main__":
    main()
