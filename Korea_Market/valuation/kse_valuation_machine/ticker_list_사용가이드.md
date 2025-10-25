# 🎯 ticker_list.py 자동 경로 탐색 시스템 가이드

## 개선 사항

기존에는 티커 리스트를 코드에 직접 입력해야 했지만, 이제는:
- ✅ **ticker_list.py 파일로 분리 관리**
- ✅ **자동 경로 탐색** (노트북, 데스크탑 환경 상관없음)
- ✅ **섹터별 그룹 관리** 가능
- ✅ **간편한 티커 추가/수정**

---

## 📁 파일 구조

### 1단계: ticker_list.py 파일 생성

다음 경로 중 **하나**에 `ticker_list.py` 파일을 저장하세요:

```
권장 경로 (우선순위):
1. C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\DATA\ticker_list.py
2. C:\Users\82108\OneDrive\바탕 화면\investment\stock_forecast\DATA\ticker_list.py
3. 현재 작업 폴더\ticker_list.py
```

**자동 탐색 순서:**
1. `stock_forecast/DATA/ticker_list.py` 
2. `stock_forecast/ticker_list.py`
3. `현재경로/DATA/ticker_list.py`
4. `현재경로/../ticker_list.py`
5. `현재경로/ticker_list.py`

→ **어느 위치에 두어도 자동으로 찾아서 import합니다!**

---

## 📝 ticker_list.py 파일 내용

제공된 `ticker_list.py` 파일을 DATA 폴더에 저장하세요.

### 기본 구조

```python
# 전체 티커 리스트
ALL_TICKERS = [
    {'ticker': 'A005930', 'hs_code': '8542'},  # 삼성전자 - 반도체
    {'ticker': 'A000660', 'hs_code': None},     # SK하이닉스
    # ... 더 많은 티커 추가
]

# 테스트용 소수 티커
TEST_TICKERS = [
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
]

# 섹터별 그룹
SEMICONDUCTOR_TICKERS = [...]  # 반도체
IT_TICKERS = [...]              # IT
AUTO_TICKERS = [...]            # 자동차
BIO_TICKERS = [...]             # 바이오
CHEMICAL_TICKERS = [...]        # 화학
```

---

## 🚀 사용 방법

### 방법 1: 자동 로드 (권장)

```python
from improved_forecast_system_v2 import process_multiple_tickers, load_ticker_list

# DB 정보
db_info = {
    'host': get_db_host(),
    'port': 3307,
    'user': 'stox7412',
    'password': 'Apt106503!~',
    'database': 'investar'
}

# ticker_list.py에서 자동으로 로드
ticker_list = load_ticker_list('all')  # 전체 티커
# ticker_list = load_ticker_list('test')  # 테스트용
# ticker_list = load_ticker_list('semiconductor')  # 반도체만

# 실행
results = process_multiple_tickers(ticker_list, db_info)
```

### 방법 2: 직접 실행 (메인 스크립트)

```bash
python improved_forecast_system_v2.py
```

실행하면 자동으로 메뉴가 나타납니다:

```
티커 리스트 로딩 옵션:
1. 전체 티커 (all)
2. 테스트용 (test)
3. 반도체 섹터 (semiconductor)
4. IT 섹터 (it)
5. 자동차 섹터 (auto)
6. 바이오 섹터 (bio)
7. 화학 섹터 (chemical)
8. 수동 입력

선택 (1-8): 2

✅ 'test' 그룹에서 3개 티커 로드됨
3개 티커를 처리하시겠습니까? (y/n): y
```

---

## 📋 ticker_list.py 편집 방법

### 1. 새 티커 추가

```python
# ticker_list.py 파일 열기

ALL_TICKERS = [
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
    
    # 새 티커 추가
    {'ticker': 'A035720', 'hs_code': None},  # 카카오
    {'ticker': 'A051910', 'hs_code': '3901'},  # LG화학
]
```

### 2. 새 섹터 그룹 만들기

