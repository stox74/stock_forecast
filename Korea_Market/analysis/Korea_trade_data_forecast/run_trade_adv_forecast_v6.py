# -*- coding: utf-8 -*-
"""
전국 품목별 수출 예측 배치 (v6, 2026-10-17)

v6 (v5 대비): 속도 개선
  - 쓰지 않던 별도 SARIMA 호출(sarima_forecast_trade_value) 제거. 다중 모형 안의 SARIMA 와 중복이었음
  - --models 로 다중 모형 선택. 기본은 v5 와 같은 5개(sarima,ets,prophet,lstm,theta)
    빠른 실행: --models sarima,ets,theta  (LSTM·Prophet 제외. fdriver 의 다른 예측과 같은 구성)
    다중 모형 함수는 성공한 모형만 열로 돌려주므로, 앙상블은 원래도 일부 모형이 빠진 경우를 처리한다

v5 (v4 대비): trade_data_import_v2 사용 (DB 엔진 재사용) + 품질 검증 강화
  - 끊긴 계열(테이블 최신월보다 MAX_LAG_MONTHS 넘게 뒤처짐)은 예측하지 않음: 오래된 시점에서 낸 예측이 최신처럼 보이는 것 방지
  - 중간에 빠진 달: MAX_FILL_MONTHS 이하면 0으로 채워 연속 월 계열로 만들고, 넘으면 건너뜀
  - 전부 0이거나 0인 달 비율이 MAX_ZERO_SHARE 초과면 건너뜀 (v1~v4 는 열 이름 불일치로 이 검사가 작동하지 않았음)
  - 끝날 때 건너뛴 이유별 표를 출력하고 CSV 로 저장

v3 (part1/2/3 세 파일) 대비 변경
  1. 한 파일로 통합: --part N --parts K 로 구간 자동 분배 (v3 Part 2 자동 구간 버그 수정)
  2. HS 목록: hs_code_map_v3.build_collection_list() = 정적 목록 + DB 수요 코드
     (V7 수집기와 같은 목록이라 '수집은 되는데 예측은 안 되는' 코드가 생기지 않음)
     --static-only 로 정적 목록만 쓸 수 있음
  3. DB 접속은 DATA/config.py 에서 읽음 (비밀번호를 코드에 적지 않음)
  4. CSV 저장은 --csv 옵션일 때만 (v3 는 파트마다 달랐음)
  5. --codes 로 특정 코드만 예측 가능 (예: --codes 8703,8542,2905)
  6. 목록은 코드 순으로 정렬해 파트 구간이 실행마다 같도록 함

사용 예 (터미널 3개에서 동시에)
  python run_trade_adv_forecast_v6.py --part 1 --parts 3
  python run_trade_adv_forecast_v6.py --part 2 --parts 3
  python run_trade_adv_forecast_v6.py --part 3 --parts 3
  python run_trade_adv_forecast_v6.py --codes 8703,8542            # 특정 코드만
  python run_trade_adv_forecast_v6.py --list-only                  # 목록과 파트 구간만 출력

예측·저장 로직(get_trade_data_by_hscode, SARIMA, 다중 모형, 앙상블, long 변환, DB 저장)은 v3 와 같다.
코드에 이모지 없음.
"""
import os
import sys
import gc
import argparse
from datetime import datetime


def setup_project_path():
    """Korea_trade_data_forecast -> analysis -> Korea_Market -> investment_strategy 를 sys.path 에 추가"""
    current_file = os.path.abspath(__file__)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
    for p in (project_root, os.path.join(project_root, "DATA")):
        if p not in sys.path:
            sys.path.insert(0, p)
    return project_root


PROJECT_ROOT = setup_project_path()

import pandas as pd  # noqa: E402

