# -*- coding: utf-8 -*-
"""2c 작업 2b: 끊긴 코드 제외 규칙, 6자리 전개 비율 0.9 규칙, 백업 후 재생성 시 다른 기업 행 보존 (DB 없이)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver.stage2c import drivers as dv
from DATA.fdriver.stage2c import s2b_d_regen as rg

T = pd.Timestamp
END = T("2026-08-31")


def _inv(rows):
    d = pd.DataFrame(rows, columns=["code", "first", "last", "n_months"])
    d["first"], d["last"] = pd.to_datetime(d["first"]), pd.to_datetime(d["last"])
    return d.set_index("code")


def _res(rows):
    cols = ["entity_id", "segment", "hs", "expand6", "leaf", "status"]
    d = pd.DataFrame(rows, columns=cols)
    d["needs_db_check"], d["hs_in_db"] = False, True
    return d


class TestApply2bRules(unittest.TestCase):
    def setUp(self):
        self.inv = _inv([
            ("111111", "2007-01-31", "2026-08-31", 236), ("222222", "2007-01-31", "2025-07-31", 223),   # 끊김
            ("333333", "2007-01-31", "2021-12-31", 180),                                               # HS 개정 폐지
            ("4444", "2008-01-31", "2026-08-31", 224), ("444411", "2007-01-31", "2026-08-31", 236),
            ("5555", "2008-01-31", "2026-01-31", 217), ("555511", "2007-01-31", "2026-08-31", 236),
            ("6666", "2008-01-31", "2026-01-31", 217), ("666611", "2007-01-31", "2026-08-31", 236),
        ])
        self.res = _res([
            ("A", "s1", "111111", False, "111111", "listed"),
            ("A", "s1", "222222", False, "222222", "listed"),
            ("A", "s1", "333333", False, "333333", "listed"),
            ("B", "s1", "4444", True, "444411", "expanded"),       # 비율 0.5, 4자리 2026-08 까지 -> 4자리 전환
            ("C", "s1", "5555", True, "555511", "expanded"),       # 비율 0.5, 4자리 끊김 -> 전개 부족 표시
            ("D", "s1", "6666", True, "666611", "expanded"),       # 비율 0.95 -> 6자리 전개
            ("010120", "변압기(자회사)", "111111", False, "111111", "listed"),
        ])
        ratios = {"4444": 0.5, "5555": 0.5, "6666": 0.95}
        self.p = mock.patch.object(dv, "expansion_ratio", side_effect=lambda hs, kids: ratios[hs])
        self.p.start()
        self.out, self.ratios = dv.apply_2b_rules(self.res, self.inv, END)

    def tearDown(self):
        self.p.stop()

    def _leaves(self, e):
        return sorted(self.out.loc[self.out["entity_id"] == e, "leaf"].dropna())

    def test_cut_code_dropped_retired_kept(self):
        self.assertEqual(self._leaves("A"), ["111111", "333333"])
        r = self.out[(self.out["entity_id"] == "A") & (self.out["dropped_leaf"] == "222222")]
        self.assertEqual(r["status"].iloc[0], "cut_dropped")

    def test_ratio_rules(self):
        self.assertEqual(self._leaves("B"), ["4444"])
        self.assertEqual(self.out.loc[self.out["entity_id"] == "B", "status"].iloc[0], "four_digit_switch")
        self.assertEqual(self._leaves("C"), ["555511"])
        self.assertTrue(self.out.loc[self.out["entity_id"] == "C", "expand_short"].all())
        self.assertEqual(self._leaves("D"), ["666611"])
        dec = dict(zip(self.ratios["hs"], self.ratios["decision"]))
        self.assertEqual(dec, {"4444": "four_digit_switch", "5555": "expand_short", "6666": "expanded"})

    def test_ls_transformer_excluded(self):
        self.assertEqual(self._leaves("010120"), [])
        self.assertEqual(self.out.loc[self.out["entity_id"] == "010120", "status"].iloc[0], "ls_transformer_excluded")


class TestReplaceEntities(unittest.TestCase):
    def _run(self, ids, compute, existing_backup=None):
        tmp = Path(tempfile.mkdtemp())
        if existing_backup is not None:
            existing_backup.to_csv(tmp / f"backup_fd_nowcast_{ids[0]}.csv", index=False)
        calls = []
        cur = pd.DataFrame({"entity_id": ids, "fiscal_quarter": "2026Q2", "g_hat": 0.1})
        with mock.patch.object(rg.db, "read_sql", side_effect=lambda sql, p: cur[cur["entity_id"] == p["e"]]), \
             mock.patch.object(rg.db, "delete_fd", side_effect=lambda t, w: calls.append(("delete", t, w)) or 7), \
             mock.patch.object(rg.db, "write_fd", side_effect=lambda df, t, k: calls.append(("write", t, sorted(set(df["entity_id"])))) or len(df)):
            out = rg.replace_entities(ids, compute, tmp)
        return tmp, calls, out

    def test_only_listed_entities_deleted_and_backup_first(self):
        compute = lambda e: {"rows": pd.DataFrame({"entity_id": [e, e], "fiscal_quarter": ["2026Q1", "2026Q2"]})}
        tmp, calls, out = self._run(["034220", "267260"], compute)
        self.assertEqual(calls[0], ("delete", "fd_nowcast", {"entity_id": ["034220", "267260"]}))
        self.assertEqual(calls[1], ("write", "fd_nowcast", ["034220", "267260"]))
        self.assertTrue((tmp / "backup_fd_nowcast_034220.csv").exists() and (tmp / "backup_fd_nowcast_267260.csv").exists())

    def test_existing_backup_not_overwritten(self):
        first = pd.DataFrame({"entity_id": ["034220"], "fiscal_quarter": ["2025Q2"], "g_hat": [9.9]})
        compute = lambda e: {"rows": pd.DataFrame({"entity_id": [e], "fiscal_quarter": ["2026Q2"]})}
        tmp, calls, out = self._run(["034220"], compute, existing_backup=first)
        kept = pd.read_csv(tmp / "backup_fd_nowcast_034220.csv")
        self.assertEqual(kept["g_hat"].iloc[0], 9.9)

    def test_compute_failure_deletes_nothing(self):
        def compute(e):
            raise ValueError("fail")
        with self.assertRaises(ValueError):
            self._run(["034220"], compute)

    def test_pilot_refused(self):
        with self.assertRaises(RuntimeError):
            rg.replace_entities(["005930"], lambda e: {}, Path(tempfile.mkdtemp()))


class TestV4FillRule(unittest.TestCase):
    """명세서 2c v4: 수집기 목록 코드는 행 없는 달을 수집기 마지막 달까지 0, 끊긴 코드 제외는 목록 밖 코드에만."""

    def _raw(self, code, months, value):
        pe = pd.to_datetime(months) + pd.offsets.MonthEnd(0)
        return pd.DataFrame({"key": code, "period_end": pe, "freq": "M", "item": "expDlr", "value": value,
                             "unit": "USD", "currency": "USD", "available_at": pe + pd.Timedelta(days=15),
                             "asof_quality": "C", "asof_note": "", "source": "t"})

    def test_collector_code_zero_filled_to_last_month(self):
        raw = pd.concat([self._raw("111111", ["2026-05", "2026-06", "2026-07", "2026-08"], 10.0),
                         self._raw("890590", ["2026-05"], 3.0)])            # 목록 코드, 2026-06 이후 행 없음
        leaves = pd.DataFrame({"leaf": ["111111", "890590"], "status": ["listed", "listed"]})
        s = dv.segment_series(leaves, "seg", raw, fill_to=END, fill_codes=frozenset({"111111", "890590"}))
        self.assertEqual(s["end"], END)
        self.assertEqual(list(s["df"]["value"]), [13.0, 10.0, 10.0, 10.0])
        self.assertEqual(s["zero_filled"], 3)

    def test_month_with_no_rows_kept_as_zero(self):
        raw = self._raw("890610", ["2026-05", "2026-07"], 5.0)
        leaves = pd.DataFrame({"leaf": ["890610"], "status": ["listed"]})
        s = dv.segment_series(leaves, "seg", raw, fill_to=END, fill_codes=frozenset({"890610"}))
        df = s["df"]
        self.assertEqual(list(df["value"]), [5.0, 0.0, 5.0, 0.0])
        z = df[df["period_end"] == T("2026-08-31")].iloc[0]
        self.assertEqual(z["available_at"], T("2026-09-15"))
        self.assertEqual(z["asof_note"], "zero_filled")

    def test_non_collector_code_still_truncates_without_fill(self):
        raw = pd.concat([self._raw("111111", ["2026-05", "2026-06", "2026-07", "2026-08"], 10.0),
                         self._raw("222222", ["2026-05", "2026-06"], 1.0)])     # 목록 밖
        leaves = pd.DataFrame({"leaf": ["111111", "222222"], "status": ["listed", "listed"]})
        s = dv.segment_series(leaves, "seg", raw, fill_to=END, fill_codes=frozenset({"111111"}))
        self.assertEqual(s["end"], T("2026-06-30"))

    def test_cut_exclusion_only_outside_collector(self):
        inv = _inv([("111111", "2007-01-31", "2026-08-31", 236), ("890590", "2007-01-31", "2026-07-31", 232),
                    ("222222", "2007-01-31", "2025-07-31", 223)])
        res = _res([("A", "s1", "111111", False, "111111", "listed"), ("A", "s1", "890590", False, "890590", "listed"),
                    ("A", "s1", "222222", False, "222222", "listed")])
        out, _ = dv.apply_2b_rules(res, inv, END, collector=frozenset({"111111", "890590"}))
        self.assertEqual(sorted(out["leaf"].dropna()), ["111111", "890590"])
        self.assertEqual(list(out["dropped_leaf"].dropna()), ["222222"])
        # collector 를 주지 않으면 2b 규칙 그대로 (890590 도 제외)
        out2, _ = dv.apply_2b_rules(res, inv, END)
        self.assertEqual(sorted(out2["leaf"].dropna()), ["111111"])


if __name__ == "__main__":
    unittest.main()
