# -*- coding: utf-8 -*-
"""2d 확대 레이더 (명세서 2d v3 7절 레이더 + 8절 작업 6). 내부용. DB 쓰기 없음.

python -m DATA.fdriver.radar.expand2d --quarter 2026Q3 [--asof YYYY-MM-DD] [--base-pickle 경로]

- 기존 레이더 39곳: radar.build.build(quarter, asof) 를 그대로 계산하고 radar.output.to_output 으로 출력 이름 표를 만든다.
  기존 판단 규칙과 값은 바꾸지 않는다.
- 확대 73곳: fd_live_2d (그 분기 실전 사전 기록, 덮어쓰기 불가) 의 값을 기존 출력 이름으로 옮긴다. 다시 계산하지 않는다.
  출처: fd_driver_map_2d (status·출처), fd_op_rule (영업이익 규칙·표시), K1 상장사 표 (업종·시장·coverage_class),
  kr_revenue_consensus (저커버리지 기업의 기준일 이전 최신 컨센서스), fd_earnings_event (예상 발표일, radar.build 와 같은 방식).
- 명세서 7절 추가 열 (두 집단 모두에 둔다. 기존 39곳은 영업이익 모듈 미적용이라 op_* 공란):
  coverage_class, gap_E_vs_B2_pct, op_rule, OP_hat (jo_OP_hat), op_margin_hat_pct, op_naive (jo_OP_naive)
  + 판단 시트용: radar_group, cmp_basis, gap_cmp_pct, signal_status, op_margin_naive_pct, op_flag_spread, op_flag_q4,
    flag_jump_2d, flag_basis_2d.
- 비교 기준 (coverage_class 에 따라, 7절): 정상 -> 컨센서스 (gap_E_vs_cons_pct), 저커버리지 -> 기준일 이전 컨센서스가
  있으면 컨센서스 ('컨센서스(저커버리지)'), 없으면 B2, 없음 -> B2 (gap_E_vs_B2_pct).
- 매매 신호: 확대 73곳은 모두 '보류' (K5 판정: D1 성공, D2 실패 -> 레이더 유지, 매매 신호로 쓰지 않음).
  decision = '보류(2d 매매 신호)', decision_reason 에 비교 기준·갭·구성 불일치를 적는다. 기존 39곳 decision 은 그대로.
- 공개 금지: HS 코드·드라이버명·가중치는 이 출력에도 넣지 않는다 (드라이버 출처 종류만 표시).
출력: OUTPUT_ROOT/radar/<분기>/radar2d_<분기>_<생성일시>.csv / _decision.csv / .xlsx / .html, radar2d_columns_*.csv
"""
import argparse
import html
import pickle

import numpy as np
import pandas as pd

from .. import db, events
from ..adapters import kr_consensus
from ..config import output_path
from . import build, coldesc, output

SIGNAL_HOLD = "보류(2d 매매 신호)"
HOLD_REASON = "2d 판정 (K5): D1 성공, D2 실패 -> 레이더 유지, 매매 신호로 쓰지 않음"

