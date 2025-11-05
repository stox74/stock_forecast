# 🇺🇸 미국 수입 데이터 수집 시스템 - 전체 요약

## 📦 생성된 파일 목록

### 1. 핵심 파일
| 파일명 | 용도 | 설명 |
|--------|------|------|
| `us_top_import_hs_codes.py` | HS 코드 모듈 | 미국 수입 상위 500개 HS 코드 리스트 |
| `us_import_data_downloader_fast.py` | 메인 실행 파일 | 수입 데이터 다운로드 및 DB 업로드 |
| `test_import_downloader.py` | 테스트 스크립트 | 소규모 테스트용 (3개 HS코드, 3개월) |

### 2. 문서 파일
| 파일명 | 용도 |
|--------|------|
| `README_import_downloader.md` | 사용 설명서 |
| `COMPARISON_export_vs_import.md` | 수출/수입 코드 비교 |
| `example_usage.py` | HS 코드 모듈 사용 예제 |

## 🚀 빠른 시작 가이드

### Step 1: 파일 준비
다운로드한 파일을 같은 폴더에 배치:
```
your_project/
├── us_import_data_downloader_fast.py  ⭐ 메인
├── us_top_import_hs_codes.py          ⭐ 필수
└── test_import_downloader.py           (선택)
```

### Step 2: 패키지 설치
```bash
pip install pandas numpy requests tqdm sqlalchemy pymysql
```

### Step 3: 테스트 실행 (권장)
```bash
python test_import_downloader.py
```
- 3개 HS 코드 × 3개월 = 9개 요청만 수행
- API 연결 확인
- 약 10초 소요

### Step 4: 전체 실행
```bash
python us_import_data_downloader_fast.py
```
- 500개 HS 코드 × 151개월 = 75,500개 요청
- 약 20-30분 소요

## ⚙️ 주요 설정

### 1. API 키 (필수)
```python
api_key = '여기에_본인의_API_키'  # 반드시 변경!
```

### 2. DB 정보 (필수)
```python
db_info = {
    'host': '192.168.0.230',
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}
```

### 3. 데이터 수집 기간
```python
start='2013-01'  # 시작
end='2025-07'    # 종료
```

### 4. 병렬 처리 속도
```python
max_workers=50  # 30-100 사이 권장
```

## 📊 DB 테이블 정보

### 테이블명: `us_trade_import_data`

```sql
CREATE TABLE us_trade_import_data (
    date DATETIME,        -- 날짜 (월말 기준)
    impDlr NUMERIC,       -- 수입 금액 (달러)
    year INT,             -- 연도
    month INT,            -- 월
    hs_code VARCHAR(10),  -- HS 코드 (6자리)
    quarter VARCHAR(10)   -- 분기 정보 (예: 2013Q1)
);
```

### 특징
- 테이블이 없으면 자동 생성
- 기존 테이블은 **삭제 후 재생성** (replace 모드)
- 약 75,000개 레코드 예상

## 🔧 주요 변경 사항 (수출 → 수입)

| 항목 | 수출 (Export) | 수입 (Import) |
|------|--------------|--------------|
| API URL | `/exports/hs` | `/imports/hs` |
| API 파라미터 | `E_COMMODITY` | `I_COMMODITY` |
| 데이터 컬럼 | `expDlr` | `impDlr` |
| DB 테이블 | `us_trade_data` | `us_trade_import_data` |
| HS 코드 소스 | 엑셀 파일 | Python 모듈 |

## 📈 예상 성능

### 데이터 규모
- **HS 코드**: 500개
- **기간**: 2013.01 ~ 2025.07 (151개월)
- **총 요청 수**: 75,500개
- **예상 데이터량**: ~75,000 레코드

### 소요 시간 (max_workers 기준)
| 설정 | 소요 시간 | 안정성 |
|------|----------|--------|
| 30 | 35-45분 | ⭐⭐⭐⭐⭐ |
| 50 | 20-30분 | ⭐⭐⭐⭐ |
| 100 | 10-15분 | ⭐⭐⭐ |

## 🎯 사용 시나리오

### 시나리오 1: 최초 실행
```bash
# 1. 테스트
python test_import_downloader.py

# 2. 전체 실행
python us_import_data_downloader_fast.py
```

