# -*- coding: utf-8 -*-
"""2c 작업 2 (c): 사업보고서 '사업의 내용'의 부문별 매출 비중 (연도별) 추출과 시점 규칙.

추출
1. 사업보고서 원공시 본문(수정한 doc_text: zip 본문 파일)에서 '주요 제품 등의 현황' 표를 찾는다. 서식이 다른 기업은
   기업별 절 이름 (POSCO홀딩스 '가. 매출현황', 롯데케미칼 2012~2020 '매출실적'·'사업부문별 매출 실적').
2. 표의 행을 '행 이름 ... 금액 비율' 로 읽는다 (parse_rows). 여러 해가 나란한 표는 첫 금액(당기)만 쓴다.
3. 행 이름 -> 공시 부문 -> 엑셀 세그먼트는 RULES (기업별 키워드, 공시 부문 이름만 보고 정함). 키워드가 없는 행은 위 행의
   부문을 잇는다 (표에서 부문 칸이 여러 행에 걸친 경우). 합계·소계·연결조정·내부거래 행은 뺀다.
4. 비중 = 엑셀 세그먼트별 금액 합 / 모든 부문 금액 합 (연결조정·내부거래 제거 전 부문 금액. POSCO 는 부문별 내부거래
   제거 후 '합계'). 공시 부문 하나에 엑셀 세그먼트 둘 이상이 섞인 연도는 추출 실패('ambiguous')로 둔다.
5. 추출 실패 연도는 가장 가까운 과거 연도 값으로 채우고 filled_from 에 그 연도를 적는다 (미래 값으로 채우지 않음).

시점 규칙 (지시문 (c)): 분기 j 에는 j 의 첫 공시일 이전(당일 제외)에 접수된 가장 최근 사업보고서 비중. 기준일 t 의
예측에서는 t 까지(당일 포함) 접수된 것만 쓴다. 첫 공시일이 없는 분기는 법정 기한(분기말 +45일, 4분기 +90일).
"""
import re
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import events
from . import dart

FIRST_YEAR = 2012
SKIP = re.compile(r"(^|\s)(합\s*계|단\s*순\s*합\s*계|소\s*계|계|총\s*계)(\s|$|-)|연\s*결\s*조\s*정|내부\s*거래")
AMT = r"\(?-?\d{1,3}(?:,\d{3})+\)?|\(?-?\d{4,}\)?"
PAIR = re.compile(r"(" + AMT + r")\s*\(?\s*(-?\d{1,3}(?:\.\d{1,2})?)\s*%?\s*\)?(?=[\s가-힣A-Za-z(]|$)")
SECTION_PATS = [r"주요\s*제품\s*등\s*의\s*현황", r"주요\s*제품\s*의\s*현황", r"주요\s*제품\s*현황",
                r"주요\s*제품\s*등\s*의\s*매출", r"주요\s*제품\s*및\s*서비스"]
SECTION_END = re.compile(r"(나\s*\.\s*)?주요\s*제품\s*(등\s*의\s*)?가격|제품\s*가격\s*변동|주요\s*원재료|시장\s*점유율|나\s*\.\s*시장")

