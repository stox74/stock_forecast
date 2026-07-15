"""프로젝트 전역 설정."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "revenue.db"
OUTPUT_DIR = BASE_DIR / "output"

# 수집 대상 기업 (종목코드: 표시용 이름)
# 필요 시 자유롭게 추가/삭제하면 됩니다. 코드는 TWSE 상장(sii) 기준.
COMPANIES = {
    "2330": "TSMC",
    "2317": "Hon Hai (Foxconn)",
    "2454": "MediaTek",
    "2308": "Delta Electronics",
    "2382": "Quanta Computer",
}

# MOPS 월별 매출 집계 페이지 (구/신 도메인 순서대로 시도)
MOPS_URL_TEMPLATES = [
    "https://mops.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_0.html",
    "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_0.html",
]

# TWSE OpenAPI - 상장사 최신 월 매출 (JSON)
TWSE_OPENAPI_MONTHLY = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"

MARKET = "sii"  # sii=상장, otc=상궤(店頭), rotc=흥궤

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

# 요청 간 대기 시간(초) - 서버 부담 방지
REQUEST_DELAY_SEC = 2.0

# SARIMA 기본 설정
SARIMA_ORDER = (1, 1, 1)
SARIMA_SEASONAL_ORDER = (1, 1, 1, 12)
MIN_OBS_FOR_FORECAST = 30  # 최소 관측치(개월)
