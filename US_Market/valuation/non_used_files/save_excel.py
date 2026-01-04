# -*- coding: utf-8 -*-

import os
import pandas as pd
import datetime as dt
from typing import Optional


def log(stage: str, msg: str):
    """간단한 로깅 함수"""
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {stage}: {msg}")


def save_excel(df: pd.DataFrame, ticker: str, filename_prefix: str = "valuation_results",
               output_dir: str = "OUTPUT", project_path: str = None) -> Optional[str]:
    """
    DataFrame을 타임스탬프가 포함된 Excel 파일로 저장합니다.

    Parameters:
    -----------
    df : pd.DataFrame
        저장할 DataFrame
    ticker : str
        티커 심볼 (로깅용)
    filename_prefix : str, optional
        파일명 접두사 (기본값: "valuation_results")
    output_dir : str, optional
        출력 디렉토리명 (기본값: "OUTPUT")
    project_path : str, optional
        프로젝트 루트 경로 (기본값: 현재 디렉토리)

    Returns:
    --------
    str or None
        저장 성공 시 파일 경로, 실패 시 None

    Examples:
    ---------
    기본 사용법::

        save_excel(valuation_filled, ticker="AAPL")

    파일명 접두사 변경::

        save_excel(revenue_forecast_df, ticker="MSFT", filename_prefix="revenue_forecast")

    프로젝트 경로 지정::

        save_excel(df, ticker="GOOGL", project_path="/path/to/project")
    """
    # project_path가 제공되지 않으면 현재 디렉토리 사용
    if project_path is None:
        project_path = os.getcwd()

    timestamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"{filename_prefix}_{ticker}_{timestamp}.xlsx"
    output_path = os.path.join(project_path, output_dir, output_filename)

    # 출력 디렉토리 생성
    os.makedirs(os.path.join(project_path, output_dir), exist_ok=True)

    try:
        df.to_excel(output_path, index=True, engine='openpyxl')
        log("EXCEL-SAVE", f"{ticker} saved to {output_filename}")
        return output_path
    except Exception as e:
        log("EXC-EXCEL-SAVE", f"{ticker} failed to save Excel: {e}")
        return None


# if __name__ == "__main__":
#     # 테스트 코드
#     print("=" * 60)
#     print("save_excel 함수 테스트")
#     print("=" * 60)
#
#     # 테스트용 DataFrame 생성
#     test_df = pd.DataFrame({
#         'date': pd.date_range('2024-01-01', periods=5),
#         'revenue': [100.5, 150.2, 200.8, 250.3, 300.1],
#         'valuation': [500, 750, 1000, 1250, 1500]
#     })
#
#     print("\n[테스트 1] 기본 사용")
#     result1 = save_excel(test_df, ticker="AAPL")
#     print(f"저장 경로: {result1}\n")
#
#     print("[테스트 2] 파일명 접두사 변경")
#     result2 = save_excel(test_df, ticker="MSFT", filename_prefix="revenue_forecast")
#     print(f"저장 경로: {result2}\n")
#
#     print("[테스트 3] 출력 디렉토리 변경")
#     result3 = save_excel(test_df, ticker="GOOGL", output_dir="TEST_OUTPUT")
#     print(f"저장 경로: {result3}\n")
#
#     print("=" * 60)
#     print("테스트 완료!")
#     print("=" * 60)🔍 주요
#     변경
#     사항