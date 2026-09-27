# -*- coding: utf-8 -*-
"""
Korea_Rate_VAR_Forecast_v1.py
================================================================
채권금융론 교육용 예제: VAR 모형을 활용한 국고채 1년 금리 예측

목적
  - 소규모 월별 VAR(벡터자기회귀)로 단기금리(국고채 1년)를
    "가늠"해 볼 수 있음을 보여주는 강의용 데모.
  - 정밀 예측이 목적이 아니며, 모형 구축의 표준 절차
    (정상성 검정 -> 시차 선택 -> 추정 -> 진단 -> 인과성/IRF/FEVD -> 예측)
    를 한 번에 보여주는 것이 목적.

변수 구성 (문헌 근거)
  1) 국고채 1년 금리 (예측 대상)
  2) 소비자물가 상승률 YoY        - Taylor(1993), Christiano-Eichenbaum-Evans(1999)
  3) 산업생산 증가율 YoY          - Ang & Piazzesi(2003), Diebold-Rudebusch-Aruoba(2006)
  4) 원/달러 환율 (로그차분)      - Cushman & Zha(1997) 소규모 개방경제 VAR
  5) 미국 금리 (연방기금금리 등)  - Cushman & Zha(1997), 해외금리 파급

실행 모드
  USE_DEMO = True  : ECOS 접속 없이 시뮬레이션 데이터로 전체 파이프라인 실행 (강의 시연용)
  USE_DEMO = False : 한국은행 ECOS Open API에서 실제 데이터 수집
                     (ECOS_API_KEY와 아래 ECOS_SERIES의 STAT_CODE/ITEM_CODE 확인 필요)

주의
  - API 키는 코드에 하드코딩하지 말고 환경변수 ECOS_API_KEY 사용을 권장.
  - ECOS 통계코드는 개편될 수 있으므로 ecos.bok.or.kr 에서 확인 후 기입.
================================================================
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from statsmodels.tsa.api import VAR
from statsmodels.tsa.stattools import adfuller, grangercausalitytests

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------
# 0. 설정
# ----------------------------------------------------------------
USE_DEMO = True                     # 강의 시연: True / 실데이터: False
FORECAST_HORIZON = 12               # 예측 기간(개월)
OUTPUT_DIR = "var_output"           # 그림 저장 폴더
ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "여기에_API_키")

# 한글 폰트 (Windows: Malgun Gothic / 기타 환경은 자동 대체)
for _f in ["Malgun Gothic", "NanumGothic", "AppleGothic", "DejaVu Sans"]:
    if any(_f.lower() in f.name.lower() for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False

# ECOS 수집 대상 (STAT_CODE / ITEM_CODE1 은 ECOS에서 확인 후 수정)
# 검색: https://ecos.bok.or.kr -> 통계코드검색
ECOS_SERIES = {
    #  키          [STAT_CODE,  ITEM_CODE1,     설명]
    "KR1Y":  ["721Y002", "확인필요",  "시장금리(월) 국고채 1년"],
    "CPI":   ["901Y009", "확인필요",  "소비자물가지수 총지수"],
    "IP":    ["901Y033", "확인필요",  "전산업(또는 광공업) 생산지수"],
    "USDKRW":["731Y005", "확인필요",  "원/달러 환율 월평균"],
    "USRATE":["902Y006", "확인필요",  "미국 연방기금금리(또는 미 국채)"],
}
START_YM, END_YM = "200401", "202609"

# 변수 표시명 (그림, 표)
LABELS = {
    "KR1Y":   "국고채 1년(%)",
    "CPI_YOY": "CPI 상승률 YoY(%)",
    "IP_YOY":  "산업생산 YoY(%)",
    "DLOG_FX": "원/달러 로그차분(%)",
    "USRATE":  "미국 금리(%)",
}
# Cholesky 축차 순서: 느리게 움직이는 변수 -> 빠른 변수
# (해외금리는 국내충격에 당월 무반응 가정 -> 최상단, 금융변수는 하단)
VAR_ORDER = ["USRATE", "IP_YOY", "CPI_YOY", "KR1Y", "DLOG_FX"]


# ----------------------------------------------------------------
# 1. 데이터 수집
# ----------------------------------------------------------------
def fetch_ecos_series(stat_code, item_code, start_ym, end_ym, api_key):
    """ECOS Open API에서 월별 시계열 1개를 수집."""
    import requests
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/"
           f"1/10000/{stat_code}/M/{start_ym}/{end_ym}/{item_code}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    js = r.json()
    if "StatisticSearch" not in js:
        raise ValueError(f"ECOS 응답 오류({stat_code}/{item_code}): {js}")
    rows = js["StatisticSearch"]["row"]
    df = pd.DataFrame(rows)[["TIME", "DATA_VALUE"]]
    df["Date"] = pd.to_datetime(df["TIME"], format="%Y%m") + pd.offsets.MonthEnd(0)
    df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    return df.set_index("Date")["value"]


def load_real_data():
    """ECOS에서 5개 변수 수집 후 원자료 DataFrame 반환."""
    raw = {}
    for key, (stat, item, desc) in ECOS_SERIES.items():
        if item == "확인필요":
            raise ValueError(f"[{key}] {desc} 의 ITEM_CODE1을 ECOS에서 확인해 기입하세요.")
        print(f"수집 중: {key} ({desc}) ...")
        raw[key] = fetch_ecos_series(stat, item, START_YM, END_YM, ECOS_API_KEY)
    return pd.DataFrame(raw)


def load_demo_data(seed=42, n=270):
    """
    시뮬레이션 데이터 생성 (2004-01 ~ 약 22년 월별).
    실제 한국 거시 시계열의 대략적 특성(수준, 지속성, 상호관계)을 흉내낸
    교육용 가짜 데이터. 결과 수치 자체에 의미를 두지 말 것.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2004-01-31", periods=n, freq="ME")

    us = np.zeros(n); ip = np.zeros(n); cpi = np.zeros(n)
    kr = np.zeros(n); fx = np.zeros(n)
    us[0], ip[0], cpi[0], kr[0], fx[0] = 1.0, 4.0, 3.0, 4.0, 0.0

    for t in range(1, n):
        # 미국 금리: 매우 지속적 + 사이클
        us[t] = 0.985*us[t-1] + 0.015*2.5 + 0.35*np.sin(t/38) * 0.03 + rng.normal(0, 0.08)
        us[t] = max(us[t], 0.05)
        # 산업생산 YoY: 경기 사이클
        ip[t] = 0.90*ip[t-1] + 0.10*3.0 + rng.normal(0, 0.9)
        # CPI YoY: 지속적 + 경기 후행
        cpi[t] = 0.93*cpi[t-1] + 0.07*2.2 + 0.03*ip[t-1] + rng.normal(0, 0.25)
        # 환율 로그차분: 미국금리 상승/국내경기 부진 시 절하 압력
        fx[t] = 0.25*fx[t-1] + 0.15*(us[t-1]-kr[t-1]) - 0.04*ip[t-1] + rng.normal(0, 0.9)
        # 국고 1년: 테일러형 반응 + 미국금리 동조 + 관성
        kr[t] = (0.90*kr[t-1] + 0.10*(1.0 + 0.9*cpi[t-1] + 0.20*ip[t-1])
                 + 0.06*(us[t]-us[t-1])*8 + 0.004*fx[t-1] + rng.normal(0, 0.10))
        kr[t] = max(kr[t], 0.3)

    # 원자료 형태로 복원 (CPI지수, IP지수, 환율수준)
    cpi_idx = 100 * np.cumprod(1 + cpi/1200)
    ip_idx = 100 * np.cumprod(1 + (ip + rng.normal(0, 0.3, n))/1200)
    fx_lvl = 1150 * np.exp(np.cumsum(fx/100)/3)

    return pd.DataFrame({"KR1Y": kr, "CPI": cpi_idx, "IP": ip_idx,
                         "USDKRW": fx_lvl, "USRATE": us}, index=dates)


