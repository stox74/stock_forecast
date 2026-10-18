# -*- coding: utf-8 -*-
"""2d 작업 3: 영업이익 규칙 배정 (명세서 2d v3 6절). 쓰기는 fd_op_rule 만.

대상: fd_driver_map_2d 의 status driver + ts_only (excluded 제외). 선정 구간 2019Q1~2023Q4 자료만 쓴다.
1. 분기 자료 (원)
   - 매출: fd_rev_adj_2d.revenue_adj
   - 영업이익·매출원가·판관비는 매출과 같은 원천을 쓴다.
     매출 기준 DG 인 기업: DataGuide (영업이익 M000906001, 매출원가 M000905001, 판관비 = 매출총이익 M000904007 - 영업이익).
     DG 가 빈 분기는 DART 값 (fd_rev_adj_2d 에서 그 분기 매출을 DART 로 채운 경우와 같음).
     매출 기준 DART 인 기업: DART V3 -> V2 (k1_survey 와 같은 계정, to_standalone: 4분기 = 연간 - 1~3분기).
   - 매출원가 0 이하는 없음으로 본다.
2. 규칙 (R4 를 먼저 거르고 R1 -> R2 -> R3)
   - R4: 분기 매출 50억 원 미만 분기 4개 이상, 또는 c = (매출 - 영업이익) / 매출 의 표준편차 > 0.15
   - R1: OLS OP = a + b * Rev, HAC 표준오차 (Newey-West, maxlags 4), b > 0, p(b, 양측) < 0.05, R^2 >= 0.3
   - R2: 매출원가·판관비가 선정 구간 20분기 모두 있음
   - R3: 그 외
   영업이익이나 매출이 빠진 분기는 빼고 계산한다 (n_q). n_q < 12 면 R4 ('자료 부족').
3. 표시
   - flag_spread: 업종코드 앞자리 19 (코크스·석유정제), 201 (기초화학), 203 (합성수지·고무), 205 (화학섬유),
     241 (1차 철강), 242 (1차 비철금속).
   - flag_q4: 어느 해든 4분기 판관비 > 그해 분기 판관비 평균 x 1.5. 판관비가 없는 기업은 영업비용(매출 - 영업이익)으로 본다.
실행: python -m DATA.fdriver.eval2d.k3_oprule
"""
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .. import db
from ..config import output_path
from . import k1_survey as K1
from .k1_survey import SEL
from .k2_targets import read_adj

