# -*- coding: utf-8 -*-
"""레이더 공개용 표 (허용 목록 방식, 출력 v3 확정안).

허용 열
- 발표 전: 기업, 업종, 분기, 예측 작성일시, 예상 매출(E, 조원), 전년 대비(%), 예상 발표일, 시장 예상 대비
  (gap_E_vs_cons_pct 기준: +5% 이상 '상회', -5% 이하 '하회', 그 사이 '부합', 컨센서스 없으면 공란)
- 발표 후 추가: 실제 매출(조원), 예측 오차(%) = (E / 실제 - 1) x 100, 방향 적중
  (실제 매출을 같은 기록 시점 컨센서스와 같은 5% 구간으로 나눈 결과가 '시장 예상 대비'와 같으면 '적중', 다르면 '빗나감',
   컨센서스나 실제가 없으면 공란)
넣지 않는 것: 컨센서스 수치, 등급, 구성 예측, z, 충격, 불일치, 판단 열, 변수설명. 엑셀은 만들지 않고 CSV·HTML 만 만든다.

- 공개 표는 실전 기록(fd_live_record) 한 회차에서 만든다 (radar.postcalc_v2 로 출력 이름 계산). 예측 작성일시 = 기록 회차 시각.
  만들 때 그 기록의 CSV 파일(records/record_<분기>_<record_id>.csv) SHA-256 을 함께 출력한다 (발표 전 해시 공개용).
- check_public(text): 민감 키워드(내부 열 이름·모델 이름·판단·컨센서스·등급 등), 드라이버 원천 코드·이름, COLDESC 문장이
  들어 있으면 멈춘다. save_public 은 쓰기 전에 CSV·HTML 문자열을 모두 이 검사에 통과시킨다.
실행: python -m DATA.fdriver.radar.public --quarter 2026Q3 --record-id 20261012_215026 [--after]
"""
import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import output_path
from ..nowcast import features as ft
from . import coldesc

GAP_BAND_PCT = 5.0
PUBLIC_BEFORE = {"name": "기업", "sector": "업종", "fiscal_quarter": "분기", "created_at": "예측 작성일시",
                 "jo_E": "예상 매출(조원)", "g_E_pct": "전년 대비(%)", "announce_date": "예상 발표일",
                 "vs_market": "시장 예상 대비"}
PUBLIC_AFTER = {"jo_actual": "실제 매출(조원)", "err_pct": "예측 오차(%)", "hit": "방향 적중"}
PUBLIC_COLUMNS = list(PUBLIC_BEFORE.values()) + list(PUBLIC_AFTER.values())
# 공개 출력에 나오면 안 되는 말 (내부 열 이름·원천·모델 구성·판단·컨센서스 수치·등급)
SENSITIVE_WORDS = ["HS", "hs_code", "대만", "월매출", "TWSE", "MOPS", "세그먼트", "사업부", "가중치", "weights", "drivers_used",
                   "M1_model", "E_components", "main_model", "nsur", "shock", "sigma", "delta", "drv_", "M4:", "M1:", "M3:",
                   "변수설명", "변수 설명", "계산식", "COLDESC", "kr_revenue_consensus", "fd_nowcast",
                   "decision", "판단", "후보", "검토", "관찰", "보류", "spread", "flag_", "dir_", "agree_", "z_drv",
                   "gap_", "jo_cons", "rev_cons", "불일치", "컨센서스", "consensus", "등급", "grade", "충격", "구성"]
MODEL_TOKENS = r"(?<![A-Za-z0-9])(B[1-3]|TS|M[1-4]|E_[A-Za-z]+)(?![A-Za-z0-9])"
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
    hits += re.findall(MODEL_TOKENS, text)
    hits += [c for c in driver_codes() if re.search(rf"(?<!\d){c}(?!\d)", text)]
    hits += [n for n in driver_names() if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text)]
    hits += [p for p in coldesc_phrases() if p in text]
    if hits:
        raise PublicLeak(f"공개 출력에 내부 정보가 들어 있음: {hits[:10]}")


# ---------------------------------------------------------------- 표
def market_label(gap_pct) -> str:
    """시장 예상(컨센서스) 대비: +5% 이상 상회, -5% 이하 하회, 그 사이 부합, 없으면 공란."""
    if gap_pct is None or pd.isna(gap_pct):
        return ""
    return "상회" if gap_pct >= GAP_BAND_PCT else ("하회" if gap_pct <= -GAP_BAND_PCT else "부합")


def public_before(out: pd.DataFrame, created_at: str) -> pd.DataFrame:
    """out: 출력 이름 표 (radar.output.to_output). created_at: 예측 작성일시 문자열.
    E 가 없는 행(대조군, 예측 없음)은 내보낼 값이 없어 뺀다. 업종의 '(대조군)' 같은 내부 표시는 지운다. 예상 발표일 순."""
    out = out[pd.to_numeric(out["jo_E"], errors="coerce").notna()]
    x = pd.DataFrame({"name": out["name"], "sector": out["sector"].astype(str).str.replace(r"\s*\(.*?\)", "", regex=True),
                      "fiscal_quarter": out["fiscal_quarter"],
                      "created_at": created_at,
                      "jo_E": pd.to_numeric(out["jo_E"], errors="coerce").round(3),
                      "g_E_pct": pd.to_numeric(out["g_E_pct"], errors="coerce").round(1),
                      "announce_date": out["announce_date"],
                      "vs_market": pd.to_numeric(out["gap_E_vs_cons_pct"], errors="coerce").map(market_label)})
    x = x.sort_values(["announce_date", "name"], key=lambda s: s.astype(str))
    return x.rename(columns=PUBLIC_BEFORE)


