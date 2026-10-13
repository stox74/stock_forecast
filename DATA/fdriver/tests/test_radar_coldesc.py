# -*- coding: utf-8 -*-
"""레이더 출력 v2 테스트 (fdriver_radar_coldesc_instruction_v2 7절).

1 출력의 모든 열이 COLDESC 에 있음 / 2 COLDESC 에만 있는 변수는 경고 / 3 방향 일관성 / 4 2026Q3 확인 사례 /
5 판단 규칙 1~8 / 6 xlsx 세 시트 (가짜 표 + 실제 2026Q2·2026Q3) / 7 DB 기존 열 이름 그대로 / 8 공개 출력에 내부 시트 없음.
"""
import glob
import tempfile
import unittest
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from . import conftest  # noqa: F401
from DATA.fdriver import db
from DATA.fdriver.config import output_path
from DATA.fdriver.radar import build as B
from DATA.fdriver.radar import coldesc as C
from DATA.fdriver.radar import excel as X
from DATA.fdriver.radar import output as O
from DATA.fdriver.radar import public as P
from .test_adapters import DB_OK


def _internal(n: int = 4) -> pd.DataFrame:
    """내부 표 (build.COLUMNS) 가짜: 삼성전자형(구성 불일치), 금호타이어형(추세 위·컨센서스 아래), 대조군, C등급."""
    t = pd.DataFrame({c: [np.nan] * n for c in B.COLUMNS}).astype(object)
    t["entity_id"] = ["005930", "073240", "006040", "009150"]
    t["name"] = ["가", "나", "다", "라"]
    t["grade"] = ["A", "A", "B2만", "C"]
    t["sector"], t["fiscal_quarter"], t["asof_date"] = "반도체", "2026Q3", pd.Timestamp("2026-10-12").date()
    base = np.array([100.0e12, 1.1e12, 2.5e12, 3.0e12])
    g = {"M1": [np.log(140.3 / 100), 0.116, np.nan, 0.10], "B2": [np.log(197.9 / 100), 0.099, 0.08, 0.10],
         "TS": [np.log(198.6 / 100), 0.145, np.nan, 0.10]}
    for k, v in g.items():
        t[f"g_{k}"] = v
    t["g_E"] = [np.nanmean([g["M1"][i], g["B2"][i], g["TS"][i]]) if i != 2 else np.nan for i in range(n)]
    t["g_M4"] = [np.log(1.95), 0.15, np.nan, 0.08]
    t["rev_base"] = base
    for k in ("E", "M4", "M1", "B2", "TS"):
        t[f"rev_{k}"] = base * np.exp(pd.to_numeric(t[f"g_{k}"]))
    t["M1_model"] = ["M2", "M1:export", None, "M1:export"]
    t["consensus"] = [200.3e12, 1.279e12, 2.68e12, 3.2e12]
    t["gap_E"] = pd.to_numeric(t["rev_E"]) / t["consensus"] - 1
    t["gap_M4"] = pd.to_numeric(t["rev_M4"]) / t["consensus"] - 1
    t["nsur_drv"] = [0.2, 0.7, np.nan, -0.3]
    t["shock_x"] = pd.to_numeric(t["g_M4"]) - pd.to_numeric(t["g_B2"])
    t["shock_alert"] = [None, "경보(상)", None, None]
    t["warnings"] = ["드라이버부분(cov=0.89)", "", "", "Q4차감;기업사건"]
    t["announce_kind"] = ["실제", "추정 (최근 5년 중앙값 35일)", "추정 (최근 5년 중앙값 35일)", "추정 불가"]
    t["drv_3m_yoy"] = [0.1, 0.05, np.nan, -0.02]
    return t


