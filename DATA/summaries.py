# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Tuple, List, Dict, Optional
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from dateutil.relativedelta import relativedelta
from pandas.tseries.offsets import MonthEnd
import sys   # ✅ 추가

# --- 듀얼 임포트: 패키지/단독 모두 대응 ---
try:
    from . import stock_invest_function as SIF
except ImportError:
    import stock_invest_function as SIF

def log(tag: str, msg: str):
    print(f"[{tag}] {msg}", file=sys.stdout, flush=True)

# =========================
# 외부로 재노출할 심볼들
# =========================
# ❌ 아래 두 줄은 삭제하세요 (SIF에 없어서 AttributeError 발생)
# audit_db_coverage = SIF.audit_db_coverage
# ticker_list = SIF.ticker_list

__all__ = [
    "make_growth_summaries",
    "to_long",
    "audit_db_coverage",
    "ticker_list",
]

# =========================
# 내부 유틸
# =========================
def _summarize_series(
    s: pd.Series,
    dates: pd.Series,
) -> Optional[Dict[str, object]]:
    s = pd.to_numeric(s, errors="coerce")
    ok = (~s.isna()) & (~pd.to_datetime(dates, errors="coerce").isna())
    if ok.sum() < 2:
        return None
    s = s[ok]
    dates = pd.to_datetime(dates[ok])
    start_idx = dates.idxmin()
    end_idx   = dates.idxmax()
    start_v = float(s.loc[start_idx])
    end_v   = float(s.loc[end_idx])
    growth = (end_v - start_v) / abs(start_v) if np.isfinite(start_v) and start_v != 0 else np.nan
    return {
        "start_month_end": pd.to_datetime(dates.loc[start_idx]).date(),
        "start_value": start_v,
        "end_value": end_v,
        "growth": float(growth),
    }