# ----------------------------------------------------------------
# 2. 변수 변환 (정상성 확보)
# ----------------------------------------------------------------
def transform(raw):
    """
    수준 변수 -> VAR 투입 변수 변환.
      CPI, IP    : 전년동월대비 증가율(YoY, %)
      USDKRW     : 로그차분 x 100 (%)
      KR1Y,USRATE: 수준 유지(금리는 관행상 수준으로 넣는 경우가 많음.
                   ADF에서 비정상으로 나오면 차분 버전으로 바꿔 볼 것 - 강의 토론 포인트)
    """
    df = pd.DataFrame(index=raw.index)
    df["KR1Y"] = raw["KR1Y"]
    df["CPI_YOY"] = raw["CPI"].pct_change(12) * 100
    df["IP_YOY"] = raw["IP"].pct_change(12) * 100
    df["DLOG_FX"] = np.log(raw["USDKRW"]).diff() * 100
    df["USRATE"] = raw["USRATE"]
    return df.dropna()[VAR_ORDER]


# ----------------------------------------------------------------
# 3. 진단 및 추정
# ----------------------------------------------------------------
def adf_table(df):
    print("\n[1] ADF 단위근 검정 (귀무가설: 단위근 존재 = 비정상)")
    print(f"{'변수':<12}{'ADF통계량':>12}{'p-value':>10}   판정(5%)")
    for c in df.columns:
        stat, p = adfuller(df[c].dropna(), autolag="AIC")[:2]
        verdict = "정상" if p < 0.05 else "비정상(주의)"
        print(f"{c:<12}{stat:>12.3f}{p:>10.3f}   {verdict}")
    print("  주: 금리를 수준으로 쓰면 경계선상인 경우가 많음. 교육 목적상 수준 유지,")
    print("      실전에서는 차분 또는 공적분(VECM) 검토가 표준 절차.")


