"""
한국은행 ECOS 경제지표 수집기 (v4, 2026-10-16)

v4: 빈 테이블(이관 직후)에서 값 비교 오류 수정 (Value 를 숫자형으로 강제)

v3: 옛 구조 테이블에서 --migrate 없이 실행하면 아무것도 쓰기 전에 멈춤 (v2 오류 수정)

v1(노트북 BOK_Data_API_Final) 대비 변경
  1. 종료월 자동: 실행일이 속한 달까지 요청 (고정값 202512 제거)
  2. 주택가격지수처럼 기준월 개편으로 통계표가 나뉜 지표는 segments 를 겹치는 기간 비율로 연결
  3. 저장: 테이블 전체 덮어쓰기(replace) 대신 (Index_Name, Date) 기준 갱신 저장(upsert)
     - first_collected_at: 그 값이 처음 수집된 시각 (갱신 때 유지)
     - last_collected_at : 마지막 수집 시각
     - available_at      : Date + lag_days (대략적인 공표일, 신뢰도 C)
  4. 이력: 값이 새로 생기거나 바뀐 행만 bok_economic_indicators_vintage 에 추가
  5. API 키는 DATA/KEYS.py 의 KEYS['ECOS_API_KEY'] 에서 읽음 (코드에 키 없음)
  6. 이름이 바뀐 지표(은행대출금_* -> 은행대출연체율_*)의 옛 행은 이관 단계에서 정리

사용
  python bok_econ_downloader_v4.py --dry-run      # 수집만 하고 CSV 로 저장 (DB 쓰기 없음)
  python bok_econ_downloader_v4.py --migrate      # 최초 1회: 기존 테이블 백업 후 새 구조로 생성 + 적재
  python bok_econ_downloader_v4.py                # 정기 실행: 갱신 저장 + 이력

같은 폴더에 bok_client.py, economy_index_codes_v2.py 가 있어야 한다. 코드에 이모지 없음.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from bok_client import BOKClient  # noqa: E402
import economy_index_codes_v2 as codes  # noqa: E402

TABLE = "bok_economic_indicators"
VINTAGE = "bok_economic_indicators_vintage"
STAGE = "_bok_stage"
DEFAULT_START = "201001"


# ---------------------------------------------------------------- 환경
def _add_data_parent_to_path() -> None:
    for start in (os.getcwd(), HERE):
        d = start
        while True:
            if os.path.isfile(os.path.join(d, "DATA", "KEYS.py")):
                if d not in sys.path:
                    sys.path.insert(0, d)
                return
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent


def get_api_key() -> str:
    _add_data_parent_to_path()
    from DATA.KEYS import KEYS  # type: ignore
    key = KEYS.get("ECOS_API_KEY") or KEYS.get("BOK")
    if not key:
        raise SystemExit("DATA/KEYS.py 의 KEYS 에 'ECOS_API_KEY' 가 없습니다.")
    return key


def get_engine():
    _add_data_parent_to_path()
    from DATA.config import get_db_info, get_engine as _ge  # type: ignore
    return _ge(get_db_info())


def current_yyyymm() -> str:
    t = date.today()
    return f"{t.year}{t.month:02d}"


# ---------------------------------------------------------------- 수집
def fetch_one(client: BOKClient, stat_code: str, items: List[str], start: str, end: str) -> pd.Series:
    df = client.statistic_search(stat_code=stat_code, cycle="M", start=start, end=end, items=items or None)
    if df.empty or "date" not in df.columns:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(df["value"], errors="coerce").values, index=pd.to_datetime(df["date"]))
    s = s[~s.index.isna()].dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def splice(segments: List[pd.Series], labels: List[str]) -> (pd.Series, str):
    """segments: 최신 표부터. 최신 표 수준에 맞춰 과거 표를 겹치는 기간의 비율(중앙값)로 이어 붙인다."""
    base = None
    used = []
    for s, lab in zip(segments, labels):
        if s.empty:
            continue
        if base is None:
            base = s.copy()
            used.append(lab)
            continue
        common = base.index.intersection(s.index)
        if len(common) < 3:
            print(f"  [경고] 연결 구간 부족 ({lab}, 겹침 {len(common)}개월) - 건너뜀")
            continue
        ratio = float(np.median(base[common] / s[common]))
        older = s[s.index < base.index.min()] * ratio
        if len(older):
            base = pd.concat([older, base]).sort_index()
            used.append(f"{lab}x{ratio:.6f}")
    if base is None:
        return pd.Series(dtype=float), ""
    return base, "연결: " + " <- ".join(used)


def collect(start: str = DEFAULT_START, end: Optional[str] = None) -> pd.DataFrame:
    end = end or current_yyyymm()
    client = BOKClient(api_key=get_api_key())
    frames = []
    fails = []
    for name, info in codes.ECOS_INDEX_MAP.items():
        detail = info.get("detail", "")
        try:
            if info.get("segments"):
                segs, labs = [], []
                for seg in info["segments"]:
                    segs.append(fetch_one(client, seg["stat_code"], seg["items"], start, end))
                    labs.append(f"{seg['stat_code']}({seg.get('base', '')})")
                    time.sleep(0.15)
                s, detail = splice(segs, labs)
            else:
                s = fetch_one(client, info["stat_code"], [str(x) for x in info.get("items", [])], start, end)
                time.sleep(0.15)
        except Exception as e:  # 한 지표 실패가 전체를 멈추지 않게
            fails.append(f"{name}: {e}")
            continue
        if s.empty:
            fails.append(f"{name}: 데이터 없음")
            continue
        d = pd.DataFrame({"Date": s.index, "Value": s.values})
        d["MoM"] = d["Value"].pct_change(1)
        d["YoY"] = d["Value"].pct_change(12)
        d["Index_Code"] = info.get("index_code", name)
        d["Index_Name"] = name
        d["Index_Detail"] = detail
        d["Category"] = info.get("category", "")
        d["stat_code"] = str(info["stat_code"])
        d["freq"] = "M"
        lag = int(info.get("lag_days", 30))
        d["lag_days"] = lag
        d["available_at"] = (d["Date"] + pd.to_timedelta(lag, unit="D")).dt.date
        frames.append(d)
        print(f"  {name:28s} {len(d):4d}개월  {d['Date'].min():%Y-%m} ~ {d['Date'].max():%Y-%m}")
    out = pd.concat(frames, ignore_index=True)
    out["Date"] = pd.to_datetime(out["Date"]).dt.date
    out = out.replace([np.inf, -np.inf], np.nan)
    if fails:
        print("\n[실패/빈 지표]")
        for f in fails:
            print("  " + f)
    return out


# ---------------------------------------------------------------- 저장
DDL_MAIN = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  Index_Code VARCHAR(100) NOT NULL,
  Index_Name VARCHAR(100) NOT NULL,
  Index_Detail VARCHAR(500) NULL,
  Category VARCHAR(50) NULL,
  stat_code VARCHAR(20) NULL,
  freq CHAR(1) NOT NULL,
  Date DATE NOT NULL,
  Value DOUBLE NULL,
  MoM DOUBLE NULL,
  YoY DOUBLE NULL,
  Report_Date DATE NULL,
  lag_days INT NULL,
  available_at DATE NULL,
  first_collected_at DATETIME NOT NULL,
  last_collected_at DATETIME NOT NULL,
  PRIMARY KEY (Index_Name, Date),
  KEY idx_date (Date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
"""

