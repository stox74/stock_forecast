# -*- coding: utf-8 -*-
"""시범 분석 v2 (같은 분기 위약)와 OUTPUT_ROOT 테스트. DB 불필요."""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import config
from DATA.fdriver.pilots import pilot_analysis_v2 as p2


class TestOutputRoot(unittest.TestCase):
    def test_default_and_env_override(self):
        old = os.environ.pop("FDRIVER_OUTPUT_ROOT", None)
        try:
            self.assertEqual(config.output_root(), Path(config.DEFAULT_OUTPUT_ROOT))
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["FDRIVER_OUTPUT_ROOT"] = tmp
                p = config.output_path("stage1_pilot", "v2", "figures")
                self.assertEqual(p, Path(tmp) / "stage1_pilot" / "v2" / "figures")
                self.assertTrue(p.is_dir())
        finally:
            os.environ.pop("FDRIVER_OUTPUT_ROOT", None)
            if old is not None:
                os.environ["FDRIVER_OUTPUT_ROOT"] = old


def _synthetic(seed=0, T=40, short=12, n_plc=300):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-03-31", periods=T, freq="QE")
    rev = pd.Series(rng.normal(size=T), index=idx)
    drv = pd.Series(np.nan, index=idx)
    drv.iloc[-short:] = rev.iloc[-short:] + rng.normal(scale=0.3, size=short)   # 짧은 드라이버
    yy = pd.DataFrame({"revenue": rev, "short": drv})
    plc = pd.DataFrame(rng.normal(size=(T, n_plc)), index=idx, columns=[f"p{i:03d}" for i in range(n_plc)])
    plc.iloc[-3:, :50] = np.nan          # 앞의 50개 위약은 최근 3분기가 비어 있음
    return yy, plc


class TestSameQuarterPlacebo(unittest.TestCase):
    def test_placebo_uses_driver_quarters_and_fills_200(self):
        yy, plc = _synthetic()
        r = p2.evaluate(yy, ["short"], plc, lags=(0,)).iloc[0]
        self.assertEqual(r["n"], 12)
        self.assertEqual(r["trend_n"], 12)                 # 추세도 같은 분기
        self.assertEqual(r["n_placebo"], 200)              # 비어 있는 50개를 건너뛰고 200개 채움
        ok = yy["short"].notna()
        cols = [c for c in plc.columns if plc.loc[ok, c].notna().all()][:200]
        self.assertEqual(cols[0], "p050")
        manual = [np.corrcoef(yy.loc[ok, "revenue"], plc.loc[ok, c])[0, 1] for c in cols]
        self.assertAlmostEqual(r["placebo_p95"], float(np.quantile(manual, 0.95)), places=10)
        self.assertAlmostEqual(r["placebo_pct_abs"],
                               100 * float((np.abs(manual) <= abs(r["corr"])).mean()), places=10)

    def test_full_scope_uses_all_revenue_quarters(self):
        yy, plc = _synthetic()
        same = p2.evaluate(yy, ["short"], plc, lags=(0,), scope="same").iloc[0]
        full = p2.evaluate(yy, ["short"], plc, lags=(0,), scope="full").iloc[0]
        self.assertEqual(full["n"], same["n"])             # 드라이버 상관은 같음
        self.assertEqual(full["n_placebo"], 200)
        # 40분기 위약 분포가 12분기 위약 분포보다 좁다 -> 짧은 드라이버가 v1 방식에서 과대평가됨
        self.assertLess(full["placebo_p95"] - full["placebo_p05"], same["placebo_p95"] - same["placebo_p05"])

    def test_fewer_candidates_uses_maximum(self):
        yy, plc = _synthetic(n_plc=120)
        r = p2.evaluate(yy, ["short"], plc, lags=(0,)).iloc[0]
        self.assertEqual(r["n_placebo"], 70)               # 120 - 비어 있는 50

    def test_lag_shifts_placebo_too(self):
        yy, plc = _synthetic()
        r = p2.evaluate(yy, ["short"], plc, lags=(1,)).iloc[0]
        self.assertEqual(r["n"], 11)
        self.assertEqual(r["trend_n"], 11)

    def test_strong_flag_and_verdict(self):
        yy, plc = _synthetic()
        r = p2.evaluate(yy, ["short"], plc, lags=(0,)).iloc[0]
        self.assertGreaterEqual(r["placebo_pct_abs"], 97.5)   # 드라이버 = 매출 + 작은 잡음
        self.assertTrue(r["strong"])
        self.assertEqual(r["verdict"], "뚜렷")

    def test_short_sample_verdict(self):
        yy, plc = _synthetic(short=6)
        r = p2.evaluate(yy, ["short"], plc, lags=(0,)).iloc[0]
        self.assertEqual(r["verdict"], "표본 부족")
        self.assertFalse(r["strong"])


class TestTimingAdvantage(unittest.TestCase):
    def test_median_days(self):
        idx = pd.date_range("2025-03-31", periods=4, freq="QE")
        av = pd.DataFrame({"revenue": idx + pd.Timedelta(days=45), "drv": idx + pd.Timedelta(days=15)}, index=idx)
        av.loc[idx[-1], "revenue"] = idx[-1] + pd.Timedelta(days=90)
        self.assertEqual(p2.timing_advantage(av, "drv"), 30.0)
        self.assertTrue(np.isnan(p2.timing_advantage(av, "missing")))


if __name__ == "__main__":
    unittest.main()
