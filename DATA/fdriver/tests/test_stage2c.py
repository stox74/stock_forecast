# -*- coding: utf-8 -*-
"""2c 작업 2: zip 본문 고르기, 세그먼트 가중평균 특징값, 세그먼트 합계 규칙, 사업부 비중 시점 규칙 테스트 (DB 없이)."""
import io
import unittest
import zipfile

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import events as ev
from DATA.fdriver.nowcast import features as ft
from DATA.fdriver.stage2c import drivers as dv
from DATA.fdriver.stage2c import segments as sg

T = pd.Timestamp


def _zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, body in files:
            z.writestr(name, body)
    return zipfile.ZipFile(io.BytesIO(b.getvalue()))


def _doc(title, n=10):
    return f"<DOCUMENT><DOCUMENT-NAME ACODE='11011'>{title}</DOCUMENT-NAME>" + "x" * n + "</DOCUMENT>"


class TestPickMainFile(unittest.TestCase):
    def test_annual_report_audit_first(self):
        z = _zip([("20260310002954_00760.xml", _doc("감사보고서", 900)),
                  ("20260310002954.xml", _doc("사업보고서", 100)),
                  ("20260310002954_00761.xml", _doc("연결감사보고서", 900))])
        name, rule, files = ev.pick_main_file(z, "20260310002954")
        self.assertEqual(name, "20260310002954.xml")
        self.assertEqual(rule, "rcept_no_name")
        self.assertEqual(len(files), 3)

    def test_single(self):
        z = _zip([("x.xml", _doc("잠정실적"))])
        self.assertEqual(ev.pick_main_file(z, "1")[:2], ("x.xml", "single"))

    def test_title_rule(self):
        z = _zip([("a_1.xml", _doc("감사보고서", 900)), ("b_2.xml", _doc("사업보고서", 10))])
        self.assertEqual(ev.pick_main_file(z, "9")[:2], ("b_2.xml", "title"))

    def test_fallback_largest_non_audit(self):
        z = _zip([("a_1.xml", _doc("첨부A", 50)), ("b_2.xml", _doc("첨부B", 500)), ("c_3.xml", _doc("감사보고서", 900))])
        self.assertEqual(ev.pick_main_file(z, "9")[:2], ("b_2.xml", "fallback_largest"))


def _seg_frame():
    rows = []
    for seg, base, g in (("A", 100.0, 0.10), ("B", 50.0, -0.20)):
        for y in (2023, 2024):
            for mth in (1, 2, 3):
                v = base * (np.exp(g) if y == 2024 else 1.0)
                pe = T(y, mth, 1) + pd.offsets.MonthEnd(0)
                rows.append({"key": seg, "item": "expDlr", "period_end": pe, "value": v, "available_at": pe + pd.Timedelta(days=15)})
    return ft.prepare_driver(pd.DataFrame(rows))


class TestWeightedFeature(unittest.TestCase):
    def test_weighted_average_of_segment_yoy(self):
        drv = _seg_frame()
        spec = ft.DriverSpec("M1:export", "x", "M", 0, lambda: drv, weights=lambda j, t: {"A": 0.6, "B": 0.2})
        f = ft.target_feature(spec, drv, T("2024-03-31"), T("2030-01-01"))
        self.assertAlmostEqual(f["x"], (0.6 * 0.10 + 0.2 * -0.20) / 0.8)

    def test_zero_weight_segment_ignored_and_missing_segment_gives_none(self):
        drv = _seg_frame()
        spec = ft.DriverSpec("M1:export", "x", "M", 0, lambda: drv, weights=lambda j, t: {"A": 1.0, "B": 0.0})
        self.assertAlmostEqual(ft.target_feature(spec, drv, T("2024-03-31"), T("2030-01-01"))["x"], 0.10)
        spec2 = ft.DriverSpec("M1:export", "x", "M", 0, lambda: drv, weights=lambda j, t: {"A": 0.5, "C": 0.5})
        self.assertIsNone(ft.target_feature(spec2, drv, T("2024-03-31"), T("2030-01-01"))["x"])

    def test_unweighted_unchanged(self):
        drv = _seg_frame()
        drv = drv[drv["key"] == "A"]
        spec = ft.DriverSpec("M1:x", "x", "M", 0, lambda: drv)
        self.assertAlmostEqual(ft.target_feature(spec, drv, T("2024-03-31"), T("2030-01-01"))["x"], 0.10)


