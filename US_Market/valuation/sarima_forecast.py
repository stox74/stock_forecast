#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SARIMA 예측 모듈 - 핵심 기능만 추출한 간결 버전
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import warnings

warnings.filterwarnings('ignore')

# 필수 라이브러리 import 시도
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.stattools import adfuller

    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False

from itertools import product


class SARIMAForecaster:
    """SARIMA 예측 클래스"""

    def __init__(self, seasonal_period=12):
        self.seasonal_period = seasonal_period
        self.model = None
        self.fitted_model = None
        self.best_params = None
        self.best_seasonal_params = None

    def _check_statsmodels(self):
        """statsmodels 설치 확인"""
        if not STATSMODELS_AVAILABLE:
            raise ImportError("statsmodels가 설치되지 않았습니다. pip install statsmodels")

    def _create_forecast_dates(self, start_date_str, months=12):
        """예측 기간 날짜 생성"""
        start_date = pd.to_datetime(start_date_str + "-01")
        forecast_dates = []
        for i in range(months):
            current_date = start_date + relativedelta(months=i)
            month_end = current_date.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
            forecast_dates.append(month_end)
        return forecast_dates

    def _prepare_data(self, final_data, export_forecast_start_date, psr_col='PSR_ttm',
                      exog_col='expDlr', use_exogenous=True):
        """데이터 준비 및 분리"""
        forecast_start = pd.to_datetime(export_forecast_start_date + "-01")
        forecast_start_month_end = forecast_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)

        # PSR 데이터
        if psr_col not in final_data.columns:
            raise ValueError(f"PSR 컬럼 '{psr_col}'이 데이터에 없습니다.")

        target_data = final_data[final_data[psr_col].notna()].copy()
        target_data = target_data.sort_values('date_month_end')
        historical_target = target_data[target_data['date_month_end'] < forecast_start_month_end]

        if historical_target.empty:
            raise ValueError(f"예측 대상 데이터({psr_col})가 없습니다.")

        # 외생변수 데이터
        exog_train = None
        exog_forecast = None

        if use_exogenous:
            if exog_col not in final_data.columns:
                print(f"외생변수 컬럼 '{exog_col}'이 데이터에 없어 외생변수를 사용하지 않습니다.")
                use_exogenous = False
            else:
                export_data = final_data[final_data[exog_col].notna()].copy()
                if not export_data.empty:
                    historical_export = export_data[export_data['date_month_end'] < forecast_start_month_end]
                    future_export = export_data[export_data['date_month_end'] >= forecast_start_month_end]

                    # YoY 계산
                    export_all = pd.concat([
                        historical_export[['date_month_end', exog_col]],
                        future_export[['date_month_end', exog_col]] if future_export is not None else pd.DataFrame()
                    ]).drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')

                    export_all = export_all.set_index('date_month_end')[exog_col].astype(float)
                    export_yoy = export_all.pct_change(12)

                    # 학습용 데이터 정렬
                    target_ts = historical_target.set_index('date_month_end')[psr_col].astype(float)
                    common_index = target_ts.index.intersection(export_yoy.index)

                    if len(common_index) > 0:
                        target_ts = target_ts.loc[common_index]
                        export_yoy_train = export_yoy.loc[common_index]

                        common_dates = target_ts.index.intersection(export_yoy_train.dropna().index)
                        if len(common_dates) > 0:
                            target_ts = target_ts.loc[common_dates]
                            exog_train = export_yoy_train.loc[common_dates].values.reshape(-1, 1)

                            # 예측용 외생변수
                            forecast_dates = pd.to_datetime(self._create_forecast_dates(export_forecast_start_date, 12))
                            exog_forecast_series = export_yoy.reindex(forecast_dates)
                            exog_forecast = exog_forecast_series.fillna(method='ffill').fillna(
                                method='bfill').values.reshape(-1, 1)

                if exog_train is None:
                    use_exogenous = False
                    target_ts = historical_target.set_index('date_month_end')[psr_col].astype(float)
        else:
            target_ts = historical_target.set_index('date_month_end')[psr_col].astype(float)

        return target_ts, exog_train, exog_forecast, use_exogenous

    def _find_best_params(self, y_train, exog_train=None):
        """최적 SARIMA 파라미터 탐색"""
        self._check_statsmodels()

        p_values = [0, 1, 2]
        d_values = [0, 1]
        q_values = [0, 1, 2]
        P_values = [0, 1]
        D_values = [0, 1]
        Q_values = [0, 1]

        best_aic = np.inf
        best_params = None
        best_seasonal_params = None

        for p, d, q in product(p_values, d_values, q_values):
            for P, D, Q in product(P_values, D_values, Q_values):
                try:
                    model = SARIMAX(
                        y_train,
                        exog=exog_train,
                        order=(p, d, q),
                        seasonal_order=(P, D, Q, self.seasonal_period),
                        enforce_stationarity=False,
                        enforce_invertibility=False
                    )

                    fitted_model = model.fit(disp=False, maxiter=100)

                    if fitted_model.aic < best_aic:
                        best_aic = fitted_model.aic
                        best_params = (p, d, q)
                        best_seasonal_params = (P, D, Q, self.seasonal_period)

                except:
                    continue

        if best_params is None:
            best_params = (1, 1, 1)
            best_seasonal_params = (1, 1, 1, self.seasonal_period)

        return best_params, best_seasonal_params

    def fit(self, target_ts, exog_train=None):
        """모델 학습"""
        self._check_statsmodels()

        # 최적 파라미터 찾기
        self.best_params, self.best_seasonal_params = self._find_best_params(target_ts, exog_train)

        # 최종 모델 학습
        self.model = SARIMAX(
            target_ts,
            exog=exog_train,
            order=self.best_params,
            seasonal_order=self.best_seasonal_params,
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        self.fitted_model = self.model.fit(disp=False, maxiter=200)
        return self

    def predict(self, forecast_months=12, exog_forecast=None):
        """예측 수행"""
        if self.fitted_model is None:
            raise ValueError("모델이 학습되지 않았습니다. fit() 메서드를 먼저 호출하세요.")

        if exog_forecast is not None:
            # 외생변수가 부족한 경우 마지막 값으로 채움
            if len(exog_forecast) < forecast_months:
                last_value = exog_forecast[-1] if len(exog_forecast) > 0 else np.array([[0]])
                missing_count = forecast_months - len(exog_forecast)
                additional_values = np.repeat(last_value, missing_count, axis=0)
                exog_forecast = np.vstack([exog_forecast, additional_values])

            forecast_result = self.fitted_model.forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            )
            conf_int = self.fitted_model.get_forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            ).conf_int()
        else:
            forecast_result = self.fitted_model.forecast(steps=forecast_months)
            conf_int = self.fitted_model.get_forecast(steps=forecast_months).conf_int()

        return forecast_result, conf_int

    def get_model_info(self):
        """모델 정보 반환"""
        if self.fitted_model is None:
            return None

        return {
            'model_type': 'SARIMA',
            'order': self.best_params,
            'seasonal_order': self.best_seasonal_params,
            'aic': self.fitted_model.aic,
            'fitted_model': self.fitted_model
        }