NEW_COLDESC = {
    "radar_group": coldesc._d("판단", None, "레이더 집단: '기존 39' (2a·2c 레이더) / '2d 확대' (2d 확대 기업)", "", "문자"),
    "coverage_class": coldesc._d("판단", "coverage_class", "컨센서스 커버리지 (2d 2절): 없음 / 저커버리지 / 정상",
                                 "직전 8분기(2024Q3~2026Q2) 컨센서스 관측 분기 수 0 / 1~4 / 5~8", "문자",
                                 "K1 상장사 표 기준. 증권사 수 항목은 없어 관측 분기 수만으로 구분"),
    "cmp_basis": coldesc._d("판단", None, "판단 비교 기준 (7절, coverage_class 에 따라)",
                            "정상 -> 컨센서스, 저커버리지 -> 컨센서스 있으면 '컨센서스(저커버리지)' 없으면 B2, 없음 -> B2",
                            "문자"),
    "gap_cmp_pct": coldesc._d("판단", None, "비교 기준 대비 E 의 차이", "cmp_basis 가 컨센서스면 gap_E_vs_cons_pct, B2 면 "
                              "gap_E_vs_B2_pct", coldesc.PCT),
    "gap_E_vs_B2_pct": coldesc._d("컨센서스 비교", None, "E 와 B2 의 차이 (2d 서프라이즈 신호 gap_E_vs_B2)",
                                  "(exp(g_E - g_B2) - 1) x 100", coldesc.PCT,
                                  "2d D2 판정에서 주가 반응 예측력 없음 (계수 음수). 참고용"),
    "signal_status": coldesc._d("판단", None, "매매 신호 상태", "2d 확대: '보류' (K5 판정 운영), 기존 39: '기존 규칙'", "문자"),
    "op_rule": coldesc._d("예측 매출 금액", "rule", "영업이익 예측 규칙 (fd_op_rule): R1 회귀, R2 비용 분리, R3 영업비용률, "
                          "R4 예측 안 함", "선정 구간 2019Q1~2023Q4 로 배정, 평가 전 고정", "문자",
                          "R2 비용 계열 예측 실패 시 R3 식으로 대체 (op_note). 기존 39곳은 미적용"),
    "jo_OP_hat": coldesc._d("예측 매출 금액", "OP_hat", "예상 영업이익 (OP_hat, 매출 입력 E)",
                            "R1 a + b E, R2 E (1 - 원가율) - 판관비, R3 E (1 - c)", "조원"),
    "op_margin_hat_pct": coldesc._d("예측 성장률 %", None, "예상 영업이익률", "OP_hat / E x 100", coldesc.PCT),
    "jo_OP_naive": coldesc._d("예측 매출 금액", "OP_naive", "단순 기준 영업이익 (op_naive)", "B2 매출 x 전년 동기 영업이익률",
                              "조원"),
    "op_margin_naive_pct": coldesc._d("예측 성장률 %", None, "단순 기준 영업이익률", "OP_naive / B2 x 100", coldesc.PCT),
    "op_flag_spread": coldesc._d("발표일·경고", "flag_spread", "원재료 스프레드 업종 표시 (영업이익 주의)",
                                 "업종코드 19, 201, 203, 205, 241, 242", "1 / 0"),
    "op_flag_q4": coldesc._d("발표일·경고", "flag_q4", "선정 구간 4분기 비용 급증 이력 (영업이익 주의)",
                             "4분기 판관비 > 연평균 x 1.5, 또는 4분기 영업비용률이 1~3분기보다 10%p 이상 높은 해 2개 이상",
                             "1 / 0"),
    "flag_jump_2d": coldesc._d("발표일·경고", "flag_jump", "매출 급변 이력 (2d 9절 4)", "2019Q1~2026Q2 전년 대비 2배 초과·"
                               "절반 미만 분기 4개 이상", "1 / 0"),
    "flag_basis_2d": coldesc._d("발표일·경고", "flag_basis", "매출 기준 불일치 표시 (2d 9절 2)", "", "1 / 0"),
}
DESC = {**coldesc.COLDESC, **NEW_COLDESC}
NEW_COLUMNS = list(NEW_COLDESC)
JUDGE_EXTRA = ["radar_group", "coverage_class", "cmp_basis", "gap_cmp_pct", "gap_E_vs_B2_pct", "signal_status",
               "op_rule", "jo_OP_hat", "op_margin_hat_pct", "jo_OP_naive", "op_flag_spread", "op_flag_q4", "flag_jump_2d"]


def k1_table() -> pd.DataFrame:
    return pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "ksic": str})


def base_rows(res) -> pd.DataFrame:
    o = output.to_output(res["table"])
    k1 = k1_table().set_index("ticker")
    o["radar_group"] = "기존 39"
    o["coverage_class"] = o["entity_id"].map(k1["coverage_class"]).fillna("정상")
    g = lambda c: pd.to_numeric(o[c], errors="coerce")
    o["gap_E_vs_B2_pct"] = (np.exp(g("g_E") - g("g_B2")) - 1) * 100
    has_cons = g("gap_E_vs_cons_pct").notna()
    o["cmp_basis"] = np.where(has_cons, "컨센서스", "B2")
    o["gap_cmp_pct"] = np.where(has_cons, g("gap_E_vs_cons_pct"), o["gap_E_vs_B2_pct"])
    o["signal_status"] = "기존 규칙"
    for c in ("op_rule", "jo_OP_hat", "op_margin_hat_pct", "jo_OP_naive", "op_margin_naive_pct", "op_flag_spread",
              "op_flag_q4", "flag_jump_2d", "flag_basis_2d"):
        o[c] = np.nan
    return o


