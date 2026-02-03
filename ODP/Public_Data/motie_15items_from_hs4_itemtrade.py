# -*- coding: utf-8 -*-
"""
motie_15items_from_hs4_itemtrade.py

산업부 15대 주력품목(근사) = 관세청 품목별(HS4) 월별 수출액 합산
- 국가별 분해 없이 전체(전세계 합계) 기준 (Itemtrade API는 국가 파라미터 없음)

[핵심 안정화]
1) 첨부 노트북과 동일한 엔드포인트/파싱 방식 사용:
   https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList
   + xmltodict.parse() 기반
2) HTTP 500 발생 시 자동 기간 분할(연→반년→분기→월)
3) "1개월 요청만 500"인 HS에 대해 2개월 요청으로 우회 후 해당 월만 필터

[저장 경로]
C:\\Users\\Hoyoung_Park\\OneDrive\\INVESTMENT\\지표상회_복제\\result_files
"""

from __future__ import annotations

import os
import time
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import xmltodict
import pandas as pd


# =============================================================================
# 0) 저장 경로 (고정)
# =============================================================================
RESULT_DIR = r"C:\Users\Hoyoung_Park\OneDrive\INVESTMENT\지표상회_복제\result_files"


# =============================================================================
# 1) 로깅
# =============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# 2) 산업부 15대(근사) HS4 매핑
#    (정확 매칭 아님: "가장 비슷한 HS4 묶음" 근사)
# =============================================================================
MOTIE_15_HS4_MAP: Dict[str, List[str]] = {
    "선박류_산통부":       ["8901", "8902", "8903", "8904", "8905", "8906", "8907", "8908"],
    "무선통신기기_산통부": ["8517", "8525", "8526"],

    # 일반기계(광범위) → 대표 HS4 일부(필요 시 확장)
    "일반기계_산통부":     ["8409", "8413", "8414", "8421", "8429", "8431", "8443", "8456", "8462", "8479"],

    "석유화학_산통부":     ["2901", "2902", "2903", "2904", "2905", "2917", "3901", "3902", "3907", "3908"],
    "철강제품_산통부":     ["7208", "7209", "7210", "7213", "7214", "7225", "7304", "7306"],
    "반도체_산통부":       ["8541", "8542"],
    "자동차_산통부":       ["8702", "8703", "8704"],
    "석유제품_산통부":     ["2710", "2711"],

    "디스플레이_산통부":   ["8528", "9013"],

    "섬유류_산통부":       ["6109", "6110", "6203", "6204", "5407", "5513"],

    "가전_산통부":         ["8418", "8450", "8516", "8509"],
    "자동차부품_산통부":   ["8708"],
    "컴퓨터_산통부":       ["8471"],
    "바이오헬스_산통부":   ["3002", "3004", "9018", "9021", "3304"],
    "이차전지_산통부":     ["8507"],
}

YOY_TARGETS = [
    ("반도체_산통부", "반도체__산통부_수출증감율"),
    ("자동차_산통부", "자동차__산통부_수출증감율"),
]


# =============================================================================
# 3) API 설정 (첨부 노트북과 동일한 엔드포인트)
# =============================================================================
ITEMTRADE_ENDPOINT = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"


@dataclass
class FetchConfig:
    service_key: str
    timeout: int = 30
    retries: int = 4
    request_delay: float = 0.08            # 너무 빠르면 서버가 500을 자주 냄
    backoff_base: float = 0.8              # 지수적 백오프 베이스


# =============================================================================
# 4) 날짜 유틸
# =============================================================================
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())


def _normalize_yymm(value: str) -> str:
    """
    API year 필드가 '2010.01' 또는 '201001' 또는 '총계'로 올 수 있음.
    -> 숫자만 남겨 YYYYMM으로 정규화 (총계는 ''로 떨어짐)
    """
    s = _digits_only(value)
    return s[:6] if len(s) >= 6 else ""


def _next_yymm(yymm: str) -> str:
    y = int(yymm[:4])
    m = int(yymm[4:])
    if m == 12:
        return f"{y+1}01"
    return f"{y}{m+1:02d}"


def _months_between(start_yymm: str, end_yymm: str) -> int:
    sy, sm = int(start_yymm[:4]), int(start_yymm[4:])
    ey, em = int(end_yymm[:4]), int(end_yymm[4:])
    return (ey - sy) * 12 + (em - sm) + 1


