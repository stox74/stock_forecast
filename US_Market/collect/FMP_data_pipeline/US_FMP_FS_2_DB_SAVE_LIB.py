"""
================================================================================
[2단계 / 보조 모듈] US_FMP_FS_2_DB_SAVE_LIB.py (V4)
================================================================================
⚠️  이 파일은 직접 실행하지 않습니다. (import 전용 라이브러리)
▶  실행은 US_FMP_FS_1_RUN_UPDATE.py 하나만 하면 됩니다. (수정 불필요 — 인터페이스 동일)

V3 대비 변경 사항 (더 단순화 — 삭제 로직도 제거)
============================================
V3는 "이번 배치와 겹치는 옛 행만 지우고 새로 넣는" 방식이었는데, 이마저도 제거했습니다.

V4는 이렇게만 합니다:
  - 새로 들어오는 데이터는 그냥 INSERT (추가)만 합니다
  - 기존 데이터는 절대 건드리지 않습니다 (삭제도, 갱신도 안 함)
  - 처음부터 깨끗하게 다시 쌓고 싶을 때만 별도로 아래 명령을 한 번 실행:

        python US_FMP_FS_2_DB_SAVE_LIB.py --truncate

    (US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP 세 테이블을 전부 비웁니다)

이러면 유니크 인덱스, 자기조인, 윈도우 함수, id 청크 분할, 임시테이블+RENAME 백업,
배치별 삭제 스캔까지 — 지금까지 문제를 일으켰던 모든 로직이 전부 사라집니다.
정기적으로 --truncate 한 번 하고 --quarters 60 으로 다시 채우는 방식으로 쓰시면
데이터도 항상 깨끗하고 코드도 가장 단순합니다.

저장 테이블: US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP
================================================================================
"""

from __future__ import annotations
import warnings
import time
import gc
import calendar
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.types import VARCHAR
from tqdm import tqdm

warnings.filterwarnings('ignore')

# ================================================================================
# DB 접속 정보 (기존 fallback 체인 유지)
# ================================================================================
try:
    from DATA.config import get_db_info
    db_info = get_db_info()
except ImportError:
    try:
        from config import get_db_info
        db_info = get_db_info()
    except ImportError:
        from DATA.stock_invest_function import get_db_host
        db_info = {
            'host': get_db_host(),
            'port': 3307,
            'user': 'stox7412',
            'password': 'Apt106503!~',
            'database': 'investar'
        }
        print("⚠️  Warning: Using fallback DB config")

port = int(db_info.get('port', 3307))

connection_string = (
    f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
    f"@{db_info['host']}:{port}/{db_info['database']}?charset=utf8mb4"
)

engine = create_engine(
    connection_string,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 10, "read_timeout": 300, "write_timeout": 300},
)

# 이제 "중복 방지"용 유니크 인덱스는 필요 없지만, ticker/date_month로 좁힌 DELETE와
# 이후 조회를 빠르게 하기 위한 일반(비유니크) 인덱스는 계속 유용하다.
KEY_COLUMNS = ['ticker', 'period', 'date_month', 'item']


def close_engine():
    """모든 배치 저장이 끝난 뒤 1단계 스크립트에서 마지막에 한 번 호출"""
    engine.dispose()
    print("✓ Database connection pool closed")


# ================================================================================
# 유틸리티 함수 (V2와 동일)
# ================================================================================

def clean_record_for_mysql(record):
    """MySQL에 안전하게 삽입할 수 있도록 레코드 정리"""
    cleaned = {}
    for key, value in record.items():
        if pd.isna(value) or value is None:
            cleaned[key] = None
        elif isinstance(value, (float, np.floating)):
            if np.isnan(value) or np.isinf(value):
                cleaned[key] = None
            else:
                cleaned[key] = float(value)
        else:
            cleaned[key] = value
    return cleaned


def table_exists(conn, table_name):
    inspector = inspect(conn)
    return table_name in inspector.get_table_names()


