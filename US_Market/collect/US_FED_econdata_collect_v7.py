"""
US_FED_econdata_collect_v7.py

1) 시장지수/원자재 일별 가격      -> Market_Index_Data   (v2와 동일)
2) 미국 경제지표 + 위험 + 밸류에이션 -> US_Econ_Data (롱 포맷), US_Econ_Meta (지표 설명)

데이터 소스
- FRED : 금리, 물가, 생산, 고용, 소비, 주택, 위험(VIX, OAS 등), 유동성
         API 키 순서: 환경변수 FRED_API_KEY -> DATA/KEYS.py 의 KEYS['FRED'] -> DATA/config.py 의 FRED_API_KEY
         (키 없는 fredgraph.csv는 서버가 자동 요청을 끊는 경우가 많아 최후 수단으로만 사용)
- multpl.com : CAPE(Shiller PE), S&P500 PE, 배당수익률, 이익수익률
- OECD SDMX  : 경기선행지수(CLI), 기업신뢰지수(BCI), 소비자신뢰지수(CCI) - 국가별
- 파생지표 : YoY 변화율, 월간 증감, 버핏지표, 초과 CAPE 수익률, 실질 기준금리

실행 예
    python US_FED_econdata_collect_v7.py               # 전체 (시장 + 경제)
    python US_FED_econdata_collect_v7.py --only econ   # 경제지표만
    python US_FED_econdata_collect_v7.py --full        # 전 기간 다시 업로드

필요 패키지: pandas numpy pymysql requests FinanceDataReader (yfinance: KRX 장애 대비용)
"""
import argparse
import html
import io
import os
import re
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pymysql
import requests

# ===============================================================
# 공통 설정
# ===============================================================
DB_CONFIG = {
    'host': os.environ.get('INVESTAR_DB_HOST', 'hystox74.synology.me'),
    # 'host': '192.168.0.230',
    'port': int(os.environ.get('INVESTAR_DB_PORT', 3307)),
    'user': os.environ.get('INVESTAR_DB_USER', 'stox7412'),
    'passwd': os.environ.get('INVESTAR_DB_PASS', 'Apt106503!~'),
    'db': os.environ.get('INVESTAR_DB_NAME', 'investar'),
}

HTTP_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
}



# DATA 폴더 후보 (노트북 / 데스크탑)
DATA_DIR_CANDIDATES = [
    r'C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast\DATA',
    r'C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy\DATA',
]


def _data_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = DATA_DIR_CANDIDATES + [os.path.join(here, *(['..'] * n), 'DATA') for n in range(4)]
    seen, out = set(), []
    for d in cands:
        d = os.path.abspath(d)
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _load_module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_fred_api_key():
    """환경변수 -> DATA/KEYS.py (KEYS['FRED']) -> DATA/config.py (FRED_API_KEY) 순으로 찾음."""
    key = os.environ.get('FRED_API_KEY')
    if key:
        return key, '환경변수'
    for d in _data_dirs():
        f = os.path.join(d, 'KEYS.py')
        if os.path.exists(f):
            try:
                key = getattr(_load_module(f, '_user_keys'), 'KEYS', {}).get('FRED')
                if key:
                    return key, f'KEYS.py ({f})'
            except Exception as e:
                print(f"  KEYS.py 읽기 실패({f}): {e}")
    for d in _data_dirs():
        f = os.path.join(d, 'config.py')
        if os.path.exists(f):
            try:
                key = getattr(_load_module(f, '_user_config'), 'FRED_API_KEY', None)
                if key:
                    return key, f'config.py ({f})'
            except Exception:
                pass
    return None, '없음 (DATA/KEYS.py 를 찾지 못함)'


FRED_API_KEY, FRED_KEY_SOURCE = get_fred_api_key()


def get_conn(autocommit=False):
    return pymysql.connect(**DB_CONFIG, autocommit=autocommit)


def _http_get(url, params=None, retries=3, wait=3, timeout=30):
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            time.sleep(wait * (i + 1))
    raise last_err


# ===============================================================
# PART 1. 시장지수 / 원자재 (v2와 동일)
# ===============================================================
MARKET_TABLE = 'Market_Index_Data'
MARKET_START = '1995-01-01'
MARKET_TICKERS = ['KS11', 'KQ11', 'US500', 'RUT', 'NG', 'ZG', 'ZI', 'HG']
YAHOO_FALLBACK = {'KS11': '^KS11', 'KQ11': '^KQ11'}
# 원자재 선물: FDR은 최근 3년 정도만 제공 -> Yahoo 연속 선물(2000년~)을 우선 사용, 실패 시 FDR
YAHOO_PRIMARY = {
    'NG': 'NG=F',   # 천연가스 (NYMEX)
    'ZG': 'GC=F',   # 금 (COMEX)
    'ZI': 'SI=F',   # 은 (COMEX)
    'HG': 'HG=F',   # 구리 (COMEX)
}
MARKET_COLS = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'ticker',
               'daily_return', 'volatility_20d', 'volatility_60d', 'volatility_120d']


