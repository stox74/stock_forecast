# -*- coding: utf-8 -*-
"""합성 데이터로 전체 파이프라인 검증 (DB 없이 로컬 실행)."""
import os
import sqlite3
import numpy as np
import pandas as pd

np.random.seed(42)

# ---------- 1) 합성 revenue.db 생성 (대만 3개사, 2019-01 ~ 2026-06 + 예측 6개월) ----------
DB = "/tmp/revenue_test.db"
if os.path.exists(DB):
    os.remove(DB)
conn = sqlite3.connect(DB)
conn.executescript("""
CREATE TABLE revenue (company_id TEXT, company_name TEXT, year INT, month INT,
 revenue INT, mom_pct REAL, yoy_pct REAL, source TEXT, collected_at TEXT,
 PRIMARY KEY (company_id, year, month));
CREATE TABLE forecast (company_id TEXT, model TEXT, basis_year INT, basis_month INT,
 target_year INT, target_month INT, predicted REAL, lower_95 REAL, upper_95 REAL,
 created_at TEXT,
 PRIMARY KEY (company_id, model, basis_year, basis_month, target_year, target_month));
""")

months = pd.date_range("2019-01-01", "2026-06-01", freq="MS")
t = np.arange(len(months))
cycle = np.sin(2 * np.pi * t / 36)  # 3년 사이클 (반도체 사이클 흉내)

base = {"2330": 100_000_000, "2454": 40_000_000, "2317": 500_000_000}
growth = {"2330": 0.012, "2454": 0.010, "2317": 0.004}
series = {}
for cid in base:
    lvl = base[cid] * np.exp(growth[cid] * t) * (1 + 0.25 * cycle) \
          * (1 + 0.05 * np.random.randn(len(t)))
    series[cid] = lvl
    for d, v in zip(months, lvl):
        conn.execute("INSERT INTO revenue VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, f"TW-{cid}", d.year, d.month, int(v),
                      None, None, "mops", ""))

# 예측 6개월 (2026-07 ~ 2026-12), basis = 2026-06
fut = pd.date_range("2026-07-01", periods=6, freq="MS")
for cid in base:
    last = series[cid][-1]
    for i, d in enumerate(fut):
        pred = last * (1 + growth[cid]) ** (i + 1)
        conn.execute("INSERT INTO forecast VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (cid, "ensemble", 2026, 6, d.year, d.month,
                      pred, pred * 0.9, pred * 1.1, ""))
conn.commit()

# ---------- 2) tw_data 검증 ----------
os.environ["TW_REVENUE_DB"] = DB
import tw_data, correlation, predictor

tconn = tw_data.get_tw_conn()
long_df = tw_data.load_tw_monthly_long(tconn)
wide = tw_data.monthly_wide(long_df)
mom = tw_data.monthly_mom(wide)
yoy_m = tw_data.monthly_yoy(wide)
print("① 월별 wide:", wide.shape, "| MoM:", mom.shape, "| YoY:", yoy_m.shape)
print(wide.tail(2), "\n")

q_rev = tw_data.quarterly_revenue(wide)
q_yoy = tw_data.quarterly_yoy(q_rev)
print("④ 분기 매출:", q_rev.shape, "분기 YoY 마지막 2행:")
print(q_yoy.tail(2), "\n")

q_rev_ext, q_yoy_ext, is_fc = tw_data.tw_quarterly_extended(tconn)
print("예측 연장 분기:", q_rev_ext.index.min(), "→", q_rev_ext.index.max(),
      "| 예측 포함 분기 수:", int(is_fc.sum()))
assert is_fc.loc[pd.Period("2026Q3")] and is_fc.loc[pd.Period("2026Q4")]

# ---------- 3) 합성 KR/US 분기 데이터 (일부는 TSMC와 강한 상관, 일부는 노이즈) ----------
quarters = q_rev.index
tsmc_q_yoy = q_yoy["2330"]

def synth_target(corr_source, noise, seed):
    rng = np.random.RandomState(seed)
    y = corr_source * 0.8 + noise * rng.randn(len(corr_source))
    lvl = 1_000_000 * np.cumprod(1 + np.nan_to_num(y.values, nan=0) / 400)
    return pd.Series(lvl, index=quarters)

kr_q = pd.DataFrame({
    "A005930": synth_target(tsmc_q_yoy, 3, 1),   # 강한 상관
    "A000660": synth_target(tsmc_q_yoy, 5, 2),   # 상관
    "A999999": pd.Series(1e6 * (1 + 0.02 * np.random.randn(len(quarters))).cumprod(),
                         index=quarters),        # 무관
})
us_q = pd.DataFrame({
    "NVDA": synth_target(tsmc_q_yoy, 4, 3),
    "KO":   pd.Series(2e6 * (1 + 0.01 * np.random.randn(len(quarters))).cumprod(),
                      index=quarters),
})

kr_yoy = correlation.yoy_from_quarterly(kr_q)
us_yoy = correlation.yoy_from_quarterly(us_q)

# ---------- 4) 상관계수 상위 n 추출 ----------
top = correlation.top_correlated("2330", q_yoy, kr_yoy, us_yoy,
                                 kr_names={"A005930": "삼성전자", "A000660": "SK하이닉스"},
                                 n=5, max_lag=1)
print("\n⑤ TSMC(2330) 상관 상위:")
print(top, "\n")
assert top.iloc[0]["ticker"] in ("A005930", "NVDA")

# ---------- 5) 예측 (삼성전자: 2026Q2까지 실적 보유 가정 → Q3/Q4 예측) ----------
target = kr_q["A005930"].loc[:pd.Period("2026Q2")]
pred = predictor.predict_revenue(target, q_yoy_ext, ["2330", "2454"],
                                 tw_is_forecast=is_fc, horizon=2, model="ridge")
print("예측 결과:")
print(pred, "\n")
assert len(pred) == 2 and pred["pred_revenue"].notna().all()

bt = predictor.backtest(target, q_yoy_ext, ["2330", "2454"],
                        model="ridge", n_test=4)
print(bt)

# OLS numpy 폴백 경로도 확인
import predictor as P
coef = P._ols_numpy(np.random.randn(20, 2), np.random.randn(20))
assert len(coef) == 3

print("\n✅ 전체 파이프라인 스모크 테스트 통과")