def public_after(out: pd.DataFrame, created_at: str, actual: pd.Series) -> pd.DataFrame:
    """actual: entity_id -> 실제 첫 공시 매출 (원). 컨센서스 수치는 방향 적중 계산에만 쓰고 내보내지 않는다."""
    b = public_before(out, created_at)
    o = out.loc[b.index]                                       # 같은 행·같은 순서
    a = pd.to_numeric(o["entity_id"].map(actual), errors="coerce").values
    e = pd.to_numeric(o["rev_E"], errors="coerce").values
    cons = pd.to_numeric(o["rev_cons"], errors="coerce").values
    real_label = [market_label((x / c - 1) * 100) if pd.notna(x) and pd.notna(c) and c > 0 else "" for x, c in zip(a, cons)]
    pred = b[PUBLIC_BEFORE["vs_market"]].tolist()
    b[PUBLIC_AFTER["jo_actual"]] = np.round(a / 1e12, 3)
    b[PUBLIC_AFTER["err_pct"]] = np.round((e / a - 1) * 100, 1)
    b[PUBLIC_AFTER["hit"]] = ["" if not p or not r else ("적중" if p == r else "빗나감") for p, r in zip(pred, real_label)]
    return b.reset_index(drop=True)


def public_html(pub: pd.DataFrame, title: str) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in pub.columns)
    body = "".join("<tr>" + "".join(f"<td>{'' if pd.isna(v) else html.escape(str(v))}</td>" for v in r) + "</tr>"
                   for r in pub.itertuples(index=False))
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title></head>'
            f"<body><h1>{html.escape(title)}</h1><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            f"</body></html>")


# ---------------------------------------------------------------- 기록에서 만들기
def created_at_of(record_id: str) -> str:
    return pd.to_datetime(record_id, format="%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")


def record_file(quarter: str, record_id: str) -> Path:
    return output_path("radar", quarter, "records", mkdir=False) / f"record_{quarter}_{record_id}.csv"


def record_sha256(quarter: str, record_id: str) -> Dict[str, str]:
    from ..live import sha256
    f = record_file(quarter, record_id)
    if not f.exists():
        raise FileNotFoundError(f)
    return {"record_file": f.name, "sha256": sha256(f)}


def actuals(out: pd.DataFrame) -> pd.Series:
    from ..evaluate.live_check import actual_first
    vals = {}
    for e, q in zip(out["entity_id"], out["fiscal_quarter"]):
        v = actual_first(e, q)["value"]
        vals[e] = v if v is not None else np.nan
    return pd.Series(vals)


def from_record(quarter: str, record_id: str, after: bool = False) -> pd.DataFrame:
    from .postcalc_v2 import postcalc
    out = postcalc(quarter, record_id)
    c = created_at_of(record_id)
    return public_after(out, c, actuals(out)) if after else public_before(out, c)


def save_public(quarter: str, record_id: str, after: bool = False, out_dir: Optional[Path] = None,
                pub: Optional[pd.DataFrame] = None) -> Dict:
    """공개용 CSV·HTML 과 기록 파일 SHA-256 (txt) 을 쓴다. 덮어쓰지 않는다. 해시 txt 는 키워드 검사 대상이 아님
    (16진수 문자열이 코드 숫자와 우연히 겹칠 수 있어서)."""
    pub = from_record(quarter, record_id, after) if pub is None else pub
    sha = record_sha256(quarter, record_id)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    d = Path(out_dir) if out_dir else output_path("radar", quarter, "public")
    d.mkdir(parents=True, exist_ok=True)
    kind = "after" if after else "before"
    base = f"radar_public_{quarter}_{kind}_{stamp}"
    csv, htm, txt = d / f"{base}.csv", d / f"{base}.html", d / f"{base}_sha256.txt"
    for f in (csv, htm, txt):
        if f.exists():
            raise FileExistsError(f)
    csv_text = pub.to_csv(index=False)
    html_text = public_html(pub, f"레이더 {quarter} ({'발표 후' if after else '발표 전'})")
    check_public(csv_text)
    check_public(html_text)
    csv.write_text(csv_text, encoding="utf-8-sig")
    htm.write_text(html_text, encoding="utf-8")
    txt.write_text(f"{sha['record_file']}\nSHA-256 {sha['sha256']}\n", encoding="utf-8")
    return {"csv": csv, "html": htm, "sha256_file": txt, **sha, "rows": len(pub)}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--record-id", required=True)
    ap.add_argument("--after", action="store_true")
    a = ap.parse_args(argv)
    r = save_public(a.quarter, a.record_id, a.after)
    print({k: str(v) for k, v in r.items()})


if __name__ == "__main__":
    main()
