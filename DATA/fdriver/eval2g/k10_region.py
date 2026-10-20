# -*- coding: utf-8 -*-
"""K10-1: 기업-지역 연결 (전 종목 드라이버 탐색 준비). 승인 아님. 지역 수출 자료(값)는 읽지 않는다.
쓰기: fd_company_region (작업 표, 실행마다 전체 교체) 와 CSV (2g/K10). 원본 테이블에는 쓰지 않는다.

대상: 2g 통합 대상 (fd_target_2g) + 2d 확대 73곳 (fd_driver_map_2d driver·ts_only).
원문: 기업별 가장 최근 사업보고서 원공시 (DART 공시목록 2025-01 이후, 없으면 2024-01 이후). 원문 캐시는 2d 와 공유.
추출 (원문은 표가 풀린 글자):
- 본사: '본사의 주소' 뒤 첫 주소. 없으면 '본점소재지' 뒤 첫 주소.
- 사업장: (1) '원재료 및 생산설비' 절 (목차 다음의 본문, '매출 및 수주상황' 앞까지),
          (2) '회사의 연혁' 의 본점·사업장(공장·제철소·사무소 등) 변동 부분 (본점소재지 뒤 2,000자)
  에서 주소를 찾는다.
  - 'full': 시도 이름(정식·약칭) 바로 뒤 시군구 이름. 시군구가 그 시도 목록에 있어야 한다.
  - 'city_only': 시도 없이 쓴 시·군 이름 중 전국에 하나뿐인 것 (예: 구미시, 아산시). 구 이름은 쓰지 않는다.
  - 'plant_name': '익산공장', '창원 제2공장' 처럼 지명 + 공장·사업장·사업소·제철소·물류센터·연구소. 지명에 시·군을 붙인 이름이
    전국에 하나뿐일 때만 (생산설비 절에서만). 유형은 붙은 낱말로 정한다.
  - 본사: 이전 표시(⇒, →, 변경 후)가 있으면 그 뒤 주소.
- 지역 체계: korea_regional_trade_data 의 sido_cd·sgg_nm (예: '경기도 화성시').
  광주(29)·전남(46) 주소는 2026-07 통합 코드 12 ('전남광주통합특별시 OO') 도 sido_cd_alt·sgg_nm_alt 에 같이 둔다.
- 유형: 주소 바로 앞 40자 안에서 주소에 가장 가까운 낱말. 본사(본사·본점) / 공장(공장·제조·생산·제철소·Fab·플랜트·사업소) /
  연구소(연구소·R&D·기술원) / 물류(물류·창고·배송·센터) / 그 밖 '사업장'. 본사 줄은 본사 추출에서만 만든다.
- weight: 원문이 표가 풀린 글자라 생산능력·비중을 사업장에 안정적으로 붙이지 못한다. 비워 두고, 같은 지역·유형이
  나온 횟수(n_mentions)와 문맥(context, 120자)을 남긴다.
python -m DATA.fdriver.eval2g.k10_region [--limit N] [--threads 4]
"""
import argparse
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List

import pandas as pd

from .. import db, events
from ..config import output_path
from ..eval2d import k2_products as P
from . import common as C

OUT = output_path("2g", "K10")
LISTS = output_path("2g", "K10", "cache", "lists")
DOCS = P.DOCS
T_REG = "fd_company_region"
log = logging.getLogger(__name__)

