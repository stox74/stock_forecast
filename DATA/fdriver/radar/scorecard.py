# -*- coding: utf-8 -*-
"""발표 후 맞춰 보기 (39곳, M4·E·B2·TS·컨센서스) 와 성적표 (레이더 명세서 v2, R2).

compare(rec): fd_live_record 의 기록 행과 실제 첫 공시 값 비교 (2a evaluate.live_check.actual_first 와 같은 값).
  g_actual = ln(실제 / rev_base). 모델 오차 = g_모델 - g_actual. 컨센서스 오차 = ln(컨센서스 / rev_base) - g_actual.
  SUR = (실제 - rev_base - delta) / sigma (기록 시점 분모). drv 적중 = sign(nsur_drv) == sign(SUR - nsur_mom).
  코스피 대비 누적 초과수익률 (2a event_study: KSE_Price prc_change - 코스피, 결측일 건너뜀, 구간 미완료면 NaN):
    car_a = 기준일 다음 거래일 ~ 발표 다음 거래일 (day +1),  car_b = 발표 2거래일 후 ~ 20거래일 (day +2 ~ +20).
    발표일(day 0) = 실제 첫 공시일 (휴장일이면 다음 거래일).
2b 추가 (adapters.kr_consensus.read_consensus): c_pre5 = 관측일 <= 기록 기준일 인 최신 컨센서스, c_final = 관측일 < 실제 첫 공시일
  인 최신 값. err_C_pre5, err_C_final = ln(값 / rev_base) - g_actual. gap_E = rev_E / c_pre5 - 1,
  surprise_c = 실제 / c_final - 1, gap_hit = sign(gap_E) == sign(surprise_c) (둘 다 0 이 아닐 때).
scorecard(compared): 성적표 1~5. 기록이 기준일별로 여러 번이면 by_asof() 로 기준일마다 따로 계산한다.
  1 방향 적중: sign(nsur_drv) == sign(SUR - nsur_mom) 비율 (nsur_drv != 0), gap_E 기준 적중률(gap_hit). 전체, 등급별,
    M4·E 일치/불일치별.
  2 오차와 승률: M4·E·B2·TS·컨센서스(기록 값)·C_pre5·C_final 의 평균 절대 로그 오차, RMSE / M4·E 의 B2 대비 승률.
  3 수익률: nsur_drv 상위 1/3 과 하위 1/3 기업의 평균 CAR 차이 (구간 a, b). 전체와 등급별 (나눔은 그 집합 안에서).
  4 충격 경보: 경보 대 비경보의 M4 평균 절대 오차, 방향 적중률.
  5 등급별 요약: A·B·C (시범 포함) 별 오차·승률·적중률.
실행: python -m DATA.fdriver.radar.scorecard --quarter 2026Q3 [--record-id ...]
"""
import argparse
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..adapters import kr_consensus
from ..evaluate import event_study as es
from ..evaluate.live_check import actual_first
from .record import T_RECORD

MODELS = {"M4": "g_M4", "E": "g_E", "B2": "g_B2", "TS": "g_TS"}
WINDOWS_R2 = {"a": ("s+1", 1), "b": (2, 20)}


def load(fiscal_quarter: str, table: str = T_RECORD, record_id: Optional[str] = None) -> pd.DataFrame:
    sql = f"SELECT * FROM `{table}` WHERE fiscal_quarter = :q"
    p = {"q": fiscal_quarter}
    if record_id:
        sql += " AND record_id = :r"
        p["r"] = record_id
    return db.read_sql(sql, p)


def add_cars(c: pd.DataFrame, xr: Optional[Dict] = None) -> pd.DataFrame:
    """car_a, car_b (%) 를 붙인다. 발표일이 없거나 구간이 미완료면 NaN."""
    c = c.copy()
    c["car_a"], c["car_b"] = np.nan, np.nan
    ok = c["actual_dt"].notna() & c["asof_date"].notna()
    if not ok.any():
        return c
    xr = xr or es.excess_returns(sorted(c.loc[ok, "entity_id"].unique()))
    cal = xr["cal"].values
    for i, r in c[ok].iterrows():
        e = r["entity_id"]
        if e not in xr["ex"]:
            continue
        d0 = int(np.searchsorted(cal, np.datetime64(pd.Timestamp(r["actual_dt"])), side="left"))
        s1 = int(np.searchsorted(cal, np.datetime64(pd.Timestamp(r["asof_date"])), side="right"))
        for name, (a, b) in WINDOWS_R2.items():
            start = s1 if a == "s+1" else d0 + a
            res = es.car(xr["ex"][e], xr["cal"], start, d0 + b, xr["last"][e], xr["first"].get(e))
            c.loc[i, f"car_{name}"] = res["car"] * 100 if pd.notna(res["car"]) else np.nan
    return c


