# -*- coding: utf-8 -*-
"""레이더 출력 v2: 출력 변수명, 구성 불일치(E_spread), 판단 (fdriver_radar_coldesc_instruction_v2).

- build.build() 의 내부 표(내부 이름, DB·실전 기록과 같은 이름)를 출력 표로 바꾼다. 예측 계산은 바꾸지 않는다.
  출력 이름과 내부 이름의 대응은 coldesc.COLDESC 의 'internal' 하나로 관리한다.
- 기준값은 고정 상수 (결과를 보고 바꾸지 않음): GAP_THRESHOLD_PCT = 5.0, SPREAD_THRESHOLD_PCT = 15.0.
- 방향(dir_*)은 '+' / '-' / 공란 (0 이나 결측은 공란), 일치(agree_*)는 '일치' / '불일치' / 공란.
- 구성 불일치: E 의 세 예측(드라이버 요소 M1 자리, B2, TS) 각각 dev_k = g_k - (나머지 둘의 평균).
  spread_E_pct = max_k |exp(dev_k) - 1| x 100, spread_E_from = argmax_k |dev_k| (드라이버 요소는 M1_model 의 모형명 M1/M2/M3).
  flag_E_spread = spread_E_pct >= 15 이면 '불일치(<from>)'. 구성 예측이 하나라도 없으면 '구성 부족', 대조군은 공란.
- 판단 규칙 (위에서부터 처음 걸리는 것): decide().
"""
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import coldesc
from .universe import CONTROLS

GAP_THRESHOLD_PCT = 5.0
SPREAD_THRESHOLD_PCT = 15.0
E_BIAS_NOTE = ("E 는 2b 검증에서 실제보다 약 -1.6% (중앙값) 낮게 나오는 경향이 있음. 보정하지 않음 "
               "(radar.scorecard 6_e_bias 에 분기별로 기록, 실전 3개 분기 후 재검토)")
DIR_FALLBACK = "방향 대체(shock_dev)"

DECISION_ORDER = ["후보", "검토", "관찰", "중립", "보류"]
JUDGE_COLUMNS = ["decision", "decision_reason", "entity_id", "name", "sector", "grade", "announce_date", "announce_basis",
                 "jo_E", "jo_cons", "gap_E_vs_cons_pct", "dir_E_vs_cons", "dir_drv_vs_trend", "flag_dir_fallback", "z_drv_vs_trend",
                 "flag_shock", "flag_E_spread", "flag_q4", "flag_drv_missing", "flag_event", "flag_nowcast"]
RULE_SUMMARY = ("판단 규칙 (위에서부터 처음 걸리는 것): 1 C등급 -> 보류(등급) / 2 구성 불일치 + 충격 -> 검토(충격+불일치) / "
                "3 구성 불일치 -> 보류(불일치) / 4 컨센서스 대비 |차이| >= {gap}% 이고 드라이버 방향 = 컨센서스 대비 방향 -> 후보(상향·하향) / "
                "5 같은 차이에서 방향 불일치 -> 관찰(방향 불일치) / 6 |차이| < {gap}% -> 중립 / "
                "7 컨센서스 없음, E·드라이버 추세 방향 일치 -> 관찰(컨센서스 없음) / 8 그 밖 -> 중립. "
                "구성 불일치 기준 {spread}%.")


# ---------------------------------------------------------------- 작은 함수
def sign_label(x) -> str:
    if x is None or pd.isna(x) or x == 0:
        return ""
    return "+" if x > 0 else "-"


def agree_label(a: str, b: str) -> str:
    if not a or not b:
        return ""
    return "일치" if a == b else "불일치"


def model_short(model_id) -> str:
    return "" if model_id is None or pd.isna(model_id) or not model_id else str(model_id).split(":")[0]