# 기업별 규칙: mode 'priority' (규칙 순서대로 첫 일치) 또는 'rightmost' (행 이름에서 가장 오른쪽에 나온 키워드)
# 각 규칙: (정규식, 공시 부문 이름, 엑셀 세그먼트, 비고). 엑셀 세그먼트 '' 은 엑셀에 없는 부문(기타).
RULES: Dict[str, Dict] = {
    "009150": {"mode": "priority", "rules": [
        (r"카메라\s*모듈|ISM", "카메라모듈(공시 부문)", "카메라모듈", ""),
        (r"MLCC|수동소자|칩부품", "컴포넌트", "MLCC", ""),
        (r"기판", "기판·패키지", "패키지기판", ""),
        (r"TUNER|통신\s*모듈|파워\s*모듈|무선\s*LAN|POWER", "통신·파워모듈", "", ""),
    ]},
    "267260": {"mode": "priority", "rules": [
        (r"전력\s*,\s*회전\s*,\s*배전", "전력·회전·배전 합산", "AMBIGUOUS", "세 세그먼트가 한 행"),
        (r"전력기기", "전력기기", "전력기기(변압기·차단기)", ""),
        (r"회전기기", "회전기기", "회전기기", ""),
        (r"배전기기", "배전기기", "배전기기", ""),
    ]},
    "010120": {"mode": "rightmost", "rules": [
        (r"저압기기|고압기기|배전반|송배전", "전력", "전력(배전반·차단기)", ""),
        (r"PLC|INVERTER|자동화", "자동화", "자동화 등", ""),
        (r"동관|STS|후육관", "금속", "자동화 등", "금속을 '자동화 등'(드라이버 없음)에 넣음"),
        (r"IT\s*설비|컴퓨터시스템", "IT", "자동화 등", "IT 를 '자동화 등'(드라이버 없음)에 넣음"),
    ]},
    "298040": {"mode": "priority", "rules": [
        (r"중공업|변압기", "중공업", "중공업", ""),
        (r"건설|해링턴", "건설", "건설", ""),
        (r"^\W*기타", "기타", "", ""),
    ]},
    "004020": {"mode": "rightmost", "rules": [
        (r"봉\s*형\s*강", "봉형강", "봉형강", ""),
        (r"판\s*재", "판재", "판재", ""),
        (r"중\s*기", "중기계", "", ""),
        (r"반제품|부산물", "기타(반제품·부산물)", "", ""),
    ]},
    "090430": {"mode": "priority", "rules": [
        (r"생활용품", "Daily Beauty(생활용품)", "생활용품", ""),
        (r"화장품|기\s*초|세안", "화장품", "화장품", ""),
        (r"녹차", "녹차", "", ""),
    ]},
    "051900": {"mode": "rightmost", "rules": [
        (r"Beaut|화장품", "Beauty(화장품)", "화장품", ""),
        (r"Healthy|생활용품|HPC|HDB", "HDB(생활용품)", "생활용품", ""),
        (r"Refresh|음료", "Refreshment(음료)", "음료", ""),
    ]},
    "161890": {"mode": "rightmost", "rules": [
        (r"화장품|색조|피부화장|기초|수분크림", "화장품", "화장품", ""),
        (r"패키징|용기|포장재", "패키징", "패키징", ""),
        (r"제약\s*(제품|사업)|전문의약품|의약외품|치약|수액|케이캡|치료제|캡슐|킨도라|헵세라|잇치|휴쎈타|록페날", "제약(전문의약품)",
         "전문의약품", "거래처 이름(동국제약 등)과 겹치지 않게 '제약 제품'으로 찾음"),
        (r"H\s*&\s*B|HB&B|식품|컨디션", "H&B(식품)", "", ""),
        (r"임대", "임대·기타", "", ""),
    ]},
    "033780": {"mode": "rightmost", "rules": [
        (r"궐련|제조담배|반제품|잎담배|향캡슐|판상엽|임가공|MAC|담배|하이브리드", "담배", "담배", ""),
        (r"홍삼|건강기능", "건강기능식품", "건강기능식품·부동산 등", ""),
        (r"분양|임대|부동산", "부동산", "건강기능식품·부동산 등", ""),
        (r"제약|화장품", "기타(제약·화장품)", "건강기능식품·부동산 등", ""),
        (r"상품|기계장치", "기타(상품 등)", "건강기능식품·부동산 등", ""),
    ]},
}
POSCO_BLOCKS = [(r"철강", "철강부문", "철강"), (r"인프라|무역|건설|물류", "인프라(무역·건설·물류 등)", "인프라(무역·건설)"),
                (r"이차전지|미래소재|기타|에너지", "기타·이차전지소재", "")]


# ---------------------------------------------------------------- 표 찾기·읽기
def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("&cr;", " "))


