# -*- coding: utf-8 -*-
"""2g 레이더 출력 v1 (method_2g_v1 3절, K8 7). fd_live_2g 의 기업별 가장 최근 스냅샷으로 기업 x 분기(Q, Q+1) 표를 만든다.
DB 쓰기 없음. 금액 단위 억원. 공개 출력이라 드라이버 코드·이름(drv_code, m1_source)은 넣지 않는다.
판단 시트에는 '매매 신호 아님(검증 전)' 을 적는다.

python -m DATA.fdriver.eval2g.radar_2g [--quarter 2026Q3]
출력: OneDrive/INVESTMENT/한국주식/fdriver/2g/K8/radar/ 와 2g/radar/<분기>/
      radar2g_v1_<분기>_<생성일시>.csv (전체), _decision.csv (판단), .xlsx (판단 | 전체 | 변수설명), .html, _columns.csv
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

VERSION = "radar2g_v1"
EOK = 1e8
SIGNAL = "매매 신호 아님(검증 전)"

COLDESC = {
    "entity_id": "종목코드",
    "name": "기업명",
    "group_2g": "대상 집단: 정상(컨센서스 정상 커버리지) / 레이더39(기존 레이더)",
    "market": "시장",
    "industry_group": "업종 묶음 (K1)",
    "quarter": "예측 분기",
    "horizon": "0 = 기준일에 끝난 마지막 분기, 1 = 그 다음 분기",
    "asof_date": "기준일 (이 날까지 공표된 자료만 사용)",
    "announced": "기준일에 이 분기 실적을 이미 발표했으면 1",
    "rule": "영업이익 규칙 (R1 회귀, R2 원가율·판관비, R3 영업비용률, R4 예측 안 함)",
    "rev_base": "전년 동기 매출 (억원)",
    "E5": "주 매출 예측 E5 (억원): SARIMA·ETS·Theta·B2 (+M1) 기하평균",
    "E5_yoy_pct": "E5 의 전년 동기 대비 성장률 (%)",
    "cons": "매출 컨센서스 (억원, 기준일 이하 최신 관측)",
    "cons_asof": "컨센서스 관측일",
    "gap_E5_vs_cons_pct": "E5 / 컨센서스 - 1 (%)",
    "SARIMA": "SARIMA 매출 예측 (억원)",
    "ETS": "ETS 매출 예측 (억원)",
    "Theta": "Theta 매출 예측 (억원)",
    "M1": "드라이버 모형 매출 예측 (억원, 기존 레이더 드라이버 기업의 첫 분기만)",
    "B2": "B2 매출 예측 (억원): 최근 분기 성장률 유지",
    "e5_n": "E5 에 들어간 모형 수",
    "OP_hat_E5": "주 영업이익 예측 (억원): 규칙에 매출 E5 를 넣은 값",
    "OP_hat_alt": "대안 영업이익 예측 (억원): R1 최근 12분기 회귀, R2 판관비 이상값 대체 후 예측, R3 주 예측과 같음",
    "OP_naive": "단순 영업이익 예측 (억원): B2 매출 x 전년 동기 영업이익률",
    "margin_E5_pct": "OP_hat_E5 / E5 (%)",
    "margin_alt_pct": "OP_hat_alt / E5 (%)",
    "margin_naive_pct": "OP_naive / B2 (%)",
    "flag_b2_jump": "1: B2 성장률이 세 시계열 모형 중앙값과 20%p 넘게 다름 (최근 한 분기 급변)",
    "flag_m1_dir": "1: 드라이버 모형 방향이 나머지 네 모형 모두와 반대",
    "flag_op_sign": "1: 세 영업이익 예측의 부호가 서로 다름",
    "sga_fixed_n": "판관비를 항등식으로 고친 분기 수 (DART 기준 기업)",
    "flag_jump": "1: 매출이 전년 대비 2배 초과·절반 미만인 분기가 4개 이상 (K1)",
    "flag_basis": "1: 보정 기준과 DART 가 선정 구간에 계속 다름",
    "flag_spread": "1: 원가 연동 업종 (정유·기초화학·합성수지·화학섬유·철강·비철)",
    "flag_q4": "1: 4분기 비용이 몰리는 기업 (K4b 기준)",
    "filter_fail": "레이더39 중 2d 필터를 통과하지 못한 항목",
    "signal": "매매 신호 여부",
    "run_id": "스냅샷 실행 번호",
}
JUDGE = ["signal", "entity_id", "name", "group_2g", "industry_group", "quarter", "announced", "E5", "cons",
         "gap_E5_vs_cons_pct", "E5_yoy_pct", "OP_hat_E5", "OP_hat_alt", "OP_naive", "margin_E5_pct", "rule",
         "flag_b2_jump", "flag_m1_dir", "flag_op_sign", "sga_fixed_n", "flag_jump", "flag_q4"]


def latest(quarter: str = None) -> pd.DataFrame:
    d = db.read_sql(f"SELECT * FROM `{C.T_LIVE}`")
    if d.empty:
        raise RuntimeError("fd_live_2g 기록 없음")
    d["recorded_at"] = pd.to_datetime(d["recorded_at"])
    last = d.sort_values("recorded_at").drop_duplicates("entity_id", keep="last")[["entity_id", "run_id"]]
    d = d.merge(last, on=["entity_id", "run_id"])
    if quarter:
        q0 = d[d["horizon"] == 0]["quarter"].mode().iloc[0]
        if q0 != quarter:
            raise RuntimeError(f"최근 스냅샷 첫 분기 {q0} != {quarter}")
    return d


def table(d: pd.DataFrame) -> pd.DataFrame:
    tg = C.read_targets().set_index("entity_id")
    o = pd.DataFrame({"entity_id": d["entity_id"], "name": d["entity_id"].map(tg["name"])})
    for c in ("group_2g", "market", "industry_group"):
        o[c] = d["entity_id"].map(tg[c])
    num = lambda c: pd.to_numeric(d[c], errors="coerce")
    o["quarter"], o["horizon"], o["asof_date"] = d["quarter"], d["horizon"], d["asof_date"]
    o["announced"] = num("announced_at_snapshot").fillna(0).astype(int)
    o["rule"] = d["rule"]
    for c in ("rev_base", "E5", "cons", "SARIMA", "ETS", "Theta", "M1", "B2", "OP_hat_E5", "OP_hat_alt", "OP_naive"):
        o[c] = num(c) / EOK
    o["E5_yoy_pct"] = (o["E5"] / o["rev_base"] - 1) * 100
    o["cons_asof"] = d["cons_asof"]
    o["gap_E5_vs_cons_pct"] = (o["E5"] / o["cons"] - 1) * 100
    o["e5_n"] = num("e5_n")
    o["margin_E5_pct"] = o["OP_hat_E5"] / o["E5"] * 100
    o["margin_alt_pct"] = o["OP_hat_alt"] / o["E5"] * 100
    o["margin_naive_pct"] = o["OP_naive"] / o["B2"] * 100
    for c in ("flag_b2_jump", "flag_m1_dir", "flag_op_sign", "sga_fixed_n", "flag_jump", "flag_basis", "flag_spread",
              "flag_q4"):
        o[c] = num(c)
    o["filter_fail"] = d["entity_id"].map(tg["filter_fail"]).fillna("")
    o["signal"] = SIGNAL
    o["run_id"] = d["run_id"]
    cols = [c for c in COLDESC if c in o]
    missing = [c for c in o.columns if c not in COLDESC]
    if missing:
        raise RuntimeError(f"설명 없는 열: {missing}")
    return o[cols].sort_values(["horizon", "group_2g", "industry_group", "name"]).reset_index(drop=True)


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
            f"<h2>{title}</h2><p><b>{SIGNAL}</b>. 금액 억원.</p><h3>판단</h3>{tab(j)}<h3>전체</h3>{tab(o)}"
            f"<h3>변수 설명</h3>{tab(desc)}</body></html>")


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (float, np.floating)):
        return f"{v:,.1f}"
    return str(v)


def main(quarter: str = None) -> dict:
    d = latest(quarter)
    o = table(d)
    q0 = o.loc[o["horizon"] == 0, "quarter"].mode().iloc[0]
    j = o[JUDGE]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{VERSION}_{q0}_{stamp}"
    out = output_path("2g", "radar", q0)
    title = f"2g 레이더 v1 {q0} (기준일 {o['asof_date'].max()})"
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
    k8 = output_path("2g", "K8", "radar")
    for p in paths.values():
        shutil.copy2(p, k8 / p.name)
    print({k: str(v) for k, v in paths.items()})
    return {"rows": len(o), "paths": paths}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default=None)
    main(ap.parse_args().quarter)
