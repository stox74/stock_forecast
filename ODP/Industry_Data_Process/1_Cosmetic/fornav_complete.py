"""
방한외래관광객세부통계조회 API 클라이언트 - 완전판
- 국가별, 성별, 연령대, 여행목적, 입국항별 세부 통계
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
import time


class ForNatVisitorStatsAPI:
    """방한외래관광객세부통계 API 클라이언트"""
    
    BASE_URL = "http://openapi.tour.go.kr/openapi/service"
    
    # 국가 코드
    COUNTRY_CODES = {
        '중국': '112',
        '일본': '130',
        '미국': '275',
        '대만': '113',
        '홍콩': '120',
        '태국': '170',
        '싱가포르': '164',
        '베트남': '185',
        '필리핀': '155',
        '인도': '133',
    }
    
    # 성별 코드
    SEX_CODES = {
        '남성': 'M',
        '여성': 'F',
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
    }
    
    # 여행목적 코드
    PURPOSE_CODES = {
        '관광': '02',
        '상용': '03',
        '업무': '03',  # 상용과 동일
        '친지방문': '04',
        '유학연수': '05',
        '기타': '99',
    }
    
    # 입국항 코드
    PORT_CODES = {
        '인천공항': 'IA',
        '김포공항': 'KA',
        '김해공항': 'PA',
        '제주공항': 'JA',
        '부산항': 'PS',
        '인천항': 'IS',
    }
    
    def __init__(self, service_key: str, debug: bool = False, delay: float = 2.0):
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
    
    def get_foreign_tourist_stats(
        self,
        ym: str,
        nat_cd: Optional[str] = None,
        sex_cd: Optional[str] = None,
        age_cd: Optional[str] = None,
        tra_purp_cd: Optional[str] = None,
        port_cd: Optional[str] = None,
        num_of_rows: int = 1000,
        page_no: int = 1,
        max_retries: int = 3
    ) -> Dict:
        """
        방한외래관광객세부통계조회
        
        Args:
            ym: 년월 (YYYYMM)
            nat_cd: 국가코드
            sex_cd: 성별코드 ('M'=남성, 'F'=여성)
            age_cd: 연령대코드 ('10'=0-10세, '20'=11-20세, ...)
            tra_purp_cd: 여행목적코드 ('02'=관광, '03'=상용, '04'=친지방문, '05'=유학연수)
            port_cd: 입국항코드 ('IA'=인천공항, ...)
            num_of_rows: 한 페이지 결과 수
            page_no: 페이지 번호
            max_retries: 최대 재시도 횟수
        """
        endpoint = f"{self.BASE_URL}/EdrcntTourismStatsService/getForeignTuristStatsList"
        
        params = {
            'serviceKey': self.service_key,
            'YM': ym,
            'numOfRows': num_of_rows,
            'pageNo': page_no,
        }
        
        if nat_cd:
            params['NAT_CD'] = nat_cd
        if sex_cd:
            params['SEX_CD'] = sex_cd
        if age_cd:
            params['AGE_CD'] = age_cd
        if tra_purp_cd:
            params['TRA_PURP_CD'] = tra_purp_cd
        if port_cd:
            params['PORT_CD'] = port_cd
        
        for attempt in range(max_retries):
            try:
                if self.debug:
                    print(f"\n[DEBUG] API 요청")
                    print(f"  파라미터: YM={ym}, NAT_CD={nat_cd}, SEX_CD={sex_cd}, AGE_CD={age_cd}")
                
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
                    print(f"  ⚠️  트래픽 제한. {wait_time}초 대기...")
                    time.sleep(wait_time)
                    continue
                
                if attempt < max_retries - 1:
                    print(f"  오류. {self.delay}초 후 재시도...")
                    time.sleep(self.delay)
                else:
                    raise Exception(f"API 요청 실패: {e}")
        
        raise Exception("API 요청 실패")
    
    def test_connection(self) -> bool:
        """API 연결 테스트"""
        print("\n" + "=" * 70)
        print("방한외래관광객세부통계 API 연결 테스트")
        print("=" * 70)
        
        try:
            print("\n테스트 요청: 2012년 1월 필리핀 여성 31-40세 관광 인천공항")
            
            response = self.get_foreign_tourist_stats(
                ym="201201",
                nat_cd="155",  # 필리핀
                sex_cd="F",    # 여성
                age_cd="40",   # 31-40세
                tra_purp_cd="02",  # 관광
                port_cd="IA"   # 인천공항
            )
            
            print("\n✓ API 연결 성공!")
            print(f"  결과: {response['result_code']} - {response['result_msg']}")
            
            if response['items']:
                item = response['items'][0]
                print(f"\n테스트 데이터:")
                print(f"  국가: {item.get('natKorNm')}")
                print(f"  성별: {item.get('sex')}")
                print(f"  연령: {item.get('age')}")
                print(f"  목적: {item.get('traPurp')}")
                print(f"  입국항: {item.get('port')}")
                print(f"  관광객수: {item.get('num')}명")
            
            print("\n" + "=" * 70)
            return True
            
        except Exception as e:
            print(f"\n✗ 실패: {e}")
            print("=" * 70)
            return False
    
    def get_by_country(self, start_ym: str, end_ym: str, countries: List[str]) -> pd.DataFrame:
        """국가별 입국자수 조회"""
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        total = len(ym_list) * len(countries)
        current = 0
        
        print("\n" + "=" * 70)
        print("국가별 입국자수 조회")
        print("=" * 70)
        
        for ym in ym_list:
            for country in countries:
                current += 1
                nat_cd = self.COUNTRY_CODES.get(country)
                if not nat_cd:
                    continue
                
                print(f"[{current}/{total}] {ym} - {country}")
                
                try:
                    response = self.get_foreign_tourist_stats(ym=ym, nat_cd=nat_cd)
                    
                    total_num = sum(int(item.get('num', 0)) for item in response['items'])
                    
                    results.append({
                        '년월': ym,
                        '국가': country,
                        '국가코드': nat_cd,
                        '입국자수': total_num
                    })
                    
                    print(f"  ✓ {total_num:,}명")
                    
                except Exception as e:
                    print(f"  ✗ 오류: {e}")
                    results.append({
                        '년월': ym,
                        '국가': country,
                        '국가코드': nat_cd,
                        '입국자수': None
                    })
        
        return pd.DataFrame(results)
    
    def get_by_purpose(self, start_ym: str, end_ym: str, countries: List[str]) -> pd.DataFrame:
        """여행목적별 입국자수 조회"""
        results = []
        ym_list = self._generate_month_list(start_ym, end_ym)
        
        purposes = list(self.PURPOSE_CODES.items())
        total = len(ym_list) * len(countries) * len(purposes)
        current = 0
        
        print("\n" + "=" * 70)
        print("여행목적별 입국자수 조회")
        print("=" * 70)
        
        for ym in ym_list:
            for country in countries:
                nat_cd = self.COUNTRY_CODES.get(country)
                if not nat_cd:
                    continue
                
                for purpose_name, purpose_cd in purposes:
                    current += 1
                    print(f"[{current}/{total}] {ym} - {country} - {purpose_name}")
                    
                    try:
                        response = self.get_foreign_tourist_stats(
                            ym=ym,
                            nat_cd=nat_cd,
                            tra_purp_cd=purpose_cd
                        )
                        
                        total_num = sum(int(item.get('num', 0)) for item in response['items'])
                        
                        results.append({
                            '년월': ym,
                            '국가': country,
                            '여행목적': purpose_name,
                            '입국자수': total_num
                        })
                        
                        print(f"  ✓ {total_num:,}명")
                        
                    except Exception as e:
                        print(f"  ✗ 오류: {e}")
                        results.append({
                            '년월': ym,
                            '국가': country,
                            '여행목적': purpose_name,
                            '입국자수': None
                        })
        
        return pd.DataFrame(results)
    
    def get_detailed_profile(self, ym: str, country: str) -> pd.DataFrame:
        """특정 국가의 상세 프로필 (성별, 연령대, 목적별)"""
        nat_cd = self.COUNTRY_CODES.get(country)
        if not nat_cd:
            raise ValueError(f"'{country}' 국가코드 없음")
        
        print(f"\n{country} {ym} 상세 프로필 조회...")
        
        response = self.get_foreign_tourist_stats(ym=ym, nat_cd=nat_cd)
        
        if not response['items']:
            return pd.DataFrame()
        
        df = pd.DataFrame(response['items'])
        
        # 컬럼명 한글화
        df = df.rename(columns={
            'ym': '년월',
            'natKorNm': '국가',
            'sex': '성별',
            'age': '연령대',
            'traPurp': '여행목적',
            'port': '입국항',
            'num': '관광객수'
        })
        
        if '관광객수' in df.columns:
            df['관광객수'] = pd.to_numeric(df['관광객수'], errors='coerce')
        
        print(f"  ✓ {len(df)}개 세부 항목")
        
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


def main():
    """메인 실행"""
    
    SERVICE_KEY = "2o6NG3ixxDgGQ9S4dWUgsMac9WlxfX46+JvFRsAlsXQ6xVi6CZewvNJvbHd4S7exkWwt3YWoKSdwvUNb46kSTQ=="
    
    print("=" * 70)
    print("방한외래관광객세부통계 수집 (완전판)")
    print("=" * 70)
    
    api = ForNatVisitorStatsAPI(SERVICE_KEY, delay=2.0)
    
    if not api.test_connection():
        return
    
    START_YM = "202201"
    END_YM = "202403"
    COUNTRIES = ['중국', '일본', '미국']
    
    print(f"\n조회: {START_YM}~{END_YM}, 국가: {', '.join(COUNTRIES)}")
    input("\nEnter로 계속...")
    
    # 1. 국가별 통계
    print("\n[1/3] 국가별 입국자수")
    df_country = api.get_by_country(START_YM, END_YM, COUNTRIES)
    
    # 2. 여행목적별 통계
    print("\n[2/3] 여행목적별 입국자수")
    df_purpose = api.get_by_purpose(START_YM, END_YM, COUNTRIES)
    
    # 3. 상세 프로필
    print("\n[3/3] 2024년 1월 중국 상세 프로필")
    df_detail = api.get_detailed_profile("202401", "중국")
    
    # 결과 저장
    print("\n" + "=" * 70)
    print("CSV 저장")
    print("=" * 70)
    
    df_country.to_csv('visitor_by_country.csv', index=False, encoding='utf-8-sig')
    print("✓ visitor_by_country.csv")
    
    df_purpose.to_csv('visitor_by_purpose.csv', index=False, encoding='utf-8-sig')
    print("✓ visitor_by_purpose.csv")
    
    if not df_detail.empty:
        df_detail.to_csv('china_202401_profile.csv', index=False, encoding='utf-8-sig')
        print("✓ china_202401_profile.csv")
    
    # 요약
    print("\n" + "=" * 70)
    print("완료!")
    print("=" * 70)
    
    print("\n[국가별 통계 요약]")
    print(df_country.groupby('국가')['입국자수'].sum())
    
    print("\n[여행목적별 통계 요약]")
    print(df_purpose.groupby('여행목적')['입국자수'].sum())
    
    if not df_detail.empty:
        print("\n[중국 2024.01 성별 분포]")
        print(df_detail.groupby('성별')['관광객수'].sum())
        
        print("\n[중국 2024.01 연령대 분포]")
        print(df_detail.groupby('연령대')['관광객수'].sum())


if __name__ == "__main__":
    main()
