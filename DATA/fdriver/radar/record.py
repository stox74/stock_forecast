# -*- coding: utf-8 -*-
"""R1 (d): 레이더 실전 기록 (39곳). 2a live.record 를 확장한다.

record(quarter, asof)
- 레이더 표 한 장(radar.build)을 만들어, 기록 시점 값 그대로 저장한다. 나중에 덮어쓰지 않는다 (db.insert_fd_strict:
  같은 키가 하나라도 있으면 아무것도 쓰지 않고 멈춤).
  - fd_live_record   : 레이더 표 행 (PK entity_id, fiscal_quarter, asof_date) + code_version + record_id(생성 시각)
  - fd_live_forecast : 모델별 상세 행 (2a 와 같은 테이블·형식, TS·E 포함)
- 이미 그 분기 첫 공시가 나온 기업(이벤트표 첫 공시일 <= 기준일)은 사전 기록에서 빼고 목록으로 돌려준다.
- 기록 전 code_version: DATA/fdriver 에 커밋 안 된 변경이 있으면 멈춘다 (2a 와 같음).
- 같은 내용을 CSV 로 저장하고 SHA-256 을 돌려준다.
모의 실행(실적이 있는 분기)은 tables 인자로 임시 테이블(fd_tmp_*)에 쓴다.
실행: python -m DATA.fdriver.radar.record --quarter 2026Q3 [--asof YYYY-MM-DD]
"""
import argparse
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .. import db, events, live
from ..config import output_path
from . import build as B

T_RECORD = "fd_live_record"
RECORD_KEY = ["entity_id", "fiscal_quarter", "asof_date"]
NUM_COLS = ["rev_M4", "g_M4", "rev_E", "g_E", "g_B2", "g_TS", "g_M1", "rev_M1", "rev_B2", "rev_TS", "nsur_drv", "shock_x", "shock_p10", "shock_p90",
            "drv_3m_yoy", "consensus", "rev_base", "nsur_M4", "nsur_mom", "delta", "sigma", "gap_E", "gap_M4"]
STR_COLS = {"grade": 10, "name": 50, "sector": 30, "mode": 20, "main_model": 40, "M1_model": 40, "E_components": 80,
            "drv_direction": 4, "E_direction": 4, "agree": 10, "shock_alert": 40, "drv_3m_last": 7, "warnings": 120, "gap_agree": 10,
            "announce_kind": 60}


