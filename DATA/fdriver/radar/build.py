# -*- coding: utf-8 -*-
"""R1 (c): 레이더. python -m DATA.fdriver.radar.build --quarter 2026Q3 [--asof YYYY-MM-DD]

1. 자료 점검: 드라이버별 최신 달(기준일에 공개된 것), 규칙상 나와 있어야 할 달 대비 지연, 최근 12개월 결측 달.
2. nowcast 갱신: 2a nowcast_entity 로 M4, M1/M3(M2), B2 (+ B1, B3) 를 계산하고 radar.ts 로 TS, E 를 더한다.
   - 실적이 아직 없는 분기: 기준일 t (기본 오늘) 한 번 (checkpoint 'live').
   - 실적이 이미 있는 분기(시험용): 기업별 pre5 기준일 (nowcast 와 같은 checkpoints 규칙).
3. 레이더 표 (기업당 한 행): 종목·업종·등급, 대표 예상 매출 E (원화 금액, 전년 대비 성장률), 보조 M4 예상, B2 성장률,
   nsur_drv 크기·방향, E 방향, 일치 여부, 충격 경보, 드라이버 최근 3개월 전년 대비, 경고, 예상 발표일, 컨센서스.
4. 출력: radar/<분기>/radar_<분기>_<생성일시>.csv 와 .html (덮어쓰지 않음).

정의 (R1 에서 정함, 리포트에 기록)
- 성장률: 전년 동기 대비 로그 성장률 g. 예상 매출 = rev_base x exp(g).
- nsur_drv: 2a v6 정의, level 변형 (signals.sur.build_signals). 방향 = 부호.
- E 방향: sign(g_E - g_B2) (모멘텀 B2 대비 E 가 위/아래). 일치 = sign(nsur_drv) == E 방향 (둘 중 0 이면 '-').
- 충격 경보: x = g_M4 - g_B2. 그 기업의 과거 pre5 nowcast(기준일 < t) 의 x 분포에서 10% 분위 미만 또는 90% 분위 초과면
  경보 (8개 미만이면 판단 안 함). 시범 대조군은 해당 없음.
- 드라이버 최근 3개월: 주 드라이버(첫 번째 주 DriverSpec)의 t 에 공개된 마지막 3개월 합 / 1년 전 같은 3개월 합 (로그).
  세그먼트 가중 기업은 세그먼트별 값을 그 분기 비중으로 가중평균.
- 경고: 'Q4차감'(대상이 회계 4분기: 실제치가 연간 - 1~3분기), '드라이버결측'(M4 가 없거나 coverage < 1 이면
  '드라이버부분(cov=)'), '기업사건'(대상 분기가 기업 사건 제외 구간).
- 예상 발표일: 이벤트표 첫 공시일이 있으면 그 날 ('실제'). 없으면 최근 5년 같은 분기 번호의 (첫 공시일 - 분기말) 일수
  중앙값을 분기말에 더함 ('추정').
- 컨센서스 (2b, K3 고정): adapters.kr_consensus.read_consensus 로 대상 분기 매출 컨센서스 중 관측일 <= t 인 최신 값
  consensus(원)와 관측일 consensus_asof. 열 정의는 COLUMN_DESC (출력 때 radar_columns_*.csv 로 함께 저장).
  g_* 열은 로그 차분 ln(예상 / 전년 동기), *_pct 열은 단순 성장률 (%), gap_* 은 비율 (0.05 = 5%).
- E 구성 예측 (K3 6): rev_M1, rev_B2, rev_TS = rev_base x exp(g_*) (원), g_M1_pct, g_B2_pct, g_TS_pct (%).
  E = rev_base x exp(구성 요소 로그 성장률의 평균) = 세 예측(rev_M1, rev_B2, rev_TS)의 기하평균 (있는 것만).
"""
import argparse
import html
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import asof, entity, events
from ..adapters import kr_consensus
from ..config import output_path, RELEASE_RULES
from ..nowcast import features as ft
from ..nowcast import run
from ..signals import sur
from . import ts
from .universe import CONTROLS, universe

ALERT_LO, ALERT_HI, ALERT_MIN_HIST = 0.10, 0.90, 8


def _register():
    from ..stage2c import registry
    registry.register(rules="2b2")


