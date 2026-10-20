# -*- coding: utf-8 -*-
"""2g K8c 2(b)(c)·3: 매출 단절 점검과 처리 규칙 (method_2g_v1 변경 기록 2026-10-20 K8c).
쓰기: fd_rev_check_2g, fd_rev_fix_2g (2g 작업 표, 실행마다 전체 교체) 와 CSV (2g/K8c). 원본·2d 표는 바꾸지 않는다.

대상: 2g 통합 1,164 + 2d 73 (2d 는 목록·영향만 보고하고 처리 표에는 넣지 않는다).
(b) 이상
  - q4_ratio: 2019~2025 어느 해든 Q4 매출 < (Q1~Q3 평균) x 0.4 또는 > x 2.5 (네 분기 모두 있는 해)
  - margin: 2019Q1 이후 어느 분기든 |영업이익 / 매출| > 100% (단순 기준 이익률)
  - level_shift (사건 확인용, 보고만): 직전 분기 대비 매출 2배 초과·절반 미만
(c) 원인 추정
  - 사건: 그 기업에 2023-01 이후 사건(k8c_events)이 있고, 이상 분기가 사건 분기 ~ 그 뒤 4분기 안
  - 4분기 산출: q4_ratio 이고 사건과 무관. 원천별(DG, DART V3 연결, V3 별도, V2) 같은 해 네 분기로 Q4 비율을 다시 보면
      · 보정 매출의 그해 원천이 섞였고(source 가 분기마다 다름) 같은 원천만으로는 정상 → '4분기 산출(기준 혼합)', 그 원천 값으로 고침
      · DG 와 DART 가 다르게 나오고 한쪽만 정상 → '4분기 산출(원천 차이)', 보정 매출과 Q1~Q3 가 2% 안에서 같은 원천의 정상 Q4 로 고침
      · 고칠 수 없으면 flag_q4_calc = 1
  - 계절성: q4_ratio 가 같은 방향으로 2개 해 이상 나오고 원천끼리 같은 값 → 고치지 않음
  - margin 만 있고 위에 해당 없음: '실적 자체' (매출이 아주 작은 분기·일회성)
처리 (2g 만)
  - 사건 확인 기업: 단절 분기(break_q) = 사건 분기 이후 첫 이상 분기.
    새 기준 시작 = level_shift 이면 break_q, q4_ratio·margin 이면 break_q 다음 분기.
    DART 재작성(전기 비교) 값은 수집 표에 없고, DART API 로 받아도 1년 전까지만 있어 12분기를 채우지 못하므로 쓰지 않는다 (보고서에 사례 표).
    새 기준 시작 이후 아는 분기가 12 이상이면 mode 'post_only' (예측에 그 구간만 사용), 아니면 'compare_only' + flag_struct_break = 1.
  - 4분기 산출 이상: 고친 값은 fd_rev_fix_2g 로 스냅샷에 반영, 못 고친 기업은 flag_q4_calc = 1.
python -m DATA.fdriver.eval2g.k8c_scan [--dry]
"""
import argparse
import json
from datetime import datetime

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..eval2d import k2_targets as K2
from ..eval2d import k3_oprule as K3
from ..nowcast import features as ft
from . import common as C

