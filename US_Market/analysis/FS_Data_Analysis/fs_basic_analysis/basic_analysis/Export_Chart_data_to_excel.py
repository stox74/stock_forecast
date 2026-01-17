import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import sys
import os


# 프로젝트 경로 설정 (노트북과 동일)
def setup_investment_strategy_path():
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        if parent.name == 'investment_strategy':
            project_root = str(parent)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            return project_root
    fallback = r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy"
    if os.path.exists(fallback):
        if fallback not in sys.path:
            sys.path.insert(0, fallback)
        return fallback
    return None


# 경로 설정
try:
    project_root = setup_investment_strategy_path()
    if project_root:
        print(f"프로젝트 루트: {project_root}")
except:
    pass

# 필요한 모듈 import
from DATA.us_financial_data_integrator import integrate_financial_ratios
from US_Market.collect.sec_data_pipeline.collectors.sec_utils import fetch_company_facts
from US_Market.collect.sec_data_pipeline.parsers.company_facts_parser import CompanyFactsParser
from US_Market.collect.sec_data_pipeline.parsers.financial_normalizer import FinancialNormalizer
from US_Market.analysis.FS_Data_Analysis.fs_basic_analysis.basic_analysis.financial_analysis_system import \
    FinancialAnalysisSystem
from US_Market.analysis.FS_Data_Analysis.fs_basic_analysis.basic_analysis.financial_forecast_extended import \
    ForecastExtended


