# -*- coding: utf-8 -*-
"""2c 작업 2b 보정 (명세서 2c v4 자료 규칙 수정): 890590·890610·850180 복원, 3개 기업 드라이버·fd_nowcast 재생성.

1. 33개 기업 드라이버 자료를 registry rules='2b'(이전) 와 '2b2'(v4) 로 각각 만들어 비교한다. 자료가 바뀐 기업이
   지시한 3곳(삼성중공업, 한화오션, HD현대일렉트릭)과 다르면 멈춘다.
2. 3곳만 백업(step2b/backup_fd_nowcast_<종목>_2b2.csv, 처음 백업을 덮어쓰지 않음) -> 그 기업 행만 삭제 -> 재삽입.
   다른 30곳 행은 전후 해시로 확인. 예측 오차는 계산하지 않는다.
실행: python -m DATA.fdriver.stage2c.s2b2_regen
산출 (2c/step2b/d_regen_2b2): driver_frame_diff.csv, compare.csv, untouched_check.csv, figures/
"""
import pandas as pd

from ..config import output_path
from ..nowcast import features as ft
from ..nowcast import run
from . import registry
from . import drivers as dv
from .d_drivers_run import plot, quarterly_growth
from .s2b_d_regen import fingerprint, replace_entities, summarize

STEP2B = output_path("2c", "step2b")
OUT = output_path("2c", "step2b", "d_regen_2b2")
FIG = output_path("2c", "step2b", "d_regen_2b2", "figures")
TARGETS = ["010140", "042660", "267260"]


def frames(b):
    return {e: dv.driver_frame(x)[["key", "period_end", "value"]].reset_index(drop=True) for e, x in b["built"].items()}


def seg_info(b, e):
    segs = b["built"][e]["segments"]
    return {"segments": ",".join(segs), "end": ",".join(f"{s['end']:%Y-%m}" for s in segs.values()),
            "zero_filled_months": sum(s["zero_filled"] for s in segs.values()),
            "codes": ",".join(sorted({c for s in segs.values() for c in s["codes"]}))}


def main():
    b0 = registry.register(refresh=True, rules="2b")
    f0 = frames(b0)
    before = {e: {**seg_info(b0, e), **{f"g_{k}": v for k, v in _growth(e).items()}} for e in TARGETS}
    b1 = registry.register(refresh=True, rules="2b2")
    f1 = frames(b1)
    names = dict(zip(b1["mapping"]["companies"]["entity_id"], b1["mapping"]["companies"]["name"]))

    diff = []
    for e in f1:
        same = f0[e].equals(f1[e])
        diff.append({"entity_id": e, "name": names[e], "same_driver_frame": same,
                     "rows_2b": len(f0[e]), "rows_2b2": len(f1[e])})
    diff = pd.DataFrame(diff)
    diff.to_csv(OUT / "driver_frame_diff.csv", index=False, encoding="utf-8-sig")
    changed = sorted(diff.loc[~diff["same_driver_frame"], "entity_id"])
    if changed != sorted(TARGETS):
        print(diff[~diff["same_driver_frame"]].to_string(index=False))
        raise RuntimeError(f"자료가 바뀐 기업 {changed} 이 지시한 3곳 {TARGETS} 과 다름: 재생성하지 않고 멈춤")

    others_before = fingerprint(exclude=TARGETS)
    old, results, deleted, written = replace_entities(TARGETS, run.nowcast_entity, STEP2B, suffix="_2b2")
    others_after = fingerprint(exclude=TARGETS)
    chk = others_before.merge(others_after, on="entity_id", how="outer", suffixes=("_before", "_after"))
    chk["same"] = (chk["n_rows_before"] == chk["n_rows_after"]) & (chk["sha256_before"] == chk["sha256_after"])
    chk.to_csv(OUT / "untouched_check.csv", index=False, encoding="utf-8-sig")

    rows = []
    for e in TARGETS:
        a = seg_info(b1, e)
        g = _growth(e)
        s0, s1 = summarize(old[e], e), summarize(results[e]["rows"], e)
        rows.append({"entity_id": e, "name": names[e],
                     "codes_before": before[e]["codes"], "codes_after": a["codes"],
                     "driver_q_before": f"{before[e]['g_first']}~{before[e]['g_last']}",
                     "driver_q_after": f"{g['first']}~{g['last']}",
                     "zero_filled_before": before[e]["zero_filled_months"], "zero_filled_after": a["zero_filled_months"],
                     "nowcast_rows_before": s0["n_rows"], "nowcast_rows_after": s1["n_rows"],
                     "pre5_main_q_before": s0["pre5_main_quarters"], "pre5_main_q_after": s1["pre5_main_quarters"],
                     "main_last_q_before": s0["main_last_q"], "main_last_q_after": s1["main_last_q"]})
        plot(e, names[e], {sp.model_id.replace("M1:", ""): quarterly_growth(sp) for sp in ft.DRIVER_SPECS[e]},
             FIG / f"{e}_{names[e]}.png")
    cmp = pd.DataFrame(rows)
    cmp.to_csv(OUT / "compare.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 80)
    print(f"deleted {deleted}, written {written}, untouched {int(chk['same'].sum())}/{len(chk)} same")
    print(cmp.T.to_string())


def _growth(e):
    sp = [s for s in ft.DRIVER_SPECS[e] if s.model_id == "M1:export"][0]
    g = quarterly_growth(sp)
    return {"first": g.index.min().strftime("%Y-%m"), "last": g.index.max().strftime("%Y-%m"), "n": len(g)}


if __name__ == "__main__":
    main()
