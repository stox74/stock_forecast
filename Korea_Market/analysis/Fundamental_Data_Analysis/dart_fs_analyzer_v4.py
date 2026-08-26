# -*- coding: utf-8 -*-
"""
dart_fs_analyzer_v4.py
======================
korea_fs_data_from_DART_V3 (DART fnlttSinglAcntAll 원본 long 테이블) 기반 재무분석 유틸리티.
dataguide_fs_analyzer_v1 과 동일한 인터페이스(search / get_ts / yoy / qoq / turnaround / ratio)를
DART 데이터로 제공 → DataGuide 반영 전 2주 시차를 메꾸는 용도.

DART 테이블 스키마 (dart_korea_fs_loader_v7/v8)
    corp_code, bsns_year, reprt_code, row_key, sj_div(BS/IS/CIS/CF/SCE), ord, quarter(Q1/H1/Q3/FY),
    sj_nm, account_id, account_nm, thstrm_nm, thstrm_amount, thstrm_add_amount, fs_div, report_date, ticker
    금액 단위: 원

DataGuide 대비 처리해야 할 3가지
    1) 계정 매핑   : account_id 가 ifrs-full_/ifrs_/dart_ 접두어로 흔들리고, '-표준계정코드 미사용-' 도 있음
                     → 접두어 제거 후 local id 매칭, 실패 시 account_nm 정규식 매칭
    2) 분기화      : IS/CF 는 누적(또는 3개월) 값 → 분기 flow 로 변환 (Q4 = FY − 3Q 누적)
    3) 기업명      : DART 테이블에 없음 → DataGuide 테이블 등에서 매핑

파이프라인
    panel = build_quarterly_panel(engine)       # ticker × 분기 × concept  (1회, 캐시 권장)
    get_ts(panel, 'A278470', [...])
    yoy_screen(panel, '매출액') / qoq_screen / turnaround_screen / ratio_screen

v4 변경
    - korea_dart_corp_master (로더 v4 에서 생성: corp_code, stock_code, corp_name, acc_mt, corp_cls) 사용
        · 기업명 1순위 소스
        · acc_mt(결산월) 로 비12월 결산 법인의 분기를 달력 분기로 변환 (3월 결산: bsns_year Y 의 Q1 = Y년 4~6월 = Y Q2)
        · DART 상장사 유니버스 판정 → diagnose_missing 에 'not_in_dart'(DataGuide 전용 코드) 구분 추가
Python 3.9 호환 (typing.Optional / Union)
"""

from typing import Optional, Union, List, Dict, Sequence, Tuple
import re
import numpy as np
import pandas as pd
from sqlalchemy import text, bindparam
from sqlalchemy.engine import Engine

TABLE_DART = "korea_fs_data_from_DART_V3"
TABLE_DG   = "korea_fs_data_from_DG"          # 기업명 매핑용 (없으면 None 처리)
TABLE_CORP = "korea_dart_corp_master"         # 로더 v4 가 만드는 기업 마스터 (corp_name, acc_mt)
UNUSED_TAG = "미사용"

# quarter 라벨 → 분기 번호
Q_OF_LABEL = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}

