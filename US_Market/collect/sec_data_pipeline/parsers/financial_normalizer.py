#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Financial Data Normalizer
다양한 XBRL 태그를 표준 재무항목으로 매핑 및 정규화
"""

import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class FinancialNormalizer:
    """재무데이터 표준화"""
    
    # XBRL 태그 -> 표준 항목 매핑
    TAG_MAPPING = {
        # Revenue (매출)
        'revenue': [
            'Revenues',
            'RevenueFromContractWithCustomerExcludingAssessedTax',
            'SalesRevenueNet',
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
        'gross_profit': [
            'GrossProfit',
        ],
        
        # Total Assets (총자산)
        'total_assets': [
            'Assets',
        ],
        
        # Current Assets (유동자산)
        'current_assets': [
            'AssetsCurrent',
        ],
        
        # Total Liabilities (총부채)
        'total_liabilities': [
            'Liabilities',
        ],
        
        # Current Liabilities (유동부채)
        'current_liabilities': [
            'LiabilitiesCurrent',
        ],
        
        # Stockholders Equity (자본)
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
        'long_term_debt': [
            'LongTermDebt',
            'LongTermDebtNoncurrent',
        ],
        
        # Cost of Revenue (매출원가)
        'cost_of_revenue': [
            'CostOfRevenue',
            'CostOfGoodsAndServicesSold',
        ],
        
        # Operating Expenses (영업비용)
        'operating_expenses': [
            'OperatingExpenses',
            'OperatingCostsAndExpenses',
        ],
        
        # Research and Development (연구개발비)
        'research_development': [
            'ResearchAndDevelopmentExpense',
        ],
        
        # Shares Outstanding (발행주식수)
        'shares_outstanding': [
            'CommonStockSharesOutstanding',
            'WeightedAverageNumberOfSharesOutstandingBasic',
        ],
        
        # EPS (주당순이익)
        'earnings_per_share': [
            'EarningsPerShareBasic',
            'EarningsPerShareDiluted',
        ],
    }
    
    def __init__(self, parser):
        """
        Args:
            parser: CompanyFactsParser 인스턴스
        """
        self.parser = parser
        self.normalized_data = {}
    
    def normalize_single_item(self, standard_name: str, 
                             period_type: str = 'quarterly',
                             unit: str = 'USD') -> Optional[pd.Series]:
        """
        단일 표준 항목을 정규화
        
        Args:
            standard_name: 표준 항목명 (예: 'revenue', 'net_income')
            period_type: 'quarterly', 'annual', 또는 'any'
            unit: 단위
            
        Returns:
            정규화된 시계열 Series
        """
        if standard_name not in self.TAG_MAPPING:
            print(f"✗ Unknown standard name: {standard_name}")
            return None
        
        possible_tags = self.TAG_MAPPING[standard_name]
        
        # 우선순위대로 태그 시도
        for tag in possible_tags:
            series = self.parser.create_time_series(tag, period_type=period_type, unit=unit)
            if series is not None and not series.empty:
                series.name = standard_name
                return series
        
        return None
    
    def normalize_all_items(self, period_type: str = 'quarterly',
                           unit: str = 'USD') -> Dict[str, pd.Series]:
        """
        모든 표준 항목을 정규화
        
        Args:
            period_type: 'quarterly', 'annual', 또는 'any'
            unit: 단위
            
        Returns:
            {standard_name: Series} 딕셔너리
        """
        results = {}
        
        for standard_name in self.TAG_MAPPING.keys():
            series = self.normalize_single_item(standard_name, period_type, unit)
            if series is not None:
                results[standard_name] = series
        
        self.normalized_data = results
        return results
    
    def create_normalized_dataframe(self, period_type: str = 'quarterly',
                                   unit: str = 'USD') -> pd.DataFrame:
        """
        정규화된 데이터를 DataFrame으로 반환
        
        Args:
            period_type: 'quarterly', 'annual', 또는 'any'
            unit: 단위
            
        Returns:
            날짜를 인덱스로 하고, 각 표준 항목을 컬럼으로 하는 DataFrame
        """
        if not self.normalized_data:
            self.normalize_all_items(period_type, unit)
        
        if not self.normalized_data:
            return pd.DataFrame()
        
        # 모든 Series를 DataFrame으로 결합
        df = pd.DataFrame(self.normalized_data)
        df.index.name = 'date'
        
        return df
    
    def convert_to_billions(self, df: pd.DataFrame, 
                           columns: Optional[List[str]] = None) -> pd.DataFrame:
        """
        금액을 billions 단위로 변환
        
        Args:
            df: DataFrame
            columns: 변환할 컬럼 리스트 (None이면 모든 컬럼)
            
        Returns:
            변환된 DataFrame
        """
        df_copy = df.copy()
        
        if columns is None:
            columns = df_copy.columns
        
        for col in columns:
            if col in df_copy.columns:
                df_copy[col] = df_copy[col] / 1_000_000_000
        
        return df_copy
    
    def calculate_ttm(self, df: pd.DataFrame, 
                     columns: Optional[List[str]] = None) -> pd.DataFrame:
        """
        TTM (Trailing Twelve Months) 계산
        
        Args:
            df: 분기 데이터 DataFrame
            columns: TTM을 계산할 컬럼 리스트 (None이면 모든 컬럼)
            
        Returns:
            TTM이 추가된 DataFrame
        """
        df_copy = df.copy()
        
        if columns is None:
            columns = df_copy.columns
        
        for col in columns:
            if col in df_copy.columns:
                ttm_col_name = f'{col}_ttm'
                df_copy[ttm_col_name] = df_copy[col].rolling(window=4, min_periods=4).sum()
        
        return df_copy
    
    def calculate_growth_rates(self, df: pd.DataFrame,
                              columns: Optional[List[str]] = None,
                              periods: int = 4) -> pd.DataFrame:
        """
        성장률 계산 (YoY for quarterly data)
        
        Args:
            df: DataFrame
            columns: 성장률을 계산할 컬럼 리스트
            periods: 비교 기간 (4 = YoY for quarterly)
            
        Returns:
            성장률이 추가된 DataFrame
        """
        df_copy = df.copy()
        
        if columns is None:
            columns = df_copy.columns
        
        for col in columns:
            if col in df_copy.columns:
                growth_col_name = f'{col}_yoy_growth'
                df_copy[growth_col_name] = df_copy[col].pct_change(periods=periods) * 100
        
        return df_copy
    
    def calculate_financial_ratios(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        주요 재무비율 계산
        
        Args:
            df: 정규화된 재무 데이터 DataFrame
            
        Returns:
            재무비율이 추가된 DataFrame
        """
        df_copy = df.copy()
        
        # Profit Margin (순이익률)
        if 'revenue' in df_copy and 'net_income' in df_copy:
            df_copy['profit_margin'] = (df_copy['net_income'] / df_copy['revenue']) * 100
        
        # Operating Margin (영업이익률)
        if 'revenue' in df_copy and 'operating_income' in df_copy:
            df_copy['operating_margin'] = (df_copy['operating_income'] / df_copy['revenue']) * 100
        
        # Current Ratio (유동비율)
        if 'current_assets' in df_copy and 'current_liabilities' in df_copy:
            df_copy['current_ratio'] = df_copy['current_assets'] / df_copy['current_liabilities']
        
        # Debt to Equity (부채비율)
        if 'total_liabilities' in df_copy and 'stockholders_equity' in df_copy:
            df_copy['debt_to_equity'] = df_copy['total_liabilities'] / df_copy['stockholders_equity']
        
        # ROE (자기자본이익률)
        if 'net_income' in df_copy and 'stockholders_equity' in df_copy:
            df_copy['roe'] = (df_copy['net_income'] / df_copy['stockholders_equity']) * 100
        
        # ROA (총자산이익률)
        if 'net_income' in df_copy and 'total_assets' in df_copy:
            df_copy['roa'] = (df_copy['net_income'] / df_copy['total_assets']) * 100
        
        return df_copy
    
    def get_latest_financial_snapshot(self) -> Dict[str, float]:
        """
        가장 최근의 재무 스냅샷
        
        Returns:
            {항목명: 값} 딕셔너리
        """
        if not self.normalized_data:
            self.normalize_all_items()
        
        snapshot = {}
        
        for name, series in self.normalized_data.items():
            if not series.empty:
                snapshot[name] = series.iloc[-1]
        
        return snapshot
    
    def export_to_csv(self, df: pd.DataFrame, filename: str):
        """
        DataFrame을 CSV로 저장
        
        Args:
            df: DataFrame
            filename: 파일명
        """
        try:
            df.to_csv(filename, encoding='utf-8-sig')
            print(f"✓ Exported to {filename}")
        except Exception as e:
            print(f"✗ Failed to export: {e}")


