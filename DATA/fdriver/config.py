# -*- coding: utf-8 -*-
"""fdriver 설정.

- DB 접속 정보와 API 키는 DATA/config.py, DATA/KEYS.py 에서만 가져온다 (이 파일에 쓰지 않음).
- RELEASE_RULES: 원천별 발표 시점(available_at) 규칙. 검증 결과에 따라 이 딕셔너리만 고친다.
"""
from typing import Dict

FD_TABLE_PREFIX = "fd_"

# 공통 반환 컬럼 (명세서 4절 + asof_note).
# asof_note 는 등급만으로 표현할 수 없는 주의사항(예: 구글 트렌드 재조정)을 적는 확장 컬럼.
COMMON_COLUMNS = [
    "key", "period_end", "freq", "item", "value", "unit", "currency",
    "available_at", "asof_quality", "asof_note", "source",
]

ASOF_GRADES = ("A", "B", "C")


def get_db_info() -> Dict[str, str]:
    from DATA.config import get_db_info as _get_db_info
    return _get_db_info()


def get_key(name: str) -> str:
    """DATA/KEYS.py 의 KEYS 딕셔너리에서 키를 읽는다."""
    from DATA.KEYS import KEYS
    return KEYS[name]


# ---------------------------------------------------------------------------
# 발표 시점 규칙
#
# 원칙: 모르면 늦게 잡는다.
# 각 규칙 항목:
#   lag         규칙 날짜 계산 방식
#                 {"days": n}            기간말 + n일
#                 {"bdays": n}           기간말 + n영업일 (주말만 제외, 공휴일 미반영)
#                 {"next_month_day": d}  기간말 다음 달 d일
#                 {"by_freq": {...}}     freq별로 위 방식 중 하나
#   rule_grade  규칙 날짜를 쓸 때의 등급
#   observed    실제 관측 날짜 컬럼 종류
#                 None                   없음 (항상 규칙 날짜)
#                 "first_collected_at"   최초 수집 시각
#                 "first_release_date"   공표일 (FRED/ALFRED)
#                 "period_end"           기간말 당일이 곧 관측일 (주가)
#   observed_grade  관측 날짜를 쓸 때의 등급
#   backfill    관측 날짜를 백필로 보고 버리는 기준
#                 {"ref": "rule", "days": 30}        관측일 > 규칙일 + 30일이면 백필
#                 {"ref": "period_end", "days": 60}  관측일 > 기간말 + 60일이면 백필
#   note        항상 붙일 주의사항 (asof_note)
# ---------------------------------------------------------------------------
_BACKFILL_30 = {"ref": "rule", "days": 30}