def _split_range_mid(start_yymm: str, end_yymm: str) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """
    start~end를 중앙에서 두 구간으로 분할
    """
    sy, sm = int(start_yymm[:4]), int(start_yymm[4:])
    span = _months_between(start_yymm, end_yymm)
    mid_offset = span // 2  # 앞 구간은 mid_offset개월

    # start에서 mid_offset-1 만큼 이동한 yymm이 left_end
    y, m = sy, sm
    for _ in range(mid_offset - 1):
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    left_end = f"{y}{m:02d}"

    right_start = _next_yymm(left_end)
    return (start_yymm, left_end), (right_start, end_yymm)


# =============================================================================
# 5) API 호출 (첨부 노트북 방식: url 쿼리스트링 구성 + xmltodict)
# =============================================================================
def _call_itemtrade_once(session: requests.Session, cfg: FetchConfig,
                         start_yymm: str, end_yymm: str, hs4: str) -> Tuple[pd.DataFrame, Optional[int], Optional[str]]:
    """
    1회 호출. 성공 시 (df, None, None) 반환.
    실패 시 (빈df, http_status, error_message) 반환.
    """
    url = (
        f"{ITEMTRADE_ENDPOINT}"
        f"?serviceKey={cfg.service_key}"
        f"&strtYymm={start_yymm}"
        f"&endYymm={end_yymm}"
        f"&hsSgn={hs4}"
    )

    try:
        resp = session.get(url, timeout=cfg.timeout)
        status = resp.status_code

        if status != 200:
            return pd.DataFrame(), status, f"HTTP {status}"

        # xml -> dict
        json_dict = json.loads(json.dumps(xmltodict.parse(resp.text)))
        items = json_dict.get("response", {}).get("body", {}).get("items")

        if items is None or items.get("item") is None:
            return pd.DataFrame(), None, None  # 빈 데이터(정상)

        item_data = items["item"]
        if isinstance(item_data, dict):
            item_data = [item_data]

        df = pd.DataFrame(item_data)
        return df, None, None

    except Exception as e:
        return pd.DataFrame(), None, str(e)


def fetch_itemtrade_range_adaptive(session: requests.Session, cfg: FetchConfig,
                                  start_yymm: str, end_yymm: str, hs4: str,
                                  depth: int = 0, max_depth: int = 6) -> pd.DataFrame:
    """
    기간(start~end)을 adaptive하게 수집:
    - 우선 해당 기간을 한번에 호출
    - HTTP 500이면 기간을 쪼개서 재귀 수집 (연→반년→분기→월)
    - "1개월 요청만 500"인 경우를 위해, 2개월 요청으로 우회 시도

    반환 DF는 raw 상태(표준화 전).
    """
    # 너무 깊게 쪼개는 것 방지
    if depth > max_depth:
        logger.warning(f"[DEPTH] max_depth 초과: hs4={hs4}, {start_yymm}~{end_yymm}")
        return pd.DataFrame()

    # 1) 1회 호출 시도(재시도 포함)
    last_status = None
    last_err = None

    for attempt in range(1, cfg.retries + 1):
        df, status, err = _call_itemtrade_once(session, cfg, start_yymm, end_yymm, hs4)

        if not df.empty or (status is None and err is None):
            # 성공(또는 정상 빈 응답)
            if cfg.request_delay > 0:
                time.sleep(cfg.request_delay)
            return df

        # 실패
        last_status = status
        last_err = err

        # 500이면 바로 분할로 넘어가고, 그 외는 백오프 후 재시도
        if status != 500:
            time.sleep((cfg.backoff_base ** attempt))
        else:
            break

    # 2) 500 처리 로직
    if last_status == 500:
        span = _months_between(start_yymm, end_yymm)

        # (A) 1개월인데 500이면: 2개월 요청으로 우회해서 해당 월만 필터
        if span == 1:
            # 다음 달이 존재하는 경우: [yymm~next]로 요청 후 해당 yymm만 남김
            next_m = _next_yymm(start_yymm)
            df2, status2, _ = _call_itemtrade_once(session, cfg, start_yymm, next_m, hs4)
            if status2 != 500:
                if cfg.request_delay > 0:
                    time.sleep(cfg.request_delay)
                return df2

            # 그래도 500이면: 어쩔 수 없이 결측 처리
            logger.warning(f"[WARN] fetch 실패: hs4={hs4}, yymm={start_yymm} / err=HTTP 500 (even 2-month fallback)")
            return pd.DataFrame()

        # (B) 2개월 이상이면 중앙 분할
        left, right = _split_range_mid(start_yymm, end_yymm)
        df_l = fetch_itemtrade_range_adaptive(session, cfg, left[0], left[1], hs4, depth + 1, max_depth)
        df_r = fetch_itemtrade_range_adaptive(session, cfg, right[0], right[1], hs4, depth + 1, max_depth)
        if df_l.empty:
            return df_r
        if df_r.empty:
            return df_l
        return pd.concat([df_l, df_r], ignore_index=True)

    # 3) 500이 아닌 기타 에러: 일단 경고 후 빈 DF
    logger.warning(f"[WARN] fetch 실패: hs4={hs4}, {start_yymm}~{end_yymm} / err={last_err}")
    return pd.DataFrame()


