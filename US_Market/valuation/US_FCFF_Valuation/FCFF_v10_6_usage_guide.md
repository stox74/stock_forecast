# FCFF DCF v10.6 사용 가이드 — Damodaran ERP + Blume β

## 변경 요약

| 항목 | v10.5 (이전) | v10.6 (변경) |
|---|---|---|
| Rf 출처 | 하드코딩 4.5% | DB `damodaran_erp_data` 최신 row |
| Rm 출처 | 하드코딩 10% | Rf + ERP (Damodaran) |
| Re default | Re_smooth (DB) | CAPM with Blume β |
| Beta default | Implied β (= (Re_smooth − Rf) / ERP) | Blume β (= 0.67 × OLS + 0.33 × 1.0) |
| Re_smooth | 메인 사용 | 보조 사용 (Section A2 비교) |

## 작업 순서

### Step 1: DB 테이블 생성 + Historical 데이터 입력 (15분)

`damodaran_erp_data_setup.sql` 실행:

```sql
-- 1. 테이블 생성
CREATE TABLE IF NOT EXISTS damodaran_erp_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL,
    rf DECIMAL(8,5) NOT NULL,
    erp DECIMAL(8,5) NOT NULL,
    rm DECIMAL(8,5) AS (rf + erp) STORED,
    source VARCHAR(50) DEFAULT 'Damodaran',
    note VARCHAR(200) DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_date (date),
    INDEX idx_date_desc (date DESC)
) ENGINE=InnoDB;

-- 2. Historical 데이터 (2021-01 ~ 2026-04)
-- SQL 파일의 INSERT 구문 실행
```

### Step 2: 노트북 v10.6 으로 교체 (1분)

`FCFF_DCF_Valuation_v11_with_excel_v10_5.ipynb` 를 `FCFF_DCF_Valuation_v11_with_excel_v10_6.ipynb` 로 교체.

### Step 3: 단일 ticker 검증 (10분) — 권장

PyCharm 또는 Jupyter 에서 v10.6 노트북 열고:

1. **Cell 1 ~ Cell 4.10** 순서대로 실행
2. **Cell 5.0 (단일 Ticker)** 에서 `TEST_TICKER = "APH"` 로 실행
3. 콘솔 로그 확인:
   ```
   [APH] Re 계산 [ols_blume]: Re=0.XXXX  source=CAPM(Blume β=1.XXXX, ERP=0.04XX)
   [APH]   CAPM Re=0.XXXX (β=1.XX)  vs  Re_smooth=0.1415 (implied β=1.75)  gap=+XXX bps
   ```
4. 생성된 Excel 파일 (`APH_FCFF_v10format.xlsx`) 의 `Cost_of_Capital` 시트 확인:
   - Section A: Rf, ERP 가 Damodaran 값으로 표시
   - Section A2: Re_smooth 와의 비교 표시
   - Section D: WACC 계산 정상

### Step 4: 배치 실행 (선택, 1-2시간)

전체 ticker 또는 일부 구간으로 v10.6 노트북 Cell 5 실행. 모든 valuation 이 새 Re 방식 사용.

## Re method 변경 방법

### 방법 1: 모듈 상수 (전체 default 변경)

Cell 4.10 코드 안에서:
```python
RE_METHOD = "ols_blume"   # → "re_smooth" 또는 "average" 로 변경
```

### 방법 2: instance 별 override (한 ticker 만 다르게)

```python
model = DCFModel("APH", engine, verbose=True)
model.re_method = "re_smooth"   # 이 ticker 만 Re_smooth 사용
model.run()
model.export_v10_excel(out_dir="C:/reports/")
```

### 방법 3: 비교 분석 (3가지 방법 모두 실행)

```python
results = {}
for method in ["ols_blume", "re_smooth", "average"]:
    model = DCFModel("APH", engine)
    model.re_method = method
    model.run()
    results[method] = model.valuation
    
# 비교
for m, v in results.items():
    print(f"{m}: WACC={v['wacc']:.4f}  target_price={v['target_price']:.2f}")
```

## 매월 ERP 갱신 절차

