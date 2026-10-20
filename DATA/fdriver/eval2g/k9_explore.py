# -*- coding: utf-8 -*-
"""2g K9 4: 참고 예측 과거 구간 탐색 -- 탐색적, 판정에 쓰지 않음. method_2g_ref_v1 (커밋 72209e4) 4절. DB 쓰기 없음.

- 대상: fd_driver_ref_2g A·B 등급 기업. 구간 2024Q1~2026Q2, 기준일 pre5 (2d K4 와 같음).
- 예측: 2g 주 예측 (k4_forecast.run_entity + live_2g.post) 위에 eval2g.ref.firm_ref 로 M1_ref, SARIMAX_ref, E5_ref, E6_ref.
  과거 구간 SARIMAX_ref 는 같은 분기 SARIMA 가 고른 차수를 그대로 쓴다 (계산량 절감, 방법 문서 4절).
  기준일 t 는 분기마다 그 분기의 pre5 다 (드라이버 값도 그 날까지 공표된 것 + 채움).
- 오차: |ln(실적/예측)| (%). 비교: SARIMA, M1_ref, SARIMAX_ref, E5, E5_ref, E6_ref, B2, 컨센서스.
- 요약: 전체·등급·출처·ksic_only 별 중앙값과 'SARIMAX_ref 가 SARIMA 보다 나은 비율' (같은 행끼리).
python -m DATA.fdriver.eval2g.k9_explore [--workers 16]
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse                                     # noqa: E402
import json                                         # noqa: E402
import logging                                      # noqa: E402
import time                                         # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from types import SimpleNamespace                   # noqa: E402

import numpy as np                                  # noqa: E402
import pandas as pd                                 # noqa: E402

from .. import db                                   # noqa: E402
from ..adapters import kr_consensus                 # noqa: E402
from ..config import output_path                    # noqa: E402
from ..eval2d import k4_forecast as K4              # noqa: E402
from ..eval2d.k5_judge import paired_test           # noqa: E402
from ..nowcast.run import _calendar                 # noqa: E402
from . import common as C                           # noqa: E402
from . import ref                                   # noqa: E402
from .live_2g import post                           # noqa: E402

log = logging.getLogger(__name__)
OUT = output_path("2g", "K9", "explore")
NOTE = "탐색적, 판정에 쓰지 않음 (method_2g_ref_v1 커밋 72209e4 뒤 계산)"
MODELS = ["SARIMA", "M1_ref", "SARIMAX_ref", "E5", "E5_ref", "E6_ref", "B2", "cons"]


def worker(args) -> list:
    row, adj_e, it_e, ann_e, cal, drv = args
    r = SimpleNamespace(**row)
    ann_df = ann_e.reset_index()
    ann_df["entity_id"] = r.entity_id
    try:
        recs = K4.run_entity(r, adj_e, it_e, ann_df, cal, quarters=None, method_v2=True)
    except Exception as ex:      # noqa: BLE001
        return [{"entity_id": r.entity_id, "quarter": "ERR", "ref_note": repr(ex)[:120]}]
    items = it_e.pivot_table(index="period_end", columns="item", values="value")
    for c in ("op", "cogs", "sga"):
        if c not in items:
            items[c] = np.nan
    R_all = adj_e.set_index("period_end")["revenue_adj"].sort_index()
    try:
        d = ref.Driver(drv["code"])
    except Exception as ex:      # noqa: BLE001
        d, err = None, repr(ex)[:100]
    out = []
    for rec in recs:
        t = pd.Timestamp(rec["asof_date"])
        try:
            rec = post(rec, r, None, items, R_all, ann_e, cal, t)
        except Exception:        # noqa: BLE001
            pass
        rec.update({"drv_code_ref": drv["code"], "drv_source_ref": drv["source"], "drv_grade": drv["grade"],
                    "drv_ksic_only": drv["ksic_only"]})
        if rec.get("rev_base") is not None and pd.notna(rec.get("rev_base")):
            if d is None:
                rec["ref_note"] = "driver_err " + err
            else:
                try:
                    ref.firm_ref(drv["code"], [rec], R_all, ann_e, cal, t, fixed_order=True, d=d)
                except Exception as ex:  # noqa: BLE001
                    rec["ref_note"] = ("ref_err " + repr(ex))[:120]
        out.append(rec)
    return out


def med(g: pd.DataFrame, cols) -> dict:
    return {"n": len(g), **{c.replace("err_", ""): round(float(g[c].median()), 2) for c in cols}}


def main(workers: int = 16):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("DATA.fdriver.adapters.revenue").setLevel(logging.ERROR)
    t0 = time.time()
    cal = _calendar()
    dref = db.read_sql("SELECT entity_id, code, source, grade, ksic_only FROM fd_driver_ref_2g WHERE grade IN ('A','B')")
    tg = C.read_targets()
    rules = db.read_sql(f"SELECT entity_id, rule, flag_spread, flag_q4 FROM `{C.T_RULE}`")
    tg = tg.drop(columns=["rule"]).merge(rules, on="entity_id", how="left")
    tg = tg[tg["entity_id"].isin(dref["entity_id"])]
    ids = tg["entity_id"].tolist()
    adj = C.read_adj(ids)
    it, _ = C.items_2g(ids, adj)
    ann = K4.announce_dates(ids, cal)
    dr = dref.set_index("entity_id")
    jobs = []
    for r in tg.itertuples():
        row = {"entity_id": r.entity_id, "status": "ts_only", "code": None, "rule": r.rule,
               "coverage_class": r.coverage_class, "flag_jump": r.flag_jump, "flag_basis": r.flag_basis,
               "flag_spread": r.flag_spread, "flag_q4": r.flag_q4}
        drv = {"code": dr.at[r.entity_id, "code"], "source": dr.at[r.entity_id, "source"],
               "grade": dr.at[r.entity_id, "grade"], "ksic_only": int(dr.at[r.entity_id, "ksic_only"])}
        jobs.append((row, adj[adj["entity_id"] == r.entity_id], it[it["entity_id"] == r.entity_id],
                     ann[ann["entity_id"] == r.entity_id].set_index("period_end")["first_dt"], cal, drv))
    t_in = time.time() - t0
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, res in enumerate(ex.map(worker, jobs, chunksize=1)):
            rows += res
            if (k + 1) % 25 == 0:
                log.info("%d/%d (%.0fs)", k + 1, len(jobs), time.time() - t0)
    t_fc = time.time() - t0
    d = pd.DataFrame(rows)
    errs = d[d["quarter"] == "ERR"][["entity_id", "ref_note"]].to_dict(orient="records") if (d["quarter"] == "ERR").any() \
        else []
    d = d[d["quarter"] != "ERR"].copy()
    d["period_end"] = pd.to_datetime(d["period_end"])
    A = adj.set_index(["entity_id", "period_end"])["revenue_adj"]
    d["rev_actual"] = [A.get((e, p)) for e, p in zip(d["entity_id"], d["period_end"])]
    cons = [kr_consensus.read_consensus(e, q, pd.Timestamp(a)) for e, q, a in
            zip(d["entity_id"], d["quarter"], d["asof_date"])]
    d["cons"] = [c["value"] for c in cons]
    for k in MODELS:
        if k not in d:
            d[k] = np.nan
        f = pd.to_numeric(d[k], errors="coerce")
        d[f"err_{k}"] = (np.abs(np.log(d["rev_actual"].astype(float) / f)) * 100).where(
            (d["rev_actual"] > 0) & (f > 0))
    g = tg.set_index("entity_id")
    for c in ("group_2g", "industry_group", "name"):
        d[c] = d["entity_id"].map(g[c])
    d["drv_stale"] = d["ref_note"].fillna("").str.startswith("drv_data_end").astype(int) if "ref_note" in d else 0
    d.drop(columns=["drv_code_ref"]).to_csv(OUT / "k9_explore_rows_public.csv", index=False, encoding="utf-8-sig")
    d.to_csv(OUT / "k9_explore_rows.csv", index=False, encoding="utf-8-sig")
    # 비교
    core = ["err_SARIMA", "err_SARIMAX_ref", "err_E5", "err_E6_ref", "err_B2"]
    sx = d.dropna(subset=core).copy()
    sx["sx_better"] = (sx["err_SARIMAX_ref"] < sx["err_SARIMA"]).astype(int)
    sx["e6_better_e5"] = (sx["err_E6_ref"] < sx["err_E5"]).astype(int)
    m1 = d.dropna(subset=["err_M1_ref", "err_E5_ref", "err_E5", "err_B2"]).copy()
    m1["e5ref_better_e5"] = (m1["err_E5_ref"] < m1["err_E5"]).astype(int)
    keep = ("n", "n_firms", "mean_diff", "se", "p_two", "share_a_better")
    pt = lambda x, a, b: {k: v for k, v in paired_test(x, a, b).items() if k in keep}

    def block(x, y):
        return {"sx": {**med(x, core), "sx_better_sarima": round(float(x["sx_better"].mean()), 3) if len(x) else None,
                       "e6_better_e5": round(float(x["e6_better_e5"].mean()), 3) if len(x) else None},
                "m1": {**med(y, ["err_M1_ref", "err_E5_ref", "err_E5", "err_B2"]),
                       "e5ref_better_e5": round(float(y["e5ref_better_e5"].mean()), 3) if len(y) else None}}

    res = {"note": NOTE, "firms": int(d["entity_id"].nunique()), "rows": len(d), "errors": errs,
           "sarimax_order": "같은 분기 SARIMA 차수 그대로 (과거 구간)",
           "coverage": {k: int(pd.to_numeric(d[k], errors="coerce").notna().sum()) for k in MODELS},
           "drv_stale_rows": int(d["drv_stale"].sum()),
           "all": block(sx, m1),
           "test_SARIMAX_vs_SARIMA": pt(sx, "err_SARIMAX_ref", "err_SARIMA"),
           "test_E6_vs_E5": pt(sx, "err_E6_ref", "err_E5"),
           "test_E5ref_vs_E5": pt(m1, "err_E5_ref", "err_E5"),
           "test_M1ref_vs_B2": pt(m1, "err_M1_ref", "err_B2"),
           "by_grade": {k: block(sx[sx["drv_grade"] == k], m1[m1["drv_grade"] == k]) for k in ("A", "B")},
           "by_source": {k: block(sx[sx["drv_source_ref"] == k], m1[m1["drv_source_ref"] == k])
                         for k in sorted(d["drv_source_ref"].dropna().unique())},
           "by_ksic_only": {str(k): block(sx[sx["drv_ksic_only"] == k], m1[m1["drv_ksic_only"] == k]) for k in (0, 1)},
           "with_cons": med(sx.dropna(subset=["err_cons"]), core + ["err_cons"]),
           "seconds": {"inputs": round(t_in), "forecast": round(t_fc - t_in), "total": round(time.time() - t0)},
           "workers": workers}
    firm = sx.groupby(["entity_id", "name", "group_2g", "drv_grade", "drv_source_ref", "drv_ksic_only"]).agg(
        n=("sx_better", "size"), sx_better_share=("sx_better", "mean"), e6_better_e5_share=("e6_better_e5", "mean"),
        med_SARIMA=("err_SARIMA", "median"), med_SARIMAX=("err_SARIMAX_ref", "median"), med_E5=("err_E5", "median"),
        med_E6=("err_E6_ref", "median"), med_B2=("err_B2", "median")).reset_index()
    res["firm_sx_better_ge_half"] = int((firm["sx_better_share"] >= 0.5).sum())
    res["firm_e6_better_ge_half"] = int((firm["e6_better_e5_share"] >= 0.5).sum())
    res["firms_with_sx"] = len(firm)
    firm.to_csv(OUT / "k9_explore_by_firm.csv", index=False, encoding="utf-8-sig")
    (OUT / "k9_explore.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with pd.ExcelWriter(OUT / "k9_explore.xlsx") as w:
        pd.DataFrame({"note": [NOTE, "과거 구간 SARIMAX_ref 는 같은 분기 SARIMA 차수를 그대로 썼다."]}).to_excel(
            w, sheet_name="안내", index=False)
        firm.round(3).to_excel(w, sheet_name="기업별", index=False)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    main(ap.parse_args().workers)
