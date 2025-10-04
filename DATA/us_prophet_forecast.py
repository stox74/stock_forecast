#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prophet 기반 매출(revenue_billions) 및 PSR_ttm 예측 모델
- 단변량 시계열 예측 (외생변수 미사용)
- 다변량 시계열 예측 (expDlr_yoy 외생변수 사용)
- 미래 값 예측
"""

import pandas as pd
import numpy as np
from prophet import Prophet
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import warnings

warnings.filterwarnings('ignore')


def ensure_sorted_unique_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    ensure_sorted_unique_dates
    - 'date_month_end' 컬럼을 datetime으로 변환하고
    - 중복 제거 및 오름차순 정렬 수행
    """
    if 'date_month_end' not in df.columns:
        raise ValueError("DataFrame must contain 'date_month_end' column")

    df = df.copy()
    df['date_month_end'] = pd.to_datetime(df['date_month_end'], errors='coerce')
    df = df.drop_duplicates(subset=['date_month_end']).sort_values('date_month_end').reset_index(drop=True)
    return df


# ==============================================
# Prophet 모델 클래스
# ==============================================

class ProphetPredictor:
    def __init__(self, use_exogenous=False):
        self.use_exogenous = use_exogenous
        self.models = {}
        self.forecasts = {}

    def prepare_prophet_data(self, df, target_col, exog_col=None):
        """Prophet 형식으로 데이터 변환"""
        data = df[['date_month_end', target_col]].copy()
        data = data.dropna()
        data.columns = ['ds', 'y']

        # 외생변수 추가
        if self.use_exogenous and exog_col and exog_col in df.columns:
            exog_data = df[['date_month_end', exog_col]].copy()
            exog_data = exog_data.dropna()
            exog_data.columns = ['ds', exog_col]
            data = pd.merge(data, exog_data, on='ds', how='inner')

        return data

    def create_future_exog(self, historical_data, exog_col, periods):
        """외생변수의 미래값 생성 (단순 평균 또는 추세 연장)"""
        if not self.use_exogenous or exog_col not in historical_data.columns:
            return None

        # 최근 12개월 평균 사용
        recent_mean = historical_data[exog_col].tail(12).mean()

        # 마지막 날짜에서 시작하여 미래 날짜 생성
        last_date = historical_data['ds'].max()
        future_dates = pd.date_range(start=last_date + pd.DateOffset(months=1),
                                     periods=periods, freq='M')

        # 외생변수 미래값을 최근 평균으로 설정
        future_exog = pd.DataFrame({
            'ds': future_dates,
            exog_col: [recent_mean] * periods
        })

        return future_exog

    def fit_and_predict(self, df, target_col, exog_col='expDlr_yoy', periods=12):
        """모델 학습 및 예측"""
        # 데이터 준비
        data = self.prepare_prophet_data(df, target_col, exog_col)

        if len(data) < 12:
            return None, None

        # Prophet 모델 생성
        model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            seasonality_mode='multiplicative',
            changepoint_prior_scale=0.05
        )

        # 외생변수 추가
        if self.use_exogenous and exog_col in data.columns:
            model.add_regressor(exog_col)

        # 모델 학습
        model.fit(data)

        # 미래 데이터프레임 생성
        future = model.make_future_dataframe(periods=periods, freq='M')

        # 외생변수 미래값 추가
        if self.use_exogenous and exog_col in data.columns:
            # 기존 데이터의 외생변수 값 추가
            historical_exog = data[['ds', exog_col]]
            future = pd.merge(future, historical_exog, on='ds', how='left')

            # 미래 외생변수 값 생성
            future_exog = self.create_future_exog(data, exog_col, periods)
            if future_exog is not None:
                # 미래 부분의 외생변수 값 채우기
                future_mask = future['ds'] > data['ds'].max()
                future_indices = future[future_mask].index

                for i, idx in enumerate(future_indices):
                    if i < len(future_exog):
                        future.loc[idx, exog_col] = future_exog.iloc[i][exog_col]

        # 예측 수행
        forecast = model.predict(future)

        # 모델 및 예측 결과 저장
        self.models[target_col] = model
        self.forecasts[target_col] = forecast

        return model, forecast

    def get_predictions(self, target_col, periods=12):
        """특정 타겟의 예측 결과 반환"""
        if target_col not in self.forecasts:
            return None

        forecast = self.forecasts[target_col]

        # 미래 예측값만 추출
        future_predictions = forecast.tail(periods)[['ds', 'yhat', 'yhat_lower', 'yhat_upper']]
        future_predictions = future_predictions.copy()
        future_predictions.columns = ['date_month_end', f'{target_col}_pred',
                                      f'{target_col}_lower', f'{target_col}_upper']

        return future_predictions


# ==============================================
# 평가 함수
# ==============================================

def evaluate_prophet_model(actual, predicted, target_name):
    """Prophet 모델 성능 평가"""
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


