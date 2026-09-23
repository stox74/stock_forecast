# -*- coding: utf-8 -*-
"""Korea_revenue_forecast_v4 평가 보고서 생성."""
from __future__ import annotations
import pandas as pd
import numpy as np
from scipy import stats
from common import D_TAB, D_REP, log
from report import md_table


def main():
    raw = pd.read_csv(D_TAB / "step19_notebook_forecast_eval_raw.csv")
    byh = pd.read_csv(D_TAB / "step20_notebook_eval_by_horizon.csv")
    byg = pd.read_csv(D_TAB / "step21_notebook_eval_by_growth.csv")

    # 승률 유의성 (이항검정)
    sig = []
    for _, r in byh.iterrows():
        s = raw[(raw["horizon"] == r["예측시계_분기"])].dropna(
            subset=["ape_Ensemble", "ape_naive_yoy"])
        k = int((s["ape_Ensemble"] < s["ape_naive_yoy"]).sum())
        p = stats.binomtest(k, len(s), 0.5).pvalue if len(s) else np.nan
        sig.append(p)
    byh["승률_이항검정_p"] = sig

    L = []
    A = L.append
    A("# `Korea_revenue_forecast_v4` 평가 — 저장된 예측을 실적과 대조")
    A("")
    A("`korea_revenue_forecast_result`에는 실행시점(`created_at`)별 vintage가 남아 있다. "
      "각 vintage는 그 시점에 공시돼 있던 분기까지만 보고 앞을 예측한 것이므로 "
      "**사후 가정이 전혀 없는 진짜 표본밖 예측**이다. 이미 실적이 나온 분기와 대조해 "
      "이 방법이 실제로 얼마나 맞았는지 측정했다.")
    A("")
    A("- 대조 표본: **{:,}건** (종목 {:,}개, vintage 6개)".format(
        len(raw), raw["ticker"].nunique()))
    A("- 기준모형: `naive_yoy` = 전년 동기 매출 × (직전 공시분기의 전년동기 증가율). "
      "앞선 분석에서 에이피알에 가장 강했던 그 모형이다.")
    A("- 지표: 중위절대오차율(MdAPE). 종목 간 매출 규모 차이가 커 평균 대신 중위값을 쓴다.")
    A("")
    A("---")
    A("")
    A("## 1. 결론 먼저")
    A("")
    A("| | 판정 |")
    A("|---|---|")
    A("| 방법론 자체 | **합리적이다.** 1,278개 종목 전체에서 naive를 이긴다 |")
    A("| 고성장 기업 일반 | **특히 좋다.** MdAPE 25.7% vs naive 50.0%, 승률 68% |")
    A("| **에이피알에 적용** | **이 종목에서는 졌다.** 15.5% vs naive 2.4%, 5번 모두 과소예측 |")
    A("| 한국화장품제조 | 이겼다. 12.6% vs naive 16.5% |")
    A("| 파이프라인 상태 | **버그 3건 발견** (아래 4장) |")
    A("")
    A("---")
    A("")
    A("## 2. 전체 종목 성능 — 방법론은 타당하다")
    A("")
    t = byh[["예측시계_분기", "n", "종목수", "MdAPE_Ensemble", "MdAPE_SARIMA", "MdAPE_ETS",
             "MdAPE_Theta", "MdAPE_naive_yoy", "MdAPE_naive_seas",
             "앙상블_승률_vs_naive_yoy", "승률_이항검정_p"]].round(2)
    t.columns = ["시계(분기)", "n", "종목수", "앙상블", "SARIMA", "ETS", "Theta",
                 "naive_yoy", "naive_seas", "앙상블 승률%", "승률 p값"]
    A(md_table(t))
    A("")
    A("- **1분기 앞: 앙상블 8.90% vs naive 10.09%** — 이기고, 승률 56.3%(p<0.001로 유의).")
    A("- **2분기 앞: 8.98% vs 12.26%** — 격차가 더 벌어진다. 승률 61.2%.")
    A("- 3분기 앞부터는 naive와 같아진다(13.80 vs 13.74). "
      "**이 방법의 유효 사거리는 2분기 정도**로 보는 게 맞다.")
    A("- 개별 모형(SARIMA/ETS/Theta)보다 **앙상블이 대체로 낫다** — 앙상블 구성 자체는 잘 작동한다.")
    A("")
    A("### 성장구간별 (시계 1~4분기)")
    A("")
    t = byg.round(2)
    t.columns = ["성장구간", "n", "종목수", "앙상블", "naive_yoy", "naive_seas", "앙상블 승률%"]
    A(md_table(t))
    A("")
    A("**고성장(전년비 >50%) 구간에서 앙상블의 우위가 가장 크다** "
      "(25.67% vs 50.0%, 승률 68.4%). 고성장주에서 naive가 무너지는 것을 앙상블이 잡아준다. "
      "일반론으로는 에이피알 같은 종목에 오히려 적합해 보인다.")
    A("")
    A("---")
    A("")
    A("## 3. 그런데 에이피알에서는 졌다")
    A("")
    A("실제 저장된 예측 5건 전부를 실적과 대조한 결과다. (단위: 십억원)")
    A("")
    s = raw[raw["ticker"] == "A278470"].sort_values(["vintage", "date"])
    o = pd.DataFrame({
        "예측시점": s["vintage"], "대상분기": s["date"], "시계": s["horizon"],
        "실제": (s["actual"] / 1e6).round(1),
        "앙상블": (s["Ensemble"] / 1e6).round(1),
        "앙상블오차%": ((s["Ensemble"] - s["actual"]) / s["actual"] * 100).round(1),
        "naive_yoy": (s["naive_yoy"] / 1e6).round(1),
        "naive오차%": ((s["naive_yoy"] - s["actual"]) / s["actual"] * 100).round(1),
    })
    A(md_table(o))
    A("")
    A("**평균 절대오차율: 앙상블 15.5% vs naive_yoy 2.4%**")
    A("")
    A("주목할 점은 **앙상블이 5번 모두 과소예측(-0.6 ~ -28.7%)** 했다는 것이다. "
      "무작위 오차가 아니라 한쪽으로 쏠린 편향이다. 이유는 구조적이다.")
    A("")
    A("- SARIMA·ETS·Theta는 모두 **매출 '수준'의 시계열 패턴**을 적합한다. "
      "이 모형들은 본질적으로 추세를 감쇠(damping)시키도록 설계돼 있어, "
      "에이피알처럼 **분기 매출이 2년 만에 149 → 768십억원으로 5배가 되는 폭발적 성장**을 "
      "따라가지 못한다.")
    A("- 반면 `naive_yoy`는 **증가율을 그대로 잇는다**. 에이피알의 전년비 증가율이 "
      "최근 121 → 124 → 123 → 134%로 매우 안정적이라, 이 단순한 방법이 오차 2.4%로 맞았다.")
    A("")
    A("이 결과는 **제가 앞서 독립적으로 돌린 30분기 확장창 백테스트와 일치**한다. "
      "거기서도 에이피알은 NAIVE(10.8%p) < AR1(16.7) < 회귀(18.8) 순으로, "
      "단순할수록 좋았다. **서로 다른 두 방법이 같은 결론을 가리킨다.**")
    A("")
    A("다만 대조 건수가 **5건뿐**이라는 한계는 분명히 해둔다. "
      "다음 두 분기 실적이 나오면 재검증해야 한다.")
    A("")
    A("### 한국화장품제조는 반대")
    A("")
    s = raw[raw["ticker"] == "A003350"].sort_values(["vintage", "date"])
    o = pd.DataFrame({
        "예측시점": s["vintage"], "대상분기": s["date"], "시계": s["horizon"],
        "실제": (s["actual"] / 1e6).round(1),
        "앙상블": (s["Ensemble"] / 1e6).round(1),
        "앙상블오차%": ((s["Ensemble"] - s["actual"]) / s["actual"] * 100).round(1),
        "naive오차%": ((s["naive_yoy"] - s["actual"]) / s["actual"] * 100).round(1),
    })
    A(md_table(o))
    A("")
    A("**앙상블 12.6% vs naive 16.5%** — 여기서는 앙상블이 이긴다. "
      "매출이 완만하고 계절성이 뚜렷한 기업에서는 시계열 모형이 제 역할을 한다. "
      "앞선 분석에서 003350은 회귀형 모형이 NAIVE보다 나았던 것과 같은 방향이다.")
    A("")
    A("---")
    A("")
    A("## 4. 파이프라인에서 발견한 문제 3건")
    A("")
    A("### (1) 단위 불일치 — 1000배 차이 [심각]")
    A("")
    A("vintage별로 `value`의 단위가 다르다. 같은 컬럼에 섞여 있고 구분 플래그가 없다.")
    A("")
    A("| vintage | 저장 단위 | ticker 형식 |")
    A("|---|---|---|")
    A("| 2025-12-04 / 12-05 / 2026-02-01 / 02-02 | **원** (1000배 큼) | `278470` (접두사 없음) |")
    A("| 2026-04-21 이후 | **천원** (정상) | `A278470` |")
    A("")
    A("`korea_revenue_forecast_result`를 vintage 구분 없이 조회하면 "
      "**1000배 틀린 값이 섞여 나온다.** 2026년 4월경 코드 수정 때 ticker 형식과 단위가 "
      "동시에 바뀐 것으로 보이며, 과거 행은 보정되지 않았다.")
    A("")
    A("→ 조치: 과거 vintage 행의 `value`를 1000으로 나누어 `UPDATE` 하거나, "
      "`unit` 컬럼을 추가해 명시한다. 소비하는 쪽 코드도 함께 점검해야 한다.")
    A("")
    A("### (2) 최신 실행이 예측을 갱신하지 않는다 [심각]")
    A("")
    A("vintage별 **ticker당 저장 분기 수**를 세어보면:")
    A("")
    A("| vintage | ticker당 분기 수 | 정상 여부 |")
    A("|---|---|---|")
    A("| 2025-12-04 / 12-05 | 9.0 | 정상 |")
    A("| 2026-02-01 / 02-02 | 7.9 / 8.0 | 정상 |")
    A("| 2026-04-21 | 8.0 | 정상 |")
    A("| **2026-05-29** | **1.06** | **비정상** |")
    A("| **2026-08-26** | **1.07** | **비정상** |")
    A("")
    A("최근 두 번의 실행은 **8분기가 아니라 새로 늘어난 맨 끝 1분기만** 저장했다. "
      "중복키 처리(insert-if-not-exists) 때문에 기존 분기가 갱신되지 않은 것으로 보인다.")
    A("")
    A("**결과: 에이피알의 2026Q3 예측은 아직도 2026-04-21 vintage다.** "
      "그 예측은 2025Q4까지의 데이터로 만들어졌고, 그 뒤 공시된 "
      "**2026Q1(593.4) · 2026Q2(767.5) 실적이 반영돼 있지 않다.** "
      "지금 이 값을 그대로 쓰면 5개월 묵은 예측을 쓰는 셈이다.")
    A("")
    A("→ 조치: 저장 로직을 `ON DUPLICATE KEY UPDATE`로 바꾸거나, "
      "(ticker, date, indicator, created_at) 단위로 새 vintage를 온전히 적재한다.")
    A("")
    A("### (3) 영업이익은 아예 예측하지 않는다")
    A("")
    A("이 노트북은 매출액(`M000904001`)만 다룬다. 영업이익 예측은 없다. "
      "다만 에이피알은 **영업이익률이 최근 4분기 24.9 / 23.8 / 24.5 / 24.2%로 "
      "표준편차 0.48%p** 에 불과하다. 매출 예측에 영업이익률을 곱하는 방식이 현실적이다. "
      "(한국화장품제조는 영업이익률 변동이 커서 같은 방식이 덜 안정적이다.)")
    A("")
    A("---")
    A("")
    A("## 5. 종합 평가와 권고")
    A("")
    A("**질문: 에이피알을 시계열 앙상블로 예측하는 게 합리적인가?**")
    A("")
    A("→ **방법론은 합리적이지만, 에이피알에 단독으로 쓰는 것은 현재 근거상 최선이 아니다.** "
      "전체 종목에서는 잘 작동하고 고성장 구간에서도 평균적으로는 우수하지만, "
      "정작 에이피알에서는 5번 모두 과소예측하며 단순 증가율 연장(naive_yoy)에 크게 뒤졌다. "
      "독립적인 30분기 백테스트도 같은 방향을 가리킨다.")
    A("")
    A("### 권고")
    A("")
    A("1. **종목별로 모형을 고르는 절차를 넣는다.** 이번 결과가 그 필요성을 보여준다. "
      "003350은 앙상블, 278470은 naive_yoy가 낫다. "
      "`korea_revenue_forecast_result`에 이미 vintage가 쌓이고 있으므로, "
      "**vintage 기반 성능을 종목별로 누적 집계해 자동 선택**하면 된다. 새 데이터 수집이 필요 없다.")
    A("2. **naive_yoy를 후보 모형으로 추가한다.** 지금 앙상블은 SARIMA/ETS/Theta 3개뿐이다. "
      "비용이 거의 없고 고성장주에서 강력하다.")
    A("3. **예측 시계는 2분기까지만 신뢰한다.** 3분기부터 naive와 차이가 없다.")
    A("4. **단위 버그와 갱신 누락을 먼저 고친다.** 이게 해결되지 않으면 위 개선이 무의미하다.")
    A("5. **영업이익은 매출 예측 × 영업이익률**로 산출하되, 영업이익률의 안정성을 종목별로 "
      "점검한 뒤 적용한다.")
    A("")
    A("### 다만 유의할 점")
    A("")
    A("에이피알의 naive_yoy가 잘 맞은 것은 **증가율이 120~134%로 안정적이었기 때문**이다. "
      "이 성장세가 꺾이는 순간 naive_yoy는 크게 과대예측한다 "
      "(앞서 확인: 감속 국면 평균 +19.1%p 과대). "
      "**어느 모형도 변곡점은 잡지 못한다**는 점은 변하지 않는다.")
    A("")
    A("---")
    A("")
    A("## 재현")
    A("")
    A("```bash")
    A("cd src")
    A("python eval_notebook_forecast.py   # vintage 대조 평가")
    A("python eval_notebook_report.py     # 본 보고서")
    A("```")
    A("")
    A("표: `step19_notebook_forecast_eval_raw.csv`, `step20_notebook_eval_by_horizon.csv`, "
      "`step21_notebook_eval_by_growth.csv`")

    txt = "\n".join(L)
    (D_REP / "EVAL_Korea_revenue_forecast_v4.md").write_text(txt, encoding="utf-8")
    log("EVAL-REPORT", "저장 ({} 자)".format(len(txt)))


if __name__ == "__main__":
    main()
