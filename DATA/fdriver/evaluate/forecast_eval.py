# -*- coding: utf-8 -*-
"""nowcast 예측 평가 (2a 명세서 v4 5절).

오차 정의
  예측 성장률 g_hat 과 실제 성장률 g = ln(실제 매출) - ln(rev_base) 의 차이 (= ln(rev_hat / 실제 매출)).
  실제 매출은 첫 공시 값(rev_actual_first, 주 지표)과 확정값(rev_actual_final, 보조 지표) 두 가지.
지표
  RMSE, MAE, 같은 분기 벤치마크(B1/B2/B3) 대비 상대 RMSE, 방향 적중률(g - 직전 g 의 부호),
  DM 검정(제곱오차, h=1, Harvey-Leybourne-Newbold 소표본 보정, t(n-1) 분포).
  직전 g 는 같은 기준일·같은 변형의 B2 예측값(기준일까지 발표된 가장 최근 분기의 g)이다.
  B2 는 정의상 g_hat = 직전 g 라 방향 적중률이 없다.
규칙
  - M2 가 B3 로 대체된 행(drivers_used = 'fallback:B3')은 평가에서 뺀다.
  - 평가 분기가 8 미만이면 '평가 불가'.
  - 합쳐진 기준일('qend+pre5')은 각 기준일에 모두 센다.
  - 결과 확인 후 추가한 변형(:krw 등)은 판정에 쓰지 않고 별도 표로만 낸다.
"""
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from .. import db, entity
from ..nowcast import features as ft

CHECKPOINTS = ["qend", "d15", "d30", "d45", "pre5"]
BENCHMARKS = ["B1", "B2", "B3"]
MIN_EVAL = 8
ALT_TAGS = ("krw", "rel30")

# 2a 명세서 2절 역할과 주 모델
SURE = ["005930", "039130"]          # "확실" 드라이버 기업
PROMISING = ["267270", "278470"]     # "유망" 드라이버 기업
CONTROLS = ["006040", "003350"]      # 대조군 (벤치마크만)
MAIN_TARGETS = SURE + PROMISING
MAIN_MODEL = {"005930": "M2", "039130": "M2", "267270": "M3:842952", "278470": "M3:amazon"}
# v5 M4 판단 지점의 주 모델: 드라이버 2개 이상이면 M4:combo, 1개면 M4
MAIN_MODEL_M4 = {"005930": "M4:combo", "039130": "M4:combo", "267270": "M4:842952", "278470": "M4:amazon"}


# ---------------------------------------------------------------- DM 검정
def dm_test(e1: Sequence[float], e2: Sequence[float], h: int = 1, power: int = 2) -> Dict:
    """Diebold-Mariano 검정 + HLN 소표본 보정.

    d_t = |e1_t|^power - |e2_t|^power. 귀무가설: E[d] = 0.
    분산은 lag h-1 까지의 자기공분산(분모 n)으로 계산 (R forecast::dm.test 와 같은 방식).
    반환: n, mean_d, dm (보정 전), stat (HLN 보정 후), p_two (양측), p_less (모델 1 이 더 정확하다는 단측).
    """
    a = np.asarray(e1, dtype=float)
    b = np.asarray(e2, dtype=float)
    ok = ~(np.isnan(a) | np.isnan(b))
    d = np.abs(a[ok]) ** power - np.abs(b[ok]) ** power
    n = len(d)
    out = {"n": n, "mean_d": float(d.mean()) if n else np.nan, "dm": np.nan, "stat": np.nan,
           "p_two": np.nan, "p_less": np.nan}
    if n < 2:
        return out
    dc = d - d.mean()
    gam = [float(np.sum(dc[k:] * dc[:n - k]) / n) for k in range(h)]
    var = (gam[0] + 2.0 * sum(gam[1:])) / n
    if not var > 0:
        return out
    dm = d.mean() / np.sqrt(var)
    k = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    st = dm * k
    out.update(dm=float(dm), stat=float(st), p_two=float(2 * stats.t.sf(abs(st), df=n - 1)),
               p_less=float(stats.t.cdf(st, df=n - 1)))
    return out


