#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Company Facts JSON Parser
SEC Company Facts API 응답을 파싱하여 재무데이터 추출
"""

import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class CompanyFactsParser:
    """Company Facts JSON 데이터 파서"""

    def __init__(self, company_facts_data: Dict):
        """
        Args:
            company_facts_data: SEC Company Facts API 응답 데이터
        """
        self.data = company_facts_data
        self.entity_name = company_facts_data.get('entityName', '')
        self.cik = company_facts_data.get('cik', '')
        self.facts = company_facts_data.get('facts', {})

    def get_available_taxonomies(self) -> List[str]:
        """사용 가능한 taxonomy 리스트 반환"""
        return list(self.facts.keys())

    def get_available_tags(self, taxonomy: str = 'us-gaap') -> List[str]:
        """
        특정 taxonomy의 사용 가능한 태그 리스트 반환

        Args:
            taxonomy: XBRL taxonomy (기본값: 'us-gaap')

        Returns:
            태그 리스트
        """
        if taxonomy not in self.facts:
            return []
        return list(self.facts[taxonomy].keys())

    def extract_tag_data(self, tag: str, taxonomy: str = 'us-gaap',
                         unit: str = 'USD') -> Optional[pd.DataFrame]:
        """
        특정 XBRL 태그의 데이터를 DataFrame으로 추출

        Args:
            tag: XBRL 태그 (예: 'Revenues', 'Assets')
            taxonomy: XBRL taxonomy
            unit: 단위 (USD, shares 등)

        Returns:
            DataFrame with columns: [end, val, accn, fy, fp, form, filed, frame]
        """
        if taxonomy not in self.facts:
            return None

        if tag not in self.facts[taxonomy]:
            return None

        tag_data = self.facts[taxonomy][tag]

        # 지정된 단위의 데이터 추출
        if 'units' not in tag_data:
            return None

        if unit not in tag_data['units']:
            return None

        records = tag_data['units'][unit]

        if not records:
            return None

        # DataFrame 생성
        df = pd.DataFrame(records)

        # 날짜 변환
        if 'end' in df.columns:
            df['end'] = pd.to_datetime(df['end'])

        if 'filed' in df.columns:
            df['filed'] = pd.to_datetime(df['filed'])

        # 정렬
        df = df.sort_values('end')

        return df

    def extract_multiple_tags(self, tags: List[str], taxonomy: str = 'us-gaap',
                              unit: str = 'USD') -> Dict[str, pd.DataFrame]:
        """
        여러 태그의 데이터를 한번에 추출

        Args:
            tags: XBRL 태그 리스트
            taxonomy: XBRL taxonomy
            unit: 단위

        Returns:
            {tag: DataFrame} 딕셔너리
        """
        results = {}
        for tag in tags:
            df = self.extract_tag_data(tag, taxonomy, unit)
            if df is not None and not df.empty:
                results[tag] = df
        return results

    def get_quarterly_data(self, tag: str, taxonomy: str = 'us-gaap',
                           unit: str = 'USD') -> Optional[pd.DataFrame]:
        """
        분기별 데이터만 추출 (Q1, Q2, Q3, Q4 포함)

        주의: SEC EDGAR에서 Q4는 종종 'FY'로 표시됨

        Args:
            tag: XBRL 태그
            taxonomy: XBRL taxonomy
            unit: 단위

        Returns:
            분기 데이터 DataFrame
        """
        df = self.extract_tag_data(tag, taxonomy, unit)
        if df is None:
            return None

        if 'fp' not in df.columns:
            return None

        # Q1, Q2, Q3 추출
        quarterly = df[df['fp'].isin(['Q1', 'Q2', 'Q3'])].copy()

        # FY 중에서 분기 데이터 식별
        # form이 10-Q인 경우 또는 frame에 'Q'가 포함된 경우 Q4로 간주
        fy_data = df[df['fp'] == 'FY'].copy()

        if not fy_data.empty:
            # 10-Q form은 분기 보고서
            q4_from_form = fy_data[fy_data['form'] == '10-Q'].copy()

            # frame에 Q가 포함된 경우도 분기 데이터
            if 'frame' in fy_data.columns:
                q4_from_frame = fy_data[fy_data['frame'].str.contains('Q', na=False)].copy()
                q4_data = pd.concat([q4_from_form, q4_from_frame]).drop_duplicates()
            else:
                q4_data = q4_from_form

            if not q4_data.empty:
                # Q4로 표시 변경
                q4_data['fp'] = 'Q4'
                quarterly = pd.concat([quarterly, q4_data])

        quarterly = quarterly.drop_duplicates(subset=['end', 'val'], keep='last')
        quarterly = quarterly.sort_values('end')

        return quarterly if not quarterly.empty else None

    def get_annual_data(self, tag: str, taxonomy: str = 'us-gaap',
                        unit: str = 'USD') -> Optional[pd.DataFrame]:
        """
        연간 데이터만 추출 (fp='FY' AND form='10-K')

        Args:
            tag: XBRL 태그
            taxonomy: XBRL taxonomy
            unit: 단위

        Returns:
            연간 데이터 DataFrame
        """
        df = self.extract_tag_data(tag, taxonomy, unit)
        if df is None:
            return None

        if 'fp' not in df.columns:
            return None

        # FY이면서 10-K form인 것만 연간 데이터로 간주
        annual = df[df['fp'] == 'FY'].copy()

        if 'form' in annual.columns:
            # 10-K 또는 10-K/A (수정본)만 연간 데이터
            annual = annual[annual['form'].str.contains('10-K', na=False)]

        # frame에 Q가 없는 것만 (분기가 아닌 연간)
        if 'frame' in annual.columns:
            annual = annual[~annual['frame'].str.contains('Q', na=False)]

        annual = annual.sort_values('end')

        return annual if not annual.empty else None

    def get_latest_value(self, tag: str, taxonomy: str = 'us-gaap',
                         unit: str = 'USD', period_type: str = 'quarterly') -> Optional[float]:
        """
        가장 최근 값 반환

        Args:
            tag: XBRL 태그
            taxonomy: XBRL taxonomy
            unit: 단위
            period_type: 'quarterly', 'annual', 또는 'any'

        Returns:
            최신 값
        """
        if period_type == 'quarterly':
            df = self.get_quarterly_data(tag, taxonomy, unit)
        elif period_type == 'annual':
            df = self.get_annual_data(tag, taxonomy, unit)
        else:
            df = self.extract_tag_data(tag, taxonomy, unit)

        if df is None or df.empty:
            return None

        # 가장 최근 날짜의 값 반환
        latest = df.sort_values('end', ascending=False).iloc[0]
        return latest.get('val')

    def create_time_series(self, tag: str, taxonomy: str = 'us-gaap',
                           unit: str = 'USD', period_type: str = 'quarterly') -> Optional[pd.Series]:
        """
        시계열 Series 생성 (end 날짜를 인덱스로)

        Args:
            tag: XBRL 태그
            taxonomy: XBRL taxonomy
            unit: 단위
            period_type: 'quarterly', 'annual', 또는 'any'

        Returns:
            날짜를 인덱스로 하는 Series
        """
        if period_type == 'quarterly':
            df = self.get_quarterly_data(tag, taxonomy, unit)
        elif period_type == 'annual':
            df = self.get_annual_data(tag, taxonomy, unit)
        else:
            df = self.extract_tag_data(tag, taxonomy, unit)

        if df is None or df.empty:
            return None

        # 중복 제거 (같은 날짜에 여러 보고가 있을 수 있음 - 가장 최근 제출 것 사용)
        if 'filed' in df.columns:
            df = df.sort_values(['end', 'filed'], ascending=[True, False])
            df = df.drop_duplicates(subset=['end'], keep='first')
        else:
            # filed가 없으면 end만으로 중복 제거
            df = df.drop_duplicates(subset=['end'], keep='last')

        # Series 생성
        series = pd.Series(df['val'].values, index=df['end'], name=tag)
        series = series.sort_index()

        return series

    def get_financial_statement_summary(self) -> Dict[str, any]:
        """
        주요 재무제표 항목 요약

        Returns:
            요약 정보 딕셔너리
        """
        summary = {
            'entity_name': self.entity_name,
            'cik': self.cik,
            'taxonomies': self.get_available_taxonomies(),
        }

        # 주요 재무 지표
        key_metrics = {
            'revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet'],
            'net_income': ['NetIncomeLoss', 'ProfitLoss'],
            'total_assets': ['Assets'],
            'total_liabilities': ['Liabilities'],
            'stockholders_equity': ['StockholdersEquity'],
            'cash': ['Cash', 'CashAndCashEquivalentsAtCarryingValue'],
        }

        summary['latest_values'] = {}

        for metric, possible_tags in key_metrics.items():
            for tag in possible_tags:
                value = self.get_latest_value(tag, period_type='any')
                if value is not None:
                    summary['latest_values'][metric] = {
                        'tag': tag,
                        'value': value
                    }
                    break

        return summary


def main():
    """테스트"""
    import json
    import sys
    sys.path.append('..')

    from collectors.sec_api_client import SECAPIClient

    # SEC API 클라이언트 생성
    user_agent = "MyCompany Research admin@mycompany.com"
    client = SECAPIClient(user_agent)

    # Apple Company Facts 가져오기
    print("Fetching Apple company facts...")
    company_facts = client.get_company_facts_by_ticker('AAPL')

    if not company_facts:
        print("Failed to fetch company facts")
        return

    # Parser 생성
    parser = CompanyFactsParser(company_facts)

    # 1. 기본 정보
    print(f"\n1. Basic Info")
    print(f"  Entity: {parser.entity_name}")
    print(f"  CIK: {parser.cik}")
    print(f"  Taxonomies: {parser.get_available_taxonomies()}")

    # 2. 사용 가능한 태그
    tags = parser.get_available_tags('us-gaap')
    print(f"\n2. Available US-GAAP Tags: {len(tags)}")
    print(f"  Sample: {tags[:10]}")

    # 3. Revenue 데이터 추출 (분기별)
    print(f"\n3. Quarterly Revenue Data")
    revenue_quarterly = parser.get_quarterly_data('Revenues')
    if revenue_quarterly is not None:
        print(f"  Records: {len(revenue_quarterly)}")
        print(f"  Date range: {revenue_quarterly['end'].min()} ~ {revenue_quarterly['end'].max()}")
        print(f"\n  Recent quarterly revenue:")
        print(revenue_quarterly[['end', 'val', 'fy', 'fp', 'form']].tail(12))

    # 4. Revenue 데이터 추출 (연간)
    print(f"\n4. Annual Revenue Data")
    revenue_annual = parser.get_annual_data('Revenues')
    if revenue_annual is not None:
        print(f"  Records: {len(revenue_annual)}")
        print(f"\n  Recent annual revenue:")
        print(revenue_annual[['end', 'val', 'fy', 'fp', 'form']].tail(10))

    # 5. 시계열 데이터
    print(f"\n5. Revenue Time Series (Quarterly)")
    revenue_series = parser.create_time_series('Revenues', period_type='quarterly')
    if revenue_series is not None:
        print(f"  Data points: {len(revenue_series)}")
        print(f"\n  Last 12 quarters:")
        print(revenue_series.tail(12))

    # 6. 재무제표 요약
    print(f"\n6. Financial Statement Summary")
    summary = parser.get_financial_statement_summary()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()