def compare(rec: pd.DataFrame, with_returns: bool = True) -> pd.DataFrame:
    rows = []
    for r in rec.to_dict("records"):
        a = actual_first(r["entity_id"], r["fiscal_quarter"])
        out = {**r, "actual": a["value"], "actual_source": a["source"], "actual_dt": a["first_dt"]}
        if a["value"] is None or not (r["rev_base"] and r["rev_base"] > 0) or not a["value"] > 0:
            out["status"] = "대기"
            rows.append(out)
            continue
        out["status"] = "비교"
        ga = float(np.log(a["value"]) - np.log(r["rev_base"]))
        out["g_actual"] = ga
        for m, col in MODELS.items():
            out[f"err_{m}"] = (r[col] - ga) if r.get(col) is not None and pd.notna(r.get(col)) else np.nan
        cons = r.get("consensus")
        out["err_consensus"] = (np.log(cons) - np.log(r["rev_base"]) - ga) if cons and pd.notna(cons) and cons > 0 else np.nan
        sig = r.get("sigma")
        out["sur"] = (a["value"] - r["rev_base"] - r["delta"]) / sig if sig and pd.notna(sig) and pd.notna(r.get("delta")) else np.nan
        out.update(consensus_check(r, a["value"], a["first_dt"], ga))
        d = r.get("nsur_drv")
        out["drv_hit"] = (np.sign(d) == np.sign(out["sur"] - r["nsur_mom"])) if (d is not None and pd.notna(d) and d != 0
                                                                                 and pd.notna(out["sur"])) else np.nan
        rows.append(out)
    c = pd.DataFrame(rows)
    return add_cars(c) if with_returns and len(c) else c


def consensus_check(r: Dict, actual: float, actual_dt, ga: float) -> Dict:
    e, fq, base = r["entity_id"], r["fiscal_quarter"], r["rev_base"]
    cp = kr_consensus.read_consensus(e, fq, r["asof_date"])["value"]
    cf = kr_consensus.read_consensus(e, fq, actual_dt, strict=True)["value"] if actual_dt is not None and pd.notna(actual_dt) else None
    err = lambda v: (float(np.log(v) - np.log(base)) - ga) if v and v > 0 else np.nan
    rev_e = r.get("rev_E")
    gap = (float(rev_e) / cp - 1) if cp and rev_e is not None and pd.notna(rev_e) else np.nan
    sc = (actual / cf - 1) if cf else np.nan
    hit = (np.sign(gap) == np.sign(sc)) if pd.notna(gap) and pd.notna(sc) and gap != 0 and sc != 0 else np.nan
    return {"c_pre5": cp if cp else np.nan, "c_final": cf if cf else np.nan, "err_C_pre5": err(cp), "err_C_final": err(cf),
            "gap_E_c": gap, "surprise_c": sc, "gap_hit": hit}


# ---------------------------------------------------------------- 성적표 항목
def _hit(c: pd.DataFrame) -> Dict:
    x = c["drv_hit"].dropna() if "drv_hit" in c else pd.Series(dtype=float)
    y = c["gap_hit"].dropna() if "gap_hit" in c else pd.Series(dtype=float)
    return {"n": len(x), "drv_hit_rate": float(x.astype(bool).mean()) if len(x) else np.nan,
            "n_gap": len(y), "gap_hit_rate": float(y.astype(bool).mean()) if len(y) else np.nan}


