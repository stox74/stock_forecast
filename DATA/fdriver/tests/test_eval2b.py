# -*- coding: utf-8 -*-
"""2b K1 테스트: 컨센서스 읽기 함수(시점·단위), 판정 통계(Q1 두 방향 군집, Q2 이항), Q3 분기별 1/3 차이,
fd_eval_2b 덮어쓰기 차단, 레이더 컨센서스 열, 성적표 컨센서스 항목, 기록 테이블 새 열 추가."""
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import db
from DATA.fdriver.adapters import kr_consensus as kc
from DATA.fdriver.eval2b import k2_table, k3_analysis as K
from .test_adapters import DB_OK

T = pd.Timestamp


def _fake_hist(ticker, source):
    d = pd.DataFrame({"ticker": "000001",
                      "target_period_end": [T("2024-03-31")] * 3 + [T("2024-06-30")],
                      "asof_date": [T("2024-01-31"), T("2024-02-29"), T("2024-03-29"), T("2024-03-29")],
                      "consensus": [100.0, 110.0, 120.0, 200.0], "unit": "억원"})
    d["value"] = d["consensus"] * d["unit"].map(kc.UNIT_TO_WON)
    return d


class TestReadConsensus(unittest.TestCase):
    def test_asof_and_unit(self):
        with mock.patch.object(kc, "_hist", _fake_hist):
            r = kc.read_consensus("000001", "2024Q1", "2024-02-29")
            self.assertEqual(r["value"], 110.0 * 1e8)                    # 억원 -> 원
            self.assertEqual(r["asof"], T("2024-02-29"))                 # 관측일 <= as_of
            r2 = kc.read_consensus("000001", "2024Q1", "2024-02-29", strict=True)
            self.assertEqual(r2["value"], 100.0 * 1e8)                   # C_final: 관측일 < 발표일
            self.assertIsNone(kc.read_consensus("000001", "2024Q1", "2024-01-30")["value"])
            self.assertEqual(kc.read_consensus("000001", T("2024-06-30"), "2024-12-31")["value"], 200.0 * 1e8)
            self.assertIsNone(kc.read_consensus("000001", "2024Q1", None)["value"])

    def test_quarter_end(self):
        self.assertEqual(kc.quarter_end("2023Q3"), T("2023-09-30"))
        self.assertEqual(kc.quarter_end(T("2023-12-15")), T("2023-12-31"))


class TestJudgementStats(unittest.TestCase):
    def test_hit_excludes_zero(self):
        r = K.hit_stats(pd.Series([1, -1, 0, 2, 1]), pd.Series([1, 1, 3, 0.5, np.nan]))
        self.assertEqual(r["n"], 3)                                      # 0 과 결측 제외
        self.assertEqual(r["hits"], 2)
        self.assertFalse(r["success"])
        big = K.hit_stats(pd.Series([1.0] * 100), pd.Series([1.0] * 60 + [-1.0] * 40))
        self.assertAlmostEqual(big["hit_rate"], 0.6)
        self.assertTrue(big["p_one"] < 0.10 and big["success"])

    def test_twoway_ols_positive_slope(self):
        rng = np.random.default_rng(0)
        n_f, n_q = 20, 12
        d = pd.DataFrame([(f"{i:06d}", f"20{20 + q // 4}Q{q % 4 + 1}") for i in range(n_f) for q in range(n_q)],
                         columns=["ticker", "quarter"])
        d["gap_E"] = rng.normal(0, 0.05, len(d))
        d["surprise"] = 0.5 * d["gap_E"] + rng.normal(0, 0.02, len(d))
        r = K.twoway_ols(d, "surprise", "gap_E")
        self.assertAlmostEqual(r["b"], 0.5, delta=0.1)
        self.assertTrue(r["success"])
        self.assertLess(r["p_one"], r["p_two"])
        r0 = K.twoway_ols(d.assign(surprise=-d["surprise"]), "surprise", "gap_E")
        self.assertFalse(r0["success"])                                  # b < 0 이면 실패

    def test_tercile_by_quarter(self):
        d = pd.DataFrame({"quarter": ["Q1"] * 6 + ["Q2"] * 3, "sig": [1, 2, 3, 4, 5, 6, 3, 2, 1],
                          "car": [0, 0, 1, 1, 4, 6, 9, 0, 3]})
        r = K.tercile_by_quarter(d, "sig", "car")
        t = r["table"].set_index("quarter")
        self.assertAlmostEqual(t.loc["Q1", "spread"], 5.0 - 0.0)         # 상위 2개 평균 5, 하위 2개 평균 0
        self.assertAlmostEqual(t.loc["Q2", "spread"], 9 - 3)             # sig 3 -> car 9, sig 1 -> car 3
        self.assertAlmostEqual(r["spread_mean"], 5.5)


