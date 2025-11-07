#%% md
# 0) 공통 유틸: 티커/CIK 입력 받아 Company Facts 수신
#%%
import json
import time
import requests

def _zero_pad_cik(cik: str) -> str:
    cik_str = str(cik).strip()
    if cik_str.isdigit():
        return cik_str.zfill(10)
    return cik_str

def resolve_cik(ticker_or_cik: str, headers: dict, cache: dict = None) -> str:
    """
    티커/CIK → 10자리 zero-pad CIK
    SEC 매핑 파일은 www.sec.gov 도메인을 사용해야 함.
    """
    if cache is None:
        cache = {}

    key = ticker_or_cik.upper().strip()

    # 이미 숫자면 CIK로 간주
    if key.isdigit():
        return _zero_pad_cik(key)

    # 캐시에 있으면 사용
    if key in cache:
        return cache[key]

    # 정식 경로(도메인 주의!): https://www.sec.gov/files/company_tickers.json
    url_candidates = [
        "https://www.sec.gov/files/company_tickers.json",               # 메인
        "https://www.sec.gov/files/company_tickers_exchange.json",      # 보조(드물게 유용)
    ]

    resp_json = None
    last_status = None
    for url_map in url_candidates:
        for attempt in range(3):
            r = requests.get(url_map, headers=headers, timeout=15)
            last_status = r.status_code
            if r.status_code == 200:
                resp_json = r.json()
                break
            time.sleep(1.2)
        if resp_json is not None:
            break

    if resp_json is None:
        raise RuntimeError(f"티커 매핑 파일 요청 실패: {last_status} (tried {', '.join(url_candidates)})")

    # {"0":{"ticker":"AAPL","cik_str":320193,"title":"Apple Inc."}, ...}
    by_ticker = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in resp_json.values()}

    if key not in by_ticker:
        raise ValueError(f"티커를 찾을 수 없습니다: {key}")

    cik_padded = by_ticker[key]
    cache[key] = cik_padded
    return cik_padded


def fetch_company_facts(ticker_or_cik: str, headers: dict) -> dict:
    cik = resolve_cik(ticker_or_cik, headers=headers)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    # 간단 retry
    for attempt in range(3):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(1.0)
    raise RuntimeError(f"Company Facts 요청 실패: {r.status_code} {url}")
