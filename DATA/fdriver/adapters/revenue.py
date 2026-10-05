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
from .. import events as events_mod
from ..config import Q4_CHECK_KNOWN_CASES, Q4_WARN_RATIO
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
Q4_CHECK_THRESHOLD = 0.10        # 4분기 점검: 잠정 4분기와 10% 초과 차이


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
                 return_meta: bool = False, release: str = "events", vintage: bool = False):
    """분기 단독 매출 (원). return_meta=True 면 (df, meta) 를 돌려준다.

    meta = {"compare": DART 와 DG 를 같은 분기에서 비교한 표, "warnings": [문자열...], "fs_div": 연도별 연결/별도,
            "first_disclosure": 분기별 첫 공시일, "q4_check": 4분기 점검표}

    release (2a 명세서 3절 발표일 규칙)
      "events"   (기본) 실제 첫 공시일(fd_earnings_event) -> 없으면 법정 기한, 휴장일이면 다음 거래일 (C)
      "legal_v1" 1단계 원래 규칙 (법정 기한, 휴장일 미반영). 1단계 결과 재현용
    vintage
      False (기본) 분기당 한 행, 값은 확정치 (1단계와 같은 값)
      True  잠정값(asof_note 'vintage:prelim') 행과 확정값('vintage:final') 행을 함께 돌려준다
    4분기 점검에 걸린 분기와 그 다음 해 4분기는 asof_note 에 'q4_check:...' 가 붙는다 (q4_exclusions).
    """
    if release not in ("events", "legal_v1"):
        raise ValueError("release must be 'events' or 'legal_v1'")
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
    }).reset_index(drop=True)
    is_annual = out["period_end"].dt.month == out["key"].map(lambda k: fye.get(k, 12))

    if release == "legal_v1":
        # 1단계 원래 규칙 (법정 기한, 휴장일 미반영) 재현
        stamped = pd.concat([asof.stamp(out[~is_annual], "revenue_quarter_legal_v1"),
                             asof.stamp(out[is_annual], "revenue_annual_legal_v1")])
        df = finalize(stamped)
        return (df, meta) if return_meta else df

    _ensure_trading_calendar()
    stamped = pd.concat([asof.stamp(out[~is_annual], "revenue_quarter"),
                         asof.stamp(out[is_annual], "revenue_annual")]).sort_index()
    ev = events_mod.read_events(ids)
    fd = events_mod.first_disclosure(ev, fye)
    meta["first_disclosure"] = fd
    stamped = _apply_first_disclosure(stamped, fd)
    q4 = q4_check(stamped, fd, fye) if basis == "consolidated" else pd.DataFrame()
    meta["q4_check"] = q4
    stamped = _apply_q4_flags(stamped, q4)
    if vintage:
        stamped = _vintage_rows(stamped, fd, ev, fye)
    df = finalize(stamped)
    return (df, meta) if return_meta else df


# ---------------------------------------------------------------- 2a: 발표일 규칙, vintage, 4분기 점검
def _ensure_trading_calendar():
    if asof._TRADING_DAYS is None:
        from .price import trading_calendar
        trading_calendar(register=True)


def _is_fiscal_q4(df: pd.DataFrame, fye: dict, key_col: str = "key") -> pd.Series:
    return df["period_end"].dt.month == df[key_col].map(lambda k: fye.get(k, 12))


def _apply_first_disclosure(stamped: pd.DataFrame, fd: pd.DataFrame) -> pd.DataFrame:
    """3절 발표일 규칙 1: 실제 첫 공시일이 있으면 그 날짜와 등급(A, prelim_sep 만 있으면 B)."""
    out = stamped.reset_index(drop=True).copy()
    if fd.empty:
        return out
    m = out[["key", "period_end"]].merge(fd.rename(columns={"entity_id": "key"}), on=["key", "period_end"], how="left")
    has = m["first_dt"].notna().values
    out.loc[has, "available_at"] = m.loc[has, "first_dt"].values
    out.loc[has, "asof_quality"] = m.loc[has, "grade"].values
    out.loc[has, "asof_note"] = ("first:" + m.loc[has, "first_type"]).values
    return out


