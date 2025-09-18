#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SARIMA 예측 모듈
외부에서 import 가능한 독립적인 예측 함수들
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import warnings

# 필수 라이브러리 import 시도
try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    from statsmodels.tsa.stattools import adfuller

    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    print("Warning: statsmodels가 설치되지 않았습니다.")
    print("pip install statsmodels 로 설치해주세요.")

from itertools import product

from prophet import Prophet
from statsmodels.tsa.holtwinters import ExponentialSmoothing

warnings.filterwarnings('ignore')


def create_forecast_dates(start_date_str, months=12):
    """예측 기간 날짜 생성"""
    start_date = pd.to_datetime(start_date_str + "-01")

    forecast_dates = []
    for i in range(months):
        current_date = start_date + relativedelta(months=i)
        month_end = current_date.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
        forecast_dates.append(month_end)

    return forecast_dates


def prepare_export_data(final_data, export_forecast_start_date):
    """수출 데이터 준비 및 분리"""
    forecast_start = pd.to_datetime(export_forecast_start_date + "-01")
    forecast_start_month_end = forecast_start.replace(day=1) + relativedelta(months=1) - timedelta(days=1)

    data_sorted = final_data.sort_values('date_month_end').copy()
    export_data = data_sorted[data_sorted['expDlr'].notna()].copy()

    if export_data.empty:
        print("수출 데이터가 없습니다.")
        return None, None, None

    historical_export = export_data[export_data['date_month_end'] < forecast_start_month_end]
    future_export = export_data[export_data['date_month_end'] >= forecast_start_month_end]

    print(f"과거 수출 데이터: {len(historical_export)}개")
    print(f"미래 수출 예측치: {len(future_export)}개")

    return historical_export, future_export, forecast_start_month_end


def prepare_target_data(final_data, forecast_start_month_end):
    """예측 대상 데이터 준비"""
    target_data = final_data[final_data['PSR_ttm'].notna()].copy()
    target_data = target_data.sort_values('date_month_end')
    historical_target = target_data[target_data['date_month_end'] < forecast_start_month_end]

    print(f"과거 PSR 데이터: {len(historical_target)}개")
    return historical_target


def check_stationarity(series, name="Series"):
    """정상성 검정"""
    if not STATSMODELS_AVAILABLE:
        print(f"{name}: 정상성 검정 건너뜀 (statsmodels 없음)")
        return True

    result = adfuller(series.dropna())

    print(f"\n{name} 정상성 검정:")
    print(f"ADF Statistic: {result[0]:.4f}")
    print(f"p-value: {result[1]:.4f}")

    if result[1] <= 0.05:
        print("시계열이 정상적입니다.")
        return True
    else:
        print("시계열이 비정상적입니다. 차분이 필요할 수 있습니다.")
        return False


def find_best_sarima_params(y_train, exog_train=None, seasonal_period=12):
    """최적 SARIMA 파라미터 찾기"""
    if not STATSMODELS_AVAILABLE:
        print("statsmodels가 없어 기본 파라미터를 사용합니다.")
        return (1, 1, 1), (1, 1, 1, seasonal_period)

    print("최적 SARIMA 파라미터 탐색 중...")

    p_values = [0, 1, 2]
    d_values = [0, 1]
    q_values = [0, 1, 2]

    P_values = [0, 1]
    D_values = [0, 1]
    Q_values = [0, 1]

    best_aic = np.inf
    best_params = None
    best_seasonal_params = None

    total_combinations = len(p_values) * len(d_values) * len(q_values) * len(P_values) * len(D_values) * len(Q_values)
    print(f"총 {total_combinations}개 조합 테스트")

    tested = 0
    for p, d, q in product(p_values, d_values, q_values):
        for P, D, Q in product(P_values, D_values, Q_values):
            try:
                model = SARIMAX(
                    y_train,
                    exog=exog_train,
                    order=(p, d, q),
                    seasonal_order=(P, D, Q, seasonal_period),
                    enforce_stationarity=False,
                    enforce_invertibility=False
                )

                fitted_model = model.fit(disp=False, maxiter=100)

                if fitted_model.aic < best_aic:
                    best_aic = fitted_model.aic
                    best_params = (p, d, q)
                    best_seasonal_params = (P, D, Q, seasonal_period)

                tested += 1
                if tested % 10 == 0:
                    print(f"진행률: {tested}/{total_combinations}")

            except:
                continue

    if best_params is None:
        print("최적 파라미터를 찾을 수 없어 기본값을 사용합니다.")
        best_params = (1, 1, 1)
        best_seasonal_params = (1, 1, 1, seasonal_period)
        best_aic = 0

    print(f"\n최적 파라미터:")
    print(f"ARIMA Order: {best_params}")
    print(f"Seasonal Order: {best_seasonal_params}")
    print(f"Best AIC: {best_aic:.4f}")

    return best_params, best_seasonal_params


