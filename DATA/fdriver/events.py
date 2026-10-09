# -*- coding: utf-8 -*-
"""실적 발표 이벤트표 (2a 명세서 3절): DART 공시검색 -> fd_earnings_event.

event_type
- prelim     연결재무제표기준영업(잠정)실적(공정공시)          분기 잠정실적 (값: 연결 매출)
- prelim_sep 영업(잠정)실적(공정공시)                         별도 잠정실적 (시점 근거로만, 값은 저장하지 않음)
- pl_change  매출액또는손익구조30%(대규모법인은15%)이상변경    연간 실적 (값: 연간 매출, 4분기 이벤트)
- periodic   분기보고서·반기보고서·사업보고서                  확정치 공시
제외: 실적 전망(가이던스), 결산실적공시예고, 자회사 손익구조변경, 증권발행실적보고서

규칙 (사전 조사 s1~s3 를 옮김)
- 실적 분기: 본문 '실적기간 당기실적 YYYY-MM-DD ~ YYYY-MM-DD' 또는 예전 서식 '당 기 : YYYY.MM.DD~YYYY.MM.DD'
  (기간이 80일 미만이면 월 단위 공시로 보고 뺀다) -> 분기 라벨 ('14.4Q) ('14년 4분기) (`20년 1분기)
  -> 둘 다 없으면 접수일 직전 분기말. 정기보고서는 제목의 (YYYY.MM). 손익구조변경은 접수일로 사업연도
  (원공시 접수일이 1~6월이면 전년도).
- 정정공시([기재정정] 등)는 원공시에 연결한다. 이벤트 날짜는 원공시 접수일. 본문이 없는 정정공시는 같은 종류의
  직전 원공시로 연결한다. 원공시가 조회 기간 밖이라 정정만 있으면, 본문의 '정정관련 공시서류제출일'을 원공시일로 쓴다.
- 한 분기·종류에 원공시가 여럿이면 가장 이른 것. 손익구조변경은 연결 기준을 먼저 쓴다.
- 매출: 본문 표의 단위(조원 단위 주석이 있으면 조원)와 '매출액 당해실적' 마지막 값, 원 단위로 저장.

쓰기는 db.write_fd(fd_earnings_event) 와 db.execute_fd_ddl 로만 한다.
"""
import io
import logging
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from . import db, entity
from .config import get_key, output_path

log = logging.getLogger(__name__)

T_EVENT = "fd_earnings_event"
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DOC_URL = "https://opendart.fss.or.kr/api/document.xml"
START = "20100101"
UNIT = {"조원": 1e12, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}
EVENT_TYPES = ("prelim", "prelim_sep", "pl_change", "periodic")
MIN_QUARTER_DAYS = 80          # 본문 실적기간이 이보다 짧으면 월 단위 공시로 보고 분기 이벤트에서 뺀다
LEGACY_DOC_CACHE = ("stage2a", "checks", "step1", "docs_text")   # 사전 조사 때 받은 본문
# 월간 공시의 실적 기간 표기: (2023년1월), ('19년10월), (2018.02월), ( 2012.9월)
MONTHLY_LABEL = re.compile(r"\(\s*['’`]?\d{2,4}\s*(?:년|\.)\s*\d{1,2}\s*월\s*\)")