OUT = output_path("2g", "K8c")
T_CHECK = "fd_rev_check_2g"
T_FIX = "fd_rev_fix_2g"
LO, HI, MARGIN = 0.4, 2.5, 1.0
YEARS = range(2019, 2026)
START = pd.Timestamp("2019-03-31")
DDL_CHECK = f"""
CREATE TABLE IF NOT EXISTS `{T_CHECK}` (
  entity_id VARCHAR(20) NOT NULL,
  name VARCHAR(100) NULL,
  group_2g VARCHAR(10) NULL,
  event_kind VARCHAR(40) NULL,
  event_dt DATE NULL,
  break_q CHAR(6) NULL,
  start_q CHAR(6) NULL,
  post_len INT NULL,
  mode VARCHAR(20) NULL,
  flag_struct_break TINYINT NULL,
  flag_q4_calc TINYINT NULL,
  q4_calc_years VARCHAR(60) NULL,
  causes VARCHAR(200) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id)
) DEFAULT CHARSET=utf8mb4
"""
DDL_FIX = f"""
CREATE TABLE IF NOT EXISTS `{T_FIX}` (
  entity_id VARCHAR(20) NOT NULL,
  period_end DATE NOT NULL,
  revenue_orig DOUBLE NULL,
  revenue_fix DOUBLE NOT NULL,
  fix_source VARCHAR(10) NULL,
  reason VARCHAR(80) NULL,
  run_id VARCHAR(20) NOT NULL,
  first_collected_at DATETIME NULL,
  last_collected_at DATETIME NULL,
  PRIMARY KEY (entity_id, period_end)
) DEFAULT CHARSET=utf8mb4
"""


def ql(d) -> str:
    d = pd.Timestamp(d)
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def qend(d) -> pd.Timestamp:
    return (pd.Timestamp(d) + pd.offsets.QuarterEnd(0)).normalize()


def scan_firm(R: pd.Series, OP: pd.Series) -> list:
    out = []
    for y in YEARS:
        qs = [pd.Timestamp(f"{y}-{m}") + pd.offsets.MonthEnd(0) for m in ("03", "06", "09", "12")]
        v = [R.get(q) for q in qs]
        if all(x is not None and pd.notna(x) for x in v) and np.mean(v[:3]) > 0:
            r = v[3] / np.mean(v[:3])
            if r < LO or r > HI:
                out.append({"kind": "q4_ratio", "q": qs[3], "value": round(float(r), 3), "dir": "low" if r < LO else "high"})
    for q, rv in R[R.index >= START].items():
        op = OP.get(q)
        if op is not None and pd.notna(op) and rv and rv > 0 and abs(op / rv) > MARGIN:
            out.append({"kind": "margin", "q": q, "value": round(float(op / rv), 3), "dir": ""})
    prev = R.shift(1)
    for q, rv in R[R.index >= START].items():
        p = prev.get(q)
        if p and p > 0 and rv and rv > 0 and (rv / p > 2 or rv / p < 0.5):
            out.append({"kind": "level_shift", "q": q, "value": round(float(rv / p), 3), "dir": ""})
    return out


def q4_sources(src: pd.DataFrame, e: str, y: int) -> dict:
    """원천별 그해 Q4/평균(Q1~Q3) 와 Q1~Q3 값."""
    out = {}
    g = src[src["entity_id"] == e]
    qs = [pd.Timestamp(f"{y}-{m}") + pd.offsets.MonthEnd(0) for m in ("03", "06", "09", "12")]
    for s, gs in g.groupby("src"):
        v = gs.set_index("period_end")["value"]
        vals = [v.get(q) for q in qs]
        if all(x is not None and pd.notna(x) for x in vals) and np.mean(vals[:3]) > 0:
            out[s] = {"ratio": vals[3] / np.mean(vals[:3]), "q123": vals[:3], "q4": vals[3]}
    return out