DDL_VINTAGE = f"""
CREATE TABLE IF NOT EXISTS {VINTAGE} (
  Index_Name VARCHAR(100) NOT NULL,
  Date DATE NOT NULL,
  Value DOUBLE NULL,
  collected_at DATETIME NOT NULL,
  change_type VARCHAR(10) NOT NULL,
  PRIMARY KEY (Index_Name, Date, collected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
"""

COLS = ["Index_Code", "Index_Name", "Index_Detail", "Category", "stat_code", "freq", "Date", "Value",
        "MoM", "YoY", "Report_Date", "lag_days", "available_at", "first_collected_at", "last_collected_at"]


def migrate(engine) -> None:
    """기존 테이블(PK 없음, replace 방식)을 백업하고 새 구조로 만든다. 최초 1회."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as con:
        if insp.has_table(TABLE):
            pk = insp.get_pk_constraint(TABLE).get("constrained_columns") or []
            if pk == ["Index_Name", "Date"]:
                print("이미 새 구조입니다. 이관 생략.")
            else:
                bak = f"{TABLE}_bak_{datetime.now():%Y%m%d_%H%M}"
                con.execute(text(f"RENAME TABLE {TABLE} TO {bak}"))
                print(f"기존 테이블 백업: {bak}")
        con.execute(text(DDL_MAIN))
        con.execute(text(DDL_VINTAGE))
    print("새 구조 생성 완료")


def check_structure(engine) -> None:
    """테이블이 있는데 새 구조(PK Index_Name+Date, 새 열)가 아니면 쓰기 전에 멈춘다."""
    from sqlalchemy import inspect
    insp = inspect(engine)
    if not insp.has_table(TABLE):
        return
    pk = insp.get_pk_constraint(TABLE).get("constrained_columns") or []
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    missing = [c for c in COLS if c not in cols]
    if pk != ["Index_Name", "Date"] or missing:
        raise SystemExit(
            f"{TABLE} 가 옛 구조입니다 (기본키 {pk or '없음'}, 없는 열 {missing}). "
            "DB는 변경하지 않았습니다. 먼저 --migrate 옵션으로 한 번 실행하세요.")


def save(engine, df: pd.DataFrame) -> None:
    from sqlalchemy import text
    now = datetime.now().replace(microsecond=0)
    with engine.begin() as con:
        con.execute(text(DDL_MAIN))
        con.execute(text(DDL_VINTAGE))
        old = pd.read_sql(text(f"SELECT Index_Name, Date, Value FROM {TABLE}"), con)
        # 이름이 바뀐 지표의 옛 행 정리
        for old_name in codes.RENAMED:
            con.execute(text(f"DELETE FROM {TABLE} WHERE Index_Name = :n"), {"n": old_name})

    old["Date"] = pd.to_datetime(old["Date"]).dt.date
    old["Value"] = pd.to_numeric(old["Value"], errors="coerce").astype(float)
    m = df.merge(old, on=["Index_Name", "Date"], how="left", suffixes=("", "_old"))
    m["Value"] = m["Value"].astype(float)
    m["Value_old"] = pd.to_numeric(m["Value_old"], errors="coerce").astype(float)
    is_new = m["Value_old"].isna() & m["Value"].notna()
    changed = m["Value_old"].notna() & ~np.isclose(m["Value"], m["Value_old"], rtol=1e-9, atol=1e-12)
    vin = m.loc[is_new | changed, ["Index_Name", "Date", "Value"]].copy()
    vin["collected_at"] = now
    vin["change_type"] = np.where(is_new[is_new | changed], "new", "revised")

    st = df.copy()
    st["Report_Date"] = now.date()
    st["first_collected_at"] = now
    st["last_collected_at"] = now
    st = st[COLS].astype(object).where(pd.notna(st[COLS]), None)

    upd = ", ".join(f"{c}=VALUES({c})" for c in COLS
                    if c not in ("Index_Name", "Date", "first_collected_at"))
    with engine.begin() as con:
        con.execute(text(f"DROP TABLE IF EXISTS {STAGE}"))
        st.to_sql(STAGE, con, index=False, chunksize=2000)
        con.execute(text(
            f"INSERT INTO {TABLE} ({', '.join(COLS)}) SELECT {', '.join(COLS)} FROM {STAGE} "
            f"ON DUPLICATE KEY UPDATE {upd}"))
        con.execute(text(f"DROP TABLE IF EXISTS {STAGE}"))
        if len(vin):
            vin.to_sql(VINTAGE, con, index=False, if_exists="append", chunksize=2000)

    n_rev = int((vin["change_type"] == "revised").sum()) if len(vin) else 0
    print(f"\n저장 완료: {len(st):,}행 갱신 저장, 이력 {len(vin):,}행 (신규 {len(vin) - n_rev:,}, 수정 {n_rev:,})")


def report(df: pd.DataFrame) -> None:
    g = df.groupby("Index_Name")["Date"].agg(["min", "max", "size"])
    print(f"\n지표 {len(g)}개, {len(df):,}행. 최신 월 분포:")
    print(g["max"].value_counts().sort_index().to_string())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰기 없이 CSV 저장")
    ap.add_argument("--migrate", action="store_true", help="최초 1회: 기존 테이블 백업 후 새 구조 생성")
    ap.add_argument("--start", default=DEFAULT_START)
    args = ap.parse_args()

    print(f"수집 기간: {args.start} ~ {current_yyyymm()}")
    df = collect(start=args.start)
    report(df)

    if args.dry_run:
        out = os.path.join(HERE, f"bok_econ_v4_dryrun_{date.today():%Y%m%d}.csv")
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n[dry-run] DB 쓰기 없음. CSV: {out}")
        return

    engine = get_engine()
    if args.migrate:
        migrate(engine)
    check_structure(engine)
    save(engine, df)


if __name__ == "__main__":
    main()