def sarima_forecast(final_data, export_forecast_start_date="2025-10",
                    psr_col='PSR_ttm', exog_col='expDlr', use_exogenous=True, forecast_months=12):
    """
    SARIMA 예측 실행 함수

    Parameters:
    - final_data: 전처리된 데이터 DataFrame
    - export_forecast_start_date: 예측 시작일 (YYYY-MM 형식)
    - psr_col: PSR 컬럼명 (기본값: 'PSR_ttm')
    - exog_col: 외생변수 컬럼명 (기본값: 'expDlr')
    - use_exogenous: 외생변수 사용 여부
    - forecast_months: 예측 개월 수

    Returns:
    - forecast_df: 예측 결과 DataFrame
    - model_info: 모델 정보
    """

    # SARIMA 모델 초기화
    forecaster = SARIMAForecaster()

    # 데이터 준비
    target_ts, exog_train, exog_forecast, use_exogenous = forecaster._prepare_data(
        final_data, export_forecast_start_date, psr_col, exog_col, use_exogenous
    )

    # 모델 학습
    forecaster.fit(target_ts, exog_train if use_exogenous else None)

    # 예측 수행
    forecast_result, conf_int = forecaster.predict(
        forecast_months,
        exog_forecast if use_exogenous else None
    )

    # 결과 정리
    forecast_dates = forecaster._create_forecast_dates(export_forecast_start_date, forecast_months)

    forecast_df = pd.DataFrame({
        'date_month_end': forecast_dates,
        f'{psr_col}_forecast': forecast_result.values,
        f'{psr_col}_lower': conf_int.iloc[:, 0].values,
        f'{psr_col}_upper': conf_int.iloc[:, 1].values,
        'forecast_type': 'SARIMA',
        'use_exogenous': use_exogenous,
        'psr_column': psr_col,
        'exog_column': exog_col if use_exogenous else None
    })

    # 외생변수 정보 추가
    if use_exogenous and exog_forecast is not None:
        forecast_df['exog_value'] = exog_forecast[:forecast_months].flatten()
    else:
        forecast_df['exog_value'] = np.nan

    model_info = forecaster.get_model_info()
    model_info['psr_column'] = psr_col
    model_info['exog_column'] = exog_col if use_exogenous else None

    return forecast_df, model_info


