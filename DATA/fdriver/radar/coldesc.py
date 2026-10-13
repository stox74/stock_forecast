# -*- coding: utf-8 -*-
"""레이더 변수 설명의 단일 원천 (COLDESC v1). 내부용.

레이더 CSV 와 함께 저장하는 열 설명 CSV, HTML 아래 설명 표, 엑셀 '변수설명' 시트와 'radar' 시트 머리글 메모는
모두 이 사전 하나에서 읽는다. 다른 곳에 열 설명을 따로 적지 않는다.
설명은 구현 기준이다 (계산을 설명에 맞추지 않음). 설계실 정의와 다른 점은 session_report_radar_coldesc.md 에 적었다.

비공개: 계산식과 비고에 드라이버 구성 같은 '레시피'가 드러나므로 공개용 출력(radar.public)에는 넣지 않는다.
"""
import warnings
from typing import Iterable, List

import pandas as pd

GROUPS = ["식별·등급", "기준매출", "예측 성장률", "예측 성장률 %", "예측 매출 금액", "서프라이즈 신호", "충격 경보",
          "드라이버 흐름", "컨센서스", "발표일·경고"]
FIELDS = ["group", "meaning", "formula", "unit", "note"]
SHEET_HEADER = {"group": "분류", "variable": "변수", "meaning": "의미", "formula": "계산식", "unit": "단위", "note": "비고"}


def _d(group, meaning, formula="", unit="", note=""):
    return {"group": group, "meaning": meaning, "formula": formula, "unit": unit, "note": note}


G1, G2, G3, G4, G5, G6, G7, G8, G9, G10 = GROUPS
LOG = "로그 성장률 (0.05 = 약 5%)"
PCT = "% (5.0 = 5%)"
WON = "원"