def q4_check(stamped: pd.DataFrame, fd: pd.DataFrame, fye: dict,
             threshold: float = None) -> pd.DataFrame:
    """'연간 - 1~3분기' 4분기 매출 점검 (2a 명세서 v3 3절). 값은 대체하지 않는다.

    flagged  잠정 4분기(연결)와 10% 넘게 다르거나 4분기 값이 음수          -> 표시만 (q4_check:flagged)
    warn     4분기 값이 같은 해 1~3분기 평균의 3배 이상 또는 1/3 이하       -> 표시만 (q4_check:warn)
    excluded config.Q4_CHECK_KNOWN_CASES 의 확인된 사례                    -> 자동 제외 (q4_check:excluded,
             그 분기를 전년 동기로 쓰는 다음 해 4분기는 q4_check:yoy_base)
    참고 열 q4_from_pl_change: 연간 손익구조변경 공시값 - 1~3분기 (판정에는 쓰지 않음)
    """
    threshold = Q4_CHECK_THRESHOLD if threshold is None else threshold
    v = stamped[["key", "period_end", "value", "source"]].reset_index(drop=True)
    q4 = v[_is_fiscal_q4(v, fye)]
    fdi = fd.set_index(["entity_id", "period_end"]) if not fd.empty else None
    rows = []
    for r in q4.itertuples():
        prelim = plv = np.nan
        if fdi is not None and (r.key, r.period_end) in fdi.index:
            f = fdi.loc[(r.key, r.period_end)]
            prelim = float(f["prelim_value"]) if pd.notna(f["prelim_value"]) else np.nan
            plv = float(f["pl_change_value"]) if pd.notna(f["pl_change_value"]) else np.nan
        q123 = v[(v["key"] == r.key) & (v["period_end"] < r.period_end)
                 & (v["period_end"] > r.period_end - pd.DateOffset(months=12))]["value"]
        implied = plv - q123.sum() if not np.isnan(plv) and len(q123) == 3 else np.nan
        diff = r.value / prelim - 1 if not np.isnan(prelim) and prelim != 0 else np.nan
        reasons = []
        if r.value < 0:
            reasons.append("negative")
        if not np.isnan(diff) and abs(diff) > threshold:
            reasons.append(f"vs_prelim {diff:+.1%}")
        q123_mean = q123.mean() if len(q123) == 3 else np.nan
        ratio = r.value / q123_mean if q123_mean and not np.isnan(q123_mean) and q123_mean > 0 else np.nan
        warn = bool(not np.isnan(ratio) and (ratio >= Q4_WARN_RATIO or ratio <= 1.0 / Q4_WARN_RATIO))
        if warn:
            reasons.append(f"q4/q1-3 mean {ratio:.2f}")
        known = r.period_end in [pd.Timestamp(x) for x in Q4_CHECK_KNOWN_CASES.get(r.key, [])]
        if known:
            reasons.append("known_case (config.Q4_CHECK_KNOWN_CASES)")
        rows.append({"entity_id": r.key, "period_end": r.period_end, "q4_derived": r.value, "q4_prelim": prelim,
                     "diff_vs_prelim": diff, "q4_over_q123_mean": ratio, "q4_from_pl_change": implied,
                     "source": r.source,
                     "flagged": bool(r.value < 0 or (not np.isnan(diff) and abs(diff) > threshold)),
                     "warn": warn, "excluded": known, "reason": "; ".join(reasons)})
    return pd.DataFrame(rows)


def _apply_q4_flags(stamped: pd.DataFrame, q4: pd.DataFrame) -> pd.DataFrame:
    """asof_note 표시: flagged / warn (표시만), excluded (+ 다음 해 4분기 yoy_base, 자동 제외)."""
    if q4.empty or not (q4["flagged"] | q4["warn"] | q4["excluded"]).any():
        return stamped
    out = stamped.copy()
    for r in q4[q4["flagged"] | q4["warn"] | q4["excluded"]].itertuples():
        nxt = (r.period_end + pd.DateOffset(years=1)) + pd.offsets.MonthEnd(0)
        tags = []
        if r.flagged:
            tags.append((r.period_end, "q4_check:flagged"))
        if r.warn:
            tags.append((r.period_end, "q4_check:warn"))
        if r.excluded:
            tags += [(r.period_end, "q4_check:excluded"), (nxt, "q4_check:yoy_base")]
        for pe, tag in tags:
            m = (out["key"] == r.entity_id) & (out["period_end"] == pe)
            out.loc[m, "asof_note"] = [";".join(x for x in (str(n), tag) if x and x != "nan")
                                       for n in out.loc[m, "asof_note"].fillna("")]
    return out


