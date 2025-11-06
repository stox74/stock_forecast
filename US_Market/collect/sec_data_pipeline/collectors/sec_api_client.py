#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SEC EDGAR API 클라이언트
Company Facts API와 Submissions API를 사용하여 재무데이터 수집
"""

import requests
import json
import time
from typing import Dict, Optional, List
from datetime import datetime


class SECAPIClient:
    """SEC EDGAR API 클라이언트"""

    BASE_URL = "https://data.sec.gov"

    def main():
        """테스트 실행"""
        print("\n" + "=" * 60)
        print("SEC EDGAR API Client Test")
        print("=" * 60)

        # User-Agent 설정 - 이름/회사명 + 이메일 형식으로!
        user_agent = 'PersonalResearch stox1224@email.com'  # 이렇게 변경!

        print(f"\n⚠️  Important: Update user_agent with your email!")
        print(f"Current: {user_agent}\n")

        client = SECAPIClient(user_agent)
        # ... 나머지 코드

    def __init__(self, user_agent: str, rate_limiter=None):
        if not user_agent or '@' not in user_agent:
            raise ValueError("User-Agent must include your email address")

        self.user_agent = user_agent
        self.headers = {
            'User-Agent': user_agent,
            'Accept-Encoding': 'gzip, deflate'
        }
        self.rate_limiter = rate_limiter
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._tickers_cache = None

    def _make_request(self, url: str, max_retries: int = 3) -> Optional[Dict]:
        """
        API 요청 실행

        Args:
            url: 요청 URL
            max_retries: 최대 재시도 횟수

        Returns:
            JSON 응답 딕셔너리 또는 None
        """
        for attempt in range(max_retries):
            try:
                # Rate limiter 적용
                if self.rate_limiter:
                    self.rate_limiter.wait_if_needed()

                response = self.session.get(url, timeout=30)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    print(f"✗ URL not found (404): {url}")
                    return None
                elif response.status_code == 429:  # Too Many Requests
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"⚠ Rate limit hit, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                elif response.status_code == 403:  # Forbidden
                    print(f"✗ Access forbidden (403). Check your User-Agent: {self.user_agent}")
                    return None
                else:
                    print(f"✗ Request failed: {response.status_code} - {url}")
                    return None

            except requests.exceptions.Timeout:
                print(f"✗ Request timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(2)
            except requests.exceptions.ConnectionError as e:
                print(f"✗ Connection error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
            except requests.exceptions.RequestException as e:
                print(f"✗ Request error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)

        return None

    def get_company_tickers(self) -> Optional[Dict]:
        """
        모든 상장 기업의 ticker-CIK 매핑 조회

        Returns:
            ticker 정보 딕셔너리
        """
        # 캐시 확인
        if self._tickers_cache is not None:
            return self._tickers_cache

        # 올바른 URL 사용
        url = "https://www.sec.gov/files/company_tickers.json"  # 이렇게 변경!
        data = self._make_request(url)

        if data:
            self._tickers_cache = data

        return data

    def get_cik_by_ticker(self, ticker: str) -> Optional[str]:
        """
        Ticker로 CIK 조회

        Args:
            ticker: 주식 티커

        Returns:
            CIK 번호 또는 None
        """
        tickers_data = self.get_company_tickers()
        if not tickers_data:
            return None

        ticker_upper = ticker.upper()

        # 데이터 구조 확인 및 검색
        for key, item in tickers_data.items():
            if isinstance(item, dict) and item.get('ticker', '').upper() == ticker_upper:
                return str(item.get('cik_str'))

        return None

    def get_company_facts(self, cik: str) -> Optional[Dict]:
        """
        특정 기업의 Company Facts 데이터 조회

        Args:
            cik: CIK 번호 (10자리, 앞에 0 패딩)

        Returns:
            Company Facts JSON 데이터
        """
        # CIK를 10자리로 패딩
        cik_padded = str(cik).zfill(10)
        url = f"{self.BASE_URL}/api/xbrl/companyfacts/CIK{cik_padded}.json"

        return self._make_request(url)

    def get_company_facts_by_ticker(self, ticker: str) -> Optional[Dict]:
        """
        Ticker로 Company Facts 조회 (ticker->CIK 변환 포함)

        Args:
            ticker: 주식 티커 (예: AAPL, MSFT)

        Returns:
            Company Facts JSON 데이터
        """
        # CIK 조회
        cik = self.get_cik_by_ticker(ticker)

        if not cik:
            print(f"✗ Ticker not found: {ticker}")
            print(f"  Hint: Check if ticker is correct or try get_company_tickers() to see available tickers")
            return None

        print(f"  Found CIK for {ticker}: {cik}")
        return self.get_company_facts(cik)

    def get_submissions(self, cik: str) -> Optional[Dict]:
        """
        특정 기업의 제출 문서 메타데이터 조회

        Args:
            cik: CIK 번호

        Returns:
            Submissions JSON 데이터
        """
        cik_padded = str(cik).zfill(10)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"

        return self._make_request(url)

    def get_frames_data(self, taxonomy: str, tag: str, unit: str, year: int, quarter: str = None) -> Optional[Dict]:
        """
        특정 XBRL 태그의 프레임 데이터 조회 (모든 기업)

        Args:
            taxonomy: XBRL taxonomy (예: 'us-gaap')
            tag: XBRL 태그 (예: 'AccountsPayableCurrent')
            unit: 단위 (예: 'USD')
            year: 연도
            quarter: 분기 (선택사항, 예: 'Q1')

        Returns:
            Frame JSON 데이터
        """
        if quarter:
            frame = f"CY{year}{quarter}"
        else:
            frame = f"CY{year}"

        url = f"{self.BASE_URL}/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{frame}.json"

        return self._make_request(url)

    def save_to_file(self, data: Dict, filename: str):
        """
        데이터를 JSON 파일로 저장

        Args:
            data: 저장할 데이터
            filename: 파일명
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"✓ Saved to {filename}")
        except Exception as e:
            print(f"✗ Failed to save file: {e}")

    def test_connection(self) -> bool:
        """
        SEC API 연결 테스트

        Returns:
            연결 성공 여부
        """
        print(f"Testing connection to SEC EDGAR API...")
        print(f"User-Agent: {self.user_agent}")

        tickers = self.get_company_tickers()

        if tickers:
            print(f"✓ Connection successful! Found {len(tickers)} companies")
            return True
        else:
            print(f"✗ Connection failed")
            print(f"\nTroubleshooting:")
            print(f"1. Check your internet connection")
            print(f"2. Verify User-Agent includes email: '{self.user_agent}'")
            print(f"3. SEC might be blocking requests - try again later")
            print(f"4. Check if you're behind a firewall/proxy")
            return False


