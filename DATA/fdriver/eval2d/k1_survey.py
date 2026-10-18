# -*- coding: utf-8 -*-
"""2d 작업 1 사전 조사 (명세서 2d v1 8절 1). DB 쓰기 없음 (db.read_sql 만 사용).

1. 컨센서스 커버리지: kr_revenue_consensus 의 target_period_end 가 2024Q3~2026Q2 (직전 8분기) 인 관측 분기 수.
   0 -> 없음, 1~4 -> 저커버리지, 5~8 -> 정상 (증권사 수 항목이 없어 관측 분기 수만 씀, 명세서 2절).
2. 유니버스 필터 (명세서 2절 1~5) 깔때기와 coverage_class x 업종 교차표.
   - 대상: KSE_Price 에 최근 14일 안 거래가 있는 종목 중 DART 고유번호가 있는 보통주, 시장 코스피·코스닥 (DART corp_cls Y/K)
   - 1 매출 이력: read_revenue 분기 매출 32분기 이상 + 2019Q1~2023Q4 20분기 모두
   - 2 매출 규모: 2025Q3~2026Q2 4분기 합 300억 원 이상 (4분기 모두 있어야 함)
   - 3 주가: 2019Q1~2026Q2 30개 분기 중 발표일 이벤트 계산 가능 12건 이상.
     발표일 = 법정 기한 (분기말 +45일, 4분기 +90일, 휴장일이면 다음 거래일). 계산 가능 = 발표일 -5 ~ +1 거래일에
     KSE_Price 행이 모두 있고 0일·+1일 거래량 > 0.
   - 4 기업 사건: 분기 제외 규칙이라 기업 수를 줄이지 않는다. 참고로 매출 전년 동기 대비 2배 초과·절반 미만 분기가
     있는 기업을 '점검 필요'로 표시만 한다.
   - 5 금융업·지주회사: DART 기업개황 업종코드(KSIC) 64~66 (금융·보험), 715 (회사 본부 = 지주회사 등록 업종),
     회사명에 '홀딩스'·'지주'·'Holdings'. 판별 근거는 fin_holding_reason 열.
   - 레이더 39곳은 판정 표본에서 빠지므로 마지막에 뺀다.
3. 영업이익 모듈 자료: DART V3 -> V2 -> DG 순으로 분기 단독 영업이익·매출원가·판관비 (매출과 같은 to_standalone 규칙).
   DG 판관비 = 매출총이익 - 영업이익. 매출원가 0 이하는 없음으로 본다. 4분기 누적 저장 여부 점검.
4. 내수 드라이버 원천 목록, 한국은행 소비 관련 계열.
5. 코스닥 지수 (FDR 야후 경로 확인만, 적재 안 함), DART 사업보고서 '주요 제품' 표 추출 시험.
실행: python -m DATA.fdriver.eval2d.k1_survey
"""
import json
import logging
import re
import time
from typing import Dict, List

import numpy as np
import pandas as pd

from .. import db, events
from ..adapters import read_revenue
from ..adapters.revenue import REPRT_QUARTER, to_standalone
from ..config import get_key, output_path

OUT = output_path("2d", "K1")
CACHE = output_path("2d", "K1", "cache")
log = logging.getLogger(__name__)


def qends(a: str, b: str) -> pd.DatetimeIndex:
    return pd.period_range(a, b, freq="Q").to_timestamp(how="end").normalize()


def qlabel(ts) -> str:
    ts = pd.Timestamp(ts)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


WIN8 = qends("2024Q3", "2026Q2")
SEL = qends("2019Q1", "2023Q4")
ALLQ = qends("2019Q1", "2026Q2")
LAST4 = qends("2025Q3", "2026Q2")
LISTED_GRACE_DAYS = 14
MIN_REV_Q, MIN_REV_4Q, MIN_EVENTS = 32, 300e8, 12
LOW_COV_MAX = 4
COV_ORDER = ["없음", "저커버리지", "정상"]

# ------------------------------------------------------------------ 업종 (KSIC 10차 중분류)
KSIC_DIV = {
    "01": "농업", "02": "임업", "03": "어업", "05": "석탄·원유·천연가스 광업", "06": "금속 광업", "07": "비금속광물 광업",
    "08": "광업 지원", "10": "식료품", "11": "음료", "12": "담배", "13": "섬유제품", "14": "의복·모피",
    "15": "가죽·가방·신발", "16": "목재", "17": "펄프·종이", "18": "인쇄", "19": "코크스·석유정제", "20": "화학물질·화학제품",
    "21": "의약품", "22": "고무·플라스틱", "23": "비금속 광물제품", "24": "1차 금속", "25": "금속가공제품",
    "26": "전자부품·컴퓨터·통신장비", "27": "의료·정밀·광학기기", "28": "전기장비", "29": "기타 기계·장비",
    "30": "자동차·트레일러", "31": "기타 운송장비", "32": "가구", "33": "기타 제품", "34": "산업용 기계 수리",
    "35": "전기·가스·증기", "36": "수도", "37": "하수·폐수", "38": "폐기물·원료재생", "39": "환경 정화", "41": "종합 건설",
    "42": "전문직별 공사", "45": "자동차·부품 판매", "46": "도매·상품중개", "47": "소매", "49": "육상 운송",
    "50": "수상 운송", "51": "항공 운송", "52": "창고·운송 서비스", "55": "숙박", "56": "음식점·주점", "58": "출판",
    "59": "영상·오디오·음악", "60": "방송", "61": "우편·통신", "62": "컴퓨터 프로그래밍·SI", "63": "정보서비스",
    "64": "금융", "65": "보험·연금", "66": "금융·보험 서비스", "68": "부동산", "70": "연구개발", "71": "전문 서비스",
    "72": "건축기술·엔지니어링", "73": "기타 과학기술 서비스", "74": "사업시설 관리", "75": "사업 지원 서비스",
    "76": "임대(부동산 제외)", "84": "공공행정", "85": "교육", "86": "보건", "87": "사회복지", "90": "창작·예술",
    "91": "스포츠·오락", "94": "협회·단체", "95": "수리", "96": "기타 개인 서비스",
}
# 교차표용 업종 묶음 (내수 드라이버 4.2 표와 대응을 보기 쉽게)
GROUPS = [
    ("식품·음료·담배", ["10", "11", "12"]),
    ("섬유·의복·생활소비재", ["13", "14", "15", "16", "32", "33"]),
    ("정유·화학·플라스틱", ["19", "20", "22"]),
    ("제약·바이오", ["21"]),
    ("비금속·철강·금속", ["05", "06", "07", "08", "23", "24", "25"]),
    ("전자부품·반도체·장비", ["26", "27"]),
    ("전기장비·기계", ["28", "29", "34"]),
    ("자동차·운송장비", ["30", "31"]),
    ("에너지·유틸리티·환경", ["35", "36", "37", "38", "39"]),
    ("건설", ["41", "42"]),
    ("도소매·유통", ["45", "46", "47"]),
    ("운송·물류", ["49", "50", "51", "52"]),
    ("숙박·음식·레저", ["55", "56", "90", "91"]),
    ("미디어·통신·소프트웨어", ["58", "59", "60", "61", "62", "63"]),
    ("금융·보험", ["64", "65", "66"]),
    ("전문·사업 서비스", ["68", "70", "71", "72", "73", "74", "75", "76"]),
]
GROUP_OF = {d: g for g, ds in GROUPS for d in ds}
FIN_KSIC_DIV = ("64", "65", "66")
HOLDING_KSIC = ("715",)          # 회사 본부 (DART 업종코드는 3~5자리)
HOLDING_NAME = re.compile(r"홀딩스|지주|Holdings", re.I)