def _fetch_fdr(ticker, start_date, retries=3, wait=3):
    import FinanceDataReader as fdr
    last_err = None
    for i in range(retries):
        try:
            df = fdr.DataReader(ticker, start_date)
            if df is not None and not df.empty:
                return df
            last_err = ValueError('빈 데이터 반환')
        except Exception as e:
            last_err = e
        print(f"  [{ticker}] FDR 시도 {i + 1}/{retries} 실패: {str(last_err)[:120]}")
        time.sleep(wait)
    raise last_err


def _fetch_yahoo(symbol, start_date):
    import yfinance as yf
    df = yf.Ticker(symbol).history(start=start_date, auto_adjust=False)
    if df.empty:
        raise ValueError(f'Yahoo에서 {symbol} 데이터 없음')
    df.index = df.index.tz_localize(None)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]


def get_index_data(ticker, start_date):
    if ticker in YAHOO_PRIMARY:
        try:
            df = _fetch_yahoo(YAHOO_PRIMARY[ticker], start_date)
            print(f"  [{ticker}] Yahoo({YAHOO_PRIMARY[ticker]})에서 수집")
            return _add_market_features(df, ticker)
        except Exception as e:
            print(f"  [{ticker}] Yahoo 실패({str(e)[:80]}) -> FDR로 시도")
    try:
        df = _fetch_fdr(ticker, start_date)
    except Exception:
        if ticker not in YAHOO_FALLBACK:
            raise
        print(f"  [{ticker}] FDR(KRX) 수집 실패 -> Yahoo({YAHOO_FALLBACK[ticker]})로 대체")
        df = _fetch_yahoo(YAHOO_FALLBACK[ticker], start_date)
    return _add_market_features(df, ticker)