# ---------------------------------------------------------------- 자료 준비
def parse_model_id(model_id: str) -> Dict[str, Optional[str]]:
    """'M1:854232:krw:excovid' -> model 'M1:854232:krw', base 'M1:854232', alt 'krw', variant 'excovid'."""
    variant = "excovid" if model_id.endswith(":excovid") else "all"
    model = model_id[: -len(":excovid")] if variant == "excovid" else model_id
    parts = model.split(":")
    alt = parts[-1] if parts[-1] in ALT_TAGS else None
    base = ":".join(parts[:-1]) if alt else model
    return {"model": model, "base": base, "alt": alt, "variant": variant}


def load_nowcast(entity_ids: Optional[Iterable[str]] = None) -> pd.DataFrame:
    if entity_ids:
        frag, params = db.in_clause("e", list(entity_ids))
        nc = db.read_sql(f"SELECT * FROM fd_nowcast WHERE entity_id IN {frag}", params)
    else:
        nc = db.read_sql("SELECT * FROM fd_nowcast")
    return prepare(nc)


def prepare(nc: pd.DataFrame) -> pd.DataFrame:
    """오차·직전 g·기준일 펼치기. fallback:B3 행은 뺀다."""
    d = nc.copy()
    d["entity_id"] = d["entity_id"].astype(str).str.zfill(6)
    d["asof_date"] = pd.to_datetime(d["asof_date"])
    p = pd.DataFrame([parse_model_id(m) for m in d["model_id"]], index=d.index)
    d = pd.concat([d, p], axis=1)
    d = d[d["drivers_used"].fillna("") != "fallback:B3"].copy()
    for m in ("first", "final"):
        act = pd.to_numeric(d[f"rev_actual_{m}"], errors="coerce")
        base = pd.to_numeric(d["rev_base"], errors="coerce")
        ok = (act > 0) & (base > 0)
        d[f"g_actual_{m}"] = np.where(ok, np.log(act.where(ok)) - np.log(base.where(ok)), np.nan)
        d[f"err_{m}"] = d["g_hat"] - d[f"g_actual_{m}"]
    b2 = d[d["model"] == "B2"][["entity_id", "fiscal_quarter", "asof_date", "variant", "g_hat"]].rename(
        columns={"g_hat": "g_prev"})
    d = d.merge(b2, on=["entity_id", "fiscal_quarter", "asof_date", "variant"], how="left")
    d["cp"] = d["checkpoint"].str.split("+")
    d = d.explode("cp").reset_index(drop=True)
    return d


def _rmse(e: pd.Series) -> float:
    e = e.dropna()
    return float(np.sqrt(np.mean(e ** 2))) if len(e) else np.nan


def _direction_hit(g: pd.DataFrame, measure: str) -> float:
    x = g.dropna(subset=["g_prev", f"g_actual_{measure}"])
    x = x[(x["g_hat"] - x["g_prev"]) != 0]
    if x.empty:
        return np.nan
    return float((np.sign(x["g_hat"] - x["g_prev"]) == np.sign(x[f"g_actual_{measure}"] - x["g_prev"])).mean())


def errors(d: pd.DataFrame, entity_id: str, variant: str, model: str, cp: str, measure: str = "first") -> pd.Series:
    g = d[(d["entity_id"] == entity_id) & (d["variant"] == variant) & (d["model"] == model) & (d["cp"] == cp)]
    return g.set_index("fiscal_quarter")[f"err_{measure}"].dropna().sort_index()


