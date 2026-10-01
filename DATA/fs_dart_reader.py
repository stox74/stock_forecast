# fs_dart_reader.py
import pymysql
import pandas as pd
from typing import Dict, Any, List, Optional

# ✅ 직접 실행과 모듈 import 모두 지원
try:
    from .fs_core import (
        make_pivot,
        merge_similar_columns_smart,
        adjust_quarterly_q4_only,
        cumulative_to_quarterly,
    )
except ImportError:
    from fs_core import (
        make_pivot,
        merge_similar_columns_smart,
        adjust_quarterly_q4_only,
        cumulative_to_quarterly,
    )

# -------------------------
# Account group dicts
# -------------------------
account_groups = {
    "revenue": ["ifrs_Revenue", "ifrs-full_Revenue"],
    "gross_profit": ["ifrs_GrossProfit", "ifrs-full_GrossProfit"],
    "operating_income": ["dart_OperatingIncomeLoss"],
    "continuing_operations": ["ifrs_ProfitLossBeforeTax", "ifrs-full_ProfitLossBeforeTax"],
    "income_tax": ["ifrs_IncomeTaxExpenseContinuingOperations", "ifrs-full_IncomeTaxExpenseContinuingOperations"],
}

account_groups_bs = {
    "assets_total": ["ifrs_Assets", "ifrs-full_Assets"],
    "cash": ["ifrs_CashAndCashEquivalents", "ifrs-full_CashAndCashEquivalents"],
    "current_assets": ["ifrs_CurrentAssets", "ifrs-full_CurrentAssets"],
    "inventories": ["ifrs_Inventories", "ifrs-full_Inventories"],
    "liabilities_total": ["ifrs_Liabilities", "ifrs-full_Liabilities"],
    "current_liabilities": ["ifrs_CurrentLiabilities", "ifrs-full_CurrentLiabilities"],
    "noncurrent_liabilities": ["ifrs_NoncurrentLiabilities", "ifrs-full_NoncurrentLiabilities"],
}

account_groups_cf = {
    "cf_operating": ["ifrs_CashFlowsFromUsedInOperatingActivities", "ifrs-full_CashFlowsFromUsedInOperatingActivities"],
    "cf_investing": ["ifrs_CashFlowsFromUsedInInvestingActivities", "ifrs-full_CashFlowsFromUsedInInvestingActivities"],
    "cf_financing": ["ifrs_CashFlowsFromUsedInFinancingActivities", "ifrs-full_CashFlowsFromUsedInFinancingActivities"],
    "cf_tax_operating": ["ifrs_IncomeTaxesPaidRefundClassifiedAsOperatingActivities", "ifrs-full_IncomeTaxesPaidRefundClassifiedAsOperatingActivities"],
    "cf_dividends_paid": ["ifrs_DividendsPaidClassifiedAsFinancingActivities", "ifrs-full_DividendsPaidClassifiedAsFinancingActivities", "ifrs_DividendsPaid", "ifrs-full_DividendsPaid"],
}


def _connect(db_info: Dict[str, Any]):
    return pymysql.connect(
        host=db_info["host"],
        port=db_info["port"],
        user=db_info["user"],
        password=db_info["password"],
        database=db_info["database"],
        charset="utf8mb4"
    )


