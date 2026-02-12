import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
from pathlib import Path
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False


class TrailValuationCalculator:
    """FMP API를 사용하여 Trail PER/PBR을 계산하는 클래스"""

    def __init__(self, api_key: str, output_folder: str = './trail_valuation_results'):
        self.api_key = api_key
        self.output_folder = Path(output_folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.base_url = "https://financialmodelingprep.com/api/v3"

    def _api_request(self, endpoint: str, params: dict = None) -> dict:
        """FMP API 요청을 수행하는 메서드"""
        if params is None:
            params = {}
        params['apikey'] = self.api_key

        url = f"{self.base_url}{endpoint}"
        response = requests.get(url, params=params)

        if response.status_code != 200:
            raise Exception(f"API 요청 실패: {response.status_code} - {response.text}")

        return response.json()

    def get_historical_prices(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """일별 주가 데이터를 가져오는 메서드"""
        print(f"\n{symbol} 주가 데이터 수집 중... ({start_date} ~ {end_date})")

        endpoint = f"/historical-price-full/{symbol}"
        params = {
            'from': start_date,
            'to': end_date
        }

        data = self._api_request(endpoint, params)

        if not data or 'historical' not in data:
            raise Exception(f"{symbol}의 주가 데이터를 찾을 수 없습니다.")

        df = pd.DataFrame(data['historical'])
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        print(f"  - {len(df)}개의 일별 데이터 수집 완료")
        return df[['date', 'close', 'volume']]

    def get_ttm_metrics(self, symbol: str) -> pd.DataFrame:
        """TTM 재무지표를 가져오는 메서드"""
        print(f"\n{symbol} TTM 재무지표 수집 중...")

        endpoint = f"/key-metrics-ttm/{symbol}"
        params = {}

        data = self._api_request(endpoint, params)

        if not data:
            raise Exception(f"{symbol}의 TTM 재무지표를 찾을 수 없습니다.")

        df = pd.DataFrame(data)
        print(f"  - TTM 재무지표 수집 완료")
        return df

    def get_quarterly_metrics(self, symbol: str, limit: int = 40) -> pd.DataFrame:
        """분기별 재무지표를 가져오는 메서드 (TTM 데이터 히스토리)"""
        print(f"\n{symbol} 분기별 재무지표 수집 중...")

        endpoint = f"/key-metrics/{symbol}"
        params = {
            'period': 'quarter',
            'limit': limit
        }

        data = self._api_request(endpoint, params)

        if not data:
            raise Exception(f"{symbol}의 분기별 재무지표를 찾을 수 없습니다.")

        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        print(f"  - {len(df)}개의 분기 데이터 수집 완료")
        return df

    def get_quarterly_financials(self, symbol: str, limit: int = 40) -> pd.DataFrame:
        """분기별 재무제표를 가져오는 메서드 - 손익계산서와 재무상태표 병합"""
        print(f"\n{symbol} 분기별 재무제표 수집 중...")

        endpoint_is = f"/income-statement/{symbol}"
        params = {
            'period': 'quarter',
            'limit': limit
        }

        data_is = self._api_request(endpoint_is, params)

        if not data_is:
            raise Exception(f"{symbol}의 분기별 손익계산서를 찾을 수 없습니다.")

        df_is = pd.DataFrame(data_is)
        df_is['date'] = pd.to_datetime(df_is['date'])
        df_is = df_is.sort_values('date').reset_index(drop=True)

        print(f"  - 손익계산서: {len(df_is)}개 수집")

        endpoint_bs = f"/balance-sheet-statement/{symbol}"
        data_bs = self._api_request(endpoint_bs, params)

        if data_bs:
            df_bs = pd.DataFrame(data_bs)
            df_bs['date'] = pd.to_datetime(df_bs['date'])

            merge_cols = ['date', 'totalStockholdersEquity']
            if 'totalEquity' in df_bs.columns:
                merge_cols.append('totalEquity')

            df = df_is.merge(df_bs[merge_cols], on='date', how='left')
            print(f"  - 재무상태표: {len(df_bs)}개 수집 및 병합 완료")
        else:
            df = df_is
            print(f"  - 경고: 재무상태표 데이터 없음")

        print(f"  - 최종 {len(df)}개의 분기 재무제표 준비 완료")
        print(f"  - 주요 컬럼 확인: {[col for col in df.columns if 'share' in col.lower() or 'equity' in col.lower()]}")

        return df

    def calculate_trailing_metrics(self, price_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Trail PER, PBR을 계산하는 메서드"""
        print(f"\n{symbol} Trail PER/PBR 계산 중...")

        try:
            quarterly_financials = self.get_quarterly_financials(symbol, limit=60)
            print(f"  - 분기별 재무제표 컬럼: {quarterly_financials.columns.tolist()[:10]}...")
        except Exception as e:
            print(f"  경고: 재무제표 수집 실패 - {e}")
            return self._return_empty_metrics(price_df)

        result_df = price_df.copy()
        result_df['eps_ttm'] = np.nan
        result_df['book_value_per_share'] = np.nan
        result_df['revenue_ttm'] = np.nan
        result_df['net_income_ttm'] = np.nan
        result_df['stockholders_equity'] = np.nan
        result_df['shares_outstanding'] = np.nan
        result_df['trail_per'] = np.nan
        result_df['trail_pbr'] = np.nan
        result_df['trail_psr'] = np.nan

        if len(quarterly_financials) == 0:
            print("  경고: 재무제표 데이터가 비어있습니다.")
            return result_df

        for idx, row in result_df.iterrows():
            price_date = row['date']

            available_quarters = quarterly_financials[quarterly_financials['date'] <= price_date]

            if len(available_quarters) >= 4:
                last_4q = available_quarters.tail(4)

                ttm_net_income = last_4q['netIncome'].sum()
                ttm_revenue = last_4q['revenue'].sum()

                latest_quarter = available_quarters.iloc[-1]

                stockholders_equity = latest_quarter.get('totalStockholdersEquity',
                                                         latest_quarter.get('totalEquity', np.nan))

                shares_outstanding = latest_quarter.get('weightedAverageShsOutDil',
                                                        latest_quarter.get('weightedAverageShsOut', np.nan))

                if pd.notna(shares_outstanding) and shares_outstanding > 0:
                    eps_ttm = ttm_net_income / shares_outstanding
                    bps = stockholders_equity / shares_outstanding if pd.notna(stockholders_equity) else np.nan
                    rps = ttm_revenue / shares_outstanding

                    result_df.at[idx, 'eps_ttm'] = eps_ttm
                    result_df.at[idx, 'book_value_per_share'] = bps
                    result_df.at[idx, 'revenue_ttm'] = ttm_revenue
                    result_df.at[idx, 'net_income_ttm'] = ttm_net_income
                    result_df.at[idx, 'stockholders_equity'] = stockholders_equity
                    result_df.at[idx, 'shares_outstanding'] = shares_outstanding

                    if eps_ttm > 0:
                        result_df.at[idx, 'trail_per'] = row['close'] / eps_ttm
                    elif eps_ttm < 0:
                        result_df.at[idx, 'trail_per'] = np.nan

                    if pd.notna(bps) and bps > 0:
                        result_df.at[idx, 'trail_pbr'] = row['close'] / bps

                    if rps > 0:
                        result_df.at[idx, 'trail_psr'] = row['close'] / rps
                else:
                    print(f"  경고: {price_date.strftime('%Y-%m-%d')} - 발행주식수 데이터 없음")

        valid_data = result_df.dropna(subset=['trail_per'])
        print(f"  - {len(valid_data)}개의 유효한 Trail PER 계산 완료")

        valid_pbr = result_df.dropna(subset=['trail_pbr'])
        print(f"  - {len(valid_pbr)}개의 유효한 Trail PBR 계산 완료")

        return result_df

    def _return_empty_metrics(self, price_df: pd.DataFrame) -> pd.DataFrame:
        """에러 발생시 빈 메트릭 반환"""
        result_df = price_df.copy()
        for col in ['eps_ttm', 'book_value_per_share', 'revenue_ttm', 'net_income_ttm',
                    'stockholders_equity', 'shares_outstanding', 'trail_per', 'trail_pbr', 'trail_psr']:
            result_df[col] = np.nan
        return result_df

    def get_monthly_data(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """일별 데이터에서 월말 데이터를 추출하는 메서드"""
        df = daily_df.copy()
        df['year_month'] = df['date'].dt.to_period('M')

        monthly_df = df.groupby('year_month').last().reset_index()
        monthly_df['date'] = monthly_df['year_month'].dt.to_timestamp('M') + pd.offsets.MonthEnd(0)
        monthly_df = monthly_df.drop('year_month', axis=1)

        return monthly_df

    def visualize_metrics(self, df: pd.DataFrame, symbol: str, frequency: str = 'daily'):
        """Trail PER/PBR을 시각화하는 메서드"""
        print(f"\n시각화 생성 중... ({frequency})")

        valid_per = df.dropna(subset=['trail_per'])
        valid_pbr = df.dropna(subset=['trail_pbr'])
        valid_psr = df.dropna(subset=['trail_psr'])

        print(f"  - Trail PER 유효 데이터: {len(valid_per)}개")
        print(f"  - Trail PBR 유효 데이터: {len(valid_pbr)}개")
        print(f"  - Trail PSR 유효 데이터: {len(valid_psr)}개")

        if len(valid_per) == 0 and len(valid_pbr) == 0:
            print("경고: 유효한 밸류에이션 데이터가 없어 시각화를 생성할 수 없습니다.")
            print("      재무제표 데이터 또는 발행주식수 데이터를 확인해주세요.")
            return None

        per_mean = valid_per['trail_per'].mean() if len(valid_per) > 0 else np.nan
        pbr_mean = valid_pbr['trail_pbr'].mean() if len(valid_pbr) > 0 else np.nan
        psr_mean = valid_psr['trail_psr'].mean() if len(valid_psr) > 0 else np.nan

        fig, axes = plt.subplots(3, 1, figsize=(14, 10))
        fig.suptitle(f'{symbol} Trail Valuation Metrics ({frequency.capitalize()})',
                     fontsize=16, fontweight='bold')

        if len(valid_per) > 0:
            axes[0].plot(valid_per['date'], valid_per['trail_per'],
                         linewidth=2, color='#2E86AB', label='Trail PER')
            if pd.notna(per_mean):
                axes[0].axhline(y=per_mean, color='red', linestyle='--',
                                linewidth=1.5, alpha=0.7, label=f'Average: {per_mean:.2f}')
            axes[0].set_title('Trailing Price-to-Earnings Ratio', fontsize=13)
        else:
            axes[0].text(0.5, 0.5, 'No Trail PER Data Available',
                         ha='center', va='center', fontsize=14, color='red',
                         transform=axes[0].transAxes)
            axes[0].set_title('Trailing Price-to-Earnings Ratio (No Data)', fontsize=13)

        axes[0].set_ylabel('Trail PER', fontsize=12, fontweight='bold')
        axes[0].legend(loc='upper left')
        axes[0].grid(True, alpha=0.3)

        if len(valid_pbr) > 0:
            axes[1].plot(valid_pbr['date'], valid_pbr['trail_pbr'],
                         linewidth=2, color='#A23B72', label='Trail PBR')
            if pd.notna(pbr_mean):
                axes[1].axhline(y=pbr_mean, color='red', linestyle='--',
                                linewidth=1.5, alpha=0.7, label=f'Average: {pbr_mean:.2f}')
            axes[1].set_title('Trailing Price-to-Book Ratio', fontsize=13)
        else:
            axes[1].text(0.5, 0.5, 'No Trail PBR Data Available',
                         ha='center', va='center', fontsize=14, color='red',
                         transform=axes[1].transAxes)
            axes[1].set_title('Trailing Price-to-Book Ratio (No Data)', fontsize=13)

        axes[1].set_ylabel('Trail PBR', fontsize=12, fontweight='bold')
        axes[1].legend(loc='upper left')
        axes[1].grid(True, alpha=0.3)

        if len(valid_psr) > 0:
            axes[2].plot(valid_psr['date'], valid_psr['trail_psr'],
                         linewidth=2, color='#F18F01', label='Trail PSR')
            if pd.notna(psr_mean):
                axes[2].axhline(y=psr_mean, color='red', linestyle='--',
                                linewidth=1.5, alpha=0.7, label=f'Average: {psr_mean:.2f}')
            axes[2].set_title('Trailing Price-to-Sales Ratio', fontsize=13)
        else:
            axes[2].text(0.5, 0.5, 'No Trail PSR Data Available',
                         ha='center', va='center', fontsize=14, color='red',
                         transform=axes[2].transAxes)
            axes[2].set_title('Trailing Price-to-Sales Ratio (No Data)', fontsize=13)

        axes[2].set_ylabel('Trail PSR', fontsize=12, fontweight='bold')
        axes[2].legend(loc='upper left')
        axes[2].grid(True, alpha=0.3)

        for ax in axes:
            if len(valid_per) > 0 or len(valid_pbr) > 0 or len(valid_psr) > 0:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
                ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

        axes[-1].set_xlabel('Date', fontsize=12, fontweight='bold')

        plt.tight_layout()

        chart_filename = self.output_folder / f"{symbol}_trail_valuation_{frequency}.png"
        plt.savefig(chart_filename, dpi=300, bbox_inches='tight')
        print(f"  - 차트 저장: {chart_filename}")

        plt.show()

        return fig

    def save_to_excel(self, df: pd.DataFrame, symbol: str, frequency: str = 'daily'):
        """결과를 Excel 파일로 저장하는 메서드"""
        print(f"\nExcel 파일 저장 중... ({frequency})")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.output_folder / f"{symbol}_trail_valuation_{frequency}_{timestamp}.xlsx"

        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            display_df = df.copy()
            display_df['date'] = display_df['date'].dt.strftime('%Y-%m-%d')

            columns_order = [
                'date', 'close', 'volume',
                'trail_per', 'trail_pbr', 'trail_psr',
                'eps_ttm', 'book_value_per_share',
                'net_income_ttm', 'revenue_ttm', 'stockholders_equity', 'shares_outstanding'
            ]

            columns_order = [col for col in columns_order if col in display_df.columns]
            display_df = display_df[columns_order]

            display_df.to_excel(writer, sheet_name='Trail_Valuation', index=False)

            valid_df = df.dropna(subset=['trail_per', 'trail_pbr'])

            if len(valid_df) > 0:
                summary_data = {
                    '지표': ['Trail PER', 'Trail PBR', 'Trail PSR', '주가(Close)'],
                    '평균': [
                        valid_df['trail_per'].mean(),
                        valid_df['trail_pbr'].mean(),
                        valid_df['trail_psr'].mean() if 'trail_psr' in valid_df.columns else np.nan,
                        valid_df['close'].mean()
                    ],
                    '중앙값': [
                        valid_df['trail_per'].median(),
                        valid_df['trail_pbr'].median(),
                        valid_df['trail_psr'].median() if 'trail_psr' in valid_df.columns else np.nan,
                        valid_df['close'].median()
                    ],
                    '최소값': [
                        valid_df['trail_per'].min(),
                        valid_df['trail_pbr'].min(),
                        valid_df['trail_psr'].min() if 'trail_psr' in valid_df.columns else np.nan,
                        valid_df['close'].min()
                    ],
                    '최대값': [
                        valid_df['trail_per'].max(),
                        valid_df['trail_pbr'].max(),
                        valid_df['trail_psr'].max() if 'trail_psr' in valid_df.columns else np.nan,
                        valid_df['close'].max()
                    ],
                    '표준편차': [
                        valid_df['trail_per'].std(),
                        valid_df['trail_pbr'].std(),
                        valid_df['trail_psr'].std() if 'trail_psr' in valid_df.columns else np.nan,
                        valid_df['close'].std()
                    ]
                }

                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)

            info_data = {
                '항목': ['종목 코드', '데이터 주기', '시작일', '종료일', '데이터 개수', '생성일시'],
                '값': [
                    symbol,
                    frequency,
                    df['date'].min().strftime('%Y-%m-%d'),
                    df['date'].max().strftime('%Y-%m-%d'),
                    len(df),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ]
            }

            info_df = pd.DataFrame(info_data)
            info_df.to_excel(writer, sheet_name='Info', index=False)

            calc_info_data = {
                '계산식': [
                    'Trail PER = Close Price / EPS(TTM)',
                    'Trail PBR = Close Price / Book Value per Share',
                    'Trail PSR = Close Price / Revenue per Share(TTM)',
                    'EPS(TTM) = Net Income(TTM) / Shares Outstanding',
                    'Book Value per Share = Stockholders Equity / Shares Outstanding',
                    'Revenue per Share(TTM) = Revenue(TTM) / Shares Outstanding',
                    'TTM = Trailing Twelve Months (최근 4분기 합산)'
                ]
            }

            calc_df = pd.DataFrame(calc_info_data)
            calc_df.to_excel(writer, sheet_name='Calculation_Method', index=False)

        print(f"  - Excel 파일 저장: {filename}")
        return filename

    def analyze(self, symbol: str, start_date: str, end_date: str, frequency: str = 'daily'):
        """전체 분석 프로세스를 실행하는 메인 메서드"""
        print(f"\n{'=' * 70}")
        print(f"Trail Valuation 분석 시작")
        print(f"  종목: {symbol}")
        print(f"  기간: {start_date} ~ {end_date}")
        print(f"  주기: {frequency}")
        print(f"{'=' * 70}")

        price_df = self.get_historical_prices(symbol, start_date, end_date)

        if len(price_df) == 0:
            raise Exception("주가 데이터가 없습니다. 날짜 범위를 확인해주세요.")

        daily_result_df = self.calculate_trailing_metrics(price_df, symbol)

        if frequency == 'monthly':
            result_df = self.get_monthly_data(daily_result_df)
            print(f"\n월말 데이터 추출 완료: {len(result_df)}개")
        else:
            result_df = daily_result_df

        self.visualize_metrics(result_df, symbol, frequency)

        excel_file = self.save_to_excel(result_df, symbol, frequency)

        valid_per = result_df.dropna(subset=['trail_per'])
        valid_pbr = result_df.dropna(subset=['trail_pbr'])
        valid_psr = result_df.dropna(subset=['trail_psr'])

        print(f"\n{'=' * 70}")
        print(f"분석 완료 요약")
        print(f"{'=' * 70}")
        print(f"전체 데이터 수: {len(result_df)}")
        print(f"유효 Trail PER: {len(valid_per)}")
        print(f"유효 Trail PBR: {len(valid_pbr)}")
        print(f"유효 Trail PSR: {len(valid_psr)}")

        if len(valid_per) > 0:
            print(f"\n평균 Trail PER: {valid_per['trail_per'].mean():.2f}")
            print(f"  - 최소: {valid_per['trail_per'].min():.2f}")
            print(f"  - 최대: {valid_per['trail_per'].max():.2f}")
        else:
            print(f"\n경고: Trail PER 계산 불가")

        if len(valid_pbr) > 0:
            print(f"\n평균 Trail PBR: {valid_pbr['trail_pbr'].mean():.2f}")
            print(f"  - 최소: {valid_pbr['trail_pbr'].min():.2f}")
            print(f"  - 최대: {valid_pbr['trail_pbr'].max():.2f}")
        else:
            print(f"\n경고: Trail PBR 계산 불가")

        if len(valid_psr) > 0:
            print(f"\n평균 Trail PSR: {valid_psr['trail_psr'].mean():.2f}")
            print(f"  - 최소: {valid_psr['trail_psr'].min():.2f}")
            print(f"  - 최대: {valid_psr['trail_psr'].max():.2f}")
        else:
            print(f"\n경고: Trail PSR 계산 불가")

        if len(valid_per) == 0 and len(valid_pbr) == 0:
            print(f"\n{'!' * 70}")
            print("데이터 부족 원인 진단:")
            print(f"{'!' * 70}")

            has_shares = result_df['shares_outstanding'].notna().sum()
            has_equity = result_df['stockholders_equity'].notna().sum()
            has_income = result_df['net_income_ttm'].notna().sum()
            has_revenue = result_df['revenue_ttm'].notna().sum()

            print(f"발행주식수 데이터: {has_shares}/{len(result_df)}")
            print(f"자본총계 데이터: {has_equity}/{len(result_df)}")
            print(f"순이익(TTM) 데이터: {has_income}/{len(result_df)}")
            print(f"매출(TTM) 데이터: {has_revenue}/{len(result_df)}")

            if has_shares == 0:
                print("\n→ 주요 원인: 발행주식수(shares outstanding) 데이터 없음")
                print("  재무제표에서 'weightedAverageShsOut' 또는 'weightedAverageShsOutDil' 필드를 확인하세요.")
            if has_income == 0:
                print("\n→ 주요 원인: 순이익(net income) 데이터 없음")
                print("  최소 4분기 재무제표가 필요합니다.")
            if has_equity == 0:
                print("\n→ 주요 원인: 자본총계(stockholders equity) 데이터 없음")

        print(f"\n결과 파일:")
        print(f"  - {excel_file}")
        print(f"{'=' * 70}\n")

        return result_df


if __name__ == "__main__":
    API_KEY = "YOUR_FMP_API_KEY_HERE"

    calculator = TrailValuationCalculator(
        api_key=API_KEY,
        output_folder='./trail_valuation_results'
    )

    symbol = "AAPL"
    start_date = "2023-01-01"
    end_date = "2024-12-31"

    print("\n1. 일별 데이터 분석")
    daily_df = calculator.analyze(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        frequency='daily'
    )

    print("\n" + "=" * 70 + "\n")

    print("2. 월별 데이터 분석")
    monthly_df = calculator.analyze(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        frequency='monthly'
    )