log = logging.getLogger(__name__)
OUT = output_path("2d", "K3")
T_RULE = "fd_op_rule"
REV_MIN = 50e8
REV_MIN_Q = 4
C_SD_MAX = 0.15
R2_MIN = 0.30
P_MAX = 0.05
HAC_LAGS = 4
N_MIN = 12
Q4_RATIO = 1.5
SPREAD_KSIC = ("19", "201", "203", "205", "241", "242")

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_RULE}` (
  entity_id VARCHAR(20) NOT NULL,
  name VARCHAR(100) NULL,
  status VARCHAR(10) NULL,
  rule VARCHAR(4) NOT NULL,
  rule_reason VARCHAR(200) NULL,
  a DOUBLE NULL,
  b DOUBLE NULL,
  p_hac DOUBLE NULL,
  r2 DOUBLE NULL,
  c_mean DOUBLE NULL,
  c_sd DOUBLE NULL,
  n_q INT NULL,
  n_rev_lt50 INT NULL,
  n_cogs INT NULL,
  n_sga INT NULL,
  item_basis VARCHAR(10) NULL,
  flag_spread TINYINT NULL,
  flag_q4 TINYINT NULL,
  q4_years VARCHAR(60) NULL,
  q4_measure VARCHAR(10) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id)
) DEFAULT CHARSET=utf8mb4
"""


def targets() -> pd.DataFrame:
    m = db.read_sql("SELECT entity_id, name, status FROM fd_driver_map_2d WHERE status IN ('driver', 'ts_only')")
    m = m.drop_duplicates("entity_id")
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "ksic": str})
    m = m.merge(k1[["ticker", "ksic", "industry_group"]].rename(columns={"ticker": "entity_id"}), on="entity_id",
                how="left")
    return m


def items(ids, adj: pd.DataFrame, quarters=SEL) -> pd.DataFrame:
    """entity_id, period_end, item(op/cogs/sga), value, src. 매출 기준과 같은 원천. quarters=None 이면 기간 제한 없음 (작업 4)."""
    v3, _ = K1._standalone_items(K1._read_dart_items("korea_fs_data_from_DART_V3", ids), "V3")
    v2, _ = K1._standalone_items(K1._read_dart_items("korea_fs_data_from_DART_V2", ids), "V2")
    dg = K1._read_dg_items(ids)
    cols = ["entity_id", "period_end", "item", "value", "src"]
    dart = pd.concat([v3[cols], v2[cols]]).sort_values("src", key=lambda s: s.map({"V3": 0, "V2": 1}))
    dart = dart.drop_duplicates(["entity_id", "period_end", "item"])
    dg = dg[dg["item"].isin(["op", "cogs", "sga"])][cols]
    basis = adj.drop_duplicates("entity_id").set_index("entity_id")["basis"]
    rows = []
    for e in ids:
        if basis.get(e) == "DG":
            g = dg[dg["entity_id"] == e]
            fill = dart[(dart["entity_id"] == e)]
            have = set(zip(g["period_end"], g["item"]))
            fill = fill[[(p, i) not in have for p, i in zip(fill["period_end"], fill["item"])]]
            rows += [g, fill]
        else:
            rows.append(dart[dart["entity_id"] == e])
    out = pd.concat(rows, ignore_index=True)
    if quarters is not None:
        out = out[out["period_end"].isin(quarters)]
    out = out[~((out["item"] == "cogs") & (out["value"] <= 0))]
    return out


def hac_reg(y: pd.Series, x: pd.Series) -> dict:
    X = sm.add_constant(x.to_numpy(float))
    r = sm.OLS(y.to_numpy(float), X).fit(cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})
    return {"a": float(r.params[0]), "b": float(r.params[1]), "p_hac": float(r.pvalues[1]), "r2": float(r.rsquared)}


def assign(t: pd.DataFrame, adj: pd.DataFrame, it: pd.DataFrame) -> pd.DataFrame:
    rev = adj[adj["period_end"].isin(SEL)].pivot_table(index="period_end", columns="entity_id", values="revenue_adj")
    basis = adj.drop_duplicates("entity_id").set_index("entity_id")["basis"]
    rows = []
    for r in t.itertuples():
        e = r.entity_id
        g = it[it["entity_id"] == e].pivot_table(index="period_end", columns="item", values="value")
        g = g.reindex(SEL)
        for c in ("op", "cogs", "sga"):
            if c not in g:
                g[c] = np.nan
        g["rev"] = rev[e].reindex(SEL) if e in rev.columns else np.nan
        d = g.dropna(subset=["rev", "op"])
        d = d[d["rev"] > 0]
        cc = (d["rev"] - d["op"]) / d["rev"]
        out = {"entity_id": e, "name": r.name, "status": r.status, "ksic": r.ksic, "industry_group": r.industry_group,
               "item_basis": basis.get(e), "n_q": len(d), "n_rev_lt50": int((g["rev"] < REV_MIN).sum()),
               "n_cogs": int(g["cogs"].notna().sum()), "n_sga": int(g["sga"].notna().sum()),
               "c_mean": float(cc.mean()) if len(cc) else np.nan, "c_sd": float(cc.std()) if len(cc) > 1 else np.nan,
               "op_margin_mean": float((d["op"] / d["rev"]).mean()) if len(d) else np.nan,
               "n_loss_q": int((d["op"] < 0).sum())}
        out.update({"a": np.nan, "b": np.nan, "p_hac": np.nan, "r2": np.nan})
        if len(d) >= 3:
            out.update(hac_reg(d["op"], d["rev"]))
        # 표시
        k = str(r.ksic or "")
        out["flag_spread"] = int(any(k.startswith(p) for p in SPREAD_KSIC))
        meas = "sga" if g["sga"].notna().sum() >= 16 else "opex"
        s = g["sga"] if meas == "sga" else (g["rev"] - g["op"])
        yrs = []
        for y in sorted(set(s.index.year)):
            qy = s[s.index.year == y]
            q4 = qy[qy.index.month == 12]
            if len(qy.dropna()) == 4 and len(q4) and q4.iloc[0] > Q4_RATIO * qy.mean():
                yrs.append(str(y))
        out.update({"flag_q4": int(bool(yrs)), "q4_years": ",".join(yrs), "q4_measure": meas})
        # 규칙
        if len(d) < N_MIN:
            rule, why = "R4", f"자료 부족 (n_q {len(d)} < {N_MIN})"
        elif out["n_rev_lt50"] >= REV_MIN_Q:
            rule, why = "R4", f"매출 50억 미만 분기 {out['n_rev_lt50']}개"
        elif out["c_sd"] > C_SD_MAX:
            rule, why = "R4", f"c 표준편차 {out['c_sd']:.3f} > {C_SD_MAX}"
        elif out["b"] > 0 and out["p_hac"] < P_MAX and out["r2"] >= R2_MIN:
            rule, why = "R1", f"b {out['b']:.3f}, p {out['p_hac']:.4f}, R2 {out['r2']:.2f}"
        elif out["n_cogs"] == len(SEL) and out["n_sga"] == len(SEL):
            rule, why = "R2", "R1 미충족 + 매출원가·판관비 20분기"
        else:
            rule, why = "R3", f"R1 미충족, 매출원가 {out['n_cogs']}·판관비 {out['n_sga']}분기"
        r1_fail = []
        if rule in ("R2", "R3"):
            if not out["b"] > 0:
                r1_fail.append("b<=0")
            if not out["p_hac"] < P_MAX:
                r1_fail.append(f"p {out['p_hac']:.3f}")
            if not out["r2"] >= R2_MIN:
                r1_fail.append(f"R2 {out['r2']:.2f}")
            why += " (R1: " + ", ".join(r1_fail) + ")"
        out.update({"rule": rule, "rule_reason": why})
        # 항등식 점검: 매출 - 매출원가 - 판관비 vs 영업이익 (매출 대비 %p)
        idt = ((g["rev"] - g["cogs"] - g["sga"]) - g["op"]) / g["rev"]
        out["identity_gap_max_pp"] = float(idt.abs().max() * 100) if idt.notna().any() else np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def main(write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    t = targets()
    ids = t["entity_id"].tolist()
    adj = read_adj(ids)
    it = items(ids, adj)
    res = assign(t, adj, it)
    res["run_id"] = run_id
    n = 0
    cols = ["entity_id", "name", "status", "rule", "rule_reason", "a", "b", "p_hac", "r2", "c_mean", "c_sd", "n_q",
            "n_rev_lt50", "n_cogs", "n_sga", "item_basis", "flag_spread", "flag_q4", "q4_years", "q4_measure", "run_id"]
    if write:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT entity_id FROM `{T_RULE}`")["entity_id"].tolist()
        if old:
            db.delete_fd(T_RULE, {"entity_id": old})
        n = db.write_fd(res[cols], T_RULE, ["entity_id"])
    res.to_csv(OUT / "k3_op_rule.csv", index=False, encoding="utf-8-sig")
    q = it.pivot_table(index=["entity_id", "period_end"], columns="item", values="value").reset_index()
    q = q.merge(adj[["entity_id", "period_end", "revenue_adj"]], on=["entity_id", "period_end"], how="left")
    q.to_csv(OUT / "k3_quarterly_inputs.csv", index=False, encoding="utf-8-sig")
    r1 = res[res["rule"] == "R1"]
    s = {"run_id": run_id, "rows_written": n, "firms": len(res),
         "rule": res["rule"].value_counts().to_dict(),
         "rule_x_status": {f"{a}/{b}": int(v) for (a, b), v in res.groupby(["rule", "status"]).size().items()},
         "r4_reasons": res.loc[res["rule"] == "R4", "rule_reason"].tolist(),
         "r1_b": r1["b"].describe().round(4).to_dict(), "r1_r2": r1["r2"].describe().round(3).to_dict(),
         "flag_spread": int(res["flag_spread"].sum()), "flag_q4": int(res["flag_q4"].sum()),
         "flag_spread_by_rule": res.groupby("rule")["flag_spread"].sum().to_dict(),
         "flag_q4_by_rule": res.groupby("rule")["flag_q4"].sum().to_dict(),
         "loss_firms": int((res["n_loss_q"] > 0).sum()), "item_basis": res["item_basis"].value_counts().to_dict(),
         "identity_gap_gt1pp": int((res["identity_gap_max_pp"] > 1).sum())}
    (OUT / "k3_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return res


if __name__ == "__main__":
    main()
