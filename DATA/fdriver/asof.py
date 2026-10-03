# -*- coding: utf-8 -*-
"""발표 시점(as-of) 처리.

available_at 규약
- 날짜(시각 없음)이며 "그날 장 마감까지 알 수 있었던 값"을 뜻한다.
- 관측 시각(first_collected_at 등)은 날짜로 내림한다. 23시에 수집됐어도 그날 알 수 있었던 것은 사실이므로.
- as_of(df, d) 는 available_at <= d 인 행만 남긴다. 백테스트에서 d 일 종가 판단에 쓰면 경계선이므로
  매매는 d 다음 영업일 기준으로 하는 것을 권장한다.

규칙 날짜는 config.RELEASE_RULES 에서만 정의한다.
"""
from typing import Optional, Union

import numpy as np
import pandas as pd

from .config import RELEASE_RULES, ASOF_GRADES

DateLike = Union[str, pd.Timestamp, "np.datetime64"]


# 거래일 달력 (price.trading_calendar 로 등록). 등록되면 bdays 규칙은 이 달력으로 센다.
_TRADING_DAYS: Optional[np.ndarray] = None


def set_trading_calendar(dates) -> None:
    """거래일 목록을 등록한다. None 이면 해제(주말만 제외하는 BDay 사용)."""
    global _TRADING_DAYS
    if dates is None:
        _TRADING_DAYS = None
        return
    d = pd.to_datetime(pd.Series(dates)).dt.normalize().drop_duplicates().sort_values()
    _TRADING_DAYS = d.to_numpy(dtype="datetime64[ns]")


def _add_bdays(period_end: pd.Series, n: int) -> pd.Series:
    """기간말 + n 거래일. 달력 범위 밖이면 BDay로 대신한다."""
    fallback = period_end + pd.offsets.BDay(n)
    if _TRADING_DAYS is None or len(_TRADING_DAYS) == 0:
        return fallback
    pe = period_end.to_numpy(dtype="datetime64[ns]")
    # period_end 보다 큰 첫 거래일이 +1, 그 다음이 +2 ...
    pos = np.searchsorted(_TRADING_DAYS, pe, side="right") + (n - 1)
    in_range = (pe >= _TRADING_DAYS[0]) & (pos < len(_TRADING_DAYS))
    picked = _TRADING_DAYS[np.clip(pos, 0, len(_TRADING_DAYS) - 1)]
    out = pd.Series(np.where(in_range, picked, fallback.to_numpy(dtype="datetime64[ns]")), index=period_end.index)
    return out.astype("datetime64[ns]")


def _to_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.normalize()


def _apply_lag(period_end: pd.Series, lag: dict) -> pd.Series:
    if "days" in lag:
        return period_end + pd.to_timedelta(lag["days"], unit="D")
    if "bdays" in lag:
        return _add_bdays(period_end, lag["bdays"])
    if "next_month_day" in lag:
        first_next = period_end + pd.offsets.MonthBegin(1)
        # 기간말이 1일인 경우에도 MonthBegin(1)은 다음 달 1일을 준다
        return first_next + pd.to_timedelta(lag["next_month_day"] - 1, unit="D")
    raise ValueError(f"unknown lag spec: {lag}")


def rule_dates(period_end: pd.Series, rule_name: str,
               freq: Optional[Union[str, pd.Series]] = None) -> pd.Series:
    """규칙으로 계산한 발표일 (RELEASE_RULES[rule_name]['lag'])."""
    rule = RELEASE_RULES[rule_name]
    lag = rule["lag"]
    pe = _to_dates(pd.Series(period_end))
    if "by_freq" not in lag:
        return _apply_lag(pe, lag)

    if freq is None:
        raise ValueError(f"rule '{rule_name}' needs freq")
    fq = pd.Series(freq, index=pe.index) if np.isscalar(freq) else pd.Series(freq).set_axis(pe.index)
    out = pd.Series(pd.NaT, index=pe.index, dtype="datetime64[ns]")
    for f in fq.dropna().unique():
        if f not in lag["by_freq"]:
            raise ValueError(f"rule '{rule_name}' has no lag for freq '{f}'")
        m = fq == f
        out.loc[m] = _apply_lag(pe[m], lag["by_freq"][f])
    if fq.isna().any():
        raise ValueError(f"rule '{rule_name}': freq is missing for some rows")
    return out


BULK_NOTE = "bulk_load"
BULK_GRADE = "B"


