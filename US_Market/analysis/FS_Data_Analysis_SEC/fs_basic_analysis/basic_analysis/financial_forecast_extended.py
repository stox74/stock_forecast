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
7. 날짜 범위 필터링 (NEW)

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
from datetime import datetime, timedelta
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
                ORDER BY ticker                """
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
                ORDER BY indicator                """
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
                         WHERE ticker = :ticker AND indicator = : indicator                         """
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
                ORDER BY date                """

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
                         WHERE ticker = :ticker AND indicator = : indicator                         """
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
                ORDER BY date                """

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
            print(f"ℹ 밸류에이션 예측 데이터 없음 (정상)")
            return None

    def _filter_dataframe_by_date(self, df: pd.DataFrame,
                                  recent_years: Optional[int] = None,
                                  start_date: Optional[str] = None,
                                  end_date: Optional[str] = None,
                                  recent_quarters: Optional[int] = None) -> pd.DataFrame:
        """
        DataFrame을 날짜 범위로 필터링

        Args:
            df: 필터링할 DataFrame (DatetimeIndex 필수)
            recent_years: 최근 N년 데이터만 (우선순위 1)
            start_date: 시작 날짜 'YYYY-MM-DD' (우선순위 2)
            end_date: 종료 날짜 'YYYY-MM-DD' (우선순위 2)
            recent_quarters: 최근 N개 분기만 (우선순위 3)

        Returns:
            필터링된 DataFrame
        """
        if df is None or df.empty:
            return df

        # 우선순위 1: recent_years
        if recent_years is not None:
            cutoff_date = datetime.now() - timedelta(days=365 * recent_years)
            return df[df.index >= cutoff_date]

        # 우선순위 2: start_date / end_date
        if start_date is not None or end_date is not None:
            filtered = df.copy()
            if start_date:
                start_dt = pd.to_datetime(start_date)
                filtered = filtered[filtered.index >= start_dt]
            if end_date:
                end_dt = pd.to_datetime(end_date)
                filtered = filtered[filtered.index <= end_dt]
            return filtered

        # 우선순위 3: recent_quarters
        if recent_quarters is not None:
            return df.tail(recent_quarters)

        # 필터 없음
        return df

    def predict_operating_income(self) -> Optional[pd.DataFrame]:
        """
        매출 예측 기반 영업이익 예측

        Returns:
            예측 DataFrame (revenue_forecast, operating_income_forecast)
        """
        if self.revenue_forecast is None:
            print("✗ 매출 예측 데이터가 없습니다.")
            return None

        if self.analyzer is None:
            print("✗ Analyzer가 없어 영업이익 예측 불가.")
            return None

        if 'revenue_oi_model' not in self.analyzer.results or not self.analyzer.results['revenue_oi_model']:
            print("✗ Revenue-OI 모델이 없습니다.")
            return None

        model = self.analyzer.results['revenue_oi_model']
        slope = model['slope']
        intercept = model['intercept']

        forecast_df = self.revenue_forecast.copy()
        forecast_df['operating_income_forecast'] = (
                slope * forecast_df['revenue_forecast'] + intercept
        )

        return forecast_df

    def create_individual_charts(self, save_dir: str = "./charts", include_qoq: bool = False,
                                 recent_years: Optional[int] = None,
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None,
                                 recent_quarters: Optional[int] = None,
                                 single_indicator: Optional[str] = None):
        """
        12개 개별 차트 생성 (날짜 범위 필터링 지원)

        Args:
            save_dir: 저장 디렉토리
            include_qoq: QoQ 성장률 포함 여부
            recent_years: 최근 N년 데이터만 (우선순위 1)
            start_date: 시작 날짜 (우선순위 2)
            end_date: 종료 날짜 (우선순위 2)
            recent_quarters: 최근 N개 분기만 (우선순위 3)
            single_indicator: 단일 재무항목 (예: 'revenue', 'net_income')
        """
        if self.analyzer is None:
            print("✗ Analyzer가 없어 차트 생성 불가")
            return

        import os
        os.makedirs(save_dir, exist_ok=True)

        # 원본 데이터 백업
        original_df = self.analyzer.df.copy()

        # 날짜 필터링 적용
        filtered_df = self._filter_dataframe_by_date(
            original_df,
            recent_years=recent_years,
            start_date=start_date,
            end_date=end_date,
            recent_quarters=recent_quarters
        )

        # 필터링 정보 출력
        if len(filtered_df) < len(original_df):
            print(f"\n날짜 필터링 적용:")
            print(f"  원본: {len(original_df)}개 분기")
            print(f"  필터링 후: {len(filtered_df)}개 분기")
            print(
                f"  기간: {filtered_df.index.min().strftime('%Y-%m-%d')} ~ {filtered_df.index.max().strftime('%Y-%m-%d')}\n")

        # Analyzer 데이터 임시 교체
        self.analyzer.df = filtered_df

        # 분석 재실행 (필터링된 데이터로)
        self.analyzer.calculate_growth_rates()
        self.analyzer.analyze_profitability_trends()
        self.analyzer.analyze_financial_health()
        self.analyzer.analyze_cash_flow()
        self.analyzer.build_revenue_operating_income_model()

        ticker = self.ticker or "UNKNOWN"

        print(f"차트 생성 중... (총 12개)")

        # 1. Revenue Growth
        self._create_revenue_growth_chart(ticker, save_dir, include_qoq)
        print(f"  ✓ 1/12: {ticker}_01_revenue_growth.png")

        # 2. Operating Income Growth
        self._create_operating_income_growth_chart(ticker, save_dir, include_qoq)
        print(f"  ✓ 2/12: {ticker}_02_operating_income_growth.png")

        # 3. Net Income Growth
        self._create_net_income_growth_chart(ticker, save_dir, include_qoq)
        print(f"  ✓ 3/12: {ticker}_03_net_income_growth.png")

        # 4. Return Ratios
        self._create_return_ratios_chart(ticker, save_dir)
        print(f"  ✓ 4/12: {ticker}_04_return_ratios.png")

        # 5. Profit Margins
        self._create_profit_margins_chart(ticker, save_dir)
        print(f"  ✓ 5/12: {ticker}_05_profit_margins.png")

        # 6. Leverage
        self._create_leverage_chart(ticker, save_dir)
        print(f"  ✓ 6/12: {ticker}_06_leverage.png")

        # 7. Liquidity
        self._create_liquidity_chart(ticker, save_dir)
        print(f"  ✓ 7/12: {ticker}_07_liquidity.png")

        # 8. Cash Flow
        self._create_cashflow_chart(ticker, save_dir)
        print(f"  ✓ 8/12: {ticker}_08_cashflow.png")

        # 9. Revenue-OI Model
        self._create_revenue_oi_model_chart(ticker, save_dir)
        print(f"  ✓ 9/12: {ticker}_09_revenue_oi_model.png")

        # 10. Forecast (날짜 필터링 미적용 - 예측은 전체 표시)
        self.analyzer.df = original_df  # 예측 차트는 원본 사용
        self._create_forecast_chart(ticker, save_dir)
        print(f"  ✓ 10/12: {ticker}_10_forecast.png")

        # 11. Quarterly Flow (NEW - 날짜 필터링 적용)
        self.analyzer.df = filtered_df  # 필터링된 데이터 사용
        self._create_quarterly_flow_chart(ticker, save_dir,
                                          recent_years=recent_years,
                                          start_date=start_date,
                                          end_date=end_date,
                                          recent_quarters=recent_quarters)
        print(f"  ✓ 11/12: {ticker}_11_quarterly_flow.png")

        # 12. Single Indicator (NEW - 날짜 필터링 적용)
        if single_indicator:
            self._create_single_indicator_chart(ticker, save_dir, single_indicator,
                                                recent_years=recent_years,
                                                start_date=start_date,
                                                end_date=end_date,
                                                recent_quarters=recent_quarters)
            safe_indicator = single_indicator.replace('/', '_').replace(' ', '_')
            print(f"  ✓ 12/12: {ticker}_12_{safe_indicator}.png")
        else:
            print(f"  ⊘ 12/12: 단일 재무항목 차트 생성 안 함 (single_indicator=None)")

        # 원본 데이터 복원
        self.analyzer.df = original_df

        print(f"\n✓ 모든 차트 생성 완료!")
        print(f"  저장 위치: {save_dir}/")

    def _create_revenue_growth_chart(self, ticker, save_dir, include_qoq):
        """Revenue Growth 차트"""
        if 'growth_rates' not in self.analyzer.results or self.analyzer.results['growth_rates'] is None or \
                self.analyzer.results['growth_rates'].empty:
            return

        growth = self.analyzer.results['growth_rates']

        fig, ax = plt.subplots(figsize=(14, 7))

        if include_qoq and 'revenue_qoq_growth' in growth:
            ax.plot(growth.index, growth['revenue_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#1E88E5')
            ax.plot(growth.index, growth['revenue_qoq_growth'], marker='s', linewidth=2,
                    label='QoQ Growth (%)', color='#43A047', linestyle='--')
        else:
            ax.plot(growth.index, growth['revenue_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#1E88E5')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax.set_title(f'{ticker} - Revenue Growth Rate', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_01_revenue_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_operating_income_growth_chart(self, ticker, save_dir, include_qoq):
        """Operating Income Growth 차트"""
        if 'growth_rates' not in self.analyzer.results or self.analyzer.results['growth_rates'] is None or \
                self.analyzer.results['growth_rates'].empty:
            return

        growth = self.analyzer.results['growth_rates']

        fig, ax = plt.subplots(figsize=(14, 7))

        if include_qoq and 'operating_income_qoq_growth' in growth:
            ax.plot(growth.index, growth['operating_income_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#E53935')
            ax.plot(growth.index, growth['operating_income_qoq_growth'], marker='s', linewidth=2,
                    label='QoQ Growth (%)', color='#FB8C00', linestyle='--')
        else:
            ax.plot(growth.index, growth['operating_income_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#E53935')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax.set_title(f'{ticker} - Operating Income Growth Rate', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_02_operating_income_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_net_income_growth_chart(self, ticker, save_dir, include_qoq):
        """Net Income Growth 차트"""
        if 'growth_rates' not in self.analyzer.results or self.analyzer.results['growth_rates'] is None or \
                self.analyzer.results['growth_rates'].empty:
            return

        growth = self.analyzer.results['growth_rates']

        fig, ax = plt.subplots(figsize=(14, 7))

        if include_qoq and 'net_income_qoq_growth' in growth:
            ax.plot(growth.index, growth['net_income_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#8E24AA')
            ax.plot(growth.index, growth['net_income_qoq_growth'], marker='s', linewidth=2,
                    label='QoQ Growth (%)', color='#AB47BC', linestyle='--')
        else:
            ax.plot(growth.index, growth['net_income_yoy_growth'], marker='o', linewidth=2.5,
                    label='YoY Growth (%)', color='#8E24AA')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax.set_title(f'{ticker} - Net Income Growth Rate', fontsize=14, fontweight='bold')
        ax.set_ylabel('Growth Rate (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_03_net_income_growth.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_return_ratios_chart(self, ticker, save_dir):
        """Return Ratios 차트"""
        df = self.analyzer.df

        fig, ax = plt.subplots(figsize=(14, 7))

        if 'roe' in df.columns:
            ax.plot(df.index, df['roe'], marker='o', linewidth=2.5, label='ROE (%)', color='#1E88E5')
        if 'roa' in df.columns:
            ax.plot(df.index, df['roa'], marker='s', linewidth=2.5, label='ROA (%)', color='#43A047')
        if 'roic' in df.columns:
            ax.plot(df.index, df['roic'], marker='^', linewidth=2.5, label='ROIC (%)', color='#E53935')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax.set_title(f'{ticker} - Return Ratios', fontsize=14, fontweight='bold')
        ax.set_ylabel('Return (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_04_return_ratios.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_profit_margins_chart(self, ticker, save_dir):
        """Profit Margins 차트"""
        df = self.analyzer.df

        fig, ax = plt.subplots(figsize=(14, 7))

        if 'gross_margin' in df.columns:
            ax.plot(df.index, df['gross_margin'], marker='o', linewidth=2.5,
                    label='Gross Margin (%)', color='#1E88E5')
        if 'operating_margin' in df.columns:
            ax.plot(df.index, df['operating_margin'], marker='s', linewidth=2.5,
                    label='Operating Margin (%)', color='#43A047')
        if 'net_margin' in df.columns:
            ax.plot(df.index, df['net_margin'], marker='^', linewidth=2.5,
                    label='Net Margin (%)', color='#E53935')

        ax.axhline(y=0, color='red', linestyle='--', linewidth=1, alpha=0.5)
        ax.set_title(f'{ticker} - Profit Margins', fontsize=14, fontweight='bold')
        ax.set_ylabel('Margin (%)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_05_profit_margins.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_leverage_chart(self, ticker, save_dir):
        """Leverage 차트"""
        df = self.analyzer.df

        fig, ax = plt.subplots(figsize=(14, 7))

        if 'debt_to_equity' in df.columns:
            ax.plot(df.index, df['debt_to_equity'], marker='o', linewidth=2.5,
                    label='Debt-to-Equity', color='#E53935')

        ax.axhline(y=1.0, color='orange', linestyle='--', linewidth=1.5, alpha=0.7, label='Benchmark (1.0)')
        ax.set_title(f'{ticker} - Leverage Ratio', fontsize=14, fontweight='bold')
        ax.set_ylabel('Debt-to-Equity Ratio', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_06_leverage.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_liquidity_chart(self, ticker, save_dir):
        """Liquidity 차트"""
        df = self.analyzer.df

        fig, ax = plt.subplots(figsize=(14, 7))

        if 'current_ratio' in df.columns:
            ax.plot(df.index, df['current_ratio'], marker='o', linewidth=2.5,
                    label='Current Ratio', color='#1E88E5')
        if 'quick_ratio' in df.columns:
            ax.plot(df.index, df['quick_ratio'], marker='s', linewidth=2.5,
                    label='Quick Ratio', color='#43A047')

        ax.axhline(y=1.0, color='orange', linestyle='--', linewidth=1.5, alpha=0.7, label='Benchmark (1.0)')
        ax.set_title(f'{ticker} - Liquidity Ratios', fontsize=14, fontweight='bold')
        ax.set_ylabel('Ratio', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_07_liquidity.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_cashflow_chart(self, ticker, save_dir):
        """Cash Flow 차트"""
        if 'cash_flow' not in self.analyzer.results or self.analyzer.results['cash_flow'] is None or \
                self.analyzer.results['cash_flow'].empty:
            return

        cf = self.analyzer.results['cash_flow']

        fig, ax = plt.subplots(figsize=(14, 7))

        if 'operating_cf' in cf.columns:
            ax.bar(cf.index, cf['operating_cf'], label='Operating CF', color='#1E88E5', alpha=0.8)
        if 'investing_cf' in cf.columns:
            ax.bar(cf.index, cf['investing_cf'], label='Investing CF', color='#E53935', alpha=0.8)
        if 'financing_cf' in cf.columns:
            ax.bar(cf.index, cf['financing_cf'], label='Financing CF', color='#43A047', alpha=0.8)

        ax.axhline(y=0, color='black', linestyle='-', linewidth=1)
        ax.set_title(f'{ticker} - Cash Flow Analysis', fontsize=14, fontweight='bold')
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

    def _create_quarterly_flow_chart(self, ticker, save_dir,
                                     recent_years: Optional[int] = None,
                                     start_date: Optional[str] = None,
                                     end_date: Optional[str] = None,
                                     recent_quarters: Optional[int] = None):
        """
        Chart 11: 분기별 매출/영업이익 흐름 차트 (막대 차트)
        날짜 범위 필터링 적용
        """
        if self.analyzer is None:
            print("  ✗ Analyzer가 없어 차트 생성 불가")
            return

        df = self.analyzer.df

        if 'revenue' not in df.columns or 'operating_income' not in df.columns:
            print("  ✗ revenue 또는 operating_income 컬럼이 없습니다.")
            return

        # 날짜 필터링 적용
        filtered_df = self._filter_dataframe_by_date(
            df,
            recent_years=recent_years,
            start_date=start_date,
            end_date=end_date,
            recent_quarters=recent_quarters
        )

        if filtered_df.empty:
            print("  ✗ 필터링 후 데이터가 없습니다.")
            return

        fig, ax = plt.subplots(figsize=(16, 8))

        # 데이터 준비
        dates = filtered_df.index
        revenue = filtered_df['revenue'].values
        oi = filtered_df['operating_income'].values

        # Bar chart
        x = np.arange(len(dates))
        width = 0.35

        bars1 = ax.bar(x - width / 2, revenue, width,
                       label='Revenue', color='#1E88E5', alpha=0.8)
        bars2 = ax.bar(x + width / 2, oi, width,
                       label='Operating Income', color='#FF6F00', alpha=0.8)

        # 값 표시 (막대 위)
        def add_value_labels(bars):
            for bar in bars:
                height = bar.get_height()
                if not np.isnan(height) and height != 0:
                    # 단위 자동 조정 (억 단위)
                    if abs(height) >= 1e8:
                        label_text = f'{height / 1e8:.1f}억'
                    elif abs(height) >= 1e4:
                        label_text = f'{height / 1e4:.0f}만'
                    else:
                        label_text = f'{height:.0f}'

                    ax.text(bar.get_x() + bar.get_width() / 2., height,
                            label_text, ha='center', va='bottom', fontsize=8)

        add_value_labels(bars1)
        add_value_labels(bars2)

        # 제목 및 라벨
        period_desc = ""
        if recent_years:
            period_desc = f" - Last {recent_years} Years"
        elif recent_quarters:
            period_desc = f" - Last {recent_quarters} Quarters"
        elif start_date or end_date:
            period_desc = f" - {start_date or 'Start'} to {end_date or 'Now'}"

        ax.set_title(f'{ticker} - Quarterly Revenue & Operating Income Flow{period_desc}',
                     fontsize=14, fontweight='bold', pad=20)
        ax.set_ylabel('Amount', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in dates],
                           rotation=45, ha='right', fontsize=9)
        ax.legend(fontsize=11, loc='upper left')
        ax.grid(True, alpha=0.3, axis='y')
        ax.axhline(y=0, color='black', linestyle='-', linewidth=0.8)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_11_quarterly_flow.png", dpi=300, bbox_inches='tight')
        plt.close()

    def _create_single_indicator_chart(self, ticker, save_dir, indicator: str,
                                       recent_years: Optional[int] = None,
                                       start_date: Optional[str] = None,
                                       end_date: Optional[str] = None,
                                       recent_quarters: Optional[int] = None):
        """
        Chart 12: 단일 재무항목 막대 차트

        Args:
            ticker: 티커
            save_dir: 저장 디렉토리
            indicator: 표시할 재무항목 (예: 'revenue', 'net_income', 'total_assets')
            recent_years: 최근 N년
            start_date: 시작일
            end_date: 종료일
            recent_quarters: 최근 N개 분기
        """
        if self.analyzer is None:
            print("  ✗ Analyzer가 없어 차트 생성 불가")
            return

        df = self.analyzer.df

        if indicator not in df.columns:
            print(f"  ✗ '{indicator}' 컬럼이 없습니다.")
            print(f"  사용 가능한 컬럼: {df.columns.tolist()}")
            return

        # 날짜 필터링 적용
        filtered_df = self._filter_dataframe_by_date(
            df,
            recent_years=recent_years,
            start_date=start_date,
            end_date=end_date,
            recent_quarters=recent_quarters
        )

        if filtered_df.empty:
            print("  ✗ 필터링 후 데이터가 없습니다.")
            return

        fig, ax = plt.subplots(figsize=(16, 8))

        # 데이터 준비
        dates = filtered_df.index
        values = filtered_df[indicator].values

        # Bar chart
        x = np.arange(len(dates))

        # 양수/음수에 따라 색상 다르게
        colors = ['#1E88E5' if v >= 0 else '#E53935' for v in values]

        bars = ax.bar(x, values, color=colors, alpha=0.8, width=0.7)

        # 값 표시 (막대 위/아래)
        for i, (bar, val) in enumerate(zip(bars, values)):
            if not np.isnan(val):
                height = bar.get_height()

                # 단위 자동 조정
                if abs(val) >= 1e8:
                    label_text = f'{val / 1e8:.1f}억'
                elif abs(val) >= 1e4:
                    label_text = f'{val / 1e4:.0f}만'
                else:
                    label_text = f'{val:.1f}'

                va = 'bottom' if height >= 0 else 'top'
                ax.text(bar.get_x() + bar.get_width() / 2., height,
                        label_text, ha='center', va=va, fontsize=8, fontweight='bold')

        # 제목 및 라벨
        period_desc = ""
        if recent_years:
            period_desc = f" - Last {recent_years} Years"
        elif recent_quarters:
            period_desc = f" - Last {recent_quarters} Quarters"
        elif start_date or end_date:
            period_desc = f" - {start_date or 'Start'} to {end_date or 'Now'}"

        # Indicator 이름 표시 (예쁘게)
        indicator_display = indicator.replace('_', ' ').title()

        ax.set_title(f'{ticker} - {indicator_display}{period_desc}',
                     fontsize=14, fontweight='bold', pad=20)
        ax.set_ylabel(indicator_display, fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in dates],
                           rotation=45, ha='right', fontsize=9)
        ax.grid(True, alpha=0.3, axis='y')
        ax.axhline(y=0, color='black', linestyle='-', linewidth=1)

        plt.tight_layout()

        # 파일명에 indicator 포함
        safe_indicator = indicator.replace('/', '_').replace(' ', '_')
        plt.savefig(f"{save_dir}/{ticker}_12_{safe_indicator}.png", dpi=300, bbox_inches='tight')
        plt.close()

    def generate_extended_report(self, ticker: str, company_name: str,
                                 save_dir: str = "./financial_reports",
                                 include_qoq: bool = False,
                                 revenue_indicator: str = 'prophet',
                                 valuation_indicator: str = 'prophet_valuation',
                                 recent_years: Optional[int] = None,
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None,
                                 recent_quarters: Optional[int] = None,
                                 single_indicator: Optional[str] = None):
        """
        확장 리포트 생성 (DB 예측 + 분석) - 날짜 범위 필터링 지원

        Args:
            ticker: 주식 티커
            company_name: 회사명
            save_dir: 저장 디렉토리
            include_qoq: QoQ 성장률 포함 여부
            revenue_indicator: 매출 예측 지표
            valuation_indicator: 밸류에이션 예측 지표
            recent_years: 최근 N년 데이터만 (우선순위 1)
            start_date: 시작 날짜 'YYYY-MM-DD' (우선순위 2)
            end_date: 종료 날짜 'YYYY-MM-DD' (우선순위 2)
            recent_quarters: 최근 N개 분기만 (우선순위 3)
            single_indicator: 단일 재무항목 (예: 'revenue', 'net_income')
        """
        import os

        print("\n" + "=" * 70)
        print(f"Extended Financial Report: {company_name} ({ticker})")
        print("=" * 70)

        # 1. DB에서 예측 데이터 로드
        print("\n[1/3] DB 예측 데이터 로드...")
        self.load_revenue_forecast(ticker, indicator=revenue_indicator)
        self.load_valuation_forecast(ticker, indicator=valuation_indicator)

        # 2. 개별 차트 생성 (날짜 범위 필터링 적용)
        print("\n[2/3] 개별 차트 생성...")
        ticker_dir = os.path.join(save_dir, ticker)
        os.makedirs(ticker_dir, exist_ok=True)

        self.create_individual_charts(
            save_dir=ticker_dir,
            include_qoq=include_qoq,
            recent_years=recent_years,
            start_date=start_date,
            end_date=end_date,
            recent_quarters=recent_quarters,
            single_indicator=single_indicator
        )

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
        print(f"  차트: 12개 (개별 PNG)")
        print(f"  QoQ 성장률: {'포함' if include_qoq else '미포함 (YoY만)'}")
        if single_indicator:
            print(f"  단일 재무항목: {single_indicator}")
        if recent_years:
            print(f"  날짜 범위: 최근 {recent_years}년")
        elif start_date or end_date:
            print(f"  날짜 범위: {start_date or '처음'} ~ {end_date or '끝'}")
        elif recent_quarters:
            print(f"  날짜 범위: 최근 {recent_quarters}개 분기")
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

    print("\n[사용 예제 2] 기존 분석 시스템과 통합 + 날짜 범위 필터링")
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

# 3. 확장 리포트 생성 (최근 3년 데이터만)
forecast.generate_extended_report(
    ticker='AAPL',
    company_name='Apple Inc.',
    save_dir='./financial_reports',
    include_qoq=False,
    revenue_indicator='prophet',
    valuation_indicator='prophet_valuation',
    recent_years=3  # 최근 3년 데이터만
)

# 또는 특정 날짜 범위 지정
forecast.generate_extended_report(
    ticker='AAPL',
    company_name='Apple Inc.',
    save_dir='./financial_reports',
    start_date='2020-01-01',
    end_date='2024-12-31'
)

# 또는 최근 20개 분기만
forecast.generate_extended_report(
    ticker='AAPL',
    company_name='Apple Inc.',
    save_dir='./financial_reports',
    recent_quarters=20
)
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
11. {ticker}_11_quarterly_flow.png          - 분기별 매출/영업이익 흐름 (NEW)
12. {ticker}_12_single_indicator.png        - 단일 재무항목 차트 (NEW)
    """)


if __name__ == "__main__":
    main()