def main_driver_model(e: str) -> Optional[str]:
    comps = [s.model_id for s in ft.DRIVER_SPECS.get(e, []) if not s.aux and s.alt is None]
    if len(comps) >= 2:
        return "M4:combo"
    return ("M4:" + comps[0].split(":", 1)[1]) if comps else None


# ---------------------------------------------------------------- 1. 자료 점검
def driver_check(e: str, t: pd.Timestamp) -> List[Dict]:
    rows = []
    for s in ft.DRIVER_SPECS.get(e, []):
        if s.alt:
            continue
        try:
            raw = s.loader()
        except Exception as ex:  # noqa: BLE001
            rows.append({"entity_id": e, "driver": s.model_id, "error": str(ex)[:120]})
            continue
        k = ft.known_at(raw.dropna(subset=["value"]), t)
        if k.empty:
            rows.append({"entity_id": e, "driver": s.model_id, "latest_month": None})
            continue
        months = pd.to_datetime(k["period_end"]).drop_duplicates().sort_values()
        latest = months.max()
        # 규칙상 t 에 나와 있어야 할 마지막 달: 그 달 말 + 공표 지연(규칙 날짜) <= t
        fam = "trade" if s.driver_key.startswith("trade") else s.driver_key.split(":")[0]
        cand = pd.date_range(t - pd.DateOffset(months=6), t, freq="ME")
        try:
            rd = asof.rule_dates(pd.Series(cand), fam if fam in RELEASE_RULES else "trade", freq="M")
            expected = max([c for c, r in zip(cand, rd) if pd.notna(r) and r <= t], default=None)
        except Exception:        # noqa: BLE001
            expected = None
        last12 = pd.date_range(latest - pd.DateOffset(months=11), latest, freq="ME")
        missing = [m.strftime("%Y-%m") for m in last12 if m not in set(months)]
        lag = None if expected is None else int(round((expected.year * 12 + expected.month - latest.year * 12 - latest.month)))
        rows.append({"entity_id": e, "driver": s.model_id, "driver_key": s.driver_key,
                     "latest_month": latest.strftime("%Y-%m"),
                     "expected_month": expected.strftime("%Y-%m") if expected is not None else None,
                     "delay_months": lag, "missing_last12": ",".join(missing)})
    return rows


# ---------------------------------------------------------------- 드라이버 최근 3개월
def driver_recent3(e: str, q: pd.Timestamp, t: pd.Timestamp) -> Dict:
    specs = [s for s in ft.DRIVER_SPECS.get(e, []) if not s.aux and s.alt is None]
    if not specs:
        return {"drv_3m_yoy": np.nan, "drv_3m_last": None}
    s = specs[0]
    k = ft.known_at(s.loader().dropna(subset=["value"]), t)
    if k.empty:
        return {"drv_3m_yoy": np.nan, "drv_3m_last": None}
    k = k.copy()
    k["period_end"] = pd.to_datetime(k["period_end"])
    last = k["period_end"].max()
    cur = pd.date_range(last - pd.DateOffset(months=2), last, freq="ME")
    prev = [c - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0) for c in cur]

    def g_of(frame):
        a = frame[frame["period_end"].isin(cur)].groupby("period_end")["value"].sum()
        b = frame[frame["period_end"].isin(prev)].groupby("period_end")["value"].sum()
        if len(a) < 3 or len(b) < 3 or a.sum() <= 0 or b.sum() <= 0:
            return np.nan
        return float(np.log(a.sum()) - np.log(b.sum()))

    if s.weights is not None:
        w = {kk: v for kk, v in s.weights(q, t).items() if v and v > 0}
        gs = {kk: g_of(k[k["key"] == kk]) for kk in w}
        ok = {kk: v for kk, v in gs.items() if pd.notna(v)}
        g = float(sum(w[kk] * v for kk, v in ok.items()) / sum(w[kk] for kk in ok)) if ok and len(ok) == len(w) else np.nan
    else:
        g = g_of(k)
    return {"drv_3m_yoy": g, "drv_3m_last": last.strftime("%Y-%m")}


