# -*- coding: utf-8 -*-
"""
predictor.py — 대만 매출(실적+예측치)을 이용한 한국/미국 기업 매출 예측 (Python 3.9)

아이디어
--------
대만 월별 매출은 익월 10일경 발표되어 한국/미국 분기 실적보다 훨씬 빠르다.
따라서 상관계수가 높은 대만 기업들의 '분기 YoY growth'(실적 + tw_revenue 예측치)를
설명변수 X 로, 목표 기업(한국/미국)의 분기 YoY growth 를 y 로 하는
회귀/머신러닝 모델을 학습하고, 미래 1~2분기의 y 를 예측한다.
예측 YoY 는 전년 동분기 실제 매출과 곱해 매출 '금액'으로도 환산한다.

사용 흐름
---------
1) top_correlated() 로 후보 확인 → 업종이 다른 기업 제외한 대만 ticker 리스트 확정
2) predict_revenue(target_series, tw_yoy_ext, tw_tickers, ...) 호출
"""
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

# sklearn 이 없으면 numpy OLS 로 폴백
try:
    from sklearn.linear_model import LinearRegression, Ridge, Lasso
    from sklearn.ensemble import RandomForestRegressor
    _HAS_SK = True
except ImportError:
    _HAS_SK = False


def _make_model(model: str):
    if not _HAS_SK:
        return None
    model = model.lower()
    if model == "ols":
        return LinearRegression()
    if model == "ridge":
        return Ridge(alpha=1.0)
    if model == "lasso":
        return Lasso(alpha=0.1, max_iter=10000)
    if model in ("rf", "randomforest"):
        return RandomForestRegressor(n_estimators=300, min_samples_leaf=2,
                                     random_state=42)
    raise ValueError(f"지원하지 않는 모델: {model} (ols/ridge/lasso/rf)")


def _ols_numpy(X: np.ndarray, y: np.ndarray):
    """sklearn 미설치 환경 폴백: 절편 포함 최소자승."""
    Xb = np.column_stack([np.ones(len(X)), X])
    coef, *_ = np.linalg.lstsq(Xb, y, rcond=None)
    return coef


def build_xy(target_yoy: pd.Series,
             tw_yoy_ext: pd.DataFrame,
             tw_tickers: Iterable[str],
             lag: int = 0):
    """
    학습 데이터 구성.
    lag=0 : 동일 분기 대응 (대만 t분기 YoY → 목표 t분기 YoY)
    lag=1 : 대만이 1분기 선행     (대만 t-1분기 YoY → 목표 t분기 YoY)
    """
    tw_sel = tw_yoy_ext[list(tw_tickers)].shift(lag)
    df = pd.concat([tw_sel, target_yoy.rename("_y_")], axis=1)
    train = df.dropna()
    X_all = df[list(tw_tickers)]
    return train[list(tw_tickers)], train["_y_"], X_all