1. [Damodaran 사이트](https://pages.stern.nyu.edu/~adamodar/) 방문
2. "Implied ERP" historical csv 다운로드 (또는 메인 페이지의 최신 값 확인)
3. 가장 최근 월의 Rf, ERP 값 확인
4. SQL 실행:
   ```sql
   INSERT INTO damodaran_erp_data (date, rf, erp, source, note) VALUES
   ('2026-05-01', 0.0XXX, 0.0XXX, 'Damodaran', '2026년 5월 발표');
   ```

DCFModel 이 자동으로 가장 최신 row 사용. 코드 수정 불필요.

## 검증 방법

### 1. DB 테이블 확인

```sql
-- 가장 최신 ERP/Rf
SELECT * FROM damodaran_erp_data ORDER BY date DESC LIMIT 5;

-- 전체 historical 시계열
SELECT date, rf, erp, rm FROM damodaran_erp_data ORDER BY date;
```

### 2. APH 의 Section A 검증

이전 (v10.5):
```
Rf  : 4.50%  (하드코딩)
Rm  : 10.00% (하드코딩)
ERP : 5.50%
β (raw)     : 1.2757
β (implied) : 1.7545  ← 사용
Re          : 14.15%  ← Re_smooth 사용
```

v10.6 (예상):
```
Rf  : 4.55% (Damodaran 2026-04)
ERP : 4.40% (Damodaran 2026-04)
Rm  : 8.95%
β (raw)   : 1.2757 (Beta_Regression 시트)
β (Blume) : 1.1847  ← 사용
Re        : 4.55% + 1.1847 × 4.40% = 9.76%

Section A2 (참고용):
Re_smooth   : 14.15%
Implied β   : (14.15 − 4.55) / 4.40 = 2.18
Δ           : +439 bps
```

### 3. Cost_of_Capital 시트 D60-66 확인

- WACC 가 v10.5 와 다른 값이어야 (Re 변경 영향)
- Δ verification 이 ≈ 0 이어야 (Excel WACC = Model WACC)

## 트러블슈팅

### "ERP DB 조회 실패" 메시지

원인: `damodaran_erp_data` 테이블이 없거나 비어있음

해결:
1. SQL 실행 확인: `SHOW TABLES LIKE 'damodaran_erp_data';`
2. row 확인: `SELECT COUNT(*) FROM damodaran_erp_data;`
3. 비어있으면 `damodaran_erp_data_setup.sql` 의 INSERT 부분 재실행

Fallback: DB 조회 실패 시 모듈 상수 (`_RF_FALLBACK = 0.0455`, `_ERP_FALLBACK = 0.0440`) 자동 사용 — valuation 진행은 계속됨.

### Beta_Regression 시트의 Blume β 와 Cost_of_Capital 의 Blume β 가 다름

**원인**: 
- Beta_Regression 시트: Excel SLOPE 함수로 실시간 계산 (정확)
- Cost_of_Capital 시트: Python `_compute_beta_blume` 가 미리 계산한 값 (수치 동일해야 함)

**확인**:
```python
model = DCFModel("APH", engine, verbose=True)
model.run()
print(model._wacc_cache.get('beta_blume'))   # Cost_of_Capital 의 값
# Beta_Regression 시트의 Blume 셀 (B23) 과 비교
```

작은 부동소수점 차이 (4번째 자리 이하) 는 정상.

### Re_smooth 가 N/A 로 표시

원인: 해당 ticker 의 `us_rim_spread_data` 에 Re_smooth 데이터 없음

이 경우 v10.6 의 default `re_method="ols_blume"` 는 영향 없음 (CAPM 만 사용). Section A2 의 Re_smooth 비교만 N/A 표시.

## 다음 단계 / Future Work

1. **FRED 자동 연동**: 매월 수동 INSERT 대신 FRED API 로 10Y T.Bond 자동 fetch
2. **ERP 자동 fetch**: Damodaran 사이트의 csv 자동 download (단, 정식 API 없음)
3. **Industry-specific β**: Blume 대신 Damodaran 의 unlevered industry β 활용
4. **Country risk premium**: 다국적 기업 대응 (현재 미국 표준만)

## 참고 자료

- Damodaran 의 Implied ERP: https://pages.stern.nyu.edu/~adamodar/
- Blume (1971): "On the Assessment of Risk", Journal of Finance
- Damodaran (2024): "Equity Risk Premiums", NYU Stern
