# -*- coding: utf-8 -*-
"""
STEP 5. 검증 : 미래정보 유입(look-ahead) 및 자료 정합성 점검
결과는 output/tables/step6_validation.csv 에 저장.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from common import D_DATA, D_TAB, COMPANIES, log
from features import SCENARIOS

RES = []


def chk(name: str, ok: bool, detail: str = ""):
    RES.append({"검사항목": name, "결과": "PASS" if ok else "FAIL", "상세": detail})
    print("  [{}] {} {}".format("PASS" if ok else "FAIL", name, detail))


def main():
    RES.clear()

    # 1) 분기 수출 = 월별 3개월 합계인지
    mo = pd.read_csv(D_DATA / "export_monthly_features.csv", parse_dates=["date"]).set_index("date")
    qe = pd.read_csv(D_DATA / "export_quarterly_features.csv", parse_dates=["date"]).set_index("date")
    man = mo["exp_usd"].groupby(pd.PeriodIndex(mo.index, freq="Q")).sum(min_count=3)
    man.index = man.index.to_timestamp(how="end").normalize()
    d = (qe["qexp_usd"] - man.reindex(qe.index)).abs().max()
    chk("분기 수출총액 = 월별 3개월 합계", (pd.isna(d) or d < 1e-6), "최대오차 {:.2e}".format(d))

    # 2) 미완성 분기(2026Q3)가 완전분기로 오인되지 않았는지
    inc = qe[qe["n_months"] < 3]
    chk("미완성 분기는 qexp_usd 결측 처리",
        bool(inc["qexp_usd"].isna().all()),
        "미완성 분기: {}".format([str(i.date()) for i in inc.index]))

    # 3) YoY 정의 검증 (임의 표본)
    s = qe["qexp_usd"]
    man_yoy = (s / s.shift(4) - 1) * 100
    d2 = (qe["qexp_usd_yoy"] - man_yoy).abs().max()
    chk("분기 수출 YoY 계산 정확", d2 < 1e-9, "최대오차 {:.2e}".format(d2))

    # 4) 정보집합별 시차가 실제로 적용됐는지 (수출변수)
    for code in COMPANIES:
        for scen, spec in SCENARIOS.items():
            df = pd.read_csv(D_DATA / "dataset_{}_{}.csv".format(code, scen),
                             parse_dates=["date"]).set_index("date")
            bad = []
            for name, (col, lag) in spec["export"].items():
                exp_ref = qe[col].reindex(df.index).shift(lag)
                diff = (df[name] - exp_ref).abs().max()
                if not (pd.isna(diff) or diff < 1e-9):
                    bad.append(name)
            chk("{} {} 수출변수 시차 적용".format(code, scen), not bad,
                "불일치: {}".format(bad) if bad else "{}개 변수 일치".format(len(spec["export"])))

    # 5) 자기회귀 변수 시차 (실적 공시시차) 검증
    for code in COMPANIES:
        p = pd.read_csv(D_DATA / "company_{}_features.csv".format(code),
                        parse_dates=["date"]).set_index("date")
        for scen, spec in SCENARIOS.items():
            df = pd.read_csv(D_DATA / "dataset_{}_{}.csv".format(code, scen),
                             parse_dates=["date"]).set_index("date")
            el = spec["earn_lag"]
            d3 = (df["rev_yoy_AR1"] - p["rev_yoy"].shift(el)).abs().max()
            d4 = (df["opm_SEAS"] - p["opm"].shift(4)).abs().max()
            chk("{} {} 실적 자기회귀 시차(earn_lag={})".format(code, scen, el),
                (pd.isna(d3) or d3 < 1e-9) and (pd.isna(d4) or d4 < 1e-9),
                "AR1 오차 {:.2e}".format(0 if pd.isna(d3) else d3))

    # 6) 표본밖 예측이 '훈련구간 이후'에만 존재하는지
    fc = pd.read_csv(D_TAB / "step4_oos_forecasts.csv", parse_dates=["date"],
                     dtype={"ticker": str})
    ok = True; det = []
    for (code, scen, tgt), g in fc.groupby(["ticker", "정보집합", "목표변수"]):
        df = pd.read_csv(D_DATA / "dataset_{}_{}.csv".format(code, scen),
                         parse_dates=["date"]).set_index("date")
        need = [tgt, "{}_AR1".format(tgt), "{}_SEAS".format(tgt)]
        avail = df.dropna(subset=need).index
        from backtest import MIN_TRAIN
        first_ok = avail[MIN_TRAIN[code]] if len(avail) > MIN_TRAIN[code] else None
        if first_ok is not None and g["date"].min() < first_ok:
            ok = False; det.append("{}/{}/{}".format(code, scen, tgt))
    chk("표본밖 예측 시작시점 >= 최소훈련표본 이후", ok, "위반: {}".format(det) if det else "전 조합 정상")

    # 7) 동일 평가분기 비교 여부
    ev = pd.read_csv(D_TAB / "step4_oos_performance.csv", dtype={"ticker": str})
    g = ev.groupby(["ticker", "정보집합", "목표변수"])["평가분기수"].nunique()
    chk("모형 간 동일 평가분기 사용", bool((g == 1).all()),
        "조합별 평가분기수 고유값 최대 {}".format(int(g.max())))

    # 8) 재무 단위 정합 (연간 합계 대조)
    fs = pd.read_csv(D_DATA / "financials_quarterly.csv", parse_dates=["date"],
                     dtype={"ticker": str})
    a = fs[(fs.ticker == "003350") & (fs.date.dt.year == 2024)]["revenue_bn"].sum()
    b = fs[(fs.ticker == "278470") & (fs.date.dt.year == 2024)]["revenue_bn"].sum()
    chk("2024년 연간매출 합계 타당성(십억원)", (150 < a < 190) and (650 < b < 800),
        "003350={:.1f}, 278470={:.1f}".format(a, b))

    out = pd.DataFrame(RES)
    out.to_csv(D_TAB / "step6_validation.csv", index=False, encoding="utf-8-sig")
    n_fail = int((out["결과"] == "FAIL").sum())
    log("VALIDATE", "{}개 검사 / FAIL {}개".format(len(out), n_fail))
    return out


if __name__ == "__main__":
    main()