def sarima_forecast_with_export(final_data, export_forecast_start_date="2025-10",
                                USE_EXOGENOUS=True, forecast_months=12):
    """
    SARIMA 모델을 사용한 PSR 예측

    Parameters:
    - final_data (pd.DataFrame): 전처리된 데이터
    - export_forecast_start_date (str): 수출 예측 시작일 (YYYY-MM 형식)
    - USE_EXOGENOUS (bool): 외생변수(수출 데이터) 사용 여부
    - forecast_months (int): 예측 개월 수

    Returns:
    - pd.DataFrame: 예측 결과 DataFrame
    """

    if not STATSMODELS_AVAILABLE:
        print("Error: statsmodels가 설치되지 않았습니다.")
        print("pip install statsmodels 명령으로 설치해주세요.")
        return None

    print("=" * 70)
    print("SARIMA 예측 시작")
    print(f"예측 시작일: {export_forecast_start_date}")
    print(f"외생변수 사용: {USE_EXOGENOUS}")
    print(f"예측 기간: {forecast_months}개월")
    print("=" * 70)

    # 예측 종료일 계산
    export_forecast_end_date = (pd.to_datetime(export_forecast_start_date + "-01") +
                                relativedelta(months=forecast_months - 1)).strftime('%Y-%m')
    print(f"예측 종료일: {export_forecast_end_date}")

    # 1. 데이터 준비
    historical_export, future_export, forecast_start_month_end = prepare_export_data(
        final_data, export_forecast_start_date)

    historical_target = prepare_target_data(final_data, forecast_start_month_end)

    if historical_target.empty:
        print("예측 대상 데이터가 없습니다.")
        return None

    # 2. 시계열 데이터 준비
    target_ts = historical_target.set_index('date_month_end')['PSR_ttm'].astype(float)

    # 외생변수 준비 (YoY)
    exog_train = None
    exog_forecast = None

    if USE_EXOGENOUS and historical_export is not None and not historical_export.empty:
        print("\n외생변수(수출 데이터) 준비 중... (YoY 변환)")

        # 학습과 미래를 합쳐 연속 시계열 구성
        export_all = pd.concat(
            [
                historical_export[['date_month_end', 'expDlr']],
                future_export[['date_month_end', 'expDlr']] if future_export is not None else pd.DataFrame(
                    columns=['date_month_end', 'expDlr'])
            ],
            ignore_index=True
        ).drop_duplicates(subset=['date_month_end']).sort_values('date_month_end')

        export_all = export_all.set_index('date_month_end')['expDlr'].astype(float)

        # YoY(12개월 전 대비) 계산: 비율(예: 0.08 = +8%)
        export_yoy = export_all.pct_change(12)

        # 학습용 YoY 시리즈를 타깃과 정렬
        target_ts = historical_target.set_index('date_month_end')['PSR_ttm'].astype(float)

        # 수정된 코드
        # 공통 인덱스만 사용하여 안전하게 정렬
        common_index = target_ts.index.intersection(export_yoy.index)
        if len(common_index) == 0:
            raise ValueError("타겟 데이터와 외생변수 데이터의 날짜가 전혀 겹치지 않습니다.")

        target_ts = target_ts.loc[common_index]
        export_yoy_train = export_yoy.loc[common_index]



        # 공통 날짜만 사용 (NaN 제거)
        common_dates = target_ts.index.intersection(export_yoy_train.dropna().index)

        if len(common_dates) > 0:
            target_ts = target_ts.loc[common_dates]
            exog_train = export_yoy_train.loc[common_dates].values.reshape(-1, 1)

            # 예측 구간의 월말 날짜 생성 후 YoY를 매칭
            forecast_dates = pd.to_datetime(create_forecast_dates(export_forecast_start_date, forecast_months))
            exog_forecast_series = export_yoy.reindex(forecast_dates)

            # 시작 직후 NaN 방지용 보간/채움 (선택: ffill→bfill)
            exog_forecast = exog_forecast_series.fillna(method='ffill').fillna(method='bfill').values.reshape(-1, 1)

            print(f"외생변수(YoY) 매칭된 학습 개월: {len(common_dates)}")
            print(f"미래 수출 YoY 예측치 개월: {len(exog_forecast)}")
        else:
            print("수출 YoY와 PSR 날짜가 매칭되지 않아 외생변수를 사용하지 않습니다.")
            USE_EXOGENOUS = False
            exog_train = None
    else:
        print("외생변수 미사용 또는 수출 데이터 부재로 건너뜀.")
        USE_EXOGENOUS = False

    # 3. 정상성 검정
    check_stationarity(target_ts, "PSR")

    if USE_EXOGENOUS and exog_train is not None:
        check_stationarity(pd.Series(exog_train.flatten()), "수출 데이터")

    # 4. 최적 파라미터 찾기
    best_order, best_seasonal_order = find_best_sarima_params(
        target_ts, exog_train if USE_EXOGENOUS else None)

    if best_order is None:
        print("최적 파라미터를 찾을 수 없습니다.")
        return None

    # 5. 최종 모델 학습
    print(f"\n최종 SARIMA 모델 학습 중...")

    try:
        model = SARIMAX(
            target_ts,
            exog=exog_train if USE_EXOGENOUS else None,
            order=best_order,
            seasonal_order=best_seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        fitted_model = model.fit(disp=False, maxiter=200)

        print("모델 학습 완료!")
        print(f"AIC: {fitted_model.aic:.4f}")

    except Exception as e:
        print(f"모델 학습 실패: {e}")
        return None

    # 6. 예측 수행
    print(f"\n{forecast_months}개월 예측 수행 중...")

    forecast_dates = create_forecast_dates(export_forecast_start_date, forecast_months)

    try:
        if USE_EXOGENOUS and exog_forecast is not None:
            # 외생변수가 부족한 경우 마지막 값으로 채움
            if len(exog_forecast) < forecast_months:
                last_value = exog_forecast[-1] if len(exog_forecast) > 0 else np.array([[0]])
                missing_count = forecast_months - len(exog_forecast)
                additional_values = np.repeat(last_value, missing_count, axis=0)
                exog_forecast = np.vstack([exog_forecast, additional_values])

            forecast_result = fitted_model.forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            )
            conf_int = fitted_model.get_forecast(
                steps=forecast_months,
                exog=exog_forecast[:forecast_months]
            ).conf_int()
        else:
            forecast_result = fitted_model.forecast(steps=forecast_months)
            conf_int = fitted_model.get_forecast(steps=forecast_months).conf_int()

    except Exception as e:
        print(f"예측 실행 실패: {e}")
        return None

    # 7. 결과 정리
    forecast_df = pd.DataFrame({
        'date_month_end': forecast_dates,
        'PSR_forecast': forecast_result.values,
        'PSR_lower': conf_int.iloc[:, 0].values,
        'PSR_upper': conf_int.iloc[:, 1].values,
        'forecast_type': 'SARIMA',
        'use_exogenous': USE_EXOGENOUS
    })

    # 외생변수 정보 추가
    if USE_EXOGENOUS and exog_forecast is not None:
        forecast_df['exog_value'] = exog_forecast[:forecast_months].flatten()
    else:
        forecast_df['exog_value'] = np.nan

    print("=" * 70)
    print("SARIMA 예측 완료!")
    print(f"예측 결과: {len(forecast_df)} 레코드")
    print("=" * 70)

    return forecast_df


def run_psr_forecast(final_data, export_forecast_start_date="2025-10", USE_EXOGENOUS=True):
    """PSR 예측 실행 함수 (간편 버전)"""

    forecast_result = sarima_forecast_with_export(
        final_data=final_data,
        export_forecast_start_date=export_forecast_start_date,
        USE_EXOGENOUS=USE_EXOGENOUS,
        forecast_months=12
    )

    if forecast_result is not None:
        print("\n예측 결과 샘플:")
        print(forecast_result.head().to_string(index=False))

        print(f"\n예측 통계:")
        print(f"평균 PSR: {forecast_result['PSR_forecast'].mean():.2f}")
        print(f"최소 PSR: {forecast_result['PSR_forecast'].min():.2f}")
        print(f"최대 PSR: {forecast_result['PSR_forecast'].max():.2f}")

    return forecast_result


# 모듈 테스트 함수
def test_sarima_module():
    """모듈이 제대로 작동하는지 테스트"""
    print("SARIMA 모듈 테스트")
    print("=" * 30)

    if STATSMODELS_AVAILABLE:
        print("✅ statsmodels 사용 가능")
    else:
        print("❌ statsmodels 설치 필요")
        print("pip install statsmodels")

    print("✅ pandas, numpy 사용 가능")
    print("✅ 날짜 처리 함수 사용 가능")

    # 간단한 날짜 테스트
    test_dates = create_forecast_dates("2025-10", 3)
    print(f"✅ 날짜 생성 테스트: {len(test_dates)}개 날짜 생성")

    print("\n모듈 import 성공!")
    print("사용법:")
    print("from sarima_forecast_module import sarima_forecast_with_export")
    print("result = sarima_forecast_with_export(final_data, '2025-10', True)")