COLDESC = {
    # 1. 식별·등급
    "grade": _d(G1, "레이더 신뢰 등급 (A/B/C, 대조군 'B2만'). 표시용이며 계산에 쓰지 않음", "radar.universe 등급표", "등급",
                "시범 6곳은 별도 등급표(PILOT_GRADE)"),
    "entity_id": _d(G1, "종목 코드 (6자리)", "", "코드"),
    "name": _d(G1, "기업 이름", "", "문자"),
    "sector": _d(G1, "업종", "2c 매핑 엑셀 기업 시트 (시범 6곳은 radar.universe.PILOT_SECTOR)", "문자"),
    "fiscal_quarter": _d(G1, "예측 대상 회계 분기", "", "YYYYQn"),
    "mode": _d(G1, "계산 방식: 실적이 아직 없으면 'live' (기준일 = 레이더 기준일), 실적이 있으면 'pre5 (실적 있음)' (시험용, "
                   "기준일 = 첫 공시 5거래일 전)", "", "문자"),
    "asof_date": _d(G1, "예측 기준일 (이 날 공개된 자료만 사용)", "live: 레이더 기준일 / pre5: 첫 공시일 5거래일 전", "날짜"),
    "main_model": _d(G1, "M4 모델 id (서프라이즈·충격 경보 기준 모델)", "드라이버 2개 이상 M4:combo, 1개면 M4:<드라이버>",
                     "문자", "대조군은 빈칸"),
    "M1_model": _d(G1, "E 의 드라이버 요소 모델 id", "드라이버 1개: M1:<드라이버> (동행) 또는 M3:<드라이버> (선행), 2개 이상: M2",
                   "문자", "대조군은 빈칸"),
    "E_components": _d(G1, "E 계산에 실제로 들어간 요소 목록", "'E:<요소>+B2+TS', 빠진 요소는 ';missing:<요소>'", "문자"),
    # 2. 기준매출
    "rev_base": _d(G2, "예측 대상 분기의 전년 동기 실제 매출. 모든 예측의 공통 출발점", "기준일에 알던 값 (잠정 -> 확정 -> 최신 순)", WON),
    # 3. 예측 성장률 (로그)
    "g_E": _d(G3, "앙상블 예측 성장률 (로그). 레이더의 대표 예측", "(g_M1 + g_B2 + g_TS) / 3", LOG,
              "구성 요소가 없으면 남은 것의 평균 (E_components 에 missing 표시). 대조군 계산식은 (g_B2 + g_TS) / 2 이지만 "
              "레이더 표에서는 대조군 E 를 빈칸으로 둠"),
    "g_M4": _d(G3, "B2 출발 + 드라이버 보정 예측 성장률 (로그). 보조 예측이자 서프라이즈·충격 경보의 기준",
               "드라이버 1개: g_B2 + b x (x_q - x_lag), b 는 원점 통과 확장 창 OLS (최소 12쌍). 2개 이상: 드라이버별 M4 의 단순 평균",
               LOG, "x 는 예측 분기와 같은 월 구성의 드라이버 성장률. b = 0 이면 B2 와 같음"),
    "g_B2": _d(G3, "나이브 예측 성장률 (로그). 기준일까지 발표된 가장 최근 분기의 전년 동기 대비 성장률을 그대로 씀", "g_lag", LOG),
    "g_TS": _d(G3, "시계열 예측 성장률 (로그). 매출 자체의 과거 패턴만 사용",
               "ln 매출에 계절 ETS (감쇠 가법 추세, 가법 계절)와 계절 ARIMA (자동 차수) 예측을 각각 구해 로그 예측의 평균 - ln(rev_base)",
               LOG, "가장 최근의 연속·양수 구간 12분기 이상일 때만. 한쪽 모형이 실패하면 남은 하나만 사용 (E_components 와 별도로 "
                    "모델 상세 drivers_used 에 one_model_only), 둘 다 실패하면 빈칸. 대조군은 레이더 표에서 빈칸"),
    "g_M1": _d(G3, "드라이버 회귀 예측 성장률 (로그). E 의 드라이버 요소이며 모델은 M1_model 에 표시",
               "M1·M3: g = a + b x (확장 창 OLS, 최소 12분기, M3 는 직전 분기 x). M2: 드라이버별 M1·M3 예측의 단순 평균", LOG),
    # 4. 예측 성장률 %
    "g_E_pct": _d(G4, "E 단순 성장률", "(exp(g_E) - 1) x 100", PCT),
    "g_M4_pct": _d(G4, "M4 단순 성장률", "(exp(g_M4) - 1) x 100", PCT),
    "g_M1_pct": _d(G4, "드라이버 요소 단순 성장률", "(exp(g_M1) - 1) x 100", PCT),
    "g_B2_pct": _d(G4, "B2 단순 성장률", "(exp(g_B2) - 1) x 100", PCT),
    "g_TS_pct": _d(G4, "TS 단순 성장률", "(exp(g_TS) - 1) x 100", PCT),
    # 5. 예측 매출 금액
    "rev_E": _d(G5, "대표 예상 매출 E", "rev_base x exp(g_E) = rev_M1, rev_B2, rev_TS 의 기하평균", WON,
                "없는 요소는 빼고 남은 것의 기하평균"),
    "rev_M4": _d(G5, "보조: M4 예상 매출", "rev_base x exp(g_M4)", WON),
    "rev_M1": _d(G5, "E 의 드라이버 요소 예상 매출", "rev_base x exp(g_M1)", WON),
    "rev_B2": _d(G5, "B2 예상 매출", "rev_base x exp(g_B2)", WON),
    "rev_TS": _d(G5, "TS 예상 매출", "rev_base x exp(g_TS)", WON),
    # 6. 서프라이즈 신호
    "nsur_drv": _d(G6, "드라이버 서프라이즈 (레이더 주 신호). 직전 추세(B2)로 설명되지 않고 드라이버에서만 나오는 부분을 "
                       "매출의 평소 변동 폭으로 나눈 값",
                   "(rev_M4 - rev_B2) / sigma", "표준화 값 (sigma 배수, 1.0 = 평소 변동 1배)",
                   "2a v6 level 변형 (signals.sur). shock_x (로그 차이)와 척도가 다름"),
    "drv_direction": _d(G6, "nsur_drv 방향", "sign(nsur_drv): '+' / '-' / '0'", "부호", "대조군은 빈칸"),
    "E_direction": _d(G6, "앙상블 E 가 직전 추세(B2)보다 높은지 낮은지", "sign(g_E - g_B2): '+' / '-' / '0'", "부호"),
    "agree": _d(G6, "nsur_drv 방향과 E_direction 이 같은지", "둘 다 0 이 아니면 '일치' / '불일치', 하나라도 0 이면 '-'", "문자",
                "HTML 에서 '불일치' 행은 노란 줄"),
    "nsur_M4": _d(G6, "M4 서프라이즈 전체 (참고)", "(rev_M4 - 기대 매출) / sigma, 기대 매출 = rev_base + delta",
                  "표준화 값", "nsur_M4 = nsur_mom + nsur_drv"),
    "nsur_mom": _d(G6, "모멘텀 서프라이즈 (참고). B2 예측이 시계열 기대에서 벗어난 정도", "(rev_B2 - 기대 매출) / sigma", "표준화 값"),
    "delta": _d(G6, "시계열 기대의 평균 증가액", "직전 8분기 D_j = Rev_j - Rev_(j-4) 의 평균 (기준일에 알던 매출, 6분기 이상)", WON,
                "4분기 점검·기업 사건 분기는 D_j 에서 뺌"),
    "sigma": _d(G6, "서프라이즈 분모: 매출 전년 동기 대비 증가액의 평소 변동 폭", "같은 D_j 8분기의 표준편차 (ddof = 1)", WON),
    # 7. 충격 경보
    "shock_x": _d(G7, "충격 경보 판정용 이번 분기 값. 드라이버 보정이 직전 추세에서 벗어난 크기", "g_M4 - g_B2", LOG,
                  "nsur_drv 와 같은 방향이지만 척도가 다름 (로그 차이, 표준화하지 않음)"),
    "shock_p10": _d(G7, "그 기업 과거 shock_x 분포의 하위 10% 값", "과거 분기 pre5 nowcast 의 (g_M4 - g_B2) 10% 분위", LOG,
                    "fd_nowcast 에 저장된 pre5 행 중 기준일이 레이더 기준일보다 앞선 모든 분기 (전체 기간 변형)"),
    "shock_p90": _d(G7, "그 기업 과거 shock_x 분포의 상위 10% 값", "같은 분포의 90% 분위", LOG),
    "shock_n": _d(G7, "위 분포를 만든 과거 분기 수", "", "개", "8개 미만이면 경보 판단 안 함"),
    "shock_alert": _d(G7, "충격 경보", "shock_x > p90 이면 '경보(상)', < p10 이면 '경보(하)', 분포 8개 미만이면 '판단 안 함'",
                      "문자", "대조군은 해당 없음. HTML 에서 경보 행은 붉은 줄"),
    # 8. 드라이버 흐름
    "drv_3m_yoy": _d(G8, "레이더 기준일에 공표된 주 드라이버 최근 3개월의 전년 동기 대비 성장률",
                     "ln(최근 3개월 합 / 1년 전 같은 3개월 합)", LOG,
                     "월별 YoY 평균이 아니라 3개월 합계의 비율. 드라이버가 여러 개면 첫 번째 주 드라이버만 (평균 아님, "
                     "DRIVER_SPECS 순서: 삼성전자·하나투어·SK하이닉스 해당). 세그먼트 가중 기업은 "
                     "세그먼트별 값을 그 분기 비중으로 가중평균. 3개월이 다 없으면 빈칸"),
    "drv_3m_last": _d(G8, "drv_3m_yoy 의 마지막 달", "", "YYYY-MM"),
    # 9. 컨센서스
    "consensus": _d(G9, "대상 분기 매출 컨센서스", "kr_revenue_consensus 중 관측일 <= 기준일 인 최신 값", WON, "억원 -> 원 변환"),
    "consensus_asof": _d(G9, "컨센서스 관측일", "", "날짜", "월별 자료라 최대 한 달 묵은 값일 수 있음"),
    "g_C_pct": _d(G9, "컨센서스 단순 성장률", "(consensus / rev_base - 1) x 100", PCT),
    "gap_E": _d(G9, "E 와 컨센서스 차이 (2b 검증 참고 신호)", "rev_E / consensus - 1", "비율 (0.05 = 5%)",
                "2b 판정에서 Q2 미달이라 주 신호가 아님"),
    "gap_M4": _d(G9, "M4 와 컨센서스 차이 (참고)", "rev_M4 / consensus - 1", "비율 (0.05 = 5%)"),
    "gap_dir": _d(G9, "gap_E 방향", "'위' (E > 컨센서스) / '아래' / '-'", "문자"),
    "gap_size": _d(G9, "|gap_E| 크기", "소 < 2.44% <= 중 < 7.33% <= 대", "문자",
                   "기준은 2b K2 판정 표본 |gap_E| 3분위 (고정값)"),
    "gap_agree": _d(G9, "gap_M4 와 gap_E 부호 일치", "'일치' / '불일치' / '-'", "문자"),
    # 10. 발표일·경고
    "announce_date": _d(G10, "예상 실적 발표일", "이벤트표 첫 공시일이 있으면 그 날, 없으면 분기말 + 최근 5년 같은 분기 (첫 공시일 - 분기말) "
                                            "일수 중앙값", "날짜"),
    "announce_kind": _d(G10, "발표일 근거", "'실제' 또는 '추정 (최근 n년 중앙값 d일)'", "문자"),
    "warnings": _d(G10, "경고", "'Q4차감' (대상이 회계 4분기: 실제치가 연간 - 1~3분기), '드라이버결측' (M4 없음), "
                              "'드라이버부분(cov=)' (드라이버 월 일부만 공개), '기업사건' (기업 사건 제외 구간), "
                              "'nowcast 없음 (사유)'", "문자", "여러 개면 ';' 로 이음"),
}


