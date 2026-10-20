# -*- coding: utf-8 -*-
"""2g K9 1: 참고용 드라이버 자동 선정 (method_2g_ref_v1 1절, 커밋 72209e4). 승인 아님.
쓰기: fd_driver_ref_2g (2g 작업 표, 실행마다 전체 교체) 와 CSV·엑셀 (2g/K9/drivers).

대상: fd_target_2g 중 M1 이 없는 기업 (fd_live_2g 최신 기록에 m1_source 가 있는 레이더 기업 제외).
후보·상관·위약은 k8_drivers 와 같은 코드 (2d K2·K2b 방식, 반도체 8486 강제 포함, 위약 200회, 기업별 시드).
등급: A = full>=0.3, excovid>=0.3, 초과>=0.3 / B = full>=0.3, excovid>=0.3, 0.2<=초과<0.3 / none.
drv_ksic_only: 최고 후보 출처가 ksic, 또는 semi8486 이면서 반도체 판단 근거가 KSIC 뿐.
python -m DATA.fdriver.eval2g.k9_drivers
"""
import json
import logging
import time
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..eval2d import k2_candidates as K
from ..eval2d import k2b_reselect as B
from . import common as C
from . import k8_drivers as D

OUT = output_path("2g", "K9", "drivers")
PRODUCTS = output_path("2g", "K8", "drivers")
T_REF = "fd_driver_ref_2g"
CORR_MIN, EXCESS_A, EXCESS_B = 0.30, 0.30, 0.20
DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_REF}` (
  entity_id VARCHAR(20) NOT NULL,
  name VARCHAR(100) NULL,
  group_2g VARCHAR(10) NULL,
  code VARCHAR(80) NULL,
  source VARCHAR(20) NULL,
  drv_basis VARCHAR(40) NULL,
  grade VARCHAR(4) NOT NULL,
  corr_full DOUBLE NULL,
  corr_excovid DOUBLE NULL,
  placebo_best_mean DOUBLE NULL,
  excess_over_placebo DOUBLE NULL,
  ksic_only TINYINT NULL,
  n_candidates INT NULL,
  method_ref_commit CHAR(40) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id)
) DEFAULT CHARSET=utf8mb4
"""
METHOD_REF_COMMIT = "72209e4ee3c4dc9bf0a0e7a5b3ecd56ab9f31eb4"
BASIS = {"dart": "DART 주요 제품", "hs_map": "기업-HS 매핑", "ksic": "KSIC 대응 수출", "domestic": "KSIC 대응 내수",
         "manual": "수동 후보", "semi8486": "반도체 8486"}


def m1_firms() -> set:
    d = db.read_sql(f"SELECT entity_id, m1_source, recorded_at FROM `{C.T_LIVE}` WHERE horizon = 0")
    d = d.sort_values("recorded_at").drop_duplicates("entity_id", keep="last")
    return set(d.loc[d["m1_source"].notna(), "entity_id"])


def main(write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t0 = time.time()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tg = C.read_targets()
    m1 = m1_firms()
    tg = tg[~tg["entity_id"].isin(m1)]
    firms = tg.rename(columns={"entity_id": "ticker"})[["ticker", "name", "ksic", "industry_group", "group_2g",
                                                         "coverage_class", "flag_jump", "flag_basis"]].copy()
    rows = pd.read_csv(PRODUCTS / "k8_products.csv", dtype={"ticker": str})
    pstatus = pd.read_csv(PRODUCTS / "k8_products_status.csv", dtype={"ticker": str})
    missing = sorted(set(firms["ticker"]) - set(pstatus["ticker"]))
    if missing:
        raise RuntimeError(f"주요 제품 표 미처리 기업 {len(missing)}곳 (k8_products 를 먼저 실행)")
    inv = K.trade_inventory()
    names = K.hs_names()
    manual, _ = B.manual_candidates(inv)
    cands, excluded = B.build(firms, rows, pstatus, inv, names, manual)
    semi = D.semi_firms(firms, rows)
    have = [c for c in D.SEMI_CODES if c in inv.index]
    add = pd.DataFrame([{"ticker": t, "code": c, "source": "semi8486", "kw": "반도체 장비·부품 (" + w + ")",
                         "product_share": np.nan, "direct6": False} for t, w in zip(semi["ticker"], semi["why"])
                        for c in have])
    if len(add):
        cands = pd.concat([cands, add], ignore_index=True).drop_duplicates(["ticker", "code"], keep="first")
    xq = K.export_yoy(sorted(set(cands.loc[cands["source"] != "domestic", "code"])))
    dom = K.domestic_series(sorted(set(cands.loc[cands["source"] == "domestic", "code"])))
    rev = D.rev_yoy(firms["ticker"].tolist())
    corr = K.correlations(cands, rev, xq, dom)
    xq_all = K.export_yoy(inv.index.tolist())
    macro = K.domestic_series(["bok:수출금액지수", "bok:전산업생산지수"])
    pl = K.placebo(firms[["ticker"]], corr, rev, xq_all, macro, per_firm_seed=True, n_reps=D.N_PLACEBO)
    c2, f = B.qualify(corr, firms.merge(pl, on="ticker", how="left"))
    q = (f["best_corr"] >= CORR_MIN) & (f["best_corr_excovid"] >= CORR_MIN)
    f["grade"] = np.where(q & (f["excess_over_placebo"] >= EXCESS_A), "A",
                          np.where(q & (f["excess_over_placebo"] >= EXCESS_B), "B", "none"))
    why = semi.set_index("ticker")["why"]
    f["ksic_only"] = ((f["best_source"] == "ksic") |
                      ((f["best_source"] == "semi8486") & (f["ticker"].map(why) == "KSIC"))).astype(int)
    f["drv_basis"] = f["best_source"].map(BASIS)
    out = pd.DataFrame({"entity_id": f["ticker"], "name": f["name"], "group_2g": f["group_2g"],
                        "code": f["best_code"], "source": f["best_source"], "drv_basis": f["drv_basis"],
                        "grade": f["grade"], "corr_full": f["best_corr"], "corr_excovid": f["best_corr_excovid"],
                        "placebo_best_mean": f["placebo_best_mean"], "excess_over_placebo": f["excess_over_placebo"],
                        "ksic_only": f["ksic_only"], "n_candidates": f["n_candidates"],
                        "method_ref_commit": METHOD_REF_COMMIT, "run_id": run_id})
    n = 0
    if write:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT entity_id FROM `{T_REF}`")["entity_id"].tolist()
        stale = sorted(set(old) - set(out["entity_id"]))
        if stale:
            db.delete_fd(T_REF, {"entity_id": stale})
        n = db.write_fd(out, T_REF, ["entity_id"])
    out.to_csv(OUT / "k9_driver_ref.csv", index=False, encoding="utf-8-sig")
    c2["name_code"] = c2["code"].map(lambda c: names.get(str(c), ""))
    c2.to_csv(OUT / "k9_driver_candidates_all.csv", index=False, encoding="utf-8-sig")
    ab = out[out["grade"].isin(["A", "B"])]
    with pd.ExcelWriter(OUT / "fdriver_2g_driver_ref_v1.xlsx") as w:
        pd.DataFrame({"안내": ["2g 참고용 드라이버 (승인 아님). method_2g_ref_v1 커밋 72209e4.",
                               "A: full>=0.3, excovid>=0.3, 위약 대비 초과>=0.3 / B: 초과 0.2~0.3 / none: 그 외",
                               "내부용 (HS 코드 포함). 공개 출력에는 넣지 않는다."]}).to_excel(w, sheet_name="안내", index=False)
        ab.assign(code_name=ab["code"].map(lambda c: names.get(str(c), ""))).round(3).to_excel(
            w, sheet_name="A_B등급", index=False)
        out.round(3).to_excel(w, sheet_name="전체", index=False)
        semi.to_excel(w, sheet_name="반도체_8486", index=False)
    s = {"run_id": run_id, "rows_written": n, "firms": len(out), "excluded_m1_firms": len(m1),
         "grade": out["grade"].value_counts().to_dict(),
         "grade_by_group": {f"{a}/{b}": int(v) for (a, b), v in out.groupby(["group_2g", "grade"]).size().items()},
         "AB_by_source": ab["source"].value_counts().to_dict(),
         "AB_ksic_only": int(ab["ksic_only"].sum()),
         "AB_by_grade_source": {f"{a}/{b}": int(v) for (a, b), v in ab.groupby(["grade", "source"]).size().items()},
         "semi": {"firms": len(semi), "AB": int(out["entity_id"].isin(semi["ticker"]).mul(out["grade"] != "none").sum()),
                  "AB_best_8486": int((ab["source"] == "semi8486").sum())},
         "products_status_all": pstatus[pstatus["ticker"].isin(firms["ticker"])]["status"].value_counts().to_dict(),
         "seconds": round(time.time() - t0)}
    (OUT / "k9_drivers_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return s


if __name__ == "__main__":
    main()