def extract_quarterly_revenue(data, revenue_col='revenue_billions', date_col='date_month_end',
                              data_end_date=None):
    """
    월별 데이터에서 분기별 매출 추출

    Parameters:
    - data: 월별 데이터 DataFrame
    - revenue_col: 매출 컬럼명
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일 (예: '2025-08', '2025-08-31')

    Returns:
    - quarterly_data: 분기별 매출 DataFrame
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 데이터 종료일 설정
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"데이터를 {end_date.strftime('%Y-%m')}까지로 제한했습니다.")

    # NaN이 아닌 매출 데이터만 사용
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("유효한 매출 데이터가 없습니다.")

    print(f"유효한 매출 데이터: {len(revenue_data)}개월")
    print(
        f"데이터 기간: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # 분기 정보 추가
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # 분기별 그룹화 (분기 마지막 월의 데이터 사용)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # 해당 분기의 마지막 월 데이터 사용
        last_month_data = group.loc[group[date_col].idxmax()]

        # 분기말 날짜 계산
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"추출된 분기 데이터: {len(quarterly_data)}분기")
    print("분기별 데이터:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}개월 데이터)")

    return quarterly_data


def sarima_quarterly_forecast(quarterly_data, forecast_quarters=4):
    """
    분기별 매출 SARIMA 예측

    Parameters:
    - quarterly_data: 분기별 매출 DataFrame
    - forecast_quarters: 예측할 분기 수

    Returns:
    - forecast_result: 예측 결과 DataFrame
    - model_info: 모델 정보
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values

        if len(revenue_series) < 8:
            raise ValueError(f"SARIMA 모델링을 위해 최소 8분기 데이터가 필요합니다. 현재: {len(revenue_series)}분기")

        print(f"SARIMA 모델링 시작: {len(revenue_series)}분기 데이터 사용")

        # SARIMA 파라미터 그리드 서치
        p_values = [0, 1, 2]
        d_values = [0, 1]
        q_values = [0, 1, 2]
        P_values = [0, 1]
        D_values = [0, 1]
        Q_values = [0, 1]
        s_value = 4  # 분기별 계절성

        best_aic = float('inf')
        best_params = None
        best_model = None

        print("SARIMA 파라미터 최적화 중...")
        tested_models = 0

        for p, d, q, P, D, Q in product(p_values, d_values, q_values, P_values, D_values, Q_values):
            try:
                tested_models += 1

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
                    best_params = (p, d, q, P, D, Q, s_value)
                    best_model = fitted_model

            except Exception:
                continue

        print(f"총 {tested_models}개 모델 테스트 완료")

        # 최적 모델을 찾지 못한 경우 기본 모델 사용
        if best_model is None:
            print("최적 모델을 찾지 못해 기본 SARIMA(1,1,1) 모델 사용")
            model = SARIMAX(
                revenue_series,
                order=(1, 1, 1),
                seasonal_order=(0, 0, 0, 0),
                enforce_stationarity=False,
                enforce_invertibility=False
            )
            best_model = model.fit(disp=False)
            best_params = (1, 1, 1, 0, 0, 0, 0)
            best_aic = best_model.aic

        print(f"최적 모델: SARIMA{best_params[:3]} x {best_params[3:]} (AIC: {best_aic:.2f})")

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
            print("신뢰구간 계산 실패, 기본값 사용")
            forecast_std = np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std

        # 예측 날짜 생성
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # 분기말로 조정
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

        # 모델 정보
        model_info = {
            'params': best_params,
            'aic': best_aic,
            'model': best_model,
            'historical_data_points': len(revenue_series)
        }

        print("분기별 SARIMA 예측 완료:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"SARIMA 예측 실패: {e}")
        return None, None


def distribute_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    분기별 예측 결과를 월별로 분배

    Parameters:
    - quarterly_forecast: 분기별 예측 결과 DataFrame
    - original_data: 원본 월별 데이터 DataFrame
    - date_col: 날짜 컬럼명

    Returns:
    - updated_data: 월별 예측값이 추가된 DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_forecast 컬럼 초기화
    df['revenue_billions_forecast'] = df['revenue_billions'].copy()

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("분기별 예측값을 월별로 분배 중...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_forecast']

        # 해당 분기의 월들
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} 예측값 {quarterly_value:.2f}B를 {months_in_quarter}월에 분배")

        for month in months_in_quarter:
            # 해당 년월의 마지막 날 계산
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # 해당 날짜의 행 찾기
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: 해당 날짜 없음")

    return df


def revenue_sarima_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                     data_end_date=None, forecast_quarters=4):
    """
    매출 SARIMA 예측 파이프라인

    Parameters:
    - data: 월별 데이터 DataFrame
    - revenue_col: 매출 컬럼명
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일 (예: '2025-08-31')
    - forecast_quarters: 예측할 분기 수

    Returns:
    - result_data: 예측값이 추가된 데이터
    - quarterly_data: 분기별 데이터
    - forecast_result: 분기별 예측 결과
    - model_info: 모델 정보
    """
    print("=== 매출 SARIMA 예측 파이프라인 시작 ===")
    print(f"데이터 종료일: {data_end_date if data_end_date else '전체 데이터 사용'}")
    print(f"예측 분기 수: {forecast_quarters}")

    try:
        # 1. 분기별 데이터 추출
        print("\n1. 분기별 매출 데이터 추출")
        quarterly_data = extract_quarterly_revenue(data, revenue_col, date_col, data_end_date)

        # 2. SARIMA 예측
        print("\n2. 분기별 SARIMA 예측")
        forecast_result, model_info = sarima_quarterly_forecast(quarterly_data, forecast_quarters)

        if forecast_result is None:
            print("SARIMA 예측 실패")
            return None, quarterly_data, None, None

        # 3. 월별 분배
        print("\n3. 분기별 예측값을 월별로 분배")
        result_data = distribute_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== 매출 SARIMA 예측 완료 ===")
        print(f"예측된 분기: {len(forecast_result)}개")
        print(f"업데이트된 월별 데이터: {len(result_data)}개월")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"예측 파이프라인 오류: {e}")
        return None, None, None, None


# LSTM 분기별 매출 예측 함수
import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
import warnings

warnings.filterwarnings('ignore')


