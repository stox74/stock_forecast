"""air_tourism_unified_v4.py - 항공사별 수송 통계 통합 수집 → investar 적재

수집 출처
  odp       : 인천공항공사 항공사별 월 여객/화물 (공공데이터포털)
  airportal : 항공정보포털 항공사별 공급석/운항/여객/화물
  kac       : 한국공항공사 항공사별 통계
  bok, fdr  : 참고용 거시지표 (여행비 지출전망 CSI, 유가/환율). DB에는 저장하지 않음
              (유가/환율은 FRED, korea_fx 테이블에 이미 있음)

저장 (investar, 표준 규격 STD-1)
  kr_airline_traffic_info     : 시리즈 설명표 (출처, 지표, 항공사, 구분정보)
  kr_airline_traffic          : 월별 값 최신본 (series_id x period_end)
  kr_airline_traffic_vintage  : 값이 처음 들어오거나 바뀐 이력

수집 기간
  파일 상단 BACKFILL_START 로 출처별 시작월을 정한다 (None 이면 최근 3개월만 갱신).
  실행 시 --start 를 주면 모든 출처에 그 값이 우선 적용된다.

실행 예
  python air_tourism_unified_v4.py                               # 기본: 최근 3개월 갱신 (또는 BACKFILL_START 설정대로)
  python air_tourism_unified_v4.py --start 201501 --end 202608   # 기간 직접 지정 (모든 출처)
  python air_tourism_unified_v4.py --start 202608 --end 202608 --no-db --save-files   # DB 없이 파일 점검

import 시 네트워크 호출 없음.
"""
from __future__ import annotations
import json
import re
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Dict, Iterable, List, Optional, Tuple
import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from pathlib import Path

# ======================================================================
# 사용자 설정
# ======================================================================
# 파일 저장 (CSV/PNG): 당분간 사용 안 함 → 기본 False
#   - 켜는 방법 1: 아래 SAVE_FILES = True 로 변경
#   - 켜는 방법 2: 실행 시 --save-files 옵션 추가
#       예) python air_tourism_unified_v4.py --start 202601 --end 202608 --save-files
#   - 저장 위치: OUTPUT_DIR (기본은 이 파일 옆 output 폴더). 실행 시 --output 경로 로 변경 가능
#   - 저장 파일: raw_{출처}.csv, collection_status.csv, air_tourism_long.csv,
#                air_tourism_monthly_analysis.csv, (--png 시) air_tourism_dashboard.png
SAVE_FILES = False
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# DB 적재: 기본 True. 끄려면 실행 시 --no-db
SAVE_DB = True

# 수집 기간
#   - 기본(None): 지난달 포함 최근 REFRESH_MONTHS 개월만 갱신 (미발표 월은 자동으로 건너뜀)
#   - 과거 자료를 채울 때: 출처별 시작월을 "YYYYMM" 으로 넣고 실행 → 끝나면 다시 None 으로 되돌림
#       예) BACKFILL_START = {"odp": "202201", "airportal": "201501", "kac": "202001"}
#   - 출처별 제공 시작 시점 (참고): airportal 2015-01, kac 2020-01, odp 2022-01
#   - 실행 시 --start 를 주면 그 값이 모든 출처에 우선 적용됨
BACKFILL_START = {"odp": None, "airportal": None, "kac": None}
END_YM = None             # 수집 마지막 월 "YYYYMM". None 이면 지난달
REFRESH_MONTHS = 3        # 기본 갱신 개월 수 (지난달 포함)

# 일시적 오류(서버 끊김 등) 재시도 횟수. 데이터 없음(미발표)은 재시도하지 않음
MONTH_RETRIES = 2

# 인천공항(odp) 수집 대상 항공사. 필요하면 추가/삭제
ICN_AIRLINES = {"KE": "대한항공", "OZ": "아시아나항공", "7C": "제주항공", "AA": "아메리칸항공"}


class EmptyResponse(Exception):
    """해당 월 데이터 없음(미발표 등). 오류가 아니라 '없음'으로 기록한다."""


# AirPortal 엑셀의 스타일 경고는 데이터와 무관하므로 숨김
warnings.filterwarnings("ignore", message="Workbook contains no default style", category=UserWarning)

_SECRETS = []


def _safe_msg(exc, limit=200):
    """오류 메시지를 보여주되 API 키는 *** 로 가린다."""
    msg = f"{type(exc).__name__}: {exc}"
    for k in _SECRETS:
        if k:
            msg = msg.replace(str(k), "***")
    return msg[:limit]


def setup_universal_paths():
    """스크립트 위치와 현재 폴더에서 상위로 올라가며 DATA 폴더를 찾아 sys.path에 추가."""
    seen = set()
    for start in (Path(__file__).resolve().parent, Path.cwd()):
        for parent in [start, *start.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "DATA").exists():
                for p in (str(parent), str(parent / "DATA")):
                    if p not in sys.path:
                        sys.path.insert(0, p)
                return parent
    return None

def month_range(start_yyyymm: str, end_yyyymm: str) -> Iterable[str]:
    cur = datetime.strptime(start_yyyymm, '%Y%m')
    end = datetime.strptime(end_yyyymm, '%Y%m')
    while cur <= end:
        yield cur.strftime('%Y%m')
        cur += relativedelta(months=1)

def to_month_end_ts(yyyymm: str) -> pd.Timestamp:
    return pd.to_datetime(yyyymm, format='%Y%m') + pd.offsets.MonthEnd(0)

