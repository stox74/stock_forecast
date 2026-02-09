# Auto-generated from 한국수출입데이터_수집_V3.ipynb
# Generated at 2026-02-09T06:42:31.143614

# ===============================
# 0) Imports
# ===============================
import os
import json
import time
import logging
import warnings
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
import xmltodict
import pandas as pd
import numpy as np
from pandas.tseries.offsets import MonthEnd
from tqdm import tqdm

from sqlalchemy import create_engine, text
from concurrent.futures import ThreadPoolExecutor, as_completed

# (선택) 프로젝트 유틸 (있으면 사용, 없으면 무시)
try:
    from DATA.stock_invest_function import get_db_host, fetch_table_data
except Exception:
    get_db_host = None
    fetch_table_data = None

# HS 코드-품목명 딕셔너리 로드 (우선순위: DATA 패키지 > 동일 폴더)
try:
    from DATA.export_code_item_dict import EXPORT_CODE_ITEM  # 권장 위치
except Exception:
    from export_code_item_dict import EXPORT_CODE_ITEM  # 같은 폴더에 있을 때


# ===============================
# 1) Logging
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("trade_v3")


# ===============================
# 2) Config
# ===============================
@dataclass
class TradeCollectorConfig:
    service_key: str
    start_year: int = 2008
    end_year: int = 2026
    max_workers: int = 12
    request_delay: float = 0.05
    retries: int = 3
    timeout: int = 30

    # DB (선택)
    use_db_upload: bool = False
    db_info: Optional[dict] = None
    monthly_table: str = "korea_monthly_trade_data"
    quarterly_table: str = "korea_quarterly_trade_data"

    # HS 필터 (선택)
    hs_include: Optional[List[str]] = None  # 지정하면 이 리스트만 수집
    hs_exclude: Optional[List[str]] = None  # 제외 리스트
    hs_limit: Optional[int] = None          # 상위 N개만 (딕셔너리 순서 기준)

    region_name: str = "전국"

# ✅ 서비스키는 하드코딩보다 환경변수/keys.py 권장
SERVICE_KEY = os.getenv("ODP_SERVICE_KEY", "YOUR_SERVICE_KEY_HERE")

# DB 정보 (원하면 사용)
DB_INFO = None
if get_db_host is not None:
    DB_INFO = {
        "host": get_db_host(),
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }

cfg = TradeCollectorConfig(
    service_key=SERVICE_KEY,
    start_year=2008,
    end_year=2026,
    max_workers=12,
    request_delay=0.05,
    retries=3,
    timeout=30,
    use_db_upload=False,   # 필요시 True로
    db_info=DB_INFO,
    hs_include=None,
    hs_exclude=None,
    hs_limit=None,
    region_name="전국",
)

# HS 코드 목록 로드 (딕셔너리 key)
HS_ALL = [str(k) for k in EXPORT_CODE_ITEM.keys()]


# ===============================
# 3) HS 코드 선택 로직
# ===============================
def build_hs_list(cfg: TradeCollectorConfig) -> List[str]:
    hs = HS_ALL.copy()

    if cfg.hs_include:
        include = set(map(str, cfg.hs_include))
        hs = [h for h in hs if h in include]

    if cfg.hs_exclude:
        exclude = set(map(str, cfg.hs_exclude))
        hs = [h for h in hs if h not in exclude]

    if cfg.hs_limit is not None:
        hs = hs[: int(cfg.hs_limit)]

    # 6자리 보정 (혹시 공백/소수점 등 들어오면 정리)
    hs = [str(h).strip().replace(".0", "") for h in hs if str(h).strip()]
    return hs

HS_CODES = build_hs_list(cfg)
logger.info(f"HS_CODES loaded: {len(HS_CODES):,} codes")


# ===============================
# 4) Period utilities
# ===============================
def yyyymm_range(start_year: int, end_year: int) -> List[str]:
    period = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            period.append(f"{y}{m:02d}")
    return period

def split_yearly(period_list: List[str]) -> Tuple[List[str], List[str]]:
    # API 제한: 1번 요청에 12개월(1년) 범위 권장
    start_list = [period_list[i] for i in range(0, len(period_list), 12)]
    end_list = [period_list[min(i + 11, len(period_list) - 1)] for i in range(0, len(period_list), 12)]
    return start_list, end_list


# ===============================
# 5) API fetch
# ===============================
API_BASE = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"