# =============================================================================
# 6) 표준화
# =============================================================================
def normalize_itemtrade_df(raw: pd.DataFrame) -> pd.DataFrame:
    """
    raw -> 표준화:
    - statYymm (YYYYMM)
    - expDlr (float)
    - impDlr (float)
    - trdbalDlr (float)
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["statYymm", "expDlr", "impDlr", "trdbalDlr"])

    cols = raw.columns.tolist()
    lower = {c.lower(): c for c in cols}

    def pick(*names: str) -> Optional[str]:
        for nm in names:
            k = nm.lower()
            if k in lower:
                return lower[k]
        return None

    c_year = pick("year", "statyymm", "yyyymm", "yymm")
    c_exp = pick("expdlr", "expDlr")
    c_imp = pick("impdlr", "impDlr")
    c_bal = pick("balpayments", "balPayments", "trdbaldlr", "trdbalDlr")

    if c_year is None:
        return pd.DataFrame(columns=["statYymm", "expDlr", "impDlr", "trdbalDlr"])

    out = pd.DataFrame()
    out["statYymm"] = raw[c_year].map(_normalize_yymm)
    out = out[out["statYymm"].str.len().eq(6)].copy()

    if out.empty:
        return pd.DataFrame(columns=["statYymm", "expDlr", "impDlr", "trdbalDlr"])

    out["expDlr"] = pd.to_numeric(raw.loc[out.index, c_exp], errors="coerce") if c_exp else pd.NA
    out["impDlr"] = pd.to_numeric(raw.loc[out.index, c_imp], errors="coerce") if c_imp else pd.NA

    if c_bal:
        out["trdbalDlr"] = pd.to_numeric(raw.loc[out.index, c_bal], errors="coerce")
    else:
        out["trdbalDlr"] = out["expDlr"] - out["impDlr"]

    # '총계' 같은 것이 섞여 들어오는 케이스 방어(이미 제거되지만 혹시 모를)
    out = out.dropna(subset=["statYymm"]).copy()

    # 월 중복 시 합산
    out = (
        out.groupby("statYymm", as_index=False)[["expDlr", "impDlr", "trdbalDlr"]]
        .sum(min_count=1)
        .sort_values("statYymm")
        .reset_index(drop=True)
    )
    return out


# =============================================================================
# 7) HS4 수집 (월 단위가 아니라 "연단위 + 500시 분할" 방식)
#    → 2710처럼 "1개월만 500"인 케이스는 2개월 우회로 처리됨
# =============================================================================
def collect_hs4_monthly_exports(service_key: str, hs4_list: List[str],
                               start_year: int, end_year: int) -> pd.DataFrame:
    """
    hs4_list 전체에 대해 월별 expDlr 수집
    전략:
    - 기본은 연단위(YYYY01~YYYY12) 한번에 시도
    - 500이면 자동 분할(반년/분기/월) + 1개월 500이면 2개월 우회
    반환: [statYymm, hs4, expDlr]
    """
    cfg = FetchConfig(service_key=service_key)
    session = requests.Session()

    frames: List[pd.DataFrame] = []

    for hs4 in hs4_list:
        logger.info(f"[HS4] {hs4} 수집 시작")

        for year in range(start_year, end_year + 1):
            start = f"{year}01"
            end = f"{year}12"

            raw = fetch_itemtrade_range_adaptive(session, cfg, start, end, hs4)
            norm = normalize_itemtrade_df(raw)

            if not norm.empty:
                norm["hs4"] = hs4
                frames.append(norm[["statYymm", "hs4", "expDlr"]])
                logger.info(f"  {year}: ✅ {len(norm)}개월")
            else:
                logger.warning(f"  {year}: ⚠️ 데이터 없음 (hs4={hs4})")

            # HS4/연도 사이에도 약간 쉬어주기(서버 안정)
            time.sleep(max(cfg.request_delay, 0.05))

    if not frames:
        return pd.DataFrame(columns=["statYymm", "hs4", "expDlr"])

    base = pd.concat(frames, ignore_index=True)

    # 혹시 중복 있으면 방어적으로 재집계
    base = (
        base.groupby(["statYymm", "hs4"], as_index=False)["expDlr"]
        .sum(min_count=1)
        .sort_values(["statYymm", "hs4"])
        .reset_index(drop=True)
    )
    return base


# =============================================================================
# 8) 15대 품목 합산
# =============================================================================
def build_motie_15_series(service_key: str, start_year: int, end_year: int) -> pd.DataFrame:
    # 중복 제거된 전체 HS4 목록
    all_hs4 = sorted({hs4 for hs_list in MOTIE_15_HS4_MAP.values() for hs4 in hs_list})
    logger.info("=" * 80)
    logger.info(f"[INFO] 수집 대상 HS4 총 {len(all_hs4)}개 (중복 제거)")
    logger.info("=" * 80)

    base = collect_hs4_monthly_exports(service_key, all_hs4, start_year, end_year)

    if base.empty:
        logger.error("[ERROR] HS4 수출 데이터가 비어 있습니다.")
        return pd.DataFrame()

    months = pd.DataFrame({"statYymm": sorted(base["statYymm"].unique().tolist())})
    out = months.copy()

    for item_name, hs4_list in MOTIE_15_HS4_MAP.items():
        sub = base[base["hs4"].isin(hs4_list)]
        agg = sub.groupby("statYymm", as_index=False)["expDlr"].sum(min_count=1)
        agg = agg.rename(columns={"expDlr": item_name})
        out = out.merge(agg, on="statYymm", how="left")

    # YoY
    out = out.sort_values("statYymm").reset_index(drop=True)
    for src, yoy in YOY_TARGETS:
        if src in out.columns:
            out[yoy] = out[src].pct_change(12) * 100.0

    # 날짜 파생
    out["년월"] = pd.to_datetime(out["statYymm"], format="%Y%m", errors="coerce")
    out["년도"] = out["년월"].dt.year
    out["월"] = out["년월"].dt.month

    return out


# =============================================================================
# 9) 분석/저장
# =============================================================================
def analyze_motie_15(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        print("⚠️ 분석할 데이터가 없습니다.")
        return

    print("\n" + "=" * 80)
    print("산통부 15대(HS4 근사) 월별 수출액 요약")
    print("=" * 80)

    if "statYymm" in df.columns:
        print(f"기간: {df['statYymm'].min()} ~ {df['statYymm'].max()} / 월수: {len(df)}")

    # 최근 12개월 샘플(너무 길면 일부만)
    show_cols = ["statYymm"] + [c for c in MOTIE_15_HS4_MAP.keys() if c in df.columns]
    show_cols = show_cols[:1 + min(8, len(show_cols) - 1)]
    print("\n최근 12개월(일부 컬럼):")
    print(df.tail(12)[show_cols].to_string(index=False))


def save_motie_15(df: pd.DataFrame, start_year: int, end_year: int, out_dir: str = RESULT_DIR) -> None:
    if df is None or df.empty:
        print("⚠️ 저장할 데이터가 없습니다.")
        return

    _ensure_dir(out_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = os.path.join(out_dir, f"산통부15대_HS4근사_Itemtrade_{start_year}_{end_year}_{ts}.csv")
    xlsx_path = os.path.join(out_dir, f"산통부15대_HS4근사_Itemtrade_{start_year}_{end_year}_{ts}.xlsx")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="월별_15대품목", index=False)

        value_cols = [c for c in MOTIE_15_HS4_MAP.keys() if c in df.columns]
        if value_cols and "년도" in df.columns:
            yearly = df.groupby("년도", as_index=False)[value_cols].sum(min_count=1)
            for c in value_cols:
                yearly[c] = yearly[c] / 1e8  # 달러 → 억달러
            yearly.to_excel(writer, sheet_name="연도별합계_억달러", index=False)

    print("\n✅ 저장 완료")
    print(f" - CSV : {csv_path}")
    print(f" - XLSX: {xlsx_path}")


# =============================================================================
# 10) 엔트리 실행
# =============================================================================
def run(service_key: str, start_year: int = 2010, end_year: int = 2025) -> pd.DataFrame:
    df = build_motie_15_series(service_key=service_key, start_year=start_year, end_year=end_year)
    analyze_motie_15(df)
    save_motie_15(df, start_year, end_year, out_dir=RESULT_DIR)
    return df


if __name__ == "__main__":
    SERVICE_KEY = os.getenv("SERVICE_KEY", "").strip()
    if not SERVICE_KEY:
        raise ValueError("SERVICE_KEY가 비어 있습니다. 환경변수 SERVICE_KEY를 설정하거나 run()에 직접 넣어주세요.")
    run(SERVICE_KEY, 2010, 2025)
