#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
EDGAR Data Validator and 보완기 with WRDS (MySQL)
EDGAR에서 누락된 재무 데이터를 WRDS MySQL DB에서 보충
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import warnings

# pymysql import (설치 안되어 있으면 안내 메시지)
try:
    import pymysql
except ImportError:
    print("=" * 80)
    print("❌ pymysql 모듈이 설치되어 있지 않습니다.")
    print("=" * 80)
    print("\n다음 명령어로 설치해주세요:")
    print("  pip install pymysql")
    print("\n또는:")
    print("  conda install -c conda-forge pymysql")
    print("=" * 80)
    raise

warnings.filterwarnings('ignore')


class WRDSDataValidator:
    """
    EDGAR 데이터를 WRDS 데이터로 검증하고 보완
    MySQL 데이터베이스 지원
    """

    # WRDS Compustat와 EDGAR 태그 매핑 (전체 버전)
    WRDS_TO_EDGAR_MAPPING = {
        # ==================== Income Statement (손익계산서) ====================

        # Revenue & Sales
        'saleq': 'revenue',  # Sales/Revenue (Quarterly) - 분기 매출
        'revtq': 'revenue',  # Revenue Total (Quarterly)

        # Cost & Expenses
        'cogsq': 'cost_of_revenue',  # Cost of Goods Sold (Quarterly) - 매출원가
        'xsgaq': 'operating_expenses',  # Selling, General & Administrative Expense - 판관비
        'xrdq': 'research_development',  # Research & Development Expense - 연구개발비

        # Income Measures
        'ibq': 'operating_income',  # Income Before Extraordinary Items - 영업이익
        'niq': 'net_income',  # Net Income (Loss) - 순이익
        'oibdpq': 'operating_income_before_dep',  # Operating Income Before Depreciation
        'oiadpq': 'operating_income_after_dep',  # Operating Income After Depreciation
        'piq': 'pretax_income',  # Pretax Income - 세전이익
        'gpq': 'gross_profit',  # Gross Profit - 매출총이익

        # Earnings Per Share
        'epspiq': 'earnings_per_share',  # EPS (Basic) Incl. Extra Items
        'epsfxq': 'earnings_per_share_basic',  # EPS (Basic) Excl. Extra Items
        'epspxq': 'earnings_per_share_diluted',  # EPS (Diluted)

        # Tax
        'txtq': 'income_tax_expense',  # Income Taxes Total - 법인세비용
        'txpq': 'income_tax_payable',  # Income Taxes Payable - 미지급법인세
        'txdiq': 'deferred_tax_income',  # Deferred Taxes (Income Statement)

        # Interest
        'xintq': 'interest_expense',  # Interest and Related Expense - 이자비용

        # Other Income/Expense
        'xidoq': 'extraordinary_items',  # Extraordinary Items and Discontinued Operations
        'mibtq': 'minority_interest_income',  # Minority Interest (Income Statement)

        # ==================== Balance Sheet - Assets (자산) ====================

        # Total Assets
        'atq': 'total_assets',  # Assets Total - 총자산

        # Current Assets
        'actq': 'current_assets',  # Current Assets Total - 유동자산 총계
        'cheq': 'cash',  # Cash and Short-Term Investments - 현금및현금성자산
        'chq': 'cash_only',  # Cash - 현금
        'ivstq': 'short_term_investments',  # Short-Term Investments - 단기투자자산
        'rectq': 'accounts_receivable',  # Receivables Total - 매출채권 총계
        'recdq': 'accounts_receivable_net',  # Receivables (Net) - 순매출채권
        'rectrq': 'accounts_receivable_trade',  # Accounts Receivable Trade (Net) - 상거래 매출채권
        'invtq': 'inventory',  # Inventories Total - 재고자산 총계

        # Non-Current Assets
        'ppentq': 'net_ppe',  # Property, Plant & Equipment (Net) - 순유형자산
        'ppegtq': 'gross_ppe',  # Property, Plant & Equipment (Gross) - 총유형자산
        'aoq': 'other_assets',  # Assets Other - 기타자산
        'intanq': 'intangible_assets',  # Intangible Assets - 무형자산
        'gdwlq': 'goodwill',  # Goodwill - 영업권
        'ivaoq': 'investment_advances',  # Investment and Advances Other - 기타투자및대여금

        # ==================== Balance Sheet - Liabilities (부채) ====================

        # Total Liabilities
        'ltq': 'total_liabilities',  # Liabilities Total - 총부채

        # Current Liabilities
        'lctq': 'current_liabilities',  # Current Liabilities Total - 유동부채 총계
        'dlcq': 'short_term_debt',  # Debt in Current Liabilities - 단기차입금
        'apq': 'accounts_payable',  # Accounts Payable - 매입채무

        # Long-term Liabilities
        'dlttq': 'long_term_debt',  # Long-Term Debt Total - 장기부채
        'loq': 'other_liabilities',  # Liabilities Other - 기타부채

        # Deferred Items
        'txditcq': 'deferred_tax_liability',  # Deferred Taxes and Investment Tax Credit

        # ==================== Balance Sheet - Equity (자본) ====================

        'seqq': 'stockholders_equity',  # Stockholders Equity Total - 자기자본 총계
        'ceqq': 'common_equity',  # Common/Ordinary Equity Total - 보통주 자본
        'pstkrq': 'preferred_stock_redemption',  # Preferred Stock Redemption Value - 우선주 상환가치

        # ==================== Cash Flow Statement (현금흐름표) ====================

        # Operating Activities
        'oancfy': 'operating_cash_flow',  # Operating Activities Net Cash Flow - 영업활동현금흐름
        'fincfy': 'financing_cash_flow',  # Financing Activities Net Cash Flow - 재무활동현금흐름
        'ivncfy': 'investing_cash_flow',  # Investing Activities Net Cash Flow - 투자활동현금흐름

        # Investing Activities Detail
        'capxy': 'capital_expenditures',  # Capital Expenditures - 자본적지출

        # Financing Activities Detail
        'dltisy': 'long_term_debt_issuance',  # Long-Term Debt Issuance - 장기부채 발행
        'dltrq': 'long_term_debt_reduction',  # Long-Term Debt Reduction - 장기부채 상환
        'prstkcy': 'purchase_common_stock',  # Purchase of Common and Preferred Stock - 자사주 매입
        'sstky': 'sale_of_stock',  # Sale of Common and Preferred Stock - 주식 발행

        # Cash Changes
        'chechy': 'cash_change',  # Cash and Cash Equivalents Change - 현금 증감
        'dlcchy': 'short_term_debt_change',  # Changes in Current Debt - 단기부채 변동

        # Non-Cash Items
        'dpq': 'depreciation_amortization',  # Depreciation and Amortization - 감가상각비

        # ==================== Other Important Items ====================

        # Shares Outstanding
        'cshoq': 'shares_outstanding',  # Common Shares Outstanding - 발행주식수
        'cshprq': 'shares_outstanding_diluted',  # Common Shares Used to Calc EPS (Diluted)

        # Invested Capital Components
        'icaptq': 'invested_capital',  # Invested Capital Total - 투하자본
    }

    def __init__(self, db_info: Dict[str, any]):
        """
        Args:
            db_info: MySQL 데이터베이스 연결 정보
                    {
                        'host': 'hostname',
                        'port': 3306,
                        'user': 'username',
                        'password': 'password',
                        'database': 'database_name'
                    }
        """
        self.db_info = db_info
        self.conn = None

    def connect(self):
        """MySQL 데이터베이스 연결"""
        try:
            self.conn = pymysql.connect(
                host=self.db_info['host'],
                port=self.db_info.get('port', 3306),
                user=self.db_info['user'],
                password=self.db_info['password'],
                database=self.db_info['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            print(
                f"✓ MySQL DB 연결 성공: {self.db_info['host']}:{self.db_info.get('port', 3306)}/{self.db_info['database']}")
        except Exception as e:
            print(f"❌ MySQL DB 연결 실패: {e}")
            raise

    def disconnect(self):
        """데이터베이스 연결 해제"""
        if self.conn:
            self.conn.close()
            print("✓ MySQL DB 연결 해제")

    def get_wrds_data(self, ticker: str, table_name: str = 'nvestar_US_fundq') -> pd.DataFrame:
        """
        WRDS MySQL DB에서 특정 종목의 분기별 데이터 조회

        Args:
            ticker: 종목 코드 (예: 'AAPL')
            table_name: WRDS 테이블명 (기본값: 'nvestar_US_fundq')

        Returns:
            분기별 재무 데이터 DataFrame
        """
        if not self.conn:
            self.connect()

        # 종목 코드로 데이터 조회
        # ticker 컬럼명은 DB에 따라 다를 수 있으므로 확인 필요
        query = f"""
        SELECT *
        FROM {table_name}
        WHERE tic = %s OR cusip LIKE %s
        ORDER BY datadate
        """

        try:
            # ticker와 cusip 검색 (더 넓은 범위)
            df = pd.read_sql_query(query, self.conn, params=(ticker, f'%{ticker}%'))

            if df.empty:
                print(f"⚠ WRDS에서 {ticker} 데이터를 찾을 수 없습니다.")
                return pd.DataFrame()

            # 날짜 변환
            if 'datadate' in df.columns:
                df['datadate'] = pd.to_datetime(df['datadate'])
            elif 'edate' in df.columns:
                df['datadate'] = pd.to_datetime(df['edate'])

            print(f"✓ WRDS에서 {ticker} 데이터 로드: {len(df)} rows")
            return df

        except Exception as e:
            print(f"❌ WRDS 데이터 조회 실패: {e}")
            return pd.DataFrame()

    def convert_wrds_to_edgar_format(self, wrds_df: pd.DataFrame) -> pd.DataFrame:
        """
        WRDS 데이터를 EDGAR 정규화 형식으로 변환

        Args:
            wrds_df: WRDS에서 가져온 원본 DataFrame

        Returns:
            EDGAR 형식으로 변환된 DataFrame
        """
        if wrds_df.empty:
            return pd.DataFrame()

        # 날짜를 인덱스로 설정
        date_col = None
        if 'datadate' in wrds_df.columns:
            date_col = 'datadate'
        elif 'edate' in wrds_df.columns:
            date_col = 'edate'
        elif 'date' in wrds_df.columns:
            date_col = 'date'

        if date_col:
            wrds_df = wrds_df.set_index(date_col)

        # 변환된 데이터 저장
        edgar_format = {}

        for wrds_col, edgar_col in self.WRDS_TO_EDGAR_MAPPING.items():
            if wrds_col in wrds_df.columns:
                # WRDS 데이터는 이미 실제 값으로 저장되어 있음
                edgar_format[edgar_col] = wrds_df[wrds_col]

        converted_df = pd.DataFrame(edgar_format)

        # 인덱스 이름을 'date'로 변경
        converted_df.index.name = 'date'

        print(f"✓ WRDS → EDGAR 형식 변환 완료: {len(converted_df.columns)} columns")

        return converted_df

    def validate_and_보완(self, edgar_df: pd.DataFrame, ticker: str,
                        table_name: str = 'nvestar_US_fundq') -> pd.DataFrame:
        """
        EDGAR 데이터를 WRDS 데이터로 검증하고 보완

        Args:
            edgar_df: EDGAR에서 가져온 정규화된 DataFrame (FinancialNormalizer 결과)
            ticker: 종목 코드
            table_name: WRDS 테이블명 (기본값: 'nvestar_US_fundq')

        Returns:
            보완된 DataFrame
        """
        print(f"\n{'=' * 80}")
        print(f"데이터 검증 및 보완 시작: {ticker}")
        print(f"{'=' * 80}\n")

        # 1. WRDS 데이터 가져오기
        wrds_df = self.get_wrds_data(ticker, table_name)

        if wrds_df.empty:
            print("⚠ WRDS 데이터가 없어 보완을 건너뜁니다.")
            return edgar_df

        # 2. WRDS 데이터를 EDGAR 형식으로 변환
        wrds_edgar_format = self.convert_wrds_to_edgar_format(wrds_df)

        if wrds_edgar_format.empty:
            print("⚠ 변환된 WRDS 데이터가 없습니다.")
            return edgar_df

        # 3. EDGAR 데이터 준비
        edgar_df_copy = edgar_df.copy()

        # date가 인덱스가 아니면 설정
        if 'date' in edgar_df_copy.columns and edgar_df_copy.index.name != 'date':
            edgar_df_copy = edgar_df_copy.set_index('date')

        # 날짜 인덱스를 datetime으로 변환
        if not isinstance(edgar_df_copy.index, pd.DatetimeIndex):
            edgar_df_copy.index = pd.to_datetime(edgar_df_copy.index)

        if not isinstance(wrds_edgar_format.index, pd.DatetimeIndex):
            wrds_edgar_format.index = pd.to_datetime(wrds_edgar_format.index)

        # 4. 결측치 보완
        print("\n데이터 보완 현황:")
        print("-" * 80)

        filled_count = 0
        for col in wrds_edgar_format.columns:
            if col in edgar_df_copy.columns:
                # EDGAR에 있지만 결측치인 경우 WRDS로 채우기
                missing_mask = edgar_df_copy[col].isna()
                missing_count = missing_mask.sum()

                if missing_count > 0:
                    # 날짜가 일치하는 WRDS 데이터로 채우기
                    for idx in edgar_df_copy[missing_mask].index:
                        if idx in wrds_edgar_format.index:
                            if pd.notna(wrds_edgar_format.loc[idx, col]):
                                edgar_df_copy.loc[idx, col] = wrds_edgar_format.loc[idx, col]
                                filled_count += 1

                    filled = missing_count - edgar_df_copy[col].isna().sum()
                    if filled > 0:
                        print(f"  {col:30s}: {filled:3d}개 결측치 보완")
            else:
                # EDGAR에 없는 컬럼은 WRDS에서 추가
                edgar_df_copy[col] = wrds_edgar_format[col]
                added_count = edgar_df_copy[col].notna().sum()
                if added_count > 0:
                    print(f"  {col:30s}: {added_count:3d}개 데이터 추가 (신규 컬럼)")
                    filled_count += added_count

        print("-" * 80)
        print(f"총 {filled_count}개의 데이터 포인트 보완 완료\n")

        # 5. 통계 비교
        self._print_comparison_stats(edgar_df_copy, wrds_edgar_format)

        return edgar_df_copy

    def _print_comparison_stats(self, edgar_df: pd.DataFrame, wrds_df: pd.DataFrame):
        """EDGAR와 WRDS 데이터 통계 비교"""
        print("\n데이터 커버리지 비교:")
        print("-" * 80)
        print(f"{'컬럼명':30s} {'EDGAR':>10s} {'WRDS':>10s} {'커버리지':>10s}")
        print("-" * 80)

        all_columns = set(edgar_df.columns) | set(wrds_df.columns)

        for col in sorted(all_columns):
            edgar_coverage = edgar_df[col].notna().sum() if col in edgar_df.columns else 0
            wrds_coverage = wrds_df[col].notna().sum() if col in wrds_df.columns else 0

            total_rows = max(len(edgar_df), len(wrds_df))
            coverage_pct = (edgar_coverage / total_rows * 100) if total_rows > 0 else 0

            print(f"{col:30s} {edgar_coverage:>10d} {wrds_coverage:>10d} {coverage_pct:>9.1f}%")

        print("-" * 80)

    def validate_and_보완_workflow(self, ticker: str,
                                 edgar_normalizer,
                                 period_type: str = 'quarterly',
                                 table_name: str = 'nvestar_US_fundq') -> pd.DataFrame:
        """
        전체 워크플로우: EDGAR 데이터 추출 → WRDS 보완 → 결과 반환

        Args:
            ticker: 종목 코드
            edgar_normalizer: FinancialNormalizer 인스턴스
            period_type: 'quarterly' 또는 'annual'
            table_name: WRDS 테이블명

        Returns:
            보완된 DataFrame
        """
        # 1. EDGAR 정규화 데이터 생성
        print(f"\n{'=' * 80}")
        print(f"EDGAR 데이터 정규화: {ticker} ({period_type})")
        print(f"{'=' * 80}\n")

        edgar_df = edgar_normalizer.create_normalized_dataframe(period_type)

        print(f"✓ EDGAR 데이터: {len(edgar_df)} rows × {len(edgar_df.columns)} columns")

        # 2. WRDS로 보완
        validated_df = self.validate_and_보완(edgar_df, ticker, table_name)

        return validated_df


if __name__ == "__main__":
    print("=" * 80)
    print("WRDS Data Validator (MySQL)")
    print("=" * 80)
    print("\n이 모듈은 EDGAR 데이터를 WRDS MySQL 데이터로 보완합니다.")
    print("\n사용 예제는 'MYSQL_사용가이드.md' 파일을 참조하세요.")
    print("\n또는 'integrated_financial_analyzer_mysql.py'를 사용하세요:")
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

analyzer = IntegratedFinancialAnalyzer(db_info=db_info)
df = analyzer.analyze(ticker="AAPL", period_type='quarterly', use_wrds=True)
    """)
    print("-" * 80)