def extract_quarterly_revenue_lstm(data, revenue_col='revenue_billions', date_col='date_month_end',
                                   data_end_date=None):
    """
    월별 데이터에서 분기별 매출 추출 (LSTM용)
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 데이터 종료일 설정
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"데이터를 {end_date.strftime('%Y-%m')}까지로 제한했습니다.")

    # NaN이 아닌 매출 데이터만 사용
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("유효한 매출 데이터가 없습니다.")

    print(f"유효한 매출 데이터: {len(revenue_data)}개월")
    print(
        f"데이터 기간: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # 분기 정보 추가
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # 분기별 그룹화 (분기 마지막 월의 데이터 사용)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # 해당 분기의 마지막 월 데이터 사용
        last_month_data = group.loc[group[date_col].idxmax()]

        # 분기말 날짜 계산
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"추출된 분기 데이터: {len(quarterly_data)}분기")
    print("분기별 데이터:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}개월 데이터)")

    return quarterly_data


def create_lstm_sequences(data, lookback_window=8):
    """
    LSTM을 위한 시퀀스 데이터 생성
    """
    X, y = [], []
    for i in range(lookback_window, len(data)):
        X.append(data[i - lookback_window:i])
        y.append(data[i])
    return np.array(X), np.array(y)


def lstm_quarterly_forecast(quarterly_data, forecast_quarters=4, lookback_window=8, epochs=100):
    """
    분기별 매출 LSTM 예측

    Parameters:
    - quarterly_data: 분기별 매출 DataFrame
    - forecast_quarters: 예측할 분기 수
    - lookback_window: LSTM 입력 시퀀스 길이
    - epochs: 훈련 에포크 수

    Returns:
    - forecast_result: 예측 결과 DataFrame
    - model_info: 모델 정보
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values.astype(np.float64)

        if len(revenue_series) < lookback_window + 4:
            raise ValueError(f"LSTM 모델링을 위해 최소 {lookback_window + 4}분기 데이터가 필요합니다. 현재: {len(revenue_series)}분기")

        print(f"LSTM 모델링 시작: {len(revenue_series)}분기 데이터 사용")
        print(f"Lookback window: {lookback_window}분기")

        # NaN 처리
        if np.isnan(revenue_series).any():
            print("NaN 값 발견, 보간 처리")
            mask = np.isnan(revenue_series)
            indices = np.where(~mask)[0]
            revenue_series = np.interp(np.arange(len(revenue_series)), indices, revenue_series[indices])

        print(f"매출 범위: {revenue_series.min():.2f}B ~ {revenue_series.max():.2f}B")

        # 데이터 정규화
        scaler = MinMaxScaler(feature_range=(0.1, 0.9))  # 안정적인 범위
        scaled_data = scaler.fit_transform(revenue_series.reshape(-1, 1)).flatten()

        print(f"정규화 후 범위: {scaled_data.min():.4f} ~ {scaled_data.max():.4f}")

        # 시퀀스 데이터 생성
        X, y = create_lstm_sequences(scaled_data, lookback_window)

        if len(X) == 0:
            raise ValueError("시퀀스 생성 실패: 데이터가 부족합니다.")

        print(f"시퀀스 데이터: X={X.shape}, y={y.shape}")

        # LSTM 입력을 위한 reshape
        X = X.reshape((X.shape[0], X.shape[1], 1))

        # LSTM 모델 구성
        model = Sequential([
            LSTM(50, return_sequences=True, input_shape=(lookback_window, 1)),
            LSTM(50, return_sequences=False),
            Dense(25, activation='relu'),
            Dense(1)
        ])

        # 모델 컴파일
        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001, clipnorm=1.0),
            loss='mse',
            metrics=['mae']
        )

        print("LSTM 모델 구조:")
        print(f"  - LSTM 레이어 1: 50 units (return_sequences=True)")
        print(f"  - LSTM 레이어 2: 50 units")
        print(f"  - Dense 레이어: 25 units (ReLU)")
        print(f"  - 출력 레이어: 1 unit")

        # 조기 종료 설정
        early_stopping = tf.keras.callbacks.EarlyStopping(
            monitor='loss',
            patience=20,
            restore_best_weights=True,
            verbose=0
        )

        # 모델 훈련
        print(f"LSTM 모델 훈련 중 (최대 {epochs} epochs)...")
        history = model.fit(
            X, y,
            epochs=epochs,
            batch_size=min(8, len(X)),
            verbose=0,
            callbacks=[early_stopping]
        )

        final_loss = history.history['loss'][-1]
        trained_epochs = len(history.history['loss'])
        print(f"훈련 완료: {trained_epochs} epochs, 최종 손실: {final_loss:.6f}")

        # 예측 수행
        print(f"LSTM으로 {forecast_quarters}분기 예측 중...")

        # 마지막 시퀀스로 시작
        last_sequence = scaled_data[-lookback_window:].copy()
        predictions = []

        for step in range(forecast_quarters):
            # 현재 시퀀스로 예측
            input_seq = last_sequence.reshape(1, lookback_window, 1)
            pred = model.predict(input_seq, verbose=0)
            pred_value = pred[0, 0]

            # NaN 체크
            if np.isnan(pred_value) or np.isinf(pred_value):
                print(f"비정상 예측값 감지 at step {step + 1}: {pred_value}")
                if predictions:
                    pred_value = np.mean(predictions)
                else:
                    pred_value = last_sequence[-1]
                print(f"대체값 사용: {pred_value:.4f}")

            # 값 범위 제한
            pred_value = np.clip(pred_value, 0.1, 0.9)

            predictions.append(pred_value)

            # 다음 예측을 위해 시퀀스 업데이트
            last_sequence = np.roll(last_sequence, -1)
            last_sequence[-1] = pred_value

            print(f"  예측 {step + 1}: 정규화값={pred_value:.4f}")

        # 역정규화
        predictions_array = np.array(predictions).reshape(-1, 1)
        forecast_values = scaler.inverse_transform(predictions_array).flatten()

        # 최종 NaN 체크
        if np.isnan(forecast_values).any() or np.isinf(forecast_values).any():
            print("최종 예측값에 비정상값 발견, 마지막 실제값으로 대체")
            last_actual = revenue_series[-1]
            forecast_values = np.full(forecast_quarters, last_actual)

        print(f"역정규화 완료: {forecast_values}")

        # 예측 날짜 생성
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # 분기말로 조정
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
            'revenue_billions_lstm_forecast': forecast_values
        })

        # 모델 정보
        model_info = {
            'model_type': 'LSTM',
            'lookback_window': lookback_window,
            'trained_epochs': trained_epochs,
            'final_loss': final_loss,
            'scaler': scaler,
            'model': model,
            'historical_data_points': len(revenue_series)
        }

        print("분기별 LSTM 예측 완료:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_lstm_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"LSTM 예측 실패: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def distribute_lstm_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    분기별 LSTM 예측 결과를 월별로 분배

    Parameters:
    - quarterly_forecast: 분기별 예측 결과 DataFrame
    - original_data: 원본 월별 데이터 DataFrame
    - date_col: 날짜 컬럼명

    Returns:
    - updated_data: 월별 예측값이 추가된 DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_lstm_forecast 컬럼 초기화
    if 'revenue_billions' in df.columns:
        df['revenue_billions_lstm_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_lstm_forecast'] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("분기별 LSTM 예측값을 월별로 분배 중...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_lstm_forecast']

        # 해당 분기의 월들
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} LSTM 예측값 {quarterly_value:.2f}B를 {months_in_quarter}월에 동일하게 적용")

        for month in months_in_quarter:
            # 해당 년월의 마지막 날 계산
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # 해당 날짜의 행 찾기
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_lstm_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: 해당 날짜 없음")

    return df


def revenue_lstm_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                   data_end_date=None, forecast_quarters=4, lookback_window=8, epochs=100):
    """
    매출 LSTM 예측 파이프라인

    Parameters:
    - data: 월별 데이터 DataFrame
    - revenue_col: 매출 컬럼명
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일 (예: '2025-08-31')
    - forecast_quarters: 예측할 분기 수
    - lookback_window: LSTM 입력 시퀀스 길이
    - epochs: 훈련 에포크 수

    Returns:
    - result_data: 예측값이 추가된 데이터
    - quarterly_data: 분기별 데이터
    - forecast_result: 분기별 예측 결과
    - model_info: 모델 정보
    """
    print("=== 매출 LSTM 예측 파이프라인 시작 ===")
    print(f"데이터 종료일: {data_end_date if data_end_date else '전체 데이터 사용'}")
    print(f"예측 분기 수: {forecast_quarters}")
    print(f"Lookback window: {lookback_window}분기")
    print(f"훈련 epochs: {epochs}")

    try:
        # 1. 분기별 데이터 추출
        print("\n1. 분기별 매출 데이터 추출")
        quarterly_data = extract_quarterly_revenue_lstm(data, revenue_col, date_col, data_end_date)

        # 2. LSTM 예측
        print("\n2. 분기별 LSTM 예측")
        forecast_result, model_info = lstm_quarterly_forecast(
            quarterly_data, forecast_quarters, lookback_window, epochs
        )

        if forecast_result is None:
            print("LSTM 예측 실패")
            return None, quarterly_data, None, None

        # 3. 월별 분배
        print("\n3. 분기별 LSTM 예측값을 월별로 분배")
        result_data = distribute_lstm_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== 매출 LSTM 예측 완료 ===")
        print(f"예측된 분기: {len(forecast_result)}개")
        print(f"업데이트된 월별 데이터: {len(result_data)}개월")
        print("추가된 컬럼: revenue_billions_lstm_forecast")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"LSTM 예측 파이프라인 오류: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None

