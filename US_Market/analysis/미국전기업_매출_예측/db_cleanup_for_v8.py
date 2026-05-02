"""
====================================================================
  DB Cleanup — forecast 데이터 모두 삭제 + actual 중복 제거
====================================================================

호영님 제안대로 "기존 DB 싹 갈아엎고 깨끗하게 다시 시작" 하는 스크립트.

작업 내용:
  1. us_revenue_forecast_data 의 모든 forecast row 삭제
  2. us_revenue_forecast_data 의 actual 중복 row 정리 
     (같은 date 에 여러 forecast_date 로 저장된 것 → 가장 최신만 keep)
  3. 통계 출력

사용법:
  forecast 노트북 v8 의 새 cell 에 붙여넣기 + 실행

주의:
  · 백업이 자동 생성되지 않습니다.
  · DELETE 실행 전 확인 prompt 있음.
  · DCFModel 의 valuation 은 FMP API 직접 호출하므로 영향 없음.
"""

# ════════════════════════════════════════════════════════════════════
#  Step 0: 현재 상태 확인
# ════════════════════════════════════════════════════════════════════
from sqlalchemy import text

print("=" * 70)
print("  현재 us_revenue_forecast_data 상태")
print("=" * 70)

with engine.connect() as conn:
    # 전체 row 수
    total = conn.execute(text("SELECT COUNT(*) FROM us_revenue_forecast_data")).scalar()
    
    # data_type 별
    rows_by_type = conn.execute(text("""
        SELECT data_type, COUNT(*) AS n
        FROM us_revenue_forecast_data
        GROUP BY data_type
    """)).fetchall()
    
    # 고유 ticker 수
    n_tickers = conn.execute(text("""
        SELECT COUNT(DISTINCT ticker) FROM us_revenue_forecast_data
    """)).scalar()

print(f"\n  총 row: {total:,}")
print(f"  고유 ticker: {n_tickers:,}")
print(f"\n  data_type 별:")
for r in rows_by_type:
    print(f"    {r[0]:<12s}: {r[1]:>10,}")

# actual 중복 확인 (같은 ticker, date 에 여러 row)
with engine.connect() as conn:
    actual_dup = conn.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT ticker, date, COUNT(*) AS n
            FROM us_revenue_forecast_data
            WHERE data_type = 'actual'
            GROUP BY ticker, date
            HAVING n > 1
        ) AS dup
    """)).scalar()

print(f"\n  actual 중복 (ticker, date 기준): {actual_dup:,}")

# ════════════════════════════════════════════════════════════════════
#  Step 1: 사용자 확인
# ════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("  실행할 작업")
print("=" * 70)
print()
print("  1. 모든 forecast row 삭제")
print("  2. actual 중복 정리 (같은 (ticker, date) 의 여러 row → 가장 최신 forecast_date 만 keep)")
print()
print("  ※ DCFModel 의 valuation 은 FMP API 직접 호출 — 영향 없음")
print("  ※ forecast 노트북에서 새로 forecast 만들면 됨")
print()

ans = input("  실행하시겠습니까? (y/N): ").strip().lower()
if ans != 'y':
    print("  → 취소됨")
else:
    # ════════════════════════════════════════════════════════════════
    #  Step 2: forecast 데이터 모두 삭제
    # ════════════════════════════════════════════════════════════════
    print()
    print("[Step 2] forecast 데이터 삭제 중...")
    
    with engine.begin() as conn:
        deleted_fc = conn.execute(text("""
            DELETE FROM us_revenue_forecast_data
            WHERE data_type = 'forecast'
        """))
    
    print(f"  ✓ {deleted_fc.rowcount:,} forecast rows 삭제")
    
    # ════════════════════════════════════════════════════════════════
    #  Step 3: actual 중복 제거
    # ════════════════════════════════════════════════════════════════
    print()
    print("[Step 3] actual 중복 정리 중...")
    
    # 같은 (ticker, date, data_type='actual') 중 forecast_date 가 가장 큰 것만 keep
    with engine.begin() as conn:
        deleted_dup = conn.execute(text("""
            DELETE t1 FROM us_revenue_forecast_data t1
            INNER JOIN us_revenue_forecast_data t2
            WHERE t1.ticker = t2.ticker
              AND t1.date = t2.date
              AND t1.data_type = 'actual'
              AND t2.data_type = 'actual'
              AND t1.forecast_date < t2.forecast_date
        """))
    
    print(f"  ✓ {deleted_dup.rowcount:,} actual 중복 rows 삭제")
    
    # ════════════════════════════════════════════════════════════════
    #  Step 4: 결과 확인
    # ════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("  Cleanup 후 상태")
    print("=" * 70)
    
    with engine.connect() as conn:
        new_total = conn.execute(text("SELECT COUNT(*) FROM us_revenue_forecast_data")).scalar()
        rows_by_type_new = conn.execute(text("""
            SELECT data_type, COUNT(*) AS n
            FROM us_revenue_forecast_data
            GROUP BY data_type
        """)).fetchall()
        actual_dup_new = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT ticker, date, COUNT(*) AS n
                FROM us_revenue_forecast_data
                WHERE data_type = 'actual'
                GROUP BY ticker, date
                HAVING n > 1
            ) AS dup
        """)).scalar()
    
    print(f"\n  총 row: {new_total:,}  (이전: {total:,}, 감소: {total - new_total:,})")
    print(f"\n  data_type 별:")
    for r in rows_by_type_new:
        print(f"    {r[0]:<12s}: {r[1]:>10,}")
    print(f"\n  actual 중복: {actual_dup_new:,}")
    
    print()
    print("=" * 70)
    print("  ✓ Cleanup 완료")
    print("=" * 70)
    print()
    print("  다음 단계:")
    print("  1. forecast 노트북 v8 의 Cell 7.5 (v8 patch) 실행")
    print("  2. Cell 13 에서 RUN_TICKERS = None, TICKER_START = 0, TICKER_END = None")
    print("  3. 전체 2,000 ticker re-forecast (40-60분)")
    print("  4. 검증")
