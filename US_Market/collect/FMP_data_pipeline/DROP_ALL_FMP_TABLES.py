# -*- coding: utf-8 -*-
"""
DROP_ALL_FMP_TABLES.py
-----------------------
US_IS_from_FMP / US_BS_from_FMP / US_CF_from_FMP 세 테이블을 통째로 삭제합니다.
(TRUNCATE 아님 - 테이블 자체를 없앰. 다음에 US_FMP_FS_1_RUN_UPDATE.py 실행하면
 스크립트가 알아서 깨끗한 구조로 새로 만듭니다.)

실행:
    python DROP_ALL_FMP_TABLES.py
"""
import sys
from pathlib import Path


def _find_project_root(module_name="DATA", max_up=6):
    here = Path(__file__).resolve().parent
    for base in [here, *list(here.parents)[:max_up]]:
        if (base / module_name).is_dir():
            return base
    return None


try:
    from DATA.stock_invest_function import get_db_host
except ModuleNotFoundError:
    _root = _find_project_root("DATA")
    if _root is None:
        raise ModuleNotFoundError("DATA 패키지를 찾을 수 없습니다.")
    sys.path.insert(0, str(_root))
    from DATA.stock_invest_function import get_db_host

from sqlalchemy import create_engine, text

db_info = {"host": get_db_host(), "port": 3307, "user": "stox7412",
           "password": "Apt106503!~", "database": "investar"}

connection_string = (
    f"mysql+pymysql://{db_info['user']}:{db_info['password']}"
    f"@{db_info['host']}:{db_info['port']}/{db_info['database']}?charset=utf8mb4"
)

engine = create_engine(
    connection_string,
    connect_args={"connect_timeout": 10, "read_timeout": 3600, "write_timeout": 3600},
)

tables = ["US_IS_from_FMP", "US_BS_from_FMP", "US_CF_from_FMP",
          "US_IS_from_FMP_old_backup", "US_BS_from_FMP_old_backup", "US_CF_from_FMP_old_backup",
          "US_IS_from_FMP_dedup_tmp", "US_BS_from_FMP_dedup_tmp", "US_CF_from_FMP_dedup_tmp"]


def kill_zombie_sessions(engine):
    """
    그동안 Ctrl+C로 중단하면서 MySQL 서버 쪽엔 안 끊긴 채 남아있는 좀비 세션을 찾아서 종료.
    - 30초 넘게 Sleep 상태로 멈춰있는 연결 (예전 스크립트가 남긴 유령 커넥션)
    - 60초 넘게 걸려서 안 끝나는 쿼리 (DROP/ALTER 등이 못 끝나는 원인이 되는 락 보유자)
    자기 자신의 연결은 제외한다.
    """
    with engine.connect() as conn:
        my_id = conn.execute(text("SELECT CONNECTION_ID()")).scalar()
        rows = conn.execute(text("SHOW FULL PROCESSLIST")).fetchall()

        killed = []
        for row in rows:
            pid, command, time_s, info = row[0], row[4], row[5], row[7]
            if pid == my_id:
                continue
            is_zombie_sleep = (command == "Sleep" and time_s and time_s > 30)
            is_stuck_query = (command == "Query" and time_s and time_s > 60)
            if is_zombie_sleep or is_stuck_query:
                try:
                    conn.execute(text(f"KILL {pid}"))
                    print(f"  [좀비 세션 종료] pid={pid} (Command={command}, {time_s}초, Info={str(info)[:80]})")
                    killed.append(pid)
                except Exception as e:
                    print(f"  [종료 실패] pid={pid}: {str(e)[:150]}")
        if not killed:
            print("  좀비 세션 없음")
        return killed


print("=" * 60)
print("좀비 세션(예전에 남은 유령 연결) 확인 및 정리")
print("=" * 60)
try:
    kill_zombie_sessions(engine)
except Exception as e:
    print(f"  ⚠️ 좀비 세션 확인 실패 (권한 문제일 수 있음): {str(e)[:200]}")
    print("  일단 DROP은 계속 시도합니다.")

print("=" * 60)
print("테이블 전체 삭제 시작 (DROP TABLE)")
print("=" * 60)

with engine.connect() as conn:
    for t in tables:
        try:
            conn.execute(text(f"DROP TABLE IF EXISTS `{t}`"))
            conn.commit()
            print(f"  ✓ {t} 삭제됨 (또는 원래 없었음)")
        except Exception as e:
            print(f"  ⚠️ {t} 삭제 실패: {str(e)[:150]}")

engine.dispose()
print("\n완료. 이제 이걸 실행하세요:")
print("  python US_FMP_FS_1_RUN_UPDATE.py --quarters 60")
