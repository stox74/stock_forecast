"""
ECOS 통계 코드 목록 내려받기 + 현재 매핑(economy_index_codes.py) 검증 도구 (v3)

하는 일
  1. 통계표 전체 목록(StatisticTableList)을 받는다.
  2. ECOS_INDEX_MAP 에 있는 통계표마다 항목 목록(StatisticItemList)을 받는다.
     항목 코드, 항목 이름, 단위, 주기, 수록 시작/종료 시점이 들어 있다.
  3. 매핑의 각 지표를 항목 목록과 대조해 문제를 표시한다.
     - 항목 코드가 목록에 없음
     - 월(M) 주기 수록 종료가 오래됨 (통계표 개편/폐지 가능성)
     - 단위가 % 등 비율인데 이름은 수준값처럼 보임
     - 서로 다른 지표가 같은 값으로 내려옴 (최근 24개월 비교)
  4. 선택: 검색어로 통계표/항목을 찾는다 (SEARCH_WORDS).

결과: ecos_catalog_v3.xlsx (시트: 매핑검증, 항목목록, 통계표목록, 검색결과)

DB 쓰기 없음. 코드에 이모지 없음.
실행 위치: BOK 모듈이 있는 폴더 (economy_index_codes.py, bok_client.py 와 같은 폴더)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date
from typing import Dict, List, Optional

import pandas as pd
import requests

BASE_URL = "https://ecos.bok.or.kr/api"
OUT_XLSX = "ecos_catalog_v3.xlsx"

# 확인하고 싶은 검색어 (통계표 이름 기준). 필요하면 추가.
SEARCH_WORDS = ["주택매매가격", "주택전세가격", "수출물가", "수입물가", "예금은행 대출금", "가계대출"]

# 매핑 교체 후보 통계표: 항목 목록만 추가로 받는다 (시트 '추가항목')
EXTRA_STAT_CODES = [
    "401Y015",                                   # 수입물가지수(기본분류)
    "104Y016",                                   # 예금은행 대출금(말잔)
    "151Y005", "151Y002",                        # 예금취급기관 가계대출(용도별, 업권별)
    "161Y011",                                   # M2 경제주체별 보유현황(말잔, 계절조정) 신지표
    "901Y113", "901Y144", "901Y149",             # 주택매매가격지수 (기준월 2025.03, 2026.01, 2026.06)
    "901Y114", "901Y145", "901Y150",             # 주택전세가격지수 (같은 기준월)
]

# 월 주기 수록 종료가 이 개월 수보다 오래되면 '종료 의심'
STALE_MONTHS = 6


def _add_data_parent_to_path() -> None:
    """실행 폴더와 스크립트 폴더에서 위로 올라가며 DATA/KEYS.py 가 있는 폴더를 sys.path 에 추가."""
    starts = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    for start in starts:
        d = start
        while True:
            if os.path.isfile(os.path.join(d, "DATA", "KEYS.py")):
                if d not in sys.path:
                    sys.path.insert(0, d)
                return
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent


def get_api_key() -> str:
    """API 키: DATA/KEYS.py 의 KEYS['ECOS_API_KEY'] (없으면 KEYS['BOK']) -> 환경변수 ECOS_API_KEY."""
    _add_data_parent_to_path()
    try:
        from DATA.KEYS import KEYS  # type: ignore
        key = KEYS.get("ECOS_API_KEY") or KEYS.get("BOK")
        if key:
            return key
    except Exception as e:
        print(f"[참고] DATA.KEYS 를 읽지 못함: {e}")
    key = os.getenv("ECOS_API_KEY", "")
    if not key:
        raise SystemExit("ECOS API 키를 찾지 못했습니다. DATA/KEYS.py 의 KEYS 에 'ECOS_API_KEY' 가 있는지, "
                         "실행 폴더의 상위 어딘가에 DATA 폴더가 있는지 확인하세요.")
    return key


def ecos_get(service: str, key: str, *parts: str, start: int = 1, end: int = 10000) -> List[dict]:
    """ECOS 목록형 서비스 호출. 오류 코드면 빈 리스트와 함께 메시지 출력."""
    url = f"{BASE_URL}/{service}/{key}/json/kr/{start}/{end}"
    if parts:
        url += "/" + "/".join(str(p) for p in parts)
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            if attempt == 2:
                print(f"[FAIL] {service} {parts}: {e}")
                return []
            time.sleep(1.5)
    if service in data:
        return data[service].get("row", [])
    res = data.get("RESULT", {})
    code = res.get("CODE", "")
    if code and code != "INFO-200":  # INFO-200 = 해당하는 데이터가 없음
        print(f"[ECOS] {service} {parts}: {code} {res.get('MESSAGE', '')}")
    return []


def month_index(t: str) -> Optional[int]:
    """'202609' -> 연*12+월. 형식이 다르면 None."""
    t = str(t or "").strip()
    if len(t) >= 6 and t[:6].isdigit():
        return int(t[:4]) * 12 + int(t[4:6])
    return None


def load_index_map() -> Dict[str, dict]:
    sys.path.insert(0, os.getcwd())
    import economy_index_codes  # type: ignore
    return economy_index_codes.ECOS_INDEX_MAP


def recent_values(key: str, stat_code: str, items: List[str], months: int = 24) -> pd.Series:
    """최근 N개월 값 (중복 시계열 비교용)."""
    today = date.today()
    end = f"{today.year}{today.month:02d}"
    y, m = today.year, today.month - months
    while m <= 0:
        y -= 1
        m += 12
    start = f"{y}{m:02d}"
    it = list(items) + ["?"] * (4 - len(items))
    rows = ecos_get("StatisticSearch", key, stat_code, "M", start, end, *it[:4], end=1000)
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({r["TIME"]: pd.to_numeric(r.get("DATA_VALUE"), errors="coerce") for r in rows})
    return s.sort_index()


def main() -> None:
    key = get_api_key()
    index_map = load_index_map()
    today_idx = date.today().year * 12 + date.today().month

    # 1. 통계표 전체 목록
    print("통계표 목록 받는 중...")
    tables = pd.DataFrame(ecos_get("StatisticTableList", key, end=100000))
    print(f"  통계표 {len(tables):,}개")

    # 2. 매핑에 쓰인 통계표의 항목 목록
    stat_codes = sorted({str(v["stat_code"]) for v in index_map.values()})
    item_frames = []
    for sc in stat_codes:
        rows = ecos_get("StatisticItemList", key, sc)
        if rows:
            item_frames.append(pd.DataFrame(rows))
        time.sleep(0.2)
    items = pd.concat(item_frames, ignore_index=True) if item_frames else pd.DataFrame()
    print(f"  항목 {len(items):,}개 (통계표 {len(stat_codes)}개)")

    # 3. 매핑 검증
    checks = []
    series_cache: Dict[str, pd.Series] = {}
    for name, info in index_map.items():
        sc = str(info["stat_code"])
        its = [str(x) for x in (info.get("items") or [])]
        tname = ""
        if not tables.empty and "STAT_CODE" in tables:
            hit = tables[tables["STAT_CODE"] == sc]
            tname = hit["STAT_NAME"].iloc[0] if len(hit) else "(통계표 목록에 없음)"
        sub = items[items["STAT_CODE"] == sc] if not items.empty else pd.DataFrame()
        sub_m = sub[sub["CYCLE"] == "M"] if not sub.empty else sub

        resolved, units, ends, flags = [], [], [], []
        for pos, code in enumerate(its, start=1):
            m = sub_m[sub_m["ITEM_CODE"] == code] if not sub_m.empty else pd.DataFrame()
            if m.empty and not sub.empty:
                m_any = sub[sub["ITEM_CODE"] == code]
                if m_any.empty:
                    flags.append(f"item{pos} '{code}' 목록에 없음")
                    resolved.append(f"{code}=?")
                    continue
                flags.append(f"item{pos} '{code}' 월 주기 없음 (주기: {','.join(sorted(m_any['CYCLE'].unique()))})")
                m = m_any
            if m.empty:
                resolved.append(f"{code}=?")
                continue
            row = m.iloc[0]
            resolved.append(f"{code}={row.get('ITEM_NAME', '')}")
            u = str(row.get("UNIT_NAME", "") or "")
            if u:
                units.append(u)
            et = row.get("END_TIME", "")
            if et:
                ends.append(str(et))

        end_time = min(ends) if ends else ""
        ei = month_index(end_time)
        if ei is not None and today_idx - ei > STALE_MONTHS:
            flags.append(f"수록 종료 의심 (END_TIME {end_time})")
        unit = " / ".join(dict.fromkeys(units))
        if any(k in unit for k in ["%", "퍼센트"]) and not any(k in name for k in ["률", "금리", "율", "국고채", "채권"]):
            flags.append(f"단위가 비율({unit})인데 이름은 수준값")

        series_cache[name] = recent_values(key, sc, its)
        time.sleep(0.2)
        checks.append({
            "지표": name, "category": info.get("category", ""), "stat_code": sc, "통계표명": tname,
            "items": ",".join(its), "항목 해석": " | ".join(resolved), "단위": unit,
            "수록 종료(M)": end_time, "최근값 개수": len(series_cache[name]), "문제": "; ".join(flags),
        })

    chk = pd.DataFrame(checks)

    # 같은 값이 내려오는 지표 쌍
    names = [n for n, s in series_cache.items() if len(s) >= 6]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sa, sb = series_cache[a], series_cache[b]
            common = sa.index.intersection(sb.index)
            if len(common) >= 6 and (sa[common] == sb[common]).all():
                for n, other in ((a, b), (b, a)):
                    k = chk.index[chk["지표"] == n][0]
                    prev = chk.at[k, "문제"]
                    chk.at[k, "문제"] = (prev + "; " if prev else "") + f"'{other}'와 최근 값 동일"

    # 4. 검색어
    found = []
    if not tables.empty:
        for w in SEARCH_WORDS:
            hit = tables[tables["STAT_NAME"].astype(str).str.contains(w.replace(" ", ""), regex=False)
                         | tables["STAT_NAME"].astype(str).str.contains(w, regex=False)]
            for _, r in hit.iterrows():
                found.append({"검색어": w, "STAT_CODE": r.get("STAT_CODE"), "STAT_NAME": r.get("STAT_NAME"),
                              "CYCLE": r.get("CYCLE"), "SRCH_YN": r.get("SRCH_YN"), "ORG_NAME": r.get("ORG_NAME")})
    srch = pd.DataFrame(found)

    # 5. 교체 후보 통계표 항목 목록
    extra_frames = []
    for sc in EXTRA_STAT_CODES:
        rows = ecos_get("StatisticItemList", key, sc)
        if rows:
            extra_frames.append(pd.DataFrame(rows))
        else:
            print(f"[참고] {sc}: 항목 없음")
        time.sleep(0.2)
    extra = pd.concat(extra_frames, ignore_index=True) if extra_frames else pd.DataFrame()
    print(f"  교체 후보 항목 {len(extra):,}개 (통계표 {len(EXTRA_STAT_CODES)}개)")

    with pd.ExcelWriter(OUT_XLSX) as xw:
        chk.to_excel(xw, sheet_name="매핑검증", index=False)
        items.to_excel(xw, sheet_name="항목목록", index=False)
        tables.to_excel(xw, sheet_name="통계표목록", index=False)
        srch.to_excel(xw, sheet_name="검색결과", index=False)
        extra.to_excel(xw, sheet_name="추가항목", index=False)

    bad = chk[chk["문제"] != ""]
    print(f"\n저장: {os.path.abspath(OUT_XLSX)}")
    print(f"문제 표시된 지표 {len(bad)}개 / 전체 {len(chk)}개")
    if len(bad):
        print(bad[["지표", "stat_code", "items", "단위", "수록 종료(M)", "문제"]].to_string(index=False))


if __name__ == "__main__":
    main()
