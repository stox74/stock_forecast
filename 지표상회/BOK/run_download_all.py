"""
economy_index_codes.py 안의 모든 지표를 다운로드하고 결과를 확인하는 실행 스크립트

사용 예)
  python run_download_all.py --api_key "..." --start 201001 --end 202512 --cycle M

일별이 필요한 지표(예: 코스피)가 섞여 있으면,
OVERRIDES에서 해당 지표의 cycle/start/end를 덮어쓰세요.
"""

import argparse
import pandas as pd

from economy_index_codes import ECOS_INDEX_MAP
from bok_econ_downloader import build_specs_from_index_map, fetch_economic_indicators, to_wide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api_key", required=True, help="ECOS API Key")
    parser.add_argument("--start", default="200001", help="시작일자 (M이면 YYYYMM, D이면 YYYYMMDD, Q이면 YYYYQn)")
    parser.add_argument("--end", default="202512", help="종료일자 (M이면 YYYYMM, D이면 YYYYMMDD, Q이면 YYYYQn)")
    parser.add_argument("--cycle", default="M", choices=["M", "D", "Q"], help="주기: M/D/Q")
    parser.add_argument("--fail_fast", action="store_true", help="하나라도 실패하면 즉시 중단")
    parser.add_argument("--preview_n", type=int, default=5, help="각 지표 head(n) 미리보기 행 수")
    args = parser.parse_args()

    # ✅ 전체 지표 대상
    targets = list(ECOS_INDEX_MAP.keys())

    # ✅ 지표별 예외(필요 시 여기에 추가)
    # 예) 코스피가 일별(D)인 경우:
    OVERRIDES = {
        # "코스피": {"cycle": "D", "start": "20000101", "end": "20251231"},
        # "코스피지수": {"cycle": "D", "start": "20000101", "end": "20251231"},
    }

    print("======================================")
    print("🚀 ECOS 다운로드 시작")
    print(" - 총 지표 수:", len(targets))
    print(" - 기본 기간:", args.start, "~", args.end, f"(cycle={args.cycle})")
    print("======================================")

    # 1) specs 생성
    specs = build_specs_from_index_map(
        ECOS_INDEX_MAP,
        names=targets,
        default_cycle=args.cycle,
        default_start=args.start,
        default_end=args.end,
        overrides=OVERRIDES,
        lang="kr",
    )

    # 2) 다운로드 실행
    results = fetch_economic_indicators(
        api_key=args.api_key,
        specs=specs,
        fail_fast=args.fail_fast,
    )

    # -----------------------------
    # 결과 요약
    # -----------------------------
    print("\n======================================")
    print("✅ 다운로드 성공 지표")
    print(" - 성공 개수:", len(results))
    print("======================================")
    for k in results.keys():
        print(" -", k)

    # -----------------------------
    # 각 지표 미리보기
    # -----------------------------
    for name, df in results.items():
        print("\n--------------------------------------")
        print(f"▶ {name}")
        print("--------------------------------------")
        print("rows =", len(df))
        # date 컬럼명은 'Date' 또는 'date' 둘 다 허용
        print(df.head(args.preview_n).to_string(index=False))

    # -----------------------------
    # wide 형태로 합쳐서 확인
    # -----------------------------
    if results:
        df_wide = to_wide(results)

        print("\n======================================")
        print("📊 wide 형태 미리보기 (상위 10행)")
        print(" - shape:", df_wide.shape)
        print("======================================")
        print(df_wide.head(10).to_string(index=False))

        # sanity check
        print("\n======================================")
        print("🔎 Sanity Check (각 지표 날짜범위/결측)")
        print("======================================")
        for name, df in results.items():
            date_col = "Date" if "Date" in df.columns else ("date" if "date" in df.columns else None)
            if date_col is None:
                print(f"{name:30s} | (no date col) | columns={list(df.columns)}")
                continue
            print(
                f"{name:30s} | "
                f"min={df[date_col].min()} | "
                f"max={df[date_col].max()} | "
                f"NA={df['value'].isna().sum()}"
            )


if __name__ == "__main__":
    main()
