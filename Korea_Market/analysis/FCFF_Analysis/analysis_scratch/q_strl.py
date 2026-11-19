# STRL 회차별 업사이드와 2026-11-17 분기 매출 예측
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
pd.set_option("display.width", 250)
r = fa.load_run("2026-11-17")
s = r[r.ticker == "STRL"]
print(s.T.to_string())
d = fa.q("SELECT quarter, sales_forecast, opm_forecast, ebit, fcff FROM us_fcff_dcf_valuation WHERE ticker=%s AND `date`=%s ORDER BY quarter", ["STRL", "2026-11-17"])
d["qoq"] = d["sales_forecast"].astype(float).pct_change()
print(d.to_string(index=False))
h = fa.ticker_history("STRL")
print(h.to_string(index=False))