def find_section(t: str, pats=SECTION_PATS) -> Tuple[Optional[str], str]:
    sec = [m.start() for m in re.finditer(r"사업\s*의\s*내용", t)]
    after = sec[1] if len(sec) > 1 else 0
    for p in pats:
        for h in [m.start() for m in re.finditer(p, t) if m.start() > after]:
            m = SECTION_END.search(t, h + 40)
            e = m.start() if (m and m.start() - h < 8000) else h + 5000
            s = t[h:e]
            if PAIR.search(s):
                return p, s
    return None, ""


def _amt(s: str) -> float:
    neg = s.startswith("(") or s.startswith("-")
    v = float(re.sub(r"[^\d]", "", s))
    return -v if neg else v


def parse_rows(s: str) -> List[Dict]:
    out, prev = [], 0
    for m in PAIR.finditer(s):
        lab = s[prev:m.start()].strip()
        prev = m.end()
        if lab:
            out.append({"label": lab, "amount": _amt(m.group(1)), "pct": float(m.group(2))})
    return out


def classify(label: str, cfg: Dict) -> Optional[Tuple[str, str, str]]:
    hits = []
    for i, (pat, disc, seg, note) in enumerate(cfg["rules"]):
        ms = list(re.finditer(pat, label))
        if ms:
            hits.append((ms[-1].start(), i, disc, seg, note))
    if not hits:
        return None
    pick = max(hits, key=lambda h: h[0]) if cfg["mode"] == "rightmost" else min(hits, key=lambda h: h[1])
    return pick[2], pick[3], pick[4]


def rows_generic(entity_id: str, t: str) -> Tuple[Optional[str], List[Dict]]:
    pat, s = find_section(t)
    cfg = RULES[entity_id]
    rows, cur = [], None
    for r in parse_rows(s):
        lab = r["label"]
        tail = lab[-25:]
        if SKIP.search(tail) or r["amount"] <= 0:
            r.update(disclosed="(합계·조정)", segment=None, note="제외")
            rows.append(r)
            continue
        c = classify(lab[-160:], cfg)
        if c is None and cur is not None:
            c = (cur[0], cur[1], "부문 칸 이어받음")
        if c is None:
            c = ("(대응 없음)", "", "키워드 없음")
        cur = c
        r.update(disclosed=c[0], segment=c[1], note=c[2])
        rows.append(r)
    return pat, rows


def rows_posco(t: str) -> Tuple[Optional[str], List[Dict]]:
    h = [m.start() for m in re.finditer(r"가\s*\.\s*매출\s*현황", t)]
    if not h:
        return None, []
    s = t[h[0]:h[0] + 6000]
    end = re.search(r"주요\s*제품\s*등\s*의\s*가격|나\s*\.\s*주요", s)
    s = s[:end.start()] if end else s
    rows, prev = [], 0
    for m in re.finditer(r"내부\s*거래\s*제거\s*(?:\(?[\d,]+\)?\s*-?\s*)+합\s*계\s*([\d,]+)", s):
        block = s[prev:m.start()]
        mb = [x.group(1) for x in re.finditer(r"([가-힣]+)\s*부문", block) if x.group(1) != "사업"]
        name = mb[0] if mb else block[:40]         # 블록의 첫 'OO부문' (철강부문, 인프라 무역부문 -> 무역 ...)
        prev = m.end()
        # 블록 이름의 가장 앞에 나온 키워드 (무역 블록의 품목 '철강/금속'에 걸리지 않게)
        hits = [(m.start(), disc, seg) for pat, disc, seg in POSCO_BLOCKS for m in [re.search(pat, name)] if m]
        pick = min(hits)[1:] if hits else ("(대응 없음)", "")
        rows.append({"label": name, "amount": _amt(m.group(1)), "pct": np.nan, "disclosed": pick[0],
                     "segment": pick[1], "note": "부문별 내부거래 제거 후 합계"})
    return "가. 매출현황", rows


