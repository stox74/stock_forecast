# -*- coding: utf-8 -*-
"""2c 작업 2 (e): 기업 사건 판정 (명세서 2c v2 4절: 대상 사업 매출이 기업 매출의 20% 이상이면 해당 분기 이후 제외).

- '공시 확인' 7건: 합병·분할·영업양수 결정 공시(주요사항보고서)의 대상 사업 매출 ÷ 기업 연결 매출(직전 연도, read_revenue
  분기 합). 공시가 밝힌 기준(분할: 존속+신설 사업부문, 별도 기준 / 영업양수: 공시의 '당사전체')의 비중도 함께 적는다.
- 판정 기준값은 연결 매출 대비 비중 (nowcast 대상 매출이 연결 매출이므로). 20% 이상 -> 제외 목록, 미만 -> 유지.
- '제외' 14건은 그대로 목록에 넣는다. 결과는 CORP_EVENTS_2C (config 로 옮겨 nowcast 가 읽음).
실행: python -m DATA.fdriver.stage2c.e_corp_events
"""
import re

import pandas as pd

from .. import events
from ..adapters import read_revenue
from ..config import output_path
from . import dart
from .mapping import read_mapping

OUT = output_path("2c", "step2", "e_corp_events")
THRESHOLD = 0.20

# (종목, 엑셀 분기 표기, 사건, 근거 공시, 대상 사업, 대상 매출(원), 대상 매출 연도, 공시 기준 분모(원), 분모 설명, 제외 시작 분기)
CHECKS = [
    ("042660", "2024Q4~", "한화오션디지털 흡수합병 (완전자회사, 2024-12 완료)", "20240927000904", "한화오션디지털㈜",
     51_769_202_702, 2023, None, "", "2024-12-31"),
    ("004020", "2020Q2~", "현대IFC 단순·물적분할 (2020-04 완료)", "20200225000219", "현대아이에프씨(단조 사업)",
     229_357_917_343, 2018, 17_844_501_443_067 + 229_357_917_343, "존속+신설 사업부문 최근 사업연도 매출(별도)", "2020-06-30"),
    ("011170", "2026Q2~", "롯데케미칼대산석화 단순·물적분할 (2026-06 완료)", "20260326000976", "대산공장 사업부문",
     3_617_567_753_803, 2025, 14_865_437_561_119 + 3_617_567_753_803, "존속+신설 사업부문 최근 사업연도 매출(별도)", "2026-06-30"),
    ("010950", "2020Q3~", "동북화학 흡수합병 (완전자회사, 2020-09 완료)", "20200602000263", "동북화학㈜",
     10_729_363_849, 2019, None, "", "2020-09-30"),
    ("003230", "2025Q3~", "삼양스퀘어팩 흡수합병 (자회사, 2025-08 완료)", "20250512000568", "㈜삼양스퀘어팩",
     32_266_024_361, 2024, None, "", "2025-09-30"),
    ("010120", "2022Q2~", "LS이모빌리티솔루션 단순·물적분할 (2022-04 완료)", "20220208000496", "EV Relay 사업",
     58_479_738_733, 2021, 1_818_968_133_542 + 58_479_738_733, "존속+신설 사업부문 최근 사업연도 매출(별도)", "2022-06-30"),
    ("000660", "2025Q1, 2026Q1~Q2", "Intel NAND 사업 영업양수 (2020-10 결정, 2021-12 1차·2025-03 2차 완료)",
     "20201020000014 (정정 20250317000576)", "Intel NAND·SSD 사업", 4_652_281_600_000, 2019, 26_990_732_675_981,
     "공시의 '당사전체' 매출 (B)", "2025-03-31"),
]
NOTES = {
    "000660": "2026Q1~Q2 의 종속회사 영업양수도(20260128800964·20260128800951)는 SK hynix NAND Product Solutions -> "
              "Solidigm Inc. 연결 범위 안 이전이라 연결 매출 비중 0 으로 봄 (공시에 대상 매출 없음).",
}


def annual_revenue(entity_id: str, fy: int) -> float:
    r = read_revenue([entity_id])
    r = r[(r["period_end"].dt.year == fy)]
    return float(r["value"].sum()) if len(r) == 4 else float("nan")


def parse_quarters(s: str) -> list:
    """'2016Q1~Q4', '2018Q2, 2019Q2', '2017Q1~2018Q1', '2013Q3', '2010Q4~2012Q4 (4분기)' -> [(시작 분기말, 끝 분기말)]."""
    out = []
    for part in re.split(r",\s*", re.sub(r"\(.*?\)", "", s)):
        m = re.match(r"\s*(\d{4})Q(\d)(?:~(?:(\d{4}))?Q(\d))?", part)
        if not m:
            continue
        y0, q0 = int(m.group(1)), int(m.group(2))
        y1 = int(m.group(3)) if m.group(3) else y0
        q1 = int(m.group(4)) if m.group(4) else q0
        out.append((events.fiscal_quarter_end(f"{y0}Q{q0}"), events.fiscal_quarter_end(f"{y1}Q{q1}")))
    return out


def main():
    m = read_mapping()
    ce = m["corp_events"]
    rows = []
    for e, qstr, what, rno, target, amt, fy, disc_den, disc_note, start in CHECKS:
        den = annual_revenue(e, fy)
        r = amt / den
        rows.append({"entity_id": e, "name": ce.loc[ce["entity_id"] == e, "name"].iloc[0], "excel_quarters": qstr,
                     "event": what, "rcept_no": rno, "target": target, "target_revenue": amt, "target_fy": fy,
                     "consolidated_revenue_fy": den, "ratio_consolidated": r,
                     "ratio_disclosure_basis": (amt / disc_den) if disc_den else None, "disclosure_basis": disc_note,
                     "decision": "제외" if r >= THRESHOLD else "유지", "exclude_from": start if r >= THRESHOLD else None,
                     "note": NOTES.get(e, "")})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "e_checks.csv", index=False, encoding="utf-8-sig")

    excl = []
    for r in ce[ce["decision"].str.strip() == "제외"].itertuples():
        for a, b in parse_quarters(r.quarters):
            excl.append({"entity_id": r.entity_id, "name": r.name, "start": a.date(), "end": b.date(),
                         "reason": f"{r.content} (매핑 엑셀 v2 기업사건: 제외)"})
    for r in t[t["decision"] == "제외"].itertuples():
        excl.append({"entity_id": r.entity_id, "name": r.name, "start": pd.Timestamp(r.exclude_from).date(),
                     "end": pd.Timestamp("2099-12-31").date(), "reason": f"{r.event}: 대상 매출 비중 {r.ratio_consolidated:.1%}"})
    ex = pd.DataFrame(excl)
    ex.to_csv(OUT / "e_exclusion_list.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 250)
    print(t.drop(columns=["note"]).to_string(index=False))
    print(ex.to_string(index=False))
    return t, ex


if __name__ == "__main__":
    main()
