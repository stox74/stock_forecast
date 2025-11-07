#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Company Facts JSON Parser (Revised)
- SEC Company Facts API 응답을 파싱하여 재무데이터 추출
- 분기(YTD 포함) 정규화, Q4 복원, 연간/분기 엄격 분리 로직 포함
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
from datetime import datetime


class CompanyFactsParser:
    """SEC Company Facts JSON 데이터 파서 (보강판)"""

    def __init__(self, company_facts_data: Dict[str, Any]):
        """
        Args:
            company_facts_data: SEC Company Facts API 응답(JSON)
        """
        self.data = company_facts_data or {}
        self.entity_name = self.data.get('entityName', '')
        self.cik = self.data.get('cik', '')
        self.facts = self.data.get('facts', {}) or {}

    # ---------------------------------------------------------------------
    # 기본 유틸
    # ---------------------------------------------------------------------
    def get_available_taxonomies(self) -> List[str]:
        """사용 가능한 taxonomy 리스트 반환"""
        return list(self.facts.keys())

    def get_available_tags(self, taxonomy: str = 'us-gaap') -> List[str]:
        """특정 taxonomy의 사용 가능한 태그 리스트 반환"""
        if taxonomy not in self.facts:
            return []
        return list(self.facts[taxonomy].keys())

    def extract_tag_data(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[pd.DataFrame]:
        """
        특정 XBRL 태그의 데이터를 DataFrame으로 추출

        Returns:
            DataFrame(columns: [end, val, accn, fy, fp, form, filed, frame]) or None
        """
        if not tag or taxonomy not in self.facts:
            return None
        tax = self.facts.get(taxonomy, {})
        if tag not in tax:
            return None

        tag_data = tax[tag]
        if 'units' not in tag_data:
            return None
        units = tag_data['units']
        if unit not in units:
            return None

        records = units.get(unit) or []
        if not records:
            return None

        df = pd.DataFrame(records)

        # 날짜 변환
        if 'end' in df.columns:
            df['end'] = pd.to_datetime(df['end'], errors='coerce')
        if 'filed' in df.columns:
            df['filed'] = pd.to_datetime(df['filed'], errors='coerce')

        # 정렬
        if 'end' in df.columns:
            df = df.sort_values('end')

        return df

    def extract_multiple_tags(
        self,
        tags: List[str],
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Dict[str, pd.DataFrame]:
        """여러 태그의 데이터를 한번에 추출"""
        results: Dict[str, pd.DataFrame] = {}
        for tag in tags or []:
            df = self.extract_tag_data(tag, taxonomy, unit)
            if df is not None and not df.empty:
                results[tag] = df
        return results

    # ---------------------------------------------------------------------
    # 분기/연간 기본 추출 (원형 유지하되, 후술 robust/strict 권장)
    # ---------------------------------------------------------------------
    def get_quarterly_data(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[pd.DataFrame]:
        """
        분기 데이터(Q1~Q4) 추출 (frame에 Q가 있으면 보조로 인식)
        - 주의: Q4는 FY로만 보고되는 기업도 많음 → robust 버전 사용 권장
        """
        df = self.extract_tag_data(tag, taxonomy, unit)
        if df is None or df.empty:
            return None

        if 'fp' not in df.columns:
            return None

        quarterly = df[df['fp'].isin(['Q1', 'Q2', 'Q3', 'Q4'])].copy()

        # frame 기반 보조 인식
        if 'frame' in df.columns:
            def _q_from_frame(frame: Any) -> Optional[str]:
                s = str(frame) if pd.notna(frame) else ''
                for q in ('Q1', 'Q2', 'Q3', 'Q4'):
                    if q in s:
                        return q
                return None

            from_frame = df[df['frame'].astype(str).str.contains('Q', na=False)].copy()
            if not from_frame.empty:
                from_frame['fp_from_frame'] = from_frame['frame'].apply(_q_from_frame)
                mask = (from_frame['fp'] == 'FY') & (from_frame['fp_from_frame'].notna())
                from_frame.loc[mask, 'fp'] = from_frame.loc[mask, 'fp_from_frame']
                from_frame = from_frame.drop(columns=['fp_from_frame'], errors='ignore')
                quarterly = pd.concat([quarterly, from_frame], ignore_index=True)

        if quarterly.empty:
            return None

        # 최신 제출 우선
        if 'filed' in quarterly.columns:
            quarterly = quarterly.sort_values(['end', 'fp', 'filed'], ascending=[True, True, False])
            quarterly = quarterly.drop_duplicates(subset=['end', 'fp'], keep='first')
        else:
            quarterly = quarterly.drop_duplicates(subset=['end', 'fp'], keep='last')

        # 동일 end에서 val 기준 중복 제거
        quarterly = quarterly.drop_duplicates(subset=['end', 'val'], keep='last')
        quarterly = quarterly.sort_values('end')

        return quarterly if not quarterly.empty else None

    def get_annual_data(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[pd.DataFrame]:
        """
        연간 데이터(FY) 추출 (frame에서 Q 제외)
        - 엄격 버전은 get_annual_data_strict 사용 권장
        """
        df = self.extract_tag_data(tag, taxonomy, unit)
        if df is None or df.empty or 'fp' not in df.columns:
            return None

        annual = df[df['fp'] == 'FY'].copy()

        # 10-K가 아닌 FY가 끼는 경우가 있어 완화 버전에서는 form 필터 생략 가능
        if 'frame' in annual.columns:
            annual = annual[~annual['frame'].astype(str).str.contains('Q', na=False)]

        if annual.empty:
            return None

        return annual.sort_values('end')

    # ---------------------------------------------------------------------
    # 최신값/시계열
    # ---------------------------------------------------------------------
    def get_latest_value(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD',
        period_type: str = 'quarterly'
    ) -> Optional[float]:
        """가장 최근 값 반환"""
        if period_type == 'quarterly':
            df = self.get_quarterly_data(tag, taxonomy, unit)
        elif period_type == 'annual':
            df = self.get_annual_data(tag, taxonomy, unit)
        else:
            df = self.extract_tag_data(tag, taxonomy, unit)

        if df is None or df.empty:
            return None

        latest = df.sort_values('end', ascending=False).iloc[0]
        return float(latest.get('val')) if pd.notna(latest.get('val')) else None

    def create_time_series(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD',
        period_type: str = 'quarterly'
    ) -> Optional[pd.Series]:
        """end를 인덱스로 한 시계열 Series 생성"""
        if period_type == 'quarterly':
            df = self.get_quarterly_data(tag, taxonomy, unit)
        elif period_type == 'annual':
            df = self.get_annual_data(tag, taxonomy, unit)
        else:
            df = self.extract_tag_data(tag, taxonomy, unit)

        if df is None or df.empty:
            return None

        if 'filed' in df.columns:
            df = df.sort_values(['end', 'filed'], ascending=[True, False]).drop_duplicates('end', keep='first')
        else:
            df = df.drop_duplicates('end', keep='last')

        s = pd.Series(df['val'].values, index=df['end'], name=tag)
        return s.sort_index()

    # ---------------------------------------------------------------------
    # 강화 로직 (권장 사용)
    # ---------------------------------------------------------------------
    def detect_best_revenue_tag(
        self,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[str]:
        """
        Revenue 후보 태그 중 실제 데이터가 존재하는 첫 태그를 자동 선택
        - 기업별 태그 편차 대응
        """
        candidates = [
            'RevenueFromContractWithCustomerExcludingAssessedTax',  # ASC 606 이후 가장 흔함
            'SalesRevenueNet',                                      # 'Net sales'
            'Revenues',                                             # 포괄적인 Revenues
            'ContractWithCustomerRevenue'                           # (드물게) 유사 개념
        ]
        for tag in candidates:
            df = self.extract_tag_data(tag, taxonomy=taxonomy, unit=unit)
            if df is not None and not df.empty:
                return tag
        return None

    def get_quarterly_data_robust(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[pd.DataFrame]:
        """
        분기 데이터(순수 분기) 강건 추출
        1) frame 기반 순수 분기(Qx) 우선
        2) YTD만 있는 경우 차분으로 복원
        3) Q4가 없으면 FY - Q3YTD로 보충
        """
        base = self.extract_tag_data(tag, taxonomy, unit)
        if base is None or base.empty:
            return None

        q = CompanyFactsParser.build_true_quarterly(base)
        if q is not None and not q.empty:
            return q.rename(columns={'q': '__q'})  # 필요 시 다운스트림 호환

        # fallback: 기본 로직
        return self.get_quarterly_data(tag, taxonomy, unit)

    def get_annual_data_strict(
        self,
        tag: str,
        taxonomy: str = 'us-gaap',
        unit: str = 'USD'
    ) -> Optional[pd.DataFrame]:
        """
        연간 데이터(FY) 엄격 추출
        - fp == 'FY'
        - form ∈ {10-K, 10-K/A, 20-F, 40-F}
        - frame에서 Q/YTD 포함 레코드 제거
        """
        df = self.extract_tag_data(tag, taxonomy, unit)
        if df is None or df.empty:
            return None

        # FY만
        annual = df[(df.get('fp') == 'FY')].copy()
        if annual.empty:
            return None

        # 미국/외국 기업의 연간 보고서 형식 필터
        if 'form' in annual.columns:
            annual = annual[annual['form'].astype(str).str.contains(r'10-K|20-F|40-F', na=False)]

        if annual.empty:
            return None

        # frame에 Q 또는 YTD 포함된 값 제거 (연간 총액만 남김)
        if 'frame' in annual.columns:
            annual = annual[~annual['frame'].astype(str).str.contains(r'Q|YTD', na=False)]

        if annual.empty:
            return None

        return annual.sort_values('end')

    # ---------------------------------------------------------------------
    # 내부 정규화 헬퍼
    # ---------------------------------------------------------------------
    @staticmethod
    def _label_frame(row: pd.Series) -> Tuple[Optional[int], Optional[bool]]:
        """
        frame에서 분기(Q1~Q4)와 YTD 여부를 추출
        - 예: CY2024Q3, CY2024Q3YTD, FY2024 등
        """
        f = row.get('frame')
        if pd.isna(f):
            return None, None
        s = str(f)
        m = re.search(r'Q([1-4])', s)
        is_ytd = 'YTD' in s
        return (int(m.group(1)) if m else None), is_ytd

    @staticmethod
    def build_true_quarterly(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        Company Facts 원시 df에서 '순수 분기' 시계열을 복원
        - 순수 Qx 우선, 없으면 YTD 차분, 그래도 Q4 없으면 FY-Q3YTD 보충
        Returns: DataFrame(['end','q','val','fy','fp'])
        """
        if df is None or df.empty:
            return None

        w = df.copy()

        # 날짜 정규화
        if 'end' in w and not pd.api.types.is_datetime64_any_dtype(w['end']):
            w['end'] = pd.to_datetime(w['end'], errors='coerce')
        if 'filed' in w and not pd.api.types.is_datetime64_any_dtype(w['filed']):
            w['filed'] = pd.to_datetime(w['filed'], errors='coerce')

        # frame/ fp 컬럼이 없을 수 있음 → 방어
        has_frame = 'frame' in w.columns
        has_fp = 'fp' in w.columns

        # 분기/ YTD 라벨링
        if has_frame:
            lbl = w.apply(lambda r: pd.Series(CompanyFactsParser._label_frame(r)), axis=1)
            lbl.columns = ['__q', '__ytd']
            w = pd.concat([w, lbl], axis=1)
        else:
            w['__q'] = pd.NA
            w['__ytd'] = pd.NA

        # (1) 순수분기(Qx & not YTD)
        pure_q = w[(w['__q'].notna()) & (w['__ytd'] == False)].copy()

        # (2) YTD만 있을 때 차분으로 복원
        ytd = w[(w['__q'].notna()) & (w['__ytd'] == True)].copy()
        if (pure_q is None or pure_q.empty) and not ytd.empty:
            if 'fy' in ytd.columns:
                ytd = ytd.sort_values(['fy', '__q', 'filed'], ascending=[True, True, False])
            else:
                ytd = ytd.sort_values(['__q', 'filed'], ascending=[True, False])
            ytd = ytd.drop_duplicates(subset=['end', '__q'], keep='first')

            q_vals = []
            group_key = 'fy' if 'fy' in ytd.columns else None
            if group_key:
                groups = ytd.groupby(group_key)
            else:
                ytd = ytd.assign(_grp=0)
                groups = ytd.groupby('_grp')

            for _, g in groups:
                g = g.sort_values('__q')
                prev = 0.0
                for _, r in g.iterrows():
                    cur = float(r['val']) if pd.notna(r.get('val')) else 0.0
                    q_val = cur - prev
                    prev = cur
                    q_vals.append({
                        'end': r['end'],
                        'q': int(r['__q']),
                        'val': float(q_val),
                        'fy': r.get('fy'),
                        'fp': f"Q{int(r['__q'])}"
                    })
            pure_q = pd.DataFrame(q_vals)

        # 이 시점에서 'q' 컬럼 보장 (순수분기 경로는 '__q'만 존재할 수 있음)
        if pure_q is None or pure_q.empty:
            pure_q = pd.DataFrame(columns=['end', 'q', 'val', 'fy', 'fp'])
        else:
            if 'q' not in pure_q.columns:
                if '__q' in pure_q.columns:
                    # 숫자 변환 → Int64 → int
                    pure_q['q'] = pd.to_numeric(pure_q['__q'], errors='coerce').astype('Int64')
                    pure_q = pure_q.dropna(subset=['q'])
                    pure_q['q'] = pure_q['q'].astype(int)
                else:
                    # q도 __q도 없으면 빈 프레임으로 처리
                    pure_q = pd.DataFrame(columns=['end', 'q', 'val', 'fy', 'fp'])

        # (3) 여전히 Q4가 없으면 FY - Q3YTD 보충
        # FY 후보와 Q3YTD 후보 만들기 (컬럼 없으면 skip)
        fy_rows = pd.DataFrame()
        q3ytd = pd.DataFrame()

        if has_fp:
            fy_rows = w[w['fp'] == 'FY'].copy()

        if has_frame:
            # FY 중에서도 frame에 Q가 안 들어간 '연간 총액'만 남기기
            if not fy_rows.empty and 'frame' in fy_rows.columns:
                fy_rows = fy_rows[~fy_rows['frame'].astype(str).str.contains('Q', na=False)]
            # Q3YTD
            q3ytd = w[w['frame'].astype(str).str.contains('Q3YTD', na=False)].copy()

        if not fy_rows.empty and not q3ytd.empty:
            fy_rows = fy_rows.sort_values(['end', 'filed'], ascending=[True, False]).drop_duplicates(['end'])
            q3ytd = q3ytd.sort_values(['end', 'filed'], ascending=[True, False]).drop_duplicates(['end'])

            # 연도 키 (회계연도 종료일 기준의 연도)
            fy_rows['fy_key'] = pd.to_datetime(fy_rows['end'], errors='coerce').dt.year
            q3ytd['fy_key'] = pd.to_datetime(q3ytd['end'], errors='coerce').dt.year

            merged = pd.merge(
                fy_rows[['end', 'val', 'fy_key']],
                q3ytd[['end', 'val', 'fy_key']],
                on='fy_key', suffixes=('_fy', '_q3ytd')
            )
            if not merged.empty:
                merged['q4_val'] = merged['val_fy'].astype(float) - merged['val_q3ytd'].astype(float)
                q4_rows = merged.rename(columns={'end_fy': 'end'})[['end', 'q4_val']]
                q4_rows['q'] = 4
                q4_rows['fp'] = 'Q4'
                q4_rows['fy'] = None
                q4_rows = q4_rows.rename(columns={'q4_val': 'val'})
                pure_q = pd.concat([pure_q, q4_rows[['end', 'q', 'val', 'fy', 'fp']]], ignore_index=True)

        # 최종 정리
        if pure_q.empty:
            return None

        pure_q = pure_q.sort_values(['end', 'q']).drop_duplicates(['end', 'q'], keep='last')
        return pure_q.sort_values('end')

    # ---------------------------------------------------------------------
    # 요약
    # ---------------------------------------------------------------------
    def get_financial_statement_summary(self) -> Dict[str, Any]:
        """
        주요 재무제표 항목 요약 (태그 자동 선택)
        """
        summary: Dict[str, Any] = {
            'entity_name': self.entity_name,
            'cik': self.cik,
            'taxonomies': self.get_available_taxonomies(),
        }

        key_metrics = {
            'revenue': [
                'RevenueFromContractWithCustomerExcludingAssessedTax',
                'SalesRevenueNet',
                'Revenues'
            ],
            'net_income': ['NetIncomeLoss', 'ProfitLoss'],
            'total_assets': ['Assets'],
            'total_liabilities': ['Liabilities'],
            'stockholders_equity': ['StockholdersEquity'],
            'cash': ['Cash', 'CashAndCashEquivalentsAtCarryingValue'],
        }

        latest = {}
        for metric, tags in key_metrics.items():
            for t in tags:
                v = self.get_latest_value(t, period_type='any')
                if v is not None:
                    latest[metric] = {'tag': t, 'value': v}
                    break

        summary['latest_values'] = latest
        return summary


# (선택) 모듈 단독 실행 테스트용
if __name__ == "__main__":
    import requests
    import json

    # 실제 테스트 시: CIK는 10자리 zero-pad, User-Agent에 이메일 포함 필수
    headers = {"User-Agent": "YourApp Research <stox1224@gmail.com>"}
    cik_padded = "0000320193"  # AAPL
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    facts = requests.get(url, headers=headers).json()

    parser = CompanyFactsParser(facts)
    print("Entity:", parser.entity_name, "CIK:", parser.cik)

    # Revenue 태그 자동 탐색
    tag = parser.detect_best_revenue_tag()
    print("Selected revenue tag:", tag)

    # Robust 분기 / Strict 연간
    rev_q = parser.get_quarterly_data_robust(tag) if tag else None
    rev_fy = parser.get_annual_data_strict(tag) if tag else None

    print("Quarterly rows:", 0 if rev_q is None else len(rev_q))
    print("Annual rows   :", 0 if rev_fy is None else len(rev_fy))
