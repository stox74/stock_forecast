# -*- coding: utf-8 -*-
"""2d 작업 4-0: K3 보완 (평가 전 고정). 쓰기는 fd_driver_map_2d, fd_op_rule (upsert) 만.

1. 리드코프(012700) 제외 (호영님 결정 2026-10-16, 대출 부문 비중으로 2절 필터 5 금융업 제외에 해당)
   - fd_driver_map_2d: status = 'excluded', note 에 '금융업 성격, 호영님 결정 2026-10-16' 덧붙임
   - fd_op_rule: 행은 두고 rule = 'excluded'
2. flag_q4 보조 기준: 선정 구간 어느 해든 4분기 영업비용률 c 가 그해 1~3분기 c 평균보다 5%p 이상 높으면 flag_q4 = 1,
   q4_measure 에 'opex_ratio' 를 덧붙이고 q4_years 에 그 해를 'YYYY(c)' 로 더한다.
3. 승인 엑셀 v6: v5 를 복사해 리드코프 상태·이유를 바꾸고 안내에 변경 내역을 적는다. fd_driver_map_2d.mapping_file 도 v6 로.
실행: python -m DATA.fdriver.eval2d.k4_k3fix
"""
import json
import shutil

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path

OUT = output_path("2d", "K4")
K2c = output_path("2d", "K2c")
K3 = output_path("2d", "K3")
V5 = "fdriver_2d_driver_mapping_v5_approved.xlsx"
V6 = "fdriver_2d_driver_mapping_v6_approved.xlsx"
EXCL = "012700"
NOTE = "금융업 성격, 호영님 결정 2026-10-16"
Q4_C_PP = 0.05


def fix_driver_map() -> int:
    m = db.read_sql("SELECT * FROM fd_driver_map_2d")
    m = m.drop(columns=["first_collected_at", "last_collected_at"], errors="ignore")
    i = m["entity_id"] == EXCL
    m.loc[i, "status"] = "excluded"
    m.loc[i, "note"] = (m.loc[i, "note"].fillna("") + " | " + NOTE).str.strip(" |").str[:500]
    m["mapping_file"] = V6
    return db.write_fd(m, "fd_driver_map_2d", ["entity_id", "code"])


def q4_opex_ratio() -> pd.DataFrame:
    q = pd.read_csv(K3 / "k3_quarterly_inputs.csv", dtype={"entity_id": str}, parse_dates=["period_end"])
    q["c"] = (q["revenue_adj"] - q["op"]) / q["revenue_adj"]
    rows = []
    for e, g in q.groupby("entity_id"):
        yrs = []
        for y, gy in g.groupby(g["period_end"].dt.year):
            c4 = gy.loc[gy["period_end"].dt.month == 12, "c"]
            c13 = gy.loc[gy["period_end"].dt.month != 12, "c"]
            if len(c4) == 1 and len(c13) == 3 and c4.iloc[0] - c13.mean() >= Q4_C_PP:
                yrs.append({"year": int(y), "c_q4": float(c4.iloc[0]), "c_q1_3": float(c13.mean())})
        rows.append({"entity_id": e, "q4c_years": ",".join(str(r["year"]) for r in yrs),
                     "q4c_detail": "; ".join(f"{r['year']}: {r['c_q4']:.3f} vs {r['c_q1_3']:.3f}" for r in yrs)})
    return pd.DataFrame(rows)


def fix_op_rule(q4c: pd.DataFrame) -> (int, pd.DataFrame):
    db.execute_fd_ddl("ALTER TABLE `fd_op_rule` MODIFY `rule` VARCHAR(10) NOT NULL")
    db.execute_fd_ddl("ALTER TABLE `fd_op_rule` MODIFY `q4_measure` VARCHAR(20) NULL")
    r = db.read_sql("SELECT * FROM fd_op_rule").drop(columns=["first_collected_at", "last_collected_at"],
                                                      errors="ignore")
    r = r.merge(q4c, on="entity_id", how="left")
    hit = r["q4c_years"].fillna("") != ""
    r["flag_q4_sga"] = r["flag_q4"]
    r.loc[hit, "flag_q4"] = 1
    r.loc[hit, "q4_measure"] = np.where(r.loc[hit, "flag_q4_sga"] == 1,
                                        r.loc[hit, "q4_measure"].fillna("") + "+opex_ratio", "opex_ratio")
    r.loc[hit, "q4_years"] = [",".join(x for x in ["" if pd.isna(a) else str(a), ",".join(f"{y}(c)" for y in b.split(","))] if x)
                              for a, b in zip(r.loc[hit, "q4_years"], r.loc[hit, "q4c_years"])]
    i = r["entity_id"] == EXCL
    r.loc[i, "rule_reason"] = ("excluded: " + NOTE + " | 이전 배정 " + r.loc[i, "rule"] + " (" +
                               r.loc[i, "rule_reason"].fillna("") + ")").str[:200]
    r.loc[i, "rule"] = "excluded"
    out = r.drop(columns=["q4c_years", "q4c_detail", "flag_q4_sga"])
    n = db.write_fd(out, "fd_op_rule", ["entity_id"])
    return n, r


def write_v6() -> None:
    src = K2c / V5
    x = pd.read_excel(src, sheet_name=None, dtype=str)
    k = x["기업"]
    i = k["종목코드"] == EXCL
    k.loc[i, "상태 (driver/ts_only/excluded)"] = "excluded"
    k.loc[i, "제안 이유"] = k.loc[i, "제안 이유"].fillna("") + " | " + NOTE
    for c in ("사용 출처", "사용 코드/계열", "사용 corr_full", "사용 corr_excovid", "사용 위약 대비 초과"):
        if c in k:
            k.loc[i, c] = ""
    g = x["안내"]
    col = g.columns[0]
    g = pd.concat([pd.DataFrame({col: [f"v6 (2026-10-16): 리드코프(012700) excluded ({NOTE}). 나머지는 v5 와 같음."]}), g],
                  ignore_index=True)
    x["안내"], x["기업"] = g, k
    with pd.ExcelWriter(OUT / V6, engine="openpyxl") as xw:
        for name, df in x.items():
            df.to_excel(xw, sheet_name=name, index=False)
    shutil.copy2(OUT / V6, K2c / V6)


def main():
    a = fix_driver_map()
    q4c = q4_opex_ratio()
    b, r = fix_op_rule(q4c)
    write_v6()
    r[["entity_id", "name", "status", "rule", "flag_q4", "q4_measure", "q4_years", "q4c_detail"]].to_csv(
        OUT / "k4_k3fix_op_rule.csv", index=False, encoding="utf-8-sig")
    s = {"driver_map_rows": a, "op_rule_rows": b,
         "flag_q4_total": int(r["flag_q4"].sum()), "flag_q4_sga_only": int(r["flag_q4_sga"].sum()),
         "flag_q4_new_by_opex_ratio": int(((r["q4c_years"].fillna("") != "") & (r["flag_q4_sga"] == 0)).sum()),
         "new": r.loc[(r["q4c_years"].fillna("") != "") & (r["flag_q4_sga"] == 0), ["name", "rule", "q4c_detail"]]
         .to_dict(orient="records")}
    (OUT / "k4_k3fix_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
