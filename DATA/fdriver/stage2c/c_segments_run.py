# -*- coding: utf-8 -*-
"""2c 작업 2 (c) 실행: 연도별 사업부 비중 추출, 대응표, 2025 엑셀 비교, 성공 연도 수 (DB 쓰기 없음).

실행: python -m DATA.fdriver.stage2c.c_segments_run
산출 (2c/step2/c_segments): shares_long.csv, shares_filled.csv, rows_mapping.csv, name_map.csv,
  compare_2025.csv, success_years.csv
"""
import json

import numpy as np
import pandas as pd

from ..config import output_path
from . import segments as sg
from .mapping import read_mapping

OUT = output_path("2c", "step2", "c_segments")


def main():
    m = read_mapping()
    w = m["weights"]
    ids = list(dict.fromkeys(w["entity_id"]))
    names = dict(zip(m["companies"]["entity_id"], m["companies"]["name"]))
    shares, rows = sg.extract_all(ids)
    filled = sg.fill_years(shares)
    shares.to_csv(OUT / "shares_long.csv", index=False, encoding="utf-8-sig")
    rows.to_csv(OUT / "rows_mapping.csv", index=False, encoding="utf-8-sig")
    f2 = filled.copy()
    f2["shares"] = f2["shares"].map(lambda d: json.dumps({k: round(v, 4) for k, v in d.items()}, ensure_ascii=False))
    f2.to_csv(OUT / "shares_filled.csv", index=False, encoding="utf-8-sig")

    # 공시 부문 -> 엑셀 세그먼트 대응표 (연도 범위)
    r = rows[rows["segment"].notna()].copy()
    r["segment"] = r["segment"].replace("", "(엑셀에 없음: 기타)")
    nm = (r.groupby(["entity_id", "disclosed", "segment"])
          .agg(years=("fiscal_year", lambda s: f"{s.min()}~{s.max()} ({s.nunique()}개 연도)"),
               note=("note", lambda s: "; ".join(sorted({x for x in s if x and x not in ("부문 칸 이어받음",)}))))
          .reset_index())
    nm.insert(1, "name", nm["entity_id"].map(names))
    nm.to_csv(OUT / "name_map.csv", index=False, encoding="utf-8-sig")

    # 2025 비교
    comp = []
    for e in ids:
        we = w[w["entity_id"] == e]
        x = shares[(shares["entity_id"] == e) & (shares["fiscal_year"] == 2025)]
        ext = dict(zip(x["segment"], x["share"]))
        for s in we.itertuples():
            v = ext.get(s.segment, np.nan if x["status"].iloc[0] != "ok" else 0.0) if len(x) else np.nan
            comp.append({"entity_id": e, "name": names.get(e), "segment": s.segment, "excel": s.weight,
                         "extracted_2025": v, "diff_pp": (v - s.weight) * 100 if pd.notna(s.weight) and pd.notna(v) else np.nan,
                         "has_driver": s.has_driver})
    comp = pd.DataFrame(comp)
    comp.to_csv(OUT / "compare_2025.csv", index=False, encoding="utf-8-sig")

    succ = (filled.assign(ok=filled["status"].eq("ok"))
            .groupby("entity_id").agg(years=("fiscal_year", "size"), ok_years=("ok", "sum"),
                                      first=("fiscal_year", "min"), last=("fiscal_year", "max"),
                                      failed=("fiscal_year", lambda s: ",".join(str(y) for y, st in
                                                                                zip(s, filled.loc[s.index, "status"]) if st != "ok")),
                                      filled=("filled_from", lambda s: int(s.notna().sum()))).reset_index())
    succ.insert(1, "name", succ["entity_id"].map(names))
    succ.to_csv(OUT / "success_years.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    print(comp.round(3).to_string(index=False))
    print(succ.to_string(index=False))
    print(nm.to_string(index=False))
    return shares, filled, rows


if __name__ == "__main__":
    main()
