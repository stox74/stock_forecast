# -*- coding: utf-8 -*-
"""STD-1 시계열과 대체 데이터 공용 읽기.

family 별 차이(테이블, 키 컬럼, 값 컬럼, 단위 위치, 관측 시각 컬럼, 발표 규칙, vintage 테이블)는
FAMILIES 설정표에만 적는다. key 는 키 컬럼 값을 ':' 로 이은 문자열이다.
  fred RSHPCS / tourism D:100, E:TOTAL / tw_revenue 2330 / amazon_bsr US:B073ZBXSJD / google_trends US:medicube
키 컬럼이 없는 표(증시 수급)는 key 가 'KR' 하나다.

vintage=True: 공표·수집 이력 테이블(_vintage)의 모든 버전을 돌려준다. 각 (key, period_end) 의 첫 버전은
일반 규칙(백필 판정 포함)으로, 이후 버전은 관측 날짜(A)로 available_at 을 준다.
asof.as_of(df, d) 다음 asof.latest_known(df) 를 쓰면 d 시점에 알던 값이 된다.
"""
import logging
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .. import asof, bulkload, db
from ..config import RELEASE_RULES
from ._common import as_list, date_range_params, empty_frame, finalize

log = logging.getLogger(__name__)

TOTAL_KEY = "TOTAL"

