# -*- coding: utf-8 -*-
"""DB 접근.

- 읽기: read_sql. 읽기 전용 엔진(접속마다 SET SESSION TRANSACTION READ ONLY)으로만 실행하고,
  SELECT/WITH/SHOW/DESCRIBE/EXPLAIN 으로 시작하지 않는 문장은 DB에 보내기 전에 거부한다.
- 쓰기: write_fd, execute_fd_ddl 이 유일한 쓰기 경로이며 fd_ 테이블만 허용한다.
  다른 모듈은 엔진이나 커넥션으로 직접 쓰지 않는다 (tests/test_db.py 의 코드 검사로 확인).
"""
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import event, text

from .config import FD_TABLE_PREFIX, get_db_info

_READ_ENGINE = None
_WRITE_ENGINE = None

# 이번 프로세스에서 실행한 쓰기 기록 (세션 리포트용)
WRITE_LOG: List[Dict] = []

_READ_FIRST_WORDS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
_FORBIDDEN_IN_READ = re.compile(r"\b(INTO\s+OUTFILE|INTO\s+DUMPFILE|FOR\s+UPDATE|LOCK\s+IN\s+SHARE\s+MODE)\b", re.I)
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_FD_IDENT = re.compile(r"^" + re.escape(FD_TABLE_PREFIX) + r"[a-z0-9_]+$")
_WRITE_TARGET = re.compile(
    r"\b(?:CREATE\s+(?:TEMPORARY\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"TRUNCATE(?:\s+TABLE)?|INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|"
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON)\s+(?!IF\b)`?([A-Za-z0-9_.]+)`?",
    re.I,
)
# 쓰기 명령이 아닌 UPDATE/DELETE 구문 (컬럼 기본값, 외래키 동작, upsert 절). 대상 추출 전에 지운다.
_NON_COMMAND_CLAUSES = re.compile(
    r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b|\bON\s+(?:UPDATE|DELETE)\b",
    re.I,
)


class ReadOnlyViolation(RuntimeError):
    pass


class FdWriteViolation(RuntimeError):
    pass


# ---------------------------------------------------------------- engines
def _make_engine():
    from DATA.config import get_engine
    return get_engine(get_db_info())


def read_engine():
    global _READ_ENGINE
    if _READ_ENGINE is None:
        eng = _make_engine()

        @event.listens_for(eng, "connect")
        def _set_read_only(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.close()

        _READ_ENGINE = eng
    return _READ_ENGINE


def _write_engine():
    global _WRITE_ENGINE
    if _WRITE_ENGINE is None:
        _WRITE_ENGINE = _make_engine()
    return _WRITE_ENGINE


# ---------------------------------------------------------------- read
def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"(--|#)[^\n]*", " ", sql)
    return sql.strip()


def check_read_sql(sql: str) -> None:
    """읽기 문장이 아니면 ReadOnlyViolation."""
    body = _strip_sql_comments(sql)
    if not body:
        raise ReadOnlyViolation("empty SQL")
    first = re.split(r"\s+", body, maxsplit=1)[0].upper().lstrip("(")
    if first not in _READ_FIRST_WORDS:
        raise ReadOnlyViolation(f"only read statements are allowed, got '{first}'")
    if _FORBIDDEN_IN_READ.search(body):
        raise ReadOnlyViolation("locking / file output clauses are not allowed")
    if ";" in body.rstrip(";"):
        raise ReadOnlyViolation("multiple statements are not allowed")


def read_sql(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    check_read_sql(sql)
    with read_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def check_ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"invalid identifier: {name!r}")
    return name


def table_columns(table: str) -> List[str]:
    df = read_sql(f"SHOW COLUMNS FROM `{check_ident(table)}`")
    return df.iloc[:, 0].tolist()


def table_exists(table: str) -> bool:
    df = read_sql(
        "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t",
        {"t": table},
    )
    return int(df["n"].iloc[0]) > 0


def in_clause(name: str, values: Sequence) -> Tuple[str, dict]:
    """IN (:name_0, :name_1, ...) 문자열과 파라미터. 값은 항상 바인딩한다 (코드 문자열 앞자리 0 보존)."""
    vals = list(values)
    if not vals:
        raise ValueError(f"empty value list for '{name}'")
    keys = [f"{name}_{i}" for i in range(len(vals))]
    return "(" + ", ".join(f":{k}" for k in keys) + ")", dict(zip(keys, vals))


# ---------------------------------------------------------------- write (fd_ only)
def check_fd_table(table: str) -> str:
    if not _FD_IDENT.match(table or ""):
        raise FdWriteViolation(f"writes are allowed only to '{FD_TABLE_PREFIX}*' tables, got {table!r}")
    return table


def write_targets(sql: str) -> List[str]:
    body = _NON_COMMAND_CLAUSES.sub(" ", _strip_sql_comments(sql))
    return [m.group(1).split(".")[-1] for m in _WRITE_TARGET.finditer(body)]


def check_fd_ddl(sql: str) -> List[str]:
    targets = write_targets(sql)
    if not targets:
        raise FdWriteViolation("no write target found in SQL")
    for t in targets:
        check_fd_table(t)
    return targets


def execute_fd_ddl(sql: str) -> None:
    """fd_ 테이블에 대한 CREATE/ALTER 등 DDL 실행."""
    targets = check_fd_ddl(sql)
    with _write_engine().begin() as conn:
        conn.execute(text(sql))
    WRITE_LOG.append({"at": datetime.now(), "op": "ddl", "tables": targets, "rows": 0})


def _clean_value(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        return v.item()
    return v


def write_fd(df: pd.DataFrame, table: str, key_cols: Iterable[str],
             stamp_collected: bool = True, chunk: int = 1000) -> int:
    """fd_ 테이블에 upsert (INSERT ... ON DUPLICATE KEY UPDATE).

    stamp_collected=True 면 first_collected_at(신규 행만), last_collected_at(항상)을 지금 시각으로 채운다.
    """
    check_fd_table(table)
    key_cols = list(key_cols)
    if df is None or df.empty:
        return 0
    data = df.copy()
    now = datetime.now().replace(microsecond=0)
    if stamp_collected:
        data["first_collected_at"] = now
        data["last_collected_at"] = now
    cols = [check_ident(c) for c in data.columns]
    missing = [k for k in key_cols if k not in cols]
    if missing:
        raise ValueError(f"key columns missing from frame: {missing}")
    update_cols = [c for c in cols if c not in key_cols and c != "first_collected_at"]

    col_sql = ", ".join(f"`{c}`" for c in cols)
    val_sql = ", ".join(f":{c}" for c in cols)
    upd_sql = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols) or f"`{key_cols[0]}` = `{key_cols[0]}`"
    sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({val_sql}) ON DUPLICATE KEY UPDATE {upd_sql}"

    records = [{c: _clean_value(v) for c, v in zip(cols, row)} for row in data[cols].itertuples(index=False, name=None)]
    n = 0
    with _write_engine().begin() as conn:
        for i in range(0, len(records), chunk):
            part = records[i:i + chunk]
            conn.execute(text(sql), part)
            n += len(part)
    WRITE_LOG.append({"at": now, "op": "upsert", "tables": [table], "rows": n})
    return n
