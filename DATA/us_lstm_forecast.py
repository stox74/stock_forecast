#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LSTM 기반 매출(revenue_billions) 및 PSR_ttm 예측 모델
- 시계열 데이터 전처리
- LSTM 모델 구축 및 훈련
- 미래 값 예측
- 결과 시각화
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
import warnings

warnings.filterwarnings('ignore')


# ==============================================
# 데이터 전처리 함수들
# ==============================================

class LSTMPreprocessor:
    def __init__(self, sequence_length=12):
        self.sequence_length = sequence_length
        self.scalers = {}

    def prepare_data(self, df, target_cols, feature_cols=None):
        """LSTM을 위한 데이터 전처리"""
        df = df.copy()
        df = df.sort_values('date_month_end').reset_index(drop=True)

        # 결측치 처리
        df = df.dropna(subset=target_cols)

        if len(df) < self.sequence_length + 1:
            raise ValueError(f"데이터가 부족합니다. 최소 {self.sequence_length + 1}개의 유효한 데이터가 필요합니다.")

        # 특성 컬럼 설정
        if feature_cols is None:
            feature_cols = target_cols.copy()
            # 추가 특성들
            additional_features = ['market_cap_billions', 'revenue_ttm_billions', 'expDlr']
            for col in additional_features:
                if col in df.columns and col not in feature_cols:
                    feature_cols.append(col)

        # 유효한 컬럼만 선택
        available_features = [col for col in feature_cols if col in df.columns and df[col].notna().any()]

        # 데이터 추출 및 스케일링
        feature_data = df[available_features].fillna(method='ffill').fillna(method='bfill')
        target_data = df[target_cols].fillna(method='ffill').fillna(method='bfill')

        # 스케일러 적용
        scaled_features = self._scale_data(feature_data, 'features')
        scaled_targets = self._scale_data(target_data, 'targets')

        return {
            'features': scaled_features,
            'targets': scaled_targets,
            'dates': df['date_month_end'].values,
            'feature_cols': available_features,
            'target_cols': target_cols
        }

    def _scale_data(self, data, data_type):
        """데이터 스케일링"""
        scaler = MinMaxScaler()
        scaled_data = scaler.fit_transform(data)
        self.scalers[data_type] = scaler
        return scaled_data

    def create_sequences(self, data_dict):
        """시퀀스 데이터 생성"""
        features = data_dict['features']
        targets = data_dict['targets']

        X, y = [], []

        for i in range(self.sequence_length, len(features)):
            X.append(features[i - self.sequence_length:i])
            y.append(targets[i])

        return np.array(X), np.array(y)

    def inverse_transform_targets(self, scaled_data):
        """타겟 데이터 역변환"""
        return self.scalers['targets'].inverse_transform(scaled_data)


# ==============================================
# LSTM 모델 클래스
# ==============================================

class LSTMModel:
    def __init__(self, sequence_length=12, n_features=None, n_targets=2):
        self.sequence_length = sequence_length
        self.n_features = n_features
        self.n_targets = n_targets
        self.model = None
        self.history = None

    def build_model(self, dropout_rate=0.2, learning_rate=0.001):
        """LSTM 모델 구축"""
        model = Sequential([
            LSTM(50, return_sequences=True, input_shape=(self.sequence_length, self.n_features)),
            Dropout(dropout_rate),
            LSTM(50, return_sequences=False),
            Dropout(dropout_rate),
            Dense(25, activation='relu'),
            Dense(self.n_targets)
        ])

        optimizer = Adam(learning_rate=learning_rate)
        model.compile(optimizer=optimizer, loss='mse', metrics=['mae'])

        self.model = model
        return model

    def train(self, X_train, y_train, X_val=None, y_val=None, epochs=100, batch_size=32):
        """모델 훈련"""
        callbacks = [
            EarlyStopping(monitor='val_loss' if X_val is not None else 'loss',
                          patience=20, restore_best_weights=True),
            ReduceLROnPlateau(monitor='val_loss' if X_val is not None else 'loss',
                              factor=0.2, patience=10, min_lr=0.0001)
        ]

        validation_data = (X_val, y_val) if X_val is not None and y_val is not None else None

        self.history = self.model.fit(
            X_train, y_train,
            validation_data=validation_data,
            epochs=epochs,
            batch_size=batch_size,
            callbacks=callbacks,
            verbose=0
        )

        return self.history

    def predict(self, X):
        """예측 수행"""
        return self.model.predict(X, verbose=0)

    def predict_future(self, last_sequence, n_periods):
        """미래 값 예측"""
        predictions = []
        current_sequence = last_sequence.copy()

        for _ in range(n_periods):
            # 현재 시퀀스로 다음 값 예측
            pred = self.model.predict(current_sequence.reshape(1, self.sequence_length, -1), verbose=0)
            predictions.append(pred[0])

            # 시퀀스 업데이트 (예측값으로 특성 일부 대체)
            new_row = current_sequence[-1].copy()
            # 타겟 컬럼에 해당하는 특성만 업데이트 (처음 n_targets개)
            new_row[:len(pred[0])] = pred[0]

            # 시퀀스 이동
            current_sequence = np.vstack([current_sequence[1:], new_row])

        return np.array(predictions)