class FinancialDataExporter:
    """재무 분석 데이터를 Long Format Excel로 변환"""

    def __init__(self, ticker='PLTR'):
        self.ticker = ticker
        self.all_data = []

    def fetch_and_prepare_data(self):
        """데이터 수집 및 준비"""
        print(f"\n{'=' * 70}")
        print(f"{self.ticker} 데이터 수집 중...")
        print(f"{'=' * 70}\n")

        # SEC API headers 설정 (원본 노트북 방식)
        headers = {"User-Agent": "Hoyoung Research <stox1224@gmail.com>"}

        try:
            # 1. SEC 데이터 수집
            print("[1/4] SEC 데이터 수집 중...")
            facts = fetch_company_facts(self.ticker, headers=headers)

            # 2. 파싱
            print("[2/4] 데이터 파싱 중...")
            parser = CompanyFactsParser(facts)

            # 3. 정규화 (원본 노트북 방식)
            print("[3/4] 데이터 정규화 중...")
            normalizer = FinancialNormalizer(parser)
            df_normalized = normalizer.create_normalized_dataframe("quarterly")

            print(f"  ✓ SEC 데이터 로드 완료: {len(df_normalized)}개 분기")
            print(f"  기간: {df_normalized.index[0]} ~ {df_normalized.index[-1]}\n")

        except Exception as e:
            print(f"  ✗ SEC 데이터 로드 실패: {e}")
            raise

        # 4. 재무비율 통합
        print("[4/4] 재무비율 계산 중...")
        df_with_ratios = integrate_financial_ratios(df_normalized)
        print(f"  ✓ 재무비율 계산 완료: 총 {len(df_with_ratios.columns)}개 지표\n")

        # 5. 분석 시스템 초기화
        analyzer = FinancialAnalysisSystem(df_with_ratios)
        self.analyzer = analyzer
        self.df = analyzer.df

        # 날짜 컬럼 확인 및 설정
        if 'end' not in self.df.columns:
            # index가 날짜인 경우
            if isinstance(self.df.index, pd.DatetimeIndex):
                self.df = self.df.reset_index()
                if 'index' in self.df.columns:
                    self.df = self.df.rename(columns={'index': 'end'})
            else:
                print("Warning: 'end' 컬럼을 찾을 수 없습니다.")
                print(f"사용 가능한 컬럼: {self.df.columns.tolist()}")

        print(f"데이터 준비 완료: {len(self.df)} 분기")
        print(f"컬럼: {len(self.df.columns)}개")
        if 'end' in self.df.columns:
            print(f"기간: {self.df['end'].min()} ~ {self.df['end'].max()}\n")
        else:
            print(f"Index 기간: {self.df.index[0]} ~ {self.df.index[-1]}\n")

        return self.df

    def create_long_format_data(self):
        """모든 차트 데이터를 Long Format으로 변환"""
        print("Long Format 데이터 생성 중...\n")

        # DataFrame의 index가 날짜인지 확인
        print(f"원본 DataFrame index 타입: {type(self.df.index)}")
        print(f"원본 DataFrame index 샘플: {self.df.index[:3].tolist()}")
        print(f"원본 DataFrame columns: {self.df.columns.tolist()[:10]}")

        # 무조건 reset_index하여 날짜를 컬럼으로 만들기
        if isinstance(self.df.index, pd.DatetimeIndex):
            df_work = self.df.reset_index()
            date_col = 'index'
            print(f"✓ DatetimeIndex 발견 -> reset_index 수행, 날짜 컬럼: '{date_col}'")
        else:
            df_work = self.df.copy()
            # 'end' 컬럼 찾기
            if 'end' in df_work.columns:
                date_col = 'end'
                print(f"✓ 'end' 컬럼 발견, 날짜 컬럼: '{date_col}'")
            else:
                # 첫 컬럼이 날짜일 가능성
                df_work = self.df.reset_index()
                date_col = df_work.columns[0]
                print(f"✓ 첫 번째 컬럼을 날짜로 사용: '{date_col}'")

        # 날짜 컬럼을 datetime으로 변환
        if df_work[date_col].dtype != 'datetime64[ns]':
            print(f"날짜 컬럼 변환 전 타입: {df_work[date_col].dtype}")
            df_work[date_col] = pd.to_datetime(df_work[date_col])
            print(f"날짜 컬럼 변환 후 타입: {df_work[date_col].dtype}")

        print(f"날짜 범위: {df_work[date_col].min()} ~ {df_work[date_col].max()}")

        # 최근 3년 데이터 필터링
        cutoff_date = df_work[date_col].max() - pd.DateOffset(years=3)
        df_recent = df_work[df_work[date_col] > cutoff_date].copy()
        df_recent = df_recent.sort_values(date_col)

        print(f"필터링된 데이터: {len(df_recent)} 분기")
        print(f"필터링 후 날짜 범위: {df_recent[date_col].min()} ~ {df_recent[date_col].max()}\n")

        all_long_data = []

        # 1. 성장률 데이터 (YoY)
        print("[1/12] 성장률 데이터...")
        growth_metrics = ['revenue', 'operating_income', 'net_income']
        for metric in growth_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],  # 날짜 컬럼에서 직접 추출
                        'category': '성장률',
                        'subcategory': metric,
                        'metric': f'{metric}_yoy',
                        'value': row.get(f'{metric}_yoy', None),
                        'unit': 'percent'
                    })

        # 2. 수익률 지표 (ROE, ROA, ROIC)
        print("[2/12] 수익률 지표...")
        return_metrics = ['roe', 'roa', 'roic']
        for metric in return_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': '수익률',
                        'subcategory': 'return_ratios',
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'percent'
                    })

        # 3. 수익성 마진
        print("[3/12] 수익성 마진...")
        margin_metrics = ['gross_margin', 'operating_margin', 'net_margin']
        for metric in margin_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': '수익성',
                        'subcategory': 'margins',
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'percent'
                    })

        # 4. 레버리지
        print("[4/12] 레버리지...")
        leverage_metrics = ['debt_to_equity', 'equity_ratio']
        for metric in leverage_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': '재무건전성',
                        'subcategory': 'leverage',
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'ratio' if metric == 'debt_to_equity' else 'percent'
                    })

        # 5. 유동성
        print("[5/12] 유동성...")
        liquidity_metrics = ['current_ratio', 'quick_ratio']
        for metric in liquidity_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': '재무건전성',
                        'subcategory': 'liquidity',
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'ratio'
                    })

        # 6. 현금흐름
        print("[6/12] 현금흐름...")
        cashflow_metrics = [
            'operating_cash_flow',
            'investing_cash_flow',
            'financing_cash_flow',
            'free_cash_flow'
        ]
        for metric in cashflow_metrics:
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': '현금흐름',
                        'subcategory': 'cashflow',
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'usd'
                    })

        # 7. 절대값 재무항목 (매출, 영업이익, 순이익, 자산 등)
        print("[7/12] 절대값 재무항목...")
        absolute_metrics = {
            'revenue': ('손익계산서', 'revenue'),
            'operating_income': ('손익계산서', 'operating_income'),
            'net_income': ('손익계산서', 'net_income'),
            'total_assets': ('재무상태표', 'assets'),
            'total_liabilities': ('재무상태표', 'liabilities'),
            'stockholders_equity': ('재무상태표', 'equity'),
            'current_assets': ('재무상태표', 'current_assets'),
            'current_liabilities': ('재무상태표', 'current_liabilities')
        }

        for metric, (category, subcategory) in absolute_metrics.items():
            if metric in df_recent.columns:
                for idx, row in df_recent.iterrows():
                    all_long_data.append({
                        'ticker': self.ticker,
                        'date': row[date_col],
                        'category': category,
                        'subcategory': subcategory,
                        'metric': metric,
                        'value': row[metric],
                        'unit': 'usd'
                    })

        # 8. 매출-영업이익 관계 (회귀분석)
        print("[8/12] 매출-영업이익 모델...")
        if 'revenue' in df_recent.columns and 'operating_income' in df_recent.columns:
            valid_data = df_recent[
                df_recent['revenue'].notna() &
                df_recent['operating_income'].notna()
                ].copy()

            if len(valid_data) >= 2:
                from scipy import stats
                slope, intercept, r_value, p_value, std_err = stats.linregress(
                    valid_data['revenue'],
                    valid_data['operating_income']
                )

                # 회귀 모델 계수 저장 - 최신 날짜 사용
                model_date = df_recent[date_col].max()

                all_long_data.append({
                    'ticker': self.ticker,
                    'date': model_date,
                    'category': '예측모델',
                    'subcategory': 'regression',
                    'metric': 'revenue_oi_slope',
                    'value': slope,
                    'unit': 'coefficient'
                })
                all_long_data.append({
                    'ticker': self.ticker,
                    'date': model_date,
                    'category': '예측모델',
                    'subcategory': 'regression',
                    'metric': 'revenue_oi_intercept',
                    'value': intercept,
                    'unit': 'coefficient'
                })
                all_long_data.append({
                    'ticker': self.ticker,
                    'date': model_date,
                    'category': '예측모델',
                    'subcategory': 'regression',
                    'metric': 'revenue_oi_r_squared',
                    'value': r_value ** 2,
                    'unit': 'coefficient'
                })

        # 9-12. 예측 데이터는 ForecastExtended 객체가 필요하므로 별도 처리
        print("[9/12] 예측 데이터 준비 중...")

        df_long = pd.DataFrame(all_long_data)
        print(f"\nLong Format 데이터 생성 완료: {len(df_long)} 행\n")

        return df_long

    def add_forecast_data(self, df_long):
        """예측 데이터 추가"""
        print("예측 데이터 생성 중...\n")

        try:
            # ForecastExtended 객체 생성
            forecast = ForecastExtended(self.analyzer)

            # 예측 실행
            forecast_results = forecast.forecast_revenue_and_operating_income(
                periods=8,
                method='ensemble'
            )

            if forecast_results is not None and not forecast_results.empty:
                print(f"예측 데이터: {len(forecast_results)} 분기\n")

                # 예측 데이터를 Long Format으로 변환
                forecast_long = []

                for idx, row in forecast_results.iterrows():
                    # 매출 예측
                    if 'revenue' in row and pd.notna(row['revenue']):
                        forecast_long.append({
                            'ticker': self.ticker,
                            'date': row['forecast_date'],
                            'category': '예측',
                            'subcategory': 'revenue_forecast',
                            'metric': 'revenue',
                            'value': row['revenue'],
                            'unit': 'usd'
                        })

                    # 영업이익 예측
                    if 'operating_income' in row and pd.notna(row['operating_income']):
                        forecast_long.append({
                            'ticker': self.ticker,
                            'date': row['forecast_date'],
                            'category': '예측',
                            'subcategory': 'operating_income_forecast',
                            'metric': 'operating_income',
                            'value': row['operating_income'],
                            'unit': 'usd'
                        })

                # 기존 데이터에 추가
                df_forecast = pd.DataFrame(forecast_long)
                df_combined = pd.concat([df_long, df_forecast], ignore_index=True)

                print(f"예측 데이터 추가 완료: +{len(df_forecast)} 행")
                return df_combined
            else:
                print("예측 데이터 생성 실패")
                return df_long

        except Exception as e:
            print(f"예측 데이터 생성 중 오류: {e}")
            return df_long

    def export_to_excel(self, df_long, output_path='financial_chart_data_long_format.xlsx'):
        """Excel 파일로 저장 (여러 시트)"""
        print(f"\n{'=' * 70}")
        print("Excel 파일 생성 중...")
        print(f"{'=' * 70}\n")

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # 1. 전체 데이터 (Long Format)
            df_long_sorted = df_long.sort_values(['date', 'category', 'subcategory', 'metric'])
            df_long_sorted.to_excel(writer, sheet_name='전체데이터_Long', index=False)
            print(f"[1/8] 전체데이터_Long: {len(df_long_sorted)} 행")

            # 2. 카테고리별 시트
            categories = df_long['category'].unique()
            for i, category in enumerate(categories, 2):
                df_category = df_long[df_long['category'] == category].copy()
                df_category = df_category.sort_values(['date', 'subcategory', 'metric'])

                # 시트명 정리 (Excel 시트명 제한 31자)
                sheet_name = category[:31]
                df_category.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"[{i}/8] {sheet_name}: {len(df_category)} 행")

            # 3. 피벗 테이블 (Wide Format 참고용)
            try:
                df_pivot = df_long.pivot_table(
                    index='date',
                    columns='metric',
                    values='value',
                    aggfunc='first'
                )
                df_pivot = df_pivot.sort_index()
                df_pivot.to_excel(writer, sheet_name='피벗_Wide')
                print(f"[추가] 피벗_Wide: {len(df_pivot)} 행 x {len(df_pivot.columns)} 열")
            except Exception as e:
                print(f"피벗 테이블 생성 실패: {e}")

            # 4. 메타데이터
            metadata = pd.DataFrame({
                '항목': [
                    '티커',
                    '생성일시',
                    '데이터 시작일',
                    '데이터 종료일',
                    '총 행수',
                    '총 지표수',
                    '카테고리수'
                ],
                '값': [
                    self.ticker,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    str(df_long['date'].min())[:10],
                    str(df_long['date'].max())[:10],
                    len(df_long),
                    df_long['metric'].nunique(),
                    df_long['category'].nunique()
                ]
            })
            metadata.to_excel(writer, sheet_name='메타데이터', index=False)
            print(f"[메타] 메타데이터 시트 생성")

        print(f"\n{'=' * 70}")
        print(f"Excel 파일 저장 완료!")
        print(f"파일: {output_path}")
        print(f"{'=' * 70}\n")

        return output_path


