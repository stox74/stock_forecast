# 연속 QoQ 증가 결과 엑셀 저장
import sys; sys.path.insert(0, "..")
import pandas as pd
import fcff_analysis_v1 as fa
r = pd.read_pickle("qoq_result.pkl")
cols = ["ticker","first_q","last_q","q1","q2","q3","q4","min_qoq","total_4q","sales_q0_bn","upside_pct","market_cap_bn","flag_g_cap","flag_thin_spread"]
ok = r[r.ok].sort_values("total_4q", ascending=False)[cols]
near = r[(~r.ok) & r.q1.notna()]
near = near[(near[["q1","q2","q3","q4"]] > 0).sum(axis=1) == 3].sort_values("total_4q", ascending=False)[cols]
fa.export_excel({"QoQ_4연속증가": ok, "QoQ_3of4증가": near, "전체": r[cols + ["ok"]]}, "FCFF_QoQ_steps_20261117")
