"""시계열 시각화.

기업별로 두 종류의 차트를 PNG로 저장한다.
1) {code}_forecast.png : 실적 + 최신 예측(95% 신뢰구간 밴드)
2) {code}_history.png  : 과거 각 basis 시점의 예측들을 실적 위에 겹쳐
                         '매월 예측이 실적을 얼마나 맞췄는지' 추적
"""
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from config import COMPANIES, OUTPUT_DIR
from db import latest_basis, load_forecasts, load_revenue_series


def _actual_frame(conn, company_id):
    rows = load_revenue_series(conn, company_id)
    df = pd.DataFrame(rows, columns=["year", "month", "revenue"])
    df["date"] = pd.to_datetime(dict(year=df.year, month=df.month, day=1))
    df["revenue_b"] = df["revenue"] / 1e6  # NTD 천 → NTD 십억(billion)
    return df


def plot_latest_forecast(conn, company_id):
    df = _actual_frame(conn, company_id)
    if df.empty:
        return None
    basis = latest_basis(conn, company_id)
    fc = load_forecasts(conn, company_id, basis=basis)

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4", lw=1.6)

    if fc:
        f = pd.DataFrame(fc, columns=["by", "bm", "ty", "tm", "pred", "lo", "hi"])
        f["date"] = pd.to_datetime(dict(year=f.ty, month=f.tm, day=1))
        for c in ("pred", "lo", "hi"):
            f[c] = f[c] / 1e6
        # 실적 마지막 점과 예측 첫 점을 이어 그리기
        last = df.iloc[-1]
        xs = [last["date"]] + list(f["date"])
        ys = [last["revenue_b"]] + list(f["pred"])
        ax.plot(xs, ys, label="Forecast", color="#d62728", lw=1.6, ls="--", marker="o", ms=4)
        ax.fill_between(f["date"], f["lo"], f["hi"], color="#d62728", alpha=0.15,
                        label="95% CI")

    ax.set_title(f"{name} ({company_id}) Monthly Revenue & SARIMA Forecast")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_forecast.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_forecast_history(conn, company_id):
    """과거 basis별 예측을 실적 위에 겹쳐 예측 정확도를 시각적으로 추적."""
    df = _actual_frame(conn, company_id)
    fc = load_forecasts(conn, company_id)
    if df.empty or not fc:
        return None

    f = pd.DataFrame(fc, columns=["by", "bm", "ty", "tm", "pred", "lo", "hi"])
    f["date"] = pd.to_datetime(dict(year=f.ty, month=f.tm, day=1))
    f["pred_b"] = f["pred"] / 1e6

    name = COMPANIES.get(company_id, company_id)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["date"], df["revenue_b"], label="Actual", color="#1f77b4", lw=2, zorder=5)

    cmap = plt.get_cmap("autumn")
    bases = sorted(f.groupby(["by", "bm"]).groups.keys())
    for i, (by, bm) in enumerate(bases):
        g = f[(f.by == by) & (f.bm == bm)].sort_values("date")
        color = cmap(i / max(len(bases) - 1, 1) * 0.8)
        ax.plot(g["date"], g["pred_b"], ls="--", lw=1.1, marker=".", ms=5,
                color=color, alpha=0.85, label=f"Forecast @ {by}-{bm:02d}")

    ax.set_title(f"{name} ({company_id}) Forecast History vs Actual")
    ax.set_ylabel("Revenue (NTD billion)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.autofmt_xdate()
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{company_id}_history.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_all(conn, companies=None):
    paths = []
    for cid in (companies or COMPANIES.keys()):
        for fn in (plot_latest_forecast, plot_forecast_history):
            p = fn(conn, cid)
            if p:
                paths.append(p)
                print(f"[plot] {p}")
    return paths
