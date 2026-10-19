# -*- coding: utf-8 -*-
"""2g K8 6: 드라이버 자동 후보 (승인 없음, 예측에 쓰지 않음). DB 쓰기 없음 (CSV·엑셀 만).

2d K2·K2b 와 같은 방식 (선정 구간 2019Q1~2023Q4 만 사용):
- 후보: DART '주요 제품' 표 키워드(K2b 보강 사전, 띄어쓰기 무시) -> 없으면 kr_company_hs_map -> 없으면 KSIC 표,
  + 내수 후보 (KSIC), + K2b 수동 후보·제외 규칙. (k2b_reselect.build)
- 2g 추가: 반도체 장비·부품 기업은 8486 계열 6자리 848620, 848630, 848640, 848690 을 반드시 후보에 넣는다 (source semi8486).
  반도체 장비·부품 기업 = KSIC 앞자리 292 (특수목적용 기계) 또는 2612 (반도체 제조) 이거나,
  DART 주요 제품 행에 '반도체' 와 함께 '장비'·'부품'·'부분품'·'소모품' 이 들어 있는 기업.
- 상관: 분기 매출 YoY (fd_rev_adj_2g) 와 후보 YoY. corr_full (2019Q1~2023Q4), corr_excovid (2020~2021 제외), corr_lead1.
- 위약: k2_candidates.placebo (무관 수출 코드를 수출 후보 수만큼 무작위, 200회, 기업별 seed [0, 종목코드]).
- 등급
  A: 최고 후보(corr_full 최대)의 corr_full >= 0.3, corr_excovid >= 0.3, 위약 대비 초과(최고 corr_full - 위약 최고상관 평균) >= 0.3
  B: corr_full·corr_excovid 자격만 충족 (초과 0.3 미만)
  C: 그 밖 / 후보 없음
- 공개 출력이 아닌 내부 엑셀 (HS 코드 포함). 레이더 출력에는 넣지 않는다.
python -m DATA.fdriver.eval2g.k8_drivers
"""
import json
import logging
import time

import numpy as np
import pandas as pd

from ..config import output_path
from ..eval2d import k2_candidates as K
from ..eval2d import k2b_reselect as B
from ..eval2d.k1_survey import SEL
from . import common as C

OUT = output_path("2g", "K8", "drivers")
log = logging.getLogger(__name__)
SEMI_CODES = ["848620", "848630", "848640", "848690"]
SEMI_KSIC = ("292", "2612")
N_PLACEBO = 200
CORR_MIN = 0.30
EXCESS_MIN = 0.30


