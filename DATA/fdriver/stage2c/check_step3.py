# -*- coding: utf-8 -*-
"""호영님 확인용: 2c 작업 3 판정 결과와 fd_signal 기록 확인 (읽기만).

실행 (저장소 루트 investment_strategy 에서):
    python -m DATA.fdriver.stage2c.check_step3
1. 판정표 (judgement.csv, 고치기 전) 와 월간 공시 버그를 고친 C3 (c3_fixed.csv)
2. 실행 정보 (커밋, 계획 파일 SHA-256) 와 계획 파일의 현재 SHA-256 이 같은지
3. fd_signal 의 2c 33곳 행 수
"""
import hashlib
import json

import pandas as pd

from .. import db
from ..config import output_path


def main():
    pd.set_option("display.width", 200)
    base = output_path("2c", "step3")
    j = pd.read_csv(base / "judgement" / "judgement.csv")
    print("== 1. 판정표 (고치기 전)")
    print(j[["test", "estimate", "p_one", "N", "success"]].round(4).to_string(index=False))
    f = pd.read_csv(base / "fix_eventdate" / "c3_fixed.csv")
    print("   C3 (월간 공시 버그 수정 후)")
    print(f[["test", "estimate", "se", "p_one", "N", "success"]].round(4).to_string(index=False))
    meta = json.loads((base / "judgement" / "run_meta.json").read_text(encoding="utf-8"))
    now = hashlib.sha256((base / "plan" / "analysis_plan.csv").read_bytes()).hexdigest()
    print("\n== 2. 실행 커밋", meta["git_head"], "| 계획 SHA-256 실행 시", meta["analysis_plan_sha256"][:16],
          "현재", now[:16], "같음" if now == meta["analysis_plan_sha256"] else "다름")
    plan = pd.read_csv(base / "plan" / "main_model_by_entity.csv", dtype={"entity_id": str})
    fr, p = db.in_clause("e", list(plan["entity_id"]))
    n = db.read_sql(f"SELECT COUNT(*) n, COUNT(DISTINCT entity_id) k FROM fd_signal WHERE entity_id IN {fr}", p)
    print("\n== 3. fd_signal 2c 행", int(n["n"].iloc[0]), "기업", int(n["k"].iloc[0]))


if __name__ == "__main__":
    main()