# ---------------------------------------------------------------- 예상 발표일
def expected_announce(e: str, q: pd.Timestamp, fye: int, ev: pd.DataFrame) -> Dict:
    fd = events.first_disclosure(ev, {e: fye})
    hit = fd[fd["period_end"] == q]
    if len(hit):
        return {"announce_date": hit["first_dt"].iloc[0].date(), "announce_kind": "실제"}
    qn = events.fiscal_quarter_label(q, fye)[-1]
    past = fd[[events.fiscal_quarter_label(p, fye)[-1] == qn for p in fd["period_end"]]]
    past = past[past["period_end"] < q].sort_values("period_end").tail(5)
    if past.empty:
        return {"announce_date": None, "announce_kind": "추정 불가"}
    days = (past["first_dt"] - past["period_end"]).dt.days.median()
    return {"announce_date": (q + pd.Timedelta(days=int(days))).date(), "announce_kind": f"추정 (최근 {len(past)}년 중앙값 {int(days)}일)"}


# ---------------------------------------------------------------- 충격 경보 분포
def shock_history(e: str, main_model: str, t: pd.Timestamp) -> pd.Series:
    from .. import db
    nc = db.read_sql("SELECT fiscal_quarter, asof_date, model_id, checkpoint, g_hat FROM fd_nowcast WHERE entity_id = :e "
                     "AND model_id IN (:m, 'B2') AND checkpoint LIKE '%%pre5%%'", {"e": e, "m": main_model})
    nc["asof_date"] = pd.to_datetime(nc["asof_date"])
    nc = nc[nc["asof_date"] < t]
    p = nc.pivot_table(index="fiscal_quarter", columns="model_id", values="g_hat", aggfunc="first").dropna()
    return (p[main_model] - p["B2"]) if main_model in p and "B2" in p else pd.Series(dtype="float64")


