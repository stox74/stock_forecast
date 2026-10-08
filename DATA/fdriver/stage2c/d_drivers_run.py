# -*- coding: utf-8 -*-
"""2c 작업 2 (d) 실행: 코드 존재 확인, 6자리 전개 점검, 카메라모듈 판정, 기업별 드라이버 기간·세그먼트 표, 그림.
DB 쓰기 없음. 실행: python -m DATA.fdriver.stage2c.d_drivers_run
산출 (2c/step2/d_drivers): code_check.csv, resolved_codes.csv, expansion_check.csv, camera_check.txt,
  segment_table.csv, driver_period.csv, weight_range.csv, driver_growth_quarterly.csv, figures/<종목코드>_<이름>.png
그림: 기업별 드라이버 분기 성장률 (전년 동기 대비 로그, 분기 3개월 모두 있을 때). 매출은 겹쳐 그리지 않는다.
"""
import numpy as np
import pandas as pd

from ..config import output_path
from ..nowcast import features as ft
from . import drivers as dv
from . import registry

OUT = output_path("2c", "step2", "d_drivers")
FIG = output_path("2c", "step2", "d_drivers", "figures")
COLORS = ["#2a78d6", "#eb6834"]
T_ALL = pd.Timestamp("2100-01-01")


def quarterly_growth(spec, frame=None) -> pd.Series:
    drv = ft.prepare_driver(spec.loader() if frame is None else frame)
    if drv.empty:
        return pd.Series(dtype="float64")
    qs = sorted(drv["qend"].unique())
    return ft.training_features(spec, drv, [pd.Timestamp(q) for q in qs], T_ALL, [0, 1, 2]).dropna()