class TestSegmentSeries(unittest.TestCase):
    def _raw(self, code, months, value=1.0):
        pe = pd.to_datetime(months) + pd.offsets.MonthEnd(0)
        return pd.DataFrame({"key": code, "period_end": pe, "freq": "M", "item": "expDlr", "value": value,
                             "unit": "USD", "currency": "USD", "available_at": pe + pd.Timedelta(days=15),
                             "asof_quality": "C", "asof_note": "", "source": "t"})

    def test_truncate_at_collection_stop_and_zero_fill(self):
        raw = pd.concat([self._raw("111111", ["2024-01", "2024-02", "2024-03", "2024-04"], 10.0),
                         self._raw("222222", ["2024-01", "2024-03"], 1.0)])          # 2024-02 없음 -> 0, 2024-03 에서 끝
        leaves = pd.DataFrame({"leaf": ["111111", "222222"], "status": ["listed", "listed"]})
        s = dv.segment_series(leaves, "seg", raw)
        self.assertEqual(s["end"], T("2024-03-31"))
        self.assertEqual(list(s["df"]["value"]), [11.0, 10.0, 11.0])
        self.assertEqual(s["zero_filled"], 1)

    def test_retired_code_not_used_for_end(self):
        raw = pd.concat([self._raw("111111", ["2021-12", "2022-01", "2024-06"], 5.0),
                         self._raw("333333", ["2021-12"], 2.0)])                    # 2023-12 이전에 끝난 코드
        leaves = pd.DataFrame({"leaf": ["111111", "333333"], "status": ["listed", "listed"]})
        self.assertEqual(dv.segment_series(leaves, "seg", raw)["end"], T("2024-06-30"))


class TestWeightTiming(unittest.TestCase):
    def test_report_before_first_disclosure_only(self):
        filled = pd.DataFrame({"entity_id": ["X", "X"], "fiscal_year": [2023, 2024],
                               "rcept_dt": [T("2024-03-15"), T("2025-03-15")],
                               "shares": [{"A": 0.6, "B": 0.4}, {"A": 0.3, "B": 0.7}]})
        fdt = {T("2025-03-31"): T("2025-04-30"), T("2024-12-31"): T("2025-03-15")}
        wf = sg.make_weight_fn("X", filled, {"A": True, "B": True}, ["A", "B"], first_dt=fdt)
        self.assertEqual(wf(T("2025-03-31"), T("2030-01-01")), {"A": 0.3, "B": 0.7})
        # 4분기 첫 공시일 = 사업보고서 접수일 -> 그 보고서는 '이미 나와 있던' 것이 아님
        self.assertEqual(wf(T("2024-12-31"), T("2030-01-01")), {"A": 0.6, "B": 0.4})
        # 예측 기준일 t 이후 접수된 보고서는 쓰지 않음
        self.assertEqual(wf(T("2025-03-31"), T("2025-03-10")), {"A": 0.6, "B": 0.4})
        self.assertEqual(wf(T("2023-09-30"), T("2030-01-01")), {})

    def test_start_rule(self):
        filled = pd.DataFrame({"entity_id": ["X"], "fiscal_year": [2023], "rcept_dt": [T("2024-03-15")],
                               "shares": [{"A": 0.6, "B": 0.4}]})
        wf = sg.make_weight_fn("X", filled, {"A": True, "B": True}, ["A", "B"], {"B": T("2024-12-31")},
                               first_dt={})
        self.assertEqual(wf(T("2024-09-30"), T("2030-01-01")), {"A": 0.6, "B": 0.0})
        self.assertEqual(wf(T("2024-12-31"), T("2030-01-01")), {"A": 0.6, "B": 0.4})


class TestParseRows(unittest.TestCase):
    def test_multi_year_first_column_and_pct_format(self):
        s = "Beauty(화장품) 기초 23,500(36.9%) 28,506(41.8%) HDB(생활용품) 샴푸 22,348(35.2%) 21,370(31.4%) 합 계 63,555(100.0%)"
        rows = sg.parse_rows(s)
        self.assertEqual([r["amount"] for r in rows], [23500, 22348, 63555])
        self.assertEqual(sg.classify(rows[1]["label"], sg.RULES["051900"])[1], "생활용품")


class TestMonthlyPrelim(unittest.TestCase):
    """2c 작업 3 수정: 예전 서식 월간 공시는 분기 이벤트에서 뺀다 (기간 표기 '(2023년1월)' 등)."""

    def _parse(self, text, dt="2023-02-01"):
        return ev.parse_prelim(text, T(dt))

    def test_monthly_labels_detected(self):
        for body in ["구분(단위:대,%) 당기실적 (2023년1월) 전기실적 (2022년12월) 국내 51,503",
                     "당기실적 전기실적 ('19년10월) ('19년9월) ('18년10월) 매출액 당해실적 - -",
                     "당기실적 (2018.02월) (2018.01월) (2017.02월) 매출액 당해실적 591,638",
                     "당기실적 ( 2012.9월) ( 2012.8월) (2011.9월) 매출액 당해실적 - -"]:
            self.assertEqual(self._parse(body)["period_src"], "monthly_label", body)
            self.assertLess(self._parse(body)["period_days"], ev.MIN_QUARTER_DAYS)

    def test_quarterly_not_monthly(self):
        q1 = "해당 기간 당 기: 2010년 3/4분기(2010.07.01-09.30) 매출액 당해실적 106,865"
        q2 = "당 기: 2012년 1월 1일 ~ 2012년 3월 31일 매출액 당해실적 32,529 정보제공(예정)일시 2012년 4월 16일"
        q3 = "실적기간 당기실적 2026-07-01 ~ 2026-09-30 매출액 당해실적 195.0"
        for body in (q1, q2, q3):
            self.assertNotEqual(self._parse(body)["period_src"], "monthly_label", body)


if __name__ == "__main__":
    unittest.main()
