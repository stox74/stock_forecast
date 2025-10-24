"""
시계열 예측 실행 스크립트
재무제표 데이터를 사용한 예측 실행
"""

import sys
import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from pathlib import Path

# 예측 함수 모듈 임포트
from forecast_functions import (
    add_repo_path,
    infer_freq_alias,
    forecast_one_from_pivot_inline,
    get_memory_usage,
    clear_memory,
    monitor_memory_usage,
    data_miss_list
)

# 한글 폰트 설정
matplotlib.rc('font', family='Malgun Gothic')
plt.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings("ignore")


def main():
    """메인 실행 함수"""

    print("=" * 70)
    print("시계열 예측 시스템 시작")
    print("=" * 70)

    # 초기 메모리 상태 확인
    print(f"\n[초기 메모리] {get_memory_usage():.2f} MB")

    # ==================== 경로 설정 ====================
    try:
        project_root = add_repo_path()
    except FileNotFoundError as e:
        print(f"경로 설정 실패: {e}")
        print("수동으로 경로를 설정하세요.")
        return

    # DATA 모듈 임포트
    try:
        from DATA.stock_invest_function import fetch_table_data
    except ImportError as e:
        print(f"모듈 임포트 실패: {e}")
        print("DATA.stock_invest_function 모듈을 확인하세요.")
        return

    # ==================== 데이터베이스 연결 ====================
    print("\n[1단계] 데이터베이스 연결 중...")

    db_info = {
        'user': 'stox7412',
        'password': 'Apt106503!~',
        'host': '192.168.0.230',
        'port': '3307',
        'database': 'investar'
    }

    try:
        fs_df = fetch_table_data(db_info, "korea_fs_data")
        print(f"? 데이터 로드 완료: {len(fs_df):,}행")
        monitor_memory_usage()
    except Exception as e:
        print(f"? 데이터베이스 연결 실패: {e}")
        return

    # ==================== 데이터 전처리 ====================
    print("\n[2단계] 데이터 전처리 중...")

    # 날짜 컬럼명 변경
    fs_df.rename(columns={'Date': 'date'}, inplace=True)

    # 특정 지표 필터링
    target_indicator = '매출액(천원)'
    filtered_df = fs_df[fs_df['indicator'] == target_indicator].copy()
    print(f"? 지표 필터링 완료: {target_indicator} - {len(filtered_df):,}행")

    # 날짜 정제 및 정렬
    filtered_df['date'] = pd.to_datetime(filtered_df['date'])
    filtered_df.sort_values(by='date', inplace=True)
    filtered_df.rename(columns={'symbol': 'ticker'}, inplace=True)

    # value 컬럼 타입 변환
    if 'value' not in filtered_df.columns:
        print("? 오류: 'value' 컬럼이 없습니다.")
        return

    filtered_df['value'] = pd.to_numeric(filtered_df['value'], errors='coerce')

    # 피벗 테이블 생성
    pivot_df = filtered_df.pivot_table(
        index='date',
        columns='ticker',
        values='value',
        aggfunc='first'
    )

    print(f"? 피벗 테이블 생성 완료: {pivot_df.shape}")

    # 전년 동분기 대비 변화율 계산 (참고용)
    fs_yoy_growth_df = pivot_df.pct_change(periods=4) * 100

    # 날짜 범위 제한
    pivot_df.index.name = 'date'
    pivot_df = pivot_df.loc['2010-03-31': '2025-06-30']
    print(f"? 날짜 범위 제한: {pivot_df.index.min()} ~ {pivot_df.index.max()}")

    # 메모리 정리
    del fs_df, filtered_df
    clear_memory()
    monitor_memory_usage()

    # ==================== 예측 설정 ====================
    print("\n[3단계] 예측 설정")

    # 예측 대상 티커
    target_ticker = "A005930"  # 삼성전자
    print(f"? 예측 대상: {target_ticker}")

    # 예측 기간
    forecast_horizon = 12
    print(f"? 예측 기간: {forecast_horizon}분기")

    # 사용할 모델
    models_to_use = ["SARIMA", "ETS", "Prophet", "LSTM", "Theta"]
    print(f"? 사용 모델: {', '.join(models_to_use)}")

    # ==================== 예측 실행 ====================
    print("\n[4단계] 예측 실행 중...")
    print("-" * 70)

    try:
        result = forecast_one_from_pivot_inline(
            pivot_df=pivot_df,
            target_col=target_ticker,
            horizon=forecast_horizon,
            models=models_to_use,
            strict_no_nan=True
        )

        print("-" * 70)
        print("? 예측 완료!")

    except Exception as e:
        print(f"? 예측 실행 중 오류 발생: {e}")
        return

    # 메모리 정리
    clear_memory()
    monitor_memory_usage()

    # ==================== 결과 출력 ====================
    print("\n[5단계] 예측 결과 요약")
    print("=" * 70)

    for model_name, info in result.items():
        print(f"\n▶ {model_name}")

        if "error" in info:
            print(f"  ? 오류: {info['error']}")
        else:
            # 메타 정보 출력
            if "spec" in info:
                print(f"  · 모델 사양: {info['spec']}")

            # 변환 정보 출력
            if "used_transform" in info:
                print(f"  · 사용 변환: {info['used_transform']}")

            # 예측값 출력 (처음 5개)
            if "forecast" in info and not isinstance(info["forecast"], dict):
                forecast_values = np.array(info["forecast"]).flatten()
                print(f"  · 예측값 (처음 5개): {np.round(forecast_values[:5], 2)}")
                print(f"  · 예측값 범위: {forecast_values.min():.2f} ~ {forecast_values.max():.2f}")
            else:
                print(f"  ? 예측값 없음")

    # ==================== 예측 결과 DataFrame 생성 ====================
    print("\n[6단계] 예측 결과 DataFrame 생성")

    forecasts = {}

    for model_name, info in result.items():
        if "forecast" in info and not isinstance(info["forecast"], dict):
            forecasts[model_name] = np.array(info["forecast"]).flatten()
        else:
            forecasts[model_name] = np.full(forecast_horizon, np.nan)

    # DataFrame 변환
    forecast_df = pd.DataFrame(forecasts)

    # 예측 구간 자동 추정
    last_date = pivot_df.index.max()
    freq_alias = infer_freq_alias(pivot_df.index)

    print(f"? 마지막 날짜: {last_date}")
    print(f"? 주기: {freq_alias}")

    if freq_alias == "M":
        future_index = pd.date_range(
            last_date + pd.offsets.MonthEnd(1),
            periods=forecast_horizon,
            freq="M"
        )
    elif freq_alias == "Q":
        future_index = pd.date_range(
            last_date + pd.offsets.QuarterEnd(1),
            periods=forecast_horizon,
            freq="Q"
        )
    elif freq_alias == "D":
        future_index = pd.date_range(
            last_date + pd.Timedelta(days=1),
            periods=forecast_horizon,
            freq="D"
        )
    else:
        future_index = pd.date_range(
            last_date,
            periods=forecast_horizon,
            freq="M"
        )

    # 인덱스 추가
    forecast_df.index = future_index
    forecast_df.index.name = "forecast_date"

    print(f"? 예측 DataFrame 생성 완료: {forecast_df.shape}")
    print(f"\n예측 기간: {future_index[0]} ~ {future_index[-1]}")

    # ==================== 결과 출력 ====================
    print("\n[7단계] 예측 결과 테이블")
    print("=" * 70)
    print(forecast_df.head(10))

    # 기술통계
    print("\n[예측값 기술통계]")
    print(forecast_df.describe())

    # ==================== 데이터 누락 리스트 ====================
    if data_miss_list:
        print("\n[데이터 누락 정보]")
        print(f"누락된 데이터 수: {len(data_miss_list)}")
        for item in data_miss_list[:5]:  # 처음 5개만 출력
            print(f"  - {item}")

    # ==================== 최종 메모리 상태 ====================
    print("\n" + "=" * 70)
    final_memory = get_memory_usage()
    print(f"[최종 메모리] {final_memory:.2f} MB")
    print("=" * 70)
    print("예측 시스템 종료")
    print("=" * 70)

    return forecast_df


if __name__ == "__main__":
    # 스크립트 실행
    forecast_result = main()

    # 결과를 전역 변수로 저장 (선택적)
    if forecast_result is not None:
        print("\n? 예측 결과가 'forecast_result' 변수에 저장되었습니다.")
        print("  사용 예시: forecast_result.to_csv('예측결과.csv')")
