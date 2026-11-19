# 81개 종목 분기 매출 예측 구조 확인
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
t = pd.read_excel(r"..\..\..\..\reports\fcff_analysis\FCFF_mcap1B_upside100_200_20261117_v1.xlsx")["ticker"].tolist()
ph = ",".join(["%s"]*len(t))
df = fa.q(f"SELECT ticker, quarter, sales_forecast FROM us_fcff_dcf_valuation WHERE `date`=%s AND ticker IN ({ph}) ORDER BY ticker, quarter", ["2026-11-17"] + t)
print(df["quarter"].value_counts().sort_index())
df.to_pickle("sales_81.pkl")
print(df[df.ticker.isin(["MU","KR","ADBE"])].to_string())
