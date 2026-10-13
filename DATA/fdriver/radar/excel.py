# -*- coding: utf-8 -*-
"""레이더 엑셀 출력 (v2). 내부용. 시트 순서: 판단 | radar | 변수설명.

판단    : 1행 규칙 요약과 기준값(radar.output 상수), 3행 머리글, 판단 결과별 행 색 (후보 상향·하향, 검토, 보류).
          숫자 소수 1자리, 조원 소수 3자리.
radar   : 출력 표 (CSV 와 같은 내용, 출력 이름). 원 단위 천 구분, 조원 소수 3자리, % 소수 1자리.
변수설명: 분류 | 출력 변수명 | 내부 변수명 | 의미 | 계산식 | 단위 | 비고 (coldesc.table). 머리글 고정·굵게, 분류가 바뀔 때 구분선,
          의미·계산식·비고 줄바꿈, 열 너비 자동.
판단·radar 시트 머리글 셀에는 COLDESC 의미를 메모로 단다.
"""
from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import coldesc, output

SHEET_JUDGE, SHEET_TABLE, SHEET_DESC = "판단", "radar", "변수설명"
MAX_WIDTH, WRAP_WIDTH = 60, 50
FILLS = {"후보(상": "E3F1E6", "후보(하": "FDE8E7", "검토": "FFF5D6", "보류": "ECEBE7"}
BOLD = Font(bold=True)


def _width(v) -> float:
    s = "" if v is None else str(v)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)        # 한글 등 넓은 글자는 2칸


def _cell(v):
    if v is None or v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


def _autofit(ws, wrap_cols=(), start_row: int = 1):
    for j, col in enumerate(ws.iter_cols(min_row=start_row, values_only=True), start=1):
        w = max((_width(v) for v in col), default=8) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(WRAP_WIDTH if j in wrap_cols else MAX_WIDTH, max(8, w))


def number_format(c: str) -> Optional[str]:
    if c.startswith("jo_"):
        return "#,##0.000"
    if coldesc.COLDESC.get(c, {}).get("unit") == coldesc.WON:
        return "#,##0"
    if c.endswith("_pct"):
        return "0.0"
    if c.startswith("z_"):
        return "0.00"
    return None


def _write_table(ws, t: pd.DataFrame, header_row: int, fill_rows: bool = False):
    cols = list(t.columns)
    for j, c in enumerate(cols, start=1):
        cell = ws.cell(row=header_row, column=j, value=c)
        cell.font = BOLD
        d = coldesc.COLDESC[c]
        cell.comment = Comment(f"{d['meaning']}\n단위: {d['unit']}" if d["unit"] else d["meaning"], "fdriver")
    fmts = {j: number_format(c) for j, c in enumerate(cols, start=1)}
    for i, r in enumerate(t.itertuples(index=False), start=header_row + 1):
        fill = None
        if fill_rows:
            dec = str(getattr(r, "decision", "") or "")
            key = next((k for k in FILLS if dec.startswith(k)), None)
            fill = PatternFill("solid", fgColor=FILLS[key]) if key else None
        for j, v in enumerate(r, start=1):
            cell = ws.cell(row=i, column=j, value=_cell(v))
            if fmts[j]:
                cell.number_format = fmts[j]
            if fill:
                cell.fill = fill


def to_xlsx(out: pd.DataFrame, path: Path, judge: Optional[pd.DataFrame] = None) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    judge = output.judgement(out) if judge is None else judge
    desc = coldesc.table(list(out.columns) + [c for c in judge.columns if c not in out.columns])  # 설명 없는 열이면 KeyError
    wb = Workbook()

    wj = wb.active
    wj.title = SHEET_JUDGE
    wj.cell(row=1, column=1, value=f"기준값: 컨센서스 대비 갭 {output.GAP_THRESHOLD_PCT:g}%, 구성 불일치 "
                                   f"{output.SPREAD_THRESHOLD_PCT:g}%. " + output.rule_summary()).font = BOLD
    _write_table(wj, judge, header_row=3, fill_rows=True)
    wj.freeze_panes = "C4"
    _autofit(wj, start_row=3)

    ws = wb.create_sheet(SHEET_TABLE)
    _write_table(ws, out, header_row=1, fill_rows=True)
    ws.freeze_panes = "E2"
    _autofit(ws)

    wd = wb.create_sheet(SHEET_DESC)
    wd.append(list(desc.columns))
    for c in wd[1]:
        c.font = BOLD
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")
    line = Border(top=Side(style="medium"))
    wrap_cols = {list(desc.columns).index(k) + 1 for k in ("의미", "계산식", "비고")}
    prev = None
    for i, r in enumerate(desc.itertuples(index=False), start=2):
        wd.append(list(r))
        for j, cell in enumerate(wd[i], start=1):
            cell.alignment = wrap if j in wrap_cols else top
            if prev is not None and r[0] != prev:
                cell.border = line
        prev = r[0]
    wd.freeze_panes = "A2"
    _autofit(wd, wrap_cols)
    wb.save(path)
    return path
