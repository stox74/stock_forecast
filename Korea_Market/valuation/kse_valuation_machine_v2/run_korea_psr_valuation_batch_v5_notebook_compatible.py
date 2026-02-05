# -*- coding: utf-8 -*-
"""
Korea valuation batch runner v5 - Notebook Compatible
노트북과 동일한 예측 결과를 내도록 수정된 버전

주요 변경사항:
1. 데이터 전처리: keep='first' (DataGuide 우선) → 노트북과 동일
2. ETS/Theta/LSTM 예측: forecast_ets() 직접 호출 → 노트북과 동일
3. 시계열 인덱스: PeriodIndex 명시 → 노트북과 동일
4. 수동 예측 시작일 지정: 모든 종목 날짜 통일
"""

import gc
import traceback
import pandas as pd
import numpy as np
from datetime import datetime
import sys, os
from typing import Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ============================================================
# 예측 날짜 설정 (수동 지정)
# ============================================================
# 모든 종목에 동일한 예측 시작일을 적용하여 날짜 통일
# None으로 설정하면 자동으로 마지막 데이터의 다음 분기/월부터 예측

FORECAST_CONFIG = {
    # Revenue 예측 시작일 (분기말: 03-31, 06-30, 09-30, 12-31)
    'revenue_start_date': '2025-12-31',  # 예: 2025년 4분기부터 예측

    # PSR 예측 시작일 (월말)
    'psr_start_date': '2026-01-31',  # 예: 2026년 1월부터 예측

    # 예측 기간
    'horizon_quarters': 6,  # Revenue 예측 분기 수
    'psr_horizon_months': 24,  # PSR 예측 월 수
}

# 자동 모드로 전환하려면 아래 주석 해제
# FORECAST_CONFIG = {
#     'revenue_start_date': None,
#     'psr_start_date': None,
#     'horizon_quarters': 6,
#     'psr_horizon_months': 24,
# }
# ============================================================

from DATA.stock_invest_function import *
from get_market_cap_by_ticker import get_market_cap_by_ticker

# 노트북과 동일한 핵심 함수들 직접 import
from DATA.universal_ts_forecast_function import (
    forecast_ets,
    forecast_theta,
    infer_freq_alias,
    seasonal_periods_from_freq,
    ensure_datetime_index_df
)

# 기존 모듈들
from get_fs_data_by_ticker_v2 import fetch_table_data
from get_hscode_processed_data import get_hscode_processed_data
from get_monthly_export_data import extract_monthly_exog_var
from get_revenue_export_joined_table import get_revenue_export_joined_table
from sarima_endog_forecast import forecast_endog_with_optional_exog
from sarima_endog_forecast import forecast_endog_fill_tail
from get_forecasted_revenue_df import build_forecast_df_from_out
from revenue_forecast_all_package import forecast_revenue_lstm, forecast_revenue_prophet
from get_revenue_ttm_df import get_revenue_ttm_df
from get_psr_from_mc_and_rev import build_psr_series
from psr_forecast_runner import forecast_psr_all_models
from ticker_list import get_ticker_list
from upload_valuation_longform import upload_valuation_longform
from valuation_forecast import compute_valuation_forecast

db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

EXOG_Q_TRANSFORM = "pct"
ERROR_RECORDS = []


