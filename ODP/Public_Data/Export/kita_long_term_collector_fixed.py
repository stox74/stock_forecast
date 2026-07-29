"""
한국무역협회/관세청 무역통계 API - 장기 무역통계 자동 수집 (안전/표준화 버전)

- 컬럼명이 달라도 자동으로 표준화 (statYymm, expDlr, impDlr, trdbalDlr)
- 월 컬럼이 statYm / yyyymm / baseYm 등으로 와도 statYymm(YYYYMM)으로 강제 생성
- 분석 단계에서 KeyError 방지
- 네트워크/파싱 예외 처리 강화 (timeout/retry)
- 저장: CSV + Excel(연도별 요약 시트 포함)
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Dict, Optional, List, Tuple

import pandas as pd
import requests
import xml.etree.ElementTree as ET


# =============================================================================
# 🔹 저장 경로 (고정)
# =============================================================================
RESULT_DIR = r"C:\Users\Hoyoung_Park\OneDrive\INVESTMENT\지표상회_복제\result_files"


# =============================================================================
# 0) 공통 유틸
# =============================================================================
def _safe_text(x: Optional[str]) -> str:
    return "" if x is None else str(x).strip()


def _clean_digits(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"[^0-9]", "", regex=True)


def _looks_like_yyyymm(series: pd.Series, min_ratio: float = 0.7) -> bool:
    """
    이 컬럼 값들이 YYYYMM(6자리 숫자)처럼 보이는지 검사.
    """
    s = _clean_digits(series)
    ok = s.str.len().eq(6) & s.str.startswith(("19", "20"))
    ratio = ok.mean() if len(s) else 0.0
    return ratio >= min_ratio


def ensure_statYymm(df: pd.DataFrame) -> pd.DataFrame:
    """
    어떤 형태로 오든 'statYymm'(YYYYMM)을 만들어준다.
    - 대표 월 컬럼 후보: statYymm, statYm, yyyymm, baseYm, ym, month 등
    - 연/월 분리 컬럼(year + month)인 경우도 결합
    """
    if df is None or df.empty:
        return df

    cols = df.columns.tolist()
    lower_map = {c.lower(): c for c in cols}

    # 1) 직접 후보명 탐색
    month_candidates = [
        "statyymm", "statym", "yyyymm", "yyymm", "baseym", "basemonth",
        "ym", "yearmonth", "stat_yymm", "stat_ym", "ymd", "yyyymmdd"
    ]
    found_month_col = None
    for key in month_candidates:
        if key in lower_map:
            found_month_col = lower_map[key]
            break

    # 2) 값 패턴으로 탐색 (YYYYMM처럼 보이는 컬럼 찾기)
    if found_month_col is None:
        for c in cols:
            # 너무 많은 컬럼 다 검사하지 않게 object/string 계열만 우helps
            if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
                if _looks_like_yyyymm(df[c]):
                    found_month_col = c
                    break

    # 3) 연/월 분리 컬럼 탐색 후 결합
    if found_month_col is None:
        year_keys = ["year", "yyyy", "statyear", "yr", "yy"]
        month_keys = ["month", "mm", "statmonth", "mon", "mn"]

        year_col = next((lower_map[k] for k in year_keys if k in lower_map), None)
        mon_col = next((lower_map[k] for k in month_keys if k in lower_map), None)

        if year_col and mon_col:
            y = _clean_digits(df[year_col]).str.zfill(4)
            m = _clean_digits(df[mon_col]).str.zfill(2)
            df["statYymm"] = (y + m)
            return df

    # 4) 찾은 월 컬럼이 있으면 statYymm로 rename/생성
    if found_month_col is not None:
        if found_month_col != "statYymm":
            df = df.rename(columns={found_month_col: "statYymm"})
        # YYYYMM만 남기기
        df["statYymm"] = _clean_digits(df["statYymm"]).str[:6]
        return df

    # 여기까지 왔으면 월 컬럼을 못 찾은 것
    return df


def normalize_trade_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    API 응답 컬럼명이 케이스/이름이 달라도 아래 표준 컬럼명으로 최대한 맞춘다.
      - statYymm : YYYYMM
      - expDlr   : 수출금액
      - impDlr   : 수입금액
      - trdbalDlr: 무역수지
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    # 월 컬럼 강제 생성/정규화
    df = ensure_statYymm(df)

    cols = df.columns.tolist()
    lower_map = {c.lower(): c for c in cols}

    rename_map: Dict[str, str] = {}

    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            kk = k.lower()
            if kk in lower_map:
                return lower_map[kk]
        return None

    # 수출 후보 (API마다 태그가 다를 수 있으니 폭넓게)
    c_exp = pick("expdlr", "expDlr", "export", "exp_amt", "expamt", "expvalue", "expval", "expusd", "exp_usd")
    # 수입 후보
    c_imp = pick("impdlr", "impDlr", "import", "imp_amt", "impamt", "impvalue", "impval", "impusd", "imp_usd")
    # 무역수지 후보
    c_bal = pick("trdbaldlr", "trdbalDlr", "trdbal", "balance", "tradebalance", "trdbal_usd", "balusd")

    if c_exp and c_exp != "expDlr":
        rename_map[c_exp] = "expDlr"
    if c_imp and c_imp != "impDlr":
        rename_map[c_imp] = "impDlr"
    if c_bal and c_bal != "trdbalDlr":
        rename_map[c_bal] = "trdbalDlr"

    if rename_map:
        df = df.rename(columns=rename_map)

    return df


def coerce_numeric(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """문자열 숫자 컬럼을 안전하게 numeric으로 변환"""
    if df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def make_date_features(df: pd.DataFrame) -> pd.DataFrame:
    """statYymm → 날짜/년도/월 파생"""
    if df is None or df.empty or "statYymm" not in df.columns:
        return df

    df["statYymm"] = _clean_digits(df["statYymm"]).str[:6]
    df = df[df["statYymm"].str.len().eq(6)].copy()
    if df.empty:
        return df

    df = df.sort_values("statYymm").reset_index(drop=True)
    df["년월"] = pd.to_datetime(df["statYymm"], format="%Y%m", errors="coerce")
    df["년도"] = df["년월"].dt.year
    df["월"] = df["년월"].dt.month
    return df


# =============================================================================
# 1) API 호출/파싱
# =============================================================================
def get_trade_data_range(
    service_key: str,
    start_month: str,
    end_month: str,
    timeout: int = 30,
    retry: int = 3,
    sleep_between_retries: float = 0.6,
) -> pd.DataFrame:
    """
    특정 기간(start_month~end_month)의 무역통계 데이터 수집
    """
    url = "https://apis.data.go.kr/1220000/Newtrade/getNewtradeList"
    params = {
        "serviceKey": service_key,
        "strtYymm": start_month,
        "endYymm": end_month,
        "numOfRows": 999,
    }

    last_err: Optional[str] = None

    for _ in range(retry):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()

            root = ET.fromstring(r.content)

            # resultCode가 있을 때만 체크
            rc = root.find(".//resultCode")
            if rc is not None and _safe_text(rc.text) not in ("", "00"):
                return pd.DataFrame()

            items = root.findall(".//item")
            if not items:
                return pd.DataFrame()

            rows: List[Dict[str, str]] = []
            for item in items:
                row = {c.tag: _safe_text(c.text) for c in item}
                if any(v != "" for v in row.values()):
                    rows.append(row)

            return pd.DataFrame(rows)

        except Exception as e:
            last_err = str(e)
            time.sleep(sleep_between_retries)

    if last_err:
        print(f"  오류: {last_err}")
    return pd.DataFrame()


def collect_long_term_trade_data(
    service_key: str,
    start_year: int = 2020,
    end_year: int = 2026,
    sleep_between_years: float = 0.3,
) -> pd.DataFrame:
    """장기간 무역통계 데이터 수집 (연도 단위 호출)"""

    all_data: List[pd.DataFrame] = []

    print("=" * 80)
    print(f"무역통계 데이터 수집: {start_year}년 ~ {end_year}년")
    print("=" * 80)

    for year in range(start_year, end_year + 1):
        start_ym = f"{year}01"
        end_ym = f"{year}12"

        print(f"\n{year}년 데이터 수집 중... ", end="")
        df = get_trade_data_range(service_key, start_ym, end_ym)

        if df is not None and not df.empty:
            all_data.append(df)
            print(f"✅ {len(df)}건")
        else:
            print("⚠️ (빈 응답)")

        time.sleep(sleep_between_years)

    if not all_data:
        print("⚠️ 전체 기간에서 유효 데이터가 없습니다.")
        return pd.DataFrame()

    result_df = pd.concat(all_data, ignore_index=True)

    print("\n" + "=" * 80)
    print("데이터 표준화/처리 중...")
    print("=" * 80)

    # 1) 컬럼 표준화 + statYymm 강제 생성
    result_df = normalize_trade_columns(result_df)

    # 2) 숫자 변환
    result_df = coerce_numeric(result_df, ["expDlr", "impDlr", "trdbalDlr"])

    # 3) 날짜 파생
    result_df = make_date_features(result_df)

    # 4) 중복 제거(가능한 경우)
    if "statYymm" in result_df.columns:
        result_df = result_df.drop_duplicates(subset=["statYymm"]).reset_index(drop=True)

    print(f"✅ 총 {len(result_df)}개 월 데이터 확보 완료")
    return result_df


# =============================================================================
# 2) 분석/저장
# =============================================================================
def analyze_trade_data(df: pd.DataFrame) -> None:
    """무역통계 데이터 분석 (절대 죽지 않는 안전 출력)"""
    if df is None or df.empty:
        print("\n⚠️ 분석할 데이터가 없습니다")
        return

    # 혹시라도 statYymm가 빠졌으면 한 번 더 강제 시도
    if "statYymm" not in df.columns:
        df = normalize_trade_columns(df)
        df = make_date_features(df)

    print("\n" + "=" * 80)
    print("데이터 분석")
    print("=" * 80)

    print(f"\n📋 컬럼: {', '.join(df.columns.tolist())}")

    # 기간
    if "statYymm" in df.columns and len(df["statYymm"]) > 0:
        print("\n📅 데이터 기간:")
        print(f"   시작: {df['statYymm'].min()}")
        print(f"   종료: {df['statYymm'].max()}")
        print(f"   총: {len(df)}개(월)")
    else:
        print("\n⚠️ 'statYymm' 컬럼을 찾지 못했습니다.")
        print("   아래는 상위 3행 샘플입니다(월 컬럼 후보 확인용):")
        print(df.head(3).to_string(index=False))
        return

    # 수출액
    if "expDlr" in df.columns:
        print("\n📊 수출액 통계 (단위: 천 달러)")
        print(f"   평균: ${df['expDlr'].mean():,.0f}")
        print(f"   최대: ${df['expDlr'].max():,.0f}")
        print(f"   최소: ${df['expDlr'].min():,.0f}")
        print(f"   합계: ${df['expDlr'].sum():,.0f}")
    else:
        print("\n⚠️ 'expDlr' 컬럼이 없습니다(수출액 통계 생략).")

    # 무역수지
    if "trdbalDlr" in df.columns:
        surplus = (df["trdbalDlr"] > 0).sum()
        deficit = (df["trdbalDlr"] < 0).sum()
        total = len(df)

        print("\n💰 무역수지:")
        print(f"   흑자: {surplus}개월 ({surplus / total * 100:.1f}%)")
        print(f"   적자: {deficit}개월 ({deficit / total * 100:.1f}%)")

    # 최근 12개월 샘플
    print("\n📋 최근 12개월 샘플:")
    needed = ["statYymm", "expDlr", "impDlr"]
    if all(c in df.columns for c in needed):
        print(df.tail(12)[needed].to_string(index=False))
    else:
        missing = [c for c in needed if c not in df.columns]
        print(f"⚠️ 샘플 출력 불가. 누락 컬럼: {missing}")
        print("   현재 컬럼:", df.columns.tolist())


def save_trade_data(df: pd.DataFrame, start_year: int, end_year: int, out_dir: str = RESULT_DIR) -> None:
    """데이터 저장 (CSV + Excel)"""

    if df is None or df.empty:
        print("⚠️ 저장할 데이터가 없습니다.")
        return

    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_file = os.path.join(out_dir, f"한국무역통계_{start_year}_{end_year}_{timestamp}.csv")
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\n✅ CSV 저장: {csv_file}")

    excel_file = os.path.join(out_dir, f"한국무역통계_{start_year}_{end_year}_{timestamp}.xlsx")
    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="전체데이터", index=False)

        # 연도별 요약(가능할 때만)
        if all(c in df.columns for c in ["년도", "expDlr", "impDlr"]):
            agg_cols = {c: "sum" for c in ["expDlr", "impDlr"] if c in df.columns}
            if "trdbalDlr" in df.columns:
                agg_cols["trdbalDlr"] = "sum"

            yearly = df.groupby("년도").agg(agg_cols).reset_index()
            yearly.to_excel(writer, sheet_name="연도별요약", index=False)

    print(f"✅ Excel 저장: {excel_file}")
