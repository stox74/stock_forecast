# -*- coding: utf-8 -*-
"""2b 작업 2: 비교표 fd_eval_2b (명세서 5절). 39곳 x 2022Q1~2026Q2 전 행, 판정 제외 행도 flags 로 남긴다.

- 키 (ticker, quarter). 같은 키가 테이블에 하나라도 있으면 아무것도 쓰지 않고 멈춘다 (db.insert_fd_strict).
- run_id = 생성 시각 (YYYYMMDD_HHMMSS). CSV 사본: OUTPUT_ROOT/2b/fd_eval_2b_<run_id>.csv
- 금액은 원. gap·surprise·rev_c 는 비율 (0.05 = 5%).
실행: python -m DATA.fdriver.eval2b.k2_table
"""
from datetime import datetime

import pandas as pd

from .. import db
from ..config import output_path
from . import frame as F

T_EVAL = "fd_eval_2b"
KEY = ["ticker", "quarter"]
NUM = ["actual", "n_E", "n_M4", "n_B2", "c_pre5", "c_final", "c_after", "c_pre5_daily", "c_final_daily",
       "gap_E", "gap_M4", "surprise", "rev_c", "gap_E_daily", "surprise_daily", "nsur_drv", "basis_ratio_median"]
DATES = ["pre5_date", "announce_date", "c_pre5_asof", "c_final_asof"]
COLS = KEY + ["name", "grade", "main_model"] + DATES + NUM + ["in_judgement", "flags", "run_id"]


def ddl(table: str = T_EVAL) -> str:
    num = ",\n".join(f"  `{c}` DOUBLE NULL" for c in NUM)
    dt = ",\n".join(f"  `{c}` DATE NULL" for c in DATES)
    return f"""
CREATE TABLE IF NOT EXISTS `{table}` (
  `ticker` VARCHAR(20) NOT NULL,
  `quarter` CHAR(6) NOT NULL,
  `name` VARCHAR(50) NULL,
  `grade` VARCHAR(10) NULL,
  `main_model` VARCHAR(40) NULL,
{dt},
{num},
  `in_judgement` TINYINT NOT NULL,
  `flags` VARCHAR(80) NULL,
  `run_id` VARCHAR(20) NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`ticker`, `quarter`),
  CONSTRAINT `fk_{table}_entity` FOREIGN KEY (`ticker`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def to_rows(d: pd.DataFrame, run_id: str) -> pd.DataFrame:
    x = d.copy()
    x["flags"] = F.flags_text(x)
    x["run_id"] = run_id
    x["in_judgement"] = x["in_judgement"].astype(int)
    for c in DATES:
        x[c] = pd.to_datetime(x[c]).dt.date
    x = x[COLS]
    return x.astype(object).where(x.notna(), None)


def load(table: str = T_EVAL) -> pd.DataFrame:
    d = db.read_sql(f"SELECT * FROM `{table}`")
    for c in DATES:
        d[c] = pd.to_datetime(d[c])
    d["in_judgement"] = d["in_judgement"].astype(bool)
    return d


def main(table: str = T_EVAL):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = F.build_events()
    rows = to_rows(d, run_id)
    db.execute_fd_ddl(ddl(table))
    n = db.insert_fd_strict(rows, table, KEY)        # 같은 키가 있으면 FdWriteViolation 으로 멈춤
    path = output_path("2b") / f"{table}_{run_id}.csv"
    rows.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"{table}: {n} rows, run_id {run_id}, in_judgement {int(d['in_judgement'].sum())}, csv {path}")
    return run_id


if __name__ == "__main__":
    main()