def main():
    """메인 실행 함수"""
    ticker = 'PLTR'  # 원하는 티커로 변경 가능

    print(f"\n{'=' * 70}")
    print(f"{ticker} 재무 차트 데이터 Long Format 변환")
    print(f"{'=' * 70}\n")

    # 1. 데이터 수집 및 준비
    exporter = FinancialDataExporter(ticker=ticker)
    df = exporter.fetch_and_prepare_data()

    # 2. Long Format 데이터 생성
    df_long = exporter.create_long_format_data()

    # 3. 예측 데이터 추가
    df_long = exporter.add_forecast_data(df_long)

    # 4. Excel로 저장
    output_file = f'{ticker}_financial_chart_data_long.xlsx'
    saved_path = exporter.export_to_excel(df_long, output_file)

    # 5. 데이터 요약 출력
    print("\n데이터 요약:")
    print(f"{'=' * 70}")
    print(f"총 데이터 행수: {len(df_long):,}")
    print(f"날짜 범위: {df_long['date'].min()} ~ {df_long['date'].max()}")
    print(f"\n카테고리별 데이터 수:")
    print(df_long['category'].value_counts().to_string())
    print(f"\n{'=' * 70}")

    return saved_path


if __name__ == "__main__":
    output_path = main()
    print(f"\n완료! 파일 위치: {output_path}")