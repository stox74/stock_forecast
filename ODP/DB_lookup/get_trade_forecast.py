# -*- coding: utf-8 -*-
"""
한국 무역 데이터 조회 모듈 (통합 버전)
- 과거 실제 데이터 조회 (korea_monthly_trade_data)
- 예측 데이터 조회 (korea_monthly_trade_forecast_v2)
"""

import pandas as pd
from sqlalchemy import create_engine
from typing import Optional, List, Union
import sys
import os


# ================================
# 프로젝트 경로 설정
# ================================
def setup_project_path():
    """프로젝트 루트 경로를 Python path에 추가"""
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(current_file))

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    return project_root


# 경로 설정
setup_project_path()

from stock_forecast.DATA.config import get_db_info, get_engine


# ================================
# 1. 과거 실제 데이터 조회 함수
# ================================
def get_trade_data_by_hscode(
        hs_code: Union[str, List[str]],
        indicator: str = 'expDlr',
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        db_info: Optional[dict] = None
) -> pd.DataFrame:
    """
    특정 hs_code(들)에 해당하는 과거 무역 데이터를 가져옵니다.

    Args:
        hs_code: HS Code 또는 HS Code 리스트 (예: '200830' 또는 ['200830', '200899'])
        indicator: 가져올 지표 (기본값: 'expDlr')
                  옵션: 'expDlr', 'impDlr', 'expDlr_yoy', 'impDlr_yoy', 'balPayments'
        start_date: 시작 날짜 (YYYY-MM-DD 형식, 예: '2020-01-01')
        end_date: 종료 날짜 (YYYY-MM-DD 형식)
        db_info: DB 연결 정보 (None이면 config에서 자동 로드)

    Returns:
        pd.DataFrame: date를 index로, hs_code를 컬럼으로 하는 데이터프레임

    Example:
        >>> # 단일 HS Code
        >>> df = get_trade_data_by_hscode('200830')
        >>>
        >>> # 여러 HS Code
        >>> df = get_trade_data_by_hscode(['200830', '200899'])
        >>>
        >>> # 특정 기간
        >>> df = get_trade_data_by_hscode('200830', start_date='2020-01-01')
    """

    # DB 정보 로드
    if db_info is None:
        db_info = get_db_info()

    engine = get_engine(db_info)

    # hs_code를 리스트로 변환
    if isinstance(hs_code, str):
        hs_code_list = [hs_code]
    else:
        hs_code_list = hs_code

    try:
        # SQL 쿼리 작성
        placeholders = ','.join(['%s'] * len(hs_code_list))

        query = f"""
        SELECT 
            date,
            root_hs_code as hs_code,
            value
        FROM 
            korea_monthly_trade_data
        WHERE 
            root_hs_code IN ({placeholders})
            AND indicator = %s
            AND value IS NOT NULL
        """

        params = list(hs_code_list) + [indicator]

        # 날짜 조건 추가
        if start_date:
            query += " AND date >= %s"
            params.append(start_date)

        if end_date:
            query += " AND date <= %s"
            params.append(end_date)

        query += " ORDER BY date, root_hs_code"

        # 데이터 가져오기
        df = pd.read_sql(query, engine, params=tuple(params))

        if df.empty:
            print(f"[경고] 조건에 맞는 데이터가 없습니다.")
            return pd.DataFrame()

        # Long format을 Wide format으로 변환
        df_wide = df.pivot(
            index='date',
            columns='hs_code',
            values='value'
        )

        # 인덱스를 datetime으로 변환
        df_wide.index = pd.to_datetime(df_wide.index)
        df_wide.index.name = 'date'

        # 컬럼명을 문자열로 변환
        df_wide.columns = df_wide.columns.astype(str)

        print(f"[성공] {len(hs_code_list)}개 HS Code, indicator={indicator}: "
              f"{len(df_wide)}개 행, 기간: {df_wide.index.min().strftime('%Y-%m')} ~ {df_wide.index.max().strftime('%Y-%m')}")

        return df_wide

    except Exception as e:
        print(f"[오류] 데이터 로드 실패: {str(e)}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

    finally:
        engine.dispose()


def get_all_indicators_by_hscode(
        hs_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        db_info: Optional[dict] = None
) -> pd.DataFrame:
    """
    특정 hs_code의 모든 지표를 가져옵니다.

    Args:
        hs_code: HS Code
        start_date: 시작 날짜
        end_date: 종료 날짜
        db_info: DB 연결 정보

    Returns:
        pd.DataFrame: date를 index로, 각 indicator를 컬럼으로 하는 데이터프레임
    """

    if db_info is None:
        db_info = get_db_info()

    engine = get_engine(db_info)

    try:
        query = """
                SELECT
                    date, indicator, value
                FROM
                    korea_monthly_trade_data
                WHERE
                    root_hs_code = %s
                  AND value IS NOT NULL \
                """

        params = [hs_code]

        if start_date:
            query += " AND date >= %s"
            params.append(start_date)

        if end_date:
            query += " AND date <= %s"
            params.append(end_date)

        query += " ORDER BY date, indicator"

        df = pd.read_sql(query, engine, params=tuple(params))

        if df.empty:
            print(f"[경고] hs_code={hs_code}에 해당하는 데이터가 없습니다.")
            return pd.DataFrame()

        # Pivot
        df_wide = df.pivot(
            index='date',
            columns='indicator',
            values='value'
        )

        df_wide.index = pd.to_datetime(df_wide.index)
        df_wide.index.name = 'date'

        print(f"[성공] hs_code={hs_code}: {len(df_wide)}개 행, {len(df_wide.columns)}개 지표")
        print(f"지표: {df_wide.columns.tolist()}")

        return df_wide

    except Exception as e:
        print(f"[오류] 데이터 로드 실패: {str(e)}")
        return pd.DataFrame()

    finally:
        engine.dispose()


# ================================
# 2. 예측 데이터 조회 함수
# ================================
def get_trade_forecast_by_hscode(
        hs_code: str,
        forecast_date: Optional[str] = None,
        db_info: Optional[dict] = None
) -> pd.DataFrame:
    """
    특정 hs_code의 예측 데이터를 가져옵니다.

    Args:
        hs_code: HS Code (예: '1703', '5515')
        forecast_date: 예측 기준일 (예: '2026-01-14', None이면 최신 데이터)
        db_info: DB 연결 정보 (None이면 config에서 자동 로드)

    Returns:
        pd.DataFrame: date를 index로, indicator를 컬럼으로 하는 데이터프레임

    Example:
        >>> # 특정 예측 날짜
        >>> df = get_trade_forecast_by_hscode('1703', '2026-01-14')
        >>>
        >>> # 최신 예측
        >>> df = get_trade_forecast_by_hscode('1703')
    """

    # DB 정보 로드
    if db_info is None:
        db_info = get_db_info()

    # forecast_date가 None이면 최신 데이터 사용
    if forecast_date is None:
        forecast_dates = get_available_forecast_dates(hs_code, db_info)
        if not forecast_dates:
            print(f"[경고] hs_code={hs_code}에 대한 예측 데이터가 없습니다.")
            return pd.DataFrame()
        forecast_date = forecast_dates[0]
        print(f"[정보] 가장 최근 forecast_date 사용: {forecast_date}")

    engine = get_engine(db_info)

    try:
        query = """
                SELECT
                    date, indicator, value
                FROM
                    korea_monthly_trade_forecast_v2
                WHERE
                    hs_code = %s
                  AND forecast_date = %s
                ORDER BY
                    date, indicator \
                """

        df = pd.read_sql(query, engine, params=(hs_code, forecast_date))

        if df.empty:
            print(f"[경고] hs_code={hs_code}, forecast_date={forecast_date}에 해당하는 데이터가 없습니다.")
            return pd.DataFrame()

        # Long format을 Wide format으로 변환
        df_wide = df.pivot(
            index='date',
            columns='indicator',
            values='value'
        )

        # 인덱스를 datetime으로 변환
        df_wide.index = pd.to_datetime(df_wide.index)
        df_wide.index.name = 'date'

        # 컬럼 정렬
        column_order = [
            'sarima_expDlr', 'avg5_expDlr', 'prophet_expDlr',
            'ensemble_expDlr', 'lstm_expDlr', 'theta_expDlr', 'ets_expDlr'
        ]
        existing_cols = [col for col in column_order if col in df_wide.columns]
        other_cols = [col for col in df_wide.columns if col not in column_order]
        df_wide = df_wide[existing_cols + other_cols]

        print(f"[성공] hs_code={hs_code}, forecast_date={forecast_date}: "
              f"{len(df_wide)}개 행, {len(df_wide.columns)}개 indicator")

        return df_wide

    except Exception as e:
        print(f"[오류] 데이터 로드 실패: {str(e)}")
        return pd.DataFrame()

    finally:
        engine.dispose()


def get_available_forecast_dates(hs_code: str, db_info: Optional[dict] = None) -> List[str]:
    """
    특정 hs_code에 대해 사용 가능한 forecast_date 목록을 반환합니다.

    Args:
        hs_code: HS Code
        db_info: DB 연결 정보

    Returns:
        list: forecast_date 목록 (최신순)
    """
    if db_info is None:
        db_info = get_db_info()

    engine = get_engine(db_info)

    try:
        query = """
                SELECT DISTINCT forecast_date
                FROM korea_monthly_trade_forecast_v2
                WHERE hs_code = %s
                ORDER BY forecast_date DESC \
                """

        df = pd.read_sql(query, engine, params=(hs_code,))

        return df['forecast_date'].tolist()

    except Exception as e:
        print(f"[오류] forecast_date 조회 실패: {str(e)}")
        return []

    finally:
        engine.dispose()


# ================================
# 3. 통합 조회 함수
# ================================
def get_combined_data_and_forecast(
        hs_code: str,
        indicator: str = 'expDlr',
        forecast_date: Optional[str] = None,
        start_date: Optional[str] = None,
        db_info: Optional[dict] = None
) -> tuple:
    """
    과거 실제 데이터와 예측 데이터를 함께 가져옵니다.

    Args:
        hs_code: HS Code
        indicator: 지표 (기본값: 'expDlr')
        forecast_date: 예측 기준일 (None이면 최신)
        start_date: 과거 데이터 시작일
        db_info: DB 연결 정보

    Returns:
        tuple: (과거 데이터 DataFrame, 예측 데이터 DataFrame)

    Example:
        >>> historical, forecast = get_combined_data_and_forecast('1703')
        >>> print(f"과거: {len(historical)}개 행")
        >>> print(f"예측: {len(forecast)}개 행")
    """

    # 과거 데이터 조회
    historical = get_trade_data_by_hscode(
        hs_code=hs_code,
        indicator=indicator,
        start_date=start_date,
        db_info=db_info
    )

    # 예측 데이터 조회
    forecast = get_trade_forecast_by_hscode(
        hs_code=hs_code,
        forecast_date=forecast_date,
        db_info=db_info
    )

    return historical, forecast


def get_available_hscodes(db_info: Optional[dict] = None) -> List[str]:
    """
    데이터베이스에서 사용 가능한 모든 HS Code 목록을 반환합니다.

    Returns:
        list: HS Code 목록
    """
    if db_info is None:
        db_info = get_db_info()

    engine = get_engine(db_info)

    try:
        query = """
                SELECT DISTINCT root_hs_code
                FROM korea_monthly_trade_data
                ORDER BY root_hs_code \
                """

        df = pd.read_sql(query, engine)
        hs_codes = df['root_hs_code'].astype(str).tolist()

        print(f"[정보] 총 {len(hs_codes)}개의 HS Code 사용 가능")

        return hs_codes

    except Exception as e:
        print(f"[오류] HS Code 목록 조회 실패: {str(e)}")
        return []

    finally:
        engine.dispose()


# ===== 사용 예시 =====
if __name__ == "__main__":

    print("=" * 60)
    print("한국 무역 데이터 조회 모듈 테스트")
    print("=" * 60)

    # 예시 1: 과거 데이터 조회
    print("\n=== 예시 1: 과거 실제 데이터 (단일 HS Code) ===")
    df_hist = get_trade_data_by_hscode('200830', indicator='expDlr')
    if not df_hist.empty:
        print(df_hist.tail())
        print(f"기간: {df_hist.index.min()} ~ {df_hist.index.max()}")

    # 예시 2: 예측 데이터 조회
    print("\n=== 예시 2: 예측 데이터 (최신) ===")
    df_forecast = get_trade_forecast_by_hscode('1703')
    if not df_forecast.empty:
        print(df_forecast.head())
        print(f"컬럼: {df_forecast.columns.tolist()}")

    # 예시 3: 과거 + 예측 통합 조회
    print("\n=== 예시 3: 과거 + 예측 통합 ===")
    hist, forecast = get_combined_data_and_forecast('1703')
    if not hist.empty and not forecast.empty:
        print(f"과거 데이터: {len(hist)}개 행 ({hist.index.min()} ~ {hist.index.max()})")
        print(f"예측 데이터: {len(forecast)}개 행 ({forecast.index.min()} ~ {forecast.index.max()})")