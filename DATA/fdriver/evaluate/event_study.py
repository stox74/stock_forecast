# -*- coding: utf-8 -*-
"""수익률 검정 (2a 명세서 7절, v6 핵심 검정).

초과수익률
  종목: KSE_Price 원천 등락률 prc_change(%) / 100. 거래량 0 인 날(거래정지)은 결측.
  시장: kr_stock_index_daily (YAHOO:^KS11) 종가 일간 수익률.
  초과 = 종목 - 시장. 둘 중 하나라도 없으면 결측.
날짜
  거래일 달력 = adapters.trading_calendar (KSE_Price 날짜 + 코스피 지수 날짜).
  day 0 = 첫 공시일 (fd_signal.event_date). 휴장일이면 다음 거래일.
  신호일 s = 기준일(asof_date). 매매·사전 구간은 s 다음 거래일부터 시작한다 (s 가 휴장일이어도 s 이후 첫 거래일).
구간 (양 끝 포함)
  trade  s+1 ~ day +1     pre  s+1 ~ day -2     ann  day 0 ~ day +1     drift  day +2 ~ day +60
  CAR = 초과수익률 단순 합. 결측일은 건너뛰고 수를 센다 (n_obs, n_missing). 구간이 비면(끝 < 시작) NaN.
  구간 끝이 종목 자료 마지막 날보다 뒤면 미완료로 NaN. 구간 시작이 종목 첫 자료일(상장 전, KSE_Price 2013-05-21
  이전) 전이면 자료 없음으로 NaN (결측일 수에 넣지 않음).
회귀
  fe_cluster_ols: y ~ x + 기업 고정효과, 분기(fiscal_quarter)별 군집 표준오차 (statsmodels).
  기업이 하나면 고정효과 대신 상수항.
"""
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from .. import market_index
from ..adapters import read_price, trading_calendar

WINDOWS = {"trade": ("s+1", 1), "pre": ("s+1", -2), "ann": (0, 1), "drift": (2, 60)}


# ---------------------------------------------------------------- 수익률
def stock_returns(entity_ids: Iterable[str]) -> pd.DataFrame:
    """date, entity_id, ret, close, prc_change, volume (ret 은 거래량 0 이면 NaN)."""
    p = read_price(list(entity_ids), field=["close", "volume", "prc_change"])
    w = p.pivot_table(index=["key", "period_end"], columns="item", values="value").reset_index()
    w = w.rename(columns={"key": "entity_id", "period_end": "date"}).sort_values(["entity_id", "date"])
    w["ret"] = np.where(w["volume"] > 0, w["prc_change"] / 100.0, np.nan)
    return w.reset_index(drop=True)


def price_check(w: pd.DataFrame, tol_pp: float = 1.0) -> pd.DataFrame:
    """종가 비율 수익률과 원천 등락률이 tol_pp(%p) 넘게 다른 날."""
    w = w.sort_values(["entity_id", "date"]).copy()
    w["ret_close_pct"] = w.groupby("entity_id")["close"].pct_change() * 100
    w["diff_pp"] = w["ret_close_pct"] - w["prc_change"]
    return w.loc[w["diff_pp"].abs() > tol_pp, ["entity_id", "date", "close", "ret_close_pct", "prc_change",
                                               "diff_pp", "volume"]].reset_index(drop=True)


def excess_returns(entity_ids: Iterable[str], stock: Optional[pd.DataFrame] = None,
                   market: Optional[pd.DataFrame] = None) -> Dict:
    """반환 {"cal": 거래일 DatetimeIndex, "ex": {entity_id: 초과수익률 Series}, "first"/"last": {entity_id: 첫/마지막 자료일}}."""
    stock = stock_returns(entity_ids) if stock is None else stock
    market = market_index.read_index() if market is None else market
    cal = trading_calendar(register=False)
    mk = market.set_index("period_end")["ret"].reindex(cal)
    out, first, last = {}, {}, {}
    for e, g in stock.groupby("entity_id"):
        r = g.set_index("date")["ret"].reindex(cal)
        out[e] = r - mk
        first[e], last[e] = g["date"].min(), g["date"].max()
    return {"cal": cal, "ex": out, "first": first, "last": last}


# ---------------------------------------------------------------- CAR
def window_positions(cal: pd.DatetimeIndex, signal_date, event_date) -> Dict[str, tuple]:
    """구간별 (시작 위치, 끝 위치). 위치는 cal 의 정수 인덱스."""
    c = cal.values
    d0 = int(np.searchsorted(c, np.datetime64(pd.Timestamp(event_date)), side="left"))
    s1 = int(np.searchsorted(c, np.datetime64(pd.Timestamp(signal_date)), side="right")) if signal_date is not None else None
    out = {}
    for name, (a, b) in WINDOWS.items():
        start = s1 if a == "s+1" else d0 + a
        out[name] = (start, d0 + b) if start is not None else (None, None)
    return out


def car(ex: pd.Series, cal: pd.DatetimeIndex, start: Optional[int], end: Optional[int], last_date,
        first_date=None) -> Dict:
    """구간 [start, end] (cal 위치) 의 CAR. 구간 시작이 종목 첫 자료일(상장·자료 시작) 전이면 자료 없음으로 NaN
    (결측일로 세지 않음). 끝이 마지막 자료일 뒤면 미완료로 NaN."""
    if start is None or end is None or end < start or start < 0:
        return {"car": np.nan, "n_obs": 0, "n_missing": 0, "start": None, "end": None}
    if first_date is not None and start < len(cal) and cal[start] < pd.Timestamp(first_date):
        return {"car": np.nan, "n_obs": 0, "n_missing": 0, "start": cal[start], "end": None, "before_data": True}
    if end >= len(cal) or cal[end] > pd.Timestamp(last_date):
        return {"car": np.nan, "n_obs": 0, "n_missing": 0, "start": cal[start] if start < len(cal) else None,
                "end": None, "incomplete": True}
    seg = ex.iloc[start:end + 1]
    n_obs = int(seg.notna().sum())
    return {"car": float(seg.sum(skipna=True)) if n_obs else np.nan, "n_obs": n_obs,
            "n_missing": int(seg.isna().sum()), "start": cal[start], "end": cal[end]}


