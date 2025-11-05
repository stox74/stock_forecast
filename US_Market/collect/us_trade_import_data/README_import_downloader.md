# 미국 수입 데이터 다운로더 - 사용 설명서

## 📋 개요
이 프로그램은 US Census Bureau API를 통해 미국의 HS 코드별 수입 데이터를 수집하고 MySQL DB에 저장합니다.

## 🔧 필요한 파일
1. **us_import_data_downloader_fast.py** - 메인 실행 파일
2. **us_top_import_hs_codes.py** - HS 코드 리스트 모듈 (같은 폴더에 위치)

## 📦 필요한 패키지 설치
```bash
pip install pandas numpy requests tqdm sqlalchemy pymysql openpyxl
```

## 🚀 실행 방법

### 1. 파일 배치
두 파일을 같은 폴더에 위치시킵니다:
```
your_folder/
├── us_import_data_downloader_fast.py
└── us_top_import_hs_codes.py
```

### 2. 실행
```bash
python us_import_data_downloader_fast.py
```

## ⚙️ 설정 변경

### API 키 변경
```python
api_key = '여기에_본인의_API_키_입력'
```

### 데이터 수집 기간 변경
```python
us_import_month, us_import_quarter = get_us_import_data_parallel(
    hs_list=hs_code,
    start='2013-01',  # 시작 날짜
    end='2025-07',    # 종료 날짜
    api_key=api_key,
    max_workers=50    # 동시 실행 스레드 수
)
```

### 병렬 처리 속도 조정
`max_workers` 값을 조정하여 속도를 조절할 수 있습니다:
- **30-50**: 안정적인 속도 (권장)
- **50-100**: 빠른 속도 (API 제한 주의)
- **100+**: 매우 빠르지만 API 제한에 걸릴 수 있음

### DB 정보 변경
```python
db_info = {
    'host': '192.168.0.230',      # DB 호스트
    'port': 3307,                  # DB 포트
    'user': 'stox7412',            # DB 사용자명
    'password': 'Apt106503!~',     # DB 비밀번호
    'database': 'investar'         # DB 이름
}
```

## 📊 DB 테이블 구조

### 테이블명: `us_trade_import_data`

| 컬럼명 | 데이터 타입 | 설명 |
|--------|------------|------|
| date | datetime | 날짜 (월말 기준) |
| impDlr | numeric | 수입 금액 (달러) |
| year | int | 연도 |
| month | int | 월 |
| hs_code | varchar | HS 코드 (6자리) |
| quarter | varchar | 분기 정보 |

### 테이블 생성
- 테이블이 없으면 자동으로 생성됩니다
- 기존 테이블이 있으면 **삭제 후 새로 생성**됩니다 (replace 모드)

## 🔍 주요 기능

### 1. HS 코드 자동 불러오기
`us_top_import_hs_codes.py` 모듈에서 500개의 HS 코드를 자동으로 불러옵니다.

### 2. 병렬 처리
여러 스레드를 사용하여 빠르게 데이터를 수집합니다.

### 3. 자동 재시도
네트워크 오류나 일시적인 API 장애 시 자동으로 재시도합니다.

### 4. 진행 상황 표시
tqdm 라이브러리를 사용하여 진행 상황을 실시간으로 표시합니다.

### 5. 데이터 전처리
- 이상치 자동 제거
- 날짜 형식 통일
- 분기 정보 자동 생성

### 6. 월별 + 분기별 데이터
- `df_monthly`: 월별 수입 데이터
- `df_quarterly`: 분기별 합계 데이터

## 🐛 문제 해결

### "가져온 데이터가 없습니다" 오류
1. API 키가 올바른지 확인
2. 네트워크 연결 확인
3. US Census API가 정상 작동하는지 확인

### DB 연결 오류
1. DB 접속 정보 (host, port, user, password) 확인
2. 방화벽 설정 확인
3. pymysql 패키지 설치 여부 확인

### API 속도 제한 오류
1. `max_workers` 값을 낮춤 (30-40 정도)
2. 요청 사이에 지연 시간 추가

## 📈 성능 예상

- **500개 HS 코드 × 151개월 (2013-2025) = 약 75,500개 요청**
- **max_workers=50 기준**: 약 20-30분 소요
- **max_workers=100 기준**: 약 10-15분 소요 (API 제한 주의)

## 💡 팁

### 테스트 실행
처음에는 작은 범위로 테스트해보세요:
```python
# HS 코드 5개만 사용
hs_code_test = hs_code[:5]

# 짧은 기간 설정
us_import_month, us_import_quarter = get_us_import_data_parallel(
    hs_list=hs_code_test,
    start='2025-01',
    end='2025-03',
    api_key=api_key,
    max_workers=10
)
```

### 데이터 저장
DB에 업로드하기 전에 CSV로 백업:
```python
if us_import_month is not None:
    us_import_month.to_csv('us_import_backup.csv')
```

## 📞 참고 자료
- US Census API: https://www.census.gov/data/developers/data-sets/international-trade.html
- HS Code 정보: https://hts.usitc.gov/

## ⚠️ 주의사항
1. API 키는 절대 공개하지 마세요
2. 과도한 요청은 API 제한에 걸릴 수 있습니다
3. DB 비밀번호는 환경 변수로 관리하는 것을 권장합니다
4. replace 모드는 기존 데이터를 삭제하므로 주의하세요
