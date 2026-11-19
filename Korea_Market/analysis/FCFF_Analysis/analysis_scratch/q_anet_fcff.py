# ANET FCFF 구성요소 (2026-11-17)
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
pd.set_option("display.width", 250)
d = fa.q("SELECT quarter, sales_forecast, ebit, tax_rate, nopat, da, capex, nwc, delta_nwc, fcff FROM us_fcff_dcf_valuation WHERE ticker=%s AND `date`=%s ORDER BY quarter", ["ANET", "2026-11-17"])
for c in ["sales_forecast","ebit","nopat","da","capex","nwc","delta_nwc","fcff"]:
    d[c] = d[c].astype(float) / 1e6
print(d.to_string(index=False, float_format=lambda x: f"{x:,.1f}"))
