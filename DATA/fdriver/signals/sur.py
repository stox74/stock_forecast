# -*- coding: utf-8 -*-
"""SUR, NSUR -> fd_signal (2a 명세서 6절, v6 신호 정의).

fd_nowcast 의 모든 행(기업 x 분기 x 기준일 x 모델)에 대해 level, log 두 변형을 계산한다.
- nsur      = (예측 - 기대) / sigma            (level: 매출, log: 성장률)
- sur       = (첫 공시 매출 - 기대) / sigma     (같은 분모, 같은 기준일 t 의 기대)
- nsur_mom  = (B2 예측 - 기대) / sigma          (같은 기준일, 같은 코로나 변형의 B2)
- nsur_drv  = (모델 예측 - B2 예측) / sigma     (log: (ln 모델 - ln B2) / sigma)
  -> nsur = nsur_mom + nsur_drv. v6 정의는 M4 에 대한 것이지만 모든 모델에 같은 방식으로 저장한다
     (B2 는 drv = 0, M2 는 'M2 와 B2 의 차이').
- event_date: fd_earnings_event 의 첫 공시일 (day 0). 이벤트가 없는 분기는 NULL.
주 신호는 pre5 기준일의 MAIN_SIGNAL_MODEL (M4:combo / M4), 대조군은 B3. 비교용 M2 계열은 COMPARE_MODEL.
쓰기는 db.execute_fd_ddl, db.write_fd 로만 한다.
"""
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from .. import db, entity
from ..events import first_disclosure, fiscal_quarter_end, read_events
from . import expectation as ex

T_SIGNAL = "fd_signal"
MAIN_SIGNAL_MODEL = {"005930": "M4:combo", "039130": "M4:combo", "267270": "M4:842952", "278470": "M4:amazon",
                     "006040": "B3", "003350": "B3"}
COMPARE_MODEL = {"005930": "M2", "039130": "M2", "267270": "M3:842952", "278470": "M3:amazon"}
SIGNAL_COLUMNS = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "variant", "checkpoint", "expectation",
                  "nsur", "nsur_mom", "nsur_drv", "sur", "delta", "sigma", "n_hist", "event_date"]