def _win(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in ("M4", "E"):
        x = c.dropna(subset=[f"err_{m}", "err_B2"])
        rows.append({"model": m, "n": len(x), "win_rate_vs_B2": float((x[f"err_{m}"].abs() < x["err_B2"].abs()).mean())
                     if len(x) else np.nan})
    return pd.DataFrame(rows)


def item1_hit(c: pd.DataFrame) -> pd.DataFrame:
    rows = [{"split": "전체", "group": "전체", **_hit(c)}]
    rows += [{"split": "등급", "group": g, **_hit(x)} for g, x in c.groupby("grade")]
    rows += [{"split": "M4·E 일치", "group": g, **_hit(x)} for g, x in c[c["agree"].isin(["일치", "불일치"])].groupby("agree")]
    return pd.DataFrame(rows)


def item2_errors(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in list(MODELS) + ["consensus", "C_pre5", "C_final"]:
        e = c[f"err_{m}"].dropna() if f"err_{m}" in c else pd.Series(dtype=float)
        rows.append({"model": m, "n": len(e), "mean_abs_err": e.abs().mean() if len(e) else np.nan,
                     "rmse": float(np.sqrt((e ** 2).mean())) if len(e) else np.nan, "mean_err": e.mean() if len(e) else np.nan})
    t = pd.DataFrame(rows)
    w = _win(c).set_index("model")["win_rate_vs_B2"]
    t["win_rate_vs_B2"] = t["model"].map(w)
    return t


def tercile_spread(c: pd.DataFrame, col: str) -> Dict:
    """nsur_drv 상위 1/3 과 하위 1/3 의 평균 col 차이 (col 과 nsur_drv 가 모두 있는 기업, 그 집합 안에서 나눔)."""
    x = c.dropna(subset=["nsur_drv", col]).sort_values("nsur_drv")
    n = len(x)
    k = n // 3
    if k < 1:
        return {"n": n, "n_each": 0, "top_mean": np.nan, "bottom_mean": np.nan, "spread": np.nan}
    lo, hi = x.iloc[:k], x.iloc[-k:]
    return {"n": n, "n_each": k, "top_mean": hi[col].mean(), "bottom_mean": lo[col].mean(),
            "spread": hi[col].mean() - lo[col].mean()}


def item3_returns(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for w, col in [("a 기준일 다음 날 ~ 발표 다음 날", "car_a"), ("b 발표 2일 후 ~ 20거래일", "car_b")]:
        if col not in c:
            continue
        rows.append({"window": w, "group": "전체", **tercile_spread(c, col)})
        rows += [{"window": w, "group": g, **tercile_spread(x, col)} for g, x in c.groupby("grade")]
    return pd.DataFrame(rows)


def item4_alert(c: pd.DataFrame) -> pd.DataFrame:
    c = c.assign(alert=c["shock_alert"].fillna("").str.startswith("경보"))
    return pd.DataFrame([{"group": "경보" if k else "비경보", "n_firms": len(g),
                          "M4_mean_abs_err": g["err_M4"].abs().mean(), **_hit(g)} for k, g in c.groupby("alert")])


def item5_grade(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for gr, g in c.groupby("grade"):
        w = _win(g).set_index("model")["win_rate_vs_B2"]
        rows.append({"grade": gr, "n_firms": len(g), "M4_mean_abs_err": g["err_M4"].abs().mean(),
                     "E_mean_abs_err": g["err_E"].abs().mean(), "B2_mean_abs_err": g["err_B2"].abs().mean(),
                     "M4_win_vs_B2": w.get("M4"), "E_win_vs_B2": w.get("E"), **_hit(g)})
    return pd.DataFrame(rows)


def scorecard(c: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    c = c[c["status"] == "비교"].copy()
    return {"1_hit": item1_hit(c), "2_errors": item2_errors(c), "3_returns": item3_returns(c),
            "4_alert": item4_alert(c), "5_by_grade": item5_grade(c)}


def by_asof(c: pd.DataFrame) -> Dict[str, Dict[str, pd.DataFrame]]:
    """기록 회차별 성적표. 실전 기록은 한 번에 기준일이 하나라 회차 = 기준일 (1차·2차 기록 비교).
    키 = '<기준일 범위> (record_id)'. record_id 가 없으면 기준일로 묶는다."""
    key = "record_id" if "record_id" in c and c["record_id"].notna().all() else "asof_date"
    out = {}
    for k, g in c.groupby(key):
        d = pd.to_datetime(g["asof_date"])
        span = f"{d.min().date()}" if d.min() == d.max() else f"{d.min().date()}~{d.max().date()}"
        out[f"{span} ({k})" if key == "record_id" else span] = scorecard(g)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--record-id", default=None)
    a = ap.parse_args(argv)
    c = compare(load(a.quarter, record_id=a.record_id))
    d = output_path("radar", a.quarter, "scorecard")
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    c.to_csv(d / f"compare_{a.quarter}_{stamp}.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 220)
    for i, (t, sc) in enumerate(by_asof(c).items(), 1):
        print(f"######## 기록 {i}: 기준일 {t}")
        for k, v in sc.items():
            v.to_csv(d / f"scorecard_rec{i}_{k}_{a.quarter}_{stamp}.csv", index=False, encoding="utf-8-sig")
            print("==", k)
            print(v.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
