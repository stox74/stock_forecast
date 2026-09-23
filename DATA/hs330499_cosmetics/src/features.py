# -*- coding: utf-8 -*-
"""
STEP 2. 시계열 변수 생성

월별 수출지표 -> 분기 집계 -> 정보집합(information set)별 설명변수 구성

[수출통계 공표 시차 가정]
  관세청/무역협회 HS 6단위 월별 확정통계: 월 M 자료는 익월(M+1) 15일경 공표.
  DB(korea_monthly_trade_data)에는 vintage(발표시점별 원본)가 없고 최신 수정치만
  저장되므로, 공표시차는 달력 규칙으로 적용하고 '수정치 사용'은 한계로 명시한다.

[정보집합 3종]
  S1 AHEAD          : 분기 Q 시작일(m1 1일) 시점. 수출은 m1-2월까지 확정 공표.
                      -> 완전한 분기 수출은 Q-2까지. Q-1은 앞 2개월만 이용 가능.
  S2 NOWCAST_PARTIAL: 분기 Q의 m2 15일 시점. 수출은 m1(당분기 1개월)까지 공표.
                      -> Q-1 전체 + 당분기 1개월.
  S3 NOWCAST_FULL   : 분기 Q 종료 후 15일 시점(분기보고서 법정기한 45일 이전).
                      -> 당분기 Q 3개월 전부 공표.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from common import D_DATA, D_TAB, COMPANIES, log, yoy


# ---------------------------------------------------------------- 월별 지표
def build_monthly() -> pd.DataFrame:
    m = pd.read_csv(D_DATA / "export_hs330499_monthly.csv", parse_dates=["date"]).set_index("date")
    m = m.sort_index()
    m["exp_usd_yoy"] = yoy(m["exp_usd"], 12)
    m["exp_kg_yoy"] = yoy(m["exp_kg"], 12)
    m["unit_price"] = m["exp_usd"] / m["exp_kg"]
    m["unit_price_yoy"] = yoy(m["unit_price"], 12)
    m["exp_usd_3mma"] = m["exp_usd"].rolling(3).mean()
    m["log_exp_usd"] = np.log(m["exp_usd"])
    m.to_csv(D_DATA / "export_monthly_features.csv", encoding="utf-8-sig")
    log("FEAT", "월별 지표 {}건 ({:%Y-%m}~{:%Y-%m})".format(len(m), m.index.min(), m.index.max()))
    return m


# ---------------------------------------------------------------- 분기 집계
def build_quarterly_export(m: pd.DataFrame) -> pd.DataFrame:
    """월별 -> 분기 합계. 3개월이 모두 있는 분기만 '완전분기'로 인정."""
    g = m.groupby(pd.PeriodIndex(m.index, freq="Q"))
    qe = pd.DataFrame({
        "qexp_usd": g["exp_usd"].sum(min_count=3),
        "qexp_kg": g["exp_kg"].sum(min_count=3),
        "n_months": g["exp_usd"].count(),
    })
    qe.index = qe.index.to_timestamp(how="end").normalize()

    # 분기 내 부분집계 (당분기 1개월 / 앞 2개월)
    mm = m.copy()
    mm["qp"] = pd.PeriodIndex(mm.index, freq="Q")
    mm["mon_in_q"] = (mm.index.month - 1) % 3          # 0=분기 첫달, 1=둘째달, 2=셋째달
    first1 = mm[mm["mon_in_q"] == 0].groupby("qp")[["exp_usd", "exp_kg"]].sum(min_count=1)
    first2 = mm[mm["mon_in_q"] <= 1].groupby("qp")[["exp_usd", "exp_kg"]].sum(min_count=2)
    first1.index = first1.index.to_timestamp(how="end").normalize()
    first2.index = first2.index.to_timestamp(how="end").normalize()
    qe["qexp_usd_m1"] = first1["exp_usd"]
    qe["qexp_kg_m1"] = first1["exp_kg"]
    qe["qexp_usd_m12"] = first2["exp_usd"]
    qe["qexp_kg_m12"] = first2["exp_kg"]

    # 전년 동기 대비
    qe["qexp_usd_yoy"] = yoy(qe["qexp_usd"], 4)
    qe["qexp_kg_yoy"] = yoy(qe["qexp_kg"], 4)
    qe["qexp_usd_m1_yoy"] = yoy(qe["qexp_usd_m1"], 4)
    qe["qexp_usd_m12_yoy"] = yoy(qe["qexp_usd_m12"], 4)
    qe["qexp_kg_m12_yoy"] = yoy(qe["qexp_kg_m12"], 4)
    qe["qexp_unit_price"] = qe["qexp_usd"] / qe["qexp_kg"]
    qe["qexp_unit_price_yoy"] = yoy(qe["qexp_unit_price"], 4)
    qe["log_qexp_usd"] = np.log(qe["qexp_usd"])

    qe.to_csv(D_DATA / "export_quarterly_features.csv", encoding="utf-8-sig")
    comp = qe[qe["n_months"] == 3]
    log("FEAT", "분기 수출 {}개 분기 / 완전분기 {}개 ({:%Y-%m}~{:%Y-%m})".format(
        len(qe), len(comp), comp.index.min(), comp.index.max()))
    return qe


# ---------------------------------------------------------------- 기업 실적
def build_company_panel() -> dict:
    fs = pd.read_csv(D_DATA / "financials_quarterly.csv", parse_dates=["date"],
                     dtype={"ticker": str})
    out = {}
    for code in COMPANIES:
        d = fs[fs["ticker"] == code].set_index("date").sort_index()
        p = pd.DataFrame(index=d.index)
        p["revenue_bn"] = d["revenue_bn"]
        p["op_bn"] = d["op_bn"]
        p["rev_yoy"] = yoy(p["revenue_bn"], 4)
        p["opm"] = p["op_bn"] / p["revenue_bn"] * 100.0        # 영업이익률 %
        p["opm_chg_yoy"] = p["opm"] - p["opm"].shift(4)        # 영업이익률 전년동기차 %p
        # 참고용 (해석 불안정 -> 주 목표변수 아님)
        p["op_yoy"] = yoy(p["op_bn"], 4)
        p["op_yoy_valid"] = (p["op_bn"].shift(4) > 0) & (p["op_bn"] > 0)
        out[code] = p
        p.to_csv(D_DATA / "company_{}_features.csv".format(code), encoding="utf-8-sig")
        log("FEAT", "{} {} : {}분기, rev_yoy 유효 {}개, 영업이익<=0 {}개".format(
            code, COMPANIES[code]["name"], len(p), int(p["rev_yoy"].notna().sum()),
            int((p["op_bn"] <= 0).sum())))
    return out


# ---------------------------------------------------------------- 정보집합
# ---------------------------------------------------------------- 정보집합 정의
# earn_lag : 해당 시점에 '이미 공시된' 가장 최근 분기실적의 시차
#            (분기보고서 법정 제출기한 = 분기 종료 후 45일)
SCENARIOS = {
    "A_AHEAD": {
        "origin": "분기 Q 시작일 (m1 1일)",
        "desc": ("Q-1 실적은 아직 미공시(법정기한 Q-1 종료 후 45일 = Q의 45일째). "
                 "수출은 m1-2월까지 확정공표 -> 완전분기 Q-2까지 + Q-1 앞 2개월"),
        "earn_lag": 2,
        "export": {
            "exp_usd_lvl": ("log_qexp_usd", 2),
            "exp_usd_yoy": ("qexp_usd_yoy", 2),
            "exp_kg_yoy": ("qexp_kg_yoy", 2),
            "exp_usd_yoy_L3": ("qexp_usd_yoy", 3),
            "exp_usd_yoy_part": ("qexp_usd_m12_yoy", 1),
            "exp_kg_yoy_part": ("qexp_kg_m12_yoy", 1),
        },
    },
    "B_NOWCAST_PARTIAL": {
        "origin": "분기 Q의 50일째 (약 m2 20일)",
        "desc": ("Q-1 실적 공시 완료(Q 45일째) + 당분기 m1 수출 공표(m2 15일). "
                 "-> 실적 Q-1까지, 수출 완전분기 Q-1 + 당분기 1개월"),
        "earn_lag": 1,
        "export": {
            "exp_usd_lvl": ("log_qexp_usd", 1),
            "exp_usd_yoy": ("qexp_usd_yoy", 1),
            "exp_kg_yoy": ("qexp_kg_yoy", 1),
            "exp_usd_yoy_L2": ("qexp_usd_yoy", 2),
            "exp_usd_yoy_cur1m": ("qexp_usd_m1_yoy", 0),
        },
    },
    "C_NOWCAST_FULL": {
        "origin": "분기 Q 종료 +15일 (실적발표 전)",
        "desc": ("당분기 3개월 수출 전부 공표. 당분기 실적은 아직 미공시 "
                 "-> 실적 Q-1까지, 수출 당분기 Q 전체"),
        "earn_lag": 1,
        "export": {
            "exp_usd_lvl": ("log_qexp_usd", 0),
            "exp_usd_yoy": ("qexp_usd_yoy", 0),
            "exp_kg_yoy": ("qexp_kg_yoy", 0),
            "exp_usd_yoy_L1": ("qexp_usd_yoy", 1),
            "exp_unitprice_yoy": ("qexp_unit_price_yoy", 0),
        },
    },
}


def assemble_dataset(qe: pd.DataFrame, panel: dict) -> dict:
    """기업별 x 정보집합별 모델링용 데이터셋 (SCENARIOS 정의에 따름)."""
    datasets = {}
    for code, p in panel.items():
        base = p.copy()
        base["quarter_no"] = base.index.quarter
        for scen, spec in SCENARIOS.items():
            df = base.copy()
            # (a) 자기회귀 변수 : 해당 시점에 '공시된' 실적만 사용
            el = spec["earn_lag"]
            for tgt in ("rev_yoy", "opm", "revenue_bn", "op_bn"):
                df["{}_AR1".format(tgt)] = df[tgt].shift(el)        # 가장 최근 공시 분기
                df["{}_AR2".format(tgt)] = df[tgt].shift(el + 1)
                df["{}_SEAS".format(tgt)] = df[tgt].shift(4)        # 전년 동기 (항상 공시됨)
            # (b) 수출 변수
            for name, (col, lag) in spec["export"].items():
                df[name] = qe[col].reindex(df.index).shift(lag)
            df["_scenario"] = scen
            df["_ticker"] = code
            datasets[(code, scen)] = df
            df.to_csv(D_DATA / "dataset_{}_{}.csv".format(code, scen), encoding="utf-8-sig")

    # 정보집합 정의표 저장
    rows = []
    for scen, spec in SCENARIOS.items():
        rows.append({"정보집합": scen, "시점": spec["origin"], "설명": spec["desc"],
                     "변수구분": "실적(자기회귀)", "변수명": "*_AR1 / *_AR2 / *_SEAS",
                     "원천컬럼": "기업 실적", "분기시차_L": "{} / {} / 4".format(spec["earn_lag"], spec["earn_lag"] + 1)})
        for name, (col, lag) in spec["export"].items():
            rows.append({"정보집합": scen, "시점": spec["origin"], "설명": spec["desc"],
                         "변수구분": "수출지표", "변수명": name,
                         "원천컬럼": col, "분기시차_L": lag})
    pd.DataFrame(rows).to_csv(D_TAB / "step2_information_sets.csv",
                              index=False, encoding="utf-8-sig")
    log("FEAT", "데이터셋 {}개 생성 (기업 {} x 정보집합 {})".format(
        len(datasets), len(panel), len(SCENARIOS)))
    return datasets


def main():
    m = build_monthly()
    qe = build_quarterly_export(m)
    panel = build_company_panel()
    ds = assemble_dataset(qe, panel)

    # 요약 출력
    print("\n--- 분기 수출 최근 8개 ---")
    print(qe[["qexp_usd", "qexp_kg", "qexp_usd_yoy", "qexp_kg_yoy", "n_months"]].tail(8).round(2).to_string())
    for code, p in panel.items():
        print("\n--- {} {} 최근 8분기 ---".format(code, COMPANIES[code]["name"]))
        print(p[["revenue_bn", "op_bn", "rev_yoy", "opm"]].tail(8).round(2).to_string())
    return ds


if __name__ == "__main__":
    main()
