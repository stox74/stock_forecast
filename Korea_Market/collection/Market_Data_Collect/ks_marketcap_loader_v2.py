import os
import time
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
from pandas.tseries.offsets import BDay
import requests

from sqlalchemy import create_engine, text, Table, MetaData
from sqlalchemy.dialects.mysql import insert as mysql_insert

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from DATA.stock_invest_function import get_db_host  # 사용자 정의 함수
from webdriver_manager.chrome import ChromeDriverManager


# ==============================
# DB 설정 (사용자 환경에서 채우기)
# ==============================
def get_db_engine():
    db_info = {
        "host": get_db_host(),
        "port": 3307,
        "user": 'stox7412',
        "password": 'Apt106503!~',
        "database": "investar",
    }
    return create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4",
        pool_pre_ping=True,
    )


def ensure_table_with_pk(engine, table_name: str):
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS `{table_name}` (
        `date` DATE NOT NULL,
        `ticker` VARCHAR(10) NOT NULL,
        `indicator` VARCHAR(50) NOT NULL,
        `value` DOUBLE NULL,
        PRIMARY KEY (`date`, `ticker`, `indicator`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.execute(text(create_sql))
    print(f"[INFO] Table ready (PK enforces uniqueness): {table_name}")


# ==============================
# KRX endpoints (OTP 방식)
# ==============================
KRX_OTP_URL = "https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
KRX_DN_URL  = "https://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"

# 전종목시세 bld (전종목시세)
BLD = "dbms/MDC/STAT/standard/MDCSTAT01501"


def _to_float(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "").replace(" ", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except Exception:
        return None


# ==============================
# Selenium driver
# ==============================
def build_driver(headless: bool = False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # 봇탐지 완화
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # webdriver flag 완화
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def save_debug(driver, out_dir, prefix):
    os.makedirs(out_dir, exist_ok=True)
    png_path = os.path.join(out_dir, f"{prefix}.png")
    html_path = os.path.join(out_dir, f"{prefix}.html")
    try:
        driver.save_screenshot(png_path)
    except Exception:
        pass
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass
    print(f"[DEBUG] Saved: {png_path}")
    print(f"[DEBUG] Saved: {html_path}")
    print(f"[DEBUG] url={driver.current_url}")
    print(f"[DEBUG] title={driver.title}")


# ==============================
# ✅ 로그인: iframe 포함 자동 탐색
# ==============================
def _find_login_inputs_in_context(driver):
    """
    현재 컨텍스트(기본 DOM 혹은 iframe 내부)에서
    (id_input, pw_input)를 최대한 관대하게 탐색
    """
    # 1) 비밀번호 input부터 찾는다 (가장 확실)
    pw_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
    if not pw_inputs:
        return None, None

    pw = pw_inputs[0]

    # 2) 같은 form 안에서 텍스트 input 찾기
    try:
        form = pw.find_element(By.XPATH, "ancestor::form")
        text_inputs = form.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email'], input:not([type])")
        if text_inputs:
            return text_inputs[0], pw
    except Exception:
        pass

    # 3) 전체 페이지에서 text input 후보들 찾기
    text_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email'], input:not([type])")
    if text_inputs:
        return text_inputs[0], pw

    return None, pw


def krx_login(driver, wait: WebDriverWait, user_id: str, user_pw: str, debug_dir: str = "_krx_debug"):
    driver.get("https://data.krx.co.kr/")
    time.sleep(1)

    # 로그인 버튼 클릭(후보)
    login_btn_candidates = [
        (By.LINK_TEXT, "로그인"),
        (By.PARTIAL_LINK_TEXT, "로그인"),
        (By.CSS_SELECTOR, "a[href*='MDCCOMS001']"),
        (By.CSS_SELECTOR, "a.btn_login"),
        (By.CSS_SELECTOR, "a[onclick*='login']"),
    ]

    clicked = False
    for by, sel in login_btn_candidates:
        try:
            btn = wait.until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            clicked = True
            break
        except Exception:
            continue

    if not clicked:
        save_debug(driver, debug_dir, "login_btn_not_found")
        raise RuntimeError("❌ 로그인 버튼을 찾지 못했습니다. (KRX 메인 UI 변경 가능)")

    time.sleep(2)

    # ✅ 핵심: 기본 DOM + 모든 iframe을 순회하며 입력칸 탐색
    id_box, pw_box = None, None

    # (A) 기본 DOM
    try:
        id_box, pw_box = _find_login_inputs_in_context(driver)
    except Exception:
        pass

    # (B) iframe 순회
    if id_box is None or pw_box is None:
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
        except Exception:
            iframes = []

        for idx, iframe in enumerate(iframes):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe)
                tmp_id, tmp_pw = _find_login_inputs_in_context(driver)
                if tmp_id is not None and tmp_pw is not None:
                    id_box, pw_box = tmp_id, tmp_pw
                    break
            except Exception:
                continue
        driver.switch_to.default_content()

    if id_box is None or pw_box is None:
        save_debug(driver, debug_dir, "login_inputs_not_found")
        raise RuntimeError("❌ 로그인 입력 폼을 찾지 못했습니다. (iframe/구조 변경 가능)")

    # 입력
    try:
        id_box.clear()
        id_box.send_keys(user_id)
        pw_box.clear()
        pw_box.send_keys(user_pw)
        pw_box.send_keys(Keys.ENTER)
    except Exception:
        save_debug(driver, debug_dir, "login_input_sendkeys_failed")
        raise

    time.sleep(3)
    driver.get("https://data.krx.co.kr/")
    time.sleep(2)

    page = driver.page_source
    if ("로그아웃" in page) or ("Logout" in page):
        print("[INFO] Login seems OK (logout detected)")
        return

    # 로그인 실패일 수도 있으니 디버그 저장
    save_debug(driver, debug_dir, "login_maybe_failed")
    print("[WARN] 로그인 성공을 확정할 수 없습니다. (계정/캡차/추가인증/차단 가능)")


def requests_session_from_selenium(driver) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://data.krx.co.kr/",
        "Origin": "https://data.krx.co.kr",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    for c in driver.get_cookies():
        sess.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return sess


def get_cap_one_day_via_otp(sess: requests.Session, ymd: str) -> pd.DataFrame:
    payload = {
        "bld": BLD,
        "mktId": "ALL",
        "trdDd": ymd,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
        "url": BLD,
    }

    r = sess.post(KRX_OTP_URL, data=payload, timeout=30)
    otp = (r.text or "").strip()
    if otp in ("", "LOGOUT") or len(otp) < 10:
        raise RuntimeError(f"❌ OTP 발급 실패: otp='{otp}'")

    r2 = sess.post(KRX_DN_URL, data={"code": otp}, timeout=30)
    if r2.status_code != 200 or len(r2.content) < 500:
        raise RuntimeError(f"❌ CSV 다운로드 실패: status={r2.status_code}, bytes={len(r2.content)}")

    try:
        df = pd.read_csv(BytesIO(r2.content), encoding="euc-kr")
    except Exception:
        df = pd.read_csv(BytesIO(r2.content), encoding="utf-8")

    if df.empty or df.shape[1] < 3:
        raise RuntimeError("❌ CSV 파싱 결과가 비정상입니다.")

    if "종목코드" not in df.columns and "단축코드" in df.columns:
        df = df.rename(columns={"단축코드": "종목코드"})
    df = df.rename(columns={"종목코드": "ticker"})

    df["ticker"] = "A" + df["ticker"].astype(str).str.zfill(6)
    df["date"] = pd.to_datetime(ymd).date()

    for col in ["시가총액", "거래량", "거래대금", "상장주식수", "종가"]:
        if col in df.columns:
            df[col] = df[col].map(_to_float)

    return df


def collect_marketcap_range(start_date: str, end_date: str, sess: requests.Session) -> pd.DataFrame:
    biz_days = pd.date_range(start=start_date, end=end_date, freq=BDay())
    all_data = []

    for day in biz_days:
        ymd = day.strftime("%Y%m%d")
        print(f" {day.date()} 시가총액 데이터 수집중...")

        try:
            df = get_cap_one_day_via_otp(sess, ymd)
            all_data.append(df)
        except Exception as e:
            print(f"[WARN] Fail {ymd}: {e}")

    if not all_data:
        return pd.DataFrame()

    return pd.concat(all_data, ignore_index=True)


def transform_to_long(df: pd.DataFrame) -> pd.DataFrame:
    long_df = df.melt(id_vars=["date", "ticker"], var_name="indicator", value_name="value")
    long_df = long_df.drop_duplicates(["date", "ticker", "indicator"], keep="last")
    return long_df[["date", "ticker", "indicator", "value"]]


def upsert_long_df(engine, table_name: str, df_long: pd.DataFrame, chunk_size: int = 5000):
    df = df_long.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates(["date", "ticker", "indicator"], keep="last")

    meta = MetaData()
    meta.reflect(bind=engine, only=[table_name])
    table = Table(table_name, meta, autoload_with=engine)

    rows = df.to_dict("records")
    with engine.begin() as conn:
        for i in range(0, len(rows), chunk_size):
            batch = rows[i:i + chunk_size]
            stmt = mysql_insert(table).values(batch)
            stmt = stmt.on_duplicate_key_update(value=stmt.inserted.value)
            conn.execute(stmt)

    print(f"[INFO] UPSERT 완료: {len(df):,} rows into {table_name}")


def main():
    # ✅ 본인 KRX 계정
    KRX_ID = "stox74"
    KRX_PW = "a4041304!"

    start_date = "2025-10-01"
    end_date = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    table_name = "ks_listed_company_daily_marketcap"

    engine = get_db_engine()
    ensure_table_with_pk(engine, table_name)

    driver = build_driver(headless=False)
    wait = WebDriverWait(driver, 20)

    try:
        krx_login(driver, wait, KRX_ID, KRX_PW, debug_dir="_krx_debug")

        sess = requests_session_from_selenium(driver)

        wide_df = collect_marketcap_range(start_date, end_date, sess)
        if wide_df.empty:
            raise RuntimeError("❌ 수집 결과가 비었습니다. (로그인 실패/권한/차단 가능)")

        long_df = transform_to_long(wide_df)
        upsert_long_df(engine, table_name, long_df)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
