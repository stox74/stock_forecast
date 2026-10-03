# -*- coding: utf-8 -*-
"""매출: DART V3(2025~) -> DART V2(2015~) -> DG(2009~) 3단 원천 (결정 D2).

분기 단독값 규칙 (사전 조사에서 삼성전자로 검증)
- DART 의 1분기·반기·3분기보고서 thstrm_amount 는 해당 3개월 값, 사업보고서는 연간 값
- V3 는 누적(thstrm_add_amount)도 있어서, 3개월 값이 누적과 같게 적힌 보고서를 찾아 누적 차분으로 바로잡는다
- Q4 = 연간 - (Q1+Q2+Q3). 세 분기가 다 없으면 연간 - 3분기 누적(V3)
DG 는 이미 분기 단독값이며 단위가 천원이라 x1000, 날짜는 분기 마지막 영업일이라 달력 분기말로 맞춘다.

발표 시점: report_date 는 기간말이라 쓰지 않는다 (D3). Q1~Q3 는 기간말 + 45일, Q4(사업보고서) 는 + 90일, 모두 C.
연결/별도 (D4): consolidated 는 V3 CFS 우선·없으면 OFS, V2·DG 는 연결로 간주. separate 는 V3 OFS 만.
결산월이 12월이 아닌 기업은 DART 연도·분기 대응이 확인되지 않아 DG 만 쓰고 경고한다.
"""
import logging
from typing import Iterable, Optional, Tuple

import numpy as np
import pandas as pd

from .. import asof, db
from ._common import as_list, date_range_params, empty_frame, finalize, require_str_codes

log = logging.getLogger(__name__)

SRC_V3 = "korea_fs_data_from_DART_V3"
SRC_V2 = "korea_fs_data_from_DART_V2"
SRC_DG = "korea_fs_data_from_DG"
SOURCE_PRIORITY = [SRC_V3, SRC_V2, SRC_DG]

REVENUE_ACCOUNT_IDS = ["ifrs-full_Revenue", "ifrs_Revenue", "dart_Revenue"]
REVENUE_ACCOUNT_NAMES = ["매출액", "수익(매출액)", "영업수익", "매출"]
REPRT_QUARTER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
DG_REVENUE_ITEM = "M000904001"   # 매출액(천원)
DG_UNIT_MULTIPLIER = 1000.0
DIFF_WARN_THRESHOLD = 0.01       # DART 와 DG 차이 1% 초과 시 경고


# ---------------------------------------------------------------- pure conversion
def to_standalone(filings: pd.DataFrame) -> pd.DataFrame:
    """정기보고서 값(분기 번호 q=1..4, amount=thstrm, cum=누적(없으면 NaN))을 분기 단독값으로 바꾼다.

    입력 컬럼: entity_id, fiscal_year, q, amount, cum
    출력 컬럼: entity_id, fiscal_year, fq, value
    """
    rows = []
    for (eid, fy), g in filings.groupby(["entity_id", "fiscal_year"], sort=True):
        amt = g.set_index("q")["amount"].to_dict()
        cum = g.set_index("q")["cum"].to_dict() if "cum" in g else {}
        cum = {k: v for k, v in cum.items() if pd.notna(v)}
        sa = {}
        if 1 in amt and pd.notna(amt[1]):
            sa[1] = amt[1]
            cum.setdefault(1, amt[1])
        for q in (2, 3):
            a = amt.get(q)
            c, cprev = cum.get(q), cum.get(q - 1)
            if c is not None and a is not None and pd.notna(a) and np.isclose(a, c, rtol=1e-9) and cprev is not None:
                sa[q] = c - cprev          # 3개월 칸에 누적이 적힌 경우
            elif a is not None and pd.notna(a):
                sa[q] = a
            elif c is not None and cprev is not None:
                sa[q] = c - cprev
        if 4 in amt and pd.notna(amt[4]):
            if all(q in sa for q in (1, 2, 3)):
                sa[4] = amt[4] - (sa[1] + sa[2] + sa[3])
            elif 3 in cum:
                sa[4] = amt[4] - cum[3]
        for q, v in sa.items():
            rows.append({"entity_id": eid, "fiscal_year": int(fy), "fq": int(q), "value": float(v)})
    return pd.DataFrame(rows, columns=["entity_id", "fiscal_year", "fq", "value"])


def _pick_revenue_rows(raw: pd.DataFrame, group_cols) -> pd.DataFrame:
    """보고서별로 매출 계정 한 줄을 고른다 (account_id 우선순위 -> 계정명 -> IS 우선)."""
    if raw.empty:
        return raw
    r = raw.copy()
    id_rank = {a: i for i, a in enumerate(REVENUE_ACCOUNT_IDS)}
    nm_rank = {a: 10 + i for i, a in enumerate(REVENUE_ACCOUNT_NAMES)}
    r["_rank"] = r["account_id"].map(id_rank).fillna(r["account_nm"].map(nm_rank)).fillna(99)
    r["_sj"] = (r["sj_div"] != "IS").astype(int)
    r = r[r["_rank"] < 99].sort_values(group_cols + ["_rank", "_sj"])
    return r.drop_duplicates(group_cols, keep="first")