SIDO_ALIAS = [
    ("서울특별시", "11"), ("서울시", "11"), ("서울", "11"), ("부산광역시", "26"), ("부산시", "26"), ("부산", "26"),
    ("대구광역시", "27"), ("대구시", "27"), ("대구", "27"), ("인천광역시", "28"), ("인천시", "28"), ("인천", "28"),
    ("광주광역시", "29"), ("대전광역시", "30"), ("대전시", "30"), ("대전", "30"), ("울산광역시", "31"), ("울산시", "31"),
    ("울산", "31"), ("세종특별자치시", "36"), ("세종시", "36"), ("경기도", "41"), ("경기", "41"),
    ("충청북도", "43"), ("충북", "43"), ("충청남도", "44"), ("충남", "44"), ("전라남도", "46"), ("전남", "46"),
    ("경상북도", "47"), ("경북", "47"), ("경상남도", "48"), ("경남", "48"), ("제주특별자치도", "50"), ("제주도", "50"),
    ("강원특별자치도", "51"), ("강원도", "51"), ("강원", "51"), ("전북특별자치도", "52"), ("전라북도", "52"), ("전북", "52"),
    ("전남광주통합특별시", "12"),
]
TYPE_KW = [("공장", r"공장|제조|생산|제철소|Fab|FAB|플랜트|사업소"), ("연구소", r"연구소|R&D|연구개발|기술원|연구센터"),
           ("물류", r"물류|창고|배송|유통센터|물류센터")]
DDL = f"""
CREATE TABLE IF NOT EXISTS `{T_REG}` (
  entity_id VARCHAR(20) NOT NULL,
  sido_cd VARCHAR(4) NOT NULL,
  sgg_nm VARCHAR(60) NOT NULL,
  site_type VARCHAR(10) NOT NULL,
  weight DOUBLE NULL,
  sido_cd_alt VARCHAR(4) NULL,
  sgg_nm_alt VARCHAR(60) NULL,
  method VARCHAR(12) NULL,
  n_mentions INT NULL,
  context VARCHAR(200) NULL,
  source_doc VARCHAR(20) NULL,
  report_fy VARCHAR(10) NULL,
  extracted_at DATETIME NOT NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, sido_cd, sgg_nm, site_type)
) DEFAULT CHARSET=utf8mb4
"""


# ------------------------------------------------------------------ 지역 사전
def gazetteer() -> Dict:
    g = db.read_sql("SELECT DISTINCT sido_cd, sido_nm, sgg_nm FROM korea_regional_trade_data WHERE sgg_nm <> ''")
    g = g[[str(s).startswith(str(n) + " ") for s, n in zip(g["sgg_nm"], g["sido_nm"])]]
    g["short"] = [s[len(n) + 1:] for s, n in zip(g["sgg_nm"], g["sido_nm"])]
    g = g[g["sido_cd"] != "12"]
    by_sido = {}
    for r in g.itertuples():
        by_sido.setdefault(r.sido_cd, {})[r.short.split(" ")[0]] = r.sgg_nm
    city_cnt = {}
    for sd, m in by_sido.items():
        for short in m:
            if short.endswith(("시", "군")):
                city_cnt.setdefault(short, []).append(sd)
    # '광주시' 는 광주광역시를 줄여 쓴 말과 섞이므로 시도 없이 쓴 경우는 쓰지 않는다
    unique_city = {c: (v[0], by_sido[v[0]][c]) for c, v in city_cnt.items() if len(v) == 1 and c != "광주시"}
    # 사업장 이름용: 시·군 글자를 뗀 지명 (예: 익산 -> 익산시). 두 곳 이상에 걸리면 쓰지 않는다.
    stem = {}
    for c, v in unique_city.items():
        stem.setdefault(c[:-1], []).append(v)
    plant_stem = {k: v[0] for k, v in stem.items() if len(v) == 1 and len(k) >= 2}
    sido_nm = dict(zip(g["sido_cd"], g["sido_nm"]))
    return {"by_sido": by_sido, "unique_city": unique_city, "sido_nm": sido_nm, "plant_stem": plant_stem}


def _alt(sd: str, sgg: str):
    if sd in ("29", "46") and sgg:
        short = sgg.split(" ", 1)[1] if " " in sgg else sgg
        return "12", "전남광주통합특별시 " + short
    return None, None