def _coerce_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(',', '', regex=False).str.strip(), errors='coerce')

def _safe_json_dumps(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return ''

@dataclass
class ODPIncheonAirlineStatsConfig:
    """수집 기간은 collect()가 월 단위로 넘긴다 (여기서 정하지 않음)."""
    service_key: str
    airlines: Optional[Dict[str, str]] = None
    timeout: int = 30

class ODPIncheonAirlineStatsClient:
    BASE = 'https://apis.data.go.kr/B551177/AviationStatsByAirline'

    def __init__(self, cfg: ODPIncheonAirlineStatsConfig):
        self.cfg = cfg

    @staticmethod
    def _normalize_json(js):
        if isinstance(js, list):
            return js[0] if js else {}
        return js if isinstance(js, dict) else {}

    def _extract_items(self, js) -> List[dict]:
        js = self._normalize_json(js)
        resp = self._normalize_json(js.get('response', js))
        body = self._normalize_json(resp.get('body', resp))
        if not isinstance(body, dict):
            return []
        items_block = body.get('items', None)
        if isinstance(items_block, dict):
            items = items_block.get('item', [])
        elif isinstance(items_block, list):
            items = items_block
        else:
            items = body.get('item', [])
        if items is None:
            items = []
        if isinstance(items, dict):
            items = [items]
        if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and ('item' in items[0]):
            nested = items[0]['item']
            if isinstance(nested, list):
                items = nested
            elif isinstance(nested, dict):
                items = [nested]
        return items

    def _call(self, endpoint: str, params: dict) -> dict:
        url = f'{self.BASE}/{endpoint}'
        r = requests.get(url, params=params, timeout=self.cfg.timeout)
        try:
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(f'ODP HTTP error: status={r.status_code}')
        try:
            return r.json()
        except Exception:
            raise RuntimeError('ODP returned non-JSON data; check service key and endpoint')

    def _fetch_one(self, endpoint: str, yyyymm: str) -> pd.DataFrame:
        params = {'serviceKey': self.cfg.service_key, 'from_month': yyyymm, 'to_month': yyyymm, 'type': 'json'}
        js = self._call(endpoint, params=params)
        items = self._extract_items(js)
        df = pd.DataFrame(items)
        if df.empty:
            return df
        df['yyyymm'] = yyyymm
        df['date'] = to_month_end_ts(yyyymm)
        return df


@dataclass
class AirPortalConfig:
    """수집 기간은 collect()가 월 단위로 넘긴다 (상단 BACKFILL_START 참고)."""
    timeout: int = 30

class AirPortalClient:
    BASE = 'https://www.airportal.go.kr'
    EXCEL_ENDPOINT = '/stats/transport/getDetailedAirTransportStats1Excel.do'

    def __init__(self, cfg: AirPortalConfig):
        self.cfg = cfg

    def download_one_month(self, year: int, month: int) -> Optional[pd.DataFrame]:
        excel_url = f'{self.BASE}{self.EXCEL_ENDPOINT}'
        yyyymm = f'{year}{month:02d}'
        params = {'last_yearmonth': yyyymm, 'this_yearmonth': yyyymm, 'pass_gubun': '4', 'carge_gubun': 'total', 'sn_gubun': 'total', 'airline_gubun': 'total', 'di_gubun': 'total', 'pyn_gubun': 'total', 'arvl_type': 'total'}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'Content-Type': 'application/json;charset=UTF-8', 'Referer': f'{self.BASE}/stats/transport/chartDetail.do'}
        r = requests.post(excel_url, json=params, headers=headers, timeout=self.cfg.timeout)
        if r.status_code != 200:
            raise RuntimeError(f'AIRPORTAL HTTP error: status={r.status_code}')
        if not r.content:
            return None
        df = pd.read_excel(BytesIO(r.content), engine='openpyxl')
        df = df.dropna(axis=1, how='all')
        df = df.loc[:, ~df.columns.astype(str).str.contains('^Unnamed', na=False)]
        if df.empty:
            return None
        df['year'] = year
        df['month'] = month
        df['yyyymm'] = yyyymm
        df['date'] = to_month_end_ts(yyyymm)
        print(f'[AIRPORTAL] {year}-{month:02d} rows={len(df)}')
        return df


@dataclass
class KACConfig:
    """수집 기간은 collect()가 월 단위로 넘긴다 (상단 BACKFILL_START 참고)."""
    pass_type: str = 'C4101'
    cago_type: str = 'C4201'
    timeout: int = 30

