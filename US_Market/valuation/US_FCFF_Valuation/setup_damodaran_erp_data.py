
# ════════════════════════════════════════════════════════════
#  Damodaran ERP DB 셋업 — Jupyter 노트북에서 실행
# ════════════════════════════════════════════════════════════
#
# 사용법:
# 1. 빈 Jupyter 노트북 새로 만들기 (또는 기존 노트북에 새 cell 추가)
# 2. 이 코드 전체를 붙여넣기
# 3. Shift+Enter 로 실행
#
# 주의:
# - 이미 테이블이 있으면 CREATE TABLE IF NOT EXISTS 로 skip
# - INSERT 는 UNIQUE KEY (date) 에 의해 중복 row 막음
# ════════════════════════════════════════════════════════════

import sys, os
from pathlib import Path

# ── 경로 설정 ────────────────────────────────────────────
_CANDIDATE_ROOTS = [
    r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast",
    r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy",
]

def _setup_path():
    try:
        start = Path(__file__).resolve().parent
    except NameError:
        start = Path.cwd()
    for p in [start] + list(start.parents):
        if (p / "DATA").is_dir():
            root = str(p)
            if root not in sys.path:
                sys.path.insert(0, root)
            return root
    for cand in _CANDIDATE_ROOTS:
        if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, "DATA")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return cand
    raise EnvironmentError("DATA 폴더를 찾을 수 없습니다.")

_setup_path()

# ── DB 연결 ─────────────────────────────────────────────
import pymysql
from DATA.config import get_db_info

db_info = get_db_info()
print(f"[DB] {db_info.get('host')}:{db_info.get('port')} / {db_info.get('database')}")

def get_conn():
    return pymysql.connect(
        host        = db_info["host"],
        port        = int(db_info["port"]),
        user        = db_info["user"],
        password    = db_info["password"],
        database    = db_info["database"],
        charset     = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
    )