def spread(g_drv, g_b2, g_ts, drv_model, control: bool) -> Dict:
    """구성 불일치. 반환 spread_E_pct, spread_E_from, flag_E_spread."""
    if control:
        return {"spread_E_pct": np.nan, "spread_E_from": "", "flag_E_spread": ""}
    g = {model_short(drv_model) or "M1": g_drv, "B2": g_b2, "TS": g_ts}
    if any(v is None or pd.isna(v) for v in g.values()):
        return {"spread_E_pct": np.nan, "spread_E_from": "", "flag_E_spread": "구성 부족"}
    dev = {k: v - np.mean([w for kk, w in g.items() if kk != k]) for k, v in g.items()}
    k = max(dev, key=lambda kk: abs(dev[kk]))
    pct = float(max(abs(np.exp(v) - 1) for v in dev.values()) * 100)
    return {"spread_E_pct": pct, "spread_E_from": k,
            "flag_E_spread": f"불일치({k})" if pct >= SPREAD_THRESHOLD_PCT else ""}


def shock_flag(alert) -> str:
    a = "" if alert is None or pd.isna(alert) else str(alert)
    return "충격(상향)" if a.startswith("경보(상)") else ("충격(하향)" if a.startswith("경보(하)") else "")


def split_warnings(w) -> Dict[str, str]:
    parts = [] if w is None or pd.isna(w) or not str(w) else str(w).split(";")
    out = {"flag_q4": "", "flag_drv_missing": "", "flag_event": "", "flag_nowcast": ""}
    for p in parts:
        if p == "Q4차감":
            out["flag_q4"] = "4분기 차감"
        elif p.startswith("드라이버결측"):
            out["flag_drv_missing"] = "드라이버 결측"
        elif p.startswith("드라이버부분"):
            out["flag_drv_missing"] = p.replace("드라이버부분", "드라이버 부분")
        elif p == "기업사건":
            out["flag_event"] = "기업 사건"
        elif p.startswith("nowcast 없음"):
            out["flag_nowcast"] = p
    return out


def announce_basis(kind) -> str:
    k = "" if kind is None or pd.isna(kind) else str(kind)
    return "확정" if k == "실제" else k


# ---------------------------------------------------------------- 판단
def decide(r: Dict) -> Tuple[str, str]:
    """판단 규칙 1~8. r 은 출력 이름의 한 행."""
    grade = r.get("grade") or ""
    gap = r.get("gap_E_vs_cons_pct")
    has_cons = gap is not None and pd.notna(gap)
    spread_on = str(r.get("flag_E_spread") or "").startswith("불일치")
    shock = str(r.get("flag_shock") or "")
    head = [f"{grade}등급"] + ([f"갭 {gap:+.1f}%"] if has_cons else [])
    if grade == "C":
        return "보류(등급)", ", ".join(head + ["C등급은 판단 보류"] + ([f"구성 {r['flag_E_spread']}"] if spread_on else []))
    if spread_on and shock:
        return "검토(충격+불일치)", ", ".join(head + [f"구성 {r['flag_E_spread']}", shock])
    if spread_on:
        return "보류(불일치)", ", ".join(head + [f"구성 {r['flag_E_spread']}"])
    if has_cons and abs(gap) >= GAP_THRESHOLD_PCT:
        ag = r.get("agree_drv_cons") or ""
        if ag == "일치":
            d = "상향" if r.get("dir_E_vs_cons") == "+" else "하향"
            return f"후보({d})" + ("+충격" if shock else ""), ", ".join(head + ["드라이버 방향 일치"] + ([shock] if shock else []))
        if ag == "불일치":
            return "관찰(방향 불일치)", ", ".join(head + ["드라이버 방향 불일치"])
        return "중립", ", ".join(head + ["드라이버 방향 없음"])
    if has_cons:
        return "중립", ", ".join(head + [f"|갭| < {GAP_THRESHOLD_PCT:g}%"])
    if (r.get("agree_E_drv_vs_trend") or "") == "일치":
        d = "상향" if r.get("dir_E_vs_trend") == "+" else "하향"
        return f"관찰(컨센서스 없음, {d})", ", ".join(head + ["컨센서스 없음", "E·드라이버 추세 방향 일치"])
    why = "E 없음" if r.get("rev_E") is None or pd.isna(r.get("rev_E")) else "컨센서스 없음, 추세 방향 불일치·없음"
    return "중립", ", ".join(head + [why])


def decision_rank(d: str) -> int:
    for i, k in enumerate(DECISION_ORDER):
        if d.startswith(k):
            return i
    return len(DECISION_ORDER)


