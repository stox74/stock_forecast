# -*- coding: utf-8 -*-
"""2g 레이더 출력 v4 (K8c: 매출 단절 표시). v3 (K9 5: 참고 열 추가). v2 (K8b 4): fd_live_2g 의 기업별 가장 최근 스냅샷(기업 x 분기 Q, Q+1)에
2d 확대 73곳을 읽기 전용으로 합친 통합본. DB 쓰기 없음. 금액 단위 억원.
- 2d 행: fd_live_2d 의 기업별 최신 method_version 'v2' 스냅샷 값을 그대로 옮긴다 (다시 계산하지 않음, Q 한 분기만).
  2d 에 없는 열(OP_hat_alt, 컨센서스, 2g 표시 열)은 비워 둔다.
- 비교 기준 (cmp_basis, cmp_gap_pct)
  2g: 컨센서스가 있으면 E5 / 컨센서스 - 1, 없으면 gap_E3_vs_B2 (SARIMA·ETS·Theta 기하평균 대 B2)
  2d: gap_E4_vs_B2 (2d 사전 등록 v2 신호)
- 판단 시트: 집단 순서대로, 그 안에서 |cmp_gap_pct| 큰 순. 모든 행에 '매매 신호 아님(검증 전)'.
- 공개 출력이라 드라이버 코드·이름(drv_code, m1_source)과 HS 코드는 넣지 않는다.

python -m DATA.fdriver.eval2g.radar_2g [--quarter 2026Q3]
v3 참고 열 (method_2g_ref_v1): M1_ref, SARIMAX_ref, E5_ref, E6_ref, drv_grade, drv_source, drv_ksic_only, x_src, ref_note.
  드라이버 코드·이름은 넣지 않는다 (출처 종류만).
v4 (method_2g_v1 변경 기록 2026-10-20): flag_struct_break, struct_mode, flag_q4_calc, rev_fixed_n, flag_cons_gap40, data_note.
  자료 단절(flag_struct_break=1) 행은 판단 시트에서 E5 를 컨센서스가 있을 때만 보이고(비교용), 없으면 비운다.
출력: OneDrive/INVESTMENT/한국주식/fdriver/2g/K8c/radar/ 와 2g/radar/<분기>/
      radar2g_v4_<분기>_<생성일시>.csv (전체), _decision.csv (판단), .xlsx (판단 | 전체 | 변수설명), .html, _columns.csv
"""
import argparse
import html
import shutil
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from . import common as C

VERSION = "radar2g_v4"
COPY_TO = ("2g", "K8c", "radar")
EOK = 1e8
SIGNAL = "매매 신호 아님(검증 전)"
GROUP_ORDER = ["레이더39", "정상", "저커버리지", "컨센서스 없음", "2d 확대"]

