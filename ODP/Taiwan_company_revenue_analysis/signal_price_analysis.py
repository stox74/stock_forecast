# -*- coding: utf-8 -*-
"""
signal_price_analysis.py — 대만 월매출 신호 vs 한국/미국 T+n 주가수익률 (Python 3.9)

아이디어
--------
대만 M월 매출은 M+1월 10일경 공시된다(신호 확정일 = publish_day, 기본 11일).
이 시점의 신호(YoY, YoY 가속도, 모델예측 대비 서프라이즈)가
관련 한국/미국 기업의 이후 T+n 수익률을 예측하는지 검증한다.

핵심 설계 원칙
--------------
1) 신호는 '수준'보다 '서프라이즈/변화': yoy, d_yoy(가속), surprise(실제/예측-1)
2) look-ahead 방지: 공시일 이후 첫 거래일부터 수익률 측정
3) 공통 베타 제거: 시장(유니버스 중위수 또는 지정 벤치마크) 대비 초과수익률
4) 사전 지정한 페어만 검증(다중비교 회피), 사후에 IC/분위/이벤트스터디로 판정

주가 DB
-------
한국: investar.KSE_Price  (date, close, code='005930' 형식)
미국: investar.us_stock_daily_market_cap (date, ticker, indicator='close_price', value)
"""
import sqlite3
from typing import Dict, Iterable, List, Optional, Union

import numpy as np
import pandas as pd

try:
    from scipy.stats import spearmanr  # 있으면 사용
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ======================================================================
# 0. 유틸
# ======================================================================
def norm_kr_code(code: str) -> str:
    """'A005930' → '005930' (KSE_Price 는 A 접두어 없음)."""
    code = str(code).strip()
    return code[1:] if code[:1].upper() == "A" and code[1:].isdigit() else code


def _spearman(x: pd.Series, y: pd.Series):
    pair = pd.concat([x, y], axis=1).dropna()
    n = len(pair)
    if n < 8:
        return np.nan, np.nan, n
    if _HAS_SCIPY:
        r, p = spearmanr(pair.iloc[:, 0], pair.iloc[:, 1])
        return float(r), float(p), n
    r = pair.iloc[:, 0].rank().corr(pair.iloc[:, 1].rank())
    return float(r), np.nan, n


# ======================================================================
# 1. 주가 로더 (한국/미국)
# ======================================================================
def load_kr_prices(engine, codes: Optional[Iterable[str]] = None,
                   start: Optional[str] = None) -> pd.DataFrame:
    """KSE_Price → 종가 wide (index=date, columns='005930' 형식)."""
    q = "SELECT date, code, close FROM KSE_Price WHERE close IS NOT NULL"
    params = {}
    if start:
        q += " AND date >= %(start)s"; params["start"] = start
    if codes:
        cs = [norm_kr_code(c) for c in codes]
        ph = ", ".join(f"%(c{i})s" for i in range(len(cs)))
        q += f" AND code IN ({ph})"
        params.update({f"c{i}": c for i, c in enumerate(cs)})
    df = pd.read_sql(q, engine, params=params)
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return (df.pivot_table(index="date", columns="code", values="close",
                           aggfunc="last").sort_index().astype(float))


def load_us_prices(engine, tickers: Optional[Iterable[str]] = None,
                   start: Optional[str] = None,
                   table: str = "us_stock_daily_market_cap",
                   indicator: str = "close_price") -> pd.DataFrame:
    """us_stock_daily_market_cap → 종가 wide (index=date, columns=ticker)."""
    q = (f"SELECT date, ticker, value FROM `{table}` "
         "WHERE indicator = %(ind)s AND value IS NOT NULL")
    params = {"ind": indicator}
    if start:
        q += " AND date >= %(start)s"; params["start"] = start
    if tickers:
        tk = list(tickers)
        ph = ", ".join(f"%(t{i})s" for i in range(len(tk)))
        q += f" AND ticker IN ({ph})"
        params.update({f"t{i}": t for i, t in enumerate(tk)})
    df = pd.read_sql(q, engine, params=params)
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return (df.pivot_table(index="date", columns="ticker", values="value",
                           aggfunc="last").sort_index().astype(float))


