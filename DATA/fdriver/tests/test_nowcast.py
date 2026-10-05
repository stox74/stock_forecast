# -*- coding: utf-8 -*-
"""2a 작업 3: nowcast 특징값·벤치마크·모델·기준일 테스트 (명세서 v3 9절 자동 테스트 포함)."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import asof
from DATA.fdriver.adapters import read_revenue
from DATA.fdriver.nowcast import benchmarks as bm
from DATA.fdriver.nowcast import features as ft
from DATA.fdriver.nowcast import models as md
from DATA.fdriver.nowcast import run
from .test_adapters import DB_OK

T = pd.Timestamp


def _monthly(values: dict, lag_days: int = 15) -> pd.DataFrame:
    """{월말: 값} -> 드라이버 긴 표 (available_at = 월말 + lag_days)."""
    pe = pd.to_datetime(list(values))
    df = pd.DataFrame({"key": "k", "item": "value", "period_end": pe, "value": list(values.values()),
                       "available_at": pe + pd.Timedelta(days=lag_days), "asof_quality": "C", "asof_note": "",
                       "freq": "M"})
    return ft.prepare_driver(df, 12)


def _spec(kind="M", lag=0):
    return ft.DriverSpec("M1:test", "test", kind, lag, loader=lambda: None)


class TestMonthPosition(unittest.TestCase):
    def test_positions(self):
        s = pd.Series(pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30"]))
        self.assertEqual(ft.month_position(s).tolist(), [0, 1, 2, 0])


class TestFeatures(unittest.TestCase):
    def setUp(self):
        months = pd.date_range("2022-01-31", "2024-03-31", freq="ME")
        self.drv = _monthly({m: 100.0 + i for i, m in enumerate(months)})

    def test_partial_quarter_uses_same_months_last_year(self):
        # t = 2024-03-20: 2024-01 (avail 02-15), 2024-02 (avail 03-15) 만 공개 -> 1~2월 합계 YoY
        f = ft.target_feature(_spec(), self.drv, T("2024-03-31"), T("2024-03-20"))
        self.assertEqual(f["positions"], [0, 1])
        self.assertAlmostEqual(f["coverage"], 2 / 3)
        v = self.drv.set_index("period_end")["value"]
        want = np.log(v[T("2024-01-31")] + v[T("2024-02-29")]) - np.log(v[T("2023-01-31")] + v[T("2023-02-28")])
        self.assertAlmostEqual(f["x"], want)
        self.assertTrue((f["inputs"]["available_at"] <= T("2024-03-20")).all())    # 시점 누출 없음

    def test_training_features_use_same_months(self):
        x = ft.training_features(_spec(), self.drv, [T("2023-06-30")], T("2024-03-20"), [0, 1])
        v = self.drv.set_index("period_end")["value"]
        want = np.log(v[T("2023-04-30")] + v[T("2023-05-31")]) - np.log(v[T("2022-04-30")] + v[T("2022-05-31")])
        self.assertAlmostEqual(x[T("2023-06-30")], want)
        full = ft.training_features(_spec(), self.drv, [T("2023-06-30")], T("2024-03-20"), [0, 1, 2])
        self.assertNotAlmostEqual(x[T("2023-06-30")], full[T("2023-06-30")])

    def test_no_month_means_no_feature(self):
        f = ft.target_feature(_spec(), self.drv, T("2024-03-31"), T("2024-02-10"))
        self.assertIsNone(f["x"])
        self.assertEqual(f["coverage"], 0.0)

    def test_lead_driver_uses_previous_quarter(self):
        f = ft.target_feature(_spec(lag=1), self.drv, T("2024-03-31"), T("2024-03-20"))
        self.assertEqual(f["driver_quarter"], T("2023-12-31"))
        self.assertEqual(f["positions"], [0, 1, 2])
        self.assertEqual(f["coverage"], 1.0)

    def test_daily_mean(self):
        days = pd.date_range("2023-01-01", "2024-03-31", freq="D")
        df = pd.DataFrame({"key": "k", "item": "v", "period_end": days, "value": np.where(days.year == 2024, 20.0, 10.0),
                           "available_at": days + pd.Timedelta(days=1), "asof_quality": "C", "asof_note": "", "freq": "D"})
        drv = ft.prepare_driver(df, 12)
        f = ft.target_feature(_spec(kind="D", lag=1), drv, T("2024-06-30"), T("2024-06-30"))
        self.assertAlmostEqual(f["x"], np.log(20.0) - np.log(10.0))
        self.assertAlmostEqual(f["coverage"], 1.0)


class TestBenchmarksModels(unittest.TestCase):
    def test_benchmarks(self):
        g = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5], index=pd.date_range("2023-03-31", periods=5, freq="QE"))
        b = bm.benchmarks(g)
        self.assertEqual(b["B1"]["g_hat"], 0.0)
        self.assertAlmostEqual(b["B2"]["g_hat"], 0.5)
        self.assertAlmostEqual(b["B3"]["g_hat"], np.mean([0.2, 0.3, 0.4, 0.5]))
        self.assertNotIn("B3", bm.benchmarks(g.iloc[:3]))

    def test_ols(self):
        idx = pd.date_range("2018-03-31", periods=20, freq="QE")
        x = pd.Series(np.linspace(-0.2, 0.3, 20), index=idx)
        y = 0.05 + 0.8 * x
        r = md.ols_predict(x, y, 0.1)
        self.assertAlmostEqual(r["g_hat"], 0.05 + 0.08)
        self.assertEqual(r["n_train"], 20)
        self.assertEqual(md.ols_predict(x.iloc[:11], y.iloc[:11], 0.1)["skipped"], "lt_min_train")
        self.assertEqual(md.ols_predict(x, y, None)["skipped"], "no_x")

    def test_combine(self):
        c = md.combine([{"g_hat": 0.1, "g_se": 0.2, "n_train": 20}, {"g_hat": 0.3, "g_se": 0.2, "n_train": 15}])
        self.assertAlmostEqual(c["g_hat"], 0.2)
        self.assertEqual(c["n_train"], 15)


class TestCheckpoints(unittest.TestCase):
    def test_skip_after_first_and_merge(self):
        cal = pd.bdate_range("2024-03-01", "2024-06-30").to_numpy(dtype="datetime64[ns]")
        cps = run.checkpoints(T("2024-03-31"), T("2024-04-05"), cal)
        # 2024-04-05 의 5거래일 전 = 2024-03-29, d15 이후는 첫 공시 뒤라 제외
        self.assertEqual(cps, [("pre5", T("2024-03-29")), ("qend", T("2024-03-31"))])
        cps2 = run.checkpoints(T("2024-03-31"), T("2024-05-24"), cal)     # d45 = 05-15, pre5 = 05-17
        self.assertEqual([c for c, _ in cps2], ["qend", "d15", "d30", "d45", "pre5"])
        cps3 = run.checkpoints(T("2024-03-31"), T("2024-05-10"), cal)     # d45(05-15) 는 첫 공시 뒤라 제외
        self.assertEqual([c for c, _ in cps3], ["qend", "d15", "d30", "pre5"])
        cal3 = pd.bdate_range("2024-03-01", "2024-06-30").to_numpy(dtype="datetime64[ns]")
        same = run.checkpoints(T("2024-03-31"), T("2024-04-22"), cal3)          # pre5 = 04-15 = d15
        self.assertIn(("d15+pre5", T("2024-04-15")), same)


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestNowcastOnDB(unittest.TestCase):
    """명세서 v3 9절 자동 테스트 (실제 DB, 표본 분기)."""
    CASES = {"005930": ["2016-06-30", "2019-03-31", "2024-03-31", "2025-06-30"],
             "039130": ["2017-09-30", "2024-12-31"],
             "267270": ["2023-03-31"], "278470": ["2025-09-30"], "006040": ["2024-06-30"]}

    @classmethod
    def setUpClass(cls):
        cls.res = {e: run.nowcast_entity(e, quarters=qs, audit=True) for e, qs in cls.CASES.items()}
        cls.rev = {e: read_revenue([e], vintage=True) for e in cls.CASES}

    def test_no_future_inputs(self):
        n = 0
        for e, r in self.res.items():
            for a in r["audit"]:
                for k in ("input_max_available", "train_input_max_available", "rev_max_available"):
                    if a.get(k) is not None and pd.notna(a.get(k)):
                        self.assertLessEqual(pd.Timestamp(a[k]), a["t"], (e, a["q"], a["model_id"], k))
                        n += 1
        self.assertGreater(n, 50)

    def test_training_quarters_announced_before_t(self):
        n = 0
        for e, r in self.res.items():
            act = ft.actuals(self.rev[e])
            for a in r["audit"]:
                for j in a["train_quarters"]:
                    self.assertLess(j, a["q"])
                    self.assertLessEqual(act.loc[j, "first_dt"], a["t"], (e, a["q"], a["model_id"], j))
                    base = ft.quarter_shift(j, 4)
                    self.assertLessEqual(act.loc[base, "first_dt"], a["t"])
                    n += 1
        self.assertGreater(n, 500)

    def test_training_and_target_month_composition_match(self):
        """삼성전자 pre5(1~2월만 공개)에서 학습 특징값이 같은 1~2월 합계로 계산됐는지 원자료로 재계산."""
        spec = ft.DRIVER_SPECS["005930"][0]
        drv = ft.prepare_driver(spec.loader(), 12)
        checked = 0
        for a in self.res["005930"]["audit"]:
            if a["model_id"] not in ("M1:854232", "M1:854232:excovid") or a["positions_target"] != [0, 1]:
                continue
            self.assertEqual(a["positions_train"], a["positions_target"])
            kn = ft.known_at(drv, a["t"])
            for j, x in a["x_train"].dropna().items():
                cur = kn[(kn["qend"] == j) & kn["pos"].isin([0, 1])]["value"].sum()
                prev = kn[(kn["qend"] == ft.quarter_shift(j, 4)) & kn["pos"].isin([0, 1])]["value"].sum()
                self.assertAlmostEqual(x, np.log(cur) - np.log(prev), places=10)
                checked += 1
        self.assertGreater(checked, 40)

    def test_b1_equals_known_prior_year_revenue(self):
        n = 0
        for e, r in self.res.items():
            rows = r["rows"][r["rows"]["model_id"] == "B1"]
            for row in rows.itertuples():
                t = pd.Timestamp(row.asof_date)
                q = ft.fiscal_quarter_end_from_label(row.fiscal_quarter) if hasattr(ft, "fiscal_quarter_end_from_label") \
                    else pd.Timestamp(int(row.fiscal_quarter[:4]), int(row.fiscal_quarter[-1]) * 3, 1) + pd.offsets.MonthEnd(0)
                known = asof.latest_known(asof.as_of(self.rev[e], t)).set_index("period_end")["value"]
                self.assertEqual(row.rev_hat, known[ft.quarter_shift(q, 4)])
                n += 1
        self.assertGreater(n, 20)

    def test_control_has_benchmarks_only(self):
        ids = set(self.res["006040"]["rows"]["model_id"])
        self.assertTrue(ids <= {"B1", "B2", "B3", "B1:excovid", "B2:excovid", "B3:excovid"})


if __name__ == "__main__":
    unittest.main()
