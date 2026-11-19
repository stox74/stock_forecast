# 최신 전체 배치 전종목: 첫 예측분기 포함 5개 분기 QoQ 매출 4연속 증가 선별
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
pd.set_option("display.width", 250)

dts = fa.list_dates(verbose=False)
DATE = dts[dts.full_batch]["date"].astype(str).max()
print("평가일:", DATE)

df = fa.q("SELECT ticker, quarter, sales_forecast FROM us_fcff_dcf_valuation WHERE `date`=%s ORDER BY ticker, quarter", [DATE])
run = fa.load_run(DATE)
run["market_cap_bn"] = run["market_cap"] / 1e9

rows = []
for tk, g in df.groupby("ticker"):
    g = g.sort_values("quarter")
    s = g["sales_forecast"].astype(float).values[:5]
    qs = g["quarter"].values[:5]
    rec = dict(ticker=tk, n=len(g), first_q=qs[0])
    if len(s) == 5 and (s > 0).all():
        qoq = s[1:] / s[:-1] - 1
        rec.update(last_q=qs[4], q1=qoq[0], q2=qoq[1], q3=qoq[2], q4=qoq[3],
                   min_qoq=qoq.min(), total_4q=s[4] / s[0] - 1, sales_q0_bn=s[0] / 1e9,
                   n_up=int((qoq > 0).sum()))
    rows.append(rec)
r = pd.DataFrame(rows).merge(
    run[["ticker", "current_price", "target_price", "upside_pct", "market_cap_bn", "wacc", "g_terminal",
         "clean", "flag_tp_neg", "flag_extreme", "flag_g_cap", "flag_wacc_floor", "flag_thin_spread"]],
    on="ticker", how="left")
# 첫 예측분기가 2026Q1 이전이면 원천 데이터가 낡은 것으로 표시
r["stale"] = r["first_q"] < "2026Q1"
print("전체", len(r), "/ 계산가능", int(r.q1.notna().sum()), "/ stale", int(r.stale.sum()))

ok = r[(r.n_up == 4) & ~r.stale].sort_values("total_4q", ascending=False)
step = ok[ok.min_qoq >= 0.01]
print("4연속 증가:", len(ok), "/ 매분기 1% 이상:", len(step), "/ 그중 clean:", int(step.clean.sum()))
cols = ["ticker", "first_q", "q1", "q2", "q3", "q4", "min_qoq", "total_4q", "sales_q0_bn",
        "upside_pct", "market_cap_bn", "clean", "flag_g_cap", "flag_thin_spread"]
fmt = lambda x: f"{x:,.3f}"
print("\n[매분기 1% 이상 증가]")
print(step[cols].to_string(index=False, float_format=fmt))
print("\n[4연속 증가이나 최소 QoQ 1% 미만]")
print(ok[ok.min_qoq < 0.01][["ticker", "min_qoq", "total_4q", "upside_pct", "market_cap_bn"]].to_string(index=False, float_format=fmt))
r.to_pickle("qoq_all.pkl")
p = fa.export_excel({"계단식_1pct이상": step[cols + ["wacc", "g_terminal", "current_price", "target_price"]],
                     "4연속증가_전체": ok[cols + ["wacc", "g_terminal", "current_price", "target_price"]],
                     "전종목": r}, f"FCFF_QoQ_steps_all_{DATE.replace('-', '')}")