def _quarter_end(fiscal_year: pd.Series, fq: pd.Series) -> pd.Series:
    # 결산월 12월 전제
    return pd.to_datetime(dict(year=fiscal_year, month=fq * 3, day=1)) + pd.offsets.MonthEnd(0)


# ---------------------------------------------------------------- sources
def _fiscal_year_ends(ids) -> dict:
    frag, p = db.in_clause("e", ids)
    if db.table_exists("fd_entity_master"):
        df = db.read_sql(f"SELECT entity_id AS k, fiscal_year_end AS m FROM fd_entity_master WHERE entity_id IN {frag}", p)
    else:
        df = db.read_sql(
            f"SELECT stock_code AS k, acc_mt AS m FROM korea_dart_corp_master WHERE stock_code IN {frag}", p)
    out = {i: 12 for i in ids}
    for k, m in zip(df["k"], df["m"]):
        if m is not None and str(m).strip().isdigit():
            out[str(k)] = int(str(m).strip())
    return out


def _read_dart(table: str, ids, basis: str) -> pd.DataFrame:
    frag, p = db.in_clause("t", ids)
    fa, pa = db.in_clause("a", REVENUE_ACCOUNT_IDS)
    fn, pn = db.in_clause("n", REVENUE_ACCOUNT_NAMES)
    if table == SRC_V3:
        extra = ", `fs_div`, `thstrm_add_amount`"
    else:
        extra = ""
    raw = db.read_sql(
        f"SELECT `ticker`, `bsns_year`, `reprt_code`, `sj_div`, `account_id`, `account_nm`, `thstrm_amount`{extra} "
        f"FROM `{table}` WHERE `ticker` IN {frag} AND `sj_div` IN ('IS', 'CIS') "
        f"AND (`account_id` IN {fa} OR `account_nm` IN {fn})",
        {**p, **pa, **pn},
    )
    if raw.empty:
        return pd.DataFrame(columns=["entity_id", "fiscal_year", "fq", "value", "fs_div", "source"])
    raw["ticker"] = raw["ticker"].astype(str)
    raw["reprt_code"] = raw["reprt_code"].astype(str)
    if table == SRC_V3:
        if basis == "separate":
            raw = raw[raw["fs_div"] == "OFS"]
        else:
            # 보고서마다 CFS 가 있으면 CFS, 없으면 OFS
            has_cfs = raw[raw["fs_div"] == "CFS"].groupby(["ticker", "bsns_year", "reprt_code"]).size()
            key = list(zip(raw["ticker"], raw["bsns_year"], raw["reprt_code"]))
            cfs_set = set(has_cfs.index)
            raw = raw[[(k in cfs_set) == (f == "CFS") for k, f in zip(key, raw["fs_div"])]]
    else:
        raw["fs_div"] = "CFS?"
        raw["thstrm_add_amount"] = np.nan
    picked = _pick_revenue_rows(raw, ["ticker", "bsns_year", "reprt_code"])
    filings = pd.DataFrame({
        "entity_id": picked["ticker"],
        "fiscal_year": picked["bsns_year"].astype(int),
        "q": picked["reprt_code"].map(REPRT_QUARTER),
        "amount": picked["thstrm_amount"].astype(float),
        "cum": picked["thstrm_add_amount"].astype(float),
    }).dropna(subset=["q"])
    sa = to_standalone(filings)
    fs = picked.groupby(["ticker", "bsns_year"])["fs_div"].agg(lambda s: ",".join(sorted(set(s))))
    sa["fs_div"] = [fs.get((e, y), "") for e, y in zip(sa["entity_id"], sa["fiscal_year"])]
    sa["source"] = table
    return sa


def _read_dg(ids) -> pd.DataFrame:
    frag, p = db.in_clause("t", ["A" + i for i in ids])
    raw = db.read_sql(
        f"SELECT `ticker`, `date`, `value` FROM `korea_fs_data_from_DG` "
        f"WHERE `item_code` = :item AND `ticker` IN {frag}",
        {**p, "item": DG_REVENUE_ITEM},
    )
    cols = ["entity_id", "period_end", "value", "source"]
    if raw.empty:
        return pd.DataFrame(columns=cols)
    raw["date"] = pd.to_datetime(raw["date"])
    ok = raw["date"].dt.month.isin([3, 6, 9, 12]) & (raw["date"].dt.day >= 20)
    if (~ok).any():
        log.warning("DG revenue rows with non quarter-end dates ignored: %d", int((~ok).sum()))
    raw = raw[ok].sort_values("date")
    raw["period_end"] = raw["date"] + pd.offsets.QuarterEnd(0)
    raw = raw.drop_duplicates(["ticker", "period_end"], keep="last")
    return pd.DataFrame({
        "entity_id": raw["ticker"].str[1:],
        "period_end": raw["period_end"],
        "value": raw["value"].astype(float) * DG_UNIT_MULTIPLIER,
        "source": SRC_DG,
    })[cols]