COLDESC = {
    "entity_id": "종목코드",
    "name": "기업명",
    "group_2g": "집단: 레이더39 / 정상 / 저커버리지 / 컨센서스 없음 (2g) / 2d 확대 (2d 기록, 읽기 전용)",
    "source": "값의 출처: 2g (fd_live_2g) / 2d 읽기 전용 (fd_live_2d v2)",
    "market": "시장",
    "industry_group": "업종 묶음 (K1)",
    "quarter": "예측 분기",
    "horizon": "0 = 기준일에 끝난 마지막 분기, 1 = 그 다음 분기",
    "asof_date": "기준일 (이 날까지 공표된 자료만 사용)",
    "announced": "기준일에 이 분기 실적을 이미 발표했으면 1",
    "rule": "영업이익 규칙 (R1 회귀, R2 원가율·판관비, R3 영업비용률, R4 예측 안 함)",
    "cmp_basis": "비교 기준: 컨센서스 / B2 (E3 신호) / B2 (2d E4 신호)",
    "cmp_gap_pct": "비교 기준 대비 차이 (%): 컨센서스면 E5 / 컨센서스 - 1, B2 면 E3(2d 는 E4) / B2 - 1",
    "rev_base": "전년 동기 매출 (억원)",
    "E5": "주 매출 예측 E5 (억원): SARIMA·ETS·Theta·B2 (+M1) 기하평균",
    "E5_yoy_pct": "E5 의 전년 동기 대비 성장률 (%)",
    "cons": "매출 컨센서스 (억원, 기준일 이하 최신 관측)",
    "cons_asof": "컨센서스 관측일",
    "gap_E5_vs_cons_pct": "E5 / 컨센서스 - 1 (%)",
    "E3": "E3 매출 (억원): SARIMA·ETS·Theta 기하평균 (B2·M1 을 섞지 않음)",
    "gap_E3_vs_B2_pct": "E3 / B2 - 1 (%)",
    "gap_E4_vs_B2_pct": "2d 신호: E4 / B2 - 1 (%) (2d 행만)",
    "SARIMA": "SARIMA 매출 예측 (억원)",
    "ETS": "ETS 매출 예측 (억원)",
    "Theta": "Theta 매출 예측 (억원)",
    "M1": "드라이버 모형 매출 예측 (억원, 기존 레이더 드라이버 기업의 첫 분기, 2d driver 기업)",
    "B2": "B2 매출 예측 (억원): 최근 분기 성장률 유지",
    "e5_n": "E5 에 들어간 모형 수",
    "OP_hat_E5": "주 영업이익 예측 (억원): 규칙에 매출 E5 를 넣은 값",
    "OP_hat_alt": "대안 영업이익 예측 (억원, 2g 만): R1 최근 12분기 회귀, R2 판관비 이상값 대체 후 예측, R3 주 예측과 같음",
    "OP_naive": "단순 영업이익 예측 (억원): B2 매출 x 전년 동기 영업이익률",
    "margin_E5_pct": "OP_hat_E5 / E5 (%)",
    "margin_alt_pct": "OP_hat_alt / E5 (%)",
    "margin_naive_pct": "OP_naive / B2 (%)",
    "drv_grade": "참고 드라이버 등급 (A / B, 승인 아님, method_2g_ref_v1)",
    "drv_source": "참고 드라이버 출처 종류 (dart / hs_map / ksic / domestic / semi8486)",
    "drv_ksic_only": "1: 참고 드라이버 근거가 업종코드뿐 (경제적 타당성 미확인)",
    "M1_ref": "참고: 드라이버 OLS 매출 예측 (억원, 공표된 달만)",
    "SARIMAX_ref": "참고: 드라이버를 외생 변수로 넣은 SARIMAX 매출 예측 (억원)",
    "E5_ref": "참고: SARIMA·ETS·Theta·B2·M1_ref 기하평균 (억원)",
    "E6_ref": "참고: E5_ref 구성 + SARIMAX_ref 기하평균 (억원)",
    "x_src": "참고: 예측 분기 드라이버 값 출처 (known / known+fill / stored_fc / fill / last30d / known_mean)",
    "ref_note": "참고 예측이 비었을 때 이유 (drv_data_end: 드라이버 자료가 끝난 달)",
    "data_note": "자료 상태: 자료 단절(사건) / 사건 후 구간만 사용 / 4분기 산출 의심 / 4분기 고침",
    "flag_struct_break": "1: 기업 사건(분할·합병 등) 뒤 매출 기준이 바뀌었고 새 기준 자료가 12분기 미만 (E5 비교용)",
    "struct_mode": "사건 확인 기업의 처리: post_only (사건 뒤 구간만으로 예측) / compare_only (자료 단절)",
    "flag_q4_calc": "1: 4분기 매출 산출 이상 의심 (사건 없음, 원천으로 고치지 못함)",
    "rev_fixed_n": "같은 기준 원천으로 고친 4분기 수",
    "flag_cons_gap40": "1: |E5 / 컨센서스 - 1| > 40% (업종과 함께 볼 것: 수주 산업 등)",
    "semi_equip": "1: 반도체 장비·부품 기업 (KSIC 292·2612 또는 주요 제품에 반도체+장비·부품, K9 와 같은 기준). 수주 영향이 큰 업종",
    "flag_b2_jump": "1: B2 성장률이 세 시계열 모형 중앙값과 20%p 넘게 다름 (2g 만)",
    "flag_m1_dir": "1: 드라이버 모형 방향이 나머지 네 모형 모두와 반대 (2g 만)",
    "flag_op_sign": "1: 세 영업이익 예측의 부호가 서로 다름 (2g 만)",
    "sga_fixed_n": "판관비를 항등식으로 고친 분기 수 (2g DART 기준 기업)",
    "flag_jump": "1: 매출이 전년 대비 2배 초과·절반 미만인 분기가 4개 이상 (K1)",
    "flag_basis": "1: 보정 기준과 DART 가 선정 구간에 계속 다름",
    "flag_spread": "1: 원가 연동 업종 (정유·기초화학·합성수지·화학섬유·철강·비철)",
    "flag_q4": "1: 4분기 비용이 몰리는 기업 (K4b 기준)",
    "filter_fail": "레이더39 중 2d 필터를 통과하지 못한 항목",
    "signal": "매매 신호 여부",
    "run_id": "스냅샷 실행 번호",
}
JUDGE = ["signal", "data_note", "group_2g", "entity_id", "name", "industry_group", "quarter", "announced", "cmp_basis",
         "cmp_gap_pct", "flag_cons_gap40", "semi_equip",
         "E5", "cons", "B2", "E3", "E5_yoy_pct", "OP_hat_E5", "OP_hat_alt", "OP_naive", "margin_E5_pct", "rule",
         "flag_b2_jump", "flag_m1_dir", "flag_op_sign", "sga_fixed_n", "flag_jump", "flag_q4", "flag_struct_break",
         "flag_q4_calc", "drv_grade", "drv_ksic_only",
         "M1_ref", "SARIMAX_ref", "E5_ref", "E6_ref", "x_src", "source"]


