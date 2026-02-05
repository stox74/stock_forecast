# -*- coding: utf-8 -*-
"""
18개 업종 통합 DB 자동 설치 스크립트
모든 것을 한 번에 처리합니다!
"""
from sqlalchemy import create_engine, text
import sys
import os

print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║     18개 업종 통합 데이터베이스 설치 프로그램               ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""")

# ============================================================
# 1단계: DB 연결 정보 입력
# ============================================================

print("1단계: DB 연결 정보 확인")
print("-" * 60)

# config.py에서 가져오기 시도
try:
    from config import get_db_info
    db_info = get_db_info()
    print("✓ config.py에서 DB 정보를 가져왔습니다.")
    print(f"  - Host: {db_info['host']}")
    print(f"  - Port: {db_info['port']}")
    print(f"  - User: {db_info['user']}")
    print(f"  - Database: {db_info['database']}")
    
    confirm = input("\n이 정보로 진행하시겠습니까? (y/n): ")
    if confirm.lower() != 'y':
        raise Exception("사용자 취소")
        
except:
    print("⚠ config.py를 찾을 수 없습니다. 직접 입력해주세요.")
    db_info = {
        "host": input("Host (예: localhost): "),
        "port": int(input("Port (예: 3307): ")),
        "user": input("User (예: stox7412): "),
        "password": input("Password: "),
        "database": input("Database (예: investar): ")
    }

# ============================================================
# 2단계: DB 연결 테스트
# ============================================================

print(f"\n2단계: DB 연결 테스트")
print("-" * 60)

try:
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    
    with engine.connect() as conn:
        result = conn.execute(text("SELECT VERSION()"))
        version = result.fetchone()[0]
        print(f"✓ DB 연결 성공!")
        print(f"  MySQL Version: {version}")
        
except Exception as e:
    print(f"✗ DB 연결 실패: {str(e)}")
    print("\n연결 정보를 확인해주세요:")
    print("  1. MySQL 서비스가 실행 중인지")
    print("  2. 호스트, 포트, 사용자명, 비밀번호가 정확한지")
    print("  3. 데이터베이스가 존재하는지")
    sys.exit(1)

# ============================================================
# 3단계: 기존 테이블 확인
# ============================================================

print(f"\n3단계: 기존 테이블 확인")
print("-" * 60)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT TABLE_NAME 
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = :db_name
          AND TABLE_NAME IN (
              'industry_indicators',
              'industry_metadata',
              'indicator_metadata',
              'entity_metadata'
          )
    """), {"db_name": db_info['database']})
    
    existing_tables = [row[0] for row in result.fetchall()]

if existing_tables:
    print(f"⚠ 기존 테이블 발견: {len(existing_tables)}개")
    for table in existing_tables:
        print(f"  - {table}")
    
    action = input("\n어떻게 하시겠습니까?\n"
                  "  1. 기존 테이블 유지하고 데이터만 추가\n"
                  "  2. 기존 테이블 삭제하고 새로 생성\n"
                  "  3. 취소\n"
                  "선택 (1/2/3): ")
    
    if action == '3':
        print("취소되었습니다.")
        sys.exit(0)
    elif action == '2':
        print("\n기존 테이블을 삭제합니다...")
        with engine.connect() as conn:
            for table in existing_tables:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                conn.commit()
                print(f"✓ {table} 삭제 완료")
    else:
        print("\n기존 테이블을 유지합니다.")
else:
    print("✓ 기존 테이블 없음 (새로 생성합니다)")

# ============================================================
# 4단계: SQL 스키마 적용
# ============================================================

print(f"\n4단계: SQL 스키마 적용")
print("-" * 60)

# SQL 파일 경로 찾기
sql_file_candidates = [
    'industry_indicators_schema_18industries.sql',
    '../industry_indicators_schema_18industries.sql',
    'downloads/industry_indicators_schema_18industries.sql'
]

sql_file = None
for candidate in sql_file_candidates:
    if os.path.exists(candidate):
        sql_file = candidate
        break

