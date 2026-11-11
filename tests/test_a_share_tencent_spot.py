#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

from reports.a_share import tencent_spot


class AShareTencentSpotTests(unittest.TestCase):
    def test_fetch_parses_full_market_quote_shape_and_enforces_minimum(self):
        parts = [""] * 38
        parts[1] = "浦发银行"
        parts[2] = "600000"
        parts[3] = "9.18"
        parts[4] = "9.06"
        parts[5] = "9.04"
        parts[30] = "20260713113000"
        parts[32] = "1.32"
        parts[35] = "9.18/605475/553722235"
        parts[37] = "55372"
        body = 'v_sh600000="' + "~".join(parts) + '";'

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return body.encode("gb18030")

        original_symbols = tencent_spot._symbols
        original_urlopen = tencent_spot.urlopen
        original_workers = os.environ.get("TEST_TENCENT_WORKERS")
        try:
            tencent_spot._symbols = lambda: ["sh600000"]
            tencent_spot.urlopen = lambda *_args, **_kwargs: Response()
            os.environ["TEST_TENCENT_WORKERS"] = "1"
            with tempfile.TemporaryDirectory() as temp_dir:
                rows, warning = tencent_spot.fetch_tencent_spot_snapshot(
                    Path(temp_dir), env_prefix="TEST_TENCENT", min_rows=1, listed_codes={'600000'}
                )
                rejected, issue = tencent_spot.fetch_tencent_spot_snapshot(
                    Path(temp_dir), env_prefix="TEST_TENCENT", min_rows=2, listed_codes={'600000'}
                )
        finally:
            tencent_spot._symbols = original_symbols
            tencent_spot.urlopen = original_urlopen
            if original_workers is None:
                os.environ.pop("TEST_TENCENT_WORKERS", None)
            else:
                os.environ["TEST_TENCENT_WORKERS"] = original_workers

        self.assertIsNone(warning)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600000")
        self.assertEqual(rows[0]["amount"], 553722235)
        quote_time = datetime.fromtimestamp(rows[0]["quote_ts"], tencent_spot.CN_TZ)
        self.assertEqual(quote_time.strftime("%Y%m%d%H%M%S"), "20260713113000")
        self.assertEqual(rejected, [])
        self.assertIn("低于完整性下限 2 只", issue)


if __name__ == "__main__":
    unittest.main()