# ==============================================
# 평가 및 시각화 함수들
# ==============================================

def evaluate_model(y_true, y_pred, target_names):
    """모델 성능 평가"""
    results = {}

    for i, target in enumerate(target_names):
        mse = mean_squared_error(y_true[:, i], y_pred[:, i])
        mae = mean_absolute_error(y_true[:, i], y_pred[:, i])
        r2 = r2_score(y_true[:, i], y_pred[:, i])

        results[target] = {
            'MSE': mse,
            'RMSE': np.sqrt(mse),
            'MAE': mae,
            'R2': r2
        }

    return results


def plot_predictions(dates, actual, predicted, target_names, title="LSTM 예측 결과"):
    """예측 결과 시각화"""
    fig, axes = plt.subplots(len(target_names), 1, figsize=(15, 5 * len(target_names)))
    if len(target_names) == 1:
        axes = [axes]

    for i, (ax, target) in enumerate(zip(axes, target_names)):
        ax.plot(dates, actual[:, i], label=f'실제 {target}', marker='o')
        ax.plot(dates, predicted[:, i], label=f'예측 {target}', marker='s')
        ax.set_title(f'{target} 예측 결과')
        ax.set_xlabel('날짜')
        ax.set_ylabel(target)
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

    plt.tight_layout()
    plt.show()


