# -*- coding: utf-8 -*-
"""실전 사전 기록: 실적 발표 전에 nowcast 와 신호를 fd_live_forecast 에 남긴다.

- 모델·규칙·신호는 2a 그대로 (nowcast.run.nowcast_entity(live=...), signals.sur.build_signals, level 변형).
- 기준일 t 하나 (checkpoint 'live'). 학습은 t 까지 첫 공시된 분기 전체.
- 기록은 덮어쓰지 않는다: db.insert_fd_strict (같은 PK 가 있으면 FdWriteViolation 으로 멈춤).
- code_version: 기록 시점 git HEAD 커밋 해시. DATA/fdriver 에 커밋되지 않은 변경이 있으면 기록하지 않는다.
- 기록 전 점검: 모든 입력(드라이버, 학습 드라이버, 매출, 신호 분모 매출)의 available_at <= t. 어기면 멈춘다.
"""
import hashlib
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from . import db, entity
from .nowcast import run
from .signals import sur

T_LIVE = "fd_live_forecast"
KEY = ["entity_id", "fiscal_quarter", "asof_date", "model_id"]
LIVE_COLUMNS = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "g_hat", "g_se", "rev_hat", "rev_base",
                "coverage", "drivers_used", "nsur", "nsur_mom", "nsur_drv", "delta", "sigma", "code_version"]
REPO = Path(__file__).resolve().parents[2]

DDL_LIVE = f"""
CREATE TABLE IF NOT EXISTS `{T_LIVE}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `asof_date` DATE NOT NULL,
  `model_id` VARCHAR(40) NOT NULL,
  `g_hat` DOUBLE NULL,
  `g_se` DOUBLE NULL,
  `rev_hat` DOUBLE NULL,
  `rev_base` DOUBLE NULL,
  `coverage` DOUBLE NULL,
  `drivers_used` VARCHAR(255) NULL,
  `nsur` DOUBLE NULL,
  `nsur_mom` DOUBLE NULL,
  `nsur_drv` DOUBLE NULL,
  `delta` DOUBLE NULL,
  `sigma` DOUBLE NULL,
  `code_version` CHAR(40) NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `asof_date`, `model_id`),
  CONSTRAINT `fk_fd_live_forecast_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def create_table() -> None:
    db.execute_fd_ddl(DDL_LIVE)


def code_version(require_clean: bool = True) -> str:
    """git HEAD 해시. require_clean 이면 DATA/fdriver 에 커밋 안 된 변경(캐시 제외)이 있을 때 멈춘다."""
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    if require_clean:
        st = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "DATA/fdriver"],
                            capture_output=True, text=True, check=True).stdout
        dirty = [l for l in st.splitlines() if "__pycache__" not in l and not l.endswith(".pyc")]
        if dirty:
            raise RuntimeError("DATA/fdriver has uncommitted changes; commit before recording:\n" + "\n".join(dirty))
    return head


def _check_audit(nc_audit, sig_audit, t: pd.Timestamp) -> int:
    n = 0
    for a in nc_audit:
        for k in ("input_max_available", "train_input_max_available", "rev_max_available"):
            v = a.get(k)
            if v is not None and pd.notna(v):
                if pd.Timestamp(v) > t:
                    raise RuntimeError(f"input after t: {a['entity_id']} {a['model_id']} {k}={v}")
                n += 1
    for a in sig_audit:
        v = a.get("max_available")
        if v is not None and pd.notna(v):
            if pd.Timestamp(v) > t:
                raise RuntimeError(f"denominator input after t: {a['entity_id']} {v}")
            n += 1
    return n


def build(entity_ids: Iterable[str], quarter_end, t, version: str) -> Dict:
    """기록할 행 (DB 쓰기 없음). 반환 {"rows", "skips", "n_checked"}."""
    q, t = pd.Timestamp(quarter_end), pd.Timestamp(t).normalize()
    out, skips, n_checked = [], {}, 0
    for e in entity_ids:
        r = run.nowcast_entity(e, quarters=[], live={q: t}, audit=True)
        rows = r["rows"].copy()
        skips[e] = r["skips"]
        if rows.empty:
            continue
        nc = rows.assign(asof_date=pd.to_datetime(rows["asof_date"]))
        s = sur.build_signals(e, nc=nc, kind="ts", variants=("level",), audit=True)
        n_checked += _check_audit(r["audit"], s["audit"], t)
        sig = s["rows"][["model_id", "nsur", "nsur_mom", "nsur_drv", "delta", "sigma"]]
        rows = rows.merge(sig, on="model_id", how="left")
        rows["code_version"] = version
        out.append(rows[LIVE_COLUMNS])
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=LIVE_COLUMNS)
    return {"rows": df, "skips": skips, "n_checked": n_checked}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(entity_ids: Iterable[str], quarter_end, t, out_csv: Path) -> Dict:
    """build -> fd_live_forecast 에 덮어쓰지 않고 기록 -> 같은 내용을 CSV 로 저장하고 SHA-256 을 돌려준다."""
    version = code_version(require_clean=True)
    b = build(entity_ids, quarter_end, t, version)
    rows = b["rows"]
    create_table()
    n = db.insert_fd_strict(rows, T_LIVE, KEY)
    saved = db.read_sql(f"SELECT * FROM `{T_LIVE}` WHERE code_version = :v AND asof_date = :t",
                        {"v": version, "t": pd.Timestamp(t).date()})
    saved = saved.sort_values(KEY).reset_index(drop=True)
    out_csv = Path(out_csv)
    if out_csv.exists():
        raise FileExistsError(f"{out_csv} already exists; refusing to overwrite")
    saved.to_csv(out_csv, index=False, encoding="utf-8", float_format="%.17g", lineterminator="\n")
    return {"written": n, "rows": saved, "csv": out_csv, "sha256": sha256(out_csv), "code_version": version,
            "n_checked": b["n_checked"], "skips": b["skips"]}