# ---------------------------------------------------------------- public
def read_revenue(entity_ids: Iterable[str], start=None, end=None, basis: str = "consolidated",
                 return_meta: bool = False):
    """분기 단독 매출 (원). return_meta=True 면 (df, meta) 를 돌려준다.

    meta = {"compare": DART 와 DG 를 같은 분기에서 비교한 표, "warnings": [문자열...], "fs_div": 연도별 연결/별도}
    """
    if basis not in ("consolidated", "separate"):
        raise ValueError("basis must be 'consolidated' or 'separate'")
    ids = sorted(set(require_str_codes(as_list(entity_ids), "entity_ids")))
    meta = {"compare": pd.DataFrame(), "warnings": [], "fs_div": pd.DataFrame()}
    if not ids:
        return (empty_frame(), meta) if return_meta else empty_frame()

    fye = _fiscal_year_ends(ids)
    dec_ids = [i for i in ids if fye.get(i, 12) == 12]
    for i in ids:
        if i not in dec_ids:
            msg = f"{i}: fiscal year end month {fye[i]} != 12, DART skipped (DG only)"
            log.warning(msg)
            meta["warnings"].append(msg)

    parts = []
    if dec_ids:
        for table in (SRC_V3, SRC_V2):
            if basis == "separate" and table == SRC_V2:
                continue
            d = _read_dart(table, dec_ids, basis)
            if not d.empty:
                d["period_end"] = _quarter_end(d["fiscal_year"], d["fq"])
                parts.append(d[["entity_id", "period_end", "value", "source", "fs_div"]])
    if basis == "consolidated":
        g = _read_dg(ids)
        if not g.empty:
            g["fs_div"] = "DG"
            parts.append(g)
    else:
        meta["warnings"].append("basis=separate: only DART V3 OFS is used (V2, DG have no separate flag)")

    if not parts:
        return (empty_frame(), meta) if return_meta else empty_frame()
    allv = pd.concat(parts, ignore_index=True)
    allv["_prio"] = allv["source"].map({s: i for i, s in enumerate(SOURCE_PRIORITY)})

    # DART 와 DG 비교 (D4)
    dart = allv[allv["source"] != SRC_DG].sort_values("_prio").drop_duplicates(["entity_id", "period_end"])
    dg = allv[allv["source"] == SRC_DG]
    cmp_ = dart.merge(dg[["entity_id", "period_end", "value"]], on=["entity_id", "period_end"],
                      suffixes=("_dart", "_dg"))
    if not cmp_.empty:
        cmp_["rel_diff"] = (cmp_["value_dart"] - cmp_["value_dg"]) / cmp_["value_dg"].abs()
        cmp_["warn"] = cmp_["rel_diff"].abs() > DIFF_WARN_THRESHOLD
        for r in cmp_[cmp_["warn"]].itertuples():
            msg = (f"{r.entity_id} {r.period_end.date()}: DART({r.source}) {r.value_dart:,.0f} vs DG {r.value_dg:,.0f} "
                   f"diff {r.rel_diff:+.2%}")
            log.warning(msg)
            meta["warnings"].append(msg)
    meta["compare"] = cmp_.drop(columns=["_prio"], errors="ignore")
    meta["fs_div"] = dart[["entity_id", "period_end", "source", "fs_div"]]

    best = allv.sort_values("_prio").drop_duplicates(["entity_id", "period_end"], keep="first")
    s, e = date_range_params(start, end)
    best = best[(best["period_end"] >= pd.Timestamp(s)) & (best["period_end"] <= pd.Timestamp(e))]
    if best.empty:
        return (empty_frame(), meta) if return_meta else empty_frame()

    out = pd.DataFrame({
        "key": best["entity_id"].astype(str),
        "period_end": best["period_end"],
        "freq": "Q",
        "item": "revenue",
        "value": best["value"],
        "unit": "KRW",
        "currency": "KRW",
        "source": best["source"],
    })
    is_annual = out["period_end"].dt.month == out["key"].map(lambda k: fye.get(k, 12))
    stamped = pd.concat([
        asof.stamp(out[~is_annual], "revenue_quarter"),
        asof.stamp(out[is_annual], "revenue_annual"),
    ])
    df = finalize(stamped)
    return (df, meta) if return_meta else df
