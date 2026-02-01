"""
한국무역협회 API - 장기 무역통계 자동 수집 (오류 수정 버전)
"""

import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import time


def get_trade_data_range(service_key: str, start_month: str, end_month: str) -> pd.DataFrame:
    """특정 기간의 무역통계 데이터 수집"""
    url = "https://apis.data.go.kr/1220000/Newtrade/getNewtradeList"

    params = {
        "serviceKey": service_key,
        "strtYymm": start_month,
        "endYymm": end_month,
        "numOfRows": 999
    }

    try:
        response = requests.get(url, params=params, timeout=30)

        if response.status_code == 200:
            root = ET.fromstring(response.content)
            result_code = root.find('.//resultCode')

            if result_code is not None and result_code.text == '00':
                items = root.findall('.//item')

                if items:
                    data_list = []
                    for item in items:
                        data = {child.tag: child.text for child in item}
                        data_list.append(data)

                    return pd.DataFrame(data_list)

        return pd.DataFrame()

    except Exception as e:
        print(f"  오류: {e}")
        return pd.DataFrame()


def collect_long_term_trade_data(
        service_key: str,
        start_year: int = 2010,
        end_year: int = 2025
) -> pd.DataFrame:
    """장기간 무역통계 데이터 수집"""

    all_data = []

    print("=" * 80)
    print(f"무역통계 데이터 수집: {start_year}년 ~ {end_year}년")
    print("=" * 80)

    for year in range(start_year, end_year + 1):
        start_ym = f"{year}01"
        end_ym = f"{year}12"

        print(f"\n{year}년 데이터 수집 중... ", end="")

        df = get_trade_data_range(service_key, start_ym, end_ym)

        if not df.empty:
            all_data.append(df)
            print(f"✅ {len(df)}개월")
        else:
            print(f"⚠️")

        time.sleep(0.3)

    if all_data:
        result_df = pd.concat(all_data, ignore_index=True)

        print("\n" + "=" * 80)
        print("데이터 처리 중...")
        print("=" * 80)

        # 숫자 컬럼 변환
        for col in ['expDlr', 'impDlr', 'trdbalDlr']:
            if col in result_df.columns:
                result_df[col] = pd.to_numeric(result_df[col], errors='coerce')

        # 날짜 처리
        if 'statYymm' in result_df.columns:
            result_df = result_df.sort_values('statYymm').reset_index(drop=True)
            result_df['년월'] = pd.to_datetime(result_df['statYymm'], format='%Y%m')
            result_df['년도'] = result_df['년월'].dt.year
            result_df['월'] = result_df['년월'].dt.month

        print(f"✅ 총 {len(result_df)}개월 데이터 수집 완료")

        return result_df
    else:
        return pd.DataFrame()


def analyze_trade_data(df: pd.DataFrame):
    """무역통계 데이터 분석"""

    if df.empty:
        print("\n⚠️ 분석할 데이터가 없습니다")
        return

    print("\n" + "=" * 80)
    print("데이터 분석")
    print("=" * 80)

    print(f"\n📋 컬럼: {', '.join(df.columns.tolist())}")

    # 기간
    if 'statYymm' in df.columns:
        print(f"\n📅 데이터 기간:")
        print(f"   시작: {df['statYymm'].min()}")
        print(f"   종료: {df['statYymm'].max()}")
        print(f"   총: {len(df)}개월")

    # 수출액
    if 'expDlr' in df.columns:
        print(f"\n📊 수출액 통계 (단위: 천 달러)")
        print(f"   평균: ${df['expDlr'].mean():,.0f}")
        print(f"   최대: ${df['expDlr'].max():,.0f}")
        print(f"   최소: ${df['expDlr'].min():,.0f}")
        print(f"   합계: ${df['expDlr'].sum():,.0f}")

    # 무역수지
    if 'trdbalDlr' in df.columns:
        surplus = (df['trdbalDlr'] > 0).sum()
        deficit = (df['trdbalDlr'] < 0).sum()

        print(f"\n💰 무역수지:")
        print(f"   흑자: {surplus}개월 ({surplus / len(df) * 100:.1f}%)")
        print(f"   적자: {deficit}개월 ({deficit / len(df) * 100:.1f}%)")

    # 연도별
    if '년도' in df.columns and 'expDlr' in df.columns:
        print(f"\n📈 연도별 수출액 (단위: 억 달러):")
        yearly = df.groupby('년도')['expDlr'].sum() / 100000
        for year, value in yearly.items():
            print(f"   {year}년: ${value:,.1f}")

    # 샘플
    print(f"\n📋 최근 12개월:")
    print(df.tail(12)[['statYymm', 'expDlr', 'impDlr']].to_string(index=False))


def save_trade_data(df: pd.DataFrame, start_year: int, end_year: int):
    """데이터 저장"""

    if df.empty:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # CSV
    csv_file = f"한국무역통계_{start_year}_{end_year}_{timestamp}.csv"
    df.to_csv(csv_file, index=False, encoding='utf-8-sig')
    print(f"\n✅ CSV: {csv_file}")

    # Excel
    excel_file = f"한국무역통계_{start_year}_{end_year}_{timestamp}.xlsx"
    with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='전체데이터', index=False)

        if '년도' in df.columns and 'expDlr' in df.columns:
            yearly = df.groupby('년도').agg({
                'expDlr': 'sum',
                'impDlr': 'sum',
                'trdbalDlr': 'sum'
            }).reset_index()
            yearly.to_excel(writer, sheet_name='연도별요약', index=False)

    print(f"✅ Excel: {excel_file}")


# 사용 예시
if __name__ == "__main__":
    SERVICE_KEY = "2o6NG3ixxDgGQ9S4dWUgsMac9WlxfX46+JvFRsAlsXQ6xVi6CZewvNJvbHd4S7exkWwt3YWoKSdwvUNb46kSTQ=="

    # 데이터 수집
    df = collect_long_term_trade_data(
        service_key=SERVICE_KEY,
        start_year=2010,
        end_year=2025
    )

    # 분석
    analyze_trade_data(df)

    # 저장
    save_trade_data(df, 2010, 2025)

    print("\n" + "=" * 80)
    print("✅ 완료!")
    print("=" * 80)