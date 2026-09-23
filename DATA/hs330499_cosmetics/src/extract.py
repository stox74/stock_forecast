# -*- coding: utf-8 -*-
"""
STEP 1. DB 구조 확인 / 데이터 추출 / 품질 점검
  (1) HS 330499 월별 수출총액(expDlr, USD) / 수출중량(expWgt, kg)
  (2) 한국화장품제조(003350), 에이피알(278470) 분기 매출액/영업이익 (천원)
  (3) 단위 / 연결-별도 구분 / 결측 / 분류변경 / 사용가능기간 점검
산출: output/data/*.csv, output/tables/step1_*.csv
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from common import (q, log, HS_CODE, COMPANIES, D_DATA, D_TAB, to_quarter_end)


# ---------------------------------------------------------------- 스키마 점검
def describe_schema() -> pd.DataFrame:
    tabs = ["korea_monthly_trade_data", "korea_monthly_trade_data_v2",
            "korea_quarterly_trade_data", "korea_fs_data_from_DG",
            "korea_fs_data_from_DART_V3", "korea_fs_data",
            "korea_export_10day_flash_by_item"]
    rows = []
    for t in tabs:
        try:
            cols = q("SHOW COLUMNS FROM " + t)
            n = q("SELECT COUNT(*) AS n FROM " + t)["n"].iloc[0]
            rows.append({"table": t, "n_rows": int(n),
                         "columns": ", ".join(cols["Field"].tolist())})
        except Exception as e:
            rows.append({"table": t, "n_rows": -1, "columns": "ERROR: " + str(e)})
    df = pd.DataFrame(rows)
    df.to_csv(D_TAB / "step1_schema.csv", index=False, encoding="utf-8-sig")
    return df


# ---------------------------------------------------------------- 수출 데이터
def extract_export():
    """HS 330499 월별 수출총액/수출중량.
    주력 소스: korea_monthly_trade_data (2007-01~, 최신 갱신)
    교차검증 : korea_monthly_trade_data_v2 (정밀값, region 전체)
    """
    main = q("SELECT date, indicator, value FROM korea_monthly_trade_data "
             "WHERE root_hs_code = :hs AND indicator IN ('expDlr','expWgt')", hs=HS_CODE)
    main["date"] = pd.to_datetime(main["date"])
    m = main.pivot_table(index="date", columns="indicator", values="value", aggfunc="first")
    m = m.sort_index().rename(columns={"expDlr": "exp_usd", "expWgt": "exp_kg"})

    v2 = q("SELECT date, indicator, value, region, item_name "
           "FROM korea_monthly_trade_data_v2 "
           "WHERE root_hs_code = :hs AND indicator IN ('expDlr','expWgt')", hs=HS_CODE)
    v2["date"] = pd.to_datetime(v2["date"])
    region = sorted(v2["region"].dropna().unique().tolist())
    item = sorted(v2["item_name"].dropna().unique().tolist())
    v2p = v2.pivot_table(index="date", columns="indicator", values="value", aggfunc="first")
    v2p = v2p.sort_index().rename(columns={"expDlr": "exp_usd_v2", "expWgt": "exp_kg_v2"})

    chk = m.join(v2p, how="outer")
    chk["usd_diff_pct"] = (chk["exp_usd"] - chk["exp_usd_v2"]) / chk["exp_usd_v2"] * 100
    chk["kg_diff_pct"] = (chk["exp_kg"] - chk["exp_kg_v2"]) / chk["exp_kg_v2"] * 100
    chk.to_csv(D_TAB / "step1_export_source_crosscheck.csv", encoding="utf-8-sig")

    log("EXPORT", "region={} / item_name={}".format(region, item))
    log("EXPORT", "기간 {:%Y-%m} ~ {:%Y-%m}  n={}".format(m.index.min(), m.index.max(), len(m)))
    ov = chk.dropna(subset=["usd_diff_pct"])
    log("EXPORT", "두 소스 겹치는 {}개월 / USD 최대괴리 {:.4f}% / KG 최대괴리 {:.4f}%".format(
        len(ov), ov["usd_diff_pct"].abs().max(), ov["kg_diff_pct"].abs().max()))

    # --- 품질 점검 ---
    full = pd.date_range(m.index.min(), m.index.max(), freq="ME")
    missing = full.difference(m.index)
    qual = {
        "n_months": len(m),
        "start": m.index.min().strftime("%Y-%m"),
        "end": m.index.max().strftime("%Y-%m"),
        "missing_months": len(missing),
        "missing_list": ", ".join(d.strftime("%Y-%m") for d in missing) if len(missing) else "없음",
        "n_zero_usd": int((m["exp_usd"] <= 0).sum()),
        "n_null_usd": int(m["exp_usd"].isna().sum()),
        "n_zero_kg": int((m["exp_kg"] <= 0).sum()),
        "n_null_kg": int(m["exp_kg"].isna().sum()),
        "usd_min": m["exp_usd"].min(), "usd_max": m["exp_usd"].max(),
        "kg_min": m["exp_kg"].min(), "kg_max": m["exp_kg"].max(),
        "unit_price_min_usd_per_kg": (m["exp_usd"] / m["exp_kg"]).min(),
        "unit_price_max_usd_per_kg": (m["exp_usd"] / m["exp_kg"]).max(),
    }
    pd.Series(qual).to_frame("value").to_csv(D_TAB / "step1_export_quality.csv",
                                             encoding="utf-8-sig")

    # HS 개정(2012/2017/2022) 전후 구조변화 점검: 단가 수준 급변 여부
    up = (m["exp_usd"] / m["exp_kg"]).rename("usd_per_kg")
    brk = []
    for yr in (2012, 2017, 2022):
        before = up[(up.index >= "{}-01-01".format(yr - 1)) & (up.index <= "{}-12-31".format(yr - 1))]
        after = up[(up.index >= "{}-01-01".format(yr)) & (up.index <= "{}-12-31".format(yr))]
        if len(before) and len(after):
            brk.append({"HS개정연도": yr,
                        "직전년_평균단가": before.mean(),
                        "당해년_평균단가": after.mean(),
                        "변화율_pct": (after.mean() / before.mean() - 1) * 100})
    pd.DataFrame(brk).to_csv(D_TAB / "step1_hs_revision_check.csv",
                             index=False, encoding="utf-8-sig")

    m.to_csv(D_DATA / "export_hs330499_monthly.csv", encoding="utf-8-sig")
    return m, chk


# ---------------------------------------------------------------- 재무 데이터
def extract_financials():
    """DataGuide(korea_fs_data_from_DG) 분기 매출액/영업이익.
    DG가 '당분기 개별' 수치인지 DART 원자료(thstrm_amount)로 검증한다.
    """
    dg_list, meta_rows = [], []
    for code, info in COMPANIES.items():
        d = q("SELECT date, indicator, value, sj_div, freq, company_name "
              "FROM korea_fs_data_from_DG "
              "WHERE ticker = :t AND sj_div='IS' "
              "AND indicator IN ('매출액(천원)','영업이익(천원)')", t=info["dg_ticker"])
        d["date"] = to_quarter_end(d["date"])
        w = d.pivot_table(index="date", columns="indicator", values="value",
                          aggfunc="first").sort_index()
        w = w.rename(columns={"매출액(천원)": "revenue_kw", "영업이익(천원)": "op_kw"})
        w["revenue_bn"] = w["revenue_kw"] / 1e6   # 천원 -> 십억원
        w["op_bn"] = w["op_kw"] / 1e6
        w["ticker"] = code
        w["name"] = info["name"]
        dg_list.append(w.reset_index())

        fsdiv = q("SELECT DISTINCT fs_div FROM korea_fs_data_from_DART_V3 WHERE ticker=:t",
                  t=info["dart_ticker"])["fs_div"].dropna().tolist()
        full_idx = pd.date_range(w.index.min(), w.index.max(), freq="QE")
        meta_rows.append({
            "ticker": code, "name": info["name"],
            "DG_기간": "{:%Y-%m} ~ {:%Y-%m}".format(w.index.min(), w.index.max()),
            "DG_분기수": len(w),
            "기간내_결측분기": len(full_idx.difference(w.index)),
            "DART_fs_div": ",".join(fsdiv) if fsdiv else "없음",
            "연결_별도": ("연결(CFS)" if "CFS" in fsdiv
                        else ("별도-개별(OFS)" if "OFS" in fsdiv else "확인불가")),
            "매출액_결측": int(w["revenue_bn"].isna().sum()),
            "영업이익_결측": int(w["op_bn"].isna().sum()),
            "영업이익_0이하_분기수": int((w["op_bn"] <= 0).sum()),
        })

    fs = pd.concat(dg_list, ignore_index=True)
    meta = pd.DataFrame(meta_rows)

    # --- DART 교차검증 : thstrm_amount(당분기 개별) vs DG ---
    ver = []
    for code, info in COMPANIES.items():
        d = q("SELECT bsns_year, reprt_code, fs_div, account_nm, thstrm_amount, "
              "thstrm_add_amount, report_date FROM korea_fs_data_from_DART_V3 "
              "WHERE ticker=:t AND account_nm IN ('매출액','영업이익','수익(매출액)')",
              t=info["dart_ticker"])
        if d.empty:
            continue
        d["date"] = to_quarter_end(pd.to_datetime(d["report_date"]))
        d = d[d["reprt_code"] != "11011"].copy()      # 사업보고서 thstrm은 연간치라 제외
        d["item"] = d["account_nm"].replace({"수익(매출액)": "매출액"})
        p = d.pivot_table(index="date", columns="item", values="thstrm_amount", aggfunc="first")
        g = fs[fs["ticker"] == code].set_index("date")
        for dt in p.index:
            if dt in g.index:
                ver.append({
                    "ticker": code, "date": dt.date(),
                    "DART_매출_당분기_십억": p.loc[dt].get("매출액", np.nan) / 1e9,
                    "DG_매출_십억": g.loc[dt, "revenue_bn"],
                    "DART_영업이익_당분기_십억": p.loc[dt].get("영업이익", np.nan) / 1e9,
                    "DG_영업이익_십억": g.loc[dt, "op_bn"],
                })
    ver = pd.DataFrame(ver)
    if not ver.empty:
        ver["매출_괴리_pct"] = (ver["DG_매출_십억"] / ver["DART_매출_당분기_십억"] - 1) * 100
        ver["영업이익_괴리_pct"] = (ver["DG_영업이익_십억"] / ver["DART_영업이익_당분기_십억"] - 1) * 100
        ver.to_csv(D_TAB / "step1_dg_vs_dart_verification.csv", index=False, encoding="utf-8-sig")
        log("FS", "DG vs DART(당분기 개별) 검증 {}건 / 매출 최대괴리 {:.4f}%".format(
            len(ver), ver["매출_괴리_pct"].abs().max()))

    fs.to_csv(D_DATA / "financials_quarterly.csv", index=False, encoding="utf-8-sig")
    meta.to_csv(D_TAB / "step1_financial_meta.csv", index=False, encoding="utf-8-sig")
    return fs, meta


# ---------------------------------------------------------------- 속보치 확인
def check_flash_availability() -> pd.DataFrame:
    """10일 수출 속보에 화장품/HS330499 항목이 있는지 확인."""
    it = q("SELECT DISTINCT item_name FROM korea_export_10day_flash_by_item")
    rng = q("SELECT MIN(stat_date) mn, MAX(stat_date) mx, COUNT(*) n "
            "FROM korea_export_10day_flash_by_item")
    has = bool(it["item_name"].str.contains("화장|코스|3304", regex=True).any())
    out = pd.DataFrame({
        "항목": ["속보_품목수", "속보_품목명", "속보_기간", "화장품_HS330499_포함여부"],
        "값": [len(it), ", ".join(it["item_name"].tolist()),
               "{} ~ {}".format(rng["mn"].iloc[0], rng["mx"].iloc[0]),
               "포함됨" if has else "미포함 (HS330499 속보치 사용 불가)"],
    })
    out.to_csv(D_TAB / "step1_flash_availability.csv", index=False, encoding="utf-8-sig")
    log("FLASH", str(out.loc[3, "값"]))
    return out


def main():
    log("STEP1", "=== 스키마 확인 ===")
    sch = describe_schema()
    for _, r in sch.iterrows():
        print("  - {:36s} rows={:>9,}".format(r["table"], r["n_rows"]))
        print("      cols: {}".format(r["columns"]))

    log("STEP1", "=== HS330499 수출 추출 ===")
    exp, chk = extract_export()
    print(exp.tail(6).to_string())

    log("STEP1", "=== 기업 재무 추출 ===")
    fs, meta = extract_financials()
    print(meta.to_string(index=False))

    log("STEP1", "=== 수출 속보치 가용성 ===")
    check_flash_availability()
    log("STEP1", "완료")


if __name__ == "__main__":
    main()
