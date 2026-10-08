# -*- coding: utf-8 -*-
"""2c 작업 2 (g): 삼성전자 2026Q3 실전 기록 마무리, 컨센서스 기록, 환율 진단 (모델 변경 아님).

1. 이벤트표 갱신: events.build_and_load(["005930"]) 로 2026-10-08 잠정실적(매출 195.0조원)을 적재 (fd_earnings_event).
   이후 live/2026Q3/c1_live_check.py 와 같은 live_check.main 으로 결과표 저장.
2. 컨센서스 두 건을 fd_consensus 에 기록. 테이블이 2a 에서 이미 만들어져 있어 그 컬럼에 맞춘다:
   observed_date -> asof_date, value_krw -> mean, item 'revenue'. note·recorded_at 컬럼은 없어 리포트에 적는다
   (recorded_at 은 first_collected_at 에 넣음).
3. 환율 진단: 2a 시범 기업 중 수출 드라이버 기업(삼성전자 854232, 현대건설기계 842952)의 pre5 주 모델(M4) 분기별 오차
   (g_hat - g_actual, 첫 공시 값)와 원/달러 분기 평균 전년 동기 대비 로그 변화(FRED DEXKOUS 일별 평균)를 나란히 둔다.
   원화 변동이 큰 분기 = 기업별 |변화| 상위 20% 가 아니라 변화율 자체의 상위 20%(원화 약세)·하위 20%(원화 강세).
   기술통계만 (평균 오차, 평균 절대오차, 분기 수). 직전 분기 대비 변화도 함께 적는다.
실행: python -m DATA.fdriver.stage2c.g_live_fx
"""
import sys

import numpy as np
import pandas as pd

from .. import db, events
from ..adapters import consensus as cs
from ..adapters import read_series
from ..config import output_path
from ..evaluate import forecast_eval as fe
from ..evaluate import live_check

OUT = output_path("2c", "step2", "g_live_fx")
LIVE_DIR = output_path("live", "2026Q3")
CONSENSUS = [
    {"entity_id": "005930", "fiscal_quarter": "2026Q3", "item": "revenue", "asof_date": "2026-10-05",
     "mean": 200.77e12, "source": "호영님 확인"},
    {"entity_id": "005930", "fiscal_quarter": "2026Q3", "item": "revenue", "asof_date": "2026-09-27",
     "mean": 204.5704e12, "source": "에프앤가이드 집계(언론 인용)"},
]
FX_FIRMS = {"005930": "M4:combo", "267270": "M4:842952"}


def step1_events_and_check():
    res = events.build_and_load(["005930"])
    ev = res["events"]
    new = ev[ev["fiscal_quarter"] == "2026Q3"]
    new.to_csv(OUT / "samsung_2026Q3_event.csv", index=False, encoding="utf-8-sig")
    out = LIVE_DIR / f"live_check_2026Q3_{pd.Timestamp.today():%Y%m%d}.csv"
    live_check.main(["2026Q3", str(out)])
    return new, pd.read_csv(out)


def step2_consensus():
    cs.create_table()
    df = pd.DataFrame(CONSENSUS)
    df["asof_date"] = pd.to_datetime(df["asof_date"]).dt.date
    n = db.write_fd(df, cs.T_CONSENSUS, ["entity_id", "fiscal_quarter", "item", "asof_date", "source"])
    back = db.read_sql("SELECT * FROM fd_consensus WHERE entity_id='005930' AND fiscal_quarter='2026Q3'")
    back.to_csv(OUT / "consensus_rows.csv", index=False, encoding="utf-8-sig")
    return n, back


def fx_quarterly() -> pd.DataFrame:
    d = read_series("fred", ["DEXKOUS"]).dropna(subset=["value"])
    d["q"] = pd.to_datetime(d["period_end"]) + pd.offsets.QuarterEnd(0)
    q = d.groupby("q")["value"].mean().rename("usdkrw_avg").to_frame()
    q["fx_yoy_log"] = np.log(q["usdkrw_avg"]).diff(4)
    q["fx_qoq_log"] = np.log(q["usdkrw_avg"]).diff(1)
    return q


def step3_fx():
    d = fe.load_nowcast(list(FX_FIRMS))
    d = d[(d["cp"] == "pre5") & (d["variant"] == "all")]
    fx = fx_quarterly()
    rows = []
    for e, mdl in FX_FIRMS.items():
        g = d[(d["entity_id"] == e) & (d["model"] == mdl)][["entity_id", "fiscal_quarter", "g_hat", "g_actual_first",
                                                             "err_first"]].dropna(subset=["err_first"])
        g["q"] = [events.fiscal_quarter_end(x) for x in g["fiscal_quarter"]]
        g = g.merge(fx, left_on="q", right_index=True, how="left")
        g["model"] = mdl
        lo, hi = g["fx_yoy_log"].quantile([0.2, 0.8])
        g["fx_group"] = np.where(g["fx_yoy_log"] >= hi, "원화 약세 상위20%",
                                 np.where(g["fx_yoy_log"] <= lo, "원화 강세 하위20%", "나머지"))
        rows.append(g)
    t = pd.concat(rows, ignore_index=True)
    t.to_csv(OUT / "fx_m4_errors_by_quarter.csv", index=False, encoding="utf-8-sig")
    s = (t.groupby(["entity_id", "model", "fx_group"])
         .agg(n=("err_first", "size"), mean_err=("err_first", "mean"), mean_abs_err=("err_first", lambda x: x.abs().mean()),
              median_err=("err_first", "median"), fx_yoy_mean=("fx_yoy_log", "mean")).reset_index())
    s.to_csv(OUT / "fx_group_stats.csv", index=False, encoding="utf-8-sig")
    corr = t.groupby("entity_id").apply(lambda g: g["err_first"].corr(g["fx_yoy_log"])).rename("corr_err_fxyoy")
    return t, s, corr


def main(steps=("1", "2", "3")):
    pd.set_option("display.width", 250)
    if "1" in steps:
        new, chk = step1_events_and_check()
        print(new.to_string(index=False))
        print(chk[chk["entity_id"].astype(str).str.zfill(6) == "005930"].to_string(index=False))
    if "2" in steps:
        n, back = step2_consensus()
        print("consensus written", n)
        print(back.to_string(index=False))
    if "3" in steps:
        t, s, corr = step3_fx()
        print(s.round(4).to_string(index=False))
        print(corr.round(3))


if __name__ == "__main__":
    main(sys.argv[1:] or ("1", "2", "3"))
