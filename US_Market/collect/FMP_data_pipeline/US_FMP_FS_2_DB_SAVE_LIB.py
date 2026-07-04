"""
================================================================================
[2단계 / 보조 모듈] US_FMP_FS_2_DB_SAVE_LIB.py
================================================================================
⚠️  이 파일은 직접 실행하지 않습니다. (import 전용 라이브러리)
▶  실행은 US_FMP_FS_1_RUN_UPDATE.py 하나만 하면 됩니다.

역할: FMP 재무제표 long-format DataFrame을 MySQL에 UPSERT 방식으로 저장

v1(US_FMP_FS_DATA_API_FIXED) 대비 변경 사항
============================================
1. INSERT IGNORE → ON DUPLICATE KEY UPDATE (UPSERT)
   - FMP는 8-K 속보치를 먼저 제공하고 10-Q/10-K 확정치로 나중에 교체함
   - IGNORE 방식은 확정치/정정치(restatement)가 영원히 DB에 반영되지 않음
   - UPSERT는 동일 키의 값이 바뀌면 자동으로 갱신됨 → "항상 최신" 보장
2. 전체 키 프리로드(get_existing_keys) 제거
   - 매 실행마다 테이블 풀스캔 + df.apply 행루프였음 (수백만 행에서 병목)
   - 중복 처리는 DB의 UNIQUE INDEX + UPSERT가 담당
3. BS/CF 섹션의 조기 return 버그 수정
   - 날짜 범위에 데이터가 없으면 함수 전체가 종료되어 이후 섹션이 스킵되던 문제
4. astype(str)로 인한 NaN → 'nan' 문자열 오염 수정
5. BS/CF 중복 키에 period 추가 + 기존 인덱스 자동 마이그레이션
   - FY 행과 Q4 행이 같은 (ticker, date_month, item)으로 충돌하던 문제 해결
6. IS/BS/CF 3중 복제 코드를 단일 함수(_save_one_statement)로 통합
7. 배치 반복 호출을 위해 engine.dispose()를 함수 밖으로 분리
   - 1단계 스크립트가 배치마다 저장 함수를 호출한 뒤 마지막에 close_engine() 호출

중복 기준(전 테이블 공통): ticker, period, date_month, item
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
from sqlalchemy import create_engine, text, Table, MetaData, inspect
from sqlalchemy.dialects.mysql import insert as mysql_insert
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
    pool_pre_ping=True
)

# 중복 체크 키 — IS/BS/CF 공통 (FMP 응답에는 세 재무제표 모두 period가 존재)
# period를 빼면 FY 행과 Q4 행(같은 결산일)이 충돌함
KEY_COLUMNS = ['ticker', 'period', 'date_month', 'item']


def close_engine():
    """모든 배치 저장이 끝난 뒤 1단계 스크립트에서 마지막에 한 번 호출"""
    engine.dispose()
    print("✓ Database connection pool closed")


# ================================================================================
# 유틸리티 함수
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
    """테이블 존재 여부 확인"""
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
            if len(start_date) == 7:  # 'YYYY-MM' → 해당 월 1일
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
            if len(end_date) == 7:  # 'YYYY-MM' → 해당 월 말일
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
    테이블이 없으면 생성
    - 키 컬럼은 VARCHAR(191)로 강제 (TEXT는 UNIQUE INDEX 생성 불가)
    - AUTO_INCREMENT PK(id) 추가
    """
    if not table_exists(conn, table_name):
        print(f"   ℹ️  Table {table_name} does not exist. Creating...")
        dtype_map = {c: VARCHAR(191) for c in key_columns if c in df_sample.columns}
        df_sample.head(0).to_sql(
            name=table_name,
            con=conn,
            if_exists='fail',
            index=False,
            dtype=dtype_map
        )
        try:
            conn.execute(text(
                f"ALTER TABLE `{table_name}` "
                f"ADD COLUMN `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
            ))
            conn.commit()
        except Exception as e:
            print(f"   ⚠️  PK creation warning: {str(e)[:150]}")
        print(f"   ✓ Table {table_name} created")
        return True
    return False


def _index_column_expr(conn, table_name, col):
    """TEXT 타입 컬럼은 prefix 길이를 붙여 인덱스 가능하게 함"""
    inspector = inspect(conn)
    for c in inspector.get_columns(table_name):
        if c['name'] == col:
            type_str = str(c['type']).upper()
            if 'TEXT' in type_str or 'BLOB' in type_str:
                return f"`{col}`(100)"
            return f"`{col}`"
    return f"`{col}`"


