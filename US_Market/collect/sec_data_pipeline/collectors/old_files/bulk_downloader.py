#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SEC Bulk Data Downloader
대량의 재무데이터를 효율적으로 다운로드
"""

import os
import json
import time
from typing import List, Dict, Optional, Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


class BulkDownloader:
    """SEC 데이터 대량 다운로드"""
    
    def __init__(self, sec_client, output_dir: str = "./sec_data", max_workers: int = 5):
        """
        Args:
            sec_client: SECAPIClient 인스턴스
            output_dir: 데이터 저장 디렉토리
            max_workers: 동시 다운로드 스레드 수
        """
        self.sec_client = sec_client
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers
        
        # 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.company_facts_dir = self.output_dir / "company_facts"
        self.company_facts_dir.mkdir(exist_ok=True)
        self.submissions_dir = self.output_dir / "submissions"
        self.submissions_dir.mkdir(exist_ok=True)
    
    def download_company_facts(self, ticker: str, save: bool = True) -> Optional[Dict]:
        """
        단일 기업의 Company Facts 다운로드
        
        Args:
            ticker: 주식 티커
            save: 파일로 저장 여부
            
        Returns:
            Company Facts 데이터
        """
        try:
            data = self.sec_client.get_company_facts_by_ticker(ticker)
            
            if data and save:
                filename = self.company_facts_dir / f"{ticker.upper()}.json"
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            
            return data
            
        except Exception as e:
            print(f"✗ Error downloading {ticker}: {e}")
            return None
    
    def download_company_facts_batch(self, tickers: List[str], 
                                     callback: Optional[Callable] = None) -> Dict[str, Dict]:
        """
        여러 기업의 Company Facts 배치 다운로드
        
        Args:
            tickers: 티커 리스트
            callback: 각 다운로드 완료 시 호출될 콜백 함수 (ticker, data, index, total)
            
        Returns:
            {ticker: data} 딕셔너리
        """
        results = {}
        total = len(tickers)
        
        print(f"\n다운로드 시작: {total}개 ticker")
        print(f"동시 다운로드: {self.max_workers}개 스레드")
        print("=" * 60)
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 모든 다운로드 작업 제출
            future_to_ticker = {
                executor.submit(self.download_company_facts, ticker): ticker 
                for ticker in tickers
            }
            
            # 완료된 작업 처리
            completed = 0
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                completed += 1
                
                try:
                    data = future.result()
                    if data:
                        results[ticker] = data
                        status = "✓"
                    else:
                        status = "✗"
                    
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    eta = (total - completed) / rate if rate > 0 else 0
                    
                    print(f"{status} [{completed:4d}/{total}] {ticker:6s} | "
                          f"Rate: {rate:5.2f}/s | ETA: {eta:6.1f}s")
                    
                    # 콜백 실행
                    if callback:
                        callback(ticker, data, completed, total)
                        
                except Exception as e:
                    print(f"✗ [{completed:4d}/{total}] {ticker:6s} - Error: {e}")
        
        elapsed = time.time() - start_time
        success_rate = len(results) / total * 100 if total > 0 else 0
        
        print("=" * 60)
        print(f"완료: {len(results)}/{total} ({success_rate:.1f}%)")
        print(f"소요 시간: {elapsed:.1f}s")
        print(f"평균 속도: {total/elapsed:.2f} ticker/s")
        
        return results
    
    def download_by_cik_range(self, start_cik: int, end_cik: int) -> Dict[str, Dict]:
        """
        CIK 범위로 데이터 다운로드
        
        Args:
            start_cik: 시작 CIK
            end_cik: 종료 CIK
            
        Returns:
            {ticker: data} 딕셔너리
        """
        # CIK 범위에 해당하는 ticker 찾기
        tickers_data = self.sec_client.get_company_tickers()
        if not tickers_data:
            print("✗ Failed to get company tickers")
            return {}
        
        # CIK 범위 필터링
        filtered_tickers = []
        for item in tickers_data.values():
            cik = int(item.get('cik_str', 0))
            if start_cik <= cik <= end_cik:
                filtered_tickers.append(item.get('ticker'))
        
        print(f"Found {len(filtered_tickers)} tickers in CIK range {start_cik}-{end_cik}")
        
        return self.download_company_facts_batch(filtered_tickers)
    
    def download_sp500_companies(self) -> Dict[str, Dict]:
        """
        S&P 500 기업들의 데이터 다운로드 (예시 - ticker 리스트 필요)
        
        Returns:
            {ticker: data} 딕셔너리
        """
        # S&P 500 ticker 리스트 (실제로는 외부 소스에서 가져와야 함)
        # 여기서는 샘플만 포함
        sp500_tickers = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B',
            'UNH', 'XOM', 'JNJ', 'JPM', 'V', 'PG', 'MA', 'HD', 'CVX', 'MRK',
            'ABBV', 'PEP', 'COST', 'AVGO', 'KO', 'WMT', 'MCD', 'CSCO', 'ACN'
        ]
        
        print(f"Downloading S&P 500 companies (sample: {len(sp500_tickers)} tickers)")
        return self.download_company_facts_batch(sp500_tickers)
    
    def download_from_file(self, ticker_file: str, 
                          ticker_column: str = 'ticker') -> Dict[str, Dict]:
        """
        파일에서 ticker 리스트를 읽어 다운로드
        
        Args:
            ticker_file: ticker가 포함된 CSV 파일 경로
            ticker_column: ticker 컬럼명
            
        Returns:
            {ticker: data} 딕셔너리
        """
        import pandas as pd
        
        try:
            df = pd.read_csv(ticker_file)
            if ticker_column not in df.columns:
                print(f"✗ Column '{ticker_column}' not found in {ticker_file}")
                return {}
            
            tickers = df[ticker_column].dropna().tolist()
            print(f"Loaded {len(tickers)} tickers from {ticker_file}")
            
            return self.download_company_facts_batch(tickers)
            
        except Exception as e:
            print(f"✗ Error reading ticker file: {e}")
            return {}
    
    def create_download_report(self, results: Dict[str, Dict], 
                               report_file: str = "download_report.json"):
        """
        다운로드 결과 리포트 생성
        
        Args:
            results: download_company_facts_batch 결과
            report_file: 리포트 파일명
        """
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_tickers': len(results),
            'tickers': list(results.keys()),
            'summary': {}
        }
        
        # 각 ticker별 요약 정보
        for ticker, data in results.items():
            if data:
                report['summary'][ticker] = {
                    'entity_name': data.get('entityName'),
                    'cik': data.get('cik'),
                    'has_us_gaap': 'us-gaap' in data.get('facts', {}),
                    'has_dei': 'dei' in data.get('facts', {}),
                }
        
        # 리포트 저장
        report_path = self.output_dir / report_file
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Download report saved to {report_path}")
    
    def resume_download(self, tickers: List[str]) -> Dict[str, Dict]:
        """
        중단된 다운로드 재개 (이미 다운로드된 파일은 건너뜀)
        
        Args:
            tickers: 다운로드할 ticker 리스트
            
        Returns:
            새로 다운로드된 {ticker: data} 딕셔너리
        """
        # 이미 다운로드된 ticker 확인
        existing_tickers = set()
        for file in self.company_facts_dir.glob("*.json"):
            existing_tickers.add(file.stem.upper())
        
        # 다운로드할 ticker 필터링
        remaining_tickers = [t for t in tickers if t.upper() not in existing_tickers]
        
        print(f"Already downloaded: {len(existing_tickers)} tickers")
        print(f"Remaining: {len(remaining_tickers)} tickers")
        
        if not remaining_tickers:
            print("✓ All tickers already downloaded")
            return {}
        
        return self.download_company_facts_batch(remaining_tickers)


def main():
    """테스트 실행"""
    from sec_api_client import SECAPIClient
    from rate_limiter import RateLimiter
    
    # 설정
    user_agent = "MyCompany Research admin@mycompany.com"
    rate_limiter = RateLimiter(max_calls=10, time_window=1.0)
    
    # 클라이언트 생성
    client = SECAPIClient(user_agent, rate_limiter)
    downloader = BulkDownloader(client, output_dir="./test_sec_data", max_workers=3)
    
    # 테스트 ticker 리스트
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
    
    print("\n테스트: 5개 ticker 다운로드")
    print("=" * 60)
    
    # 콜백 함수 정의
    def on_complete(ticker, data, index, total):
        if data:
            entity_name = data.get('entityName', 'Unknown')
            print(f"  → {ticker}: {entity_name}")
    
    # 다운로드 실행
    results = downloader.download_company_facts_batch(test_tickers, callback=on_complete)
    
    # 리포트 생성
    if results:
        downloader.create_download_report(results, "test_download_report.json")


if __name__ == "__main__":
    main()