def extract_quarterly_revenue_notebook_style(
        db_info: dict,
        ticker: str,
        fs_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    노트북 스타일의 매출 데이터 추출

    핵심 차이점:
    - keep='first' 사용 (DataGuide 우선) ← 노트북과 동일!
    - 수동 단위 변환 (천원 → 원)
    """
    import pymysql

    # 티커 정규화
    ticker_clean = ticker.lstrip('A').zfill(6)
    ticker_dg = 'A' + ticker_clean

    # 1) DataGuide 데이터
    if fs_df is None:
        fs_df = fetch_table_data(db_info, "korea_fs_data")

    revenue_dg = fs_df[
        (fs_df['symbol'] == ticker_dg) &
        (fs_df['indicator'] == '매출액(천원)')
        ].copy()

    if not revenue_dg.empty:
        revenue_from_dg = revenue_dg[['date', 'value']].copy()
        revenue_from_dg['date'] = pd.to_datetime(revenue_from_dg['date'], errors='coerce')
        revenue_from_dg['value'] = pd.to_numeric(revenue_from_dg['value'], errors='coerce') * 1000  # 천원 → 원
        revenue_from_dg = revenue_from_dg.dropna()
    else:
        revenue_from_dg = pd.DataFrame(columns=['date', 'value'])

    # 2) DART 데이터 (Q4 조정 포함)
    conn = pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        db=db_info.get("db", db_info.get("database")),
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
        revenue_df = pd.read_sql(sql, conn, params=[ticker_clean])

        if not revenue_df.empty:
            # FY → Q4 변환
            revenue_df = _adjust_fy_to_q4(revenue_df)
            revenue_from_dart = revenue_df[['report_date', 'thstrm_amount']].copy()
            revenue_from_dart['report_date'] = pd.to_datetime(revenue_from_dart['report_date'], errors='coerce')
            revenue_from_dart['thstrm_amount'] = pd.to_numeric(revenue_from_dart['thstrm_amount'], errors='coerce')
            revenue_from_dart = revenue_from_dart.dropna()
        else:
            revenue_from_dart = pd.DataFrame(columns=['report_date', 'thstrm_amount'])
    finally:
        conn.close()

    # 3) 결합 (노트북 방식: keep='first' - DataGuide 우선!)
    revenue_from_dg.columns = ['date', 'revenue']
    revenue_from_dart.columns = ['date', 'revenue']

    revenue_combined = pd.concat(
        [revenue_from_dg, revenue_from_dart],
        axis=0
    ).drop_duplicates(
        subset=['date'],
        keep='first'  # ← 노트북과 동일: DataGuide 우선!
    ).sort_values('date').reset_index(drop=True)

    if revenue_combined.empty:
        return pd.DataFrame(columns=['revenue', 'year', 'quarter', 'year_quarter', 'symbol'])

    # 4) 날짜/분기 파생 변수 생성
    revenue_combined['year'] = revenue_combined['date'].dt.year
    revenue_combined['quarter'] = revenue_combined['date'].dt.quarter
    revenue_combined['year_quarter'] = (
            revenue_combined['year'].astype(str) + "Q" +
            revenue_combined['quarter'].astype(str)
    )
    revenue_combined['symbol'] = ticker_dg

    # 5) Date 인덱스 설정
    revenue_combined = revenue_combined.set_index('date').sort_index()
    revenue_combined.index.name = 'date'

    return revenue_combined[['revenue', 'year', 'quarter', 'year_quarter', 'symbol']].copy()


def _adjust_fy_to_q4(df: pd.DataFrame) -> pd.DataFrame:
    """FY를 Q4로 변환"""
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


def convert_to_period_index(df: pd.DataFrame, value_col: str = 'revenue') -> pd.Series:
    """
    노트북 스타일: DatetimeIndex → PeriodIndex 변환

    핵심: freq='Q' 명시적 설정으로 계절성 감지 정확도 향상
    """
    df = df.copy()

    # DatetimeIndex 확인
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors='coerce')

    df = df[~df.index.isna()].sort_index()

    # 분기 정보 추출
    df['year'] = df.index.year
    df['quarter'] = df.index.quarter
    df['period'] = df['year'].astype(str) + 'Q' + df['quarter'].astype(str)

    # PeriodIndex 생성 (freq='Q' 명시!)
    df['period'] = pd.PeriodIndex(df['period'], freq='Q')
    df = df.set_index('period').sort_index()

    # 중복 제거
    if df.index.duplicated().any():
        print(f"  ⚠️  중복 인덱스 발견, 마지막 값 유지")
        df = df[~df.index.duplicated(keep='last')]

    # Series 반환
    if value_col in df.columns:
        series = df[value_col].copy()
    elif 'endog_var' in df.columns:
        series = df['endog_var'].copy()
    else:
        series = df.iloc[:, 0].copy()

    return series


def forecast_ets_notebook_style(series: pd.Series, forecast_horizon: int = 9,
                                forecast_start_date: str = None) -> pd.DataFrame:
    """
    노트북 스타일 ETS 예측

    핵심 차이점:
    1. PeriodIndex 사용
    2. m 값 명시적 계산
    3. forecast_ets() 직접 호출
    4. 수동 예측 시작일 지원
    """
    from date_standardization import get_standard_quarter_dates

    # 계절성 파라미터 계산 - PeriodIndex 처리 개선
    if isinstance(series.index, pd.PeriodIndex):
        # PeriodIndex는 freq 속성을 직접 사용
        freq = series.index.freq
        m = seasonal_periods_from_freq(freq)
        inferred_freq = freq
    else:
        inferred_freq = infer_freq_alias(series.index)
        m = seasonal_periods_from_freq(inferred_freq)

    print(f"  - ETS 예측 중... (freq={inferred_freq}, m={m})")

    # 직접 호출 (래퍼 사용 안 함!)
    ets_result = forecast_ets(
        y=series,
        forecast_horizon=forecast_horizon,
        m=m,
        try_transforms=True
    )

    # 결과를 DataFrame으로 변환
    forecast_values = ets_result['forecast']

    # 수동 시작일이 지정되었으면 사용, 아니면 자동 생성
    if forecast_start_date is not None:
        # 수동 지정된 날짜부터 시작
        last_actual = pd.Timestamp(series.index.max().to_timestamp('Q'))
        datetime_index = get_standard_quarter_dates(
            last_actual.strftime('%Y-%m-%d'),
            forecast_horizon,
            forecast_start_date=forecast_start_date
        )
    else:
        # 자동 생성
        forecast_index = ets_result.get('forecast_index', None)

        if forecast_index is None:
            # 인덱스 생성
            last_period = series.index.max()
            forecast_index = pd.period_range(
                last_period + 1,
                periods=forecast_horizon,
                freq='Q'
            )

        # PeriodIndex를 DatetimeIndex로 변환
        if isinstance(forecast_index, pd.PeriodIndex):
            # 분기 종료일로 변환 (TTM 계산 호환성)
            datetime_index = forecast_index.to_timestamp('Q')
        else:
            # 이미 DatetimeIndex인 경우 그대로 사용
            datetime_index = pd.DatetimeIndex(forecast_index)

    return pd.DataFrame(
        forecast_values,
        index=datetime_index,
        columns=['revenue_with_noexog']
    )


def forecast_theta_notebook_style(series: pd.Series, forecast_horizon: int = 9,
                                  forecast_start_date: str = None) -> pd.DataFrame:
    """노트북 스타일 Theta 예측"""
    from date_standardization import get_standard_quarter_dates

    # 계절성 파라미터 계산 - PeriodIndex 처리 개선
    if isinstance(series.index, pd.PeriodIndex):
        freq = series.index.freq
        m = seasonal_periods_from_freq(freq)
        inferred_freq = freq
    else:
        inferred_freq = infer_freq_alias(series.index)
        m = seasonal_periods_from_freq(inferred_freq)

    print(f"  - Theta 예측 중... (freq={inferred_freq}, m={m})")

    theta_result = forecast_theta(
        y=series,
        forecast_horizon=forecast_horizon,
        m=m,
        try_transforms=True
    )

    forecast_values = theta_result['forecast']

    # 수동 시작일이 지정되었으면 사용, 아니면 자동 생성
    if forecast_start_date is not None:
        last_actual = pd.Timestamp(series.index.max().to_timestamp('Q'))
        datetime_index = get_standard_quarter_dates(
            last_actual.strftime('%Y-%m-%d'),
            forecast_horizon,
            forecast_start_date=forecast_start_date
        )
    else:
        forecast_index = theta_result.get('forecast_index', None)

        if forecast_index is None:
            last_period = series.index.max()
            forecast_index = pd.period_range(
                last_period + 1,
                periods=forecast_horizon,
                freq='Q'
            )

        # PeriodIndex를 DatetimeIndex로 변환
        if isinstance(forecast_index, pd.PeriodIndex):
            # 분기 종료일로 변환 (TTM 계산 호환성)
            datetime_index = forecast_index.to_timestamp('Q')
        else:
            # 이미 DatetimeIndex인 경우 그대로 사용
            datetime_index = pd.DatetimeIndex(forecast_index)

    return pd.DataFrame(
        forecast_values,
        index=datetime_index,
        columns=['revenue_with_noexog']
    )


def _safe_prophet_forecast(final_combined_data, horizon: int = 5):
    try:
        return forecast_revenue_prophet(final_combined_data, horizon=horizon)
    except Exception:
        return None


def _get_exog_from_export(df_export: Optional[dict]) -> Optional[pd.DataFrame]:
    if isinstance(df_export, dict) and 'quarterly' in df_export:
        return df_export['quarterly']
    return None


def _transform_exog_col(df: pd.DataFrame, col: str, periods: int, method: str = "pct") -> pd.DataFrame:
    if df is None or col not in df.columns:
        return df
    out = df.copy()
    out = out.sort_index()
    if method == "pct":
        out[col] = out[col].pct_change(periods)
    elif method == "diff":
        out[col] = out[col].diff(periods)
    else:
        raise ValueError("method must be 'pct' or 'diff'")
    return out.dropna()


def _cleanup(*args):
    """메모리 즉시 정리"""
    for obj in args:
        try:
            del obj
        except:
            pass
    gc.collect()


def _validate_df_mc(df_mc: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """df_mc 컬럼명 검증 및 표준화"""
    if df_mc is None or df_mc.empty:
        raise ValueError(f"[{ticker}] df_mc is empty or None")

    if 'value' not in df_mc.columns:
        col_mapping = {col.lower(): col for col in df_mc.columns}
        possible_names = ['marketcap', 'market_cap', 'cap', 'mc', 'value']

        for name in possible_names:
            if name in col_mapping:
                df_mc = df_mc.rename(columns={col_mapping[name]: 'value'})
                return df_mc

        raise KeyError(f"[{ticker}] df_mc columns={df_mc.columns.tolist()}, 'value' column not found")

    return df_mc


def process_single_ticker(
        ticker: str,
        hs_code: Optional[str],
        db_info: dict,
        fs_df: Optional[pd.DataFrame] = None,
        table_name: str = "Korea_company_valuation_ver2",
        horizon_quarter: int = 5,
        psr_horizon_months: int = 13,
        forecast_date: Optional[str] = None,
        value_start_date: Optional[str] = None,
) -> bool:
    print(f"\n{'=' * 70}\n[{ticker}] 시작 (hs_code={hs_code})\n{'=' * 70}")

    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()

    try:
        # 1) 기본 데이터
        # 시가총액 조회는 'A' 접두사 필요
        ticker_with_a = 'A' + ticker.lstrip('A').zfill(6)
        df_mc = get_market_cap_by_ticker(db_info, ticker_with_a)
        df_mc = _validate_df_mc(df_mc, ticker)

        # ========================================
        # 변경: 노트북 스타일 데이터 추출 (keep='first')
        # ========================================
        print(f"[{ticker}] 재무제표 데이터 수집 중 (노트북 방식)...")
        df_rev = extract_quarterly_revenue_notebook_style(
            db_info=db_info,
            ticker=ticker,
            fs_df=fs_df
        )

        if df_rev.empty:
            print(f"[{ticker}] 매출 데이터 없음 - 스킵")
            return False

        print(f"[{ticker}] ✓ 매출 데이터 {len(df_rev)}개 확보 (DataGuide 우선)")
        print(f"[{ticker}]   └─ 실제 데이터 범위: {df_rev.index.min().date()} ~ {df_rev.index.max().date()}")
        # ========================================

        # 2) 수출 exog (hs_code 있을 때만)
        exog_df = None
        df_export = None
        if hs_code:
            df_export = get_hscode_processed_data(db_info, hs_code=hs_code)
            exog_df = _get_exog_from_export(df_export)
            if isinstance(exog_df, pd.DataFrame) and 'exog_var' in exog_df.columns:
                exog_df = _transform_exog_col(df=exog_df, col='exog_var', periods=4, method=EXOG_Q_TRANSFORM)

        # 3) 결합 테이블
        combined_df, final_combined_data, forecast_df = get_revenue_export_joined_table(
            df_rev=df_rev, df_exog=exog_df, join_how="outer", fill_exog="ffill")
        final_combined_data = final_combined_data.loc['2015-01-01':]
        _cleanup(combined_df, forecast_df)

        final_combined_data = final_combined_data.asfreq('Q')

        # ========================================
        # 4) Revenue forecasts - 노트북 스타일 적용
        # ========================================
        print(f"[{ticker}] 매출 예측 중 (노트북 스타일)...")

        # TTM 계산을 위해 추가 분기 예측 필요
        extended_horizon = horizon_quarter + 3
        print(f"[{ticker}]   └─ 예측 분기 수: {extended_horizon} (horizon={horizon_quarter} + 3)")

        # SARIMA: extended_horizon 사용 + 안전 처리
        out_noexog = forecast_endog_with_optional_exog(
            combined_df=final_combined_data,
            horizon=extended_horizon,
            hs_code=None
        )
        rev_sarima_noexog = build_forecast_df_from_out(out_noexog, combined_df=final_combined_data)

        # SARIMA 안전성 검증
        if rev_sarima_noexog is not None and not rev_sarima_noexog.empty:
            sarima_values = rev_sarima_noexog['revenue_with_noexog']
            # 음수, NaN, Inf, 또는 비정상적으로 큰 값 체크
            if (sarima_values < 0).any() or sarima_values.isna().any() or \
                    (sarima_values > 1e15).any() or (sarima_values.abs() > 1e15).any():
                print(f"[{ticker}] ⚠️  SARIMA_noexog 예측 불안정 - 제외")
                rev_sarima_noexog = None

        _cleanup(out_noexog)

        rev_sarima_exog = None
        if hs_code:
            out_exog = forecast_endog_fill_tail(final_combined_data, hs_code=hs_code)
            rev_sarima_exog = build_forecast_df_from_out(out_exog, combined_df=final_combined_data)

            # SARIMA_exog 안전성 검증
            if rev_sarima_exog is not None and not rev_sarima_exog.empty:
                sarima_exog_values = rev_sarima_exog['revenue_with_exog']
                if (sarima_exog_values < 0).any() or sarima_exog_values.isna().any() or \
                        (sarima_exog_values > 1e15).any() or (sarima_exog_values.abs() > 1e15).any():
                    print(f"[{ticker}] ⚠️  SARIMA_exog 예측 불안정 - 제외")
                    rev_sarima_exog = None

            _cleanup(out_exog)

        # ETS, Theta: 노트북 스타일 직접 호출
        series = convert_to_period_index(final_combined_data, value_col='endog_var')
        print(f"[{ticker}]   └─ 시계열 마지막 기간: {series.index.max()}")

        rev_ets_df = forecast_ets_notebook_style(
            series,
            forecast_horizon=extended_horizon,
            forecast_start_date=FORECAST_CONFIG.get('revenue_start_date')
        )
        print(f"[{ticker}]   └─ ETS 예측 범위: {rev_ets_df.index.min().date()} ~ {rev_ets_df.index.max().date()}")

        rev_theta_df = forecast_theta_notebook_style(
            series,
            forecast_horizon=extended_horizon,
            forecast_start_date=FORECAST_CONFIG.get('revenue_start_date')
        )
        rev_lstm_df = forecast_revenue_lstm(final_combined_data, horizon=extended_horizon, lookback=12)
        rev_prophet_df = _safe_prophet_forecast(final_combined_data, horizon=extended_horizon)

        _cleanup(final_combined_data, series)
        # ========================================

        # 5) Revenue TTM 집계
        rev_final = get_revenue_ttm_df(
            df_rev=df_rev,
            rev_sarima_noexog=rev_sarima_noexog,
            rev_sarima_exog=rev_sarima_exog,
            rev_ets_df=rev_ets_df,
            rev_prophet_df=rev_prophet_df,
            rev_theta_df=rev_theta_df,
            rev_lstm_df=rev_lstm_df
        )
        _cleanup(rev_sarima_noexog, rev_sarima_exog, rev_ets_df, rev_theta_df, rev_lstm_df, rev_prophet_df)

        # 디버깅: TTM 계산 후 확인
        print(f"[{ticker}] TTM 계산 완료:")
        print(f"[{ticker}]   └─ rev_final 행 수: {len(rev_final)}")
        print(f"[{ticker}]   └─ rev_final 범위: {rev_final.index.min().date()} ~ {rev_final.index.max().date()}")
        print(
            f"[ticker]   └─ 2026년 데이터 개수: {len(rev_final[(rev_final.index >= '2026-01-01') & (rev_final.index < '2027-01-01')])}")
        if 'revenue_ets' in rev_final.columns:
            ets_2026 = rev_final[(rev_final.index >= '2026-01-01') & (rev_final.index < '2027-01-01')]['revenue_ets']
            print(f"[ticker]   └─ 2026년 revenue_ets 값:")
            for date, val in ets_2026.items():
                print(f"              {date.date()}: {val:.2e}")

        # 6) PSR 시계열 및 예측
        # 단위 일치: 시가총액(원), 매출(원) → mc_divisor=1.0으로 단위 변환 안 함
        psr_df = build_psr_series(df_mc=df_mc, df_rev=df_rev, mc_divisor=1.0)
        _cleanup(df_mc)

        exog_monthly_df = None
        if hs_code and df_export is not None and isinstance(df_export, dict):
            try:
                df_exog_monthly = extract_monthly_exog_var(df_export)
                if isinstance(df_exog_monthly, pd.DataFrame) and 'exog_var' in df_exog_monthly.columns:
                    exog_monthly_df = _transform_exog_col(
                        df=df_exog_monthly[['exog_var']],
                        col='exog_var',
                        periods=12,
                        method="pct"
                    )
                _cleanup(df_exog_monthly)
            except Exception:
                pass
        _cleanup(df_export, exog_df)

        fc_table = forecast_psr_all_models(
            psr_df,
            horizon=psr_horizon_months,
            exog_df=exog_monthly_df,
            forecast_start_date=FORECAST_CONFIG.get('psr_start_date')
        )
        _cleanup(exog_monthly_df)

        # 7) Valuation 계산
        valuation_forecast_result = compute_valuation_forecast(
            fc_table=fc_table,
            rev_final=rev_final,
            value_start_date=value_start_date
        )

        # 8) DB 업로드
        # 디버깅: DB 저장 전 rev_final 확인
        print(f"[{ticker}] DB 저장 직전 rev_final 확인:")
        if 'revenue_ets' in rev_final.columns:
            rev_ets_2026 = rev_final[(rev_final.index >= '2026-01-01') & (rev_final.index < '2027-01-01')][
                ['revenue_ets', 'revenue_ets_ttm']]
            print(f"[{ticker}]   └─ 2026년 revenue_ets 데이터:")
            print(rev_ets_2026.to_string())

        upload_valuation_longform(
            valuation_forecast_result=valuation_forecast_result,
            fc_table=fc_table,
            rev_final=rev_final,
            psr_df=psr_df,
            ticker=ticker_with_a,  # A 접두사 포함
            db_info=db_info,
            table_name=table_name,
            forecast_date=forecast_date
        )

        _cleanup(valuation_forecast_result, fc_table, rev_final, psr_df, df_rev)
        print(f"[OK] 완료: {ticker}")
        return True

    except Exception as e:
        ERROR_RECORDS.append({
            "ticker": ticker,
            "hs_code": hs_code,
            "error": str(e),
            "traceback": traceback.format_exc()
        })
        print(f"[ERROR] {ticker}: {e}")
        return False


def _save_error_records_to_excel(prefix: str = "error_tickers") -> Optional[str]:
    if not ERROR_RECORDS:
        return None
    from datetime import datetime as dt
    fname = f"{prefix}_{dt.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    try:
        pd.DataFrame(ERROR_RECORDS).to_excel(fname, index=False, sheet_name="errors", engine="xlsxwriter")
        print(f"[INFO] 에러 기록 저장: {fname}")
        return fname
    except Exception:
        return None


def run_batch(
        group: str = "all",
        value_start_date: Optional[str] = None,
        forecast_date: Optional[str] = None,
        table_name: str = "Korea_company_valuation_ver2",
        test_tickers: Optional[list] = None
):
    """
    배치 실행 메인 함수 - 노트북 호환 버전

    주요 변경사항:
    1. 데이터 전처리: keep='first' (DataGuide 우선)
    2. ETS/Theta 예측: forecast_ets/theta() 직접 호출
    3. 시계열 인덱스: PeriodIndex(freq='Q') 명시

    Parameters
    ----------
    group : str
        ticker 그룹 (test_tickers가 None일 때만 사용)
    value_start_date : str, optional
        valuation 시작 날짜
    forecast_date : str, optional
        예측 기준일
    table_name : str
        DB 테이블명
    test_tickers : list, optional
        테스트용 ticker 리스트. 예: ['000660', '005930']
        제공되면 이것만 실행하고 group 파라미터는 무시됨

    Examples
    --------
    # 전체 실행
    run_batch(group="all")

    # 특정 종목만 테스트
    run_batch(test_tickers=['000660', '005930'])
    """
    ok, fail = 0, 0

    # test_tickers가 제공되면 우선 사용
    if test_tickers is not None:
        # 문자열 리스트를 ticker 딕셔너리 형식으로 변환
        tickers = []
        for ticker in test_tickers:
            # A 제거
            clean_ticker = ticker.lstrip('A').zfill(6)
            tickers.append({
                'ticker': clean_ticker,
                'hs_code': None  # 테스트 모드에서는 hs_code 없이 실행
            })
        print(f"\n🧪 테스트 모드: {len(tickers)}개 종목 실행")
        print(f"   종목: {', '.join(test_tickers)}\n")
    else:
        tickers = get_ticker_list(group=group)

    # fs_df 사전 로드
    print("\n" + "=" * 70)
    print("📊 korea_fs_data 사전 로딩 중...")
    print("=" * 70)
    try:
        fs_df = fetch_table_data(db_info, "korea_fs_data")
        print(f"✓ DataGuide 데이터 로드 완료: {len(fs_df):,}행")
        print("  → 모든 ticker에서 재사용하여 성능 향상\n")
    except Exception as e:
        print(f"⚠ korea_fs_data 로드 실패: {e}")
        print("  → 각 ticker마다 개별 조회로 진행\n")
        fs_df = None

    for item in tickers:
        res = process_single_ticker(
            ticker=item.get('ticker'),
            hs_code=item.get('hs_code'),
            db_info=db_info,
            fs_df=fs_df,
            table_name=table_name,
            horizon_quarter=FORECAST_CONFIG['horizon_quarters'],
            psr_horizon_months=FORECAST_CONFIG['psr_horizon_months'],
            forecast_date=forecast_date,
            value_start_date=value_start_date
        )
        ok += int(res)
        fail += int(not res)

    print(f"\n{'-' * 60}\nBatch done. success={ok}, fail={fail}, group='{group}'\n{'-' * 60}")
    _save_error_records_to_excel(prefix=f"error_tickers_{group}")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🚀 Korea PSR Valuation Batch v5 - Notebook Compatible")
    print("   - 노트북과 동일한 예측 결과 생성")
    print("   - DataGuide 우선 (keep='first')")
    print("   - ETS/Theta 직접 호출")
    print("   - PeriodIndex 명시")
    print("=" * 70 + "\n")

    # 예측 설정 출력
    print("=" * 70)
    print("📅 예측 날짜 설정")
    print("=" * 70)
    print(f"Revenue 예측 시작일: {FORECAST_CONFIG['revenue_start_date']}")
    print(f"PSR 예측 시작일:     {FORECAST_CONFIG['psr_start_date']}")
    print(f"Revenue 예측 분기:   {FORECAST_CONFIG['horizon_quarters']}분기")
    print(f"PSR 예측 월수:       {FORECAST_CONFIG['psr_horizon_months']}개월")

    if FORECAST_CONFIG['revenue_start_date'] is None:
        print("\n⚠️  자동 모드: 각 종목의 마지막 데이터 이후부터 자동 예측")
    else:
        print(f"\n✓ 수동 모드: 모든 종목이 동일한 날짜부터 예측 (날짜 통일)")
    print("=" * 70 + "\n")

    # ==================== 테스트 모드 설정 ====================
    # 특정 종목만 테스트하려면 아래 주석을 해제하고 종목 코드 입력

    # 예시 1: SK하이닉스, 삼성전자만 테스트
    # TEST_TICKERS = ['000660', '005930']

    # 예시 2: 카카오, 네이버만 테스트
    # TEST_TICKERS = ['035420', '035720', '051910']

    # ticker_list.py의 TEST_TICKERS 사용
    from ticker_list import BEAUTY_TICKERS as TICKER_LIST
    #
    # TEST_TICKERS = [item['ticker'] for item in TICKER_LIST]

    # 전체 실행 (기본값)
    TEST_TICKERS = None

    # =========================================================

    if TEST_TICKERS is not None:
        # 테스트 모드
        run_batch(test_tickers=TEST_TICKERS, value_start_date=None, forecast_date=None)
    else:
        # 전체 실행 모드
        run_batch(group="beauty", value_start_date=None, forecast_date=None)