# -*- coding: utf-8 -*-
"""
Korea valuation batch runner
- 티커 리스트와 HS 코드 설정을 불러와 순차 실행
- revenue/psr 예측 → valuation(PSR×Revenue_TTM) → long form 업로더까지 수행
- 동일 forecast_date 내 (ticker, date, indicator) 중복은 덮어쓰기, forecast_date 다르면 추가
"""

import gc
import traceback
import pandas as pd
from datetime import date
import sys, os
try:
    # Python 3.7+ 에서 지원
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from DATA.stock_invest_function import *
from get_market_cap_by_ticker import get_market_cap_by_ticker
from get_fs_data_by_ticker import extract_quarterly_fs_data
from get_hscode_processed_data import get_hscode_processed_data
from get_monthly_export_data import extract_monthly_exog_var
from get_revenue_export_joined_table import get_revenue_export_joined_table
from sarima_endog_forecast import forecast_endog_with_optional_exog
from sarima_endog_forecast import forecast_endog_fill_tail
from get_forecasted_revenue_df import build_forecast_df_from_out
from revenue_forecast_all_package import *
from get_revenue_ttm_df import get_revenue_ttm_df
from get_psr_from_mc_and_rev import build_psr_series
from psr_forecast_runner import forecast_psr_all_models
from DATA.stock_invest_function import *

# ── 사용자 환경: 필요한 내부 모듈 import 경로 조정 ──────────────────────────────
# from your_project_path import ...
# 예시) sys.path.insert(0, r"C:\Users\...\stock_forecast")

# ── 티커 리스트 모듈 (질문에 제공한 파일) ───────────────────────────────────────
from ticker_list import get_ticker_list  # 사용자 제공 코드 이름에 맞게 import

# ── DB 업서트 유틸 (앞서 드린 업로드 함수) ───────────────────────────────────────
from upload_valuation_longform import upload_valuation_longform

# ── valuation 계산 모듈 (앞서 드린 모듈) ────────────────────────────────────────
from valuation_forecast import compute_valuation_forecast


# ── DB 접속 정보 (질문 내용) ────────────────────────────────────────────────────
# from stock_invest_function import get_db_host  # 사용 중이신 함수
db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# ────────────────────────────────────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────────────────────────────────────
def _safe_prophet_forecast(final_combined_data, horizon: int = 5):
    """Prophet 미설치/오류 시 None 반환."""
    try:
        return forecast_revenue_prophet(final_combined_data, horizon=horizon)
    except Exception:
        print("[WARN] Prophet forecast failed or package missing. Skipping Prophet.")
        return None

def _get_exog_from_export(df_export):
    """df_export 사전에서 exog로 사용할 quarterly DataFrame만 추출."""
    if isinstance(df_export, dict) and 'quarterly' in df_export:
        return df_export['quarterly']
    return None