class TestK2Rules(unittest.TestCase):
    """K2: C_final = 발표 다음 달 값, 4분기 연간값 수정, 판정 제외 flags, 편향 제거 gap 의 앞선 분기 평균."""

    def test_after_month_value(self):
        def hist(t, s):
            d = pd.DataFrame({"ticker": "000001", "target_period_end": T("2024-03-31"),
                              "asof_date": [T("2024-03-29"), T("2024-04-30"), T("2024-05-31"), T("2024-06-28")],
                              "consensus": [100.0, 110.0, 120.0, 130.0], "unit": "억원"})
            d["value"] = d["consensus"] * 1e8
            return d
        with mock.patch.object(kc, "_hist", hist):
            r = kc.read_consensus_after("000001", "2024Q1", T("2024-04-25"))
            self.assertEqual((r["value"], r["asof"]), (120.0 * 1e8, T("2024-05-31")))     # 4월 발표 -> 5월 말
            self.assertEqual(kc.read_consensus_after("000001", "2024Q1", T("2024-04-30"))["value"], 120.0 * 1e8)
            self.assertIsNone(kc.read_consensus_after("000001", "2024Q1", T("2024-06-05"))["value"])  # 7월 관측 없음
            self.assertIsNone(kc.read_consensus_after("000001", "2024Q1", None)["value"])

    def test_q4_annual_fix(self):
        from DATA.fdriver.eval2b import frame as F
        self.assertEqual(F.fix_q4_actual("2024Q4", 72.7, 17.8), (17.8, True))       # 연간 값 -> 연간 - 1~3분기
        self.assertEqual(F.fix_q4_actual("2024Q4", 18.0, 17.8), (18.0, False))
        self.assertEqual(F.fix_q4_actual("2024Q3", 72.7, 17.8), (72.7, False))      # 4분기만
        self.assertEqual(F.fix_q4_actual("2024Q4", 72.7, np.nan), (72.7, False))

    def _frame(self):
        from DATA.fdriver.eval2b import frame as F
        n = 6
        d = pd.DataFrame({"ticker": ["A"] * 4 + ["B", "C"], "quarter": ["2022Q1", "2022Q2", "2022Q3", "2022Q4", "2022Q1", "2022Q1"],
                          "actual": [100.0, 100, 100, 100, 100, 100], "actual_first_raw": [100.0] * n,
                          "n_E": [105.0] * n, "n_M4": [np.nan] * n, "n_B2": [100.0] * n, "n_TS": [100.0] * n,
                          "n_drv": [100.0] * n, "c_pre5": [100.0] * n, "c_final": [102.0, 102, 60, 102, 102, 102],
                          "c_after": [102.0] * n, "c_final_prev": [101.0] * n, "c_pre5_daily": [np.nan] * n,
                          "c_final_daily": [np.nan] * n, "nsur_drv": [np.nan] * n,
                          "announce_date": [T("2022-05-01")] * n, "q4": [False, False, False, True, False, False],
                          "corp_event": [False] * n, "excluded_firm": [False] * 4 + [True, False],
                          "control": [False] * 5 + [True], "basis_extra": [True, False, False, False, False, False],
                          "q4_annual_fix": [False] * n})
        return F, F.derive(d)

    def test_exclusion_flags(self):
        F, d = self._frame()
        self.assertEqual(d["in_judgement"].tolist(), [False, True, True, False, False, False])
        self.assertTrue(d.loc[0, "basis_mismatch"])
        self.assertTrue(d.loc[2, "basis_check"])                               # 100/60 = 1.67 > 1.5 (기업 중앙값은 (1.02+0.6)/2 = 0.81 이라 기준 안)
        self.assertTrue(d.loc[2, "in_judgement"])                              # basis_check 는 표시만
        self.assertAlmostEqual(d.loc[1, "surprise"], 100 / 102 - 1)
        self.assertAlmostEqual(d.loc[1, "rev_c"], 0.02)
        f = F.flags_text(d)
        self.assertEqual(f[3], "q4;not_comparable")
        self.assertIn("excluded_firm", f[4])
        self.assertIn("control", f[5])

    def test_prior_gap_mean(self):
        from DATA.fdriver.eval2b import k4_explore as X
        k = 7
        d = pd.DataFrame({"ticker": "A", "quarter": [f"q{i}" for i in range(k)],
                          "pre5_date": pd.date_range("2022-01-01", periods=k, freq="QS"),
                          "gap_E": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "q4": [False] * 3 + [True] + [False] * 3})
        for c in ("basis_mismatch", "corp_event", "excluded_firm", "control"):
            d[c] = False
        m = X.prior_gap_mean(d)
        self.assertTrue(m.iloc[:5].isna().all())                               # 앞선 적격 분기 4개 미만 (4분기 행 제외)
        self.assertAlmostEqual(m.iloc[5], np.mean([0.1, 0.2, 0.3, 0.5]))
        self.assertAlmostEqual(m.iloc[6], np.mean([0.1, 0.2, 0.3, 0.5, 0.6]))


