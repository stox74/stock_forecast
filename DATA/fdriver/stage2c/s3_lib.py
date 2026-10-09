# -*- coding: utf-8 -*-
"""2c 작업 3: C1~C3 판정 계산 (명세서 2c v4 5절, 2a v6 6·7절). 계산은 2a 코드를 그대로 쓴다.

- 오차: forecast_eval.prepare 의 err_first = g_hat - (ln 첫 공시 매출 - ln rev_base)  (전년 동기 대비 로그 성장률 기준)
- 평가 분기: pre5, 전체 기간 변형('all'), 기업별로 주 모델과 B2 오차가 모두 있는 공통 분기, 기업 사건 제외 분기 뺌
- 신호: signals.sur.build_signals (level 변형이 주, 2a 와 같음). nsur = nsur_mom + nsur_drv
- 수익률: evaluate.event_study (코스피 대비, prc_change, 매매 구간 s+1 ~ day +1, 기업 FE + 분기 군집)
- 단측 p: 회귀(statsmodels 군집 표준오차, 2a 와 같은 기본 설정)의 양측 p 에서 가설 방향이면 p/2, 아니면 1 - p/2.
  부호 검정·이항 검정은 scipy binomtest(alternative='greater').
"""
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

from ..evaluate import event_study as es
from ..evaluate import forecast_eval as fe
from ..events import fiscal_quarter_end
from ..nowcast import features as ft
from ..signals import sur

ALPHA = 0.10
CHECKPOINT = "pre5"
VARIANT_EVAL = "all"
SIGNAL_VARIANT = "level"


def one_sided(p_two: float, coef: float, direction: str) -> float:
    """direction 'greater' (H1: coef > 0) 또는 'less' (H1: coef < 0)."""
    if pd.isna(p_two) or pd.isna(coef):
        return np.nan
    ok = coef > 0 if direction == "greater" else coef < 0
    return p_two / 2 if ok else 1 - p_two / 2


def main_model_of(entity_id: str) -> str:
    comps = [s.model_id for s in ft.DRIVER_SPECS.get(entity_id, []) if not s.aux and s.alt is None]
    if len(comps) >= 2:
        return "M4:combo"
    if len(comps) == 1:
        return "M4:" + comps[0].split(":", 1)[1]
    return "B2"


# ---------------------------------------------------------------- 평가 자료 (오차 계산 전 단계 포함)
def eval_rows(entity_ids: Iterable[str], main: Dict[str, str], nc: pd.DataFrame = None) -> pd.DataFrame:
    """pre5, 'all' 변형의 주 모델·B2 행 (forecast_eval.prepare 결과). 오차 컬럼 err_first 포함."""
    ids = list(entity_ids)
    d = fe.prepare(nc) if nc is not None else fe.load_nowcast(ids)
    d = d[d["entity_id"].isin(ids) & (d["cp"] == CHECKPOINT) & (d["variant"] == VARIANT_EVAL)]
    d = d[[m in (main[e], "B2") for e, m in zip(d["entity_id"], d["model"])]].copy()
    d["role"] = np.where(d["model"] == "B2", "B2", "main")
    # 기업 사건 제외 분기 (nowcast 가 이미 빼지만 명시적으로 한 번 더)
    excl = np.array([fiscal_quarter_end(q) in ft.corporate_event_quarters(e, [fiscal_quarter_end(q)])
                     for e, q in zip(d["entity_id"], d["fiscal_quarter"])], dtype=bool)
    return d[~excl]


def common_quarters(d: pd.DataFrame) -> pd.DataFrame:
    """기업별 공통 분기 표 (오차 값을 보지 않고 존재 여부만): entity_id, fiscal_quarter."""
    have = d[d["rev_actual_first"].notna() & d["rev_base"].notna() & d["g_hat"].notna()]
    piv = have.pivot_table(index=["entity_id", "fiscal_quarter"], columns="role", values="g_hat", aggfunc="first")
    piv = piv.dropna(subset=[c for c in ("main", "B2") if c in piv.columns])
    if "main" not in piv.columns or "B2" not in piv.columns:
        return pd.DataFrame(columns=["entity_id", "fiscal_quarter"])
    return piv.reset_index()[["entity_id", "fiscal_quarter"]]


def paired_errors(d: pd.DataFrame) -> pd.DataFrame:
    cq = common_quarters(d)
    e = d.pivot_table(index=["entity_id", "fiscal_quarter"], columns="role", values="err_first", aggfunc="first")
    e = e.reset_index().merge(cq, on=["entity_id", "fiscal_quarter"])
    e = e.rename(columns={"main": "err_main", "B2": "err_b2"}).dropna(subset=["err_main", "err_b2"])
    e["d"] = e["err_main"] ** 2 - e["err_b2"] ** 2
    return e


# ---------------------------------------------------------------- C1
def c1_by_entity(pe: pd.DataFrame, main: Dict[str, str]) -> pd.DataFrame:
    rows = []
    for e, g in pe.groupby("entity_id"):
        rm, rb = float(np.sqrt((g["err_main"] ** 2).mean())), float(np.sqrt((g["err_b2"] ** 2).mean()))
        dm = fe.dm_test(g["err_main"].values, g["err_b2"].values)
        rows.append({"entity_id": e, "main_model": main[e], "n": len(g), "rmse_main": rm, "rmse_b2": rb,
                     "rel_rmse": rm / rb if rb > 0 else np.nan, "dm_p_two": dm["p_two"], "dm_p_less": dm["p_less"],
                     "status": "평가" if len(g) >= fe.MIN_EVAL else "평가 불가"})
    return pd.DataFrame(rows)


