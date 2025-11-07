#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Financial Data Integration
FinancialNormalizer DataFrame에 재무비율 추가

사용법:
    from financial_data_integrator import integrate_financial_ratios

    df_with_ratios = integrate_financial_ratios(df_normalized)
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional


class FinancialDataIntegrator:
    """
    FinancialNormalizer의 DataFrame에 재무비율을 계산하여 통합
    """

    def __init__(self, normalized_df: pd.DataFrame):
        """
        Args:
            normalized_df: FinancialNormalizer.create_normalized_dataframe() 결과
        """
        self.df = normalized_df.copy()

        # date를 인덱스로 설정 (아직 안되어 있다면)
        if 'date' in self.df.columns and self.df.index.name != 'date':
            self.df.set_index('date', inplace=True)

        # 날짜 인덱스를 datetime으로 변환
        if not isinstance(self.df.index, pd.DatetimeIndex):
            self.df.index = pd.to_datetime(self.df.index)

    def _safe_divide(self, numerator: pd.Series, denominator: pd.Series,
                     multiplier: float = 1.0) -> pd.Series:
        """안전한 나눗셈 (0으로 나누기 방지)"""
        result = pd.Series(np.nan, index=numerator.index)
        mask = (denominator != 0) & denominator.notna() & numerator.notna()
        result[mask] = (numerator[mask] / denominator[mask]) * multiplier
        return result

    def _get_average_value(self, column: str) -> pd.Series:
        """
        기초와 기말의 평균값 계산 (전년도 데이터와 평균)
        """
        if column not in self.df.columns:
            return pd.Series(np.nan, index=self.df.index)

        current = self.df[column]
        previous = current.shift(4)  # 4분기 전 (연간 기준)

        # 평균 계산
        avg = (current + previous) / 2

        # 이전 데이터가 없으면 현재 값 사용
        avg = avg.fillna(current)

        return avg

    def calculate_tax_rate(self) -> pd.Series:
        """실효세율 계산"""
        tax_expense = self.df.get('income_tax_expense', pd.Series(np.nan, index=self.df.index))
        pretax_income = self.df.get('pretax_income', pd.Series(np.nan, index=self.df.index))

        tax_rate = self._safe_divide(tax_expense, pretax_income, 1.0)

        # 합리적인 범위로 제한 (0-50%)
        tax_rate = tax_rate.clip(0, 0.5)

        # 결측값은 기본 세율로 대체 (21%)
        tax_rate = tax_rate.fillna(0.21)

        return tax_rate

    def calculate_nopat(self) -> pd.Series:
        """NOPAT 계산"""
        operating_income = self.df.get('operating_income', pd.Series(np.nan, index=self.df.index))
        tax_rate = self.calculate_tax_rate()

        nopat = operating_income * (1 - tax_rate)

        return nopat

    def calculate_net_working_capital(self) -> pd.Series:
        """순운전자본 계산"""
        current_assets = self.df.get('current_assets', pd.Series(np.nan, index=self.df.index))
        current_liabilities = self.df.get('current_liabilities', pd.Series(np.nan, index=self.df.index))
        short_term_debt = self.df.get('short_term_debt', pd.Series(0, index=self.df.index)).fillna(0)
        long_term_debt_current = self.df.get('long_term_debt_current', pd.Series(0, index=self.df.index)).fillna(0)

        interest_bearing_current = short_term_debt + long_term_debt_current
        non_interest_bearing_current = current_liabilities - interest_bearing_current

        nwc = current_assets - non_interest_bearing_current

        return nwc

    def calculate_net_fixed_assets(self) -> pd.Series:
        """순고정자산 계산"""
        net_ppe = self.df.get('net_ppe', pd.Series(np.nan, index=self.df.index))
        intangible_assets = self.df.get('intangible_assets', pd.Series(0, index=self.df.index)).fillna(0)
        goodwill = self.df.get('goodwill', pd.Series(0, index=self.df.index)).fillna(0)

        net_fixed_assets = net_ppe + intangible_assets + goodwill

        return net_fixed_assets

    def calculate_invested_capital(self) -> pd.Series:
        """투하자본 계산"""
        nwc = self.calculate_net_working_capital()
        net_fixed_assets = self.calculate_net_fixed_assets()

        invested_capital = nwc + net_fixed_assets

        return invested_capital

    def add_profitability_ratios(self):
        """수익성 비율 추가"""

        # NOPAT 및 투하자본 계산
        nopat = self.calculate_nopat()
        invested_capital = self.calculate_invested_capital()
        avg_invested_capital = self._get_average_value_series(invested_capital)

        # ROIC
        self.df['roic'] = self._safe_divide(nopat, avg_invested_capital, 100)

        # ROA
        net_income = self.df.get('net_income', pd.Series(np.nan, index=self.df.index))
        avg_total_assets = self._get_average_value('total_assets')
        self.df['roa'] = self._safe_divide(net_income, avg_total_assets, 100)

        # ROE
        avg_equity = self._get_average_value('stockholders_equity')
        self.df['roe'] = self._safe_divide(net_income, avg_equity, 100)

        # Profit Margins
        revenue = self.df.get('revenue', pd.Series(np.nan, index=self.df.index))

        if 'gross_profit' in self.df.columns:
            self.df['gross_margin'] = self._safe_divide(self.df['gross_profit'], revenue, 100)

        if 'operating_income' in self.df.columns:
            self.df['operating_margin'] = self._safe_divide(self.df['operating_income'], revenue, 100)

        self.df['net_margin'] = self._safe_divide(net_income, revenue, 100)

    def add_leverage_ratios(self):
        """레버리지 비율 추가"""

        total_liabilities = self.df.get('total_liabilities', pd.Series(np.nan, index=self.df.index))
        total_assets = self.df.get('total_assets', pd.Series(np.nan, index=self.df.index))
        equity = self.df.get('stockholders_equity', pd.Series(np.nan, index=self.df.index))

        # 부채비율 (D/E)
        self.df['debt_to_equity'] = self._safe_divide(total_liabilities, equity, 100)

        # 부채비율 (D/A)
        self.df['debt_to_assets'] = self._safe_divide(total_liabilities, total_assets, 100)

        # 자기자본승수
        self.df['equity_multiplier'] = self._safe_divide(total_assets, equity, 1)

    def add_liquidity_ratios(self):
        """유동성 비율 추가"""

        current_assets = self.df.get('current_assets', pd.Series(np.nan, index=self.df.index))
        current_liabilities = self.df.get('current_liabilities', pd.Series(np.nan, index=self.df.index))

        # 유동비율
        self.df['current_ratio'] = self._safe_divide(current_assets, current_liabilities, 1)

        # 당좌비율
        if 'inventory' in self.df.columns:
            quick_assets = current_assets - self.df['inventory'].fillna(0)
            self.df['quick_ratio'] = self._safe_divide(quick_assets, current_liabilities, 1)

    def add_efficiency_ratios(self):
        """효율성 비율 추가"""

        revenue = self.df.get('revenue', pd.Series(np.nan, index=self.df.index))

        # 재고자산회전율
        if 'inventory' in self.df.columns and 'cost_of_revenue' in self.df.columns:
            avg_inventory = self._get_average_value('inventory')
            cost_of_revenue = self.df['cost_of_revenue']
            self.df['inventory_turnover'] = self._safe_divide(cost_of_revenue, avg_inventory, 1)

            # 재고자산회전일수
            self.df['days_inventory'] = self._safe_divide(
                pd.Series(365, index=self.df.index),
                self.df['inventory_turnover'],
                1
            )

        # 매출채권회전율
        if 'accounts_receivable' in self.df.columns:
            avg_receivables = self._get_average_value('accounts_receivable')
            self.df['receivables_turnover'] = self._safe_divide(revenue, avg_receivables, 1)

            # 매출채권회전일수
            self.df['days_receivables'] = self._safe_divide(
                pd.Series(365, index=self.df.index),
                self.df['receivables_turnover'],
                1
            )

        # 총자산회전율
        avg_total_assets = self._get_average_value('total_assets')
        self.df['asset_turnover'] = self._safe_divide(revenue, avg_total_assets, 1)

    def _get_average_value_series(self, series: pd.Series) -> pd.Series:
        """Series의 평균값 계산 (4분기 전과 평균)"""
        previous = series.shift(4)
        avg = (series + previous) / 2
        avg = avg.fillna(series)
        return avg

    def add_all_ratios(self) -> pd.DataFrame:
        """
        모든 재무비율 추가

        Returns:
            재무비율이 추가된 DataFrame
        """
        print("재무비율 계산 중...")

        print("  - 수익성 비율 계산...")
        self.add_profitability_ratios()

        print("  - 레버리지 비율 계산...")
        self.add_leverage_ratios()

        print("  - 유동성 비율 계산...")
        self.add_liquidity_ratios()

        print("  - 효율성 비율 계산...")
        self.add_efficiency_ratios()

        print("재무비율 계산 완료!")

        return self.df

    def get_ratio_columns(self) -> list:
        """추가된 비율 컬럼 리스트 반환"""
        ratio_cols = [
            # 수익성
            'roic', 'roa', 'roe', 'gross_margin', 'operating_margin', 'net_margin',
            # 레버리지
            'debt_to_equity', 'debt_to_assets', 'equity_multiplier',
            # 유동성
            'current_ratio', 'quick_ratio',
            # 효율성
            'inventory_turnover', 'receivables_turnover', 'asset_turnover',
            'days_inventory', 'days_receivables'
        ]

        return [col for col in ratio_cols if col in self.df.columns]


