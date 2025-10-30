# -*- coding: utf-8 -*-

import pandas as pd


def extract_monthly_exog_var(data_dict: dict) -> pd.DataFrame:
    """
    주어진 데이터(dict)에서 'monthly_raw' DataFrame을 추출하고,
    expDlr_forecast_12m 컬럼을 exog_var로 이름 변경하여 반환합니다.

    Parameters
    ----------
    data_dict : dict
        {'monthly_raw': DataFrame} 형태의 딕셔너리

    Returns
    -------
    pd.DataFrame
        'exog_var'로 컬럼 이름이 바뀐 월별 데이터
    """
    # 1️⃣ 'monthly_raw' 데이터 추출
    if 'monthly_raw' not in data_dict:
        raise KeyError("입력 데이터에 'monthly_raw' 키가 없습니다.")
    df = data_dict['monthly_raw'].copy()

    # 2️⃣ expDlr_forecast_12m → exog_var 로 컬럼 이름 변경
    if 'expDlr_forecast_12m' in df.columns:
        df = df.rename(columns={'expDlr_forecast_12m': 'exog_var'})
    else:
        raise KeyError("DataFrame에 'expDlr_forecast_12m' 컬럼이 없습니다.")

    # 3️⃣ 월별 데이터이므로 DatetimeIndex 확인 및 정렬
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors='coerce')
    df = df.sort_index()

    df = df[["exog_var"]]

    return df