def resolve(period_end: pd.Series, rule_name: str,
            observed: Optional[pd.Series] = None,
            freq: Optional[Union[str, pd.Series]] = None,
            bulk_dates=None) -> pd.DataFrame:
    """기간말·관측일로 available_at, asof_quality, asof_note 를 정한다.

    - 관측일이 없거나(NaT) 백필로 판정되면 규칙 날짜 + rule_grade
    - 그 외에는 관측일 + observed_grade
    - 단, 관측일이 bulk_dates(일괄 적재일)에 있으면 관측일 + B, asof_note = 'bulk_load'
    """
    rule = RELEASE_RULES[rule_name]
    pe = _to_dates(pd.Series(period_end)).reset_index(drop=True)
    rd = rule_dates(pe, rule_name, None if freq is None else
                    (freq if np.isscalar(freq) else pd.Series(freq).reset_index(drop=True)))

    avail = rd.copy()
    grade = pd.Series(rule["rule_grade"], index=pe.index, dtype=object)
    kind = rule.get("observed")

    if kind == "period_end":
        avail = pe.copy()
        grade[:] = rule["observed_grade"]
    elif kind is not None and observed is not None:
        obs = _to_dates(pd.Series(observed)).reset_index(drop=True)
        ok = obs.notna() & (obs >= pe)
        bf = rule.get("backfill")
        if bf is not None:
            ref = rd if bf["ref"] == "rule" else pe
            ok &= obs <= ref + pd.to_timedelta(bf["days"], unit="D")
        avail = avail.where(~ok, obs)
        grade = grade.where(~ok, rule["observed_grade"])
        is_bulk = pd.Series(False, index=pe.index)
        if bulk_dates is not None and len(bulk_dates):
            bd = pd.DatetimeIndex(pd.to_datetime(list(bulk_dates))).normalize()
            is_bulk = ok & obs.isin(bd)
            grade = grade.where(~is_bulk, BULK_GRADE)

    note = pd.Series(rule.get("note", ""), index=pe.index, dtype=object)
    if kind not in (None, "period_end") and observed is not None:
        note = note.where(~is_bulk, (note + ";" + BULK_NOTE).str.strip(";"))
    return pd.DataFrame({"available_at": avail, "asof_quality": grade, "asof_note": note})


def stamp(df: pd.DataFrame, rule_name: str, period_col: str = "period_end",
          freq_col: Optional[str] = "freq", observed_col: Optional[str] = None,
          bulk_dates=None) -> pd.DataFrame:
    """df 에 available_at, asof_quality, asof_note 컬럼을 붙여 돌려준다 (원본 df 는 그대로)."""
    rule = RELEASE_RULES[rule_name]
    out = df.copy()
    freq = None
    if "by_freq" in rule["lag"]:
        if freq_col is None or freq_col not in out.columns:
            raise ValueError(f"rule '{rule_name}' needs column '{freq_col}'")
        freq = out[freq_col]
    if observed_col is None and rule.get("observed") not in (None, "period_end"):
        observed_col = rule["observed"]
    observed = out[observed_col] if observed_col and observed_col in out.columns else None

    r = resolve(out[period_col], rule_name, observed=observed, freq=freq, bulk_dates=bulk_dates)
    r.index = out.index
    for c in r.columns:
        out[c] = r[c]
    return out


def as_of(df: pd.DataFrame, date: DateLike, col: str = "available_at") -> pd.DataFrame:
    """date 장 마감까지 알 수 있었던 행만 남긴다. available_at 이 비어 있으면 제외."""
    if col not in df.columns:
        raise KeyError(f"'{col}' column is required for as_of()")
    d = pd.Timestamp(date).normalize()
    av = pd.to_datetime(df[col], errors="coerce")
    return df.loc[av.notna() & (av <= d)].copy()


def quality_report(df: pd.DataFrame, by: str = "key") -> pd.DataFrame:
    """그룹별 asof_quality 등급 비율과 행 수."""
    if df.empty:
        return pd.DataFrame(columns=[by, *ASOF_GRADES, "n"])
    ct = pd.crosstab(df[by], df["asof_quality"], normalize="index")
    for g in ASOF_GRADES:
        if g not in ct.columns:
            ct[g] = 0.0
    ct = ct[list(ASOF_GRADES)].round(4)
    ct["n"] = df.groupby(by).size()
    return ct.reset_index()


def latest_known(df: pd.DataFrame, by=("key", "item", "period_end")) -> pd.DataFrame:
    """vintage 가 여러 개인 표에서 (key, item, period_end) 별로 가장 늦게 알려진 값만 남긴다.
    보통 as_of(df, d) 다음에 써서 'd 시점에 알던 최신 값'을 만든다."""
    if df.empty:
        return df.copy()
    order = df.sort_values(list(by) + ["available_at"])
    return order.drop_duplicates(list(by), keep="last").reset_index(drop=True)
