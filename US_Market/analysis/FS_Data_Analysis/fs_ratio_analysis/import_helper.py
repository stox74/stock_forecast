#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Import Helper - 다양한 환경에서 모듈 자동 import

노트북, 데스크탑, 서버 등 다양한 환경에서
financial_data_integrator 모듈을 자동으로 찾아서 import합니다.

사용법:
    from import_helper import notebook_setup
    notebook_setup()

    # 이제 어디서든 import 가능
    from financial_data_integrator import integrate_financial_ratios
"""

import sys
import os
from pathlib import Path
from typing import List, Optional


# ==========================================
# 방법 1: __file__ 기반 자동 경로 설정 (권장)
# ==========================================
def setup_project_path(file_path: Optional[str] = None,
                       target_module: str = "financial_data_integrator.py",
                       max_levels: int = 5,
                       verbose: bool = True) -> Optional[str]:
    """
    현재 파일 위치에서 시작해서 상위 디렉토리로 올라가며
    target_module을 찾아서 해당 경로를 sys.path에 추가

    Args:
        file_path: 시작 파일 경로 (__file__ 또는 None)
        target_module: 찾을 모듈 파일명
        max_levels: 최대 상위 디렉토리 탐색 깊이
        verbose: 진행 상황 출력 여부

    Returns:
        찾은 프로젝트 루트 경로 또는 None
    """
    # file_path가 없으면 현재 작업 디렉토리 사용
    if file_path is None:
        current_dir = os.getcwd()
        if verbose:
            print(f"[INFO] 시작 디렉토리: {current_dir}")
    else:
        current_dir = os.path.dirname(os.path.abspath(file_path))
        if verbose:
            print(f"[INFO] 시작 디렉토리: {current_dir}")

    # 상위 디렉토리로 올라가며 target_module 찾기
    search_dir = current_dir

    for level in range(max_levels + 1):
        # 현재 디렉토리에서 target_module 찾기
        target_path = os.path.join(search_dir, target_module)

        if os.path.exists(target_path):
            if search_dir not in sys.path:
                sys.path.insert(0, search_dir)
                if verbose:
                    print(f"[SUCCESS] 모듈 발견: {target_path}")
                    print(f"[SUCCESS] 경로 추가: {search_dir}")
            else:
                if verbose:
                    print(f"[INFO] 모듈 발견: {target_path}")
                    print(f"[INFO] 경로 이미 존재: {search_dir}")
            return search_dir

        # 하위 디렉토리도 검색 (1단계만, 안전하게)
        if level == 0:
            try:
                for item in os.listdir(search_dir):
                    item_path = os.path.join(search_dir, item)
                    if os.path.isdir(item_path):
                        target_in_subdir = os.path.join(item_path, target_module)
                        if os.path.exists(target_in_subdir):
                            if item_path not in sys.path:
                                sys.path.insert(0, item_path)
                                if verbose:
                                    print(f"[SUCCESS] 모듈 발견: {target_in_subdir}")
                                    print(f"[SUCCESS] 경로 추가: {item_path}")
                            return item_path
            except (PermissionError, OSError):
                # 접근 권한 없는 디렉토리는 건너뛰기
                pass

        # 상위 디렉토리로 이동
        parent_dir = os.path.dirname(search_dir)

        # 루트 디렉토리에 도달하면 중지
        if parent_dir == search_dir:
            break

        search_dir = parent_dir
        if verbose and level < max_levels:
            print(f"[INFO] 상위 디렉토리 탐색 중... ({level + 1}/{max_levels}): {search_dir}")

    if verbose:
        print(f"[WARNING] {target_module}를 찾을 수 없습니다.")

    return None


# ==========================================
# 방법 2: 다중 모듈 자동 설정
# ==========================================
def setup_multiple_modules(file_path: Optional[str] = None,
                           modules: Optional[List[str]] = None,
                           max_levels: int = 5,
                           verbose: bool = True) -> dict:
    """
    여러 모듈을 한 번에 찾아서 경로 설정
    """
    if modules is None:
        modules = [
            'financial_data_integrator.py',
            'financial_analysis_system.py',
        ]

    results = {}

    if verbose:
        print("=" * 70)
        print("다중 모듈 경로 설정")
        print("=" * 70)

    for module in modules:
        if verbose:
            print(f"\n[{module}] 탐색 중...")

        found_path = setup_project_path(
            file_path=file_path,
            target_module=module,
            max_levels=max_levels,
            verbose=False
        )

        if found_path:
            results[module] = found_path
            if verbose:
                print(f"  ✓ 발견: {found_path}")
        else:
            if verbose:
                print(f"  ✗ 찾을 수 없음")

    if verbose:
        print("\n" + "=" * 70)
        print(f"완료: {len(results)}/{len(modules)} 모듈 발견")
        print("=" * 70)

    return results


# ==========================================
# 방법 3: 기존 방식 (하위 호환성, 단순화)
# ==========================================
def find_module_path(module_name: str, search_paths: Optional[List[str]] = None) -> Optional[str]:
    """
    모듈 경로 자동 탐색 (기존 방식, 단순화)
    """
    module_file = f"{module_name}.py"

    # 검색할 기본 경로들
    default_search_paths = [
        os.getcwd(),
        str(Path.home() / "projects"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Desktop"),
    ]

    # __file__이 있으면 추가
    try:
        if '__file__' in globals():
            default_search_paths.insert(0, os.path.dirname(os.path.abspath(__file__)))
    except:
        pass

    if search_paths:
        default_search_paths = search_paths + default_search_paths

    # 환경 변수에서 추가 경로 읽기
    env_path = os.environ.get('FINANCIAL_ANALYSIS_PATH')
    if env_path and os.path.exists(env_path):
        default_search_paths.insert(0, env_path)

    for base_path in default_search_paths:
        if not os.path.exists(base_path):
            continue

        # 현재 디렉토리에서 직접 찾기
        module_path = os.path.join(base_path, module_file)
        if os.path.exists(module_path):
            return base_path

        # 하위 디렉토리 3단계까지 탐색 (안전하게)
        try:
            for root, dirs, files in os.walk(base_path):
                # 깊이 제한
                depth = root[len(base_path):].count(os.sep)
                if depth > 3:
                    dirs[:] = []  # 더 깊이 들어가지 않도록
                    continue

                if module_file in files:
                    return root
        except (PermissionError, OSError):
            # 접근 권한 문제는 무시
            continue

    return None


# ==========================================
# 통합 자동 설정 함수
# ==========================================
def auto_setup(file_path: Optional[str] = None,
               modules: Optional[List[str]] = None,
               max_levels: int = 5,
               verbose: bool = True) -> bool:
    """
    자동으로 모든 모듈 경로 설정 (권장)
    """
    if verbose:
        print("\n" + "=" * 70)
        print("Financial Analysis - 자동 경로 설정")
        print("=" * 70)

    # 다중 모듈 설정
    results = setup_multiple_modules(
        file_path=file_path,
        modules=modules,
        max_levels=max_levels,
        verbose=verbose
    )

    # Import 검증
    if verbose:
        print("\n" + "=" * 70)
        print("모듈 Import 검증")
        print("=" * 70)

        all_success = True
        test_modules = ['financial_data_integrator', 'financial_analysis_system']

        for module_name in test_modules:
            try:
                __import__(module_name)
                print(f"✓ {module_name}: Import 가능")
            except ImportError as e:
                print(f"✗ {module_name}: Import 실패 - {str(e)}")
                all_success = False

        print("=" * 70)

        if all_success:
            print("\n✓ 모든 모듈 준비 완료!")
        else:
            print("\n✗ 일부 모듈을 찾을 수 없습니다.")
            print("\n해결 방법:")
            print("1. 모듈 파일(.py)이 올바른 위치에 있는지 확인")
            print("2. 환경 변수 설정: set FINANCIAL_ANALYSIS_PATH=실제경로")

        return all_success

    return len(results) > 0


# ==========================================
# 간편 사용 함수들
# ==========================================
def quick_setup(file_path: Optional[str] = None) -> bool:
    """빠른 설정 (상세 출력)"""
    return auto_setup(file_path=file_path, verbose=True)


def silent_setup(file_path: Optional[str] = None) -> bool:
    """조용한 설정 (출력 없음)"""
    return auto_setup(file_path=file_path, verbose=False)


def notebook_setup() -> bool:
    """
    노트북 전용 설정

    Examples:
        # Jupyter Notebook 첫 번째 셀에서
        from import_helper import notebook_setup
        notebook_setup()
    """
    print("\n📓 Jupyter Notebook 모드")
    print("현재 작업 디렉토리:", os.getcwd())
    return auto_setup(file_path=None, verbose=True)


# ==========================================
# 검증 함수
# ==========================================
def verify_imports() -> dict:
    """주요 모듈들이 import 가능한지 확인"""
    modules_to_check = [
        'financial_data_integrator',
        'financial_analysis_system',
    ]

    results = {}

    print("\n" + "=" * 70)
    print("모듈 Import 가능 여부 확인")
    print("=" * 70)

    for module_name in modules_to_check:
        try:
            __import__(module_name)
            results[module_name] = True
            print(f"✓ {module_name}: Import 가능")
        except ImportError as e:
            results[module_name] = False
            print(f"✗ {module_name}: Import 실패 - {str(e)}")

    print("=" * 70)

    return results


# ==========================================
# 환경 변수 관리
# ==========================================
def set_env_path(path: str, persistent: bool = False):
    """환경 변수 설정"""
    os.environ['FINANCIAL_ANALYSIS_PATH'] = path
    print(f"✓ 환경 변수 설정: FINANCIAL_ANALYSIS_PATH={path}")

    if persistent:
        if sys.platform == 'win32':
            os.system(f'setx FINANCIAL_ANALYSIS_PATH "{path}"')
            print("✓ 환경 변수 영구 저장 완료 (Windows)")
        else:
            print("ℹ Linux/Mac에서는 ~/.bashrc 또는 ~/.zshrc에 수동으로 추가하세요:")
            print(f'  export FINANCIAL_ANALYSIS_PATH="{path}"')


def get_env_path() -> Optional[str]:
    """환경 변수 확인"""
    path = os.environ.get('FINANCIAL_ANALYSIS_PATH')
    if path:
        print(f"✓ 환경 변수: FINANCIAL_ANALYSIS_PATH={path}")
    else:
        print("ℹ 환경 변수가 설정되지 않았습니다.")
    return path


# ==========================================
# 경로 정보 출력
# ==========================================
def show_paths():
    """현재 Python 경로 및 모듈 위치 출력"""
    print("\n" + "=" * 70)
    print("Python Path 정보")
    print("=" * 70)
    print(f"작업 디렉토리: {os.getcwd()}")
    print(f"환경 변수: {os.environ.get('FINANCIAL_ANALYSIS_PATH', '없음')}")

    print("\nsys.path 목록 (상위 10개):")
    for i, path in enumerate(sys.path[:10], 1):
        print(f"  {i}. {path}")

    if len(sys.path) > 10:
        print(f"  ... (외 {len(sys.path) - 10}개)")

    print("\n모듈 위치:")
    for module_name in ['financial_data_integrator', 'financial_analysis_system']:
        try:
            module = __import__(module_name)
            if hasattr(module, '__file__') and module.__file__:
                print(f"  {module_name}: {module.__file__}")
            else:
                print(f"  {module_name}: (내장 모듈)")
        except ImportError:
            print(f"  {module_name}: 찾을 수 없음")

    print("=" * 70)


# ==========================================
# 메인 실행
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("Import Helper - 사용 가이드")
    print("=" * 70)

    print("\n💡 권장 방법")
    print("-" * 70)
    print("""
# Jupyter Notebook에서
from import_helper import notebook_setup
notebook_setup()

# Python 스크립트에서
from import_helper import quick_setup
quick_setup(__file__)

# 이제 어디서든 import 가능
from financial_data_integrator import integrate_financial_ratios
from financial_analysis_system import FinancialAnalysisSystem
    """)

    print("\n" + "=" * 70)
    print("자동 탐색 시작...")
    print("=" * 70)

    # 실제 실행
    auto_setup(verbose=True)