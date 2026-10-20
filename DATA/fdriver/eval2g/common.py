# -*- coding: utf-8 -*-
"""2g 공통 (method_2g_v1 1~2절). 쓰기 대상 표 이름과 읽기 함수, 매출 보정 확장, 2g 전용 판관비 항등식 수정.

2d 모듈(k2_targets, k3_oprule)은 import 만 한다. k2_targets.adjust/read_sources 는 모듈 상수 END 를 쓰므로
build_adj() 안에서만 잠시 바꾸고 되돌린다 (2d 표에는 쓰지 않음).
"""
from contextlib import contextmanager
from typing import List

import numpy as np
import pandas as pd

from .. import db
from ..eval2d import k2_targets as K2
from ..eval2d import k3_oprule as K3

METHOD_COMMIT = "5dc9fdbe147236172bf6f528675817e076b4131c"
T_TARGET = "fd_target_2g"
T_ADJ = "fd_rev_adj_2g"
T_RULE = "fd_op_rule_2g"
T_LIVE = "fd_live_2g"
SGA_TOL = 0.02


def last_completed_quarter(t: pd.Timestamp) -> pd.Timestamp:
    p = pd.Timestamp(t).to_period("Q") - 1
    return p.to_timestamp(how="end").normalize()


@contextmanager
def _k2_end(end: pd.Timestamp):
    old = K2.END
    K2.END = pd.Timestamp(end)
    try:
        yield
    finally:
        K2.END = old


def build_adj(ids: List[str], end: pd.Timestamp) -> dict:
    """2d K2 adjust() 와 같은 규칙, 기간만 2009Q1~end."""
    with _k2_end(end):
        src = K2.read_sources(ids)
        return K2.adjust(src, ids)


def read_targets() -> pd.DataFrame:
    return db.read_sql(f"SELECT * FROM `{T_TARGET}`")


def read_adj(ids=None) -> pd.DataFrame:
    if ids is None:
        d = db.read_sql(f"SELECT entity_id, period_end, revenue_adj, basis, source, adj_reason FROM `{T_ADJ}`")
    else:
        f, p = db.in_clause("e", list(ids))
        d = db.read_sql(f"SELECT entity_id, period_end, revenue_adj, basis, source, adj_reason FROM `{T_ADJ}` "
                        f"WHERE entity_id IN {f}", p)
    d["period_end"] = pd.to_datetime(d["period_end"])
    return d


def items_2g(ids: List[str], adj: pd.DataFrame) -> (pd.DataFrame, pd.DataFrame):
    """2d K3.items (기간 제한 없음) + 2g 전용 판관비 항등식 수정 (DART 기준 기업만).
    반환: (긴 표 entity_id, period_end, item, value, src, sga_fixed), 수정 기록."""
    it = K3.items(ids, adj, quarters=None).copy()
    it["sga_fixed"] = 0
    basis = adj.drop_duplicates("entity_id").set_index("entity_id")["basis"]
    dart_ids = [e for e in ids if basis.get(e) == "DART"]
    rev = adj.set_index(["entity_id", "period_end"])["revenue_adj"]
    w = it[it["entity_id"].isin(dart_ids)].pivot_table(index=["entity_id", "period_end"], columns="item",
                                                       values="value")
    for c in ("op", "cogs", "sga"):
        if c not in w:
            w[c] = np.nan
    w["rev"] = rev.reindex(w.index)
    w["resid"] = w["rev"] - w["cogs"] - w["op"]
    ok = w["resid"].notna() & (w["resid"] != 0)
    bad = ok & w["sga"].notna() & ((w["sga"] - w["resid"]).abs() > SGA_TOL * w["resid"].abs())
    fill = ok & w["sga"].isna()
    log = pd.concat([w[bad].assign(sga_fixed=1), w[fill].assign(sga_fixed=2)]).reset_index()
    log = log[["entity_id", "period_end", "rev", "cogs", "op", "sga", "resid", "sga_fixed"]]
    if len(log):
        key = set(zip(log["entity_id"], log["period_end"]))
        drop = it["item"].eq("sga") & [k in key for k in zip(it["entity_id"], it["period_end"])]
        it = it[~drop]
        add = pd.DataFrame({"entity_id": log["entity_id"], "period_end": log["period_end"], "item": "sga",
                            "value": log["resid"], "src": "identity", "sga_fixed": log["sga_fixed"]})
        it = pd.concat([it, add], ignore_index=True)
    return it, log


def safe_load_events(ids: List[str], start: str, chunk: int = 50) -> list:
    """events.build_and_load 를 기업 묶음으로 나눠 부른다. 묶음이 실패하면 기업 하나씩 다시 부르고, 실패한 기업만 건너뛴다.
    (K8b: 정정공시 원문의 잘못된 날짜 한 건이 전체 적재를 멈춘 일이 있어 2g 에서만 감싼다. events.py 는 바꾸지 않음.)
    반환: 건너뛴 (기업, 오류) 목록."""
    from .. import events
    skipped = []
    for i in range(0, len(ids), chunk):
        ch = list(ids[i:i + chunk])
        try:
            events.build_and_load(ch, start=start)
        except Exception:      # noqa: BLE001
            for e in ch:
                try:
                    events.build_and_load([e], start=start)
                except Exception as ex:  # noqa: BLE001
                    skipped.append((e, repr(ex)[:120]))
    return skipped
