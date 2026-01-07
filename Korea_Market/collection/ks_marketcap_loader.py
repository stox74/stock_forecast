# ks_marketcap_loader.py
import sys, os
from pathlib import Path

def add_repo_path():
    """
    stock_forecast 프로젝트 루트를 자동 탐색하고,
    해당 경로를 sys.path에 추가하여 import 오류를 방지합니다.
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        # DATA 폴더가 존재하는 경로를 찾으면 sys.path에 추가
        if (parent / "DATA").exists():
            sys.path.insert(0, str(parent))
            print(f"[INFO] Project root added to sys.path: {parent}")
            return str(parent)
    # 만약 못 찾을 경우 대비 - fallback 경로 지정
    fallback = r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast"
    if os.path.isdir(fallback):
        sys.path.insert(0, fallback)
        print(f"[WARNING] Using fallback path: {fallback}")
        return fallback
    raise FileNotFoundError("❌ DATA 폴더를 찾을 수 없습니다.")

# 경로 추가 실행
project_root = add_repo_path()
import requests
import pandas as pd
from datetime import datetime, timedelta
from pandas.tseries.offsets import BDay
from pykrx import stock
from dateutil.relativedelta import *
from sqlalchemy import create_engine
import warnings
from DATA.stock_invest_function import get_db_host  # 사용자 정의 함수
from sqlalchemy.types import String, Float, Date
import pymysql
import FinanceDataReader as fdr
from io import BytesIO
from dateutil.relativedelta import relativedelta


warnings.simplefilter(action='ignore', category=FutureWarning)


# ==============================
# DB 설정
# ==============================
def get_db_engine():
    db_info = {
        'host': get_db_host(),
        'port': 3307,
        'user' : 'stox7412',
        'password' : 'Apt106503!~',
        'database': 'investar'
    }
    engine = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    return engine


# ==============================
# 시가총액 데이터 수집 함수
# ==============================

KRX_OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_DN_URL  = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"

# 전종목시세 화면 (주식 > 종목시세 > 전종목 시세)
KRX_REF = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"

def _looks_like_valid_otp(s: str) -> bool:
    # 정상 OTP는 보통 길고 랜덤한 문자열입니다.
    # len=6 같은 건 거의 에러/플래그로 봅니다.
    s = (s or "").strip()
    if len(s) < 12:
        return False
    # 가끔 'false' 등 문자도 오므로 숫자/문자 상관없이 길이로 1차 판별
    return True

def get_cap(base_day, debug=True):
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": KRX_REF,
        "Origin": "https://data.krx.co.kr",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
    })

    # ✅ 0) 먼저 화면을 한번 밟아서 세션/쿠키를 확보 (이게 핵심인 경우가 많음)
    try:
        g = sess.get(KRX_REF, timeout=30)
        if debug:
            print(f"[DEBUG] warmup GET {g.status_code}, cookies={len(sess.cookies)}")
    except Exception as e:
        if debug:
            print("[DEBUG] warmup GET failed:", e)

    limit_num = 0
    while limit_num < 10:
        day = base_day + relativedelta(days=-limit_num)
        ymd = day.strftime("%Y%m%d")

        # ✅ 1) OTP 생성
        payload = {
            "mktId": "ALL",
            "trdDd": ymd,
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
            "name": "fileDown",

            # ✅ pagePath 넣어주는 게 필요한 케이스가 있음
            "pagePath": "/contents/MDC/MDI/mdiLoader",

            # ✅ 전종목시세(주식) endpoint
            # (KRX 개편에 따라 바뀔 수 있어, 안 되면 네트워크탭에서 url/bld를 정확히 복사해야 함)
            "url": "dbms/MDC/STAT/standard/MDCSTAT01501",
        }

        r = sess.post(KRX_OTP_URL, data=payload, timeout=30)
        otp = (r.text or "").strip()

        if debug:
            print(f"[DEBUG] {ymd} OTP status={r.status_code}, otp='{otp}', len={len(otp)}")

        if r.status_code != 200 or not _looks_like_valid_otp(otp):
            # OTP가 짧으면 거의 확실히 실패. 다음날로 롤백
            limit_num += 1
            continue

        # ✅ 2) 다운로드
        r2 = sess.post(KRX_DN_URL, data={"code": otp}, timeout=30)

        if debug:
            cd = r2.headers.get("Content-Disposition")
            ct = r2.headers.get("Content-Type")
            print(f"[DEBUG] {ymd} download status={r2.status_code}, bytes={len(r2.content)}, CT={ct}, CD={cd}")

        if r2.status_code != 200 or len(r2.content) < 500:
            # 빈 응답/에러 응답이면 실패 처리
            limit_num += 1
            continue

        # ✅ 3) CSV 파싱
        try:
            df = pd.read_csv(BytesIO(r2.content), encoding="euc-kr")
        except Exception:
            df = pd.read_csv(BytesIO(r2.content), encoding="utf-8")

        if df.shape[1] < 5:
            limit_num += 1
            continue

        # 컬럼 정리(후속 로직과 호환)
        if "종목코드" not in df.columns and "단축코드" in df.columns:
            df = df.rename(columns={"단축코드": "종목코드"})
        if "상장주식수" in df.columns and "Stocks" not in df.columns:
            df = df.rename(columns={"상장주식수": "Stocks"})

        return df

    raise ValueError(f"❌ {base_day.date()} 기준 10일 이내에서 전종목 시총 데이터를 못 받았습니다.")



# ==============================
# 전체 기간 시가총액 데이터프레임 생성
# ==============================
def collect_marketcap(start_date: str, end_date: str) -> pd.DataFrame:
    biz_days = pd.date_range(start=start_date, end=end_date, freq=BDay())
    all_data = []
    for day in biz_days:
        print(f" {day.date()} 시가총액 데이터 수집중...")
        cap_df = get_cap(day)
        cap_df['date'] = day.strftime('%Y-%m-%d')
        all_data.append(cap_df)
    result_df = pd.concat(all_data, ignore_index=True)
    return result_df


# ==============================
# 데이터 전처리 및 long format 변환
# ==============================
def transform_to_long_format(df: pd.DataFrame) -> pd.DataFrame:
    # ✅ KRX 전종목 시세는 보통 '종목코드'를 씁니다
    if "티커" in df.columns:
        df = df.rename(columns={"티커": "ticker"})
    elif "종목코드" in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})
    elif "단축코드" in df.columns:
        df = df.rename(columns={"단축코드": "ticker"})
    else:
        raise KeyError(f"ticker 컬럼을 찾을 수 없습니다. columns={df.columns.tolist()}")

    if "Stocks" in df.columns:
        df = df.rename(columns={"Stocks": "유통주식수"})
    elif "상장주식수" in df.columns:
        df = df.rename(columns={"상장주식수": "유통주식수"})

    df["ticker"] = "A" + df["ticker"].astype(str).str.zfill(6)

    long_df = df.melt(
        id_vars=["date", "ticker"],
        var_name="indicator",
        value_name="value"
    )
    return long_df


# ==============================
# DB 업로드 함수
# ==============================

def upload_to_db(df: pd.DataFrame, table_name: str, engine):
    # 데이터 타입 정의
    dtype_dict = {
        'date': Date(),
        'ticker': String(10),
        'indicator': String(50),
        'value': Float()
    }

    # date 컬럼을 datetime.date 로 변환
    df['date'] = pd.to_datetime(df['date']).dt.date

    # DB 커넥션 + 트랜잭션 직접 관리
    conn = engine.connect()
    trans = conn.begin()
    try:
        # chunksize 로 나누어 insert (예: 5000 행씩)
        df.to_sql(
            name=table_name,
            con=conn,
            if_exists='append',
            index=False,
            dtype=dtype_dict,
            chunksize=5000
        )
        trans.commit()
        print(f"DB 테이블 [{table_name}] 업로드 완료!")
    except Exception as e:
        print(f"DB 업로드 실패: {e}")
        trans.rollback()
    finally:
        conn.close()



# ==============================
# 📌 메인 실행 함수
# ==============================
def main():
    start_date = "2025-10-01"
    end_date = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    table_name = "ks_listed_company_daily_marketcap"

    # 1) 데이터 수집
    result_df = collect_marketcap(start_date, end_date)

    # 2) long format 변환
    long_df = transform_to_long_format(result_df)

    # 3) DB 업로드
    engine = get_db_engine()
    upload_to_db(long_df, table_name, engine)


if __name__ == "__main__":
    main()
