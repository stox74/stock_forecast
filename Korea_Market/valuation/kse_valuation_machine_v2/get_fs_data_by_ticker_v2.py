# -*- coding: utf-8 -*-
"""
get_fs_data_by_ticker_v2.py

개선된 재무제표 매출 데이터 추출 모듈
- DataGuide(korea_fs_data)와 DART(korea_fs_data_from_DART)를 자동 결합
- 최신 데이터 우선 사용 (DataGuide → DART 순서로 갱신)
- 중복 제거 및 Q4 자동 조정

주요 개선사항:
1. 수동 업로드 없이 자동으로 최신 데이터 확보
2. DataGuide와 DART 데이터를 날짜 기준으로 스마트하게 결합
3. FY를 Q4로 자동 변환하여 순수 분기 매출 계산
"""

from typing import Optional, Dict, Union
import pandas as pd
import pymysql
from sqlalchemy import create_engine, text

__all__ = [
    "extract_quarterly_revenue_auto",
    "get_quarterly_revenue_simple",
    "adjust_fy_to_q4",
    "fetch_table_data"
]


def _get_engine(db_info: Dict):
    """SQLAlchemy 엔진 생성"""
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url, pool_recycle=3600, pool_pre_ping=True)


def fetch_table_data(db_info: Dict, table_name: str) -> pd.DataFrame:
    """테이블 전체 데이터 로드"""
    eng = _get_engine(db_info)
    df = pd.read_sql(text(f"SELECT * FROM {table_name}"), eng)
    if "Date" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"Date": "date"})
    return df


def adjust_fy_to_q4(df: pd.DataFrame) -> pd.DataFrame:
    """
    FY(연간 누적) 데이터를 순수 Q4로 변환
    Q4 = FY - (Q1 + Q2 + Q3)

    Parameters
    ----------
    df : pd.DataFrame
        DART 원본 데이터 (bsns_year, quarter, thstrm_amount 컬럼 필요)

    Returns
    -------
    pd.DataFrame
        Q4로 변환된 데이터
    """
    result_df = df.copy()

    for year in result_df['bsns_year'].unique():
        year_mask = result_df['bsns_year'] == year
        fy_mask = year_mask & (result_df['quarter'] == 'FY')

        if fy_mask.any():
            fy_amount = result_df.loc[fy_mask, 'thstrm_amount'].iloc[0]
            q123_mask = year_mask & result_df['quarter'].isin(['Q1', 'Q2', 'Q3'])
            q123_sum = result_df.loc[q123_mask, 'thstrm_amount'].sum()

            pure_q4 = fy_amount - q123_sum

            result_df.loc[fy_mask, 'quarter'] = 'Q4'
            result_df.loc[fy_mask, 'thstrm_amount'] = pure_q4

    return result_df


def get_quarterly_revenue_simple(
        db_info: Dict,
        ticker: Union[str, int],
        adjust_q4: bool = True
) -> pd.DataFrame:
    """
    DART DB에서 매출 데이터 조회 + Q4 조정

    Parameters
    ----------
    db_info : dict
        DB 연결 정보
    ticker : str or int
        종목 코드 (예: '005930' 또는 5930)
    adjust_q4 : bool
        FY를 Q4로 변환할지 여부 (기본값: True)

    Returns
    -------
    pd.DataFrame
        DART 매출 데이터 (report_date, thstrm_amount, quarter, bsns_year 등)
    """
    database_name = db_info.get("db") or db_info.get("database")

    if isinstance(ticker, int):
        ticker_str = f"{ticker:06d}"
    else:
        ticker_str = str(ticker).zfill(6)

    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=database_name,
        charset="utf8mb4",
    )

    try:
        sql = """
              SELECT *
              FROM korea_fs_data_from_DART
              WHERE ticker = %s
                AND account_id IN ('ifrs_Revenue', 'ifrs-full_Revenue')
              ORDER BY bsns_year, report_date \
              """

        df = pd.read_sql(sql, conn, params=[ticker_str])

        if df.empty:
            return df

        if adjust_q4:
            df = adjust_fy_to_q4(df)

        return df
    finally:
        conn.close()


