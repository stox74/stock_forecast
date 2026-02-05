# MySQL Workbench를 사용한 스키마 적용 가이드

## 1단계: MySQL Workbench 실행

1. MySQL Workbench 프로그램 실행
2. 왼쪽 "MySQL Connections"에서 연결 선택
   - Host: [config.py의 host]
   - Port: 3307
   - Username: stox7412
   - Password: Apt106503!~
   - Default Schema: investar

## 2단계: SQL 파일 열기

1. 상단 메뉴: **File → Open SQL Script...**
2. 다운로드한 파일 선택:
   ```
   industry_indicators_schema_18industries.sql
   ```

## 3단계: SQL 실행

방법 A - 전체 실행:
1. 상단 툴바에서 번개 아이콘(⚡) 클릭
2. 또는 Ctrl + Shift + Enter

방법 B - 부분 실행:
1. 실행하고 싶은 SQL 블록 선택 (드래그)
2. 번개 아이콘(⚡) 클릭
3. 또는 Ctrl + Enter

## 4단계: 결과 확인

실행 결과 확인:
```
- 녹색 체크: 성공 ✅
- 빨간 X: 오류 ❌
```

테이블 생성 확인:
1. 왼쪽 "Schemas" 패널에서 "investar" 우클릭
2. "Refresh All" 선택
3. "Tables" 폴더 펼치기
4. 다음 테이블들이 보여야 함:
   - industry_indicators
   - industry_metadata
   - indicator_metadata
   - entity_metadata

## 5단계: 데이터 확인

```sql
-- 업종 메타데이터 확인
SELECT * FROM industry_metadata;

-- 18개 업종이 보여야 함
```

## 문제 해결

### 오류 1: "Table already exists"
```sql
-- 기존 테이블 삭제 후 재생성
DROP TABLE IF EXISTS industry_indicators;
DROP TABLE IF EXISTS industry_metadata;
DROP TABLE IF EXISTS indicator_metadata;
DROP TABLE IF EXISTS entity_metadata;

-- 그 다음 스키마 파일 다시 실행
```

### 오류 2: "Access denied"
- Username, Password 확인
- DB 접근 권한 확인

### 오류 3: "Unknown database 'investar'"
```sql
-- DB 생성
CREATE DATABASE IF NOT EXISTS investar 
CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE investar;

-- 그 다음 스키마 파일 실행
```
