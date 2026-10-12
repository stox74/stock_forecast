# -*- coding: utf-8 -*-
"""2b K3 (1): 2c nowcast 원천(fd_nowcast) 4분기 연간 값 6건 수정.

첫 공시(잠정) 4분기 값이 연간 매출인 분기 6개 (2b K2 에서 확인): rev_actual_first 를 확정 4분기 값(rev_actual_final =
연간 - 1~3분기)으로 바꾸고, 원래 값은 새 열 rev_actual_first_raw 에 둔다. 다른 열·행은 건드리지 않는다.
- 쓰기 전 해당 행 전체를 CSV 로 백업 (같은 이름이 있으면 멈춤). 이미 고친 행(raw 가 채워짐)이 있으면 멈춤.
- 영향 보고만 (재판정 안 함): 2c C1 (주 모델 대 B2) 기업별 상대 RMSE, C1(a)(b) 를
  수정 전·후 같은 코드로 계산 (2c 판정 표본 1,369 분기). fd_signal 의 sur 와 레이더 성적표(원천에서 직접 읽음)는 바꾸지 않고 영향만 적는다.
실행: python -m DATA.fdriver.eval2b.k3_fix_q4
"""
import json

import pandas as pd

from .. import db, entity
from ..config import output_path
from ..stage2c import registry
from ..stage2c import s3_lib as L

T_NC = "fd_nowcast"
PK = ["entity_id", "fiscal_quarter", "asof_date", "model_id"]
TARGETS = [("005490", "2022Q4"), ("005490", "2023Q4"), ("005490", "2024Q4"), ("005490", "2025Q4"),
           ("004020", "2025Q4"), ("011780", "2022Q4")]


def target_rows() -> pd.DataFrame:
    w = " OR ".join(f"(entity_id = :e{i} AND fiscal_quarter = :q{i})" for i in range(len(TARGETS)))
    p = {k: v for i, (e, q) in enumerate(TARGETS) for k, v in ((f"e{i}", e), (f"q{i}", q))}
    return db.read_sql(f"SELECT * FROM `{T_NC}` WHERE {w}", p)


def fixed(nc: pd.DataFrame) -> pd.DataFrame:
    """대상 분기의 rev_actual_first 를 rev_actual_final 로 바꾼 사본 (메모리)."""
    x = nc.copy()
    m = pd.Series(list(zip(x["entity_id"], x["fiscal_quarter"])), index=x.index).isin(TARGETS)
    x.loc[m, "rev_actual_first"] = x.loc[m, "rev_actual_final"]
    return x


def c1_summary(nc: pd.DataFrame, ids, main) -> dict:
    d = L.eval_rows(ids, main, nc)
    pe = L.paired_errors(d)
    ent = L.c1_by_entity(pe, main)
    return {"ent": ent, "c1a": L.c1a(ent), "c1b": L.c1b(pe)}


def impact(ids) -> pd.DataFrame:
    frag, p = db.in_clause("e", ids)
    nc = db.read_sql(f"SELECT * FROM `{T_NC}` WHERE entity_id IN {frag}", p)
    nf = fixed(nc)
    rows = []
    for label, main in [("2c C1 주 모델/B2", {e: L.main_model_of(e) for e in ids})]:
        b, a = c1_summary(nc, ids, main), c1_summary(nf, ids, main)
        for e in sorted({e for e, _ in TARGETS}):
            eb, ea = b["ent"].set_index("entity_id").loc[e], a["ent"].set_index("entity_id").loc[e]
            rows.append({"계산": label, "대상": e, "n": int(eb["n"]), "전_rmse_main": eb["rmse_main"],
                         "전_rmse_b2": eb["rmse_b2"], "전_rel": eb["rel_rmse"], "후_rmse_main": ea["rmse_main"],
                         "후_rmse_b2": ea["rmse_b2"], "후_rel": ea["rel_rmse"]})
        for k in ("c1a", "c1b"):
            rows.append({"계산": label, "대상": k.upper().replace("C1", "C1(") + ")", "n": b[k]["N"],
                         "전_rel": b[k]["estimate"], "후_rel": a[k]["estimate"],
                         "전_rmse_main": b[k].get("p_one"), "후_rmse_main": a[k].get("p_one")})
        med_b = b["ent"]["rel_rmse"].median()
        med_a = a["ent"]["rel_rmse"].median()
        rows.append({"계산": label, "대상": "상대 RMSE 중앙값", "n": len(b["ent"]), "전_rel": med_b, "후_rel": med_a})
    return pd.DataFrame(rows)


