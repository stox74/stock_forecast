# -*- coding: utf-8 -*-

import sys
import os
from pathlib import Path
import pandas as pd
from pandas.tseries.offsets import MonthEnd
import datetime as dt

def add_repo_path():
    """
    stock_forecast 프로젝트 루트를 자동 탐색하고,
    해당 경로를 sys.path에 추가하여 import 오류를 방지합니다.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "DATA").exists():
            sys.path.insert(0, str(parent))
            return str(parent)
    
    fallback = r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast"
    if os.path.isdir(fallback):
        sys.path.insert(0, fallback)
        return fallback
    raise FileNotFoundError("❌ DATA 폴더를 찾을 수 없습니다.")

def log(stage: str, msg: str):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {stage}: {msg}")

def to_month_end_safe(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, errors="coerce")
    prev_mask = s.dt.day.between(1, 5, inclusive="both")
    out = s.copy()
    out.loc[prev_mask] = (s.loc[prev_mask] + MonthEnd(-1))
    out.loc[~prev_mask] = (s.loc[~prev_mask] + MonthEnd(0))
    return out
