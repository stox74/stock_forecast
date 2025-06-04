import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.types import Float, VARCHAR, DATETIME
import pymysql
import numpy as np
import datetime
from BOK_Function import *
from Config import *
from BOK_Function import get_m_econ_data, get_m_econ_data



# 함수 정의: 데이터 불러오기 및 DB 업데이트
def update_korea_economy_data():
    # Step 1: Excel 데이터 읽기
    econ_idx_df = pd.read_excel(r'C:\Users\MetaM\PycharmProjects\pythonProject3\BOK_Index_List\Economy_Index_Reference.xlsx')
    econ_idx_df['item_code3'] = econ_idx_df['item_code3'].fillna('')
    econ_idx_df = econ_idx_df.rename(columns={'대분류': 'Category'})

    index_name_list = econ_idx_df['Index_Name'].values.tolist()
    cat_list = econ_idx_df['Category'].values.tolist()
    item_code1_list = econ_idx_df['item_code1'].values.tolist()
    item_code2_list = econ_idx_df['item_code2'].values.tolist()
    item_code3_list = econ_idx_df['item_code3'].values.tolist()

    # Step 2: 데이터 가져오기
    start_dt, end_dt, tm_index, n = get_time_series_for_bok(2003, 1, 1, 30)
    inter = 'M'

    df_list = []
    for item_code1, item_code2, item_code3, id_name, cat in zip(item_code1_list, item_code2_list, item_code3_list, index_name_list, cat_list):
        temp_df = get_m_econ_data(n, item_code1, item_code2, item_code3, start_dt, end_dt, inter)
        temp_df = temp_df[0]
        temp_df['name_index'] = id_name
        temp_df['Category'] = cat
        temp_df['DATA_VALUE'] = temp_df['DATA_VALUE'].astype('float64')
        temp_df['Interval'] = 'M'
        temp_df = temp_df[['TIME', 'STAT_CODE', 'name_index', 'ITEM_NAME1', 'DATA_VALUE', 'Interval']].copy()
        temp_df['TIME'] = pd.to_datetime(temp_df['TIME'], format='%Y%m') + pd.offsets.MonthEnd(0)
        temp_df = temp_df.set_index('TIME')
        temp_df.index.name = 'Date'
        temp_df['MoM'] = temp_df['DATA_VALUE'].pct_change()
        temp_df['YoY'] = temp_df['DATA_VALUE'].pct_change(12)
        df_list.append(temp_df)

    df = pd.concat(df_list)

    econ_list = []
    idx_list = df['STAT_CODE'].unique().tolist()
    for idx in idx_list:
        temp = df[df['STAT_CODE'] == idx].copy()
        idx_list_dt = temp.index.tolist()[-1]
        temp['Last_Report_date'] = idx_list_dt
        econ_list.append(temp)

    econ_df = pd.concat(econ_list)
    econ_df.columns = ['Index_Code', 'Index_Name', 'Index_Detail', 'Value', 'freq', 'MoM', 'YoY', 'Report_Date']
    econ_df_re = econ_df.reset_index()
    data_df = pd.merge(econ_df_re, econ_idx_df[['Index_Name', 'Category']], on='Index_Name')

    # DB 연결 설정
    db_user = "stox7412"
    db_password = "Apt106503!~"
    db_host = 'hystox74.synology.me'
    db_port = 3307
    db_name = "investar"
    table_name = "Korea_Economy_Data"

    print("MySQL 데이터베이스에 성공적으로 연결되었습니다!")

    engine = create_engine(f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}")

    try:
        existing_df = pd.read_sql_table(table_name, con=engine)
        print("기존 데이터를 성공적으로 불러왔습니다.")
    except Exception as e:
        print("기존 데이터가 없거나 불러오기 실패:", e)
        existing_df = pd.DataFrame()

    new_df = data_df.copy()
    new_df['Value'] = new_df['Value'].astype(float)
    new_df['MoM'] = new_df['MoM'].astype(float)
    new_df['YoY'] = new_df['YoY'].astype(float)

    if not existing_df.empty:
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined_df = new_df

    combined_df = combined_df.drop_duplicates(subset=['Date', 'Index_Code'], keep='last')

    try:
        dtype = {
            'Date': DATETIME(),
            'Index_Code': VARCHAR(50),
            'Index_Name': VARCHAR(100),
            'Index_Detail': VARCHAR(200),
            'Value': Float(),
            'freq': VARCHAR(10),
            'MoM': Float(),
            'YoY': Float(),
            'Report_Date': DATETIME(),
            'Category': VARCHAR(50)
        }
        combined_df.to_sql(name=table_name, con=engine, if_exists='replace', index=False, dtype=dtype)
        print("데이터가 성공적으로 업데이트되었습니다.")
    except Exception as e:
        print(f"데이터 업데이트 실패: {e}")

# 메인 함수 실행
if __name__ == "__main__":
    update_korea_economy_data()