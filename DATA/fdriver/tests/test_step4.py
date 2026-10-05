# -*- coding: utf-8 -*-
"""2a 작업 4: 기업 사건 제외, 원화 환산 시점, DM 검정, 관광 공표 일정표 테스트 (명세서 v4)."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import asof
from DATA.fdriver.config import CORPORATE_EVENT_EXCLUDE, RELEASE_CALENDARS
from DATA.fdriver.evaluate import forecast_eval as fe
from DATA.fdriver.nowcast import features as ft
from DATA.fdriver.nowcast import run
from .test_adapters import DB_OK

T = pd.Timestamp


class TestDMTest(unittest.TestCase):
    def test_known_example(self):
        """손으로 계산한 예제: e1 = [1,2,3,1,2,3,1,2], e2 = 0 -> d = [1,4,9,1,4,9,1,4].
        mean 4.125, gamma0 = 76.875/8 = 9.609375, DM = 4.125/sqrt(9.609375/8) = 3.763755,
        HLN 보정 sqrt((8+1-2)/8) = 0.935414 -> 3.520670, t(7) 양측 p = 0.009719."""
        r = fe.dm_test([1, 2, 3, 1, 2, 3, 1, 2], [0] * 8)
        self.assertEqual(r["n"], 8)
        self.assertAlmostEqual(r["mean_d"], 4.125)
        self.assertAlmostEqual(r["dm"], 4.125 / np.sqrt(9.609375 / 8), places=10)
        self.assertAlmostEqual(r["dm"], 3.763755, places=5)
        self.assertAlmostEqual(r["stat"], 3.520670, places=5)
        self.assertAlmostEqual(r["p_two"], 0.009719, places=5)
        self.assertAlmostEqual(r["p_less"], 1 - 0.009719 / 2, places=5)

    def test_symmetry_and_equal_forecasts(self):
        a, b = [0.1, -0.3, 0.2, 0.05, -0.1, 0.4], [0.2, 0.1, -0.1, 0.3, 0.0, -0.2]
        r1, r2 = fe.dm_test(a, b), fe.dm_test(b, a)
        self.assertAlmostEqual(r1["stat"], -r2["stat"])
        self.assertAlmostEqual(r1["p_two"], r2["p_two"])
        self.assertTrue(np.isnan(fe.dm_test(a, a)["stat"]))      # 분산 0 이면 검정 불가

    def test_h2_uses_lag1_autocovariance(self):
        e1, e2 = np.array([1.0, 2, 0, 3, 1, 2, 2, 0, 1, 3]), np.zeros(10)
        d = e1 ** 2
        n, dc = len(d), d - d.mean()
        g0, g1 = np.sum(dc * dc) / n, np.sum(dc[1:] * dc[:-1]) / n
        dm = d.mean() / np.sqrt((g0 + 2 * g1) / n)
        k = np.sqrt((n + 1 - 4 + 2 / n) / n)
        self.assertAlmostEqual(fe.dm_test(e1, e2, h=2)["stat"], dm * k)


class TestModelIdParse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(fe.parse_model_id("M1:854232:krw:excovid"),
                         {"model": "M1:854232:krw", "base": "M1:854232", "alt": "krw", "variant": "excovid"})
        self.assertEqual(fe.parse_model_id("M2"), {"model": "M2", "base": "M2", "alt": None, "variant": "all"})
        self.assertEqual(fe.parse_model_id("M2:krw")["base"], "M2")


class TestKrwMonthly(unittest.TestCase):
    def _daily(self):
        days = pd.bdate_range("2024-01-01", "2024-03-31")
        return pd.DataFrame({"key": "DEXKOUS", "item": "value", "freq": "D", "period_end": days,
                             "value": np.arange(len(days), dtype=float) + 1000.0,
                             "available_at": days + pd.Timedelta(days=3), "asof_quality": "A"})

    def test_available_after_month_end_and_all_days(self):
        d = self._daily()
        m = ft.fx_monthly_avg(d)
        for _, r in m.iterrows():
            days = d[(d["period_end"] + pd.offsets.MonthEnd(0)) == r["period_end"]]
            self.assertGreater(r["available_at"], r["period_end"])                 # 월말 다음 날 이후
            self.assertGreaterEqual(r["available_at"], days["available_at"].max())  # 모든 일별 값 공표 후
            self.assertAlmostEqual(r["value"], days["value"].mean())

    def test_no_value_after_t(self):
        """t 시점에 아직 공표 안 된 일별 값을 바꿔도 t 시점 월평균(알려진 값)은 변하지 않는다."""
        d = self._daily()
        m1 = ft.fx_monthly_avg(d)
        t = T("2024-03-02")                     # 2월 마지막 일별 값(2/29) 공표일 = 3/3 -> 2월 평균은 아직 모름
        known1 = asof.as_of(m1, t)
        self.assertNotIn(T("2024-02-29"), set(known1["period_end"]))
        d2 = d.copy()
        d2.loc[d2["available_at"] > t, "value"] *= 10
        known2 = asof.as_of(ft.fx_monthly_avg(d2), t)
        pd.testing.assert_frame_equal(known1.reset_index(drop=True), known2.reset_index(drop=True))
        self.assertEqual(set(asof.as_of(m1, T("2024-03-03"))["period_end"]), {T("2024-01-31"), T("2024-02-29")})

    def test_to_krw_takes_later_date(self):
        usd = pd.DataFrame({"key": "854232", "item": "expDlr", "freq": "M", "period_end": [T("2024-01-31")],
                            "value": [2.0], "unit": "USD", "currency": "USD", "available_at": [T("2024-02-01")],
                            "asof_quality": "C", "asof_note": ""})
        fx = ft.fx_monthly_avg(self._daily())
        k = ft.to_krw(usd, fx)
        self.assertEqual(k["available_at"].iloc[0], fx["available_at"].iloc[0])     # 환율 쪽이 늦음
        self.assertAlmostEqual(k["value"].iloc[0], 2.0 * fx["value"].iloc[0])
        self.assertEqual(k["currency"].iloc[0], "KRW")
        self.assertEqual(k["asof_quality"].iloc[0], "C")                            # 낮은 등급

    @unittest.skipUnless(DB_OK, "DB not reachable")
    def test_db_monthly_never_before_inputs(self):
        daily = ft.read_series("fred", [ft.FX_SERIES], start="2010-01-01")
        m = ft.fx_monthly_avg(daily)
        daily["me"] = pd.to_datetime(daily["period_end"]) + pd.offsets.MonthEnd(0)
        mx = daily.groupby("me")["available_at"].max()
        self.assertTrue((m.set_index("period_end")["available_at"] >= mx.reindex(m["period_end"]).values).all())
        self.assertTrue((m["available_at"] > m["period_end"]).all())


class TestTourismCalendar(unittest.TestCase):
    def test_calendar_hit_and_fallback(self):
        pe = pd.Series([T("2020-07-31"), T("2017-06-30"), T("2024-07-31")])
        r = asof.resolve(pe, "tourism", freq="M")
        self.assertEqual(r["available_at"].tolist(), [T("2020-09-14"), T("2017-08-04"), T("2024-08-29")])
        self.assertEqual(r["asof_quality"].tolist(), ["B", "C", "B"])

    def test_calendar_not_before_period_end(self):
        for k, v in RELEASE_CALENDARS["tourism_kcti"].items():
            self.assertGreater(T(v), T(k + "-01") + pd.offsets.MonthEnd(0), k)


class TestCorporateEvents(unittest.TestCase):
    def test_config(self):
        self.assertIn("267270", CORPORATE_EVENT_EXCLUDE)
        self.assertIn("006040", CORPORATE_EVENT_EXCLUDE)
        q = pd.date_range("2025-03-31", "2027-03-31", freq="QE")
        got = ft.corporate_event_quarters("267270", q)
        self.assertEqual(got, set(pd.date_range("2026-03-31", "2026-12-31", freq="QE")))


@unittest.skipUnless(DB_OK, "DB not reachable")
class TestCorporateEventsOnDB(unittest.TestCase):
    """기업 사건 제외 분기가 모든 모델(벤치마크 포함)의 예측 대상과 학습 분기에서 빠졌는지."""

    @classmethod
    def setUpClass(cls):
        cls.res = {e: run.nowcast_entity(e, audit=True) for e in CORPORATE_EVENT_EXCLUDE}

    def test_excluded_quarters_absent_everywhere(self):
        from DATA.fdriver.events import fiscal_quarter_label
        for e, spans in CORPORATE_EVENT_EXCLUDE.items():
            r = self.res[e]
            ex = set()
            for a, b, _ in spans:
                ex |= set(pd.date_range(a, b, freq="QE"))
            labels = {fiscal_quarter_label(q, 12) for q in ex}
            self.assertFalse(set(r["rows"]["fiscal_quarter"]) & labels, e)
            models = set(r["rows"]["model_id"])
            self.assertTrue({"B1", "B2", "B3"} <= models, e)
            n = 0
            for a in r["audit"]:
                self.assertFalse(set(a["train_quarters"]) & ex, (e, a["model_id"], a["q"]))
                n += 1
            self.assertGreater(n, 50)

    def test_db_has_no_excluded_rows(self):
        from DATA.fdriver import db
        from DATA.fdriver.events import fiscal_quarter_label
        for e, spans in CORPORATE_EVENT_EXCLUDE.items():
            labels = []
            for a, b, _ in spans:
                labels += [fiscal_quarter_label(q, 12) for q in pd.date_range(a, b, freq="QE")]
            frag, p = db.in_clause("q", labels)
            n = db.read_sql(f"SELECT COUNT(*) n FROM fd_nowcast WHERE entity_id = :e AND fiscal_quarter IN {frag}",
                            {"e": e, **p})["n"].iloc[0]
            self.assertEqual(int(n), 0, e)


if __name__ == "__main__":
    unittest.main()