def main(write: bool = True):
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tg = C.read_targets()[["entity_id", "name", "group_2g"]]
    m2d = db.read_sql("SELECT entity_id, name FROM fd_driver_map_2d WHERE status IN ('driver','ts_only')").drop_duplicates(
        "entity_id").assign(group_2g="2d 확대")
    ids2g, ids2d = tg["entity_id"].tolist(), m2d["entity_id"].tolist()
    adj = pd.concat([C.read_adj(ids2g).assign(src_tbl="2g"), K2.read_adj(ids2d).assign(src_tbl="2d")], ignore_index=True)
    it = pd.concat([C.items_2g(ids2g, adj[adj["src_tbl"] == "2g"])[0], K3.items(ids2d, adj[adj["src_tbl"] == "2d"],
                                                                                 quarters=None)], ignore_index=True)
    opv = it[it["item"] == "op"].set_index(["entity_id", "period_end"])["value"]
    ev = pd.read_csv(OUT / "k8c_events.csv", dtype={"entity_id": str}, parse_dates=["event_dt"])
    with C._k2_end(pd.Timestamp("2026-06-30")):
        src = K2.read_sources(ids2g + ids2d)
    firms = pd.concat([tg, m2d], ignore_index=True)
    anomalies, checks, fixes = [], [], []
    for f in firms.itertuples():
        e = f.entity_id
        a = adj[adj["entity_id"] == e].set_index("period_end").sort_index()
        R = a["revenue_adj"]
        OP = opv.xs(e, level=0) if e in opv.index.get_level_values(0) else pd.Series(dtype=float)
        an = scan_firm(R, OP)
        evs = ev[ev["entity_id"] == e].sort_values("event_dt")
        q4y = {}
        for x in an:
            x.update({"entity_id": e, "name": f.name, "group_2g": f.group_2g, "quarter": ql(x["q"])})
            linked = [r for r in evs.itertuples() if qend(r.event_dt) <= x["q"] <= ft.quarter_shift(qend(r.event_dt), -4)]
            if linked:
                x["cause"], x["event"] = "사건", f"{linked[0].kind} {linked[0].event_dt.date()}"
            elif x["kind"] == "q4_ratio":
                q4y.setdefault(x["dir"], []).append(x)
            elif x["kind"] == "margin":
                x["cause"] = "실적 자체 (매출 작은 분기·일회성)"
            else:
                x["cause"] = "참고 (사건 확인용)"
        # q4_ratio 원인: 계절성 / 4분기 산출
        calc_years = []
        for d, xs in q4y.items():
            for x in xs:
                y = x["q"].year
                srcs = q4_sources(src, e, y)
                ok = {s: v for s, v in srcs.items() if LO <= v["ratio"] <= HI}
                bad = {s: v for s, v in srcs.items() if not (LO <= v["ratio"] <= HI)}
                mixed = a.loc[[q for q in a.index if q.year == y], "source"].dropna().nunique() > 1
                x["src_ratios"] = ";".join(f"{s}:{v['ratio']:.2f}" for s, v in srcs.items())
                q123 = [R.get(pd.Timestamp(f"{y}-{m}") + pd.offsets.MonthEnd(0)) for m in ("03", "06", "09")]
                same = [s for s, v in ok.items() if all(abs(v["q123"][i] / q123[i] - 1) < 0.02 for i in range(3)
                                                         if q123[i])]
                if same and (mixed or bad):
                    s = same[0]
                    x["cause"] = "4분기 산출(" + ("기준 혼합" if mixed else "원천 차이") + ")"
                    x["fix"] = f"{s} Q4 {srcs[s]['q4'] / 1e8:,.0f}억"
                    if f.group_2g != "2d 확대":
                        fixes.append({"entity_id": e, "period_end": x["q"].date(), "revenue_orig": float(R.get(x["q"])),
                                      "revenue_fix": float(srcs[s]["q4"]), "fix_source": s,
                                      "reason": x["cause"], "run_id": run_id})
                elif len(xs) >= 2 and not ok:
                    x["cause"] = "계절성 (같은 방향 2개 해 이상, 원천 같음)"
                elif not ok and len(srcs) >= 1:
                    x["cause"] = "4분기 산출 의심 (원천 모두 같은 값, 사건 없음)"
                    calc_years.append(str(y))
                else:
                    x["cause"] = "4분기 산출 (고칠 원천 없음)"
                    calc_years.append(str(y))
        anomalies += an
        # 사건 확인·단절
        chk = {"entity_id": e, "name": f.name, "group_2g": f.group_2g, "flag_struct_break": 0, "flag_q4_calc": 0,
               "q4_calc_years": ",".join(sorted(set(calc_years))) or None, "run_id": run_id}
        if calc_years:
            chk["flag_q4_calc"] = 1
        ev_an = [x for x in an if x.get("cause") == "사건" and x["kind"] in ("q4_ratio", "margin", "level_shift")]
        if ev_an:
            first = min(ev_an, key=lambda x: x["q"])
            start = first["q"] if first["kind"] == "level_shift" else ft.quarter_shift(first["q"], -1)
            known = R[(R.index >= start) & R.notna()]
            chk.update({"event_kind": first["event"].split(" ")[0], "event_dt": first["event"].split(" ")[1],
                        "break_q": ql(first["q"]), "start_q": ql(start), "post_len": int(len(known))})
            chk["mode"] = "post_only" if len(known) >= 12 else "compare_only"
            chk["flag_struct_break"] = int(len(known) < 12)
        causes = sorted({x.get("cause", "") for x in an if x["kind"] != "level_shift"})
        chk["causes"] = "; ".join(c for c in causes if c)[:200] or None
        if chk["flag_struct_break"] or chk["flag_q4_calc"] or chk.get("mode") or causes:
            checks.append(chk)
    A = pd.DataFrame(anomalies)
    A["q"] = pd.to_datetime(A["q"]).dt.date if len(A) else A.get("q")
    CH = pd.DataFrame(checks)
    FX = pd.DataFrame(fixes, columns=["entity_id", "period_end", "revenue_orig", "revenue_fix", "fix_source", "reason",
                                      "run_id"])
    n = {}
    if write:
        db.execute_fd_ddl(DDL_CHECK)
        db.execute_fd_ddl(DDL_FIX)
        for tab, df, key in ((T_CHECK, CH[CH["group_2g"] != "2d 확대"], ["entity_id"]),
                             (T_FIX, FX, ["entity_id", "period_end"])):
            old = db.read_sql(f"SELECT DISTINCT entity_id FROM `{tab}`")["entity_id"].tolist()
            if old:
                db.delete_fd(tab, {"entity_id": old})
            cols = [c for c in df.columns if c in db.table_columns(tab)]
            n[tab] = db.write_fd(df[cols], tab, key) if len(df) else 0
    A.to_csv(OUT / "k8c_anomalies.csv", index=False, encoding="utf-8-sig")
    CH.to_csv(OUT / "k8c_firm_check.csv", index=False, encoding="utf-8-sig")
    FX.to_csv(OUT / "k8c_rev_fix.csv", index=False, encoding="utf-8-sig")
    main_an = A[A["kind"] != "level_shift"] if len(A) else A
    s = {"run_id": run_id, "rows_written": n, "firms_scanned": len(firms),
         "events": {"rows": len(ev), "firms": int(ev["entity_id"].nunique()), "by_kind": ev["kind"].value_counts().to_dict()},
         "anomalies": {"rows": len(main_an), "firms": int(main_an["entity_id"].nunique()) if len(main_an) else 0,
                       "by_kind": main_an["kind"].value_counts().to_dict() if len(main_an) else {},
                       "by_cause": main_an["cause"].value_counts().to_dict() if len(main_an) else {}},
         "struct_break": {"firms_event_confirmed": int(CH["mode"].notna().sum()) if len(CH) and "mode" in CH else 0,
                          "post_only": int((CH.get("mode") == "post_only").sum()) if len(CH) else 0,
                          "compare_only_flag": int(CH["flag_struct_break"].sum()) if len(CH) else 0,
                          "by_group": CH[CH.get("mode").notna()].groupby("group_2g").size().to_dict() if len(CH) else {}},
         "q4_calc_flag": int(CH["flag_q4_calc"].sum()) if len(CH) else 0,
         "q4_fixed_quarters": len(FX), "q4_fixed_firms": int(FX["entity_id"].nunique()),
         "2d_affected": CH[(CH["group_2g"] == "2d 확대") & ((CH["flag_struct_break"] == 1) | (CH["flag_q4_calc"] == 1) |
                                                          CH.get("mode").notna())][["entity_id", "name", "causes"]]
         .to_dict(orient="records") if len(CH) else []}
    (OUT / "k8c_scan_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    main(write=not ap.parse_args().dry)
