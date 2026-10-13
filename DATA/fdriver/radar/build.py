# -*- coding: utf-8 -*-
"""R1 (c): 레이더. python -m DATA.fdriver.radar.build --quarter 2026Q3 [--asof YYYY-MM-DD]

1. 자료 점검: 드라이버별 최신 달(기준일에 공개된 것), 규칙상 나와 있어야 할 달 대비 지연, 최근 12개월 결측 달.
2. nowcast 갱신: 2a nowcast_entity 로 M4, M1/M3(M2), B2 (+ B1, B3) 를 계산하고 radar.ts 로 TS, E 를 더한다.
   - 실적이 아직 없는 분기: 기준일 t (기본 오늘) 한 번 (checkpoint 'live').
   - 실적이 이미 있는 분기(시험용): 기업별 pre5 기준일 (nowcast 와 같은 checkpoints 규칙).
3. 레이더 표 (기업당 한 행): 종목·업종·등급, 대표 예상 매출 E (원화 금액, 전년 대비 성장률), 보조 M4 예상, B2 성장률,
   nsur_drv 크기·방향, E 방향, 일치 여부, 충격 경보, 드라이버 최근 3개월 전년 대비, 경고, 예상 발표일, 컨센서스.
4. 출력 (v2, radar.output): 내부 표를 출력 이름으로 바꾼 표와 판단 표.
   radar/<분기>/radar_<분기>_<생성일시>.csv (출력 이름), _decision.csv (판단), .html (판단 -> 전체 -> 변수설명),
   .xlsx (판단 | radar | 변수설명), radar_columns_*.csv (변수설명). 덮어쓰지 않음.
   내부 표(build() 의 table, COLUMNS)와 DB·실전 기록 열 이름은 그대로 둔다 (출력 이름 대응은 coldesc.COLDESC 'internal').

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
  consensus(원)와 관측일 consensus_asof.
- 열 설명 (COLDESC v1): radar.coldesc.COLDESC 하나에서 CSV(radar_columns_*), HTML 아래 설명 표, 엑셀(radar.excel)
  '변수설명' 시트와 머리글 메모를 모두 만든다. 설명이 없는 열이 있으면 출력하지 않고 멈춘다. 내부용 (공개 출력은 radar.public).
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
from . import coldesc, excel, output, ts
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


# ---------------------------------------------------------------- 4. 출력 (v2: 출력 이름, 판단, 변수설명)
def _fmt(c: str, v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return ""
    if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool):
        if c.startswith("jo_"):
            return f"{v:,.3f}"
        if c.startswith("rev_"):
            return f"{v:,.0f}"
        if c.endswith("_pct"):
            return f"{v:+.1f}"
        if c.startswith("z_"):
            return f"{v:+.2f}"
        if c.startswith("g_") or c.startswith("shock_") and c != "shock_n":
            return f"{v:+.4f}"
        return f"{v:g}"
    return html.escape(str(v))


def _table_html(t: pd.DataFrame, cls: str = "", row_class=None) -> str:
    head = "".join(f"<th title='{html.escape(coldesc.COLDESC[c]['meaning'], quote=True)}'>{html.escape(c)}</th>"
                   for c in t.columns)
    body = []
    for _, r in t.iterrows():
        rc = row_class(r) if row_class else ""
        body.append(f"<tr class='{rc}'>" + "".join(f"<td>{_fmt(c, r[c])}</td>" for c in t.columns) + "</tr>")
    return f"<div class='wrap'><table class='{cls}'><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def decision_class(r) -> str:
    d = str(r.get("decision", ""))
    return {"후보(상": "up", "후보(하": "down", "검토": "review", "보류": "hold"}.get(
        next((k for k in ("후보(상", "후보(하", "검토", "보류") if d.startswith(k)), ""), "")


def desc_html(columns) -> str:
    """변수 설명 표 (coldesc.table, 내부용)."""
    t = coldesc.table(columns)
    head = "".join(f"<th>{html.escape(c)}</th>" for c in t.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>" for r in t.itertuples(index=False))
    return f'<h2>변수 설명</h2><div class="wrap"><table class="desc"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def to_html(out: pd.DataFrame, judge: pd.DataFrame, title: str) -> str:
    """맨 위 판단 표, 그 아래 전체 레이더 표 (출력 이름), 맨 아래 변수 설명 표. 머리글에 마우스를 올리면 의미."""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#e6e5e0;--up:#e3f1e6;--down:#fde8e7;--review:#fff5d6;--hold:#ecebe7}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1a1a19;--ink:#f2f2f0;--muted:#c3c2b7;--line:#383835;--up:#1f3a26;--down:#4a2423;--review:#46391a;--hold:#2c2c2a}}}}
body{{background:var(--bg);color:var(--ink);font-family:-apple-system,'Malgun Gothic',sans-serif;margin:16px}}
h1{{font-size:18px}} h2{{font-size:15px;margin-top:24px}} p{{color:var(--muted);font-size:13px}} .wrap{{overflow-x:auto}}
table{{border-collapse:collapse;font-size:12px;white-space:nowrap}} th,td{{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}}
th{{position:sticky;top:0;background:var(--bg)}} td:nth-child(-n+4),th:nth-child(-n+4){{text-align:left}}
tr.up td{{background:var(--up)}} tr.down td{{background:var(--down)}} tr.review td{{background:var(--review)}} tr.hold td{{background:var(--hold)}}
table.desc{{white-space:normal}} table.desc td,table.desc th{{text-align:left;vertical-align:top;max-width:420px}}
</style></head><body><h1>{html.escape(title)}</h1>
<h2>판단</h2><p>{html.escape(output.rule_summary())}</p>
{_table_html(judge, "judge", decision_class)}
<h2>레이더 전체</h2><p>열 이름 규칙: rev_ 원, jo_ 조원, g_ 로그 성장률, *_pct %, gap_*_pct 차이 %, dir_ 방향 (+/-/공란), agree_ 일치, z_ 표준화 값,
spread_ 구성 불일치, shock_ 충격 경보 계산 값, flag_ 표시 (켜지면 사유), drv_ 드라이버 흐름. 행 색: 초록 후보(상향), 붉은 후보(하향), 노랑 검토, 회색 보류.</p>
{_table_html(out, "", decision_class)}
{desc_html(list(out.columns))}</body></html>"""


