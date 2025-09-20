#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETS (Exponential Smoothing) 기반 매출(revenue_billions) 및 PSR_ttm 예측 모델
- 단변량 시계열 예측
- 자동 ETS 모델 선택
- 미래 값 예측
"""

import pandas as pd
import numpy as np
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import warnings

warnings.filterwarnings('ignore')


# ==============================================
# ETS 모델 클래스
# ==============================================

class ETSPredictor:
    def __init__(self):
        self.models = {}
        self.forecasts = {}
        self.fitted_models = {}

    def prepare_ets_data(self, df, target_col):
        """ETS를 위한 데이터 전처리"""
        data = df[['date_month_end', target_col]].copy()
        data = data.dropna()
        data = data.sort_values('date_month_end').reset_index(drop=True)

        # 날짜를 인덱스로 설정
        data.set_index('date_month_end', inplace=True)

        # 월별 빈도로 설정
        data.index = pd.to_datetime(data.index)
        data = data.asfreq('M')

        return data[target_col]

    def find_best_ets_model(self, series):
        """최적의 ETS 모델 자동 선택"""
        best_aic = np.inf
        best_model = None
        best_params = None

        # 다양한 ETS 조합 시도
        error_types = ['add', 'mul']
        trend_types = [None, 'add', 'mul']
        seasonal_types = [None, 'add', 'mul']

        for error in error_types:
            for trend in trend_types:
                for seasonal in seasonal_types:
                    try:
                        # 계절성이 있는 경우 충분한 데이터 확인
                        if seasonal is not None and len(series) < 24:
                            continue

                        model = ETSModel(
                            series,
                            error=error,
                            trend=trend,
                            seasonal=seasonal,
                            seasonal_periods=12 if seasonal is not None else None
                        )

                        fitted_model = model.fit(disp=False)

                        if fitted_model.aic < best_aic:
                            best_aic = fitted_model.aic
                            best_model = fitted_model
                            best_params = (error, trend, seasonal)

                    except:
                        continue

        # 최적 모델을 찾지 못한 경우 단순 모델 사용
        if best_model is None:
            try:
                model = ETSModel(series, error='add', trend='add', seasonal=None)
                best_model = model.fit(disp=False)
                best_params = ('add', 'add', None)
            except:
                # 가장 단순한 모델
                model = ETSModel(series, error='add', trend=None, seasonal=None)
                best_model = model.fit(disp=False)
                best_params = ('add', None, None)

        return best_model, best_params

    def fit_and_predict(self, df, target_col, periods=12):
        """모델 학습 및 예측"""
        # 데이터 준비
        series = self.prepare_ets_data(df, target_col)

        if len(series) < 6:  # 최소 데이터 요구사항
            return None, None

        # 최적 ETS 모델 찾기
        fitted_model, params = self.find_best_ets_model(series)

        if fitted_model is None:
            return None, None

        # 예측 수행
        forecast = fitted_model.forecast(steps=periods)

        # 신뢰구간 계산 (시뮬레이션 기반)
        forecast_ci = fitted_model.get_prediction(
            start=len(series),
            end=len(series) + periods - 1
        )

        # 모델 및 예측 결과 저장
        self.fitted_models[target_col] = fitted_model
        self.models[target_col] = params

        # 미래 날짜 생성
        last_date = series.index[-1]
        future_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=1),
            periods=periods,
            freq='M'
        )

        # 결과 정리
        results = {
            'dates': future_dates,
            'forecast': forecast.values,
            'lower_ci': forecast_ci.conf_int().iloc[:, 0].values,
            'upper_ci': forecast_ci.conf_int().iloc[:, 1].values,
            'model_params': params
        }

        self.forecasts[target_col] = results

        return fitted_model, results

    def get_predictions(self, target_col):
        """특정 타겟의 예측 결과 반환"""
        if target_col not in self.forecasts:
            return None

        forecast_data = self.forecasts[target_col]

        predictions = pd.DataFrame({
            'date_month_end': forecast_data['dates'],
            f'{target_col}_pred': forecast_data['forecast'],
            f'{target_col}_lower': forecast_data['lower_ci'],
            f'{target_col}_upper': forecast_data['upper_ci']
        })

        return predictions


# ==============================================
# 평가 함수
# ==============================================

def evaluate_ets_model(actual, predicted, target_name):
    """ETS 모델 성능 평가"""
    # 유효한 데이터만 사용
    mask = ~(np.isnan(actual) | np.isnan(predicted))
    actual_clean = actual[mask]
    predicted_clean = predicted[mask]

    if len(actual_clean) == 0:
        return {}

    mse = mean_squared_error(actual_clean, predicted_clean)
    mae = mean_absolute_error(actual_clean, predicted_clean)
    r2 = r2_score(actual_clean, predicted_clean)

    return {
        f'{target_name}_MSE': mse,
        f'{target_name}_RMSE': np.sqrt(mse),
        f'{target_name}_MAE': mae,
        f'{target_name}_R2': r2
    }


def get_model_selection_info(predictor, target_cols):
    """선택된 ETS 모델 정보 반환"""
    model_info = {}

    for target_col in target_cols:
        if target_col in predictor.models:
            error, trend, seasonal = predictor.models[target_col]
            model_info[target_col] = {
                'error': error,
                'trend': trend,
                'seasonal': seasonal,
                'model_string': f"ETS({error[0].upper()},{trend[0].upper() if trend else 'N'},{seasonal[0].upper() if seasonal else 'N'})"
            }

    return model_info


# ==============================================
# 메인 예측 함수들
# ==============================================

def predict_with_ets(df, target_cols=['revenue_billions', 'PSR_ttm'], periods=12):
    """
    ETS를 사용한 예측

    Parameters:
    - df: 전처리된 데이터프레임
    - target_cols: 예측할 타겟 컬럼들
    - periods: 예측할 기간 수

    Returns:
    - dict: 예측 결과들
    """
    predictor = ETSPredictor()
    results = {
        'models': {},
        'forecasts': {},
        'predictor': predictor
    }

    for target_col in target_cols:
        if target_col not in df.columns:
            continue

        # ETS 모델 학습 및 예측
        fitted_model, forecast_results = predictor.fit_and_predict(
            df, target_col, periods=periods
        )

        if fitted_model is not None:
            results['models'][target_col] = fitted_model
            results['forecasts'][target_col] = forecast_results

    return results


def create_ets_forecast_dataframe(df, results, target_cols):
    """ETS 예측 결과를 DataFrame으로 변환"""
    # 원본 데이터
    base_df = df[['date_month_end'] + target_cols].copy()
    base_df['data_type'] = 'actual'

    combined_dfs = [base_df]

    predictor = results['predictor']

    for target_col in target_cols:
        if target_col in results['forecasts']:
            pred_df = predictor.get_predictions(target_col)
            if pred_df is not None:
                pred_df = pred_df.rename(columns={f'{target_col}_pred': target_col})
                pred_df['data_type'] = 'ets_forecast'

                # 다른 타겟 컬럼들은 NaN으로 설정
                for other_col in target_cols:
                    if other_col != target_col and other_col not in pred_df.columns:
                        pred_df[other_col] = np.nan

                combined_dfs.append(pred_df[['date_month_end', target_col, 'data_type']])

    # 모든 데이터 결합
    combined_df = pd.concat(combined_dfs, ignore_index=True)
    combined_df = combined_df.sort_values(['data_type', 'date_month_end']).reset_index(drop=True)

    return combined_df


def perform_backtest(df, target_col, test_periods=6):
    """ETS 모델 백테스팅"""
    if len(df) < test_periods + 12:
        return None

    # 훈련/테스트 데이터 분할
    train_df = df.iloc[:-test_periods].copy()
    test_df = df.iloc[-test_periods:].copy()

    # ETS 모델 학습
    predictor = ETSPredictor()
    fitted_model, forecast_results = predictor.fit_and_predict(
        train_df, target_col, periods=test_periods
    )

    if fitted_model is None:
        return None

    # 예측값과 실제값 비교
    predicted_values = forecast_results['forecast']
    actual_values = test_df[target_col].values

    # 성능 평가
    evaluation = evaluate_ets_model(actual_values, predicted_values, target_col)

    return {
        'actual': actual_values,
        'predicted': predicted_values,
        'evaluation': evaluation,
        'test_dates': test_df['date_month_end'].values
    }


# ==============================================
# 실행 함수
# ==============================================

def run_ets_prediction(df, prediction_months=12):
    """
    ETS 예측 실행 메인 함수

    Parameters:
    - df: 전처리된 데이터프레임
    - prediction_months: 예측할 개월 수

    Returns:
    - tuple: (예측 결과 DataFrame, 모델 결과 딕셔너리)
    """

    target_cols = ['revenue_billions', 'PSR_ttm']

    # ETS 예측 수행
    results = predict_with_ets(
        df,
        target_cols=target_cols,
        periods=prediction_months
    )

    # 예측 결과 DataFrame 생성
    prediction_df = create_ets_forecast_dataframe(df, results, target_cols)

    # 모델 선택 정보 추가
    model_info = get_model_selection_info(results['predictor'], target_cols)
    results['model_selection'] = model_info

    return prediction_df, results


def get_ets_forecast_summary(results, target_cols):
    """ETS 예측 결과 요약 정보 생성"""
    summary = {}
    predictor = results['predictor']

    for target_col in target_cols:
        if target_col in results['forecasts']:
            pred_df = predictor.get_predictions(target_col)
            if pred_df is not None:
                summary[target_col] = {
                    'mean_prediction': pred_df[f'{target_col}_pred'].mean(),
                    'first_prediction': pred_df[f'{target_col}_pred'].iloc[0],
                    'last_prediction': pred_df[f'{target_col}_pred'].iloc[-1],
                    'model_type': results['model_selection'].get(target_col, {}).get('model_string', 'Unknown')
                }

    return summary


def validate_ets_models(df, target_cols=['revenue_billions', 'PSR_ttm'], test_periods=6):
    """ETS 모델들의 백테스팅 수행"""
    validation_results = {}

    for target_col in target_cols:
        if target_col in df.columns:
            backtest_result = perform_backtest(df, target_col, test_periods)
            if backtest_result is not None:
                validation_results[target_col] = backtest_result

    return validation_results


# ==============================================
# 사용 예시
# ==============================================

"""
# 사용 예시:

