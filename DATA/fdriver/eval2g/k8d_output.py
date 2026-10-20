# -*- coding: utf-8 -*-
"""2g K8d: 출력 형식 개편 (예측 방법은 바꾸지 않음). DB 쓰기 없음. 레이더 v5.
기업당 1행, 열 이름에 분기 접두어(26Q2_, 26Q3_, 26Q4_)와 한글, 금액 억원.

입력 (읽기만)
- 2g: fd_live_2g 기업별 가장 최근 스냅샷 (26Q3 = horizon 0, 26Q4 = horizon 1)
- 2d 확대 73곳: fd_live_2d 기업별 최신 method_version 'v2' 스냅샷 (26Q3 만, 다시 계산하지 않음)
- 실적: 매출 fd_rev_adj_2g / fd_rev_adj_2d, 영업이익 2g 항목(eval2g.common.items_2g) / 2d 항목(k3_oprule.items)
- 매출 컨센서스: kr_revenue_consensus (기준일 이하 최신). 영업이익 컨센서스: 적재된 표가 없어 비운다.
계산
- QoQ% = 이번 / 직전 - 1. 영업이익 QoQ% 는 직전 영업이익 > 0 일 때만.
- 연속성장분기: 26Q2 부터 거꾸로 직전 분기보다 커진 분기가 이어진 수 (최대 8). 영업이익은 금액 증가 기준.
- 계절조정QoQ%p = 예상QoQ% - 작년 같은 분기의 QoQ% (26Q3 이면 25Q3/25Q2, 26Q4 이면 25Q4/25Q3)
- 영업이익_상태: 직전·이번 부호로 흑자지속 / 흑자전환 / 적자전환, 둘 다 0 이하면 적자확대 / 적자축소 / 적자지속(변화 1% 안)
- 26Q4 의 직전 분기는 26Q3 예상(E5, OP_hat_E5)
- 자료단절 행은 컨센서스가 없으면 예상 열을 비운다 (K8c 규칙과 같음).
python -m DATA.fdriver.eval2g.k8d_output
"""
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..adapters import kr_consensus
from ..config import output_path
from ..eval2d import k2_targets as K2
from ..eval2d import k3_oprule as K3
from . import common as C
from .radar_2g import latest_2d, latest_2g, semi_set

OUT = output_path("2g", "K8d")
VERSION = "radar2g_v5"
EOK = 1e8
SIGNAL = "아님(검증 전)"
Q = {"25Q2": "2025-06-30", "25Q3": "2025-09-30", "25Q4": "2025-12-31", "26Q1": "2026-03-31", "26Q2": "2026-06-30"}
HG = {"매출_컨센대비_min": 0.0, "영업이익_컨센대비_min": 0.0, "매출예상QoQ_min": 5.0, "영업이익예상QoQ_min": 10.0,
      "계절조정_min": 0.0, "연속성장_min": 1}  # 2026-10-20 호영님 결정: 2 -> 1
GROUP_ORDER = ["레이더39", "정상", "저커버리지", "컨센서스 없음", "2d 확대"]


def pct(a, b):
    return (a / b - 1) * 100 if a is not None and b is not None and pd.notna(a) and pd.notna(b) and b != 0 else np.nan


def op_pct(a, b):
    return (a / b - 1) * 100 if pd.notna(a) and pd.notna(b) and b > 0 else np.nan


def op_state(prev, cur) -> str:
    if pd.isna(prev) or pd.isna(cur):
        return ""
    if prev > 0 and cur > 0:
        return "흑자지속"
    if prev <= 0 < cur:
        return "흑자전환"
    if prev > 0 >= cur:
        return "적자전환"
    if abs(cur - prev) <= 0.01 * abs(prev):
        return "적자지속"
    return "적자확대" if cur < prev else "적자축소"


def streak(s: pd.Series, end: pd.Timestamp, n: int = 8) -> int:
    s = s.dropna().sort_index()
    s = s[s.index <= end]
    k, idx = 0, list(s.index)
    for i in range(len(idx) - 1, 0, -1):
        if (idx[i] - idx[i - 1]).days > 100 or k >= n:
            break
        if s.iloc[i] > s.iloc[i - 1]:
            k += 1
        else:
            break
    return k


