# -*- coding: utf-8 -*-
"""
DataFrame에서 직접 long → wide 변환하는 간단한 유틸리티
DB 연결 없이 DataFrame만으로 작동
"""
import pandas as pd
from typing import Optional


def get_mapping_table() -> pd.DataFrame:
    """
    indicator, source, entity_code와 columns_name의 매핑 테이블 반환
    
    Returns:
    --------
    pd.DataFrame
        매핑 정보 (indicator, source, entity_code, columns_name)
    """
    mapping_data = [
        {"indicator": "oil_wti", "source": "FDR", "entity_code": None, "columns_name": "국제유가_WTI"},
        {"indicator": "oil_brent", "source": "FDR", "entity_code": None, "columns_name": "국제유가_브렌트"},
        {"indicator": "usdkrw", "source": "FDR", "entity_code": None, "columns_name": "환율"},
        {"indicator": "csi_travel_spending_expectation_total", "source": "BOK_ECOS", "entity_code": None, "columns_name": "여행비지출전망_CSI"},
        {"indicator": "icn_airline_pax_passenger", "source": "ODP_B551177", "entity_code": "KE", "columns_name": "대한항공_여객자수_인천공항"},
        {"indicator": "icn_airline_pax_passenger", "source": "ODP_B551177", "entity_code": "OZ", "columns_name": "아시아나_여객자수_인천공항"},
        {"indicator": "icn_airline_pax_passenger", "source": "ODP_B551177", "entity_code": "7C", "columns_name": "제주항공_여객자수_인천공항"},
        {"indicator": "kac_S_FPI", "source": "KAC_AIRPORT", "entity_code": "KAL", "columns_name": "대한항공_여객자수_입국_공항공사"},
        {"indicator": "kac_S_FPO", "source": "KAC_AIRPORT", "entity_code": "KAL", "columns_name": "대한항공_여객자수_출국_공항공사"},
        {"indicator": "kac_S_FPI", "source": "KAC_AIRPORT", "entity_code": "AAR", "columns_name": "아시아나_여객자수_입국_공항공사"},
        {"indicator": "kac_S_FPO", "source": "KAC_AIRPORT", "entity_code": "AAR", "columns_name": "아시아나_여객자수_출국_공항공사"},
        {"indicator": "kac_S_FPI", "source": "KAC_AIRPORT", "entity_code": "JJA", "columns_name": "제주항공_여객자수_입국_공항공사"},
        {"indicator": "kac_S_FPO", "source": "KAC_AIRPORT", "entity_code": "JJA", "columns_name": "제주항공_여객자수_출국_공항공사"},
        {"indicator": "airportal_공급(석)", "source": "AIRPORTAL", "entity_code": "KAL", "columns_name": "대한항공_공급석"},
        {"indicator": "airportal_공급(석)", "source": "AIRPORTAL", "entity_code": "AAR", "columns_name": "아시아나_공급석"},
        {"indicator": "airportal_공급(석)", "source": "AIRPORTAL", "entity_code": "JJA", "columns_name": "제주항공_공급석"},
        {"indicator": "airportal_화물(톤)", "source": "AIRPORTAL", "entity_code": "KAL", "columns_name": "대한항공_화물_톤"},
        {"indicator": "airportal_화물(톤)", "source": "AIRPORTAL", "entity_code": "AAR", "columns_name": "아시아나_화물_톤"},
        {"indicator": "airportal_화물(톤)", "source": "AIRPORTAL", "entity_code": "JJA", "columns_name": "제주항공_화물_톤"},
    ]
    
    return pd.DataFrame(mapping_data)


