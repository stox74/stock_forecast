# -*- coding: utf-8 -*-
"""기준일별 nowcast 실행 -> fd_nowcast (2a 명세서 v3 4절).

기준일: 분기말(qend), +15일(d15), +30일(d30), +45일(d45), 첫 공시일 5거래일 전(pre5).
  첫 공시일 이후(당일 포함) 기준일은 건너뛴다. 두 기준일이 같은 날이면 한 행으로 두고 checkpoint 에
  'qend+pre5' 처럼 이름을 함께 적는다 (PK 에 checkpoint 가 없음).
예측 대상: 첫 공시가 있는 분기. 실제치는 첫 공시 값(잠정, 없으면 확정)과 확정값을 함께 저장한다.
제외: 4분기 확인 사례(+다음 해 4분기), 기업 사건 분기 (config.CORPORATE_EVENT_EXCLUDE, v4). 코로나 기간(2020Q1~2022Q2)을 추정과
  예측에서 뺀 변형은 model_id 에 ':excovid' 를 붙인다 (벤치마크도 같은 변형을 만든다).
결과 확인 후 추가한 변형(v4 5절): 드라이버 spec.alt 가 있으면 model_id 가 '<주 모델>:<alt>' 이고,
  그 변형으로 주 드라이버를 바꾼 M2 를 'M2:<alt>' 로 만든다 (예: M1:854232:krw, M2:krw). 주 M2 에는 넣지 않는다.
M4 (v5): 드라이버마다 'M4:<드라이버>' (보조 항공 포함, :krw 변형 제외), 드라이버 2개 이상이면 보조를 뺀
  단순 평균 'M4:combo'. 구성 예측이 하나도 없으면 M4:combo 행을 만들지 않는다 (B3 대체 없음).
대조군(동원산업, 한국화장품제조)은 features.DRIVER_SPECS 가 비어 있어 벤치마크만 만든다.

fd_nowcast 컬럼: 명세서 4절 표 + rev_base(전년 동기 매출, t 시점 값), rev_actual_first, rev_actual_final,
  first_dt(첫 공시일). 쓰기는 db.execute_fd_ddl, db.write_fd 로만 한다.
"""
import logging
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import asof, db, entity
from ..events import fiscal_quarter_label
from . import benchmarks as bm
from . import features as ft
from . import models as md

log = logging.getLogger(__name__)

T_NOWCAST = "fd_nowcast"
CHECKPOINT_DAYS = {"qend": 0, "d15": 15, "d30": 30, "d45": 45}
PRE_DAYS = 5
VARIANTS = {"all": "", "excovid": ":excovid"}

