# -*- coding: utf-8 -*-
"""R1 (d): 발표 후 맞춰 보기 (39곳, M4·E·B2·TS·컨센서스) 와 성적표.

compare(fiscal_quarter): fd_live_record 의 기록 행과 실제 첫 공시 값 비교 (2a evaluate.live_check.actual_first 와 같은 값).
  g_actual = ln(실제 / rev_base). 모델 오차 = g_모델 - g_actual. 컨센서스 오차 = ln(컨센서스 / rev_base) - g_actual.
  SUR = (실제 - rev_base - delta) / sigma (기록 시점 분모). drv 적중 = sign(nsur_drv) == sign(SUR - nsur_mom).
scorecard(compared): 성적표 1~5.
  ※ 레이더 명세서 v1 6절의 성적표 정의는 R1 지시문에 실려 있지 않아(명세서 링크는 Claude Code 에서 열리지 않음)
    아래 다섯 항목을 잠정 정의로 두었다. 설계실 확인 후 바꿀 수 있다.
  1 모델별 오차: M4, E, B2, TS, 컨센서스의 평균 절대 로그 오차, RMSE, 기업 수
  2 B2 대비 승률: M4, E 각각 |오차| < |B2 오차| 인 기업 비율
  3 드라이버 방향 적중: nsur_drv != 0 인 기업 중 sign(nsur_drv) == sign(SUR - nsur_mom) 비율
  4 충격 경보: 경보 기업과 비경보 기업의 M4 평균 절대 오차, 3 의 적중률
  5 등급별: A·B·C (시범 포함) 별 1~3 요약
실행: python -m DATA.fdriver.radar.scorecard --quarter 2026Q3 [--record-id ...]
"""
import argparse
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..evaluate.live_check import actual_first
from .record import T_RECORD

MODELS = {"M4": "g_M4", "E": "g_E", "B2": "g_B2", "TS": "g_TS"}


def load(fiscal_quarter: str, table: str = T_RECORD, record_id: Optional[str] = None) -> pd.DataFrame:
    sql = f"SELECT * FROM `{table}` WHERE fiscal_quarter = :q"
    p = {"q": fiscal_quarter}
    if record_id:
        sql += " AND record_id = :r"
        p["r"] = record_id
    return db.read_sql(sql, p)


def compare(rec: pd.DataFrame) -> pd.DataFrame:
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
        for m, c in MODELS.items():
            out[f"err_{m}"] = (r[c] - ga) if r.get(c) is not None and pd.notna(r.get(c)) else np.nan
        cons = r.get("consensus")
        out["err_consensus"] = (np.log(cons) - np.log(r["rev_base"]) - ga) if cons and pd.notna(cons) and cons > 0 else np.nan
        sig = r.get("sigma")
        out["sur"] = (a["value"] - r["rev_base"] - r["delta"]) / sig if sig and pd.notna(sig) and pd.notna(r.get("delta")) else np.nan
        d = r.get("nsur_drv")
        out["drv_hit"] = (np.sign(d) == np.sign(out["sur"] - r["nsur_mom"])) if (d is not None and pd.notna(d) and d != 0
                                                                                 and pd.notna(out["sur"])) else np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def _err_stats(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in list(MODELS) + ["consensus"]:
        e = c[f"err_{m}"].dropna() if f"err_{m}" in c else pd.Series(dtype=float)
        rows.append({"model": m, "n": len(e), "mean_abs_err": e.abs().mean() if len(e) else np.nan,
                     "rmse": float(np.sqrt((e ** 2).mean())) if len(e) else np.nan, "mean_err": e.mean() if len(e) else np.nan})
    return pd.DataFrame(rows)


def _win(c: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for m in ("M4", "E"):
        x = c.dropna(subset=[f"err_{m}", "err_B2"])
        rows.append({"model": m, "n": len(x), "win_rate_vs_B2": float((x[f"err_{m}"].abs() < x["err_B2"].abs()).mean())
                     if len(x) else np.nan})
    return pd.DataFrame(rows)


def _hit(c: pd.DataFrame) -> Dict:
    x = c["drv_hit"].dropna() if "drv_hit" in c else pd.Series(dtype=float)
    return {"n": len(x), "drv_hit_rate": float(x.astype(bool).mean()) if len(x) else np.nan}


def scorecard(c: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    c = c[c["status"] == "비교"].copy()
    s1 = _err_stats(c)
    s2 = _win(c)
    s3 = pd.DataFrame([_hit(c)])
    c["alert"] = c["shock_alert"].fillna("").str.startswith("경보")
    s4 = pd.DataFrame([{"group": "경보" if k else "비경보", "n": len(g),
                        "M4_mean_abs_err": g["err_M4"].abs().mean(), **_hit(g)} for k, g in c.groupby("alert")])
    s5 = []
    for gr, g in c.groupby("grade"):
        w = _win(g).set_index("model")["win_rate_vs_B2"]
        s5.append({"grade": gr, "n": len(g), "M4_mean_abs_err": g["err_M4"].abs().mean(),
                   "E_mean_abs_err": g["err_E"].abs().mean(), "B2_mean_abs_err": g["err_B2"].abs().mean(),
                   "M4_win_vs_B2": w.get("M4"), "E_win_vs_B2": w.get("E"), **_hit(g)})
    return {"1_errors": s1, "2_win_vs_B2": s2, "3_drv_hit": s3, "4_alert": s4, "5_by_grade": pd.DataFrame(s5)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--record-id", default=None)
    a = ap.parse_args(argv)
    c = compare(load(a.quarter, record_id=a.record_id))
    sc = scorecard(c)
    d = output_path("radar", a.quarter, "scorecard")
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    c.to_csv(d / f"compare_{a.quarter}_{stamp}.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 220)
    for k, v in sc.items():
        v.to_csv(d / f"scorecard_{k}_{a.quarter}_{stamp}.csv", index=False, encoding="utf-8-sig")
        print("==", k)
        print(v.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