class TestColumns(unittest.TestCase):
    def test_every_output_column_described(self):
        out = O.to_output(_internal())
        self.assertEqual(list(out.columns), C.OUTPUT_COLUMNS)
        self.assertEqual(C.missing(list(out.columns) + O.JUDGE_COLUMNS), [])
        internals = {d["internal"] for d in C.COLDESC.values()}
        self.assertEqual([c for c in B.COLUMNS if c not in internals], [])        # 내부 열은 모두 출력에 대응
        for c, d in C.COLDESC.items():
            self.assertIn(d["group"], C.GROUPS, c)
            self.assertTrue(d["meaning"], c)
        with self.assertRaises(KeyError):
            C.table(C.OUTPUT_COLUMNS + ["new_col"])

    def test_prefix_rules(self):
        for c, d in C.COLDESC.items():
            if c.startswith("jo_"):
                self.assertEqual(d["unit"], C.JO, c)
            elif c.startswith("rev_"):
                self.assertEqual(d["unit"], C.WON, c)
            elif c.endswith("_pct"):
                self.assertTrue(d["unit"].startswith("%"), c)
            elif c.startswith("g_"):
                self.assertTrue(d["unit"].startswith("로그"), c)
            elif c.startswith("dir_"):
                self.assertEqual(d["unit"], C.DIR, c)
            elif c.startswith("agree_"):
                self.assertEqual(d["unit"], C.AGR, c)
            elif c.startswith("flag_"):
                self.assertEqual(d["unit"], C.FLAG, c)

    def test_unused_only_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            extra = C.unused(C.OUTPUT_COLUMNS[:-1])
        self.assertEqual(extra, [C.OUTPUT_COLUMNS[-1]])
        self.assertTrue(any("COLDESC" in str(x.message) for x in w))
        self.assertIn(C.OUTPUT_COLUMNS[-1], C.COLDESC)

    def test_sheet_order(self):
        t = C.table(C.OUTPUT_COLUMNS)
        self.assertEqual(list(t.columns), ["분류", "출력 변수명", "내부 변수명", "의미", "계산식", "단위", "비고"])
        g = [C.GROUPS.index(x) for x in t["분류"]]
        self.assertEqual(g, sorted(g))
        self.assertEqual(t["출력 변수명"].iloc[0], "decision")
        self.assertEqual(t.set_index("출력 변수명").loc["dir_E_vs_trend", "내부 변수명"], "E_direction")


class TestDirectionsAndSpread(unittest.TestCase):
    def _check_dirs(self, out):
        sg = lambda x: "" if pd.isna(x) or x == 0 else ("+" if x > 0 else "-")
        self.assertEqual(out["dir_E_vs_cons"].tolist(), [sg(x) for x in out["gap_E_vs_cons_pct"]])
        self.assertEqual(out["dir_E_vs_trend"].tolist(),
                         [sg(a - b) for a, b in zip(pd.to_numeric(out["g_E"]), pd.to_numeric(out["g_B2"]))])

    def test_direction_consistency(self):
        out = O.to_output(_internal())
        self._check_dirs(out)
        self.assertEqual(out.loc[1, "dir_E_vs_trend"], "+")                    # 금호타이어형
        self.assertEqual(out.loc[1, "dir_E_vs_cons"], "-")
        self.assertEqual(out.loc[2, "dir_E_vs_cons"], "")                      # 대조군: E 없음

    def test_spread(self):
        out = O.to_output(_internal())
        self.assertEqual(out.loc[0, "flag_E_spread"], "불일치(M2)")             # 삼성전자 M2 140.3 / B2 197.9 / TS 198.6
        self.assertEqual(out.loc[0, "spread_E_from"], "M2")
        self.assertAlmostEqual(out.loc[0, "spread_E_pct"], abs(140.3 / np.sqrt(197.9 * 198.6) - 1) * 100, places=6)
        self.assertEqual(out.loc[2, "flag_E_spread"], "")                      # 대조군
        r = O.spread(0.1, np.nan, 0.1, "M1:x", False)
        self.assertEqual(r["flag_E_spread"], "구성 부족")
        self.assertEqual(O.spread(0.1, 0.11, 0.12, "M1:x", False)["flag_E_spread"], "")
        self.assertEqual(out.loc[3, "flag_q4"], "4분기 차감")
        self.assertEqual(out.loc[3, "flag_event"], "기업 사건")
        self.assertEqual(out.loc[0, "flag_drv_missing"], "드라이버 부분(cov=0.89)")
        self.assertEqual(out.loc[1, "flag_shock"], "충격(상향)")
        self.assertEqual(out.loc[0, "announce_basis"], "확정")
        self.assertAlmostEqual(out.loc[0, "drv_3m_yoy_pct"], (np.exp(0.1) - 1) * 100)


