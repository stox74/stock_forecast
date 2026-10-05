# -*- coding: utf-8 -*-
"""2a 작업 2: 이벤트표, 발표일 규칙, vintage, 4분기 점검, 외부 테이블 쓰기 제한 테스트."""
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import asof, db, entity, events as ev
from DATA.fdriver.adapters import read_revenue
from DATA.fdriver.adapters import revenue as rv
from .test_adapters import DB_OK

T = pd.Timestamp


class TestClassify(unittest.TestCase):
    def test_titles(self):
        cases = {
            "연결재무제표기준영업(잠정)실적(공정공시)": "prelim",
            "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)": "prelim",
            "영업(잠정)실적(공정공시)": "prelim_sep",
            "매출액또는손익구조30%(대규모법인은15%)이상변경": "pl_change",
            "매출액또는손익구조30%(대규모법인은15%)이상변경(자회사의주요경영사항)": "",
            "연결재무제표기준영업실적등에대한전망(공정공시)": "",
            "결산실적공시예고(안내공시)": "",
            "분기보고서 (2024.03)": "periodic",
            "[기재정정]사업보고서 (2023.12)": "periodic",
            "증권발행실적보고서": "",
        }
        for nm, want in cases.items():
            self.assertEqual(ev.classify(nm), want, nm)
        self.assertTrue(ev.is_correction("[첨부추가]사업보고서 (2009.12)"))


class TestParse(unittest.TestCase):
    def test_date_range_and_unit(self):
        t = ("1. 연결실적내용 단위 : 조원, % 실적기간 당기실적 2026-04-01 ~ 2026-06-30 "
             "구분 당기실적 매출액 당해실적 171.50 133.87")
        p = ev.parse_prelim(t, T("2026-07-07"))
        self.assertEqual((p["period_end"], p["period_src"], p["unit"], p["revenue_raw"]),
                         (T("2026-06-30"), "date_range", "조원", 171.5))
        self.assertEqual(p["period_days"], 91)

    def test_labels_and_jo_note(self):
        t = ("1. 연결실적내용 구분(단위 : 백만원, %) ('14.4Q) ('14.3Q) 매출액 당해실적 52.00 47.45 "
             "- 상기 금액은 조원 단위이며")
        p = ev.parse_prelim(t, T("2015-01-08"))
        self.assertEqual((p["period_end"], p["unit"]), (T("2014-12-31"), "조원"))
        t2 = "1. 연결실적내용 단위 : 백만원 (`20년 1분기) (`19년 4분기) 매출액 당해실적 110,824"
        self.assertEqual(ev.parse_prelim(t2, T("2020-05-06"))["period_end"], T("2020-03-31"))

    def test_old_monthly_format_is_marked(self):
        t = "1. 실적내용 해당 기간 당 기 : 2010.11.01~2010.11.30 (단위:백만원, %) 매출액 당해실적 16,249"
        p = ev.parse_prelim(t, T("2010-12-01"))
        self.assertEqual(p["period_src"], "date_range_old")
        self.assertLess(p["period_days"], ev.MIN_QUARTER_DAYS)

    def test_fallback_to_filing_date(self):
        p = ev.parse_prelim("깨진 본문 ??? 매출액 ???", T("2022-08-02"))
        self.assertEqual((p["period_end"], p["period_src"]), (T("2022-06-30"), "inferred_from_filing_date"))

    def test_pl_change_fiscal_year(self):
        t = "1. 재무제표의 종류 개별 2. 매출액 또는 손익구조 변동내용(단위:천원) - 매출액 167,469,222 109,337,763"
        p = ev.parse_pl_change(t, T("2025-02-07"))
        self.assertEqual((p["period_end"], p["fs_type"], p["unit"], p["revenue_raw"]),
                         (T("2024-12-31"), "개별", "천원", 167469222.0))


class TestFiscalQuarter(unittest.TestCase):
    def test_labels_roundtrip(self):
        for pe, f, lab in [(T("2024-09-30"), 12, "2024Q3"), (T("2024-12-31"), 12, "2024Q4"),
                           (T("2024-06-30"), 3, "2025Q1"), (T("2025-03-31"), 3, "2025Q4")]:
            self.assertEqual(ev.fiscal_quarter_label(pe, f), lab)
            self.assertEqual(ev.fiscal_quarter_end(lab, f), pe)