def expand_rows(quarter: str, t: pd.Timestamp) -> pd.DataFrame:
    live = db.read_sql("SELECT * FROM fd_live_2d WHERE quarter = :q", {"q": quarter})
    if live.empty:
        raise RuntimeError(f"fd_live_2d 에 {quarter} 기록이 없음")
    mp = db.read_sql("SELECT entity_id, name, source FROM fd_driver_map_2d")
    k1 = k1_table().set_index("ticker")
    q = events.fiscal_quarter_end(quarter)
    ev = events.read_events(live["entity_id"].tolist())
    n = lambda c: pd.to_numeric(live[c], errors="coerce")
    o = pd.DataFrame({"entity_id": live["entity_id"]})
    o["name"] = o["entity_id"].map(mp.drop_duplicates("entity_id").set_index("entity_id")["name"])
    o["sector"] = o["entity_id"].map(k1["ksic_div_name"])
    o["grade"] = "2d"
    o["fiscal_quarter"] = quarter
    o["mode"] = "live (fd_live_2d)"
    o["asof_date"] = pd.to_datetime(live["asof_date"]).dt.date
    o["main_model"] = "E(2d)"
    o["M1_model"] = np.where(live["status"] == "driver", "M1", "")
    o["E_components"] = live["e_label"]
    o["rev_base"] = n("rev_base")
    for m in ("E", "M1", "B2", "TS"):
        o[f"g_{m}"] = n(f"g_{m}")
        o[f"g_{m}_pct"] = (np.exp(n(f"g_{m}")) - 1) * 100
        o[f"rev_{m}"] = n(m)
        o[f"jo_{m}"] = n(m) / 1e12
    # 저커버리지: 기준일 이전 최신 컨센서스
    cc = [kr_consensus.read_consensus(e, quarter, t) if k1["coverage_class"].get(e) == "저커버리지"
          else {"value": None, "asof": None} for e in o["entity_id"]]
    o["rev_cons"] = [c["value"] for c in cc]
    o["jo_cons"] = pd.to_numeric(o["rev_cons"], errors="coerce") / 1e12
    o["cons_asof"] = [None if c["asof"] is None else pd.Timestamp(c["asof"]).date() for c in cc]
    o["g_cons_pct"] = (pd.to_numeric(o["rev_cons"], errors="coerce") / o["rev_base"] - 1) * 100
    o["gap_E_vs_cons_pct"] = (o["rev_E"] / pd.to_numeric(o["rev_cons"], errors="coerce") - 1) * 100
    o["dir_E_vs_cons"] = o["gap_E_vs_cons_pct"].map(output.sign_label)
    o["dir_E_vs_trend"] = (o["g_E"] - o["g_B2"]).map(output.sign_label)
    sp = n("E_spread")
    o["spread_E_pct"] = sp
    o["spread_E_from"] = live["E_spread_from"]
    o["flag_E_spread"] = np.where(sp >= output.SPREAD_THRESHOLD_PCT, "불일치(" + live["E_spread_from"].fillna("") + ")", "")
    ann = [build.expected_announce(e, q, 12, ev[ev["entity_id"] == e]) for e in o["entity_id"]]
    o["announce_date"] = [a["announce_date"] for a in ann]
    o["announce_basis"] = [output.announce_basis(a["announce_kind"]) for a in ann]
    o["flag_drv_missing"] = np.where((live["status"] == "driver") & n("g_M1").isna(), "드라이버결측", "")
    o["flag_nowcast"] = np.where(live["status"] == "ts_only", "시계열만 (드라이버 없음)", "")
    # 7절 열
    o["radar_group"] = "2d 확대"
    o["coverage_class"] = o["entity_id"].map(k1["coverage_class"])
    o["gap_E_vs_B2_pct"] = (np.exp(n("gap_E_vs_B2")) - 1) * 100
    hc = o["gap_E_vs_cons_pct"].notna()
    o["cmp_basis"] = np.where(hc, "컨센서스(저커버리지)", "B2")
    o["gap_cmp_pct"] = np.where(hc, o["gap_E_vs_cons_pct"], o["gap_E_vs_B2_pct"])
    o["signal_status"] = "보류"
    o["op_rule"] = live["rule"]
    o["jo_OP_hat"] = n("OP_hat") / 1e12
    o["op_margin_hat_pct"] = n("OP_hat") / n("E") * 100
    o["jo_OP_naive"] = n("OP_naive") / 1e12
    o["op_margin_naive_pct"] = n("OP_naive") / n("B2") * 100
    o["op_flag_spread"], o["op_flag_q4"] = n("flag_spread"), n("flag_q4")
    o["flag_jump_2d"], o["flag_basis_2d"] = n("flag_jump"), n("flag_basis")
    # 판단: 매매 신호 보류
    o["decision"] = SIGNAL_HOLD
    reasons = []
    for r in o.itertuples():
        parts = [HOLD_REASON, f"비교 {r.cmp_basis}"]
        if pd.notna(r.gap_cmp_pct):
            parts.append(f"갭 {r.gap_cmp_pct:+.1f}%")
        if str(r.flag_E_spread).startswith("불일치"):
            parts.append(f"구성 {r.flag_E_spread}")
        if r.flag_nowcast:
            parts.append(r.flag_nowcast)
        reasons.append(", ".join(parts))
    o["decision_reason"] = reasons
    for c in DESC:
        if c not in o:
            o[c] = np.nan
    return o


def combine(base: pd.DataFrame, exp: pd.DataFrame) -> pd.DataFrame:
    cols = coldesc.OUTPUT_COLUMNS + NEW_COLUMNS
    for c in cols:
        if c not in base:
            base[c] = np.nan
    return pd.concat([base[cols], exp[cols]], ignore_index=True)


