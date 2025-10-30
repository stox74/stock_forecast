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
from datetime import date, datetime
import sys, os
from typing import Optional

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

# ── 티커 리스트 ───────────────────────────────────────────────────────────────
from ticker_list import get_ticker_list  # 사용자 제공 코드 이름에 맞게 import

# ── DB 업서트 유틸 ────────────────────────────────────────────────────────────
from upload_valuation_longform import upload_valuation_longform

# ── valuation 계산 모듈 ───────────────────────────────────────────────────────
from valuation_forecast import compute_valuation_forecast

# ── DB 접속 정보 ──────────────────────────────────────────────────────────────
db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# ───────────────────────────────────────────────────────────────────────────────
# 설정: exog 변환 방식
#   - 분기 exog: EXOG_Q_TRANSFORM = "pct" 또는 "diff"
#   - 월간 exog: pct_change(12) 고정
# ───────────────────────────────────────────────────────────────────────────────
EXOG_Q_TRANSFORM = "pct"   # "pct" → pct_change(4), "diff" → diff(4)

# 배치 중 에러 기록 전역 리스트
ERROR_RECORDS = []  # dict(ticker, hs_code, error, traceback)

# ───────────────────────────────────────────────────────────────────────────────
# 유틸
# ───────────────────────────────────────────────────────────────────────────────
def _safe_prophet_forecast(final_combined_data, horizon: int = 5):
    """Prophet 미설치/오류 시 None 반환."""
    try:
        return forecast_revenue_prophet(final_combined_data, horizon=horizon)
    except Exception:
        print("[WARN] Prophet forecast failed or package missing. Skipping Prophet.")
        return None

def _get_exog_from_export(df_export: Optional[dict]) -> Optional[pd.DataFrame]:
    """df_export 사전에서 exog로 사용할 quarterly DataFrame만 추출."""
    if isinstance(df_export, dict) and 'quarterly' in df_export:
        return df_export['quarterly']
    return None

def _transform_exog_col(df: pd.DataFrame, col: str, periods: int, method: str = "pct") -> pd.DataFrame:
    """
    df[col]에 변환 적용 (pct_change or diff), 이후 dropna.
    df는 DatetimeIndex 가정(정렬 수행), 원본은 건드리지 않고 사본 반환.
    """
    if df is None or col not in df.columns:
        return df
    out = df.copy()
    out = out.sort_index()
    if method == "pct":
        out[col] = out[col].pct_change(periods)
    elif method == "diff":
        out[col] = out[col].diff(periods)
    else:
        raise ValueError("method must be 'pct' or 'diff'")
    out = out.dropna()
    return out

# ───────────────────────────────────────────────────────────────────────────────
# 단일 티커 처리
# ───────────────────────────────────────────────────────────────────────────────
def process_single_ticker(
    ticker: str,
    hs_code: Optional[str],
    db_info: dict,
    table_name: str = "Korea_company_valuation_ver2",
    horizon_quarter: int = 5,
    psr_horizon_months: int = 13,
    forecast_date: Optional[str] = None,
    value_start_date: Optional[str] = None,
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

        # 2) 수출 exog (분기) ──────────────────────────────────────────────────
        exog_df = None
        df_export = None
        if hs_code:
            df_export = get_hscode_processed_data(db_info, hs_code=hs_code)
            exog_df = _get_exog_from_export(df_export)
            # ▼ 여기서 exog_df['exog_var']에 yoy 변환 적용 후 dropna
            if isinstance(exog_df, pd.DataFrame) and 'exog_var' in exog_df.columns:
                exog_df = _transform_exog_col(
                    df=exog_df,
                    col='exog_var',
                    periods=4,
                    method=EXOG_Q_TRANSFORM  # "pct" → pct_change(4), "diff" → diff(4)
                )
            locals_to_cleanup.update(df_export=df_export, exog_df=exog_df)

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

        # 분석 대상 기간 컷
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

        # 6) PSR 시계열 및 예측 ────────────────────────────────────────────────
        psr_df = build_psr_series(df_mc=df_mc, df_rev=df_rev)  # index=DatetimeIndex, col='psr'

        # 월간 exog 추출 및 12개월 변화율 적용
        exog_monthly_df = None
        if hs_code and isinstance(df_export, dict):
            try:
                df_exog_monthly = extract_monthly_exog_var(df_export)  # dict→DataFrame
                if isinstance(df_exog_monthly, pd.DataFrame) and 'exog_var' in df_exog_monthly.columns:
                    exog_monthly_df = _transform_exog_col(
                        df=df_exog_monthly[['exog_var']],
                        col='exog_var',
                        periods=12,
                        method="pct"  # 월간은 pct_change(12) 고정
                    )
            except Exception as _e:
                # 월간 exog 실패 시 무시하고 진행
                print(f"[WARN] Monthly exog transform skipped for {ticker}: {_e}")

        fc_table = forecast_psr_all_models(psr_df, horizon=psr_horizon_months, exog_df=exog_monthly_df)
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
        # 에러 기록 추가
        err_msg = str(e)
        tb_str = traceback.format_exc()
        ERROR_RECORDS.append({
            "ticker": ticker,
            "hs_code": hs_code,
            "error": err_msg,
            "traceback": tb_str
        })
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
        gc.collect()

# ───────────────────────────────────────────────────────────────────────────────
# 배치 실행
# ───────────────────────────────────────────────────────────────────────────────
def _save_error_records_to_excel(prefix: str = "error_tickers") -> Optional[str]:
    """ERROR_RECORDS가 있으면 엑셀로 저장하고 파일 경로 반환, 없으면 None."""
    if not ERROR_RECORDS:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{prefix}_{ts}.xlsx"
    try:
        df_err = pd.DataFrame(ERROR_RECORDS)
        # 길이가 긴 traceback 표시를 위해 옵션만 조정
        with pd.ExcelWriter(fname, engine="xlsxwriter") as writer:
            df_err.to_excel(writer, index=False, sheet_name="errors")
        print(f"[INFO] 에러 기록 저장: {fname}")
        return fname
    except Exception as e:
        print(f"[WARN] 에러 기록 저장 실패: {e}")
        return None

def run_batch(
    group: str = "all",
    value_start_date: Optional[str] = None,
    forecast_date: Optional[str] = None,
    table_name: str = "Korea_company_valuation_ver2",
):
    ok, fail = 0, 0
    tickers = get_ticker_list(group=group)
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

    # 배치 종료 후 에러 기록 저장
    _save_error_records_to_excel(prefix=f"error_tickers_{group}")

# ───────────────────────────────────────────────────────────────────────────────
# 실행 예시
# ───────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 예) 테스트 3종만 돌리기, valuation 시작월은 자동(현재달+1), forecast_date는 오늘
    run_batch(group="all", value_start_date=None, forecast_date=None)

    # 전체 실행 예시
    # run_batch(group="all", value_start_date=None, forecast_date=None)
