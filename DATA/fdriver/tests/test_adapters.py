# -*- coding: utf-8 -*-
"""adapter 테스트 (명세서 7절 최소 테스트 포함).

- 분기 단독값 변환: Q1~Q4 합계 = 연간
- read_trade: 030341 이 숫자로 바뀌지 않고 조회됨
- 모든 adapter 가 COMMON_COLUMNS 를 같은 이름·형식으로 반환
- entity 링크 생성 (DB 없이)
DB 가 필요한 테스트는 접속이 안 되면 건너뛴다.
"""
import os
import unittest

import numpy as np
import pandas as pd

from . import conftest  # noqa: F401
from DATA.fdriver import db
from DATA.fdriver.adapters import read_price, read_revenue, read_series, read_trade
from DATA.fdriver.adapters.revenue import to_standalone
from DATA.fdriver.adapters.trade import _clean_codes
from DATA.fdriver.config import COMMON_COLUMNS
from DATA.fdriver import entity


def _db_ok() -> bool:
    if os.environ.get("FDRIVER_SKIP_DB") == "1":
        return False
    try:
        db.read_sql("SELECT 1 AS one")
        return True
    except Exception:
        return False


DB_OK = _db_ok()


class TestStandaloneConversion(unittest.TestCase):
    def _f(self, rows):
        return pd.DataFrame(rows, columns=["entity_id", "fiscal_year", "q", "amount", "cum"])

    def test_three_month_amounts_sum_to_annual(self):
        f = self._f([("X", 2024, 1, 100.0, 100.0), ("X", 2024, 2, 120.0, 220.0),
                     ("X", 2024, 3, 130.0, 350.0), ("X", 2024, 4, 500.0, np.nan)])
        sa = to_standalone(f)
        self.assertEqual(sa.sort_values("fq")["value"].tolist(), [100.0, 120.0, 130.0, 150.0])
        self.assertAlmostEqual(sa["value"].sum(), 500.0)

    def test_cumulative_written_in_three_month_field(self):
        # 반기·3분기 칸에 누적이 적힌 보고서 -> 누적 차분
        f = self._f([("X", 2024, 1, 100.0, 100.0), ("X", 2024, 2, 220.0, 220.0),
                     ("X", 2024, 3, 350.0, 350.0), ("X", 2024, 4, 500.0, np.nan)])
        sa = to_standalone(f)
        self.assertEqual(sa.sort_values("fq")["value"].tolist(), [100.0, 120.0, 130.0, 150.0])

    def test_v2_style_without_cumulative(self):
        f = self._f([("X", 2016, 1, 10.0, np.nan), ("X", 2016, 2, 11.0, np.nan),
                     ("X", 2016, 3, 12.0, np.nan), ("X", 2016, 4, 50.0, np.nan)])
        sa = to_standalone(f)
        self.assertAlmostEqual(sa["value"].sum(), 50.0)
        self.assertAlmostEqual(sa.loc[sa.fq == 4, "value"].iloc[0], 17.0)

    def test_q4_from_q3_cumulative_when_a_quarter_is_missing(self):
        f = self._f([("X", 2025, 3, 30.0, 90.0), ("X", 2025, 4, 130.0, np.nan)])
        sa = to_standalone(f)
        self.assertAlmostEqual(sa.loc[sa.fq == 4, "value"].iloc[0], 40.0)

    def test_no_q4_without_enough_information(self):
        f = self._f([("X", 2016, 1, 10.0, np.nan), ("X", 2016, 4, 50.0, np.nan)])
        self.assertNotIn(4, to_standalone(f)["fq"].tolist())


class TestTradeCodes(unittest.TestCase):
    def test_numeric_codes_rejected(self):
        with self.assertRaises(TypeError):
            _clean_codes([30341])

    def test_leading_zero_kept_and_legacy_dropped(self):
        self.assertEqual(_clean_codes(["030341", "842951101", "19049", "854232"]), ["030341", "854232"])