# ---------------------------------------------------------------- 2·3. 기업 한 줄
def entity_row(e: str, q: pd.Timestamp, t_live: Optional[pd.Timestamp], meta: Dict) -> Dict:
    info = entity.entity_info([e])
    fye = int(info["fiscal_year_end"].iloc[0])
    rv = ft.revenue_frame(e)
    act = ft.actuals(rv)
    has_actual = q in act.index
    if has_actual:
        r = run.nowcast_entity(e, quarters=[q], variants=("all",))
        rows = r["rows"]
        rows = rows[rows["checkpoint"].str.contains("pre5")]
        t = pd.Timestamp(rows["asof_date"].iloc[0]) if len(rows) else None
    else:
        r = run.nowcast_entity(e, quarters=[], live={q: t_live}, variants=("all",))
        rows, t = r["rows"], t_live
    out = {**meta, "fiscal_quarter": events.fiscal_quarter_label(q, fye), "asof_date": None if t is None else t.date(),
           "mode": "pre5 (실적 있음)" if has_actual else "live"}
    if rows is None or rows.empty or t is None:
        out["warnings"] = "nowcast 없음 (" + ",".join(sorted(set(r["skips"].get("reason", pd.Series(dtype=str)).astype(str)))) + ")"
        return out
    rows = rows.copy()
    rows["asof_date"] = pd.to_datetime(rows["asof_date"])
    base = float(rows["rev_base"].iloc[0])
    g = dict(zip(rows["model_id"], rows["g_hat"]))
    cov = dict(zip(rows["model_id"], rows["coverage"]))
    # TS, E
    R = ft.revenue_known(rv, t)
    tsr = ts.ts_forecast(R, q, base)
    dm = ts.e_component(e)
    en = ts.ensemble(g.get(dm) if dm else None, g.get("B2"), tsr["g_hat"], dm)
    g["TS"], g["E"] = tsr["g_hat"], en["g_hat"]
    mm = main_driver_model(e)
    extra = []
    for mid in ("TS", "E"):
        if g[mid] is not None:
            extra.append({**rows.iloc[0].to_dict(), "model_id": mid, "g_hat": g[mid], "rev_hat": base * np.exp(g[mid]),
                          "coverage": None, "drivers_used": tsr["label"] if mid == "TS" else en["label"]})
    allrows = pd.concat([rows, pd.DataFrame(extra)], ignore_index=True)
    sig = sur.build_signals(e, nc=allrows, variants=("level",))["rows"]
    sg = sig.set_index("model_id") if len(sig) else pd.DataFrame()

    def val(m, col):
        return float(sg.loc[m, col]) if (len(sg) and m in sg.index and pd.notna(sg.loc[m, col])) else np.nan

    gm4 = g.get(mm) if mm else None
    out.update({"main_model": mm, "rev_base": base,
                "g_M4": gm4, "rev_M4": base * np.exp(gm4) if gm4 is not None and pd.notna(gm4) else np.nan,
                "g_E": g["E"], "rev_E": base * np.exp(g["E"]) if g["E"] is not None else np.nan, "E_components": en["label"],
                "g_B2": g.get("B2"), "g_TS": g["TS"], "g_M1": g.get(dm) if dm else np.nan, "M1_model": dm,
                "nsur_drv": val(mm, "nsur_drv") if mm else np.nan, "nsur_M4": val(mm, "nsur") if mm else np.nan,
                "nsur_mom": val("B2", "nsur"), "delta": val("B2", "delta"), "sigma": val("B2", "sigma")})
    # 모델별 상세 (실전 기록용, live.LIVE_COLUMNS 형식)
    det = allrows.merge(sig[["model_id", "nsur", "nsur_mom", "nsur_drv", "delta", "sigma"]], on="model_id", how="left")         if len(sig) else allrows.assign(nsur=np.nan, nsur_mom=np.nan, nsur_drv=np.nan, delta=np.nan, sigma=np.nan)
    out["_models"] = det
    d_sign = np.sign(out["nsur_drv"]) if pd.notna(out["nsur_drv"]) else 0
    e_sign = np.sign(g["E"] - g["B2"]) if (g["E"] is not None and g.get("B2") is not None) else 0
    out["drv_direction"] = {1: "+", -1: "-", 0: "0"}[int(d_sign)] if mm else ""
    out["E_direction"] = {1: "+", -1: "-", 0: "0"}[int(e_sign)]
    out["agree"] = ("-" if (d_sign == 0 or e_sign == 0) else ("일치" if d_sign == e_sign else "불일치")) if mm else ""
    # 충격 경보
    if mm and gm4 is not None and pd.notna(gm4) and e not in CONTROLS:
        hist = shock_history(e, mm, t)
        x = gm4 - g["B2"]
        if len(hist) >= ALERT_MIN_HIST:
            lo, hi = hist.quantile(ALERT_LO), hist.quantile(ALERT_HI)
            out.update({"shock_x": x, "shock_p10": lo, "shock_p90": hi, "shock_n": len(hist),
                        "shock_alert": "경보(상)" if x > hi else ("경보(하)" if x < lo else "")})
        else:
            out.update({"shock_x": x, "shock_n": len(hist), "shock_alert": "판단 안 함(과거 8개 미만)"})
    out.update(driver_recent3(e, q, t))
    warn = []
    if events.fiscal_quarter_label(q, fye).endswith("Q4"):
        warn.append("Q4차감")
    if mm and (gm4 is None or pd.isna(gm4)):
        warn.append("드라이버결측")
    elif mm and pd.notna(cov.get(mm)) and cov.get(mm) < 1:
        warn.append(f"드라이버부분(cov={cov.get(mm):.2f})")
    if ft.corporate_event_quarters(e, [q]):
        warn.append("기업사건")
    out["warnings"] = ";".join(warn)
    out.update(expected_announce(e, q, fye, events.read_events([e])))
    if e in CONTROLS:              # 대조군: B2 만 표시
        for k in ("g_M4", "rev_M4", "g_E", "rev_E", "nsur_drv", "drv_direction", "E_direction", "agree", "g_TS"):
            out[k] = np.nan if not isinstance(out.get(k), str) else ""
    out.update(consensus_cols(e, out["fiscal_quarter"], t, out.get("rev_E"), out.get("rev_M4"), out.get("rev_base")))
    return out


# |gap_E| 크기 구분: 2b K2 판정 표본(fd_eval_2b run 20261012_222348, 488건)의 |gap_E| 1/3, 2/3 분위 (고정값)
GAP_SIZE_CUTS = (0.0244, 0.0733)


def _sign_label(x) -> str:
    return "" if x is None or pd.isna(x) else ("위" if x > 0 else ("아래" if x < 0 else "-"))


