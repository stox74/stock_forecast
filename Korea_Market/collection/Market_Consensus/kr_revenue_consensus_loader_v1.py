"""
kr_revenue_consensus_loader_v1.py
DataGuide 매출액 시장 컨센서스(분기) 엑셀 → investar DB 적재

원천 파일 형태 (DataGuide 시계열 출력, 가로형)
  - 1~8행 : 출력 설정 (출력주기 '월간' 또는 '일간', 기간 등)
  - 9~14행: 코드 / 코드명 / 유형 / 아이템코드 / 아이템명 / 집계주기(예: 2026(03))
  - 15행~ : 관측일(행) x [종목 x 대상분기](열) 컨센서스 값, 단위 억원
  - 시트 하나 = 한 회계연도의 4개 분기 컨센서스를 그 해 동안 관측한 이력
  - 월간 파일과 일간 파일(2023년)을 '출력주기' 행으로 자동 구분

저장 테이블 (investar, STD-1 규격)
  kr_revenue_consensus          : 월별 표준본. 종목 x 대상분기 x 관측월 1행
                                  (일간 원천은 매월 마지막 관측일 값으로 변환)
  kr_revenue_consensus_vintage  : 표준본 값이 처음 들어오거나 바뀐 이력
  kr_revenue_consensus_daily    : 일간 원천 그대로 (현재는 2023년만, SAVE_DAILY=True일 때)

실행 예
  python kr_revenue_consensus_loader_v1.py                         # INPUT_DIR 안의 컨센서스 파일 전부 → DB
  python kr_revenue_consensus_loader_v1.py --files a.xlsx b.xlsx   # 파일 지정
  python kr_revenue_consensus_loader_v1.py --no-db --save-csv      # DB 없이 변환 결과만 CSV로 점검
  python kr_revenue_consensus_loader_v1.py --check                 # 적재된 DB 내용 점검만

여러 번 실행해도 중복 저장되지 않는다 (같은 키는 갱신).

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

# ======================================================================
# 사용자 설정
# ======================================================================
# 컨센서스 엑셀 파일이 있는 폴더. 이 폴더에서 파일명에 FILE_KEYWORD가 들어간 .xlsx를 모두 읽는다.
INPUT_DIR = r"C:\Users\82108\OneDrive\INVESTMENT\한국주식\컨센서스"
FILE_KEYWORD = "컨센서스"

# 일간 원천(2023년)을 일간 테이블에도 그대로 저장할지 여부
SAVE_DAILY = True

# --save-csv 사용 시 저장 위치
OUTPUT_DIR = r"C:\Users\82108\OneDrive\INVESTMENT\한국주식\fdriver\consensus"

# 테이블 이름
T_CONS = "kr_revenue_consensus"
T_VIN = "kr_revenue_consensus_vintage"
T_DAILY = "kr_revenue_consensus_daily"

SOURCE = "dataguide"
CURRENCY = "KRW"
CHUNK = 5000
KST = timezone(timedelta(hours=9))

# DataGuide 헤더 행 라벨 (1열 값)
LBL_FREQ = "출력주기"
LBL_CODE = "코드"
LBL_NAME = "코드명"
LBL_ITEM_CODE = "아이템코드"
LBL_ITEM_NAME = "아이템명"
LBL_PERIOD = "집계주기"


# ======================================================================
# 경로 / DB 연결
# ======================================================================
def setup_universal_paths(quiet=False):
    """스크립트 위치와 현재 폴더에서 상위로 올라가며 config.py가 있는 DATA 폴더를 찾아 sys.path에 추가."""
    starts = []
    try:
        starts.append(Path(__file__).resolve().parent)
    except NameError:
        pass
    starts.append(Path.cwd())
    for start in starts:
        for folder in [start] + list(start.parents):
            for cand in (folder, folder / "DATA"):
                if (cand / "config.py").exists():
                    p = str(cand)
                    if p not in sys.path:
                        sys.path.insert(0, p)
                    if not quiet:
                        print(f"[path] config.py 위치: {cand}")
                    return cand
    raise RuntimeError(
        "DATA/config.py를 찾지 못했습니다. 이 파일을 investment_strategy 폴더 아래"
        "(DATA 폴더 안 또는 DATA 폴더가 보이는 위치)에 두고 실행하세요."
    )


def get_engine():
    setup_universal_paths()
    import config as investar_config
    return investar_config.get_engine(investar_config.get_db_info())


def now_kst():
    """현재 시각(KST, tz 정보 없는 datetime, 초 단위)."""
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


# ======================================================================
# 엑셀 파싱
# ======================================================================
def month_end(d):
    """date/datetime → 그 달의 말일 date."""
    d = pd.Timestamp(d)
    return (d + pd.offsets.MonthEnd(0)).date()


def parse_target(label):
    """'2026(03)' → (date(2026,3,31), '2026Q1'). 해석 불가면 (None, None)."""
    m = re.match(r"^\s*(\d{4})\s*\(\s*(\d{1,2})\s*\)\s*$", str(label))
    if not m:
        return None, None
    y, mo = int(m.group(1)), int(m.group(2))
    pe = month_end(date(y, mo, 1))
    q = (mo - 1) // 3 + 1
    return pe, f"{y}Q{q}"


def parse_unit(item_name):
    """'매출액(억원)' → '억원'."""
    m = re.search(r"\(([^)]+)\)\s*$", str(item_name))
    return m.group(1) if m else None


def parse_sheet(ws, src_file):
    """DataGuide 시트 1개 → (long DataFrame, 출력주기 'M'/'D', 관측일 목록).
    long 컬럼: ticker, company_name, target_period_end, target_label, asof_date,
               consensus, unit, item_code, src_file, sheet"""
    header = {}
    data_rows = []
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        first = row[0]
        if isinstance(first, (datetime, date)):
            data_rows.append(row)
        elif isinstance(first, str) and first.strip() in (
                LBL_FREQ, LBL_CODE, LBL_NAME, LBL_ITEM_CODE, LBL_ITEM_NAME, LBL_PERIOD):
            header[first.strip()] = row
        # 그 외(설정 행, 빈 행)는 무시

    missing = [k for k in (LBL_CODE, LBL_NAME, LBL_ITEM_NAME, LBL_PERIOD) if k not in header]
    if missing:
        raise ValueError(f"{src_file}/{ws.title}: 헤더 행 없음 {missing}")

    freq_txt = str(header.get(LBL_FREQ, (None, ""))[1] or "").strip()
    if freq_txt == "월간":
        src_freq = "M"
    elif freq_txt == "일간":
        src_freq = "D"
    else:
        raise ValueError(f"{src_file}/{ws.title}: 출력주기 '{freq_txt}' 미지원 (월간/일간만 처리)")

    ncol = len(header[LBL_CODE])
    cols = []  # (열번호, ticker, 이름, 대상분기말, 라벨, 단위, 아이템코드)
    bad_item = set()
    for j in range(1, ncol):
        code = header[LBL_CODE][j]
        if code is None:
            continue
        item_name = header[LBL_ITEM_NAME][j]
        if "매출액" not in str(item_name):
            bad_item.add(str(item_name))
            continue
        pe, lab = parse_target(header[LBL_PERIOD][j])
        if pe is None:
            print(f"  [경고] {ws.title} {j}열 집계주기 해석 불가: {header[LBL_PERIOD][j]}")
            continue
        code = str(code).strip()
        ticker = code[1:] if code.startswith("A") and len(code) == 7 else code
        item_code = header[LBL_ITEM_CODE][j] if LBL_ITEM_CODE in header else None
        cols.append((j, ticker, header[LBL_NAME][j], pe, lab, parse_unit(item_name), item_code))
    if bad_item:
        print(f"  [경고] {ws.title}: 매출액이 아닌 아이템 열은 건너뜀 {sorted(bad_item)}")
    if not cols:
        raise ValueError(f"{src_file}/{ws.title}: 매출액 컨센서스 열이 없음")

    dates = [pd.Timestamp(r[0]).date() for r in data_rows]
    idx = [c[0] for c in cols]
    # 값 행렬 (관측일 x 열), 숫자 아닌 값은 NaN
    mat = np.full((len(data_rows), len(cols)), np.nan)
    for i, r in enumerate(data_rows):
        for k, j in enumerate(idx):
            v = r[j] if j < len(r) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                mat[i, k] = float(v)

    ri, ci = np.nonzero(~np.isnan(mat))
    meta = pd.DataFrame(cols, columns=["col", "ticker", "company_name", "target_period_end",
                                       "target_label", "unit", "item_code"])
    out = meta.iloc[ci].reset_index(drop=True).drop(columns="col")
    out["asof_date"] = [dates[i] for i in ri]
    out["consensus"] = mat[ri, ci]
    out["src_file"] = os.path.basename(src_file)
    out["sheet"] = ws.title
    return out, src_freq, sorted(set(dates))


def to_monthly(df, src_freq, dates):
    """관측월 키(asof_period_end)를 붙인다. 일간 원천은 매월 마지막 관측일 행만 남긴다.
    (월간 출력이 그 달 마지막 영업일 값이므로 같은 기준. 월말에 값이 비어 있으면 그 달은 없음으로 둔다)"""
    df = df.copy()
    df["asof_period_end"] = [month_end(d) for d in df["asof_date"]]
    if src_freq == "D":
        last_by_month = {}
        for d in dates:
            me = month_end(d)
            if me not in last_by_month or d > last_by_month[me]:
                last_by_month[me] = d
        keep = set(last_by_month.values())
        df = df[df["asof_date"].isin(keep)].copy()
    df["src_freq"] = src_freq
    return df


def read_files(files):
    """파일 목록 → (월별 표준 long, 일간 원천 long)"""
    monthly, daily = [], []
    for f in files:
        t0 = time.time()
        print(f"[read] {os.path.basename(f)}")
        wb = load_workbook(f, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                try:
                    df, src_freq, dates = parse_sheet(ws, f)
                except ValueError as e:
                    print(f"  [건너뜀] {e}")
                    continue
                units = sorted(df["unit"].dropna().unique())
                print(f"  시트 {ws.title:<8} 출력주기={src_freq} 관측일 {len(dates):>3}개 "
                      f"({dates[0]}~{dates[-1]})  종목 {df['ticker'].nunique():>4}  "
                      f"대상분기 {sorted(df['target_label'].unique())}  값 {len(df):>7}건  단위 {units}")
                if src_freq == "D":
                    daily.append(df)
                monthly.append(to_monthly(df, src_freq, dates))
        finally:
            wb.close()
        print(f"  읽기 {time.time() - t0:.1f}초")

    m = pd.concat(monthly, ignore_index=True) if monthly else pd.DataFrame()
    d = pd.concat(daily, ignore_index=True) if daily else pd.DataFrame()

    if not m.empty:
        # 같은 키가 여러 파일에 있으면: 관측일이 늦은 것, 같으면 월간 원천 우선
        m["_p"] = (m["src_freq"] == "M").astype(int)
        m = (m.sort_values(["ticker", "target_period_end", "asof_period_end", "asof_date", "_p"])
              .drop_duplicates(["ticker", "target_period_end", "asof_period_end"], keep="last")
              .drop(columns="_p").reset_index(drop=True))
    if not d.empty:
        d = d.drop_duplicates(["ticker", "target_period_end", "asof_date"], keep="last").reset_index(drop=True)
    return m, d


# ======================================================================
# DB
# ======================================================================
DDL_CONS = f"""
CREATE TABLE IF NOT EXISTS {T_CONS} (
    ticker             VARCHAR(10)  NOT NULL COMMENT '종목코드 6자리 (A 제외)',
    target_period_end  DATE         NOT NULL COMMENT '컨센서스 대상 분기 말일',
    asof_period_end    DATE         NOT NULL COMMENT '관측월 말일 (월별 키)',
    asof_date          DATE         NOT NULL COMMENT '실제 관측 영업일',
    company_name       VARCHAR(100) NULL,
    target_label       VARCHAR(8)   NOT NULL COMMENT '예: 2026Q1',
    target_freq        CHAR(1)      NOT NULL DEFAULT 'Q',
    freq               CHAR(1)      NOT NULL DEFAULT 'M' COMMENT '관측 주기',
    consensus          DOUBLE       NOT NULL COMMENT '매출액 컨센서스',
    unit               VARCHAR(10)  NOT NULL DEFAULT '억원',
    currency           CHAR(3)      NOT NULL DEFAULT 'KRW',
    item_code          VARCHAR(20)  NULL,
    source             VARCHAR(20)  NOT NULL DEFAULT 'dataguide',
    src_freq           CHAR(1)      NOT NULL COMMENT '원천 주기 M=월간, D=일간에서 변환',
    src_file           VARCHAR(255) NULL,
    first_collected_at DATETIME     NOT NULL,
    last_collected_at  DATETIME     NOT NULL,
    PRIMARY KEY (ticker, target_period_end, asof_period_end),
    KEY idx_asof (asof_period_end),
    KEY idx_target (target_period_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='DataGuide 매출액 분기 컨센서스 월별 관측 이력'
"""

DDL_VIN = f"""
CREATE TABLE IF NOT EXISTS {T_VIN} (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    ticker            VARCHAR(10)  NOT NULL,
    target_period_end DATE         NOT NULL,
    asof_period_end   DATE         NOT NULL,
    asof_date         DATE         NOT NULL,
    consensus         DOUBLE       NOT NULL,
    src_file          VARCHAR(255) NULL,
    change_type       VARCHAR(10)  NOT NULL COMMENT 'new / revised',
    collected_at      DATETIME     NOT NULL,
    PRIMARY KEY (id),
    KEY idx_key (ticker, target_period_end, asof_period_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='kr_revenue_consensus 신규/수정 이력'
"""

DDL_DAILY = f"""
CREATE TABLE IF NOT EXISTS {T_DAILY} (
    ticker             VARCHAR(10)  NOT NULL,
    target_period_end  DATE         NOT NULL,
    asof_date          DATE         NOT NULL,
    company_name       VARCHAR(100) NULL,
    target_label       VARCHAR(8)   NOT NULL,
    consensus          DOUBLE       NOT NULL,
    unit               VARCHAR(10)  NOT NULL DEFAULT '억원',
    currency           CHAR(3)      NOT NULL DEFAULT 'KRW',
    source             VARCHAR(20)  NOT NULL DEFAULT 'dataguide',
    src_file           VARCHAR(255) NULL,
    first_collected_at DATETIME     NOT NULL,
    last_collected_at  DATETIME     NOT NULL,
    PRIMARY KEY (ticker, target_period_end, asof_date),
    KEY idx_asof (asof_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='DataGuide 매출액 분기 컨센서스 일간 원천'
"""

# MariaDB는 UPDATE 절을 왼쪽부터 평가하므로 asof_date 갱신을 조건 컬럼들 뒤에 둔다.
# 이미 저장된 값보다 관측일이 이르지 않을 때만 덮어쓴다.
UPSERT_CONS = f"""
INSERT INTO {T_CONS}
    (ticker, target_period_end, asof_period_end, asof_date, company_name, target_label,
     target_freq, freq, consensus, unit, currency, item_code, source, src_freq, src_file,
     first_collected_at, last_collected_at)
VALUES
    (:ticker, :target_period_end, :asof_period_end, :asof_date, :company_name, :target_label,
     'Q', 'M', :consensus, :unit, '{CURRENCY}', :item_code, '{SOURCE}', :src_freq, :src_file,
     :ts, :ts)
ON DUPLICATE KEY UPDATE
    company_name = IF(VALUES(asof_date) >= asof_date, COALESCE(VALUES(company_name), company_name), company_name),
    consensus    = IF(VALUES(asof_date) >= asof_date, VALUES(consensus), consensus),
    unit         = IF(VALUES(asof_date) >= asof_date, VALUES(unit), unit),
    item_code    = IF(VALUES(asof_date) >= asof_date, COALESCE(VALUES(item_code), item_code), item_code),
    src_freq     = IF(VALUES(asof_date) >= asof_date, VALUES(src_freq), src_freq),
    src_file     = IF(VALUES(asof_date) >= asof_date, VALUES(src_file), src_file),
    asof_date    = GREATEST(asof_date, VALUES(asof_date)),
    first_collected_at = LEAST(first_collected_at, VALUES(first_collected_at)),
    last_collected_at  = GREATEST(last_collected_at, VALUES(last_collected_at))
"""

INSERT_VIN = f"""
INSERT INTO {T_VIN}
    (ticker, target_period_end, asof_period_end, asof_date, consensus, src_file, change_type, collected_at)
VALUES
    (:ticker, :target_period_end, :asof_period_end, :asof_date, :consensus, :src_file, :change_type, :ts)
"""

UPSERT_DAILY = f"""
INSERT INTO {T_DAILY}
    (ticker, target_period_end, asof_date, company_name, target_label, consensus,
     unit, currency, source, src_file, first_collected_at, last_collected_at)
VALUES
    (:ticker, :target_period_end, :asof_date, :company_name, :target_label, :consensus,
     :unit, '{CURRENCY}', '{SOURCE}', :src_file, :ts, :ts)
ON DUPLICATE KEY UPDATE
    company_name = COALESCE(VALUES(company_name), company_name),
    consensus    = VALUES(consensus),
    unit         = VALUES(unit),
    src_file     = VALUES(src_file),
    first_collected_at = LEAST(first_collected_at, VALUES(first_collected_at)),
    last_collected_at  = GREATEST(last_collected_at, VALUES(last_collected_at))
"""


def ensure_tables(engine):
    from sqlalchemy import text
    with engine.begin() as cx:
        cx.execute(text(DDL_CONS))
        cx.execute(text(DDL_VIN))
        if SAVE_DAILY:
            cx.execute(text(DDL_DAILY))


def _same(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)))


def _records(df, cols, ts):
    recs = []
    for row in df[cols].itertuples(index=False):
        r = {}
        for c, v in zip(cols, row):
            if isinstance(v, float) and np.isnan(v):
                v = None
            elif isinstance(v, np.generic):
                v = v.item()
            r[c] = v
        r["ts"] = ts
        recs.append(r)
    return recs


def save_monthly(engine, m, ts):
    """월별 표준본 upsert + 신규/수정 이력. 반환: (저장, 신규, 수정)"""
    from sqlalchemy import text
    cols = ["ticker", "target_period_end", "asof_period_end", "asof_date", "company_name",
            "target_label", "consensus", "unit", "item_code", "src_freq", "src_file"]
    m = m.copy()
    m["unit"] = m["unit"].fillna("억원")
    recs = _records(m, cols, ts)

    a, b = min(m["asof_period_end"]), max(m["asof_period_end"])
    with engine.begin() as cx:
        existing = {}
        q = text(f"SELECT ticker, target_period_end, asof_period_end, asof_date, consensus "
                 f"FROM {T_CONS} WHERE asof_period_end BETWEEN :a AND :b")
        for tk, tp, ap, ad, v in cx.execute(q, {"a": a, "b": b}):
            existing[(tk, tp, ap)] = (ad, v)

        vin = []
        for r in recs:
            key = (r["ticker"], r["target_period_end"], r["asof_period_end"])
            old = existing.get(key)
            if old is None:
                vin.append(dict(r, change_type="new"))
            elif r["asof_date"] >= old[0] and not _same(old[1], r["consensus"]):
                vin.append(dict(r, change_type="revised"))

        for i in range(0, len(recs), CHUNK):
            cx.execute(text(UPSERT_CONS), recs[i:i + CHUNK])
        vcols = ["ticker", "target_period_end", "asof_period_end", "asof_date", "consensus",
                 "src_file", "change_type", "ts"]
        vrecs = [{k: v[k] for k in vcols} for v in vin]
        for i in range(0, len(vrecs), CHUNK):
            cx.execute(text(INSERT_VIN), vrecs[i:i + CHUNK])
    n_new = sum(1 for v in vin if v["change_type"] == "new")
    return len(recs), n_new, len(vin) - n_new


def save_daily(engine, d, ts):
    from sqlalchemy import text
    cols = ["ticker", "target_period_end", "asof_date", "company_name", "target_label",
            "consensus", "unit", "src_file"]
    d = d.copy()
    d["unit"] = d["unit"].fillna("억원")
    recs = _records(d, cols, ts)
    with engine.begin() as cx:
        for i in range(0, len(recs), CHUNK):
            cx.execute(text(UPSERT_DAILY), recs[i:i + CHUNK])
    return len(recs)


# ======================================================================
# 점검
# ======================================================================
def summarize(m, title="변환 결과"):
    print(f"\n===== {title} =====")
    if m.empty:
        print("데이터 없음")
        return
    t = m.copy()
    t["asof_year"] = [d.year for d in t["asof_period_end"]]
    t["target_year"] = [d.year for d in t["target_period_end"]]
    g = t.groupby("target_year").agg(
        rows=("consensus", "size"),
        tickers=("ticker", "nunique"),
        asof_first=("asof_period_end", "min"),
        asof_last=("asof_period_end", "max"),
        asof_months=("asof_period_end", "nunique"),
    )
    if "src_freq" in t.columns:
        g["src_freq"] = t.groupby("target_year")["src_freq"].agg(lambda s: ",".join(sorted(set(s))))
    print(g.to_string())
    sample = t[t["ticker"] == "005930"]
    if not sample.empty:
        last_year = sample["target_year"].max()
        sample = sample[sample["target_year"] == last_year]
        pv = sample.pivot_table(index="asof_period_end", columns="target_label",
                                values="consensus", aggfunc="last")
        print(f"\n[예시] 삼성전자 {last_year}년 분기 매출 컨센서스 (억원), 행=관측월, 열=대상분기")
        with pd.option_context("display.max_rows", 80, "display.width", 200):
            print(pv.round(0).to_string())


def check_db(engine):
    sql = f"""SELECT ticker, target_period_end, target_label, asof_period_end, asof_date,
                     consensus, src_freq FROM {T_CONS}"""
    m = pd.read_sql(sql, engine)
    for c in ("target_period_end", "asof_period_end", "asof_date"):
        m[c] = pd.to_datetime(m[c]).dt.date
    summarize(m, f"DB {T_CONS}")
    vin = pd.read_sql(f"SELECT change_type, COUNT(*) AS n FROM {T_VIN} GROUP BY change_type", engine)
    print(f"\n[{T_VIN}]\n{vin.to_string(index=False)}")
    if SAVE_DAILY:
        try:
            dd = pd.read_sql(f"SELECT COUNT(*) AS n, MIN(asof_date) AS first_day, MAX(asof_date) AS last_day, "
                             f"COUNT(DISTINCT ticker) AS tickers FROM {T_DAILY}", engine)
            print(f"\n[{T_DAILY}]\n{dd.to_string(index=False)}")
        except Exception as e:
            print(f"[{T_DAILY}] 조회 실패: {e}")


# ======================================================================
# 실행
# ======================================================================
def find_files(input_dir):
    pats = [os.path.join(input_dir, f"*{FILE_KEYWORD}*.xlsx")]
    files = []
    for p in pats:
        files += [f for f in glob.glob(p) if not os.path.basename(f).startswith("~$")]
    return sorted(set(files))


def main(argv=None):
    ap = argparse.ArgumentParser(description="DataGuide 매출 컨센서스 → investar")
    ap.add_argument("--files", nargs="*", help="읽을 엑셀 파일 (생략 시 INPUT_DIR 탐색)")
    ap.add_argument("--input-dir", default=INPUT_DIR)
    ap.add_argument("--no-db", action="store_true", help="DB에 저장하지 않음")
    ap.add_argument("--save-csv", action="store_true", help="변환 결과를 CSV로 저장")
    ap.add_argument("--output", default=OUTPUT_DIR)
    ap.add_argument("--check", action="store_true", help="DB 점검만 수행")
    args = ap.parse_args(argv)

    if args.check:
        check_db(get_engine())
        return

    files = args.files or find_files(args.input_dir)
    if not files:
        print(f"읽을 파일이 없습니다. INPUT_DIR 확인: {args.input_dir}")
        return
    print("[files]")
    for f in files:
        print("  ", f)

    m, d = read_files(files)
    summarize(m)
    if not d.empty:
        print(f"\n일간 원천: {len(d):,}건, 관측일 {d['asof_date'].min()}~{d['asof_date'].max()}")

    if args.save_csv:
        os.makedirs(args.output, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        p1 = os.path.join(args.output, f"{T_CONS}_{stamp}.csv")
        m.to_csv(p1, index=False, encoding="utf-8-sig")
        print(f"[csv] {p1}")
        if not d.empty:
            p2 = os.path.join(args.output, f"{T_DAILY}_{stamp}.csv")
            d.to_csv(p2, index=False, encoding="utf-8-sig")
            print(f"[csv] {p2}")

    if args.no_db:
        print("\n--no-db: DB 저장 생략")
        return

    engine = get_engine()
    ensure_tables(engine)
    ts = now_kst()
    t0 = time.time()
    n, n_new, n_rev = save_monthly(engine, m, ts)
    print(f"\n[db] {T_CONS}: 저장 {n:,}건 (신규 {n_new:,}, 수정 {n_rev:,})  {time.time() - t0:.1f}초")
    if SAVE_DAILY and not d.empty:
        t0 = time.time()
        nd = save_daily(engine, d, ts)
        print(f"[db] {T_DAILY}: 저장 {nd:,}건  {time.time() - t0:.1f}초")
    check_db(engine)


if __name__ == "__main__":
    main()
