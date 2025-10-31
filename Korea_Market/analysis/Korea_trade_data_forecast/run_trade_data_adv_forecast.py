# -*- coding: utf-8 -*-

from trade_data_import import get_trade_data_by_hscode, get_unique_hscode_list
from DATA.stock_invest_function import *
from sarima_forecast_trade import sarima_forecast_trade_value
from multi_model_trade_forecast import forecast_trade_multi_models
from ensemble_trade_forecast import build_ensemble_columns
from save_trade_forecast_long import to_long_format
from save_long_forecast_to_db import save_long_forecast_to_db
import pandas as pd
import gc
from datetime import datetime

# ================================
# 1. 데이터베이스 설정
# ================================
db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}


# ================================
# 2. 데이터 품질 검증 함수
# ================================
def validate_trade_data(df, min_length=60):
    """
    거래 데이터의 품질을 검증합니다.

    Args:
        df: 검증할 데이터프레임
        min_length: 최소 데이터 길이 (기본값: 60)

    Returns:
        bool: 데이터가 유효하면 True, 아니면 False
    """
    if df is None or df.empty:
        return False

    # 길이 검증
    if len(df) < min_length:
        return False

    # 결측치 검증
    if df.isnull().any().any():
        return False

    # 값의 유효성 검증 (음수나 0만 있는 경우 제외)
    if 'expDlr' in df.columns:
        if (df['expDlr'] <= 0).all():
            return False

    return True


# ================================
# 3. HS Code별 예측 실행 함수
# ================================
def process_single_hscode(hs_code, db_info, horizon=24):
    """
    단일 HS Code에 대한 예측을 수행합니다.

    Args:
        hs_code: 처리할 HS Code
        db_info: 데이터베이스 연결 정보
        horizon: 예측 기간 (기본값: 24개월)

    Returns:
        pd.DataFrame: long format 예측 결과 (실패 시 None)
    """
    try:
        # 데이터 가져오기
        trade_df_exp = get_trade_data_by_hscode(db_info, hs_code, 'expDlr')

        # 데이터 품질 검증
        if not validate_trade_data(trade_df_exp):
            print(f"[SKIP] {hs_code}: 데이터 품질 기준 미달 (길이: {len(trade_df_exp) if trade_df_exp is not None else 0})")
            return None

        print(f"[처리중] {hs_code}: 데이터 길이 {len(trade_df_exp)}")

        # SARIMA 예측
        sarima_df = sarima_forecast_trade_value(
            df=trade_df_exp,
            indicator='expDlr',
            horizon=horizon,
            sarima_kwargs={}
        )

        # 다중 모델 예측
        fc_table = forecast_trade_multi_models(
            trade_df=trade_df_exp,
            indicator="expDlr",
            horizon=horizon,
            model_kwargs={}
        )

        # 앙상블 생성
        ensemble_result = build_ensemble_columns(fc_table, indicator="expDlr")

        # Long format 변환
        long_df = to_long_format(
            ensemble_result,
            hs_code=hs_code,
            forecast_date=datetime.now().strftime('%Y-%m-%d')
        )

        # 메모리 정리
        del trade_df_exp, sarima_df, fc_table, ensemble_result
        gc.collect()

        return long_df

    except Exception as e:
        print(f"[오류] {hs_code}: {str(e)}")
        return None


# ================================
# 4. 배치 저장 함수
# ================================
def save_batch_to_db(batch_data, db_info):
    if not batch_data:
        return

    # ✅ 인덱스(=date) 보존: ignore_index=False
    combined_df = pd.concat(batch_data, axis=0)  # <-- 변경
    # 인덱스 이름 보장
    if combined_df.index.name != "date":
        combined_df.index.name = "date"

    save_long_forecast_to_db(long_df=combined_df, db_info=db_info)
    print(f"[저장완료] {len(batch_data)}개 HS Code 예측 결과 저장")


# ================================
# 5. 메인 실행 로직
# ================================
def main():
    # HS Code 리스트 가져오기
    hs_list = get_unique_hscode_list(db_info)
    total_count = len(hs_list)
    print(f"총 {total_count}개의 HS Code 처리 시작")
    print(f"예시: {hs_list[:5]}")

    # 배치 설정 (50개씩 모아서 저장)
    BATCH_SIZE = 50
    batch_data = []
    success_count = 0
    skip_count = 0
    error_count = 0

    # 모든 HS Code 처리
    for idx, hs_code in enumerate(hs_list, 1):
        print(f"\n진행상황: [{idx}/{total_count}] {hs_code}")

        # 예측 수행
        long_df = process_single_hscode(hs_code, db_info, horizon=24)

        if long_df is not None:
            batch_data.append(long_df)
            success_count += 1

            # 배치 크기에 도달하면 저장
            if len(batch_data) >= BATCH_SIZE:
                save_batch_to_db(batch_data, db_info)
                batch_data.clear()
                gc.collect()
        else:
            skip_count += 1

        # 진행상황 출력
        if idx % 10 == 0:
            print(f"\n--- 중간 집계 ---")
            print(f"성공: {success_count}, 스킵: {skip_count}, 오류: {error_count}")

    # 남은 배치 저장
    if batch_data:
        save_batch_to_db(batch_data, db_info)
        batch_data.clear()

    # 최종 결과 출력
    print(f"\n{'=' * 50}")
    print(f"전체 처리 완료!")
    print(f"{'=' * 50}")
    print(f"총 처리: {total_count}개")
    print(f"성공: {success_count}개")
    print(f"스킵: {skip_count}개")
    print(f"오류: {error_count}개")


# ================================
# 6. 실행
# ================================
if __name__ == "__main__":
    main()