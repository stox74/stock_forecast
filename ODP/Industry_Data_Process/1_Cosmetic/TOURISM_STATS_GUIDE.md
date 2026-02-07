# tourism_stats.py 사용 가이드

## 📊 개요

**tourism_stats.py**는 한국문화관광연구원 API를 사용하여 관광통계 데이터를 수집하고 DB에 저장하는 통합 스크립트입니다.

## 🎯 주요 기능

### 1. 데이터 수집
- **출국자수**: 월별 한국 국민 출국자 수
- **입국자수**: 월별 중국, 일본, 미국 입국자 수

### 2. YoY 증감률 계산
- 12개월 전 대비 증감률 자동 계산
- 공식: `((현재 - 1년전) / 1년전) × 100`

### 3. Long Format 데이터 구조
```
년월    구분  국가   관광객수   yoy_rate
202201  출국  한국   1500000    5.2
202201  입국  중국    234567    -3.5
202201  입국  일본    345678    10.3
202201  입국  미국    123456    2.1
```

### 4. DB 저장
- 테이블명: `tourism_indus_stats`
- DATA.config의 DB 정보 사용
- 기존 데이터 자동 대체 (replace)

## 🚀 실행 방법

### 기본 실행
```bash
python tourism_stats.py
```

### 커스터마이징

파일을 열어서 다음 부분 수정:

```python
# 조회 기간 설정
START_YM = "202201"  # 시작 년월
END_YM = "202412"    # 종료 년월

# API 호출 간격
api = TourismStatsAPI(SERVICE_KEY, delay=2.0)  # 2초
```

## 📋 데이터 구조

### 테이블 스키마 (tourism_indus_stats)

| 컬럼명 | 데이터 타입 | 설명 |
|--------|------------|------|
| 년월 | VARCHAR(6) | YYYYMM 형식 |
| 구분 | VARCHAR(10) | '출국' 또는 '입국' |
| 국가 | VARCHAR(20) | '한국', '중국', '일본', '미국' |
| 관광객수 | INT | 월별 관광객 수 |
| yoy_rate | FLOAT | YoY 증감률 (%) |

### 데이터 예시

```sql
SELECT * FROM tourism_indus_stats 
WHERE 년월 = '202401' 
ORDER BY 구분, 국가;
```

결과:
```
년월    구분  국가   관광객수   yoy_rate
202401  입국  미국    145230    8.5
202401  입국  일본    567890    12.3
202401  입국  중국    234567   -5.2
202401  출국  한국   1890123    6.7
```

## 📊 YoY 증감률 계산

### 계산 방식

```python
# 2024년 1월 출국자수
current = 1,890,123

# 2023년 1월 출국자수 (12개월 전)
prev_year = 1,771,234

# YoY 증감률
yoy_rate = ((current - prev_year) / prev_year) * 100
         = ((1,890,123 - 1,771,234) / 1,771,234) * 100
         = 6.7%
```

### 주의사항

- **첫 12개월**: YoY 값이 `NULL` (비교 데이터 없음)
- **데이터 결측**: 관광객수가 `NULL`이면 YoY도 `NULL`

## 🔧 DB 연결 설정

### config.py 의존성

```python
from DATA.config import get_db_info, get_engine, log
```

**필요한 함수:**
- `get_db_info()`: DB 연결 정보 반환
- `get_engine()`: SQLAlchemy 엔진 생성
- `log()`: 로그 출력

### config.py 예시

```python
def get_db_info():
    return {
        "host": "localhost",
        "port": 3307,
        "user": "stox7412",
        "password": "Apt106503!~",
        "database": "investar",
    }

def get_engine(db_info):
    from sqlalchemy import create_engine
    url = f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    return create_engine(url)

def log(tag, msg):
    print(f"[{tag}] {msg}")
```

## 📁 생성 파일

실행 후 생성되는 파일:

1. **tourism_stats_raw.csv**
   - 중간 저장 파일
   - YoY 계산 전 원본 데이터

2. **tourism_stats_final.csv**
   - 최종 저장 파일
   - YoY 계산 완료 데이터

3. **DB 테이블: tourism_indus_stats**
   - 최종 데이터가 저장되는 테이블

## 🔍 데이터 활용 예시

### SQL 쿼리

```sql
-- 1. 최근 12개월 출국자 추이
SELECT 년월, 관광객수, yoy_rate
FROM tourism_indus_stats
WHERE 구분 = '출국' AND 국가 = '한국'
ORDER BY 년월 DESC
LIMIT 12;

-- 2. 2024년 국가별 입국자 합계
SELECT 국가, SUM(관광객수) as 총입국자수
FROM tourism_indus_stats
WHERE 구분 = '입국' AND 년월 LIKE '2024%'
GROUP BY 국가
ORDER BY 총입국자수 DESC;

-- 3. YoY 증감률이 가장 높은 월 (최근 12개월)
SELECT 년월, 구분, 국가, 관광객수, yoy_rate
FROM tourism_indus_stats
WHERE yoy_rate IS NOT NULL
  AND 년월 >= '202301'
ORDER BY yoy_rate DESC
LIMIT 10;

-- 4. 중국 입국자 추이 (YoY 포함)
SELECT 년월, 관광객수, yoy_rate
FROM tourism_indus_stats
WHERE 구분 = '입국' AND 국가 = '중국'
ORDER BY 년월;
```

### Python 분석

```python
import pandas as pd
from DATA.config import get_engine, get_db_info

# DB 연결
engine = get_engine(get_db_info())

# 데이터 로드
df = pd.read_sql("SELECT * FROM tourism_indus_stats", con=engine)

# 1. 출국자 추이
outbound = df[df['구분'] == '출국'].sort_values('년월')
print(outbound[['년월', '관광객수', 'yoy_rate']])

# 2. 국가별 평균 입국자수
inbound = df[df['구분'] == '입국']
country_avg = inbound.groupby('국가')['관광객수'].mean()
print(country_avg)

# 3. YoY 분석
high_growth = df[df['yoy_rate'] > 10].sort_values('yoy_rate', ascending=False)
print(high_growth[['년월', '구분', '국가', 'yoy_rate']])

# 4. 시각화
import matplotlib.pyplot as plt

pivot = df[df['구분'] == '입국'].pivot(
    index='년월', 
    columns='국가', 
    values='관광객수'
)
pivot.plot(figsize=(12, 6))
plt.title('국가별 입국자수 추이')
plt.show()
```

## ⚠️ 주의사항

### 1. API 호출 제한
- 시간당 호출 제한 있음
- `delay=2.0` 권장 (2초 간격)
- 대량 데이터는 여러 번에 나누어 수집

### 2. 데이터 갱신
- 매월 중순경 최신 데이터 업데이트
- 과거 데이터는 수정되지 않음

### 3. DB 저장
- `if_exists='replace'`: 기존 테이블 전체 대체
- 증분 업데이트 필요 시 코드 수정 필요

### 4. YoY 계산
- 최소 13개월 데이터 필요 (12개월 + 1개월)
- 첫 12개월은 YoY가 NULL

## 🔧 문제 해결

### 오류 1: DB 연결 실패
```python
# config.py 확인
from DATA.config import get_db_info
print(get_db_info())
```

### 오류 2: API 키 오류
```python
# API 키 확인
SERVICE_KEY = "본인의_API_키"
```

### 오류 3: 트래픽 제한
```python
# delay 증가
api = TourismStatsAPI(SERVICE_KEY, delay=3.0)
```

## 📞 지원

문제 발생 시:
1. 로그 확인 (콘솔 출력)
2. CSV 파일 확인 (중간 저장)
3. DB 연결 테스트

---

**작성일**: 2026-02-07  
**버전**: 1.0
