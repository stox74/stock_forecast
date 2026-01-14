#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Financial Forecast Extended System
DB 매출 예측 데이터 연동 및 확장 기능

기능:
1. DB에서 매출 예측 데이터 로드
2. 매출-영업이익 회귀 모델 적용
3. 향후 실적 전망 시각화
4. Valuation 예측 (PSR 기반)
5. 개별 차트 저장 (각각 분리)
6. YoY 성장률 기본, QoQ는 옵션

사용법:
    from financial_forecast_extended import ForecastExtended

    forecast = ForecastExtended(db_config, analyzer)
    forecast.load_revenue_forecast(ticker="AAPL")
    forecast.create_forecast_visualization(save_dir="./charts")
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from sqlalchemy import create_engine, text
import warnings

warnings.filterwarnings('ignore')

# 스타일 설정
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 8)
plt.rcParams['figure.dpi'] = 100


class ForecastExtended:
    """
    DB 매출 예측 연동 및 확장 분석 시스템
    """

    def __init__(self, db_config: Dict, analyzer=None):
        """
        Args:
            db_config: DB 연결 정보 {'host', 'port', 'user', 'password', 'database'}
            analyzer: FinancialAnalysisSystem 인스턴스 (옵션)
        """
        self.db_config = db_config
        self.analyzer = analyzer
        self.engine = None
        self.revenue_forecast = None
        self.valuation_forecast = None
        self.ticker = None

        self._connect_db()

    def _connect_db(self):
        """DB 연결"""
        try:
            conn_str = (
                f"mysql+pymysql://{self.db_config['user']}:{self.db_config['password']}@"
                f"{self.db_config['host']}:{self.db_config['port']}/"
                f"{self.db_config['database']}?charset=utf8mb4"
            )
            self.engine = create_engine(conn_str)
            print(f"✓ DB 연결 성공: {self.db_config['host']}:{self.db_config['port']}")
        except Exception as e:
            print(f"✗ DB 연결 실패: {e}")
            raise

    def get_available_tickers(self) -> List[str]:
        """사용 가능한 ticker 목록 조회"""
        query = """
                SELECT DISTINCT ticker
                FROM us_revenue_forecast_result
                ORDER BY ticker \
                """
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return df['ticker'].tolist()
        except Exception as e:
            print(f"✗ Ticker 목록 조회 실패: {e}")
            return []

    def get_available_indicators(self) -> List[str]:
        """사용 가능한 indicator 목록 조회"""
        query = """
                SELECT DISTINCT indicator
                FROM us_revenue_forecast_result
                ORDER BY indicator \
                """
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return df['indicator'].tolist()
        except Exception as e:
            print(f"✗ Indicator 목록 조회 실패: {e}")
            return []

    def load_revenue_forecast(self, ticker: str,
                              indicator: str = 'prophet',
                              forecast_date: Optional[str] = None) -> pd.DataFrame:
        """
        DB에서 매출 예측 데이터 로드

        Args:
            ticker: 주식 티커
            indicator: 예측 방법 (prophet, sarima 등)
            forecast_date: 예측 기준일 (None이면 최신)

        Returns:
            매출 예측 DataFrame
        """
        self.ticker = ticker

        # 최신 forecast_date 조회
        if forecast_date is None:
            date_query = """
                         SELECT MAX(forecast_date) as max_date
                         FROM us_revenue_forecast_result
                         WHERE ticker = :ticker AND indicator = : indicator \
                         """
            try:
                with self.engine.connect() as conn:
                    result = pd.read_sql(text(date_query), conn,
                                         params={'ticker': ticker, 'indicator': indicator})
                    forecast_date = result['max_date'].iloc[0]

                if forecast_date is None:
                    print(f"✗ {ticker}의 예측 데이터가 없습니다.")
                    return None

                print(f"✓ 최신 예측 기준일: {forecast_date}")
            except Exception as e:
                print(f"✗ 예측 기준일 조회 실패: {e}")
                return None

        # 매출 예측 데이터 조회
        query = """
                SELECT date, value as revenue_forecast
                FROM us_revenue_forecast_result
                WHERE ticker = :ticker
                  AND indicator = : indicator
                  AND forecast_date = :forecast_date
                ORDER BY date \
                """

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(query), conn,
                                 params={
                                     'ticker': ticker,
                                     'indicator': indicator,
                                     'forecast_date': forecast_date
                                 })

            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)

            self.revenue_forecast = df

            print(f"✓ 매출 예측 데이터 로드 완료: {len(df)}개 기간")
            print(f"  기간: {df.index[0]} ~ {df.index[-1]}")

            return df

        except Exception as e:
            print(f"✗ 매출 예측 데이터 로드 실패: {e}")
            return None

    def load_valuation_forecast(self, ticker: str,
                                indicator: str = 'prophet_valuation',
                                forecast_date: Optional[str] = None) -> pd.DataFrame:
        """
        DB에서 밸류에이션 예측 데이터 로드

        Args:
            ticker: 주식 티커
            indicator: 예측 방법
            forecast_date: 예측 기준일

        Returns:
            밸류에이션 예측 DataFrame
        """
        # 최신 forecast_date 조회
        if forecast_date is None:
            date_query = """
                         SELECT MAX(forecast_date) as max_date
                         FROM us_revenue_forecast_result
                         WHERE ticker = :ticker AND indicator = : indicator \
                         """
            try:
                with self.engine.connect() as conn:
                    result = pd.read_sql(text(date_query), conn,
                                         params={'ticker': ticker, 'indicator': indicator})
                    forecast_date = result['max_date'].iloc[0]

                if forecast_date is None:
                    print(f"ℹ {ticker}의 밸류에이션 예측 데이터가 없습니다.")
                    return None

            except Exception as e:
                print(f"✗ 예측 기준일 조회 실패: {e}")
                return None

        # 밸류에이션 예측 데이터 조회
        query = """
                SELECT date, value as valuation_forecast
                FROM us_revenue_forecast_result
                WHERE ticker = :ticker
                  AND indicator = : indicator
                  AND forecast_date = :forecast_date
                ORDER BY date \
                """

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(query), conn,
                                 params={
                                     'ticker': ticker,
                                     'indicator': indicator,
                                     'forecast_date': forecast_date
                                 })

            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)

            self.valuation_forecast = df

            print(f"✓ 밸류에이션 예측 데이터 로드 완료: {len(df)}개 기간")

            return df

        except Exception as e:
            print(f"ℹ 밸류에이션 예측 데이터 로드 실패 (무시 가능): {e}")
            return None

    def predict_operating_income(self) -> pd.DataFrame:
        """
        매출 예측 → 영업이익 예측 (회귀 모델 적용)

        Returns:
            영업이익 예측 DataFrame
        """
        if self.revenue_forecast is None:
            print("✗ 매출 예측 데이터를 먼저 로드하세요.")
            return None

        if self.analyzer is None:
            print("✗ FinancialAnalysisSystem이 필요합니다.")
            return None

        # 회귀 모델 구축
        print("\n매출-영업이익 회귀 모델 적용 중...")
        model = self.analyzer.build_revenue_operating_income_model()

        if not model or 'predict_function' not in model:
            print("✗ 회귀 모델 구축 실패")
            return None

        predict_func = model['predict_function']

        # 영업이익 예측
        forecast_df = self.revenue_forecast.copy()
        forecast_df['operating_income_forecast'] = forecast_df['revenue_forecast'].apply(predict_func)
        forecast_df['operating_margin_forecast'] = (
                (forecast_df['operating_income_forecast'] / forecast_df['revenue_forecast']) * 100
        )

        print(f"✓ 영업이익 예측 완료")
        print(f"  평균 예상 영업이익률: {forecast_df['operating_margin_forecast'].mean():.2f}%")

        return forecast_df

    def create_individual_charts(self, save_dir: str = "./charts",
                                 include_qoq: bool = False):
        """
        개별 차트 생성 (각각 분리 저장)

        Args:
            save_dir: 저장 디렉토리
            include_qoq: QoQ 성장률 포함 여부 (기본: False, YoY만)
        """
        import os
        os.makedirs(save_dir, exist_ok=True)

        if self.analyzer is None:
            print("✗ FinancialAnalysisSystem이 필요합니다.")
            return

        ticker = self.ticker or "COMPANY"

        print(f"\n개별 차트 생성 중... (저장 위치: {save_dir})")

        # 필요한 분석 실행
        growth = self.analyzer.calculate_growth_rates()
        prof = self.analyzer.analyze_profitability_trends()
        health = self.analyzer.analyze_financial_health()
        cf = self.analyzer.analyze_cash_flow()

        # 1. Revenue Growth (YoY only 또는 YoY+QoQ)
        print("  [1/10] Revenue Growth...")
        self._create_revenue_growth_chart(growth, ticker, save_dir, include_qoq)

        # 2. Operating Income Growth
        print("  [2/10] Operating Income Growth...")
        self._create_oi_growth_chart(growth, ticker, save_dir, include_qoq)

        # 3. Net Income Growth
        print("  [3/10] Net Income Growth...")
        self._create_ni_growth_chart(growth, ticker, save_dir, include_qoq)

        # 4. Return Ratios (ROE, ROA, ROIC)
        print("  [4/10] Return Ratios...")
        self._create_return_ratios_chart(prof, ticker, save_dir)

        # 5. Profit Margins
        print("  [5/10] Profit Margins...")
        self._create_profit_margins_chart(prof, ticker, save_dir)

        # 6. Leverage Ratios
        print("  [6/10] Leverage Ratios...")
        self._create_leverage_chart(health, ticker, save_dir)

        # 7. Liquidity Ratios
        print("  [7/10] Liquidity Ratios...")
        self._create_liquidity_chart(health, ticker, save_dir)

        # 8. Cash Flow
        print("  [8/10] Cash Flow...")
        self._create_cashflow_chart(cf, ticker, save_dir)

        # 9. Revenue-OI Model (있으면)
        print("  [9/10] Revenue-OI Model...")
        self._create_revenue_oi_model_chart(ticker, save_dir)

        # 10. Forecast (DB 데이터 + 회귀 모델)
        print("  [10/10] Revenue & Operating Income Forecast...")
        self._create_forecast_chart(ticker, save_dir)

        print(f"\n✓ 모든 차트 생성 완료! (총 10개)")
        print(f"  저장 위치: {save_dir}")

    def _create_revenue_growth_chart(self, growth, ticker, save_dir, include_qoq):
        """Revenue Growth 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'revenue_yoy_growth' in growth.columns:
            growth['revenue_yoy_growth'].plot(ax=ax, marker='o',
                                              label='YoY Growth', linewidth=2.5, color='#2E86AB')

        if include_qoq and 'revenue_qoq_growth' in growth.columns:
            growth['revenue_qoq_growth'].plot(ax=ax, marker='s',
                                              label='QoQ Growth', linewidth=2, alpha=0.7, color='#A23B72')

        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title(f'{ticker} - Revenue Growth Rate (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_01_revenue_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_oi_growth_chart(self, growth, ticker, save_dir, include_qoq):
        """Operating Income Growth 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'operating_income_yoy_growth' in growth.columns:
            growth['operating_income_yoy_growth'].plot(ax=ax, marker='o',
                                                       label='YoY Growth', linewidth=2.5, color='#F18F01')

        if include_qoq and 'operating_income_qoq_growth' in growth.columns:
            growth['operating_income_qoq_growth'].plot(ax=ax, marker='s',
                                                       label='QoQ Growth', linewidth=2, alpha=0.7, color='#C73E1D')

        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title(f'{ticker} - Operating Income Growth Rate (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_02_operating_income_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_ni_growth_chart(self, growth, ticker, save_dir, include_qoq):
        """Net Income Growth 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'net_income_yoy_growth' in growth.columns:
            growth['net_income_yoy_growth'].plot(ax=ax, marker='o',
                                                 label='YoY Growth', linewidth=2.5, color='#06A77D')

        if include_qoq and 'net_income_qoq_growth' in growth.columns:
            growth['net_income_qoq_growth'].plot(ax=ax, marker='s',
                                                 label='QoQ Growth', linewidth=2, alpha=0.7, color='#005377')

        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title(f'{ticker} - Net Income Growth Rate (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_03_net_income_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_return_ratios_chart(self, prof, ticker, save_dir):
        """Return Ratios 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        for metric, color in [('roe', '#E63946'), ('roa', '#457B9D'), ('roic', '#1D3557')]:
            if metric in prof.columns:
                prof[metric].plot(ax=ax, marker='o', label=metric.upper(),
                                  linewidth=2.5, color=color)

        ax.set_title(f'{ticker} - Return Ratios (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Return (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_04_return_ratios.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_profit_margins_chart(self, prof, ticker, save_dir):
        """Profit Margins 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        margins = [
            ('gross_margin', 'Gross Margin', '#2A9D8F'),
            ('operating_margin', 'Operating Margin', '#E76F51'),
            ('net_margin', 'Net Margin', '#264653')
        ]

        for metric, label, color in margins:
            if metric in prof.columns:
                prof[metric].plot(ax=ax, marker='o', label=label,
                                  linewidth=2.5, color=color)

        ax.set_title(f'{ticker} - Profit Margins (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Margin (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_05_profit_margins.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_leverage_chart(self, health, ticker, save_dir):
        """Leverage Ratios 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'debt_to_equity' in health.columns:
            health['debt_to_equity'].plot(ax=ax, marker='o', label='D/E Ratio',
                                          linewidth=2.5, color='#D62828')
        if 'debt_to_assets' in health.columns:
            health['debt_to_assets'].plot(ax=ax, marker='s', label='D/A Ratio',
                                          linewidth=2, alpha=0.7, color='#F77F00')

        ax.set_title(f'{ticker} - Leverage Ratios (%)', fontsize=14, fontweight='bold')
        ax.set_ylabel('Ratio (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_06_leverage.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_liquidity_chart(self, health, ticker, save_dir):
        """Liquidity Ratios 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'current_ratio' in health.columns:
            health['current_ratio'].plot(ax=ax, marker='o', label='Current Ratio',
                                         linewidth=2.5, color='#06A77D')
        if 'quick_ratio' in health.columns:
            health['quick_ratio'].plot(ax=ax, marker='s', label='Quick Ratio',
                                       linewidth=2, alpha=0.7, color='#005377')

        ax.axhline(y=1, color='red', linestyle='--', alpha=0.5, label='Threshold')
        ax.set_title(f'{ticker} - Liquidity Ratios', fontsize=14, fontweight='bold')
        ax.set_ylabel('Ratio', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_07_liquidity.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_cashflow_chart(self, cf, ticker, save_dir):
        """Cash Flow 차트"""
        fig, ax = plt.subplots(figsize=(14, 7))

        if 'operating_cash_flow' in cf.columns:
            cf['operating_cash_flow'].plot(ax=ax, marker='o', label='Operating CF',
                                           linewidth=2.5, color='#2A9D8F')
        if 'free_cash_flow' in cf.columns:
            cf['free_cash_flow'].plot(ax=ax, marker='s', label='Free CF',
                                      linewidth=2, alpha=0.7, color='#E76F51')

        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title(f'{ticker} - Cash Flow', fontsize=14, fontweight='bold')
        ax.set_ylabel('Cash Flow', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_08_cashflow.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_revenue_oi_model_chart(self, ticker, save_dir):
        """Revenue-OI Model 차트"""
        if 'revenue_oi_model' not in self.analyzer.results or not self.analyzer.results['revenue_oi_model']:
            return

        model = self.analyzer.results['revenue_oi_model']

        fig, ax = plt.subplots(figsize=(14, 7))

        ax.scatter(model['X'], model['y'], alpha=0.6, s=100, label='Actual', color='#457B9D')
        ax.plot(model['X'], model['y_pred'], 'r-', linewidth=3, label='Regression Line')

        slope = model['slope']
        intercept = model['intercept']
        r2 = model['r2']

        equation_text = f'OI = {slope:.4f} × Revenue + {intercept:.2f}\nR² = {r2:.4f}'
        ax.text(0.05, 0.95, equation_text, transform=ax.transAxes,
                fontsize=12, verticalalignment='top', fontweight='bold',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        ax.set_title(f'{ticker} - Revenue vs Operating Income (TTM)', fontsize=14, fontweight='bold')
        ax.set_xlabel('Revenue (TTM)', fontsize=12)
        ax.set_ylabel('Operating Income (TTM)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_09_revenue_oi_model.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_forecast_chart(self, ticker, save_dir):
        """Revenue & Operating Income Forecast 차트 (DB 데이터 + 회귀 모델)"""
        if self.revenue_forecast is None:
            return

        # 영업이익 예측
        forecast_df = self.predict_operating_income()
        if forecast_df is None:
            return

        # Historical 데이터
        historical_periods = 12
        hist_revenue = self.analyzer.df['revenue'].tail(historical_periods)
        hist_oi = self.analyzer.df['operating_income'].tail(historical_periods)

        fig, axes = plt.subplots(1, 2, figsize=(18, 7))

        # 1. Revenue Forecast
        ax = axes[0]
        ax.plot(hist_revenue.index, hist_revenue.values,
                marker='o', linewidth=2.5, label='Historical', color='#2E86AB')

        # Connect to forecast
        last_date = hist_revenue.index[-1]
        first_forecast_date = forecast_df.index[0]
        ax.plot([last_date, first_forecast_date],
                [hist_revenue.iloc[-1], forecast_df['revenue_forecast'].iloc[0]],
                'g--', linewidth=2, alpha=0.5)

        ax.plot(forecast_df.index, forecast_df['revenue_forecast'].values,
                marker='s', linewidth=2.5, label='Forecast (DB)', color='#06A77D', linestyle='--')

        ax.set_title(f'{ticker} - Revenue Forecast', fontsize=13, fontweight='bold')
        ax.set_ylabel('Revenue', fontsize=11)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        # 2. Operating Income Forecast
        ax = axes[1]
        ax.plot(hist_oi.index, hist_oi.values,
                marker='o', linewidth=2.5, label='Historical', color='#E76F51')

        # Connect to forecast
        ax.plot([last_date, first_forecast_date],
                [hist_oi.iloc[-1], forecast_df['operating_income_forecast'].iloc[0]],
                'g--', linewidth=2, alpha=0.5)

        ax.plot(forecast_df.index, forecast_df['operating_income_forecast'].values,
                marker='s', linewidth=2.5, label='Forecast (Model)', color='#F18F01', linestyle='--')

        ax.set_title(f'{ticker} - Operating Income Forecast', fontsize=13, fontweight='bold')
        ax.set_ylabel('Operating Income', fontsize=11)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_10_forecast.png", dpi=300, bbox_inches='tight')
        plt.close()

    def generate_extended_report(self, ticker: str, company_name: str,
                                 save_dir: str = "./financial_reports",
                                 include_qoq: bool = False,
                                 revenue_indicator: str = 'prophet',
                                 valuation_indicator: str = 'prophet_valuation'):
        """
        확장 리포트 생성 (DB 예측 + 분석)

        Args:
            ticker: 주식 티커
            company_name: 회사명
            save_dir: 저장 디렉토리
            include_qoq: QoQ 성장률 포함 여부
            revenue_indicator: 매출 예측 지표
            valuation_indicator: 밸류에이션 예측 지표
        """
        import os

        print("\n" + "=" * 70)
        print(f"Extended Financial Report: {company_name} ({ticker})")
        print("=" * 70)

        # 1. DB에서 예측 데이터 로드
        print("\n[1/3] DB 예측 데이터 로드...")
        self.load_revenue_forecast(ticker, indicator=revenue_indicator)
        self.load_valuation_forecast(ticker, indicator=valuation_indicator)

        # 2. 개별 차트 생성
        print("\n[2/3] 개별 차트 생성...")
        ticker_dir = os.path.join(save_dir, ticker)
        os.makedirs(ticker_dir, exist_ok=True)

        self.create_individual_charts(save_dir=ticker_dir, include_qoq=include_qoq)

        # 3. 텍스트 리포트 (기존 방식 사용)
        print("\n[3/3] 텍스트 리포트 생성...")
        if self.analyzer:
            report = self.analyzer.generate_summary_report()
            report_path = os.path.join(ticker_dir, f"{ticker}_extended_report.txt")
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(f"Company: {company_name} ({ticker})\n")
                f.write("=" * 70 + "\n")
                f.write("EXTENDED REPORT (with DB Forecast)\n")
                f.write("=" * 70 + "\n\n")
                f.write(report)
            print(f"  리포트 저장: {report_path}")

        print("\n" + "=" * 70)
        print("✓ Extended Report 생성 완료!")
        print(f"  저장 위치: {ticker_dir}")
        print(f"  차트: 10개 (개별 PNG)")
        print(f"  QoQ 성장률: {'포함' if include_qoq else '미포함 (YoY만)'}")
        print("=" * 70)


def main():
    """사용 예제"""
    print("=" * 70)
    print("Financial Forecast Extended - 사용 가이드")
    print("=" * 70)

    print("\n[사용 예제 1] DB 예측 데이터만 사용")
    print("-" * 70)
    print("""
# DB 설정
db_config = {
    'host': 'localhost',
    'port': 3306,
    'user': 'username',
    'password': 'password',
    'database': 'investar'
}

# ForecastExtended 초기화
forecast = ForecastExtended(db_config)

# 매출 예측 로드
forecast.load_revenue_forecast(ticker='AAPL', indicator='prophet')

# 밸류에이션 예측 로드
forecast.load_valuation_forecast(ticker='AAPL', indicator='prophet_valuation')
    """)

    print("\n[사용 예제 2] 기존 분석 시스템과 통합")
    print("-" * 70)
    print("""
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem
from financial_forecast_extended import ForecastExtended

# 1. 기존 분석 실행
df_with_ratios = integrate_financial_ratios(df_normalized)
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 2. 확장 시스템 초기화
forecast = ForecastExtended(db_config, analyzer=analyzer)

# 3. 확장 리포트 생성 (DB 예측 + 회귀 모델)
forecast.generate_extended_report(
    ticker='AAPL',
    company_name='Apple Inc.',
    save_dir='./financial_reports',
    include_qoq=False,  # YoY만, QoQ 미포함
    revenue_indicator='prophet',
    valuation_indicator='prophet_valuation'
)

# 결과:
# - 10개 개별 차트 (각각 PNG)
# - YoY 성장률 기본
# - DB 매출 예측 + 회귀 모델 영업이익 예측
    """)

    print("\n[차트 목록]")
    print("-" * 70)
    print("""
1. {ticker}_01_revenue_growth.png           - 매출 성장률 (YoY ± QoQ)
2. {ticker}_02_operating_income_growth.png  - 영업이익 성장률
3. {ticker}_03_net_income_growth.png        - 순이익 성장률
4. {ticker}_04_return_ratios.png            - ROE, ROA, ROIC
5. {ticker}_05_profit_margins.png           - 이익률
6. {ticker}_06_leverage.png                 - 부채비율
7. {ticker}_07_liquidity.png                - 유동성
8. {ticker}_08_cashflow.png                 - 현금흐름
9. {ticker}_09_revenue_oi_model.png         - 회귀 모델
10. {ticker}_10_forecast.png                - DB 예측 + 모델 예측
    """)


if __name__ == "__main__":
    main()