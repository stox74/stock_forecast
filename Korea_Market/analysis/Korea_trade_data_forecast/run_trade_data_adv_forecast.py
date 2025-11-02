# -*- coding: utf-8 -*-

from trade_data_import import get_trade_data_by_hscode, get_unique_hscode_list
from DATA.stock_invest_function import *
from sarima_forecast_trade import sarima_forecast_trade_value
from multi_model_trade_forecast import forecast_trade_multi_models
from ensemble_trade_forecast import build_ensemble_columns
from save_trade_forecast_long import to_long_format
from save_long_forecast_to_db import save_long_forecast_to_db
from memory_manager import MemoryManager, memory_context
import pandas as pd
import gc
import sys
import argparse
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
def process_single_hscode(hs_code, db_info, horizon=24, mem_manager=None):
    """
    단일 HS Code에 대한 예측을 수행합니다.

    Args:
        hs_code: 처리할 HS Code
        db_info: 데이터베이스 연결 정보
        horizon: 예측 기간 (기본값: 24개월)
        mem_manager: 메모리 매니저 인스턴스 (선택)

    Returns:
        pd.DataFrame: long format 예측 결과 (실패 시 None)
    """
    # 로컬 변수 추적을 위한 초기 목록
    local_objects = []

    try:
        # 데이터 가져오기
        trade_df_exp = get_trade_data_by_hscode(db_info, hs_code, 'expDlr')
        local_objects.append('trade_df_exp')

        # 데이터 품질 검증
        if not validate_trade_data(trade_df_exp):
            print(f"[SKIP] {hs_code}: 데이터 품질 기준 미달 "
                  f"(길이: {len(trade_df_exp) if trade_df_exp is not None else 0})")
            del trade_df_exp
            return None

        print(f"[처리중] {hs_code}: 데이터 길이 {len(trade_df_exp)}")

        # SARIMA 예측
        sarima_df = sarima_forecast_trade_value(
            df=trade_df_exp,
            indicator='expDlr',
            horizon=horizon,
            sarima_kwargs={}
        )
        local_objects.append('sarima_df')

        # 다중 모델 예측
        fc_table = forecast_trade_multi_models(
            trade_df=trade_df_exp,
            indicator="expDlr",
            horizon=horizon,
            model_kwargs={}
        )
        local_objects.append('fc_table')

        # 앙상블 생성
        ensemble_result = build_ensemble_columns(fc_table, indicator="expDlr")
        local_objects.append('ensemble_result')

        # Long format 변환
        long_df = to_long_format(
            ensemble_result,
            hs_code=hs_code,
            forecast_date=datetime.now().strftime('%Y-%m-%d')
        )

        # 메모리 정리 - 중간 객체 삭제
        del trade_df_exp, sarima_df, fc_table, ensemble_result
        gc.collect()

        return long_df

    except Exception as e:
        print(f"[오류] {hs_code}: {str(e)}")
        # 오류 발생 시에도 메모리 정리
        for obj_name in local_objects:
            try:
                exec(f"del {obj_name}")
            except:
                pass
        gc.collect()
        return None


# ================================
# 4. 배치 저장 함수
# ================================
def save_batch_to_db(batch_data, db_info, mem_manager=None):
    """
    배치 데이터를 DB에 저장하고 메모리 정리

    Args:
        batch_data: 저장할 데이터 리스트
        db_info: DB 연결 정보
        mem_manager: 메모리 매니저 인스턴스
    """
    if not batch_data:
        return

    try:
        # 데이터 결합
        combined_df = pd.concat(batch_data, axis=0)

        # 인덱스 이름 보장
        if combined_df.index.name != "date":
            combined_df.index.name = "date"

        # DB 저장
        save_long_forecast_to_db(long_df=combined_df, db_info=db_info)
        print(f"[저장완료] {len(batch_data)}개 HS Code 예측 결과 저장")

        # 저장 후 메모리 정리
        del combined_df
        batch_data.clear()

        if mem_manager:
            cleanup_result = mem_manager.cleanup_batch()
            print(f"[메모리 정리] {cleanup_result['freed_mb']:.2f} MB 해제, "
                  f"{cleanup_result['collected_objects']}개 객체 수집")
        else:
            gc.collect()

    except Exception as e:
        print(f"[오류] 배치 저장 실패: {str(e)}")
        batch_data.clear()
        gc.collect()


