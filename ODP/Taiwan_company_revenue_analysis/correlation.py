# -*- coding: utf-8 -*-
"""
correlation.py — 대만 분기 YoY vs 한국/미국 분기 YoY 상관계수 (Python 3.9)

대만 월별 실적을 3개월(캘린더 분기)로 groupby → 분기 YoY growth 를 만든 뒤
한국/미국 기업들의 분기 매출 YoY growth 와 Pearson 상관계수를 계산한다.
YoY growth 기준 비교이므로 통화/단위(NTD·KRW·USD)가 달라도 무방하다.
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from analysis_config import MIN_OVERLAP_QUARTERS


def yoy_from_quarterly(q_rev: pd.DataFrame) -> pd.DataFrame:
    """분기 매출 wide → 분기 YoY growth (%)."""
    return q_rev.pct_change(4) * 100.0


def corr_table(tw_yoy: pd.DataFrame, other_yoy: pd.DataFrame,
               market: str,
               names: Optional[Dict[str, str]] = None,
               min_overlap: int = MIN_OVERLAP_QUARTERS,
               max_lag: int = 0) -> pd.DataFrame:
    """
    대만 전 기업 × 상대 시장 전 기업 상관계수 테이블 (long format).

    Parameters
    ----------
    tw_yoy, other_yoy : index=PeriodIndex('Q') 의 YoY wide
    max_lag : 0이면 동일 분기만. k>0 이면 lag 0..k 중
              '대만이 상대를 k분기 선행'하는 경우까지 검사해 최적 lag 도 보고.
              (lag=1 → 대만 t분기 YoY vs 상대 t+1분기 YoY)

    Returns
    -------
    DataFrame [tw_id, market, ticker, name, corr, lag, n_obs]
    """
    idx = tw_yoy.index.union(other_yoy.index)
    tw = tw_yoy.reindex(idx)
    ot = other_yoy.reindex(idx)

    rows = []
    for tw_id in tw.columns:
        x0 = tw[tw_id]
        for tk in ot.columns:
            y = ot[tk]
            best = None
            for lag in range(0, max_lag + 1):
                x = x0.shift(lag)  # 대만이 lag분기 선행
                pair = pd.concat([x, y], axis=1).dropna()
                n = len(pair)
                if n < min_overlap:
                    continue
                # 분산 0 방어
                if pair.iloc[:, 0].std() == 0 or pair.iloc[:, 1].std() == 0:
                    continue
                c = pair.iloc[:, 0].corr(pair.iloc[:, 1])
                if best is None or abs(c) > abs(best[0]):
                    best = (c, lag, n)
            if best is not None:
                rows.append({
                    "tw_id": tw_id, "market": market, "ticker": tk,
                    "name": (names or {}).get(tk, tk),
                    "corr": round(float(best[0]), 4),
                    "lag": best[1], "n_obs": best[2],
                })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["tw_id", "corr"], ascending=[True, False])
    return out


def top_correlated(tw_company_id: str,
                   tw_yoy: pd.DataFrame,
                   kr_yoy: Optional[pd.DataFrame] = None,
                   us_yoy: Optional[pd.DataFrame] = None,
                   kr_names: Optional[dict] = None,
                   us_names: Optional[dict] = None,
                   n: int = 10,
                   min_corr: Optional[float] = None,
                   min_overlap: int = MIN_OVERLAP_QUARTERS,
                   max_lag: int = 0) -> pd.DataFrame:
    """
    ★ 요구사항 5) 대만 특정 기업 코드 입력 → 상관계수 상위 n개 한국/미국 기업 추출.

    Parameters
    ----------
    tw_company_id : 대만 종목코드 (예: '2330')
    n             : 시장별 상위 n개
    min_corr      : 지정 시 corr >= min_corr 만 (예: 0.75)

    Returns
    -------
    DataFrame [market, ticker, name, corr, lag, n_obs] — corr 내림차순
    """
    if tw_company_id not in tw_yoy.columns:
        raise KeyError(f"대만 종목코드 {tw_company_id} 가 분기 YoY 데이터에 없습니다.")
    one = tw_yoy[[tw_company_id]]

    parts = []
    if kr_yoy is not None and not kr_yoy.empty:
        parts.append(corr_table(one, kr_yoy, "KR", kr_names,
                                min_overlap, max_lag))
    if us_yoy is not None and not us_yoy.empty:
        parts.append(corr_table(one, us_yoy, "US", us_names,
                                min_overlap, max_lag))
    if not parts:
        return pd.DataFrame()

    res = pd.concat(parts, ignore_index=True)
    if min_corr is not None:
        res = res[res["corr"] >= min_corr]
    res = (res.sort_values("corr", ascending=False)
              .groupby("market", group_keys=False).head(n)
              .sort_values("corr", ascending=False)
              .reset_index(drop=True))
    return res.drop(columns=["tw_id"])


def top_correlated_split(tw_company_id: str,
                         tw_yoy: pd.DataFrame,
                         kr_yoy: Optional[pd.DataFrame] = None,
                         us_yoy: Optional[pd.DataFrame] = None,
                         kr_names: Optional[dict] = None,
                         us_names: Optional[dict] = None,
                         n: int = 10,
                         min_corr: Optional[float] = None,
                         min_overlap: int = MIN_OVERLAP_QUARTERS,
                         max_lag: int = 0):
    """
    ★ 상관 상위 기업을 한국/미국 별도 DataFrame 으로 분리 반환.

    Returns
    -------
    (kr_df, us_df) : 각각 [ticker, name, corr, lag, n_obs], corr 내림차순 상위 n개
    """
    both = top_correlated(tw_company_id, tw_yoy, kr_yoy, us_yoy,
                          kr_names=kr_names, us_names=us_names,
                          n=n, min_corr=min_corr,
                          min_overlap=min_overlap, max_lag=max_lag)
    cols = ["ticker", "name", "corr", "lag", "n_obs"]
    if both.empty:
        empty = pd.DataFrame(columns=cols)
        return empty.copy(), empty.copy()
    kr_df = (both[both["market"] == "KR"][cols]
             .reset_index(drop=True))
    us_df = (both[both["market"] == "US"][cols]
             .reset_index(drop=True))
    return kr_df, us_df


# ----------------------------------------------------------------------
# 임의의 두 기업(대만/한국/미국) 페어 상관분석 + 근거 데이터
# ----------------------------------------------------------------------
def _find_market(ticker: str, frames: dict) -> str:
    """ticker 가 속한 시장('TW'/'KR'/'US') 자동 판별."""
    hits = [m for m, (rev, _) in frames.items()
            if rev is not None and ticker in rev.columns]
    if not hits:
        raise KeyError(f"'{ticker}' 를 어느 시장 데이터에서도 찾을 수 없습니다. "
                       "(대만: '2330' / 한국: 'A005930' / 미국: 'NVDA' 형식)")
    if len(hits) > 1:
        print(f"[주의] '{ticker}' 가 복수 시장에 존재: {hits} → {hits[0]} 사용")
    return hits[0]


def pair_correlation(ticker1: str, ticker2: str, frames: dict,
                     lag: int = 0, max_lag: int = 0,
                     min_overlap: int = 8):
    """
    ★ 두 ticker 를 입력하면 매출 상관계수와 '근거 데이터'를 함께 반환.

    Parameters
    ----------
    ticker1, ticker2 : 대만/한국/미국 어느 시장이든 가능 (시장 자동 판별)
    frames : {"TW": (분기매출 wide, 분기YoY wide), "KR": (...), "US": (...)}
             — 노트북에서 FRAMES 변수로 미리 구성해 둔다.
    lag    : ticker1 이 ticker2 를 lag 분기 선행한다고 가정한 매핑
             (ticker1 의 t-lag 분기 YoY ↔ ticker2 의 t분기 YoY)
    max_lag: >0 이면 lag 0..max_lag 를 모두 검사해 |corr| 최대 lag 자동 선택
    min_overlap : 최소 겹침 분기 수 (미달 시 경고)

    Returns
    -------
    summary : 1행 DataFrame [ticker1, market1, ticker2, market2,
                             corr, lag, n_obs, first_q, last_q]
    detail  : 근거 데이터 DataFrame (index=분기)
              [rev_<t1>, rev_<t2>, yoy_<t1>, yoy_<t2>(lag 적용시 시프트 표시),
               used(상관계산 포함 여부)]
    """
    m1 = _find_market(ticker1, frames)
    m2 = _find_market(ticker2, frames)
    rev1, yoy1 = frames[m1][0][ticker1], frames[m1][1][ticker1]
    rev2, yoy2 = frames[m2][0][ticker2], frames[m2][1][ticker2]

    idx = yoy1.index.union(yoy2.index)
    y1, y2 = yoy1.reindex(idx), yoy2.reindex(idx)

    # lag 선택 (max_lag 지정 시 자동 탐색)
    lags = range(0, max_lag + 1) if max_lag > 0 else [lag]
    best = None
    for L in lags:
        pair = pd.concat([y1.shift(L), y2], axis=1).dropna()
        if len(pair) < 3 or pair.iloc[:, 0].std() == 0 or pair.iloc[:, 1].std() == 0:
            continue
        c = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if best is None or abs(c) > abs(best[0]):
            best = (c, L, pair)
    if best is None:
        raise ValueError("겹치는 유효 표본이 부족해 상관계수를 계산할 수 없습니다.")
    corr, use_lag, pair = best
    n_obs = len(pair)
    if n_obs < min_overlap:
        print(f"[경고] 겹침 표본 {n_obs}분기 < {min_overlap} — "
              "상관계수의 신뢰도가 낮습니다.")

    # 근거 데이터: 매출 원값 + YoY (+ lag 시프트된 t1 YoY) + 사용 여부
    c1 = f"yoy_{ticker1}" + (f"(t-{use_lag})" if use_lag else "")
    detail = pd.DataFrame({
        f"rev_{ticker1}": rev1.reindex(idx),
        f"rev_{ticker2}": rev2.reindex(idx),
        c1: y1.shift(use_lag),
        f"yoy_{ticker2}": y2,
    })
    detail["used"] = detail[[c1, f"yoy_{ticker2}"]].notna().all(axis=1)
    detail = detail[detail.drop(columns="used").notna().any(axis=1)]

    summary = pd.DataFrame([{
        "ticker1": ticker1, "market1": m1,
        "ticker2": ticker2, "market2": m2,
        "corr": round(float(corr), 4), "lag": use_lag, "n_obs": n_obs,
        "first_q": str(pair.index.min()), "last_q": str(pair.index.max()),
    }])
    return summary, detail