class TestEntityLinksOffline(unittest.TestCase):
    def test_hs_links_use_map_as_is(self):
        m = pd.DataFrame({
            "ticker": ["006040", "005930", "000001"], "hs_code": ["030341", "8542321010", "999999"],
            "hs6": ["030341", "854232", "999999"], "hs_name": ["참치", "메모리", "x"],
            "fix_type": ["leading_zero", "none", "unresolved"], "is_active": [1, 1, 1],
        })
        out = entity.hs_links(m)
        self.assertEqual(out["link_key"].tolist(), ["030341", "8542321010"])
        self.assertEqual(out["series_key"].tolist(), ["030341", "854232"])

    def test_pilot_links_shape(self):
        pl = entity.pilot_links(["B073ZBXSJD"])
        self.assertEqual(list(pl.columns), entity.LINK_COLUMNS)
        self.assertFalse(pl.duplicated(["entity_id", "link_type", "link_key"]).any())
        self.assertTrue(set(pl["link_type"]) <= set(entity.LINK_TYPES))
        self.assertTrue((pl["link_source"] == "manual").all())
        keys = set(zip(pl["entity_id"], pl["link_key"]))
        for k in [("005930", "854231"), ("003350", "330420"), ("005930", "000660"), ("039130", "D:100")]:
            self.assertIn(k, keys)
        self.assertTrue(set(pl["entity_id"]) <= set(entity.PILOTS))

    def test_link_family(self):
        self.assertEqual(entity.link_family("alt_data", "amazon_bsr:US:B073ZBXSJD"), "amazon_bsr")
        self.assertEqual(entity.link_family("tourism_series", "D:100"), "tourism")
        self.assertEqual(entity.link_family("hs_code", "030341"), "trade")


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestAdaptersOnDB(unittest.TestCase):
    def assert_common(self, df, name):
        self.assertEqual(list(df.columns), COMMON_COLUMNS, name)
        self.assertGreater(len(df), 0, name)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["period_end"]), name)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["available_at"]), name)
        self.assertEqual(df["value"].dtype, np.float64, name)
        self.assertTrue(df["key"].map(lambda x: isinstance(x, str)).all(), name)
        self.assertTrue(df["asof_quality"].isin(["A", "B", "C"]).all(), name)
        self.assertTrue(df["available_at"].notna().all(), name)
        self.assertTrue((df["available_at"] >= df["period_end"]).all(), name)

    def test_all_adapters_return_common_columns(self):
        frames = {
            "revenue": read_revenue(["005930"], start="2023-01-01"),
            "trade": read_trade(["030341"], items=["expDlr", "expWgt"], start="2024-01-01"),
            "price": read_price(["005930"], start="2026-08-01"),
            "fred": read_series("fred", ["RSHPCS"], start="2025-01-01"),
            "kosis": read_series("kosis", ["kosis_113bb45a04e152ed"], start="2025-01-01"),
            "tourism": read_series("tourism", ["D:100", "E:TOTAL"], start="2025-01-01"),
            "airline": read_series("airline", ["apt_245a109ad3b4f119"], start="2025-01-01"),
            "tw_revenue": read_series("tw_revenue", ["2330"], start="2025-01-01"),
            "credit_balance": read_series("credit_balance", start="2026-08-01"),
            "amazon_bsr": read_series("amazon_bsr", ["US:B073ZBXSJD"], start="2026-07-01"),
            "google_trends": read_series("google_trends", ["US:medicube"], start="2026-01-01"),
            "fred_vintage": read_series("fred", ["RSHPCS"], start="2024-01-01", end="2024-03-31", vintage=True),
        }
        for name, df in frames.items():
            self.assert_common(df, name)
        dtypes = {n: tuple(str(t) for t in f.dtypes) for n, f in frames.items()}
        self.assertEqual(len(set(dtypes.values())), 1, dtypes)

    def test_read_trade_keeps_leading_zero(self):
        df = read_trade(["030341"], start="2007-01-01")
        self.assertEqual(df["key"].unique().tolist(), ["030341"])
        self.assertGreaterEqual(len(df), 200)
        self.assertLessEqual(df["period_end"].min(), pd.Timestamp("2007-01-31"))

    def test_quarterly_sum_equals_annual_on_db(self):
        # 삼성전자 2024 (DART V2) 와 2025 (DART V3): 분기 단독값 합 = 사업보고서 연간
        df = read_revenue(["005930"], start="2024-01-01", end="2025-12-31")
        annual = db.read_sql(
            "SELECT bsns_year, thstrm_amount FROM korea_fs_data_from_DART_V2 WHERE ticker = '005930' "
            "AND reprt_code = '11011' AND account_id = 'ifrs-full_Revenue' AND bsns_year = 2024 "
            "UNION ALL SELECT bsns_year, thstrm_amount FROM korea_fs_data_from_DART_V3 WHERE ticker = '005930' "
            "AND reprt_code = '11011' AND account_id = 'ifrs-full_Revenue' AND fs_div = 'CFS' AND bsns_year = 2025")
        for y, a in zip(annual["bsns_year"], annual["thstrm_amount"]):
            s = df[df["period_end"].dt.year == int(y)]["value"]
            self.assertEqual(len(s), 4)
            self.assertAlmostEqual(s.sum() / a, 1.0, places=9)

    def test_revenue_asof_rules(self):
        df = read_revenue(["005930"], start="2025-01-01", end="2025-12-31")
        got = dict(zip(df["period_end"], df["available_at"]))
        self.assertEqual(got[pd.Timestamp("2025-03-31")], pd.Timestamp("2025-05-15"))
        self.assertEqual(got[pd.Timestamp("2025-12-31")], pd.Timestamp("2026-03-31"))
        self.assertTrue((df["asof_quality"] == "C").all())

    def test_dg_scaled_and_quarter_end(self):
        df = read_revenue(["005930"], start="2010-01-01", end="2010-12-31")
        self.assertTrue((df["source"] == "korea_fs_data_from_DG").all())
        self.assertTrue(df["period_end"].dt.is_quarter_end.all())
        self.assertGreater(df["value"].min(), 1e13)   # 천원 x1000 -> 원 (삼성 분기 매출 수십조)


if __name__ == "__main__":
    unittest.main()