def extract_quarterly_revenue_auto(
        db_info: Dict,
        ticker: str,
        fs_df: Optional[pd.DataFrame] = None,
        date_col: str = "date",
        value_col: str = "revenue"
) -> pd.DataFrame:
    """
    DataGuide와 DART 데이터를 자동으로 결합하여 최신 매출 데이터 반환

    **작동 방식:**
    1. DataGuide(korea_fs_data)에서 기본 매출 데이터 추출
    2. DART(korea_fs_data_from_DART)에서 최신 매출 데이터 추출
    3. 날짜 기준으로 중복 제거 (DART 데이터 우선)
    4. Date-indexed DataFrame으로 반환

    Parameters
    ----------
    db_info : dict
        DB 연결 정보 {user, password, host, port, database}
    ticker : str
        종목 코드 (예: '005930', 'A005930')
    fs_df : pd.DataFrame, optional
        미리 로드된 korea_fs_data DataFrame (성능 최적화용)
        None이면 DB에서 직접 조회
    date_col : str
        최종 출력 날짜 컬럼명 (기본값: 'date')
    value_col : str
        최종 출력 매출 컬럼명 (기본값: 'revenue')

    Returns
    -------
    pd.DataFrame
        Date-indexed DataFrame
        Columns: ['revenue', 'year', 'quarter', 'year_quarter', 'symbol']

    Examples
    --------
    >>> db_info = {'host': 'localhost', 'port': 3307, 'user': 'user',
    ...            'password': 'pwd', 'database': 'investar'}
    >>> # 방법 1: 개별 조회
    >>> df = extract_quarterly_revenue_auto(db_info, '005930')
    >>>
    >>> # 방법 2: 배치 처리용 (fs_df 재사용)
    >>> fs_df = fetch_table_data(db_info, "korea_fs_data")
    >>> for ticker in tickers:
    ...     df = extract_quarterly_revenue_auto(db_info, ticker, fs_df=fs_df)
    """

    # 1) 티커 정규화
    ticker_clean = ticker.lstrip('A').zfill(6)
    ticker_dg = 'A' + ticker_clean

    # 2) DataGuide 데이터 추출
    if fs_df is None:
        fs_df = fetch_table_data(db_info, "korea_fs_data")

    revenue_dg = fs_df[
        (fs_df['symbol'] == ticker_dg) &
        (fs_df['indicator'] == '매출액(천원)')
        ].copy()

    if not revenue_dg.empty:
        revenue_from_dg = revenue_dg[['date', 'value']].copy()
        revenue_from_dg['date'] = pd.to_datetime(revenue_from_dg['date'], errors='coerce')
        revenue_from_dg['value'] = pd.to_numeric(revenue_from_dg['value'], errors='coerce') * 1000
        revenue_from_dg = revenue_from_dg.dropna()
    else:
        revenue_from_dg = pd.DataFrame(columns=['date', 'value'])

    # 3) DART 데이터 추출
    revenue_df = get_quarterly_revenue_simple(db_info, ticker=ticker_clean, adjust_q4=True)

    if not revenue_df.empty:
        revenue_from_dart = revenue_df[['report_date', 'thstrm_amount']].copy()
        revenue_from_dart['report_date'] = pd.to_datetime(revenue_from_dart['report_date'], errors='coerce')
        revenue_from_dart['thstrm_amount'] = pd.to_numeric(revenue_from_dart['thstrm_amount'], errors='coerce')
        revenue_from_dart = revenue_from_dart.dropna()
    else:
        revenue_from_dart = pd.DataFrame(columns=['report_date', 'thstrm_amount'])

    # 4) 데이터 결합 (컬럼명 통일)
    revenue_from_dg.columns = ['date', 'revenue']
    revenue_from_dart.columns = ['date', 'revenue']

    # DART 데이터를 뒤에 배치하여 중복 시 DART 우선 적용
    revenue_combined = pd.concat(
        [revenue_from_dg, revenue_from_dart],
        axis=0
    ).drop_duplicates(
        subset=['date'],
        keep='last'  # DART 데이터(나중에 추가된 것) 우선
    ).sort_values('date').reset_index(drop=True)

    if revenue_combined.empty:
        empty = pd.DataFrame(columns=[value_col, "year", "quarter", "year_quarter", "symbol"])
        empty.index.name = date_col
        return empty

    # 5) 날짜/분기 파생 변수 생성
    revenue_combined['year'] = revenue_combined['date'].dt.year
    revenue_combined['quarter'] = revenue_combined['date'].dt.quarter
    revenue_combined['year_quarter'] = (
            revenue_combined['year'].astype(str) + "Q" +
            revenue_combined['quarter'].astype(str)
    )
    revenue_combined['symbol'] = ticker_dg

    # 6) Date 인덱스 설정
    revenue_combined = revenue_combined.set_index('date').sort_index()
    revenue_combined.index.name = date_col

    # 7) 최종 컬럼 순서 정리
    result = revenue_combined[[value_col, 'year', 'quarter', 'year_quarter', 'symbol']].copy()

    return result


if __name__ == "__main__":
    # 테스트 코드
    import os

    EXAMPLE_DB = {
        "user": os.getenv("DB_USER", "stox7412"),
        "password": os.getenv("DB_PASSWORD", "Apt106503!~"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "3307")),
        "database": os.getenv("DB_NAME", "investar"),
    }

    try:
        print("=" * 70)
        print("테스트: 삼성전자 매출 데이터 추출")
        print("=" * 70)

        # 방법 1: 개별 조회
        print("\n[방법 1] 개별 조회 (fs_df 없이)")
        df1 = extract_quarterly_revenue_auto(EXAMPLE_DB, '005930')
        print(f"✓ 데이터 개수: {len(df1)}개")
        print("\n최근 10개 데이터:")
        print(df1.tail(10))

        # 방법 2: fs_df 재사용 (배치 처리용)
        print("\n\n[방법 2] fs_df 재사용 (배치 처리용)")
        fs_df = fetch_table_data(EXAMPLE_DB, "korea_fs_data")
        print(f"✓ korea_fs_data 로드 완료: {len(fs_df):,}행")

        df2 = extract_quarterly_revenue_auto(EXAMPLE_DB, 'A005930', fs_df=fs_df)
        print(f"✓ 데이터 개수: {len(df2)}개")
        print("\n최근 10개 데이터:")
        print(df2.tail(10))

        # 두 방법의 결과가 같은지 확인
        print("\n\n[검증]")
        print(f"방법1과 방법2 결과 동일: {df1.equals(df2)}")

    except Exception as e:
        print(f"테스트 실패: {e}")
        import traceback

        traceback.print_exc()