import requests
import pandas as pd

BASE_URL = "https://ecos.bok.or.kr/api"


def ecos_get(service: str, *parts, timeout=30):
    """
    ECOS OpenAPI 공통 호출 헬퍼
    service: 예) "StatisticSearch"
    parts: 서비스별 path 파라미터들
    """
    url = f"{BASE_URL}/{service}/" + "/".join(str(p) for p in parts)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()  # 요청유형 json 사용 전제


def find_stat_table_code(api_key: str, keyword: str, lang="kr", max_rows=1000):
    """
    StatisticTableList로 통계표 검색 후,
    keyword(예: '수출물량지수')가 포함된 통계표코드(stat_code) 후보를 반환
    """
    # StatisticTableList/{key}/{type}/{lang}/{start}/{end}/{search_word}
    data = ecos_get(
        "StatisticTableList",
        api_key, "json", lang, 1, max_rows, keyword
    )

    rows = data.get("StatisticTableList", {}).get("row", [])
    if not rows:
        raise ValueError(f"통계표 검색 결과가 없습니다. keyword={keyword}")

    # 통계표명(STAT_NAME) 기준으로 후보를 정렬/필터
    # (필드명은 ECOS 응답에서 보통 STAT_CODE, STAT_NAME 형태)
    candidates = []
    for r in rows:
        stat_code = r.get("STAT_CODE")
        stat_name = r.get("STAT_NAME")
        if stat_code and stat_name:
            candidates.append((stat_code, stat_name))

    # 우선순위: 통계표명에 keyword가 더 명확히 포함된 것부터
    candidates.sort(key=lambda x: (keyword not in x[1], len(x[1])))
    return candidates


def fetch_series_statistic_search(
    api_key: str,
    stat_code: str,
    item_code: str,
    cycle: str = "M",
    start_date: str = "201001",
    end_date: str = "202512",
    lang: str = "kr",
    max_rows: int = 10000,
):
    """
    첨부 XLS(통계 조회 조건 설정)의 StatisticSearch 스펙 사용:
    StatisticSearch/{key}/{type}/{lang}/{start}/{end}/{stat_code}/{cycle}/{start_date}/{end_date}/{item1}/{item2}/{item3}/{item4}

    - item_code를 통계항목코드1(item1)에 넣고, item2~4는 생략(또는 '?') 처리
    """
    # ECOS는 item2~4를 생략할 수 없는 경우가 있어 보통 '?'로 채웁니다.
    item2 = "?"
    item3 = "?"
    item4 = "?"

    data = ecos_get(
        "StatisticSearch",
        api_key, "json", lang, 1, max_rows,
        stat_code, cycle, start_date, end_date,
        item_code, item2, item3, item4
    )

    rows = data.get("StatisticSearch", {}).get("row", [])
    if not rows:
        # 에러 메시지 구조가 같이 올 수 있어 원문을 보여주기 위함
        raise ValueError(f"조회 결과가 비었습니다. stat_code={stat_code}, item_code={item_code}\n응답키={list(data.keys())}")

    df = pd.DataFrame(rows)

    # 보통 시계열 값 컬럼은 TIME / DATA_VALUE 형태
    # (혹시 다르면 df.columns 찍어서 바꿔주면 됩니다)
    df["TIME"] = df["TIME"].astype(str)
    df["DATA_VALUE"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")

    # 월(M) 기준이면 YYYYMM을 datetime으로
    if cycle == "M":
        df["date"] = pd.to_datetime(df["TIME"], format="%Y%m")
    elif cycle == "Q":
        # 예: 2015Q1 형태일 수 있음 → 필요 시 별도 파싱
        df["date"] = df["TIME"]
    else:
        df["date"] = df["TIME"]

    # 깔끔한 정리
    out = df[["date", "DATA_VALUE", "ITEM_NAME1", "STAT_NAME"]].copy()
    out = out.rename(columns={"DATA_VALUE": "value", "ITEM_NAME1": "item_name", "STAT_NAME": "stat_name"})
    out = out.sort_values("date").reset_index(drop=True)
    return out


if __name__ == "__main__":
    API_KEY = "여기에_본인_ECOS_API_KEY"

    # 1) 통계표코드 후보 찾기
    candidates = find_stat_table_code(API_KEY, keyword="수출물량지수", lang="kr")

    print("통계표코드 후보(상위 10개):")
    for c in candidates[:10]:
        print("-", c[0], c[1])

    # 2) 보통 1순위 후보로 시도 (안 되면 2~3개를 바꿔가며 테스트)
    stat_code = candidates[0][0]

    # 3) 반도체 / 운송장비 수출물량지수 다운로드
    semi = fetch_series_statistic_search(
        API_KEY,
        stat_code=stat_code,
        item_code="3091AA",          # 반도체
        cycle="M",
        start_date="201001",
        end_date="202512",
        lang="kr"
    )

    transport = fetch_series_statistic_search(
        API_KEY,
        stat_code=stat_code,
        item_code="3122AA",          # 운송장비
        cycle="M",
        start_date="201001",
        end_date="202512",
        lang="kr"
    )

    # 4) 합치기(와이드 형태)
    merged = (
        semi[["date", "value"]].rename(columns={"value": "semi_export_volume_index"})
        .merge(
            transport[["date", "value"]].rename(columns={"value": "transport_export_volume_index"}),
            on="date",
            how="outer"
        )
        .sort_values("date")
        .reset_index(drop=True)
    )

    print("\n[반도체] 샘플:")
    print(semi.head(5))

    print("\n[운송장비] 샘플:")
    print(transport.head(5))

    # 5) 저장
    semi.to_csv("ecos_semi_export_volume_index.csv", index=False, encoding="utf-8-sig")
    transport.to_csv("ecos_transport_export_volume_index.csv", index=False, encoding="utf-8-sig")
    merged.to_csv("ecos_export_volume_index_merged.csv", index=False, encoding="utf-8-sig")

    print("\n저장 완료:")
    print("- ecos_semi_export_volume_index.csv")
    print("- ecos_transport_export_volume_index.csv")
    print("- ecos_export_volume_index_merged.csv")
