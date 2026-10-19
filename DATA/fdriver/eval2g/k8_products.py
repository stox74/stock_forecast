# -*- coding: utf-8 -*-
"""2g K8 6-1: DART 사업보고서 '주요 제품' 표 추출 (2d k2_products.extract 그대로, 대상만 2g). DB 쓰기 없음.
캐시는 2d 와 공유 (2d/K2/cache/lists, 2d/K1/cache/annual_docs). 결과: 2g/K8/drivers/k8_products.csv, k8_products_status.csv.
이미 처리한 기업은 다시 받지 않는다.
python -m DATA.fdriver.eval2g.k8_products
"""
import logging
import time

import pandas as pd

from ..config import output_path
from ..eval2d import k2_products as P
from . import common as C

OUT = output_path("2g", "K8", "drivers")
log = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = C.read_targets()[["entity_id", "name"]].rename(columns={"entity_id": "ticker"})
    k1 = pd.read_csv(output_path("2d", "K1") / "k1_universe.csv", dtype={"ticker": str, "corp_code": str})
    t = t.merge(k1[["ticker", "corp_code"]], on="ticker", how="left")
    fs, fr = OUT / "k8_products_status.csv", OUT / "k8_products.csv"
    done = pd.read_csv(fs, dtype={"ticker": str}) if fs.exists() else pd.DataFrame(columns=["ticker"])
    rows_old = pd.read_csv(fr, dtype={"ticker": str}) if fr.exists() else pd.DataFrame()
    st, rows = [], []
    todo = t[~t["ticker"].isin(set(done["ticker"]))]
    t0 = time.time()
    for i, r in enumerate(todo.itertuples()):
        try:
            res, rr = P.extract(r.ticker, r.corp_code)
        except Exception as e:      # noqa: BLE001  한 기업 실패는 기록만
            res, rr = {"status": "error", "error": repr(e)[:200]}, []
            time.sleep(3)
        st.append({"ticker": r.ticker, "name": r.name, **res})
        rows += rr
        if (i + 1) % 25 == 0 or i + 1 == len(todo):
            pd.concat([done, pd.DataFrame(st)], ignore_index=True).to_csv(fs, index=False, encoding="utf-8-sig")
            pd.concat([rows_old, pd.DataFrame(rows)], ignore_index=True).to_csv(fr, index=False, encoding="utf-8-sig")
            log.info("%d/%d (%.0fs)", i + 1, len(todo), time.time() - t0)
    s = pd.read_csv(fs, dtype={"ticker": str})
    print(s["status"].value_counts().to_dict(), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
