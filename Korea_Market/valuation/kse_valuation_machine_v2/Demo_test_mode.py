# -*- coding: utf-8 -*-
"""
테스트 모드 데모 스크립트

배치 스크립트 자체를 수정하지 않고
Python에서 직접 호출하는 방법 시연
"""

from run_korea_psr_valuation_batch_v5_notebook_compatible import run_batch

# ==================== 사용 예시 ====================

print("=" * 80)
print("📚 테스트 모드 사용 예시")
print("=" * 80 + "\n")

# 예시 1: SK하이닉스, 삼성전자
print("예시 1: 반도체 2종목 테스트")
print("-" * 80)
print("run_batch(test_tickers=['000660', '005930'])\n")

# 예시 2: 카카오, 네이버
print("예시 2: IT 플랫폼 2종목 테스트")
print("-" * 80)
print("run_batch(test_tickers=['035720', '035420'])\n")

# 예시 3: 단일 종목
print("예시 3: 삼성전자만 테스트")
print("-" * 80)
print("run_batch(test_tickers=['005930'])\n")

# 예시 4: 여러 종목
print("예시 4: 4개 종목 테스트")
print("-" * 80)
print("run_batch(test_tickers=['000660', '005930', '035720', '035420'])\n")

print("=" * 80)
print("실제 실행하려면 아래 주석을 해제하세요")
print("=" * 80 + "\n")


# ==================== 실제 실행 ====================
# 아래 주석을 해제하고 실행하세요

# 예시 1 실행
# run_batch(test_tickers=['000660', '005930'])

# 예시 2 실행
# run_batch(test_tickers=['035720', '035420'])

# 예시 3 실행
# run_batch(test_tickers=['005930'])

# 예시 4 실행
# run_batch(test_tickers=['000660', '005930', '035720', '035420'])


# ==================== 고급 사용법 ====================

def test_with_custom_params():
    """
    커스텀 파라미터와 함께 테스트
    """
    from datetime import datetime

    run_batch(
        test_tickers=['000660', '005930'],
        forecast_date=datetime.now().strftime('%Y-%m-%d'),
        value_start_date='2024-01-01',
        table_name='Korea_company_valuation_ver2'
    )


# 고급 사용법 실행
# test_with_custom_params()


# ==================== 대화형 사용 ====================

if __name__ == "__main__":
    print("이 스크립트는 예시만 보여줍니다.")
    print("실제로 실행하려면 위의 주석을 해제하세요.\n")

    print("또는 대화형 Python에서:")
    print("-" * 80)
    print(">>> from run_korea_psr_valuation_batch_v5_notebook_compatible import run_batch")
    print(">>> run_batch(test_tickers=['000660', '005930'])")
    print("-" * 80)