DDL_NOWCAST = f"""
CREATE TABLE IF NOT EXISTS `{T_NOWCAST}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `fiscal_quarter` CHAR(7) NOT NULL,
  `asof_date` DATE NOT NULL,
  `model_id` VARCHAR(40) NOT NULL,
  `checkpoint` VARCHAR(10) NOT NULL,
  `g_hat` DOUBLE NULL,
  `g_se` DOUBLE NULL,
  `rev_hat` DOUBLE NULL,
  `coverage` DOUBLE NULL,
  `n_train` INT NULL,
  `drivers_used` VARCHAR(255) NULL,
  `rev_base` DOUBLE NULL,
  `rev_actual_first` DOUBLE NULL,
  `rev_actual_final` DOUBLE NULL,
  `first_dt` DATE NULL,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `fiscal_quarter`, `asof_date`, `model_id`),
  KEY `idx_checkpoint` (`checkpoint`),
  CONSTRAINT `fk_fd_nowcast_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `fd_entity_master` (`entity_id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
NOWCAST_COLUMNS = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "checkpoint", "g_hat", "g_se", "rev_hat",
                   "coverage", "n_train", "drivers_used", "rev_base", "rev_actual_first", "rev_actual_final", "first_dt"]


def _calendar() -> np.ndarray:
    if asof._TRADING_DAYS is None:
        from ..adapters import trading_calendar
        trading_calendar(register=True)
    return asof._TRADING_DAYS


def checkpoints(q: pd.Timestamp, first_dt: pd.Timestamp, cal: np.ndarray) -> List[Tuple[str, pd.Timestamp]]:
    """(이름, 날짜) 목록. 첫 공시일 이후(당일 포함)는 뺀다. 같은 날짜는 이름을 '+' 로 합친다."""
    out = {name: q + pd.Timedelta(days=d) for name, d in CHECKPOINT_DAYS.items()}
    pos = int(np.searchsorted(cal, np.datetime64(first_dt), side="left")) - PRE_DAYS
    if pos >= 0:
        out["pre5"] = pd.Timestamp(cal[pos])
    merged: Dict[pd.Timestamp, List[str]] = {}
    for name, d in out.items():
        if d < first_dt:
            merged.setdefault(d, []).append(name)
    return [("+".join(names), d) for d, names in sorted(merged.items())]


def m4_id(spec: ft.DriverSpec) -> str:
    """M1:854232 -> M4:854232, M3:sk_hynix -> M4:sk_hynix."""
    return "M4:" + spec.model_id.split(":", 1)[1]


def _driver_label(spec: ft.DriverSpec, f: Dict) -> str:
    pos = "" if f.get("positions") is None else "[m" + ",".join(str(p + 1) for p in f["positions"]) + "]"
    lag = f"(q-{spec.lag})" if spec.lag else ""
    return f"{spec.driver_key}{lag}{pos}"


def nowcast_entity(entity_id: str, variants: Iterable[str] = ("all", "excovid"), audit: bool = False,
                   quarters: Optional[Iterable[pd.Timestamp]] = None,
                   live: Optional[Dict[pd.Timestamp, pd.Timestamp]] = None) -> Dict:
    """한 기업의 기준일별 nowcast.

    live = {분기말 q: 기준일 t}: 아직 실적이 없는 분기를 기준일 t 하나(checkpoint 'live')로 예측한다
    (실전 사전 기록용). 계산은 2a 와 같고, 실제치·첫 공시일은 비워 둔다. 기업 사건 제외 분기는 건너뛴다.

    반환: {"rows": DataFrame (NOWCAST_COLUMNS), "counts": Counter (건너뛴 이유별), "audit": list,
           "skips": 건너뛴 내역 DataFrame (q, t, checkpoint, model_id, reason)}
    audit 항목: 기준일 t 의 입력 최대 available_at, 학습 분기, 월 위치 (테스트용)
    """
    info = entity.entity_info([entity_id])
    fye = int(info["fiscal_year_end"].iloc[0])
    cal = _calendar()
    rev_v = ft.revenue_frame(entity_id)
    act = ft.actuals(rev_v)
    excl = ft.excluded_quarters(entity_id, rev_v)
    specs = ft.DRIVER_SPECS.get(entity_id, [])
    drv = {s.model_id: ft.prepare_driver(s.loader(), fye) for s in specs}
    n_main = sum(1 for s in specs if not s.aux and s.alt is None)
    alts = sorted({s.alt for s in specs if s.alt})
    counts, rows, aud, skips = Counter(), [], [], []
    live = {pd.Timestamp(k): pd.Timestamp(v) for k, v in (live or {}).items()}
    targets = sorted(act.index) if quarters is None else sorted(pd.Timestamp(q) for q in quarters)
    targets = sorted(set(targets) | set(live))

    for q in targets:
        is_live = q in live
        if is_live and (q in act.index or ft.corporate_event_quarters(entity_id, [q])):
            counts["live_skipped"] += 1
            skips.append({"q": q, "t": live[q], "checkpoint": "live", "model_id": "*",
                          "reason": "live_has_actual" if q in act.index else "target_excluded"})
            continue
        if not is_live and q not in act.index:
            continue
        if q in excl:
            counts["target_excluded"] += 1
            skips.append({"q": q, "t": None, "checkpoint": None, "model_id": "*", "reason": "target_excluded"})
            continue
        a = {"rev_actual_first": np.nan, "rev_actual_final": np.nan} if is_live else act.loc[q]
        first_dt = pd.NaT if is_live else pd.Timestamp(a["first_dt"])
        fq = fiscal_quarter_label(q, fye)
        cps = [("live", live[q])] if is_live else checkpoints(q, first_dt, cal)
        for cp, t in cps:
            R = ft.revenue_known(rev_v, t)
            base = R.get(ft.quarter_shift(q, 4))
            if base is None or not base > 0:
                counts["no_base"] += 1
                skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": "*", "reason": "no_base"})
                continue
            g_all = ft.growth(R)
            g_all = g_all[(g_all.index < q) & ~g_all.index.isin(list(excl))]
            rev_seen = ft.known_at(rev_v, t)

            # 드라이버 특징값은 변형과 무관하게 한 번만 계산
            feats = {}
            for s in specs:
                kn = ft.known_at(drv[s.model_id], t)          # 같은 t 에서 한 번만 거른다
                f = ft.target_feature(s, drv[s.model_id], q, t, known=kn)
                xtr = (ft.training_features(s, drv[s.model_id], list(g_all.index), t, f["positions"], known=kn)
                       if f["x"] is not None else pd.Series(dtype="float64"))
                feats[s.model_id] = (f, xtr, kn)

            for variant in variants:
                suffix = VARIANTS[variant]
                if variant == "excovid" and ft.is_covid(q):
                    counts["target_covid"] += 1
                    skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": "*" + suffix, "reason": "target_covid"})
                    continue
                g_hist = g_all if variant == "all" else g_all[[not ft.is_covid(j) for j in g_all.index]]
                common = {"entity_id": entity_id, "fiscal_quarter": fq, "asof_date": t.date(), "checkpoint": cp,
                          "rev_base": float(base), "rev_actual_first": float(a["rev_actual_first"]),
                          "rev_actual_final": float(a["rev_actual_final"]) if pd.notna(a["rev_actual_final"]) else None,
                          "first_dt": None if pd.isna(first_dt) else first_dt.date()}
                bres = bm.benchmarks(g_hist)
                for mid, r in bres.items():
                    rows.append({**common, "model_id": mid + suffix, "g_hat": r["g_hat"], "g_se": r["g_se"],
                                 "rev_hat": float(base * np.exp(r["g_hat"])), "coverage": None,
                                 "n_train": r["n_train"], "drivers_used": None})
                    if audit:
                        aud.append({"entity_id": entity_id, "q": q, "t": t, "model_id": mid + suffix,
                                    "train_quarters": r["train_quarters"],
                                    "rev_max_available": rev_seen["available_at"].max(), "rev_base": float(base)})
                preds, alt_preds = [], {}
                for s in specs:
                    f, xtr, kn = feats[s.model_id]
                    if f["x"] is None:
                        counts[f"{s.model_id}:no_x(cov={f['coverage']:.2f})"] += 1
                        skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": s.model_id + suffix, "reason": "no_x", "coverage": f["coverage"]})
                        continue
                    res = md.ols_predict(xtr, g_hist, f["x"])
                    if "skipped" in res:
                        counts[f"{s.model_id}:{res['skipped']}"] += 1
                        skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": s.model_id + suffix, "reason": res["skipped"], "n_train": res["n_train"]})
                        continue
                    label = _driver_label(s, f)
                    rows.append({**common, "model_id": s.model_id + suffix, "g_hat": res["g_hat"], "g_se": res["g_se"],
                                 "rev_hat": float(base * np.exp(res["g_hat"])), "coverage": f["coverage"],
                                 "n_train": res["n_train"], "drivers_used": label[:255]})
                    if s.alt:
                        alt_preds.setdefault(s.alt, {})[s.replaces] = {**res, "coverage": f["coverage"], "label": label}
                    elif not s.aux:
                        preds.append({**res, "coverage": f["coverage"], "label": label, "model_id": s.model_id})
                    if audit:
                        aud.append({"entity_id": entity_id, "q": q, "t": t, "model_id": s.model_id + suffix,
                                    "train_quarters": res["train_quarters"],
                                    "input_max_available": f["inputs"]["available_at"].max() if len(f["inputs"]) else None,
                                    "train_input_max_available": kn["available_at"].max(),
                                    "rev_max_available": rev_seen["available_at"].max(),
                                    "positions_target": f["positions"], "positions_train": f["positions"],
                                    "x_train": xtr.reindex(res["train_quarters"])})
                if n_main >= 2:
                    m2_sets = [("M2", preds)]
                    for alt in alts:
                        # 주 드라이버 중 변형이 있는 것만 바꾼다. 변형이 이 기준일에 없으면 그 드라이버는 빠진다.
                        ap = [alt_preds.get(alt, {}).get(p["model_id"]) if p["model_id"] in
                              {s.replaces for s in specs if s.alt == alt} else p for p in preds]
                        m2_sets.append((f"M2:{alt}", [p for p in ap if p is not None]))
                    for m2_id, pr in m2_sets:
                        comb = md.combine(pr)
                        if comb is not None:
                            rows.append({**common, "model_id": m2_id + suffix, "g_hat": comb["g_hat"], "g_se": comb["g_se"],
                                         "rev_hat": float(base * np.exp(comb["g_hat"])),
                                         "coverage": float(np.mean([p["coverage"] for p in pr])),
                                         "n_train": comb["n_train"],
                                         "drivers_used": ";".join(p["label"] for p in pr)[:255]})
                        elif "B3" in bres:
                            counts[f"{m2_id}:fallback_B3"] += 1
                            skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": m2_id + suffix, "reason": "fallback_B3"})
                            r = bres["B3"]
                            rows.append({**common, "model_id": m2_id + suffix, "g_hat": r["g_hat"], "g_se": r["g_se"],
                                         "rev_hat": float(base * np.exp(r["g_hat"])), "coverage": 0.0,
                                         "n_train": r["n_train"], "drivers_used": "fallback:B3"})
                # M4 (v5): B2 출발점 + 드라이버 보정. 변형(alt) 드라이버는 만들지 않는다.
                m4_preds = []
                for s in specs:
                    if s.alt:
                        continue
                    f, xtr, kn = feats[s.model_id]
                    mid = m4_id(s) + suffix
                    res = md.m4_predict(g_hist, xtr, f["x"])
                    if "skipped" in res:
                        counts[f"{m4_id(s)}:{res['skipped']}"] += 1
                        skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": mid, "reason": res["skipped"],
                                      "n_train": res["n_train"]})
                        continue
                    label = _driver_label(s, f) + f";lag={fiscal_quarter_label(res['lag_q'], fye)}"
                    rows.append({**common, "model_id": mid, "g_hat": res["g_hat"], "g_se": res["g_se"],
                                 "rev_hat": float(base * np.exp(res["g_hat"])), "coverage": f["coverage"],
                                 "n_train": res["n_train"], "drivers_used": label[:255]})
                    if not s.aux:
                        m4_preds.append({**res, "coverage": f["coverage"], "label": label})
                    if audit:
                        lag_dq = ft.quarter_shift(res["lag_q"], s.lag)
                        lag_inputs = kn[kn["qend"].isin([lag_dq, ft.quarter_shift(lag_dq, 4)])]
                        if s.kind == "M":
                            lag_inputs = lag_inputs[lag_inputs["pos"].isin(f["positions"])]
                        aud.append({"entity_id": entity_id, "q": q, "t": t, "model_id": mid,
                                    "train_quarters": res["train_quarters"], "b": res["b"],
                                    "n_pairs": res["n_train"], "lag_q": res["lag_q"], "g_lag": res["g_lag"],
                                    "x_lag": res["x_lag"], "x_q": f["x"], "lag_driver_quarter": lag_dq,
                                    "b2_quarter": bres["B2"]["train_quarters"][0] if "B2" in bres else None,
                                    "b2_g_hat": bres["B2"]["g_hat"] if "B2" in bres else None,
                                    "input_max_available": max([x for x in (
                                        f["inputs"]["available_at"].max() if len(f["inputs"]) else None,
                                        lag_inputs["available_at"].max() if len(lag_inputs) else None) if x is not None],
                                        default=None),
                                    "train_input_max_available": kn["available_at"].max(),
                                    "rev_max_available": rev_seen["available_at"].max(),
                                    "positions_target": f["positions"], "positions_train": f["positions"]})
                if n_main >= 2:
                    comb = md.combine(m4_preds)
                    if comb is not None:
                        rows.append({**common, "model_id": "M4:combo" + suffix, "g_hat": comb["g_hat"],
                                     "g_se": comb["g_se"], "rev_hat": float(base * np.exp(comb["g_hat"])),
                                     "coverage": float(np.mean([p["coverage"] for p in m4_preds])),
                                     "n_train": comb["n_train"],
                                     "drivers_used": ";".join(p["label"].split(";lag=")[0] for p in m4_preds)[:255]})
                    else:
                        counts["M4:combo:none"] += 1
                        skips.append({"q": q, "t": t, "checkpoint": cp, "model_id": "M4:combo" + suffix,
                                      "reason": "no_component"})
    df = pd.DataFrame(rows, columns=NOWCAST_COLUMNS)
    return {"rows": df, "counts": counts, "audit": aud, "skips": pd.DataFrame(skips)}


def create_table() -> None:
    db.execute_fd_ddl(DDL_NOWCAST)


def run_and_load(entity_ids: Optional[Iterable[str]] = None) -> Dict:
    ids = list(entity_ids or entity.PILOTS)
    results = {e: nowcast_entity(e) for e in ids}
    allrows = pd.concat([r["rows"] for r in results.values()], ignore_index=True)
    create_table()
    n = db.write_fd(allrows, T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    return {"rows": allrows, "results": results, "written": n}


def widen_model_id() -> None:
    """model_id 를 VARCHAR(40) 으로 (v4 변형 이름 'M1:854232:krw:excovid' 등). 이미 넓으면 그대로."""
    db.execute_fd_ddl(f"ALTER TABLE `{T_NOWCAST}` MODIFY `model_id` VARCHAR(40) NOT NULL")


def regenerate(entity_ids: Iterable[str]) -> Dict:
    """기업 단위 재생성: 먼저 전부 계산한 뒤(실패하면 아무것도 지우지 않음) 그 기업 행을 지우고 다시 쓴다.
    제외 규칙이 바뀌어 더 이상 만들지 않는 행(예: 기업 사건 분기)이 남지 않게 하려는 것."""
    ids = list(entity_ids)
    results = {e: nowcast_entity(e) for e in ids}
    allrows = pd.concat([r["rows"] for r in results.values()], ignore_index=True)
    create_table()
    widen_model_id()
    deleted = db.delete_fd(T_NOWCAST, {"entity_id": ids})
    n = db.write_fd(allrows, T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    return {"rows": allrows, "results": results, "deleted": deleted, "written": n}


def add_m4(entity_ids: Iterable[str]) -> Dict:
    """M4 행만 fd_nowcast 에 upsert (기존 B/M1~M3 행은 쓰지 않는다)."""
    ids = list(entity_ids)
    results = {e: nowcast_entity(e) for e in ids}
    allrows = pd.concat([r["rows"] for r in results.values()], ignore_index=True)
    m4 = allrows[allrows["model_id"].str.startswith("M4:")].reset_index(drop=True)
    n = db.write_fd(m4, T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])
    return {"rows": m4, "all_rows": allrows, "results": results, "written": n}