# 분기별 매출 예측 함수들
def extract_quarterly_revenue(data, revenue_col='revenue_billions', date_col='date_month_end', data_end_date=None):
    """월별 데이터에서 분기별 매출 추출"""
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]

    revenue_data = df[df[revenue_col].notna()].copy()
    if len(revenue_data) == 0:
        raise ValueError("유효한 매출 데이터가 없습니다.")

    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter

    quarterly_list = []
    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        last_month_data = group.loc[group[date_col].idxmax()]

        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col]
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    return quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)


def sarima_quarterly_forecast(quarterly_data, forecast_quarters=4):
    """분기별 매출 SARIMA 예측"""
    if not STATSMODELS_AVAILABLE:
        raise ImportError("statsmodels가 설치되지 않았습니다.")

    revenue_series = quarterly_data['revenue_billions'].values

    if len(revenue_series) < 8:
        raise ValueError(f"SARIMA 모델링을 위해 최소 8분기 데이터가 필요합니다. 현재: {len(revenue_series)}분기")

    # 파라미터 그리드 서치
    p_values = [0, 1, 2]
    d_values = [0, 1]
    q_values = [0, 1, 2]
    P_values = [0, 1]
    D_values = [0, 1]
    Q_values = [0, 1]
    s_value = 4  # 분기별 계절성

    best_aic = float('inf')
    best_model = None

    for p, d, q, P, D, Q in product(p_values, d_values, q_values, P_values, D_values, Q_values):
        try:
            # 파라미터 수 제한
            total_params = p + q + P + Q + 1
            if total_params >= len(revenue_series) * 0.4:
                continue

            model = SARIMAX(
                revenue_series,
                order=(p, d, q),
                seasonal_order=(P, D, Q, s_value),
                enforce_stationarity=False,
                enforce_invertibility=False
            )

            fitted_model = model.fit(disp=False, maxiter=100)

            if fitted_model.aic < best_aic:
                best_aic = fitted_model.aic
                best_model = fitted_model

        except Exception:
            continue

    # 최적 모델을 찾지 못한 경우 기본 모델 사용
    if best_model is None:
        model = SARIMAX(
            revenue_series,
            order=(1, 1, 1),
            seasonal_order=(0, 0, 0, 0),
            enforce_stationarity=False,
            enforce_invertibility=False
        )
        best_model = model.fit(disp=False)

    # 예측 수행
    forecast = best_model.forecast(steps=forecast_quarters)
    forecast_values = forecast.values if hasattr(forecast, 'values') else forecast

    # 신뢰구간 계산
    try:
        prediction_results = best_model.get_prediction(
            start=len(revenue_series),
            end=len(revenue_series) + forecast_quarters - 1
        )
        forecast_ci = prediction_results.conf_int()
        forecast_lower = forecast_ci.iloc[:, 0].values
        forecast_upper = forecast_ci.iloc[:, 1].values
    except:
        forecast_std = np.std(revenue_series) * 0.1
        forecast_lower = forecast_values - 1.96 * forecast_std
        forecast_upper = forecast_values + 1.96 * forecast_std

    # 예측 날짜 생성
    last_date = quarterly_data['date_quarter_end'].iloc[-1]
    forecast_dates = []

    for i in range(1, forecast_quarters + 1):
        next_quarter_date = last_date + pd.DateOffset(months=3 * i)
        quarter_end = pd.Timestamp(
            year=next_quarter_date.year,
            month=next_quarter_date.month,
            day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
        )
        forecast_dates.append(quarter_end)

    # 결과 DataFrame 생성
    forecast_result = pd.DataFrame({
        'date_quarter_end': forecast_dates,
        'year': [d.year for d in forecast_dates],
        'quarter': [d.quarter for d in forecast_dates],
        'year_quarter': [f"{d.year}Q{d.quarter}" for d in forecast_dates],
        'revenue_billions_forecast': forecast_values,
        'forecast_lower': forecast_lower,
        'forecast_upper': forecast_upper
    })

    model_info = {
        'model_type': 'SARIMA_Quarterly',
        'aic': best_aic,
        'model': best_model,
        'historical_data_points': len(revenue_series)
    }

    return forecast_result, model_info


