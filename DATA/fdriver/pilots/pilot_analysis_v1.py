# -*- coding: utf-8 -*-
"""시범 분석 도우미 (pilot_v1.ipynb 에서 사용).

- 매출 YoY 와 드라이버 YoY 의 상관: 동행(0), 1분기 선행, 2분기 선행
- 위약 검정
  (a) 회사와 연결되지 않은 무작위 hs6 30개의 같은 상관 분포에서 실제 드라이버 |상관|의 백분위
  (b) 시간 추세만 있는 가짜 시계열(선형 추세)과의 상관
- 1단계 목적은 흐름 검증이며 예측력 평가는 2단계에서 한다. 상관은 참고용.
"""
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from DATA.fdriver import db, entity, panel as fpanel
from DATA.fdriver.adapters import read_trade

LAGS = (0, 1, 2)
MIN_N = 8
N_PLACEBO = 30
PLACEBO_MIN_MONTHS = 200          # 위약 후보: 6자리 hs 중 수출 월 데이터가 이 수 이상
CLEAR_PCT = 90.0                  # 위약 hs 분포에서 이 백분위 이상이고 추세 상관보다 크면 "뚜렷"

# 명세서 6절 후보 드라이버 (표·그림에서 앞에 보여 줌)
SPEC_DRIVERS: Dict[str, List[str]] = {
    "005930": ["trade:854232:expDlr", "trade:854231:expDlr", "tw_revenue:2330", "tw_revenue:2408",
               "kr_peer:000660:revenue"],
    "006040": ["trade:030341:expDlr", "trade:030342:expDlr", "trade:030343:expDlr", "trade:030344:expDlr",
               "trade:030341:impDlr", "trade:030342:impDlr", "trade:030343:impDlr", "trade:030344:impDlr"],
    "267270": ["trade:842951:expDlr", "trade:842952:expDlr", "fred:HOUST", "fred:PERMIT"],
    "278470": ["amazon_bsr:median_rank", "google_trends:US:medicube", "google_trends:US:korean skincare",
               "fred:RSHPCS", "kosis:kosis_113bb45a04e152ed", "kosis:kosis_b85f8425a11ca86a"],
    "003350": ["trade:330499:expDlr", "trade:330420:expDlr", "trade:330499:expWgt", "trade:330420:expWgt",
               "trade:330499:unit_price", "trade:330420:unit_price"],
    "039130": ["tourism:D:100", "tourism:E:TOTAL", "airline:intl_dep_sum"],
}
TRADE_ITEMS: Dict[str, tuple] = {"006040": ("expDlr", "expWgt", "impDlr")}

# 동원산업 2022년 합병: 2022 Q4 단독값은 연간(합병 기준) - 1~3분기(합병 전 기준)라 비정상.
# YoY 가 합병 전 값끼리인 구간과 합병 후 값끼리인 구간으로 나눈다.
DONGWON_PRE_END = pd.Timestamp("2022-09-30")
DONGWON_POST_START = pd.Timestamp("2024-03-31")


def quarterly_index(wide: pd.DataFrame) -> pd.DataFrame:
    if wide.empty:
        return wide
    idx = pd.date_range(wide.index.min(), wide.index.max(), freq="QE")
    return wide.reindex(idx)


def yoy(wide: pd.DataFrame) -> pd.DataFrame:
    """전년 같은 분기 대비 증가율. 기준값이 0 이하이면 계산하지 않는다."""
    w = quarterly_index(wide)
    base = w.shift(4)
    out = w / base - 1
    return out.where(base > 0)


def _corr(x: pd.Series, y: pd.Series, mask: Optional[pd.Series] = None):
    ok = x.notna() & y.notna()
    if mask is not None:
        ok &= mask.reindex(ok.index).fillna(False)
    n = int(ok.sum())
    if n < 3 or x[ok].std() == 0 or y[ok].std() == 0:
        return np.nan, n, ok
    return float(np.corrcoef(x[ok], y[ok])[0, 1]), n, ok


def trend_series(index: pd.DatetimeIndex) -> pd.Series:
    """시간 추세만 있는 가짜 수준 시계열 (100 + t)."""
    return pd.Series(100.0 + np.arange(len(index)), index=index)


def corr_table(yy: pd.DataFrame, drivers: Iterable[str], target: str = "revenue",
               lags=LAGS, mask: Optional[pd.Series] = None) -> pd.DataFrame:
    """드라이버가 lag 분기 앞서는 상관. trend_corr 는 같은 표본(같은 분기들)에서 선형 추세 YoY 와의 상관."""
    tr_yoy = yoy(trend_series(yy.index).to_frame("trend"))["trend"]
    rows = []
    for d in drivers:
        if d not in yy:
            continue
        for k in lags:
            x = yy[d].shift(k)
            c, n, ok = _corr(x, yy[target], mask)
            tc, _, _ = _corr(tr_yoy.shift(k).where(ok), yy[target].where(ok))
            idx = ok[ok].index
            rows.append({"driver": d, "lag": k, "corr": c, "n": n, "trend_corr": tc,
                         "start": idx.min().date() if n else None, "end": idx.max().date() if n else None})
    return pd.DataFrame(rows)


