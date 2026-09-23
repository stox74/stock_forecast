# -*- coding: utf-8 -*-
"""
STEP 8. 에이피알 대안 선행지표 검증
        (구글트렌드 / 아마존 BSR·브랜드지수) vs 산업 수출지표

[자료]
  apr_us_google_trends   : 주간, 2021-08 ~ 2026-08, 키워드 5종 (US)
  apr_us_trends_metrics  : 주간, brand_level / category_heat / sos (medicube vs skincare)
  amazon_brand_index     : 일간, A278470/US, gmv_index_all·fixed·dev, brand_strength,
                           n_active_asin, review_velocity
  amazon_bsr_daily       : 일간, 50 ASIN (전부 medicube 브랜드), 2017-08 ~ 2026-08
  apr_revenue_quarterly  : 분기, 뷰티 부문 내수/수출 분리 (2024Q1~2026Q1, 9분기)

[반드시 통제해야 하는 편의]
  아마존 ASIN 패널은 '오늘 시점'에 살아있는 50개를 뽑아 과거를 역채운 것이다.
  분기별 관측 ASIN 수: 2020Q4 1개 -> 2023Q4 8개 -> 2026Q2 49개.
  따라서 gmv_index_all 의 상승은 상당 부분 '패널이 커진 것'이며 생존편의(survivorship)다.
  -> (a) 고정코호트 지수를 직접 만들어 비교하고
     (b) n_active_asin 자체를 위약변수로 넣어, 그것만으로도 같은 성능이 나오는지 본다.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import q, D_DATA, D_TAB, D_FIG, log, setup_matplotlib, yoy
from backtest import ols_fit, ols_predict, dm_test
from deepdive import backtest_variable, score, load_monthly, cumulative_quarter_features

plt = setup_matplotlib()

TICKER = "278470"
MIN_TRAIN_ALT = 8          # 표본이 짧아 8분기로 낮춤 (결과 해석 시 반드시 감안)
COHORT_CUTOFF = "2022-12-31"
BSR_ALPHA = 0.7            # GMV ~ BSR^(-alpha), amazon_brand_index 의 alpha 와 동일


# ---------------------------------------------------------------- 분기 집계
def to_quarterly(df: pd.DataFrame, min_obs: int) -> pd.DataFrame:
    g = df.groupby(pd.PeriodIndex(df.index, freq="Q"))
    out = g.mean()
    cnt = g.size()
    out = out[cnt >= min_obs]
    out.index = out.index.to_timestamp(how="end").normalize()
    return out


def build_trends() -> pd.DataFrame:
    g = q("SELECT date, keyword, value_anchored FROM apr_us_google_trends")
    g["date"] = pd.to_datetime(g["date"])
    g["value_anchored"] = pd.to_numeric(g["value_anchored"], errors="coerce")
    w = g.pivot_table(index="date", columns="keyword", values="value_anchored")
    m = q("SELECT date, brand_level, category_heat, sos FROM apr_us_trends_metrics")
    m["date"] = pd.to_datetime(m["date"])
    for c in ("brand_level", "category_heat", "sos"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    w = w.join(m.set_index("date"), how="outer").sort_index()
    # 경쟁사 대비 상대지표 (추세에 덜 민감)
    peers = [c for c in ("anua", "beauty of joseon") if c in w.columns]
    if peers:
        w["rel_peer"] = w["medicube"] / w[peers].sum(axis=1)
    qd = to_quarterly(w, min_obs=10)
    out = pd.DataFrame(index=qd.index)
    out["gt_brand_level"] = qd["brand_level"]
    out["gt_sos"] = qd["sos"]
    out["gt_category_heat"] = qd["category_heat"]
    out["gt_medicube"] = qd.get("medicube")
    out["gt_rel_peer"] = qd.get("rel_peer")
    for c in list(out.columns):
        out[c + "_yoy"] = yoy(out[c], 4)
    return out


def build_amazon_given() -> pd.DataFrame:
    d = q("SELECT date, gmv_index_all, gmv_index_fixed, gmv_index_dev, brand_strength, "
          "n_active_asin, review_velocity FROM amazon_brand_index WHERE ticker='A278470'")
    d["date"] = pd.to_datetime(d["date"])
    for c in d.columns[1:]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    qd = to_quarterly(d.set_index("date"), min_obs=60)
    qd = qd.rename(columns=lambda c: "az_" + c)
    for c in list(qd.columns):
        qd[c + "_yoy"] = yoy(qd[c], 4)
    return qd


def build_amazon_cohort() -> pd.DataFrame:
    """생존편의 통제: COHORT_CUTOFF 이전부터 존재한 ASIN만으로 고정코호트 지수."""
    b = q("SELECT asin, date, bsr_root, review_count, is_oos FROM amazon_bsr_daily WHERE domain='US'")
    b["date"] = pd.to_datetime(b["date"])
    b["bsr_root"] = pd.to_numeric(b["bsr_root"], errors="coerce")
    b["review_count"] = pd.to_numeric(b["review_count"], errors="coerce")
    first = b.groupby("asin")["date"].min()
    cohort = first[first <= COHORT_CUTOFF].index.tolist()
    log("APR-ALT", "고정코호트 ASIN {}개 (기준일 {} 이전 등장)".format(len(cohort), COHORT_CUTOFF))
    c = b[b["asin"].isin(cohort)].copy()
    # 재고소진(is_oos)일은 판매 없음으로 간주 -> GMV 프록시 0
    c["gmv_proxy"] = np.where((c["is_oos"] == 1) | c["bsr_root"].isna() | (c["bsr_root"] <= 0),
                              0.0, c["bsr_root"].astype(float) ** (-BSR_ALPHA))
    daily = c.groupby("date")["gmv_proxy"].sum().to_frame("coh_gmv")
    # 코호트 누적 리뷰수 -> 리뷰 증가속도(판매 프록시)
    rev = c.pivot_table(index="date", columns="asin", values="review_count").sort_index()
    rev = rev.ffill()
    daily["coh_reviews"] = rev.sum(axis=1)
    daily["coh_rev_velocity"] = daily["coh_reviews"].diff(7).clip(lower=0)
    qd = to_quarterly(daily, min_obs=60)
    for c2 in ("coh_gmv", "coh_rev_velocity"):
        qd[c2 + "_yoy"] = yoy(qd[c2], 4)
    return qd[["coh_gmv", "coh_gmv_yoy", "coh_rev_velocity", "coh_rev_velocity_yoy"]]


def build_segment_revenue() -> pd.DataFrame:
    d = q("SELECT yyyyq, segment, channel, amount_mkrw FROM apr_revenue_quarterly "
          "WHERE segment='beauty'")
    if d.empty:
        return pd.DataFrame()
    d["date"] = pd.PeriodIndex(d["yyyyq"].str.replace("Q", "Q"), freq="Q").to_timestamp(how="end").normalize()
    p = d.pivot_table(index="date", columns="channel", values="amount_mkrw", aggfunc="first")
    p = p / 1000.0        # 백만원 -> 십억원
    p.columns = ["beauty_" + c for c in p.columns]
    for c in list(p.columns):
        p[c + "_yoy"] = yoy(p[c], 4)
    return p


# ---------------------------------------------------------------- 실행
def main():
    panel = pd.read_csv(D_DATA / "company_{}_features.csv".format(TICKER),
                        parse_dates=["date"]).set_index("date")

    gt = build_trends()
    az = build_amazon_given()
    co = build_amazon_cohort()
    seg = build_segment_revenue()
    feats = gt.join(az, how="outer").join(co, how="outer")

    # 위약변수
    feats["PLB_trend"] = np.arange(len(feats), dtype=float)
    semi = cumulative_quarter_features(load_monthly("854231"))
    feats["PLB_semi_yoy"] = semi["cum3_usd_yoy"].reindex(feats.index)
    feats["PLB_n_asin"] = az["az_n_active_asin"]        # 패널 구성 자체 = 생존편의 위약

    feats.to_csv(D_DATA / "apr_alt_features.csv", encoding="utf-8-sig")
    if not seg.empty:
        seg.to_csv(D_DATA / "apr_segment_revenue.csv", encoding="utf-8-sig")

    log("APR-ALT", "지표 {}개 / 기간 {:%Y-%m}~{:%Y-%m}".format(
        feats.shape[1], feats.index.min(), feats.index.max()))

    # ---------- (1) 기술통계: 자료 가용성 ----------
    avail = []
    for c in feats.columns:
        s = feats[c].dropna()
        if len(s) == 0:
            continue
        avail.append({"지표": c, "n_분기": len(s),
                      "시작": "{:%Y-%m}".format(s.index.min()),
                      "종료": "{:%Y-%m}".format(s.index.max())})
    av = pd.DataFrame(avail)
    av.to_csv(D_TAB / "step11_apr_alt_availability.csv", index=False, encoding="utf-8-sig")

    # ---------- (2) 시차 상관 (사후적) ----------
    rows = []
    for tgt, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
        y = panel[tgt].dropna()
        for c in feats.columns:
            for L in range(0, 3):
                x = feats[c].shift(L).reindex(y.index)
                m = pd.concat([y, x], axis=1).dropna()
                m.columns = ["y", "x"]
                if len(m) < 8 or m["x"].std() == 0:
                    continue
                r, pv = stats.pearsonr(m["y"], m["x"])
                rows.append({"목표변수": tl, "지표": c, "시차_분기": L, "n": len(m),
                             "Pearson_r": round(r, 3), "p값": round(pv, 4),
                             "표본": "{:%Y-%m}~{:%Y-%m}".format(m.index.min(), m.index.max())})
    corr = pd.DataFrame(rows)
    corr.to_csv(D_TAB / "step12_apr_alt_lagcorr.csv", index=False, encoding="utf-8-sig")

    # ---------- (3) 표본밖 검증 (짧은 표본) ----------
    res = []
    for tgt, tl in (("rev_yoy", "매출액 YoY(%)"), ("opm", "영업이익률(%)")):
        for c in feats.columns:
            for L in (0, 1):
                fc = backtest_variable(panel, feats[c].shift(L), tgt, MIN_TRAIN_ALT)
                if fc.empty:
                    continue
                s = score(fc)
                if not s or s["n_eval"] < 6:
                    continue
                grp = ("위약" if c.startswith("PLB_") else
                       ("구글트렌드" if c.startswith("gt_") else
                        ("아마존(제공지수)" if c.startswith("az_") else "아마존(고정코호트)")))
                s.update({"목표변수": tl, "지표": c, "구분": grp, "시차_분기": L})
                res.append(s)
    oos = pd.DataFrame(res)
    oos.to_csv(D_TAB / "step13_apr_alt_oos.csv", index=False, encoding="utf-8-sig")

    # ---------- 출력 ----------
    print("\n### 지표 가용 기간")
    print(av.to_string(index=False))

    if not seg.empty:
        print("\n### 에이피알 뷰티부문 내수/수출 분리 매출 (십억원)")
        print(seg.round(1).to_string())

    for tl in ("매출액 YoY(%)", "영업이익률(%)"):
        print("\n" + "=" * 110)
        print("■ {} — 시차상관 상위 (사후적, 예측성능 아님)".format(tl))
        s = corr[corr["목표변수"] == tl].copy()
        s["abs_r"] = s["Pearson_r"].abs()
        print(s.sort_values("abs_r", ascending=False).head(10)[
            ["지표", "시차_분기", "n", "Pearson_r", "p값", "표본"]].to_string(index=False))

        print("\n■ {} — 표본밖 성능 (min_train={}, 기준모형 대비)".format(tl, MIN_TRAIN_ALT))
        t = oos[oos["목표변수"] == tl].sort_values("MAE_개선_vs_최선기준_pct", ascending=False)
        if t.empty:
            print("  (표본부족)")
            continue
        print(t.head(14)[["구분", "지표", "시차_분기", "n_eval", "최선기준", "MAE_EXP",
                          "MAE_개선_vs_최선기준_pct", "DM_p", "encomp_p"]].round(3).to_string(index=False))
        print("  --- 위약변수 성적 ---")
        print(t[t["구분"] == "위약"][["지표", "시차_분기", "n_eval", "MAE_EXP",
                                   "MAE_개선_vs_최선기준_pct", "encomp_p"]].round(3).to_string(index=False))

    return feats, corr, oos, seg


if __name__ == "__main__":
    main()