def integrate_financial_ratios(normalized_df: pd.DataFrame) -> pd.DataFrame:
    """
    간편 함수: FinancialNormalizer DataFrame에 재무비율 추가

    Parameters
    ----------
    normalized_df : pd.DataFrame
        FinancialNormalizer.create_normalized_dataframe() 결과

    Returns
    -------
    pd.DataFrame
        재무비율이 추가된 DataFrame
    """
    integrator = FinancialDataIntegrator(normalized_df)
    return integrator.add_all_ratios()
    """
    FinancialNormalizer의 DataFrame에 재무비율을 계산하여 통합
    """

    def __init__(self, normalized_df: pd.DataFrame):
        """
        Args:
            normalized_df: FinancialNormalizer.create_normalized_dataframe() 결과
        """
        self.df = normalized_df.copy()

        # date를 인덱스로 설정 (아직 안되어 있다면)
        if 'date' in self.df.columns and self.df.index.name != 'date':
            self.df.set_index('date', inplace=True)

        # 날짜 인덱스를 datetime으로 변환
        if not isinstance(self.df.index, pd.DatetimeIndex):
            self.df.index = pd.to_datetime(self.df.index)

    def _safe_divide(self, numerator: pd.Series, denominator: pd.Series,
                     multiplier: float = 1.0) -> pd.Series:
        """안전한 나눗셈 (0으로 나누기 방지)"""
        result = pd.Series(np.nan, index=numerator.index)
        mask = (denominator != 0) & denominator.notna() & numerator.notna()
        result[mask] = (numerator[mask] / denominator[mask]) * multiplier
        return result

    def _get_average_value(self, column: str) -> pd.Series:
        """
        기초와 기말의 평균값 계산 (전년도 데이터와 평균)
        """
        if column not in self.df.columns:
            return pd.Series(np.nan, index=self.df.index)

        current = self.df[column]
        previous = current.shift(4)  # 4분기 전 (연간 기준)

        # 평균 계산
        avg = (current + previous) / 2

        # 이전 데이터가 없으면 현재 값 사용
        avg = avg.fillna(current)

        return avg

    def calculate_tax_rate(self) -> pd.Series:
        """실효세율 계산"""
        tax_expense = self.df.get('income_tax_expense', pd.Series(np.nan, index=self.df.index))
        pretax_income = self.df.get('pretax_income', pd.Series(np.nan, index=self.df.index))

        tax_rate = self._safe_divide(tax_expense, pretax_income, 1.0)

        # 합리적인 범위로 제한 (0-50%)
        tax_rate = tax_rate.clip(0, 0.5)

        # 결측값은 기본 세율로 대체 (21%)
        tax_rate = tax_rate.fillna(0.21)

        return tax_rate

    def calculate_nopat(self) -> pd.Series:
        """NOPAT 계산"""
        operating_income = self.df.get('operating_income', pd.Series(np.nan, index=self.df.index))
        tax_rate = self.calculate_tax_rate()

        nopat = operating_income * (1 - tax_rate)

        return nopat

    def calculate_net_working_capital(self) -> pd.Series:
        """순운전자본 계산"""
        current_assets = self.df.get('current_assets', pd.Series(np.nan, index=self.df.index))
        current_liabilities = self.df.get('current_liabilities', pd.Series(np.nan, index=self.df.index))
        short_term_debt = self.df.get('short_term_debt', pd.Series(0, index=self.df.index)).fillna(0)
        long_term_debt_current = self.df.get('long_term_debt_current', pd.Series(0, index=self.df.index)).fillna(0)

        interest_bearing_current = short_term_debt + long_term_debt_current
        non_interest_bearing_current = current_liabilities - interest_bearing_current

        nwc = current_assets - non_interest_bearing_current

        return nwc

    def calculate_net_fixed_assets(self) -> pd.Series:
        """순고정자산 계산"""
        net_ppe = self.df.get('net_ppe', pd.Series(np.nan, index=self.df.index))
        intangible_assets = self.df.get('intangible_assets', pd.Series(0, index=self.df.index)).fillna(0)
        goodwill = self.df.get('goodwill', pd.Series(0, index=self.df.index)).fillna(0)

        net_fixed_assets = net_ppe + intangible_assets + goodwill

        return net_fixed_assets

    def calculate_invested_capital(self) -> pd.Series:
        """투하자본 계산"""
        nwc = self.calculate_net_working_capital()
        net_fixed_assets = self.calculate_net_fixed_assets()

        invested_capital = nwc + net_fixed_assets

        return invested_capital

    def add_profitability_ratios(self):
        """수익성 비율 추가"""

        # NOPAT 및 투하자본 계산
        nopat = self.calculate_nopat()
        invested_capital = self.calculate_invested_capital()
        avg_invested_capital = self._get_average_value_series(invested_capital)

        # ROIC
        self.df['roic'] = self._safe_divide(nopat, avg_invested_capital, 100)

        # ROA
        net_income = self.df.get('net_income', pd.Series(np.nan, index=self.df.index))
        avg_total_assets = self._get_average_value('total_assets')
        self.df['roa'] = self._safe_divide(net_income, avg_total_assets, 100)

        # ROE
        avg_equity = self._get_average_value('stockholders_equity')
        self.df['roe'] = self._safe_divide(net_income, avg_equity, 100)

        # Profit Margins
        revenue = self.df.get('revenue', pd.Series(np.nan, index=self.df.index))

        if 'gross_profit' in self.df.columns:
            self.df['gross_margin'] = self._safe_divide(self.df['gross_profit'], revenue, 100)

        if 'operating_income' in self.df.columns:
            self.df['operating_margin'] = self._safe_divide(self.df['operating_income'], revenue, 100)

        self.df['net_margin'] = self._safe_divide(net_income, revenue, 100)

    def add_leverage_ratios(self):
        """레버리지 비율 추가"""

        total_liabilities = self.df.get('total_liabilities', pd.Series(np.nan, index=self.df.index))
        total_assets = self.df.get('total_assets', pd.Series(np.nan, index=self.df.index))
        equity = self.df.get('stockholders_equity', pd.Series(np.nan, index=self.df.index))

        # 부채비율 (D/E)
        self.df['debt_to_equity'] = self._safe_divide(total_liabilities, equity, 100)

        # 부채비율 (D/A)
        self.df['debt_to_assets'] = self._safe_divide(total_liabilities, total_assets, 100)

        # 자기자본승수
        self.df['equity_multiplier'] = self._safe_divide(total_assets, equity, 1)

    def add_liquidity_ratios(self):
        """유동성 비율 추가"""

        current_assets = self.df.get('current_assets', pd.Series(np.nan, index=self.df.index))
        current_liabilities = self.df.get('current_liabilities', pd.Series(np.nan, index=self.df.index))

        # 유동비율
        self.df['current_ratio'] = self._safe_divide(current_assets, current_liabilities, 1)

        # 당좌비율
        if 'inventory' in self.df.columns:
            quick_assets = current_assets - self.df['inventory'].fillna(0)
            self.df['quick_ratio'] = self._safe_divide(quick_assets, current_liabilities, 1)

    def add_efficiency_ratios(self):
        """효율성 비율 추가"""

        revenue = self.df.get('revenue', pd.Series(np.nan, index=self.df.index))

        # 재고자산회전율
        if 'inventory' in self.df.columns and 'cost_of_revenue' in self.df.columns:
            avg_inventory = self._get_average_value('inventory')
            cost_of_revenue = self.df['cost_of_revenue']
            self.df['inventory_turnover'] = self._safe_divide(cost_of_revenue, avg_inventory, 1)

            # 재고자산회전일수
            self.df['days_inventory'] = self._safe_divide(
                pd.Series(365, index=self.df.index),
                self.df['inventory_turnover'],
                1
            )

        # 매출채권회전율
        if 'accounts_receivable' in self.df.columns:
            avg_receivables = self._get_average_value('accounts_receivable')
            self.df['receivables_turnover'] = self._safe_divide(revenue, avg_receivables, 1)

            # 매출채권회전일수
            self.df['days_receivables'] = self._safe_divide(
                pd.Series(365, index=self.df.index),
                self.df['receivables_turnover'],
                1
            )

        # 총자산회전율
        avg_total_assets = self._get_average_value('total_assets')
        self.df['asset_turnover'] = self._safe_divide(revenue, avg_total_assets, 1)

    def _get_average_value_series(self, series: pd.Series) -> pd.Series:
        """Series의 평균값 계산 (4분기 전과 평균)"""
        previous = series.shift(4)
        avg = (series + previous) / 2
        avg = avg.fillna(series)
        return avg

    def add_all_ratios(self) -> pd.DataFrame:
        """
        모든 재무비율 추가

        Returns:
            재무비율이 추가된 DataFrame
        """
        print("재무비율 계산 중...")

        print("  - 수익성 비율 계산...")
        self.add_profitability_ratios()

        print("  - 레버리지 비율 계산...")
        self.add_leverage_ratios()

        print("  - 유동성 비율 계산...")
        self.add_liquidity_ratios()

        print("  - 효율성 비율 계산...")
        self.add_efficiency_ratios()

        print("재무비율 계산 완료!")

        return self.df

    def get_ratio_columns(self) -> list:
        """추가된 비율 컬럼 리스트 반환"""
        ratio_cols = [
            # 수익성
            'roic', 'roa', 'roe', 'gross_margin', 'operating_margin', 'net_margin',
            # 레버리지
            'debt_to_equity', 'debt_to_assets', 'equity_multiplier',
            # 유동성
            'current_ratio', 'quick_ratio',
            # 효율성
            'inventory_turnover', 'receivables_turnover', 'asset_turnover',
            'days_inventory', 'days_receivables'
        ]

        return [col for col in ratio_cols if col in self.df.columns]


