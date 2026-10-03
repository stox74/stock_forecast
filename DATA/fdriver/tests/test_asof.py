# -*- coding: utf-8 -*-
"""asof 규칙과 as_of 필터 테스트. DB 불필요."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401  (unittest 실행 시 경로 설정)
from DATA.fdriver.asof import as_of, quality_report, resolve, rule_dates, stamp
from DATA.fdriver.config import ASOF_GRADES, RELEASE_RULES

T = pd.Timestamp


class TestReleaseRulesConfig(unittest.TestCase):
    def test_every_rule_is_well_formed(self):
        for name, r in RELEASE_RULES.items():
            self.assertIn("lag", r, name)
            self.assertIn(r["rule_grade"], ASOF_GRADES, name)
            if r.get("observed") is not None:
                self.assertIn(r["observed_grade"], ASOF_GRADES, name)

    def test_required_rules_exist(self):
        for name in ["revenue_quarter", "revenue_annual", "trade", "tw_revenue", "fred",
                     "kosis", "komis", "airline", "tourism", "price", "google_trends"]:
            self.assertIn(name, RELEASE_RULES)


class TestRuleDates(unittest.TestCase):
    def test_revenue_quarter_45_and_annual_90(self):
        q = rule_dates(pd.Series([T("2025-03-31")]), "revenue_quarter")
        a = rule_dates(pd.Series([T("2025-12-31")]), "revenue_annual")
        self.assertEqual(q.iloc[0], T("2025-05-15"))
        self.assertEqual(a.iloc[0], T("2026-03-31"))

    def test_revenue_never_uses_observed_date(self):
        # report_date 등 어떤 관측일을 넘겨도 법정 기한(C)
        r = resolve(pd.Series([T("2025-03-31")]), "revenue_quarter",
                    observed=pd.Series([T("2025-03-31")]))
        self.assertEqual(r["available_at"].iloc[0], T("2025-05-15"))
        self.assertEqual(r["asof_quality"].iloc[0], "C")

    def test_trade_15_days(self):
        r = resolve(pd.Series([T("2026-08-31")]), "trade")
        self.assertEqual(r["available_at"].iloc[0], T("2026-09-15"))
        self.assertEqual(r["asof_quality"].iloc[0], "C")

    def test_tw_next_month_10th(self):
        d = rule_dates(pd.Series([T("2026-02-28"), T("2026-12-31")]), "tw_revenue")
        self.assertEqual(list(d), [T("2026-03-10"), T("2027-01-10")])

    def test_by_freq_requires_known_freq(self):
        with self.assertRaises(ValueError):
            rule_dates(pd.Series([T("2026-01-31")]), "fred", freq="X")
        with self.assertRaises(ValueError):
            rule_dates(pd.Series([T("2026-01-31")]), "fred")


class TestMarketDailyAndCalendar(unittest.TestCase):
    def tearDown(self):
        from DATA.fdriver import asof
        asof.set_trading_calendar(None)

    def test_market_daily_two_bdays_C_and_realtime_A(self):
        pe = pd.Series([T("2026-09-25"), T("2023-01-02")])          # 금요일, 백필 행
        obs = pd.Series([T("2026-09-29 18:00"), T("2026-10-01")])
        r = resolve(pe, "market_daily", observed=obs)
        self.assertEqual(r["available_at"].tolist(), [T("2026-09-29"), T("2023-01-04")])
        self.assertEqual(r["asof_quality"].tolist(), ["A", "C"])

    def test_bdays_use_trading_calendar(self):
        from DATA.fdriver import asof
        # 2026-09-24~26 추석 연휴 가정: 23(수) 다음 거래일은 28(월)
        days = pd.bdate_range("2026-09-01", "2026-10-30").difference(
            pd.DatetimeIndex(["2026-09-24", "2026-09-25"]))
        asof.set_trading_calendar(days)
        r = rule_dates(pd.Series([T("2026-09-23")]), "market_daily")
        self.assertEqual(r.iloc[0], T("2026-09-29"))               # +2 거래일 (28, 29)
        # 달력 범위 밖은 BDay
        r2 = rule_dates(pd.Series([T("2027-01-04")]), "market_daily")
        self.assertEqual(r2.iloc[0], T("2027-01-06"))


class TestResolve(unittest.TestCase):
    def test_tw_realtime_is_A_backfill_is_B(self):
        pe = pd.Series([T("2026-08-31"), T("2019-01-31")])
        obs = pd.Series([T("2026-09-14 00:47:39"), T("2026-07-15 23:42:08")])
        r = resolve(pe, "tw_revenue", observed=obs)
        self.assertEqual(r["available_at"].tolist(), [T("2026-09-14"), T("2019-02-10")])
        self.assertEqual(r["asof_quality"].tolist(), ["A", "B"])

    def test_fred_release_date_within_60_days_is_A(self):
        pe = pd.Series([T("2024-01-31"), T("2010-01-31")])
        rel = pd.Series([T("2024-02-15"), T("2013-05-14")])   # 2010년분은 이력 시작일이 찍힌 경우
        r = resolve(pe, "fred", observed=rel, freq="M")
        self.assertEqual(r["available_at"].tolist(), [T("2024-02-15"), T("2010-03-17")])
        self.assertEqual(r["asof_quality"].tolist(), ["A", "C"])

    def test_fred_by_freq(self):
        pe = pd.Series([T("2026-06-30"), T("2026-09-19"), T("2026-09-25")])
        r = resolve(pe, "fred", observed=pd.Series([pd.NaT] * 3), freq=pd.Series(["Q", "W", "D"]))
        self.assertEqual(r["available_at"].tolist(), [T("2026-09-28"), T("2026-09-26"), T("2026-09-28")])
        self.assertTrue((r["asof_quality"] == "C").all())

    def test_kosis_backfill_uses_rule(self):
        pe = pd.Series([T("2020-05-31"), T("2026-08-31")])
        obs = pd.Series([T("2026-10-01 18:12:13"), T("2026-10-01 18:12:13")])
        r = resolve(pe, "kosis", observed=obs, freq="M")
        # 2020-05: 규칙일 07-05 보다 6년 늦음 -> 백필 -> 규칙(C)
        # 2026-08: 규칙일 10-05 이전 관측 -> 관측일(A)
        self.assertEqual(r["available_at"].tolist(), [T("2020-07-05"), T("2026-10-01")])
        self.assertEqual(r["asof_quality"].tolist(), ["C", "A"])

    def test_observed_before_period_end_is_ignored(self):
        r = resolve(pd.Series([T("2026-08-31")]), "kosis",
                    observed=pd.Series([T("2026-08-01")]), freq="M")
        self.assertEqual(r["asof_quality"].iloc[0], "C")

    def test_price_same_day_A(self):
        r = resolve(pd.Series([T("2026-09-01")]), "price")
        self.assertEqual(r["available_at"].iloc[0], T("2026-09-01"))
        self.assertEqual(r["asof_quality"].iloc[0], "A")

    def test_google_trends_flag(self):
        r = resolve(pd.Series([T("2026-07-26")]), "google_trends")
        self.assertEqual(r["available_at"].iloc[0], T("2026-08-02"))
        self.assertEqual(r["asof_quality"].iloc[0], "C")
        self.assertEqual(r["asof_note"].iloc[0], "rescaled_at_collection")

    def test_available_never_before_period_end(self):
        pe = pd.Series(pd.date_range("2015-01-31", periods=100, freq="ME"))
        for name, r in RELEASE_RULES.items():
            freq = "M" if "by_freq" in r["lag"] and "M" in r["lag"]["by_freq"] else None
            if "by_freq" in r["lag"] and freq is None:
                continue
            out = resolve(pe, name, observed=pe + pd.Timedelta(days=3), freq=freq)
            self.assertTrue((out["available_at"] >= pe).all(), name)


class TestStampAndAsOf(unittest.TestCase):
    def _panel(self, seed=0, n=500):
        rng = np.random.default_rng(seed)
        pe = pd.Series(pd.date_range("2018-01-31", periods=n // 5, freq="ME")).repeat(5).reset_index(drop=True)
        df = pd.DataFrame({
            "key": np.tile(list("abcde"), n // 5),
            "period_end": pe,
            "freq": "M",
            "value": rng.normal(size=n),
            # 일부는 실시간, 일부는 백필, 일부는 비어 있음
            "first_collected_at": pe + pd.to_timedelta(rng.choice([5, 20, 3000], size=n), unit="D"),
        })
        df.loc[rng.choice(n, 30, replace=False), "first_collected_at"] = pd.NaT
        return stamp(df, "kosis")

    def test_stamp_adds_columns_without_mutating_input(self):
        df = pd.DataFrame({"period_end": [T("2026-01-31")], "freq": ["M"], "value": [1.0]})
        out = stamp(df, "kosis")
        self.assertNotIn("available_at", df.columns)
        for c in ["available_at", "asof_quality", "asof_note"]:
            self.assertIn(c, out.columns)

    def test_as_of_has_no_future_values_on_10_random_dates(self):
        df = self._panel()
        rng = np.random.default_rng(42)
        lo, hi = df["available_at"].min().value, df["available_at"].max().value
        dates = pd.to_datetime(rng.integers(lo, hi, size=10)).normalize()
        for d in dates:
            got = as_of(df, d)
            self.assertEqual(int((got["available_at"] > d).sum()), 0, str(d))
            # 걸러진 쪽에는 d 이전 값이 없어야 한다 (과하게 버리지 않음)
            dropped = df.loc[~df.index.isin(got.index)]
            self.assertEqual(int((dropped["available_at"] <= d).sum()), 0, str(d))

    def test_as_of_is_inclusive_and_drops_missing(self):
        df = pd.DataFrame({"available_at": [T("2026-01-10"), T("2026-01-11"), pd.NaT]})
        self.assertEqual(len(as_of(df, "2026-01-10")), 1)
        self.assertEqual(len(as_of(df, "2026-01-11 15:00")), 2)

    def test_quality_report(self):
        rep = quality_report(self._panel())
        self.assertEqual(list(rep.columns), ["key", "A", "B", "C", "n"])
        self.assertTrue(np.allclose(rep[["A", "B", "C"]].sum(axis=1), 1.0))


if __name__ == "__main__":
    unittest.main()
