import pandas as pd
from typing import Dict, Any, Optional

from sec_data_pipeline.collectors.sec_utils import fetch_company_facts
from sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
from sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer

# ✅ 통합 WRDS Validator (MySQL 연동 버전)
from sec_data_pipeline.validators.wrds_data_validator_mysql_integrated import WRDSDataValidator

# ✅ 재무비율 통합 함수
from sec_data_pipeline.valuation.financial_data_integrator import integrate_financial_ratios


class IntegratedFinancialAnalyzer:
    """
    1) EDGAR Company Facts → 정규화 분기 재무제표
    2) WRDS us_fundq 데이터로 보완
    3) 재무비율 계산 통합
    """

    def __init__(self, db_info: Dict[str, Any], wrds_conn_str: str):
        """
        db_info : (현재는 로깅용/확장용, 필수는 아님)
        wrds_conn_str : WRDS(Postgres) 접속용 SQLAlchemy connection string
                        예) 'postgresql://wrds_username:wrds_password@wrds.wharton.upenn.edu:9737/wrds'
        """
        self.db_info = db_info
        self.validator = WRDSDataValidator(db_info)   # integrated 버전은 db_info를 받도록 구현됨

    def analyze(self, ticker: str, headers: Dict[str, str],
                table_name: str = "us_fundq") -> Optional[tuple]:
        """
        단일 티커에 대해:

        1) EDGAR Company Facts 호출
        2) 정규화(분기) 데이터프레임 생성
        3) WRDS(us_fundq) 데이터로 보완
        4) 재무비율 통합

        Returns
        -------
        (final_df, cik, entity_name) 또는 None
        """
        print("=" * 80)
        print(f"[{ticker}] 통합 재무분석 시작")

        # ------------------------------------------------------------------
        # STEP 1: EDGAR Company Facts 로딩
        # ------------------------------------------------------------------
        try:
            facts = fetch_company_facts(ticker, headers=headers)
        except Exception as e:
            print(f"✗ EDGAR Company Facts 로딩 실패 ({ticker}): {e}")
            return None

        parser = CompanyFactsParser(facts)
        entity_name = parser.entity_name
        cik = parser.cik

        print(f"✓ Entity: {entity_name} (CIK: {cik})")

        # ------------------------------------------------------------------
        # STEP 2: FinancialNormalizer로 분기 정규화 재무제표 생성
        # ------------------------------------------------------------------
        normalizer = FinancialNormalizer(parser)

        try:
            edgar_df = normalizer.create_normalized_dataframe(period_type='quarterly')
        except Exception as e:
            print(f"✗ FinancialNormalizer 실패 ({ticker}): {e}")
            return None

        if edgar_df is None or edgar_df.empty:
            print(f"⚠ EDGAR 정규화 DF가 비어 있습니다 ({ticker})")
            # 그래도 WRDS만으로 채울 수 있으므로 일단 진행
            edgar_df = pd.DataFrame()

        print(f"✓ EDGAR 정규화 DF: {edgar_df.shape[0]} rows × {edgar_df.shape[1]} cols")

        # ------------------------------------------------------------------
        # STEP 3: WRDS(us_fundq) 데이터로 보완
        # ------------------------------------------------------------------
        try:
            merged_df = self.validator.validate_and_fill_improved(
                edgar_df=edgar_df,
                ticker=ticker,
                table_name=table_name,
                days_tolerance=15,
                verbose=True,
                ticker_col="tic",   # WRDS us_fundq 기준 (필요시 수정)
                date_col="datadate"
            )
        except Exception as e:
            print(f"✗ WRDS 보완 실패 ({ticker}): {e}")
            merged_df = edgar_df

        if merged_df is None or merged_df.empty:
            print(f"⚠ WRDS 보완 후에도 DF가 비어 있습니다 ({ticker})")
            return None

        print(f"✓ EDGAR+WRDS 병합 DF: {merged_df.shape[0]} rows × {merged_df.shape[1]} cols")

        # ------------------------------------------------------------------
        # STEP 4: 재무비율 통합
        # ------------------------------------------------------------------
        try:
            final_df = integrate_financial_ratios(merged_df)
        except Exception as e:
            print(f"✗ 재무비율 통합 실패 ({ticker}): {e}")
            final_df = merged_df

        print(f"✓ 최종 DF (비율 포함): {final_df.shape[0]} rows × {final_df.shape[1]} cols")
        print(f"[{ticker}] 통합 재무분석 종료")
        print("=" * 80)

        # ✅ cik와 entity_name까지 함께 반환
        return final_df, cik, entity_name