def run_sql(sql, params=None):
    """단일 SQL 실행"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            try:
                rows = cur.fetchall()
            except Exception:
                rows = None
        conn.commit()
        return rows, cur.rowcount
    finally:
        conn.close()

# ════════════════════════════════════════════════════════
# Step 1: 테이블 생성
# ════════════════════════════════════════════════════════
print("\n[Step 1] 테이블 생성 중...")

create_sql = """
CREATE TABLE IF NOT EXISTS damodaran_erp_data (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    date         DATE         NOT NULL  COMMENT '발표 시점 (월 첫 영업일)',
    rf           DECIMAL(8,5) NOT NULL  COMMENT '10Y T.Bond rate',
    erp          DECIMAL(8,5) NOT NULL  COMMENT 'Implied Equity Risk Premium',
    rm           DECIMAL(8,5) AS (rf + erp) STORED  COMMENT '= rf + erp',
    source       VARCHAR(50)  DEFAULT 'Damodaran',
    note         VARCHAR(200) DEFAULT NULL,
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_date (date),
    INDEX idx_date_desc (date DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Damodaran ERP & Rf 시점별 저장 (DCFModel 사용)'
"""
run_sql(create_sql)
print("  ✓ 테이블 'damodaran_erp_data' 생성 완료")

# ════════════════════════════════════════════════════════
# Step 2: Historical 데이터 입력
# ════════════════════════════════════════════════════════
print("\n[Step 2] Historical 데이터 입력 중...")

# 데이터 (date, rf, erp, note)
historical_data = [
    # 2026
    ('2026-04-01', 0.0455, 0.0440, '2026년 4월 (호영님이 실제 값으로 교체 권장)'),
    
    # 2025 (월별)
    ('2025-12-01', 0.0440, 0.0445, '2025년 12월'),
    ('2025-11-01', 0.0428, 0.0438, '2025년 11월'),
    ('2025-10-01', 0.0420, 0.0420, '2025년 10월'),
    ('2025-09-01', 0.0410, 0.0440, '2025년 9월'),
    ('2025-08-01', 0.0420, 0.0445, '2025년 8월'),
    ('2025-07-01', 0.0432, 0.0440, '2025년 7월'),
    ('2025-06-01', 0.0437, 0.0440, '2025년 6월'),
    ('2025-05-01', 0.0440, 0.0445, '2025년 5월'),
    ('2025-04-01', 0.0420, 0.0480, '2025년 4월 (관세 충격)'),
    ('2025-03-01', 0.0418, 0.0445, '2025년 3월'),
    ('2025-02-01', 0.0455, 0.0432, '2025년 2월'),
    ('2025-01-01', 0.0458, 0.0433, '2025년 1월'),
    
    # 2024 (분기별)
    ('2024-10-01', 0.0420, 0.0440, '2024년 4분기'),
    ('2024-07-01', 0.0436, 0.0411, '2024년 3분기'),
    ('2024-04-01', 0.0440, 0.0420, '2024년 2분기'),
    ('2024-01-01', 0.0388, 0.0460, '2024년 1분기'),
    
    # 2023 (분기별)
    ('2023-10-01', 0.0457, 0.0552, '2023년 4분기'),
    ('2023-07-01', 0.0381, 0.0501, '2023년 3분기'),
    ('2023-04-01', 0.0349, 0.0494, '2023년 2분기'),
    ('2023-01-01', 0.0388, 0.0594, '2023년 1분기'),
    
    # 2022 (분기별)
    ('2022-10-01', 0.0383, 0.0670, '2022년 4분기'),
    ('2022-07-01', 0.0301, 0.0537, '2022년 3분기'),
    ('2022-04-01', 0.0235, 0.0500, '2022년 2분기'),
    ('2022-01-01', 0.0151, 0.0428, '2022년 1분기'),
    
    # 2021 (반년별)
    ('2021-07-01', 0.0145, 0.0428, '2021년 7월'),
    ('2021-01-01', 0.0093, 0.0472, '2021년 1월'),
]

insert_sql = """
INSERT INTO damodaran_erp_data (date, rf, erp, source, note) 
VALUES (%s, %s, %s, 'Damodaran', %s)
ON DUPLICATE KEY UPDATE 
    rf = VALUES(rf), 
    erp = VALUES(erp), 
    note = VALUES(note)
"""

n_inserted = 0
n_updated = 0
for row in historical_data:
    _, rowcount = run_sql(insert_sql, row)
    if rowcount == 1:
        n_inserted += 1
    elif rowcount == 2:  # MySQL: ON DUPLICATE KEY UPDATE → rowcount=2
        n_updated += 1

print(f"  ✓ 입력 완료: 신규 {n_inserted}건, 업데이트 {n_updated}건")

# ════════════════════════════════════════════════════════
# Step 3: 검증
# ════════════════════════════════════════════════════════
print("\n[Step 3] 검증")

rows, _ = run_sql("SELECT date, rf, erp, rm, source, note FROM damodaran_erp_data ORDER BY date DESC LIMIT 5")
print("\n[가장 최근 5 row]")
for r in rows:
    print(f"  {r['date']}: Rf={float(r['rf'])*100:.2f}%  ERP={float(r['erp'])*100:.2f}%  Rm={float(r['rm'])*100:.2f}%  ({r['note']})")

rows, _ = run_sql("SELECT COUNT(*) AS n, MIN(date) AS oldest, MAX(date) AS latest FROM damodaran_erp_data")
print(f"\n[전체 통계]")
r = rows[0]
print(f"  총 row 수: {r['n']:,}")
print(f"  oldest   : {r['oldest']}")
print(f"  latest   : {r['latest']}")

print("\n" + "="*60)
print("  ✓ Damodaran ERP DB 셋업 완료")
print("="*60)
print("  다음 단계:")
print("  1. v10.6 노트북의 Cell 4.10 실행 — _get_erp_rf 가 이 데이터 사용")
print("  2. Cell 5.0 (단일 ticker, 예: APH) 로 검증")
print("  3. 매월 새 데이터 추가 (Damodaran 사이트에서 가져와서):")
print("     INSERT INTO damodaran_erp_data (date, rf, erp, source, note)")
print("     VALUES ('2026-05-01', 0.0XXX, 0.0XXX, 'Damodaran', '2026년 5월');")