ALIAS_RE = "|".join(re.escape(a) for a, _ in sorted(SIDO_ALIAS, key=lambda x: -len(x[0])))
FULL = re.compile(rf"({ALIAS_RE})\s*([가-힣]{{1,5}}(?:시|군|구))")
CITY = re.compile(r"(?<![가-힣])([가-힣]{1,4}(?:시|군))(?![가-힣])")


def find_full(text: str, gz: Dict) -> List[Dict]:
    out, alias = [], dict(SIDO_ALIAS)
    for m in FULL.finditer(text):
        sd = alias[m.group(1)]
        sg = m.group(2)
        if sd == "12":       # 통합 이름으로 쓴 주소: 29/46 중 시군구가 있는 쪽
            sd = "29" if sg in gz["by_sido"].get("29", {}) else ("46" if sg in gz["by_sido"].get("46", {}) else None)
            if sd is None:
                continue
        if sd == "36":
            full = next(iter(gz["by_sido"].get("36", {}).values()), "세종특별자치시")
        else:
            full = gz["by_sido"].get(sd, {}).get(sg)
        if full is None:
            continue
        out.append({"sido_cd": sd, "sgg_nm": full, "pos": m.start(), "method": "full"})
    return out


def find_city(text: str, gz: Dict, taken: set) -> List[Dict]:
    out = []
    for m in CITY.finditer(text):
        c = m.group(1)
        if c in gz["unique_city"]:
            sd, full = gz["unique_city"][c]
            if any(abs(m.start() - p) < 12 for p in taken):
                continue
            out.append({"sido_cd": sd, "sgg_nm": full, "pos": m.start(), "method": "city_only"})
    return out


PLANT = re.compile(r"(?<![가-힣])([가-힣]{2,3})\s?(?:제?\d\s?)?(공장|사업장|사업소|제철소|물류센터|연구소)")


def find_plant(text: str, gz: Dict) -> List[Dict]:
    """'익산공장', '창원 제2공장' 처럼 지명 + 사업장 종류. 지명이 전국에 하나뿐인 시·군일 때만."""
    out = []
    for m in PLANT.finditer(text):
        st = m.group(1)
        if st in gz["plant_stem"]:
            sd, full = gz["plant_stem"][st]
            out.append({"sido_cd": sd, "sgg_nm": full, "pos": m.end(), "method": "plant_name"})
    return out


def site_type(prefix: str) -> str:
    """주소 바로 앞 글자(prefix)에서 주소에 가장 가까운 유형 낱말로 정한다 (표에서 사업장 이름이 주소 앞에 온다)."""
    best, pos = "사업장", -1
    for name, pat in TYPE_KW + [("본사", r"본사|본점")]:
        for m in re.finditer(pat, prefix):
            if m.end() > pos:
                best, pos = name, m.end()
    return best


# ------------------------------------------------------------------ 원문
ANNUAL = re.compile(r"^(\[[^\]]+\])?사업보고서\((\d{4})\.(\d{2})\)$")


def latest_annual(ticker: str, corp_code: str):
    f = LISTS / f"{ticker}.csv"
    if f.exists():
        lst = pd.read_csv(f, dtype=str)
    else:
        lst = events.fetch_list(corp_code, bgn="20240101")
        lst.to_csv(f, index=False, encoding="utf-8-sig")
    rows = []
    for r in lst.itertuples():
        m = ANNUAL.match(events.norm_title(str(r.report_nm)))
        if m and not m.group(1):
            rows.append((f"{m.group(2)}.{m.group(3)}", r.rcept_no, r.rcept_dt))
    if not rows:
        return None
    return sorted(rows, key=lambda x: x[2])[-1]