def outputs(res: Dict) -> Dict:
    """내부 표 -> 출력 표·판단 표 (판단·변수설명은 내부용)."""
    out = output.to_output(res["table"])
    return {"out": out, "judge": output.judgement(out)}


def save(res: Dict) -> Dict:
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    d = output_path("radar", res["quarter"])
    base = f"radar_{res['quarter']}_{stamp}"
    csv, dec, htm, xlsx = d / f"{base}.csv", d / f"{base}_decision.csv", d / f"{base}.html", d / f"{base}.xlsx"
    chk = d / f"datacheck_{res['quarter']}_{stamp}.csv"
    cols = d / f"radar_columns_{res['quarter']}_{stamp}.csv"
    for f in (csv, dec, htm, chk, cols, xlsx):
        if f.exists():
            raise FileExistsError(f)
    o = outputs(res)
    out, judge = o["out"], o["judge"]
    desc = coldesc.table(list(out.columns))                   # 설명이 없는 열이 있으면 아무것도 쓰기 전에 멈춤
    coldesc.table(list(judge.columns))
    coldesc.unused(out.columns)
    out.to_csv(csv, index=False, encoding="utf-8-sig")
    judge.to_csv(dec, index=False, encoding="utf-8-sig")
    res["checks"].to_csv(chk, index=False, encoding="utf-8-sig")
    desc.to_csv(cols, index=False, encoding="utf-8-sig")
    excel.to_xlsx(out, xlsx, judge=judge)
    htm.write_text(to_html(out, judge, f"fdriver 레이더 {res['quarter']} (기준일 {res['t'].date()}, 생성 {stamp})"),
                   encoding="utf-8")
    return {"csv": csv, "decision": dec, "html": htm, "xlsx": xlsx, "datacheck": chk, "columns": cols, "stamp": stamp,
            "judge": judge}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args(argv)
    res = build(a.quarter, a.asof)
    paths = save(res)
    pd.set_option("display.width", 250)
    print({k: v for k, v in paths.items() if k != "judge"})
    j = paths["judge"]
    print(output.decision_counts(j).to_string())
    print(j[["decision", "decision_reason", "name", "jo_E", "jo_cons", "gap_E_vs_cons_pct", "flag_shock",
             "flag_E_spread"]].to_string(index=False))


if __name__ == "__main__":
    main()