FAMILIES: Dict[str, dict] = {
    "fred": {
        "table": "us_fred_data", "keys": ["series_id"], "values": {"value": "value"},
        "freq": {"col": "freq"}, "observed": "first_release_date",
        "rule": "fred", "fallback": {"rule": "fred_collected", "observed": "first_collected_at"},
        "unit": {"info": "us_fred_series_info", "on": "series_id", "unit": "unit", "currency": "currency"},
        "vintage": {"table": "us_fred_data_vintage", "values": {"value": "value"},
                    "observed": "release_date", "where": "`release_date` IS NOT NULL"},
    },
    "kosis": {
        "table": "kr_kosis_data", "keys": ["series_id"], "values": {"value": "value"},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "kosis",
        "unit": {"info": "kr_kosis_series_info", "on": "series_id", "unit": "unit"},
        "vintage": {"table": "kr_kosis_data_vintage", "values": {"value": "value"}, "observed": "collected_at"},
    },
    "komis": {
        "table": "kr_komis_mineral_price", "keys": ["series_id"], "values": {"price": "price"},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "komis",
        "unit": {"info": "kr_komis_mineral_price_info", "on": "series_id", "unit": "unit", "currency": "currency"},
        "vintage": {"table": "kr_komis_mineral_price_vintage", "values": {"price": "price"}, "observed": "collected_at"},
    },
    "airline": {
        "table": "kr_airline_traffic", "keys": ["series_id"], "values": {"value": "value"},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "airline",
        "unit": {"info": "kr_airline_traffic_info", "on": "series_id", "unit": "unit"},
        "vintage": {"table": "kr_airline_traffic_vintage", "values": {"value": "value"}, "observed": "collected_at"},
    },
    "tourism": {
        "table": "kr_tourism_visitors_monthly", "keys": ["direction", "nat_code"], "values": {"visitors": "visitors"},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "tourism",
        "unit": {"col": "unit"},
        # '방향:TOTAL' 키는 kr_tourism_country_info.exclude_from_total = 0 인 국가 합계
        "total": {"info": "kr_tourism_country_info", "on": "nat_code", "exclude_col": "exclude_from_total"},
        "vintage": {"table": "kr_tourism_visitors_monthly_vintage", "values": {"visitors": "visitors"},
                    "observed": "collected_at"},
    },
    "tw_revenue": {
        "table": "tw_revenue_monthly", "keys": ["ticker"], "values": {"revenue": "revenue"},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "tw_revenue",
        "unit": {"col": "unit", "currency_col": "currency"},
        "vintage": {"table": "tw_revenue_monthly_vintage", "values": {"revenue": "revenue"}, "observed": "collected_at"},
    },
    "credit_balance": {
        "table": "kr_stock_credit_balance_daily", "keys": [],
        "values": {c: c for c in ["credit_loan_total", "credit_loan_kospi", "credit_loan_kosdaq",
                                  "stock_lending_total", "stock_lending_kospi", "stock_lending_kosdaq",
                                  "subscription_loan", "securities_collateral_loan"]},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "market_daily",
        "unit": {"col": "unit", "currency_col": "currency"},
        "vintage": {"table": "kr_stock_credit_balance_daily_vintage", "observed": "collected_at"},
    },
    "market_funds": {
        "table": "kr_stock_market_funds_daily", "keys": [],
        "values": {c: c for c in ["investor_deposit", "derivatives_deposit", "retail_rp_balance",
                                  "margin_receivable", "forced_sale_amount", "forced_sale_ratio_pct"]},
        "freq": {"col": "freq"}, "observed": "first_collected_at", "rule": "market_daily",
        "unit": {"col": "unit", "currency_col": "currency", "override": {"forced_sale_ratio_pct": ("pct", None)}},
        "vintage": {"table": "kr_stock_market_funds_daily_vintage", "observed": "collected_at"},
    },
    "amazon_bsr": {
        "table": "amazon_bsr_daily", "keys": ["domain", "asin"], "period": "date",
        "values": {"bsr_root": "bsr_root", "bsr_sub": "bsr_sub", "price_buybox": "price_buybox",
                   "review_count": "review_count", "rating": "rating", "is_oos": "is_oos"},
        "default_items": ["bsr_root"],
        "freq": {"const": "D"}, "observed": "created_at", "rule": "amazon_bsr",
        "unit": {"const": {"bsr_root": ("rank", None), "bsr_sub": ("rank", None), "price_buybox": ("USD", "USD"),
                           "review_count": ("count", None), "rating": ("stars", None), "is_oos": ("flag", None)}},
        "note": {"col": "is_ffilled", "text": "ffilled"},
    },
    "google_trends": {
        "table": "apr_us_google_trends", "keys": ["geo", "keyword"], "period": "date",
        "values": {"trend_raw": "value_raw", "trend_anchored": "value_anchored"},
        "default_items": ["trend_anchored"],
        "freq": {"const": "W"}, "observed": None, "rule": "google_trends",
        "unit": {"const": {"trend_raw": ("index", None), "trend_anchored": ("index", None)}},
        "latest_by": "collected_at",   # 같은 날짜가 여러 번 수집됐으면 가장 최근 수집분
    },
}


def _key_where(cfg: dict, keys: List[str], total_dirs: List[str]):
    """키 목록을 WHERE 조건과 파라미터로. TOTAL 키는 해당 방향 전체를 읽는다."""
    kcols = cfg["keys"]
    if not kcols:
        return "1=1", {}
    conds, params = [], {}
    for i, k in enumerate(keys):
        parts = k.split(":")
        if len(parts) != len(kcols):
            raise ValueError(f"key {k!r} must have {len(kcols)} part(s) joined by ':' ({kcols})")
        sub = []
        for j, (c, v) in enumerate(zip(kcols, parts)):
            pname = f"k{i}_{j}"
            sub.append(f"`{c}` = :{pname}")
            params[pname] = v
        conds.append("(" + " AND ".join(sub) + ")")
    for i, d in enumerate(total_dirs):
        conds.append(f"(`{kcols[0]}` = :td{i})")
        params[f"td{i}"] = d
    return "(" + " OR ".join(conds) + ")", params