def ordered(columns: Iterable[str]) -> List[str]:
    """설명 시트 순서: 분류 순서, 분류 안에서는 레이더 표의 열 순서."""
    cols = [c for c in columns if c in COLDESC]
    pos = {c: i for i, c in enumerate(cols)}
    return sorted(cols, key=lambda c: (GROUPS.index(COLDESC[c]["group"]), pos[c]))


def missing(columns: Iterable[str]) -> List[str]:
    """출력에 있는데 COLDESC 에 없는 열."""
    return [c for c in columns if c not in COLDESC]


def unused(columns: Iterable[str], warn: bool = True) -> List[str]:
    """COLDESC 에 있는데 출력에 없는 열 (삭제하지 않고 경고만)."""
    cols = set(columns)
    extra = [c for c in COLDESC if c not in cols]
    if extra and warn:
        warnings.warn(f"COLDESC 에만 있는 변수 (출력에 없음): {extra}")
    return extra


def table(columns: Iterable[str]) -> pd.DataFrame:
    """변수설명 표: 분류 | 변수 | 의미 | 계산식 | 단위 | 비고."""
    cols = list(columns)
    miss = missing(cols)
    if miss:
        raise KeyError(f"COLDESC 에 설명이 없는 열: {miss}")
    rows = [{"group": COLDESC[c]["group"], "variable": c, **{k: COLDESC[c][k] for k in FIELDS[1:]}} for c in ordered(cols)]
    return pd.DataFrame(rows, columns=list(SHEET_HEADER)).rename(columns=SHEET_HEADER)