# ---------------------------------------------------------------- 결과표
def metrics_table(d: pd.DataFrame, measure: str = "first") -> pd.DataFrame:
    """기업 x 변형 x 기준일 x 모델: 모델이 예측한 분기에서 RMSE, MAE, 벤치마크 대비 상대 RMSE, DM p, 방향 적중률."""
    rows = []
    for (e, v, cp, m), g in d.groupby(["entity_id", "variant", "cp", "model"]):
        em = g.set_index("fiscal_quarter")[f"err_{measure}"].dropna().sort_index()
        r = {"entity_id": e, "name": entity.PILOTS.get(e, e), "variant": v, "checkpoint": cp, "model": m,
             "alt": parse_model_id(m)["alt"], "n": len(em),
             "first_q": em.index.min() if len(em) else None, "last_q": em.index.max() if len(em) else None,
             "rmse": _rmse(em), "mae": float(em.abs().mean()) if len(em) else np.nan,
             "dir_hit": np.nan if m == "B2" else _direction_hit(g[g[f"err_{measure}"].notna()], measure)}
        for b in BENCHMARKS:
            eb = errors(d, e, v, b, cp, measure)
            q = em.index.intersection(eb.index)
            r[f"n_vs_{b}"] = len(q)
            rb = _rmse(eb[q])
            r[f"rel_{b}"] = _rmse(em[q]) / rb if len(q) and rb > 0 else np.nan
            if m == b:
                r[f"dm_p_{b}"] = np.nan
                r[f"dm_pless_{b}"] = np.nan
            else:
                t = dm_test(em[q].values, eb[q].values)
                r[f"dm_p_{b}"], r[f"dm_pless_{b}"] = t["p_two"], t["p_less"]
        r["status"] = "평가 불가" if len(em) < MIN_EVAL else "평가"
        rows.append(r)
    out = pd.DataFrame(rows)
    out["cp_order"] = out["checkpoint"].map({c: i for i, c in enumerate(CHECKPOINTS)})
    return out.sort_values(["entity_id", "variant", "cp_order", "model"]).drop(columns="cp_order").reset_index(drop=True)


def main_components(entity_id: str) -> List[str]:
    return [s.model_id for s in ft.DRIVER_SPECS.get(entity_id, []) if not s.aux and s.alt is None]


def m4_components(entity_id: str) -> List[str]:
    return ["M4:" + m.split(":", 1)[1] for m in main_components(entity_id)]


def m4_model_set(entity_id: str) -> List[str]:
    """M4 공통 분기 비교 집합: M4:combo(드라이버 2개 이상), M4 구성, M2(있으면), M1~M3 구성, B2."""
    comps = main_components(entity_id)
    if not comps:
        return []
    multi = len(comps) >= 2
    return (["M4:combo"] if multi else []) + m4_components(entity_id) + (["M2"] if multi else []) + comps + ["B2"]


def common_quarter_table(d: pd.DataFrame, cp: str = "pre5", measure: str = "first", model_sets=None) -> pd.DataFrame:
    """M2(있으면)·구성 모델·B2 가 모두 예측한 공통 분기에서 비교. 드라이버가 없는 기업은 B1~B3.
    model_sets(entity_id) -> 모델 목록을 주면 그 집합으로 비교 (빈 목록이면 그 기업은 건너뜀)."""
    rows = []
    for e in sorted(d["entity_id"].unique()):
        comps = main_components(e)
        if model_sets is not None:
            models = model_sets(e)
            if not models:
                continue
        else:
            models = (["M2"] if len(comps) >= 2 else []) + comps + ["B2"] if comps else BENCHMARKS
        for v in ("all", "excovid"):
            es = {m: errors(d, e, v, m, cp, measure) for m in models}
            q = None
            for s in es.values():
                q = s.index if q is None else q.intersection(s.index)
            q = q if q is not None else pd.Index([])
            rb = _rmse(es["B2"][q]) if len(q) else np.nan
            for m in models:
                rm = _rmse(es[m][q]) if len(q) else np.nan
                t = dm_test(es[m][q].values, es["B2"][q].values) if m != "B2" and len(q) else {"p_two": np.nan}
                rows.append({"entity_id": e, "name": entity.PILOTS.get(e, e), "variant": v, "checkpoint": cp,
                             "models_in_set": "+".join(models), "model": m, "n_common": len(q),
                             "rmse": rm, "rel_B2": rm / rb if rb and rb > 0 else np.nan, "dm_p_B2": t["p_two"],
                             "status": "평가 불가" if len(q) < MIN_EVAL else "평가"})
    return pd.DataFrame(rows)