def judgement(out: pd.DataFrame) -> pd.DataFrame:
    """기존 39 는 기존 판단 순서, 2d 확대는 |비교 갭| 큰 순."""
    cols = output.JUDGE_COLUMNS + [c for c in JUDGE_EXTRA if c not in output.JUDGE_COLUMNS]
    j = out[cols].copy()
    j["_g0"] = (j["radar_group"] != "기존 39").astype(int)
    j["_r"] = j["decision"].map(output.decision_rank)
    j["_g"] = -pd.to_numeric(j["gap_cmp_pct"], errors="coerce").abs().fillna(-1)
    return j.sort_values(["_g0", "_r", "_g", "name"]).drop(columns=["_g0", "_r", "_g"]).reset_index(drop=True)


def desc_table(columns) -> pd.DataFrame:
    rows = []
    for c in dict.fromkeys(columns):
        if c not in DESC:
            raise KeyError(f"설명 없는 열: {c}")
        d = DESC[c]
        rows.append({"분류": d["group"], "출력 변수명": c, "내부 변수명": d.get("internal") or "", "의미": d["meaning"],
                     "계산식": d["formula"], "단위": d["unit"], "비고": d["note"], "추가 (2d)": "Y" if c in NEW_COLDESC else ""})
    return pd.DataFrame(rows)


def to_html(out, judge, title) -> str:
    def tab(t):
        head = "".join(f"<th title='{html.escape(DESC[c]['meaning'], quote=True)}'>{html.escape(c)}</th>" for c in t.columns)
        body = "".join("<tr class='" + ("hold" if str(r.get("decision", "")).startswith("보류") else "") + "'>" +
                       "".join(f"<td>{build._fmt(c, r[c])}</td>" for c in t.columns) + "</tr>"
                       for _, r in t.iterrows())
        return f"<div class='wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#e6e5e0;--hold:#ecebe7}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1a1a19;--ink:#f2f2f0;--muted:#c3c2b7;--line:#383835;--hold:#2c2c2a}}}}
body{{background:var(--bg);color:var(--ink);font-family:-apple-system,'Malgun Gothic',sans-serif;margin:16px}}
h1{{font-size:18px}} h2{{font-size:15px;margin-top:24px}} p{{color:var(--muted);font-size:13px}} .wrap{{overflow-x:auto}}
table{{border-collapse:collapse;font-size:12px;white-space:nowrap}} th,td{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}}
th{{position:sticky;top:0;background:var(--bg)}} tr.hold td{{background:var(--hold)}}</style></head><body>
<h1>{html.escape(title)}</h1><h2>판단</h2><p>{html.escape(output.rule_summary())}</p>
<p>2d 확대 73곳: {html.escape(HOLD_REASON)}. 비교 기준은 coverage_class 에 따라 다름 (cmp_basis).</p>{tab(judge)}
<h2>레이더 전체</h2>{tab(out)}</body></html>"""


def save(out, judge, quarter, t) -> dict:
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    d = output_path("radar", quarter)
    base = f"radar2d_{quarter}_{stamp}"
    p = {"csv": d / f"{base}.csv", "decision": d / f"{base}_decision.csv", "xlsx": d / f"{base}.xlsx",
         "html": d / f"{base}.html", "columns": d / f"radar2d_columns_{quarter}_{stamp}.csv"}
    for f in p.values():
        if f.exists():
            raise FileExistsError(f)
    desc = desc_table(list(out.columns))
    out.to_csv(p["csv"], index=False, encoding="utf-8-sig")
    judge.to_csv(p["decision"], index=False, encoding="utf-8-sig")
    desc.to_csv(p["columns"], index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(p["xlsx"], engine="openpyxl") as xw:
        judge.to_excel(xw, sheet_name="판단", index=False)
        out.to_excel(xw, sheet_name="radar", index=False)
        desc.to_excel(xw, sheet_name="변수설명", index=False)
    p["html"].write_text(to_html(out, judge, f"fdriver 레이더 + 2d 확대 {quarter} (기준일 {t.date()}, 생성 {stamp})"),
                         encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--base-pickle", default=None)
    a = ap.parse_args(argv)
    t = pd.Timestamp(a.asof).normalize() if a.asof else pd.Timestamp.today().normalize()
    res = pickle.load(open(a.base_pickle, "rb")) if a.base_pickle else build.build(a.quarter, t)
    out = combine(base_rows(res), expand_rows(a.quarter, t))
    judge = judgement(out)
    p = save(out, judge, a.quarter, t)
    print({k: str(v) for k, v in p.items()})
    print(out.groupby(["radar_group", "decision"]).size().to_string())
    print(out.groupby(["radar_group", "cmp_basis"]).size().to_string())


if __name__ == "__main__":
    main()
