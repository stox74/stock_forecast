# -*- coding: utf-8 -*-
"""레이더 공개용 표 (허용 목록 방식). COLDESC v1 에서 새로 만듦 (그전에는 공개 경로와 키워드 차단 테스트가 코드에 없었음).

- PUBLIC_COLUMNS 에 있는 열만 내보낸다 (잠정 목록, 설계실 확정 필요). 모델 구성·드라이버·분모 같은 '레시피' 열은 넣지 않는다.
- 변수설명(coldesc) 시트·설명 표와 판단 시트(v2)는 공개 경로에 넣지 않는다. 엑셀은 만들지 않고 CSV·HTML 표만 만든다.
- check_public(text): 민감 키워드(드라이버 원천·모델 구성·내부 열 이름)나 COLDESC 의 의미·계산식·비고 문장이 들어 있으면 멈춘다.
  save_public 은 쓰기 전에 CSV·HTML 문자열을 모두 이 검사에 통과시킨다.
"""
import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import pandas as pd

from ..config import output_path
from ..nowcast import features as ft
from . import coldesc

PUBLIC_COLUMNS = ["name", "sector", "fiscal_quarter", "asof_date", "rev_E", "g_E_pct", "announce_date"]
PUBLIC_HEADER = {"name": "기업", "sector": "업종", "fiscal_quarter": "분기", "asof_date": "기준일",
                 "rev_E": "예상 매출(조원)", "g_E_pct": "전년 대비(%)", "announce_date": "예상 발표일"}
# 내부 열 이름·원천·모델 구성 (공개 출력에 나오면 안 되는 말)
SENSITIVE_WORDS = ["HS", "hs_code", "대만", "월매출", "TWSE", "MOPS", "세그먼트", "사업부", "가중치", "weights", "drivers_used",
                   "M1_model", "E_components", "main_model", "nsur", "shock_", "sigma", "delta", "drv_3m", "M4:", "M1:", "M3:",
                   "변수설명", "변수 설명", "계산식", "COLDESC", "kr_revenue_consensus", "fd_nowcast",
                   # v2 판단 시트·출력 이름 (내부용)
                   "decision", "판단", "후보(", "검토(", "관찰(", "보류(", "spread_", "flag_", "dir_", "agree_", "z_drv",
                   "gap_E", "jo_cons", "불일치"]
MIN_PHRASE = 12                          # COLDESC 문장 검사: 이 길이 이상 문장만 (짧은 단어 오탐 방지)


class PublicLeak(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _registry() -> dict:
    """2c 드라이버 구성과 매핑 엑셀 (registry.build: 전역 DRIVER_SPECS 를 바꾸지 않음)."""
    from ..stage2c import registry
    return registry.build(False, "2b2")


def _specs() -> tuple:
    two_c = [s for specs in _registry()["specs"].values() for s in specs]
    return tuple(two_c + [s for specs in ft.DRIVER_SPECS.values() for s in specs])


def driver_codes() -> List[str]:
    """드라이버 원천 코드 (숫자 4자리 이상): model_id 'M1:854232' 의 뒷부분 + 2c 매핑 엑셀 HS 코드."""
    codes = set()
    for s in _specs():
        codes |= set(re.findall(r"\d{4,}", s.model_id))
    for h in _registry()["mapping"]["codes"]["hs"].dropna().astype(str):
        codes |= set(re.findall(r"\d{4,}", h))
    return sorted(codes)


def driver_names() -> List[str]:
    """드라이버 이름 (model_id 'M1:export' 의 뒷부분 중 숫자가 아닌 것, 3글자 이상)."""
    names = set()
    for s in _specs():
        names |= {p for p in s.model_id.split(":")[1:] if not p.isdigit() and len(p) >= 3}
    return sorted(names)


def coldesc_phrases() -> List[str]:
    out = set()
    for d in coldesc.COLDESC.values():
        for k in ("meaning", "formula", "note"):
            out |= {p.strip() for p in re.split(r"[.,;()]", d[k]) if len(p.strip()) >= MIN_PHRASE}
    return sorted(out)


def check_public(text: str) -> None:
    hits = [w for w in SENSITIVE_WORDS if w in text]
    hits += [c for c in driver_codes() if re.search(rf"(?<!\d){c}(?!\d)", text)]
    hits += [n for n in driver_names() if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text)]
    hits += [p for p in coldesc_phrases() if p in text]
    if hits:
        raise PublicLeak(f"공개 출력에 내부 정보가 들어 있음: {hits[:10]}")


def public_table(tab: pd.DataFrame) -> pd.DataFrame:
    x = tab[[c for c in PUBLIC_COLUMNS if c in tab.columns]].copy()
    if "rev_E" in x:
        x["rev_E"] = (pd.to_numeric(x["rev_E"], errors="coerce") / 1e12).round(3)
    if "g_E_pct" in x:
        x["g_E_pct"] = pd.to_numeric(x["g_E_pct"], errors="coerce").round(1)
    return x.rename(columns=PUBLIC_HEADER)


def public_html(pub: pd.DataFrame, title: str) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in pub.columns)
    body = "".join("<tr>" + "".join(f"<td>{'' if pd.isna(v) else html.escape(str(v))}</td>" for v in r) + "</tr>"
                   for r in pub.itertuples(index=False))
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>'
            f"<body><h1>{html.escape(title)}</h1><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>")


def save_public(tab: pd.DataFrame, quarter: str, out_dir: Path = None) -> Dict[str, Path]:
    pub = public_table(tab)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    d = Path(out_dir) if out_dir else output_path("radar", quarter, "public")
    d.mkdir(parents=True, exist_ok=True)
    csv, htm = d / f"radar_public_{quarter}_{stamp}.csv", d / f"radar_public_{quarter}_{stamp}.html"
    for f in (csv, htm):
        if f.exists():
            raise FileExistsError(f)
    csv_text = pub.to_csv(index=False)
    html_text = public_html(pub, f"레이더 {quarter}")
    check_public(csv_text)
    check_public(html_text)
    csv.write_text(csv_text, encoding="utf-8-sig")
    htm.write_text(html_text, encoding="utf-8")
    return {"csv": csv, "html": htm}
