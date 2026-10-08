# -*- coding: utf-8 -*-
"""관세청 품목별 수출입실적 API (data.go.kr 1220000/Itemtrade/getItemtradeList) 조회 - 진단용, 읽기만.

수집기(Korea_Market/collection/Korea_Export_Data/한국수출입데이터_수집_V6.ipynb)와 같은 엔드포인트·같은 인증키
(DATA/KEYS.py 의 KEYS['CUSTOMS'] 또는 KEYS['ODPD'])를 쓴다. DB 에는 쓰지 않는다 (쓰기는 수집기만).
- 요청 HS 는 2~6자리 (hsSgn). 응답은 10자리 품목 행 + '총계'(hsCode '-') 행.
- 조회 기간은 한 번에 12개월 이내.
"""
import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote

import pandas as pd

URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


def _key() -> str:
    from DATA.KEYS import KEYS
    k = next((KEYS[x] for x in ("CUSTOMS", "ODPD") if KEYS.get(x)), None)
    if not k:
        raise RuntimeError("KEYS['CUSTOMS'] 없음")
    return k if "%" in k else quote(k, safe="")


def query(hs: str, start_yymm: str, end_yymm: str, retries: int = 3) -> Tuple[Dict, pd.DataFrame, str]:
    """반환: (header, 품목 행 DataFrame, 원문 XML). 총계 행은 뺀다."""
    import requests
    import xmltodict
    url = f"{URL}?serviceKey={_key()}&strtYymm={start_yymm}&endYymm={end_yymm}&hsSgn={hs}"
    for i in range(retries):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** i)
                continue
            p = xmltodict.parse(r.text).get("response", {})
            header = p.get("header") or {}
            items = (p.get("body") or {}).get("items")
            it = items.get("item") if isinstance(items, dict) else None
            if isinstance(it, dict):
                it = [it]
            df = pd.DataFrame(it or [])
            if not df.empty:
                df = df[df["year"].astype(str).str.match(r"^\d{4}\.\d{2}$")].copy()
                df["expDlr"] = pd.to_numeric(df["expDlr"], errors="coerce").fillna(0.0)
                df["month"] = pd.to_datetime(df["year"].str.replace(".", "-")) + pd.offsets.MonthEnd(0)
            time.sleep(0.1)
            return header, df, re.sub(r"serviceKey=[^&\"<]+", "serviceKey=***", r.text)
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                return {"resultCode": "EXC", "resultMsg": str(e)[:200]}, pd.DataFrame(), ""
            time.sleep(2 ** i)
    return {"resultCode": "RETRY", "resultMsg": "retries exhausted"}, pd.DataFrame(), ""


def windows(start_yymm: str, end_yymm: str):
    s = int(start_yymm[:4]) * 12 + int(start_yymm[4:]) - 1
    e = int(end_yymm[:4]) * 12 + int(end_yymm[4:]) - 1
    while s <= e:
        w = min(s + 11, e)
        yield f"{s // 12:04d}{s % 12 + 1:02d}", f"{w // 12:04d}{w % 12 + 1:02d}"
        s = w + 1


def query_range(hs: str, start_yymm: str, end_yymm: str) -> pd.DataFrame:
    parts = [query(hs, a, b)[1] for a, b in windows(start_yymm, end_yymm)]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
