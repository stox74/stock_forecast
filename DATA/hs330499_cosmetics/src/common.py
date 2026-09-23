# -*- coding: utf-8 -*-
"""
공통 설정 / DB 연결 / 경로 / 그래프 설정
HS 330499 수출지표의 화장품기업 실적 예측력 분석
"""
from __future__ import annotations
import os, socket, warnings
from pathlib import Path
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")

# ---------------- 경로 ----------------
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
D_DATA = OUT / "data"
D_FIG = OUT / "figures"
D_TAB = OUT / "tables"
D_REP = OUT / "report"
for _p in (D_DATA, D_FIG, D_TAB, D_REP):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------- DB ----------------
DB_USER = "stox7412"
DB_PASS = "Apt106503!~"
DB_PORT = 3307
DB_NAME = "investar"


def get_db_host() -> str:
    """기존 프로젝트(stock_invest_function.get_db_host)와 동일한 규칙."""
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
        if local_ip.startswith("192.168."):
            return "192.168.0.230"
        return "hystox74.synology.me"
    except Exception:
        return "hystox74.synology.me"


_ENGINE = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        url = (f"mysql+pymysql://{DB_USER}:{DB_PASS}@{get_db_host()}:{DB_PORT}"
               f"/{DB_NAME}?charset=utf8mb4")
        _ENGINE = create_engine(url, pool_recycle=3600)
    return _ENGINE


def q(sql: str, **params) -> pd.DataFrame:
    """바운드 파라미터 쿼리 (LIKE의 % 이스케이프 문제 회피)."""
    return pd.read_sql(text(sql), get_engine(), params=params)


# ---------------- 분석 대상 ----------------
HS_CODE = "330499"

COMPANIES = {
    "003350": {"dg_ticker": "A003350", "dart_ticker": "003350", "name": "한국화장품제조"},
    "278470": {"dg_ticker": "A278470", "dart_ticker": "278470", "name": "에이피알"},
}

# 수출통계 공표 시차 가정 (관세청/무역협회 HS 6단위 월별 확정통계)
# 월 M 자료 -> 익월(M+1) 15일경 공표
EXPORT_PUB_LAG_DAYS = 15
EXPORT_PUB_LAG_MONTHS = 1

# ---------------- 그래프 ----------------
def setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    cands = ["Malgun Gothic", "NanumGothic", "Gulim", "Batang", "AppleGothic"]
    avail = {f.name for f in font_manager.fontManager.ttflist}
    for c in cands:
        if c in avail:
            plt.rcParams["font.family"] = c
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.dpi"] = 140
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    return plt


def to_quarter_end(s: pd.Series) -> pd.Series:
    """거래일 기준 날짜를 회계 분기말로 정규화."""
    return pd.to_datetime(s) + pd.offsets.QuarterEnd(0)


def yoy(s: pd.Series, periods: int) -> pd.Series:
    """전년 동기 대비 증가율(%). 분모가 0 이하이면 NaN."""
    prev = s.shift(periods)
    out = (s / prev - 1.0) * 100.0
    out[prev <= 0] = np.nan
    return out


def log(tag: str, msg: str):
    print(f"[{tag}] {msg}", flush=True)