### 시나리오 2: 데이터 업데이트
```python
# us_import_data_downloader_fast.py 수정
start='2025-08'  # 새로운 데이터만
end='2025-12'
```

### 시나리오 3: 특정 HS 코드만 수집
```python
# us_import_data_downloader_fast.py 수정
from us_top_import_hs_codes import get_hs_codes

all_hs_codes = get_hs_codes()
hs_code = all_hs_codes[:50]  # 처음 50개만
```

## 🔍 문제 해결

### ❌ "ModuleNotFoundError: No module named 'us_top_import_hs_codes'"
**원인**: `us_top_import_hs_codes.py` 파일이 같은 폴더에 없음  
**해결**: 두 파일을 같은 폴더에 배치

### ❌ "가져온 데이터가 없습니다"
**원인**: API 키 오류 또는 네트워크 문제  
**해결**:
1. API 키 확인
2. `test_import_downloader.py` 실행하여 연결 테스트
3. 방화벽 설정 확인

### ❌ "DB 업로드 실패"
**원인**: DB 접속 정보 오류  
**해결**:
1. host, port, user, password 확인
2. DB 서버 실행 여부 확인
3. 방화벽 설정 확인

### ❌ "API 속도 제한"
**원인**: 너무 많은 동시 요청  
**해결**: `max_workers` 값을 낮춤 (30-40)

## 💡 활용 아이디어

### 1. 무역수지 분석
```python
# us_trade_data (수출) + us_trade_import_data (수입)
trade_balance = export_value - import_value
```

### 2. 품목별 수입 동향
```python
# 특정 HS 코드의 시계열 분석
df[df['hs_code'] == '270900'].plot(x='date', y='impDlr')
```

### 3. 분기별 집계
```python
# 이미 quarter 컬럼 포함됨
df.groupby(['quarter', 'hs_code'])['impDlr'].sum()
```

### 4. 대시보드 구축
- Streamlit, Dash 등으로 시각화
- 실시간 모니터링
- 트렌드 예측

## 📚 참고 자료

### API 문서
- US Census Bureau API: https://www.census.gov/data/developers/data-sets/international-trade.html
- API Key 발급: https://api.census.gov/data/key_signup.html

### HS Code 정보
- USITC HTS: https://hts.usitc.gov/
- World Customs Organization: https://www.wcoomd.org/

### 기술 문서
- pandas: https://pandas.pydata.org/
- SQLAlchemy: https://www.sqlalchemy.org/
- requests: https://requests.readthedocs.io/

## ⚠️ 주의사항

1. **API 키 보안**
   - API 키를 절대 공개하지 마세요
   - GitHub 등에 업로드 시 주의
   - 환경 변수 사용 권장

2. **DB 비밀번호**
   - 코드에 직접 입력하지 말고
   - 별도 설정 파일 또는 환경 변수 사용

3. **데이터 백업**
   - replace 모드는 기존 데이터 삭제
   - 중요 데이터는 먼저 백업

4. **API 제한**
   - 과도한 요청 지양
   - max_workers 적절히 조절

## ✅ 체크리스트

실행 전 확인:
- [ ] `us_top_import_hs_codes.py` 파일 준비
- [ ] API 키 설정
- [ ] DB 접속 정보 확인
- [ ] 필요한 패키지 설치
- [ ] 네트워크 연결 확인
- [ ] 테스트 스크립트 실행
- [ ] 충분한 디스크 공간 확보

## 🎓 다음 단계

1. ✅ 수입 데이터 수집 완료
2. ⬜ 수출 데이터와 결합
3. ⬜ 무역수지 계산
4. ⬜ 시계열 분석
5. ⬜ 예측 모델 구축
6. ⬜ 대시보드 개발
7. ⬜ 정기 자동 실행 설정

## 📞 지원

문제가 발생하면:
1. 먼저 `test_import_downloader.py` 실행
2. 에러 메시지 확인
3. 문제 해결 섹션 참고
4. API 문서 확인

---

**작성일**: 2025.11.05  
**버전**: 1.0  
**Python**: 3.7+  
**필수 패키지**: pandas, numpy, requests, tqdm, sqlalchemy, pymysql