```python
# ticker_list.py에 추가

# 내가 관심있는 종목들
MY_WATCHLIST = [
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A035420', 'hs_code': None},
    {'ticker': 'A051910', 'hs_code': None'},
]
```

그리고 `get_ticker_list()` 함수에 추가:

```python
def get_ticker_list(group='all'):
    groups = {
        'all': ALL_TICKERS,
        'test': TEST_TICKERS,
        'semiconductor': SEMICONDUCTOR_TICKERS,
        'it': IT_TICKERS,
        'auto': AUTO_TICKERS,
        'bio': BIO_TICKERS,
        'chemical': CHEMICAL_TICKERS,
        'watchlist': MY_WATCHLIST,  # 추가!
    }
    # ...
```

### 3. HS Code 추가/수정

```python
# 반도체 HS Code 예시
{'ticker': 'A005930', 'hs_code': '8542'},  # 8542: 전자집적회로

# HS Code 없는 경우
{'ticker': 'A000660', 'hs_code': None},

# 새 HS Code 추가
{'ticker': 'A051910', 'hs_code': '3901'},  # 3901: 플라스틱
```

---

## 🔍 자동 경로 탐색 확인

프로그램 실행 시 다음과 같이 출력됩니다:

```
============================================================
경로 탐색 및 모듈 로딩 중...
============================================================
✅ stock_forecast 경로 발견: C:\Users\...\stock_forecast
✅ ticker_list.py 발견: C:\Users\...\DATA\ticker_list.py
✅ ticker_list 모듈 import 성공
```

### 경로를 찾지 못한 경우

```
⚠️  ticker_list.py를 찾을 수 없습니다.
다음 위치에 ticker_list.py 파일을 생성하세요:
   C:\Users\82108\OneDrive\바탕 화면\investment\stock_forecast\DATA\ticker_list.py
```

→ 표시된 경로에 `ticker_list.py` 파일을 복사하세요.

---

## 💡 사용 예시

### 예시 1: 전체 티커 처리

```python
from improved_forecast_system_v2 import process_multiple_tickers, load_ticker_list

# 전체 티커 로드
ticker_list = load_ticker_list('all')
print(f"총 {len(ticker_list)}개 티커")

# 실행
results = process_multiple_tickers(ticker_list, db_info)
```

### 예시 2: 테스트 실행

```python
# 소수 티커로 먼저 테스트
ticker_list = load_ticker_list('test')  # 3개만

results = process_multiple_tickers(ticker_list, db_info)
```

### 예시 3: 특정 섹터만

```python
# 반도체 섹터만
ticker_list = load_ticker_list('semiconductor')

# IT 섹터만
# ticker_list = load_ticker_list('it')

results = process_multiple_tickers(ticker_list, db_info)
```

### 예시 4: 수동 입력 (ticker_list.py 없이)

```python
# ticker_list.py를 사용할 수 없는 경우
ticker_list = [
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
]

results = process_multiple_tickers(ticker_list, db_info)
```

---

## 📊 ticker_list.py 관리 팁

### 1. 버전 관리 (주석 활용)

```python
# ticker_list.py
# 최종 수정: 2025-10-26
# 버전: 2.0
# 총 티커 수: 50개

ALL_TICKERS = [
    # 2025-10-26 추가
    {'ticker': 'A005930', 'hs_code': '8542'},
    
    # 2025-10-20 추가
    {'ticker': 'A000660', 'hs_code': None},
]
```

### 2. 섹터별 정리

```python
ALL_TICKERS = [
    # ===== 반도체 =====
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
    
    # ===== IT =====
    {'ticker': 'A035420', 'hs_code': None},
    {'ticker': 'A035720', 'hs_code': None},
    
    # ===== 자동차 =====
    {'ticker': 'A005380', 'hs_code': None},
]
```

### 3. CSV에서 자동 생성