def fetch_itemtrade(session: requests.Session, cfg: TradeCollectorConfig, start: str, end: str, hs_code: str) -> pd.DataFrame:
    url = (
        f"{API_BASE}"
        f"?serviceKey={cfg.service_key}"
        f"&strtYymm={start}"
        f"&endYymm={end}"
        f"&hsSgn={hs_code}"
    )

    for attempt in range(cfg.retries):
        try:
            resp = session.get(url, timeout=cfg.timeout)
            resp.raise_for_status()

            json_dict = json.loads(json.dumps(xmltodict.parse(resp.text), indent=2))
            items = json_dict.get("response", {}).get("body", {}).get("items")

            if not items or items.get("item") is None:
                logger.warning(f"No data: hs={hs_code}, {start}-{end}")
                return pd.DataFrame()

            item_data = items["item"]
            if isinstance(item_data, dict):
                item_data = [item_data]

            df = pd.DataFrame(item_data)
            df["root_hs_code"] = str(hs_code)
            df["item_name"] = EXPORT_CODE_ITEM.get(str(hs_code), None)

            if cfg.request_delay > 0:
                time.sleep(cfg.request_delay)

            return df

        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt+1}/{cfg.retries} failed: hs={hs_code}, {start}-{end}, err={e}")
            if attempt < cfg.retries - 1:
                time.sleep(2 ** attempt)  # backoff
            else:
                logger.error(f"All attempts failed: hs={hs_code}, {start}-{end}")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"Unexpected error: hs={hs_code}, {start}-{end}, err={e}")
            return pd.DataFrame()