# ======================================================================
# 2. 대만 월매출 → 신호 생성 (revenue.db)
# ======================================================================
def tw_signals(conn: sqlite3.Connection, company_id: str,
               model: str = "ensemble",
               publish_day: int = 11) -> pd.DataFrame:
    """
    대만 기업 1개의 월별 신호 테이블.

    Returns (index=대상월 M의 월초)
    -------
    publish   : 신호 확정일 = M+1월 publish_day (대만 월매출 공시 마감 다음날)
    yoy       : M월 매출 YoY (%)
    d_yoy     : YoY 가속도 = yoy(M) - yoy(M-1)  (%p)
    surprise  : 실제/모델예측(1개월 선행 basis=M-1, target=M) - 1  (%)
                → 예측 이력이 없는 과거 구간은 NaN
    """
    a = pd.read_sql(
        "SELECT year, month, revenue FROM revenue "
        "WHERE company_id = ? AND revenue IS NOT NULL ORDER BY year, month",
        conn, params=(company_id,))
    if a.empty:
        return pd.DataFrame()
    a["date"] = pd.to_datetime(dict(year=a["year"], month=a["month"], day=1))
    s = a.set_index("date")["revenue"].astype(float)

    yoy = (s / s.shift(12) - 1) * 100
    d_yoy = yoy - yoy.shift(1)

    # 1개월 선행 예측(basis = M-1, target = M) → 서프라이즈
    f = pd.read_sql(
        "SELECT basis_year, basis_month, target_year, target_month, predicted "
        "FROM forecast WHERE company_id = ? AND model = ?",
        conn, params=(company_id, model))
    surprise = pd.Series(np.nan, index=s.index)
    if not f.empty:
        f["basis"] = pd.to_datetime(dict(year=f["basis_year"],
                                         month=f["basis_month"], day=1))
        f["target"] = pd.to_datetime(dict(year=f["target_year"],
                                          month=f["target_month"], day=1))
        one = f[f["target"] == f["basis"] + pd.DateOffset(months=1)]
        one = one.set_index("target")["predicted"].astype(float)
        common = s.index.intersection(one.index)
        surprise.loc[common] = (s.loc[common] / one.loc[common] - 1) * 100

    out = pd.DataFrame({"yoy": yoy, "d_yoy": d_yoy, "surprise": surprise})
    out["publish"] = [d + pd.DateOffset(months=1, days=publish_day - 1)
                      for d in out.index]
    return out.dropna(subset=["yoy"])


# ======================================================================
# 3. T+n 수익률 (거래일 기준, 시장조정)
# ======================================================================
def _next_trading_idx(price_index: pd.DatetimeIndex, date) -> Optional[int]:
    """date 이후(포함) 첫 거래일의 위치. 범위 밖이면 None."""
    i = price_index.searchsorted(pd.Timestamp(date))
    return int(i) if i < len(price_index) else None