class TestDecisionRules(unittest.TestCase):
    def _r(self, **kw):
        r = {"grade": "A", "gap_E_vs_cons_pct": np.nan, "flag_E_spread": "", "flag_shock": "", "agree_drv_cons": "",
             "dir_E_vs_cons": "", "agree_E_drv_vs_trend": "", "dir_E_vs_trend": "", "rev_E": 1.0}
        r.update(kw)
        return O.decide(r)[0]

    def test_rules_1_to_8(self):
        self.assertEqual(self._r(grade="C", flag_E_spread="불일치(M2)", gap_E_vs_cons_pct=-20.0), "보류(등급)")      # 1
        self.assertEqual(self._r(flag_E_spread="불일치(M2)", flag_shock="충격(상향)"), "검토(충격+불일치)")            # 2
        self.assertEqual(self._r(flag_E_spread="불일치(TS)", gap_E_vs_cons_pct=9.0), "보류(불일치)")                 # 3
        self.assertEqual(self._r(gap_E_vs_cons_pct=6.0, agree_drv_cons="일치", dir_E_vs_cons="+"), "후보(상향)")       # 4
        self.assertEqual(self._r(gap_E_vs_cons_pct=-6.0, agree_drv_cons="일치", dir_E_vs_cons="-",
                                 flag_shock="충격(하향)"), "후보(하향)+충격")                                          # 4 + 충격
        self.assertEqual(self._r(gap_E_vs_cons_pct=-5.0, agree_drv_cons="불일치", dir_E_vs_cons="-"),
                         "관찰(방향 불일치)")                                                                          # 5 (경계 5.0)
        self.assertEqual(self._r(gap_E_vs_cons_pct=4.99, agree_drv_cons="일치"), "중립")                             # 6
        self.assertEqual(self._r(agree_E_drv_vs_trend="일치", dir_E_vs_trend="-"), "관찰(컨센서스 없음, 하향)")        # 7
        self.assertEqual(self._r(agree_E_drv_vs_trend="불일치"), "중립")                                              # 8
        self.assertEqual(self._r(flag_E_spread="구성 부족", gap_E_vs_cons_pct=8.0, agree_drv_cons="일치",
                                 dir_E_vs_cons="+"), "후보(상향)")                                                   # 구성 부족은 불일치 아님

    def test_order_and_thresholds(self):
        self.assertEqual((O.GAP_THRESHOLD_PCT, O.SPREAD_THRESHOLD_PCT), (5.0, 15.0))
        out = O.to_output(_internal())
        j = O.judgement(out)
        ranks = [O.decision_rank(d) for d in j["decision"]]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(list(j.columns), O.JUDGE_COLUMNS)
        self.assertIn("5%", O.rule_summary())
        self.assertIn("15%", O.rule_summary())