def plot(e, name, series: dict, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ..evaluate.forecast_eval import _setup_fonts
    _setup_fonts()
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for i, (lab, s) in enumerate(series.items()):
        if len(s):
            ax.plot(s.index, s.values, color=COLORS[i % 2], lw=2, label=lab)
    ax.axhline(0, color="#52514e", lw=0.8)
    ax.grid(axis="y", color="#e6e5e0", lw=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.set_title(f"{name} ({e}) 드라이버 분기 성장률 (전년 동기 대비 로그)", fontsize=11, loc="left")
    ax.set_ylabel("로그 YoY")
    if len(series) >= 2:
        ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    b = registry.build()
    m, res, inv = b["mapping"], b["resolved"], b["inventory"]
    names = dict(zip(m["companies"]["entity_id"], m["companies"]["name"]))
    res.to_csv(OUT / "resolved_codes.csv", index=False, encoding="utf-8-sig")

    # 1. 'DB 존재 확인 필요' 코드
    chk = m["codes"][m["codes"]["needs_db_check"]].copy()
    rows = []
    for r in chk.itertuples():
        kids = sorted(c for c in inv.index if len(c) == 6 and c.startswith(r.hs))
        rr = res[(res["entity_id"] == r.entity_id) & (res["hs"] == r.hs)]
        rows.append({"entity_id": r.entity_id, "name": r.name, "hs": r.hs, "segment": r.segment, "expand6": r.expand6,
                     "hs_in_db": r.hs in inv.index,
                     "hs_period": (f"{inv.loc[r.hs, 'first']:%Y-%m}~{inv.loc[r.hs, 'last']:%Y-%m}" if r.hs in inv.index else ""),
                     "children_in_db": ",".join(kids), "used_leaves": ",".join(rr["leaf"].dropna()),
                     "status": ",".join(sorted(set(rr["status"]))),
                     "result": "사용" if rr["leaf"].notna().any() else "제외(DB 없음)"})
    code_check = pd.DataFrame(rows)
    code_check.to_csv(OUT / "code_check.csv", index=False, encoding="utf-8-sig")

    # 2. 6자리 전개 점검
    ex = dv.expansion_check(res)
    nochild = res[res["status"] == "no_children_4digit"][["entity_id", "hs"]].drop_duplicates()
    ex.to_csv(OUT / "expansion_check.csv", index=False, encoding="utf-8-sig")
    nochild.to_csv(OUT / "expansion_no_children.csv", index=False, encoding="utf-8-sig")

    # 3. 카메라모듈
    cam = b["camera"]
    (OUT / "camera_check.txt").write_text(
        f"개정 후 코드(DB): {cam['post_codes']}\n연결 시계열: {cam['first']:%Y-%m} ~ {cam['last']:%Y-%m}\n"
        f"2014-01 이후 빈 달: {cam['gaps_since_2014'] or '없음'}\n"
        f"개정 전 12개월(2021-01~12) 평균 {cam['pre12_mean']:,.0f} USD, 개정 후 12개월(2022-01~12) 평균 {cam['post12_mean']:,.0f} USD, "
        f"비 {cam['ratio_post_pre']:.3f}\n판정: {'사용' if cam['use'] else '드라이버 없음'}\n", encoding="utf-8")
    cam["series"].rename("value").to_csv(OUT / "camera_series.csv", encoding="utf-8-sig")

    # 4. 기업별 세그먼트·기간
    seg_rows, per_rows, wr_rows, growth_rows = [], [], [], []
    for e, bt in b["built"].items():
        for seg, s in bt["segments"].items():
            leaves = res[(res["entity_id"] == e) & (res["leaf"].notna()) & ((res["segment"] == seg) | (seg == "all"))]
            seg_rows.append({"entity_id": e, "name": names[e], "segment": seg, "excel_codes": ",".join(sorted(leaves["hs"].unique())),
                             "n_excel_codes": leaves["hs"].nunique(), "n_leaf_codes": len(s["codes"]),
                             "start": f"{s['start']:%Y-%m}" if s.get("start") is not None else None,
                             "end": f"{s['end']:%Y-%m}" if s.get("end") is not None else None,
                             "end_by": ",".join(s.get("end_by") or []), "n_months": s.get("n_months"),
                             "zero_filled_months": s.get("zero_filled")})
        for seg, why in bt["dropped"].items():
            seg_rows.append({"entity_id": e, "name": names[e], "segment": seg, "dropped": why})
        specs = b["specs"][e]
        fig_series = {}
        for sp in specs:
            g = quarterly_growth(sp)
            fig_series[sp.model_id.replace("M1:", "")] = g
            for q, v in g.items():
                growth_rows.append({"entity_id": e, "model_id": sp.model_id, "quarter": q, "g": v})
            drv = ft.prepare_driver(sp.loader())
            months = pd.date_range(drv["period_end"].min(), drv["period_end"].max(), freq="ME") if len(drv) else []
            have = set(drv["period_end"]) if len(drv) else set()
            per_rows.append({"entity_id": e, "name": names[e], "model_id": sp.model_id, "driver_key": sp.driver_key,
                             "weighted": sp.weights is not None,
                             "first_month": f"{min(have):%Y-%m}" if have else None,
                             "last_month": f"{max(have):%Y-%m}" if have else None,
                             "missing_months": int(sum(1 for x in months if x not in have and
                                                       (sp.weights is None or True))) if have else None,
                             "growth_quarters": len(g),
                             "first_growth_q": g.index.min().strftime("%Y-%m") if len(g) else None,
                             "last_growth_q": g.index.max().strftime("%Y-%m") if len(g) else None})
        if e in b["weight_fns"]:
            wf = b["weight_fns"][e]
            qs = pd.date_range("2010-03-31", "2026-06-30", freq="QE")
            ws = []
            for q in qs:
                w = {k: v for k, v in wf(q, T_ALL).items() if v > 0}
                tot = sum(w.values())
                ws.append({"q": q, **{k: v / tot for k, v in w.items()}} if tot else {"q": q})
            wdf = pd.DataFrame(ws).set_index("q")
            for seg in bt["segments"]:
                col = wdf[seg] if seg in wdf else pd.Series(dtype=float)
                wr_rows.append({"entity_id": e, "name": names[e], "segment": seg,
                                "w_min": col.min(), "w_max": col.max(), "quarters_with_weight": int(col.notna().sum()),
                                "first_q_with_weight": col.dropna().index.min().strftime("%Y-%m") if col.notna().any() else None})
            wdf.to_csv(OUT / f"weights_by_quarter_{e}.csv", encoding="utf-8-sig")
        plot(e, names[e], fig_series, FIG / f"{e}_{names[e]}.png")
    pd.DataFrame(seg_rows).to_csv(OUT / "segment_table.csv", index=False, encoding="utf-8-sig")
    per = pd.DataFrame(per_rows)
    per.to_csv(OUT / "driver_period.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(wr_rows).to_csv(OUT / "weight_range.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(growth_rows).to_csv(OUT / "driver_growth_quarterly.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    print(code_check.to_string(index=False))
    print(ex.round(3).to_string(index=False))
    print(nochild.to_string(index=False))
    print((OUT / "camera_check.txt").read_text(encoding="utf-8"))
    print(pd.DataFrame(seg_rows).to_string(index=False))
    print(per.to_string(index=False))
    print(pd.DataFrame(wr_rows).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