def long_to_wide(
    df_long: pd.DataFrame, 
    mapping_table: Optional[pd.DataFrame] = None,
    resample_daily_to_monthly: bool = True
) -> pd.DataFrame:
    """
    Long format 데이터를 Wide format으로 변환
    
    Parameters:
    -----------
    df_long : pd.DataFrame
        Long format 데이터
        필수 컬럼: date, indicator, source, entity_code, value
    mapping_table : pd.DataFrame, optional
        매핑 테이블. None이면 기본 매핑 테이블 사용
        필수 컬럼: indicator, source, entity_code, columns_name
    resample_daily_to_monthly : bool, default=True
        일별 데이터(FDR 데이터 등)를 월말 기준으로 리샘플링할지 여부
        True: 일별 데이터를 월말 값으로 변환하여 월별 데이터와 주기 일치
        False: 원본 데이터 그대로 사용
    
    Returns:
    --------
    pd.DataFrame
        Wide format 데이터
        - index: date (월말 기준)
        - columns: columns_name (매핑 테이블 기준)
        - values: value
    
    Examples:
    ---------
    >>> import pandas as pd
    >>> df_long = pd.read_csv('air_tourism_long_V3.csv')
    >>> df_wide = long_to_wide(df_long)
    >>> print(df_wide.head())
    """
    # 매핑 테이블이 없으면 기본 테이블 사용
    if mapping_table is None:
        mapping_table = get_mapping_table()
    
    # 입력 데이터 복사 (원본 보존)
    df = df_long.copy()
    
    # date 컬럼을 datetime으로 변환
    df['date'] = pd.to_datetime(df['date'])
    
    # entity_code의 빈 문자열과 NaN을 모두 None으로 통일
    df['entity_code'] = df['entity_code'].replace('', None)
    df['entity_code'] = df['entity_code'].where(df['entity_code'].notna(), None)
    
    # 필요한 컬럼만 선택
    df = df[['date', 'indicator', 'source', 'entity_code', 'value']].copy()
    
    # 일별 데이터를 월말 기준으로 리샘플링 (옵션)
    if resample_daily_to_monthly:
        # FDR 데이터 (일별) 식별
        daily_data_sources = ['FDR']  # 필요시 추가 가능
        
        df_daily = df[df['source'].isin(daily_data_sources)].copy()
        df_monthly = df[~df['source'].isin(daily_data_sources)].copy()
        
        if len(df_daily) > 0:
            print(f"일별 데이터 발견: {len(df_daily):,}행 → 월말 기준으로 리샘플링")
            
            # 각 (indicator, source, entity_code) 조합별로 월말 값 추출
            df_daily_resampled_list = []
            
            for (indicator, source, entity_code), group in df_daily.groupby(['indicator', 'source', 'entity_code'], dropna=False):
                # 날짜를 인덱스로 설정
                group_series = group.set_index('date')['value'].sort_index()
                
                # 월말 기준으로 리샘플링 (마지막 값 사용)
                monthly_series = group_series.resample('ME').last()
                
                # entity_code가 NaN이면 None으로 변환
                if pd.isna(entity_code):
                    entity_code = None
                
                # DataFrame으로 변환
                monthly_df = pd.DataFrame({
                    'date': monthly_series.index,
                    'indicator': indicator,
                    'source': source,
                    'entity_code': entity_code,
                    'value': monthly_series.values
                })
                
                df_daily_resampled_list.append(monthly_df)
            
            if df_daily_resampled_list:
                df_daily_resampled = pd.concat(df_daily_resampled_list, ignore_index=True)
                print(f"리샘플링 결과: {len(df_daily_resampled):,}행 (월말 기준)")
                
                # 월별 데이터와 합치기
                df = pd.concat([df_monthly, df_daily_resampled], ignore_index=True)
            else:
                df = df_monthly
        else:
            print("일별 데이터 없음 - 리샘플링 불필요")
    
    # 매핑 테이블과 조인
    df_merged = df.merge(
        mapping_table,
        on=['indicator', 'source', 'entity_code'],
        how='inner'
    )
    
    print(f"매핑 전 데이터: {len(df):,}행")
    print(f"매핑 후 데이터: {len(df_merged):,}행")
    
    # pivot: date를 index, columns_name을 컬럼으로
    df_wide = df_merged.pivot_table(
        index='date',
        columns='columns_name',
        values='value',
        aggfunc='mean'  # 중복이 있을 경우 평균값 사용
    )
    
    # 날짜 순으로 정렬
    df_wide = df_wide.sort_index()
    
    print(f"변환 결과: {len(df_wide):,}행 x {len(df_wide.columns)}열")
    print(f"날짜 범위: {df_wide.index.min()} ~ {df_wide.index.max()}")
    
    return df_wide


