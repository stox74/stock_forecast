# 첫 예측분기(이번 분기) 포함 5개 분기 동안 QoQ 매출이 4번 연속 증가하는 종목
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
df = pd.read_pickle("sales_81.pkl")
rk = pd.read_excel(r"..\..\..\..\reports\fcff_analysis\FCFF_mcap1B_upside100_200_20261117_v1.xlsx")
rows = []
for tk, g in df.groupby("ticker"):
    g = g.sort_values("quarter")
    s = g["sales_forecast"].astype(float).values[:5]
    qs = g["quarter"].values[:5]
    if len(s) < 5:
        rows.append(dict(ticker=tk, n=len(g), first_q=qs[0] if len(qs) else None, ok=False)); continue
    qoq = s[1:] / s[:-1] - 1
    rows.append(dict(ticker=tk, n=len(g), first_q=qs[0], last_q=qs[4],
                     q1=qoq[0], q2=qoq[1], q3=qoq[2], q4=qoq[3],
                     min_qoq=qoq.min(), total_4q=s[4]/s[0]-1, sales_q0_bn=s[0]/1e9,
                     ok=bool((qoq > 0).all())))
r = pd.DataFrame(rows).merge(rk[["ticker", "upside_pct", "market_cap", "flag_g_cap", "flag_thin_spread"]], on="ticker")
r["market_cap_bn"] = r["market_cap"] / 1e9
print("이상 분기 구조:", r[(r.n != 8)][["ticker", "n", "first_q"]].to_string(index=False))
ok = r[r.ok].sort_values("total_4q", ascending=False)
pd.set_option("display.width", 250)
print("\n연속 증가:", len(ok))
print(ok[["ticker","first_q","last_q","q1","q2","q3","q4","min_qoq","total_4q","sales_q0_bn","upside_pct","market_cap_bn","flag_g_cap"]]
      .to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
# 3/4 증가 (아깝게 탈락)
near = r[(~r.ok) & r.q1.notna()]
near = near[((near[["q1","q2","q3","q4"]] > 0).sum(axis=1) == 3)]
print("\n4번 중 3번 증가:", len(near))
print(near[["ticker","first_q","q1","q2","q3","q4","total_4q"]].to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
r.to_pickle("qoq_result.pkl")
