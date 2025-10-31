# -*- coding: utf-8 -*-
"""
get_trade_data_by_hscode.py

특정 HS Code와 indicator에 해당하는 무역 데이터를 DB에서 읽어와 trade_df로 반환합니다.
다른 코드에서 import 하여 사용할 수 있도록 설계되었습니다.
"""

import pandas as pd
from sqlalchemy import create_engine, text


def get_trade_data_by_hscode(
    db_info: dict,
    hs_code: str,
    indicator: str = None
) -> pd.DataFrame:
    """
    주어진 HS Code (및 선택적 indicator)에 해당하는 무역 데이터를 DB에서 조회하여 DataFrame으로 반환합니다.

    Parameters
    ----------
    db_info : dict
        예시:
        {
            'host': '192.168.0.230',
            'port': 3307,
            'user': 'root',
            'password': '1234',
            'database': 'investar'
        }

    hs_code : str
        조회할 6자리 HS Code (예: '121120')

    indicator : str, optional
        조회할 지표명 (예: 'expDlr', 'impDlr', 'expDlr_yoy', 등)
        None이면 해당 HS Code의 모든 indicator를 반환.

    Returns
    -------
    trade_df : pd.DataFrame
        columns = ['date', 'root_hs_code', 'indicator', 'value']
        date는 DatetimeIndex로 변환됩니다.
    """

    # --- DB 연결 문자열 생성 ---
    conn_str = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    engine = create_engine(conn_str)

    # --- 쿼리 구성 ---
    base_query = """
        SELECT date, root_hs_code, indicator, value
        FROM korea_monthly_trade_data
        WHERE root_hs_code = :hs_code
    """

    if indicator:
        base_query += " AND indicator = :indicator"

    base_query += " ORDER BY date ASC"

    query = text(base_query)

    # --- 데이터 조회 ---
    params = {'hs_code': hs_code}
    if indicator:
        params['indicator'] = indicator

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params=params)

    # --- 후처리 ---
    if df.empty:
        print(f"[WARN] HS Code {hs_code}, Indicator '{indicator}' 데이터 없음.")
        return df

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    # df = df.set_index('date')

    print(f"[INFO] HS Code={hs_code}, Indicator={indicator or 'ALL'} → {len(df)}행 불러옴")

    return df


# ==============================================
# ✅ 추가된 함수: root_hs_code 유니크 리스트 반환
# ==============================================

def get_unique_hscode_list(db_info: dict) -> list:
    """
    korea_monthly_trade_data 테이블에서 root_hs_code의 유니크한 값을 리스트로 반환합니다.

    Parameters
    ----------
    db_info : dict
        DB 접속 정보 (host, port, user, password, database)

    Returns
    -------
    list
        root_hs_code의 고유값 리스트
    """
    conn_str = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    engine = create_engine(conn_str)

    query = text("""
        SELECT DISTINCT root_hs_code
        FROM korea_monthly_trade_data
        WHERE root_hs_code IS NOT NULL
        ORDER BY root_hs_code ASC
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    hs_list = df['root_hs_code'].dropna().astype(str).unique().tolist()

    print(f"[INFO] 총 {len(hs_list)}개의 고유 HS Code를 불러왔습니다.")
    return hs_list


# 단독 실행 테스트용
# if __name__ == "__main__":
#     db_info = {
#         'host': '192.168.0.230',
#         'port': 3307,
#         'user': 'root',
#         'password': '1234',
#         'database': 'investar'
#     }
#
#     hs_code = "121120"
#     trade_df = get_trade_data_by_hscode(db_info, hs_code)
#     print(trade_df.head())