DDL_SIGNAL = f"""
CREATE TABLE IF NOT EXISTS `{T_SIGNAL}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `asof_date` DATE NOT NULL,
  `model_id` VARCHAR(40) NOT NULL,
  `variant` VARCHAR(10) NOT NULL,
  `checkpoint` VARCHAR(10) NULL,
  `expectation` VARCHAR(10) NOT NULL,
  `nsur` DOUBLE NULL,
  `nsur_mom` DOUBLE NULL,
  `nsur_drv` DOUBLE NULL,
  `sur` DOUBLE NULL,
  `delta` DOUBLE NULL,
  `sigma` DOUBLE NULL,
  `n_hist` INT NULL,
  `event_date` DATE NULL,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `asof_date`, `model_id`, `variant`),
  KEY `idx_checkpoint` (`checkpoint`),
  CONSTRAINT `fk_fd_signal_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def read_nowcast(entity_id: str) -> pd.DataFrame:
    nc = db.read_sql("SELECT entity_id, fiscal_quarter, asof_date, model_id, checkpoint, g_hat, rev_hat, rev_base, "
                     "rev_actual_first, drivers_used FROM fd_nowcast WHERE entity_id = :e", {"e": entity_id})
    nc["asof_date"] = pd.to_datetime(nc["asof_date"])
    return nc


def event_dates(entity_id: str, fye: int = 12) -> pd.Series:
    """분기말 -> 첫 공시일 (fd_earnings_event)."""
    fd = first_disclosure(read_events([entity_id]), {entity_id: fye})
    return fd.set_index("period_end")["first_dt"]


def _suffix(model_id: str) -> str:
    return ":excovid" if model_id.endswith(":excovid") else ""


def build_signals(entity_id: str, nc: Optional[pd.DataFrame] = None, kind: str = "ts",
                  variants: Iterable[str] = ex.VARIANTS, ctx: Optional[ex.Context] = None,
                  audit: bool = False) -> Dict:
    """반환 {"rows": DataFrame(SIGNAL_COLUMNS), "base_mismatch": int, "audit": list}."""
    info = entity.entity_info([entity_id])
    fye = int(info["fiscal_year_end"].iloc[0])
    ctx = ctx or ex.Context(entity_id, fye=fye)
    nc = read_nowcast(entity_id) if nc is None else nc
    nc = nc[nc["drivers_used"].fillna("") != "fallback:B3"]
    evd = event_dates(entity_id, fye)
    rows, aud, mismatch = [], [], 0
    for (fq, t), g in nc.groupby(["fiscal_quarter", "asof_date"]):
        q = fiscal_quarter_end(fq, fye)
        ev = evd.get(q)
        b2 = {s: g.loc[g["model_id"] == "B2" + s, "rev_hat"] for s in ("", ":excovid")}
        b2 = {s: (float(v.iloc[0]) if len(v) else np.nan) for s, v in b2.items()}
        for variant in variants:
            e = ex.expectation(entity_id, q, t, kind=kind, variant=variant, ctx=ctx)
            if audit:
                aud.append({"entity_id": entity_id, "q": q, "t": t, "variant": variant, **e})
            for r in g.itertuples(index=False):
                if e["base"] is not None and r.rev_base and abs(e["base"] / r.rev_base - 1) > 1e-9:
                    mismatch += 1
                rb2 = b2[_suffix(r.model_id)]
                nsur = ex.surprise(r.rev_hat, e, variant)
                mom = ex.surprise(rb2, e, variant)
                if pd.isna(rb2) or pd.isna(e["sigma"]):
                    drv = np.nan
                elif variant == "level":
                    drv = (r.rev_hat - rb2) / e["sigma"]
                else:
                    drv = (np.log(r.rev_hat) - np.log(rb2)) / e["sigma"]
                rows.append({"entity_id": entity_id, "fiscal_quarter": fq, "asof_date": t.date(), "model_id": r.model_id,
                             "variant": variant, "checkpoint": r.checkpoint, "expectation": kind, "nsur": nsur,
                             "nsur_mom": mom, "nsur_drv": float(drv) if pd.notna(drv) else np.nan,
                             "sur": ex.surprise(r.rev_actual_first, e, variant),
                             "delta": e["delta"], "sigma": e["sigma"], "n_hist": int(e["n_hist"]),
                             "event_date": None if ev is None or pd.isna(ev) else pd.Timestamp(ev).date()})
    return {"rows": pd.DataFrame(rows, columns=SIGNAL_COLUMNS), "base_mismatch": mismatch, "audit": aud}


def create_table() -> None:
    db.execute_fd_ddl(DDL_SIGNAL)


def run_and_load(entity_ids: Optional[Iterable[str]] = None) -> Dict:
    ids = list(entity_ids or entity.PILOTS)
    res = {e: build_signals(e) for e in ids}
    allrows = pd.concat([r["rows"] for r in res.values()], ignore_index=True)
    allrows = allrows[allrows["nsur"].notna() | allrows["sur"].notna()]
    create_table()
    n = db.write_fd(allrows, T_SIGNAL, ["entity_id", "fiscal_quarter", "asof_date", "model_id", "variant"])
    return {"rows": allrows, "results": res, "written": n}


def read_signals(entity_ids: Optional[Iterable[str]] = None, expectation: str = "ts") -> pd.DataFrame:
    sql = f"SELECT * FROM `{T_SIGNAL}` WHERE expectation = :x"
    params = {"x": expectation}
    if entity_ids:
        frag, p = db.in_clause("e", list(entity_ids))
        sql += f" AND entity_id IN {frag}"
        params.update(p)
    s = db.read_sql(sql, params)
    s["asof_date"] = pd.to_datetime(s["asof_date"])
    s["event_date"] = pd.to_datetime(s["event_date"])
    return s
