# -*- coding: utf-8 -*-
"""일괄 적재 등급과 분기 패널 테스트."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import asof, bulkload, config, panel
from DATA.fdriver.adapters._common import finalize
from DATA.fdriver.config import COMMON_COLUMNS
from .test_adapters import DB_OK

T = pd.Timestamp


class TestBulkClassification(unittest.TestCase):
    def _daily(self, rows):
        return pd.DataFrame(rows, columns=["d", "n", "n_periods", "p1", "p2"])

    def test_large_history_load_is_bulk(self):
        d = self._daily([(T("2026-07-15"), 4247, 90, T("2019-01-31"), T("2026-06-30")),
                         (T("2026-08-14"), 40, 1, T("2026-07-31"), T("2026-07-31")),
                         (T("2026-09-14"), 43, 1, T("2026-08-31"), T("2026-08-31"))])
        self.assertEqual(bulkload.classify_days(d)["is_bulk"].tolist(), [True, False, False])

    def test_small_history_load_is_bulk_by_span(self):
        # 출국자 180행: 규모 기준(10%, 1,000행) 미달이지만 15년치 이력이라 일괄 적재
        d = self._daily([(T("2026-10-01"), 35772, 180, T("2011-09-30"), T("2026-08-31")),
                         (T("2026-10-03"), 180, 180, T("2011-09-30"), T("2026-08-31"))])
        self.assertEqual(bulkload.classify_days(d)["is_bulk"].tolist(), [True, True])

    def test_regular_monthly_run_with_many_series_is_not_bulk(self):
        # 항공처럼 시리즈가 많아 정기 수집이 하루 2,000행이어도 최근 1~2개 기간이면 정기 수집
        d = self._daily([(T("2026-10-01"), 86436, 140, T("2015-01-31"), T("2026-08-31")),
                         (T("2026-11-02"), 2000, 2, T("2026-08-31"), T("2026-09-30"))])
        self.assertEqual(bulkload.classify_days(d)["is_bulk"].tolist(), [True, False])

    def test_config_override(self):
        old = dict(config.BULK_LOAD["dates"])
        try:
            config.BULK_LOAD["dates"]["some_table"] = ["2026-01-02"]
            self.assertEqual(bulkload.bulk_dates("some_table", "first_collected_at"), [T("2026-01-02")])
            config.BULK_LOAD["dates"]["other_table"] = []
            self.assertEqual(bulkload.bulk_dates("other_table", "first_collected_at"), [])
            # 공표일 컬럼은 일괄 적재 대상이 아님
            self.assertEqual(bulkload.bulk_dates("some_table", "first_release_date"), [])
        finally:
            config.BULK_LOAD["dates"].clear()
            config.BULK_LOAD["dates"].update(old)


class TestBulkGrade(unittest.TestCase):
    def test_bulk_observed_date_is_B_with_note(self):
        pe = pd.Series([T("2026-08-31"), T("2026-08-31"), T("2020-01-31")])
        obs = pd.Series([T("2026-10-01 18:00"), T("2026-10-02"), T("2026-10-01")])
        r = asof.resolve(pe, "kosis", observed=obs, freq="M", bulk_dates=[T("2026-10-01")])
        self.assertEqual(r["available_at"].tolist(), [T("2026-10-01"), T("2026-10-02"), T("2020-03-06")])
        self.assertEqual(r["asof_quality"].tolist(), ["B", "A", "C"])   # 백필(규칙일 사용)은 C 그대로
        self.assertEqual(r["asof_note"].tolist(), ["bulk_load", "", ""])

    def test_bulk_note_appends_to_rule_note(self):
        # tw_revenue: 규칙 등급도 B 지만 관측일 사용 + bulk 표시
        r = asof.resolve(pd.Series([T("2026-06-30")]), "tw_revenue",
                         observed=pd.Series([T("2026-07-15")]), bulk_dates=[T("2026-07-15")])
        self.assertEqual(r["available_at"].iloc[0], T("2026-07-15"))
        self.assertEqual((r["asof_quality"].iloc[0], r["asof_note"].iloc[0]), ("B", "bulk_load"))


def _long(rows):
    df = pd.DataFrame(rows, columns=["key", "period_end", "freq", "item", "value", "unit", "available_at",
                                     "asof_quality", "asof_note", "source"])
    df["currency"] = None
    return finalize(df)


class TestFiscalQuarter(unittest.TestCase):
    def test_quarter_end_mapping(self):
        pe = pd.Series([T("2026-01-31"), T("2026-03-31"), T("2026-04-15"), T("2026-12-31")])
        self.assertEqual(panel.fiscal_quarter_end(pe, 12).tolist(),
                         [T("2026-03-31"), T("2026-03-31"), T("2026-06-30"), T("2026-12-31")])
        # 3월 결산: 분기말 6, 9, 12, 3월
        self.assertEqual(panel.fiscal_quarter_end(pd.Series([T("2026-01-31"), T("2026-04-30")]), 3).tolist(),
                         [T("2026-03-31"), T("2026-06-30")])

    def test_sum_mean_and_latest_available(self):
        rows = []
        for m, (v, av, g, note) in zip([1, 2, 3], [(10, "2026-02-15", "C", ""), (20, "2026-03-15", "A", ""),
                                                  (30, "2026-04-20", "B", "bulk_load")]):
            pe = T(2026, m, 1) + pd.offsets.MonthEnd(0)
            rows.append(("854232", pe, "M", "expDlr", v, "USD", T(av), g, note, "korea_monthly_trade_data"))
            rows.append(("kosis_x", pe, "M", "value", v, "2020＝100", T(av), "A", "", "kr_kosis_data"))
        q = panel.to_fiscal_quarter(_long(rows))
        a = q[q["key"] == "854232"].iloc[0]
        self.assertEqual((a["value"], a["agg"], a["n_obs"]), (60.0, "sum", 3))
        self.assertEqual(a["available_at"], T("2026-04-20"))      # 가장 늦은 원천 값
        self.assertEqual(a["asof_quality"], "C")                   # 가장 낮은 등급
        self.assertEqual(a["asof_note"], "bulk_load")
        b = q[q["key"] == "kosis_x"].iloc[0]
        self.assertEqual((b["value"], b["agg"]), (20.0, "mean"))
        self.assertEqual(list(q.columns), COMMON_COLUMNS + ["n_obs", "agg"])

    def test_incomplete_quarter_dropped(self):
        rows = [("854232", T("2026-07-31"), "M", "expDlr", 5, "USD", T("2026-08-15"), "C", "", "t"),
                ("854232", T("2026-08-31"), "M", "expDlr", 5, "USD", T("2026-09-15"), "C", "", "t")]
        self.assertTrue(panel.to_fiscal_quarter(_long(rows)).empty)

    def test_agg_rule_precedence(self):
        self.assertEqual(panel.agg_rule("HOUST", "value", "us_fred_data", "Thous. of Units"), "mean")
        self.assertEqual(panel.agg_rule("RSHPCS", "value", "us_fred_data", "Mil. of $"), "sum")
        self.assertEqual(panel.agg_rule("apt_x", "value", "kr_airline_traffic", None), "sum")
        self.assertEqual(panel.agg_rule("k", "value", "kr_kosis_data", "백만원"), "sum")
        self.assertEqual(panel.agg_rule("k", "value", "kr_kosis_data", "2020＝100"), "mean")
        self.assertEqual(panel.agg_rule("k", "expDlr", "x", None, how={"k": "mean"}), "mean")

    def test_combine_across_keys_requires_all(self):
        rows = [("a", T("2026-01-31"), "M", "value", 1, None, T("2026-03-01"), "A", "", "kr_airline_traffic"),
                ("b", T("2026-01-31"), "M", "value", 2, None, T("2026-03-05"), "C", "", "kr_airline_traffic"),
                ("a", T("2026-02-28"), "M", "value", 1, None, T("2026-04-01"), "A", "", "kr_airline_traffic")]
        c = panel.combine_across_keys(_long(rows), "sum_ab", "value")
        self.assertEqual(len(c), 1)
        self.assertEqual((c["value"].iloc[0], c["available_at"].iloc[0], c["asof_quality"].iloc[0]),
                         (3.0, T("2026-03-05"), "C"))


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestPanelOnDB(unittest.TestCase):
    def test_panel_asof_no_future_values(self):
        p = panel.panel("003350")
        self.assertIn("revenue", set(p["driver"]))
        self.assertIn("trade:330420:unit_price", set(p["driver"]))
        self.assertEqual(list(p.columns), panel.PANEL_COLUMNS)
        self.assertTrue(p["available_at"].notna().all())
        rng = np.random.default_rng(3)
        lo, hi = p["available_at"].min().value, p["available_at"].max().value
        for d in pd.to_datetime(rng.integers(lo, hi, 10)).normalize():
            got = asof.as_of(p, d)
            self.assertEqual(int((got["available_at"] > d).sum()), 0)
            self.assertEqual(len(got), int((p["available_at"] <= d).sum()))


if __name__ == "__main__":
    unittest.main()
