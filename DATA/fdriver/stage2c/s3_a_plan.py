# -*- coding: utf-8 -*-
"""2c 작업 3 1-(a): 판정 계획 고정. analysis_plan.csv, main_model_by_entity.csv (오차는 계산하지 않음).

평가 분기 수는 주 모델과 B2 의 pre5 예측과 첫 공시 값이 모두 있는 분기 수다 (값의 크기는 보지 않음).
실행: python -m DATA.fdriver.stage2c.s3_a_plan
"""
import hashlib

import pandas as pd

from .. import db, entity
from ..config import output_path
from . import registry
from . import s3_lib as L

OUT = output_path("2c", "step3", "plan")

PLAN = [
    ("판정 표본", "2c 33곳 (2a 시범 6곳 제외)"),
    ("기준일", "pre5"),
    ("실제 값", "첫 공시 값 (rev_actual_first)"),
    ("주 모델", "드라이버 2개 지정 기업(SK하이닉스 000660) = M4:combo, 나머지 = M4:<드라이버> (M4:export, M4:inbound, M4:outbound)"),
    ("비교 기준", "B2"),
    ("평가 분기", "기업별로 주 모델과 B2 가 모두 있는 공통 분기 (pre5, 전체 기간 변형 'all'), 기업 사건 제외 분기 뺌, fallback:B3 행 뺌"),
    ("오차", "g_hat - (ln 첫 공시 매출 - ln rev_base): 전년 동기 대비 로그 성장률 기준 (2a forecast_eval.prepare 와 동일)"),
    ("유의수준", "단측 p < 0.10, 다중 검정 보정 없음 (사전 지정 3개 기준)"),
    ("C1(a)", "기업별 상대 RMSE = RMSE(주 모델)/RMSE(B2) (공통 분기). 1 미만 기업 비율, 50% 대비 부호 검정 단측 (binomtest greater)"),
    ("C1(b)", "기업·분기를 모두 쌓은 d = 주 모델 제곱오차 - B2 제곱오차의 평균. d ~ 1 OLS, 분기(fiscal_quarter) 군집 표준오차, H1: 평균 < 0 단측"),
    ("C1 성공", "(a) 비율 > 50% 이고 p < 0.10, (b) 평균 < 0 이고 p < 0.10 둘 다"),
    ("C2 신호", "2a v6 6절 정의 그대로 (signals.sur.build_signals). level 변형(2a 주), 기준일 pre5, 주 모델 행. delta·sigma 는 각 시점 값"),
    ("C2 검정", "기업 전체에서 corr(nsur_drv, SUR - nsur_mom). 부호 적중률 = nsur_drv != 0 인 행에서 sign(nsur_drv) = sign(SUR - nsur_mom) 비율 (2a 와 같음), 50% 대비 이항 검정 단측. 판정 p 는 이항 검정 p (상관의 t 검정 p 는 참고로만 보고)"),
    ("C2 성공", "상관 > 0, 부호 적중률 > 50%, 이항 검정 단측 p < 0.10"),
    ("C3", "매매 구간(신호 다음 거래일 ~ day +1) CAR(%, 코스피 대비, prc_change) = 기업 FE + b_mom*nsur_mom + b_drv*nsur_drv, 분기 군집 표준오차 (2a event_study.fe_cluster_ols). 표본 = C2 신호 행 중 매매 구간 CAR 이 있는 이벤트"),
    ("C3 성공", "b_drv > 0 이고 단측 p < 0.10. N 과 기업 수 보고 (목표 약 1,000건, 미달이어도 판정)"),
    ("단측 p 계산", "회귀(statsmodels 군집 표준오차, 2a 와 같은 기본 설정)의 양측 p 에서 가설 방향이면 p/2, 아니면 1 - p/2"),
    ("자료", "fd_nowcast (작업 2b 보정 커밋 0afac7e 기준, registry rules '2b2'), fd_earnings_event, KSE_Price·kr_stock_index_daily (2026-10-02 까지)"),
    ("보고 전용 (판정에 안 씀)", "업종별, 기업별(DM), 구간별(사전·발표 반응·사후 표류), 코로나 2020Q1~2021Q4 제외, Cook's distance 상위 1% 제외 C3, M1·B1·B3 비교, 환율 기술통계(원/달러 분기 평균의 전년 동기 대비 로그 변화, v_korea_fx_monthly_wide), 2a 시범 6곳, 2b 규칙 대비 v4 3곳"),
]


def main():
    b = registry.register(refresh=True, rules="2b2")
    comp = b["mapping"]["companies"]
    comp = comp[comp["included"] & ~comp["entity_id"].isin(entity.PILOTS)]
    ids = list(comp["entity_id"])
    main = {e: L.main_model_of(e) for e in ids}
    d = L.eval_rows(ids, main)
    cq = L.common_quarters(d)
    cnt = cq.groupby("entity_id").agg(n_eval_quarters=("fiscal_quarter", "size"), first_q=("fiscal_quarter", "min"),
                                      last_q=("fiscal_quarter", "max")).reset_index()
    t = comp[["entity_id", "name", "sector"]].merge(cnt, on="entity_id", how="left")
    t.insert(3, "main_model", t["entity_id"].map(main))
    t["n_eval_quarters"] = t["n_eval_quarters"].fillna(0).astype(int)
    t.to_csv(OUT / "main_model_by_entity.csv", index=False, encoding="utf-8-sig")
    plan = pd.DataFrame(PLAN, columns=["항목", "값"])
    plan.to_csv(OUT / "analysis_plan.csv", index=False, encoding="utf-8-sig")
    sha = hashlib.sha256((OUT / "analysis_plan.csv").read_bytes()).hexdigest()
    (OUT / "analysis_plan.sha256").write_text(sha + "\n", encoding="utf-8")
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))
    print("total eval quarters", int(t["n_eval_quarters"].sum()), "sha256", sha)


if __name__ == "__main__":
    main()
