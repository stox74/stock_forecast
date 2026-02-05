# -*- coding: utf-8 -*-
"""
Python으로 SQL 스키마 적용하기
"""
from sqlalchemy import create_engine, text
import sys

# config.py에서 DB 정보 가져오기
try:
    from config import get_db_info
    db_info = get_db_info()
except ImportError:
    # config.py가 없으면 직접 입력
    db_info = {
        "host": "localhost",  # 실제 호스트로 변경
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }

# DB 연결
engine = create_engine(
    f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
    f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
)

def apply_sql_schema(sql_file_path: str):
    """
    SQL 스키마 파일을 읽어서 DB에 적용
    
    Args:
        sql_file_path: SQL 파일 경로
    """
    print(f"{'='*60}")
    print(f"SQL 스키마 적용 시작")
    print(f"파일: {sql_file_path}")
    print(f"DB: {db_info['database']}@{db_info['host']}:{db_info['port']}")
    print(f"{'='*60}\n")
    
    try:
        # SQL 파일 읽기
        with open(sql_file_path, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # SQL 문장 분리 (세미콜론 기준)
        # 주석 제거
        sql_statements = []
        current_statement = []
        
        for line in sql_content.split('\n'):
            # 주석 라인 건너뛰기
            if line.strip().startswith('--'):
                continue
            
            current_statement.append(line)
            
            # 세미콜론으로 끝나면 하나의 문장 완성
            if line.strip().endswith(';'):
                stmt = '\n'.join(current_statement)
                if stmt.strip():
                    sql_statements.append(stmt)
                current_statement = []
        
        # 각 SQL 문장 실행
        with engine.connect() as conn:
            success_count = 0
            error_count = 0
            
            for i, statement in enumerate(sql_statements, 1):
                # 빈 문장 건너뛰기
                if not statement.strip():
                    continue
                
                # CREATE, INSERT, DROP 문만 표시
                stmt_preview = statement.strip()[:100].replace('\n', ' ')
                
                try:
                    conn.execute(text(statement))
                    conn.commit()
                    
                    if any(keyword in statement.upper() for keyword in ['CREATE TABLE', 'INSERT INTO', 'CREATE VIEW']):
                        print(f"✓ [{i:3d}] {stmt_preview}...")
                        success_count += 1
                    
                except Exception as e:
                    error_msg = str(e)
                    
                    # 이미 존재하는 테이블은 경고만 (에러 아님)
                    if "already exists" in error_msg.lower():
                        print(f"⚠ [{i:3d}] 이미 존재: {stmt_preview}...")
                    else:
                        print(f"✗ [{i:3d}] 오류: {error_msg[:100]}")
                        error_count += 1
        
        # 결과 요약
        print(f"\n{'='*60}")
        print(f"완료!")
        print(f"성공: {success_count}건")
        if error_count > 0:
            print(f"오류: {error_count}건")
        print(f"{'='*60}\n")
        
        # 생성된 테이블 확인
        verify_tables()
        
    except FileNotFoundError:
        print(f"✗ 오류: 파일을 찾을 수 없습니다 - {sql_file_path}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ 오류: {str(e)}")
        sys.exit(1)


def verify_tables():
    """생성된 테이블 확인"""
    print("생성된 테이블 확인 중...")
    
    with engine.connect() as conn:
        # 테이블 목록 조회
        result = conn.execute(text("""
            SELECT TABLE_NAME, TABLE_ROWS, CREATE_TIME
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
        
        if tables:
            print("\n생성된 테이블:")
            print(f"{'테이블명':<30} {'행수':<10} {'생성일시'}")
            print("-" * 60)
            for table in tables:
                print(f"{table[0]:<30} {table[1] or 0:<10} {table[2]}")
            
            # 업종 메타데이터 확인
            print("\n업종 메타데이터 확인:")
            result = conn.execute(text("""
                SELECT industry_code, industry_name, industry_category
                FROM industry_metadata
                ORDER BY display_order
            """))
            
            industries = result.fetchall()
            if industries:
                print(f"\n등록된 업종: {len(industries)}개")
                for ind in industries[:5]:  # 처음 5개만 표시
                    print(f"  - {ind[1]} ({ind[0]}, {ind[2]})")
                if len(industries) > 5:
                    print(f"  ... 외 {len(industries) - 5}개")
            
        else:
            print("\n⚠ 경고: 테이블이 생성되지 않았습니다.")
            print("SQL 파일을 확인해주세요.")


def drop_all_tables():
    """모든 테이블 삭제 (재설치 시 사용)"""
    print("\n⚠ 경고: 모든 테이블을 삭제합니다!")
    confirm = input("계속하시겠습니까? (yes/no): ")
    
    if confirm.lower() != 'yes':
        print("취소되었습니다.")
        return
    
    with engine.connect() as conn:
        tables = [
            'industry_indicators',
            'industry_metadata',
            'indicator_metadata',
            'entity_metadata'
        ]
        
        for table in tables:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
                conn.commit()
                print(f"✓ {table} 삭제 완료")
            except Exception as e:
                print(f"✗ {table} 삭제 실패: {str(e)}")
    
    print("\n모든 테이블이 삭제되었습니다.")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='SQL 스키마 적용')
    parser.add_argument('--file', '-f', 
                       default='industry_indicators_schema_18industries.sql',
                       help='SQL 파일 경로')
    parser.add_argument('--drop', action='store_true',
                       help='기존 테이블 삭제')
    
    args = parser.parse_args()
    
    if args.drop:
        drop_all_tables()
    
    # SQL 스키마 적용
    apply_sql_schema(args.file)
