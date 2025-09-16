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
        export_yoy_train = export_yoy.loc[target_ts.index]

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


if __name__ == "__main__":
    test_sarima_module()