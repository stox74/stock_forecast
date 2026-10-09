# -*- coding: utf-8 -*-
"""2c 작업 3 3절 보고 항목(판정에 쓰지 않음)과 4절 그림. 판정 계산(s3_c_run) 뒤에 실행. DB 쓰기 없음.

판정 결과 파일(judgement/c1_pairs.csv, c2_signals_main.csv, c3_events.csv)을 읽어 같은 자료로 계산한다.
1 업종별 C1~C3 / 2 기업별 표 / 3 구간별(사전·발표 반응·사후 표류) / 4 코로나 2020Q1~2021Q4 제외 / 5 Cook's distance 상위 1% 제외 C3
6 M1·B1·B3 vs B2 상대 RMSE 중앙값 / 7 환율 기술통계 / 8 2a 시범 6곳 / 9 2b 규칙 대비 v4 (3곳)
실행: python -m DATA.fdriver.stage2c.s3_d_extras            (판정 때 이벤트 날짜, 결과 extras/, figures/)
      python -m DATA.fdriver.stage2c.s3_d_extras fixed      (월간 공시 버그를 고친 이벤트 날짜, extras_fixed/, figures_fixed/)
"""
import sys
import math

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from .. import db, entity
from ..config import output_path
from ..evaluate import forecast_eval as fe
from ..events import fiscal_quarter_end
from ..nowcast import features as ft
from . import registry
from . import s3_lib as L

J = output_path("2c", "step3", "judgement")
FIXED = "fixed" in sys.argv[1:]
OUT = output_path("2c", "step3", "extras_fixed" if FIXED else "extras")
FIG = output_path("2c", "step3", "figures_fixed" if FIXED else "figures")
COVID = ("2020Q1", "2021Q4")
FX_FIRMS_EXCLUDE = ("080160", "114090", "034230", "008770")     # 관광 드라이버 기업 (환율 표에서 뺌)


def load():
    pe = pd.read_csv(J / "c1_pairs.csv", dtype={"entity_id": str})
    sig = pd.read_csv(J / "c2_signals_main.csv", dtype={"entity_id": str})
    evf = (output_path("2c", "step3", "fix_eventdate") / "c3_events_fixed.csv") if FIXED else (J / "c3_events.csv")
    ev = pd.read_csv(evf, dtype={"entity_id": str})
    ent = pd.read_csv(J / "c1_by_entity.csv", dtype={"entity_id": str})
    return pe, sig, ev, ent


def all_tests(pe, sig, ev, main):
    ent = L.c1_by_entity(pe, main) if len(pe) else pd.DataFrame()
    out = []
    if len(ent):
        out.append(L.c1a(ent))
    if pe["fiscal_quarter"].nunique() >= 3:
        out.append(L.c1b(pe))
    if len(sig) >= 3:
        out.append(L.c2(sig))
    r3 = L.c3(ev)
    out.append({k: v for k, v in r3.items() if k != "_fit"})
    return L.verdict(out)


def cooks_drop(ev: pd.DataFrame, y="car_trade_pct", share=0.01):
    d = ev.dropna(subset=[y, "nsur_mom", "nsur_drv"]).copy()
    f = smf.ols(f"{y} ~ nsur_mom + nsur_drv + C(entity_id)", data=d).fit()
    d["cooks_d"] = f.get_influence().cooks_distance[0]
    k = math.ceil(len(d) * share)
    drop = d.sort_values("cooks_d", ascending=False).head(k)
    return d.drop(drop.index), drop


def fx_quarterly() -> pd.DataFrame:
    m = db.read_sql("SELECT ym, is_complete, usdkrw_avg FROM v_korea_fx_monthly_wide")
    m = m[m["is_complete"] == 1].copy()
    m["ym"] = pd.to_datetime(m["ym"])
    m["q"] = m["ym"] + pd.offsets.QuarterEnd(0)
    q = m.groupby("q").agg(usdkrw_q=("usdkrw_avg", lambda s: float(pd.to_numeric(s).mean())), n_months=("usdkrw_avg", "size"))
    q = q[q["n_months"] == 3]
    q["fx_yoy_log"] = np.log(q["usdkrw_q"]).diff(4)
    return q


