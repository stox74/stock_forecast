"""SQLite 저장 계층.

테이블 구조
- revenue  : 실제 발표된 월 매출 (종목, 연, 월 기준 UPSERT)
- forecast : 예측 이력. '어느 시점(basis)의 데이터로 어느 달(target)을 예측했는가'를
             함께 저장해 매월 예측 기록을 추적할 수 있게 한다.
"""
import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS revenue (
    company_id   TEXT NOT NULL,
    company_name TEXT,
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    revenue      INTEGER,           -- 단위: NTD 천 (MOPS 원본 단위)
    mom_pct      REAL,              -- 전월 대비 %
    yoy_pct      REAL,              -- 전년 동월 대비 %
    source       TEXT,              -- 'mops' | 'openapi'
    collected_at TEXT,
    PRIMARY KEY (company_id, year, month)
);

CREATE TABLE IF NOT EXISTS forecast (
    company_id   TEXT NOT NULL,
    basis_year   INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 연
    basis_month  INTEGER NOT NULL,  -- 예측에 사용한 마지막 실적의 월
    target_year  INTEGER NOT NULL,
    target_month INTEGER NOT NULL,
    predicted    REAL,
    lower_95     REAL,
    upper_95     REAL,
    model        TEXT,
    created_at   TEXT,
    PRIMARY KEY (company_id, basis_year, basis_month, target_year, target_month)
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_revenue(conn, rows):
    """rows: dict 리스트 (company_id, company_name, year, month, revenue, mom_pct, yoy_pct, source)"""
    now = _now()
    conn.executemany(
        """
        INSERT INTO revenue
            (company_id, company_name, year, month, revenue, mom_pct, yoy_pct, source, collected_at)
        VALUES
            (:company_id, :company_name, :year, :month, :revenue, :mom_pct, :yoy_pct, :source, :collected_at)
        ON CONFLICT (company_id, year, month) DO UPDATE SET
            company_name = excluded.company_name,
            revenue      = excluded.revenue,
            mom_pct      = excluded.mom_pct,
            yoy_pct      = excluded.yoy_pct,
            source       = excluded.source,
            collected_at = excluded.collected_at
        """,
        [{**r, "collected_at": now} for r in rows],
    )
    conn.commit()


def upsert_forecast(conn, rows):
    now = _now()
    conn.executemany(
        """
        INSERT INTO forecast
            (company_id, basis_year, basis_month, target_year, target_month,
             predicted, lower_95, upper_95, model, created_at)
        VALUES
            (:company_id, :basis_year, :basis_month, :target_year, :target_month,
             :predicted, :lower_95, :upper_95, :model, :created_at)
        ON CONFLICT (company_id, basis_year, basis_month, target_year, target_month)
        DO UPDATE SET
            predicted  = excluded.predicted,
            lower_95   = excluded.lower_95,
            upper_95   = excluded.upper_95,
            model      = excluded.model,
            created_at = excluded.created_at
        """,
        [{**r, "created_at": now} for r in rows],
    )
    conn.commit()


def load_revenue_series(conn, company_id):
    """특정 기업의 월 매출을 (year, month, revenue) 오름차순으로 반환."""
    cur = conn.execute(
        "SELECT year, month, revenue FROM revenue "
        "WHERE company_id = ? AND revenue IS NOT NULL ORDER BY year, month",
        (company_id,),
    )
    return cur.fetchall()


def load_forecasts(conn, company_id, basis=None):
    """예측 이력 조회. basis=(year, month) 지정 시 해당 시점 예측만."""
    q = ("SELECT basis_year, basis_month, target_year, target_month, "
         "predicted, lower_95, upper_95 FROM forecast WHERE company_id = ?")
    args = [company_id]
    if basis:
        q += " AND basis_year = ? AND basis_month = ?"
        args += list(basis)
    q += " ORDER BY basis_year, basis_month, target_year, target_month"
    return conn.execute(q, args).fetchall()


def latest_basis(conn, company_id):
    """가장 최근 실적의 (year, month)."""
    row = conn.execute(
        "SELECT year, month FROM revenue WHERE company_id = ? "
        "ORDER BY year DESC, month DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row