from DATA.hs_code_map_v3 import HS_CODE_MAP, build_collection_list  # noqa: E402
from trade_data_import_v2 import get_trade_data_by_hscode, check_trade_series, get_latest_month  # noqa: E402
from multi_model_trade_forecast import forecast_trade_multi_models  # noqa: E402
from ensemble_trade_forecast import build_ensemble_columns  # noqa: E402
from save_trade_forecast_long import to_long_format  # noqa: E402
from save_long_forecast_to_db import save_long_forecast_to_db  # noqa: E402
from memory_manager import MemoryManager, memory_context  # noqa: E402

# DB 접속 정보: DATA/config.py
try:
    import config as investar_config
except ImportError:
    from DATA import config as investar_config
db_info = investar_config.get_db_info()

HORIZON = 24
DEFAULT_MODELS = ["sarima", "ets", "prophet", "lstm", "theta"]
USE_MODELS = list(DEFAULT_MODELS)   # main 에서 --models 로 바뀜
BATCH_SIZE = 50
MIN_LENGTH = 60          # 최소 연속 월 수
MAX_LAG_MONTHS = 2       # 테이블 최신월보다 이만큼 넘게 뒤처지면 끊긴 계열로 보고 건너뜀
MAX_FILL_MONTHS = 6      # 중간에 빠진 달이 이 수 이하면 0으로 채움
MAX_ZERO_SHARE = 0.30    # 0인 달 비율 상한
REF_MONTH = None         # main 에서 테이블 최신월로 채움
CSV_EXPORT_DIR = r"C:\Users\82108\OneDrive\INVESTMENT\Data_Asnal\Export_data"


# ================================
# HS 목록
# ================================
def get_hs_list(static_only: bool = False, codes: str = None) -> list:
    if codes:
        return [c.strip() for c in codes.split(",") if c.strip()]
    if static_only:
        return sorted(HS_CODE_MAP.keys())
    engine = investar_config.get_engine(db_info)
    items = build_collection_list(engine, use_demand=True, allow_10digit=False)
    n_extra = sum(1 for it in items if not it["in_static"])
    print(f"[목록] 정적 {len(items) - n_extra} + DB 수요 {n_extra} = {len(items)}개")
    return [it["code"] for it in items]          # build_collection_list 는 코드 순 정렬


def part_range(n: int, part: int, parts: int) -> tuple:
    """n개를 parts 등분했을 때 part 번째(1부터) 구간 [start, end)"""
    if not (1 <= part <= parts):
        raise ValueError(f"--part 는 1~{parts} 사이여야 합니다: {part}")
    base, extra = divmod(n, parts)
    start = (part - 1) * base + min(part - 1, extra)
    end = start + base + (1 if part <= extra else 0)
    return start, end


# ================================
# 품질 검증 / 단일 코드 예측 (v3 와 같음)
# ================================
def validate_trade_data(df):
    """품질 점검 + 필요하면 빈 달 0 채우기. 반환 (df 또는 None, 이유 문자열, 점검 dict)"""
    info = check_trade_series(df, REF_MONTH)
    if info["n_rows"] == 0:
        return None, "자료 없음", info
    if info["all_zero"]:
        return None, "전부 0", info
    if info["lag_months"] is not None and info["lag_months"] > MAX_LAG_MONTHS:
        return None, f"끊김 (최신월 {info['last']:%Y-%m}, {info['lag_months']}개월 뒤처짐)", info
    if info["missing_months"] > MAX_FILL_MONTHS:
        return None, f"빈 달 {info['missing_months']}개", info
    if info["span_months"] < MIN_LENGTH:
        return None, f"길이 {info['span_months']}개월 < {MIN_LENGTH}", info
    if info["zero_share"] is not None and info["zero_share"] > MAX_ZERO_SHARE:
        return None, f"0인 달 {info['zero_share']:.0%}", info
    if info["missing_months"] > 0:      # 몇 달 빠진 것은 0으로 채워 연속 월로
        idx = pd.date_range(info["first"], info["last"], freq="M" if pd.__version__ < "2.2" else "ME")
        base = df.set_index(pd.to_datetime(df["date"]).dt.to_period("M").dt.to_timestamp(how="end").dt.normalize())
        full = base.reindex(idx)
        full["value"] = full["value"].fillna(0.0)
        full["root_hs_code"] = df["root_hs_code"].iloc[0]
        full["indicator"] = df["indicator"].iloc[0]
        full["date"] = full.index
        df = full.reset_index(drop=True)[["date", "root_hs_code", "indicator", "value"]]
        return df, f"ok (빈 달 {info['missing_months']}개 0 채움)", info
    return df, "ok", info


