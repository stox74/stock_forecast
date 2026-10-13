# -*- coding: utf-8 -*-
"""레이더 엑셀 출력 (COLDESC v1). 내부용.

시트 1 'radar'   : 레이더 표 (CSV 와 같은 내용). 금액 열(원)은 천 단위 구분 서식, 머리글 셀 메모 = COLDESC 의미.
시트 2 '변수설명': 분류 | 변수 | 의미 | 계산식 | 단위 | 비고 (coldesc.table). 머리글 고정·굵게, 분류가 바뀔 때 구분선,
                   의미·계산식·비고 줄바꿈, 열 너비 자동.
"""
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from . import coldesc

SHEET_TABLE, SHEET_DESC = "radar", "변수설명"
MAX_WIDTH, WRAP_WIDTH = 60, 50


def _width(v) -> float:
    s = "" if v is None else str(v)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)        # 한글 등 넓은 글자는 2칸


def _cell(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


def _autofit(ws, wrap_cols=()):
    for j, col in enumerate(ws.iter_cols(values_only=True), start=1):
        w = max((_width(v) for v in col), default=8) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(WRAP_WIDTH if j in wrap_cols else MAX_WIDTH, max(8, w))


def money_columns(columns):
    return [c for c in columns if coldesc.COLDESC.get(c, {}).get("unit") == coldesc.WON]


def to_xlsx(tab: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    cols = list(tab.columns)
    desc = coldesc.table(cols)                                  # 설명이 없는 열이 있으면 KeyError
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TABLE
    bold = Font(bold=True)
    ws.append(cols)
    for j, c in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=j)
        cell.font = bold
        d = coldesc.COLDESC[c]
        cell.comment = Comment(f"{d['meaning']}\n단위: {d['unit']}" if d["unit"] else d["meaning"], "fdriver")
    for r in tab.itertuples(index=False):
        ws.append([_cell(v) for v in r])
    money = {cols.index(c) + 1 for c in money_columns(cols)}
    for j in money:
        for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
            row[0].number_format = "#,##0"
    ws.freeze_panes = "D2"
    _autofit(ws)

    wd = wb.create_sheet(SHEET_DESC)
    wd.append(list(desc.columns))
    for c in wd[1]:
        c.font = bold
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")
    line = Border(top=Side(style="medium"))
    prev = None
    wrap_cols = {list(desc.columns).index(k) + 1 for k in ("의미", "계산식", "비고")}
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
