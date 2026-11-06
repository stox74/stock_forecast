"""
코스피 지수 일별 OHLCV 수집 -> CSV 저장 (v3)
- 1순위: pykrx (name_display=False 로 지수명 조회 단계 생략, 연도별 재시도)
- 2순위: 네이버 차트 API
- 3순위: yfinance (^KS11, 1996년 이후만 제공)
"""

import time
import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime

START_YEAR = 1980
END_DATE = datetime.today().strftime("%Y%m%d")
OUT_FILE = "kospi_daily.csv"
COLS = ["open", "high", "low", "close", "volume"]


def fetch_pykrx(retries=4):
    from pykrx import stock
    frames, failed = [], []
    for year in range(START_YEAR, datetime.today().year + 1):
        start = f"{year}0101"
        end = min(f"{year}1231", END_DATE)
        for attempt in range(1, retries + 1):
            try:
                df = stock.get_index_ohlcv(start, end, "1001", name_display=False)
                if df.empty:
                    print(f"  {year}: 데이터 없음")
                else:
                    frames.append(df)
                    print(f"  {year}: {len(df)}행")
                break
            except Exception as e:
                msg = str(e)[:50]
                if attempt < retries:
                    print(f"  {year}: 재시도 {attempt}/{retries - 1} - {msg}")
                    time.sleep(3 * attempt)
                else:
                    print(f"  {year}: 최종 실패 - {msg}")
                    failed.append(year)
        time.sleep(0.5)
    if failed:
        print(f"\n  [경고] 누락 연도: {failed} -> 이 연도 구간은 데이터 공백")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).rename(columns={
        "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume"})
    return df[COLS]


def fetch_naver():
    url = ("https://fchart.stock.naver.com/sise.nhn"
           "?symbol=KOSPI&timeframe=day&count=20000&requestType=0")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for item in root.iter("item"):
        d = item.get("data").split("|")
        rows.append([d[0]] + [float(x) for x in d[1:6]])
    df = pd.DataFrame(rows, columns=["date"] + COLS)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.set_index("date")


def fetch_yahoo():
    import yfinance as yf
    df = yf.download("^KS11", start=f"{START_YEAR}-01-01",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df[COLS]


if __name__ == "__main__":
    sources = [("pykrx", fetch_pykrx),
               ("네이버", fetch_naver),
               ("yfinance", fetch_yahoo)]
    df, used = pd.DataFrame(), None
    for name, func in sources:
        print(f"\n[{name}] 수집 시도")
        try:
            df = func()
        except Exception as e:
            print(f"  실패 - {str(e)[:80]}")
            df = pd.DataFrame()
        if not df.empty:
            used = name
            break

    if df.empty:
        print("\n모든 출처에서 수집 실패")
    else:
        df.index.name = "date"
        df = df[~df.index.duplicated()].sort_index()
        df.to_csv(OUT_FILE, encoding="utf-8-sig")
        print(f"\n출처: {used}")
        print(f"기간: {df.index.min().date()} ~ {df.index.max().date()}")
        print(f"행 수: {len(df):,}")
        print(f"저장: {OUT_FILE}")
