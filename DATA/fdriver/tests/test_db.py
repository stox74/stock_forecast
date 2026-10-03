# -*- coding: utf-8 -*-
"""db 안전장치 테스트.

- 읽기 함수가 쓰기 문장을 DB에 보내기 전에 거부하는지
- 쓰기 함수가 fd_ 이외 테이블을 거부하는지
- fdriver 코드 어디에도 fd_ 이외 테이블에 대한 쓰기 문장이 없는지 (코드 검사)
- (DB 접속 가능 시) 읽기 전용 세션으로 SELECT 가 되는지
"""
import os
import re
import unittest
from pathlib import Path

from . import conftest  # noqa: F401
from DATA.fdriver import db

PKG = Path(__file__).resolve().parents[1]


class TestReadGuard(unittest.TestCase):
    def test_accepts_reads(self):
        for sql in ["SELECT 1", "  select * from KSE_Price", "WITH x AS (SELECT 1) SELECT * FROM x",
                    "SHOW COLUMNS FROM `KSE_Price`", "/* c */ SELECT 1;", "(SELECT 1)"]:
            db.check_read_sql(sql)

    def test_rejects_writes(self):
        for sql in ["INSERT INTO KSE_Price VALUES (1)", "update KSE_Price set close=1",
                    "DELETE FROM korea_monthly_trade_data", "ALTER TABLE x ADD c INT",
                    "DROP TABLE x", "TRUNCATE x", "CREATE TABLE fd_x (a INT)", "REPLACE INTO x VALUES (1)",
                    "SELECT 1; DELETE FROM x", "SELECT * FROM x FOR UPDATE",
                    "SELECT * FROM x INTO OUTFILE '/tmp/a'", "-- SELECT\nDELETE FROM x", ""]:
            with self.assertRaises(db.ReadOnlyViolation, msg=sql):
                db.check_read_sql(sql)


class TestWriteGuard(unittest.TestCase):
    def test_fd_table_names(self):
        db.check_fd_table("fd_entity_master")
        for bad in ["KSE_Price", "korea_fs_data_from_DG", "fd_Entity", "xfd_a", "fd_a;drop", "", None]:
            with self.assertRaises(db.FdWriteViolation, msg=str(bad)):
                db.check_fd_table(bad)

    def test_ddl_targets(self):
        self.assertEqual(db.check_fd_ddl("CREATE TABLE IF NOT EXISTS `fd_entity_link` (a INT)"), ["fd_entity_link"])
        self.assertEqual(db.check_fd_ddl("ALTER TABLE fd_entity_master ADD COLUMN x INT"), ["fd_entity_master"])
        for bad in ["CREATE TABLE IF NOT EXISTS KSE_Price (a INT)", "ALTER TABLE korea_dart_corp_master ADD x INT",
                    "INSERT INTO investar.KSE_Price VALUES (1)", "SELECT 1"]:
            with self.assertRaises(db.FdWriteViolation, msg=bad):
                db.check_fd_ddl(bad)

    def test_std1_ddl_with_on_update_passes(self):
        ddl = """CREATE TABLE IF NOT EXISTS `fd_x` (
          `entity_id` VARCHAR(20) NOT NULL,
          `first_collected_at` DATETIME NULL,
          `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (`entity_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        self.assertEqual(db.check_fd_ddl(ddl), ["fd_x"])

    def test_fd_ddl_with_on_delete_cascade_passes(self):
        ddl = """CREATE TABLE IF NOT EXISTS fd_child (
          entity_id VARCHAR(20) NOT NULL,
          CONSTRAINT fk1 FOREIGN KEY (entity_id) REFERENCES fd_parent (entity_id)
            ON DELETE CASCADE ON UPDATE CASCADE
        )"""
        self.assertEqual(db.check_fd_ddl(ddl), ["fd_child"])

    def test_upsert_clause_is_not_a_write_target(self):
        sql = "INSERT INTO `fd_x` (`a`, `b`) VALUES (:a, :b) ON DUPLICATE KEY UPDATE `b` = VALUES(`b`)"
        self.assertEqual(db.write_targets(sql), ["fd_x"])

    def test_real_update_delete_still_detected(self):
        self.assertEqual(db.write_targets("UPDATE KSE_Price SET close = 1"), ["KSE_Price"])
        self.assertEqual(db.write_targets("DELETE FROM korea_fs_data_from_DG WHERE 1"), ["korea_fs_data_from_DG"])
        with self.assertRaises(db.FdWriteViolation):
            db.check_fd_ddl("CREATE TABLE KSE_Price2 (u TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)")
        with self.assertRaises(db.FdWriteViolation):
            db.check_fd_ddl("CREATE TABLE fd_ok (a INT); UPDATE KSE_Price SET close = 1")

    def test_entity_ddl_passes(self):
        from DATA.fdriver import entity
        self.assertEqual(db.check_fd_ddl(entity.DDL_MASTER), ["fd_entity_master"])
        self.assertEqual(db.check_fd_ddl(entity.DDL_LINK), ["fd_entity_link"])

    def test_write_fd_rejects_before_connecting(self):
        import pandas as pd
        with self.assertRaises(db.FdWriteViolation):
            db.write_fd(pd.DataFrame({"a": [1]}), "KSE_Price", ["a"])


class TestNoRawWritesInCode(unittest.TestCase):
    """db.py 를 제외한 fdriver 코드가 fd_ 이외 테이블에 쓰지 않는지 소스 검사."""
    FORBIDDEN_CALLS = re.compile(r"\.to_sql\(|_write_engine|create_engine\(|\.begin\(\)")

    def _sources(self):
        for p in PKG.rglob("*.py"):
            if p.name == "db.py" or "tests" in p.parts:
                continue
            yield p, p.read_text(encoding="utf-8")

    def test_write_statements_target_fd_only(self):
        for p, src in self._sources():
            for t in db.write_targets(src):
                self.assertTrue(t.startswith("fd_"), f"{p.name}: write to non-fd table '{t}'")

    def test_no_direct_engine_writes(self):
        for p, src in self._sources():
            self.assertIsNone(self.FORBIDDEN_CALLS.search(src), f"{p.name}: direct DB write path")

    def test_no_secrets_in_code(self):
        pat = re.compile(r"(password|passwd|api_key|apikey|secret)\s*[:=]\s*['\"][^'\"]+['\"]", re.I)
        for p in PKG.rglob("*.py"):
            self.assertIsNone(pat.search(p.read_text(encoding="utf-8")), p.name)


@unittest.skipIf(os.environ.get("FDRIVER_SKIP_DB") == "1", "DB tests disabled")
class TestReadOnlyIntegration(unittest.TestCase):
    def test_select_through_read_only_session(self):
        try:
            db.read_sql("SELECT 1 AS one")
        except Exception as e:  # 접속 불가 환경
            self.skipTest(f"DB unreachable: {e}")
        # MariaDB 10.x 는 tx_read_only, MySQL 8 은 transaction_read_only
        try:
            df = db.read_sql("SELECT @@session.tx_read_only AS ro, 1 AS one")
        except Exception:
            df = db.read_sql("SELECT @@session.transaction_read_only AS ro, 1 AS one")
        self.assertEqual(int(df["one"].iloc[0]), 1)
        self.assertEqual(int(df["ro"].iloc[0]), 1)

    def test_in_clause_keeps_leading_zero(self):
        frag, params = db.in_clause("hs", ["030341", "854232"])
        self.assertEqual(frag, "(:hs_0, :hs_1)")
        self.assertEqual(params["hs_0"], "030341")


if __name__ == "__main__":
    unittest.main()