def industry_group(ksic: str) -> str:
    return GROUP_OF.get(str(ksic or "")[:2], "기타")


# ------------------------------------------------------------------ 대상 상장사와 DART 기업개황
def listed_codes() -> pd.DataFrame:
    px = db.read_sql("SELECT code, MIN(date) AS d0, MAX(date) AS d1 FROM KSE_Price GROUP BY code")
    px["d0"], px["d1"] = pd.to_datetime(px["d0"]), pd.to_datetime(px["d1"])
    cutoff = px["d1"].max() - pd.Timedelta(days=LISTED_GRACE_DAYS)
    cur = px[px["d1"] >= cutoff].copy()
    cm = db.read_sql("SELECT corp_code, stock_code, corp_name FROM korea_dart_corp_master "
                     "WHERE stock_code IS NOT NULL AND stock_code <> '000000'")
    cm = cm.drop_duplicates("stock_code", keep="last")
    m = cur.merge(cm, left_on="code", right_on="stock_code", how="left")
    META["price_last_date"] = str(px["d1"].max().date())
    META["n_price_codes_current"] = len(m)
    META["n_without_corp_code"] = int(m["corp_code"].isna().sum())
    return m


def dart_company(corp_codes: List[str]) -> pd.DataFrame:
    """DART company.json (기업개황). CACHE/dart_company.csv 에 저장하고 없는 것만 받는다."""
    import requests
    f = CACHE / "dart_company.csv"
    have = pd.read_csv(f, dtype=str) if f.exists() else pd.DataFrame(columns=["corp_code"])
    need = sorted(set(corp_codes) - set(have["corp_code"]))
    rows = []
    for i, c in enumerate(need):
        js = {"status": "ERR"}
        for k in range(3):
            try:
                js = requests.get("https://opendart.fss.or.kr/api/company.json",
                                  params={"crtfc_key": get_key("DART"), "corp_code": c}, timeout=20).json()
                break
            except Exception as e:      # 네트워크 오류는 다시 시도
                js = {"status": "ERR", "message": repr(e)}
                time.sleep(2)
        js["corp_code"] = c
        rows.append(js)
        time.sleep(0.12)
        if i % 200 == 0:
            log.info("company.json %d/%d", i, len(need))
    if rows:
        have = pd.concat([have, pd.DataFrame(rows).astype(str)], ignore_index=True)
        have.to_csv(f, index=False, encoding="utf-8-sig")
    return have


def fin_holding_reason(ksic: str, name: str) -> str:
    k = str(ksic or "")
    if k[:2] in FIN_KSIC_DIV:
        return f"금융업(KSIC {k})"
    if any(k.startswith(h) for h in HOLDING_KSIC):
        return f"지주회사(KSIC {k} 회사 본부)"
    if HOLDING_NAME.search(str(name or "")):
        return f"지주회사(회사명, KSIC {k})"
    return ""


