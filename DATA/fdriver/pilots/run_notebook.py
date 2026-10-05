# -*- coding: utf-8 -*-
"""시범 노트북 실행기.

저장소 안의 노트북(pilots/pilot_vN.ipynb)은 실행 결과 없이 두고, 실행된 사본을
OUTPUT_ROOT/stage1_pilot/vN/pilot_vN_executed.ipynb 로 저장한다.
(사용자 Jupyter 설정에 설치되지 않은 nbconvert 전처리기가 등록돼 있어 nbconvert --execute 대신 nbclient 를 쓴다.)

사용: python -m DATA.fdriver.pilots.run_notebook pilot_v2.ipynb   (investment_strategy 폴더에서)
"""
import re
import sys
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient

from DATA.fdriver.config import output_path

PILOTS_DIR = Path(__file__).resolve().parent


def clear_outputs(nb) -> int:
    n = 0
    for c in nb.cells:
        if c.cell_type == "code":
            n += len(c.get("outputs", []))
            c["outputs"] = []
            c["execution_count"] = None
    return n


def run(name: str, timeout: int = 1800) -> Path:
    src = PILOTS_DIR / name
    m = re.match(r"pilot_(v\d+)\.ipynb$", src.name)
    if not m:
        raise ValueError(f"expected pilot_vN.ipynb, got {src.name}")
    ver = m.group(1)
    nb = nbformat.read(src, as_version=4)
    clear_outputs(nb)
    t0 = time.time()
    client = NotebookClient(nb, timeout=timeout, kernel_name="python3",
                            resources={"metadata": {"path": str(PILOTS_DIR)}})
    try:
        client.execute()
    finally:
        dst = output_path("stage1_pilot", ver) / f"pilot_{ver}_executed.ipynb"
        nbformat.write(nb, dst)
    errs = sum(1 for c in nb.cells if c.cell_type == "code"
               for o in c.get("outputs", []) if o.get("output_type") == "error")
    print(f"executed {src.name} in {time.time() - t0:.0f}s, error cells {errs} -> {dst}")
    # 저장소 사본은 실행 결과 없이 유지
    clean = nbformat.read(src, as_version=4)
    if clear_outputs(clean):
        nbformat.write(clean, src)
    return dst


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "pilot_v2.ipynb")
