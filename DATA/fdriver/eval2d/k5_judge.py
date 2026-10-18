# -*- coding: utf-8 -*-
"""2d 작업 5: D1~D3 판정 (명세서 2d v3 7절). 쓰기는 fd_eval_2d 만. 기준은 결과를 보기 전에 아래에 고정한다.

입력: fd_forecast_2d (최신 run), fd_driver_map_2d, fd_op_rule, fd_rev_adj_2d, 영업이익 (K3 와 같은 원천).
평가 구간 2024Q1~2026Q2, 예측 시점 pre5.

공통
- 표준오차: 기업·분기 이중 군집 (eval2b.k3_analysis.twoway_ols 와 같음. 이중 분산이 음수면 한 방향 군집 중 큰 것).
- p: 성공 방향 단측 (2b Q1, 2c 판정과 같은 관례). 양측 p 도 함께 낸다.
D1 예측력 (driver 기업)
- e_E = |ln(실적/E)|, e_B2 = |ln(실적/B2)|, d = e_E - e_B2. d ~ 1 회귀 (상수의 이중 군집 표준오차).
- 성공: 중앙값(e_E) < 중앙값(e_B2) 이고 상수 < 0, 단측 p < 0.05.
- 판정 표본: driver & flag_jump = 0. flag_jump = 1 은 같은 검정을 따로, 전체(driver 모두)는 참고.
- 보조: 기업별 승률(기업 안에서 e_E < e_B2 인 분기 비율 > 0.5 인 기업 비율), 분기별 결과, E_fill 로 바꾼 결과,
  M1 이 빠진 행(e_label 'missing:M1')을 뺀 결과. D1 실패 시 업종·출처·E_spread 구간(<5, 5~15, >=15%)별 원인 분석.
D2 주가 반영 (coverage_class '없음' & driver)
- CAR[0,+1] (%): 발표일(첫 공시일) 0일, 0~+1 거래일 (종목 일간 수익률 - 시장 수익률) 합. 시장은 코스피 상장사 YAHOO:^KS11,
  코스닥 상장사 YAHOO:^KQ11 (kr_stock_index_daily). 종목 수익률은 KSE_Price prc_change, 거래량 0 인 날은 결측이고 창 안에
  결측이 있으면 그 이벤트를 뺀다. fd_driver_map_2d 의 price_excl_from~to 와 [-5,+1] 창이 겹치면 뺀다.
- gap_E_vs_B2 = ln(E) - ln(B2). CAR ~ gap 이중 군집. 성공: 계수 > 0, 단측 p < 0.05.
- 보조: 분기별 gap 상위 1/3 - 하위 1/3 CAR 평균 차이의 분기 평균 (eval2b tercile_by_quarter), CAR[-5,+1],
  품목 분할: 드라이버 출처가 dart 이고 그 코드의 DART 매출 비중 합 >= 30% 인 기업 vs 그 밖.
D3 영업이익 (R1~R3, OP_hat·OP_naive·실적 영업이익이 있는 행)
- 오차 = |예측 - 실적 OP| / 최근 4분기(q-4~q-1) 평균 실적 매출 x 100 (%p). d = e_hat - e_naive, d ~ 1 이중 군집.
- 성공: 중앙값(e_hat) < 중앙값(e_naive) 이고 상수 < 0, 단측 p < 0.05. 규칙별(R1/R2/R3 대체 포함)도 보고.
판정 운영 (7절): D1·D2 성공 -> 실전 전환 권고 / D1 만 성공 -> 레이더 유지·매매 신호 보류 / D1 실패 -> 원인 분석까지.
실행: python -m DATA.fdriver.eval2d.k5_judge
"""
import json
from datetime import datetime
from typing import Dict

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from .. import db
from ..config import output_path
from ..eval2b.k3_analysis import tercile_by_quarter
from ..stage2c.s3_lib import one_sided
from . import k3_oprule as K3
from .k2_targets import read_adj

