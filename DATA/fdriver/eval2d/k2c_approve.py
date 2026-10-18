# -*- coding: utf-8 -*-
"""2d 작업 2c: 대체시험 재계산 + 승인 매핑 DB 등록 (명세서 2d v3 9절 7항). 쓰기는 fd_driver_map_2d 만.

입력: 호영님 승인 엑셀 fdriver_2d_driver_mapping_v4.xlsx (Downloads -> 2d/K2c/ 에 복사해 읽음).
1. '수동후보_추가' 시트 22건을 출처 manual 로 계산 (선정 구간 2019Q1~2023Q4, 매출 fd_rev_adj_2d).
   corr_full / corr_excovid / corr_lead1 은 k2_candidates.correlations 와 같다. 수출 테이블에 선정 구간 자료가 없는
   코드는 계산하지 못하고 사유를 남긴다.
   위약: k2_candidates.placebo (무관 수출 코드를 수출 후보 수만큼 무작위 추출, 200회, 기업별 seed [0, 종목코드]) 를
   그 기업의 계산 가능한 수동 후보 집합으로 돌린다 (수출 후보 수 = 그 기업의 수동 수출 후보 수, 내수만이면 1).
   위약 대비 초과 = 후보 corr_full - 그 기업 위약 최고상관 평균.
2. 제안 '대체시험' 기업: 수동 후보 중 corr_full >= 0.3 이고 corr_excovid >= 0.3 인 후보에서 위약 대비 초과가 가장 큰
   것을 사용 Y (status driver). 없으면 '시계열만' (ts_only).
3. 승인 매핑: v4 의 Y 기업(후보 시트 '사용 Y' 1개씩) + 2번 결과. '시계열만'(ts_only) 과 'N'(excluded) 도 남긴다.
   가중치: 기업당 드라이버 1개라 1.0.
4. DB: fd_driver_map_2d (PK entity_id + code). driver 는 사용 후보 1행, ts_only/excluded 는 code '' 1행.
   price_excl_from/to: 주가 이벤트 계산에서 뺄 기간 (아이큐어 거래정지 2025-08-06 ~ 재개일 2026-09-03).
실행: python -m DATA.fdriver.eval2d.k2c_approve
"""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from . import k2_candidates as C
from .k2b_reselect import N_PLACEBO_K2B

log = logging.getLogger(__name__)
OUT = output_path("2d", "K2c")
K2b = output_path("2d", "K2b")
SRC_XLSX = Path(r"C:\Users\82108\Downloads\fdriver_2d_driver_mapping_v4.xlsx")
V4 = "fdriver_2d_driver_mapping_v4.xlsx"
V5 = "fdriver_2d_driver_mapping_v5_approved.xlsx"
T_MAP = "fd_driver_map_2d"
APPROVED = "2026-10-16"
CORR_MIN = C.CORR_MIN
HALT = {"175250": ("2025-08-06", "2026-09-03", "거래정지 (거래량 0, 2025-08-06 ~ 2026-09-02), 재개일 2026-09-03 수익률에 "
                                               "정지 기간 가격 변화가 들어가 재개일까지 제외")}

DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_MAP}` (
  entity_id VARCHAR(20) NOT NULL,
  code VARCHAR(60) NOT NULL,
  name VARCHAR(100) NULL,
  status VARCHAR(10) NOT NULL,
  source VARCHAR(20) NULL,
  corr_full DOUBLE NULL,
  corr_excovid DOUBLE NULL,
  corr_lead1 DOUBLE NULL,
  placebo_best_mean DOUBLE NULL,
  excess_over_placebo DOUBLE NULL,
  weight DOUBLE NULL,
  decision VARCHAR(20) NULL,
  note VARCHAR(500) NULL,
  coverage_class VARCHAR(10) NULL,
  flag_basis TINYINT NULL,
  flag_jump TINYINT NULL,
  price_excl_from DATE NULL,
  price_excl_to DATE NULL,
  price_excl_note VARCHAR(300) NULL,
  approved_date DATE NOT NULL,
  mapping_file VARCHAR(100) NOT NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, code)
) DEFAULT CHARSET=utf8mb4
"""


def _s(x) -> str:
    return "" if pd.isna(x) else str(x).strip()


def read_v4() -> dict:
    dst = OUT / V4
    shutil.copy2(SRC_XLSX, dst)
    x = pd.read_excel(dst, sheet_name=None, dtype=str)
    f = x["기업"].rename(columns={"종목코드": "ticker", "기업": "name", "제안 (Y/N/검토)": "decision", "제안 이유": "reason"})
    f["decision"] = f["decision"].map(_s)
    c = x["후보"].rename(columns={"종목코드": "ticker", "출처": "source", "HS 코드 / 내수 계열": "code",
                                 "corr_full (2019~2023)": "corr_full", "corr_excovid": "corr_excovid",
                                 "corr_lead1 (보고만)": "corr_lead1", "사용 (Y/N)": "use", "가중치": "weight"})
    c["use"] = c["use"].map(_s).str.upper()
    m = x["수동후보_추가"].rename(columns={"종목코드": "ticker", "HS 코드/계열": "code", "이유": "why"})
    m = m[m["ticker"].map(_s) != ""].copy()
    m["code"] = m["code"].map(_s)
    return {"firms": f, "cands": c, "manual": m}


def manual_calc(manual: pd.DataFrame, firms: pd.DataFrame):
    inv = C.trade_inventory()
    is_dom = manual["code"].str.contains(":")
    ok = manual[is_dom | manual["code"].isin(inv.index)].copy()
    bad = manual[~(is_dom | manual["code"].isin(inv.index))].copy()
    bad["reason_unavailable"] = "수출 테이블(korea_monthly_trade_data, _v2, korea_quarterly_trade_data)에 코드 없음"
    cands = pd.DataFrame({"ticker": ok["ticker"], "code": ok["code"],
                          "source": np.where(ok["code"].str.contains(":"), "domestic", "manual"),
                          "kw": ok["why"], "product_share": np.nan, "direct6": False})
    exp_codes = sorted(set(cands.loc[cands["source"] == "manual", "code"]))
    xq = C.export_yoy(exp_codes) if exp_codes else pd.DataFrame()
    dom = C.domestic_series(sorted(set(cands.loc[cands["source"] == "domestic", "code"])))
    rev = C.rev_yoy_frame(sorted(set(cands["ticker"])))
    corr = C.correlations(cands, rev, xq, dom)
    corr["source"] = "manual"       # 출처 표기는 모두 manual (내수 계열 수동 후보 포함)
    corr["series_type"] = np.where(corr["code"].str.contains(":"), "domestic", "export")
    xq_all = C.export_yoy(C.trade_inventory().index.tolist())
    macro = C.domestic_series(["bok:수출금액지수", "bok:전산업생산지수"])
    pl_in = corr.assign(source=np.where(corr["series_type"] == "domestic", "domestic", "manual"))
    pl = C.placebo(pd.DataFrame({"ticker": sorted(set(corr["ticker"]))}), pl_in, rev, xq_all, macro,
                   per_firm_seed=True, n_reps=N_PLACEBO_K2B)
    corr = corr.merge(pl[["ticker", "placebo_best_mean", "placebo_pass_share"]], on="ticker", how="left")
    corr["excess_over_placebo"] = corr["corr_full"] - corr["placebo_best_mean"]
    corr["qualified"] = (corr["corr_full"] >= CORR_MIN) & (corr["corr_excovid"] >= CORR_MIN)
    corr = corr.merge(firms[["ticker", "name", "decision"]], on="ticker", how="left")
    return corr, bad


def judge_alt(firms: pd.DataFrame, mc: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in firms[firms["decision"] == "대체시험"].itertuples():
        g = mc[(mc["ticker"] == r.ticker)]
        q = g[g["qualified"]].sort_values("excess_over_placebo", ascending=False)
        if len(q):
            b = q.iloc[0]
            rows.append({"ticker": r.ticker, "name": r.name, "result": "driver", "code": b["code"],
                         "corr_full": b["corr_full"], "corr_excovid": b["corr_excovid"], "corr_lead1": b["corr_lead1"],
                         "placebo_best_mean": b["placebo_best_mean"], "excess_over_placebo": b["excess_over_placebo"],
                         "n_tried": len(g), "tried": "; ".join(f"{c} ({cf:.3f}/{ce:.3f})" for c, cf, ce in
                                                              zip(g["code"], g["corr_full"], g["corr_excovid"]))})
        else:
            rows.append({"ticker": r.ticker, "name": r.name, "result": "ts_only", "code": "", "n_tried": len(g),
                         "tried": "; ".join(f"{c} ({cf:.3f}/{ce:.3f})" for c, cf, ce in
                                            zip(g["code"], g["corr_full"], g["corr_excovid"])) or "계산 가능한 후보 없음"})
    return pd.DataFrame(rows)


def build_map(v4: dict, alt: pd.DataFrame, run_id: str) -> pd.DataFrame:
    f, c = v4["firms"], v4["cands"]
    t2b = pd.read_csv(K2b / "k2b_firm_tiers.csv", dtype={"ticker": str})
    t2b = t2b.set_index("ticker")
    rows = []
    for r in f.itertuples():
        base = {"entity_id": r.ticker, "name": r.name, "decision": r.decision, "note": _s(r.reason)[:500],
                "coverage_class": t2b["coverage_class"].get(r.ticker), "flag_basis": t2b["flag_basis"].get(r.ticker),
                "flag_jump": t2b["flag_jump"].get(r.ticker)}
        if r.decision == "Y":
            u = c[(c["ticker"] == r.ticker) & (c["use"] == "Y")].iloc[0]
            pm = t2b["placebo_best_mean"].get(r.ticker)
            cf = float(u["corr_full"])
            rows.append({**base, "status": "driver", "source": u["source"], "code": u["code"], "corr_full": cf,
                         "corr_excovid": float(u["corr_excovid"]), "corr_lead1": float(u["corr_lead1"]),
                         "placebo_best_mean": pm, "excess_over_placebo": cf - pm, "weight": 1.0})
        elif r.decision == "대체시험":
            a = alt[alt["ticker"] == r.ticker].iloc[0]
            if a["result"] == "driver":
                rows.append({**base, "status": "driver", "source": "manual", "code": a["code"],
                             "corr_full": a["corr_full"], "corr_excovid": a["corr_excovid"],
                             "corr_lead1": a["corr_lead1"], "placebo_best_mean": a["placebo_best_mean"],
                             "excess_over_placebo": a["excess_over_placebo"], "weight": 1.0,
                             "note": (base["note"] + " | 대체시험 통과")[:500]})
            else:
                rows.append({**base, "status": "ts_only", "code": "",
                             "note": (base["note"] + " | 대체시험 미달 -> 시계열만")[:500]})
        elif r.decision == "시계열만":
            rows.append({**base, "status": "ts_only", "code": ""})
        else:
            rows.append({**base, "status": "excluded", "code": ""})
    m = pd.DataFrame(rows)
    m["price_excl_from"] = m["entity_id"].map(lambda t: HALT.get(t, (None,))[0])
    m["price_excl_to"] = m["entity_id"].map(lambda t: HALT.get(t, (None, None))[1])
    m["price_excl_note"] = m["entity_id"].map(lambda t: HALT.get(t, (None, None, None))[2])
    m["approved_date"] = APPROVED
    m["mapping_file"] = V5
    m["run_id"] = run_id
    return m


def write_v5(v4: dict, m: pd.DataFrame, alt: pd.DataFrame, mc: pd.DataFrame, bad: pd.DataFrame) -> None:
    src = OUT / V4
    x = pd.read_excel(src, sheet_name=None, dtype=str)
    st = m.set_index("entity_id")
    k = x["기업"].copy()
    k["상태 (driver/ts_only/excluded)"] = k["종목코드"].map(st["status"])
    k["사용 출처"] = k["종목코드"].map(st["source"])
    k["사용 코드/계열"] = k["종목코드"].map(st["code"])
    k["사용 corr_full"] = k["종목코드"].map(st["corr_full"]).astype(float).round(3)
    k["사용 corr_excovid"] = k["종목코드"].map(st["corr_excovid"]).astype(float).round(3)
    k["사용 위약 대비 초과"] = k["종목코드"].map(st["excess_over_placebo"]).astype(float).round(3)
    c = x["후보"].copy()
    # 대체시험 통과 기업의 기존 후보 '사용' 은 N 으로, 수동 후보 행을 추가
    alt_ok = alt[alt["result"] == "driver"]
    c.loc[c["종목코드"].isin(alt["ticker"]), "사용 (Y/N)"] = "N"
    add = mc[mc["decision"] == "대체시험"].copy()
    add_rows = pd.DataFrame({"종목코드": add["ticker"], "기업": add["name"], "출처": "manual",
                             "HS 코드 / 내수 계열": add["code"], "품목 (참고)": add["kw"],
                             "corr_full (2019~2023)": add["corr_full"].round(3), "n": add["n_full"],
                             "corr_excovid": add["corr_excovid"].round(3),
                             "corr_lead1 (보고만)": add["corr_lead1"].round(3),
                             "사용 (Y/N)": ["Y" if ((alt_ok["ticker"] == t) & (alt_ok["code"] == cd)).any() else "N"
                                          for t, cd in zip(add["ticker"], add["code"])], "가중치": ""})
    add_rows.loc[add_rows["사용 (Y/N)"] == "Y", "가중치"] = "1"
    c.loc[c["사용 (Y/N)"].map(_s).str.upper() == "Y", "가중치"] = "1"
    c = pd.concat([c, add_rows], ignore_index=True)
    man = mc.copy()
    man_sheet = pd.DataFrame({"종목코드": man["ticker"], "기업": man["name"], "제안": man["decision"],
                              "코드/계열": man["code"], "이유": man["kw"], "corr_full": man["corr_full"].round(3),
                              "corr_excovid": man["corr_excovid"].round(3), "corr_lead1": man["corr_lead1"].round(3),
                              "위약 최고상관 평균": man["placebo_best_mean"].round(3),
                              "위약 대비 초과": man["excess_over_placebo"].round(3),
                              "자격 (0.3/0.3)": np.where(man["qualified"], "Y", "")})
    bad_sheet = pd.DataFrame({"종목코드": bad["ticker"], "코드": bad["code"], "이유": bad["why"],
                              "계산 못 한 이유": bad["reason_unavailable"]})
    guide = pd.DataFrame({"fdriver 2d 드라이버 매핑 승인본 v5": [
        f"승인일 {APPROVED}. v4 (호영님 제안) + 대체시험 재계산 결과 (2d 작업 2c). 명세서 2d v3 9절 7항.",
        "기업 시트 '상태': driver (드라이버 사용), ts_only (시계열만), excluded (N).",
        "대체시험: 수동 후보 중 corr_full >= 0.3, corr_excovid >= 0.3 인 후보에서 위약 대비 초과가 가장 큰 것을 사용. 없으면 시계열만.",
        f"위약: 무관 수출 코드를 기업 수동 수출 후보 수만큼 무작위 추출 {N_PLACEBO_K2B}회, 기업별 seed [0, 종목코드].",
        "DB fd_driver_map_2d 와 같은 내용입니다. 이후 단계는 DB 테이블만 읽습니다.",
        "이 파일의 HS 코드·드라이버명·가중치는 어떤 공개물에도 넣지 않습니다 (7절).",
    ]})
    with pd.ExcelWriter(OUT / V5, engine="openpyxl") as xw:
        guide.to_excel(xw, sheet_name="안내", index=False)
        k.to_excel(xw, sheet_name="기업", index=False)
        c.to_excel(xw, sheet_name="후보", index=False)
        man_sheet.to_excel(xw, sheet_name="수동후보_결과", index=False)
        bad_sheet.to_excel(xw, sheet_name="수동후보_자료없음", index=False)
        for s in ("미연결_품목", "대기", "기업사건"):
            x[s].to_excel(xw, sheet_name=s, index=False)


def main(write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    v4 = read_v4()
    mc, bad = manual_calc(v4["manual"], v4["firms"])
    alt = judge_alt(v4["firms"], mc)
    m = build_map(v4, alt, run_id)
    write_v5(v4, m, alt, mc, bad)
    n = 0
    if write:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{T_MAP}`")["entity_id"].tolist()
        if old:
            db.delete_fd(T_MAP, {"entity_id": old})
        n = db.write_fd(m, T_MAP, ["entity_id", "code"])
    mc.to_csv(OUT / "k2c_manual_results.csv", index=False, encoding="utf-8-sig")
    bad.to_csv(OUT / "k2c_manual_unavailable.csv", index=False, encoding="utf-8-sig")
    alt.to_csv(OUT / "k2c_alt_test.csv", index=False, encoding="utf-8-sig")
    m.to_csv(OUT / "k2c_driver_map.csv", index=False, encoding="utf-8-sig")
    s = {"run_id": run_id, "rows_written": n, "status": m["status"].value_counts().to_dict(),
         "decision_x_status": {f"{a}->{b}": int(v) for (a, b), v in m.groupby(["decision", "status"]).size().items()},
         "alt": alt["result"].value_counts().to_dict(), "manual_calc": len(mc), "manual_unavailable": len(bad),
         "driver_by_source": m[m["status"] == "driver"]["source"].value_counts().to_dict()}
    (OUT / "k2c_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return m, alt, mc, bad


if __name__ == "__main__":
    main()
