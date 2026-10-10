# -*- coding: utf-8 -*-
"""R1 (d) 모의 실행: 2026Q2 로 기록 -> 같은 키 다시 기록 시도(멈춰야 함) -> 맞춰 보기·성적표. 임시 테이블 사용.

임시 테이블 fd_tmp_radar_record_mock, fd_tmp_radar_models_mock 에 쓰고, 끝에 지운다 (실전 기록 테이블은 건드리지 않음).
2026Q3 는 기록하지 않고, 오늘 기준 레이더(미리보기)로 '이미 발표한 기업' 목록만 만든다.
실행: python -m DATA.fdriver.radar.d_mock_record
"""
import pandas as pd

from .. import db
from ..config import output_path
from . import build as B
from . import record as R
from . import scorecard as S

TMP = {"record": "fd_tmp_radar_record_mock", "models": "fd_tmp_radar_models_mock"}
OUT = output_path("radar", "d_mock")


def main():
    log = []
    try:
        r1 = R.record("2026Q2", tables=TMP, require_clean=False, exclude_announced=False, out_dir=OUT / "records")
        log.append({"step": "1 기록", "result": f"record {r1['record_rows']}행, model {r1['model_rows']}행, csv {r1['csv'].name}, "
                                               f"sha256 {r1['sha256'][:16]}"})
        try:
            R.record("2026Q2", tables=TMP, require_clean=False, exclude_announced=False, out_dir=OUT / "records")
            log.append({"step": "2 같은 키 다시 기록", "result": "멈추지 않음 (오류)"})
        except db.FdWriteViolation as ex:
            n = db.read_sql(f"SELECT COUNT(*) n FROM `{TMP['record']}`")["n"].iloc[0]
            log.append({"step": "2 같은 키 다시 기록", "result": f"멈춤 (FdWriteViolation: {str(ex)[:80]}), 테이블 행 {n} 그대로"})
        c = S.compare(S.load("2026Q2", table=TMP["record"]))
        c.to_csv(OUT / "mock_compare_2026Q2.csv", index=False, encoding="utf-8-sig")
        sc = S.scorecard(c)
        for k, v in sc.items():
            v.to_csv(OUT / f"mock_scorecard_{k}.csv", index=False, encoding="utf-8-sig")
        log.append({"step": "3 맞춰 보기", "result": f"비교 {int((c['status'] == '비교').sum())}곳, 대기 {int((c['status'] == '대기').sum())}곳"})
    finally:
        for tb in TMP.values():
            db.execute_fd_ddl(f"DROP TABLE IF EXISTS `{tb}`")
        log.append({"step": "4 정리", "result": "임시 테이블 삭제"})
    pd.DataFrame(log).to_csv(OUT / "mock_log.csv", index=False, encoding="utf-8-sig")

    # 2026Q3: 오늘 기준 미리보기 레이더와 이미 발표한 기업
    res = B.build("2026Q3")
    paths = B.save(res)
    tab = res["table"]
    t_map = {e: res["t"] for e in tab["entity_id"]}
    done = R.announced_before(tab, t_map)
    done.to_csv(OUT / "announced_2026Q3.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 220)
    print(pd.DataFrame(log).to_string(index=False))
    for k, v in sc.items():
        print("==", k)
        print(v.round(4).to_string(index=False))
    print("2026Q3 preview:", paths)
    print("이미 발표:", done.to_string(index=False))
    print(tab[["grade", "name", "g_M4", "g_E", "g_B2", "warnings", "announce_date", "announce_kind"]].to_string(index=False))


if __name__ == "__main__":
    main()
