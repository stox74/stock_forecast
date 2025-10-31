# -*- coding: utf-8 -*-

import requests
import pandas as pd
from typing import Optional, List

def fetch_quarterly_equity_fmp(
    symbol: str,
    api_key: str,
    start_date: str = "2010-01-01",
    api_version: str = "stable",
    timeout: int = 30,
    extra_fields: Optional[List[str]] = None
) -> pd.DataFrame:
    """
    FMP Balance Sheet API에서 분기별(total shareholders' equity) 시계열을 수신해 DataFrame으로 반환.
    - symbol: 티커 (예: "AAPL", "A005930.KS" 등)
    - api_key: FMP API Key
    - start_date: 이 날짜(포함) 이후의 데이터만 반환
    - api_version: 'stable' 권장 (FMP 문서 기준)
    - extra_fields: 함께 받고 싶은 추가 필드명 목록 (예: ['totalAssets','totalLiabilities'])

    반환 컬럼:
      ['date', 'symbol', 'total_stockholders_equity', 'period', 'fiscalDateEnding', ...extra_fields]
      date는 Pandas datetime, 과거→최근 오름차순 정렬
    """
    base = f"https://financialmodelingprep.com/{api_version}/balance-sheet-statement"
    url = f"{base}?symbol={symbol}&period=quarter&limit=2000&apikey={api_key}"

    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"FMP API error {r.status_code}: {r.text[:300]}")

    payload = r.json()

    # 응답 형태 방어적 파싱 (리스트 or 딕셔너리 하위 키)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        # 과거/플레이그라운드/버전에 따라 달라질 수 있으므로 가능한 키들을 탐색
        for key in ["financials", "data", "items", "statements", symbol]:
            if key in payload and isinstance(payload[key], list):
                rows = payload[key]
                break
        else:
            rows = []
    else:
        rows = []

    if not rows:
        # 빈 결과 방어
        return pd.DataFrame(columns=["date", "symbol", "total_stockholders_equity", "period", "fiscalDateEnding"])

    # 필드 후보들(버전마다 표기 차이를 흡수)
    equity_keys = [
        "totalStockholdersEquity",
        "totalShareholdersEquity",
        "totalEquity",
        "stockholdersEquity",
        "shareholdersEquity",
    ]

    # DataFrame 구성
    records = []
    for rec in rows:
        # 날짜 필드 후보
        date_val = rec.get("date") or rec.get("fiscalDateEnding") or rec.get("calendarYear")
        # 자기자본 파싱
        equity_val = None
        for k in equity_keys:
            if k in rec:
                equity_val = rec.get(k)
                break

        item = {
            "date": date_val,
            "symbol": rec.get("symbol", symbol),
            "total_stockholders_equity": pd.to_numeric(equity_val, errors="coerce"),
            "period": rec.get("period"),
            "fiscalDateEnding": rec.get("fiscalDateEnding"),
        }

        # 요청 시 추가 필드 포함
        if extra_fields:
            for f in extra_fields:
                item[f] = pd.to_numeric(rec.get(f), errors="ignore")

        records.append(item)

    df = pd.DataFrame.from_records(records)
    # 날짜 정제 및 필터링
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= pd.to_datetime(start_date)]

    # 중복 제거(동일 분기 중복시 최신 한 줄만 남김)
    df = df.drop_duplicates(subset=["date", "symbol"], keep="last").reset_index(drop=True)

    return df
