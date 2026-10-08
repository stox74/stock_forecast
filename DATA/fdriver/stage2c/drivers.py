# -*- coding: utf-8 -*-
"""2c 드라이버 시계열 (지시문 작업 2 (d), 명세서 2c v2 4절). 매핑 엑셀 v2 를 읽어 만든다. DB 쓰기 없음.

코드 해석 (엑셀 '코드' 시트, 사용 = Y 만)
- 6자리 전개 = Y: 수출 테이블에 있는 그 4자리 아래 6자리 코드 전체 (leaf). 6자리가 하나도 없으면 4자리 그대로 쓰고
  'no_children_4digit' 로 표시한다.
- 전개 = 공란: 적힌 코드 그대로. 수출 테이블에 없으면 그 코드만 뺀다 ('missing').
- 삼성전기 카메라모듈 (852589): HS2022 개정 전 852580 (~2021-12) + 개정 후 852581~852589 (2022-01~) 연결.
  판정 (지시문 (d)3, 상관 보지 않음): 2014-01 이후 빈 달이 없고 개정 전후 12개월 평균 비가 1/3~3 이면 사용.

세그먼트 월 값 (2c 작업 2 에서 정한 자료 규칙, 결과 보기 전)
- 같은 세그먼트 leaf 코드의 월 수출액 합. 기간 안에서 어떤 코드의 값이 없는 달은 0 으로 본다 (수출 실적 없음:
  선박·특수 코드는 띄엄띄엄 있다). 코드별 첫 달이 늦은 것은 HS 개정 신설 코드라 그대로 합한다.
- 세그먼트 끝 달 = leaf 코드 마지막 달의 최솟값. 수집이 멈춘 코드(2025-07, 2026-01 등)가 끝난 뒤를 0 으로 채우면
  가짜 감소가 생기므로 자른다. 단 마지막 달이 RETIRED_BY 이전인 코드는 HS 개정으로 없어진 코드로 보고 끝 달 계산에서 뺀다.
- available_at 은 그 달 leaf 값 중 가장 늦은 것, 등급은 가장 낮은 것.

드라이버
- 세그먼트 가중치가 없는 기업: 사용 코드 전체를 한 세그먼트로 합산 (key = 'all').
- 가중치가 있는 기업: 세그먼트별 시계열 (key = 세그먼트 이름) + weights(j, t) (segments.weight_fn).
  드라이버 있음 = N 인 세그먼트와 남는 코드가 없는 세그먼트는 뺀다.
- 비수출: 모두투어 출국자(tourism D:100), GKL·파라다이스·호텔신라 방한객(tourism E:TOTAL), SK하이닉스 Nanya 월매출
  (tw_revenue 2408, 명세서 4절 '모두 동행' 규칙에 따라 lag 0).
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import db
from ..adapters import read_series, read_trade
from ..adapters._common import finalize
from ..nowcast.features import DriverSpec
from ..panel import _notes, _worst
from .mapping import read_mapping

RETIRED_BY = pd.Timestamp("2023-12-31")
CAMERA = {"entity_id": "009150", "segment": "카메라모듈", "code": "852589", "pre": "852580",
          "post_prefix": "85258", "revision": pd.Timestamp("2022-01-31"), "check_from": pd.Timestamp("2014-01-31")}
LS_TRANSFORMER = {"entity_id": "010120", "segment": "변압기(자회사)", "from_quarter": pd.Timestamp("2024-12-31")}
NON_EXPORT = {
    "080160": [("M1:outbound", "tourism:D:100", "tourism", "D:100")],
    "114090": [("M1:inbound", "tourism:E:TOTAL", "tourism", "E:TOTAL")],
    "034230": [("M1:inbound", "tourism:E:TOTAL", "tourism", "E:TOTAL")],
    "008770": [("M1:inbound", "tourism:E:TOTAL", "tourism", "E:TOTAL")],
}
EXTRA_SERIES = {"000660": [("M1:nanya", "tw_revenue:2408", "tw_revenue", "2408")]}


# ---------------------------------------------------------------- 코드 목록
def code_inventory() -> pd.DataFrame:
    """수출 테이블(expDlr)의 코드별 첫 달, 마지막 달, 월 수."""
    d = db.read_sql("SELECT root_hs_code code, MIN(date) first, MAX(date) last, COUNT(DISTINCT date) n_months "
                    "FROM korea_monthly_trade_data WHERE indicator = 'expDlr' GROUP BY root_hs_code")
    d["code"] = d["code"].astype(str)
    d["first"] = pd.to_datetime(d["first"]) + pd.offsets.MonthEnd(0)
    d["last"] = pd.to_datetime(d["last"]) + pd.offsets.MonthEnd(0)
    return d.set_index("code")


def resolve_codes(mapping: Optional[Dict] = None, inv: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """엑셀 코드(사용 = Y) -> leaf 코드. 한 줄 = (기업, 세그먼트, 엑셀 코드, leaf 코드)."""
    mapping = mapping or read_mapping()
    inv = code_inventory() if inv is None else inv
    six = inv[inv.index.str.len() == 6]
    rows = []
    for r in mapping["codes"][mapping["codes"]["use"]].itertuples():
        base = {"entity_id": r.entity_id, "segment": r.segment, "hs": r.hs, "expand6": r.expand6,
                "needs_db_check": r.needs_db_check, "hs_in_db": r.hs in inv.index}
        if r.entity_id == CAMERA["entity_id"] and r.hs == CAMERA["code"]:
            pre = [CAMERA["pre"]] if CAMERA["pre"] in inv.index else []
            post = sorted(c for c in six.index if c.startswith(CAMERA["post_prefix"]) and c != CAMERA["pre"])
            for c in pre + post:
                rows.append({**base, "leaf": c, "status": "camera_splice_pre" if c in pre else "camera_splice_post"})
            continue
        if r.expand6:
            kids = sorted(c for c in six.index if c.startswith(r.hs)) if len(r.hs) == 4 else []
            if kids:
                rows += [{**base, "leaf": c, "status": "expanded"} for c in kids]
            elif r.hs in inv.index:
                rows.append({**base, "leaf": r.hs, "status": "no_children_4digit"})
            else:
                rows.append({**base, "leaf": None, "status": "missing"})
        else:
            rows.append({**base, "leaf": r.hs if r.hs in inv.index else None,
                         "status": "listed" if r.hs in inv.index else "missing"})
    out = pd.DataFrame(rows)
    li = out["leaf"].map(lambda c: inv.loc[c] if c in inv.index else None)
    out["leaf_first"] = [x["first"] if x is not None else pd.NaT for x in li]
    out["leaf_last"] = [x["last"] if x is not None else pd.NaT for x in li]
    out["leaf_months"] = [x["n_months"] if x is not None else 0 for x in li]
    return out


# ---------------------------------------------------------------- 2c 작업 2b 규칙 (명세서 2c v3 4절 결정 1·2·3)
DATA_END = pd.Timestamp("2026-08-31")      # 2b 수집 끝 달
EXPAND_MIN_RATIO = 0.9


def expansion_ratio(hs: str, kids: List[str]) -> Optional[float]:
    """6자리 합 / 4자리 비율의 중앙값 (4자리 값이 있는 달). 4자리나 6자리가 없으면 None."""
    four = read_trade([hs], items=["expDlr"]).set_index("period_end")["value"]
    if four.empty or not kids:
        return None
    ks = read_trade(kids, items=["expDlr"]).groupby("period_end")["value"].sum().reindex(four.index).fillna(0.0)
    r = (ks / four).replace([np.inf, -np.inf], np.nan).dropna()
    return float(r.median()) if len(r) else None


def apply_2b_rules(resolved: pd.DataFrame, inv: pd.DataFrame, end: pd.Timestamp = DATA_END):
    """쓸 코드 다시 정하기. 반환 (새 resolved, 전개 비율 표).

    1. 전개 비율: 6자리 전개 = Y 인 4자리마다 비율 중앙값. 0.9 이상 -> 6자리 전개. 0.9 미만이고 4자리가 end 까지 있음 ->
       4자리로 바꿈 ('four_digit_switch'). 0.9 미만이고 4자리도 끊김 -> 6자리 유지, expand_short = True.
    2. 끊긴 코드: leaf 마지막 달 < end (RETIRED_BY 이전 종료 코드 제외) -> 그 leaf 를 뺌 ('cut_dropped', leaf = None).
    3. LS ELECTRIC 변압기(자회사) 세그먼트는 쓰지 않음 ('ls_transformer_excluded').
    """
    r = resolved.copy()
    r["expand_short"] = False
    r["rule_note"] = ""
    ratios = []
    for hs in sorted(r.loc[r["expand6"] & (r["hs"].str.len() == 4), "hs"].unique()):
        g = r[r["hs"] == hs]
        kids = sorted(g.loc[g["status"] == "expanded", "leaf"].dropna().unique())
        ratio = expansion_ratio(hs, kids)
        four_last = inv.loc[hs, "last"] if hs in inv.index else pd.NaT
        decision = "expanded"
        if ratio is not None and ratio < EXPAND_MIN_RATIO:
            decision = "four_digit_switch" if (pd.notna(four_last) and four_last >= end) else "expand_short"
        ratios.append({"hs": hs, "entities": ",".join(sorted(set(g["entity_id"]))), "children": ",".join(kids),
                       "n_children": len(kids), "ratio_median": ratio,
                       "four_last": four_last.strftime("%Y-%m") if pd.notna(four_last) else None, "decision": decision})
        if decision == "four_digit_switch":
            keep = []
            for (eid, seg), gg in g.groupby(["entity_id", "segment"]):
                row = gg.iloc[0].to_dict()
                row.update(leaf=hs, status="four_digit_switch", rule_note=f"비율 {ratio:.3f} < 0.9, 4자리 사용")
                keep.append(row)
            r = pd.concat([r[r["hs"] != hs], pd.DataFrame(keep)], ignore_index=True)
        elif decision == "expand_short":
            r.loc[r["hs"] == hs, "expand_short"] = True
            r.loc[r["hs"] == hs, "rule_note"] = f"전개 부족 (비율 {ratio:.3f}, 4자리도 끊김)"
    li = r["leaf"].map(lambda c: inv.loc[c] if isinstance(c, str) and c in inv.index else None)
    r["leaf_first"] = [x["first"] if x is not None else pd.NaT for x in li]
    r["leaf_last"] = [x["last"] if x is not None else pd.NaT for x in li]
    r["leaf_months"] = [x["n_months"] if x is not None else 0 for x in li]
    cut = r["leaf"].notna() & (r["leaf_last"] < end) & (r["leaf_last"] > RETIRED_BY) & ~r["status"].eq("camera_splice_pre")
    r.loc[cut, "rule_note"] = [f"끊긴 코드 (마지막 {d:%Y-%m}) 제외" for d in r.loc[cut, "leaf_last"]]
    r.loc[cut, "status"] = "cut_dropped"
    r["dropped_leaf"] = np.where(cut, r["leaf"], None)
    r.loc[cut, "leaf"] = None
    ls = (r["entity_id"] == LS_TRANSFORMER["entity_id"]) & (r["segment"] == LS_TRANSFORMER["segment"])
    r.loc[ls, "dropped_leaf"] = r.loc[ls, "leaf"]
    r.loc[ls, ["leaf", "status", "rule_note"]] = [None, "ls_transformer_excluded", "명세서 v3 결정 3: 변압기(자회사) 쓰지 않음"]
    return r.sort_values(["entity_id", "segment", "hs", "leaf"], na_position="last").reset_index(drop=True), pd.DataFrame(ratios)


# ---------------------------------------------------------------- 세그먼트 시계열
DATA_START = pd.Timestamp("2007-01-31")    # 드라이버 자료 시작 (수집 범위 2007-01~). 2b 재수집 때 수집기가 YoY 계산용으로
                                           # 2006년 12개월을 함께 넣어, 일부 코드만 있는 2006 달이 생기지 않게 자른다.


def _monthly(codes: List[str]) -> pd.DataFrame:
    return read_trade(codes, items=["expDlr"], start=DATA_START) if codes else pd.DataFrame()


def camera_splice(raw: pd.DataFrame) -> pd.DataFrame:
    """852580 (개정 전, revision 전 달까지) + 852581~852589 (revision 달부터) 만 남긴다."""
    pre = (raw["key"] == CAMERA["pre"]) & (raw["period_end"] < CAMERA["revision"])
    post = raw["key"].str.startswith(CAMERA["post_prefix"]) & (raw["key"] != CAMERA["pre"]) & (raw["period_end"] >= CAMERA["revision"])
    return raw[pre | post]


def segment_series(leaves: pd.DataFrame, key: str, raw: Optional[pd.DataFrame] = None) -> Dict:
    """leaf 코드 표 -> 월 합계 (COMMON_COLUMNS) 와 진단 정보."""
    codes = sorted(set(leaves["leaf"].dropna()))
    raw = _monthly(codes) if raw is None else raw[raw["key"].isin(codes)]
    if raw.empty:
        return {"df": finalize(pd.DataFrame()), "codes": codes, "end": None, "end_by": None, "zero_filled": 0}
    if (leaves["status"].str.startswith("camera")).any():
        raw = camera_splice(raw)
        last = {CAMERA["pre"]: CAMERA["revision"] - pd.offsets.MonthEnd(1)}
    else:
        last = {}
    lastm = raw.groupby("key")["period_end"].max().to_dict()
    lastm.update(last)
    active = {c: v for c, v in lastm.items() if v > RETIRED_BY}
    end = min(active.values()) if active else max(lastm.values())
    end_by = sorted(c for c, v in active.items() if v == end)
    start = raw["period_end"].min()
    months = pd.date_range(start, end, freq="ME")
    piv = raw.pivot_table(index="period_end", columns="key", values="value", aggfunc="sum").reindex(months)
    av = raw.pivot_table(index="period_end", columns="key", values="available_at", aggfunc="max").reindex(months)
    first = raw.groupby("key")["period_end"].min()
    # 코드의 첫 달 이후 기간에서 값이 없는 달 수 (0 으로 채운 달)
    zero = 0
    for c in piv.columns:
        span = piv.index >= first[c]
        if c in last:
            span &= piv.index <= last[c]
        zero += int(piv.loc[span, c].isna().sum())
    val = piv.fillna(0.0).sum(axis=1)
    has = piv.notna().any(axis=1)
    q = raw.groupby("period_end")["asof_quality"].agg(_worst).reindex(months)
    note = raw.groupby("period_end")["asof_note"].agg(_notes).reindex(months)
    df = pd.DataFrame({"key": key, "period_end": months, "freq": "M", "item": "expDlr_sum", "value": val.values,
                       "unit": "USD", "currency": "USD", "available_at": av.max(axis=1).values,
                       "asof_quality": q.values, "asof_note": note.fillna("").values,
                       "source": "korea_monthly_trade_data"})
    df = df[has.values]
    return {"df": finalize(df), "codes": codes, "start": start, "end": end, "end_by": end_by, "zero_filled": zero,
            "n_months": len(df)}


# ---------------------------------------------------------------- 점검
def expansion_check(resolved: pd.DataFrame) -> pd.DataFrame:
    """6자리 전개 코드: 4자리 값과 6자리 합을 4자리가 있는 달에서 비교 (차이 1% 넘는 달 수)."""
    rows = []
    for hs, g in resolved[resolved["status"] == "expanded"].groupby("hs"):
        kids = sorted(g["leaf"].unique())
        four = read_trade([hs], items=["expDlr"]).set_index("period_end")["value"]
        if four.empty:
            rows.append({"hs": hs, "entities": ",".join(sorted(g["entity_id"].unique())), "children": ",".join(kids),
                         "n_children": len(kids), "overlap_months": 0, "note": "4자리 코드가 DB 에 없어 비교 불가"})
            continue
        k = read_trade(kids, items=["expDlr"])
        ksum = k.groupby("period_end")["value"].sum().reindex(four.index).fillna(0.0)
        rel = (ksum - four) / four
        rows.append({"hs": hs, "entities": ",".join(sorted(g["entity_id"].unique())), "children": ",".join(kids),
                     "n_children": len(kids), "overlap_months": int(len(four)),
                     "first": four.index.min().strftime("%Y-%m"), "last": four.index.max().strftime("%Y-%m"),
                     "n_diff_gt_1pct": int((rel.abs() > 0.01).sum()),
                     "median_ratio_6_to_4": float((ksum / four).median()),
                     "min_ratio": float((ksum / four).min()), "max_ratio": float((ksum / four).max())})
    return pd.DataFrame(rows)


def camera_check() -> Dict:
    """카메라모듈 연결 판정 (지시문 (d)3 고정 기준)."""
    raw = read_trade([CAMERA["pre"]] + [c for c in code_inventory().index
                                        if len(c) == 6 and c.startswith(CAMERA["post_prefix"]) and c != CAMERA["pre"]],
                     items=["expDlr"])
    s = camera_splice(raw).groupby("period_end")["value"].sum()
    months = pd.date_range(CAMERA["check_from"], s.index.max(), freq="ME")
    gaps = [m.strftime("%Y-%m") for m in months if m not in s.index]
    rev = CAMERA["revision"]
    pre = s[(s.index < rev) & (s.index >= rev - pd.DateOffset(months=12))].mean()
    post = s[(s.index >= rev) & (s.index < rev + pd.DateOffset(months=12))].mean()
    ratio = post / pre
    use = (not gaps) and (1 / 3 < ratio < 3)
    return {"post_codes": sorted(raw.loc[raw["key"] != CAMERA["pre"], "key"].unique()), "first": s.index.min(),
            "last": s.index.max(), "gaps_since_2014": gaps, "pre12_mean": pre, "post12_mean": post,
            "ratio_post_pre": ratio, "use": use, "series": s}


# ---------------------------------------------------------------- 기업별 드라이버
def build_entity(entity_id: str, mapping: Dict, resolved: pd.DataFrame, camera_use: bool = True) -> Dict:
    """기업 하나의 드라이버 자료. 반환: segments {key: segment_series 결과}, weighted, dropped(드라이버 없음 처리 사유)."""
    w = mapping["weights"][mapping["weights"]["entity_id"] == entity_id]
    res = resolved[(resolved["entity_id"] == entity_id) & resolved["leaf"].notna()]
    out = {"entity_id": entity_id, "weighted": not w.empty, "segments": {}, "dropped": {}}
    if res.empty:
        return out
    raw = _monthly(sorted(res["leaf"].unique()))
    if w.empty:
        out["segments"]["all"] = segment_series(res, "all", raw)
        return out
    for seg in w["segment"]:
        row = w[w["segment"] == seg].iloc[0]
        if not row["has_driver"]:
            out["dropped"][seg] = "드라이버 있음 = N"
            continue
        leaves = res[res["segment"] == seg]
        if entity_id == CAMERA["entity_id"] and seg == CAMERA["segment"] and not camera_use:
            out["dropped"][seg] = "카메라모듈 연결 판정 실패 -> 드라이버 없음"
            continue
        if leaves.empty:
            out["dropped"][seg] = "남는 코드 없음 -> 드라이버 없음"
            continue
        out["segments"][seg] = segment_series(leaves, seg, raw)
    return out


def driver_frame(built: Dict) -> pd.DataFrame:
    parts = [s["df"] for s in built["segments"].values() if len(s["df"])]
    return pd.concat(parts, ignore_index=True) if parts else finalize(pd.DataFrame())


def specs_for(entity_id: str, built: Optional[Dict], weight_fn=None) -> List[DriverSpec]:
    """nowcast 용 DriverSpec 목록. 수출 드라이버는 model_id 'M1:export' (M4 는 'M4:export')."""
    specs = []
    if built is not None and built["segments"]:
        frame = driver_frame(built)
        wf = weight_fn if built["weighted"] else None
        specs.append(DriverSpec("M1:export", f"trade2c:{entity_id}", "M", 0, (lambda f=frame: f.copy()), weights=wf))
    for mid, key, fam, sk in NON_EXPORT.get(entity_id, []) + EXTRA_SERIES.get(entity_id, []):
        specs.append(DriverSpec(mid, key, "M", 0, (lambda fam=fam, sk=sk: read_series(fam, [sk]))))
    return specs
