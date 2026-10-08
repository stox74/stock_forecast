# -*- coding: utf-8 -*-
"""2c 작업 2 (f): 선정 33개 기업 nowcast 생성 -> fd_nowcast (2a nowcast_entity 그대로, 드라이버는 registry 등록).

먼저 전부 계산하고(실패하면 아무것도 쓰지 않음), 그 기업들의 기존 행을 지운 뒤 다시 쓴다 (run.regenerate 와 같은 방식).
예측 오차는 계산·요약하지 않는다. 보고: 기업 x 모델 생성 건수, 건너뛴 이유별 건수, 업종 쏠림, 평가 가능 분기 수.
평가 가능 분기 = pre5 기준일에 주 모델(드라이버 2개 이상 M4:combo, 1개 M4:<드라이버>) 행이 있는 분기 (전체 기간 변형).
실행: python -m DATA.fdriver.stage2c.f_nowcast_run
"""
import pandas as pd

from .. import db, entity
from ..config import output_path
from ..nowcast import features as ft
from ..nowcast import run
from . import registry

OUT = output_path("2c", "step2", "f_nowcast")
MIN_EVAL = 8


def main():
    b = registry.register()
    m = b["mapping"]
    comp = m["companies"][m["companies"]["included"]]
    ids = [e for e in comp["entity_id"] if e not in entity.PILOTS]
    results = {}
    for e in ids:
        results[e] = run.nowcast_entity(e)
        print(e, len(results[e]["rows"]), flush=True)
    allrows = pd.concat([r["rows"] for r in results.values()], ignore_index=True)
    run.create_table()
    run.widen_model_id()
    deleted = db.delete_fd(run.T_NOWCAST, {"entity_id": ids})
    n = db.write_fd(allrows, run.T_NOWCAST, ["entity_id", "fiscal_quarter", "asof_date", "model_id"])

    names = dict(zip(comp["entity_id"], comp["name"]))
    sectors = dict(zip(comp["entity_id"], comp["sector"]))
    a = allrows.copy()
    a["variant"] = a["model_id"].str.endswith(":excovid").map({True: "excovid", False: "all"})
    a["model"] = a["model_id"].str.replace(":excovid", "", regex=False)
    cnt = a.groupby(["entity_id", "variant", "model"]).size().unstack("model", fill_value=0)
    cnt.to_csv(OUT / "rows_by_entity_model.csv", encoding="utf-8-sig")
    skips = pd.concat([r["skips"].assign(entity_id=e) for e, r in results.items() if len(r["skips"])], ignore_index=True)
    skips.to_csv(OUT / "skips.csv", index=False, encoding="utf-8-sig")
    skip_sum = skips.groupby(["entity_id", "reason"]).size().unstack("reason", fill_value=0)
    skip_sum.to_csv(OUT / "skips_by_reason.csv", encoding="utf-8-sig")

    summ = []
    for e in ids:
        g = a[(a["entity_id"] == e) & (a["variant"] == "all")]
        comps = [s.model_id for s in ft.DRIVER_SPECS.get(e, []) if not s.aux and s.alt is None]
        main_model = ("M4:combo" if len(comps) >= 2 else ("M4:" + comps[0].split(":", 1)[1] if comps else "B2"))
        pre5 = g[g["checkpoint"].str.contains("pre5")]
        target_q = sorted(a.loc[a["entity_id"] == e, "fiscal_quarter"].unique())
        mq = sorted(pre5.loc[pre5["model"] == main_model, "fiscal_quarter"].unique())
        b2q = sorted(pre5.loc[pre5["model"] == "B2", "fiscal_quarter"].unique())
        excl = ft.CORPORATE_EVENT_EXCLUDE.get(e, [])
        summ.append({"entity_id": e, "name": names[e], "sector": sectors[e], "main_model": main_model,
                     "n_rows": int((a["entity_id"] == e).sum()), "target_quarters": len(target_q),
                     "first_q": target_q[0] if target_q else None, "last_q": target_q[-1] if target_q else None,
                     "pre5_B2_quarters": len(b2q), "pre5_main_quarters": len(mq),
                     "main_first_q": mq[0] if mq else None, "main_last_q": mq[-1] if mq else None,
                     "corp_event_ranges": "; ".join(f"{x}~{y}" for x, y, _ in excl),
                     "flag_lt8": len(mq) < MIN_EVAL})
    summ = pd.DataFrame(summ)
    summ.to_csv(OUT / "entity_summary.csv", index=False, encoding="utf-8-sig")
    sec = comp[~comp["entity_id"].isin(entity.PILOTS)].groupby("sector").size().sort_values(ascending=False)
    sec_share = (sec / sec.sum()).rename("share")
    pd.concat([sec.rename("n"), sec_share], axis=1).to_csv(OUT / "sector_share.csv", encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    print(f"deleted {deleted}, written {n}, rows {len(allrows)}")
    print(summ.to_string(index=False))
    print(skip_sum.to_string())
    print(pd.concat([sec.rename("n"), sec_share.round(3)], axis=1).to_string())


if __name__ == "__main__":
    main()
