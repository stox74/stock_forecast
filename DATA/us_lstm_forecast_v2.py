
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LSTM 기반 매출(revenue_billions) 및 PSR_ttm 예측 모듈 (시각화 제거 버전)

요청사항 반영:
1) 모든 시각화 코드 제거
2) 매출은 분기 기준으로 4개만 예측하고, 각 분기 예측값을 그 분기의 3개월에 동일하게 복제하여 월별로 채움
   - 내부 LSTM은 월단위(시퀀스 길이 12개월, 미래 12개월)로 학습/예측
   - 결과 반영 시 매출은 12개월 예측을 4분기 평균(또는 대표값)으로 축약 후 각 분기를 3개월씩 복제
3) PSR_ttm은 월별 그대로 예측
4) 예측값을 원본 df에 컬럼 추가하여 반환
   - revenue_billions_lstm_forecast
   - PSR_lstm_forecast
   과거 구간은 실제값으로 채움, 미래 구간은 예측으로 채움
5) 사용하지 않는 함수/시각화 관련 코드 제거
"""

from __future__ import annotations

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau


# =========================================================
# 유틸
# =========================================================

from pandas.tseries.offsets import MonthEnd

def to_month_end(s: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """날짜를 월말로 정규화."""
    s = pd.to_datetime(s)
    return s + MonthEnd(0)


def ensure_sorted_unique_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['date_month_end'] = to_month_end(df['date_month_end'])
    df = df.sort_values('date_month_end').drop_duplicates(subset=['date_month_end']).reset_index(drop=True)
    return df


# =========================================================
# 전처리
# =========================================================

class LSTMPreprocessor:
    def __init__(self, sequence_length: int = 12):
        self.sequence_length = sequence_length
        self.scalers: dict[str, MinMaxScaler] = {}

    def prepare_data(self, df: pd.DataFrame, target_cols: list[str], feature_cols: list[str] | None = None):
        """LSTM 학습용 데이터 준비."""
        if 'date_month_end' not in df.columns:
            raise ValueError("df에는 'date_month_end' 컬럼이 필요합니다.")

        df = ensure_sorted_unique_dates(df)

        # 타깃 결측 제거(학습 구간)
        df = df.dropna(subset=[c for c in target_cols if c in df.columns])

        if len(df) < self.sequence_length + 1:
            raise ValueError(f"데이터가 부족합니다. 최소 {self.sequence_length + 1}개의 유효한 관측치가 필요합니다.")

        # 피처 자동 확장 (타깃 + 추가 후보)
        if feature_cols is None:
            feature_cols = list(dict.fromkeys(target_cols + [
                'market_cap_billions', 'revenue_ttm_billions', 'expDlr'
            ]))

        # 사용 가능한 피처만
        feature_cols = [c for c in feature_cols if c in df.columns]
        if not feature_cols:
            feature_cols = target_cols[:]  # 최소한 타깃 사용

        # 결측 보간 (앞/뒤 채움)
        feature_data = df[feature_cols].ffill().bfill()
        target_data = df[target_cols].ffill().bfill()

        # 스케일링
        scaled_features = self._fit_transform(feature_data, key='features')
        scaled_targets = self._fit_transform(target_data, key='targets')

        return {
            'features': scaled_features,
            'targets': scaled_targets,
            'dates': df['date_month_end'].to_numpy(),
            'feature_cols': feature_cols,
            'target_cols': target_cols
        }

    def _fit_transform(self, data: pd.DataFrame, key: str) -> np.ndarray:
        scaler = MinMaxScaler()
        arr = scaler.fit_transform(data.values.astype('float32'))
        self.scalers[key] = scaler
        return arr

    def inverse_transform_targets(self, scaled: np.ndarray) -> np.ndarray:
        return self.scalers['targets'].inverse_transform(scaled)

    def create_sequences(self, data_dict: dict) -> tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        features = data_dict['features']
        targets = data_dict['targets']
        L = self.sequence_length
        for i in range(L, len(features)):
            X.append(features[i-L:i])
            y.append(targets[i])
        return np.asarray(X), np.asarray(y)


# =========================================================
# 모델
# =========================================================

class LSTMModel:
    def __init__(self, sequence_length: int, n_features: int, n_targets: int):
        self.sequence_length = sequence_length
        self.n_features = n_features
        self.n_targets = n_targets
        self.model: tf.keras.Model | None = None
        self.history = None

    def build(self, dropout_rate: float = 0.2, learning_rate: float = 1e-3):
        m = Sequential([
            LSTM(50, return_sequences=True, input_shape=(self.sequence_length, self.n_features)),
            Dropout(dropout_rate),
            LSTM(50, return_sequences=False),
            Dropout(dropout_rate),
            Dense(25, activation='relu'),
            Dense(self.n_targets)
        ])
        m.compile(optimizer=Adam(learning_rate=learning_rate), loss='mse', metrics=['mae'])
        self.model = m
        return m

    def train(self, X_train, y_train, X_val=None, y_val=None, epochs: int = 120, batch_size: int = 32):
        callbacks = [
            EarlyStopping(monitor='val_loss' if X_val is not None else 'loss', patience=20, restore_best_weights=True),
            ReduceLROnPlateau(monitor='val_loss' if X_val is not None else 'loss', factor=0.2, patience=10, min_lr=1e-4)
        ]
        self.history = self.model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val) if X_val is not None else None,
            epochs=epochs, batch_size=batch_size, verbose=0, callbacks=callbacks
        )
        return self.history

    def predict(self, X) -> np.ndarray:
        return self.model.predict(X, verbose=0)

    def predict_future(self, last_sequence: np.ndarray, n_periods: int) -> np.ndarray:
        """오토레그레시브 방식으로 미래 n_periods 스텝 예측 (스케일 공간)."""
        preds = []
        cur = last_sequence.copy()
        for _ in range(n_periods):
            yhat = self.model.predict(cur.reshape(1, self.sequence_length, -1), verbose=0)[0]
            preds.append(yhat)
            # 마지막 타임스텝의 피처 일부(타깃 위치)를 예측값으로 치환하여 시퀀스 갱신
            new_row = cur[-1].copy()
            new_row[:len(yhat)] = yhat
            cur = np.vstack([cur[1:], new_row])
        return np.asarray(preds)


# =========================================================
# 평가(선택적)
# =========================================================

def evaluate_model(y_true: np.ndarray, y_pred: np.ndarray, target_names: list[str]) -> dict:
    res = {}
    for i, name in enumerate(target_names):
        mse = mean_squared_error(y_true[:, i], y_pred[:, i])
        mae = mean_absolute_error(y_true[:, i], y_pred[:, i])
        r2 = r2_score(y_true[:, i], y_pred[:, i])
        res[name] = {'MSE': mse, 'RMSE': float(np.sqrt(mse)), 'MAE': mae, 'R2': r2}
    return res


# =========================================================
# 메인 파이프라인
# =========================================================

def predict_revenue_and_psr(df: pd.DataFrame,
                            sequence_length: int = 12,
                            prediction_periods: int = 12,
                            train_split: float = 0.8,
                            validation_split: float = 0.1) -> dict:
    """월 단위(12개월) 미래 예측을 수행하고, 원복 가능한 정보 반환."""
    target_cols = ['revenue_billions', 'PSR_ttm']
    pre = LSTMPreprocessor(sequence_length=sequence_length)

    data = pre.prepare_data(df, target_cols=target_cols)
    X, y = pre.create_sequences(data)
    if len(X) == 0:
        return {'error': '시퀀스 데이터 생성 실패'}

    n = len(X)
    n_train = int(n * train_split)
    n_val = int(n * validation_split)

    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = (X[n_train:n_train+n_val], y[n_train:n_train+n_val]) if n_val > 0 else (None, None)
    X_test, y_test = X[n_train+n_val:], y[n_train+n_val:]

    model = LSTMModel(sequence_length=sequence_length, n_features=X.shape[2], n_targets=len(target_cols))
    model.build()
    model.train(X_train, y_train, X_val, y_val)

    # 테스트 평가(있다면)
    eval_results = None
    if len(X_test) > 0:
        y_pred = model.predict(X_test)
        y_test_orig = pre.inverse_transform_targets(y_test)
        y_pred_orig = pre.inverse_transform_targets(y_pred)
        eval_results = evaluate_model(y_test_orig, y_pred_orig, target_cols)

    # 미래 12개월 예측 (스케일 공간 → 역변환)
    last_seq = X[-1]
    future_scaled = model.predict_future(last_seq, prediction_periods)
    future = pre.inverse_transform_targets(future_scaled)

    # 미래 날짜
    last_date = pd.to_datetime(data['dates'][-1])
    future_dates = pd.date_range(start=last_date + pd.offsets.MonthEnd(1), periods=prediction_periods, freq='M')

    out = {
        'model': model,
        'preprocessor': pre,
        'evaluation': eval_results,
        'future_predictions': future,          # shape: (12, 2) 순서: [revenue, psr]
        'future_dates': future_dates,          # 월말 12개
        'target_columns': target_cols,
        'sequence_length': sequence_length
    }
    return out


def add_forecasts_to_df(original_df: pd.DataFrame, results: dict, prediction_quarters: int = 4) -> pd.DataFrame:
    """원본 df에 예측 결과를 합쳐서 반환.
    - revenue_billions_lstm_forecast: 과거=실제, 미래=분기별 예측을 월별로 복제하여 채움
    - PSR_lstm_forecast: 과거=실제, 미래=월별 예측
    """
    if 'error' in results:
        raise ValueError(results['error'])

    df = ensure_sorted_unique_dates(original_df)

    # 예측 벡터
    future_dates = results['future_dates']
    future_pred = results['future_predictions']   # (12, 2)
    rev_monthly_pred = future_pred[:, 0].copy()
    psr_monthly_pred = future_pred[:, 1].copy()

    # ---------- 매출: 분기 단위 4개로 요약 후 3개월씩 복제 ----------
    # 12개월 → 4분기. 각 분기 대표값(평균 사용). 필요 시 마지막 값으로 바꾸려면 .mean() → .last() 로 조정.
    q_idx = np.arange(len(rev_monthly_pred)) // 3  # 0,0,0,1,1,1,2,2,2,3,3,3
    quarterly_vals = pd.Series(rev_monthly_pred).groupby(q_idx).mean().values  # (4,)
    rev_monthly_from_quarter = np.repeat(quarterly_vals, 3)                    # (12,)

    # ---------- df에 컬럼 준비(과거=실제) ----------
    for col, source in [('revenue_billions_lstm_forecast', 'revenue_billions'),
                        ('PSR_lstm_forecast', 'PSR_ttm')]:
        if source in df.columns:
            df[col] = df[source].copy()
        else:
            # 소스가 없는 경우 NaN으로 생성
            df[col] = np.nan

    # ---------- 미래 구간 병합 ----------
    # 기존 df에 없는 미래 날짜가 있으면 행 추가
    future_frame = pd.DataFrame({'date_month_end': future_dates})
    df = pd.merge(df, future_frame, on='date_month_end', how='outer').sort_values('date_month_end').reset_index(drop=True)

    # 매출 예측(분기 복제), PSR 예측(월별 그대로) 적용
    rev_series = pd.Series(rev_monthly_from_quarter, index=future_dates)
    psr_series = pd.Series(psr_monthly_pred, index=future_dates)

    df.loc[df['date_month_end'].isin(future_dates), 'revenue_billions_lstm_forecast'] = df.loc[df['date_month_end'].isin(future_dates), 'date_month_end'].map(rev_series)
    df.loc[df['date_month_end'].isin(future_dates), 'PSR_lstm_forecast'] = df.loc[df['date_month_end'].isin(future_dates), 'date_month_end'].map(psr_series)

    return df


def run_lstm_prediction(df: pd.DataFrame,
                        ticker: str = 'UNKNOWN',
                        prediction_quarters: int = 4,
                        start_date: str | pd.Timestamp | None = None
                       ) -> tuple[pd.DataFrame, dict]:
    """
    엔드투엔드 실행 함수 (start_date 이후 4분기 예측 가능)

    - start_date 가 df 안에 없으면, 마지막 월 이후부터 start_date 까지 빈 구간을 생성하여 예측 시작
    """

    prediction_months = prediction_quarters * 3
    results = predict_revenue_and_psr(df, sequence_length=12, prediction_periods=prediction_months)
    if 'error' in results:
        return df.copy(), results

    if start_date is not None:
        start_date = pd.to_datetime(start_date) + pd.offsets.MonthEnd(0)

        pre = results['preprocessor']
        data = pre.prepare_data(df, target_cols=['revenue_billions', 'PSR_ttm'])
        X, _ = pre.create_sequences(data)
        dates = pd.to_datetime(data['dates'])

        # --- case A: start_date 데이터 안에 있음 ---
        if start_date in dates:
            pos = list(dates).index(start_date)
            if pos < pre.sequence_length:
                raise ValueError("start_date 이전 데이터가 부족하여 시퀀스를 만들 수 없습니다.")
            last_seq = data['features'][pos-pre.sequence_length:pos]

        # --- case B: start_date 데이터에 없음 (미래 날짜) ---
        else:
            last_available = dates[-1]
            if start_date <= last_available:
                raise ValueError(f"지정한 start_date {start_date} 은 데이터에 없고, 미래도 아닙니다.")
            # 마지막 시퀀스를 그대로 사용
            last_seq = X[-1]

        # 미래 예측
        future_scaled = results['model'].predict_future(last_seq, prediction_months)
        future = pre.inverse_transform_targets(future_scaled)

        future_dates = pd.date_range(start=start_date + pd.offsets.MonthEnd(1),
                                     periods=prediction_months, freq='M')

        results['future_predictions'] = future
        results['future_dates'] = future_dates

    merged_df = add_forecasts_to_df(df, results, prediction_quarters=prediction_quarters)
    return merged_df, results

def predict_revenue_only(df: pd.DataFrame,
                         sequence_length: int = 12,
                         prediction_periods: int = 12,
                         train_split: float = 0.8,
                         validation_split: float = 0.1) -> dict:
    """월 단위 revenue_billions 예측 전용."""
    target_cols = ['revenue_billions']
    pre = LSTMPreprocessor(sequence_length=sequence_length)

    data = pre.prepare_data(df, target_cols=target_cols)
    X, y = pre.create_sequences(data)
    if len(X) == 0:
        return {'error': '시퀀스 데이터 생성 실패'}

    n = len(X)
    n_train = int(n * train_split)
    n_val = int(n * validation_split)

    X_train, y_train = X[:n_train], y[:n_train]
    X_val, y_val = (X[n_train:n_train+n_val], y[n_train:n_train+n_val]) if n_val > 0 else (None, None)
    X_test, y_test = X[n_train+n_val:], y[n_train+n_val:]

    model = LSTMModel(sequence_length=sequence_length, n_features=X.shape[2], n_targets=len(target_cols))
    model.build()
    model.train(X_train, y_train, X_val, y_val)

    # 테스트 평가(있다면)
    eval_results = None
    if len(X_test) > 0:
        y_pred = model.predict(X_test)
        y_test_orig = pre.inverse_transform_targets(y_test)
        y_pred_orig = pre.inverse_transform_targets(y_pred)
        eval_results = evaluate_model(y_test_orig, y_pred_orig, target_cols)

    # 미래 예측
    last_seq = X[-1]
    future_scaled = model.predict_future(last_seq, prediction_periods)
    future = pre.inverse_transform_targets(future_scaled)

    # 미래 날짜
    last_date = pd.to_datetime(data['dates'][-1])
    future_dates = pd.date_range(start=last_date + pd.offsets.MonthEnd(1), periods=prediction_periods, freq='M')

    return {
        'model': model,
        'preprocessor': pre,
        'evaluation': eval_results,
        'future_predictions': future,     # shape: (n_months, 1)
        'future_dates': future_dates,
        'target_columns': target_cols,
        'sequence_length': sequence_length
    }


def add_revenue_forecast_to_df(original_df: pd.DataFrame, results: dict, prediction_quarters: int = 4) -> pd.DataFrame:
    """원본 df에 revenue LSTM 예측 결과 합치기."""
    if 'error' in results:
        raise ValueError(results['error'])

    df = ensure_sorted_unique_dates(original_df)

    # 예측 벡터
    future_dates = results['future_dates']
    rev_monthly_pred = results['future_predictions'][:, 0]  # revenue만 존재

    # ---------- 분기 단위 요약 후 3개월씩 복제 ----------
    q_idx = np.arange(len(rev_monthly_pred)) // 3
    quarterly_vals = pd.Series(rev_monthly_pred).groupby(q_idx).mean().values
    rev_monthly_from_quarter = np.repeat(quarterly_vals, 3)

    # ---------- df에 컬럼 준비 ----------
    if 'revenue_billions' in df.columns:
        df['revenue_billions_lstm_forecast'] = df['revenue_billions'].copy()
    else:
        df['revenue_billions_lstm_forecast'] = np.nan

    # ---------- 미래 구간 병합 ----------
    future_frame = pd.DataFrame({'date_month_end': future_dates})
    df = pd.merge(df, future_frame, on='date_month_end', how='outer').sort_values('date_month_end').reset_index(drop=True)

    rev_series = pd.Series(rev_monthly_from_quarter, index=future_dates)
    df.loc[df['date_month_end'].isin(future_dates), 'revenue_billions_lstm_forecast'] = \
        df.loc[df['date_month_end'].isin(future_dates), 'date_month_end'].map(rev_series)

    return df


def run_lstm_revenue_prediction(df: pd.DataFrame,
                                ticker: str = 'UNKNOWN',
                                prediction_quarters: int = 4) -> tuple[pd.DataFrame, dict]:
    """엔드투엔드 실행 (revenue만 예측)."""
    prediction_months = prediction_quarters * 3
    results = predict_revenue_only(df, sequence_length=12, prediction_periods=prediction_months)
    if 'error' in results:
        return df.copy(), results

    merged_df = add_revenue_forecast_to_df(df, results, prediction_quarters=prediction_quarters)
    return merged_df, results




# =========================================================
# (모듈 사용 예시)
# =========================================================
if __name__ == '__main__':
    # 간단한 스모크 테스트(실사용 시 주석 처리 가능)
    print("us_lstm_forecast.py loaded. Use run_lstm_prediction(df, ticker='AMAT', prediction_quarters=4)")