def semi_firms(firms: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    k = firms.set_index("ticker")["ksic"].astype(str)
    by_ksic = {t for t, v in k.items() if v.startswith(SEMI_KSIC)}
    lab = rows.assign(l=rows["label"].astype(str).str.replace(r"\s+", "", regex=True))
    hit = lab[lab["l"].str.contains("반도체") & lab["l"].str.contains("장비|부품|부분품|소모품")]
    by_prod = set(hit["ticker"]) & set(firms["ticker"])
    out = pd.DataFrame({"ticker": sorted(by_ksic | by_prod)})
    out["why"] = ["KSIC" if t in by_ksic else "" for t in out["ticker"]]
    out["why"] = [(w + ("+제품" if t in by_prod else "")).strip("+") for t, w in zip(out["ticker"], out["why"])]
    return out


def rev_yoy(ids) -> pd.DataFrame:
    a = C.read_adj(ids)
    w = a.pivot_table(index="period_end", columns="entity_id", values="revenue_adj")
    w = w[(w.index >= "2018-01-01") & (w.index <= K.SEL_END)]
    return (w / w.shift(4) - 1).replace([np.inf, -np.inf], np.nan)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t0 = time.time()
    tg = C.read_targets()
    firms = tg.rename(columns={"entity_id": "ticker"})[["ticker", "name", "ksic", "industry_group", "group_2g",
                                                         "coverage_class", "flag_jump", "flag_basis"]].copy()
    rows = pd.read_csv(OUT / "k8_products.csv", dtype={"ticker": str})
    pstatus = pd.read_csv(OUT / "k8_products_status.csv", dtype={"ticker": str})
    inv = K.trade_inventory()
    names = K.hs_names()
    manual, _ = B.manual_candidates(inv)
    cands, excluded = B.build(firms, rows, pstatus, inv, names, manual)
    semi = semi_firms(firms, rows)
    have = [c for c in SEMI_CODES if c in inv.index]
    add = pd.DataFrame([{"ticker": t, "code": c, "source": "semi8486", "kw": "반도체 장비·부품 (" + w + ")",
                         "product_share": np.nan, "direct6": False} for t, w in zip(semi["ticker"], semi["why"])
                        for c in have])
    if len(add):
        cands = pd.concat([cands, add], ignore_index=True).drop_duplicates(["ticker", "code"], keep="first")
    codes = sorted(set(cands.loc[cands["source"] != "domestic", "code"]))
    xq = K.export_yoy(codes)
    dom = K.domestic_series(sorted(set(cands.loc[cands["source"] == "domestic", "code"])))
    rev = rev_yoy(firms["ticker"].tolist())
    corr = K.correlations(cands, rev, xq, dom)
    xq_all = K.export_yoy(inv.index.tolist())
    macro = K.domestic_series(["bok:수출금액지수", "bok:전산업생산지수"])
    pl = K.placebo(firms[["ticker"]], corr, rev, xq_all, macro, per_firm_seed=True, n_reps=N_PLACEBO)
    c2, f = B.qualify(corr, firms.merge(pl, on="ticker", how="left"))
    f["cond_full_strict"] = f["best_corr"] >= CORR_MIN
    f["grade"] = np.where(f["cond_full_strict"] & f["cond_excovid"] & (f["excess_over_placebo"] >= EXCESS_MIN), "A",
                          np.where(f["cond_full_strict"] & f["cond_excovid"], "B", "C"))
    f["semi8486"] = f["ticker"].isin(semi["ticker"])
    sb = c2[c2["source"] == "semi8486"].sort_values("corr_full", ascending=False).drop_duplicates("ticker")
    f["semi_best_code"] = f["ticker"].map(sb.set_index("ticker")["code"])
    f["semi_best_corr"] = f["ticker"].map(sb.set_index("ticker")["corr_full"])
    f["best_name"] = f["best_code"].map(lambda c: names.get(str(c), "") if pd.notna(c) else "")
    c2["name_code"] = c2["code"].map(lambda c: names.get(str(c), ""))
    cols = ["grade", "ticker", "name", "group_2g", "industry_group", "best_source", "best_code", "best_name", "best_kw",
            "best_corr", "best_corr_excovid", "best_corr_lead1", "placebo_best_mean", "excess_over_placebo",
            "placebo_pass_share", "corr_total_exports", "corr_ip", "corr_trend", "n_candidates", "semi8486",
            "semi_best_code", "semi_best_corr", "flag_jump", "flag_basis"]
    fa = f.sort_values(["grade", "excess_over_placebo"], ascending=[True, False])[cols]
    with pd.ExcelWriter(OUT / "fdriver_2g_driver_candidates_v1.xlsx") as w:
        pd.DataFrame({"안내": ["2g 드라이버 자동 후보. 승인 없음, 예측에 쓰지 않음. 내부용 (HS 코드 포함).",
                               "A: corr_full >= 0.3, corr_excovid >= 0.3, 위약 대비 초과 >= 0.3 (위약 200회)",
                               "선정 구간 2019Q1~2023Q4. 매출 fd_rev_adj_2g."]}).to_excel(w, sheet_name="안내", index=False)
        fa[fa["grade"] == "A"].round(3).to_excel(w, sheet_name="A등급", index=False)
        fa.round(3).to_excel(w, sheet_name="전체기업", index=False)
        c2.round(3).to_excel(w, sheet_name="후보전체", index=False)
        semi.to_excel(w, sheet_name="반도체_8486", index=False)
    c2.to_csv(OUT / "k8_driver_candidates_all.csv", index=False, encoding="utf-8-sig")
    fa.to_csv(OUT / "k8_driver_firms.csv", index=False, encoding="utf-8-sig")
    a = fa[fa["grade"] == "A"]
    s = {"note": "승인 없음, 예측에 쓰지 않음", "firms": len(f), "products_status": pstatus["status"].value_counts().to_dict(),
         "candidates_by_source": c2["source"].value_counts().to_dict(), "excluded": len(excluded),
         "firms_with_candidates": int((f["n_candidates"] > 0).sum()), "grade": f["grade"].value_counts().to_dict(),
         "A_by_source": a["best_source"].value_counts().to_dict(),
         "A_by_industry": a["industry_group"].value_counts().to_dict(),
         "A_by_group": a["group_2g"].value_counts().to_dict(),
         "A_stats": a[["best_corr", "best_corr_excovid", "placebo_best_mean", "excess_over_placebo"]].describe()
         .round(3).to_dict(),
         "semi": {"firms": len(semi), "codes_in_trade_table": have,
                  "codes_missing": [c for c in SEMI_CODES if c not in have],
                  "semi_A": int((f["semi8486"] & (f["grade"] == "A")).sum()),
                  "semi_A_best_is_8486": int((f["semi8486"] & (f["grade"] == "A") &
                                              f["best_code"].astype(str).str.startswith("8486")).sum()),
                  "semi_best_corr_median": float(f.loc[f["semi8486"], "semi_best_corr"].median())},
         "seconds": round(time.time() - t0)}
    (OUT / "k8_drivers_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return s


if __name__ == "__main__":
    main()
