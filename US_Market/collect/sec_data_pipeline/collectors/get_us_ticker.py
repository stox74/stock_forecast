# get_filtered_us_tickers.py
# 미국 NASDAQ, NYSE, AMEX 상장사 중 특정 IndustryCode 앞 4자리를 제외한 ticker 리스트 반환

import pandas as pd
import FinanceDataReader as fdr


def get_filtered_us_tickers():
    """
    NASDAQ / NYSE / AMEX 전체 상장사 정보를 기반으로
    특정 IndustryCode 앞 4자리에 해당하는 기업들을 제외하고
    최종 ticker 리스트를 반환하는 함수.

    Returns:
        tickers (list): 필터링된 미국 상장사 ticker 리스트
    """

    # 1) 미국 주요 거래소 전체 티커 로드
    nasdaq = fdr.StockListing('NASDAQ')
    nyse = fdr.StockListing('NYSE')
    amex = fdr.StockListing('AMEX')

    # 하나의 DF로 통합
    info_df = pd.concat([nasdaq, nyse, amex], ignore_index=True)

    # 2) 제외할 IndustryCode 앞 4자리 목록
    exclude_prefixes = ["5510", "5730", "5530", "6010", "5910", "5120"]

    # 3) IndustryCode 정리
    info_df["IndustryCode"] = info_df["IndustryCode"].astype(str)
    info_df["IndustryPrefix"] = info_df["IndustryCode"].str[:4]

    # 4) 제외 마스크 생성
    mask_exclude = info_df["IndustryPrefix"].isin(exclude_prefixes)

    # 5) 제외된 기업 / 남은 기업 구분
    excluded_df = info_df[mask_exclude]
    filtered_df = info_df[~mask_exclude].copy()

    # 6) 최종 ticker 리스트 추출
    tickers = filtered_df["Symbol"].unique().tolist()

    print(f"제외된 기업 수: {len(excluded_df)}")
    print(f"남은 기업 수: {len(filtered_df)}")
    print(f"티커 수: {len(tickers)}")

    return tickers


if __name__ == "__main__":
    tickers = get_filtered_us_tickers()
    print("\n샘플 20개:", tickers[:20])
