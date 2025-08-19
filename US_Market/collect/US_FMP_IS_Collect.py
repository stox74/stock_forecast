import yfinance as yf
import pandas as pd
import time
import random
from datetime import datetime, timedelta
import warnings
import threading
from IPython.display import display, clear_output
import sys

warnings.filterwarnings('ignore')


class RealtimeStockCollector:
    def __init__(self, base_delay=2.0, retry_delay=5.0):
        self.failed_tickers = []
        self.successful_tickers = []
        self.current_ticker = ""
        self.total_tickers = 0
        self.processed_count = 0
        self.start_time = None
        self.base_delay = base_delay  # 기본 대기시간 (초)
        self.retry_delay = retry_delay  # 재시도 시 대기시간 (초)
        self.collection_log = []

    def print_progress_bar(self, current, total, success_count, bar_length=30):
        """실시간 진행 상황 바 출력"""
        progress = current / total
        filled_length = int(bar_length * progress)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)

        # 경과 시간 계산
        elapsed = time.time() - self.start_time if self.start_time else 0
        elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"

        # 예상 남은 시간 계산
        if current > 0:
            avg_time_per_ticker = elapsed / current
            remaining_time = avg_time_per_ticker * (total - current)
            remaining_str = f"{int(remaining_time // 60)}:{int(remaining_time % 60):02d}"
        else:
            remaining_str = "계산중..."

        # 성공률 계산
        success_rate = (success_count / current * 100) if current > 0 else 0

        # 진행상황 출력
        print(f"\r🔄 진행상황: [{bar}] {current}/{total} ({progress * 100:.1f}%)")
        print(f"✅ 성공: {success_count}개 | ❌ 실패: {current - success_count}개 | 성공률: {success_rate:.1f}%")
        print(f"⏱️  경과시간: {elapsed_str} | 예상 남은시간: {remaining_str}")
        print(f"📊 현재 처리중: {self.current_ticker}")
        print("-" * 60)

    def get_stock_data_with_retry(self, ticker, period="1y", max_retries=5):
        """여유로운 재시도 로직이 포함된 주식 데이터 수집"""

        self.current_ticker = ticker

        for attempt in range(max_retries):
            try:
                print(f"📡 {ticker} 데이터 수집 중... (시도 {attempt + 1}/{max_retries})")

                # 여유로운 대기 시간
                delay_time = self.base_delay + random.uniform(0.5, 1.5)
                if attempt > 0:  # 재시도인 경우 더 긴 대기
                    delay_time = self.retry_delay + random.uniform(1, 3)

                print(f"⏳ {delay_time:.1f}초 대기 중...")
                time.sleep(delay_time)

                stock = yf.Ticker(ticker)

                # 여러 방법으로 데이터 시도
                data = None
                collection_methods = [
                    ("기본 period", lambda: stock.history(period=period)),
                    ("날짜 범위 지정", lambda: stock.history(
                        start=datetime.now() - timedelta(days=365),
                        end=datetime.now()
                    )),
                    ("6개월 데이터", lambda: stock.history(period="6mo")),
                    ("3개월 데이터", lambda: stock.history(period="3mo")),
                    ("1개월 데이터", lambda: stock.history(period="1mo"))
                ]

                for method_name, method_func in collection_methods:
                    try:
                        print(f"  🔍 {method_name} 방식으로 시도...")
                        data = method_func()
                        if data is not None and not data.empty and len(data) > 5:
                            print(f"  ✅ {method_name} 방식 성공!")
                            break
                        else:
                            print(f"  ⚠️ {method_name} 방식: 데이터 부족")
                    except Exception as e:
                        print(f"  ❌ {method_name} 방식 실패: {str(e)[:50]}...")
                        continue

                # 데이터 검증 및 기본 정보 수집
                if data is not None and not data.empty and len(data) > 5:
                    print(f"  📊 데이터 검증 중...")

                    # 기본 정보 수집 (여러 번 시도)
                    info = {'longName': ticker, 'sector': 'Unknown', 'industry': 'Unknown'}
                    for info_attempt in range(3):
                        try:
                            print(f"  🏢 기업 정보 수집 중... (시도 {info_attempt + 1}/3)")
                            stock_info = stock.info
                            info = {
                                'longName': stock_info.get('longName', ticker),
                                'sector': stock_info.get('sector', 'Unknown'),
                                'industry': stock_info.get('industry', 'Unknown'),
                                'marketCap': stock_info.get('marketCap', None),
                                'currency': stock_info.get('currency', 'USD'),
                                'country': stock_info.get('country', 'Unknown')
                            }
                            print(f"  ✅ 기업 정보 수집 완료!")
                            break
                        except Exception as e:
                            print(f"  ⚠️ 기업 정보 수집 실패 (시도 {info_attempt + 1}): {str(e)[:30]}...")
                            if info_attempt < 2:
                                time.sleep(1)

                    # 수집 로그 기록
                    log_entry = {
                        'ticker': ticker,
                        'success': True,
                        'data_points': len(data),
                        'date_range': f"{data.index[0].date()} ~ {data.index[-1].date()}",
                        'attempts': attempt + 1,
                        'timestamp': datetime.now().strftime("%H:%M:%S")
                    }
                    self.collection_log.append(log_entry)

                    print(f"✅ {ticker} 수집 완료: {len(data)}일치 데이터")
                    return data, info

            except Exception as e:
                print(f"❌ {ticker} 시도 {attempt + 1} 실패: {str(e)}")
                if attempt < max_retries - 1:
                    retry_wait = self.retry_delay + random.uniform(2, 5)
                    print(f"⏳ {retry_wait:.1f}초 후 재시도...")
                    time.sleep(retry_wait)

        # 최종 실패 로그
        log_entry = {
            'ticker': ticker,
            'success': False,
            'attempts': max_retries,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        }
        self.collection_log.append(log_entry)

        print(f"💥 {ticker}: {max_retries}번 시도 후 최종 실패")
        return None, None

    def collect_multiple_stocks(self, tickers, period="1y"):
        """여유로운 시간을 두고 여러 주식 데이터 수집"""
        all_data = {}
        stock_info = {}

        self.total_tickers = len(tickers)
        self.start_time = time.time()

        print("🚀 " + "=" * 50)
        print(f"📈 주식 데이터 수집 시작!")
        print(f"📊 총 {len(tickers)}개 종목 수집 예정")
        print(f"⏱️  기본 대기시간: {self.base_delay}초")
        print(f"🔄 재시도 대기시간: {self.retry_delay}초")
        print("=" * 50 + "\n")

        for i, ticker in enumerate(tickers, 1):
            self.processed_count = i

            # 실시간 진행상황 업데이트
            if i > 1:  # 첫 번째가 아닐 때만 이전 출력 지우기
                # 이전 출력 지우기
                for _ in range(6):  # 진행바 관련 줄들 지우기
                    print("\033[F\033[K", end="")

            self.print_progress_bar(i - 1, len(tickers), len(self.successful_tickers))

            print(f"\n🎯 [{i}/{len(tickers)}] {ticker} 처리 시작...")
            print("-" * 40)

            data, info = self.get_stock_data_with_retry(ticker, period)

            if data is not None:
                all_data[ticker] = data
                stock_info[ticker] = info
                self.successful_tickers.append(ticker)
                print(f"🎉 {ticker} 수집 성공!\n")
            else:
                self.failed_tickers.append(ticker)
                print(f"😞 {ticker} 수집 실패...\n")

            # 중간 결과 리포트 (10개마다)
            if i % 10 == 0 or i == len(tickers):
                self.print_intermediate_report(i, len(tickers))

        # 최종 진행바 업데이트
        self.print_progress_bar(len(tickers), len(tickers), len(self.successful_tickers))

        return all_data, stock_info

    def print_intermediate_report(self, current, total):
        """중간 진행 상황 리포트"""
        success_rate = len(self.successful_tickers) / current * 100
        elapsed = time.time() - self.start_time

        print("\n" + "🔸" * 50)
        print(f"📊 중간 리포트 ({current}/{total})")
        print(f"✅ 성공: {len(self.successful_tickers)}개")
        print(f"❌ 실패: {len(self.failed_tickers)}개")
        print(f"📈 성공률: {success_rate:.1f}%")
        print(f"⏱️  경과시간: {int(elapsed // 60)}분 {int(elapsed % 60)}초")

        if len(self.successful_tickers) >= 5:
            print(f"🎯 최근 성공: {', '.join(self.successful_tickers[-5:])}")

        if len(self.failed_tickers) > 0:
            print(f"💔 실패 종목: {', '.join(self.failed_tickers)}")

        print("🔸" * 50 + "\n")

    def print_final_report(self):
        """최종 수집 결과 리포트"""
        total_time = time.time() - self.start_time if self.start_time else 0

        print("\n" + "🏁" * 50)
        print("🎊 데이터 수집 완료!")
        print("🏁" * 50)
        print(f"📊 총 처리 종목: {self.total_tickers}개")
        print(f"✅ 성공: {len(self.successful_tickers)}개")
        print(f"❌ 실패: {len(self.failed_tickers)}개")
        print(f"📈 최종 성공률: {len(self.successful_tickers) / self.total_tickers * 100:.1f}%")
        print(f"⏱️  총 소요시간: {int(total_time // 60)}분 {int(total_time % 60)}초")
        print(f"⚡ 평균 처리시간: {total_time / self.total_tickers:.1f}초/종목")

        if self.successful_tickers:
            print(f"\n🎯 성공한 종목들:")
            for i, ticker in enumerate(self.successful_tickers):
                if i % 10 == 0:
                    print()
                print(f"{ticker}", end="  ")
            print()

        if self.failed_tickers:
            print(f"\n💔 실패한 종목들: {', '.join(self.failed_tickers)}")

        print("🏁" * 50)


