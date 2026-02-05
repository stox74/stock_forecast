# SQL 스키마 적용 방법 총정리

## 🎯 가장 쉬운 방법 (추천)

### Python 자동 설치 스크립트 실행 ⭐⭐⭐⭐⭐

```bash
# 1. 파일 다운로드
# - install_database.py
# - industry_indicators_schema_18industries.sql

# 2. Python 실행
python install_database.py

# 3. 안내에 따라 진행
#    - DB 정보 확인
#    - 자동 설치
#    - 결과 확인
```

**장점:**
- ✅ 모든 것을 자동으로 처리
- ✅ 에러 확인 및 안내
- ✅ 설치 검증까지 자동
- ✅ 가장 안전함

---

## 📋 방법별 비교

| 방법 | 난이도 | 속도 | 추천도 | 특징 |
|------|--------|------|--------|------|
| **Python 자동 설치** | ⭐ | 5분 | ⭐⭐⭐⭐⭐ | 가장 쉬움, 자동화 |
| **MySQL Workbench** | ⭐⭐ | 3분 | ⭐⭐⭐⭐ | GUI, 시각적 |
| **Python 스크립트** | ⭐⭐ | 3분 | ⭐⭐⭐⭐ | 프로그래밍 방식 |
| **명령줄(CMD)** | ⭐⭐⭐ | 2분 | ⭐⭐⭐ | 전문가용 |

---

## 방법 1: Python 자동 설치 (가장 추천)

### 준비물
- `install_database.py`
- `industry_indicators_schema_18industries.sql`
- config.py (선택사항)

### 실행
```bash
python install_database.py
```

### 과정
```
1. DB 정보 확인 (config.py 또는 수동 입력)
2. 연결 테스트
3. 기존 테이블 확인
4. SQL 스키마 적용
5. 설치 검증
6. 완료!
```

### 예상 출력
```
╔══════════════════════════════════════════════════════════╗
║     18개 업종 통합 데이터베이스 설치 프로그램               ║
╚══════════════════════════════════════════════════════════╝

1단계: DB 연결 정보 확인
--------------------------------------------------
✓ config.py에서 DB 정보를 가져왔습니다.
  - Host: localhost
  - Port: 3307
  - User: stox7412
  - Database: investar

2단계: DB 연결 테스트
--------------------------------------------------
✓ DB 연결 성공!
  MySQL Version: 8.0.x

3단계: 기존 테이블 확인
--------------------------------------------------
✓ 기존 테이블 없음 (새로 생성합니다)

4단계: SQL 스키마 적용
--------------------------------------------------
✓ SQL 파일 발견: industry_indicators_schema_18industries.sql
SQL 문장 실행 중... (총 XX개)
✓ SQL 적용 완료!
  성공: XX개

5단계: 설치 확인
--------------------------------------------------
생성된 테이블: 4개
  ✓ industry_indicators
  ✓ industry_metadata
  ✓ indicator_metadata
  ✓ entity_metadata

등록된 업종: 18개
  ✓ 18개 업종 모두 등록 완료!

업종 목록:
   1. 거시지표
   2. 화장품지표
   ...
  18. 광고미디어지표

✓ 설치가 완료되었습니다!
```

---

## 방법 2: MySQL Workbench (시각적)

### 단계별 가이드

1. **MySQL Workbench 실행**
   ```
   프로그램 실행 → Connection 선택
   ```

2. **SQL 파일 열기**
   ```
   File → Open SQL Script...
   industry_indicators_schema_18industries.sql 선택
   ```

3. **실행**
   ```
   번개 아이콘(⚡) 클릭
   또는 Ctrl + Shift + Enter
   ```

4. **확인**
   ```
   Schemas → investar → Tables 확인
   - industry_indicators
   - industry_metadata
   - indicator_metadata
   - entity_metadata
   ```

**상세 가이드:** `HOW_TO_APPLY_SQL_WORKBENCH.md` 참고

---

## 방법 3: Python 스크립트

### 코드 실행
```bash
python apply_sql_schema.py
```

### 옵션
```bash
# 특정 파일 지정
python apply_sql_schema.py -f path/to/schema.sql

# 기존 테이블 삭제 후 재생성
python apply_sql_schema.py --drop
```

---

## 방법 4: 명령줄(CMD)

### Windows
```cmd
# 1. MySQL 접속
mysql -h localhost -P 3307 -u stox7412 -p investar

# 2. 비밀번호 입력
Enter password: Apt106503!~

# 3. SQL 파일 실행
source C:\path\to\industry_indicators_schema_18industries.sql

# 4. 확인
SHOW TABLES;
SELECT * FROM industry_metadata;

# 5. 종료
EXIT;
```

