# -*- coding: utf-8 -*-
"""2a 작업 5~7: 신호 분해, 분모 시점, CAR 구간, 컨센서스 경로 테스트 (명세서 v6 6~8절)."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import db
from DATA.fdriver.adapters import consensus as cs
from DATA.fdriver.evaluate import event_study as es
from DATA.fdriver.signals import expectation as ex
from DATA.fdriver.signals import sur
from .test_adapters import DB_OK

T = pd.Timestamp


def _ctx(values: dict, lag_days: int = 30, extra: list = ()) -> ex.Context:
    """합성 매출 vintage. values: {분기말: 값}, extra: [(분기말, 값, available_at)] 개정 행."""
    rows = [{"key": "X", "item": "revenue", "period_end": T(k), "value": v,
             "available_at": T(k) + pd.Timedelta(days=lag_days), "asof_note": ""} for k, v in values.items()]
    rows += [{"key": "X", "item": "revenue", "period_end": T(k), "value": v, "available_at": T(a), "asof_note": ""}
             for k, v, a in extra]
    c = object.__new__(ex.Context)
    c.entity_id, c.rev_v, c.excl, c.fye, c._known = "X", pd.DataFrame(rows), set(), 12, {}
    return c


QS = pd.date_range("2015-03-31", "2020-12-31", freq="QE")
VALS = {q: 100.0 + 7 * i + (5 if q.month == 12 else 0) + (i % 3) for i, q in enumerate(QS)}


class TestDenominators(unittest.TestCase):
    q, t = T("2020-12-31"), T("2020-11-15")       # q-1 = 2020Q3 공표 2020-10-30

    def _manual(self, vals, quarters):
        d = [vals[j] - vals[j - pd.offsets.QuarterEnd(4)] for j in quarters]
        return float(np.mean(d)), float(np.std(d, ddof=1))

    def test_window_and_values(self):
        r = ex.denominators(_ctx(VALS), self.q, self.t)
        js = [self.q - pd.offsets.QuarterEnd(k) for k in range(8, 0, -1)]
        self.assertEqual(r["hist_quarters"], js)
        m, s = self._manual(VALS, js)
        self.assertAlmostEqual(r["delta"], m)
        self.assertAlmostEqual(r["sigma"], s)
        self.assertEqual(r["n_hist"], 8)
        self.assertTrue(all(j < self.q for j in r["hist_quarters"]))
        self.assertLessEqual(r["max_available"], self.t)

    def test_only_quarters_announced_by_t(self):
        # q-1 이 t 뒤에 발표되면 빠진다
        r = ex.denominators(_ctx(VALS), self.q, T("2020-10-29"))
        self.assertEqual(r["n_hist"], 7)
        self.assertNotIn(T("2020-09-30"), r["hist_quarters"])
        # t 뒤에 나온 개정치는 쓰지 않는다
        rev = _ctx(VALS, extra=[("2020-06-30", 9999.0, "2020-12-01")])
        self.assertAlmostEqual(ex.denominators(rev, self.q, self.t)["delta"],
                               ex.denominators(_ctx(VALS), self.q, self.t)["delta"])
        self.assertNotAlmostEqual(ex.denominators(rev, self.q, T("2020-12-02"))["delta"],
                                  ex.denominators(_ctx(VALS), self.q, T("2020-12-02"))["delta"])
        # 창 밖(q-13) 값은 영향 없음
        v2 = dict(VALS)
        v2[T("2017-09-30")] = 1e6
        self.assertAlmostEqual(ex.denominators(_ctx(v2), self.q, self.t)["delta"],
                               ex.denominators(_ctx(VALS), self.q, self.t)["delta"])

    def test_min_hist_and_exclusions(self):
        c = _ctx(VALS)
        c.excl = {T("2020-03-31"), T("2020-06-30"), T("2020-09-30")}
        r = ex.denominators(c, self.q, self.t)
        self.assertEqual(r["n_hist"], 5)
        self.assertTrue(np.isnan(r["delta"]) and np.isnan(r["sigma"]))

    def test_surprise_level_and_log(self):
        e = {"expected": 110.0, "sigma": 5.0, "base": 100.0}
        self.assertAlmostEqual(ex.surprise(120.0, e, "level"), 2.0)
        el = {"expected": 0.05, "sigma": 0.1, "base": 100.0}
        self.assertAlmostEqual(ex.surprise(110.0, el, "log"), (np.log(1.1) - 0.05) / 0.1)


class TestCar(unittest.TestCase):
    def setUp(self):
        self.cal = pd.bdate_range("2024-01-01", "2024-03-29")
        self.ex = pd.Series(0.01, index=self.cal)
        self.ex[T("2024-01-10")] = np.nan

    def _cars(self, signal, event, first=None, last="2024-03-29"):
        pos = es.window_positions(self.cal, signal, event)
        return {k: es.car(self.ex, self.cal, a, b, T(last), first) for k, (a, b) in pos.items()}

    def test_boundaries(self):
        c = self._cars(T("2024-01-05"), T("2024-01-20"))           # 금요일 신호, 토요일 공시 -> day0 = 1/22 (월)
        self.assertEqual(c["trade"]["start"], T("2024-01-08"))     # 신호일 다음 거래일
        self.assertEqual(c["trade"]["end"], T("2024-01-23"))       # day +1
        n = len(pd.bdate_range("2024-01-08", "2024-01-23"))
        self.assertEqual(c["trade"]["n_missing"], 1)               # 1/10 결측은 건너뛰고 셈
        self.assertEqual(c["trade"]["n_obs"], n - 1)
        self.assertAlmostEqual(c["trade"]["car"], 0.01 * (n - 1))
        self.assertEqual(c["pre"]["end"], T("2024-01-18"))         # day -2
        self.assertEqual((c["ann"]["start"], c["ann"]["end"]), (T("2024-01-22"), T("2024-01-23")))
        self.assertAlmostEqual(c["ann"]["car"], 0.02)
        self.assertEqual(c["drift"]["start"], T("2024-01-24"))
        self.assertTrue(np.isnan(c["drift"]["car"]))               # day +60 이 자료 끝 뒤 -> 미완료

    def test_signal_on_holiday_and_empty_pre(self):
        c = self._cars(T("2024-01-07"), T("2024-01-22"))           # 일요일 신호 -> 1/8 부터
        self.assertEqual(c["trade"]["start"], T("2024-01-08"))
        c2 = self._cars(T("2024-01-18"), T("2024-01-22"))          # 사전 구간 1/19 ~ 1/18 -> 빈 구간
        self.assertTrue(np.isnan(c2["pre"]["car"]))
        self.assertEqual(c2["trade"]["start"], T("2024-01-19"))

    def test_before_data(self):
        c = self._cars(T("2024-01-05"), T("2024-01-20"), first=T("2024-01-15"))
        self.assertTrue(np.isnan(c["trade"]["car"]))
        self.assertEqual(c["trade"]["n_missing"], 0)
        self.assertAlmostEqual(c["ann"]["car"], 0.02)               # 상장 뒤 구간은 그대로

    def test_fe_cluster_matches_dummy_ols(self):
        rng = np.random.default_rng(3)
        d = pd.DataFrame({"entity_id": np.repeat(["a", "b", "c"], 20), "fiscal_quarter": np.tile([f"q{i}" for i in range(20)], 3),
                          "x": rng.normal(size=60)})
        d["y"] = 2 * d["x"] + d["entity_id"].map({"a": 0, "b": 1, "c": -1}) + rng.normal(scale=0.1, size=60)
        r = es.fe_cluster_ols(d, "y", ["x"])
        X = np.column_stack([d["x"], pd.get_dummies(d["entity_id"]).to_numpy(dtype=float)])
        b, *_ = np.linalg.lstsq(X, d["y"].to_numpy(), rcond=None)
        self.assertAlmostEqual(r["coef"].loc["x", "coef"], b[0])
        self.assertEqual(r["n_clusters"], 20)


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestSignalsOnDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = sur.build_signals("267270", audit=True)
        cls.rows = cls.res["rows"]

    def test_mom_plus_drv_equals_nsur(self):
        m4 = self.rows[self.rows["model_id"].str.startswith("M4")].dropna(subset=["nsur"])
        self.assertGreater(len(m4), 20)
        np.testing.assert_allclose(m4["nsur"], m4["nsur_mom"] + m4["nsur_drv"], atol=1e-10)
        b2 = self.rows[self.rows["model_id"] == "B2"].dropna(subset=["nsur"])
        np.testing.assert_allclose(b2["nsur_drv"], 0.0, atol=1e-12)
        np.testing.assert_allclose(b2["nsur"], b2["nsur_mom"], atol=1e-12)

    def test_denominator_uses_prior_announced_only(self):
        n = 0
        for a in self.res["audit"]:
            self.assertTrue(all(j < a["q"] for j in a["hist_quarters"]))
            if a["max_available"] is not None and pd.notna(a["max_available"]):
                self.assertLessEqual(pd.Timestamp(a["max_available"]), pd.Timestamp(a["t"]))
                n += 1
        self.assertGreater(n, 50)
        self.assertEqual(self.res["base_mismatch"], 0)

    def test_same_denominator_for_sur_and_nsur(self):
        r = self.rows.dropna(subset=["sigma"])
        self.assertEqual(r.groupby(["fiscal_quarter", "asof_date", "variant"])["sigma"].nunique().max(), 1)


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestConsensus(unittest.TestCase):
    SRC = "unittest_fake"

    @classmethod
    def setUpClass(cls):
        cs.create_table()

    def _count(self):
        return int(db.read_sql("SELECT COUNT(*) n FROM fd_consensus WHERE source = :s", {"s": self.SRC})["n"].iloc[0])

    def test_empty_returns_empty_frame(self):
        if int(db.read_sql("SELECT COUNT(*) n FROM fd_consensus")["n"].iloc[0]) == 0:
            r = cs.read_consensus(["005930"])
            self.assertTrue(r.empty)
            self.assertEqual(list(r.columns), cs.COLUMNS)
        self.assertTrue(cs.read_consensus([]).empty)

    def test_fake_rows_path_and_delete(self):
        nc = sur.read_nowcast("005930")
        nc = nc[nc["fiscal_quarter"] == "2025Q4"]
        t = T(nc.loc[nc["checkpoint"].str.contains("pre5"), "asof_date"].iloc[0])
        fake = pd.DataFrame([{"entity_id": "005930", "fiscal_quarter": "2025Q4", "item": "revenue",
                              "asof_date": (t - pd.Timedelta(days=d)).date(), "mean": m, "median": m, "high": m, "low": m,
                              "n_analysts": 5, "source": self.SRC} for d, m in ((20, 8.0e13), (1, 9.0e13))]
                            + [{"entity_id": "005930", "fiscal_quarter": "2025Q4", "item": "revenue",
                                "asof_date": (t + pd.Timedelta(days=1)).date(), "mean": 1.0e14, "median": 1.0e14,
                                "high": 1.0e14, "low": 1.0e14, "n_analysts": 5, "source": self.SRC}])
        try:
            db.write_fd(fake, cs.T_CONSENSUS, ["entity_id", "fiscal_quarter", "item", "asof_date", "source"])
            c = cs.read_consensus(["005930"], asof=t)
            c = c[c["source"] == self.SRC]
            self.assertEqual(c["mean"].iloc[0], 9.0e13)                     # t 이전 최신, t 뒤 스냅샷은 안 씀
            self.assertEqual(c["asof_quality"].iloc[0], "A")
            self.assertEqual(c["available_at"].iloc[0], c["asof_date"].iloc[0])
            e = ex.expectation("005930", T("2025-12-31"), t, kind="consensus")
            self.assertEqual(e["expected"], 9.0e13)
            rows = sur.build_signals("005930", nc=nc, kind="consensus")["rows"]
            self.assertTrue((rows["expectation"] == "consensus").all())
            r = rows[(rows["model_id"] == "M4:combo") & (rows["variant"] == "level") & rows["checkpoint"].str.contains("pre5")]
            self.assertAlmostEqual(r["sur"].iloc[0], (float(nc["rev_actual_first"].iloc[0]) - 9.0e13) / e["sigma"])
        finally:
            db.delete_fd(cs.T_CONSENSUS, {"source": [self.SRC]})
        self.assertEqual(self._count(), 0)


if __name__ == "__main__":
    unittest.main()