def rel_median(d_all: pd.DataFrame, ids, model_of) -> pd.DataFrame:
    rows = []
    for e in ids:
        m = model_of(e)
        g = d_all[(d_all["entity_id"] == e) & (d_all["model"].isin([m, "B2"]))]
        pv = g.pivot_table(index="fiscal_quarter", columns="model", values="err_first", aggfunc="first").dropna()
        if m not in pv or "B2" not in pv or len(pv) == 0:
            continue
        rows.append({"entity_id": e, "model": m, "n": len(pv),
                     "rel_rmse": float(np.sqrt((pv[m] ** 2).mean()) / np.sqrt((pv["B2"] ** 2).mean()))})
    return pd.DataFrame(rows)


def main():
    b = registry.register(refresh=False, rules="2b2")
    comp = b["mapping"]["companies"]
    comp = comp[comp["included"] & ~comp["entity_id"].isin(entity.PILOTS)]
    names, sector = dict(zip(comp["entity_id"], comp["name"])), dict(zip(comp["entity_id"], comp["sector"]))
    ids = list(comp["entity_id"])
    main = {e: L.main_model_of(e) for e in ids}
    pe, sig, ev, ent = load()
    res = {}

    # 1 업종별
    rows = []
    for s, es_ in comp.groupby("sector")["entity_id"]:
        es_ = list(es_)
        t = all_tests(pe[pe["entity_id"].isin(es_)], sig[sig["entity_id"].isin(es_)], ev[ev["entity_id"].isin(es_)], main)
        t.insert(0, "sector", s)
        t.insert(1, "n_firms", len(es_))
        rows.append(t)
    res["1_sector"] = pd.concat(rows, ignore_index=True)

    # 2 기업별
    e2 = ent.copy()
    e2.insert(1, "name", e2["entity_id"].map(names))
    e2.insert(2, "sector", e2["entity_id"].map(sector))
    res["2_entity"] = e2

    # 3 구간별
    rows = []
    for y in ["car_trade_pct", "car_pre_pct", "car_ann_pct", "car_drift_pct"]:
        r = L.c3(ev, y)
        rows.append({k: v for k, v in r.items() if k != "_fit"})
    res["3_windows"] = pd.DataFrame(rows)

    # 4 코로나 제외
    inq = lambda q: COVID[0] <= q <= COVID[1]
    t = all_tests(pe[~pe["fiscal_quarter"].map(inq)], sig[~sig["fiscal_quarter"].map(inq)],
                  ev[~ev["fiscal_quarter"].map(inq)], main)
    t.insert(0, "sample", "코로나 2020Q1~2021Q4 제외")
    res["4_excovid"] = t

    # 5 Cook's distance 상위 1% 제외
    kept, dropped = cooks_drop(ev)
    r = L.c3(kept)
    res["5_cooks"] = pd.DataFrame([{**{k: v for k, v in r.items() if k != "_fit"},
                                    "dropped": "; ".join(f"{a} {q} (D={c:.3f})" for a, q, c in
                                                         zip(dropped["entity_id"], dropped["fiscal_quarter"], dropped["cooks_d"]))}])

    # 6 M1, B1, B3 vs B2
    d_all = fe.load_nowcast(ids)
    d_all = d_all[(d_all["cp"] == "pre5") & (d_all["variant"] == "all")]
    m1 = lambda e: ("M2" if main[e] == "M4:combo" else "M1:" + main[e].split(":", 1)[1])
    rows = []
    for label, f in [("주 모델 (M4)", lambda e: main[e]), ("M1 (드라이버 2개 기업은 M2)", m1),
                     ("B1", lambda e: "B1"), ("B3", lambda e: "B3")]:
        t = rel_median(d_all, ids, f)
        rows.append({"model": label, "n_firms": len(t), "median_rel_rmse_vs_B2": t["rel_rmse"].median(),
                     "share_below_1": float((t["rel_rmse"] < 1).mean())})
    res["6_models"] = pd.DataFrame(rows)

    # 7 환율
    fx = fx_quarterly()
    x = pe[~pe["entity_id"].isin(FX_FIRMS_EXCLUDE)].copy()
    x["q"] = [fiscal_quarter_end(q) for q in x["fiscal_quarter"]]
    x = x.merge(fx[["fx_yoy_log"]], left_on="q", right_index=True, how="left")
    qs = x.drop_duplicates("q").dropna(subset=["fx_yoy_log"])["fx_yoy_log"]
    lo, hi = qs.quantile(0.2), qs.quantile(0.8)
    x["fx_group"] = np.where(x["fx_yoy_log"] <= lo, "원화 강세 상위 20%",
                             np.where(x["fx_yoy_log"] >= hi, "원화 약세 상위 20%", "나머지"))
    x.loc[x["fx_yoy_log"].isna(), "fx_group"] = "환율 없음"
    res["7_fx"] = (x.groupby("fx_group").agg(n=("err_main", "size"), n_quarters=("q", "nunique"),
                                             fx_yoy_mean=("fx_yoy_log", "mean"),
                                             mean_err_main=("err_main", "mean"), mean_err_b2=("err_b2", "mean"),
                                             rmse_main=("err_main", lambda s: float(np.sqrt((s ** 2).mean()))),
                                             rmse_b2=("err_b2", lambda s: float(np.sqrt((s ** 2).mean()))))
                   .reset_index())
    res["7_fx"]["cutoffs"] = f"하위 20% <= {lo:.4f}, 상위 20% >= {hi:.4f} (평가 분기 {len(qs)}개의 분포)"

    # 8 시범 6곳 (같은 코드)
    p6 = list(entity.PILOTS)
    pm = {e: L.main_model_of(e) for e in p6}
    drv4 = [e for e in p6 if pm[e] != "B2"]
    ppe = L.paired_errors(L.eval_rows(drv4, pm))
    pent = L.c1_by_entity(ppe, pm)
    psig = L.pick_main_signals(L.signal_rows(drv4), pm)
    pev = L.events_with_car(psig)
    pt = all_tests(ppe, psig, pev, pm)
    pent.insert(1, "name", pent["entity_id"].map(entity.PILOTS))
    res["8_pilots_entity"] = pent
    res["8_pilots_tests"] = pt

    # 9 2b 규칙 대비 v4 (3곳)
    rows = []
    for e in ["010140", "042660", "267260"]:
        old = pd.read_csv(output_path("2c", "step2b") / f"backup_fd_nowcast_{e}_2b2.csv", dtype={"entity_id": str})
        d2b = L.eval_rows([e], main, nc=old)
        pe2b = L.paired_errors(d2b)
        r2b = L.c1_by_entity(pe2b, main).iloc[0]
        rv4 = ent[ent["entity_id"] == e].iloc[0]
        rows.append({"entity_id": e, "name": names[e], "rel_rmse_2b": r2b["rel_rmse"], "n_2b": r2b["n"],
                     "rel_rmse_v4": rv4["rel_rmse"], "n_v4": rv4["n"]})
    res["9_rule_2b_vs_v4"] = pd.DataFrame(rows)

    for k, v in res.items():
        v.to_csv(OUT / f"{k}.csv", index=False, encoding="utf-8-sig")
    figures(ent, pe, sig, ev, names, sector)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    for k, v in res.items():
        print("==", k)
        print(v.round(4).to_string(index=False))


