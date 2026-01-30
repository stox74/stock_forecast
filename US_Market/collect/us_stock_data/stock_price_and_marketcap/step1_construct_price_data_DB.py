import time
import random
import datetime as dt
from typing import Dict, List, Optional, Tuple
from collections import deque

import pandas as pd
import pymysql
import requests
from tqdm import tqdm

from DATA.stock_invest_function import get_db_host
from DATA.us_target_ticker_list_2000 import ticker_list

# =========================
# 0) DB Config
# =========================
DB_CONFIG = {
    "user": "stox7412",
    "password": 'Apt106503!~',
    "host": get_db_host(),
    "port": 3307,
    "database": "investar",
    "charset": "utf8mb4",
}

TABLE_SRC = "us_required_return_result"
TABLE_DST = "us_stock_daily_market_cap"

PRICE_INDICATOR_SRC = "price_stock"
PRICE_INDICATOR_DST = "close_price"

# FMP
FMP_API_KEY = "hT0gAk87j9xZx4PlBApvBqfVL5IahvgV"


# =========================
# Rate Limiter (글로벌)
# =========================
class RateLimiter:
    """
    FMP API 호출 간격을 제어하는 Rate Limiter
    - 분당 최대 호출 횟수 제한
    - 최근 호출 시간 추적
    """

    def __init__(self, calls_per_minute: int = 750, min_interval: float = 0.1):
        """
        calls_per_minute: 분당 최대 호출 횟수
            - FMP Starter: 250/분
            - FMP Professional: 750/분
            - FMP Enterprise: 1000+/분
        min_interval: 최소 호출 간격 (초)
        """
        self.calls_per_minute = calls_per_minute
        self.min_interval = min_interval
        self.call_times = deque()
        self.last_call = 0

    def wait_if_needed(self):
        """필요시 대기"""
        now = time.time()

        # 1) 최소 간격 체크
        elapsed = now - self.last_call
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            time.sleep(sleep_time)
            now = time.time()

        # 2) 분당 호출 횟수 체크
        cutoff = now - 60
        while self.call_times and self.call_times[0] < cutoff:
            self.call_times.popleft()

        if len(self.call_times) >= self.calls_per_minute:
            # 가장 오래된 호출 + 60초가 될 때까지 대기
            sleep_time = (self.call_times[0] + 60) - now + 0.5
            if sleep_time > 0:
                print(f"[Rate Limit] Sleeping {sleep_time:.1f}s (calls in last min: {len(self.call_times)})")
                time.sleep(sleep_time)

        # 3) 호출 기록
        now = time.time()
        self.call_times.append(now)
        self.last_call = now


# 글로벌 rate limiter 인스턴스 (유료 플랜: Professional 기준 750/분)
rate_limiter = RateLimiter(calls_per_minute=750, min_interval=0.1)


# =========================
# 1) DB helpers
# =========================
def get_conn(db_config: Dict) -> pymysql.connections.Connection:
    return pymysql.connect(**db_config)