def plot_training_history(history):
    """훈련 히스토리 시각화"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))

    # Loss
    ax1.plot(history.history['loss'], label='Training Loss')
    if 'val_loss' in history.history:
        ax1.plot(history.history['val_loss'], label='Validation Loss')
    ax1.set_title('모델 Loss')
    ax1.set_xlabel('Epoch')
    ax1.set_ylabel('Loss')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # MAE
    ax2.plot(history.history['mae'], label='Training MAE')
    if 'val_mae' in history.history:
        ax2.plot(history.history['val_mae'], label='Validation MAE')
    ax2.set_title('모델 MAE')
    ax2.set_xlabel('Epoch')
    ax2.set_ylabel('MAE')
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ==============================================
# 메인 예측 함수
# ==============================================

def predict_revenue_and_psr(df, sequence_length=12, prediction_periods=12,
                            train_split=0.8, validation_split=0.1):
    """
    매출과 PSR을 예측하는 메인 함수

    Parameters:
    - df: 전처리된 데이터프레임
    - sequence_length: LSTM 시퀀스 길이 (기본: 12개월)
    - prediction_periods: 예측할 기간 수 (기본: 12개월)
    - train_split: 훈련 데이터 비율
    - validation_split: 검증 데이터 비율

    Returns:
    - dict: 예측 결과 및 모델 정보
    """

    # 1. 데이터 전처리
    target_cols = ['revenue_billions', 'PSR_ttm']
    preprocessor = LSTMPreprocessor(sequence_length=sequence_length)

    try:
        data_dict = preprocessor.prepare_data(df, target_cols)
    except ValueError as e:
        return {'error': str(e)}

    # 2. 시퀀스 데이터 생성
    X, y = preprocessor.create_sequences(data_dict)

    if len(X) == 0:
        return {'error': '시퀀스 데이터 생성 실패'}

    # 3. 데이터 분할
    n_samples = len(X)
    train_size = int(n_samples * train_split)
    val_size = int(n_samples * validation_split)

    X_train = X[:train_size]
    y_train = y[:train_size]
    X_val = X[train_size:train_size + val_size] if val_size > 0 else None
    y_val = y[train_size:train_size + val_size] if val_size > 0 else None
    X_test = X[train_size + val_size:]
    y_test = y[train_size + val_size:]

    # 4. 모델 구축 및 훈련
    model = LSTMModel(
        sequence_length=sequence_length,
        n_features=X.shape[2],
        n_targets=len(target_cols)
    )

    model.build_model()
    history = model.train(X_train, y_train, X_val, y_val)

    # 5. 예측 수행
    if len(X_test) > 0:
        y_pred = model.predict(X_test)

        # 역변환
        y_test_orig = preprocessor.inverse_transform_targets(y_test)
        y_pred_orig = preprocessor.inverse_transform_targets(y_pred)

        # 평가
        eval_results = evaluate_model(y_test_orig, y_pred_orig, target_cols)
    else:
        y_test_orig = y_pred_orig = eval_results = None

    # 6. 미래 예측
    last_sequence = X[-1]  # 마지막 시퀀스 사용
    future_predictions_scaled = model.predict_future(last_sequence, prediction_periods)
    future_predictions = preprocessor.inverse_transform_targets(future_predictions_scaled)

    # 7. 미래 날짜 생성
    last_date = pd.to_datetime(data_dict['dates'][-1])
    future_dates = pd.date_range(start=last_date + pd.DateOffset(months=1),
                                 periods=prediction_periods, freq='M')

    # 8. 결과 정리
    results = {
        'model': model,
        'preprocessor': preprocessor,
        'history': history,
        'evaluation': eval_results,
        'future_predictions': future_predictions,
        'future_dates': future_dates,
        'target_columns': target_cols,
        'sequence_length': sequence_length
    }

    if len(X_test) > 0:
        test_dates = data_dict['dates'][sequence_length + train_size + val_size:]
        results.update({
            'test_actual': y_test_orig,
            'test_predicted': y_pred_orig,
            'test_dates': test_dates
        })

    return results


def create_prediction_dataframe(results, original_df):
    """예측 결과를 DataFrame으로 변환"""
    if 'error' in results:
        return pd.DataFrame()

    # 미래 예측 데이터프레임 생성
    future_df = pd.DataFrame({
        'date_month_end': results['future_dates'],
        'revenue_billions': results['future_predictions'][:, 0],
        'PSR_ttm': results['future_predictions'][:, 1],
        'prediction_type': 'LSTM_forecast'
    })

    # 원본 데이터와 결합
    original_subset = original_df[['date_month_end', 'revenue_billions', 'PSR_ttm']].copy()
    original_subset['prediction_type'] = 'actual'

    combined_df = pd.concat([original_subset, future_df], ignore_index=True)
    combined_df = combined_df.sort_values('date_month_end').reset_index(drop=True)

    return combined_df


# ==============================================
# 실행 예시 함수
# ==============================================

# def run_lstm_prediction(df, ticker='UNKNOWN', prediction_months=12, plot_results=True):
#     """
#     LSTM 예측 실행 메인 함수
#
#     Parameters:
#     - df: 전처리된 데이터프레임
#     - ticker: 종목명 (시각화용)
#     - prediction_months: 예측할 개월 수
#     - plot_results: 결과 시각화 여부
#
#     Returns:
#     - tuple: (예측 결과 DataFrame, 모델 결과 딕셔너리)
#     """
#
#     # LSTM 예측 수행
#     results = predict_revenue_and_psr(
#         df,
#         prediction_periods=prediction_months,
#         sequence_length=12
#     )
#
#     if 'error' in results:
#         return pd.DataFrame(), results
#
#     # 예측 결과 DataFrame 생성
#     prediction_df = create_prediction_dataframe(results, df)
#
#     # 시각화
#     if plot_results and len(prediction_df) > 0:
#         # 훈련 히스토리
#         plot_training_history(results['history'])
#
#         # 테스트 세트 예측 결과 (있는 경우)
#         if 'test_actual' in results:
#             plot_predictions(
#                 results['test_dates'],
#                 results['test_actual'],
#                 results['test_predicted'],
#                 results['target_columns'],
#                 f"{ticker} - 테스트 세트 예측 결과"
#             )
#
#         # 전체 데이터 + 미래 예측 시각화
#         plt.figure(figsize=(15, 10))
#
#         # Revenue 예측
#         plt.subplot(2, 1, 1)
#         actual_mask = prediction_df['prediction_type'] == 'actual'
#         forecast_mask = prediction_df['prediction_type'] == 'LSTM_forecast'
#
#         plt.plot(prediction_df[actual_mask]['date_month_end'],
#                  prediction_df[actual_mask]['revenue_billions'],
#                  'o-', label='실제 매출', color='blue')
#         plt.plot(prediction_df[forecast_mask]['date_month_end'],
#                  prediction_df[forecast_mask]['revenue_billions'],
#                  's--', label='LSTM 예측', color='red')
#         plt.title(f'{ticker} - 매출 예측 (Revenue Billions)')
#         plt.ylabel('매출 (Billions)')
#         plt.legend()
#         plt.grid(True, alpha=0.3)
#
#         # PSR 예측
#         plt.subplot(2, 1, 2)
#         plt.plot(prediction_df[actual_mask]['date_month_end'],
#                  prediction_df[actual_mask]['PSR_ttm'],
#                  'o-', label='실제 PSR', color='green')
#         plt.plot(prediction_df[forecast_mask]['date_month_end'],
#                  prediction_df[forecast_mask]['PSR_ttm'],
#                  's--', label='LSTM 예측', color='orange')
#         plt.title(f'{ticker} - PSR TTM 예측')
#         plt.xlabel('날짜')
#         plt.ylabel('PSR TTM')
#         plt.legend()
#         plt.grid(True, alpha=0.3)
#
#         plt.tight_layout()
#         plt.show()
#
#     return prediction_df, results


# ==============================================
# 사용 예시
# ==============================================

"""
# 사용 예시:

# 1. 전처리된 데이터로 예측 수행
prediction_df, model_results = run_lstm_prediction(
    df=preprocessed_data,  # 전처리된 데이터
    ticker='AMAT',
    prediction_months=12,
    plot_results=True
)

# 2. 예측 결과 확인
print("미래 12개월 예측:")
forecast_data = prediction_df[prediction_df['prediction_type'] == 'LSTM_forecast']
print(forecast_data[['date_month_end', 'revenue_billions', 'PSR_ttm']].head())

# 3. 모델 성능 확인 (테스트 데이터가 있는 경우)
if 'evaluation' in model_results and model_results['evaluation']:
    for target, metrics in model_results['evaluation'].items():
        print(f"{target} 성능:")
        for metric, value in metrics.items():
            print(f"  {metric}: {value:.4f}")
"""