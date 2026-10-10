# -*- coding: utf-8 -*-
"""레이더 대상 39곳 (2c 33곳 + 2a 시범 6곳)과 신뢰 등급표 (R1 지시문 v2, 초안 기간 2026Q3~2027Q1 동안 고정).

등급은 표시용이며 계산에 쓰지 않는다. 33곳 + 시범 6곳이 모두 있고 중복이 없어야 한다 (check_universe).
"""
from typing import Dict, List

import pandas as pd

from .. import entity

GRADE_BY_NAME: Dict[str, List[str]] = {
    "A": ["한국타이어앤테크놀로지", "금호타이어", "넥센타이어", "현대차", "기아", "S-Oil", "SK하이닉스", "모두투어", "GKL",
          "파라다이스", "호텔신라", "HD현대일렉트릭", "LS ELECTRIC", "효성중공업"],
    "B": ["아모레퍼시픽", "LG생활건강", "코스맥스", "한국콜마", "삼양식품", "농심", "KT&G", "POSCO홀딩스", "현대제철",
          "금호석유화학", "롯데케미칼", "덴티움", "한미반도체"],
    "C": ["HD한국조선해양", "삼성중공업", "한화오션", "LG디스플레이", "삼성SDI", "삼성전기"],
}
PILOT_GRADE = {"005930": "A", "039130": "A", "267270": "B", "278470": "B",
               "006040": "B2만", "003350": "B2만"}           # 대조군 2곳은 B2 만 표시
CONTROLS = ("006040", "003350")
PILOT_SECTOR = {"005930": "반도체", "039130": "여행", "267270": "건설기계", "278470": "화장품",
                "006040": "수산·식품 (대조군)", "003350": "화장품 (대조군)"}


def universe() -> pd.DataFrame:
    """entity_id, name, sector, grade, group('2c'/'pilot')."""
    from ..stage2c.mapping import read_mapping
    c = read_mapping()["companies"]
    c = c[c["included"]][["entity_id", "name", "sector"]].copy()
    by_name = {n: g for g, ns in GRADE_BY_NAME.items() for n in ns}
    c["grade"] = c["name"].map(by_name)
    c["group"] = "2c"
    p = pd.DataFrame({"entity_id": list(entity.PILOTS), "name": list(entity.PILOTS.values())})
    p["sector"] = p["entity_id"].map(PILOT_SECTOR)
    p["grade"] = p["entity_id"].map(PILOT_GRADE)
    p["group"] = "pilot"
    u = pd.concat([c, p], ignore_index=True)
    check_universe(u)
    return u


def check_universe(u: pd.DataFrame) -> None:
    graded = [n for ns in GRADE_BY_NAME.values() for n in ns]
    problems = []
    if len(u) != 39:
        problems.append(f"기업 수 {len(u)} != 39")
    if u["entity_id"].duplicated().any():
        problems.append(f"중복 종목: {u.loc[u['entity_id'].duplicated(), 'entity_id'].tolist()}")
    if len(graded) != len(set(graded)):
        problems.append("등급표에 중복 이름")
    missing = u.loc[u["grade"].isna(), "name"].tolist()
    if missing:
        problems.append(f"등급이 없는 기업: {missing}")
    extra = sorted(set(graded) - set(u.loc[u["group"] == "2c", "name"]))
    if extra:
        problems.append(f"등급표에만 있는 이름: {extra}")
    if problems:
        raise RuntimeError("레이더 대상 점검 실패 (멈춤): " + "; ".join(problems))