def sections(t: str) -> Dict[str, str]:
    out = {}
    hq = re.search(r"본사의\s*주소", t)
    out["hq"] = t[hq.end(): hq.end() + 250] if hq else ""
    bon = [m.start() for m in re.finditer(r"본점\s*소재지", t)]
    out["hq2"] = t[bon[0]: bon[0] + 400] if bon else ""
    out["history"] = t[bon[0]: bon[0] + 2000] if bon else ""
    heads = [m.start() for m in re.finditer(r"원재료\s*및\s*생산설비", t)]
    body = ""
    for h in heads:
        nxt = re.search(r"매출\s*및\s*수주\s*상황", t[h + 30:])
        seg = t[h: h + 30 + (nxt.start() if nxt else 60000)]
        if len(seg) > 400:          # 목차 줄은 짧다
            body = seg[:60000]
            break
    out["facility"] = body
    return out


def extract(ticker: str, corp_code: str, gz: Dict) -> (Dict, List[Dict]):
    la = latest_annual(ticker, corp_code)
    if la is None:
        return {"ticker": ticker, "status": "no_report"}, []
    fy, rno, rdt = la
    t = events.doc_text(rno, DOCS)
    if t.startswith("__ERROR__"):
        return {"ticker": ticker, "status": "doc_error", "rcept_no": rno}, []
    s = sections(t)
    rows = []
    hq_txt = s["hq"] if find_full(s["hq"], gz) else s["hq2"]
    hq = find_full(hq_txt, gz)
    if hq:
        arrow = max(hq_txt.rfind("⇒"), hq_txt.rfind("→"), hq_txt.rfind("변경 후"))
        after = [x for x in hq if x["pos"] > arrow] if arrow >= 0 else []
        h = after[0] if after else hq[0]
        rows.append({**h, "site_type": "본사", "context": re.sub(r"\s+", " ", (s["hq"] or s["hq2"])[:120])})
    for sec in ("facility", "history"):
        txt = s[sec]
        if not txt:
            continue
        fm = find_full(txt, gz)
        cm = find_city(txt, gz, {x["pos"] for x in fm}) if sec == "facility" else []
        pm = find_plant(txt, gz) if sec == "facility" else []
        for x in fm + cm + pm:
            ctx = txt[max(0, x["pos"] - 80): x["pos"] + 80]
            typ = site_type(txt[max(0, x["pos"] - 40): x["pos"]])
            if typ == "본사":
                continue                     # 본사는 본사 추출에서만
            rows.append({**x, "site_type": typ, "context": re.sub(r"\s+", " ", ctx)[:120], "section": sec})
    st = {"ticker": ticker, "status": "ok", "rcept_no": rno, "report_fy": fy, "has_facility_section": bool(s["facility"]),
          "has_hq_section": bool(s["hq"] or s["hq2"]), "n_raw": len(rows)}
    for r in rows:
        r.update({"entity_id": ticker, "source_doc": rno, "report_fy": fy})
    return st, rows


def targets() -> pd.DataFrame:
    tg = C.read_targets()[["entity_id", "name", "group_2g"]]
    m = db.read_sql("SELECT entity_id, name FROM fd_driver_map_2d WHERE status IN ('driver','ts_only')").drop_duplicates(
        "entity_id").assign(group_2g="2d 확대")
    t = pd.concat([tg, m[~m["entity_id"].isin(tg["entity_id"])]], ignore_index=True)
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "corp_code": str})
    return t.merge(k1[["ticker", "corp_code", "ksic", "industry_group"]].rename(columns={"ticker": "entity_id"}),
                   on="entity_id", how="left")


