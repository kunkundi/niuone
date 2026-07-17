#!/usr/bin/env python3
import sys
from pathlib import Path

_ENTRYPOINT_DIR = Path(__file__).resolve().parent
if str(_ENTRYPOINT_DIR) not in sys.path:
    sys.path.insert(0, str(_ENTRYPOINT_DIR))

from _bootstrap import run

run(
    globals(),
    "reports/a_share/iwencai_dragon_tiger_snapshot.py",
    "iwencai_dragon_tiger_snapshot.py",
)