def _latest(d: pd.DataFrame) -> pd.DataFrame:
    d["recorded_at"] = pd.to_datetime(d["recorded_at"])
    last = d.sort_values("recorded_at").drop_duplicates("entity_id", keep="last")[["entity_id", "run_id"]]
    return d.merge(last, on=["entity_id", "run_id"])


def latest_2g(quarter: str = None) -> pd.DataFrame:
    d = db.read_sql(f"SELECT * FROM `{C.T_LIVE}`")
    if d.empty:
        raise RuntimeError("fd_live_2g 기록 없음")
    d = _latest(d)
    if quarter:
        q0 = d[d["horizon"] == 0]["quarter"].mode().iloc[0]
        if q0 != quarter:
            raise RuntimeError(f"최근 스냅샷 첫 분기 {q0} != {quarter}")
    return d


def latest_2d(quarter: str) -> pd.DataFrame:
    d = db.read_sql("SELECT * FROM fd_live_2d WHERE quarter = :q AND method_version = 'v2'", {"q": quarter})
    return _latest(d) if len(d) else d


def table_2g(d: pd.DataFrame) -> pd.DataFrame:
    tg = C.read_targets().set_index("entity_id")
    num = lambda c: pd.to_numeric(d[c], errors="coerce") if c in d else pd.Series(np.nan, index=d.index)
    o = pd.DataFrame({"entity_id": d["entity_id"], "name": d["entity_id"].map(tg["name"])})
    for c in ("group_2g", "market", "industry_group"):
        o[c] = d["entity_id"].map(tg[c])
    o["source"] = "2g"
    o["quarter"], o["horizon"], o["asof_date"] = d["quarter"], d["horizon"], d["asof_date"]
    o["announced"] = num("announced_at_snapshot").fillna(0).astype(int)
    o["rule"] = d["rule"]
    for c in ("rev_base", "E5", "cons", "E3", "SARIMA", "ETS", "Theta", "M1", "B2", "OP_hat_E5", "OP_hat_alt",
              "OP_naive"):
        o[c] = num(c) / EOK
    o["cons_asof"] = d["cons_asof"]
    o["gap_E3_vs_B2_pct"] = (np.exp(num("gap_E3_vs_B2")) - 1) * 100
    o["gap_E4_vs_B2_pct"] = np.nan
    o["e5_n"] = num("e5_n")
    for c in ("flag_b2_jump", "flag_m1_dir", "flag_op_sign", "sga_fixed_n", "flag_jump", "flag_basis", "flag_spread",
              "flag_q4"):
        o[c] = num(c)
    o["filter_fail"] = d["entity_id"].map(tg["filter_fail"]).fillna("")
    for c in ("M1_ref", "SARIMAX_ref", "E5_ref", "E6_ref"):
        o[c] = num(c) / EOK
    o["drv_grade"] = d["drv_grade"] if "drv_grade" in d else None
    o["drv_source"] = d["drv_source_ref"] if "drv_source_ref" in d else None
    o["drv_ksic_only"] = num("drv_ksic_only")
    o["x_src"] = d["x_src"] if "x_src" in d else None
    o["ref_note"] = d["ref_note"] if "ref_note" in d else None
    for c in ("flag_struct_break", "flag_q4_calc", "rev_fixed_n", "flag_cons_gap40"):
        o[c] = num(c)
    o["struct_mode"] = d["struct_mode"] if "struct_mode" in d else None
    o["run_id"] = d["run_id"]
    return o


