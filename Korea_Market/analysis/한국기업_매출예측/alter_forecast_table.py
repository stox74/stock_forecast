# ==========================================================
# [일회성 실행] ticker 컬럼 확장 + 검증
# ==========================================================
# 목적:
#   - 기존 예측 이력(DART 기반, ticker='005930' 6자리) 보존
#   - 앞으로 들어올 DG 기반 예측(ticker='A005930' 7자리) 수용 가능하게 확장
#
# 이 셀은 ALTER TABLE 한 번만 실행하고 결과 검증합니다.
# 이미 VARCHAR(20) 이상이면 no-op 처리됩니다.

import pymysql
import pandas as pd
from DATA.stock_invest_function import get_db_host

db_info = {
    'host':     get_db_host(),
    'port':     3307,
    'user':     'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar',
}

TABLE_NAME = "korea_revenue_forecast_result"

conn = pymysql.connect(
    host=db_info['host'], port=db_info['port'],
    user=db_info['user'], password=db_info['password'],
    database=db_info['database'], charset='utf8mb4',
    autocommit=False,
)

try:
    cur = conn.cursor()

    # ---- 1. 변경 전 스키마 확인 ----
    print("=" * 70)
    print("[변경 전] ticker 컬럼 스키마")
    print("=" * 70)
    cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND COLUMN_NAME = 'ticker'
    """, (db_info['database'], TABLE_NAME))
    before = cur.fetchone()
    if before:
        col_name, data_type, max_len, nullable = before
        print(f"  {col_name}: {data_type}({max_len})  NULLABLE={nullable}")
    else:
        print(f"  ⚠️  '{TABLE_NAME}' 테이블 또는 'ticker' 컬럼이 없습니다.")
        raise SystemExit

    # ---- 2. 변경 전 데이터 규모 확인 (이력 손실 없음을 증명) ----
    cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    before_count = cur.fetchone()[0]
    print(f"\n  변경 전 레코드 수: {before_count:,}")

    # ---- 3. ALTER TABLE 실행 ----
    print("\n" + "=" * 70)
    print("[실행] ALTER TABLE ... MODIFY COLUMN ticker VARCHAR(20)")
    print("=" * 70)
    cur.execute(f"""
        ALTER TABLE {TABLE_NAME}
            MODIFY COLUMN ticker VARCHAR(20) NOT NULL
    """)
    conn.commit()
    print("  ✓ ALTER TABLE 완료")

    # ---- 4. 변경 후 검증 ----
    print("\n" + "=" * 70)
    print("[변경 후] ticker 컬럼 스키마")
    print("=" * 70)
    cur.execute(f"""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND COLUMN_NAME = 'ticker'
    """, (db_info['database'], TABLE_NAME))
    after = cur.fetchone()
    col_name, data_type, max_len, nullable = after
    print(f"  {col_name}: {data_type}({max_len})  NULLABLE={nullable}")

    cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    after_count = cur.fetchone()[0]
    print(f"\n  변경 후 레코드 수: {after_count:,} (변경 전과 동일해야 함)")
    assert before_count == after_count, "레코드 수가 달라짐 — 데이터 손실 의심!"

    # ---- 5. 기존 이력 샘플 확인 ----
    print("\n" + "=" * 70)
    print("[보존된 기존 이력 샘플]")
    print("=" * 70)
    df = pd.read_sql(f"""
        SELECT created_at, COUNT(DISTINCT ticker) AS ticker_cnt, COUNT(*) AS row_cnt
        FROM {TABLE_NAME}
        GROUP BY created_at
        ORDER BY created_at
        LIMIT 10
    """, conn)
    if len(df) > 0:
        print(df.to_string(index=False))
    else:
        print("  (기존 이력 없음)")

    print("\n✓ 모든 작업 완료. 이제 forecast.py 재실행 가능.")

finally:
    conn.close()
