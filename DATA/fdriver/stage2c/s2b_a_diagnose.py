# -*- coding: utf-8 -*-
"""2c 작업 2b (a): 끊긴 코드와 전개가 부족한 4자리 코드의 원인 진단 (DB 읽기 + 관세청 API 조회, 쓰기 없음).

대상
- 끊긴 코드: 33개 기업 사용 코드(6자리 전개 후 leaf) 중 마지막 달이 2026-08 보다 이른 것. 2023-12 이전 종료(HS 개정 폐지,
  예: 852580)는 제외.
- 전개가 부족한 4자리: 6자리 합 / 4자리 비율 중앙값 < 0.9 (작업 2 expansion_check), 하위 6자리가 DB 에 없는 4자리(7211),
  4자리가 DB 에 없는 코드(7213).
분류
- A 수집기 코드 목록(hs_code_map_v2.HS_CODE_MAP)에 없음, 원천 API 는 그 뒤 달 값을 줌 -> 추가하면 수집 가능
- B 목록에 있으나 원천 API 가 그 달부터 값을 주지 않음 (오류 응답 등)
- C 원천에 실제 거래가 없음 (API 정상 응답, 그 뒤 달 수출액 0 또는 행 없음)
- D 기타
B·D 는 API 응답 원문을 api_examples/ 에 저장.
실행: python -m DATA.fdriver.stage2c.s2b_a_diagnose
"""
import pandas as pd

from ..config import output_path
from . import customs_api as api
from . import drivers as dv
from .mapping import read_mapping

OUT = output_path("2c", "step2b", "a_diagnose")
END = pd.Timestamp("2026-08-31")
SHORT_RATIO = 0.9


def collector_codes() -> set:
    from DATA.hs_code_map_v2 import HS_CODE_MAP
    return set(HS_CODE_MAP)


def cut_codes(resolved: pd.DataFrame) -> pd.DataFrame:
    r = resolved[resolved["leaf"].notna() & ~resolved["status"].eq("camera_splice_pre")]
    r = r[(r["leaf_last"] < END) & (r["leaf_last"] > dv.RETIRED_BY)]
    return (r.groupby("leaf").agg(entities=("entity_id", lambda s: ",".join(sorted(set(s)))),
                                  excel_codes=("hs", lambda s: ",".join(sorted(set(s)))),
                                  last=("leaf_last", "max"), first=("leaf_first", "min"))
            .reset_index().rename(columns={"leaf": "code"}))


def short_expansions(resolved: pd.DataFrame, inv: pd.DataFrame) -> pd.DataFrame:
    rows = []
    exp = resolved[resolved["expand6"]]
    for hs, g in exp.groupby("hs"):
        kids = sorted(g.loc[g["status"] == "expanded", "leaf"].dropna().unique())
        ents = ",".join(sorted(set(g["entity_id"])))
        if hs not in inv.index:
            rows.append({"code": hs, "entities": ents, "reason": "4자리 DB 없음", "ratio_median": None, "db_children": ",".join(kids)})
            continue
        if not kids:
            rows.append({"code": hs, "entities": ents, "reason": "하위 6자리 DB 없음", "ratio_median": None, "db_children": ""})
            continue
        from ..adapters import read_trade
        four = read_trade([hs], items=["expDlr"]).set_index("period_end")["value"]
        ks = read_trade(kids, items=["expDlr"]).groupby("period_end")["value"].sum().reindex(four.index).fillna(0.0)
        med = float((ks / four).median())
        if med < SHORT_RATIO:
            rows.append({"code": hs, "entities": ents, "reason": f"비율 중앙값 {med:.3f} < 0.9", "ratio_median": med,
                         "db_children": ",".join(kids)})
    return pd.DataFrame(rows)


def classify_cut(code: str, last: pd.Timestamp, in_map: bool):
    nxt = (last + pd.offsets.MonthEnd(1))
    s = f"{nxt:%Y%m}"
    hdrs, parts, raw_example = [], [], ""
    for a, b in api.windows(s, f"{END:%Y%m}"):
        h, d, raw = api.query(code, a, b)
        hdrs.append(h.get("resultCode"))
        if not raw_example:
            raw_example = raw
        if not d.empty:
            parts.append(d)
    d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    pos = d[d["expDlr"] > 0] if not d.empty else d
    months_pos = sorted(pos["month"].unique()) if not pos.empty else []
    ok = all(c == "00" for c in hdrs)
    api_last = max(months_pos) if months_pos else None
    if not ok:
        cls, why = ("B" if in_map else "D"), f"API 오류 응답 {sorted(set(hdrs))}"
    elif months_pos and not in_map:
        cls, why = "A", f"목록에 없음. API 는 {s[:4]}-{s[4:]} 이후 수출 있는 달 {len(months_pos)}개 (마지막 {api_last:%Y-%m})"
    elif months_pos and in_map:
        cls, why = "D", f"목록에 있는데 DB 가 {last:%Y-%m} 에서 끝남. API 는 값 있음 (마지막 {api_last:%Y-%m})"
    elif in_map:
        cls, why = "C", "목록에 있음. API 정상 응답, 그 뒤 수출 0 또는 행 없음"
    else:
        cls, why = "C", "목록에 없음. API 정상 응답이나 그 뒤 수출 0 또는 행 없음"
    return cls, why, api_last, len(months_pos), raw_example