class TestRadarConsensus(unittest.TestCase):
    def test_gap_columns(self):
        from DATA.fdriver.radar import build as B
        with mock.patch.object(kc, "read_consensus", return_value={"value": 100.0, "asof": T("2026-09-30")}):
            r = B.consensus_cols("000001", "2026Q3", T("2026-10-12"), 110.0, None)
        self.assertAlmostEqual(r["gap_E"], 0.10)
        self.assertTrue(np.isnan(r["gap_M4"]))
        self.assertEqual(r["c_latest_asof"], T("2026-09-30").date())
        with mock.patch.object(kc, "read_consensus", return_value={"value": None, "asof": None}):
            r = B.consensus_cols("000001", "2026Q3", T("2026-10-12"), 110.0, 90.0)
        self.assertTrue(np.isnan(r["c_latest"]) and np.isnan(r["gap_E"]))

    def test_scorecard_consensus_check(self):
        from DATA.fdriver.radar import scorecard as S
        with mock.patch.object(kc, "read_consensus", return_value={"value": 100.0, "asof": T("2026-09-30")}),                 mock.patch.object(kc, "read_consensus_after", return_value={"value": 105.0, "asof": T("2026-12-31")}):
            r = S.consensus_check({"entity_id": "000001", "fiscal_quarter": "2026Q3", "asof_date": T("2026-10-12"),
                                   "rev_base": 90.0, "rev_E": 95.0}, 110.0, T("2026-11-05"), float(np.log(110 / 90)))
        self.assertAlmostEqual(r["err_C_pre5"], np.log(100 / 110))
        self.assertAlmostEqual(r["err_C_final"], np.log(105 / 110))
        self.assertAlmostEqual(r["gap_E_c"], -0.05)
        self.assertFalse(r["gap_hit"])                                   # E 는 아래, 실제는 위
        c = pd.DataFrame({"drv_hit": [True, np.nan], "gap_hit": [False, True]})
        self.assertEqual(S._hit(c)["n_gap"], 2)


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestDBWrites(unittest.TestCase):
    TMP = "fd_tmp_eval2b_test"
    TMP_REC = "fd_tmp_radar_record_2b_test"

    def tearDown(self):
        for t in (self.TMP, self.TMP_REC):
            db.execute_fd_ddl(f"DROP TABLE IF EXISTS `{t}`")

    def test_eval_same_key_refused(self):
        db.execute_fd_ddl(k2_table.ddl(self.TMP))
        row = {c: None for c in k2_table.COLS}
        row.update({"ticker": "005930", "quarter": "2024Q1", "in_judgement": 1, "run_id": "r1", "gap_E": 0.1})
        df = pd.DataFrame([row])
        self.assertEqual(db.insert_fd_strict(df, self.TMP, k2_table.KEY), 1)
        with self.assertRaises(db.FdWriteViolation):                     # 같은 run_id 로 다시 쓰기
            db.insert_fd_strict(df.assign(gap_E=0.9), self.TMP, k2_table.KEY)
        self.assertEqual(db.insert_fd_strict(df.assign(gap_E=0.5, run_id="r2"), self.TMP, k2_table.KEY), 1)
        cur = db.read_sql(f"SELECT gap_E, run_id FROM `{self.TMP}` ORDER BY run_id")
        self.assertEqual(cur["gap_E"].tolist(), [0.1, 0.5])                # 새 run 은 옆에 쌓이고 r1 은 그대로

    def test_k1_schema_migrated_keeps_rows(self):
        """K1 형식 (기본키 ticker, quarter / flags 80자 / K2 열 없음) 테이블을 K2 형식으로 바꿔도 기존 행 값은 그대로."""
        k1_num = [c for c in k2_table.NUM if c not in ("actual_first_raw", "n_TS", "n_drv", "c_final_prev",
                                                       "surprise_prev")]
        ddl = k2_table.ddl(self.TMP).replace("PRIMARY KEY (`ticker`, `quarter`, `run_id`)", "PRIMARY KEY (`ticker`, `quarter`)")
        ddl = ddl.replace(f"VARCHAR({k2_table.FLAGS_LEN})", "VARCHAR(80)")
        for c in set(k2_table.NUM) - set(k1_num):
            ddl = ddl.replace(f"  `{c}` DOUBLE NULL,\n", "")
        db.execute_fd_ddl(ddl)
        db.insert_fd_strict(pd.DataFrame([{"ticker": "005930", "quarter": "2024Q1", "in_judgement": 1, "run_id": "k1",
                                           "gap_E": 0.1}]), self.TMP, ["ticker", "quarter"])
        changed = k2_table.ensure_schema(self.TMP)
        self.assertIn("primary key -> ticker, quarter, run_id", changed)
        self.assertEqual(k2_table.primary_key(self.TMP), k2_table.KEY)
        self.assertEqual(k2_table.ensure_schema(self.TMP), [])
        cur = db.read_sql(f"SELECT gap_E, run_id, n_TS FROM `{self.TMP}`")
        self.assertEqual(cur["gap_E"].tolist(), [0.1])
        self.assertTrue(cur["n_TS"].isna().all())

    def test_record_new_columns_keep_old_values(self):
        from DATA.fdriver.radar import record as rec
        old_cols = [c for c in rec.NUM_COLS if c not in ("gap_E", "gap_M4")]
        with mock.patch.object(rec, "NUM_COLS", old_cols):
            db.execute_fd_ddl(rec.ddl_record(self.TMP_REC))
        row = {c: None for c in old_cols + list(rec.STR_COLS)}
        row.update({"entity_id": "005930", "fiscal_quarter": "2026Q3", "asof_date": T("2026-10-11").date(),
                    "g_M4": 0.1, "record_id": "r1", "code_version": "x" * 40, "shock_n": None,
                    "announce_date": None, "consensus_asof": None})
        db.insert_fd_strict(pd.DataFrame([row]), self.TMP_REC, rec.RECORD_KEY)
        self.assertEqual(rec.ensure_columns(self.TMP_REC), ["gap_E", "gap_M4"])
        self.assertEqual(rec.ensure_columns(self.TMP_REC), [])
        cur = db.read_sql(f"SELECT g_M4, gap_E FROM `{self.TMP_REC}`")
        self.assertEqual(cur["g_M4"].tolist(), [0.1])
        self.assertTrue(cur["gap_E"].isna().all())


if __name__ == "__main__":
    unittest.main()
