# -*- coding: utf-8 -*-
"""2a 작업 4b: M4 (B2 출발점 + 드라이버 보정) 테스트 (명세서 v5 4절)."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver.adapters import read_revenue
from DATA.fdriver.nowcast import benchmarks as bm
from DATA.fdriver.nowcast import features as ft
from DATA.fdriver.nowcast import models as md
from DATA.fdriver.nowcast import run
from .test_adapters import DB_OK

T = pd.Timestamp


def _qs(n, start="2015-03-31"):
    return pd.date_range(start, periods=n, freq="QE")


class TestOriginOLS(unittest.TestCase):
    def test_known_example(self):
        """x = [1,2,3], y = [2,4,7]: b = 31/14, 잔차 (-3,-6,5)/14, SSR = 70/196, s2 = SSR/2."""
        r = md.ols_origin(np.array([1.0, 2, 3]), np.array([2.0, 4, 7]))
        self.assertAlmostEqual(r["b"], 31 / 14)
        self.assertAlmostEqual(r["s2"], (70 / 196) / 2)
        self.assertAlmostEqual(r["sxx"], 14.0)

    def test_matches_lstsq_without_intercept(self):
        rng = np.random.default_rng(0)
        x, y = rng.normal(size=30), rng.normal(size=30)
        b, *_ = np.linalg.lstsq(x.reshape(-1, 1), y, rcond=None)
        self.assertAlmostEqual(md.ols_origin(x, y)["b"], float(b[0]))


class TestM4(unittest.TestCase):
    def setUp(self):
        q = _qs(20)
        rng = np.random.default_rng(1)
        self.x = pd.Series(rng.normal(size=20), index=q)
        self.g = pd.Series(0.05 + 0.5 * self.x.values + rng.normal(scale=0.01, size=20), index=q)

    def test_b_zero_equals_b2(self):
        r = md.m4_predict(self.g, self.x, 0.7, b_fixed=0.0)
        self.assertEqual(r["g_hat"], bm.benchmarks(self.g)["B2"]["g_hat"])
        self.assertEqual(r["lag_q"], bm.benchmarks(self.g)["B2"]["train_quarters"][0])

    def test_pairs_use_b2_quarter_with_gaps(self):
        g = self.g.drop(self.g.index[[5, 6]])              # 제외 분기가 있으면 lag 는 그 앞 분기
        p = md.m4_pairs(g, self.x)
        j = g.index[5]
        self.assertEqual(p.loc[j, "lag_q"], g.index[4])
        self.assertAlmostEqual(p.loc[j, "dy"], g[j] - g[g.index[4]])
        self.assertAlmostEqual(p.loc[j, "dx"], self.x[j] - self.x[g.index[4]])
        self.assertEqual(len(p), len(g) - 1)

    def test_estimate_and_min_train(self):
        r = md.m4_predict(self.g, self.x, 0.3)
        p = md.m4_pairs(self.g, self.x)
        b = float(p["dx"] @ p["dy"] / (p["dx"] @ p["dx"]))
        self.assertAlmostEqual(r["b"], b)
        self.assertAlmostEqual(r["g_hat"], self.g.iloc[-1] + b * (0.3 - self.x.iloc[-1]))
        self.assertEqual(md.m4_predict(self.g.iloc[:12], self.x, 0.3)["skipped"], "lt_min_train")   # 11쌍
        self.assertIn("g_hat", md.m4_predict(self.g.iloc[:13], self.x, 0.3))                       # 12쌍
        self.assertEqual(md.m4_predict(self.g, self.x, None)["skipped"], "no_x")

    def test_missing_x_lag(self):
        x = self.x.copy()
        x[self.g.index[-1]] = np.nan
        self.assertEqual(md.m4_predict(self.g, x, 0.3)["skipped"], "no_x")


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestM4OnDB(unittest.TestCase):
    CASES = {"005930": ["2016-06-30", "2019-03-31", "2024-03-31", "2025-06-30"],
             "039130": ["2017-09-30", "2019-06-30", "2024-12-31"],
             "267270": ["2023-03-31"], "278470": ["2025-09-30"], "006040": ["2024-06-30"]}

    @classmethod
    def setUpClass(cls):
        cls.res = {e: run.nowcast_entity(e, quarters=qs, audit=True) for e, qs in cls.CASES.items()}
        cls.rev = {e: read_revenue([e], vintage=True) for e in cls.CASES}
        cls.m4 = [(e, a) for e, r in cls.res.items() for a in r["audit"] if a["model_id"].startswith("M4:")]

    def test_m4_present_and_not_for_controls(self):
        self.assertGreater(len(self.m4), 30)
        self.assertFalse(any(m.startswith("M4") for m in self.res["006040"]["rows"]["model_id"]))
        ids = set(self.res["005930"]["rows"]["model_id"])
        self.assertTrue({"M4:854232", "M4:sk_hynix", "M4:combo", "M4:combo:excovid"} <= ids)
        self.assertFalse(any(m.startswith("M4:") and ":krw" in m for m in ids))

    def test_lag_is_b2_quarter_and_rows_reproduce(self):
        for e, a in self.m4:
            self.assertEqual(a["lag_q"], a["b2_quarter"], (e, a["q"], a["model_id"]))
            self.assertEqual(a["g_lag"], a["b2_g_hat"])
            rows = self.res[e]["rows"]
            r = rows[(rows["model_id"] == a["model_id"]) & (rows["asof_date"] == a["t"].date())].iloc[0]
            self.assertAlmostEqual(r["g_hat"], a["g_lag"] + a["b"] * (a["x_q"] - a["x_lag"]))

    def test_x_lag_same_quarter_and_months(self):
        """x_lag 를 원자료로 재계산: g_lag 분기(선행 드라이버면 한 분기 앞)의 같은 월 위치 합계 YoY."""
        specs = {run.m4_id(s): s for e in ft.DRIVER_SPECS for s in ft.DRIVER_SPECS[e] if not s.alt}
        drv_cache, n = {}, 0
        for e, a in self.m4:
            name = a["model_id"].replace(":excovid", "")
            if name == "M4:combo":
                continue
            s = specs[name]
            self.assertEqual(a["lag_driver_quarter"], ft.quarter_shift(a["lag_q"], s.lag))
            if s.kind != "M":
                continue
            if s.model_id not in drv_cache:
                drv_cache[s.model_id] = ft.prepare_driver(s.loader(), 12)
            kn = ft.known_at(drv_cache[s.model_id], a["t"])
            dq, pos = a["lag_driver_quarter"], a["positions_target"]
            cur = kn[(kn["qend"] == dq) & kn["pos"].isin(pos)]["value"].sum()
            prev = kn[(kn["qend"] == ft.quarter_shift(dq, 4)) & kn["pos"].isin(pos)]["value"].sum()
            self.assertAlmostEqual(a["x_lag"], np.log(cur) - np.log(prev), places=10)
            self.assertEqual(a["positions_train"], a["positions_target"])
            n += 1
        self.assertGreater(n, 20)

    def test_no_future_inputs_m4(self):
        n = 0
        for e, a in self.m4:
            for k in ("input_max_available", "train_input_max_available", "rev_max_available"):
                if a.get(k) is not None and pd.notna(a.get(k)):
                    self.assertLessEqual(pd.Timestamp(a[k]), a["t"], (e, a["q"], a["model_id"], k))
                    n += 1
        self.assertGreater(n, 50)

    def test_training_quarters_announced_before_t_m4(self):
        n = 0
        for e, a in self.m4:
            act = ft.actuals(self.rev[e])
            for j in a["train_quarters"]:
                self.assertLess(j, a["q"])
                self.assertLessEqual(act.loc[j, "first_dt"], a["t"], (e, a["q"], a["model_id"], j))
                self.assertLessEqual(act.loc[ft.quarter_shift(j, 4), "first_dt"], a["t"])
                n += 1
            self.assertLessEqual(act.loc[a["lag_q"], "first_dt"], a["t"])
        self.assertGreater(n, 300)


if __name__ == "__main__":
    unittest.main()
