# 2026-11-17 회차: 시가총액 10억 달러 이상 & 업사이드 100%~200% 종목
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa

pd.set_option("display.width", 250)
DATE = "2026-11-17"
df = fa.ranking(DATE, top_n=None, clean=True,
                extra_filter="market_cap >= 1e9 and upside_pct >= 100 and upside_pct <= 200")
cols = ["rank", "ticker", "current_price", "target_price", "upside_pct", "wacc", "g_terminal",
        "tv_spread", "market_cap", "flag_g_cap", "flag_wacc_floor", "flag_thin_spread"]
out = df[cols].copy()
out["rank"] = range(1, len(out) + 1)
out["market_cap_bn"] = out["market_cap"] / 1e9
print("건수:", len(out))
print(out.drop(columns="market_cap").to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

# 참고: 전체 분포
allr = fa.load_run(DATE)
print("\n전체 종목", len(allr), "/ 시총>=1B", int((allr.market_cap >= 1e9).sum()),
      "/ 시총 중앙값(B)", round(allr.market_cap.median() / 1e9, 2))
p = fa.export_excel({"mcap1B_up100_200": out.drop(columns="market_cap_bn")}, "FCFF_mcap1B_upside100_200_20261117")
print("엑셀:", p)