def rows_lotte(t: str, fy: int) -> Tuple[Optional[str], List[Dict]]:
    if fy >= 2023:
        pat, s = find_section(t, [r"주요\s*제품\s*현황"])
        rows, name = [], None
        segs = [("기초", "기초화학"), ("첨단", "첨단소재·정밀화학·전지소재"), ("정밀", "첨단소재·정밀화학·전지소재"),
                ("전지", "첨단소재·정밀화학·전지소재")]
        for r in parse_rows(s):
            ks = [(m.start(), k, seg) for k, seg in segs for m in re.finditer(k + r"(소재|화학)", r["label"])]
            if ks:
                name = max(ks)[1:]
            if re.search(r"소\s*계", r["label"][-8:]) and name:
                rows.append({**r, "disclosed": name[0], "segment": name[1], "note": "부문 소계"})
        # 마지막 부문(전지소재)은 소계 행이 없을 수 있다: 비율 100 미만 단일 행이면 그 금액 / (비율/100)
        last = [r for r in parse_rows(s) if r["label"].strip().startswith("전지소재")]
        if last and not any(r["disclosed"] == "전지" for r in rows):
            r = last[0]
            rows.append({**r, "amount": r["amount"] / (r["pct"] / 100), "disclosed": "전지",
                         "segment": "첨단소재·정밀화학·전지소재", "note": "소계 없음: 첫 행 금액 / 비율"})
        return pat, rows
    # 2012~2022: '매출실적' 표. 부문 '계'(합계) 금액
    m = re.search(r"매출\s*실적", t)
    if not m:
        return None, []
    s = t[m.start():m.start() + 6000]
    rows = []
    if fy >= 2020:
        for k, seg in (("기초소재사업부", "기초화학"), ("첨단소재사업부", "첨단소재·정밀화학·전지소재")):
            mm = re.search(k + r".*?합계\s*([\d,]+)", s)
            if mm:
                rows.append({"label": k, "amount": _amt(mm.group(1)), "pct": np.nan, "disclosed": k, "segment": seg,
                             "note": "사업부문별 매출 실적 합계(당기)"})
        if rows:
            return "매출실적", rows
    # 2014~2019: 부문 합계 행('기초유분계' 또는 부문 이름 뒤 내수·수출·합계). 2012~2013 은 품목 행만 있어 실패로 둔다.
    tail = r"\s+(?!기초유분|모노머|폴리머)[^\d]*?내수\s*[\d,\-]+\s*(?:[\d,\-]+\s*)*?수출\s*[\d,\-]+\s*(?:[\d,\-]+\s*)*?합계\s*([\d,]+)"
    for k, seg in (("기초유분", "기초화학"), ("모노머", "기초화학"), ("폴리머", "기초화학"), ("기타", "")):
        mm = re.search(k + r"계\s*내수\s*[\d,\-]+\s.*?합계\s*([\d,]+)", s) or re.search(r"(?<![가-힣])" + k + tail, s)
        if mm:
            rows.append({"label": k, "amount": _amt(mm.group(1)), "pct": np.nan, "disclosed": k,
                         "segment": seg, "note": "매출실적 부문 합계(당기). 롯데케미칼 본체 표 (2016~2019 롯데첨단소재 미포함)"})
    if not {"기초유분", "모노머", "폴리머"} <= {r["disclosed"] for r in rows}:
        return "매출실적", []
    return "매출실적", rows


def extract_year(entity_id: str, fy: int, rcept_no: str) -> Dict:
    t = _clean(dart.annual_text(rcept_no))
    if entity_id == "005490":
        pat, rows = rows_posco(t)
    elif entity_id == "011170":
        pat, rows = rows_lotte(t, fy)
    else:
        pat, rows = rows_generic(entity_id, t)
    use = [r for r in rows if r.get("segment") is not None]
    status = "ok"
    if not use:
        status = "no_table" if pat is None else "no_rows"
    elif any(r["segment"] == "AMBIGUOUS" for r in use):
        status = "ambiguous"
    shares = {}
    if status == "ok":
        tot = sum(r["amount"] for r in use)
        for r in use:
            shares[r["segment"]] = shares.get(r["segment"], 0.0) + r["amount"] / tot
    return {"entity_id": entity_id, "fiscal_year": fy, "rcept_no": rcept_no, "section": pat, "status": status,
            "rows": rows, "shares": shares}