def filter_by_date_range(df, start_date=None, end_date=None):
    """DataFrame을 날짜 범위로 필터링 ('YYYY-MM-DD' 또는 'YYYY-MM' 지원)"""
    if df is None or df.empty:
        return df
    if 'date' not in df.columns:
        print("   ⚠️  Warning: 'date' column not found, skipping date filter")
        return df

    df_filtered = df.copy()
    df_filtered['date'] = pd.to_datetime(df_filtered['date'], errors='coerce')
    original_count = len(df_filtered)

    if start_date:
        try:
            if len(start_date) == 7:
                start_date = f"{start_date}-01"
            start_dt = pd.to_datetime(start_date)
            df_filtered = df_filtered[df_filtered['date'] >= start_dt]
            print(f"   📅 Start date filter: {start_date} "
                  f"(removed {original_count - len(df_filtered):,} rows)")
            original_count = len(df_filtered)
        except Exception as e:
            print(f"   ⚠️  Invalid start_date format: {start_date} - {str(e)}")

    if end_date:
        try:
            if len(end_date) == 7:
                year, month = map(int, end_date.split('-'))
                last_day = calendar.monthrange(year, month)[1]
                end_date = f"{end_date}-{last_day:02d}"
            end_dt = pd.to_datetime(end_date)
            df_filtered = df_filtered[df_filtered['date'] <= end_dt]
            print(f"   📅 End date filter: {end_date} "
                  f"(removed {original_count - len(df_filtered):,} rows)")
        except Exception as e:
            print(f"   ⚠️  Invalid end_date format: {end_date} - {str(e)}")

    return df_filtered


def create_table_if_not_exists(conn, df_sample, table_name, key_columns):
    """
    테이블이 없으면 생성 (V3: 유니크 인덱스는 안 만듦 — 더 이상 필요 없음)
    - 키 컬럼은 VARCHAR(191)로 강제
    - AUTO_INCREMENT PK(id) 추가
    - ticker, date_month에 일반 인덱스 추가 (DELETE/조회 속도용, 유니크 아님)
    """
    if not table_exists(conn, table_name):
        print(f"   ℹ️  Table {table_name} does not exist. Creating...")
        dtype_map = {c: VARCHAR(191) for c in key_columns if c in df_sample.columns}
        df_sample.head(0).to_sql(
            name=table_name, con=conn, if_exists='fail', index=False, dtype=dtype_map
        )
        try:
            conn.execute(text(
                f"ALTER TABLE `{table_name}` "
                f"ADD COLUMN `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
            ))
            conn.commit()
        except Exception as e:
            print(f"   ⚠️  PK creation warning: {str(e)[:150]}")

        try:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD INDEX `idx_{table_name}_ticker_month` (`ticker`(50), `date_month`(10))"))
            conn.commit()
        except Exception as e:
            print(f"   ⚠️  Index creation warning: {str(e)[:150]}")

        print(f"   ✓ Table {table_name} created")
        return True
    return False


def ensure_lookup_index(conn, table_name):
    """
    (V3, 가벼움) ticker+date_month 일반 인덱스가 없으면 추가.
    유니크 인덱스가 아니라서 중복 걱정 없이 그냥 생성 시도만 하면 된다 — 실패해도 무해함.

    ticker(50), date_month(10)로 길이 제한(prefix)을 걸어둔다. 이 테이블은 오래전부터
    있던 테이블이라 컬럼 타입이 TEXT/매우 넓은 VARCHAR일 수 있는데, 제한 없이 인덱스를
    걸면 "Specified key was too long"(MySQL 1071) 오류가 난다. ticker는 보통 10자
    이내, date_month는 'YYYY-MM' 7자 고정이라 이 정도 prefix로도 완전히 구분된다.
    """
    idx_name = f"idx_{table_name}_ticker_month"
    try:
        inspector = inspect(conn)
        indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
        if idx_name in indexes:
            return
        conn.execute(text(
            f"ALTER TABLE `{table_name}` ADD INDEX `{idx_name}` (`ticker`(50), `date_month`(10))"
        ))
        conn.commit()
        print(f"   ✓ Lookup index created: {idx_name}")
    except Exception as e:
        # 유니크 인덱스가 아니므로 실패해도 기능상 문제 없음 (조회/삭제가 조금 느려질 뿐)
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"   ℹ️  Lookup index skip: {str(e)[:120]}")


def _clean_object_columns(df, max_len=500):
    """object 컬럼 문자열 길이 제한 — NaN은 그대로 유지 ('nan' 문자열 오염 방지)"""
    for col in df.select_dtypes(include=['object']).columns:
        mask = df[col].notna()
        if mask.any():
            df.loc[mask, col] = df.loc[mask, col].astype(str).str[:max_len]
    return df


# ================================================================================
# [V4 신규] 전체 테이블 비우기 — 새로 처음부터 쌓고 싶을 때 한 번 실행
# ================================================================================