def info_curve(mt: pd.DataFrame, models: Optional[Dict[str, List[str]]] = None) -> pd.DataFrame:
    """기준일별 B2 대비 상대 RMSE (모델이 예측한 분기 기준). plot = n >= 8."""
    if models is None:
        models = {e: [MAIN_MODEL[e]] + [c for c in main_components(e) if c != MAIN_MODEL[e]] for e in MAIN_MODEL}
    keep = []
    for e, ms in models.items():
        keep.append(mt[(mt["entity_id"] == e) & mt["model"].isin(ms)])
    c = pd.concat(keep, ignore_index=True)[["entity_id", "name", "variant", "model", "checkpoint", "n_vs_B2", "rel_B2"]]
    c["plot"] = c["n_vs_B2"] >= MIN_EVAL
    return c


def top_quarters(d: pd.DataFrame, entity_id: str, model: str, variant: str = "all", cp: str = "pre5",
                 k: int = 3, measure: str = "first") -> Dict:
    """손실 차이 |e_m^2 - e_B2^2| 가 큰 k 분기와, 그 분기를 뺀 B2 대비 상대 RMSE (참고용, 판정에 쓰지 않음)."""
    em = errors(d, entity_id, variant, model, cp, measure)
    eb = errors(d, entity_id, variant, "B2", cp, measure)
    q = em.index.intersection(eb.index)
    if len(q) == 0:
        return {"entity_id": entity_id, "model": model, "variant": variant, "n": 0}
    contrib = (em[q] ** 2 - eb[q] ** 2)
    top = contrib.abs().sort_values(ascending=False).index[:k]
    rest = q.difference(top)
    rel_all = _rmse(em[q]) / _rmse(eb[q])
    rel_rest = _rmse(em[rest]) / _rmse(eb[rest]) if len(rest) else np.nan
    return {"entity_id": entity_id, "name": entity.PILOTS.get(entity_id, entity_id), "model": model,
            "variant": variant, "n": len(q), "rel_B2": rel_all,
            "top_quarters": ";".join(top),
            "top_detail": ";".join(f"{x}(e_m={em[x]:+.3f},e_B2={eb[x]:+.3f})" for x in top),
            "share_of_sum_abs_diff": float(contrib[top].abs().sum() / contrib.abs().sum()) if contrib.abs().sum() else np.nan,
            "n_without_top": len(rest), "rel_B2_without_top": rel_rest}


# ---------------------------------------------------------------- 성공 기준
CHECKLIST = [
    '"확실" 드라이버 기업(삼성전자, 하나투어): pre5 기준 B2 대비 상대 RMSE 0.8 미만, DM 검정 p < 0.10',
    '"유망" 드라이버 기업: pre5 기준 B2 대비 상대 RMSE 1.0 미만 (개선 방향 확인)',
    "대조군: nowcast가 벤치마크와 사실상 같음 (드라이버가 없는데 성과가 좋게 나오면 오류 점검)",
    "모든 결과에 표본 외 분기 수를 함께 표시",
]


def _pick(mt: pd.DataFrame, e: str, model: str, variant: str, cp: str = "pre5") -> Optional[pd.Series]:
    r = mt[(mt["entity_id"] == e) & (mt["model"] == model) & (mt["variant"] == variant) & (mt["checkpoint"] == cp)]
    return None if r.empty else r.iloc[0]


