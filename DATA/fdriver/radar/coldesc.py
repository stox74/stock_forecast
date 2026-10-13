# -*- coding: utf-8 -*-
"""레이더 변수 설명의 단일 원천 (COLDESC v2, fdriver_radar_coldesc_instruction_v2). 내부용.

- 키는 출력 변수명. 'internal' 은 DB·내부 표(build.COLUMNS, 실전 기록 테이블)의 이름 (새로 만든 출력 값은 None).
  'transform' 이 있으면 내부 값을 바꿔 만든 열 (radar.output 이 계산), 없으면 이름만 바꿔 그대로 옮긴다.
- 레이더 CSV·판단 CSV, HTML 아래 설명 표, 엑셀 '변수설명' 시트와 '판단'·'radar' 시트 머리글 메모가 모두 이 사전에서 읽는다.
- 설명은 구현 기준이다 (계산을 설명에 맞추지 않음). 설계실 정의와 다른 점은 session_report_radar_output_v2.md 에 적었다.
- 비공개: 계산식과 비고에 드라이버 구성 같은 '레시피'가 드러나므로 공개용 출력(radar.public)에는 넣지 않는다.
"""
import warnings
from typing import Iterable, List

import pandas as pd

GROUPS = ["판단", "식별·등급", "기준매출", "예측 성장률", "예측 성장률 %", "예측 매출 금액", "컨센서스 비교", "방향·신호",
          "구성 불일치", "충격 경보", "드라이버 흐름", "발표일·경고"]
FIELDS = ["group", "internal", "meaning", "formula", "unit", "note"]
SHEET_HEADER = {"group": "분류", "variable": "출력 변수명", "internal": "내부 변수명", "meaning": "의미", "formula": "계산식",
                "unit": "단위", "note": "비고"}


def _d(group, internal, meaning, formula="", unit="", note="", transform=None):
    d = {"group": group, "internal": internal, "meaning": meaning, "formula": formula, "unit": unit, "note": note}
    if transform:
        d["transform"] = transform
    return d


(GJ, G1, G2, G3, G4, G5, GC, GD, GS, GK, GR, GW) = GROUPS
LOG = "로그 성장률 (0.05 = 약 5%)"
PCT = "% (5.0 = 5%)"
WON = "원"
JO = "조원"
DIR = "+ / - / 공란"
AGR = "일치 / 불일치 / 공란"
FLAG = "켜지면 사유 문자열, 아니면 공란"