def extract_quarterly_revenue_prophet(data, revenue_col='revenue_billions', date_col='date_month_end',
                                      data_end_date=None):
    """
    월별 데이터에서 분기별 매출 추출 (Prophet용)
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 데이터 종료일 설정
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"데이터를 {end_date.strftime('%Y-%m')}까지로 제한했습니다.")

    # NaN이 아닌 매출 데이터만 사용
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("유효한 매출 데이터가 없습니다.")

    print(f"유효한 매출 데이터: {len(revenue_data)}개월")
    print(
        f"데이터 기간: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # 분기 정보 추가
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # 분기별 그룹화 (분기 마지막 월의 데이터 사용)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # 해당 분기의 마지막 월 데이터 사용
        last_month_data = group.loc[group[date_col].idxmax()]

        # 분기말 날짜 계산
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"추출된 분기 데이터: {len(quarterly_data)}분기")
    print("분기별 데이터:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}개월 데이터)")

    return quarterly_data

def prepare_prophet_data(quarterly_data, exog_data=None):
    """
    Prophet을 위한 데이터 준비 (깔끔한 버전)

    Parameters:
    - quarterly_data: 분기별 매출 데이터
    - exog_data: 외생변수 데이터 (옵션)

    Returns:
    - prophet_df: Prophet 형식 DataFrame
    """
    # Prophet 형식으로 변환 (ds, y 컬럼 필요)
    prophet_df = pd.DataFrame({
        'ds': quarterly_data['date_quarter_end'],
        'y': quarterly_data['revenue_billions']
    })

    # y 컬럼 NaN이 있는 행 제거
    if prophet_df['y'].isna().any():
        original_len = len(prophet_df)
        prophet_df = prophet_df.dropna(subset=['y'])
        removed = original_len - len(prophet_df)
        print(f"매출 데이터 NaN 제거: {removed}개 행 제거됨")

    # 외생변수 추가 (있는 경우)
    if exog_data is not None:
        print("외생변수 데이터 추가 중...")

        # 외생변수 컬럼들 확인
        exog_cols = [col for col in exog_data.columns
                     if col not in ['date_quarter_end', 'year', 'quarter', 'year_quarter']]

        print(f"추가할 외생변수: {exog_cols}")

        # 날짜를 기준으로 매핑
        for _, exog_row in exog_data.iterrows():
            exog_date = exog_row['date_quarter_end']
            mask = prophet_df['ds'] == exog_date

            if mask.any():
                for col in exog_cols:
                    prophet_df.loc[mask, col] = exog_row[col]

        # 외생변수가 매핑되지 않은 행 제거 (inner join 효과)
        for col in exog_cols:
            if col in prophet_df.columns:
                original_len = len(prophet_df)
                prophet_df = prophet_df.dropna(subset=[col])
                removed = original_len - len(prophet_df)
                if removed > 0:
                    print(f"외생변수 {col} 매핑 실패로 {removed}개 행 제거됨")

        print(f"추가된 외생변수: {exog_cols}")

    print(f"Prophet 데이터 준비 완료: {len(prophet_df)}개 분기")

    return prophet_df

def extract_exogenous_variables(data, date_col='date_month_end', data_end_date=None,
                                exog_cols=['expDlr']):
    """
    외생변수 추출 및 분기별 변환 (NaN 값 제거)

    Parameters:
    - data: 월별 데이터 DataFrame
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일
    - exog_cols: 외생변수 컬럼명 리스트

    Returns:
    - quarterly_exog: 분기별 외생변수 DataFrame
    """
    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 데이터 종료일 설정
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]

    # 외생변수 데이터 확인
    available_exog_cols = [col for col in exog_cols if col in df.columns]
    if not available_exog_cols:
        print(f"외생변수 컬럼을 찾을 수 없습니다: {exog_cols}")
        return None

    print(f"사용 가능한 외생변수: {available_exog_cols}")

    # NaN이 있는 행 제거 (dropna 사용)
    original_len = len(df)
    df_clean = df.dropna(subset=available_exog_cols)
    removed_rows = original_len - len(df_clean)

    print(f"외생변수 NaN 제거: {removed_rows}개 행 제거됨 (전체 {original_len}개 중)")

    if len(df_clean) == 0:
        print("외생변수 NaN 제거 후 유효한 데이터가 없습니다.")
        return None

    exog_data = df_clean.copy()

    # 분기 정보 추가
    exog_data['year'] = exog_data[date_col].dt.year
    exog_data['quarter'] = exog_data[date_col].dt.quarter

    # 분기별 그룹화 (분기 마지막 월의 데이터 사용)
    quarterly_exog_list = []

    for (year, quarter), group in exog_data.groupby(['year', 'quarter']):
        # 해당 분기의 마지막 월 데이터 사용
        last_month_data = group.loc[group[date_col].idxmax()]

        # 분기말 날짜 계산
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        exog_dict = {
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}"
        }

        # 외생변수 값 추가
        for col in available_exog_cols:
            exog_dict[col] = last_month_data[col]

        quarterly_exog_list.append(exog_dict)

    quarterly_exog = pd.DataFrame(quarterly_exog_list)
    quarterly_exog = quarterly_exog.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"추출된 분기별 외생변수 데이터: {len(quarterly_exog)}분기")

    return quarterly_exog

def prophet_quarterly_forecast(prophet_df, forecast_quarters=4, use_exog=False, exog_cols=None):
    """
    분기별 매출 Prophet 예측

    Parameters:
    - prophet_df: Prophet 형식 데이터
    - forecast_quarters: 예측할 분기 수
    - use_exog: 외생변수 사용 여부
    - exog_cols: 외생변수 컬럼 리스트

    Returns:
    - forecast_result: 예측 결과 DataFrame
    - model_info: 모델 정보
    """
    try:
        print(f"Prophet 모델링 시작 ({'외생변수 포함' if use_exog else '외생변수 미포함'})")

        # Prophet 모델 생성
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode='additive',
            changepoint_prior_scale=0.05
        )

        # 분기 계절성 추가
        model.add_seasonality(name='quarterly', period=365.25 / 4, fourier_order=4)

        # 외생변수 추가 (있는 경우)
        if use_exog and exog_cols:
            for col in exog_cols:
                if col in prophet_df.columns:
                    model.add_regressor(col)
                    print(f"외생변수 추가: {col}")

        print("Prophet 모델 훈련 중...")
        model.fit(prophet_df)

        # 미래 날짜 생성
        last_date = prophet_df['ds'].iloc[-1]
        future_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # 분기말로 조정
            quarter_end = pd.Timestamp(
                year=next_quarter_date.year,
                month=next_quarter_date.month,
                day=pd.Timestamp(next_quarter_date.year, next_quarter_date.month, 1).days_in_month
            )
            future_dates.append(quarter_end)

        # Future DataFrame 생성
        future_df = model.make_future_dataframe(periods=forecast_quarters, freq='QS')

        # 외생변수의 미래 값 설정 (있는 경우)
        if use_exog and exog_cols:
            print("외생변수의 미래 값 설정 중...")
            for col in exog_cols:
                if col in prophet_df.columns:
                    # 마지막 값 사용 (또는 트렌드 연장)
                    last_value = prophet_df[col].iloc[-1]

                    # 최근 트렌드 계산 (최근 4분기)
                    recent_data = prophet_df[col].tail(4)
                    if len(recent_data) >= 2:
                        trend = np.mean(np.diff(recent_data))
                    else:
                        trend = 0

                    # 미래 값 생성
                    for i, future_date in enumerate(future_dates):
                        mask = future_df['ds'] == future_date
                        if mask.any():
                            # 옵션 1: 마지막 값 유지
                            future_value = last_value
                            # 옵션 2: 트렌드 연장 (주석 해제하여 사용)
                            # future_value = last_value + trend * (i + 1)

                            future_df.loc[mask, col] = future_value
                            print(f"  {future_date.strftime('%Y-Q%m')} {col}: {future_value:.2f}")

        # 예측 수행
        forecast = model.predict(future_df)

        # 예측 부분만 추출
        forecast_only = forecast.tail(forecast_quarters).copy()

        # 결과 DataFrame 생성
        forecast_result = pd.DataFrame({
            'date_quarter_end': future_dates,
            'year': [d.year for d in future_dates],
            'quarter': [d.quarter for d in future_dates],
            'year_quarter': [f"{d.year}Q{d.quarter}" for d in future_dates],
            'revenue_billions_prophet_forecast' + ('_exog' if use_exog else ''): forecast_only['yhat'].values,
            'forecast_lower': forecast_only['yhat_lower'].values,
            'forecast_upper': forecast_only['yhat_upper'].values
        })

        # 모델 정보
        model_info = {
            'model_type': 'Prophet',
            'use_exogenous': use_exog,
            'exogenous_variables': exog_cols if use_exog else None,
            'model': model,
            'historical_data_points': len(prophet_df)
        }

        forecast_col = 'revenue_billions_prophet_forecast' + ('_exog' if use_exog else '')
        print(f"분기별 Prophet 예측 완료 ({'외생변수 포함' if use_exog else '외생변수 미포함'}):")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row[forecast_col]:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"Prophet 예측 실패: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def distribute_prophet_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end',
                                            use_exog=False):
    """
    분기별 Prophet 예측 결과를 월별로 분배

    Parameters:
    - quarterly_forecast: 분기별 예측 결과 DataFrame
    - original_data: 원본 월별 데이터 DataFrame
    - date_col: 날짜 컬럼명
    - use_exog: 외생변수 사용 여부

    Returns:
    - updated_data: 월별 예측값이 추가된 DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 컬럼명 설정
    forecast_col = 'revenue_billions_prophet_forecast' + ('_exog' if use_exog else '')

    # 예측 컬럼 초기화
    if 'revenue_billions' in df.columns:
        df[forecast_col] = df['revenue_billions'].copy()
    else:
        df[forecast_col] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    exog_text = '외생변수 포함 ' if use_exog else ''
    print(f"분기별 Prophet {exog_text}예측값을 월별로 분배 중...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row[forecast_col]

        # 해당 분기의 월들
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} Prophet {exog_text}예측값 {quarterly_value:.2f}B를 {months_in_quarter}월에 동일하게 적용")

        for month in months_in_quarter:
            # 해당 년월의 마지막 날 계산
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # 해당 날짜의 행 찾기
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, forecast_col] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: 해당 날짜 없음")

    return df