def main():
    out = output_path("2b", "k3_fix_q4")
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    have = db.table_columns(T_NC)
    t = target_rows()
    if "rev_actual_first_raw" in have and t["rev_actual_first_raw"].notna().any():
        raise RuntimeError("이미 수정된 행이 있음 (rev_actual_first_raw 채워짐). 다시 실행하지 않는다.")
    bad = t[~(t["rev_actual_first"] > 2 * t["rev_actual_final"])]
    if len(bad):
        raise RuntimeError(f"연간 값이 아닌 행이 대상에 있음: {bad[['entity_id', 'fiscal_quarter']].drop_duplicates().values}")
    b = registry.register(rules="2b2")
    comp = b["mapping"]["companies"]
    ids = [e for e in comp.loc[comp["included"], "entity_id"] if e not in entity.PILOTS]
    imp = impact(ids)                                      # 수정 전 DB 값으로 전·후 계산
    bk = out / f"backup_fd_nowcast_q4_{stamp}.csv"
    if bk.exists():
        raise FileExistsError(bk)
    t.to_csv(bk, index=False, encoding="utf-8", float_format="%.17g")
    if "rev_actual_first_raw" not in have:
        db.execute_fd_ddl(f"ALTER TABLE `{T_NC}` ADD COLUMN `rev_actual_first_raw` DOUBLE NULL AFTER `rev_actual_first`")
    # upsert 는 INSERT 경로도 유효해야 해서 행 전체를 보낸다. 바뀌는 값은 두 열뿐이고 나머지는 읽은 값 그대로
    # (created_at, updated_at 은 DB 가 관리).
    up = t.drop(columns=["created_at", "updated_at"]).copy()
    up["rev_actual_first_raw"] = t["rev_actual_first"]
    up["rev_actual_first"] = t["rev_actual_final"]
    n = db.write_fd(up, T_NC, PK, stamp_collected=False)
    after = target_rows()
    ok = bool((after["rev_actual_first"] == after["rev_actual_final"]).all()
              and (after["rev_actual_first_raw"] == t.set_index(PK).loc[list(map(tuple, after[PK].values)), "rev_actual_first"].values).all())
    summ = after.groupby(["entity_id", "fiscal_quarter"]).agg(rows=("model_id", "size"),
                                                              raw=("rev_actual_first_raw", "first"),
                                                              new=("rev_actual_first", "first")).reset_index()
    sig = db.read_sql("SELECT entity_id, fiscal_quarter, COUNT(*) n FROM fd_signal WHERE " +
                      " OR ".join(f"(entity_id = '{e}' AND fiscal_quarter = '{q}')" for e, q in TARGETS) +
                      " GROUP BY entity_id, fiscal_quarter")
    imp.to_csv(out / f"impact_{stamp}.csv", index=False, encoding="utf-8-sig")
    summ.to_csv(out / f"fixed_{stamp}.csv", index=False, encoding="utf-8-sig")
    res = {"written_rows": int(n), "target_rows": len(t), "verified": ok, "backup": str(bk),
           "fd_signal_rows_unchanged": int(sig["n"].sum()) if len(sig) else 0}
    (out / f"summary_{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.set_option("display.width", 250)
    print(json.dumps(res, ensure_ascii=False))
    print(summ.to_string(index=False))
    print(imp.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
