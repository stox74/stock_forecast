# -*- coding: utf-8 -*-
"""2b 작업 2: 비교표 fd_eval_2b (명세서 5절). 39곳 x 2022Q1~2026Q2 전 행, 판정 제외 행도 flags 로 남긴다.

- 키 (ticker, quarter, run_id). 같은 키가 테이블에 하나라도 있으면 아무것도 쓰지 않고 멈춘다 (db.insert_fd_strict).
  K2: 새 run_id 로 다시 만들 수 있도록 키에 run_id 를 넣었다 (K1 행은 그대로 남음). ensure_schema 가 K1 테이블의
  기본키를 (ticker, quarter, run_id) 로 바꾸고 새 열을 NULL 허용으로 추가한다 (기존 값은 바뀌지 않음).
- run_id = 생성 시각 (YYYYMMDD_HHMMSS). CSV 사본: OUTPUT_ROOT/2b/fd_eval_2b_<run_id>.csv
- load(run_id=None): 지정 run, 없으면 가장 최근 run.
- 금액은 원. gap·surprise·rev_c 는 비율 (0.05 = 5%).
실행: python -m DATA.fdriver.eval2b.k2_table
"""
from datetime import datetime
from typing import Optional

import pandas as pd

from .. import db
from ..config import output_path
from . import frame as F

T_EVAL = "fd_eval_2b"
KEY = ["ticker", "quarter", "run_id"]
NUM = ["actual", "actual_first_raw", "n_E", "n_M4", "n_B2", "n_TS", "n_drv", "c_pre5", "c_final", "c_after",
       "c_final_prev", "c_pre5_daily", "c_final_daily", "gap_E", "gap_M4", "surprise", "rev_c", "surprise_prev",
       "gap_E_daily", "surprise_daily", "nsur_drv", "basis_ratio_median"]
DATES = ["pre5_date", "announce_date", "c_pre5_asof", "c_final_asof", "c_final_prev_asof"]
STR = {"name": 50, "grade": 10, "main_model": 40, "drv_model": 40}
COLS = ["ticker", "quarter"] + list(STR) + DATES + NUM + ["in_judgement", "flags", "run_id"]
FLAGS_LEN = 160


def ddl(table: str = T_EVAL) -> str:
    st = ",\n".join(f"  `{c}` VARCHAR({n}) NULL" for c, n in STR.items())
    num = ",\n".join(f"  `{c}` DOUBLE NULL" for c in NUM)
    dt = ",\n".join(f"  `{c}` DATE NULL" for c in DATES)
    return f"""
CREATE TABLE IF NOT EXISTS `{table}` (
  `ticker` VARCHAR(20) NOT NULL,
  `quarter` CHAR(6) NOT NULL,
{st},
{dt},
{num},
  `in_judgement` TINYINT NOT NULL,
  `flags` VARCHAR({FLAGS_LEN}) NULL,
  `run_id` VARCHAR(20) NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`ticker`, `quarter`, `run_id`),
  CONSTRAINT `fk_{table}_entity` FOREIGN KEY (`ticker`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def primary_key(table: str) -> list:
    k = db.read_sql("SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = :t AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION", {"t": table})
    return k["COLUMN_NAME"].tolist()


def ensure_schema(table: str = T_EVAL) -> list:
    """테이블을 만들거나 (없을 때), K1 형식이면 기본키·열을 K2 형식으로 맞춘다. 반환: 바꾼 내용 목록."""
    db.execute_fd_ddl(ddl(table))
    done = []
    have = set(db.read_sql(f"SELECT * FROM `{table}` LIMIT 0").columns)
    for c in list(STR) + DATES + NUM:
        if c not in have:
            typ = f"VARCHAR({STR[c]})" if c in STR else ("DATE" if c in DATES else "DOUBLE")
            db.execute_fd_ddl(f"ALTER TABLE `{table}` ADD COLUMN `{c}` {typ} NULL")
            done.append(f"add {c}")
    if primary_key(table) != KEY:
        db.execute_fd_ddl(f"ALTER TABLE `{table}` DROP PRIMARY KEY, ADD PRIMARY KEY (`ticker`, `quarter`, `run_id`)")
        done.append("primary key -> ticker, quarter, run_id")
    width = db.read_sql("SELECT CHARACTER_MAXIMUM_LENGTH n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                        "AND TABLE_NAME = :t AND COLUMN_NAME = 'flags'", {"t": table})["n"].iloc[0]
    if int(width) < FLAGS_LEN:
        db.execute_fd_ddl(f"ALTER TABLE `{table}` MODIFY COLUMN `flags` VARCHAR({FLAGS_LEN}) NULL")
        done.append(f"flags VARCHAR({int(width)}) -> VARCHAR({FLAGS_LEN})")
    return done


def to_rows(d: pd.DataFrame, run_id: str) -> pd.DataFrame:
    x = d.copy()
    x["flags"] = F.flags_text(x)
    x["run_id"] = run_id
    x["in_judgement"] = x["in_judgement"].astype(int)
    for c in DATES:
        x[c] = pd.to_datetime(x[c]).dt.date
    x = x[COLS]
    return x.astype(object).where(x.notna(), None)


def runs(table: str = T_EVAL) -> pd.DataFrame:
    return db.read_sql(f"SELECT run_id, COUNT(*) n, SUM(in_judgement) n_judgement FROM `{table}` GROUP BY run_id "
                       "ORDER BY run_id")


def load(run_id: Optional[str] = None, table: str = T_EVAL) -> pd.DataFrame:
    run_id = run_id or runs(table)["run_id"].iloc[-1]
    d = db.read_sql(f"SELECT * FROM `{table}` WHERE run_id = :r", {"r": run_id})
    for c in DATES:
        d[c] = pd.to_datetime(d[c])
    d["in_judgement"] = d["in_judgement"].astype(bool)
    f = d["flags"].fillna("")
    for k in F.EXCLUDE_FLAGS + F.SHOW_FLAGS:
        d[k] = f.str.split(";").apply(lambda s, k=k: k in s)
    return d


def main(table: str = T_EVAL):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = F.build_events()
    rows = to_rows(d, run_id)
    changed = ensure_schema(table)
    n = db.insert_fd_strict(rows, table, KEY)        # 같은 키가 있으면 FdWriteViolation 으로 멈춤
    path = output_path("2b") / f"{table}_{run_id}.csv"
    rows.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"{table}: {n} rows, run_id {run_id}, in_judgement {int(d['in_judgement'].sum())}, csv {path}")
    print("schema changes:", changed)
    return run_id, d


if __name__ == "__main__":
    main()
