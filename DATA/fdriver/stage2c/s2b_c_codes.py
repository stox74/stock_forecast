# -*- coding: utf-8 -*-
"""2c 작업 2b (c): 쓸 코드 다시 정하기 (drivers.apply_2b_rules) 와 기업별 변경 전후 표. DB 쓰기 없음.

변경 전 = 작업 2 에서 실제로 쓴 코드표 (2c/step2/d_drivers/resolved_codes.csv), 변경 후 = registry rules='2b'.
바뀐 기업 = (1) 쓸 leaf 코드 집합이 달라진 기업, 또는 (2) 쓸 leaf 코드 중 (b) 에서 수집한 코드가 있어 기간이 달라진 기업.
실행: python -m DATA.fdriver.stage2c.s2b_c_codes
산출 (2c/step2b/c_codes): code_changes.csv, expansion_ratio_2b.csv, segments_2b.csv, changed_entities.csv
"""
import pandas as pd

from ..config import output_path
from . import registry

OUT = output_path("2c", "step2b", "c_codes")


def leaf_sets(resolved: pd.DataFrame) -> dict:
    r = resolved[resolved["leaf"].notna()]
    return {(e, s): set(g["leaf"]) for (e, s), g in r.groupby(["entity_id", "segment"])}


def main():
    collected = set(open(output_path("2c", "step2b", "b_collect") / "target_codes.txt", encoding="utf-8").read().split())
    res0 = pd.read_csv(output_path("2c", "step2", "d_drivers") / "resolved_codes.csv", dtype=str)
    seg_step2 = pd.read_csv(output_path("2c", "step2", "d_drivers") / "segment_table.csv", dtype=str)
    seg_step2 = seg_step2[seg_step2["dropped"].isna()]
    seg0 = {e: sorted(g["segment"]) for e, g in seg_step2.groupby("entity_id")}
    after = registry.build(refresh=True, rules="2b")
    res1, ratios = after["resolved"], after["expansion_ratios"]
    names = dict(zip(after["mapping"]["companies"]["entity_id"], after["mapping"]["companies"]["name"]))
    ratios.to_csv(OUT / "expansion_ratio_2b.csv", index=False, encoding="utf-8-sig")
    res1.to_csv(OUT / "resolved_2b.csv", index=False, encoding="utf-8-sig")

    s0, s1 = leaf_sets(res0), leaf_sets(res1)
    rows = []
    for key in sorted(set(s0) | set(s1)):
        e, seg = key
        a, b = s0.get(key, set()), s1.get(key, set())
        for c in sorted(a | b):
            if c in a and c in b:
                kind = "유지 (재수집으로 기간 연장)" if c in collected else "유지"
            elif c in b:
                st = res1.loc[(res1["entity_id"] == e) & (res1["segment"] == seg) & (res1["leaf"] == c), "status"]
                kind = ("4자리 전환" if (st == "four_digit_switch").any() else
                        "추가 (재수집한 하위 6자리)" if c in collected else "추가")
            else:
                st = res1.loc[(res1["entity_id"] == e) & (res1["segment"] == seg) & (res1["dropped_leaf"] == c), "rule_note"]
                if len(st):
                    kind = "제외: " + st.iloc[0]
                elif len(c) == 4:
                    kind = "제외: 4자리 -> 6자리 전개로 바뀜 (하위 6자리 재수집)"
                else:
                    kind = "제외: 4자리 전환으로 대체"
            rows.append({"entity_id": e, "name": names.get(e), "segment": seg, "code": c, "change": kind})
    ch = pd.DataFrame(rows)
    ch.to_csv(OUT / "code_changes.csv", index=False, encoding="utf-8-sig")

    seg_rows, changed = [], []
    for e, b in after["built"].items():
        for seg, s in b["segments"].items():
            seg_rows.append({"entity_id": e, "name": names[e], "segment": seg, "n_leaf": len(s["codes"]),
                             "start": f"{s['start']:%Y-%m}", "end": f"{s['end']:%Y-%m}", "end_by": ",".join(s["end_by"]),
                             "zero_filled_months": s["zero_filled"]})
        for seg, why in b["dropped"].items():
            seg_rows.append({"entity_id": e, "name": names[e], "segment": seg, "dropped": why})
        g = ch[ch["entity_id"] == e]
        g = g[g["change"] != "유지"]
        code_changed = len(g) > 0
        seg_changed = sorted(b["segments"]) != seg0.get(e, [])
        if code_changed or seg_changed:
            changed.append({"entity_id": e, "name": names[e],
                            "reason": "; ".join(f"{k} {n}개" for k, n in g["change"].value_counts().items())
                            + ("; 세그먼트 구성 바뀜" if seg_changed else ""),
                            "segments_before": ",".join(seg0.get(e, [])), "segments_after": ",".join(sorted(b["segments"]))})
    pd.DataFrame(seg_rows).to_csv(OUT / "segments_2b.csv", index=False, encoding="utf-8-sig")
    cd = pd.DataFrame(changed)
    cd.to_csv(OUT / "changed_entities.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    print(ratios.round(3).to_string(index=False))
    print(ch[ch["change"] != "유지"].to_string(index=False))
    print(pd.DataFrame(seg_rows).to_string(index=False))
    print(cd.to_string(index=False))


if __name__ == "__main__":
    main()