def actuals():
    tg = C.read_targets()
    ids2g = tg["entity_id"].tolist()
    m2d = db.read_sql("SELECT entity_id, name FROM fd_driver_map_2d WHERE status IN ('driver','ts_only')").drop_duplicates(
        "entity_id")
    ids2d = m2d["entity_id"].tolist()
    a2g, a2d = C.read_adj(ids2g), K2.read_adj(ids2d)
    it = pd.concat([C.items_2g(ids2g, a2g)[0], K3.items(ids2d, a2d, quarters=None)], ignore_index=True)
    rev = pd.concat([a2g, a2d]).pivot_table(index="period_end", columns="entity_id", values="revenue_adj") / EOK
    op = it[it["item"] == "op"].pivot_table(index="period_end", columns="entity_id", values="value") / EOK
    return tg, m2d, rev, op


def quarter_block(p: str, f: dict, prev_rev, prev_op, ly_prev_rev, ly_rev, ly_prev_op, ly_op, cons_rev) -> dict:
    """p: '26Q3_' 같은 접두어. f: E5, OP (억원)."""
    E5, OP = f.get("E5"), f.get("OP")
    o = {f"{p}매출예상(E5)": E5, f"{p}매출컨센": cons_rev, f"{p}매출_컨센대비%": pct(E5, cons_rev),
         f"{p}매출예상QoQ%": pct(E5, prev_rev), f"{p}매출예상YoY%": pct(E5, ly_rev),
         f"{p}작년동기_매출QoQ%": pct(ly_rev, ly_prev_rev)}
    o[f"{p}매출_계절조정QoQ%p"] = o[f"{p}매출예상QoQ%"] - o[f"{p}작년동기_매출QoQ%"]
    o.update({f"{p}영업이익예상(OP_hat_E5)": OP, f"{p}영업이익컨센": np.nan, f"{p}영업이익_컨센대비%": np.nan,
              f"{p}영업이익예상QoQ%": op_pct(OP, prev_op),
              f"{p}영업이익_증가액": (OP - prev_op) if pd.notna(OP) and pd.notna(prev_op) else np.nan,
              f"{p}영업이익_상태": op_state(prev_op, OP), f"{p}작년동기_영업이익QoQ%": op_pct(ly_op, ly_prev_op)})
    o[f"{p}영업이익_계절조정QoQ%p"] = o[f"{p}영업이익예상QoQ%"] - o[f"{p}작년동기_영업이익QoQ%"]
    o[f"{p}영업이익률예상%"] = OP / E5 * 100 if pd.notna(OP) and pd.notna(E5) and E5 else np.nan
    return o


