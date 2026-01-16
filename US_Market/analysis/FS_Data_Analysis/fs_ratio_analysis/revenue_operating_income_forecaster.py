#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Revenue & Operating Income Forecaster
매출 예측 + 영업이익 회귀 분석 + 종합 시각화

기능:
1. DB에서 매출 예측 데이터 로드
2. Historical 매출-영업이익 회귀 분석
3. 예측된 매출 기반 영업이익 예측
4. 4가지 차트 생성:
   - Revenue vs Operating Income 회귀 분석 차트
   - 향후 예측 차트 (Revenue + Op. Income Forecast)
   - Historical + Forecast 통합 차트 (최근 2년 + 예측)
   - 연간 매출/영업이익 흐름 차트

사용법:
    from revenue_operating_income_forecaster import RevenueOIForecaster

    forecaster = RevenueOIForecaster(db_config, analyzer)
    forecaster.load_revenue_forecast(ticker="PLTR", indicator='prophet')
    forecaster.create_all_charts(ticker="PLTR", save_dir="./charts")
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
plt.rcParams['font.size'] = 10


class RevenueOIForecaster:
    """
    매출 예측 기반 영업이익 예측 및 시각화 시스템
    """

    def __init__(self, db_config: Dict, analyzer=None):
        """
        Args:
            db_config: DB 연결 정보
            analyzer: FinancialAnalysisSystem 인스턴스 (옵션)
        """
        self.db_config = db_config
        self.analyzer = analyzer
        self.engine = None
        self.revenue_forecast = None
        self.ticker = None
        self.regression_model = None

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

    def load_revenue_forecast(self, ticker: str,
                              indicator: str = 'revenue_billions_esq_forecast',
                              forecast_date: Optional[str] = None,
                              last_n_quarters: int = 5) -> pd.DataFrame:
        """
        DB에서 매출 예측 데이터 로드 (호영님 코드 스타일)

        Args:
            ticker: 주식 티커
            indicator: 예측 지표명 (예: 'revenue_billions_esq_forecast')
            forecast_date: 예측 기준일 (None이면 최신)
            last_n_quarters: 마지막 N개 분기만 사용 (기본값: 5)

        Returns:
            매출 예측 DataFrame
        """
        self.ticker = ticker

        # 최신 forecast_date 조회
        if forecast_date is None:
            date_query = """
                         SELECT MAX(forecast_date) as max_date
                         FROM us_revenue_forecast_result
                         WHERE ticker = :ticker AND indicator = : indicator
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
                SELECT date, ticker, indicator, value, forecast_date
                FROM us_revenue_forecast_result
                WHERE ticker = :ticker
                  AND indicator = : indicator
                  AND forecast_date = :forecast_date
                ORDER BY date
                """

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(query), conn,
                                 params={
                                     'ticker': ticker,
                                     'indicator': indicator,
                                     'forecast_date': forecast_date
                                 })

            # 날짜 변환
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')

            # 마지막 N개 분기만 사용
            fc_last = df.tail(last_n_quarters).copy()

            # 단위 변환: billions -> 1e8 단위 (회귀 모델에 맞춤)
            fc_last['revenue_billions'] = fc_last['value'].astype(float)
            fc_last['revenue_in_1e8'] = fc_last['revenue_billions'] * 10.0  # 1B = 10 * 1e8

            self.revenue_forecast = fc_last

            print(f"✓ 매출 예측 데이터 로드 완료: {len(fc_last)}개 분기")
            print(
                f"  기간: {fc_last['date'].iloc[0].strftime('%Y-%m-%d')} ~ {fc_last['date'].iloc[-1].strftime('%Y-%m-%d')}")
            print(f"  단위 변환: Billions → 1e8 (회귀 모델 호환)")

            return fc_last

        except Exception as e:
            print(f"✗ 매출 예측 데이터 로드 실패: {e}")
            return None

    def build_regression_model(self) -> Dict:
        """
        Revenue vs Operating Income 회귀 모델 구축

        Returns:
            회귀 모델 정보 딕셔너리
        """
        if self.analyzer is None:
            print("✗ Analyzer가 없어 회귀 모델 구축 불가")
            return None

        # Analyzer의 모델이 있으면 사용
        if 'revenue_oi_model' in self.analyzer.results and self.analyzer.results['revenue_oi_model']:
            self.regression_model = self.analyzer.results['revenue_oi_model']
            print(f"✓ 기존 회귀 모델 사용")
            return self.regression_model

        # 없으면 직접 구축
        df = self.analyzer.df

        if 'revenue' not in df.columns or 'operating_income' not in df.columns:
            print("✗ revenue 또는 operating_income 컬럼이 없습니다.")
            return None

        # 유효한 데이터만 사용
        valid_data = df[['revenue', 'operating_income']].dropna()

        if len(valid_data) < 4:
            print("✗ 회귀 분석에 충분한 데이터가 없습니다.")
            return None

        X = valid_data['revenue'].values
        y = valid_data['operating_income'].values

        # 선형 회귀
        from sklearn.linear_model import LinearRegression
        model = LinearRegression()
        model.fit(X.reshape(-1, 1), y)

        slope = model.coef_[0]
        intercept = model.intercept_
        y_pred = model.predict(X.reshape(-1, 1))
        r2 = model.score(X.reshape(-1, 1), y)

        self.regression_model = {
            'X': X,
            'y': y,
            'y_pred': y_pred,
            'slope': slope,
            'intercept': intercept,
            'r2': r2
        }

        print(f"✓ 회귀 모델 구축 완료")
        print(f"  회귀식: OI = {slope:.4f} × Revenue + {intercept:.2f}")
        print(f"  R² = {r2:.4f}")

        return self.regression_model

    def predict_operating_income(self) -> Optional[pd.DataFrame]:
        """
        매출 예측 기반 영업이익 예측 (호영님 코드 스타일)

        Returns:
            예측 DataFrame with columns:
            - date
            - ticker
            - revenue_forecast_billions
            - revenue_forecast_1e8
            - op_income_pred_1e8
            - op_income_pred_usd
            - op_income_pred_billions
        """
        if self.revenue_forecast is None:
            print("✗ 매출 예측 데이터가 없습니다.")
            return None

        if self.regression_model is None:
            self.build_regression_model()

        if self.regression_model is None:
            print("✗ 회귀 모델이 없어 영업이익 예측 불가")
            return None

        slope = self.regression_model['slope']
        intercept = self.regression_model['intercept']

        # revenue_in_1e8 컬럼 사용 (이미 단위 변환됨)
        X_future = self.revenue_forecast['revenue_in_1e8'].values.reshape(-1, 1)

        # 회귀 모델로 예측 (sklearn 사용)
        from sklearn.linear_model import LinearRegression
        model = LinearRegression()
        model.coef_ = [slope]
        model.intercept_ = intercept

        op_pred_1e8 = model.predict(X_future)

        # 결과 DataFrame 생성
        df_pred = pd.DataFrame({
            'date': self.revenue_forecast['date'].values,
            'ticker': self.revenue_forecast['ticker'].values,
            'revenue_forecast_billions': self.revenue_forecast['revenue_billions'].values,
            'revenue_forecast_1e8': self.revenue_forecast['revenue_in_1e8'].values,
            'op_income_pred_1e8': op_pred_1e8,
            'op_income_pred_usd': op_pred_1e8 * 100000000,  # USD 환산
            'op_income_pred_billions': (op_pred_1e8 * 100000000) / 1e9,  # Billions USD
        })

        df_pred['date'] = pd.to_datetime(df_pred['date'])
        df_pred = df_pred.sort_values('date')

        print(f"✓ 영업이익 예측 완료: {len(df_pred)}개 분기")
        print(f"  회귀식: OI = {slope:.4f} × Revenue + {intercept:.2f}")

        return df_pred

    def create_all_charts(self, ticker: str, save_dir: str = "./charts",
                          company_name: Optional[str] = None):
        """
        4가지 차트 생성

        Args:
            ticker: 주식 티커
            save_dir: 저장 디렉토리
            company_name: 회사명 (옵션)
        """
        import os
        os.makedirs(save_dir, exist_ok=True)

        print("\n" + "=" * 70)
        print(f"Revenue & Operating Income 차트 생성: {ticker}")
        print("=" * 70)

        # 1. Revenue vs OI Regression 차트
        print("\n[1/4] Revenue vs Operating Income Regression 차트...")
        self.create_regression_chart(ticker, save_dir, company_name)

        # 2. Forecast 차트 (예측만)
        print("[2/4] Revenue & Operating Income Forecast 차트...")
        self.create_forecast_only_chart(ticker, save_dir, company_name)

        # 3. Historical + Forecast 통합 차트
        print("[3/4] Historical + Forecast 통합 차트...")
        self.create_combined_chart(ticker, save_dir, company_name)

        # 4. 연간 흐름 차트
        print("[4/4] 연간 매출/영업이익 흐름 차트...")
        self.create_annual_flow_chart(ticker, save_dir, company_name)

        print("\n" + "=" * 70)
        print("✓ 모든 차트 생성 완료!")
        print(f"  저장 위치: {save_dir}/")
        print("=" * 70)

    def create_regression_chart(self, ticker: str, save_dir: str,
                                company_name: Optional[str] = None):
        """
        Chart 1: Revenue vs Operating Income Regression
        (이미지 1과 동일)
        """
        if self.regression_model is None:
            self.build_regression_model()

        if self.regression_model is None:
            print("  ✗ 회귀 모델이 없어 차트 생성 불가")
            return

        model = self.regression_model

        fig, ax = plt.subplots(figsize=(12, 8))

        # Scatter plot
        ax.scatter(model['X'], model['y'], alpha=0.6, s=100,
                   label='Actual Data', color='#64B5F6')

        # Regression line
        ax.plot(model['X'], model['y_pred'], 'r-', linewidth=3,
                label='Regression Line', color='#42A5F5')

        # 회귀식 표시
        slope = model['slope']
        intercept = model['intercept']
        r2 = model['r2']

        # 단위 자동 조정
        unit = 'e8'
        unit_label = '(unit: 1e8)'

        title = f'{ticker}: Revenue vs Operating Income Regression'
        if company_name:
            title = f'{ticker} ({company_name}): Revenue vs Operating Income Regression'

        equation_text = (
            f'Operating Income = {slope:.4f} × Revenue + {intercept:.2f}\n'
            f'R² = {r2:.4f}'
        )

        ax.text(0.05, 0.95, equation_text, transform=ax.transAxes,
                fontsize=11, verticalalignment='top', fontweight='bold',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.7))

        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.set_xlabel(f'Revenue {unit_label}', fontsize=12)
        ax.set_ylabel(f'Operating Income {unit_label}', fontsize=12)
        ax.legend(fontsize=11, loc='lower right')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_regression_analysis.png",
                    dpi=300, bbox_inches='tight')
        plt.close()

        print(f"  ✓ 저장: {ticker}_regression_analysis.png")

    def create_forecast_only_chart(self, ticker: str, save_dir: str,
                                   company_name: Optional[str] = None):
        """
        Chart 2: Revenue & Operating Income Forecast (예측만)
        호영님 코드 스타일: df_pred 사용, 막대 차트, 데이터 라벨
        """
        forecast_df = self.predict_operating_income()

        if forecast_df is None:
            print("  ✗ 예측 데이터가 없어 차트 생성 불가")
            return

        # 호영님 스타일: revenue_bn, op_income_bn 컬럼 사용
        forecast_data = forecast_df.copy()
        forecast_data['revenue_bn'] = forecast_data['revenue_forecast_billions']
        forecast_data['op_income_bn'] = forecast_data['op_income_pred_billions']

        fig, ax = plt.subplots(figsize=(14, 8))

        # Bar chart 설정
        bar_width = 0.35
        index = range(len(forecast_data))

        bars1 = ax.bar([i - bar_width / 2 for i in index], forecast_data['revenue_bn'],
                       bar_width, label='Revenue Forecast ($B)', color='#64B5F6', alpha=0.8)
        bars2 = ax.bar([i + bar_width / 2 for i in index], forecast_data['op_income_bn'],
                       bar_width, label='Op. Income Prediction ($B)', color='#FFB74D', alpha=0.8)

        # 막대 위에 수치 표시 (호영님 스타일)
        def add_labels(bars):
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f'{height:.2f}',
                            xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3),  # 3포인트 위로 띄움
                            textcoords="offset points",
                            ha='center', va='bottom', fontsize=10, fontweight='bold')

        add_labels(bars1)
        add_labels(bars2)

        title = f'{ticker} Revenue & Operating Income Forecast (2025-2026)'
        if company_name:
            title = f'{ticker} ({company_name}) Revenue & Operating Income Forecast (2025-2026)'

        ax.set_title(title, fontsize=15, fontweight='bold', pad=20)
        ax.set_ylabel('Amount (Billions of USD)', fontsize=12)
        ax.set_xticks(index)
        ax.set_xticklabels(forecast_data['date'].dt.strftime('%Y-%m-%d'), rotation=0)
        ax.legend(loc='upper left', fontsize=11)
        ax.grid(axis='y', linestyle='--', alpha=0.7)

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_forecast_only.png",
                    dpi=300, bbox_inches='tight')
        plt.close()

        print(f"  ✓ 저장: {ticker}_forecast_only.png")

    def create_combined_chart(self, ticker: str, save_dir: str,
                              company_name: Optional[str] = None,
                              historical_years: int = 2):
        """
        Chart 3: Historical + Forecast 통합 차트
        (이미지 3과 동일 - 최근 2년 + 예측)
        """
        if self.analyzer is None:
            print("  ✗ Analyzer가 없어 차트 생성 불가")
            return

        forecast_df = self.predict_operating_income()

        if forecast_df is None:
            print("  ✗ 예측 데이터가 없어 차트 생성 불가")
            return

        # Historical 데이터 (최근 N년)
        cutoff_date = datetime.now() - timedelta(days=365 * historical_years)
        hist_df = self.analyzer.df[self.analyzer.df.index >= cutoff_date]

        if 'revenue' not in hist_df.columns or 'operating_income' not in hist_df.columns:
            print("  ✗ Historical 데이터에 revenue 또는 operating_income이 없습니다.")
            return

        fig, ax = plt.subplots(figsize=(16, 8))

        # Historical data
        hist_dates = hist_df.index
        hist_revenue = hist_df['revenue'].values
        hist_oi = hist_df['operating_income'].values

        # Forecast data
        forecast_dates = forecast_df.index
        forecast_revenue = forecast_df['revenue_forecast'].values
        forecast_oi = forecast_df['operating_income_forecast'].values

        # Combine all dates
        all_dates = list(hist_dates) + list(forecast_dates)
        x_hist = np.arange(len(hist_dates))
        x_forecast = np.arange(len(hist_dates), len(hist_dates) + len(forecast_dates))

        width = 0.35

        # Historical bars
        bars1 = ax.bar(x_hist - width / 2, hist_revenue, width,
                       label='Revenue ($B)', color='#64B5F6', alpha=0.8)
        bars2 = ax.bar(x_hist + width / 2, hist_oi, width,
                       label='Operating Income ($B)', color='#FFB74D', alpha=0.8)

        # Forecast bars (lighter colors)
        bars3 = ax.bar(x_forecast - width / 2, forecast_revenue, width,
                       color='#64B5F6', alpha=0.4, hatch='//')
        bars4 = ax.bar(x_forecast + width / 2, forecast_oi, width,
                       color='#FFB74D', alpha=0.4, hatch='//')

        # 값 표시 (Historical만)
        for bar in list(bars1) + list(bars2):
            height = bar.get_height()
            if not np.isnan(height) and height != 0:
                ax.text(bar.get_x() + bar.get_width() / 2., height,
                        f'{height:.2f}', ha='center', va='bottom', fontsize=8)

        # Forecast 구분선
        if len(hist_dates) > 0:
            ax.axvline(x=len(hist_dates) - 0.5, color='red', linestyle='--',
                       linewidth=2, alpha=0.7, label='Forecast Start')

        title = f'{ticker} Revenue & Operating Income - Last {historical_years} Years'
        if company_name:
            title = f'{ticker} ({company_name}) Revenue & Operating Income - Last {historical_years} Years'

        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.set_ylabel('Amount (Billions of USD)', fontsize=12)
        ax.set_xticks(range(len(all_dates)))
        ax.set_xticklabels([d.strftime('%Y-%m-%d') for d in all_dates],
                           rotation=45, ha='right', fontsize=9)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_combined_historical_forecast.png",
                    dpi=300, bbox_inches='tight')
        plt.close()

        print(f"  ✓ 저장: {ticker}_combined_historical_forecast.png")

    def create_annual_flow_chart(self, ticker: str, save_dir: str,
                                 company_name: Optional[str] = None):
        """
        Chart 4: 연간 매출/영업이익 흐름 차트
        연도별로 집계하여 표시
        """
        if self.analyzer is None:
            print("  ✗ Analyzer가 없어 차트 생성 불가")
            return

        df = self.analyzer.df

        if 'revenue' not in df.columns or 'operating_income' not in df.columns:
            print("  ✗ revenue 또는 operating_income 컬럼이 없습니다.")
            return

        # 연도별 집계
        annual_df = df.copy()
        annual_df['year'] = annual_df.index.year
        annual_summary = annual_df.groupby('year').agg({
            'revenue': 'sum',
            'operating_income': 'sum'
        }).reset_index()

        # 예측 데이터 추가
        forecast_df = self.predict_operating_income()
        if forecast_df is not None:
            forecast_annual = forecast_df.copy()
            forecast_annual['year'] = forecast_annual.index.year
            forecast_summary = forecast_annual.groupby('year').agg({
                'revenue_forecast': 'sum',
                'operating_income_forecast': 'sum'
            }).reset_index()
            forecast_summary.rename(columns={
                'revenue_forecast': 'revenue',
                'operating_income_forecast': 'operating_income'
            }, inplace=True)
            forecast_summary['type'] = 'Forecast'
        else:
            forecast_summary = pd.DataFrame()

        annual_summary['type'] = 'Actual'

        # 통합
        if not forecast_summary.empty:
            combined = pd.concat([annual_summary, forecast_summary], ignore_index=True)
        else:
            combined = annual_summary

        fig, ax = plt.subplots(figsize=(16, 8))

        # Actual과 Forecast 분리
        actual = combined[combined['type'] == 'Actual']
        forecast = combined[combined['type'] == 'Forecast']

        width = 0.35

        # Actual bars
        if not actual.empty:
            x_actual = np.arange(len(actual))
            bars1 = ax.bar(x_actual - width / 2, actual['revenue'], width,
                           label='Revenue (Actual)', color='#1976D2', alpha=0.8)
            bars2 = ax.bar(x_actual + width / 2, actual['operating_income'], width,
                           label='Operating Income (Actual)', color='#F57C00', alpha=0.8)

            # 값 표시
            for bar in list(bars1) + list(bars2):
                height = bar.get_height()
                if not np.isnan(height) and height != 0:
                    ax.text(bar.get_x() + bar.get_width() / 2., height,
                            f'{height:.2f}B', ha='center', va='bottom', fontsize=9)

        # Forecast bars
        if not forecast.empty:
            x_forecast = np.arange(len(actual), len(actual) + len(forecast))
            bars3 = ax.bar(x_forecast - width / 2, forecast['revenue'], width,
                           label='Revenue (Forecast)', color='#1976D2', alpha=0.4, hatch='//')
            bars4 = ax.bar(x_forecast + width / 2, forecast['operating_income'], width,
                           label='Operating Income (Forecast)', color='#F57C00', alpha=0.4, hatch='//')

        # X축 라벨
        all_years = combined['year'].tolist()
        ax.set_xticks(range(len(all_years)))
        ax.set_xticklabels(all_years, fontsize=11)

        # Forecast 구분선
        if not actual.empty and not forecast.empty:
            ax.axvline(x=len(actual) - 0.5, color='red', linestyle='--',
                       linewidth=2, alpha=0.7, label='Forecast Start')

        title = f'{ticker} Annual Revenue & Operating Income Flow'
        if company_name:
            title = f'{ticker} ({company_name}) Annual Revenue & Operating Income Flow'

        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        ax.set_xlabel('Year', fontsize=12)
        ax.set_ylabel('Amount (Billions of USD)', fontsize=12)
        ax.legend(fontsize=11)
        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        plt.savefig(f"{save_dir}/{ticker}_annual_flow.png",
                    dpi=300, bbox_inches='tight')
        plt.close()

        print(f"  ✓ 저장: {ticker}_annual_flow.png")


