# -*- coding: utf-8 -*-
"""호영님 확인용: 2c 작업 2b 결과 (DB 읽기만).

실행 (저장소 루트 investment_strategy 에서):
    python -m DATA.fdriver.stage2c.check_step2b
1. 2b 에서 수집한 코드의 DB 기간 (korea_monthly_trade_data, expDlr)
2. 바뀐 9개 기업의 fd_nowcast 행 수와 마지막 분기
"""
import pandas as pd

from .. import db
from ..config import output_path


def main():
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 100)
    codes = open(output_path("2c", "step2b", "b_collect") / "target_codes.txt", encoding="utf-8").read().split()
    f, p = db.in_clause("c", codes)
    cov = db.read_sql(f"SELECT root_hs_code code, MIN(date) first, MAX(date) last, COUNT(DISTINCT date) n_months "
                      f"FROM korea_monthly_trade_data WHERE indicator='expDlr' AND root_hs_code IN {f} "
                      f"GROUP BY root_hs_code ORDER BY root_hs_code", p)
    print(f"== 1. 수집 코드 {len(codes)}개 중 DB 에 있는 것 {len(cov)}개, 2026-08 까지 있는 것 "
          f"{int((pd.to_datetime(cov['last']) >= '2026-08-31').sum())}개")
    print(cov.to_string(index=False))
    ids = list(pd.read_csv(output_path("2c", "step2b", "c_codes") / "changed_entities.csv", dtype=str)["entity_id"])
    f, p = db.in_clause("e", ids)
    nc = db.read_sql(f"SELECT entity_id, COUNT(*) n_rows, MAX(fiscal_quarter) last_q, "
                     f"SUM(model_id='M4:export' AND checkpoint LIKE '%%pre5%%') n_m4_pre5 "
                     f"FROM fd_nowcast WHERE entity_id IN {f} GROUP BY entity_id", p)
    print("\n== 2. 바뀐 기업 fd_nowcast")
    print(nc.to_string(index=False))


if __name__ == "__main__":
    main()