def build() -> (pd.DataFrame, dict):
    tg, m2d, rev, op = actuals()
    g = latest_2g()
    d2 = latest_2d("2026Q3")
    asof_2g = str(pd.to_datetime(g["asof_date"]).max().date())
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str}).set_index("ticker")
    semi = semi_set()
    num = lambda r, c: float(r[c]) if c in r and pd.notna(r[c]) else np.nan
    rows = []
    firms = [(e, "2g") for e in g["entity_id"].unique()] + [(e, "2d") for e in d2["entity_id"].unique()]
    tgi = tg.set_index("entity_id")
    for e, src in firms:
        if src == "2g":
            r3 = g[(g["entity_id"] == e) & (g["horizon"] == 0)].iloc[0]
            r4s = g[(g["entity_id"] == e) & (g["horizon"] == 1)]
            r4 = r4s.iloc[0] if len(r4s) else None
            name, group = tgi.at[e, "name"], tgi.at[e, "group_2g"]
        else:
            r3, r4 = d2[d2["entity_id"] == e].iloc[0], None
            name, group = m2d.set_index("entity_id").at[e, "name"], "2d 확대"
        R = rev[e] if e in rev.columns else pd.Series(dtype=float)
        O = op[e] if e in op.columns else pd.Series(dtype=float)
        v = lambda s, k: float(s.get(pd.Timestamp(Q[k]))) if pd.notna(s.get(pd.Timestamp(Q[k]), np.nan)) else np.nan
        r26q2, o26q2, r26q1, o26q1 = v(R, "26Q2"), v(O, "26Q2"), v(R, "26Q1"), v(O, "26Q1")
        sb = int(num(r3, "flag_struct_break") == 1)
        asof = pd.Timestamp(r3["asof_date"])
        c3 = kr_consensus.read_consensus(e, "2026Q3", asof)["value"]
        c4 = kr_consensus.read_consensus(e, "2026Q4", asof)["value"]
        c3 = c3 / EOK if c3 else np.nan
        c4 = c4 / EOK if c4 else np.nan
        f3 = {"E5": num(r3, "E5") / EOK, "OP": num(r3, "OP_hat_E5") / EOK}
        f4 = {"E5": num(r4, "E5") / EOK, "OP": num(r4, "OP_hat_E5") / EOK} if r4 is not None else {"E5": np.nan, "OP": np.nan}
        if sb:
            if pd.isna(c3):
                f3 = {"E5": np.nan, "OP": np.nan}
            if pd.isna(c4):
                f4 = {"E5": np.nan, "OP": np.nan}
        row = {"종목코드": e, "기업": name, "집단": group, "업종": k1["industry_group"].get(e), "반도체장비": int(e in semi),
               "시장": k1["market"].get(e), "기준일": str(asof.date()),
               "26Q3_발표여부": "발표" if num(r3, "announced_at_snapshot") == 1 else "",
               "26Q2_매출(실적)": r26q2, "26Q2_영업이익(실적)": o26q2,
               "26Q2_영업이익률%": o26q2 / r26q2 * 100 if pd.notna(o26q2) and r26q2 else np.nan,
               "26Q2_매출QoQ%": pct(r26q2, r26q1), "26Q2_영업이익QoQ%": op_pct(o26q2, o26q1),
               "매출_연속성장분기": streak(R, pd.Timestamp(Q["26Q2"])), "영업이익_연속성장분기": streak(O, pd.Timestamp(Q["26Q2"]))}
        row.update(quarter_block("26Q3_", f3, r26q2, o26q2, v(R, "25Q2"), v(R, "25Q3"), v(O, "25Q2"), v(O, "25Q3"), c3))
        row.update(quarter_block("26Q4_", f4, f3["E5"], f3["OP"], v(R, "25Q3"), v(R, "25Q4"), v(O, "25Q3"), v(O, "25Q4"), c4))
        row.update({"자료단절": sb, "4분기산출이상": int(num(r3, "flag_q4_calc") == 1),
                    "26Q3_컨센괴리40": int(abs(row["26Q3_매출_컨센대비%"]) > 40) if pd.notna(row["26Q3_매출_컨센대비%"]) else np.nan,
                    "26Q4_컨센괴리40": int(abs(row["26Q4_매출_컨센대비%"]) > 40) if pd.notna(row["26Q4_매출_컨센대비%"]) else np.nan,
                    "26Q3_B2급변": num(r3, "flag_b2_jump"), "26Q4_B2급변": num(r4, "flag_b2_jump") if r4 is not None else np.nan,
                    "26Q3_영업이익부호불일치": num(r3, "flag_op_sign"),
                    "26Q4_영업이익부호불일치": num(r4, "flag_op_sign") if r4 is not None else np.nan,
                    "규칙": r3.get("rule"), "출처": "2g" if src == "2g" else "2d 읽기 전용", "매매신호": SIGNAL})
        rows.append(row)
    d = pd.DataFrame(rows)
    d["_g"] = d["집단"].map({x: i for i, x in enumerate(GROUP_ORDER)})
    d = d.sort_values(["_g", "기업"]).drop(columns="_g").reset_index(drop=True)
    return d, {"asof_2g": asof_2g, "run_2g": g["run_id"].iloc[0]}


def high_growth(d: pd.DataFrame) -> pd.DataFrame:
    p = "26Q3_"
    has_c = d[f"{p}매출컨센"].notna()
    ok = pd.Series(True, index=d.index)
    ok &= ~has_c | (d[f"{p}매출_컨센대비%"] > HG["매출_컨센대비_min"])
    has_oc = d[f"{p}영업이익컨센"].notna()
    ok &= ~has_oc | (d[f"{p}영업이익_컨센대비%"] > HG["영업이익_컨센대비_min"])
    ok &= d[f"{p}매출예상QoQ%"] >= HG["매출예상QoQ_min"]
    ok &= (d[f"{p}영업이익예상QoQ%"] >= HG["영업이익예상QoQ_min"]) | (d[f"{p}영업이익_상태"] == "흑자전환")
    sr, so = d[f"{p}매출_계절조정QoQ%p"], d[f"{p}영업이익_계절조정QoQ%p"]
    ok &= sr.isna() | (sr > HG["계절조정_min"])
    ok &= so.isna() | (so > HG["계절조정_min"])
    ok &= (d["매출_연속성장분기"] >= HG["연속성장_min"]) & (d["영업이익_연속성장분기"] >= HG["연속성장_min"])
    ok &= (d["자료단절"] != 1) & (d["4분기산출이상"] != 1)
    h = d[ok].copy()
    h["_t"] = (h[f"{p}영업이익_상태"] == "흑자전환").astype(int)
    h["_k"] = np.where(h["_t"] == 1, h[f"{p}영업이익_증가액"], h[f"{p}영업이익예상QoQ%"])
    return h.sort_values(["_t", "_k"], ascending=[False, False]).drop(columns=["_t", "_k"])


