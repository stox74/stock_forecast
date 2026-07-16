# -*- coding: utf-8 -*-
"""
analysis_config.py — 노트북/데스크탑 공용 경로 및 DB 설정 (Python 3.9)

tw_revenue_V4.py 의 setup_universal_paths() 방식을 그대로 채택하여
어떤 PC 에서도 동일하게 동작하도록 경로를 통일한다.

  1) setup_universal_paths() : cwd / 스크립트 위치에서 상위로 올라가며
     DATA 폴더를 찾아 sys.path 에 추가 (사용자 모듈 import 용)
  2) find_revenue_db()       : 대만 매출 SQLite(revenue.db) 자동 탐색
  3) get_db_info_safe()      : MariaDB/MySQL 접속 정보 (fallback 체인)
"""
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent


# ----------------------------------------------------------------------
# 1. 범용 경로 설정 (tw_revenue_V4.py 와 동일 로직)
# ----------------------------------------------------------------------
def setup_universal_paths(quiet: bool = False):
    """
    어떤 PC에서도 작동하는 범용 경로 설정.
    스크립트 위치와 현재 작업 폴더에서 상위로 올라가며 DATA 폴더를 찾아
    sys.path 에 추가한다. 찾지 못해도 오류 없이 None 을 반환한다.
    """
    seen = set()
    for start in (Path.cwd(), BASE_DIR):
        for parent in [start, *start.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            data_folder = parent / "DATA"
            if data_folder.exists():
                for p in (str(parent), str(data_folder)):
                    if p not in sys.path:
                        sys.path.insert(0, p)
                if not quiet:
                    print("=" * 60)
                    print("경로 설정 완료")
                    print(f"프로젝트 루트: {parent}")
                    print(f"DATA 폴더:    {data_folder}")
                    print("=" * 60)
                return {"project_root": parent, "data_folder": data_folder,
                        "current": Path.cwd()}
    if not quiet:
        print("[경로] DATA 폴더를 찾지 못했습니다.")
    return None


# ----------------------------------------------------------------------
# 2. 대만 매출 DB(revenue.db) 자동 탐색
# ----------------------------------------------------------------------
def find_revenue_db(quiet: bool = False) -> Optional[Path]:
    """
    revenue.db 위치 자동 탐색. 우선순위:
      1) 환경변수 TW_REVENUE_DB
      2) 이 파일과 같은 폴더
      3) cwd / 스크립트 위치에서 상위로 올라가며 탐색
         (하위 1단계 폴더까지 함께 검사: 예 project_root/TW_revenue/revenue.db)
    """
    env = os.environ.get("TW_REVENUE_DB")
    if env and Path(env).exists():
        return Path(env)

    seen = set()
    for start in (BASE_DIR, Path.cwd()):
        for parent in [start, *start.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            cand = parent / "revenue.db"
            if cand.exists():
                if not quiet:
                    print(f"[경로] revenue.db: {cand}")
                return cand
            # 하위 1단계 폴더도 검사
            try:
                for sub in parent.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        c2 = sub / "revenue.db"
                        if c2.exists():
                            if not quiet:
                                print(f"[경로] revenue.db: {c2}")
                            return c2
            except (PermissionError, OSError):
                continue
    if not quiet:
        print("[경로] revenue.db 를 찾지 못했습니다. "
              "환경변수 TW_REVENUE_DB 로 직접 지정할 수 있습니다.")
    return None


# ----------------------------------------------------------------------
# 3. MariaDB/MySQL 접속 정보 (US_FMP_FS_2_DB_SAVE_LIB.py 의 fallback 체인)
# ----------------------------------------------------------------------
def get_db_info_safe() -> dict:
    """DATA.config → config → stock_invest_function 순서로 접속정보 확보."""
    setup_universal_paths(quiet=True)
    try:
        from DATA.config import get_db_info          # type: ignore
        return get_db_info()
    except ImportError:
        pass
    try:
        from config import get_db_info               # type: ignore
        return get_db_info()
    except ImportError:
        pass
    from DATA.stock_invest_function import get_db_host   # type: ignore
    print("⚠️  Warning: Using fallback DB config")
    return {
        "host": get_db_host(),
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }


def make_engine(db_info: Optional[dict] = None):
    """SQLAlchemy 엔진 생성 (pymysql). KR/US 로더 공용."""
    from sqlalchemy import create_engine
    info = db_info or get_db_info_safe()
    port = int(info.get("port", 3307))
    url = (f"mysql+pymysql://{info['user']}:{info['password']}"
           f"@{info['host']}:{port}/{info['database']}?charset=utf8mb4")
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


# ----------------------------------------------------------------------
# 4. 분석 공통 상수
# ----------------------------------------------------------------------
KR_REVENUE_ITEM_CODE = "M000904001"   # DataGuide 매출액
US_REVENUE_ITEM = "revenue"           # FMP income statement 매출 항목명
MIN_OVERLAP_QUARTERS = 12             # 상관계수 최소 겹침 분기 수
DEFAULT_MIN_CORR = 0.75               # 예측 후보 선별 기준 상관계수