def table_2d(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str}).set_index("ticker")
    nm = db.read_sql("SELECT entity_id, name FROM fd_driver_map_2d").drop_duplicates("entity_id").set_index(
        "entity_id")["name"]
    num = lambda c: pd.to_numeric(d[c], errors="coerce")
    o = pd.DataFrame({"entity_id": d["entity_id"], "name": d["entity_id"].map(nm)})
    o["group_2g"], o["source"] = "2d 확대", "2d 읽기 전용"
    o["market"] = d["entity_id"].map(k1["market"])
    o["industry_group"] = d["entity_id"].map(k1["industry_group"])
    o["quarter"], o["horizon"], o["asof_date"] = d["quarter"], 0, d["asof_date"]
    o["announced"] = num("announced_at_snapshot").fillna(0).astype(int)
    o["rule"] = d["rule"]
    for c in ("rev_base", "E5", "SARIMA", "ETS", "Theta", "M1", "B2", "OP_hat_E5", "OP_naive"):
        o[c] = num(c) / EOK
    for c in ("cons", "E3", "OP_hat_alt", "gap_E3_vs_B2_pct", "flag_b2_jump", "flag_m1_dir", "flag_op_sign",
              "sga_fixed_n"):
        o[c] = np.nan
    o["cons_asof"] = None
    o["gap_E4_vs_B2_pct"] = (np.exp(num("gap_E4_vs_B2")) - 1) * 100
    o["e5_n"] = num("e5_n")
    for c in ("flag_jump", "flag_basis", "flag_spread", "flag_q4"):
        o[c] = num(c)
    o["filter_fail"] = ""
    for c in ("M1_ref", "SARIMAX_ref", "E5_ref", "E6_ref", "drv_ksic_only"):
        o[c] = np.nan
    for c in ("drv_grade", "drv_source", "x_src", "ref_note", "struct_mode"):
        o[c] = None
    for c in ("flag_struct_break", "flag_q4_calc", "rev_fixed_n", "flag_cons_gap40"):
        o[c] = np.nan
    o["run_id"] = d["run_id"]
    return o


def finish(o: pd.DataFrame) -> pd.DataFrame:
    o["E5_yoy_pct"] = (o["E5"] / o["rev_base"] - 1) * 100
    o["gap_E5_vs_cons_pct"] = (o["E5"] / o["cons"] - 1) * 100
    o["margin_E5_pct"] = o["OP_hat_E5"] / o["E5"] * 100
    o["margin_alt_pct"] = o["OP_hat_alt"] / o["E5"] * 100
    o["margin_naive_pct"] = o["OP_naive"] / o["B2"] * 100
    is2d = o["source"] != "2g"
    has_c = o["gap_E5_vs_cons_pct"].notna()
    o["cmp_basis"] = np.where(is2d, "B2 (2d E4 신호)", np.where(has_c, "컨센서스", "B2 (E3 신호)"))
    o["cmp_gap_pct"] = np.where(is2d, o["gap_E4_vs_B2_pct"], np.where(has_c, o["gap_E5_vs_cons_pct"],
                                                                        o["gap_E3_vs_B2_pct"]))
    o["signal"] = SIGNAL
    o["semi_equip"] = o["entity_id"].isin(semi_set()).astype(int)
    sb = o["flag_struct_break"] == 1
    o["data_note"] = np.where(sb, "자료 단절", np.where(o["struct_mode"] == "post_only", "사건 후 구간만 사용",
                              np.where(o["flag_q4_calc"] == 1, "4분기 산출 의심",
                                       np.where(o["rev_fixed_n"].fillna(0) > 0, "4분기 고침", ""))))
    o["cmp_basis"] = np.where(sb & has_c, "컨센서스 (비교용, 자료 단절)", np.where(sb, "자료 단절 (비교 없음)", o["cmp_basis"]))
    o["cmp_gap_pct"] = np.where(sb & ~has_c, np.nan, o["cmp_gap_pct"])
    missing = [c for c in o.columns if c not in COLDESC]
    if missing:
        raise RuntimeError(f"설명 없는 열: {missing}")
    o = o[[c for c in COLDESC if c in o]]
    o["_g"] = o["group_2g"].map({g: i for i, g in enumerate(GROUP_ORDER)}).fillna(99)
    o["_a"] = -o["cmp_gap_pct"].abs().fillna(-1)
    o = o.sort_values(["horizon", "_g", "_a", "name"]).drop(columns=["_g", "_a"])
    return o.reset_index(drop=True)