def _add_market_features(df, ticker):
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    df.index.name = 'Date'
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df = df.reindex(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    df['ticker'] = ticker
    df['daily_return'] = df['Close'].pct_change(fill_method=None)
    for w in (20, 60, 120):
        df[f'volatility_{w}d'] = df['daily_return'].rolling(window=w).std() * np.sqrt(252)
    return df


def upload_market(data_df, batch_size=2000):
    cnx = cursor = None
    try:
        cnx = get_conn()
        cursor = cnx.cursor()
        cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {MARKET_TABLE} (
            Date DATETIME, Open FLOAT, High FLOAT, Low FLOAT, Close FLOAT, Volume FLOAT,
            ticker VARCHAR(10), daily_return FLOAT,
            volatility_20d FLOAT, volatility_60d FLOAT, volatility_120d FLOAT,
            PRIMARY KEY (Date, ticker)
        );""")
        upd = ', '.join(f"{c} = VALUES({c})" for c in MARKET_COLS if c not in ('Date', 'ticker'))
        q = (f"INSERT INTO {MARKET_TABLE} ({', '.join(MARKET_COLS)}) "
             f"VALUES ({', '.join(['%s'] * len(MARKET_COLS))}) ON DUPLICATE KEY UPDATE {upd}")

        df = data_df[MARKET_COLS].copy()
        df['Date'] = [d.to_pydatetime() for d in pd.to_datetime(df['Date'])]
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.astype(object).where(pd.notnull(df), None)
        rows = list(df.itertuples(index=False, name=None))
        for i in range(0, len(rows), batch_size):
            cursor.executemany(q, rows[i:i + batch_size])
            cnx.commit()
        print(f"  {MARKET_TABLE}: {len(rows):,}행 업로드 완료")
    except Exception as e:
        if cnx is not None:
            cnx.rollback()
        print(f"DB 업로드 실패({MARKET_TABLE}): {e}")
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if cnx is not None:
            cnx.close()


def update_market_data():
    print("=" * 60)
    print("[1] 시장지수 / 원자재 수집")
    print("=" * 60)
    df_list, failed = [], []
    for ticker in MARKET_TICKERS:
        print(f"Fetching {ticker}...")
        try:
            df = get_index_data(ticker, MARKET_START)
            df_list.append(df)
            print(f"  [{ticker}] {len(df):,}행 ({df.index.min().date()} ~ {df.index.max().date()})")
        except Exception as e:
            print(f"  [{ticker}] 수집 실패, 건너뜀: {str(e)[:200]}")
            failed.append(ticker)
    if not df_list:
        print("수집된 시장 데이터가 없어 업로드를 중단합니다.")
        return
    data_df = pd.concat(df_list).reset_index()
    if 'index' in data_df.columns:
        data_df.rename(columns={'index': 'Date'}, inplace=True)
    upload_market(data_df)
    if failed:
        print(f"  수집 실패 티커: {failed}")


# ===============================================================
# PART 2. 미국 경제지표 / 위험 / 밸류에이션
# ===============================================================
ECON_TABLE = 'US_Econ_Data'
META_TABLE = 'US_Econ_Meta'
ECON_START = '1950-01-01'
REVISION_WINDOW_DAYS = 730   # 증분 모드에서 다시 덮어쓸 기간 (지표 수정치 반영용)

# transform: 'yoy' = 전년동기대비 %, 'diff' = 전기대비 증감 -> 파생 시리즈(<ID>_YOY, <ID>_DIFF)로 추가 저장
# (series_id, 한글명, 카테고리, 단위, transforms)
FRED_SERIES = [
    # ---------------- 금리 ----------------
    ('DFF',          '연방기금 실효금리',                 '금리', '%', []),
    ('DFEDTARU',     '연방기금 목표금리 상단',            '금리', '%', []),
    ('DGS3MO',       '미국채 3개월',                      '금리', '%', []),
    ('DGS2',         '미국채 2년',                        '금리', '%', []),
    ('DGS5',         '미국채 5년',                        '금리', '%', []),
    ('DGS10',        '미국채 10년',                       '금리', '%', []),
    ('DGS30',        '미국채 30년',                       '금리', '%', []),
    ('T10Y2Y',       '장단기 금리차 10Y-2Y',              '금리', '%p', []),
    ('T10Y3M',       '장단기 금리차 10Y-3M',              '금리', '%p', []),
    ('DFII10',       '10년 TIPS 실질금리',                '금리', '%', []),
    ('THREEFYTP10',  '10년 기간프리미엄(Kim-Wright)',     '금리', '%', []),

    # ---------------- 물가 ----------------
    ('CPIAUCSL',     'CPI 헤드라인',                      '물가', 'index', ['yoy']),
    ('CPILFESL',     'CPI 근원',                          '물가', 'index', ['yoy']),
    ('PCEPI',        'PCE 물가',                          '물가', 'index', ['yoy']),
    ('PCEPILFE',     'PCE 근원 물가',                     '물가', 'index', ['yoy']),
    ('PPIFIS',       'PPI 최종수요',                      '물가', 'index', ['yoy']),
    ('T10YIE',       '10년 기대인플레(BEI)',              '물가', '%', []),
    ('T5YIFR',       '5년 후 5년 기대인플레',             '물가', '%', []),
    ('MICH',         '미시간대 1년 기대인플레',           '물가', '%', []),
    ('DCOILWTICO',   'WTI 유가',                          '물가', 'USD/bbl', []),

    # ---------------- 생산 / 산업 ----------------
    ('GDPC1',        '실질 GDP',                          '생산', 'bil. 2017USD', ['yoy']),
    ('A191RL1Q225SBEA', '실질 GDP 성장률(전기비 연율)',   '생산', '%', []),
    ('INDPRO',       '산업생산지수',                      '산업생산', 'index', ['yoy']),
    ('IPMAN',        '제조업 생산지수',                   '산업생산', 'index', ['yoy']),
    ('TCU',          '설비가동률',                        '산업생산', '%', []),
    ('DGORDER',      '내구재 주문',                       '산업생산', 'mil. USD', ['yoy']),
    ('NEWORDER',     '핵심 자본재 주문(비국방, 항공 제외)', '산업생산', 'mil. USD', ['yoy']),
    ('GACDFSA066MSFRBPHI', '필라델피아 연준 제조업 지수', '산업생산', 'diffusion', []),
    ('GACDINA066MSFRBNY',  '뉴욕 연준 엠파이어 지수',     '산업생산', 'diffusion', []),

    # ---------------- 고용 ----------------
    ('PAYEMS',       '비농업 고용자수',                   '고용', 'thous.', ['diff', 'yoy']),
    ('UNRATE',       '실업률',                            '고용', '%', []),
    ('CIVPART',      '경제활동참가율',                    '고용', '%', []),
    ('ICSA',         '신규 실업수당 청구',                '고용', 'persons', []),
    ('CCSA',         '연속 실업수당 청구',                '고용', 'persons', []),
    ('JTSJOL',       'JOLTS 구인건수',                    '고용', 'thous.', []),
    ('CES0500000003', '평균 시간당 임금',                 '고용', 'USD', ['yoy']),

    # ---------------- 소비 ----------------
    ('RSAFS',        '소매판매',                          '소비', 'mil. USD', ['yoy']),
    ('RRSFS',        '실질 소매판매',                     '소비', 'mil. 1982-84USD', ['yoy']),
    ('PCE',          '개인소비지출',                      '소비', 'bil. USD', ['yoy']),
    ('PCEC96',       '실질 개인소비지출',                 '소비', 'bil. 2017USD', ['yoy']),
    ('UMCSENT',      '미시간대 소비자심리지수',           '소비', 'index', []),
    ('PSAVERT',      '개인 저축률',                       '소비', '%', []),
    ('TOTALSL',      '소비자신용 잔액',                   '소비', 'bil. USD', ['yoy']),

    # ---------------- 주택 ----------------
    ('HOUST',        '주택착공',                          '주택', 'thous. units', ['yoy']),
    ('PERMIT',       '건축허가',                          '주택', 'thous. units', ['yoy']),
    ('HSN1F',        '신규주택판매',                      '주택', 'thous.', ['yoy']),
    ('MSACSR',       '신규주택 재고(개월)',               '주택', 'months', []),
    ('CSUSHPINSA',   '케이스-실러 전국 주택가격지수',     '주택', 'index', ['yoy']),
    ('MSPUS',        '주택 판매가격 중위값',              '주택', 'USD', ['yoy']),
    ('MORTGAGE30US', '30년 고정 모기지 금리',             '주택', '%', []),

    # ---------------- 시장 위험 ----------------
    ('VIXCLS',       'VIX',                               '위험', 'index', []),
    ('BAMLH0A0HYM2', '하이일드 OAS',                      '위험', '%', []),
    ('BAMLH0A3HYC',  'CCC 이하 OAS',                      '위험', '%', []),
    ('BAMLC0A0CM',   '투자등급 회사채 OAS',               '위험', '%', []),
    ('BAMLC0A4CBBB', 'BBB OAS',                           '위험', '%', []),
    ('BAA10Y',       'Moody\'s Baa - 10년물',             '위험', '%p', []),
    ('AAA10Y',       'Moody\'s Aaa - 10년물',             '위험', '%p', []),
    ('STLFSI4',      '세인트루이스 연준 금융스트레스지수', '위험', 'index', []),
    ('NFCI',         '시카고 연준 금융여건지수',          '위험', 'index', []),
    ('DTWEXBGS',     '달러 인덱스(광의, 명목)',           '위험', 'index', []),

    # ---------------- 유동성 ----------------
    ('M2SL',         'M2 통화량',                         '유동성', 'bil. USD', ['yoy']),
    ('WALCL',        '연준 총자산',                       '유동성', 'mil. USD', ['yoy']),
    ('RRPONTSYD',    '역레포(ON RRP) 잔액',               '유동성', 'bil. USD', []),
    ('WTREGEN',      '재무부 일반계정(TGA)',              '유동성', 'bil. USD', []),

    # ---------------- 밸류에이션 재료 (버핏지표용) ----------------
    ('NCBEILQ027S',  '비금융기업 주식 시가총액',          '밸류에이션', 'mil. USD', []),
    ('GDP',          '명목 GDP',                          '밸류에이션', 'bil. USD', []),
]

# multpl.com 월별 테이블: (series_id, 한글명, 단위, url slug)
MULTPL_SERIES = [
    ('CAPE',             'Shiller CAPE (S&P500)', 'ratio', 'shiller-pe'),
    ('SP500_PE',         'S&P500 PER(TTM)',       'ratio', 's-p-500-pe-ratio'),
    ('SP500_DIV_YIELD',  'S&P500 배당수익률',     '%',     's-p-500-dividend-yield'),
    ('SP500_EARN_YIELD', 'S&P500 이익수익률',     '%',     's-p-500-earnings-yield'),
]


# OECD 경기선행지수(CLI) / 기업신뢰(BCI) / 소비자신뢰(CCI)
# 데이터셋: OECD.SDD.STES, DSD_STES@DF_CLI  (진폭조정 AA, 지수 IX, 100 = 장기추세)
OECD_CLI_DATAFLOW = 'OECD.SDD.STES,DSD_STES@DF_CLI'
OECD_AREAS = {
    'USA': '미국', 'KOR': '한국', 'CHN': '중국', 'JPN': '일본', 'DEU': '독일',
    'GBR': '영국', 'G7': 'G7', 'G20': 'G20', 'OECD': 'OECD 전체',
}
OECD_MEASURES = {
    'LI':    ('CLI', '경기선행지수(CLI)', ['diff']),   # diff = 전월 대비 변화 (방향 전환 신호)
    'BCICP': ('BCI', '기업신뢰지수(BCI)', []),
    'CCICP': ('CCI', '소비자신뢰지수(CCI)', []),
}
OECD_START = '1960-01'


# ---------------- 수집 함수 ----------------
def _fred_api(series_id, start, api_key):
    r = _http_get('https://api.stlouisfed.org/fred/series/observations',
                  params={'series_id': series_id, 'api_key': api_key,
                          'file_type': 'json', 'observation_start': start},
                  timeout=60)
    js = r.json()
    if 'error_message' in js:
        raise ValueError(js['error_message'])
    df = pd.DataFrame(js.get('observations', []))
    if df.empty:
        raise ValueError('관측치 없음')
    return pd.Series(pd.to_numeric(df['value'], errors='coerce').values,
                     index=pd.to_datetime(df['date']))


def _fred_csv(series_id, start):
    r = _http_get('https://fred.stlouisfed.org/graph/fredgraph.csv',
                  params={'id': series_id, 'cosd': start}, retries=2, timeout=30)
    if not r.text.lstrip().lower().startswith(('observation_date', 'date')):
        raise ValueError(f'CSV가 아닌 응답: {r.text[:100]}')
    df = pd.read_csv(io.StringIO(r.text))
    return pd.Series(pd.to_numeric(df.iloc[:, 1], errors='coerce').values,
                     index=pd.to_datetime(df.iloc[:, 0]))


def fetch_fred(series_id, start=ECON_START):
    """FRED 시리즈를 pd.Series(Date index)로 반환. API 키가 있으면 API, 실패 시 CSV."""
    if FRED_API_KEY:
        try:
            s = _fred_api(series_id, start, FRED_API_KEY)
        except Exception as e:
            msg = str(e)
            # 시리즈가 없거나 키가 틀린 경우는 CSV로 가도 소용없음
            if 'does not exist' in msg or 'api_key' in msg.lower():
                raise
            s = _fred_csv(series_id, start)
    else:
        s = _fred_csv(series_id, start)
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep='last')]
    if s.empty:
        raise ValueError('유효한 값 없음')
    return s


def fetch_multpl(slug):
    """multpl.com 월별 테이블 -> 매월 1일 값만 남긴 pd.Series."""
    r = _http_get(f'https://www.multpl.com/{slug}/table/by-month')
    dates, vals = [], []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, flags=re.S | re.I):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S | re.I)
        if len(tds) < 2:
            continue
        d_txt = html.unescape(re.sub(r'<[^>]+>', '', tds[0])).strip()
        v_txt = html.unescape(re.sub(r'<[^>]+>', '', tds[1]))
        m = re.search(r'-?\d+(?:\.\d+)?', v_txt.replace(',', ''))
        d = pd.to_datetime(d_txt, format='%b %d, %Y', errors='coerce')
        if m and pd.notna(d):
            dates.append(d)
            vals.append(float(m.group()))
    if not dates:
        raise ValueError('테이블 파싱 실패 (사이트 구조 변경 또는 차단 가능성)')
    s = pd.Series(vals, index=pd.DatetimeIndex(dates)).sort_index()
    s = s[~s.index.duplicated(keep='last')]
    # 당월 중간에 찍히는 실시간 추정치(예: Jul 31)는 제외하고 월초 값만 사용
    return s[s.index.day == 1]


def fetch_oecd_cli(areas=OECD_AREAS, measures=OECD_MEASURES, start=OECD_START):
    """
    OECD SDMX API에서 CLI/BCI/CCI를 한 번에 받아 {(area, measure): pd.Series} 반환.
    키 구조: REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION.TIME_HORIZ.METHODOLOGY
    """
    base = f'https://sdmx.oecd.org/public/rest/data/{OECD_CLI_DATAFLOW}'
    meas = '+'.join(measures)
    params = {'startPeriod': start, 'dimensionAtObservation': 'AllDimensions', 'format': 'csvfile'}
    try:
        # 1차: 필요한 국가만 지정
        r = _http_get(f"{base}/{'+'.join(areas)}.M.{meas}...AA.IX..", params=params, timeout=90)
    except Exception as e:
        # 국가 코드 중 하나가 맞지 않아 거절되는 경우 대비: 전체 국가 받고 코드에서 필터
        print(f"  OECD 국가 지정 조회 실패({str(e)[:80]}) -> 전체 국가로 재조회")
        r = _http_get(f"{base}/.M.{meas}...AA.IX..", params=params, timeout=180)

    df = pd.read_csv(io.StringIO(r.text), dtype=str)
    need = {'REF_AREA', 'MEASURE', 'TIME_PERIOD', 'OBS_VALUE'}
    if not need.issubset(df.columns):
        raise ValueError(f'예상과 다른 응답 형식: {list(df.columns)[:10]}')
    df = df[df['REF_AREA'].isin(list(areas)) & df['MEASURE'].isin(list(measures))].copy()
    df['value'] = pd.to_numeric(df['OBS_VALUE'], errors='coerce')
    df['Date'] = pd.to_datetime(df['TIME_PERIOD'], format='%Y-%m', errors='coerce')
    df = df.dropna(subset=['value', 'Date'])

    out = {}
    for (area, m), g in df.groupby(['REF_AREA', 'MEASURE']):
        # 같은 국가/지표에 방법론 버전이 여러 개면 'H'(현행) 우선
        if 'METHODOLOGY' in g.columns and g['METHODOLOGY'].nunique() > 1:
            g = g[g['METHODOLOGY'] == ('H' if (g['METHODOLOGY'] == 'H').any()
                                       else g['METHODOLOGY'].iloc[0])]
        s = g.set_index('Date')['value'].sort_index()
        out[(area, m)] = s[~s.index.duplicated(keep='last')]
    if not out:
        raise ValueError('OECD 응답에 해당 국가/지표 데이터 없음')
    return out


# ---------------- 파생지표 ----------------
def _infer_periods_per_year(s):
    if len(s) < 3:
        return None
    gap = np.median(np.diff(s.index.values).astype('timedelta64[D]').astype(int))
    if gap <= 4:
        return None          # 일간 지표: YoY 만들지 않음
    if gap <= 10:
        return 52
    if gap <= 45:
        return 12
    if gap <= 120:
        return 4
    return 1


def make_transform(s, kind):
    if kind == 'diff':
        return s.diff()
    if kind == 'yoy':
        p = _infer_periods_per_year(s)
        if p is None:
            return None
        return (s / s.shift(p) - 1) * 100
    raise ValueError(kind)


def _monthly_mean(s):
    m = s.resample('MS').mean()
    return m.dropna()


def make_derived(store):
    """store: {series_id: pd.Series}. 여러 시리즈를 조합한 파생지표 반환."""
    out = {}
    # 버핏지표 = 비금융기업 주식 시가총액 / 명목 GDP (%)
    if 'NCBEILQ027S' in store and 'GDP' in store:
        mcap = store['NCBEILQ027S'] / 1000.0   # 백만 -> 십억 달러
        df = pd.concat([mcap, store['GDP']], axis=1, join='inner').dropna()
        out['BUFFETT_IND'] = (df.iloc[:, 0] / df.iloc[:, 1] * 100,
                              '버핏지표(시가총액/GDP)', '밸류에이션', '%')
    # 초과 CAPE 수익률 = 100/CAPE - 10년 실질금리 (월평균)
    if 'CAPE' in store and 'DFII10' in store:
        df = pd.concat([100.0 / store['CAPE'], _monthly_mean(store['DFII10'])],
                       axis=1, join='inner').dropna()
        out['EXCESS_CAPE_YIELD'] = (df.iloc[:, 0] - df.iloc[:, 1],
                                    '초과 CAPE 수익률(1/CAPE - 10Y TIPS)', '밸류에이션', '%p')
    # 주식 위험프리미엄(간이) = S&P500 이익수익률 - 10년 국채 (월평균)
    if 'SP500_EARN_YIELD' in store and 'DGS10' in store:
        df = pd.concat([store['SP500_EARN_YIELD'], _monthly_mean(store['DGS10'])],
                       axis=1, join='inner').dropna()
        out['EQUITY_YIELD_GAP'] = (df.iloc[:, 0] - df.iloc[:, 1],
                                   '이익수익률 - 10년 국채(Fed 모델)', '밸류에이션', '%p')
    # 실질 기준금리 = 연방기금 실효금리(월평균) - 근원 PCE YoY
    if 'DFF' in store and 'PCEPILFE_YOY' in store:
        df = pd.concat([_monthly_mean(store['DFF']), store['PCEPILFE_YOY']],
                       axis=1, join='inner').dropna()
        out['REAL_FED_FUNDS'] = (df.iloc[:, 0] - df.iloc[:, 1],
                                 '실질 기준금리(FF - 근원PCE YoY)', '금리', '%')
    return out


# ---------------- DB ----------------
def ensure_econ_tables(cursor):
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {ECON_TABLE} (
        Date DATE NOT NULL,
        series_id VARCHAR(40) NOT NULL,
        value DOUBLE,
        PRIMARY KEY (Date, series_id),
        KEY idx_series (series_id, Date)
    );""")
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {META_TABLE} (
        series_id VARCHAR(40) PRIMARY KEY,
        name_kr VARCHAR(100),
        category VARCHAR(20),
        unit VARCHAR(30),
        source VARCHAR(20),
        frequency VARCHAR(10),
        first_date DATE,
        last_date DATE,
        n_obs INT,
        updated_at DATETIME
    );""")


def _freq_label(s):
    p = _infer_periods_per_year(s)
    return {None: 'D', 52: 'W', 12: 'M', 4: 'Q', 1: 'A'}[p]


def upload_econ(records, full_refresh=False, batch_size=5000):
    """
    records: list of dict(series_id, name_kr, category, unit, source, series)
    full_refresh=False 이고 DB에 이미 데이터가 있으면 최근 REVISION_WINDOW_DAYS 구간만 덮어씀.
    (ICE BofA OAS처럼 FRED가 최근 몇 년만 제공하는 시리즈도, 과거 행은 지우지 않으므로 DB에 계속 누적됨)
    """
    cnx = cursor = None
    try:
        cnx = get_conn()
        cursor = cnx.cursor()
        ensure_econ_tables(cursor)

        cursor.execute(f"SELECT series_id FROM {ECON_TABLE} GROUP BY series_id")
        existing = {r[0] for r in cursor.fetchall()}
        cutoff = pd.Timestamp(datetime.today() - timedelta(days=REVISION_WINDOW_DAYS))

        q_data = (f"INSERT INTO {ECON_TABLE} (Date, series_id, value) VALUES (%s, %s, %s) "
                  f"ON DUPLICATE KEY UPDATE value = VALUES(value)")
        q_meta = (f"INSERT INTO {META_TABLE} (series_id, name_kr, category, unit, source, frequency, "
                  f"first_date, last_date, n_obs, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                  f"ON DUPLICATE KEY UPDATE name_kr=VALUES(name_kr), category=VALUES(category), "
                  f"unit=VALUES(unit), source=VALUES(source), frequency=VALUES(frequency), "
                  f"first_date=LEAST(COALESCE(first_date, VALUES(first_date)), VALUES(first_date)), "
                  f"last_date=VALUES(last_date), n_obs=VALUES(n_obs), updated_at=VALUES(updated_at)")

        rows, meta_rows = [], []
        now = datetime.now().replace(microsecond=0)
        for rec in records:
            s = rec['series'].replace([np.inf, -np.inf], np.nan).dropna()
            if s.empty:
                continue
            sid = rec['series_id']
            s_up = s if (full_refresh or sid not in existing) else s[s.index >= cutoff]
            rows.extend((d.date(), sid, float(v)) for d, v in s_up.items())
            meta_rows.append((sid, rec['name_kr'], rec['category'], rec['unit'], rec['source'],
                              _freq_label(s), s.index.min().date(), s.index.max().date(),
                              int(len(s)), now))

        for i in range(0, len(rows), batch_size):
            cursor.executemany(q_data, rows[i:i + batch_size])
            cnx.commit()
            done = min(i + batch_size, len(rows))
            if done == len(rows) or (i // batch_size) % 10 == 9:
                print(f"  {ECON_TABLE} 업로드 {done:,}/{len(rows):,}행")
        cursor.executemany(q_meta, meta_rows)
        cnx.commit()
        print(f"  {META_TABLE}: {len(meta_rows)}개 지표 메타 갱신")
    except Exception as e:
        if cnx is not None:
            cnx.rollback()
        print(f"DB 업로드 실패({ECON_TABLE}): {e}")
        raise
    finally:
        if cursor is not None:
            cursor.close()
        if cnx is not None:
            cnx.close()


# ---------------- 메인 ----------------
def update_econ_data(full_refresh=False):
    print("=" * 60)
    print("[2] 미국 경제지표 / 위험 / 밸류에이션 수집")
    print(f"    FRED 방식: {'API' if FRED_API_KEY else 'fredgraph.csv'} (키 출처: {FRED_KEY_SOURCE})")
    print("=" * 60)
    store, records, failed = {}, [], []

    def add(sid, name, cat, unit, src, s):
        store[sid] = s
        records.append(dict(series_id=sid, name_kr=name, category=cat,
                            unit=unit, source=src, series=s))

    # FRED
    consecutive_fail = 0
    for sid, name, cat, unit, transforms in FRED_SERIES:
        if consecutive_fail >= 5:
            print(f"  [{cat}] {sid:<20} 건너뜀 (FRED 연속 5회 실패 - 접속 차단 상태로 판단)")
            failed.append(sid)
            continue
        try:
            s = fetch_fred(sid)
            add(sid, name, cat, unit, 'FRED', s)
            msg = f"  [{cat}] {sid:<20} {name:<28} {len(s):>6,}건 ~{s.index.max().date()}"
            for t in transforms:
                ts = make_transform(s, t)
                if ts is not None:
                    tid = f"{sid}_{t.upper()}"
                    tname = f"{name} {'YoY' if t == 'yoy' else '전기대비 증감'}"
                    add(tid, tname, cat, '%' if t == 'yoy' else unit, 'DERIVED', ts.dropna())
                    msg += f" +{tid}"
            print(msg)
            consecutive_fail = 0
        except Exception as e:
            print(f"  [{cat}] {sid:<20} 실패: {str(e)[:120]}")
            failed.append(sid)
            if 'does not exist' not in str(e):
                consecutive_fail += 1
        time.sleep(0.6 if FRED_API_KEY else 1.0)   # API 한도: 분당 120회

    # multpl (CAPE 등)
    for sid, name, unit, slug in MULTPL_SERIES:
        try:
            s = fetch_multpl(slug)
            add(sid, name, '밸류에이션', unit, 'MULTPL', s)
            print(f"  [밸류에이션] {sid:<20} {name:<28} {len(s):>6,}건 ~{s.index.max().date()}")
        except Exception as e:
            print(f"  [밸류에이션] {sid:<20} 실패: {str(e)[:120]}")
            failed.append(sid)
        time.sleep(1.5)

    # OECD 경기선행지수 / 기업·소비자신뢰지수
    try:
        oecd = fetch_oecd_cli()
        for area, area_kr in OECD_AREAS.items():
            for m, (short, mname, transforms) in OECD_MEASURES.items():
                if (area, m) not in oecd:
                    print(f"  [선행지표] OECD_{short}_{area:<10} 데이터 없음")
                    continue
                s = oecd[(area, m)]
                sid = f"OECD_{short}_{area}"
                add(sid, f"{area_kr} {mname}", '선행지표', 'index(100=추세)', 'OECD', s)
                msg = f"  [선행지표] {sid:<20} {area_kr + ' ' + mname:<28} {len(s):>6,}건 ~{s.index.max().date()}"
                for t in transforms:
                    ts = make_transform(s, t)
                    if ts is not None:
                        add(f"{sid}_{t.upper()}", f"{area_kr} {mname} 전월대비 변화",
                            '선행지표', 'pt', 'DERIVED', ts.dropna())
                        msg += f" +{sid}_{t.upper()}"
                print(msg)
    except Exception as e:
        print(f"  [선행지표] OECD CLI 수집 실패: {str(e)[:150]}")
        failed.append('OECD_CLI')

    # 파생지표
    for sid, (s, name, cat, unit) in make_derived(store).items():
        add(sid, name, cat, unit, 'DERIVED', s)
        print(f"  [파생] {sid:<20} {name:<28} {len(s):>6,}건 ~{s.index.max().date()}")

    if not records:
        print("수집된 경제지표가 없어 업로드를 중단합니다.")
        return
    upload_econ(records, full_refresh=full_refresh)
    if failed:
        print(f"  수집 실패 지표: {failed}  (다음 실행 때 다시 시도)")


# ---------------- 조회용 헬퍼 (모형에서 사용) ----------------
def load_econ_wide(series_ids, start='1990-01-01', freq='M'):
    """
    DB에서 지표를 읽어 날짜 x 지표 형태로 반환.
    freq='M'이면 월말 기준으로 맞춤 (일간/주간은 월평균, 월간 이하 빈도는 마지막 값을 앞으로 채움).
    """
    cnx = get_conn(autocommit=True)
    try:
        ph = ', '.join(['%s'] * len(series_ids))
        df = pd.read_sql(f"SELECT Date, series_id, value FROM {ECON_TABLE} "
                         f"WHERE series_id IN ({ph}) AND Date >= %s",
                         cnx, params=list(series_ids) + [start], parse_dates=['Date'])
    finally:
        cnx.close()
    wide = df.pivot(index='Date', columns='series_id', values='value').sort_index()
    if freq == 'M':
        try:
            wide = wide.resample('ME').mean().ffill()
        except ValueError:          # pandas < 2.2
            wide = wide.resample('M').mean().ffill()
    elif freq:
        wide = wide.resample(freq).mean().ffill()
    return wide.reindex(columns=list(series_ids))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['market', 'econ'], help='한쪽만 실행')
    ap.add_argument('--full', action='store_true', help='경제지표 전 기간 다시 업로드')
    args = ap.parse_args()

    if args.only in (None, 'market'):
        try:
            update_market_data()
        except Exception as e:
            print(f"시장 데이터 단계 실패: {e}")
    if args.only in (None, 'econ'):
        update_econ_data(full_refresh=args.full)