```python
# csv_to_ticker_list.py (별도 스크립트)
import pandas as pd

df = pd.read_csv('ticker_list.csv')
# CSV: ticker, hs_code

print("ALL_TICKERS = [")
for _, row in df.iterrows():
    hs = f"'{row['hs_code']}'" if pd.notna(row['hs_code']) else 'None'
    print(f"    {{'ticker': '{row['ticker']}', 'hs_code': {hs}}},")
print("]")
```

---

## 🔧 문제 해결

### Q1: "ticker_list.py를 찾을 수 없습니다" 에러

**해결:**
```bash
# 1. DATA 폴더 확인
# 2. ticker_list.py 파일이 있는지 확인
# 3. 파일을 권장 경로에 복사

# Windows 예시:
copy ticker_list.py "C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\DATA\"
```

### Q2: import 에러 발생

**해결:**
```python
# ticker_list.py 문법 오류 확인
python ticker_list.py

# 출력이 정상이면 문법 OK
```

### Q3: 티커 리스트가 비어있음

**해결:**
```python
# ticker_list.py에서 확인
if __name__ == "__main__":
    from ticker_list import get_ticker_list
    tickers = get_ticker_list('all')
    print(f"티커 수: {len(tickers)}")
    print(tickers)
```

### Q4: 노트북과 데스크탑 경로가 다름

**해결:** 자동 탐색 기능이 알아서 처리합니다!
- 노트북: `D:\investment\stock_forecast\DATA\`
- 데스크탑: `C:\Users\...\investment\stock_forecast\DATA\`
- 둘 다 자동으로 찾습니다.

---

## 📝 체크리스트

실행 전 확인:
- [ ] ticker_list.py 파일을 DATA 폴더에 저장
- [ ] 파일에 티커 리스트가 올바르게 입력됨
- [ ] Python으로 ticker_list.py 실행 시 에러 없음
- [ ] improved_forecast_system_v2.py 실행
- [ ] 자동 경로 탐색 성공 메시지 확인

---

## 🎯 장점

### Before (기존 방식)
```python
# 코드에 직접 입력
ticker_list = [
    {'ticker': 'A005930', 'hs_code': '8542'},
    {'ticker': 'A000660', 'hs_code': None},
    # ... 100줄
]

# 티커 추가 시:
# 1. 코드 파일 열기
# 2. 리스트 수정
# 3. 파일 저장
```

### After (개선된 방식)
```python
# 자동으로 로드
ticker_list = load_ticker_list('all')

# 티커 추가 시:
# 1. ticker_list.py만 열기
# 2. 리스트에 한 줄 추가
# 3. 저장 → 즉시 반영!
```

---

## 🌟 추가 기능

### 1. 동적 필터링

```python
# HS Code가 있는 티커만
from ticker_list import get_tickers_by_hs_code
tickers = get_tickers_by_hs_code('8542')

# HS Code가 없는 티커만
from ticker_list import get_tickers_without_hs_code
tickers = get_tickers_without_hs_code()
```

### 2. 런타임 추가

```python
from ticker_list import add_ticker

# 새 티커 추가
new_ticker = add_ticker('A123456', hs_code='1234')
ticker_list.append(new_ticker)
```

---

## 📄 파일 다운로드

제공된 파일:
1. `ticker_list.py` - 티커 리스트 관리 파일
2. `improved_forecast_system_v2.py` - 자동 경로 탐색 기능 추가된 메인 코드

**설치 방법:**
```bash
# 1. ticker_list.py를 DATA 폴더에 복사
# 2. improved_forecast_system_v2.py 실행
# 3. 메뉴에서 원하는 그룹 선택
```

---

## 🎉 완료!

이제 티커 관리가 훨씬 쉬워졌습니다!
- ✅ 티커 추가: ticker_list.py만 수정
- ✅ 섹터별 관리: 그룹 선택만으로
- ✅ 자동 경로 탐색: 어디서든 작동
- ✅ 노트북/데스크탑: 동일한 코드 사용

**Happy Forecasting! 🚀**