class KACClient:
    BASE = 'https://www.airport.co.kr'
    ENTRY_URL = BASE + '/www/cms/frCon/index.do?MENU_ID=1250'
    LIST_URL = BASE + '/www/ajaxf/frFlightStatsSvc/airLineStatsList.do'

    def __init__(self, cfg: KACConfig):
        self.cfg = cfg

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': self.ENTRY_URL})
        s.get(self.ENTRY_URL, timeout=self.cfg.timeout)
        return s

    def fetch_one_month(self, session: requests.Session, yyyymm: str) -> pd.DataFrame:
        payload = {'ST_YY': yyyymm[:4], 'ST_MM': yyyymm[4:], 'EN_YY': yyyymm[:4], 'EN_MM': yyyymm[4:], 'PASS_TYPE': self.cfg.pass_type, 'CAGO_TYPE': self.cfg.cago_type}
        r = session.post(self.LIST_URL, data=payload, timeout=self.cfg.timeout)
        r.raise_for_status()
        data = r.json()
        if not data:
            return pd.DataFrame()
        df = pd.json_normalize(data)
        df['yyyymm'] = yyyymm
        df['date'] = to_month_end_ts(yyyymm)
        return df


    @staticmethod
    def normalize_nested_data(df_raw: pd.DataFrame) -> pd.DataFrame:
        if df_raw is None or df_raw.empty:
            return pd.DataFrame()
        df = df_raw.copy()
        if 'data' not in df.columns:
            return df
        exploded = df.explode('data').reset_index(drop=True)
        exploded = exploded[exploded['data'].apply(lambda x: isinstance(x, dict))].reset_index(drop=True)
        if exploded.empty:
            return pd.DataFrame()
        left = exploded.drop(columns=['data'])
        right = pd.json_normalize(exploded['data'].tolist())
        # 상위/하위에 같은 이름 컬럼이 있으면: 기간 컬럼은 상위 유지, 그 외는 하위(항목 단위) 유지
        clash = [c for c in right.columns if c in left.columns]
        keep_left = [c for c in clash if c in ('yyyymm', 'date')]
        right = right.rename(columns={c: f'item_{c}' for c in keep_left})
        left = left.drop(columns=[c for c in clash if c not in keep_left])
        return pd.concat([left, right], axis=1)

def fetch_oil_fx_fdr(start: str='2010-01-01') -> pd.DataFrame:
    try:
        import FinanceDataReader as fdr
    except Exception as e:
        raise ImportError('FinanceDataReader가 설치되어 있지 않습니다. (pip install finance-datareader)') from e
    data = []
    series_map = {'oil_wti': 'CL=F', 'oil_brent': 'BZ=F', 'usdkrw': 'USD/KRW'}
    for indicator, symbol in series_map.items():
        df = fdr.DataReader(symbol, start).reset_index()
        if 'Date' in df.columns:
            df = df.rename(columns={'Date': 'date'})
        elif 'date' not in df.columns:
            df = df.rename(columns={df.columns[0]: 'date'})
        if 'Close' in df.columns:
            value_col = 'Close'
        elif 'Price' in df.columns:
            value_col = 'Price'
        else:
            raise ValueError(f'[FDR] 가격 컬럼 없음: {symbol}')
        tmp = df[['date', value_col]].rename(columns={value_col: 'value'})
        tmp['indicator'] = indicator
        tmp['source'] = 'FDR'
        tmp['entity_type'] = ''
        tmp['entity_code'] = ''
        tmp['entity_name'] = ''
        tmp['extra_json'] = ''
        data.append(tmp)
    out = pd.concat(data, ignore_index=True)
    out['date'] = pd.to_datetime(out['date'])
    return out[['date', 'indicator', 'value', 'source', 'entity_type', 'entity_code', 'entity_name', 'extra_json']].copy()