# 사용 예시
def main():
    # 대표적인 종목들 리스트 (더 많은 종목 포함)
    major_tickers = [
        # 기술 대기업
        'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'TSLA', 'META', 'NVDA', 'NFLX', 'CRM',
        # 전통 기업들
        'JNJ', 'PG', 'WMT', 'JPM', 'BAC', 'V', 'MA', 'UNH', 'HD', 'PFE',
        # 에너지/원자재
        'XOM', 'CVX', 'CAT', 'DE', 'NEM', 'FCX',
        # ETF들
        'SPY', 'QQQ', 'VTI', 'VEA', 'VWO', 'BND',
        # 기타 대표 종목
        'BRK-B', 'ABBV', 'LLY', 'AVGO', 'ORCL', 'ACN'
    ]

    # 여유로운 설정으로 수집기 생성
    collector = RealtimeStockCollector(
        base_delay=3.0,  # 기본 3초 대기
        retry_delay=8.0  # 재시도 시 8초 대기
    )

    print("🚀 실시간 모니터링 주식 데이터 수집기 시작!")
    stock_data, stock_info = collector.collect_multiple_stocks(major_tickers, period="1y")

    # 최종 리포트 출력
    collector.print_final_report()

    # 수집된 데이터 요약
    if stock_data:
        print(f"\n📈 수집 데이터 요약 (상위 5개 종목):")
        print("-" * 80)
        for ticker in list(stock_data.keys())[:5]:
            data = stock_data[ticker]
            info = stock_info.get(ticker, {})
            current_price = data['Close'].iloc[-1]
            start_price = data['Close'].iloc[0]
            change_pct = (current_price - start_price) / start_price * 100

            print(f"📊 {ticker} ({info.get('longName', ticker)[:30]})")
            print(f"   💰 현재가: ${current_price:.2f} ({change_pct:+.1f}%)")
            print(f"   📅 데이터: {data.index[0].date()} ~ {data.index[-1].date()} ({len(data)}일)")
            print(f"   🏢 섹터: {info.get('sector', 'Unknown')}")
            print()

    return stock_data, stock_info, collector