def _units(cfg: dict, df: pd.DataFrame, raw_keys: pd.DataFrame) -> pd.DataFrame:
    u = cfg.get("unit", {})
    if "const" in u:
        df["unit"] = df["item"].map(lambda i: u["const"].get(i, (None, None))[0])
        df["currency"] = df["item"].map(lambda i: u["const"].get(i, (None, None))[1])
    elif "info" in u:
        on = u["on"]
        cols = [f"`{on}`"] + [f"`{u[c]}` AS `{c}`" for c in ("unit", "currency") if c in u]
        ids = sorted(raw_keys[on].astype(str).unique())
        frag, p = db.in_clause("u", ids)
        info = db.read_sql(f"SELECT {', '.join(cols)} FROM `{u['info']}` WHERE `{on}` IN {frag}", p)
        info[on] = info[on].astype(str)
        m = raw_keys[[on]].astype(str).merge(info, on=on, how="left")
        df["unit"] = m["unit"].values if "unit" in m else None
        df["currency"] = m["currency"].values if "currency" in m else None
    else:
        df["unit"] = raw_keys["_unit"].values if "_unit" in raw_keys else None
        df["currency"] = raw_keys["_currency"].values if "_currency" in raw_keys else None
    for item, (un, cu) in u.get("override", {}).items():
        m = df["item"] == item
        df.loc[m, "unit"] = un
        df.loc[m, "currency"] = cu
    return df


def _select_cols(cfg: dict, items: List[str], vintage: bool) -> List[str]:
    period = cfg.get("period", "period_end")
    vals = (cfg.get("vintage", {}).get("values") or cfg["values"]) if vintage else cfg["values"]
    cols = list(cfg["keys"]) + [period] + [vals[i] for i in items]
    if not vintage:
        if "col" in cfg["freq"]:
            cols.append(cfg["freq"]["col"])
        for c in [cfg.get("observed"), (cfg.get("fallback") or {}).get("observed"), cfg.get("latest_by"),
                  (cfg.get("note") or {}).get("col"), cfg.get("unit", {}).get("col"),
                  cfg.get("unit", {}).get("currency_col")]:
            if c and c not in cols:
                cols.append(c)
    else:
        cols.append(cfg["vintage"]["observed"])
    return cols