# ----------------------------------------------------------------------
# 계정 매핑 정의
#   ids  : 접두어(ifrs-full_/ifrs_/dart_) 제거한 local id, 우선순위 순
#   names: account_nm 정규식 (공백 제거 후 매칭). id 매칭 실패 시 사용 (미사용 계정 포함)
#   agg  : 동일 보고서 내 다중 매칭 시 'first'(ord 최소) / 'sum'
#   sj   : 재무제표 구분 (IS 항목은 IS 또는 CIS(포괄손익계산서 단일 표시) 둘 다 허용, IS 우선)
# ----------------------------------------------------------------------
CONCEPTS: Dict[str, Dict] = {
    # ---------------- IS ----------------
    "매출액":   dict(sj="IS", ids=["Revenue", "OperatingRevenue", "RevenueFromSaleOfGoods", "RevenueFromRenderingOfServices",
                                  "GrossRevenue", "SalesRevenue", "TotalRevenue"],
                   names=[r"^매출액$", r"^수익\(매출액\)$", r"^매출$", r"^영업수익$", r"^매출및지분법손익$", r"^수익$",
                          r"^매출액\(수익\)$", r"^매출수익$", r"^총매출액$", r"^매출액및기타수익$", r"^매출및기타수익$", r"^매출액\(주석\d*\)$", r"^매출액합계$", r"^순매출액$",
                          r"^영업수익\(매출액\)$", r"^매출\(영업수익\)$", r"^매출액\(영업수익\)$"], agg="first"),
    "매출원가": dict(sj="IS", ids=["CostOfSales"], names=[r"^매출원가$", r"^영업비용$"], agg="first"),
    "매출총이익": dict(sj="IS", ids=["GrossProfit"], names=[r"^매출총이익$", r"^매출총이익\(손실\)$"], agg="first"),
    "영업이익": dict(sj="IS", ids=["OperatingIncomeLoss"],
                   names=[r"^영업이익$", r"^영업이익\(손실\)$", r"^영업손실$", r"^영업\(손실\)이익$", r"^영업손익$"], agg="first"),
    "세전이익": dict(sj="IS", ids=["ProfitLossBeforeTax"],
                   names=[r"^법인세비용차감전순이익", r"^법인세비용차감전순손익", r"^법인세차감전순이익", r"^법인세비용차감전계속영업"], agg="first"),
    "법인세":   dict(sj="IS", ids=["IncomeTaxExpenseContinuingOperations", "IncomeTaxExpense"],
                   names=[r"^법인세비용", r"^법인세비용\(수익\)$"], agg="first"),
    "당기순이익": dict(sj="IS", ids=["ProfitLoss"],
                   names=[r"^당기순이익$", r"^당기순이익\(손실\)$", r"^당기순손익$", r"^분기순이익", r"^반기순이익", r"^당기순손실$"], agg="first"),
    "지배주주순이익": dict(sj="IS", ids=["ProfitLossAttributableToOwnersOfParent"],
                   names=[r"^지배기업.*소유주.*순이익", r"^지배기업의소유주지분", r"^지배주주지분순이익", r"^지배기업소유주"], agg="first"),
    "금융비용": dict(sj="IS", ids=["FinanceCosts"], names=[r"^금융비용$", r"^금융원가$", r"^이자비용$"], agg="first"),
    # ---------------- BS ----------------
    "자산":     dict(sj="BS", ids=["Assets"], names=[r"^자산총계$"], agg="first"),
    "유동자산": dict(sj="BS", ids=["CurrentAssets"], names=[r"^유동자산$"], agg="first"),
    "현금":     dict(sj="BS", ids=["CashAndCashEquivalents"], names=[r"^현금및현금성자산$"], agg="first"),
    "단기금융상품": dict(sj="BS", ids=["ShortTermDepositsNotClassifiedAsCashEquivalents", "ShorttermInvestments",
                                    "CurrentFinancialAssetsAtAmortisedCost"],
                   names=[r"^단기금융상품$", r"^단기금융자산$", r"^단기투자자산$"], agg="first"),
    "매출채권": dict(sj="BS", ids=["ShortTermTradeReceivable", "TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"],
                   names=[r"^매출채권$", r"^매출채권및기타채권$", r"^매출채권및기타유동채권$"], agg="first"),
    "재고자산": dict(sj="BS", ids=["Inventories"], names=[r"^재고자산$"], agg="first"),
    "부채":     dict(sj="BS", ids=["Liabilities"], names=[r"^부채총계$"], agg="first"),
    "유동부채": dict(sj="BS", ids=["CurrentLiabilities"], names=[r"^유동부채$"], agg="first"),
    "비유동부채": dict(sj="BS", ids=["NoncurrentLiabilities"], names=[r"^비유동부채$"], agg="first"),
    "매입채무": dict(sj="BS", ids=["ShortTermTradePayables", "TradeAndOtherCurrentPayables", "CurrentTradePayables"],
                   names=[r"^매입채무$", r"^매입채무및기타채무$", r"^매입채무및기타유동채무$"], agg="first"),
    "단기차입금": dict(sj="BS", ids=["ShortTermBorrowings", "ShorttermBorrowings"], names=[r"^단기차입금$"], agg="first"),
    "유동성장기부채": dict(sj="BS", ids=["CurrentPortionOfLongTermBorrowingsAndDebentures", "CurrentPortionOfLongtermBorrowings",
                                       "CurrentPortionOfLongTermBorrowings", "CurrentPortionOfDebentures"],
                   names=[r"^유동성장기부채$", r"^유동성장기차입금$", r"^유동성사채$", r"^유동성장기차입금및사채$"], agg="sum"),
    "사채":     dict(sj="BS", ids=["LongTermDebentures", "Debentures", "BondsIssued"], names=[r"^사채$", r"^장기사채$"], agg="first"),
    "장기차입금": dict(sj="BS", ids=["LongTermBorrowings", "LongtermBorrowings", "NoncurrentBorrowings"],
                   names=[r"^장기차입금$"], agg="first"),
    "리스부채": dict(sj="BS", ids=["LeaseLiabilities", "CurrentLeaseLiabilities", "NoncurrentLeaseLiabilities",
                                 "LongTermLeaseLiabilities", "ShortTermLeaseLiabilities"],
                   names=[r"^리스부채$", r"^유동리스부채$", r"^비유동리스부채$", r"^장기리스부채$", r"^유동성리스부채$"], agg="sum"),
    "자본":     dict(sj="BS", ids=["Equity"], names=[r"^자본총계$"], agg="first"),
    "지배주주지분": dict(sj="BS", ids=["EquityAttributableToOwnersOfParent"],
                   names=[r"^지배기업.*소유주.*지분", r"^지배기업소유주지분$", r"^지배주주지분$"], agg="first"),
    "비지배지분": dict(sj="BS", ids=["NoncontrollingInterests"], names=[r"^비지배지분$", r"^비지배주주지분$"], agg="first"),
    # ---------------- CF ----------------
    "영업현금흐름": dict(sj="CF", ids=["CashFlowsFromUsedInOperatingActivities"],
                   names=[r"^영업활동현금흐름$", r"^영업활동으로인한현금흐름$", r"^영업활동순현금흐름$"], agg="first"),
    "투자현금흐름": dict(sj="CF", ids=["CashFlowsFromUsedInInvestingActivities"],
                   names=[r"^투자활동현금흐름$", r"^투자활동으로인한현금흐름$"], agg="first"),
    "재무현금흐름": dict(sj="CF", ids=["CashFlowsFromUsedInFinancingActivities"],
                   names=[r"^재무활동현금흐름$", r"^재무활동으로인한현금흐름$"], agg="first"),
    "유형자산취득": dict(sj="CF", ids=["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
                   names=[r"^유형자산의취득$", r"^유형자산의증가$", r"^유형자산취득$"], agg="first"),
    "무형자산취득": dict(sj="CF", ids=["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"],
                   names=[r"^무형자산의취득$", r"^무형자산의증가$", r"^무형자산취득$"], agg="first"),
    "배당금지급": dict(sj="CF", ids=["DividendsPaidClassifiedAsFinancingActivities"],
                   names=[r"^배당금의지급$", r"^배당금지급$"], agg="first"),
}
FLOW_SJ = {"IS", "CIS", "CF"}      # 분기화 대상 (BS 는 잔액)
SJ_ALT = {"IS": ["IS", "CIS"]}     # 대체 재무제표 (우선순위 순)


def _sj_list(c: Dict) -> List[str]:
    return SJ_ALT.get(c["sj"], [c["sj"]])


# ======================================================================
# 내부 헬퍼
# ======================================================================
def _norm_tk(tk: str) -> str:
    tk = str(tk).strip().upper()
    return "A" + tk if re.fullmatch(r"\d{6}", tk) else tk


def _raw_tk(tk: str) -> str:
    """DART 테이블 저장 형식 (6자리, A 없음)"""
    return _norm_tk(tk)[1:]


def _local_id(s: pd.Series) -> pd.Series:
    """ifrs-full_Revenue / ifrs_Revenue / dart_OperatingIncomeLoss → Revenue / Revenue / OperatingIncomeLoss"""
    return s.fillna("").str.replace(r"^(ifrs-full|ifrs|dart|entity\d+)_", "", regex=True)


def _norm_nm(s: pd.Series) -> pd.Series:
    return s.fillna("").str.replace(r"\s+", "", regex=True).str.replace(r"Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|^[0-9]+\.", "", regex=True)


def _all_ids() -> List[str]:
    return sorted({i for c in CONCEPTS.values() for i in c["ids"]})


def load_corp_master(engine: Optional[Engine]) -> pd.DataFrame:
    """korea_dart_corp_master → index=ticker(A######), cols: corp_code, corp_name, acc_mt(int), corp_cls. 없으면 빈 DF."""
    if engine is None:
        return pd.DataFrame(columns=["corp_code", "corp_name", "acc_mt", "corp_cls"])
    try:
        with engine.connect() as con:
            df = pd.read_sql(text(f"SELECT corp_code, stock_code, corp_name, acc_mt, corp_cls FROM {TABLE_CORP}"), con)
        df["ticker"] = df["stock_code"].astype(str).str.strip().str.zfill(6).map(_norm_tk)
        df["acc_mt"] = pd.to_numeric(df["acc_mt"], errors="coerce").fillna(12).astype(int)
        return df.drop_duplicates("ticker").set_index("ticker")[["corp_code", "corp_name", "acc_mt", "corp_cls"]]
    except Exception:
        return pd.DataFrame(columns=["corp_code", "corp_name", "acc_mt", "corp_cls"])


def _name_map(engine: Optional[Engine] = None, name_table: Optional[str] = TABLE_DG,
              use_fdr: bool = True, dart_api_key: Optional[str] = None) -> pd.Series:
    """
    ticker(A######) → company_name. 소스를 순서대로 합침 (앞 소스에 없는 티커만 뒤 소스로 보충)
      0) korea_dart_corp_master (로더 v4) — DART 전 상장사
      1) DataGuide 테이블 (KS/KQ 적재분만 있음 → 미적재 종목은 빈칸이 됨)
      2) FinanceDataReader KRX 상장 목록 (KOSPI/KOSDAQ/KONEX 전체)
      3) DART corpCode.xml (dart_api_key 전달 시)
    """
    parts: List[pd.Series] = []
    cm = load_corp_master(engine)
    if not cm.empty:
        parts.append(cm["corp_name"])
    if engine is not None and name_table:
        try:
            with engine.connect() as con:
                df = pd.read_sql(text(f"SELECT DISTINCT ticker, company_name FROM {name_table}"), con)
            parts.append(df.drop_duplicates("ticker").set_index("ticker")["company_name"])
        except Exception:
            pass
    if use_fdr:
        try:
            import FinanceDataReader as fdr
            lst = fdr.StockListing("KRX")
            code_col = "Code" if "Code" in lst.columns else "Symbol"
            lst = lst[[code_col, "Name"]].dropna()
            lst.index = lst[code_col].astype(str).str.zfill(6).map(_norm_tk)
            parts.append(lst["Name"])
        except Exception:
            pass
    if dart_api_key:
        try:
            import io, zipfile, requests, xml.etree.ElementTree as ET
            r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": dart_api_key}, timeout=60)
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                root = ET.parse(z.open([n for n in z.namelist() if n.endswith(".xml")][0])).getroot()
            rows = [(c.findtext("stock_code"), c.findtext("corp_name")) for c in root.findall("list")]
            dc = pd.DataFrame(rows, columns=["code", "name"])
            dc = dc[dc["code"].str.strip().astype(bool)]
            dc.index = dc["code"].str.strip().map(_norm_tk)
            parts.append(dc["name"])
        except Exception:
            pass
    if not parts:
        return pd.Series(dtype=object)
    out = parts[0]
    for p_ in parts[1:]:
        out = out.combine_first(p_)
    return out[~out.index.duplicated()]


def fill_names(panel: pd.DataFrame, engine: Optional[Engine] = None, name_table: Optional[str] = TABLE_DG,
               use_fdr: bool = True, dart_api_key: Optional[str] = None) -> pd.DataFrame:
    """이미 만든 panel 의 빈 company_name 을 보충 (패널 재구축 불필요)."""
    names = _name_map(engine, name_table, use_fdr, dart_api_key)
    p = panel.copy()
    blank = p["company_name"].isna() | (p["company_name"] == "")
    p.loc[blank, "company_name"] = p.loc[blank, "ticker"].map(names).fillna("")
    n_blank = p.loc[blank, "ticker"].nunique() - p.loc[blank & (p["company_name"] == ""), "ticker"].nunique()
    print(f"기업명 보충: {n_blank}개 종목, 여전히 빈칸: {p.loc[p['company_name'] == '', 'ticker'].nunique()}개")
    return p


# ======================================================================
# 1. 계정 검색
# ======================================================================
def search_accounts(engine: Engine, keyword: Optional[str] = None,
                    sj_div: Optional[str] = None, min_tickers: int = 1) -> pd.DataFrame:
    """
    DART 테이블에 적재된 계정 목록. keyword 는 account_id / account_nm 부분일치(대소문자 무시).
    반환: sj_div, account_id, account_nm, n_ticker, n_rows, min_year, max_year, mapped(매핑된 concept)
    """
    where = ["1=1"]
    params: Dict[str, object] = {}
    if keyword:
        where.append("(account_id LIKE :kw OR account_nm LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    if sj_div:
        where.append("sj_div = :sj")
        params["sj"] = sj_div
    sql = text(f"""
        SELECT sj_div, account_id, account_nm,
               COUNT(DISTINCT ticker) AS n_ticker, COUNT(*) AS n_rows,
               MIN(bsns_year) AS min_year, MAX(bsns_year) AS max_year
        FROM {TABLE_DART}
        WHERE {' AND '.join(where)}
        GROUP BY sj_div, account_id, account_nm
        HAVING n_ticker >= :mt
        ORDER BY sj_div, n_ticker DESC
    """)
    params["mt"] = min_tickers
    with engine.connect() as con:
        df = pd.read_sql(sql, con, params=params)
    # 어떤 concept 에 매핑되는지 표시
    lid, nnm = _local_id(df["account_id"]), _norm_nm(df["account_nm"])
    mapped = pd.Series("", index=df.index)
    for cname, c in CONCEPTS.items():
        hit = df["sj_div"].isin(_sj_list(c)) & (lid.isin(c["ids"]) | nnm.str.contains("|".join(c["names"]), regex=True))
        mapped[hit & (mapped == "")] = cname
    df.insert(0, "mapped", mapped)
    return df


# ======================================================================
# 2. 원본 로드 → concept 매핑 → 분기화  (핵심)
# ======================================================================
def load_mapped(
    engine: Engine,
    concepts: Optional[Sequence[str]] = None,
    tickers: Optional[Sequence[str]] = None,
    start_year: Optional[int] = None,
) -> pd.DataFrame:
    """
    DART 원본 중 concept 에 매핑되는 행만 로드.
    반환 컬럼: ticker, bsns_year, quarter, reprt_code, sj_div, fs_div, concept, amount, add_amount, report_date, ord
    """
    concepts = list(concepts) if concepts else list(CONCEPTS)
    ids = sorted({i for c in concepts for i in CONCEPTS[c]["ids"]})
    sjs = sorted({sj for c in concepts for sj in _sj_list(CONCEPTS[c])})

    # id 매칭 후보 + 미사용/미매칭 계정(이름 매칭용) 을 SQL 로 1차 필터
    id_like = " OR ".join([f"account_id LIKE :id{i} ESCAPE '!'" for i in range(len(ids))])
    params: Dict[str, object] = {f"id{i}": f"%!_{v}" for i, v in enumerate(ids)}   # '접두어_LocalId' 로 끝나는 것
    where = [f"sj_div IN :sjs", f"(({id_like}) OR account_id LIKE :unused OR account_id = '' OR account_id IS NULL)"]
    params["sjs"] = tuple(sjs)
    params["unused"] = f"%{UNUSED_TAG}%"
    if tickers:
        where.append("ticker IN :tks")
        params["tks"] = tuple(_raw_tk(t) for t in tickers)
    if start_year:
        where.append("bsns_year >= :sy")
        params["sy"] = start_year

    sql = text(f"""
        SELECT ticker, bsns_year, quarter, reprt_code, sj_div, fs_div, ord,
               account_id, account_nm, thstrm_amount, thstrm_add_amount, report_date
        FROM {TABLE_DART}
        WHERE {' AND '.join(where)}
    """).bindparams(bindparam("sjs", expanding=True))
    if tickers:
        sql = sql.bindparams(bindparam("tks", expanding=True))
    with engine.connect() as con:
        df = pd.read_sql(sql, con, params=params)
    if df.empty:
        return df

    # ---- concept 매핑 (id 우선, 실패 시 이름) ----
    lid = _local_id(df["account_id"])
    nnm = _norm_nm(df["account_nm"])
    df["concept"] = ""
    df["map_src"] = ""
    for cname in concepts:
        c = CONCEPTS[cname]
        sj_ok = df["sj_div"].isin(_sj_list(c))
        # id 매칭: 우선순위 반영 (ids 리스트 앞이 우선)
        for pri, aid in enumerate(c["ids"]):
            m = sj_ok & (lid == aid) & (df["concept"] == "")
            df.loc[m, "concept"] = cname
            df.loc[m, "map_src"] = f"id{pri}"
        m = sj_ok & (df["concept"] == "") & nnm.str.contains("|".join(c["names"]), regex=True)
        df.loc[m, "concept"] = cname
        df.loc[m, "map_src"] = "name"
    df = df[df["concept"] != ""].copy()

    # ---- 보고서 내 다중 매칭 정리 (fs_div CFS 우선, id 매칭 우선, ord 순) ----
    df["fs_rank"] = (df["fs_div"] != "CFS").astype(int)
    df["sj_rank"] = df.apply(lambda r: _sj_list(CONCEPTS[r["concept"]]).index(r["sj_div"]), axis=1)
    df["src_rank"] = df["map_src"].map(lambda s: 9 if s == "name" else int(s[2:]))
    df = df.sort_values(["ticker", "bsns_year", "reprt_code", "concept", "fs_rank", "sj_rank", "src_rank", "ord"])
    key = ["ticker", "bsns_year", "reprt_code", "concept"]
    # 1) 같은 보고서에 CFS/OFS 둘 다 있으면 CFS 만, IS/CIS 둘 다 있으면 IS 만
    best_fs = df.groupby(key)["fs_rank"].transform("min")
    df = df[df["fs_rank"] == best_fs]
    best_sj = df.groupby(key)["sj_rank"].transform("min")
    df = df[df["sj_rank"] == best_sj]
    # 2) agg
    parts = []
    for cname, g in df.groupby("concept"):
        if CONCEPTS[cname]["agg"] == "sum":
            a = g.groupby(key, as_index=False).agg(
                amount=("thstrm_amount", "sum"), add_amount=("thstrm_add_amount", lambda s: s.sum(min_count=1)),
                quarter=("quarter", "first"), sj_div=("sj_div", "first"), fs_div=("fs_div", "first"),
                report_date=("report_date", "first"), map_src=("map_src", "first"))
        else:
            a = g.groupby(key, as_index=False).agg(
                amount=("thstrm_amount", "first"), add_amount=("thstrm_add_amount", "first"),
                quarter=("quarter", "first"), sj_div=("sj_div", "first"), fs_div=("fs_div", "first"),
                report_date=("report_date", "first"), map_src=("map_src", "first"))
        parts.append(a)
    out = pd.concat(parts, ignore_index=True)
    out["ticker"] = out["ticker"].map(_norm_tk)
    out["qn"] = out["quarter"].map(Q_OF_LABEL)
    return out


def fiscal_to_calendar_q(bsns_year: pd.Series, qn: pd.Series, acc_mt: pd.Series) -> pd.PeriodIndex:
    """
    (사업연도, 회계분기, 결산월) → 달력 분기 Period.
    사업연도 시작월 S = acc_mt % 12 + 1. 회계분기 n 의 말월 = S + 3n − 1 (12 초과 시 익년).
      acc_mt=12 : Y Q1 → YQ1 (그대로)
      acc_mt=3  : Y Q1 → Y년 6월 → YQ2,  Y FY → (Y+1)년 3월 → (Y+1)Q1
    """
    S = acc_mt.fillna(12).astype(int) % 12 + 1
    end_m = S + 3 * qn.astype(int) - 1
    yr = bsns_year.astype(int) + (end_m > 12).astype(int)
    end_m = (end_m - 1) % 12 + 1
    return pd.PeriodIndex(yr.astype(str) + "Q" + ((end_m - 1) // 3 + 1).astype(str), freq="Q")


def quarterize(mapped: pd.DataFrame, missing_add: str = "cumulative",
               acc_mt: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    보고서 값 → 분기 값.
      BS      : 잔액 그대로 (src='bal')
      IS / CF :
        Q1                     : thstrm_amount                                   (src='q1')
        H1/Q3, add_amount 있음  : thstrm_amount 가 3개월값이므로 그대로 사용          (src='3m')
                                 (amount == add 인 회사는 누적만 제시한 것 → 누적차분)  (src='diff')
        H1/Q3, add_amount 없음  : missing_add='cumulative' → 누적으로 보고 직전 누적과 차분 (src='diff-nc')
                                 missing_add='quarter'    → 3개월값으로 간주            (src='3m-nc')
        FY                     : 연간 − Q3 누적                                    (src='fy')
      누적 차분은 직전 보고서가 실제로 존재할 때만 계산 (없으면 NaN → 'diff-miss' 로 별도 반환하지 않고 제외)
    직전 분기 없이도 H1/Q3 3개월값은 살아남으므로, Q1 보고서가 누락돼도 Q2 값은 확보된다.
    """
    df = mapped.copy()
    am = df["ticker"].map(acc_mt) if acc_mt is not None else pd.Series(12, index=df.index)
    df["acc_mt"] = am.fillna(12).astype(int)
    df["q"] = fiscal_to_calendar_q(df["bsns_year"], df["qn"], df["acc_mt"])

    bs = df[~df["sj_div"].isin(FLOW_SJ)].copy()
    bs["value"] = bs["amount"]
    bs["src"] = "bal"

    fl = df[df["sj_div"].isin(FLOW_SJ)].sort_values(["ticker", "concept", "q"]).copy()
    qn = fl["qn"]
    is_q1, is_fy, is_mid = qn == 1, qn == 4, qn.isin([2, 3])
    has_add = fl["add_amount"].notna()
    same = has_add & (fl["amount"] == fl["add_amount"])          # 누적만 제시 (3개월값 없음)
    three_m = is_mid & has_add & ~same                           # 3개월값 직접 사용 가능

    # 누적 시리즈
    cum = fl["amount"].astype(float).copy()
    cum[is_mid & has_add] = fl.loc[is_mid & has_add, "add_amount"]
    if missing_add == "quarter":
        # add 없는 H1/Q3 = 3개월값 → 누적은 직전 누적 + amount (직전이 없으면 NaN)
        cum[is_mid & ~has_add] = np.nan
    fl["cum"] = cum
    grp = fl.groupby(["ticker", "concept"])
    prev_q, prev_cum = grp["q"].shift(1), grp["cum"].shift(1)
    prev_fs = grp["fs_div"].shift(1)
    # 직전 보고서가 존재하고, 연결/개별(fs_div) 이 같을 때만 누적 차분 (CFS 누적 − OFS 누적 방지)
    contiguous = ((fl["q"] - 1) == prev_q) & (fl["fs_div"] == prev_fs)
    if missing_add == "quarter":
        fix = is_mid & ~has_add & contiguous
        fl.loc[fix, "cum"] = prev_cum[fix] + fl.loc[fix, "amount"]
        prev_cum = fl.groupby(["ticker", "concept"])["cum"].shift(1)

    diff = np.where(contiguous, fl["cum"] - prev_cum, np.nan)
    value = np.where(is_q1, fl["amount"],
            np.where(three_m, fl["amount"],
            np.where(is_mid & ~has_add & (missing_add == "quarter"), fl["amount"], diff)))
    src = np.where(is_q1, "q1",
          np.where(three_m, "3m",
          np.where(is_fy, "fy",
          np.where(has_add, "diff", np.where(missing_add == "quarter", "3m-nc", "diff-nc")))))
    fl["value"], fl["src"] = value, src

    cols = ["ticker", "q", "concept", "sj_div", "fs_div", "value", "src", "map_src", "acc_mt"]
    return pd.concat([bs[cols], fl[cols]], ignore_index=True).dropna(subset=["value"])


def build_quarterly_panel(
    engine: Engine,
    concepts: Optional[Sequence[str]] = None,
    tickers: Optional[Sequence[str]] = None,
    start_year: Optional[int] = None,
    missing_add: str = "cumulative",
    name_table: Optional[str] = TABLE_DG,
    use_fdr: bool = True,
    dart_api_key: Optional[str] = None,
) -> pd.DataFrame:
    """
    DART → 분기 패널 (long). 모든 스크리너의 입력.
    컬럼: ticker, company_name, q(달력 분기), concept, sj_div, fs_div, value(원), src, map_src, acc_mt(결산월)
    """
    m = load_mapped(engine, concepts, tickers, start_year)
    if m.empty:
        raise ValueError("매핑된 DART 데이터가 없습니다.")
    cm = load_corp_master(engine)
    p = quarterize(m, missing_add=missing_add, acc_mt=cm["acc_mt"] if not cm.empty else None)
    names = _name_map(engine, name_table, use_fdr, dart_api_key)
    p.insert(1, "company_name", p["ticker"].map(names).fillna(""))
    return p.reset_index(drop=True)


def coverage_report(panel: pd.DataFrame) -> pd.DataFrame:
    """concept 별 매핑 커버리지: 종목수, 이름매칭 비율, add-missing 비율, 최신 분기"""
    g = panel.groupby("concept")
    out = pd.DataFrame({
        "n_ticker": g["ticker"].nunique(),
        "n_rows": g.size(),
        "name_match_%": g["map_src"].apply(lambda s: (s == "name").mean() * 100),
        "add_missing_%": g["src"].apply(lambda s: s.isin(["diff-nc", "3m-nc"]).mean() * 100),
        "cis_%": g["sj_div"].apply(lambda s: (s == "CIS").mean() * 100),
        "max_q": g["q"].max().astype(str),
    })
    return out.sort_values("n_ticker", ascending=False)


# ======================================================================
# 패널 → 매트릭스 헬퍼
# ======================================================================
def _mat(panel: pd.DataFrame, concept: str) -> pd.DataFrame:
    sub = panel[panel["concept"] == concept]
    return sub.pivot_table(index="ticker", columns="q", values="value", aggfunc="last")


def _names(panel: pd.DataFrame) -> pd.Series:
    return panel.drop_duplicates("ticker").set_index("ticker")["company_name"]


def pick_asof_quarter(mat: pd.DataFrame, min_frac: float = 0.5) -> pd.Period:
    cnt = mat.notna().sum(axis=0)
    ok = cnt[cnt >= cnt.max() * min_frac]
    return ok.index.max()


def _derive(panel: pd.DataFrame) -> pd.DataFrame:
    """파생 concept 추가: 매출총이익(없으면 매출−원가), 세전이익 fallback, 부채(자산−자본)"""
    p = panel
    mats = {c: _mat(p, c) for c in ["매출액", "매출원가", "매출총이익", "자산", "자본", "부채", "당기순이익", "세전이익", "법인세"]
            if c in set(p["concept"])}
    extra = []

    def _fill(target: str, calc: pd.DataFrame, src: str):
        have = mats.get(target)
        calc = calc.stack().rename("value").reset_index()
        calc.columns = ["ticker", "q", "value"]
        if have is not None:
            hv = have.stack().rename("v").reset_index()
            hv.columns = ["ticker", "q", "v"]
            calc = calc.merge(hv, on=["ticker", "q"], how="left")
            calc = calc[calc["v"].isna()].drop(columns="v")
        calc["concept"], calc["sj_div"], calc["fs_div"], calc["src"], calc["map_src"] = target, "", "", src, "derived"
        if "acc_mt" in p.columns:
            calc["acc_mt"] = calc["ticker"].map(p.drop_duplicates("ticker").set_index("ticker")["acc_mt"]).fillna(12)
        calc["company_name"] = calc["ticker"].map(_names(p)).fillna("")
        extra.append(calc[p.columns])

    if "매출액" in mats and "매출원가" in mats:
        _fill("매출총이익", mats["매출액"] - mats["매출원가"], "derived")
    if "자산" in mats and "자본" in mats:
        _fill("부채", mats["자산"] - mats["자본"], "derived")
    if "당기순이익" in mats and "법인세" in mats:
        _fill("세전이익", mats["당기순이익"] + mats["법인세"], "derived")
    return pd.concat([p] + extra, ignore_index=True) if extra else p


# ======================================================================
# 3. 종목별 시계열
# ======================================================================
def get_ts(panel: pd.DataFrame, ticker: str, concepts: Sequence[str],
           start: Optional[str] = None, unit: float = 1e8) -> pd.DataFrame:
    """행=분기(Period Q), 열=concepts. unit=1e8 → 억원."""
    tk = _norm_tk(ticker)
    sub = panel[panel["ticker"] == tk]
    if sub.empty:
        raise ValueError(f"데이터 없음: {tk}")
    wide = sub.pivot_table(index="q", columns="concept", values="value", aggfunc="last")
    wide = wide.reindex(columns=list(concepts)) / unit
    if start:
        wide = wide[wide.index >= pd.Period(start, freq="Q")]
    wide.attrs["ticker"] = tk
    wide.attrs["company_name"] = sub["company_name"].iloc[0]
    return wide.sort_index()


def get_ts_with_src(panel: pd.DataFrame, ticker: str, concept: str) -> pd.DataFrame:
    """분기값 + 산출 근거(src/map_src) — 분기화가 제대로 됐는지 점검용"""
    tk = _norm_tk(ticker)
    sub = panel[(panel["ticker"] == tk) & (panel["concept"] == concept)]
    return sub[["q", "value", "src", "map_src", "fs_div"]].sort_values("q").set_index("q")


# ======================================================================
# 4~5. YoY / QoQ
# ======================================================================
def _growth_screen(panel, item, lag, n, asof, min_base, unit, ascending):
    mat = _mat(panel, item)
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    if q_t not in mat.columns or q_b not in mat.columns:
        raise ValueError(f"분기 데이터 부족: t={q_t}, base={q_b}")
    cur, base = mat[q_t], mat[q_b]
    out = pd.DataFrame({
        "ticker": mat.index,
        "company_name": _names(panel).reindex(mat.index).values,
        "base_q": str(q_b), "t_q": str(q_t),
        f"{item}(t-{lag})": base.values / unit,
        f"{item}(t)": cur.values / unit,
    })
    valid = (base > 0) & (base.abs() >= min_base) & cur.notna()
    out["growth_%"] = np.where(valid, (cur - base) / base.abs() * 100, np.nan)
    out = out.dropna(subset=["growth_%"]).sort_values("growth_%", ascending=ascending)
    return out.head(n).reset_index(drop=True)


def yoy_screen(panel: pd.DataFrame, item: str = "매출액", n: int = 30, asof: Optional[str] = None,
               min_base: float = 1e9, unit: float = 1e8, ascending: bool = False) -> pd.DataFrame:
    """YoY 상위 N (t vs t-4). min_base 원 단위 (1e9 = 10억원)."""
    return _growth_screen(panel, item, 4, n, asof, min_base, unit, ascending)


def qoq_screen(panel: pd.DataFrame, item: str = "매출액", n: int = 30, asof: Optional[str] = None,
               min_base: float = 1e9, unit: float = 1e8, ascending: bool = False) -> pd.DataFrame:
    """QoQ 상위 N (t vs t-1)."""
    return _growth_screen(panel, item, 1, n, asof, min_base, unit, ascending)


# ======================================================================
# 6. 흑자전환
# ======================================================================
def turnaround_screen(panel: pd.DataFrame, basis: str = "yoy", item: str = "영업이익",
                      asof: Optional[str] = None, unit: float = 1e8, min_profit: float = 0.0,
                      sort_by: str = "swing") -> pd.DataFrame:
    lag = 4 if basis.lower() == "yoy" else 1
    mat, rev = _mat(panel, item), _mat(panel, "매출액")
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    cur, base = mat[q_t], mat[q_b]
    sel = mat.index[((base < 0) & (cur > min_profit)).fillna(False)]
    rev_t = rev.reindex(sel)[q_t] if q_t in rev.columns else pd.Series(np.nan, index=sel)
    out = pd.DataFrame({
        "ticker": sel, "company_name": _names(panel).reindex(sel).values,
        "base_q": str(q_b), "t_q": str(q_t),
        f"{item}(t-{lag})": base[sel].values / unit, f"{item}(t)": cur[sel].values / unit,
        "swing": (cur[sel] - base[sel]).values / unit, "매출액(t)": rev_t.values / unit,
    })
    out["swing_%rev"] = out["swing"] / out["매출액(t)"] * 100
    key = "swing" if sort_by == "swing" else f"{item}(t)"
    return out.sort_values(key, ascending=False).reset_index(drop=True)


# ======================================================================
# 7. 재무비율
# ======================================================================
def _ttm(mat: pd.DataFrame) -> pd.DataFrame:
    return mat.T.rolling(4, min_periods=4).sum().T


def _avg2(mat: pd.DataFrame, lag: int = 4) -> pd.DataFrame:
    prev = mat.shift(lag, axis=1)
    return mat.where(prev.isna(), (mat + prev) / 2)


RATIO_COMPONENTS = {
    "OPM": ["매출액", "영업이익"], "NPM": ["매출액", "당기순이익"], "GPM": ["매출액", "매출총이익"],
    "ROE": ["당기순이익", "자본(평균)"], "ROE_지배": ["지배주주순이익", "지배주주지분(평균)"],
    "ROA": ["당기순이익", "자산(평균)"], "ROIC": ["영업이익", "유효세율_%", "투하자본(평균)"],
    "부채비율": ["부채", "자본"], "순차입금비율": ["순차입금", "자본"],
    "OCF_margin": ["매출액", "영업현금흐름"], "FCF_margin": ["매출액", "영업현금흐름", "CAPEX"],
}


def compute_ratios(panel: pd.DataFrame, asof: Optional[str] = None, ttm: bool = True,
                   default_tax: float = 0.22, unit: float = 1e8) -> pd.DataFrame:
    p = _derive(panel)
    need = ["매출액", "매출총이익", "영업이익", "세전이익", "법인세", "당기순이익", "지배주주순이익",
            "자산", "자본", "부채", "지배주주지분", "현금", "단기금융상품",
            "단기차입금", "유동성장기부채", "사채", "장기차입금", "리스부채",
            "영업현금흐름", "유형자산취득", "무형자산취득"]
    have = set(p["concept"])
    mats = {c: _mat(p, c) for c in need if c in have}
    all_q = sorted(set().union(*[m.columns for m in mats.values()]))
    all_tk = sorted(set().union(*[m.index for m in mats.values()]))
    empty = pd.DataFrame(np.nan, index=all_tk, columns=all_q)
    mats = {c: (mats[c].reindex(index=all_tk, columns=all_q) if c in mats else empty.copy()) for c in need}
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mats["매출액"])

    def flow(c):  return _ttm(mats[c])[q_t] if ttm else mats[c][q_t] * 4
    def end(c):   return mats[c][q_t]
    def avg(c):   return _avg2(mats[c])[q_t]

    rev, gp, op, ni = flow("매출액"), flow("매출총이익"), flow("영업이익"), flow("당기순이익")
    ni_p, ocf = flow("지배주주순이익"), flow("영업현금흐름")
    capex = flow("유형자산취득").abs().fillna(0) + flow("무형자산취득").abs().fillna(0)
    pretax, tax = flow("세전이익"), flow("법인세")
    debt_m = sum(mats[c].fillna(0) for c in ["단기차입금", "유동성장기부채", "사채", "장기차입금", "리스부채"])
    cash_m = mats["현금"].fillna(0) + mats["단기금융상품"].fillna(0)
    ic_m = mats["자본"] + debt_m - cash_m
    net_debt = (debt_m - cash_m)[q_t]
    eff_tax = (tax / pretax).where((pretax > 0) & (tax >= 0)).clip(0, 0.30).fillna(default_tax)
    nopat = op * (1 - eff_tax)

    o = pd.DataFrame(index=all_tk)
    o["company_name"] = _names(p).reindex(all_tk)
    o["t_q"] = str(q_t)
    for k, v in [("매출액", rev), ("매출총이익", gp), ("영업이익", op), ("당기순이익", ni), ("지배주주순이익", ni_p),
                 ("영업현금흐름", ocf), ("CAPEX", capex), ("자산(평균)", avg("자산")), ("자본(평균)", avg("자본")),
                 ("지배주주지분(평균)", avg("지배주주지분")), ("자산", end("자산")), ("자본", end("자본")),
                 ("부채", end("부채")), ("순차입금", net_debt), ("투하자본(평균)", _avg2(ic_m)[q_t])]:
        o[k] = v / unit
    o["유효세율_%"] = eff_tax * 100
    o["GPM"], o["OPM"], o["NPM"] = gp / rev * 100, op / rev * 100, ni / rev * 100
    o["ROE"] = ni / avg("자본") * 100
    o["ROE_지배"] = ni_p / avg("지배주주지분") * 100
    o["ROA"] = ni / avg("자산") * 100
    o["ROIC"] = nopat / _avg2(ic_m)[q_t] * 100
    o["부채비율"] = end("부채") / end("자본") * 100
    o["순차입금비율"] = net_debt / end("자본") * 100
    o["OCF_margin"] = ocf / rev * 100
    o["FCF_margin"] = (ocf - capex) / rev * 100

    o.loc[(avg("자본") <= 0) | (end("자본") <= 0), ["ROE", "ROIC", "부채비율", "순차입금비율"]] = np.nan
    o.loc[rev <= 0, ["GPM", "OPM", "NPM", "OCF_margin", "FCF_margin"]] = np.nan
    o.loc[_avg2(ic_m)[q_t] <= 0, "ROIC"] = np.nan
    o.index.name = "ticker"
    return o.reset_index()


def ratio_screen(panel: Optional[pd.DataFrame], ratio: str = "ROE", n: int = 30, asof: Optional[str] = None,
                 ttm: bool = True, ascending: bool = False, min_revenue: float = 1e9,
                 ratios_df: Optional[pd.DataFrame] = None, unit: float = 1e8) -> pd.DataFrame:
    """비율 상위 N. ratios_df 를 넘기면 재계산 없이 정렬만. min_revenue 는 원 단위 (TTM)."""
    ratio = ratio.upper() if ratio.lower() in ("opm", "npm", "gpm", "roe", "roa", "roic") else ratio
    if ratio not in RATIO_COMPONENTS:
        raise ValueError(f"지원 비율: {list(RATIO_COMPONENTS)}")
    r = ratios_df if ratios_df is not None else compute_ratios(panel, asof, ttm, unit=unit)
    r = r[r["매출액"] * unit >= min_revenue].dropna(subset=[ratio]).sort_values(ratio, ascending=ascending)
    others = [c for c in ["OPM", "NPM", "ROE", "ROA", "ROIC", "부채비율"] if c != ratio]
    cols = ["ticker", "company_name", "t_q"] + RATIO_COMPONENTS[ratio] + [ratio] + others
    return r[cols].head(n).reset_index(drop=True)


# ======================================================================
# 8. DataGuide 대조 (검증용)
# ======================================================================
def compare_with_dataguide(panel: pd.DataFrame, engine: Engine, ticker: str,
                           pairs: Optional[Dict[str, str]] = None, unit: float = 1e8) -> pd.DataFrame:
    """
    DART 분기값 vs DataGuide 값 대조. pairs: {DART concept: DG item_code}
    DG 는 천원 단위 → ×1e3 해서 원으로 맞춘 뒤 unit 으로 나눔.
    """
    pairs = pairs or {"매출액": "M000904001", "영업이익": "M000906001", "당기순이익": "M001212450",
                      "자산": "M001190010", "자본": "M001190380", "영업현금흐름": "M001390000"}
    tk = _norm_tk(ticker)
    sql = text(f"SELECT date, item_code, value FROM {TABLE_DG} WHERE ticker = :tk AND item_code IN :codes") \
        .bindparams(bindparam("codes", expanding=True))
    with engine.connect() as con:
        dg = pd.read_sql(sql, con, params={"tk": tk, "codes": tuple(pairs.values())})
    dg["q"] = pd.to_datetime(dg["date"]).dt.to_period("Q")
    dg = dg.pivot_table(index="q", columns="item_code", values="value", aggfunc="last") * 1e3
    inv = {v: k for k, v in pairs.items()}
    dg.columns = [f"{inv[c]}_DG" for c in dg.columns]
    dt_ = get_ts(panel, tk, list(pairs), unit=1.0)
    dt_.columns = [f"{c}_DART" for c in dt_.columns]
    out = dt_.join(dg, how="outer")
    for c in pairs:
        if f"{c}_DG" in out and f"{c}_DART" in out:
            out[f"{c}_diff%"] = (out[f"{c}_DART"] - out[f"{c}_DG"]) / out[f"{c}_DG"].abs() * 100
    val_cols = [c for c in out.columns if not c.endswith("diff%")]
    out[val_cols] = out[val_cols] / unit
    return out.sort_index()


# ======================================================================
# 9. 진단: DataGuide 에는 있는데 DART 패널에 없는 종목 원인 분류
# ======================================================================
def raw_rows(engine: Engine, ticker: str, year: int, sj_div: Optional[Sequence[str]] = None,
             keyword: Optional[str] = None) -> pd.DataFrame:
    """특정 종목·연도 DART 원본 행 (매핑 여부와 무관). 계정 확인용."""
    where = ["ticker = :tk", "bsns_year = :y"]
    params: Dict[str, object] = {"tk": _raw_tk(ticker), "y": year}
    if sj_div:
        where.append("sj_div IN :sj"); params["sj"] = tuple(sj_div)
    if keyword:
        where.append("(account_id LIKE :kw OR account_nm LIKE :kw)"); params["kw"] = f"%{keyword}%"
    sql = text(f"""SELECT quarter, reprt_code, sj_div, fs_div, ord, account_id, account_nm,
                          thstrm_amount, thstrm_add_amount
                   FROM {TABLE_DART} WHERE {' AND '.join(where)} ORDER BY reprt_code, sj_div, ord""")
    if sj_div:
        sql = sql.bindparams(bindparam("sj", expanding=True))
    with engine.connect() as con:
        return pd.read_sql(sql, con, params=params)


def diagnose_missing(engine: Engine, panel: pd.DataFrame, item: str = "매출액",
                     dg_item_code: str = "M000904001", asof: Optional[str] = None,
                     lag: int = 4) -> pd.DataFrame:
    """
    DataGuide 에 t·t-lag 값이 모두 있는데 DART 패널에 없는 종목을 찾아 원인 분류.
      reason:
        'not_in_dart'    : DART 상장사 마스터에 없는 티커 (DataGuide 전용 코드: 우선주 A0008Z0, 구주 등) → 무시
        'no_report'      : 해당 (연도, 보고서) DART 원본 행 자체가 없음 → 미제출 / 수집 누락 (증분 수집 재실행)
        'unmapped'       : 원본 행은 있으나 concept 매핑 실패 → 계정명 확인 후 CONCEPTS 보강
        'quarterize_nan' : 매핑은 됐으나 분기값 산출 실패 (직전 보고서 없음 등)
    컬럼: ticker, company_name, missing_q, reason, acc_mt, n_rows_report, candidate_accounts(매출·수익 포함 계정명 상위)
    비12월 결산 법인은 missing_q 가 달력 분기라 DART 보고서 라벨과 다름 → acc_mt 로 역산해 원본 행을 찾는다.
    """
    cm = load_corp_master(engine)
    c = CONCEPTS[item]
    mat = _mat(panel, item)
    q_t = pd.Period(asof, freq="Q") if asof else pick_asof_quarter(mat)
    q_b = q_t - lag
    sql = text(f"SELECT ticker, date, value FROM {TABLE_DG} WHERE item_code = :code")
    with engine.connect() as con:
        dg = pd.read_sql(sql, con, params={"code": dg_item_code})
    dg["q"] = pd.to_datetime(dg["date"]).dt.to_period("Q")
    dgm = dg.pivot_table(index="ticker", columns="q", values="value", aggfunc="last")
    if q_t not in dgm.columns or q_b not in dgm.columns:
        raise ValueError(f"DataGuide 에 {q_t}/{q_b} 없음")
    dg_ok = dgm.index[dgm[q_t].notna() & dgm[q_b].notna()]
    dart = mat.reindex(dg_ok)
    miss = [(tk, q) for tk in dg_ok for q in (q_t, q_b)
            if q not in dart.columns or pd.isna(dart.at[tk, q])]
    if not miss:
        return pd.DataFrame(columns=["ticker", "company_name", "missing_q", "reason", "acc_mt"])
    label = {1: "Q1", 2: "H1", 3: "Q3", 4: "FY"}

    def _fiscal(tk: str, q: pd.Period) -> Tuple[int, str, int]:
        """달력 분기 → (bsns_year, quarter 라벨, acc_mt).  fiscal_to_calendar_q 의 역함수"""
        am = int(cm["acc_mt"].get(tk, 12)) if (not cm.empty and tk in cm.index) else 12
        S = am % 12 + 1                       # 사업연도 시작월
        E = q.quarter * 3                     # 달력 분기 말월
        n = ((E - S + 1 - 3) % 12) // 3 + 1   # 회계분기 번호 1..4
        y = q.year - (1 if S + 3 * n - 1 > 12 else 0)
        return y, label[n], am

    tks = tuple({_raw_tk(t) for t, _ in miss})
    years = tuple({y for tk, q in miss for y in (_fiscal(tk, q)[0], q.year)})
    sql = text(f"""SELECT ticker, bsns_year, quarter, sj_div, account_id, account_nm
                   FROM {TABLE_DART} WHERE ticker IN :tks AND bsns_year IN :ys AND sj_div IN :sjs""") \
        .bindparams(bindparam("tks", expanding=True), bindparam("ys", expanding=True), bindparam("sjs", expanding=True))
    with engine.connect() as con:
        raw = pd.read_sql(sql, con, params={"tks": tks, "ys": years, "sjs": tuple(_sj_list(c))})
    raw["ticker"] = raw["ticker"].map(_norm_tk)
    names = _names(panel)
    mapped_keys = set(zip(panel.loc[panel["concept"] == item, "ticker"], panel.loc[panel["concept"] == item, "q"]))
    rows = []
    for tk, q in miss:
        fy, ql, am = _fiscal(tk, q)
        r = raw[(raw["ticker"] == tk) & (raw["bsns_year"] == fy) & (raw["quarter"] == ql)]
        if not cm.empty and tk not in cm.index:
            reason = "not_in_dart"
        elif r.empty:
            reason = "no_report"
        elif (tk, q) in mapped_keys:
            reason = "quarterize_nan"
        else:
            lid, nnm = _local_id(r["account_id"]), _norm_nm(r["account_nm"])
            hit = lid.isin(c["ids"]) | nnm.str.contains("|".join(c["names"]), regex=True)
            reason = "quarterize_nan" if hit.any() else "unmapped"
        cand = r[r["account_nm"].str.contains("매출|수익", na=False)]["account_nm"].unique()[:5]
        rows.append(dict(ticker=tk, company_name=names.get(tk, ""), missing_q=str(q), reason=reason, acc_mt=am,
                         dart_report=f"{fy} {ql}", n_rows_report=len(r), candidate_accounts=", ".join(cand)))
    out = pd.DataFrame(rows).sort_values(["reason", "ticker"]).reset_index(drop=True)
    print(out["reason"].value_counts().to_string())
    return out


def trace(engine: Engine, ticker: str, item: str = "영업이익", year: int = 2026,
          missing_add: str = "cumulative") -> Dict[str, pd.DataFrame]:
    """
    한 종목·한 항목의 원본 → 매핑 → 분기화 과정을 단계별로 출력. quarterize_nan 원인 추적용.
    """
    c = CONCEPTS[item]
    raw = raw_rows(engine, ticker, year, sj_div=_sj_list(c))
    lid, nnm = _local_id(raw["account_id"]), _norm_nm(raw["account_nm"])
    raw["id_hit"] = lid.isin(c["ids"])
    raw["nm_hit"] = nnm.str.contains("|".join(c["names"]), regex=True)
    cand = raw[raw["id_hit"] | raw["nm_hit"]]
    print(f"[1] 원본 후보 행 ({year}, {'/'.join(_sj_list(c))}): {len(cand)}개")
    print(cand[["quarter", "sj_div", "fs_div", "ord", "account_id", "account_nm",
                "thstrm_amount", "thstrm_add_amount", "id_hit", "nm_hit"]].to_string(index=False))
    m = load_mapped(engine, [item], [ticker], year - 1)
    print(f"\n[2] load_mapped 결과 (보고서당 1행): {len(m)}개")
    print(m[["bsns_year", "quarter", "sj_div", "fs_div", "amount", "add_amount", "map_src"]].to_string(index=False))
    cm = load_corp_master(engine)
    if not cm.empty and _norm_tk(ticker) in cm.index:
        print(f"    corp_master: {cm.loc[_norm_tk(ticker), 'corp_name']} / 결산월 {cm.loc[_norm_tk(ticker), 'acc_mt']}")
    q = quarterize(m, missing_add=missing_add, acc_mt=cm["acc_mt"] if not cm.empty else None)
    print(f"\n[3] quarterize 결과 (q = 달력 분기): {len(q)}개")
    print(q[["q", "sj_div", "fs_div", "value", "src"]].to_string(index=False))
    return {"raw": raw, "mapped": m, "quarterized": q}
