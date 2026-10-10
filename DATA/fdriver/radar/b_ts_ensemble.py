# -*- coding: utf-8 -*-
"""R1 (b): 39곳 fd_nowcast 에 TS, E 추가 (기존 행은 건드리지 않음) + 참고 성적 (2c 33곳, 한 번만).

대상 행: fd_nowcast 의 전체 기간 변형(model_id 에 ':excovid' 없음) 기준일 전부. 같은 (기업, 분기, 기준일) 에 TS 와 E 를 만든다.
TS 는 기준일에 공개된 매출(같은 값이면 같은 결과)이 같으면 한 번만 계산한다 (병렬 프로세스).
참고 성적: pre5, 2c 33곳, 기업별로 E(또는 TS)와 B2 가 모두 있는 공통 분기의 상대 RMSE (오차는 forecast_eval 정의).
  전체 기간과 코로나 분기(2020Q1~2021Q4) 제외. 결과 파일이 있으면 다시 계산하지 않는다. 이 결과로 아무것도 바꾸지 않는다.
실행: python -m DATA.fdriver.radar.b_ts_ensemble
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import db, entity
from ..config import output_path
from ..evaluate import forecast_eval as fe
from ..events import fiscal_quarter_end
from ..nowcast import features as ft
from ..nowcast import run
from . import ts
from .universe import universe

OUT = output_path("radar", "b_ts")
WORKERS = 12
COVID = ("2020Q1", "2021Q4")


def _register():
    from ..stage2c import registry
    registry.register(rules="2b2")


def entity_tasks(e: str) -> pd.DataFrame:
    """그 기업의 TS 계산 단위 (분기, 기준일, 공개 매출 상태)."""
    nc = db.read_sql("SELECT fiscal_quarter, asof_date, rev_base FROM fd_nowcast WHERE entity_id = :e "
                     "AND model_id = 'B2'", {"e": e})
    nc["asof_date"] = pd.to_datetime(nc["asof_date"])
    return nc


def ts_entity(e: str) -> pd.DataFrame:
    """한 기업의 TS (기준일별). 프로세스 하나에서 실행."""
    rv = ft.revenue_frame(e)
    tasks = entity_tasks(e)
    known, done, rows = {}, {}, []
    for r in tasks.itertuples():
        q = fiscal_quarter_end(r.fiscal_quarter)
        if r.asof_date not in known:
            known[r.asof_date] = ft.revenue_known(rv, r.asof_date)
        R = known[r.asof_date]
        hist = R[R.index < q]
        key = (q, tuple(hist.index), tuple(np.round(hist.values, 0)), round(float(r.rev_base), 0))
        if key not in done:
            done[key] = ts.ts_forecast(R, q, float(r.rev_base))
        res = done[key]
        rows.append({"entity_id": e, "fiscal_quarter": r.fiscal_quarter, "asof_date": r.asof_date, **res})
    return pd.DataFrame(rows)


def run_ts(ids):
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        parts = list(ex.map(ts_entity, ids))
    return pd.concat(parts, ignore_index=True)


def build_rows(ids, tsr: pd.DataFrame) -> pd.DataFrame:
    _register()
    out = []
    for e in ids:
        nc = db.read_sql("SELECT * FROM fd_nowcast WHERE entity_id = :e", {"e": e})
        nc = nc[~nc["model_id"].str.endswith(":excovid")].copy()
        nc["asof_date"] = pd.to_datetime(nc["asof_date"])
        common_cols = ["entity_id", "fiscal_quarter", "asof_date", "checkpoint", "rev_base", "rev_actual_first",
                       "rev_actual_final", "first_dt"]
        base = nc[nc["model_id"] == "B2"][common_cols + ["g_hat"]].rename(columns={"g_hat": "g_b2"})
        dm = ts.e_component(e)
        if dm is not None:
            d = nc[(nc["model_id"] == dm) & (nc["drivers_used"].fillna("") != "fallback:B3")][
                ["fiscal_quarter", "asof_date", "g_hat"]].rename(columns={"g_hat": "g_drv"})
            base = base.merge(d, on=["fiscal_quarter", "asof_date"], how="left")
        else:
            base["g_drv"] = np.nan
        t = tsr[tsr["entity_id"] == e][["fiscal_quarter", "asof_date", "g_hat", "n_train", "label", "reason"]]
        base = base.merge(t.rename(columns={"g_hat": "g_ts"}), on=["fiscal_quarter", "asof_date"], how="left")
        for r in base.to_dict("records"):
            common = {k: r[k] for k in common_cols}
            common["asof_date"] = r["asof_date"].date()
            if r["g_ts"] is not None and pd.notna(r["g_ts"]):
                out.append({**common, "model_id": "TS", "g_hat": r["g_ts"], "g_se": None,
                            "rev_hat": float(r["rev_base"] * np.exp(r["g_ts"])), "coverage": None,
                            "n_train": int(r["n_train"]), "drivers_used": r["label"]})
            en = ts.ensemble(r["g_drv"], r["g_b2"], r["g_ts"], dm)
            if en["g_hat"] is not None:
                out.append({**common, "model_id": "E", "g_hat": en["g_hat"], "g_se": None,
                            "rev_hat": float(r["rev_base"] * np.exp(en["g_hat"])), "coverage": None,
                            "n_train": en["n_components"], "drivers_used": en["label"]})
    return pd.DataFrame(out, columns=run.NOWCAST_COLUMNS)


def reference(ids2c) -> pd.DataFrame:
    d = fe.load_nowcast(ids2c)
    d = d[(d["cp"] == "pre5") & (d["variant"] == "all") & d["model"].isin(["E", "TS", "B2"])]
    rows = []
    for label, keep in [("전체 기간", lambda q: True), ("코로나 2020Q1~2021Q4 제외", lambda q: not (COVID[0] <= q <= COVID[1]))]:
        dd = d[d["fiscal_quarter"].map(keep)]
        for m in ["E", "TS"]:
            for e in ids2c:
                g = dd[(dd["entity_id"] == e) & dd["model"].isin([m, "B2"])]
                pv = g.pivot_table(index="fiscal_quarter", columns="model", values="err_first", aggfunc="first").dropna()
                if m not in pv or "B2" not in pv or pv.empty:
                    rows.append({"sample": label, "model": m, "entity_id": e, "n": 0, "rel_rmse_vs_B2": np.nan})
                    continue
                rows.append({"sample": label, "model": m, "entity_id": e, "n": len(pv),
                             "rel_rmse_vs_B2": float(np.sqrt((pv[m] ** 2).mean()) / np.sqrt((pv["B2"] ** 2).mean()))})
    return pd.DataFrame(rows)


def main():
    u = universe()
    ids = list(u["entity_id"])
    tsr = run_ts(ids)
    tsr.to_csv(OUT / "ts_raw.csv", index=False, encoding="utf-8-sig")
    rows = build_rows(ids, tsr)
    n = db.write_fd(rows, run.T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    names = dict(zip(u["entity_id"], u["name"]))
    # 생성 현황 (기준일 단위, pre5 분기 단위)
    b2 = db.read_sql("SELECT entity_id, fiscal_quarter, checkpoint FROM fd_nowcast WHERE model_id = 'B2'")
    b2_pre5 = b2[b2["checkpoint"].str.contains("pre5")].groupby("entity_id").size()
    cnt = rows.assign(pre5=rows["checkpoint"].str.contains("pre5")).groupby(["entity_id", "model_id"]).agg(
        rows=("g_hat", "size"), pre5_quarters=("pre5", "sum")).unstack("model_id")
    cnt.columns = [f"{b}_{a}" for a, b in cnt.columns]
    cnt = cnt.reindex(ids)
    cnt["b2_pre5_quarters"] = b2_pre5.reindex(ids).values
    cnt["TS_pre5_missing"] = cnt["b2_pre5_quarters"] - cnt["TS_pre5_quarters"].fillna(0)
    cnt["E_pre5_missing"] = cnt["b2_pre5_quarters"] - cnt["E_pre5_quarters"].fillna(0)
    e_part = rows[(rows["model_id"] == "E") & rows["checkpoint"].str.contains("pre5")]
    cnt["E_pre5_partial"] = e_part[e_part["drivers_used"].str.contains("missing")].groupby("entity_id").size().reindex(ids).fillna(0).astype(int).values
    ts_reason = tsr.groupby(["entity_id", "reason"]).size().unstack(fill_value=0).reindex(ids).fillna(0).astype(int)
    cnt = cnt.join(ts_reason.add_prefix("ts_reason_"))
    cnt.insert(0, "name", [names[e] for e in cnt.index])
    cnt.to_csv(OUT / "ts_e_counts.csv", encoding="utf-8-sig")

    ref_file = OUT / "reference_rel_rmse.csv"
    ids2c = list(u.loc[u["group"] == "2c", "entity_id"])
    if ref_file.exists():
        print("참고 성적은 이미 한 번 계산됨 (다시 계산하지 않음)")
    else:
        ref = reference(ids2c)
        ref.insert(3, "name", ref["entity_id"].map(names))
        ref.to_csv(ref_file, index=False, encoding="utf-8-sig")
        summ = (ref.dropna(subset=["rel_rmse_vs_B2"]).groupby(["sample", "model"])
                .agg(n_firms=("rel_rmse_vs_B2", "size"), median=("rel_rmse_vs_B2", "median"),
                     share_below_1=("rel_rmse_vs_B2", lambda s: float((s < 1).mean())), quarters=("n", "sum"))
                .reset_index())
        summ.to_csv(OUT / "reference_summary.csv", index=False, encoding="utf-8-sig")
        print(summ.round(4).to_string(index=False))
    pd.set_option("display.width", 250)
    print(f"written {n} rows (TS {int((rows['model_id'] == 'TS').sum())}, E {int((rows['model_id'] == 'E').sum())})")
    print(cnt.to_string())


if __name__ == "__main__":
    main()
