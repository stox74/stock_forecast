"""
관광통계 API 통합 클라이언트 (tourism_stats.py)
- 국민해외관광객 (출국자) 통계
- 방한외래관광객 (입국자) 통계
- YoY 증감률 계산
- DB 저장 (tourism_indus_stats)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
import time
from sqlalchemy import create_engine
import sys

# ===== DB 설정 =====
DB_CONFIG = {
    "host": "localhost",  # DB 호스트 (예: "localhost" 또는 IP 주소)
    "port": 3307,  # DB 포트
    "user": "stox7412",  # DB 사용자명
    "password": "Apt106503!~",  # DB 비밀번호
    "database": "investar",  # DB 이름
}


def get_db_info() -> Dict[str, str]:
    """DB 연결 정보 반환"""
    return DB_CONFIG


def get_engine(db_info: Dict[str, str]):
    """SQLAlchemy 엔진 생성"""
    url = (
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
        f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return create_engine(url)


def log(tag: str, msg: str):
    """로그 출력"""
    print(f"[{tag}] {msg}", file=sys.stdout, flush=True)


class TourismStatsAPI:
    """관광통계 API 통합 클라이언트"""

    BASE_URL = "http://openapi.tour.go.kr/openapi/service"

    # 국가 코드
    COUNTRY_CODES = {
        '중국': '112',
        '일본': '130',
        '미국': '275',
    }

    def __init__(self, service_key: str, debug: bool = False, delay: float = 2.0):
        """
        Args:
            service_key: API 서비스 키
            debug: 디버그 모드
            delay: API 호출 간 대기 시간 (초)
        """
        self.service_key = service_key
        self.debug = debug
        self.delay = delay

    def _parse_xml_response(self, response_text: str) -> Dict:
        """XML 응답 파싱"""
        try:
            root = ET.fromstring(response_text)

            header = root.find('.//header')
            result_code = header.find('resultCode').text if header is not None else None
            result_msg = header.find('resultMsg').text if header is not None else None

            if result_code not in ['0000', '00']:
                raise Exception(f"API Error: {result_code} - {result_msg}")

            items = []
            for item in root.findall('.//item'):
                item_dict = {child.tag: child.text for child in item}
                items.append(item_dict)

            return {
                'result_code': result_code,
                'result_msg': result_msg,
                'items': items
            }
        except ET.ParseError as e:
            raise Exception(f"XML 파싱 오류: {e}")

    def _api_request(self, endpoint: str, params: Dict, max_retries: int = 3) -> Dict:
        """API 요청 공통 함수"""
        for attempt in range(max_retries):
            try:
                if self.debug:
                    log("DEBUG", f"API 요청: {endpoint}")

                response = requests.get(endpoint, params=params, timeout=30)
                response.raise_for_status()

                result = self._parse_xml_response(response.text)

                if attempt < max_retries - 1:
                    time.sleep(self.delay)

                return result

            except Exception as e:
                error_msg = str(e)

                if "22" in error_msg or "LIMITED" in error_msg:
                    wait_time = self.delay * (attempt + 1) * 2
                    log("WARNING", f"트래픽 제한. {wait_time}초 대기...")
                    time.sleep(wait_time)
                    continue

                if attempt < max_retries - 1:
                    log("WARNING", f"오류 발생. {self.delay}초 후 재시도...")
                    time.sleep(self.delay)
                else:
                    raise Exception(f"API 요청 실패: {e}")

        raise Exception("API 요청 실패")

    def get_overseas_outbound(self, ym: str) -> int:
        """
        국민해외관광객 (출국자수) 조회

        Args:
            ym: 년월 (YYYYMM)

        Returns:
            출국자수
        """
        endpoint = f"{self.BASE_URL}/EdrcntTourismStatsService/getOvseaTuristStatsList"

        params = {
            'serviceKey': self.service_key,
            'YM': ym,
            'numOfRows': 1000,
            'pageNo': 1,
        }

        result = self._api_request(endpoint, params)

        # 전체 합계
        total = sum(int(item.get('num', 0)) for item in result['items'])
        return total

    def get_foreign_inbound(self, ym: str, nat_cd: str) -> int:
        """
        방한외래관광객 (입국자수) 조회

        Args:
            ym: 년월 (YYYYMM)
            nat_cd: 국가코드

        Returns:
            입국자수
        """
        endpoint = f"{self.BASE_URL}/EdrcntTourismStatsService/getForeignTuristStatsList"

        params = {
            'serviceKey': self.service_key,
            'YM': ym,
            'NAT_CD': nat_cd,
            'numOfRows': 1000,
            'pageNo': 1,
        }

        result = self._api_request(endpoint, params)

        # 전체 합계
        total = sum(int(item.get('num', 0)) for item in result['items'])
        return total

    def collect_monthly_stats(self, start_ym: str, end_ym: str) -> pd.DataFrame:
        """
        월별 관광통계 수집

        Args:
            start_ym: 시작 년월 (YYYYMM)
            end_ym: 종료 년월 (YYYYMM)

        Returns:
            Long format DataFrame
        """
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)

        total_requests = len(ym_list) * 4  # 출국 1개 + 입국 3개국
        current = 0

        log("INFO", f"데이터 수집 시작: {start_ym} ~ {end_ym}")
        log("INFO", f"총 요청 수: {total_requests}건")

        for ym in ym_list:
            # 1. 출국자수
            current += 1
            log("INFO", f"[{current}/{total_requests}] 출국자수: {ym}")

            try:
                outbound = self.get_overseas_outbound(ym)
                results.append({
                    '년월': ym,
                    '구분': '출국',
                    '국가': '한국',
                    '관광객수': outbound
                })
                log("INFO", f"  ✓ {outbound:,}명")
            except Exception as e:
                log("ERROR", f"  ✗ 출국자수 오류: {e}")
                results.append({
                    '년월': ym,
                    '구분': '출국',
                    '국가': '한국',
                    '관광객수': None
                })

            # 2. 국가별 입국자수
            for country, nat_cd in self.COUNTRY_CODES.items():
                current += 1
                log("INFO", f"[{current}/{total_requests}] 입국자수 {country}: {ym}")

                try:
                    inbound = self.get_foreign_inbound(ym, nat_cd)
                    results.append({
                        '년월': ym,
                        '구분': '입국',
                        '국가': country,
                        '관광객수': inbound
                    })
                    log("INFO", f"  ✓ {inbound:,}명")
                except Exception as e:
                    log("ERROR", f"  ✗ {country} 입국자수 오류: {e}")
                    results.append({
                        '년월': ym,
                        '구분': '입국',
                        '국가': country,
                        '관광객수': None
                    })

        df = pd.DataFrame(results)
        log("INFO", f"데이터 수집 완료: {len(df)}개 레코드")

        return df

    def _generate_month_list(self, start_ym: str, end_ym: str) -> List[str]:
        """년월 리스트 생성"""
        start_date = datetime.strptime(start_ym, '%Y%m')
        end_date = datetime.strptime(end_ym, '%Y%m')

        month_list = []
        current_date = start_date

        while current_date <= end_date:
            month_list.append(current_date.strftime('%Y%m'))
            if current_date.month == 12:
                current_date = datetime(current_date.year + 1, 1, 1)
            else:
                current_date = datetime(current_date.year, current_date.month + 1, 1)

        return month_list


def calculate_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """
    YoY 증감률 계산

    Args:
        df: 년월, 구분, 국가, 관광객수 컬럼을 가진 DataFrame

    Returns:
        yoy_rate 컬럼이 추가된 DataFrame
    """
    log("INFO", "YoY 증감률 계산 시작")

    # 년월을 datetime으로 변환
    df['date'] = pd.to_datetime(df['년월'], format='%Y%m')

    # 구분 + 국가별로 정렬
    df = df.sort_values(['구분', '국가', 'date'])

    # YoY 계산 (12개월 전 대비)
    df['yoy_rate'] = None

    for (gubun, country), group in df.groupby(['구분', '국가']):
        group = group.sort_values('date')

        # 12개월 shift
        group['prev_year'] = group['관광객수'].shift(12)

        # YoY 계산: ((현재 - 1년전) / 1년전) * 100
        mask = (group['관광객수'].notna()) & (group['prev_year'].notna()) & (group['prev_year'] != 0)
        group.loc[mask, 'yoy_rate'] = ((group.loc[mask, '관광객수'] - group.loc[mask, 'prev_year']) / group.loc[
            mask, 'prev_year']) * 100

        # DataFrame 업데이트
        df.loc[group.index, 'yoy_rate'] = group['yoy_rate']

    # date와 prev_year 컬럼 제거
    df = df.drop(['date', 'prev_year'], axis=1, errors='ignore')

    log("INFO", f"YoY 계산 완료: {df['yoy_rate'].notna().sum()}개 레코드")

    return df


def save_to_db(df: pd.DataFrame, table_name: str = 'tourism_indus_stats'):
    """
    데이터베이스에 저장

    Args:
        df: 저장할 DataFrame
        table_name: 테이블 이름
    """
    log("INFO", f"데이터베이스 저장 시작: {table_name}")

    try:
        # DB 연결
        db_info = get_db_info()
        engine = get_engine(db_info)

        # 저장할 컬럼 정리
        save_df = df.copy()

        # 컬럼 순서 정리
        columns_order = ['년월', '구분', '국가', '관광객수', 'yoy_rate']
        save_df = save_df[columns_order]

        # 데이터 타입 최적화
        save_df['년월'] = save_df['년월'].astype(str)
        save_df['구분'] = save_df['구분'].astype(str)
        save_df['국가'] = save_df['국가'].astype(str)
        save_df['관광객수'] = pd.to_numeric(save_df['관광객수'], errors='coerce')
        save_df['yoy_rate'] = pd.to_numeric(save_df['yoy_rate'], errors='coerce')

        # DB에 저장 (기존 데이터 대체)
        save_df.to_sql(
            name=table_name,
            con=engine,
            if_exists='replace',  # 기존 테이블 대체
            index=False,
            dtype={
                '년월': 'VARCHAR(6)',
                '구분': 'VARCHAR(10)',
                '국가': 'VARCHAR(20)',
                '관광객수': 'INT',
                'yoy_rate': 'FLOAT'
            }
        )

        log("INFO", f"✓ 데이터베이스 저장 완료: {len(save_df)}개 레코드")

        # 저장 확인
        result = pd.read_sql(f"SELECT COUNT(*) as cnt FROM {table_name}", con=engine)
        log("INFO", f"✓ 저장 확인: {result['cnt'].iloc[0]}개 레코드")

        engine.dispose()

    except Exception as e:
        log("ERROR", f"데이터베이스 저장 실패: {e}")
        raise


def main():
    """메인 실행 함수"""

    log("INFO", "=" * 70)
    log("INFO", "관광통계 데이터 수집 및 저장")
    log("INFO", "=" * 70)

    # API 키
    SERVICE_KEY = "2o6NG3ixxDgGQ9S4dWUgsMac9WlxfX46+JvFRsAlsXQ6xVi6CZewvNJvbHd4S7exkWwt3YWoKSdwvUNb46kSTQ=="

    # 조회 기간 설정
    START_YM = "202201"  # 2022년 1월
    END_YM = "202412"  # 2024년 12월

    log("INFO", f"조회 기간: {START_YM} ~ {END_YM}")
    log("INFO", f"조회 항목: 출국자수 (한국), 입국자수 (중국, 일본, 미국)")

    # API 클라이언트 초기화
    api = TourismStatsAPI(SERVICE_KEY, debug=False, delay=2.0)

    # 1. 데이터 수집
    log("INFO", "\n[1/3] 데이터 수집")
    df = api.collect_monthly_stats(START_YM, END_YM)

    # 중간 저장 (CSV)
    df.to_csv('tourism_stats_raw.csv', index=False, encoding='utf-8-sig')
    log("INFO", "✓ 중간 저장: tourism_stats_raw.csv")

    # 2. YoY 증감률 계산
    log("INFO", "\n[2/3] YoY 증감률 계산")
    df = calculate_yoy(df)

    # 최종 CSV 저장
    df.to_csv('tourism_stats_final.csv', index=False, encoding='utf-8-sig')
    log("INFO", "✓ 최종 저장: tourism_stats_final.csv")

    # 3. 데이터베이스 저장
    log("INFO", "\n[3/3] 데이터베이스 저장")
    save_to_db(df, table_name='tourism_indus_stats')

    # 결과 요약
    log("INFO", "\n" + "=" * 70)
    log("INFO", "결과 요약")
    log("INFO", "=" * 70)

    log("INFO", f"\n총 레코드 수: {len(df)}")
    log("INFO", f"기간: {df['년월'].min()} ~ {df['년월'].max()}")

    # 구분별 통계
    log("INFO", "\n[구분별 통계]")
    for gubun in df['구분'].unique():
        gubun_df = df[df['구분'] == gubun]
        log("INFO", f"{gubun}: {len(gubun_df)}개 레코드")

    # 국가별 통계
    log("INFO", "\n[국가별 통계]")
    for country in df['국가'].unique():
        country_df = df[df['국가'] == country]
        log("INFO", f"{country}: {len(country_df)}개 레코드")

    # 샘플 데이터 출력
    log("INFO", "\n[샘플 데이터 (최근 10개)]")
    print(df.tail(10).to_string(index=False))

    log("INFO", "\n" + "=" * 70)
    log("INFO", "완료!")
    log("INFO", "=" * 70)


if __name__ == "__main__":
    main()