def integrate_financial_ratios(normalized_df: pd.DataFrame) -> pd.DataFrame:
    """
    간편 함수: FinancialNormalizer DataFrame에 재무비율 추가

    Args:
        normalized_df: FinancialNormalizer.create_normalized_dataframe() 결과

    Returns:
        재무비율이 추가된 DataFrame
    """
    integrator = FinancialDataIntegrator(normalized_df)
    return integrator.add_all_ratios()


def main():
    """
    사용 예제

    주의: 이 예제를 실행하려면 다음 모듈들이 필요합니다:
    - sec_data_pipeline.collectors.sec_utils
    - sec_data_pipeline.parsers.company_facts_parser
    - sec_data_pipeline.parsers.financial_normalizer
    """
    print("=" * 80)
    print("Financial Data Integrator - 사용 예제")
    print("=" * 80)
    print("\n이 파일은 독립적으로 실행 가능한 모듈입니다.")
    print("\n사용 방법:")
    print("-" * 80)
    print("""
from financial_data_integrator import integrate_financial_ratios

# 1. FinancialNormalizer로 정규화된 DataFrame 생성
df_q = normalizer.create_normalized_dataframe("quarterly")

# 2. 재무비율 추가
df_q_with_ratios = integrate_financial_ratios(df_q)

# 3. 결과 확인
print(df_q_with_ratios.tail(10))
print(df_q_with_ratios[['roic', 'roa', 'roe']].tail(10))
    """)
    print("-" * 80)

    # 실제 데이터로 테스트하려면 아래 주석을 해제하고 경로를 수정하세요
    """
    import sys
    sys.path.append(r"C:\\Users\\YOUR_USERNAME\\path\\to\\your\\project")

    from sec_data_pipeline.collectors.sec_utils import fetch_company_facts
    from sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
    from sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer

    # 데이터 가져오기
    headers = {"User-Agent": "YourName Research <your@email.com>"}
    facts = fetch_company_facts("AAPL", headers=headers)

    # Parser 및 Normalizer
    parser = CompanyFactsParser(facts)
    normalizer = FinancialNormalizer(parser)

    # 정규화된 DataFrame 생성
    df_q = normalizer.create_normalized_dataframe("quarterly")

    print("원본 데이터:")
    print(df_q.tail(10))

    # 재무비율 추가
    df_q_with_ratios = integrate_financial_ratios(df_q)

    print("재무비율이 추가된 데이터:")
    print(df_q_with_ratios.tail(10))

    print("주요 수익성 지표:")
    print(df_q_with_ratios[['roic', 'roa', 'roe', 'net_margin']].tail(10))
    """


if __name__ == "__main__":
    main()