def main():
    """테스트"""
    import sys
    sys.path.append('..')
    
    from collectors.sec_api_client import SECAPIClient
    from parsers.company_facts_parser import CompanyFactsParser
    
    # SEC API 클라이언트 생성
    user_agent = "MyCompany Research admin@mycompany.com"
    client = SECAPIClient(user_agent)
    
    # Apple Company Facts 가져오기
    print("Fetching Apple company facts...")
    company_facts = client.get_company_facts_by_ticker('AAPL')
    
    if not company_facts:
        print("✗ Failed to fetch company facts")
        return
    
    # Parser 생성
    parser = CompanyFactsParser(company_facts)
    
    # Normalizer 생성
    normalizer = FinancialNormalizer(parser)
    
    # 1. 모든 항목 정규화
    print("\n1. Normalizing all financial items...")
    normalized_data = normalizer.normalize_all_items(period_type='quarterly')
    print(f"  Normalized {len(normalized_data)} items")
    print(f"  Items: {list(normalized_data.keys())}")
    
    # 2. DataFrame 생성
    print("\n2. Creating normalized DataFrame...")
    df = normalizer.create_normalized_dataframe(period_type='quarterly')
    print(f"  Shape: {df.shape}")
    print(f"  Date range: {df.index.min()} ~ {df.index.max()}")
    print(f"\n  Last 4 quarters:")
    print(df[['revenue', 'net_income', 'total_assets']].tail(4))
    
    # 3. Billions 변환
    print("\n3. Converting to billions...")
    df_billions = normalizer.convert_to_billions(df)
    print(df_billions[['revenue', 'net_income']].tail(4))
    
    # 4. TTM 계산
    print("\n4. Calculating TTM...")
    df_ttm = normalizer.calculate_ttm(df_billions, columns=['revenue', 'net_income'])
    print(df_ttm[['revenue', 'revenue_ttm', 'net_income', 'net_income_ttm']].tail(4))
    
    # 5. 성장률 계산
    print("\n5. Calculating YoY growth...")
    df_growth = normalizer.calculate_growth_rates(df_billions, columns=['revenue', 'net_income'])
    print(df_growth[['revenue', 'revenue_yoy_growth']].tail(8))
    
    # 6. 재무비율 계산
    print("\n6. Calculating financial ratios...")
    df_ratios = normalizer.calculate_financial_ratios(df)
    if 'profit_margin' in df_ratios.columns:
        print(df_ratios[['revenue', 'net_income', 'profit_margin']].tail(4))
    
    # 7. 최신 스냅샷
    print("\n7. Latest financial snapshot...")
    snapshot = normalizer.get_latest_financial_snapshot()
    for key, value in list(snapshot.items())[:10]:
        print(f"  {key}: {value:,.0f}")
    
    # 8. CSV 저장
    print("\n8. Exporting to CSV...")
    normalizer.export_to_csv(df_billions, 'aapl_normalized_financials.csv')


if __name__ == "__main__":
    main()