def main():
    """사용 예제"""
    print("=" * 70)
    print("Revenue & Operating Income Forecaster - 사용 가이드")
    print("=" * 70)

    print("\n[사용 예제]")
    print("-" * 70)
    print("""
from revenue_operating_income_forecaster import RevenueOIForecaster
from financial_analysis_system import FinancialAnalysisSystem
from DATA.stock_invest_function import get_db_host

# DB 설정
db_config = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# 기존 Analyzer 사용
analyzer = FinancialAnalysisSystem(df_with_ratios)

# Forecaster 초기화
forecaster = RevenueOIForecaster(db_config, analyzer=analyzer)

# 매출 예측 로드
forecaster.load_revenue_forecast(ticker='PLTR', indicator='prophet')

# 4가지 차트 생성
forecaster.create_all_charts(
    ticker='PLTR',
    company_name='Palantir Inc.',
    save_dir='./revenue_oi_charts'
)

# 생성되는 차트:
# 1. PLTR_regression_analysis.png           - 회귀 분석
# 2. PLTR_forecast_only.png                 - 예측만
# 3. PLTR_combined_historical_forecast.png  - 최근 2년 + 예측
# 4. PLTR_annual_flow.png                   - 연도별 흐름
    """)


if __name__ == "__main__":
    main()