def figures(ent, pe, sig, ev, names, sector):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ..evaluate.forecast_eval import _setup_fonts
    _setup_fonts()
    ink, grid = "#52514e", "#e6e5e0"

    # 1 기업별 상대 RMSE (업종 순, 업종 안에서 값 순)
    t = ent.copy()
    t["sector"] = t["entity_id"].map(sector)
    t["name"] = t["entity_id"].map(names)
    order = t.groupby("sector")["rel_rmse"].median().sort_values().index
    t["sector"] = pd.Categorical(t["sector"], order, ordered=True)
    t = t.sort_values(["sector", "rel_rmse"]).reset_index(drop=True)
    cmap = plt.get_cmap("tab20")
    col = {s: cmap(i % 20) for i, s in enumerate(order)}
    fig, ax = plt.subplots(figsize=(13, 5.2))
    ax.bar(range(len(t)), t["rel_rmse"], color=[col[s] for s in t["sector"]], width=0.75)
    ax.axhline(1, color=ink, lw=1)
    ax.set_xticks(range(len(t)))
    ax.set_xticklabels(t["name"], rotation=75, fontsize=8)
    ax.set_ylabel("RMSE(주 모델) / RMSE(B2)")
    ax.set_title("기업별 상대 RMSE (pre5, 업종 순)", loc="left", fontsize=11)
    ax.grid(axis="y", color=grid, lw=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=col[s]) for s in order]
    ax.legend(handles, order, fontsize=7, ncol=6, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_rel_rmse_by_entity.png", dpi=130)
    plt.close(fig)

    # 2 분기별 손실 차이 d 평균
    q = pe.groupby("fiscal_quarter").agg(d_mean=("d", "mean"), n=("d", "size")).reset_index()
    q["date"] = [fiscal_quarter_end(x) for x in q["fiscal_quarter"]]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.plot(q["date"], q["d_mean"], color="#2a78d6", lw=2, marker="o", ms=3)
    ax.axhline(0, color=ink, lw=0.8)
    ax.set_ylabel("평균 d (주 모델 제곱오차 - B2)")
    ax.set_title("분기별 손실 차이 d 의 평균 (음수 = 주 모델이 더 정확)", loc="left", fontsize=11)
    ax.grid(axis="y", color=grid, lw=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "fig2_loss_diff_by_quarter.png", dpi=130)
    plt.close(fig)

    # 3 산점도
    g = sig.dropna(subset=["nsur_drv", "nsur_mom"])
    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    ax.scatter(g["nsur_drv"], g["sur"] - g["nsur_mom"], s=10, color="#2a78d6", alpha=0.55, edgecolors="none")
    ax.axhline(0, color=ink, lw=0.7)
    ax.axvline(0, color=ink, lw=0.7)
    ax.set_xlabel("nsur_drv")
    ax.set_ylabel("SUR - nsur_mom")
    ax.set_title(f"드라이버 성분과 모멘텀 밖 서프라이즈 (n={len(g)}, r={g['nsur_drv'].corr(g['sur'] - g['nsur_mom']):.3f})",
                 loc="left", fontsize=10)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "fig3_drv_vs_sur_minus_mom.png", dpi=130)
    plt.close(fig)

    # 4 drv 5분위별 평균 매매 구간 CAR
    e = ev.dropna(subset=["nsur_drv", "car_trade_pct"]).copy()
    e["q5"] = pd.qcut(e["nsur_drv"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    s = e.groupby("q5", observed=True).agg(car=("car_trade_pct", "mean"), n=("car_trade_pct", "size"),
                                           drv=("nsur_drv", "mean")).reset_index()
    s.to_csv(OUT / "fig4_drv_quintile_car.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(6.4, 4))
    ax.bar(s["q5"].astype(int), s["car"], color="#2a78d6", width=0.6)
    ax.axhline(0, color=ink, lw=0.8)
    for x_, y_, n_ in zip(s["q5"].astype(int), s["car"], s["n"]):
        ax.text(x_, y_, f"{y_:.2f}\n(n={n_})", ha="center", va="bottom" if y_ >= 0 else "top", fontsize=8, color=ink)
    ax.set_xlabel("nsur_drv 5분위 (1 = 가장 낮음)")
    ax.set_ylabel("평균 매매 구간 CAR (%)")
    ax.set_title("drv 5분위별 평균 CAR", loc="left", fontsize=11)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "fig4_car_by_drv_quintile.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