def event_cars(events: pd.DataFrame, xr: Dict) -> pd.DataFrame:
    """events: entity_id, fiscal_quarter, signal_date, event_date (+ 그대로 넘길 컬럼).
    반환: 입력 + 구간별 car_*, nobs_*, nmiss_*, 그리고 trade 구간 시작·끝 날짜."""
    rows = []
    cal = xr["cal"]
    for r in events.itertuples(index=False):
        d = r._asdict()
        e = d["entity_id"]
        if e not in xr["ex"] or pd.isna(d.get("event_date")):
            rows.append(d)
            continue
        pos = window_positions(cal, d.get("signal_date"), d["event_date"])
        for name, (a, b) in pos.items():
            c = car(xr["ex"][e], cal, a, b, xr["last"][e], xr.get("first", {}).get(e))
            d[f"car_{name}"], d[f"nobs_{name}"], d[f"nmiss_{name}"] = c["car"], c["n_obs"], c["n_missing"]
            if name == "trade":
                d["trade_start"], d["trade_end"] = c["start"], c["end"]
        rows.append(d)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 회귀
def fe_cluster_ols(df: pd.DataFrame, y: str, xs: Sequence[str], fe: str = "entity_id",
                   cluster: str = "fiscal_quarter") -> Dict:
    """y ~ xs + C(fe), 분기 군집 표준오차. 반환 계수표와 표본 정보."""
    d = df.dropna(subset=[y, *xs]).copy()
    n_ent = d[fe].nunique()
    info = {"n": len(d), "n_entities": n_ent, "n_clusters": d[cluster].nunique(),
            "events_by_entity": ";".join(f"{k}:{v}" for k, v in d[fe].value_counts().sort_index().items())}
    if len(d) < len(xs) + n_ent + 2 or d[cluster].nunique() < 3:
        return {**info, "coef": pd.DataFrame()}
    rhs = " + ".join(xs) + (f" + C({fe})" if n_ent > 1 else "")
    m = smf.ols(f"{y} ~ {rhs}", data=d)
    groups = pd.factorize(d[cluster])[0]
    f = m.fit(cov_type="cluster", cov_kwds={"groups": groups})
    coef = pd.DataFrame({"coef": f.params[list(xs)], "se": f.bse[list(xs)], "t": f.tvalues[list(xs)],
                         "p": f.pvalues[list(xs)]})
    return {**info, "coef": coef, "r2_within": _within_r2(d, y, xs, fe), "fit": f}


def _within_r2(d: pd.DataFrame, y: str, xs: Sequence[str], fe: str) -> float:
    w = d[[y, *xs]] - d.groupby(fe)[[y, *xs]].transform("mean")
    X = w[list(xs)].to_numpy()
    yy = w[y].to_numpy()
    if not len(yy) or not (yy @ yy) > 0:
        return np.nan
    b, *_ = np.linalg.lstsq(X, yy, rcond=None)
    r = yy - X @ b
    return float(1 - (r @ r) / (yy @ yy))


def simple_ols(df: pd.DataFrame, y: str, xs: Sequence[str]) -> Dict:
    """기업별 단순 회귀 (상수항 포함, HC1 표준오차)."""
    d = df.dropna(subset=[y, *xs])
    if len(d) < len(xs) + 3:
        return {"n": len(d), "coef": pd.DataFrame()}
    f = smf.ols(f"{y} ~ " + " + ".join(xs), data=d).fit(cov_type="HC1")
    coef = pd.DataFrame({"coef": f.params[list(xs)], "se": f.bse[list(xs)], "t": f.tvalues[list(xs)],
                         "p": f.pvalues[list(xs)]})
    return {"n": len(d), "coef": coef}


def coef_rows(res: Dict, label: Dict) -> List[Dict]:
    out = []
    if res["coef"].empty:
        return [{**label, "term": None, "n": res["n"], "events_by_entity": res.get("events_by_entity")}]
    for term, r in res["coef"].iterrows():
        out.append({**label, "term": term, "coef": r["coef"], "se": r["se"], "t": r["t"], "p": r["p"],
                    "n": res["n"], "n_entities": res.get("n_entities"), "n_clusters": res.get("n_clusters"),
                    "r2_within": res.get("r2_within"), "events_by_entity": res.get("events_by_entity")})
    return out


def half_split(df: pd.DataFrame, x: str, y: str, by: str = "entity_id") -> pd.DataFrame:
    """기업 안에서 x 중앙값으로 상위·하위 절반을 나눠 y 평균 차이 (기업별 + 전체 합산)."""
    d = df.dropna(subset=[x, y]).copy()
    d["half"] = d.groupby(by)[x].transform(lambda s: np.where(s > s.median(), "high", "low"))
    rows = []
    for e, g in list(d.groupby(by)) + [("ALL", d)]:
        hi, lo = g.loc[g["half"] == "high", y], g.loc[g["half"] == "low", y]
        rows.append({by: e, "n": len(g), "n_high": len(hi), "n_low": len(lo), "mean_high": hi.mean(),
                     "mean_low": lo.mean(), "diff_high_minus_low": hi.mean() - lo.mean()})
    return pd.DataFrame(rows)