def ensure_unique_index(conn, table_name, key_columns):
    """
    UNIQUE INDEX 생성/검증
    - 기존 인덱스의 컬럼 구성이 key_columns와 다르면 드롭 후 재생성 (마이그레이션)
      예: 구버전 BS/CF 인덱스 (ticker, date_month, item) → period 포함으로 갱신
    - 재생성 전 키 기준 중복 행 제거 (id 컬럼 필요, 없으면 자동 추가)
    """
    inspector = inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
    valid_key_columns = [col for col in key_columns if col in existing_columns]

    if not valid_key_columns:
        print(f"   ⚠️  No valid key columns for index in {table_name}")
        return

    idx_name = f"uniq_{table_name}_dedup"

    try:
        indexes = inspector.get_indexes(table_name)
        existing_idx = next((idx for idx in indexes if idx['name'] == idx_name), None)

        # 기존 인덱스의 컬럼 구성이 같으면 그대로 사용
        if existing_idx is not None:
            if list(existing_idx.get('column_names') or []) == valid_key_columns:
                return
            print(f"   🔄 Index column mismatch "
                  f"({existing_idx.get('column_names')} → {valid_key_columns}). Recreating...")
            conn.execute(text(f"ALTER TABLE `{table_name}` DROP INDEX `{idx_name}`"))
            conn.commit()

        # 중복 제거를 위해 id 컬럼 확보
        if 'id' not in existing_columns:
            try:
                conn.execute(text(
                    f"ALTER TABLE `{table_name}` "
                    f"ADD COLUMN `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
                ))
                conn.commit()
                existing_columns.append('id')
            except Exception as e:
                print(f"   ⚠️  Could not add id column: {str(e)[:150]}")

        # 키 기준 중복 행 제거 (최신 id 유지)
        if 'id' in existing_columns:
            join_cond = " AND ".join(
                f"t1.`{col}` <=> t2.`{col}`" for col in valid_key_columns
            )
            dedup_sql = f"""
                DELETE t1 FROM `{table_name}` t1
                INNER JOIN `{table_name}` t2
                ON {join_cond} AND t1.id < t2.id
            """
            result = conn.execute(text(dedup_sql))
            if result.rowcount and result.rowcount > 0:
                print(f"   ✓ Removed {result.rowcount:,} duplicate rows before creating index")
            conn.commit()

        # UNIQUE INDEX 생성 (TEXT 컬럼은 prefix 처리)
        cols_str = ", ".join(_index_column_expr(conn, table_name, col)
                             for col in valid_key_columns)
        conn.execute(text(
            f"ALTER TABLE `{table_name}` ADD UNIQUE INDEX `{idx_name}` ({cols_str})"
        ))
        conn.commit()
        print(f"   ✓ Created unique index: {idx_name} on ({', '.join(valid_key_columns)})")

    except Exception as e:
        print(f"   ⚠️  Index creation warning: {str(e)[:200]}")


def _clean_object_columns(df, max_len=500):
    """object 컬럼 문자열 길이 제한 — NaN은 그대로 유지 ('nan' 문자열 오염 방지)"""
    for col in df.select_dtypes(include=['object']).columns:
        mask = df[col].notna()
        if mask.any():
            df.loc[mask, col] = df.loc[mask, col].astype(str).str[:max_len]
    return df


# ================================================================================
# 단일 재무제표 저장 (IS/BS/CF 공통 로직)
# ================================================================================