def select_lag(df, maxlags=8):
    sel = VAR(df).select_order(maxlags=maxlags)
    print("\n[2] 시차(lag) 선택")
    print(sel.summary())
    lag = sel.selected_orders["aic"]
    lag = max(int(lag), 1)
    print(f"  -> AIC 기준 선택 시차: {lag}")
    return lag


def granger_table(df, maxlag):
    """각 변수 -> KR1Y 그랜저 인과성 (F검정 p-value)"""
    print("\n[3] 그랜저 인과성: '해당 변수의 과거가 국고1년 예측에 도움이 되는가'")
    print(f"{'원인 변수':<12}{'p-value':>10}   판정(5%)")
    for c in df.columns:
        if c == "KR1Y":
            continue
        res = grangercausalitytests(df[["KR1Y", c]].dropna(), maxlag=[maxlag])
        p = res[maxlag][0]["ssr_ftest"][1]
        verdict = "인과성 있음" if p < 0.05 else "유의하지 않음"
        print(f"{c:<12}{p:>10.4f}   {verdict}")
    print("  주: 그랜저 인과성은 '예측적 선행성'이지 구조적 인과가 아님을 강의에서 강조.")


# ----------------------------------------------------------------
# 4. 그림
# ----------------------------------------------------------------
def plot_irf(results, out_dir):
    """국고1년에 대한 각 변수 충격의 반응 + 국고1년 충격의 파급"""
    irf = results.irf(24)
    fig = irf.plot(orth=True, response="KR1Y", figsize=(10, 8))
    fig.suptitle("충격반응함수: 각 변수 1표준편차 충격 -> 국고채 1년 반응 (24개월)", fontsize=11)
    fig.tight_layout()
    fig.savefig(f"{out_dir}/irf_to_KR1Y.png", dpi=150)
    plt.close(fig)

    fig2 = irf.plot(orth=True, impulse="KR1Y", figsize=(10, 8))
    fig2.suptitle("충격반응함수: 국고채 1년 충격 -> 각 변수 반응 (24개월)", fontsize=11)
    fig2.tight_layout()
    fig2.savefig(f"{out_dir}/irf_from_KR1Y.png", dpi=150)
    plt.close(fig2)
    print(f"\n[5] IRF 그림 저장: {out_dir}/irf_to_KR1Y.png, irf_from_KR1Y.png")