DESC = [
    ("종목코드", "종목코드", ""), ("기업", "기업명", ""),
    ("집단", "레이더39 / 정상 / 저커버리지 / 컨센서스 없음 (2g) / 2d 확대 (2d 기록 읽기 전용)", ""),
    ("업종", "K1 업종 묶음", ""), ("반도체장비", "1: 반도체 장비·부품 기업 (수주 영향 큰 업종)", "K9 와 같은 기준"),
    ("시장", "코스피 / 코스닥", ""), ("기준일", "예측 기준일 (이 날까지 공표된 자료만)", ""),
    ("26Q3_발표여부", "기준일에 26Q3 실적을 이미 발표했으면 '발표'", ""),
    ("26Q2_매출(실적)", "26Q2 매출 실적 (억원)", "보정 매출"), ("26Q2_영업이익(실적)", "26Q2 영업이익 실적 (억원)", ""),
    ("26Q2_영업이익률%", "26Q2 영업이익 / 매출", "x100"), ("26Q2_매출QoQ%", "26Q2 매출 / 26Q1 매출 - 1", "x100"),
    ("26Q2_영업이익QoQ%", "26Q2 영업이익 / 26Q1 영업이익 - 1", "26Q1 영업이익 > 0 일 때만"),
    ("매출_연속성장분기", "26Q2 부터 거꾸로 직전 분기보다 매출이 커진 분기가 이어진 수", "최대 8"),
    ("영업이익_연속성장분기", "26Q2 부터 거꾸로 직전 분기보다 영업이익(금액)이 커진 분기가 이어진 수", "최대 8"),
    ("Qn_매출예상(E5)", "매출 예상 (억원): SARIMA·ETS·Theta·B2(+M1) 기하평균", "2g 방법 v1"),
    ("Qn_매출컨센", "매출 컨센서스 (억원, 기준일 이하 최신)", "kr_revenue_consensus"),
    ("Qn_매출_컨센대비%", "매출예상 / 매출컨센 - 1", "x100"),
    ("Qn_매출예상QoQ%", "매출예상 / 직전 분기 매출 - 1 (26Q3: 26Q2 실적, 26Q4: 26Q3 예상)", "x100"),
    ("Qn_매출예상YoY%", "매출예상 / 작년 같은 분기 매출 - 1", "x100"),
    ("Qn_작년동기_매출QoQ%", "작년 같은 분기의 QoQ (26Q3: 25Q3/25Q2, 26Q4: 25Q4/25Q3)", "x100"),
    ("Qn_매출_계절조정QoQ%p", "매출예상QoQ% - 작년동기_매출QoQ%", "%p"),
    ("Qn_영업이익예상(OP_hat_E5)", "영업이익 예상 (억원): 규칙에 매출 E5 를 넣은 값", "R4 는 없음"),
    ("Qn_영업이익컨센", "영업이익 컨센서스 (억원)", "적재 표 없음, 비어 있음"),
    ("Qn_영업이익_컨센대비%", "영업이익예상 / 영업이익컨센 - 1", "비어 있음"),
    ("Qn_영업이익예상QoQ%", "영업이익예상 / 직전 분기 영업이익 - 1", "직전 영업이익 > 0 일 때만"),
    ("Qn_영업이익_증가액", "영업이익예상 - 직전 분기 영업이익 (억원)", ""),
    ("Qn_영업이익_상태", "흑자지속 / 흑자전환 / 적자전환 / 적자확대 / 적자축소 / 적자지속(변화 1% 안)", "직전·이번 부호"),
    ("Qn_작년동기_영업이익QoQ%", "작년 같은 분기 영업이익 QoQ", "작년 직전 분기 영업이익 > 0 일 때만"),
    ("Qn_영업이익_계절조정QoQ%p", "영업이익예상QoQ% - 작년동기_영업이익QoQ%", "%p"),
    ("Qn_영업이익률예상%", "영업이익예상 / 매출예상", "x100"),
    ("자료단절", "1: 기업 사건 뒤 새 기준 자료 12분기 미만 (K8c). 컨센서스 없으면 예상 열 비움", ""),
    ("4분기산출이상", "1: 4분기 매출 산출 이상 의심 (K8c)", ""),
    ("Qn_컨센괴리40", "1: |매출_컨센대비%| > 40", ""), ("Qn_B2급변", "1: B2 성장률이 시계열 세 모형 중앙값과 20%p 넘게 다름", ""),
    ("Qn_영업이익부호불일치", "1: OP_hat_E5·OP_hat_alt·OP_naive 부호가 서로 다름", ""),
    ("규칙", "영업이익 규칙 R1 회귀 / R2 원가율·판관비 / R3 영업비용률 / R4 예측 안 함", ""),
    ("출처", "2g / 2d 읽기 전용 (2d 는 26Q3 만, 26Q4 비어 있음)", ""), ("매매신호", "매매 신호 아님 (검증 전)", ""),
]