def predict_revenue(target_q_rev: pd.Series,
                    tw_yoy_ext: pd.DataFrame,
                    tw_tickers: Iterable[str],
                    tw_is_forecast: Optional[pd.Series] = None,
                    horizon: int = 2,
                    model: str = "ridge",
                    lag: int = 0,
                    min_train: int = 12) -> pd.DataFrame:
    """
    ★ 요구사항: 지정한 한국/미국 기업의 매출을 대만 매출(실적+예측)로 1~2분기 예측.

    Parameters
    ----------
    target_q_rev  : 목표 기업의 분기 매출 Series (index=PeriodIndex('Q'))
    tw_yoy_ext    : tw_data.tw_quarterly_extended() 의 분기 YoY (예측치 포함)
    tw_tickers    : 사용자가 선별한 대만 종목코드 리스트 (상관계수·업종 검토 후)
    tw_is_forecast: 각 분기가 대만 예측치를 포함하는지 표시 (결과 표기용)
    horizon       : 예측 분기 수 (1 또는 2)
    model         : 'ols' | 'ridge' | 'lasso' | 'rf'
    lag           : 0=동분기, 1=대만 1분기 선행 매핑.
                    ★ 미국 기업 예측 시 lag=1 권장 — 비표준 회계분기와
                    발표 후행성(Q-1)을 함께 흡수하며, 첫 예측 분기의 X가
                    대만 '실적'만으로 구성되어 예측치 오차 전파도 줄어든다.
    min_train     : 최소 학습 표본(분기) 수

    Returns
    -------
    DataFrame [quarter, pred_yoy_pct, base_rev(전년동기 실적), pred_revenue,
               uses_tw_forecast, model, n_train, train_r2]
    """
    tw_tickers = list(tw_tickers)
    missing = [t for t in tw_tickers if t not in tw_yoy_ext.columns]
    if missing:
        raise KeyError(f"대만 YoY 데이터에 없는 종목: {missing}")

    target_q_rev = target_q_rev.dropna().sort_index()
    target_yoy = target_q_rev.pct_change(4) * 100.0

    X_tr, y_tr, X_all = build_xy(target_yoy, tw_yoy_ext, tw_tickers, lag=lag)
    if len(X_tr) < min_train:
        raise ValueError(
            f"학습 표본 부족: {len(X_tr)}분기 (최소 {min_train}). "
            "min_train 을 낮추거나 기간을 늘리세요.")

    # ----- 학습 -----
    mdl = _make_model(model)
    if mdl is not None:
        mdl.fit(X_tr.values, y_tr.values)
        yhat_tr = mdl.predict(X_tr.values)
    else:
        coef = _ols_numpy(X_tr.values, y_tr.values)
        yhat_tr = coef[0] + X_tr.values @ coef[1:]
        model = "ols(numpy)"
    ss_res = float(np.sum((y_tr.values - yhat_tr) ** 2))
    ss_tot = float(np.sum((y_tr.values - y_tr.values.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan

    # ----- 예측 대상 분기: 목표 기업 마지막 실적 이후, 대만 X가 존재하는 분기 -----
    last_q = target_q_rev.index.max()
    future_q = [q for q in X_all.index if q > last_q][:horizon]

    rows = []
    for q in future_q:
        x = X_all.loc[q]
        if x.isna().any():
            continue  # 대만 데이터(예측 포함)로도 채워지지 않은 분기
        if mdl is not None:
            yoy_pred = float(mdl.predict(x.values.reshape(1, -1))[0])
        else:
            yoy_pred = float(coef[0] + x.values @ coef[1:])
        base_q = q - 4
        base = target_q_rev.get(base_q, np.nan)
        rev_pred = base * (1 + yoy_pred / 100.0) if pd.notna(base) else np.nan
        uses_fc = bool(tw_is_forecast.get(q, True)) if tw_is_forecast is not None else None
        rows.append({"quarter": str(q), "pred_yoy_pct": round(yoy_pred, 2),
                     "base_rev": base, "pred_revenue": rev_pred,
                     "uses_tw_forecast": uses_fc,
                     "model": model, "n_train": len(X_tr),
                     "train_r2": round(r2, 3)})
    return pd.DataFrame(rows)


def backtest(target_q_rev: pd.Series,
             tw_yoy_ext: pd.DataFrame,
             tw_tickers: Iterable[str],
             model: str = "ridge",
             lag: int = 0,
             n_test: int = 6,
             min_train: int = 12) -> pd.DataFrame:
    """
    확장 윈도우 1-step 백테스트: 최근 n_test 분기를 하나씩 예측해 정확도 확인.
    반환: [quarter, actual_yoy, pred_yoy, abs_err(yoy %p), actual_rev, pred_rev, ape_pct]
    """
    tw_tickers = list(tw_tickers)
    target_q_rev = target_q_rev.dropna().sort_index()
    target_yoy = (target_q_rev.pct_change(4) * 100.0).dropna()

    tw_sel = tw_yoy_ext[tw_tickers].shift(lag)
    df = pd.concat([tw_sel, target_yoy.rename("_y_")], axis=1).dropna()
    if len(df) < min_train + 1:
        raise ValueError("백테스트에 필요한 표본이 부족합니다.")

    n_test = min(n_test, len(df) - min_train)
    rows = []
    for i in range(len(df) - n_test, len(df)):
        train, test = df.iloc[:i], df.iloc[i]
        mdl = _make_model(model)
        Xtr, ytr = train[tw_tickers].values, train["_y_"].values
        xte = test[tw_tickers].values.reshape(1, -1)
        if mdl is not None:
            mdl.fit(Xtr, ytr)
            pred = float(mdl.predict(xte)[0])
        else:
            coef = _ols_numpy(Xtr, ytr)
            pred = float(coef[0] + xte[0] @ coef[1:])
        q = df.index[i]
        actual = float(test["_y_"])
        base = target_q_rev.get(q - 4, np.nan)
        a_rev = target_q_rev.get(q, np.nan)
        p_rev = base * (1 + pred / 100.0) if pd.notna(base) else np.nan
        ape = (abs(p_rev - a_rev) / abs(a_rev) * 100.0
               if pd.notna(p_rev) and pd.notna(a_rev) and a_rev != 0 else np.nan)
        rows.append({"quarter": str(q), "actual_yoy": round(actual, 2),
                     "pred_yoy": round(pred, 2),
                     "abs_err_yoy_pp": round(abs(actual - pred), 2),
                     "actual_rev": a_rev, "pred_rev": p_rev,
                     "ape_pct": round(ape, 2) if pd.notna(ape) else np.nan})
    out = pd.DataFrame(rows)
    if not out.empty:
        print(f"[백테스트 {model}] YoY MAE = {out['abs_err_yoy_pp'].mean():.2f}%p, "
              f"매출 MAPE = {out['ape_pct'].mean():.2f}%")
    return out
