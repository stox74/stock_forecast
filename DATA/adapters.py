# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd
import numpy as np

def _try_import(path: str):
    """ 'DATA.mod' 또는 'mod' 양쪽 시도 """
    mod = None
    try:
        mod = __import__(path, fromlist=['*'])
    except Exception:
        try:
            if path.startswith("DATA."):
                mod = __import__(path.replace("DATA.", ""), fromlist=['*'])
        except Exception:
            mod = None
    return mod

def _get_attr(mod, name: str):
    return getattr(mod, name) if (mod is not None and hasattr(mod, name)) else None

def build_fx() -> Dict[str, Any]:
    """
    프로젝트 환경에서 필요한 함수들을 최대한 모아 dict로 반환.
    - 우선순위: DATA.stock_invest_function → DATA.<개별모듈> → 전역 모듈
    - 없으면 명시적 에러 던지는 람다 포함
    """
    fx: Dict[str, Any] = {}

    # 1) 최상위 수집 모듈(있을 수도 있고 없을 수도 있음)
    sif = _try_import("DATA.stock_invest_function") or _try_import("stock_invest_function")

    # 2) 개별 모듈 후보
    sarima = _try_import("DATA.sarima") or _try_import("sarima")
    lstm_v2 = _try_import("DATA.lstm_v2") or _try_import("lstm_v2")
    prophet_v3 = _try_import("DATA.prophet_v3") or _try_import("prophet_v3")
    esmod = _try_import("DATA.esmod") or _try_import("esmod")

    # --- 필수 함수들 바인딩 (sif에 없으면 개별 모듈에서 찾기) ---
    def _missing(name):
        def _raise(*args, **kwargs):
            raise AttributeError(
                f"필요한 함수 '{name}' 를 찾을 수 없습니다. "
                f"DATA.stock_invest_function 또는 개별 모듈에 정의해 주세요."
            )
        return _raise

    fx["fetch_revenue_data"] = _get_attr(sif, "fetch_revenue_data") or _missing("fetch_revenue_data")
    fx["fetch_db_revenue_data"] = _get_attr(sif, "fetch_db_revenue_data") or _missing("fetch_db_revenue_data")
    fx["fetch_market_data_yearly"] = _get_attr(sif, "fetch_market_data_yearly") or _missing("fetch_market_data_yearly")
    fx["process_daily_to_monthly_market_data"] = (
        _get_attr(sif, "process_daily_to_monthly_market_data") or _missing("process_daily_to_monthly_market_data")
    )
    fx["_safe_get_db_market_df"] = _get_attr(sif, "_safe_get_db_market_df") or _missing("_safe_get_db_market_df")

    # 전처리 유틸 (없으면 안전한 대체 제공)
    fx["to_month_end_safe"] = _get_attr(sif, "to_month_end_safe") or (lambda s: pd.to_datetime(s).dt.to_period('M').dt.to_timestamp('M'))
    fx["clean_rev_data"] = _get_attr(sif, "clean_rev_data") or (lambda df: df.rename(columns={"revenue_billions_x":"revenue_billions"}))

    fx["calculate_enhanced_ttm_and_psr"] = _get_attr(sif, "calculate_enhanced_ttm_and_psr") or _missing("calculate_enhanced_ttm_and_psr")
    fx["prepare_revenue_ttm"] = _get_attr(sif, "prepare_revenue_ttm") or _missing("prepare_revenue_ttm")

    # 모델들
    fx["sarima"] = sarima or _missing("sarima")
    fx["lstm_v2"] = lstm_v2 or _missing("lstm_v2")
    fx["prophet_v3"] = prophet_v3 or _missing("prophet_v3")
    fx["esmod"] = esmod or _missing("esmod")

    # 티커 리스트/감사 (없으면 안전한 대체)
    fx["ticker_list"] = _get_attr(sif, "ticker_list") or []
    fx["audit_db_coverage"] = _get_attr(sif, "audit_db_coverage") or (lambda db_info, tickers: ([], []))

    return fx