def base_universe() -> pd.DataFrame:
    m = listed_codes()
    ok = m.dropna(subset=["corp_code"]).copy()
    ci = dart_company(ok["corp_code"].tolist())
    ci = ci[["corp_code", "status", "corp_name", "stock_name", "corp_cls", "induty_code", "acc_mt"]]
    u = ok[["code", "corp_code", "d0", "d1"]].merge(ci, on="corp_code", how="left")
    u = u.rename(columns={"code": "ticker", "stock_name": "name"})
    u["market"] = u["corp_cls"].map({"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "기타"})
    u["ksic"] = u["induty_code"].fillna("").astype(str)
    u["ksic_div"] = u["ksic"].str[:2]
    u["ksic_div_name"] = u["ksic_div"].map(KSIC_DIV).fillna("(미상)")
    u["industry_group"] = u["ksic"].map(industry_group)
    u["fin_holding_reason"] = [fin_holding_reason(k, n) for k, n in zip(u["ksic"], u["name"])]
    return u


# ------------------------------------------------------------------ 1. 컨센서스 커버리지
def consensus_coverage(tickers: List[str]) -> pd.DataFrame:
    c = db.read_sql("SELECT ticker, target_period_end, COUNT(*) AS n_obs FROM kr_revenue_consensus "
                    "GROUP BY ticker, target_period_end")
    c["target_period_end"] = pd.to_datetime(c["target_period_end"])
    w = c[c["target_period_end"].isin(WIN8)]
    n8 = w.groupby("ticker")["target_period_end"].nunique()
    nall = c.groupby("ticker")["target_period_end"].nunique()
    obs8 = w.groupby("ticker")["n_obs"].sum()
    out = pd.DataFrame({"ticker": tickers})
    out["cons_q_8"] = out["ticker"].map(n8).fillna(0).astype(int)
    out["cons_obs_8"] = out["ticker"].map(obs8).fillna(0).astype(int)
    out["cons_q_all"] = out["ticker"].map(nall).fillna(0).astype(int)
    out["coverage_class"] = np.select([out["cons_q_8"] == 0, out["cons_q_8"] <= LOW_COV_MAX],
                                      ["없음", "저커버리지"], "정상")
    return out


# ------------------------------------------------------------------ 2. 매출 이력·규모
META: Dict = {}


def revenue_panel(ids: List[str]) -> pd.DataFrame:
    """read_revenue 분기 매출. DART 와 DG 비교표(meta compare)는 META['rev_compare'] 에 둔다."""
    parts, cmp_ = [], []
    for i in range(0, len(ids), 300):
        r, meta = read_revenue(ids[i:i + 300], start="2009-01-01", return_meta=True)
        parts.append(r)
        cmp_.append(meta["compare"])
    META["rev_compare"] = pd.concat(cmp_, ignore_index=True)
    r = pd.concat(parts, ignore_index=True)
    return r[["key", "period_end", "value", "source"]].rename(columns={"key": "ticker"})


def revenue_basis_check(rev: pd.DataFrame) -> pd.DataFrame:
    """2019Q1~2026Q2 에서 쓰인 DART 매출이 DG 와 5% 넘게 다른 분기, 음수 매출 분기 (V2 누적 저장 의심)."""
    c = META["rev_compare"]
    c = c[c["period_end"].isin(ALLQ) & (c["rel_diff"].abs() > 0.05)]
    c = c[["entity_id", "period_end", "source", "value_dart", "value_dg", "rel_diff"]].assign(kind="dart_vs_dg_5pct")
    neg = rev[rev["period_end"].isin(ALLQ) & (rev["value"] < 0)]
    neg = pd.DataFrame({"entity_id": neg["ticker"], "period_end": neg["period_end"], "source": neg["source"],
                        "value_dart": neg["value"], "kind": "negative_revenue"})
    return pd.concat([c, neg], ignore_index=True)


def revenue_stats(rev: pd.DataFrame, ids: List[str]) -> pd.DataFrame:
    r = rev.dropna(subset=["value"])
    r = r[r["period_end"] <= ALLQ[-1]]
    g = r.groupby("ticker")
    out = pd.DataFrame({"ticker": ids})
    out["rev_q_total"] = out["ticker"].map(g.size()).fillna(0).astype(int)
    out["rev_q_sel"] = out["ticker"].map(r[r["period_end"].isin(SEL)].groupby("ticker").size()).fillna(0).astype(int)
    l4 = r[r["period_end"].isin(LAST4)].groupby("ticker")["value"].agg(["size", "sum"])
    out["rev_last4_n"] = out["ticker"].map(l4["size"]).fillna(0).astype(int)
    out["rev_last4_sum_eok"] = (out["ticker"].map(l4["sum"]) / 1e8).round(1)
    # 기업 사건 점검 (표시만): 2019Q1~2026Q2 매출 전년 동기 대비 2배 초과·절반 미만
    p = r.pivot_table(index="period_end", columns="ticker", values="value")
    ratio = (p / p.shift(4)).loc[p.index.isin(ALLQ)]
    jump = ((ratio > 2) | (ratio < 0.5)).sum()
    out["rev_jump_q"] = out["ticker"].map(jump).fillna(0).astype(int)
    out["rev_jump_quarters"] = out["ticker"].map(
        {t: ",".join(qlabel(q) for q in ratio.index[(ratio[t] > 2) | (ratio[t] < 0.5)]) for t in ratio.columns})
    return out


# ------------------------------------------------------------------ 3. 주가 이벤트
def legal_announce(q: pd.Timestamp) -> pd.Timestamp:
    return q + pd.Timedelta(days=90 if q.month == 12 else 45)


def price_events(ids: List[str]) -> pd.DataFrame:
    px = db.read_sql("SELECT code, date, volume FROM KSE_Price WHERE date >= '2019-01-01'")
    px["date"] = pd.to_datetime(px["date"])
    idx = db.read_sql("SELECT period_end FROM kr_stock_index_daily WHERE idx_name = 'YAHOO:^KS11' "
                      "AND period_end >= '2019-01-01'")
    cal = pd.DatetimeIndex(sorted(set(px["date"]) | set(pd.to_datetime(idx["period_end"]))))
    pos = {d: i for i, d in enumerate(cal)}
    ev_pos = {}
    for q in ALLQ:
        j = cal.searchsorted(legal_announce(q))
        ev_pos[q] = j if j < len(cal) else None
    px = px[px["code"].isin(set(ids))]
    rows = []
    for code, g in px.groupby("code"):
        have = np.zeros(len(cal), bool)
        traded = np.zeros(len(cal), bool)
        p = g["date"].map(pos).to_numpy()
        have[p] = True
        traded[p] = g["volume"].fillna(0).to_numpy() > 0
        n = 0
        for q, j in ev_pos.items():
            if j is None or j - 5 < 0 or j + 1 >= len(cal):
                continue
            if have[j - 5:j + 2].all() and traded[j] and traded[j + 1]:
                n += 1
        rows.append({"ticker": code, "price_events": n})
    out = pd.DataFrame({"ticker": ids}).merge(pd.DataFrame(rows), on="ticker", how="left")
    out["price_events"] = out["price_events"].fillna(0).astype(int)
    out.attrs["n_quarters_possible"] = sum(1 for j in ev_pos.values() if j is not None and j + 1 < len(cal))
    return out


# ------------------------------------------------------------------ 깔때기
def apply_filters(u: pd.DataFrame, radar_ids: set) -> pd.DataFrame:
    u = u.copy()
    u["f0_market"] = u["market"].isin(["KOSPI", "KOSDAQ"])
    u["f1_rev_history"] = (u["rev_q_total"] >= MIN_REV_Q) & (u["rev_q_sel"] == len(SEL))
    u["f2_rev_size"] = (u["rev_last4_n"] == 4) & (u["rev_last4_sum_eok"] * 1e8 >= MIN_REV_4Q)
    u["f3_price"] = u["price_events"] >= MIN_EVENTS
    u["f4_corp_event_check"] = u["rev_jump_q"] > 0          # 표시만
    u["f5_not_fin_holding"] = u["fin_holding_reason"] == ""
    u["in_radar39"] = u["ticker"].isin(radar_ids)
    s0 = u["f0_market"]
    s1 = s0 & u["f1_rev_history"]
    s2 = s1 & u["f2_rev_size"]
    s3 = s2 & u["f3_price"]
    s5 = s3 & u["f5_not_fin_holding"]
    s6 = s5 & ~u["in_radar39"]
    u["pass_all"] = s5
    u["pass_all_ex39"] = s6
    u["target_2d"] = s6 & u["coverage_class"].isin(["없음", "저커버리지"])
    u.attrs["steps"] = [("0 상장 보통주 (코스피·코스닥)", s0), ("1 매출 이력 32분기 + 2019~2023 20분기", s1),
                        ("2 최근 4분기 매출 300억 이상", s2), ("3 주가 이벤트 12건 이상", s3),
                        ("4 기업 사건 (분기 제외 규칙, 기업 수 변화 없음)", s3), ("5 금융업·지주회사 제외", s5),
                        ("6 레이더 39곳 제외", s6)]
    return u


def funnel_table(u: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, mask in u.attrs["steps"]:
        d = u[mask]
        r = {"step": name, "전체": len(d)}
        for c in COV_ORDER:
            r[c] = int((d["coverage_class"] == c).sum())
        r["KOSPI"] = int((d["market"] == "KOSPI").sum())
        r["KOSDAQ"] = int((d["market"] == "KOSDAQ").sum())
        rows.append(r)
    return pd.DataFrame(rows)


def crosstabs(u: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    d = u[u["pass_all_ex39"]]
    g = pd.crosstab(d["industry_group"], d["coverage_class"], margins=True, margins_name="합계")
    g = g.reindex(columns=[c for c in COV_ORDER + ["합계"] if c in g.columns])
    k = pd.crosstab([d["ksic_div"], d["ksic_div_name"]], d["coverage_class"], margins=True, margins_name="합계")
    k = k.reindex(columns=[c for c in COV_ORDER + ["합계"] if c in k.columns])
    return {"group": g, "ksic": k}


# ------------------------------------------------------------------ 3. 영업이익 모듈 자료
OP_ITEMS = {
    "op": (["dart_OperatingIncomeLoss"], ["영업이익", "영업이익(손실)", "영업손익", "영업손실"]),
    "cogs": (["ifrs-full_CostOfSales", "ifrs_CostOfSales"], ["매출원가"]),
    "sga": (["dart_TotalSellingGeneralAdministrativeExpenses", "ifrs-full_SellingGeneralAndAdministrativeExpense"],
            ["판매비와관리비", "판매비와 관리비", "판매비및관리비"]),
    "opex": (["ifrs-full_OperatingExpense", "dart_OperatingExpenses"], ["영업비용"]),
}
DG_ITEMS = {"op": "M000906001", "cogs": "M000905001", "gp": "M000904007", "rev": "M000904001"}
SRC_RANK = {"V3": 0, "V2": 1, "DG": 2}


def _read_dart_items(table: str, ids: List[str]) -> pd.DataFrame:
    acc = sorted({a for v in OP_ITEMS.values() for a in v[0]})
    nms = sorted({a for v in OP_ITEMS.values() for a in v[1]})
    extra = ", fs_div, thstrm_add_amount" if table.endswith("V3") else ""
    parts = []
    for i in range(0, len(ids), 150):
        ft, pt = db.in_clause("t", ids[i:i + 150])
        fa, pa = db.in_clause("a", acc)
        fn, pn = db.in_clause("n", nms)
        parts.append(db.read_sql(
            f"SELECT ticker, bsns_year, reprt_code, sj_div, account_id, account_nm, thstrm_amount{extra} "
            f"FROM `{table}` WHERE ticker IN {ft} AND sj_div IN ('IS','CIS') AND bsns_year >= 2018 "
            f"AND (account_id IN {fa} OR account_nm IN {fn})", {**pt, **pa, **pn}))
    raw = pd.concat(parts, ignore_index=True)
    raw["reprt_code"] = raw["reprt_code"].astype(str)
    if table.endswith("V3"):
        has_cfs = set(raw.loc[raw["fs_div"] == "CFS", ["ticker", "bsns_year", "reprt_code"]].itertuples(index=False,
                                                                                                         name=None))
        key = list(zip(raw["ticker"], raw["bsns_year"], raw["reprt_code"]))
        raw = raw[[(k in has_cfs) == (f == "CFS") for k, f in zip(key, raw["fs_div"])]]
    else:
        raw["thstrm_add_amount"] = np.nan
    return raw


def _standalone_items(raw: pd.DataFrame, src: str) -> (pd.DataFrame, pd.DataFrame):
    """항목별 분기 단독값과 원 보고서 값 (연간 값 확인용)."""
    out, filings_all = [], []
    for item, (ids_, nms) in OP_ITEMS.items():
        r = raw.copy()
        r["_rank"] = r["account_id"].map({a: i for i, a in enumerate(ids_)}).fillna(
            r["account_nm"].map({a: 10 + i for i, a in enumerate(nms)})).fillna(99)
        r = r[r["_rank"] < 99]
        r["_sj"] = (r["sj_div"] != "IS").astype(int)
        r = r.sort_values(["ticker", "bsns_year", "reprt_code", "_rank", "_sj"]).drop_duplicates(
            ["ticker", "bsns_year", "reprt_code"])
        f = pd.DataFrame({"entity_id": r["ticker"], "fiscal_year": r["bsns_year"].astype(int),
                          "q": r["reprt_code"].map(REPRT_QUARTER), "amount": r["thstrm_amount"].astype(float),
                          "cum": pd.to_numeric(r["thstrm_add_amount"], errors="coerce")}).dropna(subset=["q"])
        sa = to_standalone(f)
        sa["item"], sa["src"] = item, src
        out.append(sa)
        f = f.assign(item=item, src=src)
        filings_all.append(f)
    sa = pd.concat(out, ignore_index=True)
    sa["period_end"] = (pd.to_datetime(dict(year=sa["fiscal_year"], month=sa["fq"] * 3, day=1))
                        + pd.offsets.MonthEnd(0))
    return sa, pd.concat(filings_all, ignore_index=True)


def _read_dg_items(ids: List[str]) -> pd.DataFrame:
    parts = []
    for i in range(0, len(ids), 300):
        ft, pt = db.in_clause("t", ["A" + x for x in ids[i:i + 300]])
        fi, pi = db.in_clause("i", list(DG_ITEMS.values()))
        parts.append(db.read_sql(f"SELECT ticker, date, item_code, value FROM korea_fs_data_from_DG "
                                 f"WHERE ticker IN {ft} AND item_code IN {fi} AND date >= '2018-01-01'", {**pt, **pi}))
    d = pd.concat(parts, ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["date"].dt.month.isin([3, 6, 9, 12]) & (d["date"].dt.day >= 20)]
    d["period_end"] = d["date"] + pd.offsets.QuarterEnd(0)
    inv = {v: k for k, v in DG_ITEMS.items()}
    d["item"] = d["item_code"].map(inv)
    d = d.sort_values("date").drop_duplicates(["ticker", "period_end", "item"], keep="last")
    w = d.pivot_table(index=["ticker", "period_end"], columns="item", values="value").reset_index()
    for c in DG_ITEMS:
        if c not in w:
            w[c] = np.nan
    w["sga"] = w["gp"] - w["op"]
    long = w.melt(id_vars=["ticker", "period_end"], value_vars=["op", "cogs", "sga", "rev"], var_name="item")
    long["value"] = long["value"] * 1000.0
    long["src"] = "DG"
    return long.dropna(subset=["value"]).rename(columns={"ticker": "entity_id"}).assign(
        entity_id=lambda x: x["entity_id"].str[1:])


def op_panel(ids: List[str]):
    v3, f3 = _standalone_items(_read_dart_items("korea_fs_data_from_DART_V3", ids), "V3")
    v2, f2 = _standalone_items(_read_dart_items("korea_fs_data_from_DART_V2", ids), "V2")
    dg = _read_dg_items(ids)
    cols = ["entity_id", "period_end", "item", "value", "src"]
    allv = pd.concat([v3[cols], v2[cols], dg[cols]], ignore_index=True)
    allv["_r"] = allv["src"].map(SRC_RANK)
    best = allv[allv["item"] != "rev"].sort_values("_r").drop_duplicates(["entity_id", "period_end", "item"])
    return best.drop(columns="_r"), allv, pd.concat([f3, f2], ignore_index=True)


def op_stats(best: pd.DataFrame, ids: List[str]) -> pd.DataFrame:
    b = best[best["period_end"].isin(ALLQ)].copy()
    b = b[~((b["item"] == "cogs") & (b["value"] <= 0))]       # 매출원가 0 이하는 없음
    out = pd.DataFrame({"ticker": ids})
    for item in ["op", "cogs", "sga", "opex"]:
        x = b[b["item"] == item]
        out[f"n_{item}_all"] = out["ticker"].map(x.groupby("entity_id").size()).fillna(0).astype(int)
        out[f"n_{item}_sel"] = out["ticker"].map(x[x["period_end"].isin(SEL)].groupby("entity_id").size()
                                                 ).fillna(0).astype(int)
    src = b[b["item"] == "op"].groupby("entity_id")["src"].agg(lambda s: ",".join(sorted(set(s))))
    out["op_sources"] = out["ticker"].map(src).fillna("")
    out["op_full_sel"] = out["n_op_sel"] == len(SEL)
    out["cost_split_full_sel"] = (out["n_cogs_sel"] == len(SEL)) & (out["n_sga_sel"] == len(SEL))
    out["cost_split_partial"] = ~out["cost_split_full_sel"] & (out["n_cogs_all"] > 0)
    out["nature_of_expense"] = (out["n_op_all"] > 0) & (out["n_cogs_all"] == 0)     # R3 후보
    out["has_opex_account"] = out["n_opex_all"] > 0
    return out


def q4_check(allv: pd.DataFrame, filings: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """(a) DG 4분기 값 = DART 연간 값인지 (누적 저장), (b) V2 2·3분기 칸이 누적인지, (c) DART 와 DG 일치율."""
    piv = allv.pivot_table(index=["entity_id", "period_end", "item"], columns="src", values="value").reset_index()
    for c in ("V3", "V2", "DG"):
        if c not in piv:
            piv[c] = np.nan
    piv["DART"] = piv["V3"].combine_first(piv["V2"])
    piv["fy"], piv["fq"] = piv["period_end"].dt.year, piv["period_end"].dt.quarter
    ann = filings[filings["q"] == 4].groupby(["entity_id", "fiscal_year", "item"])["amount"].first()
    piv["dart_annual"] = [ann.get((e, y, i), np.nan) for e, y, i in zip(piv["entity_id"], piv["fy"], piv["item"])]
    both = piv.dropna(subset=["DART", "DG"])
    both = both[both["item"].isin(["op", "cogs", "sga"]) & both["period_end"].isin(ALLQ)]
    scale = both[["DART", "DG"]].abs().max(axis=1).replace(0, np.nan)
    both = both.assign(rel=(both["DART"] - both["DG"]).abs() / scale)
    agree = both.assign(q4=both["fq"] == 4).groupby(["item", "q4"])["rel"].agg(
        n="size", within_1pct=lambda s: float((s <= 0.01).mean()), within_5pct=lambda s: float((s <= 0.05).mean())
    ).reset_index()
    q4 = both[both["fq"] == 4].copy()
    q4["dg_eq_annual"] = ((q4["DG"] - q4["dart_annual"]).abs() <= 0.01 * q4["dart_annual"].abs()) & \
                         ((q4["dart_annual"] - q4["DART"]).abs() > 0.05 * q4["dart_annual"].abs())
    q4_flag = q4[q4["dg_eq_annual"]]
    # V2 2·3분기 칸이 누적: V2 원 보고서 값이 DG (해당 분기 + 이전 분기들) 합과 같고 DG 단독값과 다름
    f2 = filings[(filings["src"] == "V2") & filings["q"].isin([2, 3])].copy()
    dgw = allv[allv["src"] == "DG"].pivot_table(index=["entity_id", "item"], columns="period_end", values="value")
    hits = []
    for r in f2.itertuples():
        qe = pd.Timestamp(r.fiscal_year, int(r.q) * 3, 1) + pd.offsets.MonthEnd(0)
        if (r.entity_id, r.item) not in dgw.index or qe not in dgw.columns:
            continue
        row = dgw.loc[(r.entity_id, r.item)]
        prev = [pd.Timestamp(r.fiscal_year, m, 1) + pd.offsets.MonthEnd(0) for m in range(3, int(r.q) * 3 + 1, 3)]
        if any(p not in row.index or pd.isna(row[p]) for p in prev):
            continue
        cum, sa = row[prev].sum(), row[qe]
        if abs(r.amount - cum) <= 0.01 * abs(cum) and abs(cum - sa) > 0.05 * abs(cum):
            hits.append({"entity_id": r.entity_id, "fiscal_year": r.fiscal_year, "q": r.q, "item": r.item,
                         "v2_amount": r.amount, "dg_cum": cum, "dg_standalone": sa})
    return {"agree": agree, "dg_q4_annual": q4_flag, "v2_cum": pd.DataFrame(hits)}


# ------------------------------------------------------------------ 4. 내수 드라이버 원천
def driver_inventory() -> Dict[str, pd.DataFrame]:
    rows = []

    def add(cand, table, series, freq, rng, lag, note=""):
        rows.append({"후보(4.2)": cand, "table": table, "series": series, "freq": freq, "period": rng,
                     "lag_rule": lag, "note": note})

    k = db.read_sql("SELECT i.table_alias, i.c1_name, i.itm_name, i.freq, MIN(d.period_end) d0, MAX(d.period_end) d1 "
                    "FROM kr_kosis_series_info i JOIN kr_kosis_data d USING(series_id) "
                    "WHERE i.table_alias IN ('retail_sales_by_store_type','retail_sales_index') GROUP BY i.series_id")
    for alias, g in k.groupby("table_alias"):
        for (c1, d0, d1), gg in g.groupby(["c1_name", "d0", "d1"]):
            add("백화점·편의점·대형마트" if alias == "retail_sales_by_store_type" else "소비재 일부 (상품군별)",
                "kr_kosis_data", f"{alias} / {c1} ({', '.join(sorted(gg['itm_name']))})", "M", f"{d0} ~ {d1}",
                "kosis: 월말+35일 (C), 최초 수집일 A", "")
    t = db.read_sql("SELECT direction, COUNT(DISTINCT nat_code) n, MIN(period_end) d0, MAX(period_end) d1 "
                    "FROM kr_tourism_visitors_monthly GROUP BY direction")
    for r in t.itertuples():
        add("면세·카지노·호텔" if r.direction == "E" else "여행·항공 (출국자)", "kr_tourism_visitors_monthly",
            f"direction={r.direction} ({'방한객, 국가 ' + str(r.n) + '개' if r.direction == 'E' else '출국자 합계'})",
            "M", f"{r.d0} ~ {r.d1}", "tourism: 관광통계 공표일정 (B), 없으면 +35일", "")
    a = db.read_sql("SELECT i.source, i.indicator, COUNT(DISTINCT i.series_id) n, MIN(d.period_end) d0, "
                    "MAX(d.period_end) d1 FROM kr_airline_traffic_info i JOIN kr_airline_traffic d USING(series_id) "
                    "GROUP BY i.source, i.indicator")
    for r in a.itertuples():
        note = ("인천공항 국제선 (대한·아시아나·제주·아메리칸 4개사만)" if r.source == "ODP_B551177"
                else "국내·국제선 구분 없음")
        add("여행·항공 (국제선 여객)", "kr_airline_traffic", f"{r.source} / {r.indicator} (항공사 {r.n})", "M",
            f"{r.d0} ~ {r.d1}", "airline: 월말+35일 (C)", note)
    km = db.read_sql("SELECT i.category_name, COUNT(DISTINCT i.series_id) n, MIN(d.period_end) d0, "
                     "MAX(d.period_end) d1, GROUP_CONCAT(DISTINCT i.mineral_name) names "
                     "FROM kr_komis_mineral_price_info i JOIN kr_komis_mineral_price d USING(series_id) "
                     "GROUP BY i.category_name")
    for r in km.itertuples():
        add("정유·화학 (원자재 가격)", "kr_komis_mineral_price", f"{r.category_name}: {r.names}", "D/W",
            f"{r.d0} ~ {r.d1}", "komis: 다음 영업일 (C)", "석유화학 제품(나프타·에틸렌 등) 가격은 없음")
    fr = db.read_sql("SELECT i.series_id, i.title, i.freq, MIN(d.period_end) d0, MAX(d.period_end) d1 "
                     "FROM us_fred_series_info i JOIN us_fred_data d USING(series_id) "
                     "WHERE i.series_id IN ('DCOILWTICO','PCOPPUSDM') GROUP BY i.series_id")
    for r in fr.itertuples():
        add("정유·화학 (FRED 유가)", "us_fred_data", f"{r.series_id} {r.title}", r.freq, f"{r.d0} ~ {r.d1}",
            "fred: 일간 다음 영업일, 공표일 A (ALFRED)", "")
    b = db.read_sql("SELECT Category, Index_Name, stat_code, freq, MIN(Date) d0, MAX(Date) d1, COUNT(*) n, "
                    "MAX(Report_Date) rd FROM bok_economic_indicators GROUP BY Category, Index_Name, stat_code, freq")
    cons_names = ["소매판매액지수_계절조정", "자동차판매액지수_계절조정", "소비자심리지수", "도소매업지수", "서비스업생산지수",
                  "소비자물가지수", "생활물가지수", "소비자물가지수(농산물,석유제외)", "은행대출금_가계", "취업자수", "고용률"]
    constr = ["건설기성액_불변_계절조정", "건설수주액", "건축허가면적", "건축착공면적", "미분양주택", "주택매매가격지수"]
    for r in b.itertuples():
        if r.Index_Name in constr:
            cand = "건설·건자재"
        elif r.Index_Name == "자동차판매액지수_계절조정":
            cand = "자동차 내수"
        elif r.Index_Name == "국제유가":
            cand = "정유·화학 (한국은행 국제유가)"
        elif r.Index_Name in cons_names:
            cand = "소비재 일부 (한국은행)"
        else:
            continue
        add(cand, "bok_economic_indicators", f"{r.Category} / {r.Index_Name} ({r.stat_code})", r.freq,
            f"{str(r.d0)[:10]} ~ {str(r.d1)[:10]}",
            "규칙 없음 (Report_Date 는 수집일 하나, vintage 없음)", f"{r.n}개월")
    inv = pd.DataFrame(rows)
    bok = b.copy()
    bok["consumption_related"] = bok["Index_Name"].isin(cons_names) | (bok["Category"] == "소비")
    return {"inventory": inv, "bok": bok}


# ------------------------------------------------------------------ 5. 코스닥 지수, DART 주요 제품
def kosdaq_check() -> Dict:
    import FinanceDataReader as fdr
    out = {}
    for sym in ("YAHOO:^KQ11", "KQ11"):
        try:
            d = fdr.DataReader(sym, "2013-01-01")
            out[sym] = {"ok": True, "rows": len(d), "first": str(d.index.min().date()),
                        "last": str(d.index.max().date()), "close_nan": int(d["Close"].isna().sum())}
            if sym.startswith("YAHOO"):
                kq = d
        except Exception as e:      # 원천 오류는 결과로 남긴다
            out[sym] = {"ok": False, "error": repr(e)[:200]}
    px = db.read_sql("SELECT DISTINCT date FROM KSE_Price WHERE date >= '2013-06-01' AND code = '005930'")
    cal = pd.DatetimeIndex(pd.to_datetime(px["date"]))
    ks = db.read_sql("SELECT period_end FROM kr_stock_index_daily WHERE idx_name='YAHOO:^KS11' AND period_end >= "
                     "'2013-06-01'")
    end = min(cal.max(), kq.index.max())
    cal = cal[cal <= end]
    out["missing_vs_kse_calendar"] = {
        "trading_days": len(cal),
        "kosdaq_yahoo_missing": int((~cal.isin(kq.dropna(subset=["Close"]).index.normalize())).sum()),
        "kospi_yahoo_in_db_missing": int((~cal.isin(pd.to_datetime(ks["period_end"]))).sum()),
    }
    return out


def dart_product_test(firms: pd.DataFrame, first_year: int = 2019, last_year: int = 2025) -> pd.DataFrame:
    from ..stage2c import segments as S
    lists = output_path("2d", "K1", "cache", "lists")
    docs = output_path("2d", "K1", "cache", "annual_docs")
    rows = []
    for f in firms.itertuples():
        lf = lists / f"{f.ticker}.csv"
        if lf.exists():
            lst = pd.read_csv(lf, dtype=str)
        else:
            lst = events.fetch_list(f.corp_code, bgn=f"{first_year + 1}0101")
            lst.to_csv(lf, index=False, encoding="utf-8-sig")
        ann = []
        for r in lst.itertuples():
            m = re.match(r"^(\[[^\]]+\])?사업보고서\((\d{4})\.(\d{2})\)$", events.norm_title(r.report_nm))
            if m and not m.group(1):
                ann.append({"fy": int(m.group(2)), "rcept_no": r.rcept_no, "rcept_dt": r.rcept_dt})
        ann = pd.DataFrame(ann).sort_values("rcept_dt").drop_duplicates("fy") if ann else pd.DataFrame(columns=["fy"])
        for fy in range(first_year, last_year + 1):
            a = ann[ann["fy"] == fy]
            row = {"ticker": f.ticker, "name": f.name, "industry_group": f.industry_group, "fiscal_year": fy}
            if a.empty:
                rows.append({**row, "status": "사업보고서 없음"})
                continue
            t = events.doc_text(a["rcept_no"].iloc[0], docs)
            if t.startswith("__ERROR__"):
                rows.append({**row, "rcept_no": a["rcept_no"].iloc[0], "status": t[:60]})
                continue
            pat, s = S.find_section(t)
            pr = S.parse_rows(s) if pat else []
            keep = [r for r in pr if not S.SKIP.search(r["label"][-25:]) and r["amount"] > 0]
            pct = sum(r["pct"] for r in keep)
            ok = bool(pat) and len(keep) >= 1 and 90 <= pct <= 110
            rows.append({**row, "rcept_no": a["rcept_no"].iloc[0], "section": pat or "", "n_rows": len(keep),
                         "pct_sum": round(pct, 1), "status": "성공" if ok else ("표 못 찾음" if not pat else "비중 합 불일치"),
                         "labels": " | ".join(re.sub(r"\s+", " ", r["label"][-40:]) for r in keep[:4])})
    return pd.DataFrame(rows)


def pick_test_firms(u: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    d = u[u["target_2d"] & (u["coverage_class"] == "없음")].sort_values("rev_last4_sum_eok", ascending=False)
    return d.drop_duplicates("industry_group").head(n)


# ------------------------------------------------------------------ main
def _csv(df: pd.DataFrame, name: str, index: bool = False) -> None:
    df.to_csv(OUT / name, index=index, encoding="utf-8-sig")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for n in ("DATA.fdriver.adapters.revenue", "DATA.fdriver.adapters"):
        logging.getLogger(n).setLevel(logging.ERROR)
    from ..radar.universe import universe as radar_universe
    radar_ids = set(radar_universe()["entity_id"])

    u = base_universe()
    ids = u["ticker"].tolist()
    log.info("base %d", len(u))
    u = u.merge(consensus_coverage(ids), on="ticker", how="left")
    rev = revenue_panel(ids)
    u = u.merge(revenue_stats(rev, ids), on="ticker", how="left")
    rbc = revenue_basis_check(rev)
    pe = price_events(ids)
    u = u.merge(pe, on="ticker", how="left")
    u = apply_filters(u, radar_ids)
    fun = funnel_table(u)
    ct = crosstabs(u)

    best, allv, filings = op_panel(u.loc[u["f0_market"], "ticker"].tolist())
    ops = op_stats(best, ids)
    u = u.merge(ops, on="ticker", how="left")
    q4 = q4_check(allv, filings)

    def op_summary(mask, label):
        d = u[mask]
        return {"set": label, "firms": len(d), "op_any": int((d["n_op_all"] > 0).sum()),
                "op_full_2019_2023": int(d["op_full_sel"].sum()),
                "cogs_sga_full_2019_2023 (R2 가능)": int(d["cost_split_full_sel"].sum()),
                "cogs_partial": int(d["cost_split_partial"].sum()),
                "no_cogs (R3 후보, 성격별)": int(d["nature_of_expense"].sum()),
                "opex_account": int(d["has_opex_account"].sum())}
    ops_sum = pd.DataFrame([op_summary(u["f0_market"], "상장 보통주 전체"),
                            op_summary(u["pass_all_ex39"], "필터 통과 (39곳 제외)"),
                            op_summary(u["target_2d"], "확대 대상 (없음+저커버리지)")])

    drv = driver_inventory()
    kq = kosdaq_check()
    test_firms = pick_test_firms(u)
    dt = dart_product_test(test_firms)

    cols = ["ticker", "name", "market", "corp_code", "ksic", "ksic_div_name", "industry_group", "fin_holding_reason",
            "cons_q_8", "cons_obs_8", "cons_q_all", "coverage_class", "rev_q_total", "rev_q_sel", "rev_last4_n",
            "rev_last4_sum_eok", "price_events", "rev_jump_q", "rev_jump_quarters", "f0_market", "f1_rev_history",
            "f2_rev_size", "f3_price", "f4_corp_event_check", "f5_not_fin_holding", "in_radar39", "pass_all",
            "pass_all_ex39", "target_2d", "n_op_all", "n_op_sel", "n_cogs_all", "n_cogs_sel", "n_sga_all",
            "n_sga_sel", "n_opex_all", "op_sources", "op_full_sel", "cost_split_full_sel", "nature_of_expense",
            "has_opex_account"]
    _csv(u[cols], "k1_universe.csv")
    cov_counts = u[u["f0_market"]].groupby(["coverage_class", "cons_q_8"]).size().rename("firms").reset_index()
    _csv(cov_counts, "k1_coverage_counts.csv")
    _csv(fun, "k1_funnel.csv")
    _csv(ct["group"], "k1_crosstab_industry_group.csv", index=True)
    _csv(ct["ksic"], "k1_crosstab_ksic.csv", index=True)
    _csv(u[u["f0_market"] & (u["fin_holding_reason"] != "")][["ticker", "name", "market", "ksic", "ksic_div_name",
                                                              "fin_holding_reason", "pass_all", "coverage_class"]],
         "k1_fin_holding_excluded.csv")
    _csv(ops_sum, "k1_op_summary.csv")
    _csv(q4["agree"], "k1_op_dart_dg_agreement.csv")
    _csv(q4["dg_q4_annual"], "k1_op_q4_dg_annual.csv")
    _csv(q4["v2_cum"], "k1_op_v2_cumulative.csv")
    _csv(drv["inventory"], "k1_domestic_drivers.csv")
    _csv(drv["bok"], "k1_bok_indicators.csv")
    _csv(dt, "k1_dart_products_test.csv")
    _csv(rbc, "k1_revenue_basis_check.csv")

    summary = {
        "price_last_date": META.get("price_last_date"),
        "n_price_codes_current": META.get("n_price_codes_current"),
        "n_without_corp_code": META.get("n_without_corp_code"),
        "rev_basis_rows": len(rbc), "rev_basis_firms": int(rbc["entity_id"].nunique()),
        "rev_basis_firms_in_target": int(rbc.loc[rbc["entity_id"].isin(set(u.loc[u["target_2d"], "ticker"])),
                                                 "entity_id"].nunique()),
        "base_by_market": u["market"].value_counts(dropna=False).to_dict(),
        "coverage_class_listed": u[u["f0_market"]]["coverage_class"].value_counts().to_dict(),
        "funnel": fun.to_dict(orient="records"),
        "target_2d": int(u["target_2d"].sum()),
        "price_quarters_possible": pe.attrs.get("n_quarters_possible"),
        "fin_holding_by_reason": u[u["f0_market"]]["fin_holding_reason"].str.extract(r"^([^()]+)")[0]
        .value_counts().to_dict(),
        "corp_event_check_in_target": int((u["target_2d"] & u["f4_corp_event_check"]).sum()),
        "op_summary": ops_sum.to_dict(orient="records"),
        "q4_dg_eq_annual_rows": len(q4["dg_q4_annual"]),
        "q4_dg_eq_annual_firms": int(q4["dg_q4_annual"]["entity_id"].nunique()) if len(q4["dg_q4_annual"]) else 0,
        "v2_cumulative_rows": len(q4["v2_cum"]),
        "kosdaq": kq,
        "dart_test_success": dt["status"].value_counts().to_dict(),
    }
    (OUT / "k1_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
    pd.set_option("display.width", 220)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return u


if __name__ == "__main__":
    main()