def truncate_all_tables():
    """
    US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP 세 테이블을 전부 비운다.
    (테이블 구조/인덱스는 그대로 두고 데이터만 삭제 — DROP 아님)

    사용법 (터미널에서 한 번만 실행):
        python US_FMP_FS_2_DB_SAVE_LIB.py --truncate

    이후 python US_FMP_FS_1_RUN_UPDATE.py --quarters 60 을 실행하면
    깨끗한 상태에서 새로 쌓인다.
    """
    tables = ["US_IS_from_FMP", "US_BS_from_FMP", "US_CF_from_FMP"]
    with engine.connect() as conn:
        for table_name in tables:
            if not table_exists(conn, table_name):
                print(f"   ℹ️  {table_name} 없음 (건너뜀)")
                continue
            try:
                conn.execute(text(f"TRUNCATE TABLE `{table_name}`"))
                conn.commit()
                print(f"   ✓ {table_name} 비움 완료")
            except Exception as e:
                print(f"   ⚠️  {table_name} TRUNCATE 실패: {str(e)[:200]}")
                try:
                    conn.rollback()
                except Exception:
                    pass
    print("\n전체 테이블 비우기 완료. 이제 python US_FMP_FS_1_RUN_UPDATE.py --quarters 60 을 실행하세요.")


# ================================================================================
# 단일 재무제표 저장 (IS/BS/CF 공통 로직) — V4: 단순 INSERT (삭제 없음)
# ================================================================================

