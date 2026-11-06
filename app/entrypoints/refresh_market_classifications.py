#!/usr/bin/env python3
import sys
from pathlib import Path

_ENTRYPOINT_DIR = Path(__file__).resolve().parent
if str(_ENTRYPOINT_DIR) not in sys.path:
    sys.path.insert(0, str(_ENTRYPOINT_DIR))

import _bootstrap  # initializes package and compatibility paths
from app.market_data.board_recovery import main

if __name__ == "__main__":
    raise SystemExit(main())