def success_table(mt: pd.DataFrame, d: pd.DataFrame, main_model: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """기업별 성공 기준 판정. DM p 는 양측. 결과 확인 후 변형(alt)은 쓰지 않는다."""
    main_model = MAIN_MODEL if main_model is None else main_model
    rows = []
    for v in ("all", "excovid"):
        for e in SURE + PROMISING:
            m = main_model[e]
            r = _pick(mt, e, m, v)
            item = CHECKLIST[0] if e in SURE else CHECKLIST[1]
            if r is None or r["n_vs_B2"] < MIN_EVAL:
                verdict, ev = "평가 불가", f"n={0 if r is None else int(r['n_vs_B2'])}"
            else:
                if e in SURE:
                    ok = (r["rel_B2"] < 0.8) and (r["dm_p_B2"] < 0.10)
                else:
                    ok = r["rel_B2"] < 1.0
                verdict = "충족" if ok else "미충족"
                ev = (f"{m}: n={int(r['n_vs_B2'])}, rel_B2={r['rel_B2']:.3f}, DM p(양측)={r['dm_p_B2']:.3f}, "
                      f"rmse={r['rmse']:.4f}")
            rows.append({"variant": v, "item": item, "entity_id": e, "name": entity.PILOTS[e], "model": m,
                         "n": None if r is None else int(r["n_vs_B2"]),
                         "rel_B2": None if r is None else r["rel_B2"], "dm_p_B2": None if r is None else r["dm_p_B2"],
                         "verdict": verdict, "evidence": ev})
        for e in CONTROLS:
            drv_rows = int(((d["entity_id"] == e) & ~d["model"].isin(BENCHMARKS)).sum())
            parts, flags = [], []
            for b in ("B1", "B3"):
                r = _pick(mt, e, b, v)
                if r is not None:
                    parts.append(f"{b}/B2={r['rel_B2']:.3f}(n={int(r['n_vs_B2'])}, DM p={r['dm_p_B2']:.3f})")
                    if r["dm_p_B2"] < 0.10:
                        flags.append(f"{b} 가 B2 보다 {'나쁨' if r['rel_B2'] > 1 else '좋음'} (p={r['dm_p_B2']:.3f})")
            note = ("벤치마크 간 차이: " + "; ".join(flags)) if flags else "벤치마크 간 유의한 차이 없음"
            rows.append({"variant": v, "item": CHECKLIST[2], "entity_id": e, "name": entity.PILOTS[e],
                         "model": "벤치마크끼리", "n": None, "rel_B2": None, "dm_p_B2": None,
                         "verdict": "드라이버 모델 없음 (확인)" if drv_rows == 0 else f"오류 점검: 드라이버 행 {drv_rows}",
                         "evidence": ", ".join(parts) + f"; 드라이버 모델 행 {drv_rows}건; {note}"})
        rows.append({"variant": v, "item": CHECKLIST[3], "entity_id": "*", "name": "*", "model": "*", "n": None,
                     "rel_B2": None, "dm_p_B2": None,
                     "verdict": "충족" if mt["n"].notna().all() else "미충족",
                     "evidence": "모든 결과표에 n(표본 외 분기 수) 컬럼, 8분기 미만은 '평가 불가'"})
    return pd.DataFrame(rows)


def decision_count(mt: pd.DataFrame, variant: str = "all", main_model: Optional[Dict[str, str]] = None) -> Dict:
    """판단 지점: pre5 B2 대비 상대 RMSE < 1 인 주 대상 기업 수 (평가 가능한 기업만 셈)."""
    main_model = MAIN_MODEL if main_model is None else main_model
    hit, rows = 0, []
    for e in MAIN_TARGETS:
        r = _pick(mt, e, main_model[e], variant)
        ok = r is not None and r["n_vs_B2"] >= MIN_EVAL and r["rel_B2"] < 1.0
        hit += int(ok)
        rows.append({"entity_id": e, "model": main_model[e], "n": None if r is None else int(r["n_vs_B2"]),
                     "rel_B2": None if r is None else r["rel_B2"], "below_1": ok})
    return {"count": hit, "of": len(MAIN_TARGETS), "detail": pd.DataFrame(rows)}


def variants_table(d: pd.DataFrame, cp: str = "pre5", measure: str = "first") -> pd.DataFrame:
    """결과 확인 후 추가한 변형: 변형 모델 vs 원래 모델 vs B2 (변형·원래·B2 공통 분기)."""
    rows = []
    alts = d[d["alt"].notna()][["entity_id", "model", "base"]].drop_duplicates()
    for _, a in alts.iterrows():
        for v in ("all", "excovid"):
            ea = errors(d, a["entity_id"], v, a["model"], cp, measure)
            eo = errors(d, a["entity_id"], v, a["base"], cp, measure)
            eb = errors(d, a["entity_id"], v, "B2", cp, measure)
            q = ea.index.intersection(eo.index).intersection(eb.index)
            rb = _rmse(eb[q])
            t = dm_test(ea[q].values, eo[q].values) if len(q) else {"p_two": np.nan}
            rows.append({"entity_id": a["entity_id"], "name": entity.PILOTS.get(a["entity_id"]), "variant": v,
                         "checkpoint": cp, "model": a["model"], "base": a["base"], "n_common": len(q),
                         "rmse_alt": _rmse(ea[q]), "rmse_base": _rmse(eo[q]), "rmse_B2": rb,
                         "rel_B2_alt": _rmse(ea[q]) / rb if rb else np.nan,
                         "rel_B2_base": _rmse(eo[q]) / rb if rb else np.nan,
                         "dm_p_alt_vs_base": t["p_two"],
                         "status": "평가 불가" if len(q) < MIN_EVAL else "평가"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 그림
def _setup_fonts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def plot_info_curves(curve: pd.DataFrame, path) -> None:
    plt = _setup_fonts()
    ents = list(dict.fromkeys(curve["entity_id"]))
    fig, axes = plt.subplots(len(ents), 2, figsize=(11, 3.2 * len(ents)), squeeze=False)
    for i, e in enumerate(ents):
        for j, v in enumerate(("all", "excovid")):
            ax = axes[i, j]
            c = curve[(curve["entity_id"] == e) & (curve["variant"] == v) & curve["plot"]]
            for m, g in c.groupby("model", sort=False):
                g = g.set_index("checkpoint").reindex(CHECKPOINTS)
                ax.plot(range(len(CHECKPOINTS)), g["rel_B2"].values, marker="o", label=m)
                for k, (n, y) in enumerate(zip(g["n_vs_B2"].values, g["rel_B2"].values)):
                    if pd.notna(y):
                        ax.annotate(f"{int(n)}", (k, y), textcoords="offset points", xytext=(0, 5), fontsize=7)
            ax.axhline(1.0, color="gray", lw=0.8, ls="--")
            ax.set_xticks(range(len(CHECKPOINTS)))
            ax.set_xticklabels(CHECKPOINTS)
            ax.set_title(f"{entity.PILOTS.get(e, e)} ({e}) - {v}", fontsize=10)
            ax.set_ylabel("B2 대비 상대 RMSE")
            if len(c):
                ax.legend(fontsize=7)
    fig.suptitle("정보 누적 곡선 (점 위 숫자 = 평가 분기 수, 8분기 이상만 표시)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_quarter_errors(d: pd.DataFrame, entity_id: str, model: str, path, cp: str = "pre5",
                        measure: str = "first") -> None:
    plt = _setup_fonts()
    fig, axes = plt.subplots(2, 1, figsize=(12, 6.5), squeeze=False)
    for j, v in enumerate(("all", "excovid")):
        ax = axes[j, 0]
        em = errors(d, entity_id, v, model, cp, measure)
        eb = errors(d, entity_id, v, "B2", cp, measure)
        q = sorted(em.index.union(eb.index))
        x = np.arange(len(q))
        ax.bar(x - 0.2, em.reindex(q).values, width=0.4, label=model)
        ax.bar(x + 0.2, eb.reindex(q).values, width=0.4, label="B2")
        ax.axhline(0, color="gray", lw=0.8)
        step = max(1, len(q) // 20)
        ax.set_xticks(x[::step])
        ax.set_xticklabels([q[k] for k in range(0, len(q), step)], rotation=60, fontsize=7)
        ax.set_ylabel("성장률 오차 (예측 - 실제)")
        ax.set_title(f"{entity.PILOTS.get(entity_id, entity_id)} {model} vs B2, {cp}, {v}", fontsize=10)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