def q4_exclusions(df: pd.DataFrame) -> pd.Series:
    """추정·평가에서 뺄 분기: 확인된 사례(q4_check:excluded)와 그 다음 해 4분기(q4_check:yoy_base)만.
    flagged / warn 은 표시만 하고 빼지 않는다 (2a 명세서 v3 3절)."""
    note = df["asof_note"].fillna("")
    return note.str.contains("q4_check:excluded") | note.str.contains("q4_check:yoy_base")


def _vintage_rows(stamped: pd.DataFrame, fd: pd.DataFrame, ev: pd.DataFrame, fye: dict) -> pd.DataFrame:
    """3절 규칙 4: 잠정값(잠정 공시일)과 확정값(정기보고서일)을 따로 돌려준다.

    - prelim: 연결 잠정 매출. 4분기에 연결 잠정이 없고 손익구조변경 연간값이 있으면 연간 - 1~3분기 확정값
      (그 기업의 손익구조변경이 모두 개별·별도 기준이면 개별도 허용, 연결이 하나라도 있으면 연결만)
    - final: 확정값. available_at = 정기보고서 첫 접수일(A), 없으면 규칙 날짜(C). 그 분기 첫 공시일보다 이르지 않게.
    asof_note: 'vintage:prelim' / 'vintage:final' (+ 4분기 점검 표시). as_of 뒤 asof.latest_known 으로 시점 값.
    """
    base = stamped.reset_index(drop=True)
    if fd.empty:
        fd = pd.DataFrame(columns=["entity_id", "period_end", "first_dt", "prelim_dt", "prelim_value", "periodic_dt",
                                   "pl_change_dt", "pl_change_value"])
    m = base[["key", "period_end"]].merge(fd.rename(columns={"entity_id": "key"}), on=["key", "period_end"], how="left")
    q4_notes = base["asof_note"].fillna("").map(lambda n: ";".join(x for x in n.split(";") if x.startswith("q4_check:")))
    is_q4 = _is_fiscal_q4(base, fye)

    rule_dt = pd.Series(pd.NaT, index=base.index, dtype="datetime64[ns]")
    if (~is_q4).any():
        rule_dt[~is_q4] = asof.rule_dates(base.loc[~is_q4, "period_end"], "revenue_quarter").values
    if is_q4.any():
        rule_dt[is_q4] = asof.rule_dates(base.loc[is_q4, "period_end"], "revenue_annual").values
    final = base.copy()
    per = pd.to_datetime(m["periodic_dt"])
    final["available_at"] = per.where(per.notna(), rule_dt).values
    final["asof_quality"] = np.where(per.notna(), "A", "C")
    first = pd.to_datetime(m["first_dt"])
    early = (first.notna() & (pd.to_datetime(final["available_at"]) < first)).values
    final.loc[early, "available_at"] = first[early].values
    final["asof_note"] = ["vintage:final" + (";" + n if n else "") for n in q4_notes]

    pl_fs = ev[ev["event_type"] == "pl_change"].groupby("entity_id")["fs_type"].agg(lambda s: set(s.dropna()))
    pre = []
    for i in base.index:
        r, row = base.loc[i], m.loc[i]
        val = dt = None
        src = ""
        if pd.notna(row.get("prelim_value")) and pd.notna(row.get("prelim_dt")):
            val, dt = row["prelim_value"], row["prelim_dt"]
        elif is_q4[i] and pd.notna(row.get("pl_change_value")):
            fs = pl_fs.get(r["key"], set())
            ok = ("연결" in fs) or (bool(fs) and fs <= {"개별", "별도"})
            q123 = base[(base["key"] == r["key"]) & (base["period_end"] < r["period_end"])
                        & (base["period_end"] > r["period_end"] - pd.DateOffset(months=12))]["value"]
            if ok and len(q123) == 3:
                val, dt, src = row["pl_change_value"] - q123.sum(), row["pl_change_dt"], ";from_pl_change"
        if val is None:
            continue
        p = r.copy()
        p["value"], p["available_at"], p["asof_quality"] = val, dt, "A"
        p["asof_note"] = "vintage:prelim" + src + (";" + q4_notes[i] if q4_notes[i] else "")
        p["source"] = events_mod.T_EVENT
        pre.append(p)
    return pd.concat([final, pd.DataFrame(pre)], ignore_index=True) if pre else final
