# -*- coding: utf-8 -*-
"""COLDESC v1 테스트: 레이더 열 설명 완전성, 설명 시트 순서, 엑셀 두 시트·서식, 공개 출력에 변수설명·레시피 없음."""
import glob
import unittest
import warnings

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from . import conftest  # noqa: F401
from DATA.fdriver.config import output_path
from DATA.fdriver.radar import build as B
from DATA.fdriver.radar import coldesc as C
from DATA.fdriver.radar import excel as X
from DATA.fdriver.radar import public as P


def _fake_table(n: int = 3) -> pd.DataFrame:
    t = pd.DataFrame({c: [np.nan] * n for c in B.COLUMNS})
    t["entity_id"], t["name"], t["grade"], t["sector"] = ["005930", "000660", "006040"], ["가", "나", "다"], "A", "반도체"
    t["fiscal_quarter"], t["asof_date"] = "2026Q2", pd.Timestamp("2026-07-01").date()
    t["rev_E"], t["rev_base"], t["g_E"], t["g_E_pct"] = [1.2e14, 9.8e12, 2.1e12], [1.0e14, 9.0e12, 2.0e12], 0.1, 10.5
    t["M1_model"], t["E_components"], t["main_model"] = "M1:854232", "E:M1:854232+B2+TS", "M4:854232"
    t["nsur_drv"], t["shock_x"], t["warnings"] = 0.5, 0.02, "드라이버부분(cov=0.67)"
    t["announce_date"] = pd.Timestamp("2026-07-28").date()
    return t


class TestColdesc(unittest.TestCase):
    def test_every_output_column_described(self):
        """레이더 출력의 모든 열이 COLDESC 에 있어야 한다 (하나라도 없으면 실패)."""
        self.assertEqual(C.missing(B.COLUMNS), [])
        for c in B.COLUMNS:
            d = C.COLDESC[c]
            self.assertIn(d["group"], C.GROUPS, c)
            self.assertTrue(d["meaning"], c)
            self.assertEqual(set(d), set(C.FIELDS), c)
        with self.assertRaises(KeyError):
            C.table(B.COLUMNS + ["new_col"])

    def test_unused_only_warns(self):
        """COLDESC 에만 있는 변수는 경고로만 알린다 (삭제·실패 아님)."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            extra = C.unused(B.COLUMNS[:-1])
        self.assertEqual(extra, [B.COLUMNS[-1]])
        self.assertTrue(any("COLDESC" in str(x.message) for x in w))
        self.assertIn(B.COLUMNS[-1], C.COLDESC)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            C.unused(B.COLUMNS)
        if C.unused(B.COLUMNS, warn=False):
            print("COLDESC 에만 있는 변수:", C.unused(B.COLUMNS, warn=False))

    def test_sheet_order(self):
        t = C.table(B.COLUMNS)
        self.assertEqual(list(t.columns), ["분류", "변수", "의미", "계산식", "단위", "비고"])
        g = [C.GROUPS.index(x) for x in t["분류"]]
        self.assertEqual(g, sorted(g))                                         # 분류 순서
        for grp, x in t.groupby("분류", sort=False):                            # 분류 안에서는 레이더 표 순서
            pos = [B.COLUMNS.index(v) for v in x["변수"]]
            self.assertEqual(pos, sorted(pos), grp)
        self.assertEqual(len(t), len(B.COLUMNS))
        self.assertEqual(t["변수"].iloc[0], "grade")

    def test_html_has_description_table(self):
        h = B.to_html(_fake_table(), "t")
        self.assertIn("변수 설명", h)
        self.assertIn(C.COLDESC["g_TS"]["formula"].split("(")[0].strip(), h)


class TestExcel(unittest.TestCase):
    def test_two_sheets_and_format(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = X.to_xlsx(_fake_table(), Path(d) / "r.xlsx")
            with self.assertRaises(FileExistsError):
                X.to_xlsx(_fake_table(), p)                                     # 덮어쓰지 않음
            wb = load_workbook(p)
            self.assertEqual(wb.sheetnames, ["radar", "변수설명"])
            ws, wd = wb["radar"], wb["변수설명"]
            self.assertEqual([c.value for c in ws[1]], B.COLUMNS)
            j = B.COLUMNS.index("rev_E") + 1
            self.assertEqual(ws.cell(row=2, column=j).number_format, "#,##0")
            self.assertIn(C.COLDESC["rev_E"]["meaning"], ws.cell(row=1, column=j).comment.text)
            self.assertEqual(wd.freeze_panes, "A2")
            self.assertTrue(wd["A1"].font.b)
            self.assertEqual([c.value for c in wd[1]], ["분류", "변수", "의미", "계산식", "단위", "비고"])
            self.assertEqual(wd.max_row - 1, len(B.COLUMNS))
            self.assertTrue(wd["C2"].alignment.wrap_text)
            changes = [i for i in range(3, wd.max_row + 1) if wd.cell(row=i, column=1).value != wd.cell(row=i - 1, column=1).value]
            self.assertTrue(changes)
            self.assertTrue(all(wd.cell(row=i, column=1).border.top.style == "medium" for i in changes))

    def test_real_2026q2_xlsx(self):
        """2026Q2 시험분 레이더 xlsx 가 열리고 두 시트가 있는지 (생성된 파일이 있을 때)."""
        files = sorted(glob.glob(str(output_path("radar", "2026Q2", mkdir=False) / "radar_2026Q2_*.xlsx")))
        if not files:
            self.skipTest("2026Q2 xlsx 없음")
        wb = load_workbook(files[-1])
        self.assertEqual(wb.sheetnames, ["radar", "변수설명"])
        self.assertEqual(C.missing([c.value for c in wb["radar"][1]]), [])


class TestPublic(unittest.TestCase):
    def test_public_has_no_coldesc_or_recipe(self):
        t = _fake_table()
        pub = P.public_table(t)
        self.assertTrue(set(pub.columns) <= set(P.PUBLIC_HEADER.values()))
        for text in (pub.to_csv(index=False), P.public_html(pub, "레이더 2026Q2")):
            P.check_public(text)
            self.assertNotIn("변수설명", text)
            self.assertNotIn("변수 설명", text)
            for c, d in C.COLDESC.items():
                if len(d["formula"]) >= P.MIN_PHRASE:
                    self.assertNotIn(d["formula"], text, c)

    def test_guard_blocks_description_and_recipe(self):
        for bad in [C.table(B.COLUMNS).to_csv(index=False),                     # 변수설명 표
                    B.desc_html(B.COLUMNS),                                     # HTML 설명 표
                    "기업,드라이버\n가,M1:854232",                               # 드라이버 모델 id
                    "기업,코드\n가,854232",                                       # HS 코드
                    "기업,원천\n가,export",                                       # 드라이버 이름
                    "x " + C.COLDESC["drv_3m_yoy"]["note"]]:                    # COLDESC 비고 문장
            with self.assertRaises(P.PublicLeak):
                P.check_public(bad)

    def test_save_public_writes_only_allowed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = P.save_public(_fake_table(), "2026Q2", out_dir=d)
            self.assertEqual(set(out), {"csv", "html"})                          # 엑셀·설명 시트 없음
            txt = out["csv"].read_text(encoding="utf-8-sig")
            self.assertEqual(txt.splitlines()[0].split(","), [P.PUBLIC_HEADER[c] for c in P.PUBLIC_COLUMNS])
            self.assertNotIn("854232", txt + out["html"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
