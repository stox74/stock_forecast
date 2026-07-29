import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Tuple, Union


BASE_URL = "http://openapi.tour.go.kr/openapi/service/EdrcntTourismStatsService/getEdrcntTourismStatsList"


def _parse_header(xml_obj: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    """response/header의 resultCode, resultMsg 파싱"""
    header = xml_obj.find("header")
    if header is None:
        return None, None
    code = header.find_text("resultCode")
    msg = header.find_text("resultMsg")
    return code, msg


def _safe_int(x: str) -> Optional[int]:
    try:
        return int(str(x).strip())
    except Exception:
        return None


def fetch_single_month(
    api_key: str,
    country_code: Optional[Union[int, str]],
    ed_code: str,
    ym: str,
    timeout: int = 15,
    retries: int = 2,
    sleep_sec: float = 0.3,
    verbose: bool = False
) -> List[Dict]:
    """
    단일 월(YYYYMM)의 출입국관광통계(관광출입국자수)를 조회.

    가이드 v1.2 기준:
      - YM: 필수 (6자리)
      - NAT_CD: 옵션 (특정 국가 조회 시 사용)
      - ED_CD: 옵션 (E=방한외래관광객, D=국민해외관광객)
    """
    params = {
        "serviceKey": api_key,
        "YM": ym,
        "ED_CD": ed_code,
    }

    # "전체" 처리: NAT_CD 자체를 넣지 않는 방식이 가장 안전
    if country_code is not None:
        cc = str(country_code).strip()
        if cc != "" and cc not in ("0", "000"):
            params["NAT_CD"] = cc

    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(BASE_URL, params=params, timeout=timeout)
            r.raise_for_status()

            xml_obj = BeautifulSoup(r.content, "lxml-xml")
            result_code = xml_obj.find("resultCode")
            result_msg = xml_obj.find("resultMsg")

            if result_code and result_code.text.strip() != "0000":
                # API 레벨 에러
                code = result_code.text.strip()
                msg = result_msg.text.strip() if result_msg else ""
                if verbose:
                    print(f"[API ERROR] YM={ym} code={code} msg={msg}")
                return []

            items = xml_obj.find_all("item")
            data_list = []
            for item in items:
                data_dict = {tag.name: tag.text for tag in item.find_all()}
                data_list.append(data_dict)

            return data_list

        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(sleep_sec * (attempt + 1))
                continue

    if verbose:
        print(f"[FETCH FAIL] YM={ym} err={last_err}")
    return []


def fetch_tourism_data_bulk(
    api_key: str,
    country_code: Optional[Union[int, str]],
    ed_code: str,
    date_list: List[str],
    max_workers: int = 5,
    aggregate_when_total: bool = True,
    timeout: int = 15,
    retries: int = 2,
    verbose: bool = True
) -> pd.DataFrame:
    """
    여러 월의 데이터를 병렬 수집하여 DataFrame으로 반환.

    - country_code가 0/None/'0'/'000'이면 (전체 조회로 간주)
        * NAT_CD를 생략해서 요청
        * 응답이 여러 국적 rows로 오면 aggregate_when_total=True일 때 월별 합계로 집계하여 "전체" 1 row로 변환
    """

    # "전체" 판정
    is_total = (
        country_code is None
        or str(country_code).strip() in ("", "0", "000")
    )

    all_data: List[Dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ym = {
            executor.submit(
                fetch_single_month,
                api_key=api_key,
                country_code=(None if is_total else country_code),
                ed_code=ed_code,
                ym=ym,
                timeout=timeout,
                retries=retries,
                verbose=False
            ): ym
            for ym in date_list
        }

        for future in as_completed(future_to_ym):
            ym = future_to_ym[future]
            try:
                data = future.result()
                all_data.extend(data)
            except Exception as e:
                if verbose:
                    print(f"[WARN] YM={ym} future error: {e}")

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)

    # 가이드 응답 필드(주요): ym, natCd, natKorNm, num, edCd/ed ...
    # 컬럼명 표준화
    rename_map = {
        "rnum": "항목",
        "ed": "구분",
        "edCd": "구분코드",
        "natCd": "국가코드",
        "natKorNm": "국적",
        "num": "인원",
        "ym": "날짜",
    }
    df = df.rename(columns=rename_map)

    # 타입 변환
    if "인원" in df.columns:
        df["인원"] = pd.to_numeric(df["인원"], errors="coerce")

    if "날짜" in df.columns:
        # 월말 날짜로 통일 (YYYYMM -> Timestamp month end)
        df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m") + pd.offsets.MonthEnd(1)

    # "전체"는 월별 합계로 집계해서 1 row로 만들기
    if is_total and aggregate_when_total:
        # 일부 응답은 natKorNm이 공백/None인 경우가 있어도 합계는 가능
        g = df.groupby("날짜", as_index=False)["인원"].sum()
        g["국적"] = "전체"
        g["국가코드"] = "0"
        g["구분코드"] = ed_code
        # 인덱스 설정
        g = g.set_index("날짜").sort_index()
        return g[["국적", "국가코드", "구분코드", "인원"]]

    # 일반(특정 국가) 처리
    df = df.set_index("날짜").sort_index()
    # 보기 좋게 컬럼 순서 정리
    cols = [c for c in ["국적", "국가코드", "구분코드", "구분", "인원"] if c in df.columns]
    df = df[cols]

    return df
