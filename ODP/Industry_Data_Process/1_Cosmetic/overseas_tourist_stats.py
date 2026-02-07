"""
국민해외관광객세부통계조회 API 클라이언트
- 성별, 연령대, 출국항별 한국 국민 출국자수 세부 통계
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
import time


class OverseasTouristStatsAPI:
    """국민해외관광객세부통계 API 클라이언트"""
    
    BASE_URL = "http://openapi.tour.go.kr/openapi/service"
    
    # 성별 코드
    SEX_CODES = {
        '남성': 'M',
        '여성': 'F',
        '전체': None  # 성별 구분 없음
    }
    
    # 연령대 코드
    AGE_CODES = {
        '0-10세': '10',
        '11-20세': '20',
        '21-30세': '30',
        '31-40세': '40',
        '41-50세': '50',
        '51-60세': '60',
        '61-70세': '70',
        '71세이상': '80',
        '전체': None
    }
    
    # 출국항 코드
    PORT_CODES = {
        '인천공항': 'IA',
        '김포공항': 'KA',
        '김해공항': 'PA',
        '제주공항': 'JA',
        '부산항': 'PS',
        '인천항': 'IS',
        '전체': None
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
            
            if result_code != '0000':
                raise Exception(f"API Error: {result_code} - {result_msg}")
            
            items = []
            for item in root.findall('.//item'):
                item_dict = {child.tag: child.text for child in item}
                items.append(item_dict)
            
            body = root.find('.//body')
            meta = {}
            if body is not None:
                for tag in ['numOfRows', 'pageNo', 'totalCount']:
                    elem = body.find(tag)
                    if elem is not None:
                        meta[tag] = elem.text
            
            return {
                'result_code': result_code,
                'result_msg': result_msg,
                'meta': meta,
                'items': items
            }
        except ET.ParseError as e:
            raise Exception(f"XML 파싱 오류: {e}\n응답 내용: {response_text[:500]}")
    
    def get_overseas_tourist_stats(
        self,
        ym: str,
        sex_cd: Optional[str] = None,
        age_cd: Optional[str] = None,
        port_cd: Optional[str] = None,
        num_of_rows: int = 100,
        page_no: int = 1,
        max_retries: int = 3
    ) -> Dict:
        """
        국민해외관광객통계조회
        
        Args:
            ym: 년월 (YYYYMM)
            sex_cd: 성별코드 ('M'=남성, 'F'=여성, None=전체)
            age_cd: 연령대코드 ('10'=0-10세, '20'=11-20세, ..., None=전체)
            port_cd: 출국항코드 ('IA'=인천공항, 'KA'=김포공항, ..., None=전체)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호
            max_retries: 최대 재시도 횟수
        
        Returns:
            API 응답 데이터 딕셔너리
        """
        endpoint = f"{self.BASE_URL}/EdrcntTourismStatsService/getOvseaTuristStatsList"
        
        params = {
            'serviceKey': self.service_key,
            'YM': ym,
            'numOfRows': num_of_rows,
            'pageNo': page_no,
        }
        
        if sex_cd:
            params['SEX_CD'] = sex_cd
        if age_cd:
            params['AGE_CD'] = age_cd
        if port_cd:
            params['PORT_CD'] = port_cd
        
        for attempt in range(max_retries):
            try:
                if self.debug:
                    print(f"\n[DEBUG] API 요청 (시도 {attempt + 1}/{max_retries})")
                    print(f"  파라미터: YM={ym}, SEX_CD={sex_cd}, AGE_CD={age_cd}, PORT_CD={port_cd}")
                
                response = requests.get(endpoint, params=params, timeout=30)
                
                if self.debug:
                    print(f"[DEBUG] 응답 코드: {response.status_code}")
                    print(f"[DEBUG] URL: {response.url}")
                
                response.raise_for_status()
                result = self._parse_xml_response(response.text)
                
                # 성공 시 대기
                if attempt < max_retries - 1:
                    time.sleep(self.delay)
                
                return result
                
            except Exception as e:
                error_msg = str(e)
                
                # 오류 코드 22 (트래픽 제한)인 경우
                if "22" in error_msg or "LIMITED" in error_msg:
                    wait_time = self.delay * (attempt + 1) * 2
                    print(f"  ⚠️  시간당 제한 감지. {wait_time}초 대기 후 재시도...")
                    time.sleep(wait_time)
                    continue
                
                # 그 외 오류
                if attempt < max_retries - 1:
                    print(f"  오류 발생. {self.delay}초 후 재시도... ({e})")
                    time.sleep(self.delay)
                else:
                    raise Exception(f"API 요청 실패 (최대 재시도 초과): {e}")
        
        raise Exception("API 요청 실패")
    
    def test_connection(self) -> bool:
        """API 연결 테스트"""
        print("\n" + "=" * 70)
        print("국민해외관광객세부통계 API 연결 테스트")
        print("=" * 70)
        
        try:
            print("\n테스트 요청 전송 중...")
            print("  년월: 201201")
            print("  성별: 여성 (F)")
            print("  연령대: 11-20세 (20)")
            print("  출국항: 인천공항 (IA)")
            
            response = self.get_overseas_tourist_stats(
                ym="201201",
                sex_cd="F",
                age_cd="20",
                port_cd="IA"
            )
            
            print("\n✓ API 연결 성공!")
            print(f"  결과 코드: {response['result_code']}")
            print(f"  결과 메시지: {response['result_msg']}")
            
            if response['items']:
                item = response['items'][0]
                print(f"\n테스트 데이터:")
                print(f"  년월: {item.get('ym', 'N/A')}")
                print(f"  성별: {item.get('sex', 'N/A')}")
                print(f"  연령대: {item.get('age', 'N/A')}")
                print(f"  출국항: {item.get('port', 'N/A')}")
                print(f"  출국자수: {item.get('num', 'N/A')}명")
            
            print("\n" + "=" * 70)
            return True
            
        except Exception as e:
            print("\n✗ API 연결 실패!")
            print(f"\n오류 내용:\n{e}")
            print("\n" + "=" * 70)
            return False
    
    def get_monthly_total(self, start_ym: str, end_ym: str) -> pd.DataFrame:
        """
        월별 전체 출국자수 조회 (세부 구분 없음)
        
        Args:
            start_ym: 시작 년월
            end_ym: 종료 년월
        
        Returns:
            DataFrame with columns: 년월, 출국자수
        """
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        total = len(ym_list)
        
        print("\n" + "=" * 70)
        print("월별 전체 출국자수 조회")
        print("=" * 70)
        
        for idx, ym in enumerate(ym_list, 1):
            print(f"[{idx}/{total}] {ym}")
            
            try:
                response = self.get_overseas_tourist_stats(
                    ym=ym,
                    num_of_rows=1000
                )
                
                # 전체 합계
                total_num = 0
                if response['items']:
                    for item in response['items']:
                        total_num += int(item.get('num', 0))
                
                results.append({
                    '년월': ym,
                    '출국자수': total_num
                })
                
                print(f"  ✓ {total_num:,}명")
                
            except Exception as e:
                print(f"  ✗ 오류: {e}")
                results.append({
                    '년월': ym,
                    '출국자수': None
                })
        
        return pd.DataFrame(results)
    
    def get_by_sex(self, start_ym: str, end_ym: str) -> pd.DataFrame:
        """
        성별 출국자수 조회
        
        Returns:
            DataFrame with columns: 년월, 성별, 출국자수
        """
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        total = len(ym_list) * 2  # 남성, 여성
        current = 0
        
        print("\n" + "=" * 70)
        print("성별 출국자수 조회")
        print("=" * 70)
        
        for ym in ym_list:
            for sex_name, sex_cd in [('남성', 'M'), ('여성', 'F')]:
                current += 1
                print(f"[{current}/{total}] {ym} - {sex_name}")
                
                try:
                    response = self.get_overseas_tourist_stats(
                        ym=ym,
                        sex_cd=sex_cd,
                        num_of_rows=1000
                    )
                    
                    total_num = 0
                    if response['items']:
                        for item in response['items']:
                            total_num += int(item.get('num', 0))
                    
                    results.append({
                        '년월': ym,
                        '성별': sex_name,
                        '출국자수': total_num
                    })
                    
                    print(f"  ✓ {total_num:,}명")
                    
                except Exception as e:
                    print(f"  ✗ 오류: {e}")
                    results.append({
                        '년월': ym,
                        '성별': sex_name,
                        '출국자수': None
                    })
        
        return pd.DataFrame(results)
    
    def get_by_age(self, start_ym: str, end_ym: str) -> pd.DataFrame:
        """
        연령대별 출국자수 조회
        
        Returns:
            DataFrame with columns: 년월, 연령대, 출국자수
        """
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        age_groups = [
            ('0-10세', '10'),
            ('11-20세', '20'),
            ('21-30세', '30'),
            ('31-40세', '40'),
            ('41-50세', '50'),
            ('51-60세', '60'),
            ('61-70세', '70'),
            ('71세이상', '80')
        ]
        
        total = len(ym_list) * len(age_groups)
        current = 0
        
        print("\n" + "=" * 70)
        print("연령대별 출국자수 조회")
        print("=" * 70)
        
        for ym in ym_list:
            for age_name, age_cd in age_groups:
                current += 1
                print(f"[{current}/{total}] {ym} - {age_name}")
                
                try:
                    response = self.get_overseas_tourist_stats(
                        ym=ym,
                        age_cd=age_cd,
                        num_of_rows=1000
                    )
                    
                    total_num = 0
                    if response['items']:
                        for item in response['items']:
                            total_num += int(item.get('num', 0))
                    
                    results.append({
                        '년월': ym,
                        '연령대': age_name,
                        '출국자수': total_num
                    })
                    
                    print(f"  ✓ {total_num:,}명")
                    
                except Exception as e:
                    print(f"  ✗ 오류: {e}")
                    results.append({
                        '년월': ym,
                        '연령대': age_name,
                        '출국자수': None
                    })
        
        return pd.DataFrame(results)
    
    def get_by_port(self, start_ym: str, end_ym: str) -> pd.DataFrame:
        """
        출국항별 출국자수 조회
        
        Returns:
            DataFrame with columns: 년월, 출국항, 출국자수
        """
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        ports = [
            ('인천공항', 'IA'),
            ('김포공항', 'KA'),
            ('김해공항', 'PA'),
            ('제주공항', 'JA'),
            ('부산항', 'PS'),
            ('인천항', 'IS')
        ]
        
        total = len(ym_list) * len(ports)
        current = 0
        
        print("\n" + "=" * 70)
        print("출국항별 출국자수 조회")
        print("=" * 70)
        
        for ym in ym_list:
            for port_name, port_cd in ports:
                current += 1
                print(f"[{current}/{total}] {ym} - {port_name}")
                
                try:
                    response = self.get_overseas_tourist_stats(
                        ym=ym,
                        port_cd=port_cd,
                        num_of_rows=1000
                    )
                    
                    total_num = 0
                    if response['items']:
                        for item in response['items']:
                            total_num += int(item.get('num', 0))
                    
                    results.append({
                        '년월': ym,
                        '출국항': port_name,
                        '출국자수': total_num
                    })
                    
                    print(f"  ✓ {total_num:,}명")
                    
                except Exception as e:
                    print(f"  ✗ 오류: {e}")
                    results.append({
                        '년월': ym,
                        '출국항': port_name,
                        '출국자수': None
                    })
        
        return pd.DataFrame(results)
    
    def get_comprehensive_stats(self, start_ym: str, end_ym: str) -> Dict[str, pd.DataFrame]:
        """
        종합 통계 조회 (전체, 성별, 연령대별, 출국항별)
        """
        print("=" * 70)
        print("국민해외관광객 종합 세부통계 수집")
        print("=" * 70)
        
        stats = {}
        
        print("\n[1/4] 월별 전체 출국자수")
        stats['total'] = self.get_monthly_total(start_ym, end_ym)
        
        print("\n[2/4] 성별 출국자수")
        stats['by_sex'] = self.get_by_sex(start_ym, end_ym)
        
        print("\n[3/4] 연령대별 출국자수")
        stats['by_age'] = self.get_by_age(start_ym, end_ym)
        
        print("\n[4/4] 출국항별 출국자수")
        stats['by_port'] = self.get_by_port(start_ym, end_ym)
        
        print("\n" + "=" * 70)
        print("데이터 수집 완료")
        print("=" * 70)
        
        return stats
    
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


def main():
    """메인 실행 함수"""
    
    SERVICE_KEY = "2o6NG3ixxDgGQ9S4dWUgsMac9WlxfX46+JvFRsAlsXQ6xVi6CZewvNJvbHd4S7exkWwt3YWoKSdwvUNb46kSTQ=="
    
    print("=" * 70)
    print("국민해외관광객세부통계 데이터 수집")
    print("=" * 70)
    
    # API 초기화
    api = OverseasTouristStatsAPI(SERVICE_KEY, debug=False, delay=2.0)
    
    # 연결 테스트
    if not api.test_connection():
        print("\n프로그램을 종료합니다.")
        return
    
    # 조회 설정
    START_YM = "202401"
    END_YM = "202403"  # 테스트용 짧은 기간
    
    print(f"\n조회 기간: {START_YM} ~ {END_YM}")
    print("⚠️  주의: 세부 통계는 API 호출이 많습니다.")
    print("  - 성별: 2개 × 개월수")
    print("  - 연령대: 8개 × 개월수")
    print("  - 출국항: 6개 × 개월수")
    
    input("\n계속하려면 Enter를 누르세요...")
    
    # 종합 통계 수집
    start_time = time.time()
    
    stats = api.get_comprehensive_stats(START_YM, END_YM)
    
    end_time = time.time()
    
    # 결과 출력
    print("\n\n" + "=" * 70)
    print("수집 결과")
    print("=" * 70)
    
    print("\n[1] 월별 전체 출국자수")
    print(stats['total'])
    
    print("\n[2] 성별 출국자수")
    print(stats['by_sex'])
    
    print("\n[3] 연령대별 출국자수")
    print(stats['by_age'].head(10))
    
    print("\n[4] 출국항별 출국자수")
    print(stats['by_port'].head(10))
    
    # CSV 저장
    print("\n" + "=" * 70)
    print("CSV 파일 저장")
    print("=" * 70)
    
    stats['total'].to_csv('overseas_total.csv', index=False, encoding='utf-8-sig')
    print("✓ overseas_total.csv")
    
    stats['by_sex'].to_csv('overseas_by_sex.csv', index=False, encoding='utf-8-sig')
    print("✓ overseas_by_sex.csv")
    
    stats['by_age'].to_csv('overseas_by_age.csv', index=False, encoding='utf-8-sig')
    print("✓ overseas_by_age.csv")
    
    stats['by_port'].to_csv('overseas_by_port.csv', index=False, encoding='utf-8-sig')
    print("✓ overseas_by_port.csv")
    
    print("\n" + "=" * 70)
    print("완료!")
    print("=" * 70)
    print(f"\n소요 시간: {(end_time - start_time)/60:.1f}분")


if __name__ == "__main__":
    main()
