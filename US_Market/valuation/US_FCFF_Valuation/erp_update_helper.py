# ════════════════════════════════════════════════════════════
#  Damodaran ERP 업데이트 헬퍼 — Jupyter 노트북에서 실행
# ════════════════════════════════════════════════════════════
#
# 사용 시나리오:
#   1. 매월 새로운 Damodaran ERP 값 추가
#   2. 기존 row 의 값 수정 (정확한 데이터로 교체)
#   3. 여러 row 한 번에 업데이트
#   4. 특정 날짜 row 삭제
#   5. 현재 DB 상태 조회
#
# 사용 방법:
#   1. 이 코드 전체를 Jupyter 노트북 cell 에 복사
#   2. Shift+Enter 로 함수 정의 실행
#   3. 아래 예시처럼 함수 호출
# ════════════════════════════════════════════════════════════

import sys, os
from pathlib import Path
import pandas as pd
import pymysql

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

from DATA.config import get_db_info

db_info = get_db_info()


def _get_conn():
    return pymysql.connect(
        host        = db_info["host"],
        port        = int(db_info["port"]),
        user        = db_info["user"],
        password    = db_info["password"],
        database    = db_info["database"],
        charset     = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
    )


# ════════════════════════════════════════════════════════════
#  헬퍼 함수
# ════════════════════════════════════════════════════════════

def add_or_update_erp(date, rf, erp, note=None, source="Damodaran"):
    """
    Damodaran ERP/Rf 값 추가 또는 업데이트 (UPSERT)
    
    Parameters
    ----------
    date : str
        'YYYY-MM-DD' 형식 (예: '2026-05-01')
    rf : float
        10Y T.Bond rate (소수점 형식, 예: 4.55% → 0.0455)
    erp : float
        Implied ERP (소수점 형식, 예: 4.40% → 0.0440)
    note : str, optional
        설명 (기본값: '{YYYY}년 {M}월 발표')
    source : str
        출처 (default: 'Damodaran')
    
    Returns
    -------
    str : 'inserted' or 'updated'
    """
    if note is None:
        ts = pd.Timestamp(date)
        note = f"{ts.year}년 {ts.month}월 발표"
    
    sql = """
        INSERT INTO damodaran_erp_data (date, rf, erp, source, note)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rf = VALUES(rf),
            erp = VALUES(erp),
            source = VALUES(source),
            note = VALUES(note)
    """
    
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 먼저 기존 row 존재 여부 확인
            cur.execute("SELECT COUNT(*) AS n FROM damodaran_erp_data WHERE date = %s", (date,))
            n_before = cur.fetchone()["n"]
            
            cur.execute(sql, (date, rf, erp, source, note))
        conn.commit()
        action = "updated" if n_before > 0 else "inserted"
    finally:
        conn.close()
    
    rm = rf + erp
    print(f"  ✓ {action.upper():>8s}  {date}  Rf={rf*100:.2f}%  ERP={erp*100:.2f}%  Rm={rm*100:.2f}%  ({note})")
    return action


def add_or_update_erp_batch(rows):
    """
    여러 row 한 번에 추가/업데이트
    
    Parameters
    ----------
    rows : list of tuples
        [(date, rf, erp, note), ...] 또는 [(date, rf, erp), ...]
    
    Examples
    --------
    >>> rows = [
    ...     ('2026-05-01', 0.0445, 0.0455, '2026년 5월'),
    ...     ('2026-06-01', 0.0440, 0.0450),  # note 생략 시 자동 생성
    ... ]
    >>> add_or_update_erp_batch(rows)
    """
    print(f"[Batch update] {len(rows)} row 처리 중...")
    n_inserted = 0
    n_updated = 0
    for row in rows:
        if len(row) == 4:
            date, rf, erp, note = row
        elif len(row) == 3:
            date, rf, erp = row
            note = None
        else:
            print(f"  ⚠ skip: {row} (예상 형식: (date, rf, erp[, note]))")
            continue
        action = add_or_update_erp(date, rf, erp, note)
        if action == "inserted":
            n_inserted += 1
        else:
            n_updated += 1
    print(f"\n  Summary: 신규 {n_inserted}건, 업데이트 {n_updated}건")


def delete_erp(date):
    """
    특정 날짜 row 삭제 (실수로 잘못 입력했을 때)
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM damodaran_erp_data WHERE date = %s", (date,))
            n = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    
    if n > 0:
        print(f"  ✓ DELETED  {date} ({n} row)")
    else:
        print(f"  ⚠ {date} 에 해당하는 row 없음")
    return n


def show_erp_table(n=10):
    """
    현재 DB 상태 조회 (최근 N row)
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT date, rf, erp, rm, source, note
                FROM damodaran_erp_data
                ORDER BY date DESC
                LIMIT {int(n)}
            """)
            rows = cur.fetchall()
            
            cur.execute("SELECT COUNT(*) AS n, MIN(date) AS oldest, MAX(date) AS latest FROM damodaran_erp_data")
            stats = cur.fetchone()
    finally:
        conn.close()
    
    print(f"[damodaran_erp_data 상태]")
    print(f"  총 row 수: {stats['n']:,}")
    print(f"  oldest   : {stats['oldest']}")
    print(f"  latest   : {stats['latest']}")
    print()
    print(f"[가장 최근 {n} row]")
    print(f"  {'date':<12s}  {'Rf':>8s}  {'ERP':>8s}  {'Rm':>8s}  {'note'}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*40}")
    for r in rows:
        print(f"  {str(r['date']):<12s}  "
              f"{float(r['rf'])*100:>7.2f}%  "
              f"{float(r['erp'])*100:>7.2f}%  "
              f"{float(r['rm'])*100:>7.2f}%  "
              f"{r['note'] or ''}")


print("[OK] ERP 헬퍼 함수 로드 완료")
print()
print("사용 가능한 함수:")
print("  add_or_update_erp(date, rf, erp, note=None)     - 단일 row UPSERT")
print("  add_or_update_erp_batch(rows)                    - 여러 row 한 번에")
print("  delete_erp(date)                                 - 특정 날짜 삭제")
print("  show_erp_table(n=10)                             - 현재 상태 조회")


# ════════════════════════════════════════════════════════════
#  사용 예시 (주석 해제 후 실행)
# ════════════════════════════════════════════════════════════

# ── 예시 1: 매월 새 ERP 추가 (가장 흔한 경우) ────────────
# add_or_update_erp(
#     date='2026-05-01',
#     rf=0.0445,    # 4.45% (Damodaran 사이트의 T.Bond rate)
#     erp=0.0455,   # 4.55% (Damodaran 사이트의 Implied ERP)
#     note='2026년 5월 발표'
# )

# ── 예시 2: 기존 row 값 수정 (잘못 입력한 값 정정) ──────
# add_or_update_erp(
#     date='2025-01-01',
#     rf=0.0458,    # 정확한 값
#     erp=0.0433,   # 정확한 값
#     note='2025년 1월 (정확한 Damodaran 값으로 수정)'
# )

# ── 예시 3: 여러 row 한 번에 업데이트 ────────────────────
# rows = [
#     ('2026-05-01', 0.0445, 0.0455, '2026년 5월'),
#     ('2026-06-01', 0.0440, 0.0450, '2026년 6월'),
# ]
# add_or_update_erp_batch(rows)

# ── 예시 4: 잘못 입력한 row 삭제 ─────────────────────────
# delete_erp('2026-05-01')

# ── 예시 5: 현재 DB 상태 확인 ────────────────────────────
# show_erp_table(n=12)   # 최근 12개월 (1년)