# ---------------------------------------------------------------- 연도표와 시점 규칙
def extract_all(entity_ids: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """반환: (연도별 세그먼트 비중 긴 표, 행 단위 대응표)."""
    shares, rows = [], []
    for e in entity_ids:
        ar = dart.annual_reports(e, FIRST_YEAR)
        for r in ar.itertuples():
            x = extract_year(e, r.fiscal_year, r.rcept_no)
            for row in x["rows"]:
                rows.append({"entity_id": e, "fiscal_year": r.fiscal_year, "rcept_no": r.rcept_no, **row})
            base = {"entity_id": e, "fiscal_year": r.fiscal_year, "rcept_no": r.rcept_no, "rcept_dt": r.rcept_dt,
                    "status": x["status"], "section": x["section"]}
            if x["shares"]:
                for seg, w in x["shares"].items():
                    shares.append({**base, "segment": seg, "share": w})
            else:
                shares.append({**base, "segment": None, "share": np.nan})
    return pd.DataFrame(shares), pd.DataFrame(rows)


def fill_years(shares: pd.DataFrame) -> pd.DataFrame:
    """기업 x 사업연도 표 (세그먼트 열). 실패 연도는 가장 가까운 과거 성공 연도 값, filled_from 표시."""
    out = []
    for e, g in shares.groupby("entity_id"):
        years = g.drop_duplicates("fiscal_year").set_index("fiscal_year")[["rcept_no", "rcept_dt", "status"]].sort_index()
        wide = g[g["segment"].notna()].pivot_table(index="fiscal_year", columns="segment", values="share", aggfunc="sum")
        last = None
        for fy, r in years.iterrows():
            ok = r["status"] == "ok" and fy in wide.index
            if ok:
                last = fy
            vals = wide.loc[last].fillna(0.0).to_dict() if last is not None else {}
            out.append({"entity_id": e, "fiscal_year": fy, "rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"],
                        "status": r["status"], "filled_from": None if ok else last, "shares": vals})
    return pd.DataFrame(out)


def first_dates(entity_id: str) -> Dict[pd.Timestamp, pd.Timestamp]:
    """분기말 -> 첫 공시일 (이벤트표). 없는 분기는 weight_fn 에서 법정 기한으로."""
    fd = events.first_disclosure(events.read_events([entity_id]), {entity_id: 12})
    return dict(zip(fd["period_end"], fd["first_dt"]))


def legal_date(q: pd.Timestamp) -> pd.Timestamp:
    return q + pd.Timedelta(days=90 if q.month == 12 else 45)


def make_weight_fn(entity_id: str, filled: pd.DataFrame, has_driver: Dict[str, bool],
                   segments_with_codes: List[str], start_rules: Optional[Dict[str, pd.Timestamp]] = None,
                   first_dt: Optional[Dict] = None) -> Callable:
    """weights(j, t) -> {세그먼트: 비중}. 드라이버 있음 = Y 이고 코드가 남은 세그먼트만.
    start_rules {세그먼트: 첫 분기말}: 그 분기 전에는 0 (LS ELECTRIC 변압기 자회사 2024Q4~)."""
    f = filled[filled["entity_id"] == entity_id].sort_values("rcept_dt")
    dates = list(pd.to_datetime(f["rcept_dt"]))
    vals = list(f["shares"])
    fdt = first_dates(entity_id) if first_dt is None else first_dt
    segs = [s for s in segments_with_codes if has_driver.get(s)]
    start_rules = start_rules or {}

    def weights(j, t) -> Dict[str, float]:
        j = pd.Timestamp(j)
        cut = fdt.get(j, legal_date(j))
        cut = min(pd.Timestamp(cut), pd.Timestamp(t) + pd.Timedelta(days=1))   # j 첫 공시일 전, t 당일까지
        k = None
        for i, d in enumerate(dates):
            if d < cut:
                k = i
        if k is None:
            return {}
        w = {s: float(vals[k].get(s, 0.0)) for s in segs}
        for s, q0 in start_rules.items():
            if s in w and j < q0:
                w[s] = 0.0
        return w

    return weights
