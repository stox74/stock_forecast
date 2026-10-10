# -*- coding: utf-8 -*-
"""레이더 R1 테스트: 대상 39곳·등급표, 앙상블 E 규칙, TS 학습 구간, 레이더 실전 기록 덮어쓰기 차단."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import db
from DATA.fdriver.radar import record as rec
from DATA.fdriver.radar import ts
from DATA.fdriver.radar import universe as uv
from .test_adapters import DB_OK

T = pd.Timestamp


class TestUniverse(unittest.TestCase):
    def test_grade_table_complete(self):
        names = [n for ns in uv.GRADE_BY_NAME.values() for n in ns]
        self.assertEqual(len(names), 33)
        self.assertEqual(len(set(names)), 33)
        self.assertEqual(len(uv.PILOT_GRADE), 6)

    def test_check_stops_on_missing_or_duplicate(self):
        u = pd.DataFrame({"entity_id": [str(i) for i in range(39)], "name": ["x"] * 39, "grade": ["A"] * 39,
                          "group": ["pilot"] * 39})
        with self.assertRaises(RuntimeError):                 # 등급표 이름이 표에 없음
            uv.check_universe(u)
        u2 = pd.concat([u.iloc[:38], u.iloc[:1]])
        with self.assertRaises(RuntimeError):                 # 중복
            uv.check_universe(u2)

    @unittest.skipUnless(DB_OK, "DB unreachable")
    def test_real_universe(self):
        u = uv.universe()
        self.assertEqual(len(u), 39)
        self.assertEqual(int((u["group"] == "pilot").sum()), 6)


class TestEnsemble(unittest.TestCase):
    def test_equal_weights_and_missing(self):
        e = ts.ensemble(0.3, 0.0, 0.3, "M1:export")
        self.assertAlmostEqual(e["g_hat"], 0.2)
        self.assertEqual(e["label"], "E:M1:export+B2+TS")
        e2 = ts.ensemble(None, 0.1, 0.3, "M2")
        self.assertAlmostEqual(e2["g_hat"], 0.2)
        self.assertIn("missing:M2", e2["label"])
        e3 = ts.ensemble(None, 0.1, 0.3, None)                # 대조군: (B2 + TS) / 2
        self.assertAlmostEqual(e3["g_hat"], 0.2)
        self.assertNotIn("missing", e3["label"])
        self.assertIsNone(ts.ensemble(None, None, None, "M2")["g_hat"])


class TestTSTraining(unittest.TestCase):
    def _R(self, n, gap_at=None, neg_at=None):
        idx = pd.date_range("2010-03-31", periods=n, freq="QE")
        v = pd.Series(100 + np.arange(n) + 10 * np.tile([0, 1, 2, 3], n // 4 + 1)[:n], index=idx, dtype=float)
        if gap_at is not None:
            v = v.drop(idx[gap_at])
        if neg_at is not None:
            v.iloc[neg_at] = -5.0
        return v

    def test_contiguous_positive_tail_only(self):
        R = self._R(30, gap_at=10)
        q = R.index.max() + pd.offsets.QuarterEnd(1)
        y = ts.training_series(R, q)
        self.assertEqual(len(y), 30 - 11)                     # 빈 분기 뒤 구간만
        R2 = self._R(30, neg_at=20)
        self.assertEqual(len(ts.training_series(R2, q)), 9)   # 음수 분기 뒤 구간만

    def test_min_train_missing(self):
        R = self._R(11)
        q = R.index.max() + pd.offsets.QuarterEnd(1)
        r = ts.ts_forecast(R, q, float(R.iloc[-4]))
        self.assertIsNone(r["g_hat"])
        self.assertEqual(r["reason"], "lt_min_train")

    def test_forecast_uses_only_history_before_q(self):
        R = self._R(40)
        q = R.index[30]
        r1 = ts.ts_forecast(R, q, float(R.iloc[26]))
        r2 = ts.ts_forecast(R.iloc[:30], q, float(R.iloc[26]))  # q 이후 값을 지워도 같은 결과
        self.assertAlmostEqual(r1["g_hat"], r2["g_hat"], places=10)


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestRecordStrict(unittest.TestCase):
    TMP = "fd_tmp_radar_record_test"

    def setUp(self):
        db.execute_fd_ddl(rec.ddl_record(self.TMP))

    def tearDown(self):
        db.execute_fd_ddl(f"DROP TABLE IF EXISTS `{self.TMP}`")

    def test_same_key_refused(self):
        row = {c: None for c in rec.NUM_COLS + list(rec.STR_COLS)}
        row.update({"entity_id": "005930", "fiscal_quarter": "2026Q2", "asof_date": T("2026-07-01").date(),
                    "g_M4": 0.1, "record_id": "r1", "code_version": "x" * 40, "shock_n": None,
                    "announce_date": None, "consensus_asof": None})
        df = pd.DataFrame([row])
        self.assertEqual(db.insert_fd_strict(df, self.TMP, rec.RECORD_KEY), 1)
        with self.assertRaises(db.FdWriteViolation):
            db.insert_fd_strict(df.assign(g_M4=0.9, record_id="r2"), self.TMP, rec.RECORD_KEY)
        cur = db.read_sql(f"SELECT g_M4, record_id FROM `{self.TMP}`")
        self.assertEqual(cur["g_M4"].tolist(), [0.1])


class TestScorecardR2(unittest.TestCase):
    """R2: 방향 적중 분할, nsur_drv 상·하위 1/3 수익률 차이, 기록 회차별 계산."""

    def _c(self):
        from DATA.fdriver.radar import scorecard as S
        n = 9
        return S, pd.DataFrame({
            "status": ["비교"] * n, "grade": ["A"] * 6 + ["B"] * 3,
            "nsur_drv": [-3, -2, -1, 0.5, 1, 2, -1, 0.2, 3.0],
            "car_a": [-1.0, -2.0, -3.0, 0.0, 1.0, 5.0, -2.0, 0.0, 4.0],
            "car_b": [0.0] * n,
            "drv_hit": [True, False, True, True, np.nan, False, True, True, False],
            "agree": ["일치", "불일치", "일치", "일치", "-", "불일치", "일치", "불일치", "일치"],
            "err_M4": [0.1] * n, "err_E": [0.05] * n, "err_B2": [0.2] * n, "err_TS": [0.1] * n,
            "err_consensus": [np.nan] * n, "shock_alert": [""] * 8 + ["경보(상)"],
            "record_id": ["r1"] * 5 + ["r2"] * 4,
            "asof_date": [T("2026-10-16")] * 5 + [T("2026-10-23")] * 4})

    def test_tercile_spread(self):
        S, c = self._c()
        r = S.tercile_spread(c, "car_a")
        # nsur_drv 정렬: 하위 3개 (-3,-2,-1 의 car -1,-2,-3 / 그리고 -1(B) -2.0) -> 9개 중 3개씩
        self.assertEqual(r["n_each"], 3)
        lo = c.sort_values("nsur_drv")["car_a"].iloc[:3].mean()
        hi = c.sort_values("nsur_drv")["car_a"].iloc[-3:].mean()
        self.assertAlmostEqual(r["spread"], hi - lo)
        t = S.item3_returns(c)
        self.assertEqual(set(t["group"]), {"전체", "A", "B"})
        self.assertEqual(set(t["window"].str[0]), {"a", "b"})

    def test_hit_splits(self):
        S, c = self._c()
        t = S.item1_hit(c)
        all_ = t[t["split"] == "전체"].iloc[0]
        self.assertEqual(all_["n"], 8)                     # drv_hit 결측 1개 제외
        self.assertAlmostEqual(all_["drv_hit_rate"], 5 / 8)
        agree = t[t["split"] == "M4·E 일치"].set_index("group")["n"].to_dict()
        self.assertEqual(agree, {"일치": 5, "불일치": 3})    # '-' 는 빠짐

    def test_by_record(self):
        S, c = self._c()
        sc = S.by_asof(c)
        self.assertEqual(len(sc), 2)
        self.assertTrue(any(k.startswith("2026-10-16") for k in sc))
        self.assertEqual(int(sc[[k for k in sc if "r2" in k][0]]["1_hit"].iloc[0]["n"]), 4)


if __name__ == "__main__":
    unittest.main()
