# -*- coding: utf-8 -*-
"""2g K8 5: 과거 구간 탐색 -- 탐색적, 판정에 쓰지 않음. method_2g_v1 (커밋 5dc9fdb) 5절. DB 쓰기 없음 (CSV 만).

- 구간 2024Q1~2026Q2, 기준일 pre5 (첫 공시일 5거래일 전, 공시 없으면 법정 기한, 2d K4 와 같음).
- 예측: 2d k4_forecast.run_entity(method_v2=True) + eval2g.live_2g.post (실전 스냅샷과 같은 M1·E5·OP_hat_E5·OP_hat_alt·표시).
  M1 (레이더 기업): fd_nowcast pre5 기록 중 10/19 스냅샷의 m1_source 모형 값 (그 분기 레이더 nowcast).
- 실적: 매출 fd_rev_adj_2g, 영업이익 2g 항목 (판관비 수정과 무관). 컨센서스는 기준일 이하 최신 관측.
- 오차: 매출 |ln(실적/예측)| (%), 영업이익 |예측 - 실적| / 최근 4분기(q-4~q-1) 평균 실적 매출 x 100.
- 요약: 전체·업종·시가총액 구간·집단별 중앙값, 기업별 'E5 가 컨센서스보다 나은 비율', 표시 열별 오차.
  비교는 세 예측이 모두 있는 같은 행끼리. 참고로 2d K5 와 같은 이중 군집 대응 차이 검정도 적는다.
python -m DATA.fdriver.eval2g.k8_explore [--workers 16]
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
from ..nowcast import features as ft                # noqa: E402
from ..nowcast.run import _calendar                 # noqa: E402
from . import common as C                           # noqa: E402
from .live_2g import post                           # noqa: E402

log = logging.getLogger(__name__)
OUT = output_path("2g", "K8", "explore")
NOTE = "탐색적, 판정에 쓰지 않음 (method_2g_v1 커밋 5dc9fdb 뒤 계산)"
MCAP_BINS = [0, 5e3, 2e4, 1e5, np.inf]          # 억원
MCAP_LABELS = ["5천억 미만", "5천억~2조", "2조~10조", "10조 이상"]


def worker(args) -> list:
    row, adj_e, it_e, ann_e, cal, m1q = args
    r = SimpleNamespace(**row)
    ann_df = ann_e.reset_index()
    ann_df["entity_id"] = r.entity_id
    try:
        recs = K4.run_entity(r, adj_e, it_e, ann_df, cal, quarters=None, method_v2=True)
    except Exception as ex:      # noqa: BLE001
        return [{"entity_id": r.entity_id, "quarter": "ERR", "op_note": repr(ex)[:120]}]
    items = it_e.pivot_table(index="period_end", columns="item", values="value")
    for c in ("op", "cogs", "sga"):
        if c not in items:
            items[c] = np.nan
    R_all = adj_e.set_index("period_end")["revenue_adj"].sort_index()
    out = []
    for rec in recs:
        try:
            out.append(post(rec, r, m1q.get(rec["quarter"]), items, R_all, ann_e, cal, pd.Timestamp(rec["asof_date"])))
        except Exception as ex:  # noqa: BLE001
            rec["alt_note"] = ("post_err " + repr(ex))[:80]
            out.append(rec)
    return out


def radar_m1_hist(radar_ids) -> dict:
    src = db.read_sql(f"SELECT DISTINCT entity_id, m1_source FROM `{C.T_LIVE}` WHERE m1_source IS NOT NULL")
    src = src[src["entity_id"].isin(radar_ids)].drop_duplicates("entity_id").set_index("entity_id")["m1_source"]
    out = {}
    for e, mid in src.items():
        h = db.read_sql("SELECT fiscal_quarter, asof_date, g_hat FROM fd_nowcast WHERE entity_id = :e AND model_id = :m "
                        "AND checkpoint LIKE :c AND fiscal_quarter >= '2024Q1'", {"e": e, "m": mid, "c": "%pre5%"})
        h = h.sort_values("asof_date").drop_duplicates("fiscal_quarter", keep="last")
        out[e] = {q: float(g) for q, g in zip(h["fiscal_quarter"], h["g_hat"]) if pd.notna(g)}
    return out


def mcap() -> pd.Series:
    m = db.read_sql("SELECT ticker, value FROM ks_listed_company_daily_marketcap WHERE indicator = '시가총액' AND "
                    "date = (SELECT MAX(date) FROM ks_listed_company_daily_marketcap WHERE date <= '2026-06-30')")
    m["entity_id"] = m["ticker"].str.replace("A", "", regex=False)
    return m.set_index("entity_id")["value"] / 100.0      # 백만원 -> 억원


def med(g: pd.DataFrame, cols) -> dict:
    return {"n": len(g), **{c: round(float(g[c].median()), 2) for c in cols}}


def main(workers: int = 16):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("DATA.fdriver.adapters.revenue").setLevel(logging.ERROR)
    t0 = time.time()
    cal = _calendar()
    tg = C.read_targets()
    rules = db.read_sql(f"SELECT entity_id, rule, flag_spread, flag_q4 FROM `{C.T_RULE}`")
    tg = tg.drop(columns=["rule"]).merge(rules, on="entity_id", how="left")
    ids = tg["entity_id"].tolist()
    adj = C.read_adj(ids)
    it, _ = C.items_2g(ids, adj)
    ann = K4.announce_dates(ids, cal)
    m1h = radar_m1_hist(tg.loc[tg["group_2g"] == "레이더39", "entity_id"].tolist())
    jobs = []
    for r in tg.itertuples():
        row = {"entity_id": r.entity_id, "status": "ts_only", "code": None, "rule": r.rule,
               "coverage_class": r.coverage_class, "flag_jump": r.flag_jump, "flag_basis": r.flag_basis,
               "flag_spread": r.flag_spread, "flag_q4": r.flag_q4}
        jobs.append((row, adj[adj["entity_id"] == r.entity_id], it[it["entity_id"] == r.entity_id],
                     ann[ann["entity_id"] == r.entity_id].set_index("period_end")["first_dt"], cal,
                     m1h.get(r.entity_id, {})))
    t_in = time.time() - t0
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for k, res in enumerate(ex.map(worker, jobs, chunksize=2)):
            rows += res
            if (k + 1) % 50 == 0:
                log.info("%d/%d (%.0fs)", k + 1, len(jobs), time.time() - t0)
    t_fc = time.time() - t0
    d = pd.DataFrame(rows)
    if "op_note" not in d:
        d["op_note"] = None
    errs = d[d["quarter"] == "ERR"][["entity_id", "op_note"]].to_dict(orient="records")
    d = d[d["quarter"] != "ERR"].copy()
    d["period_end"] = pd.to_datetime(d["period_end"])
    # 실적
    A = adj.set_index(["entity_id", "period_end"])["revenue_adj"]
    OPa = it[it["item"] == "op"].set_index(["entity_id", "period_end"])["value"]
    d["rev_actual"] = [A.get((e, p)) for e, p in zip(d["entity_id"], d["period_end"])]
    d["op_actual"] = [OPa.get((e, p)) for e, p in zip(d["entity_id"], d["period_end"])]
    d["rev_avg4"] = [np.nanmean([A.get((e, ft.quarter_shift(p, k)), np.nan) for k in (1, 2, 3, 4)])
                     for e, p in zip(d["entity_id"], d["period_end"])]
    cons = [kr_consensus.read_consensus(e, q, pd.Timestamp(a)) for e, q, a in
            zip(d["entity_id"], d["quarter"], d["asof_date"])]
    d["cons"] = [c["value"] for c in cons]
    d["cons_asof"] = [c["asof"] for c in cons]
    t_cons = time.time() - t0
    lg = lambda f: (np.abs(np.log(d["rev_actual"].astype(float) / d[f].astype(float))) * 100).where(
        (d["rev_actual"] > 0) & (d[f].astype(float) > 0))
    for k in ("E5", "cons", "B2", "E", "SARIMA", "ETS", "Theta"):
        d[f"err_{k}"] = lg(k)
    for k in ("OP_hat_E5", "OP_hat_alt", "OP_naive"):
        d[f"err_{k}"] = (d[k].astype(float) - d["op_actual"]).abs() / d["rev_avg4"] * 100
    g = tg.set_index("entity_id")
    for c in ("group_2g", "industry_group", "name"):
        d[c] = d["entity_id"].map(g[c])
    mc = mcap()
    d["mcap_eok"] = d["entity_id"].map(mc)
    d["mcap_bin"] = pd.cut(d["mcap_eok"], MCAP_BINS, labels=MCAP_LABELS, right=False).astype(str)
    d.to_csv(OUT / "k8_explore_rows.csv", index=False, encoding="utf-8-sig")
    # 비교 표본
    rv = d.dropna(subset=["err_E5", "err_cons", "err_B2"]).copy()
    op = d[d["rule"].isin(["R1", "R2", "R3"])].dropna(subset=["err_OP_hat_E5", "err_OP_hat_alt", "err_OP_naive"]).copy()
    rc, oc = ["err_E5", "err_cons", "err_B2"], ["err_OP_hat_E5", "err_OP_hat_alt", "err_OP_naive"]
    rv["e5_better"] = (rv["err_E5"] < rv["err_cons"]).astype(int)
    keep = ("n", "n_firms", "mean_diff", "se", "p_two", "share_a_better")
    pt = lambda x, a, b: {k: v for k, v in paired_test(x, a, b).items() if k in keep}
    res = {"note": NOTE, "errors": errs, "rows": len(d), "firms": int(d["entity_id"].nunique()),
           "rev_sample": med(rv, rc + ["err_E", "err_SARIMA", "err_ETS", "err_Theta"]),
           "rev_sample_flag_jump0": med(rv[rv["flag_jump"] == 0], rc),
           "rev_E5_better_than_cons_share": round(float(rv["e5_better"].mean()), 3),
           "rev_test_E5_vs_cons": pt(rv, "err_E5", "err_cons"), "rev_test_E5_vs_B2": pt(rv, "err_E5", "err_B2"),
           "op_sample": med(op, oc),
           "op_test_E5_vs_naive": pt(op, "err_OP_hat_E5", "err_OP_naive"),
           "op_test_alt_vs_E5": pt(op, "err_OP_hat_alt", "err_OP_hat_E5"),
           "op_by_rule": {k: med(x, oc) for k, x in op.groupby("rule")},
           "rev_by_group": {k: med(x, rc) for k, x in rv.groupby("group_2g")},
           "rev_by_mcap": {k: med(x, rc) for k, x in rv.groupby("mcap_bin")},
           "op_by_mcap": {k: med(x, oc) for k, x in op.groupby("mcap_bin")},
           "rev_flag_b2_jump": {str(k): med(x, rc) for k, x in rv.groupby("flag_b2_jump")},
           "rev_flag_m1_dir": {str(k): med(x, rc) for k, x in rv.groupby("flag_m1_dir")},
           "op_flag_op_sign": {str(k): med(x, oc) for k, x in op.groupby("flag_op_sign")},
           "m1_rows": int(d["g_M1"].notna().sum()),
           "seconds": {"inputs": round(t_in), "forecast": round(t_fc - t_in), "consensus_actuals": round(t_cons - t_fc),
                       "total": round(time.time() - t0)}, "workers": workers}
    # 업종·기업 표
    ind = pd.DataFrame([{"industry_group": k, **med(x, rc), "e5_better_share": round(float(x["e5_better"].mean()), 3)}
                        for k, x in rv.groupby("industry_group")]).sort_values("n", ascending=False)
    ind_op = pd.DataFrame([{"industry_group": k, **med(x, oc)} for k, x in op.groupby("industry_group")])
    ind = ind.merge(ind_op, on="industry_group", how="left", suffixes=("", "_op"))
    firm = rv.groupby(["entity_id", "name", "group_2g", "industry_group", "mcap_bin"]).agg(
        n=("e5_better", "size"), e5_better_share=("e5_better", "mean"), med_err_E5=("err_E5", "median"),
        med_err_cons=("err_cons", "median"), med_err_B2=("err_B2", "median")).reset_index()
    fo = op.groupby("entity_id").agg(med_err_OP_hat_E5=("err_OP_hat_E5", "median"),
                                     med_err_OP_hat_alt=("err_OP_hat_alt", "median"),
                                     med_err_OP_naive=("err_OP_naive", "median")).reset_index()
    firm = firm.merge(fo, on="entity_id", how="left")
    res["firm_e5_better_share_dist"] = firm["e5_better_share"].describe().round(3).to_dict()
    res["firms_e5_better_ge_half"] = int((firm["e5_better_share"] >= 0.5).sum())
    ind.to_csv(OUT / "k8_explore_by_industry.csv", index=False, encoding="utf-8-sig")
    firm.to_csv(OUT / "k8_explore_by_firm.csv", index=False, encoding="utf-8-sig")
    (OUT / "k8_explore.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with pd.ExcelWriter(OUT / "k8_explore.xlsx") as w:
        pd.DataFrame({"note": [NOTE]}).to_excel(w, sheet_name="안내", index=False)
        ind.to_excel(w, sheet_name="업종별", index=False)
        firm.round(3).to_excel(w, sheet_name="기업별", index=False)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    main(ap.parse_args().workers)