if not sql_file:
    print("✗ SQL 파일을 찾을 수 없습니다.")
    sql_file = input("SQL 파일 경로를 입력해주세요: ")
    
    if not os.path.exists(sql_file):
        print("✗ 파일이 존재하지 않습니다.")
        sys.exit(1)

print(f"✓ SQL 파일 발견: {sql_file}")

# SQL 파일 읽기 및 실행
try:
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    # SQL 문장 분리
    statements = []
    current = []
    
    for line in sql_content.split('\n'):
        if line.strip().startswith('--'):
            continue
        current.append(line)
        if line.strip().endswith(';'):
            stmt = '\n'.join(current)
            if stmt.strip():
                statements.append(stmt)
            current = []
    
    # 실행
    print(f"\nSQL 문장 실행 중... (총 {len(statements)}개)")
    
    with engine.connect() as conn:
        success = 0
        skip = 0
        
        for stmt in statements:
            try:
                conn.execute(text(stmt))
                conn.commit()
                
                if any(kw in stmt.upper() for kw in ['CREATE TABLE', 'INSERT INTO', 'CREATE VIEW']):
                    success += 1
                    
            except Exception as e:
                if "already exists" in str(e).lower():
                    skip += 1
                else:
                    print(f"  ⚠ 경고: {str(e)[:80]}")
    
    print(f"\n✓ SQL 적용 완료!")
    print(f"  성공: {success}개")
    if skip > 0:
        print(f"  건너뜀: {skip}개 (이미 존재)")

except Exception as e:
    print(f"✗ SQL 적용 실패: {str(e)}")
    sys.exit(1)

# ============================================================
# 5단계: 설치 확인
# ============================================================

print(f"\n5단계: 설치 확인")
print("-" * 60)

with engine.connect() as conn:
    # 테이블 확인
    result = conn.execute(text("""
        SELECT TABLE_NAME, TABLE_ROWS
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = :db_name
          AND TABLE_NAME IN (
              'industry_indicators',
              'industry_metadata',
              'indicator_metadata',
              'entity_metadata'
          )
        ORDER BY TABLE_NAME
    """), {"db_name": db_info['database']})
    
    tables = result.fetchall()
    
    print(f"\n생성된 테이블: {len(tables)}개")
    for table in tables:
        print(f"  ✓ {table[0]:<30} (행수: {table[1] or 0})")
    
    # 업종 메타데이터 확인
    result = conn.execute(text("""
        SELECT COUNT(*) FROM industry_metadata
    """))
    
    industry_count = result.fetchone()[0]
    
    print(f"\n등록된 업종: {industry_count}개")
    
    if industry_count == 18:
        print("  ✓ 18개 업종 모두 등록 완료!")
        
        # 업종 목록 표시
        result = conn.execute(text("""
            SELECT display_order, industry_name, industry_code, industry_category
            FROM industry_metadata
            ORDER BY display_order
        """))
        
        print("\n업종 목록:")
        for row in result.fetchall():
            print(f"  {row[0]:2d}. {row[1]:<25} ({row[2]:<25} - {row[3]})")
    else:
        print(f"  ⚠ 예상된 업종 수(18개)와 다릅니다.")

# ============================================================
# 6단계: 완료
# ============================================================

print(f"\n{'='*60}")
print("✓ 설치가 완료되었습니다!")
print("="*60)

print("""
다음 단계:
1. Python에서 데이터 저장:
   from industry_processor_18industries import IndustryDataProcessor
   processor = IndustryDataProcessor(db_info)
   processor.save_to_db(your_dataframe)

2. SQL로 데이터 조회:
   SELECT * FROM industry_metadata;
   SELECT * FROM industry_indicators LIMIT 10;

3. 기존 항공 데이터 마이그레이션:
   python migrate_aviation_data.py
""")

print("\n문제가 있으시면 다음을 확인해주세요:")
print("  - GUIDE_18INDUSTRIES.md")
print("  - HOW_TO_APPLY_SQL_WORKBENCH.md")
print("  - HOW_TO_APPLY_SQL_CMD.md")