def _save_one_statement(df, table_name, label,
                        start_date=None, end_date=None,
                        chunk_size=5000, mode='upsert', verbose=True):
    """
    하나의 재무제표 DataFrame을 UPSERT 방식으로 저장

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

    # 1) 날짜 범위 필터링 — 비어도 return하지 않고 이 섹션만 스킵 (v1 버그 수정)
    if start_date or end_date:
        df_clean = filter_by_date_range(df_clean, start_date, end_date)
        if df_clean is None or df_clean.empty:
            print(f"   ℹ️  No data in date range for {table_name} — skipped")
            return None

    # 2) 데이터 정제
    df_clean = _clean_object_columns(df_clean)
    df_clean = df_clean.replace([float('inf'), float('-inf')], None)

    # 3) 파생 컬럼 생성
    if 'date_month' not in df_clean.columns and 'date' in df_clean.columns:
        df_clean['date_month'] = (
            pd.to_datetime(df_clean['date']).dt.to_period('M').astype(str)
        )

    if 'period' not in df_clean.columns:
        print("   ⚠️  Warning: 'period' column missing, using empty string")
        df_clean['period'] = ''

    with engine.connect() as conn:
        # 4) 테이블/인덱스 준비
        create_table_if_not_exists(conn, df_clean, table_name, KEY_COLUMNS)
        ensure_unique_index(conn, table_name, KEY_COLUMNS)

        # 테이블 실제 컬럼과 교집합만 사용 (스키마 불일치 방지)
        inspector = inspect(conn)
        table_columns = [c['name'] for c in inspector.get_columns(table_name)]
        insert_columns = [c for c in df_clean.columns if c in table_columns]
        missing = set(df_clean.columns) - set(insert_columns)
        if missing:
            print(f"   ⚠️  Columns not in table (dropped): {sorted(missing)}")
        df_clean = df_clean[insert_columns]

    # 5) 청크 단위 UPSERT
    total_rows = len(df_clean)
    total_chunks = (total_rows + chunk_size - 1) // chunk_size

    pbar = tqdm(total=total_chunks, desc=f"Saving {table_name}", ncols=100,
                disable=not verbose)
    affected_total = 0
    failed_chunks = 0

    valid_keys = [c for c in KEY_COLUMNS if c in insert_columns]

    for i in range(total_chunks):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, total_rows)
        chunk = df_clean.iloc[start_idx:end_idx]

        retry, max_retries = 0, 3
        success = False

        while retry < max_retries and not success:
            try:
                with engine.connect() as conn_insert:
                    rows = [clean_record_for_mysql(r)
                            for r in chunk.to_dict(orient='records')]

                    md = MetaData()
                    tbl = Table(table_name, md, autoload_with=conn_insert)
                    stmt = mysql_insert(tbl).values(rows)

                    if mode == 'upsert':
                        # 키/PK를 제외한 모든 컬럼을 새 값으로 갱신
                        update_cols = {
                            c.name: stmt.inserted[c.name]
                            for c in tbl.columns
                            if c.name not in valid_keys
                            and c.name != 'id'
                            and c.name in insert_columns
                        }
                        if update_cols:
                            stmt = stmt.on_duplicate_key_update(**update_cols)
                        else:
                            stmt = stmt.prefix_with("IGNORE")
                    else:  # 'insert_ignore' — 필요 시 강제 사용 가능
                        stmt = stmt.prefix_with("IGNORE")

                    res = conn_insert.execute(stmt)
                    conn_insert.commit()
                    # rowcount 의미: 신규삽입=1, 갱신=2, 동일값=0 (행당)
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

        pbar.update(1)

    pbar.close()
    if verbose:
        print(f"   ✅ {label} done. Affected rows: {affected_total:,} "
              f"(신규=1, 갱신=2, 동일=0 per row)")
    if failed_chunks > 0:
        print(f"   ⚠️  {failed_chunks} chunks failed")

    del df_clean
    gc.collect()

    return table_name


# ================================================================================
# 메인 저장 함수 - 증분 업데이트(UPSERT) 방식
# ================================================================================

def save_financial_data_incremental(IS=None, BS=None, CF=None,
                                    start_date=None, end_date=None,
                                    mode='upsert', chunk_size=5000,
                                    verify=True, verbose=True):
    """
    재무제표 데이터를 증분 UPSERT 방식으로 저장
    - 신규 키 → INSERT
    - 기존 키의 값 변경(8-K→10-Q 확정, restatement) → UPDATE  ← "항상 최신" 보장
    - 기존 키의 동일 값 → 무변화 (affected 0)

    Parameters
    ----------
    IS, BS, CF : DataFrame, optional
        long-format 재무제표 데이터 (ticker, period, date, date_month, item, value)
    start_date, end_date : str, optional
        'YYYY-MM-DD' 또는 'YYYY-MM'
    mode : str
        'upsert' (기본, 권장) 또는 'insert_ignore' (구버전 호환)
    chunk_size : int
        청크당 행 수 (기본 5000)
    verify : bool
        저장 후 테이블 통계 출력 여부 (배치 반복 호출 시 False 권장)
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
        for df, table_name, label in jobs:
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
            total_count = conn.execute(text(
                f"SELECT COUNT(*) FROM `{table_name}`"
            )).fetchone()[0]
            ticker_count = conn.execute(text(
                f"SELECT COUNT(DISTINCT ticker) FROM `{table_name}`"
            )).fetchone()[0]
            date_result = conn.execute(text(
                f"SELECT MIN(date), MAX(date) FROM `{table_name}`"
            )).fetchone()
            print(f"\n📊 {table_name}:")
            print(f"   - Total records: {total_count:,}")
            print(f"   - Unique tickers: {ticker_count:,}")
            print(f"   - Date range: {date_result[0]} to {date_result[1]}")


# ================================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("⚠️  이 파일은 보조 모듈입니다. 직접 실행하지 마세요.")
    print("▶  실행 파일: US_FMP_FS_1_RUN_UPDATE.py")
    print("=" * 70)
    print("\n  python US_FMP_FS_1_RUN_UPDATE.py --quarters 8")
