"""
ks_marketcap_fetcher.py

DB 테이블(long format)에서
ticker 리스트를 넣으면
index = ticker
column = market_cap
형태의 DataFrame을 반환

- as_of_date 미지정 시: MAX(date) 기준
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

import pandas as pd
import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from DATA.stock_invest_function import fetch_table_data, get_db_host


# ===============================
# 1. DB 연결 정보 (직접 입력)
# ===============================
db_info = {
    "host": get_db_host(),
    "port": 3307,
    "user": "stox7412",
    "password": "Apt106503!~",
    "database": "investar",
}


# ===============================
# 2. 유틸
# ===============================
def normalize_kr_ticker(t: str) -> str:
    """
    "005930" / "A005930" / "5930" -> "A005930"
    """
    s = str(t).strip().upper()
    s = re.sub(r"[^0-9A]", "", s)

    if s.startswith("A"):
        num = re.sub(r"[^0-9]", "", s[1:])
    else:
        num = re.sub(r"[^0-9]", "", s)

    if not num:
        raise ValueError(f"Invalid ticker: {t}")

    return f"A{num.zfill(6)}"


def make_mysql_engine(db_info: dict) -> Engine:
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}"
        f"?charset=utf8mb4"
    )
    return create_engine(url, pool_pre_ping=True)


# ===============================
# 3. 핵심 함수
# ===============================
def fetch_marketcap_for_tickers(
    tickers: Iterable[str],
    as_of_date: Optional[str] = None,
    table_name: str = "ks_listed_company_daily_marketcap",
    indicator: str = "시가총액",
    db_info: dict = db_info,
) -> pd.DataFrame:
    """
    Parameters
    ----------
    tickers : ["005930", "000660", ...]
    as_of_date : "YYYY-MM-DD" or None
    """

    tickers_list: List[str] = [normalize_kr_ticker(t) for t in tickers]
    if not tickers_list:
        return pd.DataFrame(columns=["market_cap"], index=pd.Index([], name="ticker"))

    engine = make_mysql_engine(db_info)

    with engine.connect() as conn:
        if as_of_date is None:
            as_of_date = conn.execute(
                text(f"SELECT MAX(date) FROM {table_name}")
            ).scalar()

        params = {"as_of_date": as_of_date, "indicator": indicator}
        in_keys = []
        for i, t in enumerate(tickers_list):
            k = f"t{i}"
            in_keys.append(f":{k}")
            params[k] = t

        sql = f"""
        SELECT ticker, value
        FROM {table_name}
        WHERE date = :as_of_date
          AND indicator = :indicator
          AND ticker IN ({",".join(in_keys)})
        """

        df = pd.read_sql(text(sql), conn, params=params)

    df = df.rename(columns={"value": "market_cap"})
    df = df.drop_duplicates(subset=["ticker"], keep="last")
    df = df.set_index("ticker")

    out = df.reindex(tickers_list)[["market_cap"]]
    out.index.name = "ticker"

    return out


# ===============================
# 4. 테스트
# ===============================
if __name__ == "__main__":
    tickers = ["005930", "000660", "A035420"]

    print("=== latest date ===")
    print(fetch_marketcap_for_tickers(tickers))

    print("\n=== specified date ===")
    print(fetch_marketcap_for_tickers(tickers, as_of_date="2025-12-18"))
