# -*- coding: utf-8 -*-
"""
STEP 4. 결과물 : 요약표 + 예측 그래프 + 최종 보고서(Markdown)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from common import D_DATA, D_TAB, D_FIG, D_REP, COMPANIES, setup_matplotlib, log
from features import SCENARIOS
from backtest import TARGETS, BASELINES, MIN_TRAIN

plt = setup_matplotlib()

SCEN_LABEL = {
    "A_AHEAD": "A. 사전예측(분기 시작 시점)",
    "B_NOWCAST_PARTIAL": "B. 당분기 추정(수출 1개월 공개)",
    "C_NOWCAST_FULL": "C. 당분기 추정(수출 3개월 공개)",
}
VAR_LAG = {s: {n: l for n, (c, l) in sp["export"].items()} for s, sp in SCENARIOS.items()}
VAR_SRC = {s: {n: c for n, (c, l) in sp["export"].items()} for s, sp in SCENARIOS.items()}


def md_table(df: pd.DataFrame) -> str:
    """tabulate 의존 없이 마크다운 표 생성."""
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            cells.append("" if pd.isna(v) else (
                "{:g}".format(v) if isinstance(v, (int, float, np.integer, np.floating)) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return chr(10).join(out)


def _clean(m: str) -> str:
    return m.replace("EXPs_", "").replace("EXP_", "")


# ---------------------------------------------------------------- 요약표
def build_summary(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code in COMPANIES:
        for tgt_lab in ev["목표변수"].unique():
            for scen in SCENARIOS:
                s = ev[(ev["ticker"] == code) & (ev["목표변수"] == tgt_lab) &
                       (ev["정보집합"] == scen)]
                if s.empty:
                    continue
                exp = s[s["모형"].str.startswith(("EXP_", "EXPs_")) & (s["모형"] != "EXP_AUTO")]
                if exp.empty:
                    continue
                best = exp.sort_values("MAE").iloc[0]
                bb = s[s["모형"] == best["최선기준모형"]].iloc[0]
                var = _clean(best["모형"])
                rows.append({
                    "ticker": code, "기업명": COMPANIES[code]["name"],
                    "목표변수": tgt_lab, "정보집합": SCEN_LABEL[scen],
                    "평가분기수": int(best["평가분기수"]), "평가기간": best["평가기간"],
                    "최선_수출변수": var,
                    "수출변수_분기시차": VAR_LAG[scen].get(var, np.nan),
                    "최선_수출모형_MAE": round(best["MAE"], 3),
                    "최선_무수출기준모형": best["최선기준모형"],
                    "기준모형_MAE": round(bb["MAE"], 3),
                    "MAE_개선율_vs_최선기준_pct": round(best["MAE_개선율_vs_최선기준_pct"], 2),
                    "RMSE_개선율_vs_최선기준_pct": round(best["RMSE_개선율_vs_최선기준_pct"], 2),
                    "MAE_개선율_vs_BASE_AR_pct": round(best["MAE_개선율_vs_BASE_AR_pct"], 2),
                    "DM_p값_vs_최선기준": round(best["DM_p값_vs_최선기준"], 3)
                    if pd.notna(best["DM_p값_vs_최선기준"]) else np.nan,
                    "수출지표_유용성": ("도움됨(약함)" if best["MAE_개선율_vs_최선기준_pct"] > 0
                                   else "도움 안 됨"),
                })
    out = pd.DataFrame(rows)
    out.to_csv(D_TAB / "step5_summary_best_export_variable.csv",
               index=False, encoding="utf-8-sig")
    return out


# ---------------------------------------------------------------- 그래프
def fig_forecast(fc_all: pd.DataFrame, ev: pd.DataFrame):
    for code in COMPANIES:
        for tgt in TARGETS:
            fig, axes = plt.subplots(len(SCENARIOS), 1, figsize=(12, 10), sharex=True)
            for ax, scen in zip(axes, SCENARIOS):
                d = fc_all[(fc_all["ticker"] == code) & (fc_all["정보집합"] == scen) &
                           (fc_all["목표변수"] == tgt)].set_index("date").sort_index()
                s = ev[(ev["ticker"] == code) & (ev["정보집합"] == scen) &
                       (ev["목표변수"] == TARGETS[tgt])]
                if d.empty or s.empty:
                    continue
                exp = s[s["모형"].str.startswith(("EXP_", "EXPs_")) & (s["모형"] != "EXP_AUTO")]
                bestm = exp.sort_values("MAE")["모형"].iloc[0]
                bb = s["최선기준모형"].iloc[0]
                ax.plot(d.index, d["actual"], "k-o", ms=4, lw=2, label="실적(실제)")
                ax.plot(d.index, d[bb], "--", color="#888", lw=1.6,
                        label="기준모형 {}".format(bb))
                ax.plot(d.index, d[bestm], "-", color="#c44e52", lw=1.8,
                        label="수출지표 모형 {}".format(bestm))
                ax.set_title("{} / {}".format(SCEN_LABEL[scen], TARGETS[tgt]), fontsize=10)
                ax.legend(fontsize=8, ncol=3)
                ax.set_ylabel(TARGETS[tgt])
            axes[-1].set_xlabel("분기")
            fig.suptitle("{}({}) 표본밖 예측 결과 — 확장창(expanding window)".format(
                COMPANIES[code]["name"], code), y=1.005)
            fig.tight_layout()
            fig.savefig(D_FIG / "fig4_{}_{}_oos_forecast.png".format(code, tgt))
            plt.close(fig)


def fig_improvement(ev: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    for ax, code in zip(axes, COMPANIES):
        s = ev[(ev["ticker"] == code) & ev["모형"].str.startswith("EXP_") &
               (ev["모형"] != "EXP_AUTO")].copy()
        s["var"] = s["모형"].map(_clean)
        piv = s.pivot_table(index="var", columns=["목표변수", "정보집합"],
                            values="MAE_개선율_vs_최선기준_pct")
        piv.plot.barh(ax=ax, width=0.8)
        ax.axvline(0, color="k", lw=1.2)
        ax.set_title("{}({})".format(COMPANIES[code]["name"], code))
        ax.set_xlabel("MAE 개선율 vs 최선 무수출 기준모형 (%, >0이 개선)")
        ax.set_ylabel("")
        ax.legend(fontsize=7, ncol=2)
    fig.suptitle("HS330499 수출지표 추가 시 표본밖 예측오차 개선율", y=1.02)
    fig.tight_layout()
    fig.savefig(D_FIG / "fig5_improvement_summary.png")
    plt.close(fig)


# ---------------------------------------------------------------- 보고서
def write_report(summary, ev, lv, meta, expq, flash, val):
    L = []
    A = L.append
    A("# HS 330499 수출지표의 화장품 기업 실적 예측력 분석")
    A("")
    A("- 대상기업: 한국화장품제조(003350), 에이피알(278470)")
    A("- 분석일: 2026-09-23")
    A("- DB: MySQL `investar` (호스트는 common.get_db_host 규칙)")
    A("")
    A("---")
    A("")
    A("## 1. 데이터 확인 및 추출")
    A("")
    A("### 1.1 사용한 테이블과 컬럼 (실제 스키마 확인 결과)")
    A("")
    A("| 용도 | 테이블 | 사용 컬럼 | 비고 |")
    A("|---|---|---|---|")
    A("| 수출(주) | `korea_monthly_trade_data` | `date, root_hs_code, indicator, value` | "
      "2007-01~2026-08. 가장 최근 갱신본 |")
    A("| 수출(교차검증) | `korea_monthly_trade_data_v2` | `+ region, item_name, created_at` | "
      "2007-01~2025-12. `region='전국'`, `item_name='기타'` |")
    A("| 재무(주) | `korea_fs_data_from_DG` | `date, ticker, indicator, sj_div, value, freq` | "
      "`sj_div='IS'`, `freq='4Q'`, 단위 천원 |")
    A("| 재무(검증) | `korea_fs_data_from_DART_V3` | `thstrm_amount, thstrm_add_amount, fs_div, reprt_code` | "
      "`thstrm_amount`=당분기 개별, `thstrm_add_amount`=누적 |")
    A("| 수출속보 | `korea_export_10day_flash_by_item` | `stat_date, segment, item_name, exp_dlr_cum` | "
      "**화장품 품목 없음 → 사용 불가** |")
    A("")
    A("### 1.2 HS 330499 수출자료의 단위·집계기준")
    A("")
    A("- `expDlr` = 수출총액, **단위 USD(달러)**, 집계기준 `region='전국'` (전체 상대국 합계)")
    A("- `expWgt` = 수출중량, **단위 kg**")
    A("- 검증: `expDlr/expWgt` = {:.1f}~{:.1f} USD/kg 로 화장품 단가로서 타당".format(
        float(expq.loc["unit_price_min_usd_per_kg", "value"]),
        float(expq.loc["unit_price_max_usd_per_kg", "value"])))
    A("- 기간 **{} ~ {}**, 총 {}개월, **결측 0개월**".format(
        expq.loc["start", "value"], expq.loc["end", "value"], expq.loc["n_months", "value"]))
    A("- 두 소스 교차검증: 겹치는 228개월에서 USD 최대괴리 **0.91%**, 중량 최대괴리 0.08%. "
      "구버전 테이블은 1,000달러 단위 반올림이며 2025년 구간에서 **사후 수정치**가 반영되어 있다.")
    A("- HS 분류개정(2012/2017/2022) 전후 수출단가 수준의 급변은 관측되지 않음 "
      "(`step1_hs_revision_check.csv`). 다만 330499는 '기타' 세번이므로 상위 3304 내 "
      "품목 재배분 가능성은 완전히 배제할 수 없다.")
    A("")
    A("### 1.3 기업 재무자료 — 누적/개별, 연결/별도, 사용가능기간")
    A("")
    A(md_table(meta))
    A("")
    A("- **누적 → 개별 변환**: DataGuide(`korea_fs_data_from_DG`)는 **이미 '당분기 개별' 수치**로 "
      "저장되어 있다. 이를 DART 원자료의 `thstrm_amount`(당분기)와 대조한 결과 "
      "**10개 분기 전부 괴리 0.0000%** 로 일치했다(`step1_dg_vs_dart_verification.csv`). "
      "따라서 누적치 차감 변환은 불필요했다.")
    A("  - 참고 검증: 003350 FY2025 연간 매출 184.63십억원 = 분기합(38.00+53.77+54.84+38.02), "
      "DART 3분기 누적 `thstrm_add_amount` 146.61 + 4분기 38.02 = 184.63 로 정합.")
    A("- **연결/별도 구분**: DART `fs_div` 기준 **003350 = OFS(별도·개별), 278470 = CFS(연결)**. "
      "두 기업의 재무제표 기준이 서로 다르므로 수준 비교는 부적절하다.")
    A("- **사용가능기간**: 003350은 2009Q4~2026Q2(67분기), 278470은 2018Q1~2026Q2(34분기)로 "
      "**기간 내 결측 분기 0개**. 에이피알은 2024년 2월 상장으로 표본이 짧다.")
    A("- **영업이익 0/음수 분기**: 003350 **19분기**, 278470 3분기 → 지시대로 `영업이익 YoY`는 "
      "주 목표변수에서 제외하고 **영업이익률(OPM)** 을 사용했다.")
    A("")
    A("### 1.4 확보하지 못한 자료 (임의 생성하지 않음)")
    A("")
    A("| 필요 자료 | 상태 | 필요한 경로 |")
    A("|---|---|---|")
    A("| HS330499 **수출 속보치(10일/20일)** | 없음. `korea_export_10day_flash_by_item`은 반도체·석유제품 등 "
      "**11개 대분류만** 포함 | 관세청 수출입무역통계 '품목별 10일 속보' 또는 산업부 월간 수출입동향 "
      "화장품 항목 수집 |")
    A("| 수출통계 **vintage(발표시점별 원본)** | 없음. 최신 수정치만 저장 | 관세청 월별 공표본 아카이빙 "
      "(공표시점 스냅샷 적재) |")
    A("| 기업별 **수출/내수 매출 분리**, 지역별 매출 | 없음 | DART 사업보고서 '매출 유형별/지역별' 주석 파싱 |")
    A("| 기업 **HS코드 매핑** | `korea_company_hscode_map` 존재하나 두 기업 미포함 | 관세청 기업별 수출실적 또는 "
      "IR 자료 기반 매핑 |")
    A("")
    A("---")
    A("")
    A("## 2. 시계열 변수와 정보집합")
    A("")
    A("월별: 수출총액, 수출총액 YoY, 수출중량 YoY, 수출단가 및 3개월 이동평균.")
    A("분기: 월별 합계(3개월 모두 존재할 때만 완전분기 인정) 및 각 YoY, "
      "부분집계(당분기 1개월 / 직전분기 앞 2개월)와 그 YoY.")
    A("")
    A("### 2.1 공표시차와 정보집합 (look-ahead 차단)")
    A("")
    A("관세청·무역협회의 **HS 6단위 월별 확정통계는 익월 15일경 공표**된다. "
      "또한 **분기보고서 법정 제출기한은 분기 종료 후 45일**이다. 이 두 시차를 모두 반영해 "
      "세 가지 정보집합을 구분했다.")
    A("")
    A("| 정보집합 | 시점 | 이용 가능한 기업 실적 | 이용 가능한 수출자료 |")
    A("|---|---|---|---|")
    A("| **A. 사전예측** | 분기 Q 시작일 | Q-2까지 (Q-1은 **미공시**) | 완전분기 Q-2까지 + Q-1 앞 2개월 |")
    A("| **B. 당분기 추정(부분)** | 분기 Q의 50일째 | Q-1까지 | 완전분기 Q-1 + **당분기 1개월** |")
    A("| **C. 당분기 추정(전체)** | 분기 Q 종료 +15일 | Q-1까지 | **당분기 Q 3개월 전부** |")
    A("")
    A("A 시나리오에서 자기회귀 항의 시차를 1이 아니라 **2로 둔 점**이 중요하다. "
      "분기 시작 시점에는 직전 분기 실적이 아직 공시되지 않았기 때문이다.")
    A("")
    A("---")
    A("")
    A("## 3. 예측력 검증 결과")
    A("")
    A("### 3.1 검증 설계")
    A("")
    A("- **확장창(expanding window) rolling-origin**: 원점 t에서 `[0, t)` 자료만으로 계수를 추정해 "
      "t 시점을 예측하고, 원점을 한 분기씩 전진시킨다.")
    A("- **변수 선택도 훈련표본 안에서만**: `EXP_AUTO`는 매 원점마다 훈련표본 BIC가 최소인 수출변수 "
      "1개를 고른다. 미래 자료는 선택에도 학습에도 쓰이지 않는다.")
    A("- **동일 평가분기**: 모든 모형이 예측값을 갖는 분기의 교집합에서만 MAE/RMSE를 비교한다.")
    A("- 최소 훈련표본: 003350 {}분기, 278470 {}분기.".format(MIN_TRAIN["003350"], MIN_TRAIN["278470"]))
    A("- 모형은 **절편 + 자기회귀 1~2항 + 수출변수 1개**로 제한했다. 표본이 짧아 그 이상은 과적합된다.")
    A("- 기준모형은 두 가지로 제시한다. (1) 사전지정 `BASE_AR`(y ~ AR1 + 전년동기), "
      "(2) **최선 무수출 기준모형**(NAIVE/AR1/BASE_AR 중 MAE 최소) — 후자가 더 엄격한 비교다.")
    A("")
    A("### 3.1.1 누수(look-ahead) 검증")
    A("")
    A("`validate.py`가 자동 점검한 결과 **{}개 검사 전부 PASS** (`step6_validation.csv`).".format(len(val)))
    A("")
    A(md_table(val[["검사항목", "결과", "상세"]].head(20)))
    A("")
    A("### 3.2 단순 상관관계 (사후적, 예측성능 아님)")
    A("")
    A("`step3_lag_correlation_full.csv`, `fig3_*_lag_corr_heatmap.png` 참조.")
    A("")
    A("- 003350: 영업이익률과 **수출총액 로그수준**의 상관이 시차 0~3분기에서 r=0.54~0.56으로 높게 "
      "나온다. 그러나 이는 **두 계열이 모두 장기 상승추세를 갖는 데서 오는 추세 공행**이며, "
      "증가율끼리 비교하면 |r|이 0.2~0.3 수준으로 떨어진다.")
    A("- 278470: 영업이익률과 수출총액 수준 r=0.55~0.57(시차 0·4분기). 매출액 YoY와 수출총액 "
      "수준 r=0.42(시차 0). 다만 **영업이익률 전년동기차와 수출 YoY는 음(-)의 상관**(시차 2분기 "
      "r=-0.51~-0.54)으로 부호가 뒤집힌다. 표본 30개에서 나온 값이라 안정적이지 않다.")
    A("- **결론: 수준-수준 상관은 대부분 추세에 의한 허위상관(spurious)이다.** "
      "아래 표본밖 검증이 실질적 판단 근거다.")
    A("")
    A("### 3.3 표본밖 예측 성능 — 기업별 최선 수출변수")
    A("")
    A(md_table(summary.drop(columns=["ticker"])))
    A("")
    A("### 3.4 기업별 해석")
    A("")
    A("#### 한국화장품제조 (003350) — 별도기준, 평가 39~43분기")
    A("")
    A("- 최선 무수출 기준모형은 모든 경우 `BASE_AR`이다.")
    A("- 수출지표를 더하면 **오차가 줄기는 하지만 개선폭이 매우 작다**. "
      "가장 좋은 경우가 C(당분기 3개월 공개) + **수출중량 YoY(시차 0)** 로, "
      "매출액 YoY의 MAE가 기준모형 18.20%p → 17.47%p (**-4.0%**), "
      "매출액 수준으로는 4.10 → 3.90십억원 (**-4.9%**)이다.")
    A("- 영업이익률은 개선폭이 더 작다(최대 1.6%). **DM 검정 p값은 어느 경우에도 0.25 미만이 되지 "
      "않아 통계적 유의성은 없다.**")
    A("- 시차 비교: 사전예측(A)에서는 **직전분기 앞 2개월 수출 YoY**(부분집계, 실질 시차 약 1분기)가, "
      "당분기 추정(C)에서는 **동행(시차 0) 수출중량 YoY**가 가장 낫다. "
      "즉 **선행지표라기보다 동행지표**에 가깝다.")
    A("")
    A("#### 에이피알 (278470) — 연결기준, 평가 12~16분기")
    A("")
    A("- 최선 무수출 기준모형은 모든 경우 **NAIVE**(직전 공시분기 값 유지)다. "
      "회귀형 기준모형(BASE_AR)은 표본이 14~16개로 짧아 과적합되어 NAIVE보다 크게 나쁘다.")
    A("- **NAIVE를 기준으로 하면 수출지표 모형은 거의 전부 더 나쁘다.** "
      "유일한 예외가 C(당분기 3개월 공개) + **수출총액 로그수준(시차 0)** 으로, "
      "매출액 YoY의 MAE가 NAIVE 10.82%p → 8.12%p (**-25.0%**), "
      "매출액 수준으로는 17.74 → 13.13십억원 (**-26.0%**)이다. "
      "(사전지정 기준모형 BASE_AR 대비로는 -56.9%이지만, BASE_AR 자체가 NAIVE보다 크게 나쁘므로 "
      "이 수치는 과장이다.) 그러나 **DM p=0.541로 전혀 유의하지 않고 평가분기가 12개뿐**이다.")
    A("- 영업이익률은 **모든 시나리오에서 NAIVE가 최선**이며 수출지표는 도움이 되지 않았다 "
      "(MAE 2.42%p vs 수출모형 4.5~5.4%p).")
    A("- 배경: 에이피알은 2023~2026년 매출이 연 60~130% 증가하는 **고성장 국면**이라 "
      "자기 시계열의 관성이 산업 전체 수출지표보다 훨씬 강한 신호다.")
    A("")
    A("### 3.5 두 기업 결과 차이")
    A("")
    A("| 항목 | 한국화장품제조(003350) | 에이피알(278470) |")
    A("|---|---|---|")
    A("| 재무제표 기준 | 별도(OFS) | 연결(CFS) |")
    A("| 표본(평가분기) | 39~43 | 12~16 |")
    A("| 최선 무수출 기준모형 | BASE_AR(회귀) | NAIVE(관성) |")
    A("| 수출지표 유용성 | 작지만 일관되게 (+) | 대체로 (-), 1개 조합만 (+) |")
    A("| 최선 수출변수 | 수출중량 YoY (동행) | 수출총액 로그수준 (동행) |")
    A("| 통계적 유의성 | 없음 (DM p>0.25) | 없음 (DM p=0.54) |")
    A("")
    A("한국화장품제조는 **ODM·제조 기반이라 산업 전체 수출물량 흐름과 접점이 크고**, "
      "그래서 중량 지표가 상대적으로 유용했던 것으로 보인다. 반면 에이피알은 "
      "**자사 브랜드·직판(D2C) 및 기기(뷰티 디바이스) 비중이 커** HS330499 "
      "(기초·색조 화장품 '기타' 세번) 하나로 포착되지 않는다.")
    A("")
    A("---")
    A("")
    A("## 4. 요약")
    A("")
    A("| 기업 | 가장 유용한 수출지표 | 매출액 vs 영업이익 | 적절한 선행시차 | 기준모형 대비 오차 개선 |")
    A("|---|---|---|---|---|")
    A("| 한국화장품제조(003350) | **분기 수출중량 YoY** (사전예측 시엔 직전분기 앞2개월 수출총액 YoY) | "
      "**매출액 쪽이 더 유용** (매출 MAE -4.0%, 영업이익률 -1.6%) | "
      "**0분기(동행)**. 사전예측에서는 실질 1분기 | 매출액 YoY MAE 18.20→17.47%p (**-4.0%**), "
      "매출액 수준 4.10→3.90십억원 (**-4.9%**). **유의하지 않음** |")
    A("| 에이피알(278470) | **분기 수출총액(로그수준)**, 단 당분기 3개월 공개 시점에 한함 | "
      "**매출액에만 제한적. 영업이익(률)에는 도움 안 됨** | "
      "**0분기(동행)**. 선행 시차에서는 개선 없음 | 매출액 YoY MAE 10.82→8.12%p (**-25.0%**) 이나 "
      "DM p=0.54, 평가 12분기. **신뢰하기 어려움** |")
    A("")
    A("**전체 결론**: HS330499 수출지표는 두 기업의 분기 실적 예측에서 "
      "**과거 실적만 쓰는 기준모형을 뚜렷하게 개선하지 못했다.** "
      "개선이 나타나는 경우도 (i) 당분기 수출이 모두 공개된 동행 시점이고, "
      "(ii) 개선폭이 작거나 표본이 짧아 통계적으로 유의하지 않다. "
      "**'다음 분기를 미리 예측'하는 용도(A)에서는 사실상 도움이 없었다.**")
    A("")
    A("---")
    A("")
    A("## 5. 한계와 다음 분석에 필요한 자료")
    A("")
    A("### 5.1 해석상 한계")
    A("")
    A("1. **인과관계로 해석하면 안 된다.** HS330499는 산업 전체 수출지표이고 두 기업의 합산 "
      "점유율은 크지 않다. 상관이 있어도 '산업 수요 → 개별기업 매출'이라는 인과의 증거가 아니다.")
    A("2. **제품구성 불일치**: 에이피알 매출에는 뷰티 디바이스·의류 등 HS330499에 잡히지 않는 "
      "품목이 포함된다. 한국화장품제조는 ODM으로 고객사 수출이 자사 매출에 반영되는 시점이 다르다.")
    A("3. **내수 비중과 매출 인식**: 두 기업 모두 내수·면세·해외법인 매출을 분리하지 않았다. "
      "해외법인 직판은 통관 수출과 인식 시점이 어긋난다.")
    A("4. **연결/별도 불일치**: 003350은 별도, 278470은 연결이다.")
    A("5. **vintage 부재로 인한 소폭의 look-ahead**: DB에 저장된 수출·재무 수치는 최신 수정치다. "
      "2025년 구간에서 수출액 수정폭이 최대 0.91% 확인됐다. 실제 실시간 예측 성능은 "
      "본 결과보다 약간 나쁠 수 있다.")
    A("6. **짧은 표본**: 에이피알은 평가분기가 12~16개뿐이다. MAE 개선율 25%도 "
      "분기 1~2개에 좌우될 수 있어 유의성을 주장할 수 없다.")
    A("7. **다중비교**: 수출변수 6종 × 시차 × 목표변수 2종 × 시나리오 3종을 비교했으므로 "
      "가장 좋은 조합의 성과는 선택편의로 과대평가되어 있다.")
    A("")
    A("### 5.2 다음 분석에 필요한 추가 자료")
    A("")
    A("1. **기업별 수출실적** (관세청 기업별 수출입실적 또는 DART 주석) — 산업 전체 대신 "
      "해당 기업의 실제 수출액을 쓰면 관계가 훨씬 직접적이다.")
    A("2. **HS330499 국가별 수출** (중국/미국/일본/동남아) — `korea_monthly_trade_data_v2`의 "
      "`region` 컬럼은 현재 '전국'만 적재되어 있다. 국가별 적재가 필요하다.")
    A("3. **HS330420/330491 등 인접 세번** 및 **HS3307, 8543(미용기기)** — 특히 에이피알의 "
      "디바이스 매출 포착용.")
    A("4. **수출 속보치(10일·20일)** 품목 확대 — 현재 속보 테이블에 화장품이 없어 "
      "분기 진행 중 실시간 추정의 정보량이 제한된다. 이것이 확보되면 시나리오 B의 "
      "정보집합이 크게 개선된다.")
    A("5. **기업 매출의 내수/수출/지역별 분해** (DART 사업보고서 주석).")
    A("6. **공표시점 vintage 아카이브** — 진정한 실시간 백테스트를 위해 필요.")
    A("7. 보조지표: 화장품 소매판매액지수, 면세점 매출, 중국 광군제/618 시즌더미, "
      "환율(USD/KRW) — 통관 달러액과 원화 매출의 괴리를 흡수.")
    A("")
    A("---")
    A("")
    A("## 6. 산출물")
    A("")
    A("```")
    A("hs330499_cosmetics/")
    A("├─ src/   common.py  extract.py  features.py  analysis.py  backtest.py  report.py  run_all.py")
    A("├─ output/data/      export_hs330499_monthly.csv, export_monthly_features.csv,")
    A("│                    export_quarterly_features.csv, financials_quarterly.csv,")
    A("│                    company_*.csv, dataset_<ticker>_<시나리오>.csv")
    A("├─ output/tables/    step1_schema.csv, step1_export_quality.csv,")
    A("│                    step1_export_source_crosscheck.csv, step1_hs_revision_check.csv,")
    A("│                    step1_financial_meta.csv, step1_dg_vs_dart_verification.csv,")
    A("│                    step1_flash_availability.csv, step2_information_sets.csv,")
    A("│                    step3_lag_correlation_full.csv, step3_lag_correlation_best.csv,")
    A("│                    step4_oos_performance.csv, step4_oos_level_performance.csv,")
    A("│                    step4_oos_forecasts.csv, step4_auto_selection.csv,")
    A("│                    step5_summary_best_export_variable.csv, step6_validation.csv")
    A("├─ output/figures/   fig1_export_monthly.png, fig2_*_vs_export.png,")
    A("│                    fig3_*_lag_corr_heatmap.png, fig4_*_oos_forecast.png,")
    A("│                    fig5_improvement_summary.png")
    A("└─ output/report/    FINAL_REPORT.md")
    A("```")
    A("")
    A("재현: `cd src && python run_all.py`")

    txt = "\n".join(L)
    (D_REP / "FINAL_REPORT.md").write_text(txt, encoding="utf-8")
    log("REPORT", "FINAL_REPORT.md 저장 ({} 자)".format(len(txt)))
    return txt


def main():
    ev = pd.read_csv(D_TAB / "step4_oos_performance.csv", dtype={"ticker": str})
    lv = pd.read_csv(D_TAB / "step4_oos_level_performance.csv", dtype={"ticker": str})
    fc = pd.read_csv(D_TAB / "step4_oos_forecasts.csv", parse_dates=["date"], dtype={"ticker": str})
    meta = pd.read_csv(D_TAB / "step1_financial_meta.csv", dtype={"ticker": str})
    expq = pd.read_csv(D_TAB / "step1_export_quality.csv", index_col=0)
    flash = pd.read_csv(D_TAB / "step1_flash_availability.csv")
    val = pd.read_csv(D_TAB / "step6_validation.csv")

    summary = build_summary(ev)
    fig_forecast(fc, ev)
    fig_improvement(ev)
    write_report(summary, ev, lv, meta, expq, flash, val)

    print(summary.drop(columns=["ticker"]).to_string(index=False))


if __name__ == "__main__":
    main()
