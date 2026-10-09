# -*- coding: utf-8 -*-
"""2c 작업 3 2절: C1~C3 판정 (2c 33곳, 한 번만 실행). fd_signal 에 33곳 신호 기록 (같은 PK 는 덮어쓰지 않음).

한 번만: 결과 폴더에 judgement.csv 가 있으면 멈춘다. 실행 전 DATA/fdriver 에 커밋 안 된 변경이 있으면 멈춘다
(실행 코드 = 커밋된 코드). 실행 정보(커밋 해시, 계획 파일 SHA-256, 시각)를 run_meta.json 에 남긴다.
실행: python -m DATA.fdriver.stage2c.s3_c_run
산출 (2c/step3/judgement): judgement.csv, c1_by_entity.csv, c1_pairs.csv, c2_signals_main.csv, c3_events.csv, run_meta.json
"""
import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

from .. import db, entity
from ..config import output_path
from ..evaluate import event_study as es
from ..signals import sur
from . import registry
from . import s3_lib as L

OUT = output_path("2c", "step3", "judgement")
PLAN = output_path("2c", "step3", "plan") / "analysis_plan.csv"
REPO = Path(__file__).resolve().parents[3]
SIGNAL_KEY = ["entity_id", "fiscal_quarter", "asof_date", "model_id", "variant"]


def git_head(require_clean: bool = True) -> str:
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if require_clean:
        st = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "DATA/fdriver"],
                            capture_output=True, text=True).stdout
        dirty = [l for l in st.splitlines() if "__pycache__" not in l]
        if dirty:
            raise RuntimeError(f"DATA/fdriver 에 커밋 안 된 변경이 있어 판정을 실행하지 않음: {dirty}")
    return head


def main():
    if (OUT / "judgement.csv").exists():
        raise RuntimeError("판정은 이미 한 번 실행됨 (judgement.csv 있음). 다시 돌리지 않는다.")
    head = git_head()
    plan_sha = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    started = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    b = registry.register(refresh=True, rules="2b2")
    comp = b["mapping"]["companies"]
    ids = [e for e in comp.loc[comp["included"], "entity_id"] if e not in entity.PILOTS]
    main_m = {e: L.main_model_of(e) for e in ids}

    # C1
    d = L.eval_rows(ids, main_m)
    pe = L.paired_errors(d)
    ent = L.c1_by_entity(pe, main_m)
    r1a, r1b = L.c1a(ent), L.c1b(pe)

    # C2, C3 (신호는 먼저 계산만 하고, 판정 계산이 모두 끝난 뒤 fd_signal 에 기록)
    sig_all = L.signal_rows(ids)
    sig = L.pick_main_signals(sig_all, main_m)
    r2 = L.c2(sig)
    xr = es.excess_returns(ids)
    ev = L.events_with_car(sig, xr)
    r3 = L.c3(ev)

    sur.create_table()
    rows = sig_all.copy()
    rows["asof_date"] = pd.to_datetime(rows["asof_date"]).dt.date
    written = db.insert_fd_strict(rows, sur.T_SIGNAL, SIGNAL_KEY)

    jt = L.verdict([r1a, r1b, r2, {k: v for k, v in r3.items() if k != "_fit"}])
    c1 = {"test": "C1 (a 와 b 모두)", "success": bool(jt.loc[jt["test"] == "C1(a)", "success"].iloc[0]
                                                and jt.loc[jt["test"] == "C1(b)", "success"].iloc[0])}
    jt = pd.concat([jt, pd.DataFrame([c1])], ignore_index=True)
    jt.to_csv(OUT / "judgement.csv", index=False, encoding="utf-8-sig")
    ent.to_csv(OUT / "c1_by_entity.csv", index=False, encoding="utf-8-sig")
    pe.to_csv(OUT / "c1_pairs.csv", index=False, encoding="utf-8-sig")
    sig.to_csv(OUT / "c2_signals_main.csv", index=False, encoding="utf-8-sig")
    ev.to_csv(OUT / "c3_events.csv", index=False, encoding="utf-8-sig")
    meta = {"git_head": head, "analysis_plan_sha256": plan_sha, "started": started,
            "finished": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"), "entities": len(ids),
            "fd_signal_rows_written": int(written), "signal_rows": len(sig_all)}
    (OUT / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(json.dumps(meta, ensure_ascii=False))
    print(jt.to_string(index=False))


if __name__ == "__main__":
    main()