def distribute_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """분기별 예측 결과를 월별로 분배"""
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    if 'revenue_billions' in df.columns:
        df['revenue_billions_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_forecast'] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_forecast']

        months_in_quarter = quarter_month_map[quarter]

        for month in months_in_quarter:
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_forecast'] = quarterly_value

    return df


def revenue_sarima_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                     data_end_date=None, forecast_quarters=4):
    """
    매출 SARIMA 예측 파이프라인

    Returns:
    - result_data: 예측값이 추가된 데이터
    - quarterly_data: 분기별 데이터
    - forecast_result: 분기별 예측 결과
    - model_info: 모델 정보
    """

    # 1. 분기별 데이터 추출
    quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)

    # 2. SARIMA 예측
    forecast_result, model_info = sarima_quarterly_forecast(quarterly_data, forecast_quarters)

    if forecast_result is None:
        return None, quarterly_data, None, None

    # 3. 월별 분배
    result_data = distribute_quarterly_to_monthly(forecast_result, data, date_col)

    return result_data, quarterly_data, forecast_result, model_info


# 테스트 함수
def test_sarima_module():
    """모듈 테스트"""
    if STATSMODELS_AVAILABLE:
        print("✅ statsmodels 사용 가능")
    else:
        print("❌ statsmodels 설치 필요: pip install statsmodels")

    print("✅ SARIMA 모듈 import 성공")
    print("\n사용법:")
    print("from sarima_forecast import sarima_forecast, revenue_sarima_forecast_pipeline")
    print("# PSR 예측 (컬럼 지정 가능)")
    print("forecast_df, model_info = sarima_forecast(final_data, '2025-10', psr_col='PSR_ttm', exog_col='expDlr')")
    print("# 매출 예측")
    print("result_data, _, _, _ = revenue_sarima_forecast_pipeline(data, revenue_col='revenue_billions')")


if __name__ == "__main__":
    test_sarima_module()