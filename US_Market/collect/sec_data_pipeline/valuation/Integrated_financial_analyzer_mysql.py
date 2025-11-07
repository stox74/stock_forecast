#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
통합 재무분석 파이프라인 (MySQL)
EDGAR → WRDS (MySQL) 보완 → 재무비율 계산
"""

import sys
import pandas as pd
from typing import Optional, Dict

# 프로젝트 경로 추가
sys.path.append(r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\US_Market\collect")

from sec_data_pipeline.collectors.sec_utils import fetch_company_facts
from sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
from sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer
from sec_data_pipeline.valuation.financial_data_integrator import integrate_financial_ratios
from sec_data_pipeline.validators.wrds_data_validator_mysql import WRDSDataValidator


class IntegratedFinancialAnalyzer:
    """
    통합 재무분석 파이프라인 (MySQL 지원)
    EDGAR → WRDS MySQL 검증/보완 → 재무비율 계산
    """

    def __init__(self, db_info: Optional[Dict] = None, user_agent: Optional[str] = None):
        """
        Args:
            db_info: MySQL 데이터베이스 연결 정보
                    {
                        'host': 'hostname',
                        'port': 3307,
                        'user': 'username',
                        'password': 'password',
                        'database': 'database_name'
                    }
            user_agent: SEC API 요청용 User-Agent
        """
        self.db_info = db_info
        self.user_agent = user_agent or "Investment Research <research@example.com>"
        self.validator = None

        if db_info:
            self.validator = WRDSDataValidator(db_info)

    def analyze(self, ticker: str,
                period_type: str = 'quarterly',
                use_wrds: bool = True,
                table_name: str = 'investar_US_fundq') -> pd.DataFrame:
        """
        전체 분석 파이프라인 실행

        Args:
            ticker: 종목 코드
            period_type: 'quarterly' 또는 'annual'
            use_wrds: WRDS 보완 사용 여부
            table_name: WRDS 테이블명 (기본값: 'nvestar_US_fundq')

        Returns:
            재무비율이 포함된 완성된 DataFrame
        """
        print(f"\n{'=' * 80}")
        print(f"통합 재무분석 시작: {ticker}")
        print(f"{'=' * 80}\n")

        # 1. EDGAR 데이터 수집
        print("STEP 1: SEC EDGAR 데이터 수집")
        print("-" * 80)

        headers = {"User-Agent": self.user_agent}
        facts = fetch_company_facts(ticker, headers=headers)

        if not facts:
            raise ValueError(f"❌ {ticker} EDGAR 데이터를 가져올 수 없습니다.")

        print(f"✓ EDGAR Company Facts 데이터 수집 완료\n")

        # 2. EDGAR 데이터 파싱 및 정규화
        print("STEP 2: EDGAR 데이터 파싱 및 정규화")
        print("-" * 80)

        parser = CompanyFactsParser(facts)
        normalizer = FinancialNormalizer(parser)

        edgar_df = normalizer.create_normalized_dataframe(period_type)
        print(f"✓ EDGAR 정규화 완료: {len(edgar_df)} rows × {len(edgar_df.columns)} columns\n")

        # 3. WRDS 데이터로 보완 (옵션)
        if use_wrds and self.validator:
            print("STEP 3: WRDS MySQL 데이터로 보완")
            print("-" * 80)

            self.validator.connect()
            try:
                validated_df = self.validator.validate_and_보완(
                    edgar_df=edgar_df,
                    ticker=ticker,
                    table_name=table_name
                )
            finally:
                self.validator.disconnect()

            print(f"✓ WRDS 보완 완료: {len(validated_df)} rows × {len(validated_df.columns)} columns\n")
        else:
            if not use_wrds:
                print("STEP 3: WRDS 보완 건너뛰기 (use_wrds=False)\n")
            elif not self.validator:
                print("STEP 3: WRDS 보완 건너뛰기 (DB 정보 미제공)\n")
            validated_df = edgar_df

        # 4. 재무비율 계산
        print("STEP 4: 재무비율 계산")
        print("-" * 80)

        final_df = integrate_financial_ratios(validated_df)

        print(f"✓ 재무비율 계산 완료: {len(final_df)} rows × {len(final_df.columns)} columns\n")

        # 5. 결과 요약
        self._print_summary(final_df, ticker, period_type)

        return final_df

    def _print_summary(self, df: pd.DataFrame, ticker: str, period_type: str):
        """분석 결과 요약 출력"""
        print(f"\n{'=' * 80}")
        print(f"분석 완료: {ticker} ({period_type})")
        print(f"{'=' * 80}\n")

        print(f"데이터 기간: {df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}")
        print(f"총 기간: {len(df)} {period_type} periods")
        print(f"총 컬럼: {len(df.columns)} columns")

        # 재무비율 컬럼 확인
        ratio_cols = [col for col in df.columns if col in [
            'roic', 'roa', 'roe', 'gross_margin', 'operating_margin', 'net_margin',
            'debt_to_equity', 'current_ratio', 'inventory_turnover', 'receivables_turnover'
        ]]

        if ratio_cols:
            print(f"계산된 재무비율: {len(ratio_cols)}개")
            print(f"  {', '.join(ratio_cols)}")

        print(f"\n{'=' * 80}\n")


if __name__ == "__main__":
    print("=" * 80)
    print("Integrated Financial Analyzer (MySQL)")
    print("=" * 80)
    print("\n이 모듈은 EDGAR + WRDS MySQL + 재무비율을 통합 분석합니다.")
    print("\n사용 예제:")
    print("-" * 80)
    print("""
from sec_data_pipeline.valuation.integrated_financial_analyzer_mysql import IntegratedFinancialAnalyzer
from DATA.stock_invest_function import get_db_host

db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

analyzer = IntegratedFinancialAnalyzer(
    db_info=db_info,
    user_agent="HoyoungPark Research <stox1224@email.com>"
)

df = analyzer.analyze(
    ticker="SMCI",
    period_type='quarterly',
    use_wrds=True,
    table_name='nvestar_US_fundq'
)

print(df[['revenue', 'net_income', 'roic', 'roa', 'roe']].tail(12))
df.to_csv('SMCI_quarterly_complete.csv')
    """)
    print("-" * 80)
    print("\n자세한 내용은 'MYSQL_사용가이드.md' 파일을 참조하세요.")