class TestExcel(unittest.TestCase):
    def test_three_sheets_and_format(self):
        out = O.to_output(_internal())
        with tempfile.TemporaryDirectory() as d:
            p = X.to_xlsx(out, Path(d) / "r.xlsx")
            with self.assertRaises(FileExistsError):
                X.to_xlsx(out, p)
            wb = load_workbook(p)
            self.assertEqual(wb.sheetnames, ["판단", "radar", "변수설명"])
            wj, ws, wd = wb["판단"], wb["radar"], wb["변수설명"]
            self.assertIn("5%", wj["A1"].value)
            self.assertEqual([c.value for c in wj[3]], O.JUDGE_COLUMNS)
            self.assertIn(C.COLDESC["decision"]["meaning"], wj.cell(row=3, column=1).comment.text)
            jo = O.JUDGE_COLUMNS.index("jo_E") + 1
            self.assertEqual(wj.cell(row=4, column=jo).number_format, "#,##0.000")
            self.assertEqual([c.value for c in ws[1]], C.OUTPUT_COLUMNS)
            self.assertEqual(ws.cell(row=2, column=C.OUTPUT_COLUMNS.index("rev_E") + 1).number_format, "#,##0")
            self.assertEqual(wd.freeze_panes, "A2")
            self.assertTrue(wd["A1"].font.b)
            self.assertEqual(wd.max_row - 1, len(C.OUTPUT_COLUMNS))
            self.assertTrue(wd["D2"].alignment.wrap_text)
            fills = {wj.cell(row=i, column=1).fill.fgColor.rgb for i in range(4, wj.max_row + 1)}
            self.assertTrue(any(f and f.endswith("ECEBE7") for f in fills))         # 보류 색

    def _latest(self, q):
        fs = sorted(glob.glob(str(output_path("radar", q, mkdir=False) / f"radar_{q}_*.xlsx")))
        fs = [f for f in fs if "판단" in load_workbook(f, read_only=True).sheetnames]
        return fs[-1] if fs else None

    def test_real_xlsx(self):
        for q in ("2026Q2", "2026Q3"):
            f = self._latest(q)
            if not f:
                self.skipTest(f"{q} v2 xlsx 없음")
            wb = load_workbook(f)
            self.assertEqual(wb.sheetnames, ["판단", "radar", "변수설명"], q)
            self.assertEqual(C.missing([c.value for c in wb["radar"][1]]), [], q)

    def test_real_2026q3_cases(self):
        fs = sorted(glob.glob(str(output_path("radar", "2026Q3", mkdir=False) / "radar_2026Q3_*.csv")))
        fs = [f for f in fs if not f.endswith("_decision.csv") and "decision" in pd.read_csv(f, nrows=0).columns]
        if not fs:
            self.skipTest("2026Q3 v2 csv 없음")
        out = pd.read_csv(fs[-1], dtype={"entity_id": str}, keep_default_na=False, na_values=[""])
        out[["dir_E_vs_cons", "dir_E_vs_trend"]] = out[["dir_E_vs_cons", "dir_E_vs_trend"]].fillna("")
        TestDirectionsAndSpread._check_dirs(self, out)
        k = out.set_index("name")
        self.assertEqual(k.loc["금호타이어", "dir_E_vs_trend"], "+")
        self.assertEqual(k.loc["금호타이어", "dir_E_vs_cons"], "-")
        self.assertEqual(k.loc["삼성전자", "flag_E_spread"], "불일치(M2)")
        self.assertIn(k.loc["삼성전자", "decision"], ("보류(불일치)", "검토(충격+불일치)"))