OUT = output_path("2d", "K5")
T_EVAL = "fd_eval_2d"
ALPHA = 0.05
SHARE_MIN = 30.0
EVAL = pd.period_range("2024Q1", "2026Q2", freq="Q").to_timestamp(how="end").normalize()
IDX = {"KOSPI": "YAHOO:^KS11", "KOSDAQ": "YAHOO:^KQ11"}

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_EVAL}` (
  entity_id VARCHAR(20) NOT NULL,
  quarter CHAR(6) NOT NULL,
  run_id VARCHAR(20) NOT NULL,
  forecast_run_id VARCHAR(20) NULL,
  name VARCHAR(100) NULL,
  status VARCHAR(10) NULL, rule VARCHAR(10) NULL, coverage_class VARCHAR(10) NULL, market VARCHAR(10) NULL,
  industry_group VARCHAR(40) NULL, drv_source VARCHAR(20) NULL, drv_code VARCHAR(60) NULL, e_label VARCHAR(80) NULL,
  asof_date DATE NULL, announce_date DATE NULL,
  rev_actual DOUBLE NULL, op_actual DOUBLE NULL, rev_avg4 DOUBLE NULL,
  E DOUBLE NULL, B2 DOUBLE NULL, TS DOUBLE NULL, M1 DOUBLE NULL, E_fill DOUBLE NULL, E_spread DOUBLE NULL,
  err_E DOUBLE NULL, err_B2 DOUBLE NULL, err_E_fill DOUBLE NULL,
  gap_E_vs_B2 DOUBLE NULL, car_0_1 DOUBLE NULL, car_m5_1 DOUBLE NULL, car_note VARCHAR(60) NULL,
  OP_hat DOUBLE NULL, OP_naive DOUBLE NULL, err_op_hat DOUBLE NULL, err_op_naive DOUBLE NULL,
  flag_jump TINYINT NULL, flag_basis TINYINT NULL, flag_spread TINYINT NULL, flag_q4 TINYINT NULL,
  share_big TINYINT NULL,
  in_d1 TINYINT NULL, in_d1_jump TINYINT NULL, in_d2 TINYINT NULL, in_d3 TINYINT NULL,
  first_collected_at DATETIME NULL, last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, quarter, run_id)
) DEFAULT CHARSET=utf8mb4
"""


# ------------------------------------------------------------------ 검정
def paired_test(d: pd.DataFrame, a: str, b: str) -> Dict:
    """d = a - b 의 평균 < 0 검정 (이중 군집). 중앙값도 낸다."""
    z = d.dropna(subset=[a, b]).copy()
    z["dd"] = z[a] - z[b]
    out = {"n": len(z), "n_firms": int(z["entity_id"].nunique()), "n_quarters": int(z["quarter"].nunique()),
           f"median_{a}": float(z[a].median()) if len(z) else np.nan,
           f"median_{b}": float(z[b].median()) if len(z) else np.nan,
           "mean_diff": float(z["dd"].mean()) if len(z) else np.nan,
           "share_a_better": float((z["dd"] < 0).mean()) if len(z) else np.nan}
    if len(z) < 5 or z["entity_id"].nunique() < 2 or z["quarter"].nunique() < 2:
        return {**out, "se": np.nan, "p_two": np.nan, "p_one": np.nan, "success": False}
    g = np.column_stack([pd.factorize(z["entity_id"])[0], pd.factorize(z["quarter"])[0]])
    m = smf.ols("dd ~ 1", data=z)
    f = m.fit(cov_type="cluster", cov_kwds={"groups": g}, use_t=True)
    b0, se, p2, fb = float(f.params["Intercept"]), float(f.bse["Intercept"]), float(f.pvalues["Intercept"]), ""
    if not np.isfinite(se):
        one = [m.fit(cov_type="cluster", cov_kwds={"groups": g[:, k]}, use_t=True) for k in (0, 1)]
        k = int(np.argmax([float(o.bse["Intercept"]) for o in one]))
        se, p2, fb = float(one[k].bse["Intercept"]), float(one[k].pvalues["Intercept"]), ("firm", "quarter")[k] + "_only"
    p1 = one_sided(p2, b0, "less")
    ok = bool(out[f"median_{a}"] < out[f"median_{b}"] and b0 < 0 and p1 < ALPHA)
    return {**out, "mean_diff": b0, "se": se, "p_two": p2, "p_one": p1, "se_fallback": fb, "success": ok}


def slope_test(d: pd.DataFrame, y: str, x: str) -> Dict:
    z = d.dropna(subset=[y, x])
    if len(z) < 5 or z["entity_id"].nunique() < 2 or z["quarter"].nunique() < 2:
        return {"n": len(z), "b": np.nan, "se": np.nan, "p_two": np.nan, "p_one": np.nan, "success": False}
    g = np.column_stack([pd.factorize(z["entity_id"])[0], pd.factorize(z["quarter"])[0]])
    m = smf.ols(f"{y} ~ {x}", data=z)
    f = m.fit(cov_type="cluster", cov_kwds={"groups": g}, use_t=True)
    b, se, p2, fb = float(f.params[x]), float(f.bse[x]), float(f.pvalues[x]), ""
    if not np.isfinite(se):
        one = [m.fit(cov_type="cluster", cov_kwds={"groups": g[:, k]}, use_t=True) for k in (0, 1)]
        k = int(np.argmax([float(o.bse[x]) for o in one]))
        se, p2, fb = float(one[k].bse[x]), float(one[k].pvalues[x]), ("firm", "quarter")[k] + "_only"
    p1 = one_sided(p2, b, "greater")
    return {"n": len(z), "n_firms": int(z["entity_id"].nunique()), "n_quarters": int(z["quarter"].nunique()),
            "a": float(f.params["Intercept"]), "b": b, "se": se, "p_two": p2, "p_one": p1, "r2": float(f.rsquared),
            "se_fallback": fb, "success": bool(b > 0 and p1 < ALPHA)}


# ------------------------------------------------------------------ 주가
def car_frame(ev: pd.DataFrame) -> pd.DataFrame:
    """ev: entity_id, quarter, announce_date, market, excl_from, excl_to -> car_0_1, car_m5_1, car_note."""
    ids = sorted(ev["entity_id"].unique())
    f, p = db.in_clause("c", ids)
    px = db.read_sql(f"SELECT code, date, prc_change, volume FROM KSE_Price WHERE code IN {f} AND date >= '2024-01-01'", p)
    px["date"] = pd.to_datetime(px["date"])
    px["ret"] = np.where(px["volume"].fillna(0) > 0, px["prc_change"] / 100.0, np.nan)
    mk = {}
    for k, sym in IDX.items():
        m = db.read_sql("SELECT period_end, close FROM kr_stock_index_daily WHERE idx_name = :n AND period_end >= "
                        "'2023-12-01' ORDER BY period_end", {"n": sym})
        m["period_end"] = pd.to_datetime(m["period_end"])
        mk[k] = m.set_index("period_end")["close"].pct_change()
    cal = pd.DatetimeIndex(sorted(set(px["date"])))
    rows = []
    for r in ev.itertuples():
        out = {"entity_id": r.entity_id, "quarter": r.quarter, "car_0_1": np.nan, "car_m5_1": np.nan, "car_note": ""}
        d0 = int(cal.searchsorted(pd.Timestamp(r.announce_date)))
        if d0 + 1 >= len(cal) or d0 - 5 < 0:
            rows.append({**out, "car_note": "window_out_of_data"})
            continue
        win = cal[d0 - 5:d0 + 2]
        if pd.notna(r.excl_from) and (win[-1] >= pd.Timestamp(r.excl_from)) and (win[0] <= pd.Timestamp(r.excl_to)):
            rows.append({**out, "car_note": "price_excl"})
            continue
        s = px[px["code"] == r.entity_id].set_index("date")["ret"].reindex(win)
        m = mk.get(r.market, mk["KOSPI"]).reindex(win)
        ex = (s - m) * 100
        e01, e51 = ex.iloc[5:7], ex
        out["car_0_1"] = float(e01.sum()) if e01.notna().all() else np.nan
        out["car_m5_1"] = float(e51.sum()) if e51.notna().all() else np.nan
        if pd.isna(out["car_0_1"]):
            out["car_note"] = "missing_return"
        rows.append(out)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ main
def build_frame() -> pd.DataFrame:
    fc = db.read_sql("SELECT * FROM fd_forecast_2d")
    run_fc = fc["run_id"].max()
    fc = fc[fc["run_id"] == run_fc].drop(columns=["first_collected_at", "last_collected_at"], errors="ignore")
    mp = db.read_sql("SELECT entity_id, name, source, price_excl_from, price_excl_to FROM fd_driver_map_2d "
                     "WHERE status IN ('driver','ts_only')")
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str})
    ids = fc["entity_id"].unique().tolist()
    adj = read_adj(ids)
    it = K3.items(ids, adj, quarters=None)
    op = it[it["item"] == "op"].rename(columns={"value": "op_actual"})[["entity_id", "period_end", "op_actual"]]
    rev = adj[["entity_id", "period_end", "revenue_adj"]].rename(columns={"revenue_adj": "rev_actual"})
    fc["period_end"] = pd.to_datetime(fc["period_end"])
    d = fc.merge(rev, on=["entity_id", "period_end"], how="left").merge(op, on=["entity_id", "period_end"], how="left")
    # 최근 4분기 평균 매출 (q-4 ~ q-1 실적)
    rw = adj.pivot_table(index="period_end", columns="entity_id", values="revenue_adj").sort_index()
    avg4 = rw.shift(1).rolling(4, min_periods=4).mean()
    d["rev_avg4"] = [avg4.at[q, e] if (q in avg4.index and e in avg4.columns) else np.nan
                     for q, e in zip(d["period_end"], d["entity_id"])]
    d = d.merge(mp, on="entity_id", how="left").merge(
        k1[["ticker", "market", "industry_group"]].rename(columns={"ticker": "entity_id"}), on="entity_id", how="left")
    d["drv_source"] = d["source"]
    d["forecast_run_id"] = run_fc
    # 오차
    lg = lambda a, f: np.abs(np.log(a / f)).where((a > 0) & (f > 0))
    d["err_E"], d["err_B2"], d["err_E_fill"] = lg(d["rev_actual"], d["E"]), lg(d["rev_actual"], d["B2"]), \
        lg(d["rev_actual"], d["E_fill"])
    d["gap_E_vs_B2"] = np.log(d["E"] / d["B2"])
    d["err_op_hat"] = (d["OP_hat"] - d["op_actual"]).abs() / d["rev_avg4"] * 100
    d["err_op_naive"] = (d["OP_naive"] - d["op_actual"]).abs() / d["rev_avg4"] * 100
    # 품목 비중 (K2b 후보표의 DART 매출 비중 합)
    cb = pd.read_csv(output_path("2d", "K2b") / "k2b_candidates_all.csv", dtype={"ticker": str, "code": str})
    sh = cb.set_index(["ticker", "code"])["product_share"]
    d["share_big"] = [int(src == "dart" and pd.notna(sh.get((e, c))) and sh.get((e, c)) >= SHARE_MIN)
                      for e, c, src in zip(d["entity_id"], d["drv_code"].fillna(""), d["drv_source"].fillna(""))]
    # 표본
    drv = d["status"] == "driver"
    d["in_d1"] = (drv & (d["flag_jump"] == 0)).astype(int)
    d["in_d1_jump"] = (drv & (d["flag_jump"] == 1)).astype(int)
    d["in_d2"] = (drv & (d["coverage_class"] == "없음")).astype(int)
    d["in_d3"] = (d["rule"].isin(["R1", "R2", "R3"]) & d["OP_hat"].notna() & d["OP_naive"].notna()
                  & d["op_actual"].notna() & d["rev_avg4"].notna()).astype(int)
    # CAR
    ev = d[d["in_d2"] == 1][["entity_id", "quarter", "announce_date", "market", "price_excl_from", "price_excl_to"]]
    ev = ev.rename(columns={"price_excl_from": "excl_from", "price_excl_to": "excl_to"})
    cars = car_frame(ev) if len(ev) else pd.DataFrame(columns=["entity_id", "quarter", "car_0_1", "car_m5_1", "car_note"])
    d = d.merge(cars, on=["entity_id", "quarter"], how="left")
    return d


def by_group(d, col, a, b) -> pd.DataFrame:
    rows = []
    for k, g in d.groupby(col):
        r = paired_test(g, a, b)
        rows.append({col: k, **{x: r.get(x) for x in ("n", "n_firms", f"median_{a}", f"median_{b}", "mean_diff",
                                                       "p_one", "share_a_better")}})
    return pd.DataFrame(rows)


def firm_win(d, a, b) -> Dict:
    z = d.dropna(subset=[a, b])
    w = z.groupby("entity_id").apply(lambda g: (g[a] < g[b]).mean())
    return {"firms": int(len(w)), "share_firms_E_wins": float((w > 0.5).mean()) if len(w) else np.nan,
            "share_firms_tie": float((w == 0.5).mean()) if len(w) else np.nan}


def main(write: bool = True):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = build_frame()
    d1s = d[d["in_d1"] == 1]
    res = {"run_id": run_id, "forecast_run_id": d["forecast_run_id"].iloc[0]}
    # D1
    res["D1"] = paired_test(d1s, "err_E", "err_B2")
    res["D1_jump"] = paired_test(d[d["in_d1_jump"] == 1], "err_E", "err_B2")
    res["D1_all_driver"] = paired_test(d[d["status"] == "driver"], "err_E", "err_B2")
    res["D1_no_missing_M1"] = paired_test(d1s[~d1s["e_label"].fillna("").str.contains("missing:M1")], "err_E", "err_B2")
    res["D1_E_fill"] = paired_test(d1s, "err_E_fill", "err_B2")
    res["D1_firm_win"] = firm_win(d1s, "err_E", "err_B2")
    res["D1_ts_only_ref"] = paired_test(d[d["status"] == "ts_only"], "err_E", "err_B2")
    q1 = by_group(d1s, "quarter", "err_E", "err_B2")
    d1s = d1s.assign(spread_bin=pd.cut(d1s["E_spread"], [-np.inf, 5, 15, np.inf], labels=["<5", "5~15", ">=15"]))
    g_ind = by_group(d1s, "industry_group", "err_E", "err_B2")
    g_src = by_group(d1s, "drv_source", "err_E", "err_B2")
    g_spr = by_group(d1s, "spread_bin", "err_E", "err_B2")
    # D2
    d2 = d[(d["in_d2"] == 1)]
    res["D2"] = slope_test(d2, "car_0_1", "gap_E_vs_B2")
    res["D2_events"] = {"rows": len(d2), "with_car": int(d2["car_0_1"].notna().sum()),
                        "car_note": d2["car_note"].fillna("").value_counts().to_dict()}
    t = tercile_by_quarter(d2.rename(columns={"entity_id": "ticker"}), "gap_E_vs_B2", "car_0_1")
    res["D2_tercile_0_1"] = {k: v for k, v in t.items() if k != "table"}
    t5 = tercile_by_quarter(d2.rename(columns={"entity_id": "ticker"}), "gap_E_vs_B2", "car_m5_1")
    res["D2_tercile_m5_1"] = {k: v for k, v in t5.items() if k != "table"}
    res["D2_car_m5_1"] = slope_test(d2, "car_m5_1", "gap_E_vs_B2")
    res["D2_share_big"] = slope_test(d2[d2["share_big"] == 1], "car_0_1", "gap_E_vs_B2")
    res["D2_share_other"] = slope_test(d2[d2["share_big"] == 0], "car_0_1", "gap_E_vs_B2")
    res["P1_note"] = "P1 (K2): B2 를 기대 대용으로 쓰기에 '기대 대용 약함' (부호 일치율 63.9%, 기준 65%)"
    # D3
    d3 = d[d["in_d3"] == 1]
    res["D3"] = paired_test(d3, "err_op_hat", "err_op_naive")
    d3 = d3.assign(rule_eff=np.where(d3["op_note"].fillna("").str.contains("R3 대체"), "R2->R3", d3["rule"]))
    g_rule = by_group(d3, "rule_eff", "err_op_hat", "err_op_naive")
    res["D3_by_rule"] = g_rule.to_dict(orient="records")
    # 운영 판정
    d1ok, d2ok = res["D1"]["success"], res["D2"]["success"]
    res["decision"] = ("D1·D2 모두 성공: 확대 레이더 실전 전환 권고" if d1ok and d2ok else
                       "D1 만 성공: 레이더 유지, 매매 신호로는 쓰지 않음" if d1ok else
                       "D1 실패: 원인 분석까지만 (업종·출처·E_spread 구간)")
    # 저장
    cols = ["entity_id", "quarter", "run_id", "forecast_run_id", "name", "status", "rule", "coverage_class", "market",
            "industry_group", "drv_source", "drv_code", "e_label", "asof_date", "announce_date", "rev_actual",
            "op_actual", "rev_avg4", "E", "B2", "TS", "M1", "E_fill", "E_spread", "err_E", "err_B2", "err_E_fill",
            "gap_E_vs_B2", "car_0_1", "car_m5_1", "car_note", "OP_hat", "OP_naive", "err_op_hat", "err_op_naive",
            "flag_jump", "flag_basis", "flag_spread", "flag_q4", "share_big", "in_d1", "in_d1_jump", "in_d2", "in_d3"]
    d["run_id"] = run_id
    out = d[cols].copy()
    for c in ("asof_date", "announce_date"):
        out[c] = pd.to_datetime(out[c]).dt.date
    n = 0
    if write:
        db.execute_fd_ddl(DDL)
        n = db.write_fd(out, T_EVAL, ["entity_id", "quarter", "run_id"])
    res["rows_written"] = n
    out.to_csv(OUT / f"fd_eval_2d_{run_id}.csv", index=False, encoding="utf-8-sig")
    q1.to_csv(OUT / "k5_d1_by_quarter.csv", index=False, encoding="utf-8-sig")
    g_ind.to_csv(OUT / "k5_d1_by_industry.csv", index=False, encoding="utf-8-sig")
    g_src.to_csv(OUT / "k5_d1_by_source.csv", index=False, encoding="utf-8-sig")
    g_spr.to_csv(OUT / "k5_d1_by_spread.csv", index=False, encoding="utf-8-sig")
    g_rule.to_csv(OUT / "k5_d3_by_rule.csv", index=False, encoding="utf-8-sig")
    t["table"].to_csv(OUT / "k5_d2_tercile_by_quarter.csv", index=False, encoding="utf-8-sig")
    (OUT / "k5_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return d, res


if __name__ == "__main__":
    main()