def create_dst_table(db_config: Dict):
    sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_DST} (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        date DATE NOT NULL,
        ticker VARCHAR(20) NOT NULL,
        indicator VARCHAR(50) NOT NULL,
        value DOUBLE,
        source VARCHAR(20) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_date_ticker_ind (date, ticker, indicator),
        INDEX idx_ticker_date (ticker, date),
        INDEX idx_date (date),
        INDEX idx_indicator (indicator)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    conn = get_conn(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def get_last_saved_date(db_config: Dict, ticker: str, indicator: str) -> Optional[dt.date]:
    """
    목적 테이블(TABLE_DST)에 이미 저장된 마지막 날짜를 가져옴 (증분 수집용)
    """
    conn = get_conn(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT MAX(date) FROM {TABLE_DST} WHERE ticker=%s AND indicator=%s",
                (ticker, indicator),
            )
            (mx,) = cur.fetchone()
            if mx is None:
                return None
            if isinstance(mx, dt.datetime):
                return mx.date()
            return mx
    finally:
        conn.close()


def fetch_price_stock_from_src(db_config: Dict, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    us_required_return_result에서 price_stock 데이터를 가져옴.
    반환 DF: date, close_price
    """
    conn = get_conn(db_config)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT date, value
                FROM {TABLE_SRC}
                WHERE ticker=%s
                  AND indicator=%s
                  AND date >= %s
                  AND date <= %s
                ORDER BY date
                """,
                (ticker, PRICE_INDICATOR_SRC, start_date, end_date),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=["date", "close_price"])

    df = pd.DataFrame(rows, columns=["date", "close_price"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df = df.dropna(subset=["close_price"])
    return df


# =========================
# 2) FMP price fetch (개선)
# =========================
def fmp_get_historical_daily(ticker: str, start_date: str, end_date: str, api_key: str,
                             max_retry: int = 3) -> pd.DataFrame:
    """
    FMP historical-price-full로 일별 OHLCV 가져오기.
    반환 DF: date, close_price

    개선사항:
    - 글로벌 rate limiter 사용
    - 429 에러시 적절한 대기 시간
    - 점진적 백오프
    - 유료 플랜: 재시도 횟수 3회로 축소 (안정적인 API)
    """
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
    params = {
        "from": start_date,
        "to": end_date,
        "apikey": api_key,
    }

    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            # Rate limiter 대기
            rate_limiter.wait_if_needed()

            r = requests.get(url, params=params, timeout=20)

            # 429 처리
            if r.status_code == 429:
                raise RuntimeError("429 Too Many Requests (FMP rate limit)")

            r.raise_for_status()

            js = r.json()
            hist = js.get("historical", [])
            if not hist:
                return pd.DataFrame(columns=["date", "close_price"])

            df = pd.DataFrame(hist)
            df = df[["date", "close"]].rename(columns={"close": "close_price"})
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
            df = df.dropna(subset=["close_price"])
            df = df.sort_values("date")
            return df

        except Exception as e:
            last_err = e
            msg = str(e).lower()

            # 429 또는 rate limit 에러 (유료 사용자는 드물게 발생)
            if "429" in msg or "too many requests" in msg or "rate" in msg:
                # 30초 ~ 60초 대기 (유료 플랜이므로 짧게)
                sleep_s = random.randint(30, 60) + attempt * random.randint(5, 15)
                print(f"[RATE LIMIT] {ticker} (try {attempt}/{max_retry}): sleeping {sleep_s:.0f}s")
            else:
                # 일반 에러: 지수 백오프
                sleep_s = min(20, 2 ** (attempt - 1)) + random.uniform(0, 1.0)
                print(f"[ERROR] {ticker} (try {attempt}/{max_retry}): {e} / sleep {sleep_s:.1f}s")

            time.sleep(sleep_s)

    print(f"[FINAL FAIL] {ticker}: {last_err}")
    return pd.DataFrame(columns=["date", "close_price"])


# =========================
# 3) Upsert to DST (long format)
# =========================
def upsert_close_price_long(db_config: Dict, ticker: str, df_price: pd.DataFrame, source: str) -> int:
    """
    df_price: columns = ['date','close_price'] (date is date)
    저장 형식: (date, ticker, 'close_price', value, source)
    """
    if df_price is None or df_price.empty:
        return 0

    rows: List[Tuple] = []
    for _, r in df_price.iterrows():
        rows.append((
            r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"]),
            ticker,
            PRICE_INDICATOR_DST,
            float(r["close_price"]),
            source
        ))

    sql = f"""
    INSERT INTO {TABLE_DST} (date, ticker, indicator, value, source)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        value = VALUES(value),
        source = VALUES(source)
    """

    conn = get_conn(db_config)
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        return len(rows)
    except Exception as e:
        conn.rollback()
        print(f"[DB ERROR] {ticker}: {e}")
        return 0
    finally:
        conn.close()


# =========================
# 4) 배치 저장 함수 (개선)
# =========================
def save_batch(db_config: Dict, batch_data: List[Tuple[str, pd.DataFrame, str]]):
    """
    여러 ticker의 데이터를 한번에 저장
    batch_data: [(ticker, df_price, source), ...]
    """
    if not batch_data:
        return 0

    all_rows = []
    for ticker, df_price, source in batch_data:
        if df_price is None or df_price.empty:
            continue

        for _, r in df_price.iterrows():
            all_rows.append((
                r["date"].strftime("%Y-%m-%d") if hasattr(r["date"], "strftime") else str(r["date"]),
                ticker,
                PRICE_INDICATOR_DST,
                float(r["close_price"]),
                source
            ))

    if not all_rows:
        return 0

    sql = f"""
    INSERT INTO {TABLE_DST} (date, ticker, indicator, value, source)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        value = VALUES(value),
        source = VALUES(source)
    """

    conn = get_conn(db_config)
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, all_rows)
        conn.commit()
        return len(all_rows)
    except Exception as e:
        conn.rollback()
        print(f"[BATCH SAVE ERROR]: {e}")
        return 0
    finally:
        conn.close()


# =========================
# 5) Main pipeline (개선된 버전)
# =========================
def run_pipeline(
        tickers: List[str],
        start_date_default: str,
        end_date: str,
        db_config: Dict,
        fmp_api_key: str,
        test_mode: bool = False,
        test_count: int = 20,
        batch_size: int = 100  # 배치 저장 크기 (유료 플랜: 100)
):
    if test_mode:
        tickers = tickers[:test_count]
        print(f"[TEST MODE] tickers={len(tickers)}")

    create_dst_table(db_config)

    total_saved = 0
    used_src = 0
    used_fmp = 0
    skipped = 0
    failed = 0

    batch_data = []

    print(f"[START] Total tickers: {len(tickers)}")
    print(f"[CONFIG] Batch size: {batch_size}")
    print(f"[CONFIG] Rate limit: {rate_limiter.calls_per_minute} calls/min, min interval: {rate_limiter.min_interval}s")
    print("=" * 80)

    for idx, t in enumerate(tqdm(tickers, desc="Collecting", unit="ticker"), start=1):
        last_dt = get_last_saved_date(db_config, t, PRICE_INDICATOR_DST)

        if last_dt is None:
            start_date = start_date_default
        else:
            next_dt = last_dt + dt.timedelta(days=1)
            start_date = next_dt.strftime("%Y-%m-%d")

        if start_date > end_date:
            skipped += 1
            continue

        # 1) DB price_stock 우선
        df_src = fetch_price_stock_from_src(db_config, t, start_date, end_date)
        if df_src is not None and not df_src.empty:
            batch_data.append((t, df_src, "db_price_stock"))
            used_src += 1

        else:
            # 2) FMP fallback
            df_fmp = fmp_get_historical_daily(t, start_date, end_date, fmp_api_key)

            if df_fmp is None or df_fmp.empty:
                failed += 1
                continue

            batch_data.append((t, df_fmp, "fmp"))
            used_fmp += 1

        # 배치 저장
        if len(batch_data) >= batch_size:
            saved = save_batch(db_config, batch_data)
            total_saved += saved
            print(f"[BATCH SAVE] {len(batch_data)} tickers, {saved:,} rows saved")
            batch_data = []

        # 진행률 출력
        if idx % 100 == 0:
            print(
                f"[PROGRESS] {idx}/{len(tickers)} | Saved: {total_saved:,} | DB: {used_src} | FMP: {used_fmp} | Skip: {skipped} | Fail: {failed}")

    # 남은 배치 저장
    if batch_data:
        saved = save_batch(db_config, batch_data)
        total_saved += saved
        print(f"[FINAL BATCH] {len(batch_data)} tickers, {saved:,} rows saved")

    print("=" * 80)
    print("[DONE]")
    print(f"Total tickers processed: {len(tickers)}")
    print(f"Rows saved: {total_saved:,}")
    print(f"Used DB price_stock: {used_src} tickers")
    print(f"Used FMP: {used_fmp} tickers")
    print(f"Skipped (up-to-date): {skipped} tickers")
    print(f"Failed: {failed} tickers")
    print("=" * 80)


if __name__ == "__main__":
    START_DATE_DEFAULT = "2015-01-01"
    END_DATE = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d")

    run_pipeline(
        tickers=ticker_list,
        start_date_default=START_DATE_DEFAULT,
        end_date=END_DATE,
        db_config=DB_CONFIG,
        fmp_api_key=FMP_API_KEY,
        test_mode=False,  # True로 하면 테스트 모드
        test_count=30,
        batch_size=100  # 유료 플랜: 100개씩 배치 저장
    )