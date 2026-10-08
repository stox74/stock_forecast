# -*- coding: utf-8 -*-
"""nowcast 특징값 (2a 명세서 v3 4절).

드라이버 변환 (1단계 v2 시범 분석과 같은 분기 집계 + 전년 동기 대비, 4절 예측 대상에 맞춰 로그 성장률)
- 월간 금액·인원(수출액, 대만 월매출, 관광객, 항공 여객): 분기 내 월 합계의 로그 YoY
- 분기 값(SK하이닉스 매출): 분기 값의 로그 YoY
- 일별(아마존 순위 중앙값): 분기 내 관측일 평균의 로그 YoY
선행(M3) 드라이버는 예측 분기 q 대신 직전 분기 q-1 의 값을 쓴다.

시점 규칙
- 기준일 t 에는 asof.as_of(df, t) 를 통과하고 asof.latest_known 으로 고른 값만 쓴다.
- 분기 중 일부 월만 나왔으면 나온 월(분기 내 위치)만 합해 전년 같은 월 합계와 비교한다. coverage = 월 수 / 3.
- 학습용 과거 분기 특징값도 예측 시점과 같은 월 위치로 만든다 (예: 1~2월만 있으면 과거도 1~2월).
- 일별 드라이버의 coverage = 관측일 수 / 분기 일수. 분기 단위 드라이버는 1 또는 0.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .. import asof, entity, panel as fp
from ..adapters import read_revenue, read_series, read_trade
from ..adapters.revenue import q4_exclusions
from ..config import CORPORATE_EVENT_EXCLUDE

COVID_START = pd.Timestamp("2020-03-31")      # 2020 Q1
COVID_END = pd.Timestamp("2022-06-30")        # 2022 Q2


@dataclass
class DriverSpec:
    model_id: str                 # 예: M1:854232, M3:nanya
    driver_key: str               # 1단계 panel 의 driver 이름
    kind: str                     # "M" 월간 합계, "Q" 분기 값, "D" 일별 평균
    lag: int                      # 0 동행(M1), 1 선행(M3)
    loader: Callable[[], pd.DataFrame] = field(repr=False)
    aux: bool = False             # 보조 드라이버 (M2 평균에서 뺌)
    transform: str = "log_yoy"
    alt: Optional[str] = None     # 결과 확인 후 추가한 변형 이름 (예: "krw"). 주 모델·주 M2 에는 넣지 않는다
    replaces: Optional[str] = None  # 변형 M2 를 만들 때 이 변형이 대신하는 주 드라이버 model_id
    # 2c 변경 (명세서 2c v2 4절): 세그먼트 가중평균. weights(분기 j, 기준일 t) -> {세그먼트(key): 가중치}.
    # 있으면 특징값 = Σ w_s·g_s / Σ w_s (w_s > 0 인 세그먼트, g_s = 세그먼트 key 의 로그 YoY). 하나라도 없으면 None.
    weights: Optional[Callable[[pd.Timestamp, object], Dict[str, float]]] = field(default=None, repr=False)


def _trade(code: str) -> Callable[[], pd.DataFrame]:
    return lambda: read_trade([code], items=["expDlr"])


# ---------------------------------------------------------------- 원화 환산 (2a 명세서 v4 5절 변형 :krw)
FX_SERIES = "DEXKOUS"          # FRED 일별 원/달러 (KRW per USD)


def fx_monthly_avg(daily: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """일별 원/달러 -> 월평균. available_at = max(월말 다음 날, 그 달 일별 값 available_at 의 최댓값).

    월평균은 그 달 마지막 일별 값까지 알려져야 계산할 수 있으므로, 달이 끝나기 전이나 일부 일별 값이
    아직 공표되지 않은 시점에는 쓰지 않는다. 등급은 일별 값 중 가장 낮은 등급.
    """
    d = read_series("fred", [FX_SERIES]) if daily is None else daily
    d = d.dropna(subset=["value"]).copy()
    d["period_end"] = pd.to_datetime(d["period_end"])
    d["available_at"] = pd.to_datetime(d["available_at"])
    d["month_end"] = d["period_end"] + pd.offsets.MonthEnd(0)
    g = d.groupby("month_end").agg(value=("value", "mean"), n_days=("value", "size"),
                                   max_avail=("available_at", "max"), asof_quality=("asof_quality", "max"))
    g["available_at"] = np.maximum(g["max_avail"], g.index + pd.Timedelta(days=1))
    out = g.reset_index().rename(columns={"month_end": "period_end"})
    out["key"], out["item"], out["freq"] = FX_SERIES, "monthly_avg", "M"
    return out[["key", "item", "freq", "period_end", "value", "n_days", "available_at", "asof_quality"]]


def to_krw(usd: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """월간 달러 금액 x 같은 달 원/달러 월평균. available_at 은 둘 중 늦은 날, 등급은 낮은 쪽.
    같은 달 환율이 없으면 그 행은 버린다."""
    f = fx[["period_end", "value", "available_at", "asof_quality"]].rename(
        columns={"value": "_fx", "available_at": "_fx_av", "asof_quality": "_fx_q"})
    m = usd.copy()
    m["period_end"] = pd.to_datetime(m["period_end"])
    m = m.merge(f, on="period_end", how="inner")
    m["value"] = m["value"] * m["_fx"]
    m["available_at"] = np.maximum(pd.to_datetime(m["available_at"]), m["_fx_av"])
    m["asof_quality"] = np.maximum(m["asof_quality"].astype(str), m["_fx_q"].astype(str))
    if "unit" in m:
        m["unit"] = "KRW"
    if "currency" in m:
        m["currency"] = "KRW"
    if "asof_note" in m:
        m["asof_note"] = (m["asof_note"].fillna("") + ";krw:" + FX_SERIES).str.strip(";")
    return m.drop(columns=["_fx", "_fx_av", "_fx_q"])


def _trade_krw(code: str) -> Callable[[], pd.DataFrame]:
    return lambda: to_krw(read_trade([code], items=["expDlr"]), fx_monthly_avg())


def _series(fam: str, key: str) -> Callable[[], pd.DataFrame]:
    return lambda: read_series(fam, [key])


def _airline() -> pd.DataFrame:
    links = entity.entity_links("039130", "airline_series")
    return fp.combine_across_keys(read_series("airline", sorted(links["series_key"])), "intl_dep_sum", "value", "sum")


def _amazon() -> pd.DataFrame:
    links = entity.entity_links("278470", "alt_data")
    keys = sorted(links.loc[links["link_key"].str.startswith("amazon_bsr:"), "series_key"])
    return fp.combine_across_keys(read_series("amazon_bsr", keys), "median", "bsr_rank_median", how="median",
                                  require_all=False)


def _kr_peer(eid: str) -> Callable[[], pd.DataFrame]:
    return lambda: read_revenue([eid], vintage=True)


# 2a 명세서 v3 2절 대상 드라이버 (새 드라이버 탐색 금지)
DRIVER_SPECS: Dict[str, List[DriverSpec]] = {
    "005930": [
        DriverSpec("M1:854232", "trade:854232:expDlr", "M", 0, _trade("854232")),
        DriverSpec("M3:sk_hynix", "kr_peer:000660:revenue", "Q", 1, _kr_peer("000660")),
        DriverSpec("M3:nanya", "tw_revenue:2408", "M", 1, _series("tw_revenue", "2408")),
        DriverSpec("M1:854232:krw", "trade:854232:expKrw", "M", 0, _trade_krw("854232"), alt="krw", replaces="M1:854232"),
    ],
    "039130": [
        DriverSpec("M1:outbound", "tourism:D:100", "M", 0, _series("tourism", "D:100")),
        DriverSpec("M1:inbound", "tourism:E:TOTAL", "M", 0, _series("tourism", "E:TOTAL")),
        DriverSpec("M1:airline", "airline:intl_dep_sum", "M", 0, _airline, aux=True),
    ],
    "267270": [
        DriverSpec("M3:842952", "trade:842952:expDlr", "M", 1, _trade("842952")),
        DriverSpec("M3:842952:krw", "trade:842952:expKrw", "M", 1, _trade_krw("842952"), alt="krw", replaces="M3:842952"),
    ],
    "278470": [DriverSpec("M3:amazon", "amazon_bsr:median_rank", "D", 1, _amazon)],
    "006040": [],      # 대조군: 벤치마크만
    "003350": [],      # 대조군: 벤치마크만
}


# ---------------------------------------------------------------- 공통
def known_at(df: pd.DataFrame, t) -> pd.DataFrame:
    """t 장 마감까지 알려진 값 (vintage 가 여럿이면 가장 늦게 알려진 것)."""
    if df.empty:
        return df
    return asof.latest_known(asof.as_of(df, t))


def quarter_shift(q: pd.Timestamp, n: int) -> pd.Timestamp:
    """분기말 q 에서 n 분기 앞(음수면 뒤)의 분기말."""
    return (q - pd.offsets.QuarterEnd(n)) if n > 0 else (q + pd.offsets.QuarterEnd(-n) if n < 0 else q)


def month_position(period_end: pd.Series, fiscal_year_end: int = 12) -> pd.Series:
    """분기 안에서 몇 번째 달인지 (0, 1, 2)."""
    qe = fp.fiscal_quarter_end(period_end, fiscal_year_end)
    return ((qe.dt.year * 12 + qe.dt.month) - (period_end.dt.year * 12 + period_end.dt.month)).rsub(2).astype(int)


def prepare_driver(raw: pd.DataFrame, fiscal_year_end: int = 12) -> pd.DataFrame:
    """드라이버 긴 표 -> period_end, value, available_at, asof_note, qend, pos."""
    d = raw.dropna(subset=["value"]).copy()
    d["qend"] = fp.fiscal_quarter_end(d["period_end"], fiscal_year_end)
    d["pos"] = month_position(d["period_end"], fiscal_year_end)
    return d.reset_index(drop=True)


# ---------------------------------------------------------------- 드라이버 분기 값
def _level(known: pd.DataFrame, dq: pd.Timestamp, kind: str, positions: Optional[Sequence[int]]):
    """분기 dq 의 수준값. 월간은 positions 월 합계(하나라도 없으면 None), 일별은 관측 평균, 분기는 그 값."""
    g = known[known["qend"] == dq]
    if g.empty:
        return None
    if kind == "M":
        by_pos = g.groupby("pos")["value"].sum()
        if positions is None or any(p not in by_pos.index for p in positions):
            return None
        return float(by_pos.loc[list(positions)].sum())
    if kind == "D":
        return float(g["value"].mean())
    return float(g.loc[g["period_end"] == dq, "value"].iloc[-1]) if (g["period_end"] == dq).any() else None


def _log_yoy(a, b):
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return float(np.log(a) - np.log(b))


def _feature(spec: DriverSpec, known: pd.DataFrame, j: pd.Timestamp, dq: pd.Timestamp, t,
             positions: Optional[Sequence[int]]):
    """분기 j (드라이버 분기 dq) 의 로그 YoY. 세그먼트 가중치가 있으면 세그먼트별 YoY 의 가중평균 (2c 변경)."""
    if spec.weights is None:
        return _log_yoy(_level(known, dq, spec.kind, positions), _level(known, quarter_shift(dq, 4), spec.kind, positions))
    w = {s: v for s, v in spec.weights(j, t).items() if v and v > 0}
    if not w:
        return None
    num = 0.0
    for s, v in w.items():
        ks = known[known["key"] == s]
        g = _log_yoy(_level(ks, dq, spec.kind, positions), _level(ks, quarter_shift(dq, 4), spec.kind, positions))
        if g is None:
            return None
        num += v * g
    return float(num / sum(w.values()))


def target_feature(spec: DriverSpec, drv: pd.DataFrame, q: pd.Timestamp, t,
                   known: Optional[pd.DataFrame] = None) -> Dict:
    """예측 분기 q 의 기준일 t 특징값. 반환: x, coverage, positions, driver_quarter, inputs(사용한 행).
    known: known_at(drv, t) 를 미리 계산했으면 넘긴다 (같은 t 에서 여러 번 쓰지 않도록)."""
    known = known_at(drv, t) if known is None else known
    dq = quarter_shift(q, spec.lag)
    out = {"x": None, "coverage": 0.0, "positions": None, "driver_quarter": dq, "inputs": known.iloc[0:0]}
    g = known[known["qend"] == dq]
    if g.empty:
        return out
    if spec.kind == "M":
        positions = sorted(int(p) for p in g["pos"].unique())
        out["positions"] = positions
        out["coverage"] = len(positions) / 3.0
    elif spec.kind == "D":
        days = (dq - (dq - pd.offsets.QuarterBegin(1, startingMonth=1))).days + 1
        out["coverage"] = min(1.0, g["period_end"].nunique() / days)
    else:
        out["coverage"] = 1.0 if (g["period_end"] == dq).any() else 0.0
    out["x"] = _feature(spec, known, q, dq, t, out["positions"])
    used = known[known["qend"].isin([dq, quarter_shift(dq, 4)])]
    if spec.kind == "M":
        used = used[used["pos"].isin(out["positions"])]
    out["inputs"] = used
    return out


def training_features(spec: DriverSpec, drv: pd.DataFrame, quarters: Sequence[pd.Timestamp], t,
                      positions: Optional[Sequence[int]], known: Optional[pd.DataFrame] = None) -> pd.Series:
    """과거 분기들의 특징값 (기준일 t 에 알던 값, 예측과 같은 월 위치)."""
    known = known_at(drv, t) if known is None else known
    vals = {}
    for j in quarters:
        vals[j] = _feature(spec, known, j, quarter_shift(j, spec.lag), t, positions)
    return pd.Series(vals, dtype="float64")


# ---------------------------------------------------------------- 매출 (목표)
def revenue_frame(entity_id: str) -> pd.DataFrame:
    """read_revenue(vintage=True): 잠정·확정 vintage 행 (available_at 포함)."""
    return read_revenue([entity_id], vintage=True)


def excluded_quarters(entity_id: str, rev_v: pd.DataFrame) -> set:
    """추정·평가에서 빼는 분기: 4분기 확인 사례(+다음 해 4분기)와 기업 사건 분기 (config.CORPORATE_EVENT_EXCLUDE)."""
    ex = set(rev_v.loc[q4_exclusions(rev_v), "period_end"])
    ex |= corporate_event_quarters(entity_id, rev_v["period_end"])
    return ex


def corporate_event_quarters(entity_id: str, quarters) -> set:
    """quarters 중 기업 사건 제외 구간(양 끝 포함)에 드는 분기."""
    qs = pd.DatetimeIndex(pd.to_datetime(pd.Series(quarters)).unique())
    out = set()
    for a, b, _ in CORPORATE_EVENT_EXCLUDE.get(entity_id, []):
        out |= set(qs[(qs >= pd.Timestamp(a)) & (qs <= pd.Timestamp(b))])
    return out


def is_covid(q: pd.Timestamp) -> bool:
    return COVID_START <= q <= COVID_END


def revenue_known(rev_v: pd.DataFrame, t) -> pd.Series:
    """기준일 t 에 알던 분기 매출 (잠정 공시 뒤면 잠정, 확정 공시 뒤면 확정)."""
    k = known_at(rev_v, t)
    return k.set_index("period_end")["value"].sort_index()


def growth(R: pd.Series) -> pd.Series:
    """g_q = ln R_q - ln R_{q-4} (둘 다 양수일 때만)."""
    out = {}
    for q, v in R.items():
        b = R.get(quarter_shift(q, 4))
        if b is not None and v > 0 and b > 0:
            out[q] = float(np.log(v) - np.log(b))
    return pd.Series(list(out.values()), index=pd.DatetimeIndex(list(out.keys())), dtype="float64").sort_index()


def actuals(rev_v: pd.DataFrame) -> pd.DataFrame:
    """분기별 실제치: 첫 공시 값(가장 먼저 알려진 vintage)과 확정값."""
    d = rev_v.sort_values(["period_end", "available_at"])
    first = d.groupby("period_end").first()[["value", "available_at"]].rename(
        columns={"value": "rev_actual_first", "available_at": "first_dt"})
    fin = d[d["asof_note"].str.startswith("vintage:final")].groupby("period_end")["value"].last().rename("rev_actual_final")
    return first.join(fin, how="left")