def _save_one_statement(df, table_name, label,
                        start_date=None, end_date=None,
                        chunk_size=5000, mode='upsert', verbose=True):
    """
    하나의 재무제표 DataFrame을 저장 (V4: 삭제 없이 그냥 INSERT만 함)

    Returns
    -------
    str or None : 저장에 성공한 테이블명 (변경 없음/스킵이면 None)
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        if verbose:
            print(f"\n{label}: No data to save")
        return None

    if verbose:
        print(f"\nProcessing {label} data... (shape: {df.shape})")

    df_clean = df.copy()

    if start_date or end_date:
        df_clean = filter_by_date_range(df_clean, start_date, end_date)
        if df_clean is None or df_clean.empty:
            print(f"   ℹ️  No data in date range for {table_name} — skipped")
            return None

    df_clean = _clean_object_columns(df_clean)
    df_clean = df_clean.replace([float('inf'), float('-inf')], None)

    if 'date_month' not in df_clean.columns and 'date' in df_clean.columns:
        df_clean['date_month'] = (
            pd.to_datetime(df_clean['date']).dt.to_period('M').astype(str)
        )
    if 'period' not in df_clean.columns:
        print("   ⚠️  Warning: 'period' column missing, using empty string")
        df_clean['period'] = ''

    with engine.connect() as conn:
        create_table_if_not_exists(conn, df_clean, table_name, KEY_COLUMNS)
        ensure_lookup_index(conn, table_name)

        inspector = inspect(conn)
        table_columns = [c['name'] for c in inspector.get_columns(table_name)]
        insert_columns = [c for c in df_clean.columns if c in table_columns]
        missing = set(df_clean.columns) - set(insert_columns)
        if missing:
            print(f"   ⚠️  Columns not in table (dropped): {sorted(missing)}")
        df_clean = df_clean[insert_columns]

        # V4: 삭제 없이 그냥 추가만 함 (기존 정보는 그대로 두고 새 데이터만 INSERT)

    # 청크 단위 단순 INSERT (V4: 삭제 없이 그냥 추가)
    total_rows = len(df_clean)
    total_chunks = (total_rows + chunk_size - 1) // chunk_size

    pbar = tqdm(total=total_rows, desc=f"Saving {table_name}", unit="rows",
                unit_scale=True, ncols=100, disable=not verbose)
    affected_total = 0
    failed_chunks = 0

    for i in range(total_chunks):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, total_rows)
        chunk = df_clean.iloc[start_idx:end_idx]

        retry, max_retries = 0, 3
        success = False
        while retry < max_retries and not success:
            try:
                rows = [clean_record_for_mysql(r) for r in chunk.to_dict(orient='records')]
                col_list = ", ".join(f"`{c}`" for c in insert_columns)
                placeholders = ", ".join(f":{c}" for c in insert_columns)
                insert_sql = text(f"INSERT INTO `{table_name}` ({col_list}) VALUES ({placeholders})")

                with engine.connect() as conn_insert:
                    res = conn_insert.execute(insert_sql, rows)
                    conn_insert.commit()
                    affected_total += (res.rowcount or 0)
                success = True
            except Exception as e:
                retry += 1
                if retry < max_retries:
                    tqdm.write(f"   ⚠️  Chunk {i+1}/{total_chunks} failed "
                               f"(attempt {retry}): {str(e)[:100]}")
                    time.sleep(2)

        if not success:
            failed_chunks += 1
        if i % 10 == 0:
            gc.collect()
        pbar.update(len(chunk))

    pbar.close()
    if verbose:
        print(f"   ✅ {label} done. Inserted rows: {affected_total:,}")
    if failed_chunks > 0:
        print(f"   ⚠️  {failed_chunks} chunks failed")

    del df_clean
    gc.collect()

    return table_name


# ================================================================================
# 메인 저장 함수 — 인터페이스는 V1/V2와 완전히 동일 (drop-in 교체)
# ================================================================================

def save_financial_data_incremental(IS=None, BS=None, CF=None,
                                    start_date=None, end_date=None,
                                    mode='upsert', chunk_size=5000,
                                    verify=True, verbose=True):
    """
    재무제표 데이터를 저장 (V4: 삭제 없이 그냥 INSERT만 함 — 기존 데이터는 그대로 보존)
    - 새로 들어온 데이터는 무조건 INSERT (추가)
    - 기존 데이터는 절대 건드리지 않음
    - 처음부터 다시 쌓고 싶으면 `python US_FMP_FS_2_DB_SAVE_LIB.py --truncate` 한 번 실행
    - mode 파라미터는 V1/V2 호환을 위해 유지되나 V4에서는 사용하지 않음(항상 위 방식)

    Parameters
    ----------
    IS, BS, CF : DataFrame, optional
        long-format 재무제표 데이터 (ticker, period, date, date_month, item, value)
    start_date, end_date : str, optional
        'YYYY-MM-DD' 또는 'YYYY-MM'
    chunk_size : int
        청크당 행 수 (기본 5000)
    verify : bool
        저장 후 테이블 통계 출력 여부
    verbose : bool
        상세 로그 출력 여부

    Returns
    -------
    list : 저장된 테이블명 리스트
    """
    saved_tables = []

    jobs = [
        (IS, 'US_IS_from_FMP', 'Income Statement'),
        (BS, 'US_BS_from_FMP', 'Balance Sheet'),
        (CF, 'US_CF_from_FMP', 'Cash Flow'),
    ]

    try:
        job_pbar = tqdm(jobs, desc="전체 저장 진행", ncols=100, disable=not verbose,
                         bar_format="{desc}: {n_fmt}/{total_fmt} 테이블 [{elapsed}] {postfix}")
        for df, table_name, label in job_pbar:
            job_pbar.set_postfix_str(label)
            result = _save_one_statement(
                df, table_name, label,
                start_date=start_date, end_date=end_date,
                chunk_size=chunk_size, mode=mode, verbose=verbose
            )
            if result:
                saved_tables.append(result)

        if verify and saved_tables:
            print_table_stats(saved_tables)

    except Exception as e:
        print(f"\n❌ Error occurred: {str(e)}")
        import traceback
        traceback.print_exc()

    return saved_tables


def print_table_stats(table_names):
    """테이블별 저장 결과 검증 출력"""
    print("\n" + "=" * 70)
    print("VERIFYING SAVED DATA")
    print("=" * 70)
    with engine.connect() as conn:
        for table_name in table_names:
            total_count = conn.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).fetchone()[0]
            ticker_count = conn.execute(text(f"SELECT COUNT(DISTINCT ticker) FROM `{table_name}`")).fetchone()[0]
            date_result = conn.execute(text(f"SELECT MIN(date), MAX(date) FROM `{table_name}`")).fetchone()
            print(f"\n📊 {table_name}:")
            print(f"   - Total records: {total_count:,}")
            print(f"   - Unique tickers: {ticker_count:,}")
            print(f"   - Date range: {date_result[0]} to {date_result[1]}")


# ================================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="이 파일은 보조 모듈입니다. 평소엔 US_FMP_FS_1_RUN_UPDATE.py 만 실행하세요. "
                     "여기서는 --truncate 옵션만 지원합니다 (전체 테이블 비우기)."
    )
    parser.add_argument("--truncate", action="store_true",
                         help="US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP 세 테이블을 전부 비웁니다.")
    args = parser.parse_args()

    if args.truncate:
        print("=" * 70)
        print("전체 테이블 비우기 (TRUNCATE)")
        print("=" * 70)
        truncate_all_tables()
    else:
        print("=" * 70)
        print("⚠️  이 파일은 보조 모듈입니다. 직접 실행하지 마세요.")
        print("▶  실행 파일: US_FMP_FS_1_RUN_UPDATE.py")
        print("=" * 70)
        print("\n  python US_FMP_FS_1_RUN_UPDATE.py --quarters 8")
        print("\n  테이블을 전부 비우고 새로 시작하고 싶으면:")
        print("  python US_FMP_FS_2_DB_SAVE_LIB.py --truncate")
