"""
LSTM 주가 예측 실행 스크립트 (GPU 가속)
"""

import sys
from datetime import datetime
from DATA.us_target_ticker_list_2000 import ticker_list
from stock_price_forecast_lstm import (
    process_tickers_batch,
    get_forecast_summary,
    get_latest_forecasts_by_ticker
)


def main():
    """
    메인 실행 함수
    """
    print("=" * 80)
    print("LSTM 기반 미국 주식 월말 주가 예측 (GPU 가속)")
    print("=" * 80)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # GPU 상태 확인
    import tensorflow as tf
    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        print(f"\nGPU 감지: {len(gpus)}개")
        for i, gpu in enumerate(gpus):
            print(f"  GPU {i}: {gpu.name}")
    else:
        print("\n경고: GPU가 감지되지 않았습니다. CPU로 실행됩니다.")
        print("GPU 사용을 원하시면 TensorFlow GPU 설정을 확인하세요.")

    # ========================================================================
    # 설정 (여기를 수정하세요)
    # ========================================================================
    FORECAST_MONTHS = 6  # 예측 개월 수
    BATCH_SIZE = 10  # 배치 크기 (GPU 메모리 고려)
    OPTIMIZE = True  # 파라미터 최적화 여부
    INCLUDE_CURRENT_MONTH = True  # 현재월 최신 주가 포함

    # 테스트 모드 설정
    # TEST_MODE = None   : 대화형 모드 (실행 시 선택)
    # TEST_MODE = True   : 자동 테스트 모드 (TEST_SIZE 개수만큼 실행)
    # TEST_MODE = False  : 전체 모드 (전체 티커 실행)
    TEST_MODE = None
    TEST_SIZE = 10  # 테스트 모드 시 티커 개수 (기본값: 10)
    # ========================================================================

    if TEST_MODE:
        test_tickers = ticker_list[:TEST_SIZE]
        mode_name = f"테스트 모드 - {TEST_SIZE}개"
        print(f"\n[{mode_name}] 처리 대상: {len(test_tickers)}개 티커")
        print(f"티커 목록: {test_tickers}")
    elif TEST_MODE is False:
        # 전체 모드
        test_tickers = ticker_list
        mode_name = "전체 모드 - 2000개"
        print(f"\n[{mode_name}] 처리 대상: {len(test_tickers)}개 티커")
        print(f"샘플 티커: {test_tickers[:10]}")
    else:
        # 대화형 모드 선택 (TEST_MODE = None)
        print("\n실행 모드 선택:")
        print("1. 테스트 모드 (상위 5개 티커) - 빠른 테스트")
        print("2. 테스트 모드 (상위 10개 티커)")
        print("3. 소규모 (상위 20개 티커)")
        print("4. 중간 규모 (상위 50개 티커)")
        print("5. 대규모 (상위 100개 티커)")
        print("6. 전체 실행 (2000개 티커) - 장시간 소요")

        mode = input("선택 (1-6): ").strip()

        mode_config = {
            '1': (5, "테스트 모드 - 5개"),
            '2': (10, "테스트 모드 - 10개"),
            '3': (20, "소규모 - 20개"),
            '4': (50, "중간 규모 - 50개"),
            '5': (100, "대규모 - 100개"),
            '6': (2000, "전체 모드 - 2000개")
        }

        if mode not in mode_config:
            print("잘못된 선택입니다. 종료합니다.")
            return

        num_tickers, mode_name = mode_config[mode]
        test_tickers = ticker_list[:num_tickers]

        print(f"\n[{mode_name}] 처리 대상: {len(test_tickers)}개 티커")
        print(f"샘플 티커: {test_tickers[:min(10, len(test_tickers))]}")

    # 최적화 설정 확인 (대화형 모드이고 티커가 많은 경우)
    if TEST_MODE is None and len(test_tickers) > 50 and OPTIMIZE:
        print("\n주의: 티커가 많고 최적화가 활성화되어 있습니다.")
        optimize_confirm = input("최적화를 유지하시겠습니까? (y/n, 기본값: n): ").strip().lower()
        if optimize_confirm != 'y':
            OPTIMIZE = False
            print("최적화 비활성화 - 기본 파라미터 사용")

    # 설정 정보 출력
    print("\n" + "-" * 80)
    print("예측 설정:")
    print("-" * 80)
    print(f"모델: LSTM (Long Short-Term Memory)")
    print(f"GPU 가속: {'활성화' if gpus else '비활성화 (CPU 사용)'}")

    if INCLUDE_CURRENT_MONTH:
        print("현재 진행 중인 월: 최신 주가 포함 (해당 월말로 간주)")
    else:
        print("현재 진행 중인 월: 제외 (완료된 월만 사용)")

    if OPTIMIZE:
        print("파라미터 최적화: 활성화 (각 티커마다 최적 파라미터 탐색)")
        print("  - 시퀀스 길이: 12, 24, 36개월")
        print("  - LSTM 유닛: [64], [128,64], [128,64,32]")
        print("  - 드롭아웃: 0.2")
        print("  - 학습률: 0.001, 0.0005")
        print("  - 최대 시도: 6회")
    else:
        print("파라미터 최적화: 비활성화 (기본 파라미터 사용)")
        print("  - 시퀀스 길이: 24개월")
        print("  - LSTM 유닛: [128, 64]")
        print("  - 드롭아웃: 0.2")
        print("  - 학습률: 0.001")

    print(f"예측 기간: {FORECAST_MONTHS}개월")
    print(f"배치 크기: {BATCH_SIZE}개")
    print("\n중복 데이터 처리: 같은 날 예측 데이터는 덮어쓰기, 다른 날 예측 데이터는 추가")

    # 예상 소요 시간 안내
    print("\n" + "-" * 80)
    print("예상 소요 시간:")
    print("-" * 80)
    if OPTIMIZE:
        time_per_ticker = 30 if gpus else 60  # 초
    else:
        time_per_ticker = 10 if gpus else 20  # 초

    estimated_time = (len(test_tickers) * time_per_ticker) / 60
    print(f"약 {estimated_time:.0f}분 소요 예상")
    print(f"(티커당 평균 {time_per_ticker}초 기준)")

    # 기존 예측 데이터 확인
    print("\n" + "-" * 80)
    print("기존 예측 데이터 확인:")
    print("-" * 80)
    df_existing = get_latest_forecasts_by_ticker()
    if not df_existing.empty:
        print(f"총 {len(df_existing)}개 티커의 예측 데이터 존재")
        print(f"샘플 (상위 5개):")
        print(df_existing.head().to_string(index=False))
    else:
        print("기존 예측 데이터 없음")

    # 실행 확인 (대화형 모드인 경우만)
    if TEST_MODE is None:
        confirm = input("\n예측을 시작하시겠습니까? (y/n): ").strip().lower()
        if confirm != 'y':
            print("실행을 취소했습니다.")
            return
    else:
        print(f"\n자동 실행 모드: {mode_name}")

    # 예측 시작
    print("\n" + "=" * 80)
    print("예측 시작...")
    print("=" * 80)

    start_time = datetime.now()

    success, fail, failed_list = process_tickers_batch(
        tickers=test_tickers,
        forecast_months=FORECAST_MONTHS,
        batch_size=BATCH_SIZE,
        optimize_params=OPTIMIZE,
        include_current_month=INCLUDE_CURRENT_MONTH
    )

    end_time = datetime.now()
    elapsed_time = (end_time - start_time).total_seconds()

    # 결과 요약
    print("\n" + "=" * 80)
    print("예측 완료")
    print("=" * 80)
    print(f"총 처리 시간: {elapsed_time:.1f}초 ({elapsed_time / 60:.1f}분)")
    print(f"성공: {success}개 티커")
    print(f"실패: {fail}개 티커")

    if success > 0:
        print(f"평균 처리 시간: {elapsed_time / success:.2f}초/티커")

    if failed_list:
        print(f"\n실패한 티커 목록 ({len(failed_list)}개):")
        for i in range(0, len(failed_list), 10):
            print("  " + ", ".join(failed_list[i:i + 10]))

    # 저장된 결과 확인
    print("\n" + "=" * 80)
    print("저장된 예측 결과 샘플 확인")
    print("=" * 80)

    if success > 0:
        # 첫 번째 성공한 티커의 예측 결과 확인
        sample_ticker = [t for t in test_tickers if t not in failed_list][0]
        today = datetime.now().strftime('%Y-%m-%d')
        df_sample = get_forecast_summary(ticker=sample_ticker, forecast_date=today)

        if not df_sample.empty:
            print(f"\n{sample_ticker} 예측 결과 (예측일: {today}):")
            print(df_sample[['date', 'ticker', 'item', 'value']].to_string(index=False))

            # item에서 파라미터 정보 추출
            if not df_sample.empty:
                item_name = df_sample['item'].iloc[0]
                print(f"\n사용된 모델: {item_name}")
        else:
            # 가장 최근 예측 결과 조회
            df_sample = get_forecast_summary(ticker=sample_ticker)
            if not df_sample.empty:
                print(f"\n{sample_ticker} 최근 예측 결과:")
                print(df_sample.head(10)[['date', 'ticker', 'item', 'value']].to_string(index=False))

    # 전체 예측 현황
    print("\n" + "=" * 80)
    print("전체 예측 현황")
    print("=" * 80)
    df_summary = get_latest_forecasts_by_ticker()
    if not df_summary.empty:
        print(f"\n총 {len(df_summary)}개 티커의 예측 데이터:")
        print(df_summary.head(20).to_string(index=False))

    # 재시도 옵션
    if failed_list and len(failed_list) <= 20:
        retry = input(f"\n실패한 {len(failed_list)}개 티커를 재시도하시겠습니까? (y/n): ").strip().lower()
        if retry == 'y':
            print("\n재시도 중...\n")
            success_retry, fail_retry, failed_retry = process_tickers_batch(
                tickers=failed_list,
                forecast_months=FORECAST_MONTHS,
                batch_size=5,  # 재시도 시 작은 배치
                optimize_params=False,  # 재시도 시 최적화 비활성화
                include_current_month=INCLUDE_CURRENT_MONTH
            )

            print("\n재시도 결과:")
            print(f"성공: {success_retry}개")
            print(f"실패: {fail_retry}개")

    # GPU 메모리 정리
    if gpus:
        import tensorflow as tf
        tf.keras.backend.clear_session()
        print("\nGPU 메모리 정리 완료")

    print("\n" + "=" * 80)
    print("모든 작업 완료")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        print("GPU 메모리 정리 중...")

        import tensorflow as tf

        tf.keras.backend.clear_session()

        sys.exit(0)
    except Exception as e:
        print(f"\n오류 발생: {str(e)}")
        import traceback

        traceback.print_exc()

        # GPU 메모리 정리
        try:
            import tensorflow as tf

            tf.keras.backend.clear_session()
        except:
            pass

        sys.exit(1)