def ecos_stat_search(api_key: str, stat_code: str, cycle: str, start_date: str, end_date: str, item_code1: str='', item_code2: str='', item_code3: str='', lang: str='kr', timeout: int=30) -> pd.DataFrame:
    base = 'https://ecos.bok.or.kr/api'
    url = '/'.join([base, 'StatisticSearch', api_key, 'json', lang, '1', '100000', stat_code, cycle, start_date, end_date, item_code1, item_code2, item_code3])
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    rows = js.get('StatisticSearch', {}).get('row', [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns={'TIME': 'date', 'DATA_VALUE': 'value', 'STAT_CODE': 'stat_code', 'STAT_NAME': 'stat_name', 'ITEM_CODE1': 'item_code1', 'ITEM_NAME1': 'item_name1', 'ITEM_CODE2': 'item_code2', 'ITEM_NAME2': 'item_name2', 'ITEM_CODE3': 'item_code3', 'ITEM_NAME3': 'item_name3', 'UNIT_NAME': 'unit'})
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    if cycle == 'M':
        df['date'] = pd.to_datetime(df['date'], format='%Y%m') + pd.offsets.MonthEnd(0)
    else:
        df['date'] = df['date'].astype(str)
    return df

def fetch_travel_spending_expectation_csi_total(api_key: str, start_date: str='200809', end_date: str='202512') -> pd.DataFrame:
    df = ecos_stat_search(api_key=api_key, stat_code='511Y002', cycle='M', start_date=start_date, end_date=end_date, item_code1='FMCCD', item_code2='99988')
    if df.empty:
        return pd.DataFrame(columns=['date', 'indicator', 'value', 'source', 'entity_type', 'entity_code', 'entity_name', 'extra_json'])
    out = df[['date', 'value', 'stat_code', 'stat_name', 'unit', 'item_code1', 'item_name1', 'item_code2', 'item_name2']].copy()
    out['indicator'] = 'csi_travel_spending_expectation_total'
    out['source'] = 'BOK_ECOS'
    out['entity_type'] = ''
    out['entity_code'] = ''
    out['entity_name'] = ''
    out['extra_json'] = [_safe_json_dumps({'stat_code': r['stat_code'], 'stat_name': r['stat_name'], 'unit': r['unit'], 'item_code1': r['item_code1'], 'item_name1': r['item_name1'], 'item_code2': r['item_code2'], 'item_name2': r['item_name2']}) for r in out.to_dict(orient='records')]
    out['value'] = pd.to_numeric(out['value'], errors='coerce')
    return out[['date', 'indicator', 'value', 'source', 'entity_type', 'entity_code', 'entity_name', 'extra_json']].sort_values('date').reset_index(drop=True)

import argparse
import hashlib
import os

LONG_COLUMNS = ['date', 'indicator', 'value', 'source', 'entity_type',
                'entity_code', 'entity_name', 'extra_json']
TIME_FIELDS = {'date', 'yyyymm', 'year', 'month', '년도', '월', '년월'}
ID_FIELDS = {'SEQ', 'seq', '순번', 'RN', 'ROWNUM'}
METRIC_ODP = {'passenger', 'arrPassenger', 'depPassenger',
              'baggage', 'arrBaggage', 'depBaggage'}


TOTAL_LABELS = {'합계', '소계', '계', '총계', '전체', 'total', 'subtotal', 'sum'}


def _is_total(name, code):
    """합계/소계 행 식별. 항공사 상세행과 섞어 더하지 않도록 entity_type='total'로 구분."""
    for v in (name, code):
        t = str(v or '').strip().lower().replace(' ', '')
        if t in TOTAL_LABELS:
            return True
    return False


def normalize_long(raw, source):
    """원본 구분정보 유지. 숫자형 코드/순번은 측정값으로 취급하지 않는다."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df = KACClient.normalize_nested_data(raw) if source == 'KAC_AIRPORT' else raw.copy()
    if not df.columns.is_unique:
        dups = sorted(set(df.columns[df.columns.duplicated()].astype(str)))
        raise ValueError(f'중복 컬럼 {dups[:5]}: raw CSV 확인 필요')
    code = next((c for c in ['airlineCode', 'A_AIRLINE', '항공사코드', 'AIRLINE_CD', 'AIRLINE_CODE'] if c in df), None)
    name = next((c for c in ['airlineName_kr', 'A_AIRKOR', '항공사명', '항공사', 'airlineName', 'AIRLINE_NM'] if c in df), None)
    metadata, metrics = [], []
    for c in df:
        if c in TIME_FIELDS or c in ID_FIELDS or c in (code, name):
            continue
        if source == 'ODP_B551177':
            is_metric = c in METRIC_ODP
        else:
            numeric = _coerce_numeric(df[c])
            is_code = any(x in str(c).lower() for x in ['code', '_cd', '_type', '코드', '구분'])
            is_metric = not is_code and numeric.notna().sum() >= max(1, int(.05 * len(df)))
        (metrics if is_metric else metadata).append(c)
    rows = []
    for _, r in df.iterrows():
        entity_name = '' if name is None or pd.isna(r[name]) else str(r[name])
        entity_code = '' if code is None or pd.isna(r[code]) else str(r[code])
        if not entity_code:
            match = re.search(r'\(([^)]+)\)\s*$', entity_name)
            entity_code = match.group(1) if match else entity_name
        extras = {str(c): (None if pd.isna(r[c]) else str(r[c])) for c in metadata}
        # Keep dimensions in identity; year/month/row sequence do not define a series.
        extra_json = json.dumps(extras, ensure_ascii=False, sort_keys=True)
        for c in metrics:
            value = _coerce_numeric(pd.Series([r[c]])).iloc[0]
            if pd.isna(value) or not float('-inf') < float(value) < float('inf'):
                continue
            if source == 'ODP_B551177':
                prefix = 'icn_airline_pax_' if 'Passenger' in c or c == 'passenger' else 'icn_airline_cargo_tons_'
            else:
                prefix = 'airportal_' if source == 'AIRPORTAL' else 'kac_'
            rows.append(dict(date=r['date'], indicator=prefix + str(c).strip(),
                             value=float(value), source=source,
                             entity_type=('total' if _is_total(entity_name, entity_code)
                                          else 'airline') if code or name else '',
                             entity_code=entity_code, entity_name=entity_name,
                             extra_json=extra_json))
    return pd.DataFrame(rows, columns=LONG_COLUMNS)


def clean_long(df):
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['value'] = pd.to_numeric(df['value'], errors='coerce')
    df = df.dropna(subset=['date', 'value'])
    # Only exact duplicates are removed; conflicting values remain and are flagged.
    return df.drop_duplicates().sort_values(['date', 'source', 'entity_code', 'indicator']).reset_index(drop=True)


def series_key(row):
    return hashlib.sha256(json.dumps([str(row[c]) for c in
        ['source', 'indicator', 'entity_type', 'entity_code', 'entity_name', 'extra_json']],
        ensure_ascii=False).encode('utf-8')).hexdigest()


def monthly_analysis(df):
    """같은 출처/항공사/구분만 비교. FDR 월말값 사용; 누락월 채우기 없음."""
    df = df.copy()
    df['series_id'] = df.apply(series_key, axis=1)
    metadata = df[['series_id', 'source', 'indicator', 'entity_code', 'entity_name', 'extra_json']].drop_duplicates()
    parts = []
    for sid, g in df.groupby('series_id'):
        s = g.set_index('date')['value'].sort_index()
        if s.index.duplicated().any():
            raise ValueError(f'Conflicting observations in series {sid}; inspect long CSV')
        periods = s.index.to_period('M')
        if g['source'].iloc[0] != 'FDR' and periods.duplicated().any():
            raise ValueError(f'Multiple monthly observations in series {sid}')
        s = s.groupby(periods).last()
        full = pd.period_range(s.index.min(), s.index.max(), freq='M')
        s = s.reindex(full)  # missing month = NaN, prevents previous-row lag error
        prev1, prev12 = s.shift(1), s.shift(12)
        part = pd.DataFrame({'date': full.to_timestamp('M'), 'value': s.to_numpy(),
            'mom_pct': ((s / prev1.where(prev1.ne(0)) - 1) * 100).to_numpy(),
            'yoy_pct': ((s / prev12.where(prev12.ne(0)) - 1) * 100).to_numpy(),
            'series_id': sid})
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).merge(metadata, on='series_id', how='left')


# ======================================================================
# DB 적재 (investar, 표준 규격 STD-1)
# ======================================================================
# STD-1 요약: 소문자 snake_case 테이블명 / utf8mb4_general_ci / 기간 = period_end(월말) + freq /
#   수집 시각 first_collected_at, last_collected_at (KST) / 값 변경 이력은 _vintage 테이블 /
#   시계열형 데이터의 단위·구분정보는 설명표(_info)에 둔다.
T_INFO = "kr_airline_traffic_info"
T_DATA = "kr_airline_traffic"
T_VIN = "kr_airline_traffic_vintage"
DB_SOURCES = {"ODP_B551177": ("icn", "인천공항공사 항공사별 통계 (공공데이터포털)"),
              "AIRPORTAL": ("apt", "항공정보포털 항공사별 수송통계"),
              "KAC_AIRPORT": ("kac", "한국공항공사 항공사별 통계")}
_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"

DDL = [
f"""
CREATE TABLE IF NOT EXISTS {T_INFO} (
    series_id          VARCHAR(50)  NOT NULL COMMENT '출처약어_해시16자리',
    series_hash        CHAR(64)     NOT NULL COMMENT '출처+지표+항공사+구분정보 SHA-256',
    source             VARCHAR(20)  NOT NULL COMMENT 'ODP_B551177 | AIRPORTAL | KAC_AIRPORT',
    source_name        VARCHAR(100) NULL,
    indicator          VARCHAR(150) NOT NULL COMMENT '지표명 (출처접두어_원본컬럼명)',
    metric             VARCHAR(100) NULL     COMMENT '원본 컬럼명',
    entity_type        VARCHAR(20)  NULL     COMMENT 'airline 등',
    airline_code       VARCHAR(50)  NULL,
    airline_name       VARCHAR(100) NULL,
    dims_json          TEXT         NULL     COMMENT '구분정보 원본 (국내/국제, 도착/출발, 공항 등)',
    freq               CHAR(1)      NOT NULL DEFAULT 'M',
    unit               VARCHAR(50)  NULL     COMMENT '확인된 경우만 기록, 그 외 출처 원 단위',
    is_active          TINYINT      NOT NULL DEFAULT 1,
    first_collected_at DATETIME     NOT NULL,
    last_collected_at  DATETIME     NOT NULL,
    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (series_id),
    UNIQUE KEY uk_series_hash (series_hash),
    KEY idx_source_indicator (source, indicator),
    KEY idx_airline (airline_code)
) {_OPTS} COMMENT='항공사별 수송통계 시리즈 설명표 (STD-1)'
""",
f"""
CREATE TABLE IF NOT EXISTS {T_DATA} (
    series_id          VARCHAR(50) NOT NULL,
    period_end         DATE        NOT NULL COMMENT '월말일',
    freq               CHAR(1)     NOT NULL DEFAULT 'M',
    value              DOUBLE      NULL,
    first_collected_at DATETIME    NOT NULL COMMENT '최초 관측 시각(KST)',
    last_collected_at  DATETIME    NOT NULL COMMENT '최근 관측 시각(KST)',
    created_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (series_id, period_end),
    KEY idx_period_end (period_end)
) {_OPTS} COMMENT='항공사별 수송통계 월별 값 (STD-1)'
""",
f"""
CREATE TABLE IF NOT EXISTS {T_VIN} (
    id           BIGINT      NOT NULL AUTO_INCREMENT,
    series_id    VARCHAR(50) NOT NULL,
    period_end   DATE        NOT NULL,
    value        DOUBLE      NULL,
    change_type  VARCHAR(10) NOT NULL COMMENT 'new | revised',
    collected_at DATETIME    NOT NULL COMMENT '이 값을 관측한 시각(KST)',
    created_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_key_time (series_id, period_end, collected_at)
) {_OPTS} COMMENT='항공사별 수송통계 관측 이력 (STD-1 vintage)'
""",
]


def _now_kst():
    from datetime import timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None, microsecond=0)


def _unit_guess(source, metric):
    """확실한 경우만 단위 기록 (인천공항 API 명세 기준)."""
    if source == 'ODP_B551177':
        return 'persons' if 'assenger' in metric else 'tons'
    return None


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))


def get_db_engine():
    setup_universal_paths()
    try:
        import config as investar_config
    except ImportError:
        from DATA import config as investar_config
    return investar_config.get_engine(investar_config.get_db_info())


def save_db(df, chunk=2000):
    """항공 수송통계(odp/airportal/kac)를 STD-1 테이블에 적재. bok/fdr은 건너뜀."""
    from sqlalchemy import text
    air = df[df['source'].isin(DB_SOURCES)].copy()
    skipped = sorted(set(df['source']) - set(DB_SOURCES))
    if skipped:
        print(f"[DB] 참고지표는 DB 저장 제외: {', '.join(skipped)}")
    if air.empty:
        print("[DB] 저장할 항공 수송통계가 없습니다.")
        return

    air['series_hash'] = air.apply(series_key, axis=1)
    air['series_id'] = [DB_SOURCES[src][0] + '_' + h[:16] for src, h in zip(air['source'], air['series_hash'])]
    air['period_end'] = pd.to_datetime(air['date']).dt.to_period('M').dt.to_timestamp('M').dt.date

    dup = air.duplicated(['series_id', 'period_end'], keep=False)
    if dup.any():
        bad = air[dup].groupby(['series_id', 'period_end'])['value'].nunique()
        if (bad > 1).any():
            raise ValueError(f"같은 시리즈/월에 서로 다른 값 {int((bad > 1).sum())}건: DB 저장 중단. --save-files로 long CSV를 확인하세요.")
        air = air.drop_duplicates(['series_id', 'period_end'])

    ts = _now_kst()
    info = air.drop_duplicates('series_id')
    info_recs = [{
        'series_id': r.series_id, 'series_hash': r.series_hash, 'source': r.source,
        'source_name': DB_SOURCES[r.source][1], 'indicator': r.indicator[:150],
        'metric': r.indicator.split('_', 1)[1][:100] if r.source != 'ODP_B551177'
                  else r.indicator.replace('icn_airline_pax_', '').replace('icn_airline_cargo_tons_', '')[:100],
        'entity_type': r.entity_type or None, 'airline_code': (r.entity_code or None) and r.entity_code[:50],
        'airline_name': (r.entity_name or None) and r.entity_name[:100], 'dims_json': r.extra_json or None,
        'unit': None, 'ts': ts} for r in info.itertuples()]
    for x in info_recs:
        x['unit'] = _unit_guess(x['source'], x['metric'] or '')
    data_recs = [{'series_id': r.series_id, 'period_end': r.period_end, 'value': float(r.value), 'ts': ts}
                 for r in air.itertuples()]

    sql_info = f"""
    INSERT INTO {T_INFO}
        (series_id, series_hash, source, source_name, indicator, metric, entity_type,
         airline_code, airline_name, dims_json, freq, unit, first_collected_at, last_collected_at)
    VALUES
        (:series_id, :series_hash, :source, :source_name, :indicator, :metric, :entity_type,
         :airline_code, :airline_name, :dims_json, 'M', :unit, :ts, :ts)
    ON DUPLICATE KEY UPDATE
        source_name = VALUES(source_name), unit = COALESCE(VALUES(unit), unit),
        last_collected_at = VALUES(last_collected_at)
    """
    sql_data = f"""
    INSERT INTO {T_DATA} (series_id, period_end, freq, value, first_collected_at, last_collected_at)
    VALUES (:series_id, :period_end, 'M', :value, :ts, :ts)
    ON DUPLICATE KEY UPDATE
        value = VALUES(value),
        first_collected_at = LEAST(first_collected_at, VALUES(first_collected_at)),
        last_collected_at  = GREATEST(last_collected_at, VALUES(last_collected_at))
    """
    sql_vin = f"""
    INSERT INTO {T_VIN} (series_id, period_end, value, change_type, collected_at)
    VALUES (:series_id, :period_end, :value, :change_type, :ts)
    """
    engine = get_db_engine()
    try:
        with engine.begin() as cx:
            for ddl in DDL:
                cx.execute(text(ddl))
            n_series_before = cx.execute(text(f"SELECT COUNT(*) FROM {T_INFO}")).scalar()
            pmin, pmax = min(r['period_end'] for r in data_recs), max(r['period_end'] for r in data_recs)
            existing = {(sid, pe): v for sid, pe, v in cx.execute(
                text(f"SELECT series_id, period_end, value FROM {T_DATA} WHERE period_end BETWEEN :a AND :b"),
                {'a': pmin, 'b': pmax})}
            vin = []
            for r in data_recs:
                key = (r['series_id'], r['period_end'])
                if key not in existing:
                    vin.append(dict(r, change_type='new'))
                elif not _same(existing[key], r['value']):
                    vin.append(dict(r, change_type='revised'))
            for i in range(0, len(info_recs), chunk):
                cx.execute(text(sql_info), info_recs[i:i + chunk])
            for i in range(0, len(data_recs), chunk):
                cx.execute(text(sql_data), data_recs[i:i + chunk])
            for i in range(0, len(vin), chunk):
                cx.execute(text(sql_vin), vin[i:i + chunk])
            n_series_after = cx.execute(text(f"SELECT COUNT(*) FROM {T_INFO}")).scalar()
        db = engine.url.database
    finally:
        engine.dispose()
    n_new = sum(1 for v in vin if v['change_type'] == 'new')
    n_rev = len(vin) - n_new
    print(f"[DB] {db}: 시리즈 {len(info_recs)}개(신규 {n_series_after - n_series_before}), "
          f"값 {len(data_recs):,}건 저장 (신규 {n_new:,}, 수정 {n_rev:,})")
    by_src = air.groupby('source').agg(series=('series_id', 'nunique'), rows=('value', 'size'),
                                       first=('period_end', 'min'), last=('period_end', 'max'))
    print(by_src.to_string())


def plot_dashboard(monthly, outpath):
    import matplotlib.pyplot as plt
    series = monthly[['series_id', 'source', 'indicator', 'entity_code']].drop_duplicates()
    # Select interpretable ODP and macro series, exclude aggregate AirPortal rows.
    series = series[series['source'].isin(['ODP_B551177', 'FDR', 'BOK_ECOS'])].head(9)
    if series.empty:
        print('PNG skipped: no ODP/macro series. Other source data remain in CSV.')
        return
    fig, axes = plt.subplots(len(series), 1, figsize=(12, 3 * len(series)), squeeze=False)
    for ax, (_, r) in zip(axes[:, 0], series.iterrows()):
        g = monthly[monthly['series_id'] == r['series_id']].sort_values('date')
        ax.plot(g['date'], g['value'])
        ax.set_title(f"{r['source']} / {r['indicator']} / {r['entity_code']}")
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(outpath, dpi=160)
    plt.close(fig)


def load_keys():
    setup_universal_paths()
    try:
        from DATA.KEYS import KEYS
    except ImportError:
        KEYS = {}
    return os.environ.get('ODP_SERVICE_KEY') or KEYS.get('ODPD'), os.environ.get('BOK_API_KEY') or KEYS.get('BOK')


SOURCE_CODE = {'odp': 'ODP_B551177', 'airportal': 'AIRPORTAL', 'kac': 'KAC_AIRPORT',
               'bok': 'BOK_ECOS', 'fdr': 'FDR'}


def source_start(source, args):
    """출처별 시작월: 실행 시 --start > 상단 BACKFILL_START > 최근 REFRESH_MONTHS 개월."""
    if args.start:
        return args.start
    st = (BACKFILL_START or {}).get(source)
    if st:
        return str(st)
    return (pd.Period(args.end, freq='M') - (REFRESH_MONTHS - 1)).strftime('%Y%m')


def collect(args, output=None):
    """출처별 원자료와 요청 상태를 남겨 부분 수집 실패를 식별한다."""
    odp_key, bok_key = load_keys()
    _SECRETS[:] = [odp_key, bok_key]
    src_code = {'odp': 'ODP_B551177', 'airportal': 'AIRPORTAL', 'kac': 'KAC_AIRPORT'}
    frames, status = [], []
    plan = {src: source_start(src, args) for src in args.sources}
    print('[수집 계획]')
    for src, st_ym in plan.items():
        n = len(list(month_range(st_ym, args.end)))
        print(f'  {src:<10} {st_ym} ~ {args.end} ({n}개월)')
    print()
    for source in args.sources:
        raw_frames, long_frames = [], []
        src_start = plan[source]
        months = list(month_range(src_start, args.end))
        try:
            if source == 'odp':
                if not odp_key:
                    raise ValueError('ODP_SERVICE_KEY or DATA.KEYS[ODPD] required')
                client = ODPIncheonAirlineStatsClient(ODPIncheonAirlineStatsConfig(odp_key))
                airlines = client.cfg.airlines or ICN_AIRLINES
            elif source == 'airportal':
                client = AirPortalClient(AirPortalConfig())
            elif source == 'kac':
                client = KACClient(KACConfig())
                session = client._new_session()
            for ym in months if source in ('odp', 'airportal', 'kac') else [src_start]:
                for attempt in range(MONTH_RETRIES + 1):
                    try:
                        if source == 'odp':
                            chunks = []
                            for endpoint in ['getTotalNumberOfPassenger', 'getTotalTonsOfCargo']:
                                chunk = client._fetch_one(endpoint, ym)
                                if chunk.empty:
                                    raise EmptyResponse(endpoint)
                                if 'airlineCode' not in chunk:
                                    raise ValueError('airlineCode missing in API response')
                                chunk = chunk[chunk['airlineCode'].isin(airlines)].copy()
                                chunk['airlineName_kr'] = chunk['airlineCode'].map(airlines)
                                chunks.append(chunk)
                            raw = pd.concat(chunks, ignore_index=True)
                        elif source == 'airportal':
                            raw = client.download_one_month(int(ym[:4]), int(ym[4:]))
                        elif source == 'kac':
                            raw = client.fetch_one_month(session, ym)
                        elif source == 'bok':
                            if not bok_key:
                                raise ValueError('BOK_API_KEY or DATA.KEYS[BOK] required')
                            raw = fetch_travel_spending_expectation_csi_total(bok_key, src_start, args.end)
                        else:
                            raw = fetch_oil_fx_fdr(src_start[:4]+'-'+src_start[4:]+'-01')
                            raw = raw[raw['date'] <= to_month_end_ts(args.end)].copy()
                        if raw is None or raw.empty:
                            raise EmptyResponse('응답 행 없음')
                        if source in src_code:
                            # 월 단위로 숫자 값이 실제로 있는지 확인 (미발표 월은 빈 양식/0만 오는 경우가 있음)
                            long_m = normalize_long(raw, src_code[source])
                            if long_m.empty:
                                raise EmptyResponse(f'숫자 값 없음 (응답 {len(raw)}행)')
                            if float(long_m['value'].abs().sum()) == 0.0:
                                raise EmptyResponse(f'값이 모두 0 (응답 {len(raw)}행)')
                            long_frames.append(long_m)
                        raw_frames.append(raw)
                        status.append(dict(source=source, period=ym, status='ok', rows=len(raw), error=''))
                        break
                    except EmptyResponse as exc:
                        status.append(dict(source=source, period=ym, status='empty', rows=0, error=str(exc)))
                        print(f'[EMPTY]  {source} {ym}: 데이터 없음 - {exc} (미발표 가능)')
                        break
                    except Exception as exc:
                        msg = _safe_msg(exc)
                        if attempt < MONTH_RETRIES:
                            print(f'[RETRY]  {source} {ym}: {msg} → {attempt + 1}회 재시도')
                            time.sleep(3 * (attempt + 1))
                            continue
                        status.append(dict(source=source, period=ym, status='failed', rows=0, error=msg))
                        print(f'[FAILED] {source} {ym}: {msg}')
                if source in ('odp', 'airportal', 'kac'):
                    time.sleep(args.delay)
            if source == 'kac':
                session.close()
            if raw_frames:
                raw = pd.concat(raw_frames, ignore_index=True)
                if output is not None:
                    raw.to_csv(output / f'raw_{source}.csv', index=False, encoding='utf-8-sig')
                long = raw if source in ('bok', 'fdr') else pd.concat(long_frames, ignore_index=True)
                frames.append(long)
        except Exception as exc:
            msg = _safe_msg(exc)
            status.append(dict(source=source, period='source', status='failed', rows=0, error=msg))
            print(f'[FAILED] source={source}: {msg}')
    st = pd.DataFrame(status, columns=['source', 'period', 'status', 'rows', 'error'])
    if output is not None:
        st.to_csv(output / 'collection_status.csv', index=False, encoding='utf-8-sig')
    if not st.empty:
        print('\n[수집 상태]')
        print(st.pivot_table(index='source', columns='status', values='period', aggfunc='count', fill_value=0).to_string())
    if not frames:
        if not st.empty and (st['status'] != 'failed').all():
            raise SystemExit('수집 기간에 발표된 데이터가 없습니다 (모두 미발표). --end를 이전 달로 지정해 보세요.')
        raise RuntimeError('수집된 데이터가 없습니다. 위 [FAILED] 메시지와 키/주소를 확인하세요.')
    df = clean_long(pd.concat(frames, ignore_index=True))
    failed_sources = sorted({x['source'] for x in status if x['status'] == 'failed'})
    return df, failed_sources


def main():
    parser = argparse.ArgumentParser(description='항공사별 수송통계 통합 수집 → investar 적재 (관광객 국적별 출입국통계 아님)')
    parser.add_argument('--start', default=None, help='YYYYMM. 주면 모든 출처에 적용 (기본: BACKFILL_START, 없으면 최근 3개월)')
    parser.add_argument('--end', default=None, help='YYYYMM (기본: END_YM, 없으면 지난달)')
    parser.add_argument('--sources', nargs='+', choices=['odp', 'airportal', 'kac', 'bok', 'fdr'], default=['odp', 'airportal', 'kac'])
    parser.add_argument('--output', default=str(OUTPUT_DIR), help='파일 저장 위치 (--save-files 일 때만 사용)')
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--save-files', action='store_true', help='CSV 파일 저장 (기본 꺼짐, SAVE_FILES 참고)')
    parser.add_argument('--png', action='store_true', help='대시보드 PNG 저장 (파일 저장이 켜진 경우)')
    parser.add_argument('--no-db', action='store_true', help='DB 적재 안 함')
    parser.add_argument('--allow-partial', action='store_true', help='요청 실패가 있어도 DB 저장 진행')
    args = parser.parse_args()
    args.end = args.end or END_YM or (pd.Timestamp.today().to_period('M') - 1).strftime('%Y%m')
    for src in args.sources:
        st = source_start(src, args)
        if not (len(st) == 6 and st.isdigit()) or not list(month_range(st, args.end)):
            parser.error(f'{src}: 시작월 {st} 이 잘못됐거나 마지막 월 {args.end} 보다 늦습니다')
    if args.delay < 0:
        parser.error('negative delay')

    save_files = SAVE_FILES or args.save_files
    save_db_flag = SAVE_DB and not args.no_db
    output = None
    if save_files:
        output = Path(args.output).resolve()
        output.mkdir(parents=True, exist_ok=True)

    df, failed_sources = collect(args, output)
    if save_files:
        df.to_csv(output / 'air_tourism_long.csv', index=False, encoding='utf-8-sig')
    df_db = df
    if failed_sources and not args.allow_partial:
        # 실패가 있는 출처만 DB 저장에서 빼고, 문제없는 출처는 저장한다
        codes = {SOURCE_CODE[s] for s in failed_sources}
        df_db = df[~df['source'].isin(codes)]
        print(f"\n[주의] 요청 실패가 있는 출처는 DB 저장을 건너뜁니다: {', '.join(failed_sources)}")
        print("       같은 설정으로 다시 실행하면 됩니다 (이미 저장된 달은 덮어쓰기만 함). "
              "의도적으로 부분 저장하려면 --allow-partial")
    if save_files:
        monthly = monthly_analysis(df)
        monthly.to_csv(output / 'air_tourism_monthly_analysis.csv', index=False, encoding='utf-8-sig')
        if args.png:
            plot_dashboard(monthly, output / 'air_tourism_dashboard.png')
    if save_db_flag:
        if df_db.empty:
            print('[DB] 저장할 데이터가 없습니다.')
        else:
            save_db(df_db)
    print(f'Done: {len(df):,} observations.' + (f' Files: {output}' if save_files else ''))
    if any((BACKFILL_START or {}).values()) and not args.start:
        print('[안내] BACKFILL_START 가 설정돼 있습니다. 백필이 끝났으면 None 으로 되돌려야 평소처럼 최근 3개월만 갱신합니다.')


if __name__ == '__main__':
    main()
