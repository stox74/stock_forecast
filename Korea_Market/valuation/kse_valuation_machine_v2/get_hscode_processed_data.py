# -*- coding: utf-8 -*-
"""
get_hscode_precessed_data.py

수출입 HS 코드별 예측 데이터를 불러와 전처리하는 함수 제공.

주요 기능:
- 특정 hs_code의 월별 예측 데이터를 로드
- 분기 완결(말월 존재) 기준으로 분기 집계
- 분기 합계(expDlr_forecast_12m) 산출
- 4분기 전 대비 YoY(%) exogenous 변수 계산
- 모든 반환 DataFrame은 DatetimeIndex('Date')

사용 예시:
    from get_hscode_precessed_data import get_hscode_precessed_data

    db_info = {"user":"USER","password":"PWD","host":"localhost","port":3306,"database":"investar"}
    out = get_hscode_precessed_data(db_info, hs_code="854231")
    qdf = out["quarterly"]       # 분기 데이터 (Date index, columns: expDlr_forecast_12m, year, quarter, year_quarter, root_hs_code)
    exog = out["exog"]           # exogenous YoY (Date index, column: exog_var)
"""

from typing import Dict, Optional
import warnings
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from pandas.tseries.offsets import MonthEnd

__all__ = ["get_hscode_processed_data"]


def _get_engine(db_info: Dict):
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url, pool_recycle=3600, pool_pre_ping=True)


def _fetch_table_data(db_info: Dict, table_name: str) -> pd.DataFrame:
    eng = _get_engine(db_info)
    return pd.read_sql(text(f"SELECT * FROM {table_name}"), eng)


def _ensure_date_index(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        out.index = pd.DatetimeIndex([], name="Date")
        return out
    out = df.copy()
    out["Date"] = pd.to_datetime(out[date_col], errors="coerce")
    out = out.drop(columns=[date_col])
    out = out.set_index("Date").sort_index()
    out.index.name = "Date"
    return out


def get_hscode_processed_data(
    db_info: Dict,
    hs_code: Optional[str],
    table_name: str = "korea_monthly_trade_data_forecast",
    value_col: str = "expDlr_forecast_12m",
) -> Dict[str, pd.DataFrame]:
    """
    HS 코드별 월별 예측 데이터를 불러와 전처리 후 분기 데이터와 YoY exogenous 변수를 반환.

    Parameters
    ----------
    db_info : dict
        DB 접속 정보 {user, password, host, port, database}
    hs_code : str or None
        처리할 HS 코드. None이면 빈 결과 반환.
    table_name : str
        원본 테이블명
    value_col : str
        예측값 컬럼명 (기본 'expDlr_forecast_12m')

    Returns
    -------
    dict with:
        'monthly_raw' : Date-indexed 월별 원본(필터 후) 데이터 (존재 시)
        'quarterly'   : Date-indexed 분기 집계 데이터
        'exog'        : Date-indexed exogenous YoY(%) DataFrame (columns: ['exog_var'])
    """
    if hs_code is None:
        return {"monthly_raw": pd.DataFrame().rename_axis("Date"),
                "quarterly": pd.DataFrame().rename_axis("Date"),
                "exog": pd.DataFrame().rename_axis("Date")}

    # 1) 로드 & 필터
    df = _fetch_table_data(db_info, table_name)
    if df.empty:
        return {"monthly_raw": pd.DataFrame().rename_axis("Date"),
                "quarterly": pd.DataFrame().rename_axis("Date"),
                "exog": pd.DataFrame().rename_axis("Date")}

    if "root_hs_code" not in df.columns:
        raise KeyError(f"'root_hs_code' column not found in {table_name}")
    if "date" not in df.columns and "Date" in df.columns:
        df = df.rename(columns={"Date": "date"})
    if "date" not in df.columns:
        raise KeyError(f"'date' column not found in {table_name}")
    if value_col not in df.columns:
        raise KeyError(f"'{value_col}' column not found in {table_name}")

    export_company = df[df["root_hs_code"] == hs_code].copy()
    if export_company.empty:
        return {"monthly_raw": pd.DataFrame().rename_axis("Date"),
                "quarterly": pd.DataFrame().rename_axis("Date"),
                "exog": pd.DataFrame().rename_axis("Date")}

    # 2) 타입 변환
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        export_company["date"] = pd.to_datetime(export_company["date"], errors="coerce")
        export_company[value_col] = pd.to_numeric(export_company[value_col], errors="coerce")

    export_company = export_company.dropna(subset=[value_col]).sort_values("date")

    # 3) 연/분기/월 파생
    export_company["year"] = export_company["date"].dt.year
    export_company["quarter"] = export_company["date"].dt.quarter
    export_company["month"] = export_company["date"].dt.month
    export_company["year_quarter"] = export_company["year"].astype(str) + "Q" + export_company["quarter"].astype(str)

    # 4) 분기 완결 체크(말월 존재)
    quarter_end_months = {1: 3, 2: 6, 3: 9, 4: 12}
    quarter_check = (
        export_company.groupby(["year", "quarter"])
        .agg(month=("month", "max"), cnt=("date", "count"))
        .reset_index()
    )
    complete_quarters = []
    for _, row in quarter_check.iterrows():
        expected_end_month = quarter_end_months.get(int(row["quarter"]))
        if int(row["month"]) == expected_end_month:
            complete_quarters.append((int(row["year"]), int(row["quarter"])))

    mask_complete = export_company.apply(lambda x: (int(x["year"]), int(x["quarter"])) in complete_quarters, axis=1)
    export_company_filtered = export_company[mask_complete].copy()

    # 5) 분기 집계
    if export_company_filtered.empty:
        quarterly = pd.DataFrame().rename_axis("Date")
    else:
        quarterly = (
            export_company_filtered.groupby(["year", "quarter"], as_index=False)
            .agg({value_col: "sum", "year_quarter": "last", "root_hs_code": "last"})
            .sort_values(["year", "quarter"])
            .reset_index(drop=True)
        )
        # 분기 말일 Date 생성
        end_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarterly["month"] = quarterly["quarter"].map(end_month_map)
        quarterly["Date"] = pd.to_datetime(
            quarterly["year"].astype(str) + "-" + quarterly["month"].astype(str) + "-01"
        ) + MonthEnd(0)

        quarterly = quarterly.rename(columns={value_col: "expDlr_forecast_12m"})
        quarterly = quarterly[["Date", "expDlr_forecast_12m", "year", "quarter", "year_quarter", "root_hs_code"]]
        quarterly = quarterly.set_index("Date").sort_index()
        quarterly.index.name = "Date"

    # 6) YoY(4분기 전 대비) exogenous 계산
    if quarterly.empty:
        exog = pd.DataFrame().rename_axis("Date")
    else:
        exog = quarterly[["expDlr_forecast_12m"]].copy()
        exog["exog_var"] = exog["expDlr_forecast_12m"].pct_change(periods=4) * 100.0
        exog = exog[["exog_var"]].dropna().copy()
        exog.index.name = "Date"

    # 7) 월별 원본을 Date 인덱스로도 제공
    monthly_raw = export_company.copy()
    monthly_raw = monthly_raw[["date", value_col, "year", "quarter", "month", "year_quarter", "root_hs_code"]]
    monthly_raw = monthly_raw.rename(columns={value_col: "expDlr_forecast_12m"})
    monthly_raw = _ensure_date_index(monthly_raw, date_col="date")

    return {"monthly_raw": monthly_raw, "quarterly": quarterly, "exog": exog}