# ===============================
# 6) Transform / Aggregate / YoY / Long-format
# ===============================
def process_and_aggregate(df: pd.DataFrame, region_name: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """원자료를 월/분기 집계 데이터로 변환"""
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = df.copy()

    # 총계 제거
    if "year" in df.columns:
        df = df[df["year"] != "총계"].copy()

    # 날짜 처리: year 컬럼이 '2024.01' 형태일 때
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if "year" in df.columns:
            df["date"] = pd.to_datetime(df["year"].astype(str).str.replace(".", "-", regex=False), errors="coerce") + MonthEnd(0)
        else:
            # fallback: statYymm이 있으면 사용
            if "statYymm" in df.columns:
                df["date"] = pd.to_datetime(df["statYymm"].astype(str) + "01", format="%Y%m%d", errors="coerce") + MonthEnd(0)
            else:
                raise ValueError("No date source column found (need year or statYymm).")

    df = df.dropna(subset=["date"])
    df["new_year"] = df["date"].dt.year
    df["new_quarter"] = df["date"].dt.quarter
    df["new_month"] = df["date"].dt.month

    # 수치형 변환
    numeric_cols = ["balPayments", "expDlr", "expWgt", "impDlr", "impWgt"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    df["root_hs_code"] = df["root_hs_code"].astype(str)
    df["item_name"] = df.get("item_name", None)

    agg_dict = {"balPayments": "sum", "expDlr": "sum", "impDlr": "sum"}

    m = (df.groupby(["root_hs_code", "item_name", "new_year", "new_quarter", "new_month"], dropna=False)
           .agg(agg_dict)
           .reset_index())
    m["region"] = region_name
    m["date"] = pd.to_datetime(m["new_year"].astype(str) + "-" + m["new_month"].astype(str) + "-01") + MonthEnd(0)

    q = (df.groupby(["root_hs_code", "item_name", "new_year", "new_quarter"], dropna=False)
           .agg(agg_dict)
           .reset_index())
    q["region"] = region_name
    end_month_map = {1: "03", 2: "06", 3: "09", 4: "12"}
    q_end_month = q["new_quarter"].map(end_month_map)
    q["date"] = pd.to_datetime(q["new_year"].astype(str) + "-" + q_end_month + "-01") + MonthEnd(0)

    return q, m

def add_yoy_growth(df: pd.DataFrame, steps: int) -> pd.DataFrame:
    """YoY(전년동기대비) 증가율 계산: 월(12), 분기(4)"""
    if df.empty:
        return df
    df = df.copy()
    df = df.sort_values(["root_hs_code", "date"])
    g = df.groupby("root_hs_code", dropna=False)
    df["expDlr_yoy"] = g["expDlr"].transform(lambda x: x.pct_change(periods=steps))
    df["impDlr_yoy"] = g["impDlr"].transform(lambda x: x.pct_change(periods=steps))
    return df

def reshape_to_long(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    id_vars = ["date", "root_hs_code", "item_name", "region"]
    value_vars = ["balPayments", "expDlr", "impDlr", "expDlr_yoy", "impDlr_yoy"]
    value_vars = [c for c in value_vars if c in df.columns]

    long_df = df.melt(id_vars=id_vars, value_vars=value_vars, var_name="indicator", value_name="value")
    long_df = long_df.dropna(subset=["value"])
    long_df["root_hs_code"] = long_df["root_hs_code"].astype(str)
    return long_df


# ===============================
# 7) DB Upload (optional, incremental)
# ===============================
def make_engine(db_info: dict):
    return create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}",
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
    )

def ensure_table(engine, table_name: str):
    create_sql = text(f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `date` DATE NOT NULL,
        `root_hs_code` VARCHAR(20) NOT NULL,
        `indicator` VARCHAR(50) NOT NULL,
        `value` DOUBLE,
        `item_name` VARCHAR(255),
        `region` VARCHAR(50),
        PRIMARY KEY (`date`, `root_hs_code`, `indicator`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)
    with engine.connect() as conn:
        conn.execute(create_sql)
        conn.commit()

def fetch_existing_keys(engine, table_name: str) -> set:
    existing = set()
    try:
        q = text(f"SELECT CONCAT(`date`,'|',`root_hs_code`,'|',`indicator`) FROM {table_name}")
        with engine.connect() as conn:
            res = conn.execute(q)
            existing = {row[0] for row in res}
    except Exception as e:
        logger.warning(f"existing key fetch failed: {e}")
    return existing

def upload_long_df(df_long: pd.DataFrame, db_info: dict, table_name: str, chunk_size: int = 2000):
    if df_long.empty:
        logger.info("No rows to upload.")
        return

    df = df_long.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.where(pd.notnull(df), None)

    engine = make_engine(db_info)
    ensure_table(engine, table_name)

    existing = fetch_existing_keys(engine, table_name)

    key_combo = (
        pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        + "|"
        + df["root_hs_code"].astype(str)
        + "|"
        + df["indicator"].astype(str)
    )
    df["_key"] = key_combo
    to_up = df[~df["_key"].isin(existing)].drop(columns=["_key"])

    if to_up.empty:
        logger.info(f"[{table_name}] 업로드할 신규 데이터 없음")
        return

    logger.info(f"[{table_name}] 업로드 대상: {len(to_up):,} rows")

    for i in tqdm(range(0, len(to_up), chunk_size), desc=f"Upload {table_name}"):
        chunk = to_up.iloc[i:i+chunk_size]
        chunk.to_sql(table_name, con=engine, if_exists="append", index=False, method="multi")

    logger.info(f"[{table_name}] ✅ 업로드 완료: {len(to_up):,} rows")


# ===============================
# 8) Main runner
# ===============================
def run_trade_collector(cfg: TradeCollectorConfig) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    hs_codes = build_hs_list(cfg)
    if not hs_codes:
        raise ValueError("HS_CODES is empty. Check cfg.hs_include/hs_exclude/hs_limit.")

    periods = yyyymm_range(cfg.start_year, cfg.end_year)
    start_list, end_list = split_yearly(periods)

    logger.info(f"Collect period months: {len(periods)} ({periods[0]}~{periods[-1]})")
    logger.info(f"HS codes: {len(hs_codes)} | workers: {cfg.max_workers}")

    session = requests.Session()
    all_dfs = []
    errors = []

    with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
        futures = {}
        for hs in hs_codes:
            for s, e in zip(start_list, end_list):
                fut = ex.submit(fetch_itemtrade, session, cfg, s, e, hs)
                futures[fut] = (hs, s, e)

        for fut in tqdm(as_completed(futures), total=len(futures), desc="API requests"):
            hs, s, e = futures[fut]
            try:
                df = fut.result(timeout=60)
                if df is not None and not df.empty:
                    all_dfs.append(df)
            except Exception as err:
                logger.error(f"failed: hs={hs} {s}-{e} err={err}")
                errors.append((hs, s, e))

    session.close()

    if not all_dfs:
        logger.warning("No data collected.")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    raw = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"Raw rows: {len(raw):,} | errors: {len(errors)}")

    q_df, m_df = process_and_aggregate(raw, cfg.region_name)
    m_yoy = add_yoy_growth(m_df, steps=12)
    q_yoy = add_yoy_growth(q_df, steps=4)

    m_long = reshape_to_long(m_yoy)
    q_long = reshape_to_long(q_yoy)

    # (선택) DB 업로드
    if cfg.use_db_upload:
        if not cfg.db_info:
            raise ValueError("cfg.use_db_upload=True but cfg.db_info is None")
        upload_long_df(m_long, cfg.db_info, cfg.monthly_table)
        upload_long_df(q_long, cfg.db_info, cfg.quarterly_table)

    return m_yoy, q_yoy, m_long, q_long

# 실행
start = time.time()
monthly_yoy, quarterly_yoy, monthly_long, quarterly_long = run_trade_collector(cfg)
logger.info(f"Done. elapsed={time.time()-start:.1f}s")

monthly_yoy.head(), monthly_long.head()

