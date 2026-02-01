"""
한국무역협회 무역통계 API - 월별 수출입 데이터 수집
- 총 수출/수입 금액
- 무역수지
- 시계열 데이터 자동 수집
"""

import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, List, Dict
import warnings

warnings.filterwarnings('ignore')


class KITATradeCollector:
    """한국무역협회(KITA) 무역통계 수집 클래스"""

    def __init__(self, service_key: str):
        """
        초기화

        Parameters:
            service_key: 공공데이터포털 API 인증키 (URL 인코딩된 키)
        """
        self.service_key = service_key
        self.base_url = "https://apis.data.go.kr/1220000/Newtrade/getNewtradeList"

    def get_trade_data(
            self,
            start_month: str,
            end_month: str,
            num_of_rows: int = 999,
            page_no: int = 1
    ) -> pd.DataFrame:
        """
        무역통계 데이터 조회

        Parameters:
            start_month: 시작 년월 (YYYYMM)
            end_month: 종료 년월 (YYYYMM)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호

        Returns:
            pd.DataFrame: 무역통계 데이터
        """
        params = {
            "serviceKey": self.service_key,
            "strtYymm": start_month,
            "endYymm": end_month,
            "numOfRows": num_of_rows,
            "pageNo": page_no
        }

        try:
            print(f"조회 중: {start_month} ~ {end_month}...", end=" ")

            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()

            # XML 파싱
            root = ET.fromstring(response.content)

            # 결과 코드 확인
            result_code = root.find('.//resultCode')
            result_msg = root.find('.//resultMsg')

            if result_code is not None and result_code.text == '00':
                # 성공
                items = root.findall('.//item')

                if items:
                    data_list = []
                    for item in items:
                        data = {}
                        for child in item:
                            data[child.tag] = child.text
                        data_list.append(data)

                    df = pd.DataFrame(data_list)
                    print(f"✅ {len(df)}행 수집")
                    return df
                else:
                    print("⚠️ 데이터 없음")
                    return pd.DataFrame()
            else:
                error_msg = result_msg.text if result_msg is not None else "알 수 없는 오류"
                print(f"❌ API 오류: {error_msg}")
                return pd.DataFrame()

        except ET.ParseError as e:
            print(f"❌ XML 파싱 오류: {e}")
            print(f"응답 내용: {response.text[:500]}")
            return pd.DataFrame()
        except requests.exceptions.RequestException as e:
            print(f"❌ API 요청 실패: {e}")
            return pd.DataFrame()
        except Exception as e:
            print(f"❌ 처리 실패: {e}")
            return pd.DataFrame()

    def get_trade_data_multi_year(
            self,
            start_year: int,
            end_year: int,
            start_month: int = 1,
            end_month: int = 12
    ) -> pd.DataFrame:
        """
        여러 해의 무역통계 데이터 수집

        Parameters:
            start_year: 시작 년도
            end_year: 종료 년도
            start_month: 시작 월 (1-12)
            end_month: 종료 월 (1-12)

        Returns:
            pd.DataFrame: 전체 기간 무역통계
        """
        all_data = []

        print("=" * 80)
        print(f"무역통계 수집: {start_year}년 ~ {end_year}년")
        print("=" * 80)

        for year in range(start_year, end_year + 1):
            # 시작/종료 월 조정
            if year == start_year:
                sm = start_month
            else:
                sm = 1

            if year == end_year:
                em = end_month
            else:
                em = 12

            start_ym = f"{year}{sm:02d}"
            end_ym = f"{year}{em:02d}"

            df = self.get_trade_data(start_ym, end_ym)

            if not df.empty:
                all_data.append(df)

        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)

            print("\n" + "=" * 80)
            print("수집 완료")
            print("=" * 80)
            print(f"총 데이터: {len(result_df)}행")

            return result_df
        else:
            print("\n⚠️ 수집된 데이터가 없습니다")
            return pd.DataFrame()