def forward_returns(prices: pd.DataFrame, ticker: str,
                    publish_dates: pd.Series,
                    horizons: Iterable[int] = (5, 21, 42, 63),
                    market: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    각 공시일로부터 h거래일 후까지의 수익률(%). market 지정 시 초과수익률.
    horizons 는 거래일 수 (5≈1주, 21≈1개월, 42≈2개월, 63≈3개월).
    추가 컬럼 pre_21: 공시 전 21거래일 수익률 (주가 선행 여부 진단용).
    """
    if ticker not in prices.columns:
        raise KeyError(f"주가 데이터에 {ticker} 없음")
    p = prices[ticker].dropna()
    idx = p.index
    mkt = market.reindex(idx).ffill() if market is not None else None

    rows = {}
    for m_date, pub in publish_dates.items():
        i0 = _next_trading_idx(idx, pub)
        if i0 is None or i0 >= len(idx):
            continue
        base = p.iloc[i0]
        row = {"t0": idx[i0]}
        for h in horizons:
            j = i0 + h
            if j < len(idx) and base > 0:
                r = (p.iloc[j] / base - 1) * 100
                if mkt is not None and mkt.iloc[i0] > 0:
                    r -= (mkt.iloc[j] / mkt.iloc[i0] - 1) * 100
                row[f"T+{h}d"] = r
            else:
                row[f"T+{h}d"] = np.nan
        # 공시 전 21거래일 수익률 (선행 진단)
        k = i0 - 21
        if k >= 0 and p.iloc[k] > 0:
            r = (base / p.iloc[k] - 1) * 100
            if mkt is not None and mkt.iloc[k] > 0:
                r -= (mkt.iloc[i0] / mkt.iloc[k] - 1) * 100
            row["pre_21d"] = r
        else:
            row["pre_21d"] = np.nan
        rows[m_date] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def market_proxy(prices: pd.DataFrame) -> pd.Series:
    """벤치마크 지수가 없을 때: 유니버스 종가의 중위수 지수(시장 프록시)."""
    norm = prices / prices.iloc[0]
    return norm.median(axis=1).rename("market_proxy")


# ======================================================================
# 4. 검증 3종: IC / 분위 / 이벤트 스터디
# ======================================================================
def ic_table(signals: pd.DataFrame, fwd: pd.DataFrame,
             signal_cols: Iterable[str] = ("yoy", "d_yoy", "surprise")
             ) -> pd.DataFrame:
    """신호별 × horizon별 Spearman IC. pre_21d 는 선행성(주가가 먼저 아는지) 진단."""
    hcols = [c for c in fwd.columns if c.startswith("T+") or c == "pre_21d"]
    rows = []
    for sc in signal_cols:
        if sc not in signals.columns:
            continue
        sig = signals[sc]
        for hc in hcols:
            r, p, n = _spearman(sig, fwd[hc])
            rows.append({"signal": sc, "horizon": hc,
                         "IC": round(r, 3) if pd.notna(r) else np.nan,
                         "p_value": round(p, 3) if pd.notna(p) else np.nan,
                         "n": n})
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.pivot(index="signal", columns="horizon",
                        values="IC").join(
              out.groupby("signal")["n"].max().rename("n_obs"))
        order = ["pre_21d"] + sorted(
            [c for c in out.columns if c.startswith("T+")],
            key=lambda x: int(x[2:-1]))
        out = out[[c for c in order if c in out.columns] + ["n_obs"]]
    return out


def quantile_analysis(signals: pd.DataFrame, fwd: pd.DataFrame,
                      signal_col: str = "d_yoy", q: int = 3) -> pd.DataFrame:
    """신호를 q분위로 나눠 분위별 평균 T+n 초과수익률과 적중률(>0 비율)."""
    df = pd.concat([signals[signal_col].rename("sig"), fwd], axis=1).dropna(
        subset=["sig"])
    if len(df) < q * 4:
        print(f"[경고] 표본 {len(df)}개 — 분위 분석 신뢰도 낮음")
    try:
        df["Q"] = pd.qcut(df["sig"], q, labels=[f"Q{i+1}" for i in range(q)],
                          duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    hcols = [c for c in fwd.columns if c.startswith("T+")]
    mean_tbl = df.groupby("Q", observed=True)[hcols].mean().round(2)
    hit_tbl = (df.groupby("Q", observed=True)[hcols]
                 .apply(lambda g: (g > 0).mean() * 100).round(0)
                 .add_suffix(" hit%"))
    out = mean_tbl.join(hit_tbl)
    # 스프레드 (최상위 - 최하위)
    spread = (mean_tbl.iloc[-1] - mean_tbl.iloc[0]).rename("Q_top-Q_bot")
    return pd.concat([out, spread.to_frame().T])


def event_study(prices: pd.DataFrame, ticker: str,
                signals: pd.DataFrame, signal_col: str = "d_yoy",
                window: tuple = (-10, 40), q: int = 3,
                market: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    공시일 전후 누적 초과수익률 경로(day -10 ~ +40)를 신호 분위별 평균으로.
    반환: index=상대거래일, columns=분위 (day 0 = 공시 후 첫 거래일 = 0%)
    """
    p = prices[ticker].dropna()
    idx = p.index
    mkt = market.reindex(idx).ffill() if market is not None else None

    df = signals.dropna(subset=[signal_col]).copy()
    try:
        df["Q"] = pd.qcut(df[signal_col], q,
                          labels=[f"Q{i+1}" for i in range(q)],
                          duplicates="drop")
    except ValueError:
        return pd.DataFrame()

    lo, hi = window
    days = range(lo, hi + 1)
    paths = {}
    for m_date, row in df.iterrows():
        i0 = _next_trading_idx(idx, row["publish"])
        if i0 is None or i0 + hi >= len(idx) or i0 + lo < 0:
            continue
        seg = p.iloc[i0 + lo: i0 + hi + 1].values
        base = p.iloc[i0]
        path = (seg / base - 1) * 100
        if mkt is not None:
            mseg = mkt.iloc[i0 + lo: i0 + hi + 1].values
            path = path - (mseg / mkt.iloc[i0] - 1) * 100
        paths[m_date] = pd.Series(path, index=list(days))
    if not paths:
        return pd.DataFrame()
    P = pd.DataFrame(paths).T
    P["Q"] = df.loc[P.index, "Q"]
    return P.groupby("Q", observed=True).mean().T


# ======================================================================
# 5. 원스톱 실행기
# ======================================================================
def run_pair_study(tw_conn, tw_company_id: str,
                   prices: pd.DataFrame, target_ticker: str,
                   model: str = "ensemble",
                   horizons: Iterable[int] = (5, 21, 42, 63),
                   use_market_adjust: bool = True,
                   benchmark: Optional[str] = None,
                   publish_day: int = 11) -> Dict[str, pd.DataFrame]:
    """
    대만 기업 1개 × 대상 종목 1개 페어 전체 검증.
    benchmark: 주가 wide 안의 벤치마크 컬럼명(예: 지수 ETF). 없으면 중위수 프록시.

    Returns dict: signals / fwd / ic / quantile(신호별) / event(신호별)
    """
    sig = tw_signals(tw_conn, tw_company_id, model=model,
                     publish_day=publish_day)
    if sig.empty:
        raise ValueError(f"{tw_company_id}: 신호 생성 실패 (매출 데이터 없음)")

    mkt = None
    if use_market_adjust:
        mkt = (prices[benchmark].dropna() if benchmark
               else market_proxy(prices))

    fwd = forward_returns(prices, target_ticker, sig["publish"],
                          horizons=horizons, market=mkt)
    common = sig.index.intersection(fwd.index)
    sig, fwd = sig.loc[common], fwd.loc[common]

    res = {"signals": sig, "fwd": fwd, "ic": ic_table(sig, fwd)}
    for sc in ("yoy", "d_yoy", "surprise"):
        if sig[sc].notna().sum() >= 12:
            res[f"quantile_{sc}"] = quantile_analysis(sig, fwd, sc)
            res[f"event_{sc}"] = event_study(prices, target_ticker, sig, sc,
                                             market=mkt)
    return res


def scan_pairs(tw_conn, tw_company_id: str, prices: pd.DataFrame,
               target_tickers: Iterable[str], signal_col: str = "d_yoy",
               horizon: str = "T+21d", model: str = "ensemble",
               use_market_adjust: bool = True) -> pd.DataFrame:
    """여러 대상 종목을 같은 신호로 스캔 → IC 순위표 (사전 지정 페어 검증용)."""
    sig = tw_signals(tw_conn, tw_company_id, model=model)
    mkt = market_proxy(prices) if use_market_adjust else None
    rows = []
    for tk in target_tickers:
        if tk not in prices.columns:
            rows.append({"ticker": tk, "IC": np.nan, "p": np.nan, "n": 0,
                         "note": "주가 없음"})
            continue
        fwd = forward_returns(prices, tk, sig["publish"], market=mkt)
        common = sig.index.intersection(fwd.index)
        r, p, n = _spearman(sig.loc[common, signal_col],
                            fwd.loc[common, horizon])
        rows.append({"ticker": tk, "IC": round(r, 3) if pd.notna(r) else np.nan,
                     "p": round(p, 3) if pd.notna(p) else np.nan,
                     "n": n, "note": ""})
    return (pd.DataFrame(rows)
            .sort_values("IC", ascending=False, na_position="last")
            .reset_index(drop=True))
