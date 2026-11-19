# 81개 종목의 섹터 (us_relative_valuation 최신값)
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
t = pd.read_excel(r"..\..\..\..\reports\fcff_analysis\FCFF_mcap1B_upside100_200_20261117_v1.xlsx")["ticker"].tolist()
cols = fa.q("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='us_relative_valuation'")
print(cols["column_name"].tolist())
ph = ",".join(["%s"]*len(t))
df = fa.q(f"SELECT ticker, sector FROM us_relative_valuation WHERE ticker IN ({ph}) GROUP BY ticker, sector", t)
print(len(df)); print(df.to_string())