def consensus_cols(e: str, fq: str, t: pd.Timestamp, rev_E, rev_M4, rev_base=None) -> Dict:
    c = kr_consensus.read_consensus(e, fq, t)
    v = c["value"]
    gap = lambda n: (float(n) / v - 1) if v and n is not None and pd.notna(n) else np.nan
    gE, gM = gap(rev_E), gap(rev_M4)
    size = "" if pd.isna(gE) else ("소" if abs(gE) < GAP_SIZE_CUTS[0] else ("중" if abs(gE) < GAP_SIZE_CUTS[1] else "대"))
    agree = "" if pd.isna(gE) or pd.isna(gM) else ("-" if gE == 0 or gM == 0 else ("일치" if np.sign(gE) == np.sign(gM) else "불일치"))
    g_c = (v / float(rev_base) - 1) * 100 if v and rev_base is not None and pd.notna(rev_base) and rev_base > 0 else np.nan
    return {"consensus": v if v else np.nan, "consensus_asof": c["asof"].date() if c["asof"] is not None else None,
            "g_C_pct": g_c, "gap_E": gE, "gap_M4": gM, "gap_dir": _sign_label(gE), "gap_size": size, "gap_agree": agree}


COLUMNS = ["grade", "entity_id", "name", "sector", "fiscal_quarter", "mode", "asof_date", "main_model",
           "rev_E", "g_E", "g_E_pct", "rev_M4", "g_M4", "g_M4_pct", "g_B2", "g_TS", "g_M1", "M1_model", "E_components",
           "rev_M1", "rev_B2", "rev_TS", "g_M1_pct", "g_B2_pct", "g_TS_pct",
           "nsur_drv",
           "drv_direction", "E_direction", "agree", "shock_alert", "shock_x", "shock_p10", "shock_p90", "shock_n",
           "drv_3m_yoy", "drv_3m_last", "warnings", "announce_date", "announce_kind", "consensus", "consensus_asof",
           "g_C_pct", "gap_E", "gap_M4", "gap_dir", "gap_size", "gap_agree",
           "rev_base", "nsur_M4", "nsur_mom", "delta", "sigma"]
COLUMN_DESC = {
    "rev_E": ("대표 예상 매출 E (원) = rev_base x exp((g_M1 + g_B2 + g_TS) / 3), 즉 세 구성 예측 rev_M1, rev_B2, rev_TS 의 "
              "기하평균 (없는 요소는 빼고 남은 것의 평균, 대조군은 B2·TS)"),
    "g_E": "E 성장률, 로그 차분 ln(rev_E / rev_base)",
    "g_E_pct": "E 단순 성장률 (%) = (exp(g_E) - 1) x 100",
    "rev_M4": "보조: M4 예상 매출 (원)",
    "g_M4": "M4 성장률, 로그 차분",
    "g_M4_pct": "M4 단순 성장률 (%)",
    "g_B2": "B2 (모멘텀 벤치마크) 성장률, 로그 차분",
    "g_TS": "TS (ETS, ARIMA 평균) 성장률, 로그 차분",
    "g_M1": "E 의 드라이버 요소 성장률, 로그 차분 (모델은 M1_model: M1/M3 드라이버 1개, M2 드라이버 2개 이상)",
    "M1_model": "E 의 드라이버 요소 모델 id",
    "rev_M1": "E 의 드라이버 요소 예상 매출 (원) = rev_base x exp(g_M1)",
    "rev_B2": "B2 예상 매출 (원) = rev_base x exp(g_B2)",
    "rev_TS": "TS 예상 매출 (원) = rev_base x exp(g_TS)",
    "g_M1_pct": "드라이버 요소 단순 성장률 (%) = (exp(g_M1) - 1) x 100",
    "g_B2_pct": "B2 단순 성장률 (%)",
    "g_TS_pct": "TS 단순 성장률 (%)",
    "nsur_drv": "2c M4 서프라이즈 신호 (레이더 주 신호)",
    "agree": "drv 방향(nsur_drv 부호)과 E 방향(sign(g_E - g_B2)) 일치",
    "consensus": "대상 분기 매출 컨센서스 (원), 관측일 <= 기준일 인 최신 값 (kr_revenue_consensus)",
    "consensus_asof": "컨센서스 관측일",
    "g_C_pct": "컨센서스 단순 성장률 (%) = (consensus / rev_base - 1) x 100",
    "gap_E": "rev_E / consensus - 1 (비율, 2b 검증 참고 신호)",
    "gap_M4": "rev_M4 / consensus - 1 (비율, 참고)",
    "gap_dir": "gap_E 방향: 위 (예상 > 컨센서스) / 아래 / -",
    "gap_size": "|gap_E| 크기: 소 < 2.44% <= 중 < 7.33% <= 대 (2b K2 판정 표본 3분위)",
    "gap_agree": "gap_M4 와 gap_E 부호 일치 (일치 / 불일치 / -)",
    "rev_base": "전년 동기 매출 (원, 기준일에 알던 값)",
}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "B2만": 3}