# 1. ETS 예측 수행
prediction_df, model_results = run_ets_prediction(
    df=preprocessed_data,  # 전처리된 데이터
    prediction_months=12
)

# 2. 예측 결과 확인
ets_forecasts = prediction_df[prediction_df['data_type'] == 'ets_forecast']
print("ETS 예측 결과:")
print(ets_forecasts[['date_month_end', 'revenue_billions', 'PSR_ttm']].head())

# 3. 선택된 모델 정보 확인
if 'model_selection' in model_results:
    print("\n선택된 ETS 모델:")
    for target, info in model_results['model_selection'].items():
        print(f"{target}: {info['model_string']}")

# 4. 예측 요약 정보
summary = get_ets_forecast_summary(model_results, ['revenue_billions', 'PSR_ttm'])
print("\n예측 요약:")
for target, data in summary.items():
    print(f"{target}:")
    print(f"  평균 예측값: {data['mean_prediction']:.2f}")
    print(f"  모델 타입: {data['model_type']}")

# 5. 백테스팅 (선택사항)
validation_results = validate_ets_models(preprocessed_data, test_periods=6)
for target, results in validation_results.items():
    print(f"\n{target} 백테스팅 결과:")
    for metric, value in results['evaluation'].items():
        print(f"  {metric}: {value:.4f}")
"""