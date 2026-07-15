"""대만 상장사 월별 매출 수집기.

데이터 소스
1) MOPS 월별 매출 집계 정적 페이지 (과거 이력 수집용)
   https://mops.twse.com.tw/nas/t21/sii/t21sc03_{민국연도}_{월}_0.html
   - 연도는 민국(서기-1911), 인코딩은 Big5
2) TWSE OpenAPI t187ap05_L (최신 월 JSON, 증분 수집용 보조 소스)
"""
import io
import time

import pandas as pd
import requests

from config import (COMPANIES, MARKET, MOPS_URL_TEMPLATES, REQUEST_DELAY_SEC,
                    REQUEST_HEADERS, TWSE_OPENAPI_MONTHLY)


def _find_col(columns, keyword):
    """평탄화된 컬럼명 중 keyword를 포함하는 첫 컬럼을 찾는다."""
    for c in columns:
        if keyword in c:
            return c
    return None


def _flatten_columns(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["_".join(str(x) for x in tup if str(x) != "nan")
                      for tup in df.columns]
    else:
        df.columns = [str(c) for c in df.columns]
    return df


def _to_float(v):
    try:
        s = str(v).replace(",", "").replace("%", "").strip()
        if s in ("", "-", "nan", "None"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_mops_month(year: int, month: int, market: str = MARKET):
    """특정 (서기)연/월의 전 상장사 월 매출 표를 가져와 dict 리스트로 반환.

    페이지가 아직 없으면(미발표 등) None 반환.
    """
    roc_year = year - 1911
    html = None
    for tpl in MOPS_URL_TEMPLATES:
        url = tpl.format(market=market, roc_year=roc_year, month=month)
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"  [warn] 요청 실패 {url}: {e}")
            continue
        if r.status_code == 200 and len(r.content) > 5000:
            r.encoding = "big5"
            html = r.text
            break
    if html is None:
        return None

    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return None

    rows = []
    for df in tables:
        df = _flatten_columns(df)
        code_col = _find_col(df.columns, "公司代號")
        rev_col = _find_col(df.columns, "當月營收")
        if not code_col or not rev_col:
            continue
        name_col = _find_col(df.columns, "公司名稱")
        mom_col = _find_col(df.columns, "上月比較增減")
        yoy_col = _find_col(df.columns, "去年同月增減")

        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            if not code.isdigit():  # 소계/합계 행 제거
                continue
            rev = _to_float(row[rev_col])
            rows.append({
                "company_id": code,
                "company_name": str(row[name_col]).strip() if name_col else None,
                "year": year,
                "month": month,
                "revenue": int(rev) if rev is not None else None,
                "mom_pct": _to_float(row[mom_col]) if mom_col else None,
                "yoy_pct": _to_float(row[yoy_col]) if yoy_col else None,
                "source": "mops",
            })
    return rows or None


def fetch_openapi_latest():
    """TWSE OpenAPI에서 최신 월 매출(JSON)을 가져온다. 실패 시 None."""
    try:
        r = requests.get(TWSE_OPENAPI_MONTHLY, headers=REQUEST_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [warn] OpenAPI 실패: {e}")
        return None

    rows = []
    for item in data:
        # 필드명이 바뀔 수 있어 부분 일치로 방어적으로 탐색
        def pick(kw):
            for k, v in item.items():
                if kw in k:
                    return v
            return None

        code = str(pick("公司代號") or "").strip()
        ym = str(pick("資料年月") or "").strip()  # 예: '11506' (민국 115년 6월)
        if not code.isdigit() or len(ym) < 4:
            continue
        try:
            year = int(ym[:-2]) + 1911
            month = int(ym[-2:])
        except ValueError:
            continue
        rev = _to_float(pick("當月營收"))
        rows.append({
            "company_id": code,
            "company_name": str(pick("公司名稱") or "").strip() or None,
            "year": year,
            "month": month,
            "revenue": int(rev) if rev is not None else None,
            "mom_pct": _to_float(pick("上月比較增減")),
            "yoy_pct": _to_float(pick("去年同月增減")),
            "source": "openapi",
        })
    return rows or None


def _month_range(start, end):
    """('YYYY-MM','YYYY-MM') → [(y,m), ...]"""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def collect_range(conn, start: str, end: str, companies=None):
    """기간 내 MOPS 페이지를 순회하며 대상 기업 매출을 DB에 저장."""
    from db import upsert_revenue
    targets = set(companies or COMPANIES.keys())

    for (y, m) in _month_range(start, end):
        print(f"[collect] {y}-{m:02d} ...", end=" ")
        rows = fetch_mops_month(y, m)
        if not rows:
            print("데이터 없음(미발표이거나 페이지 없음)")
        else:
            picked = [r for r in rows if r["company_id"] in targets]
            # 표시용 이름을 영문으로 통일
            for r in picked:
                r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
            upsert_revenue(conn, picked)
            print(f"{len(picked)}개 기업 저장")
        time.sleep(REQUEST_DELAY_SEC)


def collect_latest(conn, companies=None):
    """OpenAPI로 최신 월 증분 수집. 실패 시 MOPS로 폴백."""
    from datetime import date
    from db import upsert_revenue
    targets = set(companies or COMPANIES.keys())

    rows = fetch_openapi_latest()
    if rows:
        picked = [r for r in rows if r["company_id"] in targets]
        for r in picked:
            r["company_name"] = COMPANIES.get(r["company_id"], r["company_name"])
        upsert_revenue(conn, picked)
        got = {(r['year'], r['month']) for r in picked}
        print(f"[collect-latest] OpenAPI에서 {len(picked)}건 저장 (연월: {sorted(got)})")
        return

    # 폴백: 지난달 MOPS 페이지 시도
    today = date.today()
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    print("[collect-latest] OpenAPI 실패 → MOPS 폴백")
    collect_range(conn, f"{y}-{m:02d}", f"{y}-{m:02d}", companies)