def semi_set() -> set:
    """K9 와 같은 반도체 장비·부품 판정 (k8_drivers.semi_firms). 2d 확대 기업은 2d 제품 표로 본다."""
    from ..eval2d import k1_survey  # noqa: F401  (경로 확인용)
    from . import k8_drivers as D
    rows = pd.read_csv(output_path("2g", "K8", "drivers") / "k8_products.csv", dtype={"ticker": str})
    r2 = output_path("2d", "K2") / "k2_products.csv"
    if r2.exists():
        rows = pd.concat([rows, pd.read_csv(r2, dtype={"ticker": str})], ignore_index=True)
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "ksic": str})
    return set(D.semi_firms(k1[["ticker", "ksic"]], rows)["ticker"])


def to_html(o: pd.DataFrame, j: pd.DataFrame, title: str) -> str:
    def tab(df):
        head = "".join(f'<th title="{html.escape(COLDESC.get(c, ""))}">{html.escape(c)}</th>' for c in df.columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(_fmt(v))}</td>" for v in r) + "</tr>"
                       for r in df.itertuples(index=False))
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    desc = pd.DataFrame({"column": list(COLDESC), "description": list(COLDESC.values())})
    css = ("body{font-family:sans-serif;font-size:12px}table{border-collapse:collapse;margin:8px 0}"
           "td,th{border:1px solid #ccc;padding:2px 5px;white-space:nowrap}th{background:#eee;position:sticky;top:0}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{css}</style></head><body>"
            f"<h2>{title}</h2><p><b>{SIGNAL}</b>. 금액 억원. 2d 확대 행은 fd_live_2d 기록을 그대로 옮긴 읽기 전용입니다.</p>"
            f"<h3>판단</h3>{tab(j)}<h3>전체</h3>{tab(o)}<h3>변수 설명</h3>{tab(desc)}</body></html>")


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (float, np.floating)):
        return f"{v:,.1f}"
    return str(v)


def main(quarter: str = None) -> dict:
    d = latest_2g(quarter)
    q0 = d.loc[d["horizon"] == 0, "quarter"].mode().iloc[0]
    o = finish(pd.concat([table_2g(d), table_2d(latest_2d(q0))], ignore_index=True))
    j = o[JUDGE].copy()
    hide = (o["flag_struct_break"] == 1) & o["cons"].isna()
    for c in ("E5", "E3", "E5_yoy_pct", "OP_hat_E5", "OP_hat_alt", "margin_E5_pct", "E5_ref", "E6_ref"):
        j.loc[hide, c] = np.nan
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{VERSION}_{q0}_{stamp}"
    out = output_path("2g", "radar", q0)
    title = f"2g 통합 레이더 v4 {q0} (2g 기준일 {d['asof_date'].max()})"
    paths = {"csv": out / f"{stem}.csv", "decision": out / f"{stem}_decision.csv", "xlsx": out / f"{stem}.xlsx",
             "html": out / f"{stem}.html", "columns": out / f"{stem}_columns.csv"}
    o.to_csv(paths["csv"], index=False, encoding="utf-8-sig")
    j.to_csv(paths["decision"], index=False, encoding="utf-8-sig")
    desc = pd.DataFrame({"column": list(COLDESC), "description": list(COLDESC.values())})
    desc.to_csv(paths["columns"], index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(paths["xlsx"]) as w:
        j.round(2).to_excel(w, sheet_name="판단", index=False)
        o.round(2).to_excel(w, sheet_name="전체", index=False)
        desc.to_excel(w, sheet_name="변수설명", index=False)
    paths["html"].write_text(to_html(o, j, title), encoding="utf-8")
    k8 = output_path(*COPY_TO)
    for p in paths.values():
        shutil.copy2(p, k8 / p.name)
    print({k: str(v) for k, v in paths.items()})
    print(o.groupby(["quarter", "group_2g"]).size().to_dict())
    return {"rows": len(o), "paths": paths}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default=None)
    main(ap.parse_args().quarter)
