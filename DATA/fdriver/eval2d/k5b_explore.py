# -*- coding: utf-8 -*-
"""2d K5b 탐색 (판정 아님): H4a·H4b 를 K5 평가 구간(2024Q1~2026Q2) 자료로 계산. DB 쓰기 없음.

사전 등록 문서 `docs/preregistration_2d_live.md` (커밋 4b0a4b3) 의 정의를 그대로 쓴다. K5 D2 결과를 보고 만든 가설이라
이 계산은 탐색적이며 판정에 쓰지 않는다.
- 표본: fd_eval_2d (run 20261018_013015) 의 in_d2 = 1 행 (driver, 컨센서스 없음)
- H4a: CAR[asof+1, 발표일-1] ~ gap_E_vs_B2, 계수 > 0 (단측)
- H4b: CAR[분기 첫 거래일, asof] ~ gap_E_vs_B2, 계수 > 0 (단측, 설명용)
- 수익률·시장조정·결측·주가 제외 규칙은 K5 와 같다. 거래일 달력은 KSE_Price 날짜.
- 참고: 분기별 gap 3분위 스프레드 (eval2b tercile_by_quarter), H2·H3 형식(양측) 값도 함께 적는다.
실행: python -m DATA.fdriver.eval2d.k5b_explore
"""
import json

import numpy as np
import pandas as pd

from .. import db
from ..config import output_path
from ..eval2b.k3_analysis import tercile_by_quarter
from .k5_judge import IDX, slope_test

OUT = output_path("2d", "K5b")
EVAL_RUN = "20261018_013015"


def frame() -> pd.DataFrame:
    d = db.read_sql("SELECT * FROM fd_eval_2d WHERE run_id = :r AND in_d2 = 1", {"r": EVAL_RUN})
    mp = db.read_sql("SELECT entity_id, price_excl_from, price_excl_to FROM fd_driver_map_2d")
    d = d.merge(mp, on="entity_id", how="left")
    for c in ("asof_date", "announce_date", "price_excl_from", "price_excl_to"):
        d[c] = pd.to_datetime(d[c])
    d["q_start"] = pd.PeriodIndex(d["quarter"], freq="Q").start_time
    return d


def window_cars(d: pd.DataFrame) -> pd.DataFrame:
    ids = sorted(d["entity_id"].unique())
    f, p = db.in_clause("c", ids)
    px = db.read_sql(f"SELECT code, date, prc_change, volume FROM KSE_Price WHERE code IN {f} AND date >= '2023-12-01'", p)
    px["date"] = pd.to_datetime(px["date"])
    px["ret"] = np.where(px["volume"].fillna(0) > 0, px["prc_change"] / 100.0, np.nan)
    mk = {}
    for k, sym in IDX.items():
        m = db.read_sql("SELECT period_end, close FROM kr_stock_index_daily WHERE idx_name = :n AND period_end >= "
                        "'2023-11-01' ORDER BY period_end", {"n": sym})
        m["period_end"] = pd.to_datetime(m["period_end"])
        mk[k] = m.set_index("period_end")["close"].pct_change()
    cal = pd.DatetimeIndex(sorted(set(px["date"])))
    ret = {c: g.set_index("date")["ret"] for c, g in px.groupby("code")}
    rows = []
    for r in d.itertuples():
        out = {"entity_id": r.entity_id, "quarter": r.quarter}
        ia = int(cal.searchsorted(r.asof_date))           # asof (거래일)
        i0 = int(cal.searchsorted(r.announce_date))       # 발표일 0
        iq = int(cal.searchsorted(r.q_start))             # 분기 첫 거래일
        for name, (a, b) in {"car_pre": (ia + 1, i0 - 1), "car_qtd": (iq, ia)}.items():
            if b < a or a < 0 or b >= len(cal):
                out[name], out[name + "_note"] = np.nan, "short_window"
                continue
            win = cal[a:b + 1]
            if pd.notna(r.price_excl_from) and win[-1] >= r.price_excl_from and win[0] <= r.price_excl_to:
                out[name], out[name + "_note"] = np.nan, "price_excl"
                continue
            s = ret.get(r.entity_id, pd.Series(dtype=float)).reindex(win)
            m = mk.get(r.market, mk["KOSPI"]).reindex(win)
            ex = (s - m) * 100
            out[name] = float(ex.sum()) if ex.notna().all() else np.nan
            out[name + "_note"] = "" if ex.notna().all() else "missing_return"
            out[name + "_days"] = len(win)
        rows.append(out)
    return pd.DataFrame(rows)


def two_sided_view(r: dict) -> str:
    if pd.isna(r.get("p_two")):
        return "계산 불가"
    if r["p_two"] < 0.05:
        return "양의 방향 (H2 형식)" if r["b"] > 0 else "음의 방향 (H3 형식)"
    return "방향 없음"


def main():
    d = frame()
    c = window_cars(d)
    d = d.merge(c, on=["entity_id", "quarter"], how="left")
    res = {"note": "탐색적, 판정에 쓰지 않음 (K5 D2 결과를 보고 만든 가설). 사전 등록 커밋 4b0a4b3",
           "sample_rows": len(d)}
    for name, col in (("H4a_pre", "car_pre"), ("H4b_qtd", "car_qtd"), ("ref_H2H3_0_1", "car_0_1")):
        r = slope_test(d, col, "gap_E_vs_B2")
        t = tercile_by_quarter(d.rename(columns={"entity_id": "ticker"}), "gap_E_vs_B2", col)
        res[name] = {**r, "two_sided_view": two_sided_view(r),
                     "tercile": {k: v for k, v in t.items() if k != "table"},
                     "car_mean": float(d[col].mean()), "car_n": int(d[col].notna().sum()),
                     "notes": d.get(col + "_note", pd.Series(dtype=str)).fillna("").value_counts().to_dict()
                     if col != "car_0_1" else {}}
        t["table"].to_csv(OUT / f"k5b_{name}_tercile_by_quarter.csv", index=False, encoding="utf-8-sig")
    res["window_days"] = {"car_pre": d["car_pre_days"].describe().round(1).to_dict(),
                          "car_qtd": d["car_qtd_days"].describe().round(1).to_dict()}
    d[["entity_id", "quarter", "market", "asof_date", "announce_date", "gap_E_vs_B2", "car_pre", "car_pre_days",
       "car_pre_note", "car_qtd", "car_qtd_days", "car_qtd_note", "car_0_1"]].to_csv(
        OUT / "k5b_explore_events.csv", index=False, encoding="utf-8-sig")
    (OUT / "k5b_explore.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