def read_series(family: str, keys: Optional[Iterable[str]] = None, start=None, end=None,
                vintage: bool = False, items: Optional[Iterable[str]] = None) -> pd.DataFrame:
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; allowed {list(FAMILIES)}")
    cfg = FAMILIES[family]
    kcols = cfg["keys"]
    period = cfg.get("period", "period_end")
    items = as_list(items) or cfg.get("default_items") or list(cfg["values"])
    bad = [i for i in items if i not in cfg["values"]]
    if bad:
        raise ValueError(f"unknown item(s) for {family}: {bad}")
    if vintage and "vintage" not in cfg:
        raise ValueError(f"family {family!r} has no vintage table")

    keys = [str(k) for k in as_list(keys)]
    if kcols and not keys:
        raise ValueError(f"family {family!r} needs keys")
    total_dirs = []
    if cfg.get("total"):
        total_dirs = [k.split(":")[0] for k in keys if k.endswith(":" + TOTAL_KEY)]
        keys = [k for k in keys if not k.endswith(":" + TOTAL_KEY)]

    table = cfg["vintage"]["table"] if vintage else cfg["table"]
    cols = _select_cols(cfg, items, vintage)
    where, params = _key_where(cfg, keys, total_dirs)
    extra = f" AND {cfg['vintage']['where']}" if vintage and cfg["vintage"].get("where") else ""
    s, e = date_range_params(start, end)
    raw = db.read_sql(
        f"SELECT {', '.join('`%s`' % c for c in cols)} FROM `{table}` "
        f"WHERE {where} AND `{period}` BETWEEN :s AND :e{extra}",
        {**params, "s": s, "e": e},
    )
    if raw.empty:
        return empty_frame()
    for c in kcols:
        raw[c] = raw[c].astype(str)
    raw[period] = pd.to_datetime(raw[period])

    if cfg.get("latest_by") and not vintage:
        raw = raw.sort_values(cfg["latest_by"]).drop_duplicates(kcols + [period], keep="last")

    # freq
    if "const" in cfg["freq"]:
        raw["_freq"] = cfg["freq"]["const"]
    elif not vintage:
        raw["_freq"] = raw[cfg["freq"]["col"]].astype(str)
    else:
        raw["_freq"] = _freq_map(cfg, raw)

    # 국가 합계 키
    if total_dirs:
        raw = _add_totals(cfg, raw, keys, total_dirs, items, vintage)

    raw["_key"] = raw[kcols].astype(str).agg(":".join, axis=1) if kcols else "KR"
    vals = (cfg.get("vintage", {}).get("values") or cfg["values"]) if vintage else cfg["values"]
    u = cfg.get("unit", {})
    if "col" in u and not vintage:
        raw["_unit"] = raw[u["col"]]
    if "currency_col" in u and not vintage:
        raw["_currency"] = raw[u["currency_col"]]

    frames = []
    for it in items:
        part = pd.DataFrame({
            "key": raw["_key"].values, "period_end": raw[period].values, "freq": raw["_freq"].values,
            "item": it, "value": pd.to_numeric(raw[vals[it]], errors="coerce").astype("float64").values,
        })
        part = _units(cfg, part, raw.reset_index(drop=True))
        part["source"] = table
        part["_obs"] = raw[cfg["vintage"]["observed"]].values if vintage else (
            raw[cfg["observed"]].values if cfg.get("observed") else pd.NaT)
        if cfg.get("fallback") and not vintage:
            part["_obs2"] = raw[cfg["fallback"]["observed"]].values
        if cfg.get("note") and not vintage:
            part["_note"] = np.where(raw[cfg["note"]["col"]].fillna(0).astype(int).values == 1, cfg["note"]["text"], "")
        frames.append(part)
    long = pd.concat(frames, ignore_index=True)
    if vintage:
        bulk = bulkload.bulk_dates(table, cfg["vintage"]["observed"], period)
        long = _stamp_vintage(cfg, long, bulk)
    else:
        bulk = bulkload.bulk_dates(table, cfg["observed"], period) if cfg.get("observed") else []
        bulk2 = bulkload.bulk_dates(table, cfg["fallback"]["observed"], period) if cfg.get("fallback") else []
        long = _stamp_current(cfg, long, bulk, bulk2)
    return finalize(long)


def _freq_map(cfg: dict, raw: pd.DataFrame) -> pd.Series:
    kcols = cfg["keys"]
    q = f"SELECT DISTINCT {', '.join('`%s`' % c for c in kcols + [cfg['freq']['col']])} FROM `{cfg['table']}`"
    if kcols:
        sub = raw[kcols].drop_duplicates()
        where, p = _key_where(cfg, sub.astype(str).agg(":".join, axis=1).tolist(), [])
        fm = db.read_sql(q + f" WHERE {where}", p)
    else:
        fm = db.read_sql(q)
    if kcols:
        for c in kcols:
            fm[c] = fm[c].astype(str)
        m = raw[kcols].merge(fm, on=kcols, how="left")
        return m[cfg["freq"]["col"]].astype(str).values
    return fm[cfg["freq"]["col"]].iloc[0]