def ddl_record(table: str = T_RECORD) -> str:
    num = ",\n".join(f"  `{c}` DOUBLE NULL" for c in NUM_COLS)
    st = ",\n".join(f"  `{c}` VARCHAR({n}) NULL" for c, n in STR_COLS.items())
    return f"""
CREATE TABLE IF NOT EXISTS `{table}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `asof_date` DATE NOT NULL,
{num},
{st},
  `shock_n` INT NULL,
  `announce_date` DATE NULL,
  `consensus_asof` DATE NULL,
  `record_id` VARCHAR(20) NOT NULL,
  `code_version` CHAR(40) NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `asof_date`),
  CONSTRAINT `fk_{table}_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ddl_live_like(table: str) -> str:
    return live.DDL_LIVE.replace(f"`{live.T_LIVE}`", f"`{table}`").replace(
        f"fk_{live.T_LIVE}_entity", f"fk_{table}_entity")


def ensure_columns(table: str = T_RECORD) -> list:
    """이미 있는 기록 테이블에 NUM_COLS, STR_COLS 의 새 열(2b: gap_E, gap_M4 / K3: gap_agree)이 없으면 NULL 허용 열로
    추가한다. 기존 행의 값은 바뀌지 않는다 (새 열만 NULL)."""
    have = set(db.read_sql(f"SELECT * FROM `{table}` LIMIT 0").columns)
    add = [c for c in NUM_COLS + list(STR_COLS) if c not in have]
    for c in add:
        typ = f"VARCHAR({STR_COLS[c]})" if c in STR_COLS else "DOUBLE"
        db.execute_fd_ddl(f"ALTER TABLE `{table}` ADD COLUMN `{c}` {typ} NULL")
    return add


def announced_before(table: pd.DataFrame, t_map: Dict[str, pd.Timestamp]) -> pd.DataFrame:
    """이미 첫 공시가 나온 기업: announce_kind '실제' 이고 그 날짜 <= 기준일."""
    x = table[(table["announce_kind"] == "실제")].copy()
    x["t"] = x["entity_id"].map(t_map)
    return x[pd.to_datetime(x["announce_date"]) <= x["t"]][["entity_id", "name", "fiscal_quarter", "announce_date"]]


def prepare_rows(res: Dict, version: str, record_id: str, exclude_announced: bool):
    tab = res["table"].copy()
    tab = tab[tab["asof_date"].notna()]
    t_map = {e: pd.Timestamp(d) for e, d in zip(tab["entity_id"], tab["asof_date"])}
    done = announced_before(tab, t_map) if exclude_announced else tab.iloc[0:0][["entity_id", "name"]]
    tab = tab[~tab["entity_id"].isin(done["entity_id"])]
    rec = tab[RECORD_KEY + NUM_COLS + list(STR_COLS) + ["shock_n", "announce_date", "consensus_asof"]].copy()
    rec["record_id"], rec["code_version"] = record_id, version
    rec["shock_n"] = [int(x) if pd.notna(x) else None for x in pd.to_numeric(rec["shock_n"], errors="coerce")]
    rec = rec.astype(object).where(rec.notna(), None)
    m = res["models"]
    m = m[m["entity_id"].isin(rec["entity_id"])].copy() if len(m) else m
    if len(m):
        m["code_version"] = version
        m["asof_date"] = pd.to_datetime(m["asof_date"]).dt.date
        m = m[[c for c in live.LIVE_COLUMNS]]
    return rec, m, done


def record(quarter: str, asof_date=None, tables: Optional[Dict[str, str]] = None, require_clean: bool = True,
           exclude_announced: bool = True, out_dir: Optional[Path] = None) -> Dict:
    tables = tables or {"record": T_RECORD, "models": live.T_LIVE}
    version = live.code_version(require_clean=require_clean)
    record_id = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    res = B.build(quarter, asof_date)
    rec, models, done = prepare_rows(res, version, record_id, exclude_announced)
    db.execute_fd_ddl(ddl_record(tables["record"]))
    ensure_columns(tables["record"])
    db.execute_fd_ddl(ddl_live_like(tables["models"]) if tables["models"] != live.T_LIVE else live.DDL_LIVE)
    # 둘 다 먼저 키 충돌을 확인한 뒤 쓴다 (한쪽만 쓰이는 일이 없게)
    for df, tb, key in [(rec, tables["record"], RECORD_KEY), (models, tables["models"], live.KEY)]:
        if len(df) and len(db.existing_keys(tb, df, key)):
            raise db.FdWriteViolation(f"{tb}: 같은 키가 이미 기록됨, 덮어쓰지 않음")
    n1 = db.insert_fd_strict(rec, tables["record"], RECORD_KEY)
    n2 = db.insert_fd_strict(models, tables["models"], live.KEY) if len(models) else 0
    out_dir = Path(out_dir) if out_dir else output_path("radar", quarter, "records")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"record_{quarter}_{record_id}.csv"
    if f.exists():
        raise FileExistsError(f)
    saved = db.read_sql(f"SELECT * FROM `{tables['record']}` WHERE record_id = :r", {"r": record_id})
    saved.sort_values(RECORD_KEY).to_csv(f, index=False, encoding="utf-8", float_format="%.17g", lineterminator="\n")
    paths = B.save(res)
    return {"record_rows": n1, "model_rows": n2, "csv": f, "sha256": live.sha256(f), "code_version": version,
            "record_id": record_id, "excluded_announced": done, "radar": paths, "table": res["table"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args(argv)
    r = record(a.quarter, a.asof)
    print({k: v for k, v in r.items() if k not in ("table", "excluded_announced")})
    print("이미 발표해 기록에서 뺀 기업:")
    print(r["excluded_announced"].to_string(index=False))


if __name__ == "__main__":
    main()
