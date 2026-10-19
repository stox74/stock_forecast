# -*- coding: utf-8 -*-
"""2d 실전 스냅샷 (주 1회). 사전 등록 v2 (docs/preregistration_2d_live_v2.md, 커밋 51c6991) 2절. 쓰기는 fd_live_2d,
fd_earnings_event (공시일 갱신) 만.

python -m DATA.fdriver.eval2d.fd_live_snapshot --quarter 2026Q3 [--asof YYYY-MM-DD] [--dry]

1. 공시일 갱신: 2d 대상 73곳과 기존 레이더 39곳의 분기 시작 이후 공시를 events.build_and_load 로 받는다.
   기준일까지 그 분기 첫 공시가 있는 기업을 '이미 발표'로 표시하고 목록을 남긴다 (두 집단 모두).
2. 예측: fd_driver_map_2d driver + ts_only 73곳. k4_forecast.run_entity(live_t=asof, method_v2=True).
   v2 열(E5, E4, E5_noM1, SARIMA, ETS, Theta, gap_E4_vs_B2, OP_hat_E5) 과 기존 열(E, TS, gap_E_vs_B2, OP_hat) 을 함께 남긴다.
3. 기록: fd_live_2d 에 (entity_id, quarter, run_id) 로 덮어쓰기 불가 누적 (insert_fd_strict). 이미 발표한 기업도 기록하되
   announced_at_snapshot = 1 (평가 규칙상 쓰이지 않음). method_version = 'v2'.
   테이블 구조: 처음 실행할 때 한 번 PK 를 (entity_id, quarter) -> (entity_id, quarter, run_id) 로 바꾸고 v2 열을 더한다.
   기존 행(10/18 스냅샷, method_version 공란 = v1)은 값을 바꾸지 않는다.
4. 증거: CSV SHA-256, 코드 SHA-256 (k4_forecast.py, k7_methods.py, k3_oprule.py, fd_live_snapshot.py), 사전 등록 커밋 해시.
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
from ..radar.universe import universe as radar_universe
from . import k3_oprule as K3
from . import k4_forecast as K4
from .k2_targets import read_adj
from .k6_live import COLS as V1_COLS, DDL as V1_DDL, MAPPING_FILE, T_LIVE

PREREG_V2_COMMIT = "51c6991583d94d7aa003b8d85a35dd17ae4a17cd"
METHOD = "v2"
V2_COLS = {
    "g_SARIMA": "DOUBLE", "g_ETS": "DOUBLE", "g_Theta": "DOUBLE", "g_E5": "DOUBLE", "g_E4": "DOUBLE",
    "g_E5_noM1": "DOUBLE", "SARIMA": "DOUBLE", "ETS": "DOUBLE", "Theta": "DOUBLE", "E5": "DOUBLE", "E4": "DOUBLE",
    "E5_noM1": "DOUBLE", "gap_E4_vs_B2": "DOUBLE", "sarima_order": "VARCHAR(40)", "ts3_n_train": "INT",
    "e5_n": "INT", "OP_hat_E5": "DOUBLE", "method_version": "VARCHAR(10)", "announced_at_snapshot": "TINYINT",
    "first_disclosure_dt": "DATE", "prereg_v2_commit": "CHAR(40)",
}
COLS = V1_COLS + list(V2_COLS)


def ensure_table() -> list:
    """v1 테이블이 없으면 만들고, v2 열과 누적 PK 를 한 번 적용한다. 반환: 실행한 DDL."""
    done = []
    db.execute_fd_ddl(V1_DDL)
    have = set(db.table_columns(T_LIVE))
    for c, typ in V2_COLS.items():
        if c not in have:
            db.execute_fd_ddl(f"ALTER TABLE `{T_LIVE}` ADD COLUMN `{c}` {typ} NULL")
            done.append(f"add {c}")
    pk = db.read_sql(f"SHOW INDEX FROM `{T_LIVE}` WHERE Key_name = 'PRIMARY'")["Column_name"].tolist()
    if pk != ["entity_id", "quarter", "run_id"]:
        db.execute_fd_ddl(f"ALTER TABLE `{T_LIVE}` DROP PRIMARY KEY, ADD PRIMARY KEY (entity_id, quarter, run_id)")
        done.append(f"pk {pk} -> entity_id, quarter, run_id")
    return done


def code_hash() -> str:
    h = hashlib.sha256()
    for f in ("k4_forecast.py", "k7_methods.py", "k3_oprule.py", "fd_live_snapshot.py"):
        h.update(Path(__file__).with_name(f).read_bytes())
    return h.hexdigest()


def announced(ids, q: pd.Timestamp, t: pd.Timestamp, refresh: bool) -> pd.DataFrame:
    if refresh:
        start = (q - pd.offsets.QuarterEnd(1) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        events.build_and_load(ids, start=start)
    ev = events.read_events(ids)
    fd = events.first_disclosure(ev, {i: 12 for i in ids})
    fd = fd[fd["period_end"] == q]
    out = pd.DataFrame({"entity_id": ids})
    out["first_dt"] = out["entity_id"].map(fd.set_index("entity_id")["first_dt"])
    out["first_type"] = out["entity_id"].map(fd.set_index("entity_id")["first_type"])
    out["announced"] = out["first_dt"].notna() & (out["first_dt"] <= t)
    return out


def main(quarter: str, asof: str, dry: bool, refresh: bool = True):
    q = pd.Period(quarter, freq="Q").to_timestamp(how="end").normalize()
    t = pd.Timestamp(asof).normalize()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = output_path("2d", "live", quarter)
    cal = _calendar()
    m = K4.inputs()["map"]
    ids = m["entity_id"].tolist()
    radar_ids = radar_universe()["entity_id"].tolist()
    ann_all = announced(sorted(set(ids) | set(radar_ids)), q, t, refresh and not dry)
    ann_all["group"] = np.where(ann_all["entity_id"].isin(ids), "2d 확대", "기존 39")
    adj = read_adj(ids)
    it = K3.items(ids, adj, quarters=None)
    ann = K4.announce_dates(ids, cal)
    rows, errs = [], []
    for r in m.itertuples():
        try:
            rows += K4.run_entity(r, adj, it, ann, cal, quarters=[q], live_t=t, method_v2=True)
        except Exception as ex:     # 실패 기업은 기록하지 않고 남긴다
            errs.append({"entity_id": r.entity_id, "error": repr(ex)[:200]})
    d = pd.DataFrame(rows)
    a = ann_all.set_index("entity_id")
    d["announced_at_snapshot"] = d["entity_id"].map(a["announced"]).astype(int)
    d["first_disclosure_dt"] = d["entity_id"].map(a["first_dt"]).dt.date
    d["gap_E_vs_B2"] = np.log(d["E"] / d["B2"])
    d["recorded_at"] = datetime.now().replace(microsecond=0)
    d["mapping_file"], d["prereg_commit"], d["prereg_v2_commit"] = MAPPING_FILE, PREREG_V2_COMMIT, PREREG_V2_COMMIT
    d["code_sha256"], d["run_id"], d["method_version"] = code_hash(), run_id, METHOD
    for c in COLS:
        if c not in d:
            d[c] = np.nan
    d = d[COLS]
    tag = "_dry" if dry else ""
    csv = out_dir / f"fd_live_2d_{quarter}_{run_id}_v2{tag}.csv"
    d.to_csv(csv, index=False, encoding="utf-8-sig")
    ann_all.to_csv(out_dir / f"announced_{quarter}_{run_id}{tag}.csv", index=False, encoding="utf-8-sig")
    s = {"quarter": quarter, "asof": str(t.date()), "run_id": run_id, "method_version": METHOD, "dry": dry,
         "rows": len(d), "errors": errs,
         "announced": ann_all[ann_all["announced"]][["entity_id", "group", "first_dt", "first_type"]]
         .to_dict(orient="records"),
         "E5_missing": int(d["E5"].isna().sum()), "E4_missing": int(d["E4"].isna().sum()),
         "ts3_missing": {k: int(d[f"g_{k}"].isna().sum()) for k in ("SARIMA", "ETS", "Theta")},
         "e5_n": d["e5_n"].value_counts(dropna=False).to_dict(),
         "OP_hat_E5_rows": int(d["OP_hat_E5"].notna().sum()), "csv": str(csv), "code_sha256": code_hash(),
         "prereg_v2_commit": PREREG_V2_COMMIT, "ddl": [], "rows_written": 0}
    if not dry:
        s["ddl"] = ensure_table()
        w = d.copy()
        for c in ("period_end", "asof_date"):
            w[c] = pd.to_datetime(w[c]).dt.date
        s["rows_written"] = db.insert_fd_strict(w, T_LIVE, ["entity_id", "quarter", "run_id"])
    s["csv_sha256"] = hashlib.sha256(csv.read_bytes()).hexdigest()
    (out_dir / f"fd_live_2d_{quarter}_{run_id}_v2{tag}_summary.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return d, s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()
    main(a.quarter, a.asof, a.dry, refresh=not a.no_refresh)
