# -*- coding: utf-8 -*-
"""컨센서스 읽기 (2a 명세서 8절). 2a 에서는 빈 테이블과 읽기 함수만 둔다.

fd_consensus (PK: entity_id, fiscal_quarter, item, asof_date, source)
read_consensus(entity_ids, item="revenue", asof=None)
  asof 이전(당일 포함)의 가장 최근 스냅샷을 실적 분기별로 하나 돌려준다. 같은 날 원천이 여럿이면
  source 이름 순으로 첫 번째. 테이블이 없거나 비어 있으면 빈 DataFrame (오류 없음).
  결과에 available_at = asof_date, asof_quality = 'A' 를 붙인다.
"""
from typing import Iterable, Optional

import pandas as pd

from .. import db

T_CONSENSUS = "fd_consensus"
COLUMNS = ["entity_id", "fiscal_quarter", "item", "asof_date", "mean", "median", "high", "low", "n_analysts",
           "source", "available_at", "asof_quality"]

DDL_CONSENSUS = f"""
CREATE TABLE IF NOT EXISTS `{T_CONSENSUS}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `item` VARCHAR(20) NOT NULL,
  `asof_date` DATE NOT NULL,
  `mean` DOUBLE NULL,
  `median` DOUBLE NULL,
  `high` DOUBLE NULL,
  `low` DOUBLE NULL,
  `n_analysts` INT NULL,
  `source` VARCHAR(20) NOT NULL,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `item`, `asof_date`, `source`),
  CONSTRAINT `fk_fd_consensus_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def create_table() -> None:
    db.execute_fd_ddl(DDL_CONSENSUS)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def read_consensus(entity_ids: Iterable[str], item: str = "revenue", asof=None) -> pd.DataFrame:
    ids = [str(x) for x in entity_ids]
    if not ids or not db.table_exists(T_CONSENSUS):
        return _empty()
    frag, params = db.in_clause("e", ids)
    sql = (f"SELECT entity_id, fiscal_quarter, item, asof_date, mean, median, high, low, n_analysts, source "
           f"FROM `{T_CONSENSUS}` WHERE entity_id IN {frag} AND item = :item")
    params["item"] = item
    if asof is not None:
        sql += " AND asof_date <= :asof"
        params["asof"] = pd.Timestamp(asof).date()
    raw = db.read_sql(sql, params)
    if raw.empty:
        return _empty()
    raw["asof_date"] = pd.to_datetime(raw["asof_date"])
    raw = raw.sort_values(["entity_id", "fiscal_quarter", "asof_date", "source"], ascending=[True, True, False, True])
    out = raw.drop_duplicates(["entity_id", "fiscal_quarter", "item"], keep="first").copy()
    out["available_at"] = out["asof_date"]
    out["asof_quality"] = "A"
    return out[COLUMNS].reset_index(drop=True)
