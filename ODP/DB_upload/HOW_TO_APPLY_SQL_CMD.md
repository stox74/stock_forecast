# 명령줄(CMD)에서 SQL 스키마 적용하기

## Windows 환경

### 1단계: MySQL 경로 확인

MySQL이 설치된 경로를 찾습니다 (보통 아래 경로 중 하나):
```
C:\Program Files\MySQL\MySQL Server 8.0\bin
C:\Program Files\MySQL\MySQL Server 5.7\bin
```

### 2단계: CMD 실행

1. Windows 키 + R
2. "cmd" 입력 후 엔터

### 3단계: MySQL 접속

```cmd
# MySQL 경로로 이동 (예시)
cd "C:\Program Files\MySQL\MySQL Server 8.0\bin"

# MySQL 접속
mysql -h [host주소] -P 3307 -u stox7412 -p investar

# 비밀번호 입력: Apt106503!~
```

### 4단계: SQL 파일 실행

MySQL 프롬프트에서:
```sql
-- SQL 파일 실행
source C:\Users\YourName\Downloads\industry_indicators_schema_18industries.sql

-- 또는 절대 경로로
\. C:\Users\YourName\Downloads\industry_indicators_schema_18industries.sql
```

### 5단계: 확인

```sql
-- 테이블 목록 확인
SHOW TABLES;

-- 업종 데이터 확인
SELECT * FROM industry_metadata;

-- 종료
EXIT;
```

---

## 경로에 공백이 있는 경우

```cmd
# 경로를 따옴표로 감싸기
source "C:\Users\Your Name\Downloads\industry_indicators_schema_18industries.sql"
```

---

## 에러 해결

### 오류 1: "mysql: command not found"

**해결방법:**
1. MySQL bin 폴더의 전체 경로 사용
```cmd
"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -h [host] -P 3307 -u stox7412 -p investar
```

2. 또는 환경변수 PATH에 MySQL bin 폴더 추가
   - "내 PC" 우클릭 → "속성" → "고급 시스템 설정"
   - "환경 변수" → "Path" 편집
   - MySQL bin 폴더 경로 추가

### 오류 2: "Access denied"

**확인사항:**
- 호스트 주소 정확한지
- 포트 번호 정확한지 (3307)
- 사용자명 정확한지 (stox7412)
- 비밀번호 정확한지

### 오류 3: "Can't connect to MySQL server"

**해결방법:**
1. MySQL 서비스가 실행 중인지 확인
2. 방화벽 설정 확인
3. 호스트 주소 ping 테스트
```cmd
ping [host주소]
```

---

## 더 간단한 방법 (권장)

Python 스크립트 사용:
```cmd
# Python으로 실행
python apply_sql_schema.py

# 또는 파일 경로 지정
python apply_sql_schema.py -f industry_indicators_schema_18industries.sql

# 기존 테이블 삭제 후 재생성
python apply_sql_schema.py --drop
```