def main():
    d, meta = build()
    hg = high_growth(d)
    q3 = [c for c in d.columns if not c.startswith("26Q4_")]
    base = ["종목코드", "기업", "집단", "업종", "반도체장비", "시장", "기준일", "26Q3_발표여부"]
    q4 = base + [c for c in d.columns if c.startswith("26Q4_")] + ["자료단절", "4분기산출이상", "규칙", "출처", "매매신호"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{VERSION}_2026Q3_{stamp}"
    setting = [f"고성장 후보(26Q3) 조건 (설정값) - 2g 기준일 {meta['asof_2g']}, run {meta['run_2g']}",
               f"컨센서스 있음: 26Q3_매출_컨센대비% > {HG['매출_컨센대비_min']}, 영업이익컨센 있으면 26Q3_영업이익_컨센대비% > {HG['영업이익_컨센대비_min']}",
               f"공통: 26Q3_매출예상QoQ% >= {HG['매출예상QoQ_min']}, 26Q3_영업이익예상QoQ% >= {HG['영업이익예상QoQ_min']} (또는 영업이익_상태 '흑자전환')",
               f"매출_계절조정QoQ%p > {HG['계절조정_min']}, 영업이익_계절조정QoQ%p > {HG['계절조정_min']} (계산 가능할 때)",
               f"매출_연속성장분기 >= {HG['연속성장_min']}, 영업이익_연속성장분기 >= {HG['연속성장_min']}, 자료단절·4분기산출이상 표시 없음",
               "정렬: 흑자전환은 영업이익_증가액 순으로 맨 위, 나머지는 26Q3_영업이익예상QoQ% 내림차순",
               "영업이익 컨센서스는 적재 표가 없어 비어 있음 (조건에서 빠짐). 매매 신호 아님(검증 전)."]
    desc = pd.DataFrame(DESC, columns=["열", "설명", "계산식·비고"])
    desc["열"] = desc["열"].str.replace("Qn_", "26Q3_ / 26Q4_ ")
    with pd.ExcelWriter(OUT / f"{stem}.xlsx") as w:
        pd.DataFrame({"설정": setting}).to_excel(w, sheet_name="고성장 후보(26Q3)", index=False)
        hg[q3].round(2).to_excel(w, sheet_name="고성장 후보(26Q3)", index=False, startrow=len(setting) + 2)
        d[q3].round(2).to_excel(w, sheet_name="판단(26Q3)", index=False)
        d[q4].round(2).to_excel(w, sheet_name="다음 분기(26Q4)", index=False)
        d.round(2).to_excel(w, sheet_name="전체", index=False)
        desc.to_excel(w, sheet_name="변수설명", index=False)
    for nm, df in (("high_growth_26Q3", hg[q3]), ("judge_26Q3", d[q3]), ("next_26Q4", d[q4]), ("all", d),
                   ("columns", desc)):
        df.to_csv(OUT / f"{stem}_{nm}.csv", index=False, encoding="utf-8-sig")
    (OUT / f"{stem}_high_growth_settings.txt").write_text("\n".join(setting), encoding="utf-8")
    s = {"rows": len(d), "by_group": d["집단"].value_counts().to_dict(), "high_growth": len(hg),
         "high_growth_by_group": hg["집단"].value_counts().to_dict(),
         "high_growth_semi": int(hg["반도체장비"].sum()),
         "high_growth_turnaround": int((hg["26Q3_영업이익_상태"] == "흑자전환").sum()),
         "op_state_26Q3": d["26Q3_영업이익_상태"].value_counts().to_dict(),
         "cons_rows_26Q3": int(d["26Q3_매출컨센"].notna().sum()), "file": str(OUT / f"{stem}.xlsx")}
    print(s)
    return d, hg, s


if __name__ == "__main__":
    main()