def fetch_dart_rows_by_ticker(
    db_info: Dict[str, Any],
    ticker: str,
    table_name: str = "korea_fs_data_from_DART",
    ticker_variants: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    DART 테이블에서 ticker로 원본 행을 읽어옵니다.
    ticker_variants를 넣으면 OR 조건으로 함께 조회합니다.
    """
    if ticker_variants is None:
        ticker_variants = [ticker, f"A{ticker}", f"{ticker}.KS", f"{ticker}.KQ"]

    placeholders = " OR ".join(["ticker=%s"] * len(ticker_variants))
    sql = f"""
        SELECT
            corp_code, bsns_year, reprt_code, quarter,
            account_id, sj_div, sj_nm, account_nm,
            thstrm_nm, thstrm_amount, report_date, ticker
        FROM {table_name}
        WHERE ({placeholders})
        ORDER BY report_date, bsns_year, reprt_code, sj_div, account_nm
    """

    conn = _connect(db_info)
    try:
        df = pd.read_sql(sql, conn, params=ticker_variants)
        if not df.empty:
            df["report_date"] = pd.to_datetime(df["report_date"])
        return df
    finally:
        conn.close()


def extract_is(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["sj_div"].isin(["IS", "CIS"])].copy()


def extract_bs(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["sj_div"].eq("BS")].copy()


def extract_cf(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["sj_div"].eq("CF")].copy()


def build_dart_fs_tables(df_all: pd.DataFrame) -> pd.DataFrame:
    """
    DART 원본을 받아 IS/BS/CF 표준 컬럼들로 pivot해서 합칩니다.
    (단위는 원본 그대로: thstrm_amount)
    """
    if df_all.empty:
        return pd.DataFrame()

    df_all = df_all.copy()
    df_all["report_date"] = pd.to_datetime(df_all["report_date"])

    # ---- IS ----
    rev_df = df_all[df_all["account_id"].isin(account_groups["revenue"])]
    gp_df  = df_all[df_all["account_id"].isin(account_groups["gross_profit"])]
    op_df  = df_all[df_all["account_id"].isin(account_groups["operating_income"])]
    pretax_df = df_all[df_all["account_id"].isin(account_groups["continuing_operations"])]
    tax_df = df_all[df_all["account_id"].isin(account_groups["income_tax"])]

    rev = make_pivot(rev_df, "매출액")
    gp  = make_pivot(gp_df, "매출이익")
    op  = make_pivot(op_df, "영업이익")
    pretax = make_pivot(pretax_df, "법인세비용차감전순이익")
    tax = make_pivot(tax_df, "법인세비용")

    is_parts = [x for x in [rev, gp, op, pretax, tax] if not x.empty]
    is_table = pd.concat(is_parts, axis=1) if is_parts else pd.DataFrame()
    if not is_table.empty:
        is_table = merge_similar_columns_smart(is_table)
        is_table = adjust_quarterly_q4_only(is_table, is_table.columns.tolist())
        # 당기순이익
        if "법인세비용차감전순이익" in is_table.columns and "법인세비용" in is_table.columns:
            is_table["당기순이익"] = is_table["법인세비용차감전순이익"] - is_table["법인세비용"]

    # ---- BS ----
    def bs_p(name, key):
        return make_pivot(df_all[(df_all["sj_div"] == "BS") & df_all["account_id"].isin(account_groups_bs[key])], name)

    assets = bs_p("자산이계", "assets_total")
    cash = bs_p("현금및현금성자산", "cash")
    ca = bs_p("유동자산", "current_assets")
    inv = bs_p("재고자산", "inventories")
    liab = bs_p("부채이계", "liabilities_total")

    bs_parts = [x for x in [assets, cash, ca, inv, liab] if not x.empty]
    bs_table = pd.concat(bs_parts, axis=1) if bs_parts else pd.DataFrame()
    if not bs_table.empty and "자산이계" in bs_table.columns and "부채이계" in bs_table.columns:
        bs_table["자본이계"] = bs_table["자산이계"] - bs_table["부채이계"]

    # ---- CF ----
    def cf_p(name, key):
        return make_pivot(df_all[(df_all["sj_div"] == "CF") & df_all["account_id"].isin(account_groups_cf[key])], name)

    cfo = cf_p("영업활동현금흐름", "cf_operating")
    cfi = cf_p("투자활동현금흐름", "cf_investing")
    cff = cf_p("재무활동현금흐름", "cf_financing")
    div = cf_p("배당금", "cf_dividends_paid")

    cf_parts = [x for x in [cfo, cfi, cff, div] if not x.empty]
    cf_table = pd.concat(cf_parts, axis=1) if cf_parts else pd.DataFrame()
    if not cf_table.empty:
        cf_table = cumulative_to_quarterly(cf_table, cf_table.columns.tolist())

    # ---- merge all ----
    parts = [x for x in [is_table, bs_table, cf_table] if isinstance(x, pd.DataFrame) and (not x.empty)]
    if not parts:
        return pd.DataFrame()

    fs = pd.concat(parts, axis=1)
    fs.index.name = "date"
    fs = fs.sort_index()
    return fs