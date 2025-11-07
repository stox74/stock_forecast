#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Financial Data Normalizer (Robust/Strict 통합판)
- 분기: CompanyFactsParser.get_quarterly_data_robust() 사용 (Q4 복원)
- 연간: CompanyFactsParser.get_annual_data_strict() 사용 (FY 엄격)
- 태그 자동 탐색: Revenue는 detect_best_revenue_tag()를 우선
"""

from __future__ import annotations
from typing import Dict, List, Optional
import pandas as pd


class FinancialNormalizer:
    """재무데이터 표준화 (Robust/Strict)"""

    # XBRL 태그 후보 (우선순위 순)
    TAG_MAPPING: Dict[str, List[str]] = {
        # Revenue (매출)
        # ① detect_best_revenue_tag()가 먼저 시도되므로 아래는 백업 후보
        'revenue': [
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'SalesRevenueNet',
            'Revenues',
            'RevenueFromContractWithCustomerIncludingAssessedTax',
            'SalesRevenueGoodsNet',
        ],

        # Net Income (순이익)
        'net_income': [
            'NetIncomeLoss',
            'ProfitLoss',
            'NetIncomeLossAvailableToCommonStockholdersBasic',
        ],

        # Operating Income (영업이익)
        'operating_income': [
            'OperatingIncomeLoss',
            'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
        ],

        # Gross Profit (매출총이익)
        'gross_profit': ['GrossProfit'],

        # Total Assets (총자산)
        'total_assets': ['Assets'],

        # Current Assets (유동자산)
        'current_assets': ['AssetsCurrent'],

        # Total Liabilities (총부채)
        'total_liabilities': ['Liabilities'],

        # Current Liabilities (유동부채)
        'current_liabilities': ['LiabilitiesCurrent'],

        # Stockholders' Equity (자본)
        'stockholders_equity': [
            'StockholdersEquity',
            'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
        ],

        # Cash and Cash Equivalents (현금및현금성자산)
        'cash': [
            'CashAndCashEquivalentsAtCarryingValue',
            'Cash',
            'CashCashEquivalentsAndShortTermInvestments',
        ],

        # Long-term Debt (장기부채)
        'long_term_debt': ['LongTermDebt', 'LongTermDebtNoncurrent'],

        # Cost of Revenue (매출원가)
        'cost_of_revenue': ['CostOfRevenue', 'CostOfGoodsAndServicesSold'],

        # Operating Expenses (영업비용)
        'operating_expenses': ['OperatingExpenses', 'OperatingCostsAndExpenses'],

        # Research and Development (연구개발비)
        'research_development': ['ResearchAndDevelopmentExpense'],

        # Shares Outstanding (발행주식수)
        'shares_outstanding': [
            'CommonStockSharesOutstanding',
            'WeightedAverageNumberOfSharesOutstandingBasic',
        ],

        # EPS (주당순이익)
        'earnings_per_share': ['EarningsPerShareBasic', 'EarningsPerShareDiluted'],

        # ========== ROIC 계산을 위한 추가 태그 ==========

        # NOPAT 계산용
        # Income Tax Expense (법인세비용)
        'income_tax_expense': [
            'IncomeTaxExpenseBenefit',
            'CurrentIncomeTaxExpenseBenefit',
            'IncomeTaxesPaid',
        ],

        # Pretax Income (세전이익)
        'pretax_income': [
            'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
            'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments',
            'IncomeLossBeforeIncomeTaxes',
        ],

        # Interest Expense (이자비용)
        'interest_expense': [
            'InterestExpense',
            'InterestExpenseDebt',
            'InterestAndDebtExpense',
        ],

        # Interest Income (이자수익) - Net Interest 계산용
        'interest_income': [
            'InterestIncomeOther',
            'InvestmentIncomeInterest',
            'InterestAndDividendIncomeOperating',
        ],

        # Invested Capital 계산용 (순운전자본 접근법)
        # Property, Plant and Equipment, Net (순유형자산)
        'net_ppe': [
            'PropertyPlantAndEquipmentNet',
            'PropertyPlantAndEquipmentGross',  # 백업용 (감가상각 차감 전)
        ],

        # Accumulated Depreciation (감가상각누계액) - Gross PP&E를 사용할 경우 필요
        'accumulated_depreciation': [
            'AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment',
            'PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAccumulatedDepreciationAndAmortization',
        ],

        # Intangible Assets, Net (무형자산)
        'intangible_assets': [
            'IntangibleAssetsNetExcludingGoodwill',
            'FiniteLivedIntangibleAssetsNet',
            'IndefiniteLivedIntangibleAssetsExcludingGoodwill',
        ],

        # Goodwill (영업권)
        'goodwill': ['Goodwill'],

        # Other Non-current Assets (기타비유동자산)
        'other_noncurrent_assets': [
            'OtherAssetsNoncurrent',
            'DeferredCostsAndOtherAssets',
        ],

        # Short-term Debt (단기차입금)
        'short_term_debt': [
            'ShortTermBorrowings',
            'DebtCurrent',
            'ShortTermDebtAndCapitalLeaseObligations',
            'CommercialPaper',
        ],

        # Long-term Debt Current Portion (유동성장기부채)
        'long_term_debt_current': [
            'LongTermDebtCurrent',
            'LongTermDebtAndCapitalLeaseObligationsCurrent',
        ],

        # Total Debt (총부채) - 단기+장기
        'total_debt': [
            'DebtAndCapitalLeaseObligations',
            'LongTermDebtAndCapitalLeaseObligations',
        ],

        # Accounts Payable (매입채무)
        'accounts_payable': [
            'AccountsPayableCurrent',
            'AccountsPayableAndAccruedLiabilitiesCurrent',
        ],

        # Accounts Receivable (매출채권)
        'accounts_receivable': [
            'AccountsReceivableNetCurrent',
            'AccountsReceivableNet',
            'ReceivablesNetCurrent',
        ],

        # Inventory (재고자산)
        'inventory': [
            'InventoryNet',
            'InventoryGross',
        ],

        # Deferred Revenue (이연수익/선수금)
        'deferred_revenue': [
            'DeferredRevenueCurrent',
            'ContractWithCustomerLiabilityCurrent',
            'CustomerAdvancesAndDepositsCurrent',
        ],

        # Accrued Liabilities (미지급비용)
        'accrued_liabilities': [
            'AccruedLiabilitiesCurrent',
            'EmployeeRelatedLiabilitiesCurrent',
            'AccruedIncomeTaxesCurrent',
        ],

        # Working Capital (운전자본) - 직접 보고되는 경우
        'working_capital': [
            'WorkingCapital',
        ],

        # Non-Interest Bearing Current Liabilities (무이자유동부채)
        # 일반적으로 계산: Current Liabilities - Short-term Debt - Current Portion of Long-term Debt
        'non_interest_current_liabilities': [
            'AccountsPayableAndAccruedLiabilitiesCurrent',
            'OtherLiabilitiesCurrent',
        ],

        # Depreciation and Amortization (감가상각비) - Cash Flow 검증용
        'depreciation_amortization': [
            'DepreciationDepletionAndAmortization',
            'Depreciation',
            'AmortizationOfIntangibleAssets',
        ],

        # Capital Expenditures (자본적지출) - Free Cash Flow 계산용
        'capital_expenditures': [
            'PaymentsToAcquirePropertyPlantAndEquipment',
            'CapitalExpendituresIncurredButNotYetPaid',
        ],
    }

    def __init__(self, parser, taxonomy: str = 'us-gaap', unit: str = 'USD'):
        """
        Args:
            parser: CompanyFactsParser 인스턴스 (robust/strict 메서드가 구현되어 있어야 함)
            taxonomy: 기본 taxonomy (us-gaap)
            unit: 기본 통화 단위 (USD)
        """
        self.parser = parser
        self.taxonomy = taxonomy
        self.unit = unit
        self.normalized_data: Dict[str, pd.Series] = {}

    # -----------------------
    # 내부 헬퍼
    # -----------------------
    def _detect_first_available(self, candidates: List[str]) -> Optional[str]:
        """후보 태그 중 실제 데이터가 존재하는 첫 태그 반환"""
        for tag in candidates:
            df = self.parser.extract_tag_data(tag, taxonomy=self.taxonomy, unit=self.unit)
            if df is not None and not df.empty:
                return tag
        return None

    def _series_from_quarterly_df(self, dfq: Optional[pd.DataFrame]) -> Optional[pd.Series]:
        """분기 DF(['end','val',...]) → 시리즈(index=end, values=val)"""
        if dfq is None or dfq.empty:
            return None
        s = (dfq[['end', 'val']]
             .dropna()
             .drop_duplicates('end', keep='last')
             .sort_values('end')
             .set_index('end')['val'])
        # 숫자형 변환 시도
        try:
            s = s.astype(float)
        except Exception:
            pass
        return s

    def _series_from_annual_df(self, dfa: Optional[pd.DataFrame]) -> Optional[pd.Series]:
        """연간 DF(['end','val',...]) → 시리즈(index=end, values=val)"""
        if dfa is None or dfa.empty:
            return None
        s = (dfa[['end', 'val']]
             .dropna()
             .drop_duplicates('end', keep='last')
             .sort_values('end')
             .set_index('end')['val'])
        try:
            s = s.astype(float)
        except Exception:
            pass
        return s

    def _fetch_quarterly_series_robust(self, tag: str) -> Optional[pd.Series]:
        """robust 분기 시리즈(Q4 복원 포함)"""
        dfq = self.parser.get_quarterly_data_robust(tag, taxonomy=self.taxonomy, unit=self.unit)
        return self._series_from_quarterly_df(dfq)

    def _fetch_annual_series_strict(self, tag: str) -> Optional[pd.Series]:
        """strict 연간 시리즈(FY만, 10-K/20-F/40-F, frame의 Q/YTD 제거)"""
        dfa = self.parser.get_annual_data_strict(tag, taxonomy=self.taxonomy, unit=self.unit)
        return self._series_from_annual_df(dfa)

    # -----------------------
    # 공개 API
    # -----------------------
    def normalize_single_item(
        self,
        standard_name: str,
        period_type: str = 'quarterly',
        unit: Optional[str] = None
    ) -> Optional[pd.Series]:
        """
        단일 표준 항목을 정규화 (robust/strict 사용)
        - quarterly → robust(Q4 복원)
        - annual    → strict(FY 엄격)
        - any       → parser.create_time_series(tag, period_type='any')
        """
        if standard_name not in self.TAG_MAPPING:
            print(f"✗ Unknown standard name: {standard_name}")
            return None

        # 단위 override 허용
        if unit is None:
            unit = self.unit

        # Revenue는 detect_best_revenue_tag()를 우선 사용
        if standard_name == 'revenue':
            tag = self.parser.detect_best_revenue_tag(taxonomy=self.taxonomy, unit=unit)
            if not tag:
                tag = self._detect_first_available(self.TAG_MAPPING['revenue'])
        else:
            tag = self._detect_first_available(self.TAG_MAPPING[standard_name])

        if not tag:
            return None

        if period_type == 'quarterly':
            series = self._fetch_quarterly_series_robust(tag)
        elif period_type == 'annual':
            series = self._fetch_annual_series_strict(tag)
        elif period_type == 'any':
            # 가장 일반적인 원형: frame/FP 구분 없이 end별 최신값 시리즈
            s = self.parser.create_time_series(tag, taxonomy=self.taxonomy, unit=unit, period_type='any')
            series = s.astype(float) if s is not None else None
        else:
            raise ValueError("period_type must be one of: 'quarterly', 'annual', 'any'")

        if series is not None and not series.empty:
            series.name = standard_name
        return series

    def normalize_all_items(
        self,
        period_type: str = 'quarterly',
        unit: Optional[str] = None
    ) -> Dict[str, pd.Series]:
        """모든 표준 항목 정규화 (사전에 robust/strict 적용)"""
        results: Dict[str, pd.Series] = {}
        for standard_name in self.TAG_MAPPING.keys():
            s = self.normalize_single_item(standard_name, period_type=period_type, unit=unit)
            if s is not None and not s.empty:
                results[standard_name] = s
        self.normalized_data = results
        return results

    def create_normalized_dataframe(
        self,
        period_type: str = 'quarterly',
        unit: Optional[str] = None
    ) -> pd.DataFrame:
        """
        정규화된 데이터를 DataFrame으로 반환
        - quarterly → Q4 복원 반영
        - annual    → FY 엄격 반영
        """
        if not self.normalized_data:
            self.normalize_all_items(period_type=period_type, unit=unit)

        if not self.normalized_data:
            return pd.DataFrame()

        df = None
        for name, series in self.normalized_data.items():
            df = series.to_frame(name) if df is None else df.join(series.to_frame(name), how='outer')

        df.index.name = 'date'
        # 분기/연간 공통: 결측값 앞방향 보간 정도만 (필요 시 전략 변경)
        return df.sort_index().ffill()

    # -----------------------
    # 편의 기능 (기존과 동일)
    # -----------------------
    def convert_to_billions(self, df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
        df_copy = df.copy()
        if columns is None:
            columns = list(df_copy.columns)
        for col in columns:
            if col in df_copy.columns:
                df_copy[col] = df_copy[col] / 1_000_000_000
        return df_copy

    def calculate_ttm(self, df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
        df_copy = df.copy()
        if columns is None:
            columns = list(df_copy.columns)
        for col in columns:
            if col in df_copy.columns:
                df_copy[f'{col}_ttm'] = df_copy[col].rolling(window=4, min_periods=4).sum()
        return df_copy

    def calculate_growth_rates(self, df: pd.DataFrame, columns: Optional[List[str]] = None, periods: int = 4) -> pd.DataFrame:
        df_copy = df.copy()
        if columns is None:
            columns = list(df_copy.columns)
        for col in columns:
            if col in df_copy.columns:
                df_copy[f'{col}_yoy_growth'] = df_copy[col].pct_change(periods=periods) * 100
        return df_copy

    def calculate_financial_ratios(self, df: pd.DataFrame) -> pd.DataFrame:
        df_copy = df.copy()
        if 'revenue' in df_copy and 'net_income' in df_copy:
            df_copy['profit_margin'] = (df_copy['net_income'] / df_copy['revenue']) * 100
        if 'revenue' in df_copy and 'operating_income' in df_copy:
            df_copy['operating_margin'] = (df_copy['operating_income'] / df_copy['revenue']) * 100
        if 'current_assets' in df_copy and 'current_liabilities' in df_copy:
            df_copy['current_ratio'] = df_copy['current_assets'] / df_copy['current_liabilities']
        if 'total_liabilities' in df_copy and 'stockholders_equity' in df_copy:
            df_copy['debt_to_equity'] = df_copy['total_liabilities'] / df_copy['stockholders_equity']
        if 'net_income' in df_copy and 'stockholders_equity' in df_copy:
            df_copy['roe'] = (df_copy['net_income'] / df_copy['stockholders_equity']) * 100
        if 'net_income' in df_copy and 'total_assets' in df_copy:
            df_copy['roa'] = (df_copy['net_income'] / df_copy['total_assets']) * 100
        return df_copy

    def get_latest_financial_snapshot(self) -> Dict[str, float]:
        if not self.normalized_data:
            self.normalize_all_items()
        snapshot: Dict[str, float] = {}
        for name, series in self.normalized_data.items():
            if series is not None and not series.empty:
                try:
                    snapshot[name] = float(series.iloc[-1])
                except Exception:
                    pass
        return snapshot

    def export_to_csv(self, df: pd.DataFrame, filename: str):
        try:
            df.to_csv(filename, encoding='utf-8-sig')
            print(f"✓ Exported to {filename}")
        except Exception as e:
            print(f"✗ Failed to export: {e}")
