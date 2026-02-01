"""
KOSIS API 탐색 도구
- 실제 테이블 ID 찾기
- 메타데이터 확인
- 올바른 파라미터 탐색
"""

import requests
import pandas as pd
import json
from typing import Optional, Dict, List


class KOSISExplorer:
    """KOSIS API 탐색 클래스"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

    def list_statistics(self, org_id: str = "301", vw_cd: str = "MT_ZTITLE"):
        """
        통계표 목록 조회

        Parameters:
            org_id: 기관 ID (301: 산업통상자원부)
            vw_cd:
                - MT_ZTITLE: 주제별
                - MT_OTITLE: 기관별
                - MT_CTITLE: 승인통계
        """
        url = "https://kosis.kr/openapi/statisticsList.do"

        params = {
            "method": "getList",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "orgId": org_id,
            "vwCd": vw_cd
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                df = pd.DataFrame(data)
                print(f"\n✅ {org_id} 기관의 통계표 목록 ({len(df)}개)")

                if 'TBL_NM' in df.columns and 'TBL_ID' in df.columns:
                    print("\n주요 통계표:")
                    for idx, row in df.head(20).iterrows():
                        print(f"  {row['TBL_ID']}: {row['TBL_NM'][:60]}")

                # 수출 관련 통계표 필터링
                if 'TBL_NM' in df.columns:
                    export_tables = df[df['TBL_NM'].str.contains('수출', na=False)]
                    if not export_tables.empty:
                        print(f"\n📊 수출 관련 통계표 ({len(export_tables)}개):")
                        for idx, row in export_tables.iterrows():
                            print(f"  {row['TBL_ID']}: {row['TBL_NM']}")

                return df
            else:
                print("통계표 목록 조회 실패")
                print(f"응답: {data}")
                return pd.DataFrame()

        except Exception as e:
            print(f"오류: {e}")
            return pd.DataFrame()

    def get_table_metadata(self, tbl_id: str, org_id: str = "301"):
        """
        특정 통계표의 메타데이터 조회
        """
        params = {
            "method": "getMeta",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "orgId": org_id,
            "tblId": tbl_id
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            print(f"\n📋 통계표 {tbl_id} 메타데이터:")
            print(json.dumps(data, indent=2, ensure_ascii=False))

            return data

        except Exception as e:
            print(f"메타데이터 조회 실패: {e}")
            return None

    def explore_table_structure(self, tbl_id: str, org_id: str = "301"):
        """
        통계표 구조 탐색 - 전체 데이터 샘플 조회
        """
        print(f"\n🔍 통계표 {tbl_id} 구조 탐색 중...")

        # 방법 1: 최근 1개월 데이터만 조회
        params = {
            "method": "getList",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "orgId": org_id,
            "tblId": tbl_id,
            "newEstPrdCnt": 1,  # 최근 1개
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                print(f"✅ 샘플 데이터 조회 성공 ({len(df)}행)")
                print(f"\n컬럼 목록: {list(df.columns)}")
                print(f"\n샘플 데이터:")
                print(df.head())

                # 분류 컬럼 확인
                classification_cols = [col for col in df.columns if
                                       any(x in col.upper() for x in ['C', 'ITM', 'OBJ', 'PRD'])]
                if classification_cols:
                    print(f"\n분류 관련 컬럼:")
                    for col in classification_cols:
                        unique_values = df[col].unique()[:10]
                        print(f"  {col}: {unique_values}")

                return df
            else:
                print("⚠️ 데이터 없음")
                print(f"응답: {data}")
                return pd.DataFrame()

        except Exception as e:
            print(f"❌ 오류: {e}")
            return pd.DataFrame()


def find_export_tables(api_key: str):
    """산업통상자원부의 수출 관련 통계표 찾기"""

    explorer = KOSISExplorer(api_key)

    print("=" * 80)
    print("산업통상자원부 통계표 탐색")
    print("=" * 80)

    # 1단계: 통계표 목록 조회
    df_tables = explorer.list_statistics(org_id="301")

    if df_tables.empty:
        print("\n다른 방법 시도 중...")
        # 다른 뷰 코드로 시도
        df_tables = explorer.list_statistics(org_id="301", vw_cd="MT_OTITLE")

    # 2단계: 수출 관련 테이블 확인
    if not df_tables.empty and 'TBL_NM' in df_tables.columns:
        export_keywords = ['수출', '무역', '품목별']

        for keyword in export_keywords:
            print(f"\n'{keyword}' 검색 중...")
            mask = df_tables['TBL_NM'].str.contains(keyword, na=False)
            result = df_tables[mask]

            if not result.empty:
                print(f"발견: {len(result)}개")

                # 각 테이블 탐색
                for idx, row in result.head(5).iterrows():
                    tbl_id = row['TBL_ID']
                    tbl_name = row['TBL_NM']

                    print(f"\n{'=' * 60}")
                    print(f"테이블: {tbl_id} - {tbl_name}")
                    print(f"{'=' * 60}")

                    # 구조 탐색
                    df_sample = explorer.explore_table_structure(tbl_id)

                    if not df_sample.empty:
                        # CSV 저장
                        filename = f"KOSIS_{tbl_id}_sample.csv"
                        df_sample.to_csv(filename, index=False, encoding='utf-8-sig')
                        print(f"샘플 저장: {filename}")

    return df_tables


def test_known_tables(api_key: str):
    """알려진 테이블 ID들을 테스트"""

    explorer = KOSISExplorer(api_key)

    # 산업통상자원부 주요 통계표 ID (예상)
    test_tables = [
        "DT_1K51001",  # 산업별 수출
        "DT_1K51002",
        "DT_1K51003",  # 지역별 수출
        "DT_1K51004",
        "DT_1K51005",  # 품목별 수출 (예상)
        "DT_1ZGA",  # 수출입 통계
        "DT_1ZGA001",
        "DT_1ZGA002",
        "DT_1K52001",  # 무역통계
        "DT_1K52002",
    ]

    print("\n" + "=" * 80)
    print("알려진 테이블 ID 테스트")
    print("=" * 80)

    working_tables = []

    for tbl_id in test_tables:
        print(f"\n테스트: {tbl_id}...")
        df = explorer.explore_table_structure(tbl_id, org_id="301")

        if not df.empty:
            working_tables.append(tbl_id)
            print(f"✅ {tbl_id} 작동함!")

            # 샘플 저장
            filename = f"KOSIS_{tbl_id}_working.csv"
            df.to_csv(filename, index=False, encoding='utf-8-sig')

    print("\n" + "=" * 80)
    print("작동하는 테이블 ID:")
    print("=" * 80)
    for tbl_id in working_tables:
        print(f"  ✅ {tbl_id}")

    return working_tables


def main():
    """메인 실행"""

    print("=" * 80)
    print("KOSIS API 탐색 도구")
    print("=" * 80)

    api_key = input("\nAPI 키 입력: ").strip()

    if not api_key:
        print("API 키가 필요합니다")
        return

    print("\n실행 모드:")
    print("1. 통계표 목록 조회 및 수출 테이블 찾기")
    print("2. 알려진 테이블 ID 테스트")
    print("3. 특정 테이블 구조 탐색")

    choice = input("\n선택 (1-3): ").strip()

    if choice == "1":
        find_export_tables(api_key)

    elif choice == "2":
        test_known_tables(api_key)

    elif choice == "3":
        tbl_id = input("테이블 ID 입력: ").strip()
        if tbl_id:
            explorer = KOSISExplorer(api_key)
            explorer.explore_table_structure(tbl_id)

    else:
        print("잘못된 선택")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n중단됨")
    except Exception as e:
        print(f"\n오류: {e}")
        import traceback

        traceback.print_exc()