def save_long_df_to_csv(long_df: pd.DataFrame, hs_code: str) -> None:
    try:
        os.makedirs(CSV_EXPORT_DIR, exist_ok=True)
        path = os.path.join(CSV_EXPORT_DIR, f"forecast_{hs_code}_{datetime.now():%Y%m%d}.csv")
        long_df.to_csv(path, encoding="utf-8-sig")
        print(f"[CSV 저장] {path}")
    except Exception as e:
        print(f"[CSV 저장 오류] {hs_code}: {e}")


def process_single_hscode(hs_code, horizon=HORIZON, save_csv=False):
    """반환 (long_df 또는 None, 결과 'ok'/'skip'/'error', 이유)"""
    try:
        raw = get_trade_data_by_hscode(db_info, hs_code, "expDlr", verbose=False)
        trade_df_exp, reason, info = validate_trade_data(raw)
        if trade_df_exp is None:
            print(f"[SKIP] {hs_code}: {reason}")
            return None, "skip", reason
        print(f"[처리중] {hs_code}: {info['span_months']}개월 {info['first']:%Y-%m}~{info['last']:%Y-%m} | {reason}")
        fc_table = forecast_trade_multi_models(trade_df=trade_df_exp, indicator="expDlr",
                                               horizon=horizon, use_models=USE_MODELS, model_kwargs={})
        ensemble_result = build_ensemble_columns(fc_table, indicator="expDlr")
        long_df = to_long_format(ensemble_result, hs_code=hs_code,
                                 forecast_date=datetime.now().strftime("%Y-%m-%d"))
        del trade_df_exp, fc_table, ensemble_result
        gc.collect()
        if save_csv:
            save_long_df_to_csv(long_df, hs_code)
        return long_df, "ok", reason
    except Exception as e:
        print(f"[오류] {hs_code}: {e}")
        gc.collect()
        return None, "error", str(e)[:200]


def save_batch_to_db(batch_data, mem_manager=None):
    if not batch_data:
        return
    try:
        combined_df = pd.concat(batch_data, axis=0)
        if combined_df.index.name != "date":
            combined_df.index.name = "date"
        save_long_forecast_to_db(long_df=combined_df, db_info=db_info)
        print(f"[저장완료] {len(batch_data)}개 HS Code 예측 결과 저장")
        del combined_df
    except Exception as e:
        print(f"[오류] 배치 저장 실패: {e}")
    finally:
        batch_data.clear()
        if mem_manager:
            mem_manager.cleanup_batch()
        else:
            gc.collect()


