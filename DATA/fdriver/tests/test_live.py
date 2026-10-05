# -*- coding: utf-8 -*-
"""실전 사전 기록 (fdriver/live.py) 테스트: 덮어쓰기 차단, 시점 누출, 2a 경로 재현."""
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import db, live
from DATA.fdriver.nowcast import features as ft
from DATA.fdriver.nowcast import run
from .test_adapters import DB_OK

T = pd.Timestamp
TMP = "fd_tmp_live_strict_test"


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestStrictInsert(unittest.TestCase):
    def setUp(self):
        db.execute_fd_ddl(f"CREATE TABLE IF NOT EXISTS `{TMP}` (`k1` VARCHAR(10) NOT NULL, `k2` DATE NOT NULL, "
                          f"`v` DOUBLE NULL, PRIMARY KEY (`k1`, `k2`)) ENGINE=InnoDB")

    def tearDown(self):
        db.execute_fd_ddl(f"DROP TABLE IF EXISTS `{TMP}`")

    def test_same_pk_is_refused_and_value_kept(self):
        a = pd.DataFrame({"k1": ["x", "y"], "k2": [T("2026-10-05").date()] * 2, "v": [1.0, 2.0]})
        self.assertEqual(db.insert_fd_strict(a, TMP, ["k1", "k2"]), 2)
        b = pd.DataFrame({"k1": ["y", "z"], "k2": [T("2026-10-05").date()] * 2, "v": [99.0, 3.0]})
        with self.assertRaises(db.FdWriteViolation):
            db.insert_fd_strict(b, TMP, ["k1", "k2"])
        cur = db.read_sql(f"SELECT * FROM `{TMP}` ORDER BY k1")
        self.assertEqual(cur["k1"].tolist(), ["x", "y"])            # z 도 쓰이지 않음 (전부 아니면 전무)
        self.assertEqual(cur["v"].tolist(), [1.0, 2.0])             # y 는 덮어쓰이지 않음
        with self.assertRaises(db.FdWriteViolation):                # 프레임 안 중복도 막음
            db.insert_fd_strict(pd.concat([a.iloc[:1].assign(k1="w")] * 2), TMP, ["k1", "k2"])

    def test_only_fd_tables(self):
        with self.assertRaises(db.FdWriteViolation):
            db.insert_fd_strict(pd.DataFrame({"a": [1]}), "KSE_Price", ["a"])


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestLiveInputs(unittest.TestCase):
    T_LIVE = T("2026-10-05")

    @classmethod
    def setUpClass(cls):
        cls.res = {e: run.nowcast_entity(e, quarters=[], live={T("2026-09-30"): cls.T_LIVE}, audit=True)
                   for e in ["039130", "278470"]}

    def test_no_input_after_t(self):
        n = 0
        for e, r in self.res.items():
            self.assertFalse(r["rows"].empty)
            self.assertTrue((r["rows"]["checkpoint"] == "live").all())
            for a in r["audit"]:
                for k in ("input_max_available", "train_input_max_available", "rev_max_available"):
                    if a.get(k) is not None and pd.notna(a.get(k)):
                        self.assertLessEqual(pd.Timestamp(a[k]), self.T_LIVE, (e, a["model_id"], k))
                        n += 1
        self.assertGreater(n, 30)

    def test_build_checks_signal_inputs(self):
        b = live.build(["278470"], "2026-09-30", self.T_LIVE, "test")
        self.assertGreater(b["n_checked"], 5)
        self.assertTrue((b["rows"]["code_version"] == "test").all())
        m4 = b["rows"][b["rows"]["model_id"] == "M4:amazon"].iloc[0]
        self.assertAlmostEqual(m4["nsur"], m4["nsur_mom"] + m4["nsur_drv"])

    def test_lead_driver_uses_previous_quarter_only(self):
        """에이피알 M3·M4 (1분기 선행) 입력은 2026Q2 이전 값만."""
        spec = [s for s in ft.DRIVER_SPECS["278470"] if s.model_id == "M3:amazon"][0]
        drv = ft.prepare_driver(spec.loader(), 12)
        f = ft.target_feature(spec, drv, T("2026-09-30"), self.T_LIVE)
        self.assertEqual(f["driver_quarter"], T("2026-06-30"))
        self.assertLessEqual(f["inputs"]["period_end"].max(), T("2026-06-30"))

    def test_skip_excluded_and_known_quarters(self):
        r = run.nowcast_entity("267270", quarters=[], live={T("2026-09-30"): self.T_LIVE})
        self.assertTrue(r["rows"].empty)
        self.assertEqual(r["skips"]["reason"].tolist(), ["target_excluded"])
        r2 = run.nowcast_entity("039130", quarters=[], live={T("2026-06-30"): self.T_LIVE})
        self.assertTrue(r2["rows"].empty)                                   # 이미 실적이 있는 분기
        self.assertEqual(r2["skips"]["reason"].tolist(), ["live_has_actual"])


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestLiveReproduces2a(unittest.TestCase):
    def test_live_path_equals_pre5_rows(self):
        """과거 분기를 '아직 실적이 없는 것처럼' live 로 돌리면 2a 의 pre5 행과 같다."""
        e, q = "039130", T("2025-09-30")
        ref = run.nowcast_entity(e, quarters=[q])["rows"]
        ref = ref[ref["checkpoint"].str.contains("pre5")]
        t = T(ref["asof_date"].iloc[0])
        orig = ft.actuals

        def drop_q(rev_v):
            a = orig(rev_v)
            return a.drop(index=q)

        with mock.patch.object(run.ft, "actuals", side_effect=drop_q):
            got = run.nowcast_entity(e, quarters=[], live={q: t})["rows"]
        m = ref.merge(got, on=["model_id"], suffixes=("_ref", "_live"))
        self.assertEqual(len(m), len(ref))
        self.assertEqual(len(got), len(ref))
        np.testing.assert_allclose(m["g_hat_ref"], m["g_hat_live"], rtol=0, atol=1e-12)
        np.testing.assert_allclose(m["rev_hat_ref"], m["rev_hat_live"], rtol=1e-12)


if __name__ == "__main__":
    unittest.main()
