# -*- coding: utf-8 -*-
# pytest 실행 시 investment_strategy 를 import 경로에 넣어 `DATA.fdriver` 로 불러오게 한다.
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
