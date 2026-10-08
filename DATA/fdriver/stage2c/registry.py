# -*- coding: utf-8 -*-
"""2c 선정 기업의 DriverSpec 을 만들어 nowcast 의 DRIVER_SPECS 에 등록한다 (2a 시범 기업 항목은 건드리지 않음).

build() 결과
- resolved: 엑셀 코드 -> leaf 코드 표 (drivers.resolve_codes)
- camera: 삼성전기 카메라모듈 연결 판정 (drivers.camera_check)
- built: 기업별 세그먼트 시계열 (drivers.build_entity)
- filled: 연도별 사업부 비중 (segments.fill_years)
- specs: {entity_id: [DriverSpec, ...]}
가중치가 있는 기업 중 드라이버 세그먼트가 하나뿐이면 가중평균 = 그 세그먼트 성장률이므로 가중치를 쓰지 않는다
(사업보고서가 없는 2012 이전 분기도 계산됨). LS ELECTRIC 은 변압기(자회사)가 2024Q4 부터라 weight_fn 으로 처리한다.
"""
from typing import Dict, Optional

from ..nowcast import features as ft
from . import drivers as dv
from . import segments as sg
from .mapping import read_mapping

_CACHE: Dict = {}
RULES = ("step2", "2b", "2b2")
# step2: 작업 2 정의 / 2b: 작업 2b 규칙 (끊긴 코드 제외, 전개 비율 0.9, LS 변압기 제외)
# 2b2: 명세서 2c v4 자료 규칙 (세그먼트 끝 달 = 수집기 마지막 수집 달, 수집기 목록 코드는 행 없는 달 0,
#      끊긴 코드 제외는 목록 밖 코드에만)


class NoDriverSegments(RuntimeError):
    """2b 규칙 적용 후 수출 드라이버 세그먼트가 모두 없어진 기업 (지시문 2b (c)1: 보고하고 멈춤)."""


def build(refresh: bool = False, rules: str = "2b2") -> Dict:
    if rules not in RULES:
        raise ValueError(rules)
    if _CACHE.get("rules") == rules and not refresh:
        return _CACHE
    _CACHE.clear()
    m = read_mapping()
    inv = dv.code_inventory()
    resolved_step2 = dv.resolve_codes(m, inv)
    ratios = None
    resolved = resolved_step2
    fill_to, fill_codes = None, frozenset()
    if rules == "2b":
        resolved, ratios = dv.apply_2b_rules(resolved_step2, inv)
    elif rules == "2b2":
        fill_codes = dv.collector_codes()
        fill_to = inv["last"].max()                  # 수집기의 마지막 수집 달
        resolved, ratios = dv.apply_2b_rules(resolved_step2, inv, end=fill_to, collector=fill_codes)
    cam = dv.camera_check()
    ids = list(m["companies"].loc[m["companies"]["included"], "entity_id"])
    w = m["weights"]
    shares, rows = sg.extract_all(list(dict.fromkeys(w["entity_id"])))
    filled = sg.fill_years(shares)
    built, specs, wfns = {}, {}, {}
    for e in ids:
        b = dv.build_entity(e, m, resolved, camera_use=cam["use"], fill_to=fill_to, fill_codes=fill_codes)
        built[e] = b
        had_export = resolved_step2["entity_id"].eq(e).any()
        if rules in ("2b", "2b2") and had_export and not b["segments"]:
            raise NoDriverSegments(f"{e}: 2b 규칙 후 드라이버 세그먼트가 없음 ({b['dropped']})")
        wf = None
        if b["weighted"] and len(b["segments"]) >= 2:
            we = w[w["entity_id"] == e]
            start = ({dv.LS_TRANSFORMER["segment"]: dv.LS_TRANSFORMER["from_quarter"]}
                     if e == dv.LS_TRANSFORMER["entity_id"] else None)
            wf = sg.make_weight_fn(e, filled, dict(zip(we["segment"], we["has_driver"])), list(b["segments"]), start)
            wf = _single_active(wf, list(b["segments"]), start)
            wfns[e] = wf
        specs[e] = dv.specs_for(e, b, wf)
    _CACHE.update({"rules": rules, "fill_to": fill_to, "resolved_step2": resolved_step2, "expansion_ratios": ratios,
                   "mapping": m, "inventory": inv, "resolved": resolved, "camera": cam, "built": built,
                   "shares": shares, "rows": rows, "filled": filled, "specs": specs, "weight_fns": wfns})
    return _CACHE


def _single_active(wf, segs, start: Optional[Dict]):
    """시작 분기 규칙 때문에 j 에 쓸 수 있는 드라이버 세그먼트가 하나면 비중 없이 {그 세그먼트: 1}."""
    start = start or {}

    def weights(j, t):
        active = [s for s in segs if not (s in start and j < start[s])]
        if len(active) == 1:
            return {active[0]: 1.0}
        return wf(j, t)
    return weights


def register(refresh: bool = False, rules: str = "2b2") -> Dict:
    b = build(refresh, rules)
    from .. import entity
    for e, sp in b["specs"].items():
        if e not in entity.PILOTS:          # 2a 시범 기업 항목은 건드리지 않는다
            ft.DRIVER_SPECS[e] = sp
    return b