def plot_fevd(results, out_dir):
    fevd = results.fevd(24)
    idx = list(results.names).index("KR1Y")
    dec = fevd.decomp[idx]              # (기간 x 변수) 기여율
    fig, ax = plt.subplots(figsize=(9, 5))
    bottom = np.zeros(dec.shape[0])
    for j, name in enumerate(results.names):
        ax.bar(range(1, dec.shape[0]+1), dec[:, j]*100, bottom=bottom,
               label=LABELS.get(name, name))
        bottom += dec[:, j]*100
    ax.set_title("예측오차 분산분해(FEVD): 국고채 1년 변동의 원천")
    ax.set_xlabel("예측 시계(개월)"); ax.set_ylabel("기여율(%)")
    ax.legend(fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(f"{out_dir}/fevd_KR1Y.png", dpi=150)
    plt.close(fig)
    print(f"[6] FEVD 그림 저장: {out_dir}/fevd_KR1Y.png")
    # 12개월 시점 기여율 표
    print("    12개월 시점 국고1년 분산 기여율:")
    for j, name in enumerate(results.names):
        print(f"      {LABELS.get(name,name):<20}{dec[11, j]*100:6.1f}%")


def plot_forecast(df, results, horizon, out_dir):
    lag = results.k_ar
    point = results.forecast(df.values[-lag:], steps=horizon)
    mid, lower, upper = results.forecast_interval(df.values[-lag:], steps=horizon, alpha=0.05)
    f_idx = pd.date_range(df.index[-1] + pd.offsets.MonthEnd(1), periods=horizon, freq="ME")
    k = list(df.columns).index("KR1Y")

    hist = df["KR1Y"].iloc[-60:]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(hist.index, hist.values, color="#1E2761", lw=1.6, label="실적(최근 5년)")
    ax.plot(f_idx, mid[:, k], color="#D62728", lw=2, ls="--", label=f"VAR {horizon}개월 예측")
    ax.fill_between(f_idx, lower[:, k], upper[:, k], color="#D62728", alpha=0.15, label="95% 예측구간")
    ax.axvline(df.index[-1], color="gray", lw=0.8, ls=":")
    ax.set_title("국고채 1년 금리: VAR 예측 (교육용 데모)")
    ax.set_ylabel("금리(%)"); ax.legend()
    fig.tight_layout()
    fig.savefig(f"{out_dir}/forecast_KR1Y.png", dpi=150)
    plt.close(fig)

    print(f"\n[7] {horizon}개월 예측 (저장: {out_dir}/forecast_KR1Y.png)")
    out = pd.DataFrame({"예측": mid[:, k], "하한95": lower[:, k], "상한95": upper[:, k]},
                       index=f_idx.strftime("%Y-%m"))
    print(out.round(3).to_string())
    return out


# ----------------------------------------------------------------
# 5. 메인
# ----------------------------------------------------------------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 64)
    print("VAR 기반 국고채 1년 금리 예측 - 교육용 데모")
    print("=" * 64)

    raw = load_demo_data() if USE_DEMO else load_real_data()
    print(f"\n원자료: {raw.index[0]:%Y-%m} ~ {raw.index[-1]:%Y-%m}, {len(raw)}개월")
    df = transform(raw)
    print(f"변환 후 표본: {len(df)}개월, 변수: {list(df.columns)}")

    adf_table(df)
    lag = select_lag(df)

    results = VAR(df).fit(lag)
    print(f"\n[4] VAR({lag}) 추정 완료. 안정성(모든 고유근<1): {results.is_stable()}")
    print("    (안정성 만족 시 IRF가 0으로 수렴 -> 충격의 효과가 일시적)")

    granger_table(df, lag)
    plot_irf(results, OUTPUT_DIR)
    plot_fevd(results, OUTPUT_DIR)
    plot_forecast(df, results, FORECAST_HORIZON, OUTPUT_DIR)

    print("\n완료. var_output 폴더의 그림 4개를 강의 슬라이드에 활용하세요.")
    print("실데이터 전환: USE_DEMO=False, ECOS_API_KEY 설정, ECOS_SERIES 코드 확인.")


if __name__ == "__main__":
    main()