@unittest.skipUnless(DB_OK, "DB unreachable")
class TestDBNamesUnchanged(unittest.TestCase):
    def test_existing_columns_kept(self):
        rec = set(db.table_columns("fd_live_record"))
        for c in ("E_direction", "drv_direction", "agree", "shock_alert", "shock_x", "nsur_drv", "consensus", "gap_E",
                  "gap_M4", "warnings", "drv_3m_yoy", "announce_kind", "g_E", "rev_E"):
            self.assertIn(c, rec)
        for c in ("z_drv_vs_trend", "shock_dev", "flag_shock", "gap_E_vs_cons_pct", "dir_E_vs_trend"):
            self.assertNotIn(c, rec)                                              # 출력 이름은 DB 에 없음
        nc = set(db.table_columns("fd_nowcast"))
        self.assertTrue({"g_hat", "rev_hat", "rev_actual_first", "rev_base"} <= nc)
        sig = set(db.table_columns("fd_signal"))
        self.assertTrue({"nsur", "nsur_mom", "nsur_drv", "sigma", "delta"} <= sig)

    def test_first_record_untouched(self):
        r = db.read_sql("SELECT COUNT(*) n FROM fd_live_record WHERE record_id = '20261011_013503'")
        self.assertEqual(int(r["n"].iloc[0]), 38)
        if "decision" in db.table_columns("fd_live_record"):
            r = db.read_sql("SELECT COUNT(decision) n FROM fd_live_record WHERE record_id = '20261011_013503'")
            self.assertEqual(int(r["n"].iloc[0]), 0)


class TestV3(unittest.TestCase):
    """출력 v3: C등급 사유, 방향 대체, E 편향 기록."""

    def test_c_grade_reason_includes_spread(self):
        d, why = O.decide({"grade": "C", "gap_E_vs_cons_pct": 12.2, "flag_E_spread": "불일치(M1)", "flag_shock": ""})
        self.assertEqual(d, "보류(등급)")
        self.assertIn("C등급은 판단 보류", why)
        self.assertIn("구성 불일치(M1)", why)
        d, why = O.decide({"grade": "C", "gap_E_vs_cons_pct": 1.0, "flag_E_spread": "", "flag_shock": ""})
        self.assertNotIn("구성", why)

    def test_no_driver_direction_is_neutral(self):
        d, why = O.decide({"grade": "A", "gap_E_vs_cons_pct": 8.0, "flag_E_spread": "", "flag_shock": "",
                           "agree_drv_cons": "", "dir_E_vs_cons": "+", "rev_E": 1.0})
        self.assertEqual(d, "중립")
        self.assertIn("드라이버 방향 없음", why)
        self.assertIn("드라이버 방향(dir_drv_vs_trend)이 공란이면", C.COLDESC["decision"]["note"])

    def test_direction_fallback(self):
        t = _internal()
        t.loc[1, "nsur_drv"] = np.nan                                       # z 없음, shock_dev 있음 (+)
        out = O.to_output(t)
        self.assertEqual(out.loc[1, "dir_drv_vs_trend"], "+")
        self.assertEqual(out.loc[1, "flag_dir_fallback"], "방향 대체(shock_dev)")
        self.assertEqual(out.loc[0, "flag_dir_fallback"], "")
        self.assertEqual(out.loc[2, "dir_drv_vs_trend"], "")                # 대조군: 둘 다 없음
        self.assertEqual(out.loc[2, "flag_dir_fallback"], "")
        self.assertIn("flag_dir_fallback", O.JUDGE_COLUMNS)
        self.assertIn("실전 3개 분기 후 재검토", O.rule_summary())

    def test_scorecard_e_bias(self):
        from DATA.fdriver.radar import scorecard as S
        c = pd.DataFrame({"status": ["비교"] * 4, "fiscal_quarter": ["2026Q3"] * 3 + ["2026Q4"],
                          "e_bias_pct": [-2.0, 1.0, -5.0, 3.0]})
        t = S.item6_e_bias(c).set_index("fiscal_quarter")
        self.assertAlmostEqual(t.loc["2026Q3", "e_bias_mean_pct"], -2.0)
        self.assertAlmostEqual(t.loc["2026Q3", "e_bias_median_pct"], -2.0)
        self.assertAlmostEqual(t.loc["2026Q3", "share_below_actual"], 2 / 3)
        self.assertEqual(int(t.loc["2026Q4", "n"]), 1)


