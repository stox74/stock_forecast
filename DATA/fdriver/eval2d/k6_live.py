# -*- coding: utf-8 -*-
"""2d 작업 6: 실전 사전 기록 (명세서 2d v3 3절 실전, 사전 등록 docs/preregistration_2d_live.md 커밋 4b0a4b3).
쓰기는 fd_live_2d (덮어쓰기 불가, db.insert_fd_strict) 만.

- 대상: fd_driver_map_2d status driver + ts_only (73곳) 중, 기록 분기의 첫 공시가 기준일까지 없는 기업.
  첫 공시 확인은 fd_earnings_event (기록 직전 events.build_and_load 로 갱신).
- 기준일 asof: 기록하는 날 (live_t). 그날까지 공표된 자료만 쓴다 (매출은 발표일 <= asof 인 분기, 드라이버는 available_at
  <= asof). 계산은 k4_forecast.run_entity 실전 모드와 같다 (E, B2, TS, M1, E_noM1, E_fill, OP_hat, OP_naive).
- 기록: (entity_id, quarter) 마다 한 번. 같은 키가 이미 있으면 아무것도 쓰지 않고 멈춘다 (insert_fd_strict).
  recorded_at = 기록 시각. 평가에서는 recorded_at 이 첫 공시일보다 앞선 행만 쓴다 (사전 등록 1절).
- 증거: 기록 CSV 의 SHA-256, 예측 코드(k4_forecast.py, k6_live.py) 파일 SHA-256, 사전 등록 커밋 해시를 행과 요약에 남긴다.
실행: python -m DATA.fdriver.eval2d.k6_live --quarter 2026Q3 [--asof 2026-10-18] [--dry]
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import db, events
from ..config import output_path
from ..nowcast.run import _calendar
from . import k3_oprule as K3
from . import k4_forecast as K4
from .k2_targets import read_adj

T_LIVE = "fd_live_2d"
PREREG_COMMIT = "4b0a4b3d311adf18b0f9bbddb40df27e667f9ff7"
MAPPING_FILE = "fdriver_2d_driver_mapping_v6_approved.xlsx"

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_LIVE}` (
  entity_id VARCHAR(20) NOT NULL,
  quarter CHAR(6) NOT NULL,
  period_end DATE NOT NULL,
  asof_date DATE NOT NULL,
  recorded_at DATETIME NOT NULL,
  status VARCHAR(10) NOT NULL,
  rule VARCHAR(10) NULL,
  drv_code VARCHAR(60) NULL,
  drv_coverage DOUBLE NULL,
  drv_months VARCHAR(20) NULL,
  rev_base DOUBLE NULL,
  g_M1 DOUBLE NULL, g_B2 DOUBLE NULL, g_TS DOUBLE NULL, g_E DOUBLE NULL, g_E_noM1 DOUBLE NULL, g_E_fill DOUBLE NULL,
  M1 DOUBLE NULL, B2 DOUBLE NULL, TS DOUBLE NULL, E DOUBLE NULL, E_noM1 DOUBLE NULL, E_fill DOUBLE NULL,
  n_filled INT NULL,
  E_spread DOUBLE NULL, E_spread_from VARCHAR(10) NULL,
  gap_E_vs_B2 DOUBLE NULL,
  e_label VARCHAR(80) NULL, ts_label VARCHAR(80) NULL, m1_n_train INT NULL,
  OP_hat DOUBLE NULL, OP_naive DOUBLE NULL,
  op_a DOUBLE NULL, op_b DOUBLE NULL, cost_ratio_hat DOUBLE NULL, sga_hat DOUBLE NULL, c_hat DOUBLE NULL,
  op_n_models VARCHAR(20) NULL, op_note VARCHAR(120) NULL,
  coverage_class VARCHAR(10) NULL,
  flag_jump TINYINT NULL, flag_basis TINYINT NULL, flag_spread TINYINT NULL, flag_q4 TINYINT NULL,
  mapping_file VARCHAR(100) NOT NULL,
  prereg_commit CHAR(40) NOT NULL,
  code_sha256 CHAR(64) NOT NULL,
  run_id VARCHAR(20) NOT NULL,
  PRIMARY KEY (entity_id, quarter)
) DEFAULT CHARSET=utf8mb4
"""