def collect_export_data(
        service_key: str,
        start_year: int = 2020,
        end_year: Optional[int] = None,
        save_csv: bool = True,
        save_excel: bool = True
) -> pd.DataFrame:
    """
    월별 총 수출 데이터 수집 및 저장

    Parameters:
        service_key: API 인증키
        start_year: 시작 년도
        end_year: 종료 년도 (None이면 현재 년도)
        save_csv: CSV 저장 여부
        save_excel: Excel 저장 여부

    Returns:
        pd.DataFrame: 무역통계 데이터
    """
    if end_year is None:
        end_year = datetime.now().year

    # 데이터 수집
    collector = KITATradeCollector(service_key)
    df = collector.get_trade_data_multi_year(start_year, end_year)

    if df.empty:
        print("\n수집된 데이터가 없습니다")
        return df

    # 데이터 처리
    print("\n" + "=" * 80)
    print("데이터 처리 중...")
    print("=" * 80)

    # 컬럼명 확인
    print(f"컬럼: {', '.join(df.columns.tolist())}")

    # 숫자 컬럼 변환
    numeric_columns = ['expDlr', 'impDlr', 'trdbalDlr']  # 수출, 수입, 무역수지

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 날짜 정렬
    if 'statYymm' in df.columns:
        df = df.sort_values('statYymm').reset_index(drop=True)

        # 날짜 컬럼 추가
        df['년월'] = pd.to_datetime(df['statYymm'], format='%Y%m')
        df['년도'] = df['년월'].dt.year
        df['월'] = df['년월'].dt.month

    # 데이터 미리보기
    print("\n" + "=" * 80)
    print("데이터 미리보기 (최근 10개)")
    print("=" * 80)
    print(df.tail(10))

    # 기본 통계
    print("\n" + "=" * 80)
    print("기본 통계")
    print("=" * 80)

    if 'expDlr' in df.columns:
        print(f"\n수출액 (단위: 천 달러)")
        print(f"  평균: ${df['expDlr'].mean():,.0f}")
        print(f"  최대: ${df['expDlr'].max():,.0f} ({df.loc[df['expDlr'].idxmax(), 'statYymm']})")
        print(f"  최소: ${df['expDlr'].min():,.0f} ({df.loc[df['expDlr'].idxmin(), 'statYymm']})")

    if 'impDlr' in df.columns:
        print(f"\n수입액 (단위: 천 달러)")
        print(f"  평균: ${df['impDlr'].mean():,.0f}")
        print(f"  최대: ${df['impDlr'].max():,.0f}")
        print(f"  최소: ${df['impDlr'].min():,.0f}")

    if 'trdbalDlr' in df.columns:
        print(f"\n무역수지 (단위: 천 달러)")
        print(f"  평균: ${df['trdbalDlr'].mean():,.0f}")
        print(f"  최대: ${df['trdbalDlr'].max():,.0f}")
        print(f"  최소: ${df['trdbalDlr'].min():,.0f}")

    # 파일 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if save_csv:
        csv_file = f"한국_월별수출입통계_{start_year}_{end_year}_{timestamp}.csv"
        df.to_csv(csv_file, index=False, encoding='utf-utf-sig')
        print(f"\n✅ CSV 저장: {csv_file}")

    if save_excel:
        excel_file = f"한국_월별수출입통계_{start_year}_{end_year}_{timestamp}.xlsx"

        with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
            # 전체 데이터
            df.to_excel(writer, sheet_name='전체데이터', index=False)

            # 연도별 요약
            if '년도' in df.columns and 'expDlr' in df.columns:
                yearly = df.groupby('년도').agg({
                    'expDlr': 'sum',
                    'impDlr': 'sum',
                    'trdbalDlr': 'sum'
                }).reset_index()
                yearly.columns = ['년도', '수출액', '수입액', '무역수지']
                yearly.to_excel(writer, sheet_name='연도별요약', index=False)

        print(f"✅ Excel 저장: {excel_file}")

    return df


def plot_export_trend(df: pd.DataFrame):
    """
    수출 추이 그래프 (선택사항)

    Parameters:
        df: 무역통계 데이터프레임
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm

        # 한글 폰트 설정
        plt.rcParams['font.family'] = 'Malgun Gothic'
        plt.rcParams['axes.unicode_minus'] = False

        if '년월' in df.columns and 'expDlr' in df.columns:
            fig, axes = plt.subplots(2, 1, figsize=(14, 10))

            # 수출/수입 추이
            ax1 = axes[0]
            ax1.plot(df['년월'], df['expDlr'], label='수출', marker='o', markersize=3)
            ax1.plot(df['년월'], df['impDlr'], label='수입', marker='s', markersize=3)
            ax1.set_title('월별 수출입 추이', fontsize=14, fontweight='bold')
            ax1.set_xlabel('년월')
            ax1.set_ylabel('금액 (천 달러)')
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # 무역수지
            ax2 = axes[1]
            colors = ['green' if x > 0 else 'red' for x in df['trdbalDlr']]
            ax2.bar(df['년월'], df['trdbalDlr'], color=colors, alpha=0.6)
            ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax2.set_title('월별 무역수지', fontsize=14, fontweight='bold')
            ax2.set_xlabel('년월')
            ax2.set_ylabel('무역수지 (천 달러)')
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()

            graph_file = f"수출입_추이그래프_{datetime.now().strftime('%Y%m%d')}.png"
            plt.savefig(graph_file, dpi=300, bbox_inches='tight')
            print(f"✅ 그래프 저장: {graph_file}")

            plt.show()

    except ImportError:
        print("\n⚠️ matplotlib이 설치되지 않아 그래프를 생성할 수 없습니다")
        print("설치: pip install matplotlib")
    except Exception as e:
        print(f"\n⚠️ 그래프 생성 실패: {e}")


def main():
    """메인 실행 함수"""

    print("=" * 80)
    print("한국무역협회 무역통계 API - 월별 수출입 데이터 수집")
    print("=" * 80)

    # API 키 입력
    print("\nAPI 키 입력 방법:")
    print("1. URL 인코딩된 키 (추천)")
    print("   예: 2o6NG3ixxDgGQ9S4dWUg...%252BJvF...")
    print("2. 디코딩된 키")
    print("   예: 2o6NG3ixxDgGQ9S4dWUg...+JvF...")

    service_key = input("\nAPI 키 입력: ").strip()

    if not service_key:
        print("API 키가 필요합니다")
        return

    # 기간 설정
    print("\n데이터 수집 기간 설정:")
    start_year = input("시작 년도 (예: 2020): ").strip()
    end_year = input("종료 년도 (공백=현재 년도): ").strip()

    start_year = int(start_year) if start_year else 2020
    end_year = int(end_year) if end_year else None

    # 데이터 수집
    df = collect_export_data(
        service_key=service_key,
        start_year=start_year,
        end_year=end_year,
        save_csv=True,
        save_excel=True
    )

    if not df.empty:
        # 그래프 생성 옵션
        create_graph = input("\n그래프를 생성하시겠습니까? (y/n): ").strip().lower()
        if create_graph == 'y':
            plot_export_trend(df)

    print("\n" + "=" * 80)
    print("완료!")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n사용자에 의해 중단되었습니다")
    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback

        traceback.print_exc()