"""SARIMA 기반 월 매출 예측.

- 매출은 로그 변환 후 SARIMA(1,1,1)(1,1,1,12) 적합 (config에서 조정 가능)
- 예측 결과는 basis(예측에 사용한 마지막 실적 연월)와 함께 forecast 테이블에 저장
  → 매월 예측을 다시 돌려도 과거 예측 이력이 보존되어 실적과 비교 가능
"""
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from config import (COMPANIES, MIN_OBS_FOR_FORECAST, SARIMA_ORDER,
                    SARIMA_SEASONAL_ORDER)
from db import load_revenue_series, upsert_forecast


def _series_to_frame(rows):
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df = df.set_index("date").asfreq("MS")  # 결측월은 NaN으로
    return df


def forecast_company(conn, company_id: str, horizon: int = 6):
    rows = load_revenue_series(conn, company_id)
    if len(rows) < MIN_OBS_FOR_FORECAST:
        print(f"[forecast] {company_id}: 관측치 {len(rows)}개 "
              f"(최소 {MIN_OBS_FOR_FORECAST}개 필요) → 건너뜀")
        return None

    df = _series_to_frame(rows)
    y = np.log(df["revenue"].astype(float))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            y,
            order=SARIMA_ORDER,
            seasonal_order=SARIMA_SEASONAL_ORDER,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        result = model.fit(disp=False)

    fc = result.get_forecast(steps=horizon)
    mean = np.exp(fc.predicted_mean)
    ci = np.exp(fc.conf_int(alpha=0.05))

    basis_year, basis_month = int(rows[-1][0]), int(rows[-1][1])
    model_tag = f"SARIMA{SARIMA_ORDER}x{SARIMA_SEASONAL_ORDER}-log"

    out = []
    for ts, pred in mean.items():
        lo, hi = ci.loc[ts].iloc[0], ci.loc[ts].iloc[1]
        out.append({
            "company_id": company_id,
            "basis_year": basis_year,
            "basis_month": basis_month,
            "target_year": int(ts.year),
            "target_month": int(ts.month),
            "predicted": float(pred),
            "lower_95": float(lo),
            "upper_95": float(hi),
            "model": model_tag,
        })
    upsert_forecast(conn, out)
    name = COMPANIES.get(company_id, company_id)
    print(f"[forecast] {name}({company_id}): basis={basis_year}-{basis_month:02d}, "
          f"{horizon}개월 예측 저장")
    return out


def forecast_all(conn, horizon: int = 6, companies=None):
    for cid in (companies or COMPANIES.keys()):
        try:
            forecast_company(conn, cid, horizon)
        except Exception as e:  # 한 종목 실패가 전체를 막지 않도록
            print(f"[forecast] {cid} 실패: {e}")