def children_from_api(code4: str, start="200701", end="202608") -> pd.DataFrame:
    d = api.query_range(code4, start, end)
    if d.empty:
        return pd.DataFrame(columns=["child6", "months_with_exports", "first", "last", "exp_total"])
    d["child6"] = d["hsCode"].astype(str).str[:6]
    d = d[d["child6"].str.startswith(code4)]
    g = d[d["expDlr"] > 0].groupby("child6").agg(months_with_exports=("month", "nunique"), first=("month", "min"),
                                                last=("month", "max"), exp_total=("expDlr", "sum")).reset_index()
    return g


def main():
    from .registry import build
    b = build()
    res, inv = b["resolved"], b["inventory"]
    m = read_mapping()
    names = dict(zip(m["companies"]["entity_id"], m["companies"]["name"]))
    cmap = collector_codes()
    ex_dir = output_path("2c", "step2b", "a_diagnose", "api_examples")

    cut = cut_codes(res)
    rows = []
    for r in cut.itertuples():
        in_map = r.code in cmap
        cls, why, api_last, npos, raw = classify_cut(r.code, r.last, in_map)
        if cls in ("B", "D"):
            (ex_dir / f"{r.code}.xml").write_text(raw, encoding="utf-8")
        rows.append({"code": r.code, "kind": "끊긴 코드", "excel_codes": r.excel_codes, "entities": r.entities,
                     "entity_names": ",".join(names[e] for e in r.entities.split(",")),
                     "db_first": f"{r.first:%Y-%m}", "db_last": f"{r.last:%Y-%m}", "in_collector_list": in_map,
                     "class": cls, "evidence": why, "api_last_positive": f"{api_last:%Y-%m}" if api_last is not None else None})

    short = short_expansions(res, inv)
    child_rows = []
    for r in short.itertuples():
        ch = children_from_api(r.code)
        dbk = set(r.db_children.split(",")) if r.db_children else set()
        ch["in_db"] = ch["child6"].isin(dbk) | ch["child6"].isin(inv.index)
        ch["db_last"] = [f"{inv.loc[c, 'last']:%Y-%m}" if c in inv.index else None for c in ch["child6"]]
        ch["in_collector_list"] = ch["child6"].isin(cmap)
        ch["parent"] = r.code
        child_rows.append(ch)
        missing = ch[~ch["in_db"]]
        four_in_map = r.code in cmap
        four_last = f"{inv.loc[r.code, 'last']:%Y-%m}" if r.code in inv.index else None
        rows.append({"code": r.code, "kind": "전개 부족 4자리", "excel_codes": r.code, "entities": r.entities,
                     "entity_names": ",".join(names[e] for e in r.entities.split(",")),
                     "db_first": f"{inv.loc[r.code, 'first']:%Y-%m}" if r.code in inv.index else None,
                     "db_last": four_last, "in_collector_list": four_in_map,
                     "class": "A" if len(missing) or not four_in_map else "D",
                     "evidence": (f"{r.reason}. API 의 하위 6자리(수출 있는 것) {len(ch)}개 중 DB 에 없는 것 {len(missing)}개: "
                                  f"{','.join(missing['child6'])}. 4자리 자체도 수집기 목록에 "
                                  f"{'있음' if four_in_map else '없음'}"),
                     "api_last_positive": f"{ch['last'].max():%Y-%m}" if len(ch) else None})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "a_cause_table.csv", index=False, encoding="utf-8-sig")
    if child_rows:
        cc = pd.concat(child_rows, ignore_index=True)
        cc["first"] = cc["first"].dt.strftime("%Y-%m")
        cc["last"] = cc["last"].dt.strftime("%Y-%m")
        cc.to_csv(OUT / "a_children_from_api.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 120)
    print(out.to_string(index=False))
    if child_rows:
        print(cc.to_string(index=False))


if __name__ == "__main__":
    main()