def placebo_pool(min_months: int = PLACEBO_MIN_MONTHS) -> List[str]:
    df = db.read_sql(
        "SELECT `root_hs_code` FROM `korea_monthly_trade_data` WHERE `indicator` = 'expDlr' "
        "AND CHAR_LENGTH(`root_hs_code`) = 6 GROUP BY `root_hs_code` HAVING COUNT(*) >= :m",
        {"m": min_months},
    )
    return sorted(df["root_hs_code"].astype(str))


def placebo_codes(entity_id: str, pool: List[str], n: int = N_PLACEBO) -> List[str]:
    links = entity.entity_links(entity_id)
    linked = set(links.loc[links["link_type"] == "hs_code", "series_key"])
    linked |= {k[:4] for k in linked}                      # 같은 4자리 품목군도 제외
    cand = [c for c in pool if c not in linked and c[:4] not in linked]
    rng = np.random.default_rng(int(entity_id))           # 기업별로 고정된 무작위 추출
    return sorted(rng.choice(cand, size=min(n, len(cand)), replace=False).tolist())


def placebo_corrs(codes: List[str], rev_yoy: pd.Series, fye: int, lags=LAGS,
                  mask: Optional[pd.Series] = None) -> pd.DataFrame:
    tr = read_trade(codes, items=["expDlr"])
    q = fpanel.to_fiscal_quarter(tr, fye)
    w = q.pivot_table(index="period_end", columns="key", values="value")
    w = w.reindex(w.index.union(rev_yoy.index))
    yy = yoy(w).reindex(rev_yoy.index)
    rows = []
    for c in yy.columns:
        for k in lags:
            r, n, _ = _corr(yy[c].shift(k), rev_yoy, mask)
            rows.append({"code": c, "lag": k, "corr": r, "n": n})
    return pd.DataFrame(rows)


def judge(ct: pd.DataFrame, plc: pd.DataFrame) -> pd.DataFrame:
    """실제 드라이버 |상관|이 위약 hs |상관| 분포의 몇 백분위인지, 추세 상관보다 큰지."""
    out = ct.copy()
    pct, lo, hi = [], [], []
    for r in out.itertuples():
        p = plc.loc[(plc["lag"] == r.lag) & plc["corr"].notna(), "corr"]
        if np.isnan(r.corr) or p.empty:
            pct.append(np.nan); lo.append(np.nan); hi.append(np.nan)
            continue
        pct.append(100.0 * float((p.abs() <= abs(r.corr)).mean()))
        lo.append(float(p.quantile(0.05))); hi.append(float(p.quantile(0.95)))
    out["placebo_pct_abs"] = pct
    out["placebo_p05"] = lo
    out["placebo_p95"] = hi

    def verdict(r):
        if r["n"] < MIN_N or np.isnan(r["corr"]):
            return "표본 부족"
        beats_trend = np.isnan(r["trend_corr"]) or abs(r["corr"]) > abs(r["trend_corr"])
        if r["placebo_pct_abs"] >= CLEAR_PCT and beats_trend:
            return "뚜렷"
        if r["placebo_pct_abs"] >= CLEAR_PCT:
            return "위약 hs보다 큼, 추세와 비슷"
        return "위약과 구분 안 됨"
    out["verdict"] = out.apply(verdict, axis=1)
    return out


def level_vs_trend(wide: pd.DataFrame, drivers: Iterable[str], target: str = "revenue") -> pd.DataFrame:
    """수준(원 값) 상관과 추세 가짜 시계열 수준 상관 비교. 수준 상관은 추세만으로도 높게 나온다."""
    w = quarterly_index(wide)
    t = trend_series(w.index)
    rows = []
    for d in drivers:
        if d not in w:
            continue
        c, n, ok = _corr(w[d], w[target])
        tc, _, _ = _corr(t.where(ok), w[target].where(ok))
        rows.append({"driver": d, "level_corr": c, "trend_level_corr": tc, "n": n})
    return pd.DataFrame(rows)


def ordered_drivers(entity_id: str, available: Iterable[str]) -> List[str]:
    avail = [d for d in available if d != "revenue"]
    spec = [d for d in SPEC_DRIVERS.get(entity_id, []) if d in avail]
    rest = sorted(d for d in avail if d not in spec)
    return spec + rest