def revenue_prophet_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                      data_end_date=None, forecast_quarters=4,
                                      use_exogenous=True, exog_cols=['expDlr']):
    """
    매출 Prophet 예측 파이프라인 (외생변수 포함/미포함 둘 다)

    Parameters:
    - data: 월별 데이터 DataFrame
    - revenue_col: 매출 컬럼명
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일 (예: '2025-08-31')
    - forecast_quarters: 예측할 분기 수
    - use_exogenous: 외생변수 사용 여부
    - exog_cols: 외생변수 컬럼 리스트

    Returns:
    - result_data: 예측값이 추가된 데이터
    - quarterly_data: 분기별 데이터
    - forecast_results: 예측 결과 딕셔너리 {'no_exog': ..., 'with_exog': ...}
    - model_infos: 모델 정보 딕셔너리
    """
    print("=== 매출 Prophet 예측 파이프라인 시작 ===")
    print(f"데이터 종료일: {data_end_date if data_end_date else '전체 데이터 사용'}")
    print(f"예측 분기 수: {forecast_quarters}")
    print(f"외생변수 사용: {use_exogenous}")
    if use_exogenous:
        print(f"외생변수 컬럼: {exog_cols}")

    try:
        # 1. 분기별 데이터 추출
        print("\n1. 분기별 매출 데이터 추출")
        quarterly_data = extract_quarterly_revenue_prophet(data, revenue_col, date_col, data_end_date)

        result_data = data.copy()
        forecast_results = {}
        model_infos = {}

        # 2. 외생변수 미포함 Prophet 예측
        print("\n2. Prophet 예측 (외생변수 미포함)")
        prophet_df_no_exog = prepare_prophet_data(quarterly_data)

        forecast_no_exog, model_info_no_exog = prophet_quarterly_forecast(
            prophet_df_no_exog, forecast_quarters, use_exog=False
        )

        if forecast_no_exog is not None:
            print("외생변수 미포함 Prophet 예측 성공")
            result_data = distribute_prophet_quarterly_to_monthly(
                forecast_no_exog, result_data, date_col, use_exog=False
            )
            forecast_results['no_exog'] = forecast_no_exog
            model_infos['no_exog'] = model_info_no_exog
        else:
            print("외생변수 미포함 Prophet 예측 실패")
            forecast_results['no_exog'] = None
            model_infos['no_exog'] = None

        # 3. 외생변수 포함 Prophet 예측
        if use_exogenous:
            print("\n3. Prophet 예측 (외생변수 포함)")

            # 외생변수 데이터 추출
            quarterly_exog = extract_exogenous_variables(data, date_col, data_end_date, exog_cols)

            if quarterly_exog is not None:
                prophet_df_with_exog = prepare_prophet_data(quarterly_data, quarterly_exog)

                forecast_with_exog, model_info_with_exog = prophet_quarterly_forecast(
                    prophet_df_with_exog, forecast_quarters, use_exog=True, exog_cols=exog_cols
                )

                if forecast_with_exog is not None:
                    print("외생변수 포함 Prophet 예측 성공")
                    result_data = distribute_prophet_quarterly_to_monthly(
                        forecast_with_exog, result_data, date_col, use_exog=True
                    )
                    forecast_results['with_exog'] = forecast_with_exog
                    model_infos['with_exog'] = model_info_with_exog
                else:
                    print("외생변수 포함 Prophet 예측 실패")
                    forecast_results['with_exog'] = None
                    model_infos['with_exog'] = None
            else:
                print("외생변수 데이터 없음 - 외생변수 포함 예측 건너뜀")
                forecast_results['with_exog'] = None
                model_infos['with_exog'] = None
        else:
            forecast_results['with_exog'] = None
            model_infos['with_exog'] = None

        print("\n=== 매출 Prophet 예측 완료 ===")

        # 성공적으로 완료된 예측 수 확인
        successful_predictions = sum(1 for result in forecast_results.values() if result is not None)
        print(f"완료된 예측: {successful_predictions}/{'2' if use_exogenous else '1'}개")

        if successful_predictions > 0:
            print("추가된 컬럼:")
            if forecast_results['no_exog'] is not None:
                print("  - revenue_billions_prophet_forecast")
            if forecast_results['with_exog'] is not None:
                print("  - revenue_billions_prophet_forecast_exog")

        return result_data, quarterly_data, forecast_results, model_infos

    except Exception as e:
        print(f"Prophet 예측 파이프라인 오류: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None

def extract_quarterly_revenue_es(data, revenue_col='revenue_billions', date_col='date_month_end',
                                 data_end_date=None):

    df = data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # 데이터 종료일 설정
    if data_end_date:
        end_date = pd.to_datetime(data_end_date)
        df = df[df[date_col] <= end_date]
        print(f"데이터를 {end_date.strftime('%Y-%m')}까지로 제한했습니다.")

    # NaN이 아닌 매출 데이터만 사용
    revenue_data = df[df[revenue_col].notna()].copy()

    if len(revenue_data) == 0:
        raise ValueError("유효한 매출 데이터가 없습니다.")

    print(f"유효한 매출 데이터: {len(revenue_data)}개월")
    print(
        f"데이터 기간: {revenue_data[date_col].min().strftime('%Y-%m')} ~ {revenue_data[date_col].max().strftime('%Y-%m')}")

    # 분기 정보 추가
    revenue_data['year'] = revenue_data[date_col].dt.year
    revenue_data['quarter'] = revenue_data[date_col].dt.quarter
    revenue_data['year_quarter'] = revenue_data['year'].astype(str) + 'Q' + revenue_data['quarter'].astype(str)

    # 분기별 그룹화 (분기 마지막 월의 데이터 사용)
    quarterly_list = []

    for (year, quarter), group in revenue_data.groupby(['year', 'quarter']):
        # 해당 분기의 마지막 월 데이터 사용
        last_month_data = group.loc[group[date_col].idxmax()]

        # 분기말 날짜 계산
        quarter_month_map = {1: 3, 2: 6, 3: 9, 4: 12}
        quarter_end_month = quarter_month_map[quarter]
        quarter_end_date = pd.Timestamp(year=year, month=quarter_end_month,
                                        day=pd.Timestamp(year, quarter_end_month, 1).days_in_month)

        quarterly_list.append({
            'date_quarter_end': quarter_end_date,
            'year': year,
            'quarter': quarter,
            'year_quarter': f"{year}Q{quarter}",
            'revenue_billions': last_month_data[revenue_col],
            'data_months_in_quarter': len(group)
        })

    quarterly_data = pd.DataFrame(quarterly_list)
    quarterly_data = quarterly_data.sort_values('date_quarter_end').reset_index(drop=True)

    print(f"추출된 분기 데이터: {len(quarterly_data)}분기")
    print("분기별 데이터:")
    for _, row in quarterly_data.iterrows():
        print(f"  {row['year_quarter']}: {row['revenue_billions']:.2f}B ({row['data_months_in_quarter']}개월 데이터)")

    return quarterly_data

def exponential_smoothing_quarterly_forecast(quarterly_data, forecast_quarters=4):
    """
    분기별 매출 Exponential Smoothing 예측

    Parameters:
    - quarterly_data: 분기별 매출 DataFrame
    - forecast_quarters: 예측할 분기 수

    Returns:
    - forecast_result: 예측 결과 DataFrame
    - model_info: 모델 정보
    """
    try:
        revenue_series = quarterly_data['revenue_billions'].values

        if len(revenue_series) < 8:
            raise ValueError(f"Exponential Smoothing 모델링을 위해 최소 8분기 데이터가 필요합니다. 현재: {len(revenue_series)}분기")

        print(f"Exponential Smoothing 모델링 시작: {len(revenue_series)}분기 데이터 사용")

        # 시계열 데이터로 변환 (날짜 인덱스 사용)
        ts_data = pd.Series(
            revenue_series,
            index=pd.date_range(
                start=quarterly_data['date_quarter_end'].iloc[0],
                periods=len(revenue_series),
                freq='QS'
            )
        )

        print(f"매출 범위: {revenue_series.min():.2f}B ~ {revenue_series.max():.2f}B")

        # 다양한 Exponential Smoothing 모델 시도
        models_to_try = [
            # (trend, seasonal, damped_trend, seasonal_periods)
            ('add', 'add', False, 4),  # Holt-Winters Additive
            ('add', 'mul', False, 4),  # Holt-Winters Multiplicative
            ('add', 'add', True, 4),  # Damped Holt-Winters Additive
            ('add', 'mul', True, 4),  # Damped Holt-Winters Multiplicative
            ('add', None, False, None),  # Holt's Linear Trend
            ('add', None, True, None),  # Damped Holt's Linear Trend
            (None, None, False, None)  # Simple Exponential Smoothing
        ]

        best_aic = float('inf')
        best_model = None
        best_config = None

        print("다양한 Exponential Smoothing 모델 테스트 중...")

        for i, (trend, seasonal, damped, seasonal_periods) in enumerate(models_to_try):
            try:
                # 계절성 사용 시 데이터 길이 확인
                if seasonal is not None and seasonal_periods is not None:
                    if len(revenue_series) < seasonal_periods * 2:
                        print(f"  모델 {i + 1}: 계절성 모델을 위한 데이터 부족 (건너뜀)")
                        continue

                # 모델 생성
                if seasonal is not None and seasonal_periods is not None:
                    model = ExponentialSmoothing(
                        ts_data,
                        trend=trend,
                        seasonal=seasonal,
                        damped_trend=damped,
                        seasonal_periods=seasonal_periods
                    )
                    model_name = f"Holt-Winters ({seasonal})"
                else:
                    model = ExponentialSmoothing(
                        ts_data,
                        trend=trend,
                        damped_trend=damped
                    )
                    if trend is not None:
                        model_name = f"Holt ({'Damped' if damped else 'Linear'})"
                    else:
                        model_name = "Simple ES"

                # 모델 피팅
                fitted_model = model.fit(optimized=True, use_brute=False)

                # AIC 비교
                if fitted_model.aic < best_aic:
                    best_aic = fitted_model.aic
                    best_model = fitted_model
                    best_config = (trend, seasonal, damped, seasonal_periods, model_name)

                print(f"  모델 {i + 1} ({model_name}): AIC = {fitted_model.aic:.2f}")

            except Exception as e:
                print(f"  모델 {i + 1}: 실패 ({str(e)[:50]}...)")
                continue

        # 최적 모델을 찾지 못한 경우 Simple ES 사용
        if best_model is None:
            print("최적 모델을 찾지 못해 Simple Exponential Smoothing 사용")
            model = ExponentialSmoothing(ts_data, trend=None)
            best_model = model.fit(optimized=True)
            best_config = (None, None, False, None, "Simple ES (Fallback)")
            best_aic = best_model.aic

        trend, seasonal, damped, seasonal_periods, model_name = best_config
        print(f"\n최적 모델: {model_name} (AIC: {best_aic:.2f})")

        # 모델 파라미터 출력 (안전하게)
        try:
            print("모델 파라미터:")
            if hasattr(best_model, 'params'):
                for param_name, param_value in best_model.params.items():
                    if param_value is not None:
                        try:
                            if isinstance(param_value, (list, np.ndarray)):
                                if len(param_value) == 1:
                                    print(f"  - {param_name}: {float(param_value[0]):.4f}")
                                else:
                                    print(f"  - {param_name}: {[float(x) for x in param_value[:3]]}")
                            else:
                                print(f"  - {param_name}: {float(param_value):.4f}")
                        except (ValueError, TypeError):
                            print(f"  - {param_name}: {str(param_value)}")
        except Exception:
            print("모델 파라미터 출력 생략")

        # 예측 수행
        print(f"Exponential Smoothing으로 {forecast_quarters}분기 예측 중...")
        forecast = best_model.forecast(steps=forecast_quarters)

        # 예측값 검증
        if isinstance(forecast, pd.Series):
            forecast_values = forecast.values
        else:
            forecast_values = np.array(forecast)

        # NaN 체크
        if np.isnan(forecast_values).any():
            print("Warning: NaN values in forecast, using last known value")
            last_value = revenue_series[-1]
            forecast_values = np.nan_to_num(forecast_values, nan=last_value)

        # 무한값 체크
        if np.isinf(forecast_values).any():
            print("Warning: Infinite values in forecast, using last known value")
            last_value = revenue_series[-1]
            forecast_values = np.where(np.isinf(forecast_values), last_value, forecast_values)

        # 신뢰구간 계산 (기본값 사용)
        try:
            # 잔차의 표준편차 사용
            residuals = best_model.resid
            forecast_std = np.std(residuals) if residuals is not None else np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std
        except:
            print("신뢰구간 계산 실패, 기본값 사용")
            forecast_std = np.std(revenue_series) * 0.1
            forecast_lower = forecast_values - 1.96 * forecast_std
            forecast_upper = forecast_values + 1.96 * forecast_std

        # 예측 날짜 생성
        last_date = quarterly_data['date_quarter_end'].iloc[-1]
        forecast_dates = []

        for i in range(1, forecast_quarters + 1):
            next_quarter_date = last_date + pd.DateOffset(months=3 * i)
            # 분기말로 조정
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
            'revenue_billions_es_forecast': forecast_values,
            'forecast_lower': forecast_lower,
            'forecast_upper': forecast_upper
        })

        # 모델 정보
        model_info = {
            'model_type': 'ExponentialSmoothing',
            'best_config': best_config,
            'aic': best_aic,
            'model': best_model,
            'historical_data_points': len(revenue_series)
        }

        print("분기별 Exponential Smoothing 예측 완료:")
        for _, row in forecast_result.iterrows():
            print(f"  {row['year_quarter']}: {row['revenue_billions_es_forecast']:.2f}B")

        return forecast_result, model_info

    except Exception as e:
        print(f"Exponential Smoothing 예측 실패: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def distribute_es_quarterly_to_monthly(quarterly_forecast, original_data, date_col='date_month_end'):
    """
    분기별 Exponential Smoothing 예측 결과를 월별로 분배

    Parameters:
    - quarterly_forecast: 분기별 예측 결과 DataFrame
    - original_data: 원본 월별 데이터 DataFrame
    - date_col: 날짜 컬럼명

    Returns:
    - updated_data: 월별 예측값이 추가된 DataFrame
    """
    df = original_data.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    # revenue_billions_es_forecast 컬럼 초기화
    if 'revenue_billions' in df.columns:
        df['revenue_billions_es_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_es_forecast'] = np.nan

    quarter_month_map = {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}

    print("분기별 Exponential Smoothing 예측값을 월별로 분배 중...")

    for _, forecast_row in quarterly_forecast.iterrows():
        year = forecast_row['year']
        quarter = forecast_row['quarter']
        quarterly_value = forecast_row['revenue_billions_es_forecast']

        # 해당 분기의 월들
        months_in_quarter = quarter_month_map[quarter]

        print(f"{year}Q{quarter} ES 예측값 {quarterly_value:.2f}B를 {months_in_quarter}월에 동일하게 적용")

        for month in months_in_quarter:
            # 해당 년월의 마지막 날 계산
            month_end = pd.Timestamp(year=year, month=month,
                                     day=pd.Timestamp(year, month, 1).days_in_month)

            # 해당 날짜의 행 찾기
            mask = df[date_col] == month_end
            if mask.any():
                df.loc[mask, 'revenue_billions_es_forecast'] = quarterly_value
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: {quarterly_value:.2f}B")
            else:
                print(f"  -> {month_end.strftime('%Y-%m-%d')}: 해당 날짜 없음")

    return df

def revenue_es_forecast_pipeline(data, revenue_col='revenue_billions', date_col='date_month_end',
                                 data_end_date=None, forecast_quarters=4):
    """
    매출 Exponential Smoothing 예측 파이프라인

    Parameters:
    - data: 월별 데이터 DataFrame
    - revenue_col: 매출 컬럼명
    - date_col: 날짜 컬럼명
    - data_end_date: 데이터 종료일 (예: '2025-08-31')
    - forecast_quarters: 예측할 분기 수

    Returns:
    - result_data: 예측값이 추가된 데이터
    - quarterly_data: 분기별 데이터
    - forecast_result: 분기별 예측 결과
    - model_info: 모델 정보
    """
    print("=== 매출 Exponential Smoothing 예측 파이프라인 시작 ===")
    print(f"데이터 종료일: {data_end_date if data_end_date else '전체 데이터 사용'}")
    print(f"예측 분기 수: {forecast_quarters}")

    try:
        # 1. 분기별 데이터 추출
        print("\n1. 분기별 매출 데이터 추출")
        quarterly_data = extract_quarterly_revenue_es(data, revenue_col, date_col, data_end_date)

        # 2. Exponential Smoothing 예측
        print("\n2. 분기별 Exponential Smoothing 예측")
        forecast_result, model_info = exponential_smoothing_quarterly_forecast(
            quarterly_data, forecast_quarters
        )

        if forecast_result is None:
            print("Exponential Smoothing 예측 실패")
            return None, quarterly_data, None, None

        # 3. 월별 분배
        print("\n3. 분기별 ES 예측값을 월별로 분배")
        result_data = distribute_es_quarterly_to_monthly(forecast_result, data, date_col)

        print("\n=== 매출 Exponential Smoothing 예측 완료 ===")
        print(f"예측된 분기: {len(forecast_result)}개")
        print(f"업데이트된 월별 데이터: {len(result_data)}개월")
        print("추가된 컬럼: revenue_billions_es_forecast")

        return result_data, quarterly_data, forecast_result, model_info

    except Exception as e:
        print(f"Exponential Smoothing 예측 파이프라인 오류: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None, None


if __name__ == "__main__":
    test_sarima_module()