# ================================
# 5. 메인 실행 로직
# ================================
def main(start_idx=None, end_idx=None):
    """
    메인 실행 함수

    Args:
        start_idx: 시작 인덱스 (None이면 처음부터)
        end_idx: 종료 인덱스 (None이면 끝까지)
    """
    # 메모리 매니저 초기화
    mem_manager = MemoryManager(
        warning_threshold_mb=8000,  # 8GB 경고
        critical_threshold_mb=12000  # 12GB 위험
    )

    print("=" * 60)
    print("무역 데이터 예측 배치 프로세스 시작")
    print("=" * 60)
    mem_manager.print_memory_status("[초기] ")
    print()

    # HS Code 리스트 가져오기
    hs_list = get_unique_hscode_list(db_info)
    total_count = len(hs_list)

    # 범위 지정
    if start_idx is not None or end_idx is not None:
        start = start_idx if start_idx is not None else 0
        end = end_idx if end_idx is not None else total_count
        hs_list = hs_list[start:end]
        print(f"[테스트 모드] 인덱스 {start}:{end} 범위만 처리")
        print(f"처리할 HS Code: {len(hs_list)}개")
    else:
        print(f"총 {total_count}개의 HS Code 처리 시작")

    print(f"예시: {hs_list[:5]}\n")

    # 배치 설정
    BATCH_SIZE = 50
    batch_data = []
    success_count = 0
    skip_count = 0
    error_count = 0

    # 모든 HS Code 처리
    for idx, hs_code in enumerate(hs_list, 1):
        # 실제 인덱스 계산 (전체 리스트 기준)
        actual_idx = (start_idx if start_idx else 0) + idx

        print(f"\n{'=' * 60}")
        print(f"진행상황: [{idx}/{len(hs_list)}] (전체: [{actual_idx}/{total_count}]) {hs_code}")
        print(f"{'=' * 60}")

        # 메모리 상태 확인
        memory_status = mem_manager.check_memory_status()
        mem_manager.print_memory_status()

        # 위험 수준이면 긴급 정리
        if memory_status == 'critical':
            print("\n[경고] 메모리 위험 수준 - 긴급 정리 수행")
            mem_manager.emergency_cleanup()

            # 현재 배치 강제 저장
            if batch_data:
                print("현재 배치 강제 저장...")
                save_batch_to_db(batch_data, db_info, mem_manager)
                batch_data = []

        # 경고 수준이면 배치 크기 축소 고려
        elif memory_status == 'warning' and len(batch_data) >= BATCH_SIZE // 2:
            print("\n[경고] 메모리 경고 - 조기 배치 저장")
            save_batch_to_db(batch_data, db_info, mem_manager)
            batch_data = []

        # 예측 수행
        with memory_context(mem_manager, f"HS Code {hs_code} 처리"):
            long_df = process_single_hscode(hs_code, db_info, horizon=24, mem_manager=mem_manager)

        if long_df is not None:
            batch_data.append(long_df)
            success_count += 1
            del long_df  # 즉시 삭제

            # 배치 크기에 도달하면 저장
            if len(batch_data) >= BATCH_SIZE:
                save_batch_to_db(batch_data, db_info, mem_manager)
                batch_data = []
        else:
            skip_count += 1

        # 주기적 메모리 정리 (10개마다)
        if idx % 10 == 0:
            print(f"\n{'-' * 60}")
            print("[정리] 주기적 메모리 정리 수행")
            cleanup_result = mem_manager.cleanup_batch()
            print(f"   해제: {cleanup_result['freed_mb']:.2f} MB")
            print(f"   수집: {cleanup_result['collected_objects']}개 객체")
            mem_manager.print_memory_status("   ")

            print(f"\n[집계] 중간 집계")
            print(f"   성공: {success_count}, 스킵: {skip_count}, 오류: {error_count}")
            print(f"{'-' * 60}\n")

    # 남은 배치 저장
    if batch_data:
        print("\n최종 배치 저장 중...")
        save_batch_to_db(batch_data, db_info, mem_manager)
        batch_data = []

    # 최종 메모리 정리
    print("\n" + "=" * 60)
    print("[정리] 최종 메모리 정리")
    print("=" * 60)
    final_cleanup = mem_manager.cleanup_batch()
    print(f"해제된 메모리: {final_cleanup['freed_mb']:.2f} MB")
    print(f"수집된 객체: {final_cleanup['collected_objects']}개")
    mem_manager.print_memory_status("[최종] ")

    # 최종 결과 출력
    print(f"\n{'=' * 60}")
    print("[완료] 전체 처리 완료!")
    print(f"{'=' * 60}")
    print(f"총 처리:  {len(hs_list)}개")
    print(f"[성공]   {success_count}개")
    print(f"[스킵]   {skip_count}개")
    print(f"[오류]   {error_count}개")
    print(f"성공률:   {success_count / len(hs_list) * 100:.1f}%")
    print(f"{'=' * 60}\n")


# ================================
# 6. 실행
# ================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='무역 데이터 예측 배치 프로세스')
    parser.add_argument('--start', type=int, default=None,
                        help='시작 인덱스 (0부터 시작)')
    parser.add_argument('--end', type=int, default=None,
                        help='종료 인덱스 (exclusive)')
    parser.add_argument('--range', type=str, default=None,
                        help='범위 지정 (예: 500:510)')

    args = parser.parse_args()

    # range 파라미터 파싱
    if args.range:
        try:
            start, end = map(int, args.range.split(':'))
            args.start = start
            args.end = end
        except ValueError:
            print("[오류] --range 형식이 잘못되었습니다. 예: --range 500:510")
            sys.exit(1)

    # 범위 출력
    if args.start is not None or args.end is not None:
        start_str = args.start if args.start is not None else "처음"
        end_str = args.end if args.end is not None else "끝"
        print(f"\n[설정] 처리 범위: {start_str} ~ {end_str}")
        print()

    main(start_idx=args.start, end_idx=args.end)