# ────────────────────────────────────────────────────────────────────────────────
# 단일 티커 처리
# ────────────────────────────────────────────────────────────────────────────────
def process_single_ticker(
    ticker: str,
    hs_code: Optional[str],                 # ← 수정
    db_info: dict,
    table_name: str = "Korea_company_valuation_ver2",
    horizon_quarter: int = 5,
    psr_horizon_months: int = 13,
    forecast_date: Optional[str] = None,    # ← 수정
    value_start_date: Optional[str] = None, # ← 수정
) -> bool:
    """
    1) 재무/수출 데이터 결합 → 2) Revenue 예측(TTM) → 3) PSR 예측 → 4) Valuation 계산 →
    5) Long form 업로드. 예외 발생 시 False, 성공 시 True.
    """
    print("\n" + "="*70)
    print(f"[{ticker}] 시작 (hs_code={hs_code})")
    print("="*70)

    # forecast_date 기본: 오늘
    if forecast_date is None:
        forecast_date = pd.Timestamp.today().date().isoformat()

    # 메모리 관리를 위해 로컬 변수 모아 두었다가 finally에서 정리
    locals_to_cleanup = {}

    try:
        # 1) 기본 데이터
        df_mc = get_market_cap_by_ticker(db_info, ticker)
        df_rev = extract_quarterly_fs_data(
            db_info=db_info,
            table_name="korea_fs_data",
            target_indicator="매출액(천원)",
            ticker=ticker,
        )
        locals_to_cleanup.update(df_mc=df_mc, df_rev=df_rev)

        # 2) 수출 exog
        exog_df = None
        if hs_code:
            df_export = get_hscode_processed_data(db_info, hs_code=hs_code)
            exog_df = _get_exog_from_export(df_export)
            locals_to_cleanup.update(df_export=df_export)

        # 3) 결합 테이블
        combined_df, final_combined_data, forecast_df = get_revenue_export_joined_table(
            df_rev=df_rev,
            df_exog=exog_df,
            join_how="outer",
            fill_exog="ffill",
        )
        locals_to_cleanup.update(
            combined_df=combined_df,
            final_combined_data=final_combined_data,
            forecast_df=forecast_df,
        )

        final_combined_data = final_combined_data.loc['2015-01-01':]

        # 4) Revenue forecasts
        out_noexog = forecast_endog_with_optional_exog(
            combined_df=final_combined_data, horizon=horizon_quarter, hs_code=None
        )
        rev_forecast_with_noexog = out_noexog['forecast']
        rev_sarima_noexog = build_forecast_df_from_out(out_noexog, combined_df=final_combined_data)

        if hs_code:
            out_exog = forecast_endog_fill_tail(final_combined_data, hs_code=hs_code)
            rev_forecast_with_exog = out_exog['forecast']
            rev_sarima_exog = build_forecast_df_from_out(out_exog, combined_df=final_combined_data)
        else:
            rev_forecast_with_exog = None
            rev_sarima_exog = None

        rev_ets_df   = forecast_revenue_ets(final_combined_data, horizon=horizon_quarter)
        rev_theta_df = forecast_revenue_theta(final_combined_data, horizon=horizon_quarter)
        rev_lstm_df  = forecast_revenue_lstm(final_combined_data, horizon=horizon_quarter, lookback=12)
        rev_prophet_df = _safe_prophet_forecast(final_combined_data, horizon=horizon_quarter)

        locals_to_cleanup.update(
            out_noexog=out_noexog,
            rev_forecast_with_noexog=rev_forecast_with_noexog,
            rev_forecast_with_exog=rev_forecast_with_exog,
            rev_sarima_noexog=rev_sarima_noexog,
            rev_sarima_exog=rev_sarima_exog,
            rev_ets_df=rev_ets_df,
            rev_theta_df=rev_theta_df,
            rev_lstm_df=rev_lstm_df,
            rev_prophet_df=rev_prophet_df,
        )

        # 5) Revenue TTM 집계
        rev_final = get_revenue_ttm_df(
            df_rev=df_rev,
            rev_sarima_noexog=rev_sarima_noexog,
            rev_sarima_exog=rev_sarima_exog,
            rev_ets_df=rev_ets_df,
            rev_prophet_df=rev_prophet_df,
            rev_theta_df=rev_theta_df,
            rev_lstm_df=rev_lstm_df
        )
        locals_to_cleanup.update(rev_final=rev_final)

        # 6) PSR 시계열 및 예측
        psr_df = build_psr_series(df_mc=df_mc, df_rev=df_rev)  # index=DatetimeIndex, col='psr'
        # psr 예측에서 exog가 필요하면 final_combined_data의 exog_var 사용
        exog_for_psr = None
        if 'exog_var' in final_combined_data.columns:
            df_exog_monthly = extract_monthly_exog_var(df_export)
            exog_monthly_df = df_exog_monthly[['exog_var']]

        fc_table = forecast_psr_all_models(psr_df, horizon=psr_horizon_months, exog_df = exog_monthly_df)
        locals_to_cleanup.update(psr_df=psr_df, fc_table=fc_table)

        # 7) Valuation 계산 (PSR×Revenue_TTM)
        valuation_forecast_result = compute_valuation_forecast(
            fc_table=fc_table,
            rev_final=rev_final,
            value_start_date=value_start_date  # None이면 모듈 내에서 현재달+1 자동
        )
        locals_to_cleanup.update(valuation_forecast_result=valuation_forecast_result)

        # 8) DB 업로드 (long form 업서트)
        upload_valuation_longform(
            valuation_forecast_result=valuation_forecast_result,
            fc_table=fc_table,
            rev_final=rev_final,
            psr_df=psr_df,
            ticker=ticker,
            db_info=db_info,
            table_name=table_name,
            forecast_date=forecast_date,
        )

        print(f"[OK] 완료: {ticker}")
        return True

    except Exception as e:
        print(f"[ERROR] {ticker}: {e}")
        traceback.print_exc()
        return False

    finally:
        # ── 메모리 정리 ─────────────────────────────────────────────────────────
        for k in list(locals_to_cleanup.keys()):
            try:
                del locals_to_cleanup[k]
            except Exception:
                pass
        # 판다스 캐시/가비지 수집
        gc.collect()


# ────────────────────────────────────────────────────────────────────────────────
# 배치 실행
# ────────────────────────────────────────────────────────────────────────────────
def run_batch(
    group: str = "all",
    value_start_date: Optional[str] = None,  # ← 수정
    forecast_date: Optional[str] = None,     # ← 수정
    table_name: str = "Korea_company_valuation_ver2",
):
    tickers = get_ticker_list(group=group)
    ok, fail = 0, 0
    for item in tickers:
        ticker = item.get('ticker')
        hs_code = item.get('hs_code')
        res = process_single_ticker(
            ticker=ticker,
            hs_code=hs_code,
            db_info=db_info,
            table_name=table_name,
            horizon_quarter=5,
            psr_horizon_months=13,
            forecast_date=forecast_date,
            value_start_date=value_start_date,
        )
        ok += int(res)
        fail += int(not res)
    print("\n" + "-"*60)
    print(f"Batch done. success={ok}, fail={fail}, group='{group}'")
    print("-"*60)


# ────────────────────────────────────────────────────────────────────────────────
# 실행 예시
# ────────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 예) 테스트 3종만 돌리기, valuation 시작월은 자동(현재달+1), forecast_date는 오늘
    run_batch(group="all", value_start_date=None, forecast_date=None)

    # 전체 실행 예시
    # run_batch(group="all", value_start_date=None, forecast_date=None)