# ---------------------------------------------------------------- 표 변환
def to_output(tab: pd.DataFrame) -> pd.DataFrame:
    """내부 표 -> 출력 표 (coldesc.OUTPUT_COLUMNS 순서)."""
    t = tab.copy()
    num = lambda c: pd.to_numeric(t[c], errors="coerce")
    o = pd.DataFrame(index=t.index)
    for out, d in coldesc.COLDESC.items():
        src = d.get("internal")
        if src and src in t.columns and d.get("transform") is None:
            o[out] = t[src]
    o["jo_E"], o["jo_M4"], o["jo_M1"], o["jo_B2"], o["jo_TS"], o["jo_cons"] = (
        num("rev_E") / 1e12, num("rev_M4") / 1e12, num("rev_M1") / 1e12, num("rev_B2") / 1e12, num("rev_TS") / 1e12,
        num("consensus") / 1e12)
    o["gap_E_vs_cons_pct"] = num("gap_E") * 100
    o["gap_M4_vs_cons_pct"] = num("gap_M4") * 100
    o["dir_E_vs_cons"] = o["gap_E_vs_cons_pct"].map(sign_label)
    o["dir_E_vs_trend"] = (num("g_E") - num("g_B2")).map(sign_label)
    # z 가 공란이고 shock_dev 가 있으면 shock_dev 부호로 대신 (분모 sigma 가 없을 때. 같은 M4 - B2 의 부호)
    z, dev = num("nsur_drv"), num("shock_x")
    fb = z.isna() & dev.notna()
    o["dir_drv_vs_trend"] = z.where(~fb, dev).map(sign_label)
    o["flag_dir_fallback"] = np.where(fb, DIR_FALLBACK, "")
    o["agree_E_drv_vs_trend"] = [agree_label(a, b) for a, b in zip(o["dir_E_vs_trend"], o["dir_drv_vs_trend"])]
    o["agree_drv_cons"] = [agree_label(a, b) for a, b in zip(o["dir_drv_vs_trend"], o["dir_E_vs_cons"])]
    o["drv_3m_yoy_pct"] = (np.exp(num("drv_3m_yoy")) - 1) * 100
    o["announce_basis"] = t["announce_kind"].map(announce_basis)
    o["flag_shock"] = t["shock_alert"].map(shock_flag)
    sp = [spread(a, b, c, m, e in CONTROLS) for a, b, c, m, e in
          zip(num("g_M1"), num("g_B2"), num("g_TS"), t["M1_model"], t["entity_id"])]
    o = o.join(pd.DataFrame(sp, index=t.index))
    o = o.join(pd.DataFrame([split_warnings(w) for w in t["warnings"]], index=t.index))
    dec = [decide(r) for r in o.to_dict("records")]
    o["decision"] = [d for d, _ in dec]
    o["decision_reason"] = [w for _, w in dec]
    miss = [c for c in coldesc.OUTPUT_COLUMNS if c not in o.columns]
    if miss:
        raise KeyError(f"출력 열을 만들지 못함: {miss}")
    return o[coldesc.OUTPUT_COLUMNS].reset_index(drop=True)


def judgement(out: pd.DataFrame) -> pd.DataFrame:
    """판단 시트: 종목당 한 행, 후보 -> 검토 -> 관찰 -> 중립 -> 보류, 같은 판단 안에서는 |갭| 큰 순."""
    j = out[JUDGE_COLUMNS].copy()
    j["_r"] = j["decision"].map(decision_rank)
    j["_g"] = -pd.to_numeric(j["gap_E_vs_cons_pct"], errors="coerce").abs().fillna(-1)
    return j.sort_values(["_r", "_g", "name"]).drop(columns=["_r", "_g"]).reset_index(drop=True)


def rule_summary() -> str:
    return RULE_SUMMARY.format(gap=f"{GAP_THRESHOLD_PCT:g}", spread=f"{SPREAD_THRESHOLD_PCT:g}") + " " + E_BIAS_NOTE


def decision_counts(j: pd.DataFrame) -> pd.Series:
    return j["decision"].value_counts().reindex(sorted(j["decision"].unique(), key=lambda d: (decision_rank(d), d)))
