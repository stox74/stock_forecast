# -*- coding: utf-8 -*-

import requests
import pandas as pd
from typing import Optional

def fetch_quarterly_pbr_fmp(
    symbol: str,
    api_key: str,
    start_date: str = "2010-01-01",
    api_base: str = "https://financialmodelingprep.com/api/v3",
    timeout: int = 30
) -> pd.DataFrame:
    """
    FMP Financial Ratios API에서 분기별 PBR(priceToBookRatio) 시계열을 수신해 DataFrame으로 반환.
    - symbol: 티커(예: "AAPL", "MSFT", "A005930.KS")
    - api_key: FMP API Key
    - start_date: 이 날짜(포함) 이후 데이터만 반환 (문자열, 예: "2010-01-01")
    - api_base: 기본 API 베이스 URL (기본값: v3)
    - timeout: 요청 타임아웃(초)

    반환 컬럼:
      ['date', 'symbol', 'pbr', 'period', 'calendarYear', 'fiscalDateEnding']
      - date: pandas datetime (오름차순 정렬)
      - pbr : priceToBookRatio (float)
    """
    url = f"{api_base}/ratios/{symbol}?period=month&limit=2000&apikey={api_key}"
    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"FMP API error {r.status_code}: {r.text[:300]}")

    payload = r.json()
    if not isinstance(payload, list) or len(payload) == 0:
        # 빈 결과 방어
        return pd.DataFrame(columns=["date", "symbol", "pbr", "period", "calendarYear", "fiscalDateEnding"])

    records = []
    for rec in payload:
        pbr = rec.get("priceToBookRatio")
        # 날짜 후보값(엔드포인트별 key 차이를 방어적으로 처리)
        date_val = rec.get("date") or rec.get("fiscalDateEnding") or rec.get("publishedDate")

        records.append({
            "date": date_val,
            "symbol": rec.get("symbol", symbol),
            "pbr": pd.to_numeric(pbr, errors="coerce"),
            "period": rec.get("period"),
            "calendarYear": rec.get("calendarYear"),
            "fiscalDateEnding": rec.get("fiscalDateEnding")
        })

    df = pd.DataFrame.from_records(records)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= pd.to_datetime(start_date)]

    # 동일 분기 중복 제거
    df = df.drop_duplicates(subset=["date", "symbol"], keep="last").reset_index(drop=True)
    return df