**상세 가이드:** `HOW_TO_APPLY_SQL_CMD.md` 참고

---

## 🔧 문제 해결

### 문제 1: "파일을 찾을 수 없습니다"

**해결:**

```python
# 현재 디렉토리 확인
import os

print(os.getcwd())

# 파일 목록 확인
print(os.listdir('../../../../../../Downloads'))

# 파일 경로를 절대 경로로 지정
python
install_database.py
# 프롬프트에서 절대 경로 입력
```

### 문제 2: "DB 연결 실패"

**체크리스트:**
- [ ] MySQL 서비스 실행 중인가?
- [ ] 호스트 주소 정확한가?
- [ ] 포트 번호 정확한가? (3307)
- [ ] 사용자명 정확한가? (stox7412)
- [ ] 비밀번호 정확한가?
- [ ] 데이터베이스가 존재하는가? (investar)

**확인 방법:**
```python
from config import get_db_info
db_info = get_db_info()
print(db_info)
```

### 문제 3: "Table already exists"

**해결 방법 1 - 무시하고 계속:**
```
이미 존재하는 테이블은 건너뛰고 계속 진행됩니다.
데이터는 유지되며 새로운 데이터만 추가됩니다.
```

**해결 방법 2 - 재생성:**
```bash
# Python 스크립트로 삭제 후 재생성
python install_database.py
# → 옵션 2 선택 (기존 테이블 삭제하고 새로 생성)

# 또는
python apply_sql_schema.py --drop
```

### 문제 4: "권한 오류"

**해결:**
```sql
-- MySQL에서 권한 확인
SHOW GRANTS FOR 'stox7412'@'%';

-- 필요한 권한
-- CREATE, INSERT, UPDATE, DELETE, SELECT
```

---

## ✅ 설치 확인

### SQL로 확인
```sql
-- 1. 테이블 확인
SHOW TABLES;

-- 2. 업종 메타데이터 확인
SELECT * FROM industry_metadata;

-- 3. 행 수 확인
SELECT 
    'industry_metadata' as table_name, 
    COUNT(*) as row_count 
FROM industry_metadata
UNION ALL
SELECT 
    'indicator_metadata', 
    COUNT(*) 
FROM indicator_metadata;
```

### Python으로 확인
```python
from sqlalchemy import create_engine, text
from config import get_db_info

engine = create_engine(
    f"mysql+pymysql://{get_db_info()['user']}:{get_db_info()['password']}"
    f"@{get_db_info()['host']}:{get_db_info()['port']}/{get_db_info()['database']}?charset=utf8mb4"
)

with engine.connect() as conn:
    # 테이블 확인
    result = conn.execute(text("SHOW TABLES"))
    tables = [row[0] for row in result.fetchall()]
    print("Tables:", tables)
    
    # 업종 수 확인
    result = conn.execute(text("SELECT COUNT(*) FROM industry_metadata"))
    count = result.fetchone()[0]
    print(f"Industries: {count}")
    
    if count == 18:
        print("✓ 설치 성공!")
    else:
        print("✗ 설치 확인 필요")
```

---

## 🎯 추천 순서

### 초보자
```
1. install_database.py 실행
2. 안내에 따라 진행
3. 완료!
```

### 중급자
```
1. MySQL Workbench로 실행
2. 또는 apply_sql_schema.py 사용
```

### 고급자
```
1. 명령줄에서 직접 실행
2. 또는 자신만의 스크립트 작성
```

---

## 📞 다음 단계

설치 완료 후:

1. **데이터 저장 테스트**
   ```python
   from industry_processor_18industries import IndustryDataProcessor
   processor = IndustryDataProcessor(get_db_info())
   
   # 테스트 데이터
   import pandas as pd
   test_df = pd.DataFrame({
       'date': ['2024-01-01'],
       'indicator': ['test'],
       'value': [100]
   })
   
   result = processor.standardize_data(
       test_df, 
       'macro', 
       'TEST'
   )
   processor.save_to_db(result)
   ```

2. **데이터 조회 테스트**
   ```sql
   SELECT * FROM industry_indicators 
   WHERE source = 'TEST';
   ```

3. **실제 데이터 마이그레이션**
   - 항공 데이터 → aviation_travel
   - 거시지표 → macro
   - 기타 업종 순차 추가

---

## 📚 참고 문서

- `GUIDE_18INDUSTRIES.md` - 전체 가이드
- `HOW_TO_APPLY_SQL_WORKBENCH.md` - Workbench 상세 가이드
- `HOW_TO_APPLY_SQL_CMD.md` - 명령줄 상세 가이드

**질문이 있으시면 언제든 문의해주세요!** 💪
