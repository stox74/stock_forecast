#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Rate Limiter
SEC API는 초당 10 요청 제한이 있음
"""

import time
import threading
from collections import deque
from typing import Optional


class RateLimiter:
    """Rate limiter for API calls"""
    
    def __init__(self, max_calls: int = 10, time_window: float = 1.0):
        """
        Args:
            max_calls: 시간 윈도우 내 최대 호출 횟수
            time_window: 시간 윈도우 (초)
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """필요시 대기하여 rate limit 준수"""
        with self.lock:
            now = time.time()
            
            # 시간 윈도우를 벗어난 호출 기록 제거
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            # Rate limit 초과 시 대기
            if len(self.calls) >= self.max_calls:
                sleep_time = self.time_window - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    # 대기 후 다시 오래된 기록 제거
                    now = time.time()
                    while self.calls and self.calls[0] < now - self.time_window:
                        self.calls.popleft()
            
            # 현재 호출 기록
            self.calls.append(now)
    
    def get_remaining_calls(self) -> int:
        """현재 시간 윈도우에서 남은 호출 가능 횟수 반환"""
        with self.lock:
            now = time.time()
            
            # 오래된 기록 제거
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            return max(0, self.max_calls - len(self.calls))
    
    def reset(self):
        """호출 기록 초기화"""
        with self.lock:
            self.calls.clear()


class AdaptiveRateLimiter(RateLimiter):
    """
    적응형 Rate Limiter
    429 에러 발생 시 자동으로 rate를 낮춤
    """
    
    def __init__(self, max_calls: int = 10, time_window: float = 1.0, 
                 min_calls: int = 5, backoff_factor: float = 0.8):
        """
        Args:
            max_calls: 초기 최대 호출 횟수
            time_window: 시간 윈도우 (초)
            min_calls: 최소 호출 횟수 (이 이하로는 낮추지 않음)
            backoff_factor: Rate limit 초과 시 감소 비율
        """
        super().__init__(max_calls, time_window)
        self.initial_max_calls = max_calls
        self.min_calls = min_calls
        self.backoff_factor = backoff_factor
        self.last_429_time: Optional[float] = None
    
    def on_rate_limit_error(self):
        """
        429 에러 발생 시 호출
        Rate를 낮추고 복구 타이머 시작
        """
        with self.lock:
            self.last_429_time = time.time()
            new_max = max(self.min_calls, int(self.max_calls * self.backoff_factor))
            
            if new_max < self.max_calls:
                print(f"⚠ Rate limit hit! Reducing from {self.max_calls} to {new_max} calls per {self.time_window}s")
                self.max_calls = new_max
                self.calls.clear()  # 기록 초기화
    
    def try_recover(self, recovery_window: float = 300):
        """
        일정 시간 동안 429 에러가 없으면 rate를 증가
        
        Args:
            recovery_window: 복구를 시도하기 전 대기 시간 (초)
        """
        with self.lock:
            now = time.time()
            
            # 마지막 429 에러로부터 충분한 시간이 지났고, max_calls가 초기값보다 작으면
            if (self.last_429_time and 
                now - self.last_429_time > recovery_window and 
                self.max_calls < self.initial_max_calls):
                
                new_max = min(self.initial_max_calls, 
                             int(self.max_calls / self.backoff_factor))
                
                if new_max > self.max_calls:
                    print(f"✓ Recovering rate limit: {self.max_calls} -> {new_max} calls per {self.time_window}s")
                    self.max_calls = new_max


class BurstRateLimiter:
    """
    버스트를 허용하는 Rate Limiter
    짧은 시간에 여러 요청을 보낼 수 있지만, 장기적으로는 평균 rate를 유지
    """
    
    def __init__(self, rate: float = 10, burst: int = 20):
        """
        Args:
            rate: 초당 평균 요청 수
            burst: 한 번에 보낼 수 있는 최대 요청 수
        """
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_update = time.time()
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """토큰 버킷 알고리즘으로 rate limit 관리"""
        with self.lock:
            now = time.time()
            
            # 경과 시간에 따라 토큰 추가
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            # 토큰이 부족하면 대기
            if self.tokens < 1:
                sleep_time = (1 - self.tokens) / self.rate
                time.sleep(sleep_time)
                self.tokens = 0
            else:
                self.tokens -= 1


def test_rate_limiter():
    """Rate limiter 테스트"""
    print("Testing RateLimiter...")
    print("=" * 60)
    
    # 기본 Rate Limiter 테스트
    print("\n1. Basic RateLimiter (max 5 calls per 1 second)")
    limiter = RateLimiter(max_calls=5, time_window=1.0)
    
    start = time.time()
    for i in range(10):
        limiter.wait_if_needed()
        elapsed = time.time() - start
        remaining = limiter.get_remaining_calls()
        print(f"  Call {i+1:2d} at {elapsed:5.2f}s, remaining: {remaining}")
    
    # Adaptive Rate Limiter 테스트
    print("\n2. AdaptiveRateLimiter")
    adaptive = AdaptiveRateLimiter(max_calls=10, time_window=1.0)
    
    print(f"  Initial rate: {adaptive.max_calls} calls/s")
    
    # 429 에러 시뮬레이션
    adaptive.on_rate_limit_error()
    print(f"  After 429 error: {adaptive.max_calls} calls/s")
    
    adaptive.on_rate_limit_error()
    print(f"  After 2nd 429 error: {adaptive.max_calls} calls/s")
    
    # 복구 시도
    time.sleep(0.1)
    adaptive.last_429_time = time.time() - 301  # 5분 전으로 설정
    adaptive.try_recover()
    print(f"  After recovery: {adaptive.max_calls} calls/s")
    
    # Burst Rate Limiter 테스트
    print("\n3. BurstRateLimiter (rate=5/s, burst=10)")
    burst = BurstRateLimiter(rate=5, burst=10)
    
    start = time.time()
    for i in range(15):
        burst.wait_if_needed()
        elapsed = time.time() - start
        print(f"  Call {i+1:2d} at {elapsed:5.2f}s")
    
    print("\n✓ All tests completed")


if __name__ == "__main__":
    test_rate_limiter()