def build(quarter: str, asof_date=None) -> Dict:
    _register()
    u = universe()
    q = events.fiscal_quarter_end(quarter)
    t = pd.Timestamp(asof_date).normalize() if asof_date else pd.Timestamp.today().normalize()
    rows, checks, models = [], [], []
    for r in u.itertuples():
        meta = {"entity_id": r.entity_id, "name": r.name, "sector": r.sector, "grade": r.grade}
        row = entity_row(r.entity_id, q, t, meta)
        m = row.pop("_models", None)
        if m is not None and len(m):
            models.append(m.assign(entity_id=r.entity_id))
        rows.append(row)
        checks += driver_check(r.entity_id, t)
    tab = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in tab:
            tab[c] = np.nan
    base = pd.to_numeric(tab["rev_base"], errors="coerce")
    for m in ("E", "M4", "M1", "B2", "TS"):
        g = pd.to_numeric(tab[f"g_{m}"], errors="coerce")
        tab[f"g_{m}_pct"] = (np.exp(g) - 1) * 100
        if m in ("M1", "B2", "TS"):
            tab[f"rev_{m}"] = base * np.exp(g)
    tab = tab[COLUMNS]
    tab["_o"] = tab["grade"].map(GRADE_ORDER)
    tab = tab.sort_values(["_o", "name"]).drop(columns="_o").reset_index(drop=True)
    return {"table": tab, "checks": pd.DataFrame(checks), "quarter": quarter, "t": t,
            "models": pd.concat(models, ignore_index=True) if models else pd.DataFrame()}


