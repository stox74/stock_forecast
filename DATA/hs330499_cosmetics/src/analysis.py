# -*- coding: utf-8 -*-
"""
STEP 3-A. 기술적 분석 : 시계열 그래프 + 시차별 상관관계

주의) 여기서 계산하는 상관계수는 '전체 표본 기준 사후적(in-sample) 상관'이며
      예측 성능이 아니다. 표본 밖 예측력은 backtest.py에서 따로 검증한다.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from common import D_DATA, D_FIG, D_TAB, COMPANIES, setup_matplotlib, log

plt = setup_matplotlib()

TARGETS = {
    "rev_yoy": "매출액 YoY(%)",
    "opm": "영업이익률(%)",
    "opm_chg_yoy": "영업이익률 전년동기차(%p)",
}
EXPVARS = {
    "qexp_usd": "분기 수출총액(USD)",
    "log_qexp_usd": "분기 수출총액 로그수준",
    "qexp_usd_yoy": "분기 수출총액 YoY(%)",
    "qexp_kg_yoy": "분기 수출중량 YoY(%)",
    "qexp_unit_price_yoy": "분기 수출단가 YoY(%)",
}


def load():
    qe = pd.read_csv(D_DATA / "export_quarterly_features.csv", parse_dates=["date"]).set_index("date")
    mo = pd.read_csv(D_DATA / "export_monthly_features.csv", parse_dates=["date"]).set_index("date")
    panel = {c: pd.read_csv(D_DATA / "company_{}_features.csv".format(c),
                            parse_dates=["date"]).set_index("date") for c in COMPANIES}
    return mo, qe, panel


# ---------------------------------------------------------------- 그래프
def fig_export_monthly(mo: pd.DataFrame):
    fig, ax = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    ax[0].plot(mo.index, mo["exp_usd"] / 1e6, lw=1.2, color="#1f77b4")
    ax[0].plot(mo.index, mo["exp_usd_3mma"] / 1e6, lw=1.8, color="#d62728", label="3개월 이동평균")
    ax[0].set_ylabel("백만 USD")
    ax[0].set_title("HS 330499 월별 수출총액 (2007-01 ~ 2026-08, 전국 기준)")
    ax[0].legend()

    ax[1].plot(mo.index, mo["exp_usd_yoy"], lw=1.3, color="#1f77b4", label="수출총액 YoY")
    ax[1].plot(mo.index, mo["exp_kg_yoy"], lw=1.3, color="#2ca02c", label="수출중량 YoY")
    ax[1].axhline(0, color="k", lw=0.8)
    ax[1].set_ylabel("%")
    ax[1].set_title("월별 전년동월대비 증가율")
    ax[1].legend()

    ax[2].plot(mo.index, mo["unit_price"], lw=1.3, color="#9467bd")
    ax[2].set_ylabel("USD/kg")
    ax[2].set_title("수출단가 (수출총액/수출중량) — HS 분류개정 전후 구조변화 점검용")
    ax[2].set_xlabel("연월")
    for yr in (2012, 2017, 2022):
        for a in ax:
            a.axvline(pd.Timestamp("{}-01-01".format(yr)), color="gray", ls="--", lw=0.8, alpha=0.6)
    fig.tight_layout()
    fig.savefig(D_FIG / "fig1_export_monthly.png")
    plt.close(fig)


def fig_company_vs_export(panel, qe):
    for code, p in panel.items():
        nm = COMPANIES[code]["name"]
        d = p.join(qe[["qexp_usd", "qexp_usd_yoy", "qexp_kg_yoy"]], how="left")
        d = d.dropna(subset=["revenue_bn"])
        fig, ax = plt.subplots(3, 1, figsize=(12, 11), sharex=True)

        a = ax[0]
        a.bar(d.index, d["revenue_bn"], width=70, color="#4c72b0", alpha=0.8, label="매출액(좌, 십억원)")
        a.set_ylabel("십억원")
        a2 = a.twinx()
        a2.plot(d.index, d["qexp_usd"] / 1e6, color="#c44e52", lw=1.8, label="HS330499 분기 수출액(우, 백만USD)")
        a2.set_ylabel("백만 USD")
        a2.grid(False)
        a.set_title("{}({}) 분기 매출액 vs HS330499 분기 수출총액".format(nm, code))
        h1, l1 = a.get_legend_handles_labels(); h2, l2 = a2.get_legend_handles_labels()
        a.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)

        a = ax[1]
        a.plot(d.index, d["rev_yoy"], color="#4c72b0", lw=1.8, marker="o", ms=3, label="매출액 YoY")
        a.plot(d.index, d["qexp_usd_yoy"], color="#c44e52", lw=1.8, label="수출총액 YoY")
        a.plot(d.index, d["qexp_kg_yoy"], color="#55a868", lw=1.5, ls="--", label="수출중량 YoY")
        a.axhline(0, color="k", lw=0.8)
        a.set_ylabel("%")
        a.set_title("전년동기대비 증가율 비교")
        a.legend(fontsize=9)

        a = ax[2]
        a.bar(d.index, d["op_bn"], width=70,
              color=np.where(d["op_bn"] >= 0, "#55a868", "#c44e52"), alpha=0.85, label="영업이익(좌)")
        a.set_ylabel("십억원")
        a.axhline(0, color="k", lw=0.8)
        a3 = a.twinx()
        a3.plot(d.index, d["opm"], color="#8172b2", lw=1.8, marker="s", ms=3, label="영업이익률(우, %)")
        a3.set_ylabel("%"); a3.grid(False)
        a.set_title("분기 영업이익 및 영업이익률")
        a.set_xlabel("분기")
        h1, l1 = a.get_legend_handles_labels(); h3, l3 = a3.get_legend_handles_labels()
        a.legend(h1 + h3, l1 + l3, loc="upper left", fontsize=9)

        fig.tight_layout()
        fig.savefig(D_FIG / "fig2_{}_{}_vs_export.png".format(code, nm))
        plt.close(fig)


# ---------------------------------------------------------------- 시차 상관
def lag_correlation(panel, qe, max_lag=4) -> pd.DataFrame:
    rows = []
    for code, p in panel.items():
        for tkey, tlab in TARGETS.items():
            y = p[tkey].dropna()
            for ekey, elab in EXPVARS.items():
                for L in range(0, max_lag + 1):
                    x = qe[ekey].shift(L).reindex(y.index)
                    m = pd.concat([y, x], axis=1).dropna()
                    m.columns = ["y", "x"]
                    if len(m) < 8:
                        continue
                    r, pv = stats.pearsonr(m["y"], m["x"])
                    rs, pvs = stats.spearmanr(m["y"], m["x"])
                    rows.append({
                        "ticker": code, "기업명": COMPANIES[code]["name"],
                        "목표변수": tlab, "target_key": tkey,
                        "수출변수": elab, "exp_key": ekey,
                        "시차_분기": L, "n": len(m),
                        "Pearson_r": round(r, 3), "p값": round(pv, 4),
                        "Spearman_r": round(rs, 3),
                        "표본기간": "{:%Y-%m}~{:%Y-%m}".format(m.index.min(), m.index.max()),
                    })
    df = pd.DataFrame(rows)
    df.to_csv(D_TAB / "step3_lag_correlation_full.csv", index=False, encoding="utf-8-sig")

    # 요약: 목표변수별 |r| 최대 시차
    best = (df.sort_values("Pearson_r", key=lambda s: s.abs(), ascending=False)
              .groupby(["ticker", "target_key", "exp_key"], as_index=False).first())
    best = best.sort_values(["ticker", "target_key", "Pearson_r"],
                            key=lambda s: s.abs() if s.name == "Pearson_r" else s,
                            ascending=[True, True, False])
    best.to_csv(D_TAB / "step3_lag_correlation_best.csv", index=False, encoding="utf-8-sig")
    return df


def fig_lag_heatmap(df: pd.DataFrame):
    for code in COMPANIES:
        sub = df[df["ticker"] == code]
        tks = list(TARGETS.keys())
        fig, axes = plt.subplots(1, len(tks), figsize=(16, 4.6))
        for ax, tk in zip(axes, tks):
            piv = (sub[sub["target_key"] == tk]
                   .pivot_table(index="exp_key", columns="시차_분기", values="Pearson_r"))
            piv = piv.reindex(list(EXPVARS.keys()))
            im = ax.imshow(piv.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
            ax.set_xticks(range(piv.shape[1])); ax.set_xticklabels(piv.columns)
            ax.set_yticks(range(piv.shape[0]))
            ax.set_yticklabels([EXPVARS[k] for k in piv.index], fontsize=8)
            ax.set_xlabel("수출변수 시차(분기)")
            ax.set_title(TARGETS[tk], fontsize=10)
            for i in range(piv.shape[0]):
                for j in range(piv.shape[1]):
                    v = piv.values[i, j]
                    if not np.isnan(v):
                        ax.text(j, i, "{:.2f}".format(v), ha="center", va="center",
                                fontsize=8, color="white" if abs(v) > 0.5 else "black")
            ax.grid(False)
        fig.colorbar(im, ax=axes, fraction=0.015, label="Pearson r")
        fig.suptitle("{}({}) 시차별 상관계수 (전체표본 사후적 상관, 예측성능 아님)".format(
            COMPANIES[code]["name"], code), y=1.02)
        fig.savefig(D_FIG / "fig3_{}_lag_corr_heatmap.png".format(code))
        plt.close(fig)


def main():
    mo, qe, panel = load()
    fig_export_monthly(mo)
    fig_company_vs_export(panel, qe)
    df = lag_correlation(panel, qe)
    fig_lag_heatmap(df)
    log("STEP3A", "시차상관 {}행 / 그림 저장 완료".format(len(df)))

    for code in COMPANIES:
        print("\n===== {} {} : 목표변수별 상위 시차상관 =====".format(code, COMPANIES[code]["name"]))
        sub = df[df["ticker"] == code]
        for tk, tl in TARGETS.items():
            s = sub[sub["target_key"] == tk].copy()
            s["abs_r"] = s["Pearson_r"].abs()
            top = s.sort_values("abs_r", ascending=False).head(4)
            print("[{}]".format(tl))
            print(top[["수출변수", "시차_분기", "n", "Pearson_r", "p값", "Spearman_r"]].to_string(index=False))


if __name__ == "__main__":
    main()
