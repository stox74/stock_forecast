# -*- coding: utf-8 -*-
"""
수정된 버전 - 일별 데이터 자동 월말 리샘플링
"""
import pandas as pd
from simple_transform import long_to_wide, extract_series, get_mapping_table

print("=" * 80)
print("예시: 일별 데이터 자동 월말 변환")
print("=" * 80)

# CSV 로드
df_long = pd.read_csv("air_tourism_long_V3.csv")
print(f"Long format: {len(df_long):,}행 로드")

# ==========================================
# 방법 1: 전체 Wide Format 변환 (기본 - 자동 리샘플링)
# ==========================================
print("\n[방법 1] 전체 변환 - 일별 데이터 자동 월말 변환")
df_wide = long_to_wide(df_long)  # resample_daily_to_monthly=True (기본값)

print(f"\nShape: {df_wide.shape}")
print(f"\n컬럼 목록:")
for i, col in enumerate(df_wide.columns, 1):
    print(f"  {i:2d}. {col}")

print(f"\n최근 10개월:")
print(df_wide.tail(10))

# CSV 저장
df_wide.to_csv("air_tourism_wide.csv", encoding='utf-8-sig')
print(f"\n✅ air_tourism_wide.csv 저장 완료")


# ==========================================
# 방법 2: 리샘플링 하지 않고 원본 그대로 (옵션)
# ==========================================
print("\n" + "=" * 80)
print("[방법 2] 리샘플링 없이 원본 데이터 그대로 사용")

df_wide_raw = long_to_wide(df_long, resample_daily_to_monthly=False)
print(f"\nShape: {df_wide_raw.shape}")
print(f"날짜 개수: {len(df_wide_raw):,}개 (일별 포함)")


# ==========================================
# 방법 3: 특정 시계열만 추출
# ==========================================
print("\n" + "=" * 80)
print("[방법 3] 특정 시계열 추출")

# 3-1. 국제유가 (자동 월말 변환)
print("\n[WTI 유가 - 월말 기준]")
oil_wti = extract_series(df_long, 'oil_wti', 'FDR')  # resample_to_monthly=True (기본값)
print(f"최근 10개월:\n{oil_wti.tail(10)}\n")

# 3-2. 환율 (자동 월말 변환)
print("[USD/KRW 환율 - 월말 기준]")
usdkrw = extract_series(df_long, 'usdkrw', 'FDR')
print(f"최근 10개월:\n{usdkrw.tail(10)}\n")

# 3-3. 브렌트유 (자동 월말 변환)
print("[브렌트유 - 월말 기준]")
oil_brent = extract_series(df_long, 'oil_brent', 'FDR')
print(f"최근 10개월:\n{oil_brent.tail(10)}\n")

# 3-4. 항공사 데이터 (원래 월별)
print("[대한항공 여객수 - 인천공항]")
ke_pax = extract_series(df_long, 'icn_airline_pax_passenger', 'ODP_B551177', 'KE')
print(f"최근 10개월:\n{ke_pax.tail(10)}\n")


# ==========================================
# 방법 4: 선택한 지표만 모아서 DataFrame
# ==========================================
print("\n" + "=" * 80)
print("[방법 4] 경제 지표 + 항공사 데이터 조합")

df_selected = pd.DataFrame({
    'WTI유가': extract_series(df_long, 'oil_wti', 'FDR'),
    '브렌트유': extract_series(df_long, 'oil_brent', 'FDR'),
    '환율': extract_series(df_long, 'usdkrw', 'FDR'),
    '여행비CSI': extract_series(df_long, 'csi_travel_spending_expectation_total', 'BOK_ECOS'),
    '대한항공': extract_series(df_long, 'icn_airline_pax_passenger', 'ODP_B551177', 'KE'),
    '아시아나': extract_series(df_long, 'icn_airline_pax_passenger', 'ODP_B551177', 'OZ'),
    '제주항공': extract_series(df_long, 'icn_airline_pax_passenger', 'ODP_B551177', '7C'),
})

print(f"\nShape: {df_selected.shape}")
print(f"\n최근 12개월:")
print(df_selected.tail(12))

# 저장
df_selected.to_csv("air_tourism_selected.csv", encoding='utf-8-sig')
print(f"\n✅ air_tourism_selected.csv 저장 완료")


# ==========================================
# 방법 5: 일별 데이터를 원본 그대로 유지하고 싶을 때
# ==========================================
print("\n" + "=" * 80)
print("[방법 5] 일별 데이터 원본 그대로 추출")

# resample_to_monthly=False 옵션 사용
oil_wti_daily = extract_series(df_long, 'oil_wti', 'FDR', resample_to_monthly=False)
print(f"\n일별 WTI 유가:")
print(f"총 {len(oil_wti_daily):,}개 (일별)")
print(f"최근 10일:\n{oil_wti_daily.tail(10)}")


# ==========================================
# 방법 6: 결측치 처리
# ==========================================
print("\n" + "=" * 80)
print("[방법 6] 결측치 확인 및 처리")

print(f"\n결측치 개수:")
null_counts = df_wide.isnull().sum()
for col in df_wide.columns:
    count = null_counts[col]
    if count > 0:
        pct = count / len(df_wide) * 100
        print(f"  {col}: {count}개 ({pct:.1f}%)")

# 선형 보간
df_filled = df_wide.interpolate(method='linear')
print(f"\n선형 보간 후 결측치: {df_filled.isnull().sum().sum()}개")

# 이전값으로 채우기
df_ffill = df_wide.fillna(method='ffill')
print(f"이전값 채우기 후 결측치: {df_ffill.isnull().sum().sum()}개")


# ==========================================
# 방법 7: 특정 기간만 필터링
# ==========================================
print("\n" + "=" * 80)
print("[방법 7] 특정 기간 데이터만 추출")

# 2020년 이후
df_2020 = df_wide[df_wide.index >= '2020-01-01']
print(f"\n2020년 이후: {len(df_2020):,}개월")

# 특정 기간 (2022-2024)
df_range = df_wide[(df_wide.index >= '2022-01-01') & (df_wide.index < '2025-01-01')]
print(f"2022-2024년: {len(df_range):,}개월")


# ==========================================
# 방법 8: 매핑 테이블 확인 및 수정
# ==========================================
print("\n" + "=" * 80)
print("[방법 8] 매핑 테이블 확인")

mapping = get_mapping_table()
print(f"\n전체 매핑 ({len(mapping)}개):")
print(mapping)

# FDR 데이터 확인
print(f"\nFDR 데이터 (일별 → 월말 변환):")
print(mapping[mapping['source'] == 'FDR'])


print("\n" + "=" * 80)
print("✅ 모든 예시 완료!")
print("\n생성된 파일:")
print("  - air_tourism_wide.csv (전체 데이터, 월말 기준)")
print("  - air_tourism_selected.csv (선택 지표)")
print("=" * 80)