COLDESC = {
    # 판단
    "decision": _d(GJ, None, "판단 결과 (후보·검토·관찰·중립·보류)",
                   "1 C등급 -> 보류(등급), 2 구성 불일치 + 충격 -> 검토(충격+불일치), 3 구성 불일치 -> 보류(불일치), "
                   "4 컨센서스 대비 |갭| >= 5% 이고 agree_drv_cons = 일치 -> 후보(상향/하향, 충격이면 +충격), "
                   "5 같은 갭에서 불일치 -> 관찰(방향 불일치), 6 |갭| < 5% -> 중립, "
                   "7 컨센서스 없음 + agree_E_drv_vs_trend = 일치 -> 관찰(컨센서스 없음, 상향/하향), 8 그 밖 -> 중립",
                   "문자", "위에서부터 처음 걸리는 규칙. 기준값은 radar.output 상수 (갭 5%, 구성 불일치 15%). |갭| >= 5% 인데 드라이버 "
                   "방향이 없으면 중립. E 의 하향 편향(2b 약 -1.6%)은 보정하지 않음", transform="radar.output.decide"),
    "decision_reason": _d(GJ, None, "판단에 이른 단계와 사유", "등급, 갭, 걸린 규칙의 사유를 ', ' 로 이음", "문자",
                          transform="radar.output.decide"),
    # 식별·등급
    "entity_id": _d(G1, "entity_id", "종목 코드 (6자리)", "", "코드"),
    "name": _d(G1, "name", "기업 이름", "", "문자"),
    "sector": _d(G1, "sector", "업종", "2c 매핑 엑셀 기업 시트 (시범 6곳은 radar.universe.PILOT_SECTOR)", "문자"),
    "grade": _d(G1, "grade", "레이더 신뢰 등급 (A/B/C, 대조군 'B2만')", "radar.universe 등급표", "등급",
                "판단 규칙 1 (C등급 보류)에 쓰고, 예측 계산에는 쓰지 않음"),
    "fiscal_quarter": _d(G1, "fiscal_quarter", "예측 대상 회계 분기", "", "YYYYQn"),
    "mode": _d(G1, "mode", "계산 방식: 실적이 아직 없으면 'live', 실적이 있으면 'pre5 (실적 있음)' (시험용)", "", "문자"),
    "asof_date": _d(G1, "asof_date", "예측 기준일 (이 날 공개된 자료만 사용)", "live: 레이더 기준일 / pre5: 첫 공시 5거래일 전", "날짜"),
    "main_model": _d(G1, "main_model", "M4 모델 id (드라이버 신호·충격 경보 기준 모델)", "드라이버 2개 이상 M4:combo, 1개면 M4:<드라이버>",
                     "문자", "대조군은 공란"),
    "M1_model": _d(G1, "M1_model", "E 의 드라이버 요소 모델 id",
                   "드라이버 1개: M1:<드라이버> (동행) 또는 M3:<드라이버> (선행), 2개 이상: M2", "문자", "대조군은 공란"),
    "E_components": _d(G1, "E_components", "E 계산에 실제로 들어간 요소 목록", "'E:<요소>+B2+TS', 빠진 요소는 ';missing:<요소>'",
                       "문자"),
    # 기준매출
    "rev_base": _d(G2, "rev_base", "예측 대상 분기의 전년 동기 실제 매출. 모든 예측의 공통 출발점",
                   "기준일에 알던 값 (잠정 -> 확정 -> 최신 순)", WON),
    # 예측 성장률 (로그)
    "g_E": _d(G3, "g_E", "앙상블 예측 성장률 (로그). 레이더의 대표 예측", "(g_M1 + g_B2 + g_TS) / 3", LOG,
              "구성 요소가 없으면 남은 것의 평균 (E_components 에 missing 표시). 대조군 계산식은 (g_B2 + g_TS) / 2 이지만 "
              "레이더 표에서는 대조군 E 를 공란으로 둠"),
    "g_M4": _d(G3, "g_M4", "B2 출발 + 드라이버 보정 예측 성장률 (로그). 보조 예측이자 드라이버 신호·충격 경보의 기준",
               "드라이버 1개: g_B2 + b x (x_q - x_lag), b 는 원점 통과 확장 창 OLS (최소 12쌍). 2개 이상: 드라이버별 M4 의 단순 평균",
               LOG, "x 는 예측 분기와 같은 월 구성의 드라이버 성장률. b = 0 이면 B2 와 같음"),
    "g_M1": _d(G3, "g_M1", "드라이버 회귀 예측 성장률 (로그). E 의 드라이버 요소, 모델은 M1_model",
               "M1·M3: g = a + b x (확장 창 OLS, 최소 12분기, M3 는 직전 분기 x). M2: 드라이버별 M1·M3 예측의 단순 평균", LOG),
    "g_B2": _d(G3, "g_B2", "나이브 예측 성장률 (로그). 기준일까지 발표된 가장 최근 분기의 전년 동기 대비 성장률을 그대로 씀", "g_lag",
               LOG, "'직전 추세' (dir_*_vs_trend 의 기준)"),
    "g_TS": _d(G3, "g_TS", "시계열 예측 성장률 (로그). 매출 자체의 과거 패턴만 사용",
               "ln 매출에 계절 ETS (감쇠 가법 추세, 가법 계절)와 계절 ARIMA (자동 차수) 예측을 각각 구해 로그 예측의 평균 - ln(rev_base)",
               LOG, "가장 최근의 연속·양수 구간 12분기 이상일 때만. 한쪽 모형이 실패하면 남은 하나만 사용 (모델 상세 drivers_used 에 "
                    "one_model_only), 둘 다 실패하면 공란. 대조군은 레이더 표에서 공란"),
    # 예측 성장률 %
    "g_E_pct": _d(G4, "g_E_pct", "E 단순 성장률", "(exp(g_E) - 1) x 100", PCT),
    "g_M4_pct": _d(G4, "g_M4_pct", "M4 단순 성장률", "(exp(g_M4) - 1) x 100", PCT),
    "g_M1_pct": _d(G4, "g_M1_pct", "드라이버 요소 단순 성장률", "(exp(g_M1) - 1) x 100", PCT),
    "g_B2_pct": _d(G4, "g_B2_pct", "B2 단순 성장률", "(exp(g_B2) - 1) x 100", PCT),
    "g_TS_pct": _d(G4, "g_TS_pct", "TS 단순 성장률", "(exp(g_TS) - 1) x 100", PCT),
    # 예측 매출 금액
    "rev_E": _d(G5, "rev_E", "대표 예상 매출 E", "rev_base x exp(g_E) = rev_M1, rev_B2, rev_TS 의 기하평균", WON,
                "없는 요소는 빼고 남은 것의 기하평균"),
    "rev_M4": _d(G5, "rev_M4", "보조: M4 예상 매출", "rev_base x exp(g_M4)", WON),
    "rev_M1": _d(G5, "rev_M1", "E 의 드라이버 요소 예상 매출", "rev_base x exp(g_M1)", WON),
    "rev_B2": _d(G5, "rev_B2", "B2 예상 매출", "rev_base x exp(g_B2)", WON),
    "rev_TS": _d(G5, "rev_TS", "TS 예상 매출", "rev_base x exp(g_TS)", WON),
    "jo_E": _d(G5, "rev_E", "대표 예상 매출 E (조원)", "rev_E / 1e12", JO, transform="/1e12"),
    "jo_M4": _d(G5, "rev_M4", "M4 예상 매출 (조원)", "rev_M4 / 1e12", JO, transform="/1e12"),
    "jo_M1": _d(G5, "rev_M1", "드라이버 요소 예상 매출 (조원)", "rev_M1 / 1e12", JO, transform="/1e12"),
    "jo_B2": _d(G5, "rev_B2", "B2 예상 매출 (조원)", "rev_B2 / 1e12", JO, transform="/1e12"),
    "jo_TS": _d(G5, "rev_TS", "TS 예상 매출 (조원)", "rev_TS / 1e12", JO, transform="/1e12"),
    # 컨센서스 비교
    "rev_cons": _d(GC, "consensus", "대상 분기 매출 컨센서스", "kr_revenue_consensus 중 관측일 <= 기준일 인 최신 값", WON,
                   "억원 -> 원 변환"),
    "jo_cons": _d(GC, "consensus", "컨센서스 (조원)", "rev_cons / 1e12", JO, transform="/1e12"),
    "cons_asof": _d(GC, "consensus_asof", "컨센서스 관측일", "", "날짜", "월별 자료라 최대 한 달 묵은 값일 수 있음"),
    "g_cons_pct": _d(GC, "g_C_pct", "컨센서스 단순 성장률", "(rev_cons / rev_base - 1) x 100", PCT),
    "gap_E_vs_cons_pct": _d(GC, "gap_E", "E 의 컨센서스 대비 차이", "(rev_E / rev_cons - 1) x 100", PCT,
                            "내부 gap_E 는 비율 (x 100 하지 않은 값). 판단 규칙 4~6 의 기준 (5%)", transform="x100"),
    "gap_M4_vs_cons_pct": _d(GC, "gap_M4", "M4 의 컨센서스 대비 차이 (참고)", "(rev_M4 / rev_cons - 1) x 100", PCT,
                             transform="x100"),
    "gap_E_vs_cons_class": _d(GC, "gap_size", "|gap_E_vs_cons_pct| 크기 구분 (참고)", "소 < 2.44% <= 중 < 7.33% <= 대", "소 / 중 / 대",
                              "2b K2 판정 표본의 3분위 (고정값). 판단 규칙의 5% 기준과는 별개"),
    "dir_E_vs_cons": _d(GC, "gap_dir", "E 가 컨센서스보다 높은지 낮은지", "sign(gap_E_vs_cons_pct)", DIR,
                        "컨센서스가 없으면 공란. 추세 기준 방향은 dir_E_vs_trend. 내부 gap_dir 는 '위'/'아래'", transform="sign"),
    "agree_E_M4_vs_cons": _d(GC, "gap_agree", "E 와 M4 의 컨센서스 대비 방향이 같은지 (참고)",
                             "sign(gap_E_vs_cons_pct) 와 sign(gap_M4_vs_cons_pct) 비교", AGR),
    # 방향·신호
    "dir_E_vs_trend": _d(GD, "E_direction", "E 가 직전 추세(B2)보다 높은지 낮은지", "sign(g_E - g_B2)", DIR,
                         "컨센서스 기준 방향은 dir_E_vs_cons. 내부 E_direction 은 0 이면 '0'", transform="sign"),
    "dir_drv_vs_trend": _d(GD, "drv_direction", "드라이버가 직전 추세(B2)보다 위를 가리키는지", "sign(z_drv_vs_trend)", DIR,
                           "sign(g_M4 - g_B2) 와 같음 (분모 > 0). 대조군 공란", transform="sign"),
    "agree_E_drv_vs_trend": _d(GD, "agree", "dir_E_vs_trend 와 dir_drv_vs_trend 가 같은지", "둘 다 있으면 일치 / 불일치", AGR,
                               transform="agree"),
    "agree_drv_cons": _d(GD, None, "드라이버 방향(dir_drv_vs_trend)과 컨센서스 대비 방향(dir_E_vs_cons)이 같은지",
                         "둘 다 있으면 일치 / 불일치", AGR, "컨센서스가 없으면 공란. 판단 규칙 4·5 의 기준", transform="agree"),
    "z_drv_vs_trend": _d(GD, "nsur_drv", "드라이버 신호 크기 (표준화). 직전 추세(B2)로 설명되지 않고 드라이버에서만 나오는 부분을 "
                                         "매출의 평소 변동 폭으로 나눈 값",
                         "(rev_M4 - rev_B2) / rev_sigma_8q", "표준화 값 (sigma 배수, 1.0 = 평소 변동 1배)",
                         "2a v6 level 변형 (signals.sur). 분모 rev_sigma_8q 는 기업마다 달라 같은 로그 차이(shock_dev)라도 크기가 다름. "
                         "레이더 주 신호"),
    "z_M4_vs_exp": _d(GD, "nsur_M4", "M4 서프라이즈 전체 (참고)",
                      "(rev_M4 - 기대 매출) / rev_sigma_8q, 기대 매출 = rev_base + rev_delta_8q", "표준화 값",
                      "z_M4_vs_exp = z_B2_vs_exp + z_drv_vs_trend"),
    "z_B2_vs_exp": _d(GD, "nsur_mom", "모멘텀 서프라이즈 (참고). B2 예측이 평균 증가 기대에서 벗어난 정도",
                      "(rev_B2 - 기대 매출) / rev_sigma_8q", "표준화 값"),
    "rev_delta_8q": _d(GD, "delta", "평균 증가 기대의 증가액", "직전 8분기 D_j = Rev_j - Rev_(j-4) 의 평균 (기준일에 알던 매출, 6분기 이상)",
                       WON, "4분기 점검·기업 사건 분기는 D_j 에서 뺌"),
    "rev_sigma_8q": _d(GD, "sigma", "z_* 의 분모: 매출 전년 동기 대비 증가액의 평소 변동 폭", "같은 D_j 8분기의 표준편차 (ddof = 1)", WON),
    # 구성 불일치
    "spread_E_pct": _d(GS, None, "E 의 세 구성 예측 중 가장 튀는 예측이 나머지 둘의 평균과 다른 정도",
                       "dev_k = g_k - (나머지 둘의 평균), k = 드라이버 요소·B2·TS. max_k |exp(dev_k) - 1| x 100", PCT,
                       "구성 예측이 하나라도 없거나 대조군이면 공란. E 값은 바꾸지 않음", transform="spread"),
    "spread_E_from": _d(GS, None, "가장 튄 구성 예측", "argmax_k |dev_k|, 드라이버 요소는 M1_model 의 모형명 (M1/M2/M3)",
                        "모형명", transform="spread"),
    "flag_E_spread": _d(GS, None, "구성 불일치 표시", "spread_E_pct >= 15 이면 '불일치(<spread_E_from>)', 구성 예측이 없으면 '구성 부족'",
                        FLAG, "대조군은 공란. 판단 규칙 2·3 의 기준", transform="spread"),
    # 충격 경보
    "shock_dev": _d(GK, "shock_x", "충격 경보 판정용 이번 분기 값. 드라이버 보정이 직전 추세에서 벗어난 크기", "g_M4 - g_B2", LOG,
                    "z_drv_vs_trend 와 부호가 같지만 표준화하지 않은 로그 차이"),
    "shock_p10": _d(GK, "shock_p10", "그 기업 과거 shock_dev 분포의 하위 10% 값", "과거 분기 pre5 nowcast 의 (g_M4 - g_B2) 10% 분위", LOG,
                    "fd_nowcast 에 저장된 pre5 행 중 기준일이 레이더 기준일보다 앞선 모든 분기 (전체 기간 변형, 코로나 기간 포함)"),
    "shock_p90": _d(GK, "shock_p90", "그 기업 과거 shock_dev 분포의 상위 10% 값", "같은 분포의 90% 분위", LOG),
    "shock_n": _d(GK, "shock_n", "위 분포를 만든 과거 분기 수", "", "개", "8개 미만이면 경보 판단 안 함 (flag_shock 공란)"),
    "flag_shock": _d(GK, "shock_alert", "충격 경보", "shock_dev < p10 이면 '충격(하향)', > p90 이면 '충격(상향)'", FLAG,
                     "과거 8개 미만이거나 대조군이면 공란. 내부 shock_alert 는 '경보(상)'/'경보(하)'/'판단 안 함(...)'",
                     transform="flag"),
    # 드라이버 흐름
    "drv_3m_yoy_pct": _d(GR, "drv_3m_yoy", "레이더 기준일에 공표된 주 드라이버 최근 3개월의 전년 동기 대비 성장률",
                         "(최근 3개월 합 / 1년 전 같은 3개월 합 - 1) x 100", PCT,
                         "월별 YoY 평균이 아니라 3개월 합계의 비율 (내부 drv_3m_yoy 는 그 로그). 드라이버가 여러 개면 첫 번째 주 드라이버만 "
                         "(평균 아님, DRIVER_SPECS 순서: 삼성전자·하나투어·SK하이닉스 해당). 세그먼트 가중 기업은 세그먼트별 로그 값을 그 분기 "
                         "비중으로 가중평균. 3개월이 다 없으면 공란", transform="pct"),
    "drv_3m_last": _d(GR, "drv_3m_last", "drv_3m_yoy_pct 의 마지막 달", "", "YYYY-MM"),
    # 발표일·경고
    "announce_date": _d(GW, "announce_date", "예상 실적 발표일",
                        "이벤트표 첫 공시일이 있으면 그 날, 없으면 분기말 + 최근 5년 같은 분기 (첫 공시일 - 분기말) 일수 중앙값", "날짜"),
    "announce_basis": _d(GW, "announce_kind", "발표일 근거", "'확정' (실제 첫 공시일) 또는 '추정 (최근 n년 중앙값 d일)'", "문자",
                         transform="basis"),
    "flag_q4": _d(GW, "warnings", "4분기 차감 경고", "대상이 회계 4분기면 '4분기 차감' (실제치가 연간 - 1~3분기)", FLAG, transform="split"),
    "flag_drv_missing": _d(GW, "warnings", "드라이버 결측 경고", "M4 없음 '드라이버 결측', 드라이버 월 일부만 공개 '드라이버 부분(cov=)'",
                           FLAG, transform="split"),
    "flag_event": _d(GW, "warnings", "기업 사건 경고", "대상 분기가 기업 사건 제외 구간이면 '기업 사건'", FLAG, transform="split"),
    "flag_nowcast": _d(GW, "warnings", "예측 없음", "nowcast 를 만들지 못하면 'nowcast 없음 (사유)'", FLAG, transform="split"),
}
OUTPUT_COLUMNS = list(COLDESC)


def ordered(columns: Iterable[str]) -> List[str]:
    """설명 시트 순서: 분류 순서, 분류 안에서는 출력 표의 열 순서."""
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


def internal_name(c: str) -> str:
    return COLDESC[c]["internal"] or ""


def table(columns: Iterable[str]) -> pd.DataFrame:
    """변수설명 표: 분류 | 출력 변수명 | 내부 변수명 | 의미 | 계산식 | 단위 | 비고."""
    cols = list(dict.fromkeys(columns))
    miss = missing(cols)
    if miss:
        raise KeyError(f"COLDESC 에 설명이 없는 열: {miss}")
    rows = [{"group": COLDESC[c]["group"], "variable": c, "internal": internal_name(c),
             **{k: COLDESC[c][k] for k in ("meaning", "formula", "unit", "note")}} for c in ordered(cols)]
    return pd.DataFrame(rows, columns=list(SHEET_HEADER)).rename(columns=SHEET_HEADER)
