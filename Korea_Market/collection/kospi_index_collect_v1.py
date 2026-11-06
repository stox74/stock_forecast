"""
코스피 지수 일별 OHLCV 수집 -> CSV 저장
- pykrx 지수 API 사용 (코스피 지수 코드: 1001)
- 연도 단위로 나눠 호출 (장기간 단일 호출 시 누락/실패 방지)
- pykrx 실패 시 FinanceDataReader(KS11)로 대체
"""

import time
import pandas as pd
from datetime import datetime

START_YEAR = 1980
END_DATE = datetime.today().strftime("%Y%m%d")
OUT_FILE = "kospi_daily.csv"


def fetch_pykrx():
    from pykrx import stock
    frames = []
    for year in range(START_YEAR, datetime.today().year + 1):
        start = f"{year}0101"
        end = min(f"{year}1231", END_DATE)
        try:
            df = stock.get_index_ohlcv(start, end, "1001")
            if not df.empty:
                frames.append(df)
                print(f"{year}: {len(df)}행")
            else:
                print(f"{year}: 데이터 없음")
        except Exception as e:
            print(f"{year}: 실패 - {str(e)[:60]}")
        time.sleep(0.5)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df.rename(columns={"시가": "open", "고가": "high", "저가": "low",
                            "종가": "close", "거래량": "volume"})
    return df[["open", "high", "low", "close", "volume"]]


def fetch_fdr():
    import FinanceDataReader as fdr
    df = fdr.DataReader("KS11", f"{START_YEAR}-01-01")
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    df = fetch_pykrx()
    source = "pykrx"
    if df.empty:
        print("\npykrx 수집 실패 -> FinanceDataReader로 재시도")
        df = fetch_fdr()
        source = "FinanceDataReader"

    df.index.name = "date"
    df = df[~df.index.duplicated()].sort_index()
    df.to_csv(OUT_FILE, encoding="utf-8-sig")

    print(f"\n출처: {source}")
    print(f"기간: {df.index.min().date()} ~ {df.index.max().date()}")
    print(f"행 수: {len(df):,}")
    print(f"저장: {OUT_FILE}")
