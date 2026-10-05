# -*- coding: utf-8 -*-
"""시범 분석 도우미 v2 (pilot_v2.ipynb 에서 사용).

v1 과 다른 점
1. 위약 hs 상관을 드라이버 x 시차마다, 그 드라이버 상관을 계산한 것과 같은 분기들에서 계산한다.
   (v1 은 기업 매출 전체 기간에서 한 번만 계산 -> 표본이 짧은 드라이버가 과대평가됨)
   추세 가짜 시계열도 같은 분기에서 계산하고, 분기 수가 같은지 trend_n 으로 확인한다.
2. 위약 hs 수 30 -> 200 (기업별 고정 시드, 같은 4자리 품목군 제외).
   후보 전체를 기업별 고정 시드로 섞어 두고, 드라이버 x 시차마다 그 분기들에 YoY 가 모두 있는 후보를
   섞은 순서대로 200개 쓴다. 200개가 안 되면 가능한 최대를 쓰고, 실제로 쓴 수를 n_placebo 에 적는다.
3. 판정 기준은 v1 과 같다. 위약 백분위 97.5 이상은 strong(강함)으로 따로 표시한다.
4. 발표 시점 우위: 같은 분기의 (매출 available_at - 드라이버 available_at) 일수 중앙값 (시차 0 기준).
   양수면 드라이버가 매출보다 그만큼 먼저 알려진다.
5. v1 판정과의 비교를 위해 "위약 200개 + 매출 전체 기간"(v1 방식) 백분위도 함께 계산한다 (scope='full').
"""
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from DATA.fdriver import db, entity, panel as fpanel
from DATA.fdriver.adapters import read_trade

LAGS = (0, 1, 2)
MIN_N = 8
N_PLACEBO = 200
PLACEBO_MIN_MONTHS = 200
CLEAR_PCT = 90.0
STRONG_PCT = 97.5

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

DONGWON_PRE_END = pd.Timestamp("2022-09-30")
DONGWON_POST_START = pd.Timestamp("2024-03-31")


# ---------------------------------------------------------------- 기본 변환
def quarterly_index(wide: pd.DataFrame) -> pd.DataFrame:
    if wide.empty:
        return wide
    return wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="QE"))


def yoy(wide: pd.DataFrame) -> pd.DataFrame:
    """전년 같은 분기 대비 증가율. 기준값이 0 이하이면 계산하지 않는다."""
    w = quarterly_index(wide)
    base = w.shift(4)
    return (w / base - 1).where(base > 0)


def trend_yoy(index: pd.DatetimeIndex, offset: int = 10) -> pd.Series:
    """시간 추세만 있는 가짜 수준 시계열 L_t = 100 + (t + offset) 의 YoY.
    offset 만큼 앞에서 시작한 것으로 두어 index 첫 분기부터 값이 있다 (빈 값 없음)."""
    t = np.arange(len(index)) + offset
    return pd.Series(4.0 / (96.0 + t), index=index)


