# -*- coding: utf-8 -*-
"""2g 실전 스냅샷 (method_2g_v1 3~4절, 커밋 5dc9fdb). 판정 가설 없음, 기록만.
쓰기: fd_live_2g (덮어쓰기 불가 누적), fd_rev_adj_2g (보정 매출 갱신, upsert), fd_earnings_event (공시일 갱신).

python -m DATA.fdriver.eval2g.live_2g [--asof YYYY-MM-DD] [--dry] [--no-refresh] [--workers N]

1. 공시일 갱신 (fd_earnings_event) 과 매출 보정 갱신 (fd_rev_adj_2g, 2d K2 규칙, 실행 시점 마지막 분기까지).
2. 분기: Q = 기준일 t 에 마지막으로 끝난 분기, Q+1.
3. M1: 기존 레이더 39곳은 radar.build.build(Q, t) 의 g_M1 (주 드라이버 모형). Q 에만 쓴다.
4. 기업별 예측 (병렬): 2d k4_forecast.run_entity(status ts_only, live_t=t, method_v2=True) 로 SARIMA·ETS·Theta·B2·TS·E
   를 구하고, M1 이 있으면 E5 를 다시 조합 (k7_methods.combine), OP_hat_E5 를 같은 규칙 계수로 다시 계산.
   OP_hat_alt (R1 최근 12분기 OLS, R2 판관비 이상값 대체 후 예측), flag_b2_jump, flag_m1_dir, flag_op_sign.
5. 컨센서스: Q·Q+1 매출 컨센서스 중 관측일 <= t 최신 (kr_consensus.read_consensus).
   K8b: E3 (SARIMA·ETS·Theta 기하평균) 과 gap_E3_vs_B2 = ln(E3) - ln(B2) 를 더한다.
6. 기록: fd_live_2g (entity_id, quarter, run_id) insert_fd_strict. 이미 발표한 기업도 announced_at_snapshot = 1 로 기록.
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse                                     # noqa: E402
import hashlib                                      # noqa: E402
import json                                         # noqa: E402
import logging                                      # noqa: E402
import time                                         # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from datetime import datetime                       # noqa: E402
from pathlib import Path                            # noqa: E402
from types import SimpleNamespace                   # noqa: E402

import numpy as np                                  # noqa: E402
import pandas as pd                                 # noqa: E402

from .. import db                                   # noqa: E402
from ..adapters import kr_consensus                 # noqa: E402
from ..config import output_path                    # noqa: E402
from ..eval2d import k4_forecast as K4              # noqa: E402
from ..eval2d import k7_methods as k7               # noqa: E402
from ..eval2d.fd_live_snapshot import V2_COLS, announced  # noqa: E402
from ..eval2d.k6_live import COLS as V1_COLS, DDL as V1_DDL, T_LIVE as T_2D  # noqa: E402
from ..nowcast import features as ft                # noqa: E402
from ..nowcast.run import _calendar                 # noqa: E402
from . import common as C                           # noqa: E402

log = logging.getLogger(__name__)
B2_JUMP = 0.20
ALT_R1_N = 12
ALT_SGA_MULT = 1.5
G2_COLS = {
    "horizon": "TINYINT", "group_2g": "VARCHAR(10)", "filter_fail": "VARCHAR(80)", "m1_source": "VARCHAR(60)",
    "cons": "DOUBLE", "cons_asof": "DATE", "gap_E5_vs_cons": "DOUBLE", "OP_hat_alt": "DOUBLE", "op_a_alt": "DOUBLE",
    "op_b_alt": "DOUBLE", "sga_hat_alt": "DOUBLE", "n_sga_capped": "INT", "alt_note": "VARCHAR(80)",
    "flag_b2_jump": "TINYINT", "flag_m1_dir": "TINYINT", "flag_op_sign": "TINYINT", "sga_fixed_n": "INT",
    "method_2g_commit": "CHAR(40)", "g_E3": "DOUBLE", "E3": "DOUBLE", "gap_E3_vs_B2": "DOUBLE",
}
DROP_V2 = {"prereg_v2_commit"}
COLS = V1_COLS + [c for c in V2_COLS if c not in DROP_V2] + list(G2_COLS)
DDL = (V1_DDL.replace(f"`{T_2D}`", f"`{C.T_LIVE}`")
       .replace("PRIMARY KEY (entity_id, quarter)", "PRIMARY KEY (entity_id, quarter, run_id)")
       .replace("  run_id VARCHAR(20) NOT NULL,\n",
                "  run_id VARCHAR(20) NOT NULL,\n" + "".join(
                    f"  `{c}` {t} NULL,\n" for c, t in {**{k: v for k, v in V2_COLS.items() if k not in DROP_V2},
                                                        **G2_COLS}.items())))


# ------------------------------------------------------------------ 한 기업 (작업 프로세스)
def _sign(x):
    return None if x is None or pd.isna(x) else int(np.sign(x))


def sga_capped(s: pd.Series) -> (pd.Series, int):
    """각 분기 값이 바로 앞 4개 분기(원래 값) 중앙값 x 1.5 를 넘으면 그 중앙값으로 바꾼다. 앞 4개 분기가 다 있어야 함."""
    s = s.dropna().sort_index()
    out, n = s.copy(), 0
    for d in s.index:
        prev = [ft.quarter_shift(d, k) for k in (1, 2, 3, 4)]
        if all(p in s.index for p in prev):
            med = float(np.median(s[prev].to_numpy(float)))
            if med > 0 and s[d] > ALT_SGA_MULT * med:
                out[d] = med
                n += 1
    return out, n


def post(rec: dict, row, m1, items: pd.DataFrame, R_all: pd.Series, ann_e: pd.Series, cal, t: pd.Timestamp) -> dict:
    q = pd.Timestamp(rec["period_end"])
    if rec.get("rev_base") is None or pd.isna(rec.get("rev_base")):
        return rec
    base = float(rec["rev_base"])
    lvl = (lambda g: float(base * np.exp(g)) if g is not None and pd.notna(g) else None)
    # M1 (레이더 39, Q 만)
    if m1 is not None and pd.notna(m1):
        rec["g_M1"], rec["M1"] = float(m1), lvl(m1)
        cb = k7.combine(rec["g_SARIMA"], rec["g_ETS"], rec["g_Theta"], float(m1), rec["g_B2"], True)
        rec.update(cb)
        for k in ("E5", "E4", "E5_noM1"):
            rec[k] = lvl(rec[f"g_{k}"])
        rec["gap_E4_vs_B2"] = (cb["g_E4"] - rec["g_B2"]) if cb["g_E4"] is not None and rec["g_B2"] is not None else None
    # E3 = SARIMA·ETS·Theta 기하평균 (B2·M1 을 섞지 않은 신호, K8b), gap_E3_vs_B2 = ln(E3) - ln(B2)
    g3 = [rec.get(f"g_{k}") for k in ("SARIMA", "ETS", "Theta")]
    g3 = [g for g in g3 if g is not None and pd.notna(g)]
    rec["g_E3"] = float(np.mean(g3)) if g3 else None
    rec["E3"] = lvl(rec["g_E3"])
    b2 = rec.get("g_B2")
    rec["gap_E3_vs_B2"] = (rec["g_E3"] - b2) if g3 and b2 is not None and pd.notna(b2) else None
    E5 = rec.get("E5")
    # OP_hat_E5 다시 계산 (같은 계수·비용 예측)
    rec["OP_hat_E5"] = None
    if E5 is not None:
        if pd.notna(rec.get("op_a")):
            rec["OP_hat_E5"] = float(rec["op_a"] + rec["op_b"] * E5)
        elif pd.notna(rec.get("cost_ratio_hat")):
            rec["OP_hat_E5"] = float(E5 * (1 - rec["cost_ratio_hat"]) - rec["sga_hat"])
        elif pd.notna(rec.get("c_hat")):
            rec["OP_hat_E5"] = float(E5 * (1 - rec["c_hat"]))
    # OP_hat_alt
    def ann_dt(j):
        v = ann_e.get(j)
        return pd.Timestamp(v) if v is not None and pd.notna(v) else K4.legal_date(j, cal)
    kq = [j for j in items.index if j < q and ann_dt(j) <= t]
    I = items.reindex(kq)
    Rk = R_all.reindex(kq)
    rule = row.rule
    alt, note = None, ""
    if E5 is None or rec["OP_hat_E5"] is None:
        note = "no_E5_or_OP_hat_E5"
    elif rule == "R1":
        d = pd.DataFrame({"op": I["op"], "rev": Rk}).dropna().sort_index().tail(ALT_R1_N)
        if len(d) >= ALT_R1_N:
            X = np.column_stack([np.ones(len(d)), d["rev"].to_numpy(float)])
            beta, *_ = np.linalg.lstsq(X, d["op"].to_numpy(float), rcond=None)
            rec["op_a_alt"], rec["op_b_alt"] = float(beta[0]), float(beta[1])
            alt = float(beta[0] + beta[1] * E5)
        else:
            note = f"R1 alt n {len(d)} < {ALT_R1_N}"
    elif rule == "R2" and pd.notna(rec.get("cost_ratio_hat")):
        sc, n = sga_capped(I["sga"])
        rec["n_sga_capped"] = n
        sg, _, w = K4.cost_forecast(sc, q)
        if sg is not None:
            rec["sga_hat_alt"] = sg
            alt = float(E5 * (1 - rec["cost_ratio_hat"]) - sg)
        else:
            note = f"R2 alt sga fail {w}"
    elif rule in ("R2", "R3"):
        alt = rec["OP_hat_E5"]
        note = "same_as_main" + ("(R2->R3)" if rule == "R2" else "")
    rec["OP_hat_alt"], rec["alt_note"] = alt, note or None
    # 표시
    gs = [rec.get(f"g_{k}") for k in ("SARIMA", "ETS", "Theta")]
    gs = [g for g in gs if g is not None and pd.notna(g)]
    b2 = rec.get("g_B2")
    rec["flag_b2_jump"] = int(abs(b2 - float(np.median(gs))) > B2_JUMP) if gs and b2 is not None and pd.notna(b2) else None
    if rec.get("g_M1") is not None and pd.notna(rec.get("g_M1")):
        others = [rec.get(f"g_{k}") for k in ("SARIMA", "ETS", "Theta", "B2")]
        rec["flag_m1_dir"] = int(all(o is not None and pd.notna(o) and o * rec["g_M1"] < 0 for o in others))
    else:
        rec["flag_m1_dir"] = None
    sg3 = [_sign(rec.get(k)) for k in ("OP_hat_E5", "OP_hat_alt", "OP_naive")]
    sg3 = [s for s in sg3 if s is not None]
    rec["flag_op_sign"] = int(len(set(sg3)) > 1) if len(sg3) >= 2 else None
    return rec


def forecast_firm(args) -> list:
    row, adj_e, it_e, ann_e, cal, quarters, t, m1 = args
    r = SimpleNamespace(**row)
    ann_df = ann_e.reset_index() if len(ann_e) else pd.DataFrame(columns=["entity_id", "period_end", "first_dt"])
    ann_df["entity_id"] = r.entity_id
    try:
        recs = K4.run_entity(r, adj_e, it_e, ann_df, cal, quarters=quarters, live_t=t, method_v2=True)
    except Exception as ex:      # noqa: BLE001  한 기업 실패는 기록하고 계속
        return [{"entity_id": r.entity_id, "quarter": "ERR", "op_note": repr(ex)[:120]}]
    items = it_e.pivot_table(index="period_end", columns="item", values="value")
    for c in ("op", "cogs", "sga"):
        if c not in items:
            items[c] = np.nan
    R_all = adj_e.set_index("period_end")["revenue_adj"].sort_index()
    out = []
    for i, rec in enumerate(recs):
        rec["horizon"] = i
        try:
            out.append(post(rec, r, m1 if i == 0 else None, items, R_all, ann_e, cal, t))
        except Exception as ex:  # noqa: BLE001
            rec["alt_note"] = ("post_err " + repr(ex))[:80]
            out.append(rec)
    return out


# ------------------------------------------------------------------ 본체
def code_hash() -> str:
    h = hashlib.sha256()
    base = Path(__file__).resolve().parent
    for f in (base.parent / "eval2d" / "k4_forecast.py", base.parent / "eval2d" / "k7_methods.py",
              base.parent / "eval2d" / "k3_oprule.py", base / "common.py", base / "live_2g.py"):
        h.update(f.read_bytes())
    return h.hexdigest()


def ensure_table() -> list:
    db.execute_fd_ddl(DDL)
    have = set(db.table_columns(C.T_LIVE))
    done = []
    for c, typ in {**V2_COLS, **G2_COLS}.items():
        if c not in DROP_V2 and c not in have:
            db.execute_fd_ddl(f"ALTER TABLE `{C.T_LIVE}` ADD COLUMN `{c}` {typ} NULL")
            done.append(c)
    return done


def radar_m1(Q: pd.Timestamp, t: pd.Timestamp, ids) -> (dict, dict):
    from ..radar import build as rb
    lab = f"{Q.year}Q{Q.quarter}"
    try:
        res = rb.build(lab, t)
    except Exception as ex:      # noqa: BLE001
        log.exception("radar build failed")
        return {}, {"error": repr(ex)[:200]}
    tab = res["table"]
    tab = tab[tab["entity_id"].isin(ids)]
    g = pd.to_numeric(tab["g_M1"], errors="coerce")
    m1 = {e: float(v) for e, v in zip(tab["entity_id"], g) if pd.notna(v)}
    src = {e: str(m) for e, m in zip(tab["entity_id"], tab["M1_model"]) if e in m1}
    return m1, src


def main(asof: str, dry: bool, refresh: bool = True, workers: int = 16, only=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("DATA.fdriver.adapters.revenue").setLevel(logging.ERROR)
    t0 = time.time()
    tim = {}
    t = pd.Timestamp(asof).normalize()
    Q = C.last_completed_quarter(t)
    Q1 = ft.quarter_shift(Q, -1)
    qlab = f"{Q.year}Q{Q.quarter}"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = output_path("2g", "live", qlab)
    cal = _calendar()
    tg = C.read_targets()
    rules = db.read_sql(f"SELECT entity_id, rule, flag_spread, flag_q4 FROM `{C.T_RULE}`")
    tg = tg.drop(columns=["rule"]).merge(rules, on="entity_id", how="left")
    if only:                     # 시험용: 일부 기업만 (기록하지 않음)
        tg, dry = tg[tg["entity_id"].isin(only)], True
    ids = tg["entity_id"].tolist()
    # 1. 공시일, 매출 보정 갱신
    ev_skipped = []
    if refresh and not dry:
        start = (Q - pd.offsets.QuarterEnd(1) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        ev_skipped = C.safe_load_events(ids, start)
    ann_all = announced(ids, Q, t, False)
    tim["events"] = time.time() - t0
    adj_new = C.build_adj(ids, Q)["adj"]
    adj_new = adj_new[adj_new["entity_id"].isin(ids)].copy()
    adj_upd = 0
    if not dry:
        adj_new["run_id"] = run_id
        adj_upd = db.write_fd(adj_new, C.T_ADJ, ["entity_id", "period_end"])
    adj = adj_new.drop(columns=["run_id"], errors="ignore")
    it, fixlog = C.items_2g(ids, adj)
    ann = K4.announce_dates(ids, cal)
    tim["data"] = time.time() - t0
    # 3. M1 (레이더)
    radar_ids = tg.loc[tg["group_2g"] == "레이더39", "entity_id"].tolist()
    m1, m1_src = radar_m1(Q, t, radar_ids)
    tim["radar_m1"] = time.time() - t0
    # 4. 예측 (병렬)
    fixed_n = fixlog.groupby("entity_id").size() if len(fixlog) else pd.Series(dtype=int)
    jobs = []
    for r in tg.itertuples():
        row = {"entity_id": r.entity_id, "status": "ts_only", "code": None, "rule": r.rule,
               "coverage_class": r.coverage_class, "flag_jump": r.flag_jump, "flag_basis": r.flag_basis,
               "flag_spread": r.flag_spread, "flag_q4": r.flag_q4}
        a_e = adj[adj["entity_id"] == r.entity_id]
        i_e = it[it["entity_id"] == r.entity_id]
        n_e = ann[ann["entity_id"] == r.entity_id].set_index("period_end")["first_dt"]
        jobs.append((row, a_e, i_e, n_e, cal, [Q, Q1], t, m1.get(r.entity_id)))
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, res in enumerate(ex.map(forecast_firm, jobs, chunksize=2)):
            rows += res
            if (k + 1) % 50 == 0:
                log.info("forecast %d/%d (%.0fs)", k + 1, len(jobs), time.time() - t0)
    tim["forecast"] = time.time() - t0
    d = pd.DataFrame(rows)
    if "op_note" not in d:
        d["op_note"] = None
    errs = d[d["quarter"] == "ERR"][["entity_id", "op_note"]].to_dict(orient="records") if "quarter" in d else []
    d = d[d["quarter"] != "ERR"].copy()
    # 5. 컨센서스
    cons = []
    for e, ql in zip(d["entity_id"], d["quarter"]):
        c = kr_consensus.read_consensus(e, ql, t)
        cons.append((c["value"], c["asof"]))
    d["cons"] = [c[0] for c in cons]
    d["cons_asof"] = [pd.Timestamp(c[1]).date() if c[1] is not None else None for c in cons]
    d["gap_E5_vs_cons"] = np.where(d["cons"].fillna(0) > 0, np.log(d["E5"].astype(float) / d["cons"].astype(float)),
                                   np.nan)
    tim["consensus"] = time.time() - t0
    # 6. 기록
    a = ann_all.set_index("entity_id")
    d["announced_at_snapshot"] = np.where(d["horizon"] == 0, d["entity_id"].map(a["announced"]).astype(float), 0)
    d["first_disclosure_dt"] = np.where(d["horizon"] == 0, d["entity_id"].map(a["first_dt"]), pd.NaT)
    d["first_disclosure_dt"] = pd.to_datetime(d["first_disclosure_dt"]).dt.date
    d["gap_E_vs_B2"] = np.log(d["E"].astype(float) / d["B2"].astype(float))
    g = tg.set_index("entity_id")
    d["group_2g"] = d["entity_id"].map(g["group_2g"])
    d["filter_fail"] = d["entity_id"].map(g["filter_fail"])
    d["m1_source"] = np.where(d["horizon"] == 0, d["entity_id"].map(m1_src), None)
    d["sga_fixed_n"] = d["entity_id"].map(fixed_n).fillna(0).astype(int)
    d["recorded_at"] = datetime.now().replace(microsecond=0)
    d["mapping_file"], d["prereg_commit"], d["method_2g_commit"] = "none(2g)", C.METHOD_COMMIT, C.METHOD_COMMIT
    d["code_sha256"], d["run_id"], d["method_version"] = code_hash(), run_id, "2g_v1"
    for c in COLS:
        if c not in d:
            d[c] = np.nan
    d = d[COLS]
    tag = "_dry" if dry else ""
    csv = out_dir / f"fd_live_2g_{qlab}_{run_id}{tag}.csv"
    d.to_csv(csv, index=False, encoding="utf-8-sig")
    ann_all.to_csv(out_dir / f"announced_2g_{qlab}_{run_id}{tag}.csv", index=False, encoding="utf-8-sig")
    fixlog.to_csv(out_dir / f"sga_fixed_{run_id}{tag}.csv", index=False, encoding="utf-8-sig")
    s = {"quarters": [qlab, f"{Q1.year}Q{Q1.quarter}"], "asof": str(t.date()), "run_id": run_id, "dry": dry,
         "rows": len(d), "firms": int(d["entity_id"].nunique()), "errors": errs,
         "announced": ann_all[ann_all["announced"]][["entity_id", "first_dt", "first_type"]].to_dict(orient="records"),
         "m1_firms": len(m1), "E5_missing": d.groupby("quarter")["E5"].apply(lambda x: int(x.isna().sum())).to_dict(),
         "e5_n": {f"{a}/{b}": int(v) for (a, b), v in d.groupby(["quarter", "e5_n"]).size().items()},
         "OP_hat_E5_rows": d.groupby("quarter")["OP_hat_E5"].apply(lambda x: int(x.notna().sum())).to_dict(),
         "OP_hat_alt_rows": d.groupby("quarter")["OP_hat_alt"].apply(lambda x: int(x.notna().sum())).to_dict(),
         "cons_rows": d.groupby("quarter")["cons"].apply(lambda x: int(x.notna().sum())).to_dict(),
         "alt_note": d["alt_note"].value_counts().to_dict(),
         "flags": {f: d.groupby("quarter")[f].apply(lambda x: int((x == 1).sum())).to_dict()
                   for f in ("flag_b2_jump", "flag_m1_dir", "flag_op_sign")},
         "events_skipped": ev_skipped, "adj_rows_upserted": adj_upd, "csv": str(csv), "code_sha256": code_hash(),
         "method_2g_commit": C.METHOD_COMMIT, "ddl_added": [], "rows_written": 0}
    if not dry:
        s["ddl_added"] = ensure_table()
        w = d.copy()
        for c in ("period_end", "asof_date"):
            w[c] = pd.to_datetime(w[c]).dt.date
        s["rows_written"] = db.insert_fd_strict(w, C.T_LIVE, ["entity_id", "quarter", "run_id"])
    tim["total"] = time.time() - t0
    s["seconds"] = {k: round(v) for k, v in tim.items()}
    s["csv_sha256"] = hashlib.sha256(csv.read_bytes()).hexdigest()
    (out_dir / f"fd_live_2g_{qlab}_{run_id}{tag}_summary.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return d, s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    main(a.asof, a.dry, refresh=not a.no_refresh, workers=a.workers, only=a.only)
