# -*- coding: utf-8 -*-
"""
메모리 관리 모듈
배치 처리 시 메모리 누수를 방지하고 효율적인 메모리 관리를 수행합니다.
"""

import gc
import psutil
import os
import sys
from contextlib import contextmanager
import traceback


class MemoryManager:
    """메모리 관리 클래스"""

    def __init__(self, warning_threshold_mb=8000, critical_threshold_mb=12000):
        """
        Args:
            warning_threshold_mb: 경고 임계값 (MB)
            critical_threshold_mb: 위험 임계값 (MB)
        """
        self.warning_threshold = warning_threshold_mb
        self.critical_threshold = critical_threshold_mb
        self.process = psutil.Process(os.getpid())
        self.initial_memory = self.get_memory_usage()

    def get_memory_usage(self):
        """현재 메모리 사용량 반환 (MB)"""
        return self.process.memory_info().rss / 1024 / 1024

    def get_memory_percent(self):
        """현재 메모리 사용률 반환 (%)"""
        return self.process.memory_percent()

    def force_garbage_collection(self, generations=2):
        """
        강제 가비지 컬렉션 수행

        Args:
            generations: GC 세대 (0: 신규, 1: 중간, 2: 오래된 객체)
        """
        collected = []
        for gen in range(generations + 1):
            n = gc.collect(gen)
            collected.append(n)
        return collected

    def clear_cache(self):
        """캐시 메모리 정리"""
        # NumPy 캐시 정리 (있는 경우)
        try:
            import numpy as np
            if hasattr(np, 'ndarray'):
                pass  # NumPy는 자동으로 관리됨
        except ImportError:
            pass

        # Pandas 캐시 정리
        try:
            import pandas as pd
            # 내부 캐시 정리는 자동으로 됨
        except ImportError:
            pass

        # Matplotlib 캐시 정리
        try:
            import matplotlib.pyplot as plt
            plt.close('all')
        except ImportError:
            pass

    def cleanup_batch(self, local_vars=None):
        """
        배치 처리 후 메모리 정리

        Args:
            local_vars: 삭제할 로컬 변수 딕셔너리 (예: locals())
        """
        before_memory = self.get_memory_usage()

        # 로컬 변수 정리
        if local_vars:
            vars_to_delete = []
            for var_name, var_value in local_vars.items():
                # 특정 타입의 변수만 삭제 (함수, 모듈 등 제외)
                if not var_name.startswith('_') and not callable(var_value):
                    if not isinstance(var_value, type):
                        vars_to_delete.append(var_name)

            # 실제 삭제는 호출자가 수행해야 함 (여기서는 리스트만 반환)

        # 캐시 정리
        self.clear_cache()

        # 가비지 컬렉션 수행 (모든 세대)
        collected = self.force_garbage_collection(generations=2)

        after_memory = self.get_memory_usage()
        freed_memory = before_memory - after_memory

        return {
            'before_mb': before_memory,
            'after_mb': after_memory,
            'freed_mb': freed_memory,
            'collected_objects': sum(collected)
        }

    def check_memory_status(self):
        """
        메모리 상태 확인 및 경고

        Returns:
            str: 'normal', 'warning', 'critical'
        """
        current_memory = self.get_memory_usage()

        if current_memory >= self.critical_threshold:
            return 'critical'
        elif current_memory >= self.warning_threshold:
            return 'warning'
        else:
            return 'normal'

    def print_memory_status(self, prefix=""):
        """메모리 상태 출력"""
        current = self.get_memory_usage()
        percent = self.get_memory_percent()
        status = self.check_memory_status()

        status_text = {
            'normal': 'OK',
            'warning': 'WARNING',
            'critical': 'CRITICAL'
        }

        print(f"{prefix}[MEMORY-{status_text[status]}] "
              f"{current:.2f} MB ({percent:.1f}%)")

        return current

    def emergency_cleanup(self):
        """긴급 메모리 정리 (critical 상태일 때)"""
        print("[긴급] 메모리 위험 수준 도달 - 강제 정리 시작")

        # 최대 3회 반복 정리
        for i in range(3):
            result = self.cleanup_batch()
            print(f"  정리 #{i + 1}: {result['freed_mb']:.2f} MB 해제, "
                  f"{result['collected_objects']}개 객체 수집")

            if self.check_memory_status() != 'critical':
                print("[긴급] 메모리 안정화 완료")
                return True

        print("[긴급] 메모리 정리 후에도 위험 수준 유지")
        return False


@contextmanager
def memory_context(manager, description="작업"):
    """
    메모리 추적 컨텍스트 매니저

    Usage:
        with memory_context(mem_manager, "HS Code 처리"):
            # 작업 수행
            pass
    """
    before = manager.get_memory_usage()
    print(f"[시작] {description} (메모리: {before:.2f} MB)")

    try:
        yield manager
    finally:
        after = manager.get_memory_usage()
        delta = after - before
        print(f"[완료] {description} (메모리: {after:.2f} MB, 변화: {delta:+.2f} MB)")


def cleanup_large_objects(*objects):
    """
    큰 객체들을 명시적으로 삭제하고 메모리 정리

    Args:
        *objects: 삭제할 객체들
    """
    for obj in objects:
        try:
            del obj
        except:
            pass

    gc.collect()


# 전역 메모리 매니저 인스턴스 (선택적 사용)
_global_memory_manager = None


def get_memory_manager(warning_threshold_mb=8000, critical_threshold_mb=12000):
    """전역 메모리 매니저 인스턴스 반환"""
    global _global_memory_manager
    if _global_memory_manager is None:
        _global_memory_manager = MemoryManager(warning_threshold_mb, critical_threshold_mb)
    return _global_memory_manager