# ---------------------------------------------------------------- 4. 출력
def to_html(tab: pd.DataFrame, title: str) -> str:
    show = [("grade", "등급"), ("name", "기업"), ("sector", "업종"), ("asof_date", "기준일"),
            ("rev_E", "예상 매출 E(조원)"), ("M1_model", "드라이버 모델"), ("rev_M1", "드라이버(조원)"),
            ("rev_B2", "B2(조원)"), ("rev_TS", "TS(조원)"), ("rev_M4", "보조: M4(조원)"), ("consensus", "컨센서스(조원)"),
            ("g_E_pct", "E 성장률"), ("g_M1_pct", "드라이버 성장률"), ("g_B2_pct", "B2 성장률"), ("g_TS_pct", "TS 성장률"),
            ("g_M4_pct", "보조: M4 성장률"), ("g_C_pct", "컨센서스 성장률"),
            ("nsur_drv", "nsur_drv"), ("drv_direction", "drv 방향"), ("E_direction", "E 방향"), ("agree", "일치"),
            ("shock_alert", "충격 경보"), ("drv_3m_yoy", "드라이버 3개월"), ("warnings", "경고"),
            ("announce_date", "예상 발표일"), ("announce_kind", "발표일 근거"),
            ("consensus_asof", "컨센서스 관측일"), ("gap_E", "차이 E-컨센"),
            ("gap_dir", "차이 방향"), ("gap_size", "차이 크기"), ("gap_M4", "보조: 차이 M4-컨센"), ("gap_agree", "M4·E 차이 일치")]

    def fmt(c, v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        if c in ("rev_M4", "rev_E", "consensus", "rev_M1", "rev_B2", "rev_TS"):
            return f"{v / 1e12:,.3f}"
        if c in ("g_E_pct", "g_M4_pct", "g_C_pct", "g_M1_pct", "g_B2_pct", "g_TS_pct"):
            return f"{v:+.1f}%"
        if c in ("g_M4", "g_E", "g_B2", "drv_3m_yoy"):
            return f"{(np.exp(v) - 1) * 100:+.1f}%"
        if c in ("gap_E", "gap_M4"):
            return f"{v * 100:+.1f}%"
        if c == "nsur_drv":
            return f"{v:+.2f}"
        return html.escape(str(v))

    head = "".join(f"<th>{h}</th>" for _, h in show)
    body = []
    for _, r in tab.iterrows():
        cls = []
        if isinstance(r["shock_alert"], str) and r["shock_alert"].startswith("경보"):
            cls.append("alert")
        if r["agree"] == "불일치":
            cls.append("mismatch")
        body.append(f"<tr class='{' '.join(cls)}'>" + "".join(f"<td>{fmt(c, r[c])}</td>" for c, _ in show) + "</tr>")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#e6e5e0;--alert:#fde8e7;--mis:#fff5d6}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1a1a19;--ink:#f2f2f0;--muted:#c3c2b7;--line:#383835;--alert:#4a2423;--mis:#46391a}}}}
body{{background:var(--bg);color:var(--ink);font-family:-apple-system,'Malgun Gothic',sans-serif;margin:16px}}
h1{{font-size:18px}} p{{color:var(--muted);font-size:13px}} .wrap{{overflow-x:auto}}
table{{border-collapse:collapse;font-size:12px;white-space:nowrap}} th,td{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}}
th{{position:sticky;top:0;background:var(--bg)}} td:nth-child(-n+3),th:nth-child(-n+3){{text-align:left}}
tr.alert td{{background:var(--alert)}} tr.mismatch td{{background:var(--mis)}} tr.alert.mismatch td{{background:var(--alert)}}
</style></head><body><h1>{html.escape(title)}</h1>
<p>등급별 정렬. 대표 예상 매출은 E = 드라이버·B2·TS 세 예측의 기하평균 (로그 성장률 평균), M4 는 보조 열. 서프라이즈(nsur_drv)·충격 경보는 M4 기준.
차이 = 예상 매출 / 최신 컨센서스 - 1 (2b 검증에서 Q2 미달로 주 신호는 nsur_drv 유지, 참고 열).
붉은 줄 = 충격 경보, 노란 줄 = drv 방향과 E 방향 불일치. 성장률은 전년 동기 대비 단순 성장률 (exp(g)-1).
CSV 의 g_* 열은 로그 차분 ln(예상 / 전년 동기), *_pct 열이 단순 성장률(%). 차이 크기: 소 &lt; 2.44% &le; 중 &lt; 7.33% &le; 대.
열 설명은 같은 생성 시각의 radar_columns_*.csv. 등급은 표시용이며 계산에 쓰지 않음.</p>
<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div></body></html>"""


def save(res: Dict) -> Dict:
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    d = output_path("radar", res["quarter"])
    csv = d / f"radar_{res['quarter']}_{stamp}.csv"
    htm = d / f"radar_{res['quarter']}_{stamp}.html"
    chk = d / f"datacheck_{res['quarter']}_{stamp}.csv"
    cols = d / f"radar_columns_{res['quarter']}_{stamp}.csv"
    for f in (csv, htm, chk, cols):
        if f.exists():
            raise FileExistsError(f)
    res["table"].to_csv(csv, index=False, encoding="utf-8-sig")
    res["checks"].to_csv(chk, index=False, encoding="utf-8-sig")
    pd.DataFrame({"column": COLUMNS, "description": [COLUMN_DESC.get(c, "") for c in COLUMNS]}).to_csv(
        cols, index=False, encoding="utf-8-sig")
    htm.write_text(to_html(res["table"], f"fdriver 레이더 {res['quarter']} (기준일 {res['t'].date()}, 생성 {stamp})"),
                   encoding="utf-8")
    return {"csv": csv, "html": htm, "datacheck": chk, "columns": cols, "stamp": stamp}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args(argv)
    res = build(a.quarter, a.asof)
    paths = save(res)
    pd.set_option("display.width", 250)
    print(paths)
    print(res["table"][["grade", "name", "asof_date", "g_E", "g_M4", "g_B2", "nsur_drv", "agree", "shock_alert",
                        "warnings", "announce_date"]].to_string(index=False))


if __name__ == "__main__":
    main()
