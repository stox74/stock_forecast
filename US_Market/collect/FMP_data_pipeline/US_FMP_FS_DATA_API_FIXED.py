"""
US FMP Financial Statement Data - Incremental Update Version
기존 데이터를 유지하면서 새로운 데이터만 추가하는 방식으로 수정
중복 기준: ticker, period, date_month, item
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
from tqdm import tqdm

warnings.filterwarnings('ignore')

# ================================================================================
# DB 접속 정보
# ================================================================================
db_info = {
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'host': 'YOUR_HOST',  # get_db_host() 결과로 수정 필요
    'port': '3307',
    'database': 'investar'
}

connection_string = (
    f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
    f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
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


def get_existing_keys(conn, table_name, key_columns):
    """
    기존 데이터의 키 조합을 가져와서 set으로 반환
    key_columns: ['ticker', 'period', 'date_month', 'item'] 등
    """
    if not table_exists(conn, table_name):
        return set()
    
    # period와 date_month 컬럼이 없을 수도 있으므로 확인
    inspector = inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
    
    # 실제 존재하는 컬럼만 사용
    valid_key_columns = [col for col in key_columns if col in existing_columns]
    
    if not valid_key_columns:
        print(f"   ⚠️  Warning: No valid key columns found in {table_name}")
        return set()
    
    cols_str = ", ".join([f"`{col}`" for col in valid_key_columns])
    
    query = f"""
        SELECT {cols_str}
        FROM `{table_name}`
    """
    
    result = conn.execute(text(query))
    existing_keys = set()
    
    for row in result:
        # tuple로 변환 (None 처리 포함)
        key_tuple = tuple(
            str(val) if val is not None else None 
            for val in row
        )
        existing_keys.add(key_tuple)
    
    return existing_keys


def filter_new_data(df, existing_keys, key_columns):
    """
    DataFrame에서 기존 키와 중복되지 않는 새로운 데이터만 필터링
    
    Parameters:
    -----------
    df : DataFrame
        필터링할 데이터
    existing_keys : set
        기존 데이터의 키 tuple set
    key_columns : list
        중복 체크에 사용할 컬럼 리스트
    
    Returns:
    --------
    DataFrame : 새로운 데이터만 포함
    """
    if not existing_keys:
        return df.copy()
    
    # DataFrame에서 실제 존재하는 컬럼만 사용
    valid_key_columns = [col for col in key_columns if col in df.columns]
    
    if not valid_key_columns:
        print(f"   ⚠️  Warning: No valid key columns in DataFrame")
        return df.copy()
    
    # 각 행의 키 조합을 tuple로 만들어 비교
    def make_key(row):
        return tuple(
            str(row[col]) if pd.notna(row[col]) else None 
            for col in valid_key_columns
        )
    
    mask = ~df.apply(make_key, axis=1).isin(existing_keys)
    new_data = df[mask].copy()
    
    duplicates_count = len(df) - len(new_data)
    
    print(f"   📊 Original rows: {len(df):,}")
    print(f"   📊 Existing duplicates filtered: {duplicates_count:,}")
    print(f"   📊 New rows to insert: {len(new_data):,}")
    
    return new_data


def filter_by_date_range(df, start_date=None, end_date=None):
    """
    DataFrame을 날짜 범위로 필터링
    
    Parameters:
    -----------
    df : DataFrame
        필터링할 데이터 (date 컬럼 필요)
    start_date : str, optional
        시작 날짜 ('YYYY-MM-DD' 또는 'YYYY-MM')
    end_date : str, optional
        종료 날짜 ('YYYY-MM-DD' 또는 'YYYY-MM')
    
    Returns:
    --------
    DataFrame : 날짜 범위로 필터링된 데이터
    """
    if df is None or df.empty:
        return df
    
    if 'date' not in df.columns:
        print("   ⚠️  Warning: 'date' column not found, skipping date filter")
        return df
    
    df_filtered = df.copy()
    
    # date 컬럼을 datetime으로 변환
    df_filtered['date'] = pd.to_datetime(df_filtered['date'], errors='coerce')
    
    original_count = len(df_filtered)
    
    # 시작 날짜 필터
    if start_date:
        try:
            # 'YYYY-MM' 형식인 경우 첫날로 변환
            if len(start_date) == 7:  # 'YYYY-MM'
                start_date = f"{start_date}-01"
            
            start_dt = pd.to_datetime(start_date)
            df_filtered = df_filtered[df_filtered['date'] >= start_dt]
            print(f"   📅 Start date filter applied: {start_date} (removed {original_count - len(df_filtered):,} rows)")
            original_count = len(df_filtered)
        except Exception as e:
            print(f"   ⚠️  Invalid start_date format: {start_date} - {str(e)}")
    
    # 종료 날짜 필터
    if end_date:
        try:
            # 'YYYY-MM' 형식인 경우 마지막 날로 변환
            if len(end_date) == 7:  # 'YYYY-MM'
                year, month = map(int, end_date.split('-'))
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                end_date = f"{end_date}-{last_day:02d}"
            
            end_dt = pd.to_datetime(end_date)
            df_filtered = df_filtered[df_filtered['date'] <= end_dt]
            print(f"   📅 End date filter applied: {end_date} (removed {original_count - len(df_filtered):,} rows)")
        except Exception as e:
            print(f"   ⚠️  Invalid end_date format: {end_date} - {str(e)}")
    
    if start_date or end_date:
        print(f"   ✓ Date filtering completed: {len(df):,} → {len(df_filtered):,} rows")
    
    return df_filtered


def create_table_if_not_exists(conn, df_sample, table_name):
    """테이블이 없으면 생성"""
    if not table_exists(conn, table_name):
        print(f"   ℹ️  Table {table_name} does not exist. Creating...")
        df_sample.head(0).to_sql(
            name=table_name,
            con=conn,
            if_exists='replace',
            index=False
        )
        print(f"   ✓ Table {table_name} created")
        return True
    else:
        print(f"   ✓ Table {table_name} already exists")
        return False


def ensure_unique_index(conn, table_name, key_columns):
    """
    UNIQUE INDEX가 없으면 생성
    key_columns: ['ticker', 'period', 'date_month', 'item'] 등
    """
    # 테이블의 실제 컬럼 확인
    inspector = inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
    
    # 실제 존재하는 컬럼만 사용
    valid_key_columns = [col for col in key_columns if col in existing_columns]
    
    if not valid_key_columns:
        print(f"   ⚠️  No valid key columns for index in {table_name}")
        return
    
    idx_name = f"uniq_{table_name}_dedup"
    cols_str = ", ".join([f"`{col}`" for col in valid_key_columns])
    
    try:
        # 기존 인덱스 확인
        indexes = inspector.get_indexes(table_name)
        index_exists = any(idx['name'] == idx_name for idx in indexes)
        
        if not index_exists:
            # 중복 제거
            if 'id' in existing_columns:
                dedup_sql = f"""
                    DELETE t1 FROM `{table_name}` t1
                    INNER JOIN `{table_name}` t2
                    WHERE t1.id < t2.id
                """
                
                for col in valid_key_columns:
                    dedup_sql += f" AND t1.`{col}` = t2.`{col}`"
                
                result = conn.execute(text(dedup_sql))
                if result.rowcount > 0:
                    print(f"   ✓ Removed {result.rowcount} duplicate rows before creating index")
                conn.commit()
            
            # UNIQUE INDEX 생성
            conn.execute(text(
                f"ALTER TABLE `{table_name}` ADD UNIQUE INDEX `{idx_name}` ({cols_str})"
            ))
            conn.commit()
            print(f"   ✓ Created unique index: {idx_name} on ({', '.join(valid_key_columns)})")
        else:
            print(f"   ✓ Unique index {idx_name} already exists")
            
    except Exception as e:
        print(f"   ⚠️  Index creation warning: {str(e)[:200]}")


# ================================================================================
# 메인 저장 함수 - 증분 업데이트 방식
# ================================================================================

def save_financial_data_incremental(IS=None, BS=None, CF=None, start_date=None, end_date=None):
    """
    재무제표 데이터를 증분 업데이트 방식으로 저장
    - 기존 데이터 유지
    - ticker, period, date_month, item 기준으로 중복 제거
    - 새로운 데이터만 추가
    
    Parameters:
    -----------
    IS : DataFrame, optional
        Income Statement 데이터
    BS : DataFrame, optional
        Balance Sheet 데이터
    CF : DataFrame, optional
        Cash Flow 데이터
    start_date : str, optional
        저장할 데이터의 시작 날짜 (형식: 'YYYY-MM-DD' 또는 'YYYY-MM')
        예: '2024-01-01', '2024-01'
    end_date : str, optional
        저장할 데이터의 종료 날짜 (형식: 'YYYY-MM-DD' 또는 'YYYY-MM')
        예: '2024-12-31', '2024-12'
    
    Examples:
    ---------
    # 전체 데이터 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF)
    
    # 2024년 1월~12월 데이터만 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF, 
                                    start_date='2024-01-01', 
                                    end_date='2024-12-31')
    
    # 2024년 Q4 데이터만 저장 (10월~12월)
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date='2024-10', 
                                    end_date='2024-12')
    
    # 2024년 이후 데이터만 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date='2024-01-01')
    """
    
    print("=" * 70)
    print("SAVING FINANCIAL DATA - INCREMENTAL UPDATE MODE")
    print("=" * 70)
    print("📌 Duplicate check keys: ticker, period, date_month, item")
    print("📌 Mode: Keep existing data, append new data only")
    
    # 날짜 범위 표시
    if start_date or end_date:
        print("📌 Date Filter:")
        if start_date:
            print(f"   - Start date: {start_date}")
        if end_date:
            print(f"   - End date: {end_date}")
    else:
        print("📌 Date Filter: None (all data)")
    
    print("=" * 70)
    
    # 중복 체크에 사용할 컬럼 - Income Statement는 period 포함
    KEY_COLUMNS_IS = ['ticker', 'period', 'date_month', 'item']
    # Balance Sheet와 Cash Flow는 period가 없을 수 있음
    KEY_COLUMNS_BS_CF = ['ticker', 'date_month', 'item']
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).fetchone()
            print("✓ Database connection successful\n")
        
        saved_tables = []
        chunk_size = 5000
        
        # =========================================
        # 1) Income Statement 처리
        # =========================================
        if IS is not None and isinstance(IS, pd.DataFrame) and not IS.empty:
            print("\n1. Processing Income Statement data...")
            print(f"   - Data shape: {IS.shape}")
            
            table_name = 'US_IS_from_FMP'
            
            with engine.connect() as conn:
                # 데이터 정제
                df_clean = IS.copy()
                
                # 날짜 범위 필터링 (먼저 수행)
                if start_date or end_date:
                    df_clean = filter_by_date_range(df_clean, start_date, end_date)
                    if df_clean.empty:
                        print(f"   ℹ️  No data in date range for {table_name}")
                        print("\n1. Income Statement: Skipped (no data in date range)")
                        return
                
                for col in df_clean.select_dtypes(include=['object']).columns:
                    if df_clean[col].dtype == 'object':
                        df_clean[col] = df_clean[col].astype(str).str[:500]
                
                df_clean = df_clean.replace([float('inf'), float('-inf')], None)
                
                # date_month가 없으면 생성
                if 'date_month' not in df_clean.columns and 'date' in df_clean.columns:
                    df_clean['date_month'] = pd.to_datetime(df_clean['date']).dt.to_period('M').astype(str)
                    print("   ✓ Created date_month column from date")
                
                # period가 없으면 생성 (있어야 함)
                if 'period' not in df_clean.columns:
                    print("   ⚠️  Warning: 'period' column missing, using empty string")
                    df_clean['period'] = ''
                
                # 테이블이 없으면 생성
                is_new_table = create_table_if_not_exists(conn, df_clean, table_name)
                
                # 기존 키 조회
                print(f"   📊 Checking existing data in {table_name}...")
                existing_keys = get_existing_keys(conn, table_name, KEY_COLUMNS_IS)
                print(f"   📊 Found {len(existing_keys):,} existing records")
                
                # 새로운 데이터만 필터링
                df_new = filter_new_data(df_clean, existing_keys, KEY_COLUMNS_IS)
                
                if len(df_new) == 0:
                    print(f"   ℹ️  No new data to insert for {table_name}")
                else:
                    # UNIQUE INDEX 생성/확인
                    ensure_unique_index(conn, table_name, KEY_COLUMNS_IS)
                    
                    # 청크 단위로 삽입
                    total_rows = len(df_new)
                    total_chunks = (total_rows + chunk_size - 1) // chunk_size
                    print(f"   📊 Inserting {total_rows:,} new rows in {total_chunks} chunks...")
                    
                    pbar = tqdm(total=total_chunks, desc=f"Saving {table_name}", ncols=100)
                    newly_inserted = 0
                    failed_chunks = 0
                    
                    for i in range(total_chunks):
                        start_idx = i * chunk_size
                        end_idx = min((i + 1) * chunk_size, total_rows)
                        chunk = df_new.iloc[start_idx:end_idx].copy()
                        
                        retry, max_retries = 0, 3
                        success = False
                        
                        while retry < max_retries and not success:
                            try:
                                with engine.connect() as conn_insert:
                                    chunk_dict = chunk.to_dict(orient='records')
                                    rows = [clean_record_for_mysql(record) for record in chunk_dict]
                                    
                                    md = MetaData()
                                    tbl = Table(table_name, md, autoload_with=conn_insert)
                                    stmt = mysql_insert(tbl).values(rows).prefix_with("IGNORE")
                                    res = conn_insert.execute(stmt)
                                    conn_insert.commit()
                                    newly_inserted += (res.rowcount or 0)
                                
                                success = True
                                
                            except Exception as e:
                                retry += 1
                                if retry < max_retries:
                                    tqdm.write(f"   ⚠️  Chunk {i+1}/{total_chunks} failed (attempt {retry}): {str(e)[:100]}")
                                    time.sleep(2)
                        
                        if not success:
                            failed_chunks += 1
                        
                        del chunk
                        if i % 10 == 0:
                            gc.collect()
                        
                        pbar.update(1)
                        if i % 10 == 0 or i == total_chunks - 1:
                            tqdm.write(f"   📈 Progress: {newly_inserted:,} inserted, chunk {i+1}/{total_chunks}")
                    
                    pbar.close()
                    print(f"   ✅ Income Statement completed. New rows inserted: {newly_inserted:,}")
                    if failed_chunks > 0:
                        print(f"   ⚠️  {failed_chunks} chunks failed")
                    saved_tables.append(table_name)
                
                del df_clean, df_new
                gc.collect()
        
        else:
            print("\n1. Income Statement: No data to save")
        
        # =========================================
        # 2) Balance Sheet 처리
        # =========================================
        if BS is not None and isinstance(BS, pd.DataFrame) and not BS.empty:
            print("\n2. Processing Balance Sheet data...")
            print(f"   - Data shape: {BS.shape}")
            
            table_name = 'US_BS_from_FMP'
            
            with engine.connect() as conn:
                df_clean = BS.copy()
                
                # 날짜 범위 필터링 (먼저 수행)
                if start_date or end_date:
                    df_clean = filter_by_date_range(df_clean, start_date, end_date)
                    if df_clean.empty:
                        print(f"   ℹ️  No data in date range for {table_name}")
                        print("\n2. Balance Sheet: Skipped (no data in date range)")
                        return
                
                # 데이터 정제
                for col in df_clean.select_dtypes(include=['object']).columns:
                    if df_clean[col].dtype == 'object':
                        df_clean[col] = df_clean[col].astype(str).str[:500]
                
                df_clean = df_clean.replace([float('inf'), float('-inf')], None)
                
                # date_month가 없으면 생성
                if 'date_month' not in df_clean.columns and 'date' in df_clean.columns:
                    df_clean['date_month'] = pd.to_datetime(df_clean['date']).dt.to_period('M').astype(str)
                    print("   ✓ Created date_month column from date")
                
                # 테이블이 없으면 생성
                is_new_table = create_table_if_not_exists(conn, df_clean, table_name)
                
                # 기존 키 조회
                print(f"   📊 Checking existing data in {table_name}...")
                existing_keys = get_existing_keys(conn, table_name, KEY_COLUMNS_BS_CF)
                print(f"   📊 Found {len(existing_keys):,} existing records")
                
                # 새로운 데이터만 필터링
                df_new = filter_new_data(df_clean, existing_keys, KEY_COLUMNS_BS_CF)
                
                if len(df_new) == 0:
                    print(f"   ℹ️  No new data to insert for {table_name}")
                else:
                    # UNIQUE INDEX 생성/확인
                    ensure_unique_index(conn, table_name, KEY_COLUMNS_BS_CF)
                    
                    # 청크 단위로 삽입
                    total_rows = len(df_new)
                    total_chunks = (total_rows + chunk_size - 1) // chunk_size
                    print(f"   📊 Inserting {total_rows:,} new rows in {total_chunks} chunks...")
                    
                    pbar = tqdm(total=total_chunks, desc=f"Saving {table_name}", ncols=100)
                    newly_inserted = 0
                    failed_chunks = 0
                    
                    for i in range(total_chunks):
                        start_idx = i * chunk_size
                        end_idx = min((i + 1) * chunk_size, total_rows)
                        chunk = df_new.iloc[start_idx:end_idx].copy()
                        
                        retry, max_retries = 0, 3
                        success = False
                        
                        while retry < max_retries and not success:
                            try:
                                with engine.connect() as conn_insert:
                                    chunk_dict = chunk.to_dict(orient='records')
                                    rows = [clean_record_for_mysql(record) for record in chunk_dict]
                                    
                                    md = MetaData()
                                    tbl = Table(table_name, md, autoload_with=conn_insert)
                                    stmt = mysql_insert(tbl).values(rows).prefix_with("IGNORE")
                                    res = conn_insert.execute(stmt)
                                    conn_insert.commit()
                                    newly_inserted += (res.rowcount or 0)
                                
                                success = True
                                
                            except Exception as e:
                                retry += 1
                                if retry < max_retries:
                                    tqdm.write(f"   ⚠️  Chunk {i+1}/{total_chunks} failed (attempt {retry}): {str(e)[:100]}")
                                    time.sleep(2)
                        
                        if not success:
                            failed_chunks += 1
                        
                        del chunk
                        if i % 10 == 0:
                            gc.collect()
                        
                        pbar.update(1)
                        if i % 10 == 0 or i == total_chunks - 1:
                            tqdm.write(f"   📈 Progress: {newly_inserted:,} inserted, chunk {i+1}/{total_chunks}")
                    
                    pbar.close()
                    print(f"   ✅ Balance Sheet completed. New rows inserted: {newly_inserted:,}")
                    if failed_chunks > 0:
                        print(f"   ⚠️  {failed_chunks} chunks failed")
                    saved_tables.append(table_name)
                
                del df_clean, df_new
                gc.collect()
        
        else:
            print("\n2. Balance Sheet: No data to save")
        
        # =========================================
        # 3) Cash Flow 처리
        # =========================================
        if CF is not None and isinstance(CF, pd.DataFrame) and not CF.empty:
            print("\n3. Processing Cash Flow data...")
            print(f"   - Data shape: {CF.shape}")
            
            table_name = 'US_CF_from_FMP'
            
            with engine.connect() as conn:
                df_clean = CF.copy()
                
                # 날짜 범위 필터링 (먼저 수행)
                if start_date or end_date:
                    df_clean = filter_by_date_range(df_clean, start_date, end_date)
                    if df_clean.empty:
                        print(f"   ℹ️  No data in date range for {table_name}")
                        print("\n3. Cash Flow: Skipped (no data in date range)")
                        return
                
                # 데이터 정제
                for col in df_clean.select_dtypes(include=['object']).columns:
                    if df_clean[col].dtype == 'object':
                        df_clean[col] = df_clean[col].astype(str).str[:500]
                
                df_clean = df_clean.replace([float('inf'), float('-inf')], None)
                
                # date_month가 없으면 생성
                if 'date_month' not in df_clean.columns and 'date' in df_clean.columns:
                    df_clean['date_month'] = pd.to_datetime(df_clean['date']).dt.to_period('M').astype(str)
                    print("   ✓ Created date_month column from date")
                
                # 테이블이 없으면 생성
                is_new_table = create_table_if_not_exists(conn, df_clean, table_name)
                
                # 기존 키 조회
                print(f"   📊 Checking existing data in {table_name}...")
                existing_keys = get_existing_keys(conn, table_name, KEY_COLUMNS_BS_CF)
                print(f"   📊 Found {len(existing_keys):,} existing records")
                
                # 새로운 데이터만 필터링
                df_new = filter_new_data(df_clean, existing_keys, KEY_COLUMNS_BS_CF)
                
                if len(df_new) == 0:
                    print(f"   ℹ️  No new data to insert for {table_name}")
                else:
                    # UNIQUE INDEX 생성/확인
                    ensure_unique_index(conn, table_name, KEY_COLUMNS_BS_CF)
                    
                    # 청크 단위로 삽입
                    total_rows = len(df_new)
                    total_chunks = (total_rows + chunk_size - 1) // chunk_size
                    print(f"   📊 Inserting {total_rows:,} new rows in {total_chunks} chunks...")
                    
                    pbar = tqdm(total=total_chunks, desc=f"Saving {table_name}", ncols=100)
                    newly_inserted = 0
                    failed_chunks = 0
                    
                    for i in range(total_chunks):
                        start_idx = i * chunk_size
                        end_idx = min((i + 1) * chunk_size, total_rows)
                        chunk = df_new.iloc[start_idx:end_idx].copy()
                        
                        retry, max_retries = 0, 3
                        success = False
                        
                        while retry < max_retries and not success:
                            try:
                                with engine.connect() as conn_insert:
                                    chunk_dict = chunk.to_dict(orient='records')
                                    rows = [clean_record_for_mysql(record) for record in chunk_dict]
                                    
                                    md = MetaData()
                                    tbl = Table(table_name, md, autoload_with=conn_insert)
                                    stmt = mysql_insert(tbl).values(rows).prefix_with("IGNORE")
                                    res = conn_insert.execute(stmt)
                                    conn_insert.commit()
                                    newly_inserted += (res.rowcount or 0)
                                
                                success = True
                                
                            except Exception as e:
                                retry += 1
                                if retry < max_retries:
                                    tqdm.write(f"   ⚠️  Chunk {i+1}/{total_chunks} failed (attempt {retry}): {str(e)[:100]}")
                                    time.sleep(2)
                        
                        if not success:
                            failed_chunks += 1
                        
                        del chunk
                        if i % 10 == 0:
                            gc.collect()
                        
                        pbar.update(1)
                        if i % 10 == 0 or i == total_chunks - 1:
                            tqdm.write(f"   📈 Progress: {newly_inserted:,} inserted, chunk {i+1}/{total_chunks}")
                    
                    pbar.close()
                    print(f"   ✅ Cash Flow completed. New rows inserted: {newly_inserted:,}")
                    if failed_chunks > 0:
                        print(f"   ⚠️  {failed_chunks} chunks failed")
                    saved_tables.append(table_name)
                
                del df_clean, df_new
                gc.collect()
        
        else:
            print("\n3. Cash Flow: No data to save")
        
        # =========================================
        # 검증
        # =========================================
        if saved_tables:
            print("\n" + "=" * 70)
            print("VERIFYING SAVED DATA")
            print("=" * 70)
            
            with engine.connect() as conn:
                for table_name in saved_tables:
                    count_query = f"SELECT COUNT(*) as cnt FROM `{table_name}`"
                    total_count = conn.execute(text(count_query)).fetchone()[0]
                    
                    ticker_query = f"SELECT COUNT(DISTINCT ticker) as cnt FROM `{table_name}`"
                    ticker_count = conn.execute(text(ticker_query)).fetchone()[0]
                    
                    date_query = f"SELECT MIN(date) as min_date, MAX(date) as max_date FROM `{table_name}`"
                    date_result = conn.execute(text(date_query)).fetchone()
                    
                    print(f"\n📊 {table_name}:")
                    print(f"   - Total records: {total_count:,}")
                    print(f"   - Unique tickers: {ticker_count:,}")
                    print(f"   - Date range: {date_result[0]} to {date_result[1]}")
        
        print("\n" + "=" * 70)
        print("INCREMENTAL UPDATE COMPLETED!")
        print("=" * 70)
        if saved_tables:
            print(f"✅ Successfully updated {len(saved_tables)} tables: {saved_tables}")
        else:
            print("ℹ️  No tables were updated (no new data)")
        
    except Exception as e:
        print(f"\n❌ Error occurred: {str(e)}")
        import traceback
        traceback.print_exc()
    
    finally:
        engine.dispose()
        print("\n✓ Database connection closed")
        print("✓ Memory cleanup completed")


# ================================================================================
# 사용 예시
# ================================================================================
if __name__ == "__main__":
    """
    데이터가 이미 IS, BS, CF 변수에 로드되어 있다고 가정
    
    사용 예시:
    
    # 1. 전체 데이터 저장 (날짜 제한 없음)
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF)
    
    # 2. 2024년 전체 데이터만 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF, 
                                    start_date='2024-01-01', 
                                    end_date='2024-12-31')
    
    # 3. 2024년 Q4 데이터만 저장 (10월~12월)
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date='2024-10-01', 
                                    end_date='2024-12-31')
    
    # 4. 월 단위로 지정 (자동으로 해당 월의 첫날/마지막날로 변환)
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date='2024-10',  # 2024-10-01로 변환
                                    end_date='2024-12')    # 2024-12-31로 변환
    
    # 5. 특정 날짜 이후 모든 데이터 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date='2024-01-01')
    
    # 6. 특정 날짜 이전 모든 데이터 저장
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    end_date='2024-12-31')
    
    # 7. 최근 4개월 데이터만 저장
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
    save_financial_data_incremental(IS=IS, BS=BS, CF=CF,
                                    start_date=start_date,
                                    end_date=end_date)
    """
    print("Import this module and call save_financial_data_incremental(IS, BS, CF)")
    print("\nAvailable parameters:")
    print("  - start_date: 'YYYY-MM-DD' or 'YYYY-MM' (optional)")
    print("  - end_date: 'YYYY-MM-DD' or 'YYYY-MM' (optional)")
    print("\nExamples:")
    print("  save_financial_data_incremental(IS, BS, CF)")
    print("  save_financial_data_incremental(IS, BS, CF, start_date='2024-01', end_date='2024-12')")
    print("  save_financial_data_incremental(IS, BS, CF, start_date='2024-10-01')")
