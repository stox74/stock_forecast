import pandas as pd
from sqlalchemy import create_engine

def fetch_trade_data_multi_hscode(db_info: dict,
                                   hs_codes: list,
                                   indicator: str,
                                   table_name: str = 'korea_monthly_trade_data') -> pd.DataFrame:
    """
    여러 HS 코드와 하나의 indicator에 해당하는 무역 데이터를 MySQL/MariaDB에서 조회

    Parameters:
    - db_info (dict): DB 접속 정보 (user, password, host, port, database)
    - hs_codes (list): 조회할 HS 코드 리스트
    - indicator (str): 조회할 지표 이름 (예: 'expDlr', 'impDlr')
    - table_name (str): 조회할 테이블 이름 (기본값: 'korea_monthly_trade_data')

    Returns:
    - pd.DataFrame: 조회된 무역 데이터
    """

    try:
        # DB 엔진 생성
        engine = create_engine(
            f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        )

        # HS 코드 리스트를 안전하게 SQL용 문자열로 변환
        hs_codes_str = ', '.join(f"'{code}'" for code in hs_codes)

        # 쿼리 작성
        query = f"""
            SELECT *
            FROM {table_name}
            WHERE root_hs_code IN ({hs_codes_str})
              AND indicator = '{indicator}'
            ORDER BY root_hs_code, date
        """

        # 쿼리 실행 및 결과 DataFrame으로 변환
        df = pd.read_sql(query, engine)

        # 날짜 컬럼 변환
        df['date'] = pd.to_datetime(df['date'])

        return df

    except Exception as e:
        print(f"\u274c \ub370\uc774\ud130 \uc870\ud68c \uc2e4\ud328: {e}")
        return pd.DataFrame()


def preprocess_quarterly_growth(df: pd.DataFrame) -> pd.DataFrame:
    """
    월별 데이터를 분기별로 집계하고, 전년 동분기 대비 성장률을 계산하는 함수
    """
    df['quarter'] = df['date'].dt.to_period('Q')
    df_quarterly = (
        df.groupby(['root_hs_code', 'quarter'])['value']
        .sum()
        .reset_index()
    )
    df_quarterly['date'] = df_quarterly['quarter'].dt.to_timestamp(how='end')
    df_quarterly.drop(columns=['quarter'], inplace=True)
    df_quarterly['date'] = pd.to_datetime(df_quarterly['date']).dt.date

    df_quarterly = df_quarterly.sort_values(['root_hs_code', 'date'])
    df_quarterly['yoy_value'] = df_quarterly.groupby('root_hs_code')['value'].shift(4)
    df_quarterly['yoy_growth'] = (
                                         (df_quarterly['value'] - df_quarterly['yoy_value']) / df_quarterly['yoy_value']
                                 ) * 100

    return df_quarterly


def create_yoy_growth_pivot(df_quarterly: pd.DataFrame,
                            start_date: str = None,
                            end_date: str = None) -> pd.DataFrame:
    """
    전년 동분기 대비 증가율을 pivot 형태로 변환하고 분석기간을 설정할 수 있는 함수

    Parameters:
    - df_quarterly (DataFrame): 'root_hs_code', 'date', 'yoy_growth' 포함된 데이터
    - start_date (str or None): 분석 시작일 (예: '2015-01-01')
    - end_date (str or None): 분석 종료일 (예: '2023-12-31')

    Returns:
    - pivot_df (DataFrame): 행: date, 열: root_hs_code, 값: yoy_growth
    """
    pivot_df = df_quarterly.pivot(
        index='date',
        columns='root_hs_code',
        values='yoy_growth'
    ).sort_index()

    pivot_df.replace([np.inf, -np.inf], np.nan, inplace=True)
    pivot_df.index = pd.to_datetime(pivot_df.index)

    if start_date:
        pivot_df = pivot_df[pivot_df.index >= pd.to_datetime(start_date)]
    if end_date:
        pivot_df = pivot_df[pivot_df.index <= pd.to_datetime(end_date)]

    return pivot_df