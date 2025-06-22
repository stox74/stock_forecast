import pandas as pd
import numpy as np
import pymysql
import FinanceDataReader as fdr

def get_index_data(ticker, start_date):
    """
    특정 지수 데이터를 가져오고, ticker 컬럼과 변동성(20일, 60일, 120일)을 추가하는 함수.
    """
    df = fdr.DataReader(ticker, start_date)
    df['ticker'] = ticker

    # 일간 수익률 계산
    df['daily_return'] = df['Close'].pct_change()

    # 이동 표준편차(변동성) 계산: 20일, 60일, 120일
    df['volatility_20d'] = df['daily_return'].rolling(window=20).std() * np.sqrt(252)
    df['volatility_60d'] = df['daily_return'].rolling(window=60).std() * np.sqrt(252)
    df['volatility_120d'] = df['daily_return'].rolling(window=120).std() * np.sqrt(252)

    return df

host_num = 'hystox74.synology.me'
user = 'stox7412'
passwd = 'Apt106503!~'
db_name = 'investar'
table_name = 'Market_Index_Data'

def upload_to_db(data_df, host_num, user, passwd, db_name, table_name):
    """
    데이터프레임을 MySQL 데이터베이스 테이블에 업로드하는 함수.
    """
    try:
        # DB 연결 설정
        cnx = pymysql.connect(
            host=host_num,
            port=3307,
            user=user,
            passwd=passwd,
            db=db_name,
            autocommit=True
        )
        cursor = cnx.cursor()

        # 테이블 생성 쿼리
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            Date DATETIME,
            Open FLOAT,
            High FLOAT,
            Low FLOAT,
            Close FLOAT,
            Volume FLOAT,
            ticker VARCHAR(10),
            daily_return FLOAT,
            volatility_20d FLOAT,
            volatility_60d FLOAT,
            volatility_120d FLOAT,
            PRIMARY KEY (Date, ticker)
        );
        """
        cursor.execute(create_table_query)

        # NaN 값 처리: NaN -> None (MySQL에서 NULL로 처리)
        data_df = data_df.replace({np.nan: None})

        # 데이터 삽입
        for _, row in data_df.iterrows():
            # 데이터 매핑 디버깅
            row_data = tuple(row[['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'ticker',
                                  'daily_return', 'volatility_20d', 'volatility_60d', 'volatility_120d']])
            print(f"Attempting to insert row: {row_data}")  # 디버깅용 출력

            insert_query = f"""
            INSERT INTO {table_name} (Date, Open, High, Low, Close, Volume, ticker, daily_return, volatility_20d, volatility_60d, volatility_120d)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                Open = VALUES(Open),
                High = VALUES(High),
                Low = VALUES(Low),
                Close = VALUES(Close),
                Volume = VALUES(Volume),
                daily_return = VALUES(daily_return),
                volatility_20d = VALUES(volatility_20d),
                volatility_60d = VALUES(volatility_60d),
                volatility_120d = VALUES(volatility_120d);
            """
            cursor.execute(insert_query, row_data)

        print(f"데이터가 성공적으로 {table_name} 테이블에 업로드되었습니다.")

    except Exception as e:
        print(f"DB 업로드 실패: {e}")
    finally:
        cursor.close()
        cnx.close()

def update_market_data():
    """
    여러 지수 데이터를 가져와 데이터베이스에 업데이트하는 함수.
    """
    # Step 1: 지수 데이터 수집
    start_date = '1995-01-01'
    tickers = ['KS11', 'KQ11', 'US500', 'RUT', 'NG', 'ZG', 'ZI', 'HG']

    df_list = []
    for ticker in tickers:
        print(f"Fetching data for {ticker}...")
        df_list.append(get_index_data(ticker, start_date))

    # 모든 데이터 병합
    data_df = pd.concat(df_list)
    data_df.reset_index(inplace=True)
    if 'index' in data_df.columns:
        data_df.rename(columns={'index': 'Date'}, inplace=True)  # 인덱스에서 Date로 수정

    # Step 2: DB 연결 정보 및 업로드
    host_num = 'hystox74.synology.me'
    # host_num = '192.168.0.230'
    user = 'stox7412'
    passwd = 'Apt106503!~'
    db_name = 'investar'
    table_name = 'Market_Index_Data'

    upload_to_db(data_df, host_num, user, passwd, db_name, table_name)

# 업데이트 함수 실행
update_market_data()


















#
#
#
#
# import pandas as pd
# import numpy as np
# import pymysql
# import FinanceDataReader as fdr
#
# def get_index_data(ticker, start_date):
#     """
#     특정 지수 데이터를 가져오고, ticker 컬럼과 변동성(20일, 60일, 120일)을 추가하는 함수.
#     """
#     df = fdr.DataReader(ticker, start_date)
#     df['ticker'] = ticker
#
#     # 일간 수익률 계산
#     df['daily_return'] = df['Close'].pct_change()
#
#     # 이동 표준편차(변동성) 계산: 20일, 60일, 120일
#     df['volatility_20d'] = df['daily_return'].rolling(window=20).std() * np.sqrt(252)
#     df['volatility_60d'] = df['daily_return'].rolling(window=60).std() * np.sqrt(252)
#     df['volatility_120d'] = df['daily_return'].rolling(window=120).std() * np.sqrt(252)
#
#     return df
#
# host_num = 'hystox74.synology.me'
# user = 'stox7412'
# passwd = 'Apt106503!~'
# db_name = 'investar'
# table_name = 'Market_Index_Data'
# def upload_to_db(data_df, host_num, user, passwd, db_name, table_name):
#     """
#     데이터프레임을 MySQL 데이터베이스 테이블에 업로드하는 함수.
#     """
#     try:
#         # DB 연결 설정
#         cnx = pymysql.connect(
#             host=host_num,
#             port=3307,
#             user=user,
#             passwd=passwd,
#             db=db_name,
#             autocommit=True
#         )
#         cursor = cnx.cursor()
#
#         # 테이블 생성 쿼리
#         create_table_query = f"""
#         CREATE TABLE IF NOT EXISTS {table_name} (
#             Date DATETIME,
#             Open FLOAT,
#             High FLOAT,
#             Low FLOAT,
#             Close FLOAT,
#             Volume FLOAT,
#             ticker VARCHAR(10),
#             daily_return FLOAT,
#             volatility_20d FLOAT,
#             volatility_60d FLOAT,
#             volatility_120d FLOAT,
#             PRIMARY KEY (Date, ticker)
#         );
#         """
#         cursor.execute(create_table_query)
#
#         # NaN 값 처리: NaN -> None (MySQL에서 NULL로 처리)
#         data_df = data_df.replace({np.nan: None})
#
#         # 데이터 삽입
#         for _, row in data_df.iterrows():
#             insert_query = f"""
#             INSERT INTO {table_name} (Date, Open, High, Low, Close, Volume, ticker, daily_return, volatility_20d, volatility_60d, volatility_120d)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#             ON DUPLICATE KEY UPDATE
#                 Open = VALUES(Open),
#                 High = VALUES(High),
#                 Low = VALUES(Low),
#                 Close = VALUES(Close),
#                 Volume = VALUES(Volume),
#                 daily_return = VALUES(daily_return),
#                 volatility_20d = VALUES(volatility_20d),
#                 volatility_60d = VALUES(volatility_60d),
#                 volatility_120d = VALUES(volatility_120d);
#             """
#             cursor.execute(insert_query, tuple(row))
#
#         print(f"데이터가 성공적으로 {table_name} 테이블에 업로드되었습니다.")
#
#     except Exception as e:
#         print(f"DB 업로드 실패: {e}")
#     finally:
#         cursor.close()
#         cnx.close()
#
# def update_market_data():
#     """
#     여러 지수 데이터를 가져와 데이터베이스에 업데이트하는 함수.
#     """
#     # Step 1: 지수 데이터 수집
#     start_date = '1995-01-01'
#     tickers = ['KS11', 'KQ11', 'US500', 'RUT', 'NG', 'ZG', 'ZI', 'HG']
#
#     df_list = []
#     for ticker in tickers:
#         print(f"Fetching data for {ticker}...")
#         df_list.append(get_index_data(ticker, start_date))
#
#     # 모든 데이터 병합
#     data_df = pd.concat(df_list)
#     data_df.reset_index(inplace=True)
#     data_df.rename(columns={'Date': 'Date'}, inplace=True)
#
#     # Step 2: DB 연결 정보 및 업로드
#     host_num = 'hystox74.synology.me'
#     user = 'stox7412'
#     passwd = 'Apt106503!~'
#     db_name = 'investar'
#     table_name = 'Market_Index_Data'
#
#     upload_to_db(data_df, host_num, user, passwd, db_name, table_name)
#
# # 업데이트 함수 실행
# update_market_data()