# 개별 종목 상세 분석 함수 (여유로운 버전)
def analyze_single_stock_detailed(ticker, period="1y"):
    """개별 종목에 대한 상세하고 여유로운 분석"""
    collector = RealtimeStockCollector(base_delay=2.0, retry_delay=5.0)

    print(f"🔍 {ticker} 종목 상세 분석 시작...")
    print("=" * 50)

    data, info = collector.get_stock_data_with_retry(ticker, period)

    if data is None:
        print(f"😞 {ticker} 데이터 수집에 실패했습니다.")
        return None

    # 상세 분석
    print(f"\n📊 {ticker} 분석 결과")
    print("-" * 40)
    print(f"🏢 회사명: {info.get('longName', ticker)}")
    print(f"🏭 섹터: {info.get('sector', 'Unknown')}")
    print(f"🌍 국가: {info.get('country', 'Unknown')}")
    print(f"💵 통화: {info.get('currency', 'USD')}")
    print(f"📅 데이터 기간: {data.index[0].date()} ~ {data.index[-1].date()}")
    print(f"📈 총 거래일: {len(data)}일")

    # 가격 분석
    current_price = data['Close'].iloc[-1]
    start_price = data['Close'].iloc[0]
    change_pct = (current_price - start_price) / start_price * 100
    max_price = data['High'].max()
    min_price = data['Low'].min()
    avg_volume = data['Volume'].mean()

    print(f"\n💰 가격 정보:")
    print(f"   현재 가격: ${current_price:.2f}")
    print(f"   기간 수익률: {change_pct:+.2f}%")
    print(f"   최고가: ${max_price:.2f}")
    print(f"   최저가: ${min_price:.2f}")
    print(f"   평균 거래량: {avg_volume:,.0f}")

    return data, info


if __name__ == "__main__":
    # 전체 수집 실행
    print("🎯 대용량 주식 데이터 수집을 시작합니다!")
    print("⏰ 시간이 오래 걸릴 수 있으니 잠시만 기다려 주세요...")

    all_data, all_info, collector = main()

    # 개별 종목 상세 분석 예시
    if 'TSLA' in all_data:
        print("\n" + "=" * 60)
        print("🚗 Tesla 상세 분석")
        analyze_single_stock_detailed("TSLA", "1y")