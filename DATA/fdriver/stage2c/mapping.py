# -*- coding: utf-8 -*-
"""2c 드라이버 매핑 엑셀 v2 읽기 (명세서 2c v2 4절 'v2 확정 사항'의 원본).

엑셀 내용은 고치지 않는다. 시트별로 표 하나씩 돌려주고, 코드·종목코드는 문자열로 읽는다.
- companies: 기업 시트 (포함 Y 만 included=True)
- codes: 코드 시트 (빈 줄 제외). use = (사용 == 'Y'), expand6 = (6자리 전개 == 'Y'), needs_db_check = 메모에 'DB 존재 확인 필요'
- weights: 세그먼트_가중치 시트. weight 는 비어 있으면 NaN, has_driver = (드라이버 있음 == 'Y')
- corp_events: 기업사건 시트
"""
from pathlib import Path
from typing import Dict

import pandas as pd

from ..config import output_path

MAPPING_FILE = "fdriver_2c_driver_mapping_v2.xlsx"


def mapping_path() -> Path:
    return output_path("2c", mkdir=False) / MAPPING_FILE


def _s(x) -> str:
    return "" if pd.isna(x) else str(x).strip()


def read_mapping(path=None) -> Dict[str, pd.DataFrame]:
    path = Path(path) if path else mapping_path()
    xl = pd.read_excel(path, sheet_name=None, dtype=str)
    c = xl["기업"].rename(columns={"종목코드": "entity_id", "기업": "name", "업종": "sector", "조사 판정": "survey",
                                 "드라이버 유형": "driver_type", "포함 여부 (Y/N/검토)": "include", "메모": "memo"})
    c = c[c["entity_id"].notna()].copy()
    c["included"] = c["include"].map(_s).eq("Y")

    k = xl["코드"].rename(columns={"종목코드": "entity_id", "기업": "name", "출처": "origin", "HS 코드": "hs",
                                 "품목 (참고)": "item", "자료 시작": "start", "자료 끝": "end", "사용 (Y/N)": "use_raw",
                                 "세그먼트": "segment", "6자리 전개 (Y/공란)": "expand_raw", "메모": "memo"})
    k = k[k["entity_id"].notna() & k["hs"].notna()].copy()
    k["hs"] = k["hs"].map(_s)
    k["use"] = k["use_raw"].map(_s).eq("Y")
    k["expand6"] = k["expand_raw"].map(_s).eq("Y")
    k["segment"] = k["segment"].map(_s)
    k["needs_db_check"] = k["memo"].map(_s).str.contains("DB 존재 확인 필요")

    w = xl["세그먼트_가중치"].rename(columns={"종목코드": "entity_id", "기업": "name", "세그먼트": "segment",
                                       "가중치 (0~1)": "weight", "적용 시작 연도": "start_year",
                                       "드라이버 있음 (Y/N)": "has_driver_raw", "근거": "basis"})
    w = w[w["entity_id"].notna()].copy()
    w["weight"] = pd.to_numeric(w["weight"], errors="coerce")
    w["has_driver"] = w["has_driver_raw"].map(_s).eq("Y")
    w["segment"] = w["segment"].map(_s)

    e = xl["기업사건"].rename(columns={"종목코드": "entity_id", "기업": "name", "분기": "quarters", "내용": "content",
                                    "기본안": "default", "판단 (제외/유지/공시 확인)": "decision", "메모": "memo"})
    e = e[e["entity_id"].notna()].copy()
    return {"companies": c.reset_index(drop=True), "codes": k.reset_index(drop=True),
            "weights": w.reset_index(drop=True), "corp_events": e.reset_index(drop=True)}