def extract_series(
    df_long: pd.DataFrame,
    indicator: str,
    source: str,
    entity_code: Optional[str] = None,
    resample_to_monthly: bool = True
) -> pd.Series:
    """
    특정 (indicator, source, entity_code) 조합의 시계열 데이터 추출
    
    Parameters:
    -----------
    df_long : pd.DataFrame
        Long format 데이터
    indicator : str
        추출할 지표명
    source : str
        데이터 출처
    entity_code : str, optional
        엔티티 코드 (없으면 None)
    resample_to_monthly : bool, default=True
        일별 데이터를 월말 기준으로 리샘플링할지 여부
        True: 월말 값으로 변환 (FDR 데이터 등)
        False: 원본 그대로
    
    Returns:
    --------
    pd.Series
        시계열 데이터 (index=date, values=value)
    
    Examples:
    ---------
    >>> # 국제유가 추출 (일별 → 월별)
    >>> oil_prices = extract_series(df_long, 'oil_wti', 'FDR')
    >>> 
    >>> # 대한항공 여객수 추출 (원래 월별)
    >>> ke_pax = extract_series(df_long, 'icn_airline_pax_passenger', 'ODP_B551177', 'KE')
    """
    df = df_long.copy()
    
    # 조건 필터
    mask = (df['indicator'] == indicator) & (df['source'] == source)
    
    if entity_code is not None:
        mask &= (df['entity_code'] == entity_code)
    else:
        mask &= (df['entity_code'].isna() | (df['entity_code'] == ''))
    
    # 데이터 추출
    df_filtered = df[mask].copy()
    
    if len(df_filtered) == 0:
        print(f"경고: 조건에 맞는 데이터가 없습니다. (indicator={indicator}, source={source}, entity_code={entity_code})")
        return pd.Series(dtype=float)
    
    # date를 datetime으로 변환
    df_filtered['date'] = pd.to_datetime(df_filtered['date'])
    
    # Series로 변환 (date를 인덱스로)
    series = df_filtered.set_index('date')['value'].sort_index()
    
    # 일별 데이터를 월말 기준으로 리샘플링 (FDR 등)
    if resample_to_monthly and source in ['FDR']:
        original_count = len(series)
        series = series.resample('ME').last()
        print(f"추출 완료: {original_count:,}개 (일별) → {len(series):,}개 (월말) | 기간: {series.index.min()} ~ {series.index.max()}")
    else:
        print(f"추출 완료: {len(series):,}개 데이터 (기간: {series.index.min()} ~ {series.index.max()})")
    
    return series


if __name__ == "__main__":
    # 테스트 코드
    print("=" * 80)
    print("간단한 사용 예시")
    print("=" * 80)
    
    # CSV 파일 로드
    df_long = pd.read_csv("air_tourism_long_V3.csv")
    print(f"\nLong format 데이터 로드: {len(df_long):,}행\n")
    
    # Wide format 변환
    print("\n[변환 시작]")
    df_wide = long_to_wide(df_long)
    
    print(f"\n컬럼 목록 ({len(df_wide.columns)}개):")
    for i, col in enumerate(df_wide.columns, 1):
        print(f"  {i:2d}. {col}")
    
    print(f"\n최근 10일 데이터:")
    print(df_wide.tail(10))
    
    # CSV 저장
    df_wide.to_csv("air_tourism_wide.csv", encoding='utf-8-sig')
    print(f"\n✅ air_tourism_wide.csv 저장 완료")
