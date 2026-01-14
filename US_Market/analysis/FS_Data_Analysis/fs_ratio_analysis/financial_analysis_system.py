#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Comprehensive Financial Analysis System
SEC 데이터 기반 기업 재무분석 및 투자분석 리포트 생성

기능:
1. 성장성 분석 (Growth Analysis)
2. 수익성 분석 (Profitability Analysis)
3. 재무건전성 분석 (Financial Health)
4. 효율성 분석 (Efficiency)
5. 현금흐름 분석 (Cash Flow)
6. 밸류에이션 분석 (Valuation)
7. 예측 모델 (Forecasting)
8. 종합 리포트 생성

사용법:
    from financial_analysis_system import FinancialAnalysisSystem

    analyzer = FinancialAnalysisSystem(df_with_ratios)
    analyzer.generate_full_report(company_name="Apple Inc.", ticker="AAPL")
"""

import sys
import os
from pathlib import Path


# 모듈 자동 import를 위한 경로 설정
def _setup_module_path():
    """필요한 모듈의 경로를 자동으로 찾아서 sys.path에 추가"""

    # 검색할 경로들
    search_paths = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        str(Path.home() / "projects"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Desktop"),
    ]

    # 환경 변수에서 경로 추가
    if 'FINANCIAL_ANALYSIS_PATH' in os.environ:
        search_paths.insert(0, os.environ['FINANCIAL_ANALYSIS_PATH'])

    # financial_data_integrator.py 찾기
    module_name = 'financial_data_integrator.py'

    for base_path in search_paths:
        if not os.path.exists(base_path):
            continue

        # 현재 디렉토리에서 찾기
        if os.path.exists(os.path.join(base_path, module_name)):
            if base_path not in sys.path:
                sys.path.insert(0, base_path)
            return

        # 하위 디렉토리 3단계까지 탐색
        for root, dirs, files in os.walk(base_path):
            depth = root[len(base_path):].count(os.sep)
            if depth > 3:
                continue

            if module_name in files:
                if root not in sys.path:
                    sys.path.insert(0, root)
                return


# 경로 설정 실행
_setup_module_path()

# 이제 import 시도
try:
    from financial_data_integrator import FinancialDataIntegrator

    INTEGRATOR_AVAILABLE = True
except ImportError:
    print("Warning: financial_data_integrator를 찾을 수 없습니다.")
    print("해결 방법:")
    print("1. financial_data_integrator.py가 같은 디렉토리에 있는지 확인")
    print("2. 환경 변수 설정: set FINANCIAL_ANALYSIS_PATH=실제경로")
    print("3. import_helper.py 사용:")
    print("   from import_helper import quick_setup")
    print("   quick_setup()")
    INTEGRATOR_AVAILABLE = False

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import warnings
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

warnings.filterwarnings('ignore')

# 한글 폰트 설정
plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False

# 스타일 설정
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 8)
plt.rcParams['figure.dpi'] = 100


class FinancialAnalysisSystem:
    """
    SEC 데이터 기반 종합 재무분석 시스템
    """

    def __init__(self, df: pd.DataFrame):
        """
        Args:
            df: FinancialDataIntegrator의 결과 DataFrame (재무비율 포함)
        """
        self.df = df.copy()

        # 날짜 인덱스 확인
        if not isinstance(self.df.index, pd.DatetimeIndex):
            if 'date' in self.df.columns:
                self.df.set_index('date', inplace=True)
            self.df.index = pd.to_datetime(self.df.index)

        # 결과 저장용
        self.results = {}
        self.charts = {}

    def calculate_growth_rates(self) -> pd.DataFrame:
        """
        성장률 계산 (YoY, QoQ)
        """
        print("\n[1/8] 성장률 분석 중...")

        growth_metrics = ['revenue', 'gross_profit', 'operating_income',
                          'net_income', 'total_assets', 'stockholders_equity']

        growth_df = pd.DataFrame(index=self.df.index)

        for metric in growth_metrics:
            if metric in self.df.columns:
                # YoY Growth (4 quarters ago)
                yoy_col = f'{metric}_yoy_growth'
                growth_df[yoy_col] = (
                        (self.df[metric] / self.df[metric].shift(4) - 1) * 100
                )

                # QoQ Growth
                qoq_col = f'{metric}_qoq_growth'
                growth_df[qoq_col] = (
                        (self.df[metric] / self.df[metric].shift(1) - 1) * 100
                )

        # TTM 성장률 계산
        if 'revenue' in self.df.columns:
            revenue_ttm = self.df['revenue'].rolling(window=4, min_periods=1).sum()
            growth_df['revenue_ttm'] = revenue_ttm
            growth_df['revenue_ttm_yoy_growth'] = (
                    (revenue_ttm / revenue_ttm.shift(4) - 1) * 100
            )

        if 'net_income' in self.df.columns:
            net_income_ttm = self.df['net_income'].rolling(window=4, min_periods=1).sum()
            growth_df['net_income_ttm'] = net_income_ttm
            growth_df['net_income_ttm_yoy_growth'] = (
                    (net_income_ttm / net_income_ttm.shift(4) - 1) * 100
            )

        self.results['growth_rates'] = growth_df
        return growth_df

    def analyze_profitability_trends(self) -> pd.DataFrame:
        """
        수익성 추세 분석
        """
        print("[2/8] 수익성 추세 분석 중...")

        profitability_metrics = [
            'roe', 'roa', 'roic',
            'gross_margin', 'operating_margin', 'net_margin'
        ]

        prof_df = pd.DataFrame(index=self.df.index)

        for metric in profitability_metrics:
            if metric in self.df.columns:
                prof_df[metric] = self.df[metric]

                # TTM 평균 (4분기)
                prof_df[f'{metric}_ttm'] = (
                    self.df[metric].rolling(window=4, min_periods=1).mean()
                )

                # 추세 (최근 8분기 선형 회귀)
                if len(self.df) >= 8:
                    trend = self._calculate_trend(self.df[metric].tail(8))
                    prof_df.loc[prof_df.index[-1], f'{metric}_trend'] = trend

        self.results['profitability'] = prof_df
        return prof_df

    def analyze_financial_health(self) -> pd.DataFrame:
        """
        재무건전성 분석
        """
        print("[3/8] 재무건전성 분석 중...")

        health_df = pd.DataFrame(index=self.df.index)

        # 레버리지 지표
        if 'debt_to_equity' in self.df.columns:
            health_df['debt_to_equity'] = self.df['debt_to_equity']

        if 'debt_to_assets' in self.df.columns:
            health_df['debt_to_assets'] = self.df['debt_to_assets']

        # 유동성 지표
        if 'current_ratio' in self.df.columns:
            health_df['current_ratio'] = self.df['current_ratio']

        if 'quick_ratio' in self.df.columns:
            health_df['quick_ratio'] = self.df['quick_ratio']

        # 이자보상배율
        if 'operating_income' in self.df.columns and 'interest_expense' in self.df.columns:
            interest_expense = self.df['interest_expense'].abs()
            mask = interest_expense > 0
            health_df['interest_coverage'] = np.nan
            health_df.loc[mask, 'interest_coverage'] = (
                    self.df.loc[mask, 'operating_income'] / interest_expense[mask]
            )

        # Altman Z-Score (제조업 기준)
        if all(col in self.df.columns for col in
               ['current_assets', 'current_liabilities', 'total_assets',
                'retained_earnings', 'operating_income', 'stockholders_equity',
                'total_liabilities']):
            working_capital = self.df['current_assets'] - self.df['current_liabilities']

            X1 = working_capital / self.df['total_assets']
            X2 = self.df['retained_earnings'] / self.df['total_assets']
            X3 = self.df['operating_income'] / self.df['total_assets']
            X4 = self.df['stockholders_equity'] / self.df['total_liabilities']
            X5 = self.df['revenue'] / self.df['total_assets']

            health_df['altman_z_score'] = 1.2 * X1 + 1.4 * X2 + 3.3 * X3 + 0.6 * X4 + 1.0 * X5

        self.results['financial_health'] = health_df
        return health_df

    def analyze_efficiency(self) -> pd.DataFrame:
        """
        효율성 분석
        """
        print("[4/8] 효율성 분석 중...")

        eff_df = pd.DataFrame(index=self.df.index)

        efficiency_metrics = [
            'asset_turnover', 'inventory_turnover', 'receivables_turnover',
            'days_inventory', 'days_receivables'
        ]

        for metric in efficiency_metrics:
            if metric in self.df.columns:
                eff_df[metric] = self.df[metric]

        # Cash Conversion Cycle (CCC)
        if all(col in self.df.columns for col in
               ['days_inventory', 'days_receivables']):

            days_payable = np.nan
            if 'accounts_payable' in self.df.columns and 'cost_of_revenue' in self.df.columns:
                avg_payables = (self.df['accounts_payable'] +
                                self.df['accounts_payable'].shift(4)) / 2
                days_payable = (avg_payables / self.df['cost_of_revenue']) * 365

            eff_df['days_payable'] = days_payable
            eff_df['cash_conversion_cycle'] = (
                    self.df['days_inventory'] +
                    self.df['days_receivables'] -
                    days_payable.fillna(0)
            )

        self.results['efficiency'] = eff_df
        return eff_df

    def analyze_cash_flow(self) -> pd.DataFrame:
        """
        현금흐름 분석
        """
        print("[5/8] 현금흐름 분석 중...")

        cf_df = pd.DataFrame(index=self.df.index)

        # 영업현금흐름
        if 'operating_cash_flow' in self.df.columns:
            cf_df['operating_cash_flow'] = self.df['operating_cash_flow']
            cf_df['ocf_ttm'] = self.df['operating_cash_flow'].rolling(4, min_periods=1).sum()

        # 투자현금흐름
        if 'investing_cash_flow' in self.df.columns:
            cf_df['investing_cash_flow'] = self.df['investing_cash_flow']

        # 재무현금흐름
        if 'financing_cash_flow' in self.df.columns:
            cf_df['financing_cash_flow'] = self.df['financing_cash_flow']

        # 자유현금흐름 (FCF)
        if 'operating_cash_flow' in self.df.columns and 'capital_expenditure' in self.df.columns:
            cf_df['free_cash_flow'] = (
                    self.df['operating_cash_flow'] - self.df['capital_expenditure'].abs()
            )
            cf_df['fcf_ttm'] = cf_df['free_cash_flow'].rolling(4, min_periods=1).sum()

        # OCF/Net Income 비율 (Quality of Earnings)
        if 'operating_cash_flow' in self.df.columns and 'net_income' in self.df.columns:
            mask = self.df['net_income'] > 0
            cf_df['ocf_to_net_income'] = np.nan
            cf_df.loc[mask, 'ocf_to_net_income'] = (
                    self.df.loc[mask, 'operating_cash_flow'] / self.df.loc[mask, 'net_income']
            )

        # FCF Margin
        if 'free_cash_flow' in cf_df.columns and 'revenue' in self.df.columns:
            cf_df['fcf_margin'] = (cf_df['free_cash_flow'] / self.df['revenue']) * 100

        self.results['cash_flow'] = cf_df
        return cf_df

    def calculate_valuation_metrics(self, current_price: Optional[float] = None,
                                    shares_outstanding: Optional[float] = None) -> pd.DataFrame:
        """
        밸류에이션 지표 계산

        Args:
            current_price: 현재 주가 (optional)
            shares_outstanding: 발행주식수 (optional)
        """
        print("[6/8] 밸류에이션 지표 계산 중...")

        val_df = pd.DataFrame(index=self.df.index)

        # 시가총액 계산 (데이터에 있는 경우)
        if current_price and shares_outstanding:
            market_cap = current_price * shares_outstanding
            val_df['market_cap'] = market_cap

            # P/E Ratio (TTM)
            if 'net_income' in self.df.columns:
                net_income_ttm = self.df['net_income'].rolling(4, min_periods=1).sum()
                val_df['pe_ratio'] = market_cap / net_income_ttm

            # P/B Ratio
            if 'stockholders_equity' in self.df.columns:
                val_df['pb_ratio'] = market_cap / self.df['stockholders_equity']

            # P/S Ratio
            if 'revenue' in self.df.columns:
                revenue_ttm = self.df['revenue'].rolling(4, min_periods=1).sum()
                val_df['ps_ratio'] = market_cap / revenue_ttm

            # EV/EBITDA
            if all(col in self.df.columns for col in ['operating_income', 'depreciation_amortization']):
                ebitda = self.df['operating_income'] + self.df['depreciation_amortization'].fillna(0)
                ebitda_ttm = ebitda.rolling(4, min_periods=1).sum()

                # Enterprise Value (간단 계산)
                total_debt = self.df.get('total_debt', self.df.get('total_liabilities', 0))
                cash = self.df.get('cash_and_equivalents', 0)
                ev = market_cap + total_debt - cash

                val_df['ev_ebitda'] = ev / ebitda_ttm

        # Price-independent metrics
        # EV/Sales
        if 'revenue' in self.df.columns and 'total_liabilities' in self.df.columns:
            revenue_ttm = self.df['revenue'].rolling(4, min_periods=1).sum()
            val_df['revenue_ttm'] = revenue_ttm

        self.results['valuation'] = val_df
        return val_df

    def build_revenue_operating_income_model(self) -> Dict:
        """
        매출액-영업이익 관계 회귀분석 모델
        향후 매출 전망으로 영업이익 예측
        """
        print("[7/8] 매출-영업이익 예측 모델 구축 중...")

        if 'revenue' not in self.df.columns or 'operating_income' not in self.df.columns:
            print("  경고: 매출액 또는 영업이익 데이터 없음")
            return {}

        # 결측치 제거
        data = self.df[['revenue', 'operating_income']].dropna()

        if len(data) < 8:
            print("  경고: 데이터 포인트 부족 (최소 8개 필요)")
            return {}

        # TTM 데이터 사용 (더 안정적)
        revenue_ttm = data['revenue'].rolling(4, min_periods=4).sum().dropna()
        operating_income_ttm = data['operating_income'].rolling(4, min_periods=4).sum().dropna()

        # 공통 인덱스
        common_index = revenue_ttm.index.intersection(operating_income_ttm.index)
        X = revenue_ttm.loc[common_index].values.reshape(-1, 1)
        y = operating_income_ttm.loc[common_index].values

        if len(X) < 8:
            print("  경고: TTM 데이터 포인트 부족")
            return {}

        # 선형 회귀 모델
        model = LinearRegression()
        model.fit(X, y)

        y_pred = model.predict(X)
        r2 = r2_score(y, y_pred)

        # Operating Margin 계산
        operating_margin = (y / X.flatten()) * 100
        avg_margin = np.mean(operating_margin)

        results = {
            'model': model,
            'slope': model.coef_[0],
            'intercept': model.intercept_,
            'r2': r2,
            'avg_operating_margin': avg_margin,
            'X': X.flatten(),
            'y': y,
            'y_pred': y_pred,
            'dates': common_index
        }

        # 예측 함수
        def predict_operating_income(revenue_forecast):
            """주어진 매출 전망으로 영업이익 예측"""
            return model.predict(np.array([[revenue_forecast]]))[0]

        results['predict_function'] = predict_operating_income

        self.results['revenue_oi_model'] = results

        print(f"  회귀식: Operating Income = {model.coef_[0]:.4f} * Revenue + {model.intercept_:.2f}")
        print(f"  R² = {r2:.4f}")
        print(f"  평균 영업이익률 = {avg_margin:.2f}%")

        return results

    def forecast_next_periods(self, periods: int = 4) -> pd.DataFrame:
        """
        향후 기간 예측 (단순 추세 기반)

        Args:
            periods: 예측할 기간 수 (분기)
        """
        print("[8/8] 향후 실적 예측 중...")

        forecast_df = pd.DataFrame()

        # 최근 8분기 데이터로 추세 계산
        recent_periods = min(8, len(self.df))

        if 'revenue' in self.df.columns:
            revenue_recent = self.df['revenue'].tail(recent_periods)
            revenue_growth = revenue_recent.pct_change().mean()

            last_revenue = revenue_recent.iloc[-1]
            forecast_revenue = []
            for i in range(1, periods + 1):
                forecast_revenue.append(last_revenue * (1 + revenue_growth) ** i)

            forecast_df['revenue_forecast'] = forecast_revenue

            # 영업이익 예측 (회귀 모델 사용)
            if 'revenue_oi_model' in self.results and self.results['revenue_oi_model']:
                predict_func = self.results['revenue_oi_model']['predict_function']
                forecast_df['operating_income_forecast'] = [
                    predict_func(rev) for rev in forecast_revenue
                ]
                forecast_df['operating_margin_forecast'] = (
                        (forecast_df['operating_income_forecast'] / forecast_df['revenue_forecast']) * 100
                )

        self.results['forecast'] = forecast_df
        return forecast_df

    def _calculate_trend(self, series: pd.Series) -> float:
        """
        선형 추세 계산 (기울기)
        """
        if len(series) < 2:
            return 0

        x = np.arange(len(series))
        y = series.values

        mask = ~np.isnan(y)
        if mask.sum() < 2:
            return 0

        slope, _ = np.polyfit(x[mask], y[mask], 1)
        return slope

    def create_growth_chart(self, save_path: Optional[str] = None):
        """
        성장률 차트 생성
        """
        if 'growth_rates' not in self.results:
            self.calculate_growth_rates()

        growth_df = self.results['growth_rates']

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle('Growth Analysis', fontsize=16, fontweight='bold')

        # 1. Revenue Growth
        ax = axes[0, 0]
        if 'revenue_yoy_growth' in growth_df.columns:
            growth_df['revenue_yoy_growth'].plot(ax=ax, marker='o', label='YoY', linewidth=2)
        if 'revenue_qoq_growth' in growth_df.columns:
            growth_df['revenue_qoq_growth'].plot(ax=ax, marker='s', label='QoQ', linewidth=2, alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Revenue Growth Rate (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. Operating Income Growth
        ax = axes[0, 1]
        if 'operating_income_yoy_growth' in growth_df.columns:
            growth_df['operating_income_yoy_growth'].plot(ax=ax, marker='o', label='YoY', linewidth=2)
        if 'operating_income_qoq_growth' in growth_df.columns:
            growth_df['operating_income_qoq_growth'].plot(ax=ax, marker='s', label='QoQ', linewidth=2, alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Operating Income Growth Rate (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 3. Net Income Growth
        ax = axes[1, 0]
        if 'net_income_yoy_growth' in growth_df.columns:
            growth_df['net_income_yoy_growth'].plot(ax=ax, marker='o', label='YoY', linewidth=2)
        if 'net_income_qoq_growth' in growth_df.columns:
            growth_df['net_income_qoq_growth'].plot(ax=ax, marker='s', label='QoQ', linewidth=2, alpha=0.7)
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Net Income Growth Rate (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 4. TTM Revenue & Net Income
        ax = axes[1, 1]
        if 'revenue_ttm' in growth_df.columns:
            ax2 = ax.twinx()
            growth_df['revenue_ttm'].plot(ax=ax, marker='o', label='Revenue TTM',
                                          linewidth=2, color='blue')
            if 'net_income_ttm' in growth_df.columns:
                growth_df['net_income_ttm'].plot(ax=ax2, marker='s', label='Net Income TTM',
                                                 linewidth=2, color='green', alpha=0.7)
            ax.set_title('TTM Revenue & Net Income', fontsize=12, fontweight='bold')
            ax.set_ylabel('Revenue TTM', color='blue')
            ax2.set_ylabel('Net Income TTM', color='green')
            ax.legend(loc='upper left')
            ax2.legend(loc='upper right')
            ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['growth'] = fig
        return fig

    def create_profitability_chart(self, save_path: Optional[str] = None):
        """
        수익성 차트 생성
        """
        if 'profitability' not in self.results:
            self.analyze_profitability_trends()

        prof_df = self.results['profitability']

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle('Profitability Analysis', fontsize=16, fontweight='bold')

        # 1. ROE, ROA, ROIC
        ax = axes[0, 0]
        for metric in ['roe', 'roa', 'roic']:
            if metric in prof_df.columns:
                prof_df[metric].plot(ax=ax, marker='o', label=metric.upper(), linewidth=2)
        ax.set_title('Return Ratios (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Return (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. Profit Margins
        ax = axes[0, 1]
        for metric in ['gross_margin', 'operating_margin', 'net_margin']:
            if metric in prof_df.columns:
                label = metric.replace('_', ' ').title()
                prof_df[metric].plot(ax=ax, marker='o', label=label, linewidth=2)
        ax.set_title('Profit Margins (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Margin (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 3. ROE TTM Trend
        ax = axes[1, 0]
        if 'roe_ttm' in prof_df.columns:
            prof_df['roe_ttm'].plot(ax=ax, marker='o', linewidth=2, color='darkblue')
            ax.fill_between(prof_df.index, 0, prof_df['roe_ttm'], alpha=0.3, color='blue')
        ax.set_title('ROE (TTM) Trend', fontsize=12, fontweight='bold')
        ax.set_ylabel('ROE (%)')
        ax.grid(True, alpha=0.3)

        # 4. Operating Margin TTM Trend
        ax = axes[1, 1]
        if 'operating_margin_ttm' in prof_df.columns:
            prof_df['operating_margin_ttm'].plot(ax=ax, marker='o', linewidth=2, color='darkgreen')
            ax.fill_between(prof_df.index, 0, prof_df['operating_margin_ttm'],
                            alpha=0.3, color='green')
        ax.set_title('Operating Margin (TTM) Trend', fontsize=12, fontweight='bold')
        ax.set_ylabel('Operating Margin (%)')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['profitability'] = fig
        return fig

    def create_financial_health_chart(self, save_path: Optional[str] = None):
        """
        재무건전성 차트 생성
        """
        if 'financial_health' not in self.results:
            self.analyze_financial_health()

        health_df = self.results['financial_health']

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle('Financial Health Analysis', fontsize=16, fontweight='bold')

        # 1. Leverage Ratios
        ax = axes[0, 0]
        if 'debt_to_equity' in health_df.columns:
            health_df['debt_to_equity'].plot(ax=ax, marker='o', label='D/E Ratio', linewidth=2)
        if 'debt_to_assets' in health_df.columns:
            health_df['debt_to_assets'].plot(ax=ax, marker='s', label='D/A Ratio',
                                             linewidth=2, alpha=0.7)
        ax.set_title('Leverage Ratios (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Ratio (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. Liquidity Ratios
        ax = axes[0, 1]
        if 'current_ratio' in health_df.columns:
            health_df['current_ratio'].plot(ax=ax, marker='o', label='Current Ratio', linewidth=2)
        if 'quick_ratio' in health_df.columns:
            health_df['quick_ratio'].plot(ax=ax, marker='s', label='Quick Ratio',
                                          linewidth=2, alpha=0.7)
        ax.axhline(y=1, color='red', linestyle='--', alpha=0.5, label='Threshold')
        ax.set_title('Liquidity Ratios', fontsize=12, fontweight='bold')
        ax.set_ylabel('Ratio')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 3. Interest Coverage
        ax = axes[1, 0]
        if 'interest_coverage' in health_df.columns:
            health_df['interest_coverage'].plot(ax=ax, marker='o', linewidth=2, color='purple')
            ax.axhline(y=2, color='red', linestyle='--', alpha=0.5, label='Min Threshold')
            ax.axhline(y=5, color='green', linestyle='--', alpha=0.5, label='Healthy')
        ax.set_title('Interest Coverage Ratio', fontsize=12, fontweight='bold')
        ax.set_ylabel('Times')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 4. Altman Z-Score
        ax = axes[1, 1]
        if 'altman_z_score' in health_df.columns:
            health_df['altman_z_score'].plot(ax=ax, marker='o', linewidth=2, color='darkred')
            ax.axhline(y=1.8, color='red', linestyle='--', alpha=0.5, label='Distress (<1.8)')
            ax.axhline(y=3.0, color='green', linestyle='--', alpha=0.5, label='Safe (>3.0)')
            ax.fill_between(health_df.index, 1.8, 3.0, alpha=0.1, color='yellow')
        ax.set_title('Altman Z-Score (Bankruptcy Prediction)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Z-Score')
        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['financial_health'] = fig
        return fig

    def create_cash_flow_chart(self, save_path: Optional[str] = None):
        """
        현금흐름 차트 생성
        """
        if 'cash_flow' not in self.results:
            self.analyze_cash_flow()

        cf_df = self.results['cash_flow']

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle('Cash Flow Analysis', fontsize=16, fontweight='bold')

        # 1. Cash Flow Components
        ax = axes[0, 0]
        cf_components = ['operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow']
        for comp in cf_components:
            if comp in cf_df.columns:
                label = comp.replace('_', ' ').title()
                cf_df[comp].plot(ax=ax, marker='o', label=label, linewidth=2)
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Cash Flow Components', fontsize=12, fontweight='bold')
        ax.set_ylabel('Cash Flow')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. Free Cash Flow
        ax = axes[0, 1]
        if 'free_cash_flow' in cf_df.columns:
            cf_df['free_cash_flow'].plot(ax=ax, marker='o', linewidth=2, color='green')
            ax.fill_between(cf_df.index, 0, cf_df['free_cash_flow'],
                            where=cf_df['free_cash_flow'] >= 0, alpha=0.3, color='green')
            ax.fill_between(cf_df.index, 0, cf_df['free_cash_flow'],
                            where=cf_df['free_cash_flow'] < 0, alpha=0.3, color='red')
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Free Cash Flow', fontsize=12, fontweight='bold')
        ax.set_ylabel('FCF')
        ax.grid(True, alpha=0.3)

        # 3. OCF vs Net Income (Quality of Earnings)
        ax = axes[1, 0]
        if 'ocf_to_net_income' in cf_df.columns:
            cf_df['ocf_to_net_income'].plot(ax=ax, marker='o', linewidth=2, color='blue')
            ax.axhline(y=1, color='green', linestyle='--', alpha=0.5, label='Parity')
            ax.axhline(y=0.8, color='orange', linestyle='--', alpha=0.5, label='Warning')
        ax.set_title('OCF / Net Income Ratio (Quality of Earnings)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Ratio')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 4. FCF Margin
        ax = axes[1, 1]
        if 'fcf_margin' in cf_df.columns:
            cf_df['fcf_margin'].plot(ax=ax, marker='o', linewidth=2, color='purple')
            ax.fill_between(cf_df.index, 0, cf_df['fcf_margin'], alpha=0.3, color='purple')
        ax.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax.set_title('Free Cash Flow Margin (%)', fontsize=12, fontweight='bold')
        ax.set_ylabel('FCF Margin (%)')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['cash_flow'] = fig
        return fig

    def create_revenue_oi_model_chart(self, save_path: Optional[str] = None):
        """
        매출-영업이익 회귀 모델 차트
        """
        if 'revenue_oi_model' not in self.results or not self.results['revenue_oi_model']:
            print("  경고: 회귀 모델이 없습니다.")
            return None

        model_data = self.results['revenue_oi_model']

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))
        fig.suptitle('Revenue-Operating Income Regression Model', fontsize=16, fontweight='bold')

        # 1. Scatter Plot with Regression Line
        ax = axes[0]
        ax.scatter(model_data['X'], model_data['y'], alpha=0.6, s=100, label='Actual')
        ax.plot(model_data['X'], model_data['y_pred'], 'r-', linewidth=2, label='Regression Line')

        # 회귀식 표시
        slope = model_data['slope']
        intercept = model_data['intercept']
        r2 = model_data['r2']

        equation_text = f'OI = {slope:.4f} * Revenue + {intercept:.2f}\nR² = {r2:.4f}'
        ax.text(0.05, 0.95, equation_text, transform=ax.transAxes,
                fontsize=11, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        ax.set_title('Revenue vs Operating Income (TTM)', fontsize=12, fontweight='bold')
        ax.set_xlabel('Revenue (TTM)')
        ax.set_ylabel('Operating Income (TTM)')
        ax.legend()
        ax.grid(True, alpha=0.3)

        # 2. Residuals Plot
        ax = axes[1]
        residuals = model_data['y'] - model_data['y_pred']
        ax.scatter(model_data['y_pred'], residuals, alpha=0.6, s=100)
        ax.axhline(y=0, color='r', linestyle='--', linewidth=2)
        ax.set_title('Residuals Plot', fontsize=12, fontweight='bold')
        ax.set_xlabel('Predicted Operating Income')
        ax.set_ylabel('Residuals')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['revenue_oi_model'] = fig
        return fig

    def create_forecast_chart(self, save_path: Optional[str] = None):
        """
        예측 차트 생성
        """
        if 'forecast' not in self.results:
            self.forecast_next_periods()

        forecast_df = self.results['forecast']

        if forecast_df.empty:
            print("  경고: 예측 데이터가 없습니다.")
            return None

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))
        fig.suptitle('Revenue & Operating Income Forecast', fontsize=16, fontweight='bold')

        # Historical + Forecast
        historical_periods = 12  # 최근 12분기

        # 1. Revenue Forecast
        ax = axes[0]
        if 'revenue' in self.df.columns:
            historical_revenue = self.df['revenue'].tail(historical_periods)
            ax.plot(historical_revenue.index, historical_revenue.values,
                    marker='o', linewidth=2, label='Historical', color='blue')

            # Forecast
            if 'revenue_forecast' in forecast_df.columns:
                last_date = historical_revenue.index[-1]
                forecast_dates = pd.date_range(start=last_date, periods=len(forecast_df) + 1, freq='Q')[1:]

                # Connect historical to forecast
                ax.plot([last_date, forecast_dates[0]],
                        [historical_revenue.iloc[-1], forecast_df['revenue_forecast'].iloc[0]],
                        'g--', linewidth=2, alpha=0.5)

                ax.plot(forecast_dates, forecast_df['revenue_forecast'].values,
                        marker='s', linewidth=2, label='Forecast', color='green', linestyle='--')

        ax.set_title('Revenue Forecast', fontsize=12, fontweight='bold')
        ax.set_ylabel('Revenue')
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        # 2. Operating Income Forecast
        ax = axes[1]
        if 'operating_income' in self.df.columns:
            historical_oi = self.df['operating_income'].tail(historical_periods)
            ax.plot(historical_oi.index, historical_oi.values,
                    marker='o', linewidth=2, label='Historical', color='blue')

            # Forecast
            if 'operating_income_forecast' in forecast_df.columns:
                # Connect historical to forecast
                ax.plot([last_date, forecast_dates[0]],
                        [historical_oi.iloc[-1], forecast_df['operating_income_forecast'].iloc[0]],
                        'g--', linewidth=2, alpha=0.5)

                ax.plot(forecast_dates, forecast_df['operating_income_forecast'].values,
                        marker='s', linewidth=2, label='Forecast', color='green', linestyle='--')

        ax.set_title('Operating Income Forecast', fontsize=12, fontweight='bold')
        ax.set_ylabel('Operating Income')
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  차트 저장: {save_path}")

        self.charts['forecast'] = fig
        return fig

    def create_comprehensive_dashboard(self, save_path: Optional[str] = None):
        """
        종합 대시보드 생성
        """
        fig = plt.figure(figsize=(20, 12))
        gs = fig.add_gridspec(3, 3, hspace=0.3, wspace=0.3)

        fig.suptitle('Comprehensive Financial Dashboard', fontsize=18, fontweight='bold')

        # 1. Revenue & Net Income Trend
        ax1 = fig.add_subplot(gs[0, 0])
        if 'revenue' in self.df.columns:
            revenue_ttm = self.df['revenue'].rolling(4, min_periods=1).sum()
            ax1.plot(revenue_ttm.index, revenue_ttm.values, marker='o', linewidth=2, label='Revenue')
        if 'net_income' in self.df.columns:
            ni_ttm = self.df['net_income'].rolling(4, min_periods=1).sum()
            ax1_twin = ax1.twinx()
            ax1_twin.plot(ni_ttm.index, ni_ttm.values, marker='s', linewidth=2,
                          color='green', alpha=0.7, label='Net Income')
        ax1.set_title('Revenue & Net Income (TTM)', fontsize=10, fontweight='bold')
        ax1.legend(loc='upper left', fontsize=8)
        ax1.grid(True, alpha=0.3)

        # 2. Profit Margins
        ax2 = fig.add_subplot(gs[0, 1])
        for metric in ['gross_margin', 'operating_margin', 'net_margin']:
            if metric in self.df.columns:
                self.df[metric].plot(ax=ax2, marker='o', linewidth=2, label=metric.replace('_', ' ').title())
        ax2.set_title('Profit Margins (%)', fontsize=10, fontweight='bold')
        ax2.legend(fontsize=8)
        ax2.grid(True, alpha=0.3)

        # 3. ROE, ROA, ROIC
        ax3 = fig.add_subplot(gs[0, 2])
        for metric in ['roe', 'roa', 'roic']:
            if metric in self.df.columns:
                self.df[metric].plot(ax=ax3, marker='o', linewidth=2, label=metric.upper())
        ax3.set_title('Return Ratios (%)', fontsize=10, fontweight='bold')
        ax3.legend(fontsize=8)
        ax3.grid(True, alpha=0.3)

        # 4. Leverage
        ax4 = fig.add_subplot(gs[1, 0])
        if 'debt_to_equity' in self.df.columns:
            self.df['debt_to_equity'].plot(ax=ax4, marker='o', linewidth=2, label='D/E')
        if 'debt_to_assets' in self.df.columns:
            self.df['debt_to_assets'].plot(ax=ax4, marker='s', linewidth=2, label='D/A', alpha=0.7)
        ax4.set_title('Leverage Ratios (%)', fontsize=10, fontweight='bold')
        ax4.legend(fontsize=8)
        ax4.grid(True, alpha=0.3)

        # 5. Liquidity
        ax5 = fig.add_subplot(gs[1, 1])
        if 'current_ratio' in self.df.columns:
            self.df['current_ratio'].plot(ax=ax5, marker='o', linewidth=2, label='Current')
        if 'quick_ratio' in self.df.columns:
            self.df['quick_ratio'].plot(ax=ax5, marker='s', linewidth=2, label='Quick', alpha=0.7)
        ax5.axhline(y=1, color='red', linestyle='--', alpha=0.5)
        ax5.set_title('Liquidity Ratios', fontsize=10, fontweight='bold')
        ax5.legend(fontsize=8)
        ax5.grid(True, alpha=0.3)

        # 6. Asset Turnover
        ax6 = fig.add_subplot(gs[1, 2])
        if 'asset_turnover' in self.df.columns:
            self.df['asset_turnover'].plot(ax=ax6, marker='o', linewidth=2, color='purple')
        ax6.set_title('Asset Turnover', fontsize=10, fontweight='bold')
        ax6.grid(True, alpha=0.3)

        # 7. Cash Flow
        ax7 = fig.add_subplot(gs[2, 0])
        if 'operating_cash_flow' in self.df.columns:
            self.df['operating_cash_flow'].plot(ax=ax7, marker='o', linewidth=2, label='OCF')
        if 'free_cash_flow' in self.results.get('cash_flow', pd.DataFrame()).columns:
            self.results['cash_flow']['free_cash_flow'].plot(ax=ax7, marker='s', linewidth=2,
                                                             label='FCF', alpha=0.7)
        ax7.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax7.set_title('Cash Flow', fontsize=10, fontweight='bold')
        ax7.legend(fontsize=8)
        ax7.grid(True, alpha=0.3)

        # 8. Growth Rates
        ax8 = fig.add_subplot(gs[2, 1])
        if 'growth_rates' in self.results:
            gr = self.results['growth_rates']
            if 'revenue_yoy_growth' in gr.columns:
                gr['revenue_yoy_growth'].plot(ax=ax8, marker='o', linewidth=2, label='Revenue')
            if 'net_income_yoy_growth' in gr.columns:
                gr['net_income_yoy_growth'].plot(ax=ax8, marker='s', linewidth=2,
                                                 label='Net Income', alpha=0.7)
        ax8.axhline(y=0, color='black', linestyle='--', alpha=0.3)
        ax8.set_title('YoY Growth Rates (%)', fontsize=10, fontweight='bold')
        ax8.legend(fontsize=8)
        ax8.grid(True, alpha=0.3)

        # 9. Summary Stats
        ax9 = fig.add_subplot(gs[2, 2])
        ax9.axis('off')

        # 최근 데이터 요약
        latest_data = self.df.iloc[-1]
        summary_text = "Latest Quarter Summary:\n\n"

        if 'revenue' in self.df.columns:
            summary_text += f"Revenue: {latest_data['revenue']:,.0f}\n"
        if 'net_income' in self.df.columns:
            summary_text += f"Net Income: {latest_data['net_income']:,.0f}\n"
        if 'roe' in self.df.columns:
            summary_text += f"ROE: {latest_data['roe']:.2f}%\n"
        if 'net_margin' in self.df.columns:
            summary_text += f"Net Margin: {latest_data['net_margin']:.2f}%\n"
        if 'current_ratio' in self.df.columns:
            summary_text += f"Current Ratio: {latest_data['current_ratio']:.2f}\n"
        if 'debt_to_equity' in self.df.columns:
            summary_text += f"D/E Ratio: {latest_data['debt_to_equity']:.2f}%\n"

        ax9.text(0.1, 0.9, summary_text, transform=ax9.transAxes,
                 fontsize=10, verticalalignment='top', family='monospace',
                 bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.5))

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"  종합 대시보드 저장: {save_path}")

        self.charts['dashboard'] = fig
        return fig

    def generate_summary_report(self) -> str:
        """
        텍스트 요약 리포트 생성
        """
        report = []
        report.append("=" * 80)
        report.append("FINANCIAL ANALYSIS SUMMARY REPORT")
        report.append("=" * 80)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Period: {self.df.index[0].strftime('%Y-%m-%d')} to {self.df.index[-1].strftime('%Y-%m-%d')}")
        report.append(f"Number of Quarters: {len(self.df)}")
        report.append("=" * 80)

        # Latest Quarter Data
        latest = self.df.iloc[-1]
        report.append("\n[LATEST QUARTER METRICS]")
        report.append("-" * 80)

        if 'revenue' in self.df.columns:
            report.append(f"Revenue: {latest['revenue']:,.0f}")
        if 'net_income' in self.df.columns:
            report.append(f"Net Income: {latest['net_income']:,.0f}")
        if 'total_assets' in self.df.columns:
            report.append(f"Total Assets: {latest['total_assets']:,.0f}")
        if 'stockholders_equity' in self.df.columns:
            report.append(f"Stockholders Equity: {latest['stockholders_equity']:,.0f}")

        # Growth Analysis
        if 'growth_rates' in self.results:
            report.append("\n[GROWTH ANALYSIS]")
            report.append("-" * 80)
            gr = self.results['growth_rates'].iloc[-1]

            if 'revenue_yoy_growth' in gr:
                report.append(f"Revenue YoY Growth: {gr['revenue_yoy_growth']:.2f}%")
            if 'net_income_yoy_growth' in gr:
                report.append(f"Net Income YoY Growth: {gr['net_income_yoy_growth']:.2f}%")
            if 'revenue_ttm_yoy_growth' in gr:
                report.append(f"Revenue TTM YoY Growth: {gr['revenue_ttm_yoy_growth']:.2f}%")

        # Profitability
        if 'profitability' in self.results:
            report.append("\n[PROFITABILITY METRICS]")
            report.append("-" * 80)
            prof = self.results['profitability'].iloc[-1]

            if 'roe' in prof:
                report.append(f"ROE: {prof['roe']:.2f}%")
            if 'roa' in prof:
                report.append(f"ROA: {prof['roa']:.2f}%")
            if 'roic' in prof:
                report.append(f"ROIC: {prof['roic']:.2f}%")
            if 'gross_margin' in prof:
                report.append(f"Gross Margin: {prof['gross_margin']:.2f}%")
            if 'operating_margin' in prof:
                report.append(f"Operating Margin: {prof['operating_margin']:.2f}%")
            if 'net_margin' in prof:
                report.append(f"Net Margin: {prof['net_margin']:.2f}%")

        # Financial Health
        if 'financial_health' in self.results:
            report.append("\n[FINANCIAL HEALTH]")
            report.append("-" * 80)
            health = self.results['financial_health'].iloc[-1]

            if 'debt_to_equity' in health:
                report.append(f"Debt-to-Equity: {health['debt_to_equity']:.2f}%")
            if 'current_ratio' in health:
                report.append(f"Current Ratio: {health['current_ratio']:.2f}")
            if 'quick_ratio' in health:
                report.append(f"Quick Ratio: {health['quick_ratio']:.2f}")
            if 'interest_coverage' in health:
                report.append(f"Interest Coverage: {health['interest_coverage']:.2f}x")
            if 'altman_z_score' in health:
                z_score = health['altman_z_score']
                report.append(f"Altman Z-Score: {z_score:.2f}")
                if z_score > 3.0:
                    report.append("  Status: Safe Zone")
                elif z_score > 1.8:
                    report.append("  Status: Grey Zone")
                else:
                    report.append("  Status: Distress Zone")

        # Cash Flow
        if 'cash_flow' in self.results:
            report.append("\n[CASH FLOW ANALYSIS]")
            report.append("-" * 80)
            cf = self.results['cash_flow'].iloc[-1]

            if 'operating_cash_flow' in cf:
                report.append(f"Operating Cash Flow: {cf['operating_cash_flow']:,.0f}")
            if 'free_cash_flow' in cf:
                report.append(f"Free Cash Flow: {cf['free_cash_flow']:,.0f}")
            if 'ocf_to_net_income' in cf:
                report.append(f"OCF/Net Income: {cf['ocf_to_net_income']:.2f}")
            if 'fcf_margin' in cf:
                report.append(f"FCF Margin: {cf['fcf_margin']:.2f}%")

        # Revenue-OI Model
        if 'revenue_oi_model' in self.results and self.results['revenue_oi_model']:
            report.append("\n[REVENUE-OPERATING INCOME MODEL]")
            report.append("-" * 80)
            model = self.results['revenue_oi_model']

            report.append(f"Regression Equation:")
            report.append(f"  Operating Income = {model['slope']:.4f} * Revenue + {model['intercept']:.2f}")
            report.append(f"R-squared: {model['r2']:.4f}")
            report.append(f"Average Operating Margin: {model['avg_operating_margin']:.2f}%")

        # Forecast
        if 'forecast' in self.results and not self.results['forecast'].empty:
            report.append("\n[FORECAST (Next 4 Quarters)]")
            report.append("-" * 80)
            forecast = self.results['forecast']

            for i in range(len(forecast)):
                report.append(f"\nQuarter {i + 1}:")
                if 'revenue_forecast' in forecast.columns:
                    report.append(f"  Revenue: {forecast['revenue_forecast'].iloc[i]:,.0f}")
                if 'operating_income_forecast' in forecast.columns:
                    report.append(f"  Operating Income: {forecast['operating_income_forecast'].iloc[i]:,.0f}")
                if 'operating_margin_forecast' in forecast.columns:
                    report.append(f"  Operating Margin: {forecast['operating_margin_forecast'].iloc[i]:.2f}%")

        report.append("\n" + "=" * 80)
        report.append("END OF REPORT")
        report.append("=" * 80)

        return "\n".join(report)

    def generate_full_report(self, company_name: str = "Company",
                             ticker: str = "TICKER",
                             output_dir: str = "./financial_reports",
                             current_price: Optional[float] = None,
                             shares_outstanding: Optional[float] = None):
        """
        전체 분석 실행 및 리포트 생성

        Args:
            company_name: 회사명
            ticker: 티커
            output_dir: 출력 디렉토리
            current_price: 현재 주가 (optional)
            shares_outstanding: 발행주식수 (optional)
        """
        print("\n" + "=" * 80)
        print(f"Financial Analysis Report Generation: {company_name} ({ticker})")
        print("=" * 80)

        # 출력 디렉토리 생성
        os.makedirs(output_dir, exist_ok=True)
        ticker_dir = os.path.join(output_dir, ticker)
        os.makedirs(ticker_dir, exist_ok=True)

        # 1. 모든 분석 실행
        self.calculate_growth_rates()
        self.analyze_profitability_trends()
        self.analyze_financial_health()
        self.analyze_efficiency()
        self.analyze_cash_flow()
        self.calculate_valuation_metrics(current_price, shares_outstanding)
        self.build_revenue_operating_income_model()
        self.forecast_next_periods(periods=4)

        # 2. 차트 생성
        print("\n차트 생성 중...")
        self.create_growth_chart(os.path.join(ticker_dir, f"{ticker}_growth.png"))
        self.create_profitability_chart(os.path.join(ticker_dir, f"{ticker}_profitability.png"))
        self.create_financial_health_chart(os.path.join(ticker_dir, f"{ticker}_health.png"))
        self.create_cash_flow_chart(os.path.join(ticker_dir, f"{ticker}_cashflow.png"))
        self.create_revenue_oi_model_chart(os.path.join(ticker_dir, f"{ticker}_revenue_oi_model.png"))
        self.create_forecast_chart(os.path.join(ticker_dir, f"{ticker}_forecast.png"))
        self.create_comprehensive_dashboard(os.path.join(ticker_dir, f"{ticker}_dashboard.png"))

        # 3. 텍스트 리포트 생성
        print("\n텍스트 리포트 생성 중...")
        summary_report = self.generate_summary_report()

        report_path = os.path.join(ticker_dir, f"{ticker}_analysis_report.txt")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"Company: {company_name} ({ticker})\n")
            f.write(summary_report)

        print(f"\n리포트 저장 완료: {report_path}")

        # 4. 데이터 저장 (CSV)
        print("\n데이터 저장 중...")

        # 원본 데이터 + 모든 분석 결과 통합
        final_df = self.df.copy()

        for key, result_df in self.results.items():
            if isinstance(result_df, pd.DataFrame) and not result_df.empty:
                # 인덱스 맞추기
                common_idx = final_df.index.intersection(result_df.index)
                for col in result_df.columns:
                    if col not in final_df.columns:
                        final_df.loc[common_idx, col] = result_df.loc[common_idx, col]

        data_path = os.path.join(ticker_dir, f"{ticker}_full_data.csv")
        final_df.to_csv(data_path, encoding='utf-8-sig')
        print(f"  전체 데이터 저장: {data_path}")

        print("\n" + "=" * 80)
        print("분석 완료!")
        print(f"출력 디렉토리: {ticker_dir}")
        print("=" * 80)

        return {
            'results': self.results,
            'charts': self.charts,
            'report_path': report_path,
            'data_path': data_path,
            'output_dir': ticker_dir
        }


def main():
    """
    사용 예제
    """
    print("=" * 80)
    print("Financial Analysis System - Usage Example")
    print("=" * 80)
    print("\n이 시스템은 financial_data_integrator.py와 함께 사용됩니다.")
    print("\n사용 방법:")
    print("-" * 80)
    print("""
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem

# 1. 재무비율이 포함된 DataFrame 준비
df_with_ratios = integrate_financial_ratios(df_normalized)

# 2. 분석 시스템 초기화
analyzer = FinancialAnalysisSystem(df_with_ratios)

# 3. 전체 리포트 생성
results = analyzer.generate_full_report(
    company_name="Apple Inc.",
    ticker="AAPL",
    output_dir="./financial_reports",
    current_price=150.0,  # optional
    shares_outstanding=16000000000  # optional
)

# 4. 개별 분석 실행 (선택사항)
growth_df = analyzer.calculate_growth_rates()
prof_df = analyzer.analyze_profitability_trends()
health_df = analyzer.analyze_financial_health()

# 5. 매출-영업이익 예측
model = analyzer.build_revenue_operating_income_model()
predicted_oi = model['predict_function'](100000000)  # 매출 1억 달러 가정
    """)
    print("-" * 80)


if __name__ == "__main__":
    main()