# =========================
# 요약 생성 메인
# =========================
def make_growth_summaries(
    final_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    final_df: ['ticker','date_month_end'] 및 revenue TTM/valuation 컬럼 포함
    반환: (rev_summary, val_summary)
      각 컬럼: ['ticker','model','start_month_end','start_value','end_value','growth']
    """
    if final_df is None or final_df.empty:
        empty = pd.DataFrame(columns=["ticker","model","start_month_end","start_value","end_value","growth"])
        return empty.copy(), empty

    df = final_df.copy()
    if "date_month_end" not in df.columns:
        if "index" in df.columns:
            df = df.rename(columns={"index":"date_month_end"})
        else:
            raise ValueError("make_growth_summaries: 'date_month_end' 컬럼이 필요합니다.")

    # revenue 요약
    rev_cols_map = {
        "revenue_billions_sarima_noexog_ttm": "sarima",
        "revenue_billions_lstm_forecast_ttm": "lstm",
        "revenue_billions_prophet_forecast_ttm": "prophet",
        "revenue_billions_esq_forecast_ttm": "es",
        "revenue_billions_avg_of_4_ttm": "avg_of_4",
    }
    rev_rows: List[Dict[str, object]] = []

    # valuation 요약 (avg_of_4/avg_top3 필요시 계산)
    need_avg = (("avg_top3" not in df.columns) or ("avg_of_4" not in df.columns))
    base_val_cols = [c for c in ["sarima_valuation","lstm_valuation","prophet_valuation","es_valuation"] if c in df.columns]
    if need_avg and base_val_cols:
        tmp = df[base_val_cols].copy()
        df["avg_of_4"] = tmp.mean(axis=1, skipna=True)
        df["avg_top3"] = tmp.apply(lambda r: r.dropna().sort_values(ascending=False).head(3).mean(), axis=1)

    val_cols_map = {
        "sarima_valuation": "sarima",
        "lstm_valuation": "lstm",
        "prophet_valuation": "prophet",
        "es_valuation": "es",
        "avg_top3": "avg_top3",
        "avg_of_4": "avg_of_4",
    }
    val_rows: List[Dict[str, object]] = []

    for tk, g in df.groupby("ticker"):
        g = g.sort_values("date_month_end")
        dates = pd.to_datetime(g["date_month_end"])
        for col, mdl in rev_cols_map.items():
            if col in g.columns:
                sm = _summarize_series(g[col], dates)
                if sm is not None:
                    rev_rows.append({"ticker": tk, "model": mdl, **sm})
        for col, mdl in val_cols_map.items():
            if col in g.columns:
                sm = _summarize_series(g[col], dates)
                if sm is not None:
                    val_rows.append({"ticker": tk, "model": mdl, **sm})

    rev_summary = pd.DataFrame(rev_rows, columns=["ticker","model","start_month_end","start_value","end_value","growth"])
    val_summary = pd.DataFrame(val_rows, columns=["ticker","model","start_month_end","start_value","end_value","growth"])
    return rev_summary, val_summary

# =========================
# long 변환 유틸
# =========================
def to_long(
    summary_df: pd.DataFrame,
    category: str,
    created_at: Optional[str] = None,
) -> pd.DataFrame:
    out = summary_df.copy()
    out["category"] = category
    if created_at is not None:
        out["created_at"] = pd.to_datetime(created_at)
    cols = ["ticker","category","model","start_month_end","start_value","end_value","growth"]
    if "created_at" in out.columns:
        cols.append("created_at")
    return out[cols]

ticker_list = ['AAPL', 'MSFT', 'AMZN', 'NVDA', 'GOOGL', 'GOOG', 'META', 'TSLA', 'UNH', 'LLY',
    'JNJ', 'PG', 'AVGO', 'HD', 'MRK', 'ABBV', 'PEP', 'COST', 'ADBE', 'KO',
    'CSCO', 'WMT', 'TMO', 'MCD', 'PFE', 'CRM', 'ACN', 'CMCSA', 'LIN', 'NFLX',
    'ABT', 'ORCL', 'DHR', 'AMD', 'DIS', 'TXN', 'PM', 'VZ', 'INTU', 'CAT',
    'AMGN', 'INTC', 'UNP', 'LOW', 'IBM', 'BMY', 'RTX', 'HON', 'BA', 'UPS',
    'GE', 'QCOM', 'AMAT', 'NKE', 'NOW', 'BKNG', 'SBUX', 'ELV', 'MDT', 'DE',
    'ADP', 'LMT', 'TJX', 'T', 'ISRG', 'MDLZ', 'GILD', 'SYK', 'REGN', 'VRTX',
    'ETN', 'LRCX', 'ADI', 'CVS', 'ZTS', 'CI', 'BDX', 'MO', 'TMUS', 'FI',
    'BSX', 'MU', 'PANW', 'PYPL', 'SNPS', 'ITW', 'KLAC', 'LULU', 'APD', 'SHW',
    'CDNS', 'CSX', 'NOC', 'CL', 'HUM', 'FDX', 'WM', 'MCK', 'TGT', 'ORLY',
    'HCA', 'FCX', 'EMR', 'MMM', 'ROP', 'CMG', 'MAR', 'PH', 'APH', 'GD',
    'NXPI', 'NSC', 'F', 'MSI', 'GM', 'TT', 'EW', 'CARR', 'AZO', 'ADSK',
    'TDG', 'ANET', 'ECL', 'PCAR', 'ADM', 'MNST', 'KMB', 'CHTR', 'MCHP', 'MSCI',
    'CTAS', 'STZ', 'XYZ', 'NUE', 'ROST', 'KVUE', 'IDXX', 'TEL', 'JCI', 'GIS',
    'IQV', 'DXCM', 'HLT', 'ON', 'PAYX', 'BIIB', 'FTNT', 'DOW', 'MRNA', 'CPRT',
    'ODFL', 'DHI', 'YUM', 'CTSH', 'AME', 'SYY', 'A', 'CTVA', 'CNC', 'EL',
    'OTIS', 'ROK', 'DD', 'VRSK', 'LHX', 'DG', 'CMI', 'CSGP', 'FAST', 'PPG',
    'GWW', 'HSY', 'EA', 'NEM', 'ED', 'URI', 'KR', 'RSG', 'LEN', 'PWR',
    'WST', 'COR', 'VMC', 'KDP', 'WBD', 'IR', 'CDW', 'MLM', 'DAL', 'FTV',
    'IT', 'KHC', 'GEHC', 'HPQ', 'CBRE', 'APTV', 'TTD', 'MTD', 'DLTR', 'GDDY',
    'ALGN', 'LYB', 'TROW', 'GLW', 'EFX', 'WY', 'ZBH', 'XYL', 'RMD', 'TSCO',
    'EBAY', 'KEYS', 'CHD', 'COIN', 'ALB', 'STE', 'TTWO', 'MPWR', 'CAH', 'RCL',
    'HPE', 'GPC', 'BR', 'ULTA', 'FICO', 'BAX', 'MKC', 'WAB', 'DOV', 'FLT',
    'CLX', 'TDY', 'DRI', 'LH', 'HOLX', 'VRSN', 'MOH', 'LUV', 'NVR', 'COO',
    'WBA', 'PHM', 'NDAQ', 'HWM', 'RF', 'LVS', 'EXPD', 'FSLR', 'WDAY', 'IEX',
    'BG', 'FDS', 'ENPH', 'IFF', 'BALL', 'SWKS', 'NTAP', 'STLD', 'UAL', 'WAT',
    'OMC', 'TER', 'CCL', 'JBHT', 'TPL', 'TYL', 'K', 'GRMN', 'CBOE', 'TSN',
    'AKAM', 'EG', 'TXT', 'EXPE', 'SJM', 'PTC', 'DGX', 'AVY', 'RVTY', 'BBY',
    'CF', 'CAG', 'EPAM', 'AMCR', 'LW', 'PAYC', 'SNA', 'AXON', 'POOL', 'SYF',
    'SWK', 'ZBRA', 'DPZ', 'PKG', 'LDOS', 'VTRS', 'PODD', 'LKQ', 'MOS', 'TRMB',
    'MGM', 'NDSN', 'WDC', 'MAS', 'IPG', 'MTCH', 'STX', 'KMX', 'TECH', 'WRB',
    'BF.B', 'LYV', 'IP', 'WSM', 'INCY', 'L', 'TAP', 'GEN', 'JKHY', 'HRL',
    'CZR', 'PEAK', 'CDAY', 'PNR', 'CHRW', 'HSIC', 'CRL', 'TKO', 'GL', 'EMN',
    'WYNN', 'ALLE', 'PLTR', 'FFIV', 'DASH', 'MKTX', 'ROL', 'DDOG', 'DELL', 'BLDR',
    'FOXA', 'AOS', 'HAS', 'HII', 'CPB', 'UHS', 'WRK', 'LII', 'GEV', 'BBWI',
    'NWSA', 'TPR', 'PARA', 'SMCI', 'NCLH', 'GNRC', 'SOLV', 'CRWD', 'DVA', 'JBL',
    'HUBB', 'DECK', 'UBER', 'MHK', 'RL', 'VLTO', 'FOX', 'ABNB', 'NWS', 'AAP', 'ABG',
    'ABM', 'ACA', 'ACAD', 'ACHC', 'ACIW', 'ACLS', 'ACMR', 'ACT',
    'ADEA', 'ADMA', 'ADNT', 'ADUS', 'AEO', 'AGO', 'AGYS', 'AHCO', 'AIN', 'AIR',
    'AL', 'ALEX', 'ALG', 'ALGT', 'ALKS', 'ALRM', 'AMN', 'AMPH', 'AMR', 'AMSF',
    'AMTM', 'AMWD', 'ANDE', 'ANGI', 'ANIP', 'AORT', 'AOSL', 'APAM', 'APOG', 'ARCB',
    'ARLO', 'AROC', 'ARR', 'ARWR', 'ASIX', 'ASO', 'ASTE', 'ASTH', 'ATEN', 'ATGE',
    'AUB', 'AVA', 'AVNS', 'AWI', 'AXL', 'AZTA', 'AZZ', 'BANF', 'BANR', 'BCC',
    'BCPC', 'BFS', 'BGC', 'BHE', 'BJRI', 'BKE', 'BL', 'BLFS', 'BLMN', 'BMI',
    'BOOT', 'BOX', 'BRC', 'BWA', 'CABO', 'CAKE', 'CAL', 'CALM', 'CALX', 'CARG',
    'CARS', 'CATY', 'CBRL', 'CC', 'CCOI', 'CCS', 'CE', 'CENT', 'CENTA', 'CENX',
    'CERT', 'CEVA', 'CFFN', 'CHCO', 'CHEF', 'CLB', 'CLSK', 'CNK', 'CNMD', 'CNR',
    'CNS', 'CNXN', 'COHU', 'COLL', 'CON', 'CORT', 'CPRX', 'CRC', 'CRI', 'CRK',
    'CRSR', 'CRVL', 'CSGS', 'CSR', 'CSW', 'CTKB', 'CTS', 'CVCO', 'CWK', 'CXM',
    'CXW', 'CZR', 'DAN', 'DCOM', 'DEI', 'DFH', 'DGII', 'DIOD', 'DLX', 'DNOW',
    'DOCN', 'DORM', 'DRH', 'DV', 'DVAX', 'DXC', 'DXPE', 'DY', 'EAT', 'ECG',
    'EIG', 'ELME', 'EMBC', 'ENOV', 'ENR', 'ENVA', 'EPAC', 'EPC', 'ESE', 'ESI',
    'ETD', 'ETSY', 'EVTC', 'EXPI', 'EXTR', 'EYE', 'EZPW', 'FDP', 'FFBC', 'FHB',
    'FIZZ', 'FMC', 'FORM', 'FOXF', 'FRPT', 'FSS', 'FTDR', 'FTRE', 'FUL', 'FUN',
    'FWRD', 'GBX', 'GDEN', 'GDYN', 'GEO', 'GES', 'GFF', 'GIII', 'GKOS', 'GNL',
    'GO', 'GOGO', 'GOLF', 'GPI', 'GRBK', 'GTES', 'GVA', 'HAYW', 'HBI', 'HCC',
    'HCI', 'HCSG', 'HELE', 'HI', 'HL', 'HLIT', 'HMN', 'HNI', 'HP', 'HRMY',
    'HSII', 'HSTM', 'HTH', 'HTLD', 'HTO', 'HTZ', 'HUBG', 'HWKN', 'HZO', 'IAC',
    'IART', 'IBP', 'ICHR', 'ICUI', 'IDCC', 'IIIN', 'INDB', 'INSP', 'INSW', 'INVA',
    'INVX', 'IOSP', 'IPAR', 'ITGR', 'ITRI', 'JBLU', 'JBSS', 'JBTM', 'JJSF', 'JOE',
    'KAI', 'KALU', 'KAR', 'KFY', 'KLIC', 'KMT', 'KN', 'KNTK', 'KOP', 'KRYS',
    'KSS', 'KTB', 'KW', 'KWR', 'LCII', 'LEG', 'LGIH', 'LGND', 'LKFN', 'LMAT',
    'LNC', 'LNN', 'LPG', 'LQDT', 'LRN', 'LUMN', 'LZB', 'MAC', 'MARA', 'MATW',
    'MATX', 'MBC', 'MC', 'MCRI', 'MCW', 'MCY', 'MD', 'MDU', 'MGPI', 'MHO',
    'MIR', 'MKTX', 'MLKN', 'MMI', 'MMSI', 'MNRO', 'MODG', 'MOG.A', 'MRCY', 'MRTN',
    'MSGS', 'MTH', 'MTRN', 'MTUS', 'MTX', 'MXL', 'MYGN', 'MYRG', 'NABL', 'NATL',
    'NAVI', 'NE', 'NEO', 'NEOG', 'NGVT', 'NHC', 'NMIH', 'NPK', 'NPO', 'NSIT',
    'NTCT', 'NVRI', 'NWBI', 'NWL', 'NX', 'OGN', 'OI', 'OII', 'OMCL', 'OSIS',
    'OTTR', 'OUT', 'OXM', 'PAHC', 'PARR', 'PATK', 'PAYO', 'PBH', 'PBI', 'PCRX',
    'PDFS', 'PECO', 'PENG', 'PENN', 'PGNY', 'PHIN', 'PI', 'PINC', 'PIPR', 'PJT',
    'PLAB', 'PLAY', 'PLMR', 'PLUS', 'PLXS', 'POWL', 'PRA', 'PRAA', 'PRDO', 'PRG',
    'PRGS', 'PRK', 'PRKS', 'PRLB', 'PRSU', 'PRVA', 'PSMT', 'PTGX', 'PZZA', 'QDEL',
    'QNST', 'QRVO', 'QTWO', 'RAL', 'RAMP', 'RCUS', 'RDN', 'RDNT', 'RES', 'REX',
    'REYN', 'REZI', 'RGR', 'RHI', 'RNST', 'ROCK', 'ROG', 'RUN', 'RUSHA', 'RXO',
    'SABR', 'SAFE', 'SAFT', 'SAH', 'SANM', 'SBH', 'SBSI', 'SCHL', 'SCL', 'SCSC',
    'SCVL', 'SDGR', 'SEDG', 'SEE', 'SEM', 'SFBS', 'SFNC', 'SHAK', 'SHEN', 'SHO',
    'SHOO', 'SIG', 'SITC', 'SITM', 'SKT', 'SKY', 'SKYW', 'SLVM', 'SMP', 'SMPL',
    'SMTC', 'SNCY', 'SNDK', 'SNDR', 'SNEX', 'SONO', 'SPNT', 'SPSC', 'SPXC', 'SRPT',
    'SSTK', 'STAA', 'STC', 'STEP', 'STRA', 'STRL', 'SUPN', 'SXI', 'SXT', 'TDC',
    'TDS', 'TFX', 'TGNA', 'TGTX', 'THRM', 'THRY', 'THS', 'TILE', 'TMDX', 'TNC',
    'TNDM', 'TPH', 'TR', 'TRIP', 'TRN', 'TRUP', 'TTMI', 'TWI', 'TWO', 'UCTT',
    'UFCS', 'UFPT', 'UNF', 'UNFI', 'UNIT', 'UPBD', 'URBN', 'USNA', 'USPH', 'UTL',
    'UVV', 'VBTX', 'VCEL', 'VCYT', 'VECO', 'VIAV', 'VICR', 'VIR', 'VRE', 'VRRM',
    'VSAT', 'VSCO', 'VSH', 'VSTS', 'VTOL', 'VYX', 'WAFD', 'WAY', 'WD', 'WDFC',
    'WEN', 'WERN', 'WGO', 'WHD', 'WKC', 'WLY', 'WOR', 'WRLD', 'WS', 'WSC',
    'WT', 'WWW', 'XHR', 'XNCR', 'XPEL', 'YELP', 'YOU', 'ZD']



def _default_month_end_str(offset_months: int = 0) -> str:
    return (pd.Timestamp.today().normalize() + MonthEnd(offset_months)).strftime('%Y-%m-%d')

def audit_db_coverage(db_info, tickers):
    eng = create_engine(
        f"mysql+pymysql://{db_info['user']}:{db_info['password']}@"
        f"{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
    )
    miss_q, miss_m = [], []
    with eng.connect() as conn:
        for t in tickers:
            c_q = pd.read_sql(text(
                "SELECT COUNT(*) c FROM US_fundq WHERE UPPER(TRIM(ticker))=UPPER(:t) AND saleq IS NOT NULL"
            ), conn, params={"t": t})['c'].iloc[0]
            c_m = pd.read_sql(text(
                "SELECT COUNT(*) c FROM US_fundm WHERE UPPER(TRIM(ticker))=UPPER(:t) AND me IS NOT NULL"
            ), conn, params={"t": t})['c'].iloc[0]
            if c_q == 0: miss_q.append(t)
            if c_m == 0: miss_m.append(t)
    log("AUDIT", f"US_fundq missing: {len(miss_q)} tickers");  print(miss_q[:20])
    log("AUDIT", f"US_fundm missing: {len(miss_m)} tickers");  print(miss_m[:20])
    return miss_q, miss_m