def _add_totals(cfg, raw, keys, total_dirs, items, vintage):
    tot = cfg["total"]
    info = db.read_sql(f"SELECT `{tot['on']}`, `{tot['exclude_col']}` FROM `{tot['info']}`")
    excl = set(info.loc[info[tot["exclude_col"]].fillna(0).astype(int) == 1, tot["on"]].astype(str))
    dcol, ncol = cfg["keys"]
    period = cfg.get("period", "period_end")
    vals = (cfg.get("vintage", {}).get("values") or cfg["values"]) if vintage else cfg["values"]
    obs_col = cfg["vintage"]["observed"] if vintage else cfg["observed"]
    base = raw[raw[dcol].isin(total_dirs) & ~raw[ncol].isin(excl)]
    agg = {vals[i]: "sum" for i in items}
    agg[obs_col] = "max"         # 합계는 마지막 구성 국가가 들어온 시점에 알 수 있다
    agg["_freq"] = "first"
    for c in [cfg.get("unit", {}).get("col")]:
        if c and c in base:
            agg[c] = "first"
    t = base.groupby([dcol, period], as_index=False).agg(agg)
    t[ncol] = TOTAL_KEY
    wanted = set(tuple(k.split(":")) for k in keys)
    single = raw[[ (d, n) in wanted for d, n in zip(raw[dcol], raw[ncol]) ]]
    return pd.concat([single, t], ignore_index=True)


def _stamp_current(cfg: dict, long: pd.DataFrame, bulk=(), bulk2=()) -> pd.DataFrame:
    rule = cfg["rule"]
    needs_freq = "by_freq" in RELEASE_RULES[rule]["lag"]
    freq = long["freq"] if needs_freq else None
    r = asof.resolve(long["period_end"], rule, observed=long["_obs"] if cfg.get("observed") else None, freq=freq,
                     bulk_dates=bulk)
    if cfg.get("fallback"):
        # 공표일이 없는 행은 최초 수집 시각 기반 규칙으로
        miss = pd.to_datetime(long["_obs"]).isna().values
        if miss.any():
            fb = cfg["fallback"]
            r2 = asof.resolve(long.loc[miss, "period_end"], fb["rule"], observed=long.loc[miss, "_obs2"],
                              freq=long.loc[miss, "freq"] if needs_freq else None, bulk_dates=bulk2)
            for c in r.columns:
                r.loc[miss, c] = r2[c].values
    out = long.reset_index(drop=True).copy()
    for c in r.columns:
        out[c] = r[c].values
    if "_note" in out:
        out["asof_note"] = [";".join(x for x in (a, b) if x) for a, b in zip(out["asof_note"], out["_note"])]
    return out.drop(columns=[c for c in out.columns if c.startswith("_")])


def _stamp_vintage(cfg: dict, long: pd.DataFrame, bulk=()) -> pd.DataFrame:
    rule = RELEASE_RULES[cfg["rule"]]
    long = long.copy()
    long["_obs"] = pd.to_datetime(long["_obs"]).dt.normalize()
    long = long.sort_values(["key", "item", "period_end", "_obs"]).reset_index(drop=True)
    first = ~long.duplicated(["key", "item", "period_end"], keep="first")
    needs_freq = "by_freq" in rule["lag"]
    r = asof.resolve(long["period_end"], cfg["rule"], observed=long["_obs"],
                     freq=long["freq"] if needs_freq else None, bulk_dates=bulk)
    # 이후 버전은 실제 관측(공표·수집) 날짜. 일괄 적재일에 들어온 개정은 B + bulk_load
    later = ~first
    r.loc[later, "available_at"] = long.loc[later, "_obs"].values
    r.loc[later, "asof_quality"] = rule.get("observed_grade", rule["rule_grade"])
    r.loc[later, "asof_note"] = ""
    if len(bulk):
        later_bulk = later & long["_obs"].isin(pd.DatetimeIndex(bulk))
        r.loc[later_bulk, "asof_quality"] = asof.BULK_GRADE
        r.loc[later_bulk, "asof_note"] = asof.BULK_NOTE
    # 개정치가 첫 버전보다 이르게 잡히는 일이 없도록
    r["available_at"] = r.groupby([long["key"], long["item"], long["period_end"]])["available_at"].cummax()
    for c in r.columns:
        long[c] = r[c].values
    long["asof_note"] = np.where(first, long["asof_note"], (long["asof_note"].astype(str) + ";revision").str.strip(";"))
    return long.drop(columns=[c for c in long.columns if c.startswith("_")])