# ================================
# 메인
# ================================
def main(hs_list, label, save_csv=False):
    global REF_MONTH
    REF_MONTH = get_latest_month(db_info)
    print(f"[기준] 테이블 최신월 {REF_MONTH:%Y-%m}" if REF_MONTH is not None else "[기준] 최신월 없음")
    mem_manager = MemoryManager(warning_threshold_mb=8000, critical_threshold_mb=12000)
    print("=" * 60)
    print(f"수출 예측 배치 시작 | {label} | {len(hs_list)}개 | 모형 {USE_MODELS}")
    print("=" * 60)
    mem_manager.print_memory_status("[초기] ")

    batch_data, counts, log = [], {"ok": 0, "skip": 0, "error": 0}, []
    for idx, hs_code in enumerate(hs_list, 1):
        print(f"\n[{idx}/{len(hs_list)}] {hs_code}")
        status = mem_manager.check_memory_status()
        if status == "critical":
            print("[경고] 메모리 위험 수준 - 긴급 정리 + 배치 저장")
            mem_manager.emergency_cleanup()
            save_batch_to_db(batch_data, mem_manager)
        elif status == "warning" and len(batch_data) >= BATCH_SIZE // 2:
            print("[경고] 메모리 경고 - 조기 배치 저장")
            save_batch_to_db(batch_data, mem_manager)

        with memory_context(mem_manager, f"HS Code {hs_code} 처리"):
            long_df, res, reason = process_single_hscode(hs_code, save_csv=save_csv)
        counts[res] += 1
        log.append({"hs_code": hs_code, "result": res, "reason": reason})
        if long_df is not None:
            batch_data.append(long_df)
            del long_df
            if len(batch_data) >= BATCH_SIZE:
                save_batch_to_db(batch_data, mem_manager)

        if idx % 10 == 0:
            mem_manager.cleanup_batch()
            print(f"[집계] 성공 {counts['ok']}, 스킵 {counts['skip']}, 오류 {counts['error']}")

    save_batch_to_db(batch_data, mem_manager)
    print("\n" + "=" * 60)
    print(f"[완료] {label} | 총 {len(hs_list)} | 성공 {counts['ok']} | 스킵 {counts['skip']} | 오류 {counts['error']}")
    lg = pd.DataFrame(log)
    if len(lg):
        lg["reason_type"] = lg["reason"].str.replace(r"\s*\(.*", "", regex=True).str.replace(r"\d+", "N", regex=True)
        print(lg[lg.result != "ok"].groupby(["result", "reason_type"]).size().to_string())
        nok = lg[lg.result != "ok"]
        if len(nok):
            print(nok.to_string(index=False))
        try:
            out_dir = os.path.join(PROJECT_ROOT, "reports", "trade_forecast")
            os.makedirs(out_dir, exist_ok=True)
            p = os.path.join(out_dir, f"forecast_log_{label.split()[0]}_{datetime.now():%Y%m%d_%H%M}.csv")
            lg.to_csv(p, index=False, encoding="utf-8-sig")
            print(f"[로그] {p}")
        except Exception as e:
            print(f"[로그 저장 실패] {e}")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="전국 품목별 수출 예측 배치 v6")
    ap.add_argument("--part", type=int, default=1, help="파트 번호 (1부터)")
    ap.add_argument("--parts", type=int, default=1, help="전체 파트 수 (터미널 수)")
    ap.add_argument("--codes", type=str, default=None, help="특정 코드만, 쉼표로 구분")
    ap.add_argument("--static-only", action="store_true", help="DB 수요 코드 없이 정적 목록만")
    ap.add_argument("--csv", action="store_true", help="코드별 예측 CSV 도 저장")
    ap.add_argument("--models", type=str, default=",".join(DEFAULT_MODELS),
                    help="다중 모형 선택, 쉼표 구분 (빠른 실행: sarima,ets,theta)")
    ap.add_argument("--list-only", action="store_true", help="목록과 파트 구간만 출력하고 종료")
    args = ap.parse_args()

    USE_MODELS = [m.strip().lower() for m in args.models.split(",") if m.strip()]
    bad = [m for m in USE_MODELS if m not in DEFAULT_MODELS]
    if bad:
        print(f"[오류] 알 수 없는 모형 {bad}. 가능: {DEFAULT_MODELS}")
        sys.exit(1)
    full = get_hs_list(static_only=args.static_only, codes=args.codes)
    if args.codes:
        hs_list, label = full, f"지정 코드 {len(full)}개"
    else:
        s, e = part_range(len(full), args.part, args.parts)
        hs_list, label = full[s:e], f"Part {args.part}/{args.parts} [{s}:{e}] (전체 {len(full)})"

    if args.list_only:
        for p in range(1, args.parts + 1):
            s, e = part_range(len(full), p, args.parts)
            print(f"Part {p}/{args.parts}: [{s}:{e}] {e - s}개 ({full[s] if e > s else '-'} ~ {full[e-1] if e > s else '-'})")
        sys.exit(0)

    main(hs_list, label, save_csv=args.csv)