def _parsed(rows):
    cols = ["entity_id", "event_type", "rcept_dt", "rcept_no", "report_nm", "is_correction", "period_end",
            "orig_filing_date", "revenue_won", "unit", "fs_type", "fiscal_quarter"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ["rcept_dt", "period_end", "orig_filing_date"]:
        df[c] = pd.to_datetime(df[c])
    return df


class TestBuildEvents(unittest.TestCase):
    def test_original_preferred_and_correction_only(self):
        p = _parsed([
            ("X", "prelim", "2024-07-05", "1", "잠정", False, "2024-06-30", None, 1.0e12, "조원", "연결", "2024Q2"),
            ("X", "prelim", "2024-07-30", "2", "[기재정정]잠정", True, "2024-06-30", "2024-07-05", 1.1e12, "조원", "연결", "2024Q2"),
            ("X", "periodic", "2010-03-31", "3", "[첨부추가]사업보고서 (2009.12)", True, "2009-12-31", None, None, None, None, "2009Q4"),
            ("X", "pl_change", "2024-02-01", "4", "손익구조", False, "2023-12-31", None, 5e12, "원", "별도", "2023Q4"),
            ("X", "pl_change", "2024-02-02", "5", "손익구조", False, "2023-12-31", None, 6e12, "원", "연결", "2023Q4"),
        ])
        e = ev.build_events(p).set_index(["fiscal_quarter", "event_type"])
        self.assertEqual(e.loc[("2024Q2", "prelim"), "rcept_dt"], T("2024-07-05"))
        self.assertEqual(e.loc[("2024Q2", "prelim"), "revenue_won"], 1.0e12)       # 원공시 값
        self.assertEqual(e.loc[("2024Q2", "prelim"), "n_corrections"], 1)
        self.assertTrue(e.loc[("2009Q4", "periodic"), "from_correction_only"])
        self.assertEqual(e.loc[("2023Q4", "pl_change"), "fs_type"], "연결")         # 연결 우선

    def test_first_disclosure_grades(self):
        evs = pd.DataFrame([
            ("X", "2024Q1", "prelim_sep", T("2024-04-20"), None),
            ("X", "2024Q1", "periodic", T("2024-05-15"), None),
            ("X", "2024Q2", "prelim", T("2024-07-05"), 1e12),
            ("X", "2024Q2", "periodic", T("2024-08-14"), None),
            ("X", "2024Q3", "pl_change", T("2024-10-01"), 9e12),   # 4분기가 아니면 pl_change 무시
            ("X", "2024Q3", "periodic", T("2024-11-14"), None),
            ("X", "2024Q4", "pl_change", T("2025-02-01"), 9e12),
            ("X", "2024Q4", "periodic", T("2025-03-15"), None),
        ], columns=["entity_id", "fiscal_quarter", "event_type", "rcept_dt", "revenue_won"])
        fd = ev.first_disclosure(evs, {"X": 12}).set_index("period_end")
        self.assertEqual(tuple(fd.loc[T("2024-03-31"), ["first_type", "grade"]]), ("prelim_sep", "B"))
        self.assertEqual(tuple(fd.loc[T("2024-06-30"), ["first_type", "grade"]]), ("prelim", "A"))
        self.assertEqual(tuple(fd.loc[T("2024-09-30"), ["first_type", "grade"]]), ("periodic", "A"))
        self.assertEqual(tuple(fd.loc[T("2024-12-31"), ["first_type", "grade"]]), ("pl_change", "A"))


class TestRuleRoll(unittest.TestCase):
    def tearDown(self):
        asof.set_trading_calendar(None)

    def test_rule_date_rolls_to_next_trading_day(self):
        days = pd.bdate_range("2025-01-01", "2025-12-31").difference(pd.DatetimeIndex(["2025-05-15"]))
        asof.set_trading_calendar(days)
        # 2025-03-31 + 45일 = 05-15 (휴장 가정) -> 05-16, 2024-12-31 + 90일 = 2025-03-31 (월요일, 거래일)
        self.assertEqual(asof.rule_dates(pd.Series([T("2025-03-31")]), "revenue_quarter").iloc[0], T("2025-05-16"))
        self.assertEqual(asof.rule_dates(pd.Series([T("2024-12-31")]), "revenue_annual").iloc[0], T("2025-03-31"))
        # 1단계 규칙은 그대로 (휴장 미반영)
        self.assertEqual(asof.rule_dates(pd.Series([T("2025-03-31")]), "revenue_quarter_legal_v1").iloc[0],
                         T("2025-05-15"))

    def test_weekend_roll_without_calendar(self):
        asof.set_trading_calendar(None)
        # 2025-06-30 + 45일 = 2025-08-14 (목) 그대로, 2025-09-30 + 45일 = 11-14 (금)
        self.assertEqual(asof.rule_dates(pd.Series([T("2024-03-31")]), "revenue_quarter").iloc[0], T("2024-05-15"))
        # 2015-06-30 + 45일 = 2015-08-14 (금, 실제 임시공휴일이지만 달력 없으면 모름), 토요일 예: 2021-06-30 + 45 = 08-14(토)
        self.assertEqual(asof.rule_dates(pd.Series([T("2021-06-30")]), "revenue_quarter").iloc[0], T("2021-08-16"))


class TestExternalWriteGuard(unittest.TestCase):
    def test_only_allowlisted(self):
        self.assertEqual(db.check_external_table("kr_stock_index_daily"), ["idx_name", "period_end"])
        for bad in ["KSE_Price", "kr_kosis_data", "fd_entity_master", "kr_stock_index_daily_vintage"]:
            with self.assertRaises(db.FdWriteViolation):
                db.check_external_table(bad)
        with self.assertRaises(db.FdWriteViolation):
            db.write_std1_external(pd.DataFrame({"code": ["005930"], "period_end": [T("2026-01-02")], "close": [1.0]}),
                                   "KSE_Price", ["close"])

    def test_diff_vintage(self):
        keys, vals = ["idx_name", "period_end"], ["close"]
        new = pd.DataFrame({"idx_name": ["K"] * 3, "period_end": [1, 2, 3], "close": [10.0, 20.0, 30.0]})
        old = pd.DataFrame({"idx_name": ["K"] * 2, "period_end": [1, 2], "close": [10.0, 21.0]})
        self.assertEqual(db.diff_vintage(new, old, keys, vals).tolist(), ["", "revised", "new"])


class TestQ4Check(unittest.TestCase):
    def _stamped(self, q4_value):
        pe = pd.date_range("2023-03-31", periods=8, freq="QE")
        vals = [100.0] * 8
        vals[3] = q4_value          # 2023 Q4
        return pd.DataFrame({"key": "X", "period_end": pe, "value": vals, "source": "s", "asof_note": ""})

    def _fd(self, prelim):
        return pd.DataFrame([{"entity_id": "X", "period_end": T("2023-12-31"), "prelim_value": prelim,
                              "pl_change_value": np.nan}])

    def test_rule_flag_is_display_only(self):
        # 잠정 대비 10% 초과: 표시만 하고 제외하지 않는다 (v3)
        st = self._stamped(150.0)
        q = rv.q4_check(st, self._fd(100.0), {"X": 12})
        r = q.set_index("period_end").loc[T("2023-12-31")]
        self.assertTrue(r["flagged"])
        self.assertFalse(r["excluded"])
        out = rv._apply_q4_flags(st, q)
        notes = out.set_index("period_end")["asof_note"]
        self.assertEqual(notes[T("2023-12-31")], "q4_check:flagged")
        self.assertEqual(notes[T("2024-12-31")], "")
        self.assertFalse(rv.q4_exclusions(out).any())

    def test_known_case_excluded_with_next_year(self):
        from DATA.fdriver import config
        old = dict(config.Q4_CHECK_KNOWN_CASES)
        try:
            rv.Q4_CHECK_KNOWN_CASES["X"] = ["2023-12-31"]
            st = self._stamped(150.0)
            q = rv.q4_check(st, self._fd(100.0), {"X": 12})
            out = rv._apply_q4_flags(st, q)
            notes = out.set_index("period_end")["asof_note"]
            self.assertIn("q4_check:excluded", notes[T("2023-12-31")])
            self.assertEqual(notes[T("2024-12-31")], "q4_check:yoy_base")
            ex = out[rv.q4_exclusions(out)]["period_end"].tolist()
            self.assertEqual(ex, [T("2023-12-31"), T("2024-12-31")])
        finally:
            rv.Q4_CHECK_KNOWN_CASES.clear()
            rv.Q4_CHECK_KNOWN_CASES.update(old)

    def test_warn_ratio(self):
        # 1~3분기 평균 100, 4분기 300 -> 3배 경고 (잠정 없음)
        q = rv.q4_check(self._stamped(300.0), self._fd(np.nan), {"X": 12}).set_index("period_end")
        self.assertTrue(q.loc[T("2023-12-31"), "warn"])
        self.assertFalse(q.loc[T("2023-12-31"), "flagged"])
        self.assertFalse(rv.q4_check(self._stamped(250.0), self._fd(np.nan), {"X": 12})["warn"].any())
        self.assertTrue(rv.q4_check(self._stamped(30.0), self._fd(np.nan), {"X": 12})["warn"].any())   # 1/3 이하

    def test_within_threshold_and_negative(self):
        self.assertFalse(rv.q4_check(self._stamped(105.0), self._fd(100.0), {"X": 12})["flagged"].any())
        q = rv.q4_check(self._stamped(-5.0), self._fd(np.nan), {"X": 12}).set_index("period_end")
        r = q.loc[T("2023-12-31")]
        self.assertTrue(r["flagged"])                       # 음수
        self.assertTrue(r["warn"])                          # 1~3분기 평균의 1/3 이하이기도 함
        self.assertTrue(r["reason"].startswith("negative"))


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestRevenueReleaseOnDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = list(entity.PILOTS)
        cls.df, cls.meta = read_revenue(cls.ids, return_meta=True)

    def test_available_at_never_before_first_disclosure(self):
        """명세서 3절: 매출 available_at 이 같은 분기 첫 공시일보다 이른 행 0건 (기본 모드와 vintage 모드)."""
        fd = self.meta["first_disclosure"].rename(columns={"entity_id": "key"})
        for df in [self.df, read_revenue(self.ids, vintage=True)]:
            m = df.merge(fd[["key", "period_end", "first_dt"]], on=["key", "period_end"], how="inner")
            self.assertGreater(len(m), 300)
            self.assertEqual(int((m["available_at"] < m["first_dt"]).sum()), 0)

    def test_values_unchanged_from_stage1(self):
        old = read_revenue(self.ids, release="legal_v1")
        self.assertEqual(len(old), len(self.df))
        self.assertTrue(np.array_equal(old["value"].values, self.df["value"].values))
        self.assertTrue((old["asof_quality"] == "C").all())

    def test_vintage_rows(self):
        v = read_revenue(["005930"], start="2025-01-01", end="2025-12-31", vintage=True)
        pre = v[v["asof_note"].str.startswith("vintage:prelim")]
        fin = v[v["asof_note"].str.startswith("vintage:final")]
        self.assertEqual(len(pre), 4)
        self.assertEqual(len(fin), 4)
        m = pre.merge(fin, on="period_end", suffixes=("_p", "_f"))
        self.assertTrue((m["available_at_p"] <= m["available_at_f"]).all())
        known = asof.latest_known(asof.as_of(v, "2025-07-10"))
        self.assertIn("vintage:prelim", known.set_index("period_end").loc[T("2025-06-30"), "asof_note"])

    def test_known_q4_breaks_excluded(self):
        q = self.meta["q4_check"]
        excluded = set(zip(q.loc[q["excluded"], "entity_id"], q.loc[q["excluded"], "period_end"]))
        known = {("039130", T("2020-12-31")), ("267270", T("2021-12-31")), ("006040", T("2022-12-31"))}
        self.assertEqual(excluded, known)
        ex = self.df[rv.q4_exclusions(self.df)]
        self.assertEqual(len(ex), 6)                                                          # 3 + 다음 해 4분기 3
        self.assertIn(T("2021-12-31"), set(ex.loc[ex["key"] == "039130", "period_end"]))


if __name__ == "__main__":
    unittest.main()
