"""
Theta 주가 예측 실행 스크립트
"""

import sys
from datetime import datetime
from DATA.us_target_ticker_list_2000 import ticker_list
from stock_price_forecast_theta import (
    process_tickers_batch,
    get_forecast_summary,
    get_latest_forecasts_by_ticker
)


def main():
    """
    메인 실행 함수
    """
    print("=" * 80)
    print("Theta 기반 미국 주식 월말 주가 예측")
    print("=" * 80)
    print(f"실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 설정
    FORECAST_MONTHS = 6  # 예측 개월 수
    BATCH_SIZE = 20  # 배치 크기
    OPTIMIZE = True  # 파라미터 최적화 여부
    INCLUDE_CURRENT_MONTH = True  # 현재월 최신 주가 포함

    # 테스트 모드 (기본값: 10개)
    TEST_MODE = None
    TEST_SIZE = 10

    if TEST_MODE:
        test_tickers = ticker_list[:TEST_SIZE]
        print(f"\n[테스트 모드] 처리 대상: {len(test_tickers)}개 티커")
        print(f"티커 목록: {test_tickers}")
    else:
        test_tickers = ticker_list
        print(f"\n[전체 모드] 처리 대상: {len(test_tickers)}개 티커")
        print(f"샘플 티커: {test_tickers[:10]}")

    # 설정 정보 출력
    print("\n" + "-" * 80)
    print("예측 설정:")
    print("-" * 80)
    print("모델: Theta Method")

    if INCLUDE_CURRENT_MONTH:
        print("현재 진행 중인 월: 최신 주가 포함 (해당 월말로 간주)")
    else:
        print("현재 진행 중인 월: 제외 (완료된 월만 사용)")

    if OPTIMIZE:
        print("파라미터 최적화: 활성화")
        print("  - Theta 계수: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0")
        print("  - Deseasonalize: True, False")
        print("  - Method: Additive, Multiplicative")
        print("  - 선택 기준: AIC 최소화 (근사값)")
    else:
        print("파라미터 최적화: 비활성화 (기본: Theta=2.0, Additive, Deseasonalize)")

    print(f"예측 기간: {FORECAST_MONTHS}개월")
    print(f"배치 크기: {BATCH_SIZE}개")
    print("\n중복 데이터 처리: 같은 날 예측 데이터는 덮어쓰기, 다른 날 예측 데이터는 추가")

    # 기존 예측 데이터 확인
    print("\n" + "-" * 80)
    print("기존 Theta 예측 데이터 확인:")
    print("-" * 80)
    df_existing = get_latest_forecasts_by_ticker()
    if not df_existing.empty:
        print(f"총 {len(df_existing)}개 티커의 Theta 예측 데이터 존재")
        print(f"샘플 (상위 5개):")
        print(df_existing.head().to_string(index=False))
    else:
        print("기존 Theta 예측 데이터 없음")

    # 실행 확인
    confirm = input("\n예측을 시작하시겠습니까? (y/n): ").strip().lower()
    if confirm != 'y':
        print("실행을 취소했습니다.")
        return

    # 예측 시작
    print("\n" + "=" * 80)
    print("Theta 예측 시작...")
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
    print(f"평균 처리 시간: {elapsed_time / len(test_tickers):.2f}초/티커")

    if failed_list:
        print(f"\n실패한 티커 목록 ({len(failed_list)}개):")
        for i in range(0, len(failed_list), 10):
            print("  " + ", ".join(failed_list[i:i + 10]))

    # 저장된 결과 확인
    print("\n" + "=" * 80)
    print("저장된 Theta 예측 결과 샘플 확인")
    print("=" * 80)

    if success > 0:
        # 첫 번째 성공한 티커의 예측 결과 확인
        sample_ticker = [t for t in test_tickers if t not in failed_list][0]
        today = datetime.now().strftime('%Y-%m-%d')
        df_sample = get_forecast_summary(ticker=sample_ticker, forecast_date=today)

        if not df_sample.empty:
            print(f"\n{sample_ticker} Theta 예측 결과 (예측일: {today}):")
            print(df_sample.to_string(index=False))
        else:
            # 가장 최근 예측 결과 조회
            df_sample = get_forecast_summary(ticker=sample_ticker)
            if not df_sample.empty:
                print(f"\n{sample_ticker} 최근 Theta 예측 결과:")
                print(df_sample.head(10).to_string(index=False))

    # 전체 예측 현황
    print("\n" + "=" * 80)
    print("전체 Theta 예측 현황")
    print("=" * 80)
    df_summary = get_latest_forecasts_by_ticker()
    if not df_summary.empty:
        print(f"\n총 {len(df_summary)}개 티커의 Theta 예측 데이터:")
        print(df_summary.head(20).to_string(index=False))

    # 재시도 옵션
    if failed_list and len(failed_list) <= 50:
        retry = input(f"\n실패한 {len(failed_list)}개 티커를 재시도하시겠습니까? (y/n): ").strip().lower()
        if retry == 'y':
            print("\n재시도 중...\n")
            success_retry, fail_retry, failed_retry = process_tickers_batch(
                tickers=failed_list,
                forecast_months=FORECAST_MONTHS,
                batch_size=10,  # 재시도 시 작은 배치
                optimize_params=False,  # 재시도 시 최적화 비활성화
                include_current_month=INCLUDE_CURRENT_MONTH
            )

            print("\n재시도 결과:")
            print(f"성공: {success_retry}개")
            print(f"실패: {fail_retry}개")

    print("\n" + "=" * 80)
    print("모든 작업 완료")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n오류 발생: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)