def main(limit: int = None, threads: int = 4, write: bool = True):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t0 = time.time()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    gz = gazetteer()
    t = targets()
    if limit:
        t = t.head(limit)
    stats, rows = [], []

    def one(r):
        try:
            return extract(r.entity_id, r.corp_code, gz)
        except Exception as ex:      # noqa: BLE001
            return {"ticker": r.entity_id, "status": "error", "error": repr(ex)[:150]}, []

    with ThreadPoolExecutor(max_workers=threads) as ex:
        for k, (st, rr) in enumerate(ex.map(one, list(t.itertuples()))):
            stats.append(st)
            rows += rr
            if (k + 1) % 100 == 0:
                log.info("%d/%d (%.0fs)", k + 1, len(t), time.time() - t0)
    st = pd.DataFrame(stats).rename(columns={"ticker": "entity_id"}).merge(t, on="entity_id", how="left")
    raw = pd.DataFrame(rows)
    now = datetime.now().replace(microsecond=0)
    if len(raw):
        agg = raw.groupby(["entity_id", "sido_cd", "sgg_nm", "site_type"]).agg(
            n_mentions=("pos", "size"),
            method=("method", lambda x: next(m for m in ("full", "city_only", "plant_name") if (x == m).any())),
            context=("context", "first"), source_doc=("source_doc", "first"), report_fy=("report_fy", "first")).reset_index()
    else:
        agg = pd.DataFrame(columns=["entity_id", "sido_cd", "sgg_nm", "site_type"])
    alt = [_alt(a, b) for a, b in zip(agg["sido_cd"], agg["sgg_nm"])]
    agg["sido_cd_alt"] = [a for a, _ in alt]
    agg["sgg_nm_alt"] = [b for _, b in alt]
    agg["weight"] = None
    agg["extracted_at"], agg["run_id"] = now, run_id
    n = 0
    if write and not limit:
        db.execute_fd_ddl(DDL)
        old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{T_REG}`")["entity_id"].tolist()
        if old:
            db.delete_fd(T_REG, {"entity_id": old})
        n = db.write_fd(agg, T_REG, ["entity_id", "sido_cd", "sgg_nm", "site_type"])
    # 품질
    per = agg.groupby("entity_id")
    st["n_sites"] = st["entity_id"].map(per.size()).fillna(0).astype(int)
    st["n_factory"] = st["entity_id"].map(agg[agg["site_type"] == "공장"].groupby("entity_id").size()).fillna(0).astype(int)
    st["has_hq"] = st["entity_id"].isin(agg.loc[agg["site_type"] == "본사", "entity_id"])
    st["hq_only"] = st["has_hq"] & (st["n_sites"] == 1)
    ok = st[st["status"] == "ok"]
    s = {"run_id": run_id, "rows_written": n, "firms": len(st), "status": st["status"].value_counts().to_dict(),
         "firms_with_any_site": int((st["n_sites"] > 0).sum()), "firms_with_hq": int(st["has_hq"].sum()),
         "firms_with_factory": int((st["n_factory"] > 0).sum()), "hq_only": int(st["hq_only"].sum()),
         "no_site_at_all": int(((st["status"] == "ok") & (st["n_sites"] == 0)).sum()),
         "sites_per_firm": ok["n_sites"].describe().round(2).to_dict(),
         "sites_per_firm_hist": ok["n_sites"].clip(upper=10).value_counts().sort_index().to_dict(),
         "site_type": agg["site_type"].value_counts().to_dict(), "method": agg["method"].value_counts().to_dict(),
         "factory_by_sido": agg[agg["site_type"] == "공장"].groupby("sido_cd").size().to_dict(),
         "rows_alt12": int(agg["sido_cd_alt"].notna().sum()),
         "report_fy": st["report_fy"].value_counts().to_dict() if "report_fy" in st else {},
         "seconds": round(time.time() - t0)}
    tag = "_test" if limit else ""
    agg.to_csv(OUT / f"k10_company_region{tag}.csv", index=False, encoding="utf-8-sig")
    st.to_csv(OUT / f"k10_firm_status{tag}.csv", index=False, encoding="utf-8-sig")
    raw.to_csv(OUT / f"k10_raw_mentions{tag}.csv", index=False, encoding="utf-8-sig")
    st[(st["status"] == "ok") & (st["n_factory"] == 0)].to_csv(OUT / f"k10_no_factory{tag}.csv", index=False,
                                                               encoding="utf-8-sig")
    st[st["hq_only"]].to_csv(OUT / f"k10_hq_only{tag}.csv", index=False, encoding="utf-8-sig")
    (OUT / f"k10_summary{tag}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str),
                                               encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    main(a.limit, a.threads)
