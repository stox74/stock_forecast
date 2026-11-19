# ANET 최신 평가와 회차별 추이
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
pd.set_option("display.width", 250)
r = fa.load_run("2026-11-17")
print(r[r.ticker == "ANET"].T.to_string())
d = fa.q("SELECT quarter, sales_forecast, opm_forecast, fcff, roic FROM us_fcff_dcf_valuation WHERE ticker=%s AND `date`=%s ORDER BY quarter", ["ANET", "2026-11-17"])
print(d.to_string(index=False))
print(fa.ticker_history("ANET").to_string(index=False))