# ==============================================
# 메인 예측 함수들
# ==============================================

def predict_with_prophet(df, target_cols=['revenue_billions', 'PSR_ttm'],
                         exog_col='expDlr_yoy', periods=12):
    """
    Prophet을 사용한 예측 (외생변수 사용/미사용 모두)

    Parameters:
    - df: 전처리된 데이터프레임
    - target_cols: 예측할 타겟 컬럼들
    - exog_col: 외생변수 컬럼
    - periods: 예측할 기간 수

    Returns:
    - dict: 예측 결과들
    """
    results = {
        'without_exog': {},
        'with_exog': {},
        'evaluations': {}
    }

    for target_col in target_cols:
        if target_col not in df.columns:
            continue

        # 1. 외생변수 미사용 예측
        predictor_no_exog = ProphetPredictor(use_exogenous=False)
        model_no_exog, forecast_no_exog = predictor_no_exog.fit_and_predict(
            df, target_col, periods=periods
        )

        if model_no_exog is not None:
            results['without_exog'][target_col] = {
                'model': model_no_exog,
                'forecast': forecast_no_exog,
                'predictions': predictor_no_exog.get_predictions(target_col, periods)
            }

        # 2. 외생변수 사용 예측
        if exog_col in df.columns and df[exog_col].notna().sum() > 12:
            predictor_with_exog = ProphetPredictor(use_exogenous=True)
            model_with_exog, forecast_with_exog = predictor_with_exog.fit_and_predict(
                df, target_col, exog_col, periods=periods
            )

            if model_with_exog is not None:
                results['with_exog'][target_col] = {
                    'model': model_with_exog,
                    'forecast': forecast_with_exog,
                    'predictions': predictor_with_exog.get_predictions(target_col, periods)
                }

    return results


def create_combined_forecast_dataframe(df, results, target_cols):
    """예측 결과를 통합 DataFrame으로 변환"""
    # 원본 데이터
    base_df = df[['date_month_end'] + target_cols].copy()
    base_df['data_type'] = 'actual'

    combined_dfs = [base_df]

    for target_col in target_cols:
        # 외생변수 미사용 예측
        if target_col in results['without_exog']:
            pred_df = results['without_exog'][target_col]['predictions'].copy()
            pred_df = pred_df.rename(columns={f'{target_col}_pred': target_col})
            pred_df['data_type'] = 'prophet_no_exog'

            # 다른 타겟 컬럼들은 NaN으로 설정
            for other_col in target_cols:
                if other_col != target_col and other_col not in pred_df.columns:
                    pred_df[other_col] = np.nan

            combined_dfs.append(pred_df[['date_month_end', target_col, 'data_type']])

        # 외생변수 사용 예측
        if target_col in results['with_exog']:
            pred_df = results['with_exog'][target_col]['predictions'].copy()
            pred_df = pred_df.rename(columns={f'{target_col}_pred': target_col})
            pred_df['data_type'] = 'prophet_with_exog'

            # 다른 타겟 컬럼들은 NaN으로 설정
            for other_col in target_cols:
                if other_col != target_col and other_col not in pred_df.columns:
                    pred_df[other_col] = np.nan

            combined_dfs.append(pred_df[['date_month_end', target_col, 'data_type']])

    # 모든 데이터 결합
    combined_df = pd.concat(combined_dfs, ignore_index=True)
    combined_df = combined_df.sort_values(['data_type', 'date_month_end']).reset_index(drop=True)

    return combined_df


# ==============================================
# 실행 함수
# ==============================================

def run_prophet_prediction(df, ticker=None, prediction_months=12):
    """
    Prophet 예측 실행 메인 함수

    Parameters:
    - df: 전처리된 데이터프레임
    - ticker: 종목명 (결과 메타데이터용)
    - prediction_months: 예측할 개월 수

    Returns:
    - tuple: (예측 결과 DataFrame, 모델 결과 딕셔너리)
    """

    target_cols = ['revenue_billions', 'PSR_ttm']

    # Prophet 예측 수행
    results = predict_with_prophet(
        df,
        target_cols=target_cols,
        exog_col='expDlr_yoy',
        periods=prediction_months
    )

    # 예측 결과 DataFrame 생성
    prediction_df = create_combined_forecast_dataframe(df, results, target_cols)

    # ticker 정보를 결과에 추가
    if ticker:
        results['ticker'] = ticker
        # prediction_df에도 ticker 컬럼 추가
        prediction_df['ticker'] = ticker

    return prediction_df, results


