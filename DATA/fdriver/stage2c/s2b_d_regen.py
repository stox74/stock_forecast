# -*- coding: utf-8 -*-
"""2c 작업 2b (d): 바뀐 기업만 드라이버와 fd_nowcast 재생성 (2a nowcast_entity 그대로, 드라이버는 registry rules='2b').

순서: 바뀐 기업 목록 = (c) changed_entities.csv -> 그 기업 fd_nowcast 행을 CSV 로 백업 (step2b/backup_fd_nowcast_<종목>.csv)
  -> 전부 계산 (실패하면 아무것도 지우지 않음) -> 그 기업 행만 지우고 다시 넣음. 2a 시범 기업과 다른 기업 행은 건드리지 않음
  (전후 기업별 행 수와 g_hat 합으로 확인).
예측 오차는 계산하지 않는다.
실행: python -m DATA.fdriver.stage2c.s2b_d_regen
산출 (2c/step2b/d_regen): driver_period_compare.csv, nowcast_compare.csv, untouched_check.csv, sector_share.csv,
  figures/<종목>_<이름>.png
"""
import hashlib

import pandas as pd

from .. import db, entity
from ..config import output_path
from ..nowcast import features as ft
from ..nowcast import run
from . import registry
from .d_drivers_run import plot, quarterly_growth

STEP2B = output_path("2c", "step2b")
OUT = output_path("2c", "step2b", "d_regen")
FIG = output_path("2c", "step2b", "d_regen", "figures")
MIN_EVAL = 8


def changed_entities() -> list:
    return list(pd.read_csv(output_path("2c", "step2b", "c_codes") / "changed_entities.csv", dtype=str)["entity_id"])


