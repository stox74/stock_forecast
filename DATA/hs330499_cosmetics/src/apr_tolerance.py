# -*- coding: utf-8 -*-
"""
에이피알 — '오차 ±7% 이내' 목표가 실제로 달성 가능한가

목표: 다음 분기 매출액을 절대오차 7% 이내로 예측
방법: 단순 후보 4개를 전 기간 확장창으로 돌려 '±7% 적중률'을 센다.
정보 시점: 직전 분기 실적이 공시된 뒤(분기 종료 후 45일) = 1분기 앞 예측
"""
import numpy as np, pandas as pd
from common import D_DATA, D_TAB, log
pd.set_option('display.width',250)

p = pd.read_csv(D_DATA/"company_278470_features.csv", parse_dates=["date"]).set_index("date")
r = p["revenue_bn"].dropna()

f = pd.DataFrame(index=r.index)
f["actual"] = r
# 후보 1: 전년동기 x 직전분기 YoY  (지금까지 최선)
yoy1 = r.shift(1)/r.shift(5) - 1
f["naive_yoy"] = r.shift(4) * (1 + yoy1)
# 후보 2: 전년동기 x 직전 2분기 평균 YoY (완충)
yoy2 = ((r.shift(1)/r.shift(5) - 1) + (r.shift(2)/r.shift(6) - 1)) / 2
f["naive_yoy_2q"] = r.shift(4) * (1 + yoy2)
# 후보 3: 직전분기 x 계절요인(과거 3년 평균 QoQ)
qoq = r / r.shift(1)
seas = qoq.groupby(qoq.index.quarter).apply(lambda s: s.shift(1).rolling(3, min_periods=2).mean())
seas = pd.Series(seas.values, index=qoq.index) if isinstance(seas.index, pd.MultiIndex) else seas
f["seasonal_qoq"] = r.shift(1) * seas
# 후보 4: 전년동기 그대로
f["naive_seas"] = r.shift(4)

CAND = ["naive_yoy","naive_yoy_2q","seasonal_qoq","naive_seas"]
for c in CAND:
    f["err_"+c] = (f[c] - f["actual"]) / f["actual"] * 100

v = f.dropna(subset=["actual","err_naive_yoy"])
print("=== 에이피알 1분기 앞 매출 예측 : 절대오차 ±7% 적중률 ===")
print("   평가구간 %s ~ %s (%d분기)\n" % (v.index.min().date(), v.index.max().date(), len(v)))
rows=[]
for c in CAND:
    e = f["err_"+c].dropna()
    if len(e)==0: continue
    rows.append({"모형":c,"n":len(e),"평균절대오차%":e.abs().mean(),
                 "중위절대오차%":e.abs().median(),
                 "±7%_적중률":(e.abs()<=7).mean()*100,
                 "±10%_적중률":(e.abs()<=10).mean()*100,
                 "최대오차%":e.abs().max()})
print(pd.DataFrame(rows).round(1).to_string(index=False))

best="naive_yoy"
e=f["err_"+best].dropna()
print("\n=== %s : 최근 12분기 상세 ===" % best)
o=pd.DataFrame({"실제":f["actual"],"예측":f[best],"오차%":f["err_"+best]}).dropna().tail(12)
o["±7%"]=np.where(o["오차%"].abs()<=7,"O","X")
print(o.round(1).to_string())

# 국면별
chg = (r/r.shift(4)-1)*100
acc = chg.diff()
reg = pd.Series(np.where(acc>5,"가속",np.where(acc<-5,"감속","완만")), index=r.index)
print("\n=== 국면별 ±7% 적중률 ===")
rows=[]
for g in ("가속","감속","완만"):
    idx = reg[reg==g].index
    ee = f["err_"+best].reindex(idx).dropna()
    if len(ee)<2: continue
    rows.append({"국면":g,"n":len(ee),"평균절대오차%":ee.abs().mean(),
                 "±7%_적중률":(ee.abs()<=7).mean()*100,"평균편향%":ee.mean()})
print(pd.DataFrame(rows).round(1).to_string(index=False))

# 최근 3년만
rec = f[f.index>="2023-01-01"]
e2 = rec["err_"+best].dropna()
print("\n=== 최근 3년(2023~)만 : %s ===" % best)
print("   n=%d  평균절대오차 %.1f%%  ±7%% 적중률 %.0f%%  최대 %.1f%%" % (
    len(e2), e2.abs().mean(), (e2.abs()<=7).mean()*100, e2.abs().max()))

f.to_csv(D_TAB/"step26_apr_tolerance.csv", encoding="utf-8-sig")
log("TOL","저장")