COLS = ["entity_id", "quarter", "period_end", "asof_date", "recorded_at", "status", "rule", "drv_code", "drv_coverage",
        "drv_months", "rev_base", "g_M1", "g_B2", "g_TS", "g_E", "g_E_noM1", "g_E_fill", "M1", "B2", "TS", "E",
        "E_noM1", "E_fill", "n_filled", "E_spread", "E_spread_from", "gap_E_vs_B2", "e_label", "ts_label",
        "m1_n_train", "OP_hat", "OP_naive", "op_a", "op_b", "cost_ratio_hat", "sga_hat", "c_hat", "op_n_models",
        "op_note", "coverage_class", "flag_jump", "flag_basis", "flag_spread", "flag_q4", "mapping_file",
        "prereg_commit", "code_sha256", "run_id"]


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def code_hash() -> str:
    h = hashlib.sha256()
    for f in ("k4_forecast.py", "k6_live.py", "k3_oprule.py"):
        h.update(Path(__file__).with_name(f).read_bytes())
    return h.hexdigest()


def main(quarter: str, asof: str, dry: bool):
    q = pd.Period(quarter, freq="Q").to_timestamp(how="end").normalize()
    t = pd.Timestamp(asof)
    out_dir = output_path("2d", "live", quarter)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    cal = _calendar()
    m = K4.inputs()["map"]
    ids = m["entity_id"].tolist()
    ev = events.read_events(ids)
    fd = events.first_disclosure(ev, {i: 12 for i in ids})
    done = fd[(fd["period_end"] == q) & (fd["first_dt"] <= t)]["entity_id"].tolist()
    todo = m[~m["entity_id"].isin(done)]
    adj = read_adj(ids)
    it = K3.items(ids, adj, quarters=None)
    ann = K4.announce_dates(ids, cal)
    rows, errs = [], []
    for r in todo.itertuples():
        try:
            rows += K4.run_entity(r, adj, it, ann, cal, quarters=[q], live_t=t)
        except Exception as ex:      # 실패 기업은 기록하지 않고 남긴다
            errs.append({"entity_id": r.entity_id, "error": repr(ex)[:200]})
    d = pd.DataFrame(rows)
    d["gap_E_vs_B2"] = np.log(d["E"] / d["B2"])
    d["recorded_at"] = datetime.now().replace(microsecond=0)
    d["mapping_file"], d["prereg_commit"], d["code_sha256"], d["run_id"] = MAPPING_FILE, PREREG_COMMIT, code_hash(), run_id
    for c in COLS:
        if c not in d:
            d[c] = np.nan
    d = d[COLS]
    csv = out_dir / f"fd_live_2d_{quarter}_{run_id}.csv"
    d.to_csv(csv, index=False, encoding="utf-8-sig")
    s = {"quarter": quarter, "asof": str(t.date()), "run_id": run_id, "already_announced": done,
         "recorded_firms": len(d), "errors": errs, "status": d["status"].value_counts().to_dict(),
         "E_missing": int(d["E"].isna().sum()), "M1_missing_driver": int((d["status"].eq("driver") & d["g_M1"].isna()).sum()),
         "drv_months": d["drv_months"].fillna("").value_counts().to_dict(),
         "n_filled": d["n_filled"].value_counts(dropna=False).to_dict(),
         "OP_hat_rows": int(d["OP_hat"].notna().sum()), "op_note": d["op_note"].fillna("").value_counts().to_dict(),
         "csv": str(csv), "csv_sha256": sha256_file(csv), "code_sha256": code_hash(), "prereg_commit": PREREG_COMMIT,
         "dry": dry, "rows_written": 0}
    if not dry:
        db.execute_fd_ddl(DDL)
        w = d.copy()
        for c in ("period_end", "asof_date"):
            w[c] = pd.to_datetime(w[c]).dt.date
        s["rows_written"] = db.insert_fd_strict(w, T_LIVE, ["entity_id", "quarter"])
    (out_dir / f"fd_live_2d_{quarter}_{run_id}_summary.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return d, s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    main(a.quarter, a.asof, a.dry)