def get_forecast_summary(results, target_cols):
    """예측 결과 요약 정보 생성"""
    summary = {}

    for target_col in target_cols:
        summary[target_col] = {}

        # 외생변수 미사용 예측
        if target_col in results['without_exog']:
            pred = results['without_exog'][target_col]['predictions']
            if pred is not None:
                summary[target_col]['without_exog'] = {
                    'mean_prediction': pred[f'{target_col}_pred'].mean(),
                    'first_prediction': pred[f'{target_col}_pred'].iloc[0],
                    'last_prediction': pred[f'{target_col}_pred'].iloc[-1]
                }

        # 외생변수 사용 예측
        if target_col in results['with_exog']:
            pred = results['with_exog'][target_col]['predictions']
            if pred is not None:
                summary[target_col]['with_exog'] = {
                    'mean_prediction': pred[f'{target_col}_pred'].mean(),
                    'first_prediction': pred[f'{target_col}_pred'].iloc[0],
                    'last_prediction': pred[f'{target_col}_pred'].iloc[-1]
                }

    return summary

def run_prophet_psr_only(df: pd.DataFrame,
                         ticker: str = 'UNKNOWN',
                         prediction_months: int = 12,
                         start_date: Optional[Union[str, pd.Timestamp]] = None,
                         use_exogenous: bool = False,
                         exog_col: Optional[str] = None
                         ) -> tuple[pd.DataFrame, dict]:
    """
    Prophet으로 PSR_ttm만 월별 예측.
    - prediction_months: 예측 개월 수 (기본 12)
    - start_date: 예측 시작 월말 (None이면 자동 설정: 데이터 마지막 월 + 1개월)
    - use_exogenous: 외생변수 사용 여부
    - exog_col: 외생변수 칼럼명 (use_exogenous=True일 때만 유효)
    반환: (예측 칼럼이 병합된 DataFrame, 결과 dict)
    """
    # ✅ 1) 날짜 정규화 및 정렬
    df = ensure_sorted_unique_dates(df)
    out_df = df.copy()

    # ✅ 2) start_date 자동 지정
    if start_date is None:
        last_date = pd.to_datetime(out_df['date_month_end'].max())
        # 마지막 월의 다음 월 말일
        start_date = (last_date + pd.offsets.MonthEnd(1)).normalize()

    # ✅ 3) Prophet 예측 수행
    predictor = ProphetPredictor(use_exogenous=use_exogenous)
    fc = predictor.fit_and_predict(
        df=df,
        target_col='PSR_ttm',
        periods=prediction_months,
        start_date=start_date,
        exog_col=(exog_col if use_exogenous else None)
    )

    # ✅ 4) 결과 저장
    results = {
        'meta': {
            'ticker': ticker,
            'target': 'PSR_ttm',
            'prediction_months': prediction_months,
            'start_date': pd.to_datetime(start_date),
            'use_exogenous': use_exogenous,
            'exog_col': exog_col
        },
        'psr': {}
    }

    if fc is not None and len(fc) > 0:
        # ✅ 5) 예측 결과를 원본 df에 병합
        s = pd.Series(fc['yhat'].values, index=pd.to_datetime(fc['ds']))
        add = pd.DataFrame({'date_month_end': s.index})
        out_df = (pd.merge(out_df, add, on='date_month_end', how='outer')
                    .sort_values('date_month_end')
                    .reset_index(drop=True))

        col = "PSR_prophet_forecast_exog" if use_exogenous else "PSR_prophet_forecast_noexog"
        out_df.loc[out_df['date_month_end'].isin(s.index), col] = \
            out_df.loc[out_df['date_month_end'].isin(s.index), 'date_month_end'].map(s)

        # 과거 구간 보정 (NaN → 실제값)
        if 'PSR_ttm' in out_df.columns:
            out_df[col] = out_df[col].combine_first(out_df['PSR_ttm'])

        results['psr']['forecast'] = fc[['ds', 'yhat']]

    # ✅ 6) 인덱스 정리
    out_df = out_df.sort_values('date_month_end').set_index('date_month_end')
    return out_df, results



# ==============================================
# 사용 예시
# ==============================================

"""
# 사용 예시:

# 1. Prophet 예측 수행 (ticker 포함)
prediction_df, model_results = run_prophet_prediction(
    df=preprocessed_data,  # 전처리된 데이터
    ticker='AMAT',  # 종목명
    prediction_months=12
)

# 2. 예측 결과 확인
# 외생변수 미사용 예측
no_exog_revenue = prediction_df[
    (prediction_df['data_type'] == 'prophet_no_exog') & 
    (prediction_df['revenue_billions'].notna())
]

# 외생변수 사용 예측
with_exog_revenue = prediction_df[
    (prediction_df['data_type'] == 'prophet_with_exog') & 
    (prediction_df['revenue_billions'].notna())
]

# 3. 예측 요약 정보
summary = get_forecast_summary(model_results, ['revenue_billions', 'PSR_ttm'])
for target, data in summary.items():
    if 'without_exog' in data:
        print(f"{target} 외생변수 미사용: {data['without_exog']['mean_prediction']:.2f}")
    if 'with_exog' in data:
        print(f"{target} 외생변수 사용: {data['with_exog']['mean_prediction']:.2f}")

# 4. ticker 정보 확인
print(f"분석 종목: {model_results.get('ticker', 'Unknown')}")
"""