DDL_EVENT = f"""
CREATE TABLE IF NOT EXISTS `{T_EVENT}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `event_type` VARCHAR(10) NOT NULL,
  `rcept_dt` DATE NOT NULL,
  `rcept_no` CHAR(14) NOT NULL,
  `report_nm` VARCHAR(200) NOT NULL,
  `revenue_won` DOUBLE NULL,
  `unit` VARCHAR(10) NULL,
  `fs_type` VARCHAR(10) NULL,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `event_type`),
  KEY `idx_rcept_dt` (`rcept_dt`),
  CONSTRAINT `fk_fd_earnings_event_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
EVENT_COLUMNS = ["entity_id", "fiscal_quarter", "event_type", "rcept_dt", "rcept_no", "report_nm",
                 "revenue_won", "unit", "fs_type"]


# ---------------------------------------------------------------- 분류
def norm_title(nm: str) -> str:
    return re.sub(r"\s+", "", str(nm))


def is_correction(nm: str) -> bool:
    return norm_title(nm).startswith("[")


def classify(nm: str) -> str:
    """공시 제목 -> event_type ('' 이면 대상 아님)."""
    n = re.sub(r"^\[[^\]]+\]", "", norm_title(nm))
    if n.startswith("연결재무제표기준영업(잠정)실적"):
        return "prelim"
    if n.startswith("영업(잠정)실적"):
        return "prelim_sep"
    if n.startswith("매출액또는손익구조") and "자회사" not in n:
        return "pl_change"
    if re.match(r"^(분기보고서|반기보고서|사업보고서)\(\d{4}\.\d{2}\)$", n):
        return "periodic"
    return ""


# ---------------------------------------------------------------- 분기 표기
def fiscal_quarter_label(period_end: pd.Timestamp, fiscal_year_end: int = 12) -> str:
    """분기말 날짜 -> 'YYYYQn' (결산월 기준, 회계연도는 끝나는 해)."""
    m = period_end.month
    q = ((m - fiscal_year_end - 1) % 12) // 3 + 1
    fy = period_end.year if m <= fiscal_year_end else period_end.year + 1
    return f"{fy}Q{q}"


def fiscal_quarter_end(label: str, fiscal_year_end: int = 12) -> pd.Timestamp:
    fy, q = int(label[:4]), int(label[-1])
    end_month = (fiscal_year_end - 3 * (4 - q) - 1) % 12 + 1
    year = fy if end_month <= fiscal_year_end else fy - 1
    return pd.Timestamp(year, end_month, 1) + pd.offsets.MonthEnd(0)


def _qend(y: int, q: int) -> pd.Timestamp:
    return pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0)


def quarter_before(dt: pd.Timestamp) -> pd.Timestamp:
    """접수일 직전 분기말."""
    return (dt - pd.offsets.QuarterEnd(1)).normalize()


# ---------------------------------------------------------------- 본문 파싱
def _num(s: str):
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _orig_filing_date(t: str) -> Optional[pd.Timestamp]:
    m = re.search(r"정정관련 공시서류제출일\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", t)
    return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_prelim(t: str, rcept_dt: Optional[pd.Timestamp] = None) -> Dict:
    out = {"period_end": None, "period_src": None, "period_days": None, "unit": None, "revenue_raw": None,
           "orig_filing_date": _orig_filing_date(t)}
    body_start = max(t.rfind("실적내용"), 0)
    body = t[body_start - 400 if body_start > 400 else 0:]
    m = list(re.finditer(r"당기실적\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", t))
    # 예전 서식: '당 기 : 2010.11.01~2010.11.30' (월 단위 잠정실적도 이 서식)
    m_old = re.search(r"당\s*기\s*:\s*(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})", t)
    if m:
        s0, e0 = pd.Timestamp(m[-1].group(1)), pd.Timestamp(m[-1].group(2))
        out["period_end"], out["period_src"], out["period_days"] = e0, "date_range", (e0 - s0).days + 1
    elif m_old:
        g = [int(x) for x in m_old.groups()]
        s0, e0 = pd.Timestamp(g[0], g[1], g[2]), pd.Timestamp(g[3], g[4], g[5])
        out["period_end"], out["period_src"], out["period_days"] = e0, "date_range_old", (e0 - s0).days + 1
    else:
        q_ = "['`’‘]?"
        for p in (r"\(\s*" + q_ + r"(\d{2,4})\s*\.\s*([1-4])\s*Q\s*\)",
                  r"\(\s*" + q_ + r"(\d{2,4})\s*년\s*([1-4])\s*분기\s*\)"):
            mm = re.search(p, body) or re.search(p, t)
            if mm:
                y = int(mm.group(1))
                y = y + 2000 if y < 100 else y
                out["period_end"], out["period_src"] = _qend(y, int(mm.group(2))), "label"
                break
    if out["period_src"] in (None, "label") and MONTHLY_LABEL.search(t):
        # 2c 작업 3 수정: 예전 서식 월간 공시(판매 대수·월 매출, '당기실적 (2023년1월)', "('19년10월)", '(2018.02월)')는
        # 날짜 범위가 없어 접수일 직전 분기로 잡히던 것을 월 단위로 보고 뺀다.
        out["period_src"], out["period_days"] = "monthly_label", 31
        if out["period_end"] is None and rcept_dt is not None:
            out["period_end"] = quarter_before(out["orig_filing_date"] or rcept_dt)
    if out["period_end"] is None and rcept_dt is not None:
        out["period_end"] = quarter_before(out["orig_filing_date"] or rcept_dt)
        out["period_src"] = "inferred_from_filing_date"
    mu = re.search(r"단위\s*:\s*(조원|억원|백만원|천원|원)", body) or re.search(r"단위\s*:\s*(조원|억원|백만원|천원|원)", t)
    if mu:
        out["unit"] = mu.group(1)
    if re.search(r"조원\s*단위", t) and out["unit"] != "조원":
        out["unit"] = "조원"
    mr = list(re.finditer(r"매출액\s*당해실적\s*(-?[\d,]+(?:\.\d+)?)", t))
    if mr:
        out["revenue_raw"] = _num(mr[-1].group(1))
    return out


def parse_pl_change(t: str, rcept_dt: pd.Timestamp) -> Dict:
    out = {"period_end": None, "period_src": "inferred_from_filing_date", "unit": None, "revenue_raw": None,
           "orig_filing_date": _orig_filing_date(t), "fs_type": None}
    m = re.search(r"재무제표의 종류\s*(연결|개별|별도)", t)
    if m:
        out["fs_type"] = m.group(1)
    mu = list(re.finditer(r"단위\s*:\s*(조원|억원|백만원|천원|원)", t))
    if mu:
        out["unit"] = mu[-1 if "정정" in t[:200] else 0].group(1)
    mr = list(re.finditer(r"-\s*매출액\s*(?:\([^)]*\))?\s*(-?[\d,]+)", t))
    if mr:
        out["revenue_raw"] = _num(mr[-1].group(1))
    base = out["orig_filing_date"] or rcept_dt
    fy = base.year - 1 if base.month <= 6 else base.year
    out["period_end"] = pd.Timestamp(fy, 12, 31)
    return out


# ---------------------------------------------------------------- DART 호출
def doc_cache_dir() -> Path:
    d = output_path("stage2a", "cache", "dart_docs")
    legacy = output_path(*LEGACY_DOC_CACHE, mkdir=False)
    if legacy.is_dir():
        for f in legacy.glob("*.txt"):
            if not (d / f.name).exists():
                shutil.copy2(f, d / f.name)
    return d


def fetch_list(corp_code: str, bgn: str = START, end: Optional[str] = None) -> pd.DataFrame:
    import requests
    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    rows, page = [], 1
    while True:
        r = requests.get(LIST_URL, params={"crtfc_key": get_key("DART"), "corp_code": corp_code, "bgn_de": bgn,
                                           "end_de": end, "page_no": page, "page_count": 100,
                                           "last_reprt_at": "N"}, timeout=30)
        js = r.json()
        if js.get("status") == "013":
            break
        if js.get("status") != "000":
            raise RuntimeError(f"DART list error {js.get('status')}: {js.get('message')}")
        rows += js["list"]
        if page >= int(js["total_page"]):
            break
        page += 1
        time.sleep(0.15)
    return pd.DataFrame(rows)


def doc_text(rcept_no: str, cache: Path) -> str:
    """본문 텍스트 (태그 제거). 없으면 '__ERROR__ <status> <message>'. 캐시에 저장한다."""
    f = cache / f"{rcept_no}.txt"
    if f.exists():
        return f.read_text(encoding="utf-8")
    import requests
    for attempt in range(3):
        r = requests.get(DOC_URL, params={"crtfc_key": get_key("DART"), "rcept_no": rcept_no}, timeout=60)
        if r.content[:2] == b"PK":
            break
        msg = re.search(r"<status>(\d+)</status><message>([^<]*)</message>", r.content.decode("utf-8", "replace"))
        status = msg.group(1) if msg else "?"
        if status == "020":                 # 요청 제한: 쉬고 다시
            time.sleep(5 * (attempt + 1))
            continue
        time.sleep(0.2)
        return f"__ERROR__ {status} {msg.group(2) if msg else ''}"
    else:
        return "__ERROR__ 020 retry exhausted"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name, rule, files = pick_main_file(z, rcept_no)
    _log_zip_choice(cache, rcept_no, name, rule, files)
    t = clean_text(_decode(z.read(name)))
    f.write_text(t, encoding="utf-8")
    time.sleep(0.2)
    return t


# ---------------------------------------------------------------- zip 본문 고르기 (2c 작업 2 수정)
# document.xml zip 에는 본문 외에 감사보고서·검토보고서가 함께 들어 있을 수 있고 순서가 일정하지 않다
# (사업보고서: <접수번호>.xml 본문, _00760.xml 감사보고서, _00761.xml 연결감사보고서). 예전에는 첫 파일을 읽었다.
ATTACHED_DOC_TITLE = re.compile(r"감사보고서|검토보고서|감사인")
ZIP_LOG = "_zip_choice.csv"


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def clean_text(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = re.sub(r"\.xforms[^{]*\{[^}]*\}", " ", t).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def zip_titles(z: zipfile.ZipFile) -> List[Dict]:
    """zip 안 파일마다 이름, 크기, 문서 제목(<DOCUMENT-NAME>, 없으면 None)."""
    out = []
    for n in z.namelist():
        head = _decode(z.read(n)[:4000])
        m = re.search(r"<DOCUMENT-NAME[^>]*>([^<]*)<", head)
        out.append({"name": n, "size": z.getinfo(n).file_size, "title": m.group(1).strip() if m else None})
    return out


def pick_main_file(z: zipfile.ZipFile, rcept_no: str):
    """본문 파일 고르기. 반환: (파일명, 판별 규칙, 파일 목록).

    1) 파일이 하나면 그 파일 ('single')
    2) '<접수번호>.xml' 이 있으면 그 파일 ('rcept_no_name')
    3) 이름에 '_' 꼬리가 없는 파일이 하나뿐이면 그 파일 ('no_suffix')
    4) 문서 제목이 감사·검토보고서가 아닌 파일이 하나뿐이면 그 파일 ('title')
    5) 판별 실패: 감사·검토보고서가 아닌 파일 중 가장 큰 것 ('fallback_largest', 경고 + 파일 목록 기록)
    """
    files = zip_titles(z)
    names = [f["name"] for f in files]
    if len(names) == 1:
        return names[0], "single", files
    if f"{rcept_no}.xml" in names:
        return f"{rcept_no}.xml", "rcept_no_name", files
    plain = [n for n in names if "_" not in Path(n).stem]
    if len(plain) == 1:
        return plain[0], "no_suffix", files
    main = [f for f in files if not (f["title"] and ATTACHED_DOC_TITLE.search(f["title"]))]
    if len(main) == 1:
        return main[0]["name"], "title", files
    pool = main or files
    name = max(pool, key=lambda f: f["size"])["name"]
    log.warning("DART zip %s: 본문 판별 실패, 가장 큰 파일 %s 사용. 파일 목록: %s", rcept_no, name,
                [(f["name"], f["size"], f["title"]) for f in files])
    return name, "fallback_largest", files


def _log_zip_choice(cache: Path, rcept_no: str, name: str, rule: str, files: List[Dict]) -> None:
    """고른 파일과 전체 파일 목록을 캐시 폴더의 _zip_choice.csv 에 덧붙인다."""
    f = cache / ZIP_LOG
    row = pd.DataFrame([{"rcept_no": rcept_no, "chosen": name, "rule": rule, "n_files": len(files),
                         "files": " | ".join(f"{x['name']}:{x['size']}:{x['title'] or ''}" for x in files),
                         "logged_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}])
    row.to_csv(f, mode="a", header=not f.exists(), index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------- 이벤트표 만들기
def parse_filings(lst: pd.DataFrame, fye: Dict[str, int], cache: Optional[Path] = None,
                  fetch_docs: bool = True) -> pd.DataFrame:
    """공시 목록 -> 공시 한 건당 한 행 (실적 분기, 원공시일, 매출 등)."""
    cache = cache or doc_cache_dir()
    d = lst.copy()
    d["event_type"] = d["report_nm"].map(classify)
    d = d[d["event_type"] != ""].copy()
    d["rcept_dt"] = pd.to_datetime(d["rcept_dt"])
    d["is_correction"] = d["report_nm"].map(is_correction)
    rows = []
    for r in d.itertuples():
        base = {"entity_id": r.entity_id, "event_type": r.event_type, "rcept_dt": r.rcept_dt, "rcept_no": r.rcept_no,
                "report_nm": str(r.report_nm).strip(), "is_correction": r.is_correction}
        if r.event_type == "periodic":
            ym = re.search(r"\((\d{4})\.(\d{2})\)", norm_title(r.report_nm))
            pe = pd.Timestamp(int(ym.group(1)), int(ym.group(2)), 1) + pd.offsets.MonthEnd(0)
            rows.append({**base, "period_end": pe, "period_src": "title", "orig_filing_date": None})
            continue
        t = doc_text(r.rcept_no, cache) if fetch_docs else ""
        if t.startswith("__ERROR__") or not t:
            rows.append({**base, "period_end": None, "period_src": None, "orig_filing_date": None,
                         "doc_error": t[10:] if t else "not fetched"})
            continue
        p = parse_pl_change(t, r.rcept_dt) if r.event_type == "pl_change" else parse_prelim(t, r.rcept_dt)
        if p.get("period_days") is not None and p["period_days"] < MIN_QUARTER_DAYS:
            # 월 단위 잠정실적(하나투어 2010년 등): 분기 이벤트가 아니므로 뺀다
            p["period_src"] = "monthly_excluded"
        if r.event_type == "prelim":
            p["fs_type"] = "연결"
        elif r.event_type == "prelim_sep":
            p["fs_type"] = "별도"
        rows.append({**base, **p})
    out = pd.DataFrame(rows)
    for c in ["period_end", "orig_filing_date"]:
        out[c] = pd.to_datetime(out[c])
    # 본문 없는 정정공시: 같은 기업·종류의 직전 원공시에 연결
    miss = out["period_end"].isna()
    for i in out[miss].index:
        r = out.loc[i]
        prev = out[(out["entity_id"] == r["entity_id"]) & (out["event_type"] == r["event_type"])
                   & (~out["is_correction"]) & (out["rcept_dt"] <= r["rcept_dt"]) & out["period_end"].notna()
                   & (out["period_src"] != "monthly_excluded")]
        if len(prev):
            p = prev.sort_values("rcept_dt").iloc[-1]
            out.loc[i, "period_end"] = p["period_end"]
            out.loc[i, "orig_filing_date"] = p["rcept_dt"]
            out.loc[i, "period_src"] = "correction_linked_to_previous_original"
    for c in ["unit", "revenue_raw", "fs_type", "doc_error"]:
        if c not in out:
            out[c] = None
    out["revenue_won"] = pd.to_numeric(out["revenue_raw"], errors="coerce") * out["unit"].map(UNIT)
    out["fiscal_quarter"] = [fiscal_quarter_label(pe, fye.get(e, 12)) if pd.notna(pe) and src != "monthly_excluded" else None
                             for e, pe, src in zip(out["entity_id"], out["period_end"], out["period_src"])]
    return out


def build_events(parsed: pd.DataFrame) -> pd.DataFrame:
    """공시 단위 -> (기업, 분기, 종류) 단위 이벤트. 원공시를 쓰고, 원공시가 없으면 정정공시의 원공시일."""
    p = parsed[parsed["fiscal_quarter"].notna()].copy()
    p["event_dt"] = p["rcept_dt"].where(~p["is_correction"], p["orig_filing_date"].fillna(p["rcept_dt"]))
    p["_orig"] = (~p["is_correction"]).astype(int)
    p["_cons"] = (p.get("fs_type").fillna("") == "연결").astype(int) if "fs_type" in p else 0
    # 원공시 우선, (손익구조변경은 연결 우선), 그다음 가장 이른 날짜
    p = p.sort_values(["entity_id", "fiscal_quarter", "event_type", "_orig", "_cons", "event_dt"],
                      ascending=[True, True, True, False, False, True])
    first = p.drop_duplicates(["entity_id", "fiscal_quarter", "event_type"], keep="first").copy()
    n_corr = p[p["is_correction"]].groupby(["entity_id", "fiscal_quarter", "event_type"]).size().rename("n_corrections")
    first = first.merge(n_corr, on=["entity_id", "fiscal_quarter", "event_type"], how="left")
    first["n_corrections"] = first["n_corrections"].fillna(0).astype(int)
    first["from_correction_only"] = first["is_correction"]
    first["rcept_dt"] = first["event_dt"]
    keep_value = first["event_type"].isin(["prelim", "pl_change"])
    first["revenue_won"] = first["revenue_won"].where(keep_value)
    first["unit"] = first["unit"].where(keep_value | first["event_type"].eq("prelim_sep"))
    return first.reset_index(drop=True)


def create_table() -> None:
    db.execute_fd_ddl(DDL_EVENT)


def build_and_load(entity_ids: Optional[Iterable[str]] = None, start: str = START) -> Dict:
    """DART 에서 받아 fd_earnings_event 에 적재. 반환: 공시 단위 파싱 결과와 이벤트표."""
    ids = list(entity_ids or entity.PILOTS)
    info = entity.entity_info(ids)
    fye = dict(zip(info["entity_id"], info["fiscal_year_end"].astype(int)))
    lists = []
    for r in info.itertuples():
        df = fetch_list(r.dart_corp_code, bgn=start)
        df["entity_id"] = r.entity_id
        lists.append(df)
    lst = pd.concat(lists, ignore_index=True)
    parsed = parse_filings(lst, fye)
    events = build_events(parsed)
    create_table()
    rows = events[EVENT_COLUMNS].copy()
    rows["rcept_dt"] = rows["rcept_dt"].dt.date
    n = db.write_fd(rows, T_EVENT, ["entity_id", "fiscal_quarter", "event_type"])
    return {"list": lst, "parsed": parsed, "events": events, "rows_written": n}


# ---------------------------------------------------------------- 읽기
def read_events(entity_ids: Iterable[str]) -> pd.DataFrame:
    ids = list(entity_ids)
    if not ids or not db.table_exists(T_EVENT):
        return pd.DataFrame(columns=EVENT_COLUMNS)
    f, p = db.in_clause("e", ids)
    df = db.read_sql(f"SELECT {', '.join('`%s`' % c for c in EVENT_COLUMNS)} FROM `{T_EVENT}` WHERE `entity_id` IN {f}", p)
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"])
    return df


def first_disclosure(events: pd.DataFrame, fye: Dict[str, int]) -> pd.DataFrame:
    """분기별 첫 공시일과 근거 (3절 발표일 규칙 1). 4분기에만 pl_change 를 본다.

    반환: entity_id, period_end, first_dt, first_type, grade(A/B), prelim_dt, prelim_value, periodic_dt
    """
    cols = ["entity_id", "period_end", "first_dt", "first_type", "grade", "prelim_dt", "prelim_value", "periodic_dt",
            "pl_change_dt", "pl_change_value"]
    if events.empty:
        return pd.DataFrame(columns=cols)
    e = events.copy()
    e["period_end"] = [fiscal_quarter_end(q, fye.get(i, 12)) for i, q in zip(e["entity_id"], e["fiscal_quarter"])]
    e["is_q4"] = e["fiscal_quarter"].str.endswith("Q4")
    e = e[(e["event_type"] != "pl_change") | e["is_q4"]]
    rows = []
    grade = {"prelim": "A", "pl_change": "A", "periodic": "A", "prelim_sep": "B"}
    for (eid, pe), g in e.groupby(["entity_id", "period_end"]):
        g = g.sort_values("rcept_dt")
        f = g.iloc[0]
        get = lambda t, c: (g.loc[g["event_type"] == t, c].iloc[0] if (g["event_type"] == t).any() else None)
        rows.append({"entity_id": eid, "period_end": pe, "first_dt": f["rcept_dt"], "first_type": f["event_type"],
                     "grade": grade[f["event_type"]], "prelim_dt": get("prelim", "rcept_dt"),
                     "prelim_value": get("prelim", "revenue_won"), "periodic_dt": get("periodic", "rcept_dt"),
                     "pl_change_dt": get("pl_change", "rcept_dt"), "pl_change_value": get("pl_change", "revenue_won")})
    out = pd.DataFrame(rows, columns=cols)
    for c in ["first_dt", "prelim_dt", "periodic_dt", "pl_change_dt"]:
        out[c] = pd.to_datetime(out[c])
    return out