def c1a(ent: pd.DataFrame) -> Dict:
    x = ent.dropna(subset=["rel_rmse"])
    k, n = int((x["rel_rmse"] < 1).sum()), len(x)
    p = stats.binomtest(k, n, 0.5, alternative="greater").pvalue if n else np.nan
    return {"test": "C1(a)", "estimate": k / n if n else np.nan, "k": k, "N": n, "p_one": p,
            "direction_ok": k / n > 0.5 if n else False}


def c1b(pe: pd.DataFrame) -> Dict:
    m = smf.ols("d ~ 1", data=pe).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(pe["fiscal_quarter"])[0]})
    coef, se, p2 = float(m.params["Intercept"]), float(m.bse["Intercept"]), float(m.pvalues["Intercept"])
    return {"test": "C1(b)", "estimate": coef, "se": se, "p_two": p2, "p_one": one_sided(p2, coef, "less"),
            "N": len(pe), "n_entities": pe["entity_id"].nunique(), "n_clusters": pe["fiscal_quarter"].nunique(),
            "direction_ok": coef < 0}


# ---------------------------------------------------------------- C2
def signal_rows(entity_ids: Iterable[str]) -> pd.DataFrame:
    parts = [sur.build_signals(e)["rows"] for e in entity_ids]
    s = pd.concat(parts, ignore_index=True)
    return s[s["nsur"].notna() | s["sur"].notna()]


def pick_main_signals(s: pd.DataFrame, main: Dict[str, str]) -> pd.DataFrame:
    s = s.copy()
    s["asof_date"] = pd.to_datetime(s["asof_date"])
    s["event_date"] = pd.to_datetime(s["event_date"])
    m = s[(s["variant"] == SIGNAL_VARIANT) & s["checkpoint"].astype(str).str.contains(CHECKPOINT)
          & np.array([main.get(e) == mid for e, mid in zip(s["entity_id"], s["model_id"])], dtype=bool)]
    excl = np.array([fiscal_quarter_end(q) in ft.corporate_event_quarters(e, [fiscal_quarter_end(q)])
                     for e, q in zip(m["entity_id"], m["fiscal_quarter"])], dtype=bool)
    return m[~excl].dropna(subset=["nsur", "sur"]).reset_index(drop=True)


def c2(sig: pd.DataFrame) -> Dict:
    g = sig.dropna(subset=["nsur_drv", "nsur_mom"])
    target = g["sur"] - g["nsur_mom"]
    corr = float(g["nsur_drv"].corr(target))
    dz = g[g["nsur_drv"] != 0]
    hits = int((np.sign(dz["nsur_drv"]) == np.sign(dz["sur"] - dz["nsur_mom"])).sum())
    n = len(dz)
    p = stats.binomtest(hits, n, 0.5, alternative="greater").pvalue if n else np.nan
    r_t = corr * np.sqrt((len(g) - 2) / (1 - corr ** 2)) if len(g) > 2 and abs(corr) < 1 else np.nan
    p_corr = float(stats.t.sf(r_t, len(g) - 2)) if pd.notna(r_t) else np.nan
    return {"test": "C2", "estimate": corr, "hit_rate": hits / n if n else np.nan, "hits": hits, "N_hit": n,
            "N": len(g), "n_entities": g["entity_id"].nunique(), "p_one": p, "p_one_corr_ref": p_corr,
            "direction_ok": (corr > 0) and (n > 0 and hits / n > 0.5)}


# ---------------------------------------------------------------- C3
def events_with_car(sig: pd.DataFrame, xr: Dict = None) -> pd.DataFrame:
    ev = sig[["entity_id", "fiscal_quarter", "asof_date", "event_date", "model_id", "nsur", "nsur_mom", "nsur_drv", "sur"]]
    ev = ev.rename(columns={"asof_date": "signal_date"})
    xr = xr or es.excess_returns(sorted(ev["entity_id"].unique()))
    out = es.event_cars(ev, xr)
    for c in ["car_trade", "car_pre", "car_ann", "car_drift"]:
        if c in out:
            out[c + "_pct"] = out[c] * 100
    return out


def c3(ev: pd.DataFrame, y: str = "car_trade_pct") -> Dict:
    r = es.fe_cluster_ols(ev, y, ["nsur_mom", "nsur_drv"])
    if r["coef"].empty:
        return {"test": "C3", "N": r["n"], "estimate": np.nan, "p_one": np.nan, "direction_ok": False}
    b = r["coef"].loc["nsur_drv"]
    bm = r["coef"].loc["nsur_mom"]
    return {"test": "C3", "y": y, "estimate": float(b["coef"]), "se": float(b["se"]), "p_two": float(b["p"]),
            "p_one": one_sided(float(b["p"]), float(b["coef"]), "greater"), "beta_mom": float(bm["coef"]),
            "se_mom": float(bm["se"]), "N": r["n"], "n_entities": r["n_entities"], "n_clusters": r["n_clusters"],
            "events_by_entity": r["events_by_entity"], "direction_ok": b["coef"] > 0, "_fit": r.get("fit")}


def verdict(rows: List[Dict]) -> pd.DataFrame:
    t = pd.DataFrame(rows)
    t["success"] = t["direction_ok"] & (t["p_one"] < ALPHA)
    return t