def main():
    """테스트 실행"""
    print("\n" + "=" * 60)
    print("SEC EDGAR API Client Test")
    print("=" * 60)

    # User-Agent 설정 (실제 사용 시 수정 필요)
    user_agent = 'PersonalResearch stox1224@email.com'

    print(f"\n⚠️  Important: Update user_agent with your email!")
    print(f"Current: {user_agent}\n")

    client = SECAPIClient(user_agent)

    # 1. 연결 테스트
    print("\n1. Connection Test")
    print("-" * 60)
    if not client.test_connection():
        print("\n✗ Cannot proceed without connection")
        return

    # 2. Company tickers 조회
    print("\n2. Getting company tickers...")
    print("-" * 60)
    tickers = client.get_company_tickers()
    if tickers:
        print(f"✓ Found {len(tickers)} companies")
        # 처음 5개만 출력
        for i, (key, value) in enumerate(list(tickers.items())[:5]):
            if isinstance(value, dict):
                print(
                    f"  {value.get('ticker', 'N/A')}: CIK {value.get('cik_str', 'N/A')} - {value.get('title', 'N/A')}")

    # 3. 특정 기업의 Company Facts 조회 (Apple)
    print("\n3. Getting Apple (AAPL) company facts...")
    print("-" * 60)
    aapl_facts = client.get_company_facts_by_ticker('AAPL')
    if aapl_facts:
        print(f"✓ Retrieved Apple company facts")
        print(f"  Entity: {aapl_facts.get('entityName')}")
        print(f"  CIK: {aapl_facts.get('cik')}")

        # 사용 가능한 facts 키 출력
        if 'facts' in aapl_facts:
            print(f"  Available taxonomies: {list(aapl_facts['facts'].keys())}")

            # US-GAAP facts 샘플 출력
            if 'us-gaap' in aapl_facts['facts']:
                us_gaap = aapl_facts['facts']['us-gaap']
                print(f"  US-GAAP tags count: {len(us_gaap)}")
                print(f"  Sample tags: {list(us_gaap.keys())[:5]}")

        # 파일로 저장
        client.save_to_file(aapl_facts, 'aapl_company_facts.json')

    # 4. Submissions 조회
    print("\n4. Getting Apple submissions...")
    print("-" * 60)
    aapl_submissions = client.get_submissions('320193')  # Apple CIK
    if aapl_submissions:
        print(f"✓ Retrieved Apple submissions")
        recent = aapl_submissions.get('filings', {}).get('recent', {})
        if recent:
            print(f"  Recent filings: {len(recent.get('accessionNumber', []))}")
            if recent.get('form'):
                print(f"  Latest form: {recent.get('form', [])[0]}")

    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()