def fingerprint(exclude=()) -> pd.DataFrame:
    """기업별 행 수와 내용 해시 (다른 기업 행이 그대로인지 확인용)."""
    d = db.read_sql("SELECT entity_id, fiscal_quarter, asof_date, model_id, g_hat, rev_hat FROM fd_nowcast")
    d = d[~d["entity_id"].isin(exclude)].sort_values(["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    rows = []
    for e, g in d.groupby("entity_id"):
        h = hashlib.sha256(g.to_csv(index=False, float_format="%.12g").encode()).hexdigest()
        rows.append({"entity_id": e, "n_rows": len(g), "sha256": h})
    return pd.DataFrame(rows)


def replace_entities(ids, compute, backup_dir, suffix: str = ""):
    """ids 기업의 fd_nowcast 행을 백업(처음 한 번) -> 전부 계산 -> 그 기업 행만 지우고 다시 넣는다.
    compute(entity_id) -> {"rows": DataFrame}. 시범 기업이 있으면 멈춘다. 반환 (백업, 결과, 지운 수, 쓴 수)."""
    ids = list(ids)
    bad = [e for e in ids if e in entity.PILOTS]
    if bad or not ids:
        raise RuntimeError(f"재생성 대상 오류 (시범 기업 {bad} 또는 빈 목록)")
    old = {}
    for e in ids:
        f = backup_dir / f"backup_fd_nowcast_{e}{suffix}.csv"
        if f.exists():            # 처음 백업(작업 2 행)을 덮어쓰지 않는다. 전후 비교도 그 백업 기준
            old[e] = pd.read_csv(f, dtype={"entity_id": str})
        else:
            o = db.read_sql("SELECT * FROM fd_nowcast WHERE entity_id = :e", {"e": e})
            o.to_csv(f, index=False, encoding="utf-8-sig")
            old[e] = o
    results = {e: compute(e) for e in ids}       # 전부 계산 먼저 (실패하면 아무것도 지우지 않음)
    new = pd.concat([r["rows"] for r in results.values()], ignore_index=True)
    deleted = db.delete_fd(run.T_NOWCAST, {"entity_id": ids})
    written = db.write_fd(new, run.T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    return old, results, deleted, written


def summarize(rows: pd.DataFrame, e: str) -> dict:
    a = rows.copy()
    a["variant"] = a["model_id"].str.endswith(":excovid").map({True: "excovid", False: "all"})
    a["model"] = a["model_id"].str.replace(":excovid", "", regex=False)
    g = a[a["variant"] == "all"]
    comps = [s.model_id for s in ft.DRIVER_SPECS.get(e, []) if not s.aux and s.alt is None]
    main_model = "M4:combo" if len(comps) >= 2 else ("M4:" + comps[0].split(":", 1)[1] if comps else "B2")
    pre5 = g[g["checkpoint"].astype(str).str.contains("pre5")]
    mq = sorted(pre5.loc[pre5["model"] == main_model, "fiscal_quarter"].unique())
    return {"n_rows": len(a), "pre5_main_quarters": len(mq), "main_first_q": mq[0] if mq else None,
            "main_last_q": mq[-1] if mq else None, "main_model": main_model}


def main():
    ids = changed_entities()
    b = registry.register(refresh=True, rules="2b")
    names = dict(zip(b["mapping"]["companies"]["entity_id"], b["mapping"]["companies"]["name"]))

    others_before = fingerprint(exclude=ids)
    old, results, deleted, written = replace_entities(ids, run.nowcast_entity, STEP2B)
    others_after = fingerprint(exclude=ids)
    chk = others_before.merge(others_after, on="entity_id", how="outer", suffixes=("_before", "_after"))
    chk["same"] = (chk["n_rows_before"] == chk["n_rows_after"]) & (chk["sha256_before"] == chk["sha256_after"])
    chk.to_csv(OUT / "untouched_check.csv", index=False, encoding="utf-8-sig")

    per0 = pd.read_csv(output_path("2c", "step2", "d_drivers") / "driver_period.csv", dtype=str)
    drows, nrows = [], []
    for e in ids:
        series = {}
        for sp in ft.DRIVER_SPECS[e]:
            gq = quarterly_growth(sp)
            series[sp.model_id.replace("M1:", "")] = gq
            p0 = per0[(per0["entity_id"] == e) & (per0["model_id"] == sp.model_id)]
            drows.append({"entity_id": e, "name": names[e], "model_id": sp.model_id,
                          "before_first_q": p0["first_growth_q"].iloc[0] if len(p0) else None,
                          "before_last_q": p0["last_growth_q"].iloc[0] if len(p0) else None,
                          "after_first_q": gq.index.min().strftime("%Y-%m") if len(gq) else None,
                          "after_last_q": gq.index.max().strftime("%Y-%m") if len(gq) else None,
                          "after_growth_quarters": len(gq), "weighted": sp.weights is not None})
        plot(e, names[e], series, FIG / f"{e}_{names[e]}.png")
        s0 = summarize(old[e], e) if len(old[e]) else {}
        s1 = summarize(results[e]["rows"], e)
        nrows.append({"entity_id": e, "name": names[e], "main_model": s1["main_model"],
                      "rows_before": s0.get("n_rows"), "rows_after": s1["n_rows"],
                      "pre5_main_q_before": s0.get("pre5_main_quarters"), "pre5_main_q_after": s1["pre5_main_quarters"],
                      "main_last_q_before": s0.get("main_last_q"), "main_last_q_after": s1["main_last_q"],
                      "main_first_q_after": s1["main_first_q"], "flag_lt8": s1["pre5_main_quarters"] < MIN_EVAL})
    dp = pd.DataFrame(drows)
    dp.to_csv(OUT / "driver_period_compare.csv", index=False, encoding="utf-8-sig")
    nc = pd.DataFrame(nrows)
    nc.to_csv(OUT / "nowcast_compare.csv", index=False, encoding="utf-8-sig")

    comp = b["mapping"]["companies"]
    comp = comp[comp["included"] & ~comp["entity_id"].isin(entity.PILOTS)]
    sec = comp.groupby("sector").size().sort_values(ascending=False)
    pd.concat([sec.rename("n"), (sec / sec.sum()).rename("share")], axis=1).to_csv(OUT / "sector_share.csv", encoding="utf-8-sig")
    # 전 기업 8분기 미만 재확인 (바뀌지 않은 기업은 작업 2 entity_summary 값)
    s2 = pd.read_csv(output_path("2c", "step2", "f_nowcast") / "entity_summary.csv", dtype={"entity_id": str})
    s2 = s2.set_index("entity_id")["pre5_main_quarters"].to_dict()
    s2.update(dict(zip(nc["entity_id"], nc["pre5_main_q_after"])))
    lt8 = {e: n for e, n in s2.items() if n < MIN_EVAL}

    pd.set_option("display.width", 250)
    print(f"deleted {deleted}, written {written}, untouched entities all same: {bool(chk['same'].all())} ({len(chk)})")
    print(dp.to_string(index=False))
    print(nc.to_string(index=False))
    print("max sector share", round(float((sec / sec.sum()).max()), 3), sec.idxmax(), "| lt8:", lt8 or "없음")


if __name__ == "__main__":
    main()