class TestPublic(unittest.TestCase):
    def _out(self):
        return O.to_output(_internal())

    def test_market_label(self):
        self.assertEqual([P.market_label(x) for x in (5.0, 4.99, -4.99, -5.0, np.nan)], ["상회", "부합", "부합", "하회", ""])

    def test_before_columns_only_allowed(self):
        pub = P.public_before(self._out(), "2026-10-12 21:50")
        self.assertEqual(list(pub.columns), list(P.PUBLIC_BEFORE.values()))
        self.assertEqual(len(pub), 3)                                         # E 없는 대조군 행 제외
        self.assertNotIn("(", "".join(pub["업종"].astype(str)))
        k = pub.set_index("기업")
        self.assertEqual(k.loc["나", "시장 예상 대비"], "부합")                 # 금호타이어형 -3%
        self.assertEqual(k.loc["가", "시장 예상 대비"], "하회")                 # 삼성전자형 -11.8%

    def test_after_hit(self):
        out = self._out()
        actual = pd.Series({"005930": 230e12, "073240": 1.28e12, "009150": 3.0e12})
        pub = P.public_after(out, "2026-10-12 21:50", actual)
        self.assertEqual(list(pub.columns), P.PUBLIC_COLUMNS)
        k = pub.set_index("기업")
        self.assertEqual(k.loc["가", "방향 적중"], "빗나감")                    # 예측 하회, 실제 230/200.3 = +14.8% 상회
        self.assertEqual(k.loc["나", "방향 적중"], "적중")                      # 둘 다 부합
        self.assertAlmostEqual(k.loc["나", "예측 오차(%)"], round((out.loc[1, "rev_E"] / 1.28e12 - 1) * 100, 1))

    def test_public_has_no_internal(self):
        out = self._out()
        for pub in (P.public_before(out, "2026-10-12 21:50"),
                    P.public_after(out, "2026-10-12 21:50", pd.Series({"005930": 2e14}))):
            for text in (pub.to_csv(index=False), P.public_html(pub, "레이더 2026Q3")):
                P.check_public(text)
                for c in O.JUDGE_COLUMNS + ["spread_E_pct", "z_drv_vs_trend", "shock_dev", "grade", "rev_cons", "jo_cons"]:
                    self.assertNotIn(c, text)
                for v in list(out["decision"]) + list(out["flag_E_spread"]):
                    if v:
                        self.assertNotIn(v, text)
                for v in out["jo_cons"].dropna():                              # 컨센서스 수치
                    self.assertNotIn(f"{v:.3f}", text)

    def test_guard_blocks_internal(self):
        out = self._out()
        for bad in [C.table(C.OUTPUT_COLUMNS).to_csv(index=False), B.desc_html(C.OUTPUT_COLUMNS),
                    O.judgement(out).to_csv(index=False), "기업,판단\n가,후보(상향)", "기업,드라이버\n가,M1:854232",
                    "기업,원천\n가,export", "x " + C.COLDESC["drv_3m_yoy_pct"]["note"], "기업,등급\n가,A",
                    "기업,컨센서스(조원)\n가,1.2", "기업,모형\n가,TS", "기업,경보\n가,충격(상향)"]:
            with self.assertRaises(P.PublicLeak):
                P.check_public(bad)

    def test_save_public_with_sha(self):
        pub = P.public_before(self._out(), "2026-10-12 21:50")
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "record_2026Q3_x.csv"
            rec.write_text("a,b\n1,2\n", encoding="utf-8")
            from unittest import mock
            with mock.patch.object(P, "record_file", return_value=rec):
                r = P.save_public("2026Q3", "x", out_dir=Path(d) / "pub", pub=pub)
            from DATA.fdriver.live import sha256
            self.assertEqual(r["sha256"], sha256(rec))
            self.assertIn(r["sha256"], r["sha256_file"].read_text(encoding="utf-8"))
            self.assertFalse(list((Path(d) / "pub").glob("*.xlsx")))


if __name__ == "__main__":
    unittest.main()