RELEASE_RULES: Dict[str, dict] = {
    # 매출: 공시 접수일이 원천에 없음 (report_date 는 기간말이라 사용 금지) -> 법정 제출기한
    "revenue_quarter": {          # 1분기·반기·3분기보고서
        "lag": {"days": 45}, "rule_grade": "C",
        "observed": None,
    },
    "revenue_annual": {           # 사업보고서 (Q4 단독값 포함)
        "lag": {"days": 90}, "rule_grade": "C",
        "observed": None,
    },
    # 수출입: 관세청 월간 확정치. 수집 시각 컬럼 없음 (미결 4: 1단계 중 검증)
    "trade": {
        "lag": {"days": 15}, "rule_grade": "C",
        "observed": None,
    },
    # 대만 월매출: 법정 공시 기한 익월 10일(B), 실시간 수집분은 first_collected_at(A)
    "tw_revenue": {
        "lag": {"next_month_day": 10}, "rule_grade": "B",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    # FRED: first_release_date 가 기간말 + 60일 이내면 실제 공표일(A), 아니면 규칙(C)
    "fred": {
        "lag": {"by_freq": {
            "D": {"bdays": 1}, "W": {"days": 7}, "M": {"days": 45},
            "Q": {"days": 90}, "Y": {"days": 120},
        }},
        "rule_grade": "C",
        "observed": "first_release_date", "observed_grade": "A",
        "backfill": {"ref": "period_end", "days": 60},
    },
    # FRED 공표 이력이 없는 시리즈용 (최초 수집 시각 기반)
    "fred_collected": {
        "lag": {"by_freq": {
            "D": {"bdays": 1}, "W": {"days": 7}, "M": {"days": 45},
            "Q": {"days": 90}, "Y": {"days": 120},
        }},
        "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    # 국내 STD-1 시계열: 월간 기간말 + 35일, 일별 다음 영업일
    "kosis": {
        "lag": {"by_freq": {
            "D": {"bdays": 1}, "W": {"days": 7}, "M": {"days": 35},
            "Q": {"days": 75}, "Y": {"days": 120},
        }},
        "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    "komis": {
        "lag": {"by_freq": {"D": {"bdays": 1}, "W": {"days": 7}, "M": {"days": 35}}},
        "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    "airline": {
        "lag": {"by_freq": {"M": {"days": 35}}},
        "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    "tourism": {                  # 방한(E)·출국(D) 공통
        "lag": {"by_freq": {"M": {"days": 35}}},
        "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    # 주가: 거래일 당일
    "price": {
        "lag": {"days": 0}, "rule_grade": "A",
        "observed": "period_end", "observed_grade": "A",
    },
    # 증시 수급 일별 (신용잔고·증시자금): 공표가 다음 영업일 이후라 기간말 + 2영업일(C), 실시간 수집분 A
    "market_daily": {
        "lag": {"bdays": 2}, "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    # 아마존 BSR: Keepa 백필분은 다음 날(C), 정기 수집분은 created_at(A)
    "amazon_bsr": {
        "lag": {"days": 1}, "rule_grade": "C",
        "observed": "first_collected_at", "observed_grade": "A",
        "backfill": _BACKFILL_30,
    },
    # 구글 트렌드: 주 시작일 기준 주가 끝난 뒤(+7일). 수집 시점 기준으로 전 기간이 재조정되므로
    # 관측 날짜를 쓰지 않고 항상 C + 주의 표시
    "google_trends": {
        "lag": {"days": 7}, "rule_grade": "C",
        "observed": None,
        "note": "rescaled_at_collection",
    },
}


# ---------------------------------------------------------------------------
# 일괄 적재(bulk load) 판별
#
# 일괄 적재일에 처음 들어온 값은 관측일(적재일)을 available_at 으로 쓰되 등급은 A 가 아니라 B,
# asof_note = 'bulk_load' 로 한다 (관측일이 규칙일 + 30일 안이어서 관측일을 쓰게 되는 행만 해당).
# 테이블별로 "하루에 처음 수집된 행"을 모아 아래 둘 중 하나면 일괄 적재일로 본다.
#   (1) 규모: 전체의 min_share 이상 또는 min_rows 행 이상, 그리고 그 행들의 기간 폭이 min_span_days 이상
#   (2) 과거 이력: 서로 다른 기간이 history_periods 개 이상이고 기간 폭이 history_span_days 이상
# 기간 폭 조건은 정기 수집(최근 1~2개 기간만 들어옴)을 일괄 적재로 오판하지 않게 하려는 것이다.
# 예: 항공 수송통계는 시리즈가 약 2,000개라 정기 월간 수집도 하루 1,000행을 넘는다.
# dates 에 테이블을 적으면 자동 판별 대신 그 날짜 목록을 쓴다 (빈 목록이면 일괄 적재일 없음).
# ---------------------------------------------------------------------------
BULK_LOAD: Dict[str, object] = {
    "min_share": 0.10,
    "min_rows": 1000,
    "min_span_days": 90,
    "history_periods": 12,
    "history_span_days": 365,
    "observed_columns": ("first_collected_at", "created_at", "collected_at"),
    "dates": {
        # "kr_tourism_visitors_monthly": ["2026-10-01", "2026-10-03"],
    },
}