def _corr_vec(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """y (n,) 와 X (n, m) 의 열별 피어슨 상관. X 에 빈 값이 없어야 한다."""
    yc = y - y.mean()
    Xc = X - X.mean(axis=0)
    den = np.sqrt((yc ** 2).sum() * (Xc ** 2).sum(axis=0))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (yc @ Xc) / den


# ---------------------------------------------------------------- 위약 hs
def placebo_pool(min_months: int = PLACEBO_MIN_MONTHS) -> List[str]:
    df = db.read_sql(
        "SELECT `root_hs_code` FROM `korea_monthly_trade_data` WHERE `indicator` = 'expDlr' "
        "AND CHAR_LENGTH(`root_hs_code`) = 6 GROUP BY `root_hs_code` HAVING COUNT(*) >= :m",
        {"m": min_months},
    )
    return sorted(df["root_hs_code"].astype(str))


def placebo_candidates(entity_id: str, pool: List[str]) -> List[str]:
    links = entity.entity_links(entity_id)
    linked = set(links.loc[links["link_type"] == "hs_code", "series_key"])
    fam4 = {k[:4] for k in linked}
    return [c for c in pool if c not in linked and c[:4] not in fam4]


def placebo_order(entity_id: str, pool: List[str]) -> List[str]:
    """후보 전체를 기업별 고정 시드로 섞은 순서. evaluate 가 이 순서대로 조건을 채우는 200개를 쓴다."""
    cand = placebo_candidates(entity_id, pool)
    rng = np.random.default_rng(int(entity_id))
    return [cand[i] for i in rng.permutation(len(cand))]


def placebo_yoy(codes: List[str], fye: int, index: pd.DatetimeIndex) -> pd.DataFrame:
    """위약 hs 수출액의 분기 YoY (index 에 맞춤). 열 순서는 codes 순서를 따른다."""
    tr = read_trade(codes, items=["expDlr"])
    q = fpanel.to_fiscal_quarter(tr, fye)
    w = q.pivot_table(index="period_end", columns="key", values="value")
    w = w.reindex(w.index.union(index))
    out = yoy(w).reindex(index)
    return out[[c for c in codes if c in out.columns]]


# ---------------------------------------------------------------- 평가
def _verdict(n, corr, pct, trend_corr) -> str:
    if n < MIN_N or np.isnan(corr):
        return "표본 부족"
    if np.isnan(pct):
        return "위약 없음"
    beats_trend = np.isnan(trend_corr) or abs(corr) > abs(trend_corr)
    if pct >= CLEAR_PCT and beats_trend:
        return "뚜렷"
    if pct >= CLEAR_PCT:
        return "위약 hs보다 큼, 추세와 비슷"
    return "위약과 구분 안 됨"


def timing_advantage(avail: pd.DataFrame, driver: str, target: str = "revenue") -> float:
    """같은 분기 (매출 available_at - 드라이버 available_at) 일수의 중앙값. 시차 0 기준."""
    if driver not in avail or target not in avail:
        return np.nan
    a = avail[[target, driver]].dropna()
    if a.empty:
        return np.nan
    return float((a[target] - a[driver]).dt.days.median())


def evaluate(yy: pd.DataFrame, drivers: Iterable[str], plc: pd.DataFrame,
             avail: Optional[pd.DataFrame] = None, target: str = "revenue", lags=LAGS,
             mask: Optional[pd.Series] = None, scope: str = "same",
             n_placebo: int = N_PLACEBO) -> pd.DataFrame:
    """드라이버 x 시차별 상관과 위약 비교.

    scope='same': 위약 상관을 그 드라이버 상관과 같은 분기들에서 계산 (v2 기본)
    scope='full': 위약 상관을 매출 YoY 가 있는 전체 분기에서 계산 (v1 방식, 비교용)
    """
    y_all = yy[target]
    base = y_all.notna()
    if mask is not None:
        base &= mask.reindex(yy.index).fillna(False).astype(bool)
    tr = trend_yoy(yy.index)
    rows = []
    for d in drivers:
        if d not in yy:
            continue
        adv = timing_advantage(avail, d, target) if avail is not None else np.nan
        for k in lags:
            x = yy[d].shift(k)
            ok = base & x.notna()
            n = int(ok.sum())
            c = np.nan
            if n >= 3 and x[ok].std() > 0 and y_all[ok].std() > 0:
                c = float(np.corrcoef(x[ok], y_all[ok])[0, 1])
            trv = tr.shift(k)[ok]
            trend_n = int(trv.notna().sum())
            tc = float(np.corrcoef(trv, y_all[ok])[0, 1]) if trend_n >= 3 and trend_n == n else np.nan

            win = ok if scope == "same" else base
            P = plc.shift(k)[win]
            # 그 분기들을 모두 채운 위약만, 섞은 순서대로 n_placebo 개
            P = P.loc[:, P.notna().all()].iloc[:, :n_placebo] if len(P) else P
            npl = int(P.shape[1]) if len(P) else 0
            pct = p05 = p95 = np.nan
            if npl and win.sum() >= 3:
                pc = _corr_vec(y_all[win].to_numpy(), P.to_numpy())
                pc = pc[~np.isnan(pc)]
                npl = len(pc)
                if npl and not np.isnan(c):
                    pct = 100.0 * float((np.abs(pc) <= abs(c)).mean())
                if npl:
                    p05, p95 = float(np.quantile(pc, 0.05)), float(np.quantile(pc, 0.95))
            idx = ok[ok].index
            rows.append({
                "driver": d, "lag": k, "corr": c, "n": n,
                "start": idx.min().date() if n else None, "end": idx.max().date() if n else None,
                "trend_corr": tc, "trend_n": trend_n, "n_placebo": npl,
                "placebo_pct_abs": pct, "placebo_p05": p05, "placebo_p95": p95,
                "verdict": _verdict(n, c, pct, tc),
                "strong": bool(n >= MIN_N and not np.isnan(pct) and pct >= STRONG_PCT),
                "timing_adv_days": adv,
            })
    return pd.DataFrame(rows)


def compare_v1(v1: pd.DataFrame, v2: pd.DataFrame, full200: pd.DataFrame, rev_n: Dict[str, int]) -> pd.DataFrame:
    """v1 판정과 v2 판정 비교. full200(위약 200개, 전체 기간) 으로 원인을 나눈다.

    reason
      - 위약 기간 맞춤: 위약 수만 늘린 판정(full200)은 v1 과 같고, 같은 분기 위약(v2)에서 바뀜
      - 위약 수 30->200: full200 에서 이미 바뀌고 v2 도 같음
      - 둘 다: 위의 둘이 모두 작용
    """
    key = ["entity", "driver", "lag"]
    a = v1[key + ["verdict", "placebo_pct_abs", "n"]].rename(
        columns={"verdict": "verdict_v1", "placebo_pct_abs": "pct_v1", "n": "n_v1"})
    b = full200[key + ["verdict", "placebo_pct_abs"]].rename(
        columns={"verdict": "verdict_full200", "placebo_pct_abs": "pct_full200"})
    c = v2[key + ["verdict", "placebo_pct_abs", "n", "n_placebo", "strong"]].rename(
        columns={"verdict": "verdict_v2", "placebo_pct_abs": "pct_v2", "n": "n_v2"})
    m = a.merge(b, on=key, how="outer").merge(c, on=key, how="outer")
    m["rev_yoy_n"] = m["entity"].map(rev_n)
    m["short_sample"] = m["n_v2"] < m["rev_yoy_n"]

    def reason(r):
        if r["verdict_v1"] == r["verdict_v2"]:
            return ""
        if r["verdict_full200"] == r["verdict_v1"]:
            return f"위약 기간 맞춤 (표본 {int(r['n_v2'])}/{int(r['rev_yoy_n'])}분기)"
        if r["verdict_full200"] == r["verdict_v2"]:
            return "위약 수 30->200"
        return f"둘 다 (표본 {int(r['n_v2'])}/{int(r['rev_yoy_n'])}분기)"
    m["changed"] = m["verdict_v1"] != m["verdict_v2"]
    m["reason"] = m.apply(reason, axis=1)
    return m


def ordered_drivers(entity_id: str, available: Iterable[str]) -> List[str]:
    avail = [d for d in available if d != "revenue"]
    spec = [d for d in SPEC_DRIVERS.get(entity_id, []) if d in avail]
    return spec + sorted(d for d in avail if d not in spec)
