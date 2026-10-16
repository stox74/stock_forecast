"""
한국은행 ECOS 지표 매핑 (v2, 2026-10-16)

v1 대비 변경 (ecos_catalog_v2/v3 검증 결과 반영)
- 은행대출금_기업/가계 -> 은행대출연체율_기업/가계 (901Y054 는 연체율 통계표였음)
- 대출 잔액 신규: 은행대출금_총액(104Y016), 가계대출_예금취급기관/주택관련(151Y005), 가계대출_예금은행(151Y002)
- 수입물가지수: 402Y015(수출물가 특수분류) -> 401Y015
- M2광의통화: 101Y005(구지표, 2004-09 종료) -> 161Y011
- 주택매매/전세가격지수: 기준월별 통계표 4개를 segments 로 등록, 수집 시 연결
- 모든 지표에 lag_days (기간 말일 이후 대략적인 공표 시차, 일). 신뢰도 C

필드: category, stat_code, items, [segments], lag_days, [note]
"""
from __future__ import annotations

ECOS_INDEX_MAP = {
  "M2광의통화": {
    "category": "통화량",
    "stat_code": "161Y011",
    "items": [
      "BBGS00"
    ],
    "note": "신지표로 교체 (구 101Y005는 2004-09 수록 종료)",
    "lag_days": 45
  },
  "은행대출연체율_기업": {
    "category": "대출연체율",
    "stat_code": "901Y054",
    "items": [
      "MO3AA",
      "AB"
    ],
    "note": "901Y054는 연체율(1일 이상) 통계표. 구 이름 은행대출금_기업",
    "lag_days": 45
  },
  "은행대출연체율_가계": {
    "category": "대출연체율",
    "stat_code": "901Y054",
    "items": [
      "MO3AB",
      "AB"
    ],
    "note": "901Y054는 연체율(1일 이상) 통계표. 구 이름 은행대출금_가계",
    "lag_days": 45
  },
  "예금은행_수신금리(신규)": {
    "category": "여수신금리",
    "stat_code": "121Y002",
    "items": [
      "BEABAA2"
    ],
    "lag_days": 30
  },
  "예금은행_수신금리(잔액)": {
    "category": "여수신금리",
    "stat_code": "121Y015",
    "items": [
      "BECBLB01"
    ],
    "lag_days": 30
  },
  "예금은행_대출금리(신규)": {
    "category": "여수신금리",
    "stat_code": "121Y006",
    "items": [
      "BECBLA01"
    ],
    "lag_days": 30
  },
  "전산업생산지수": {
    "category": "생산",
    "stat_code": "901Y033",
    "items": [
      "A00",
      "1"
    ],
    "lag_days": 30
  },
  "제조업생산지수_계절조정": {
    "category": "생산",
    "stat_code": "901Y032",
    "items": [
      "I11A",
      "2"
    ],
    "lag_days": 30
  },
  "제조업출하지수_계절조정": {
    "category": "생산",
    "stat_code": "901Y032",
    "items": [
      "I11AC",
      "4"
    ],
    "lag_days": 30
  },
  "제조업재고지수": {
    "category": "생산",
    "stat_code": "901Y032",
    "items": [
      "I11AC",
      "6"
    ],
    "lag_days": 30
  },
  "제조업가동률지수": {
    "category": "생산",
    "stat_code": "901Y035",
    "items": [
      "I32A",
      "I11C"
    ],
    "lag_days": 30
  },
  "서비스업생산지수": {
    "category": "생산",
    "stat_code": "901Y038",
    "items": [
      "I51A",
      "3"
    ],
    "lag_days": 30
  },
  "도소매업지수": {
    "category": "생산",
    "stat_code": "901Y038",
    "items": [
      "I51AB",
      "3"
    ],
    "lag_days": 30
  },
  "소매판매액지수_계절조정": {
    "category": "소비",
    "stat_code": "901Y100",
    "items": [
      "G0",
      "T3"
    ],
    "lag_days": 30
  },
  "자동차판매액지수_계절조정": {
    "category": "소비",
    "stat_code": "901Y100",
    "items": [
      "G11",
      "T3"
    ],
    "lag_days": 30
  },
  "설비투자지수": {
    "category": "투자",
    "stat_code": "901Y066",
    "items": [
      "I15B"
    ],
    "lag_days": 30
  },
  "설비용기계류생산지수": {
    "category": "투자",
    "stat_code": "901Y036",
    "items": [
      "20",
      "T10"
    ],
    "lag_days": 30
  },
  "국내수요기계수주액": {
    "category": "투자",
    "stat_code": "901Y018",
    "items": [
      "I35BAA"
    ],
    "lag_days": 30
  },
  "건설기성액_불변_계절조정": {
    "category": "투자",
    "stat_code": "901Y104",
    "items": [
      "I48A",
      "I37D"
    ],
    "lag_days": 30
  },
  "건축허가면적": {
    "category": "투자",
    "stat_code": "901Y037",
    "items": [
      "I43AA",
      "1"
    ],
    "lag_days": 30
  },
  "건설수주액": {
    "category": "투자",
    "stat_code": "901Y020",
    "items": [
      "I42A"
    ],
    "lag_days": 30
  },
  "건축착공면적": {
    "category": "투자",
    "stat_code": "901Y103",
    "items": [
      "1",
      "I47AA"
    ],
    "lag_days": 30
  },
  "경기선행지수": {
    "category": "경기순환",
    "stat_code": "901Y067",
    "items": [
      "I16A"
    ],
    "lag_days": 30
  },
  "동행지수순환변동치": {
    "category": "경기순환",
    "stat_code": "901Y067",
    "items": [
      "I16D"
    ],
    "lag_days": 30
  },
  "선행지수순환변동치": {
    "category": "경기순환",
    "stat_code": "901Y067",
    "items": [
      "I16E"
    ],
    "lag_days": 30
  },
  "소비자심리지수": {
    "category": "심리지표",
    "stat_code": "511Y002",
    "items": [
      "FME",
      "99988"
    ],
    "lag_days": 0
  },
  "BSI": {
    "category": "심리지표",
    "stat_code": "512Y007",
    "items": [
      "AA",
      "99988"
    ],
    "lag_days": 0
  },
  "경제심리지수": {
    "category": "심리지표",
    "stat_code": "513Y001",
    "items": [
      "E1000"
    ],
    "lag_days": 0
  },
  "실업률": {
    "category": "고용",
    "stat_code": "901Y027",
    "items": [
      "I61BC",
      "I28B"
    ],
    "lag_days": 15
  },
  "고용률": {
    "category": "고용",
    "stat_code": "901Y027",
    "items": [
      "I61E",
      "I28B"
    ],
    "lag_days": 15
  },
  "경제활동인구": {
    "category": "고용",
    "stat_code": "901Y027",
    "items": [
      "I61B",
      "I28B"
    ],
    "lag_days": 15
  },
  "취업자수": {
    "category": "고용",
    "stat_code": "901Y027",
    "items": [
      "I61BA",
      "I28B"
    ],
    "lag_days": 15
  },
  "경상수지": {
    "category": "국제수지",
    "stat_code": "301Y013",
    "items": [
      "000000"
    ],
    "lag_days": 38
  },
  "수출금액지수": {
    "category": "통관수출입",
    "stat_code": "403Y001",
    "items": [
      "*AA"
    ],
    "lag_days": 30
  },
  "수입금액지수": {
    "category": "통관수출입",
    "stat_code": "403Y003",
    "items": [
      "*AA"
    ],
    "lag_days": 30
  },
  "순상품교역조건지수": {
    "category": "통관수출입",
    "stat_code": "403Y005",
    "items": [
      "A"
    ],
    "lag_days": 30
  },
  "소득교역조건지수": {
    "category": "통관수출입",
    "stat_code": "403Y005",
    "items": [
      "B"
    ],
    "lag_days": 30
  },
  "외환보유액": {
    "category": "대외채권",
    "stat_code": "732Y001",
    "items": [
      "99"
    ],
    "lag_days": 3
  },
  "소비자물가지수": {
    "category": "물가",
    "stat_code": "901Y009",
    "items": [
      "0"
    ],
    "lag_days": 2
  },
  "소비자물가지수(농산물,석유제외)": {
    "category": "물가",
    "stat_code": "901Y010",
    "items": [
      "QB"
    ],
    "lag_days": 2
  },
  "생활물가지수": {
    "category": "물가",
    "stat_code": "901Y010",
    "items": [
      "110"
    ],
    "lag_days": 2
  },
  "생산자물가지수": {
    "category": "물가",
    "stat_code": "404Y014",
    "items": [
      "*AA"
    ],
    "lag_days": 2
  },
  "수출물가지수": {
    "category": "수출입물가",
    "stat_code": "402Y014",
    "items": [
      "*AA",
      "D"
    ],
    "lag_days": 15
  },
  "수입물가지수": {
    "category": "수출입물가",
    "stat_code": "401Y015",
    "items": [
      "*AA",
      "D"
    ],
    "note": "구 코드 402Y015는 수출물가(특수분류)였음",
    "lag_days": 15
  },
  "주택매매가격지수": {
    "category": "부동산가격",
    "stat_code": "901Y149",
    "items": [
      "00",
      "a0"
    ],
    "segments": [
      {
        "stat_code": "901Y149",
        "items": [
          "00",
          "a0"
        ],
        "base": "2026.06=100"
      },
      {
        "stat_code": "901Y144",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2026.01=100"
      },
      {
        "stat_code": "901Y113",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2025.03=100"
      },
      {
        "stat_code": "901Y093",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2021.06=100"
      }
    ],
    "note": "기준월 개편마다 통계표가 바뀜. 최신 표 기준으로 과거 표를 겹치는 기간 비율로 연결",
    "lag_days": 15
  },
  "주택전세가격지수": {
    "category": "부동산가격",
    "stat_code": "901Y150",
    "items": [
      "00",
      "a0"
    ],
    "segments": [
      {
        "stat_code": "901Y150",
        "items": [
          "00",
          "a0"
        ],
        "base": "2026.06=100"
      },
      {
        "stat_code": "901Y145",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2026.01=100"
      },
      {
        "stat_code": "901Y114",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2025.03=100"
      },
      {
        "stat_code": "901Y094",
        "items": [
          "H69A",
          "R70A"
        ],
        "base": "2021.06=100"
      }
    ],
    "note": "기준월 개편마다 통계표가 바뀜. 최신 표 기준으로 과거 표를 겹치는 기간 비율로 연결",
    "lag_days": 15
  },
  "지가변동률": {
    "category": "부동산가격",
    "stat_code": "901Y064",
    "items": [
      "P65A"
    ],
    "lag_days": 25
  },
  "미분양주택": {
    "category": "부동산가격",
    "stat_code": "901Y074",
    "items": [
      "I410A"
    ],
    "lag_days": 30
  },
  "국제유가": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "010101"
    ],
    "lag_days": 5
  },
  "금": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "040101"
    ],
    "lag_days": 5
  },
  "철광석": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "040205"
    ],
    "lag_days": 5
  },
  "구리": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "040202"
    ],
    "lag_days": 5
  },
  "아연": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "040201"
    ],
    "lag_days": 5
  },
  "알루미늄": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "040203"
    ],
    "lag_days": 5
  },
  "대두": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "030201"
    ],
    "lag_days": 5
  },
  "옥수수": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "030101"
    ],
    "lag_days": 5
  },
  "소맥": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "030102"
    ],
    "lag_days": 5
  },
  "원당": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "030301"
    ],
    "lag_days": 5
  },
  "원면": {
    "category": "원자재",
    "stat_code": "902Y003",
    "items": [
      "020101"
    ],
    "lag_days": 5
  },
  "수출물량지수_반도체": {
    "category": "통관수출입",
    "stat_code": "403Y002",
    "items": [
      "309112AA"
    ],
    "lag_days": 30
  },
  "수출물량지수_운송장비": {
    "category": "통관수출입",
    "stat_code": "403Y002",
    "items": [
      "312AA"
    ],
    "lag_days": 30
  },
  "국고채(3년)": {
    "category": "시장금리",
    "stat_code": "721Y001",
    "items": [
      "5020000"
    ],
    "lag_days": 1
  },
  "국고채(5년)": {
    "category": "시장금리",
    "stat_code": "721Y001",
    "items": [
      "5040000"
    ],
    "lag_days": 1
  },
  "국고채(10년)": {
    "category": "시장금리",
    "stat_code": "721Y001",
    "items": [
      "5050000"
    ],
    "lag_days": 1
  },
  "KOSPI_거래대금": {
    "category": "주식_채권_재정",
    "stat_code": "901Y014",
    "items": [
      "1060000"
    ],
    "lag_days": 5
  },
  "KOSPI_사장주식_회전률": {
    "category": "주식_채권_재정",
    "stat_code": "901Y014",
    "items": [
      "1090000"
    ],
    "lag_days": 5
  },
  "은행대출금_총액": {
    "category": "대출금",
    "stat_code": "104Y016",
    "items": [
      "BDCA1"
    ],
    "note": "예금은행 총대출금(말잔), 십억원",
    "lag_days": 40
  },
  "가계대출_예금취급기관": {
    "category": "대출금",
    "stat_code": "151Y005",
    "items": [
      "1110000"
    ],
    "note": "예금취급기관 가계대출(말잔), 십억원",
    "lag_days": 40
  },
  "가계대출_주택관련": {
    "category": "대출금",
    "stat_code": "151Y005",
    "items": [
      "11100A0"
    ],
    "note": "주택관련대출-예금취급기관, 2007-12부터",
    "lag_days": 40
  },
  "가계대출_예금은행": {
    "category": "대출금",
    "stat_code": "151Y002",
    "items": [
      "1111000"
    ],
    "note": "예금은행 가계대출(말잔), 십억원",
    "lag_days": 40
  }
}


def get_index(name: str) -> dict:
    return ECOS_INDEX_MAP[name]


# 이름이 바뀐 지표 (DB 이관용): 구 이름 -> 새 이름
RENAMED = {
    "은행대출금_기업": "은행대출연체율_기업",
    "은행대출금_가계": "은행대출연체율_가계",
}

ECONOMY_INDEX_CODES = ECOS_INDEX_MAP
__all__ = ["ECOS_INDEX_MAP", "ECONOMY_INDEX_CODES", "RENAMED", "get_index"]
