from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from app.backtesting.replay_cache import ReplayTapeCache, build_replay_cache_key
from app.backtesting.selection import (
    HistoricalBar,
    SelectionReplayFrame,
    SelectionReplayTape,
    SelectionSignal,
)
from app.backtesting.service import NIUONE_REPLAY_SCORED_FIELDS


def _bar(*, industry: str = "白酒", close: float = 10.0) -> HistoricalBar:
    return HistoricalBar.from_value("sh600519", {
        "date": "2026-01-05",
        "open": 10,
        "high": max(10, close),
        "low": min(10, close),
        "close": close,
        "volume": 100,
        "industry": industry,
        "themes": ["消费", industry],
    })


class BacktestReplayCacheTests(unittest.TestCase):
    def test_compact_tape_covers_every_niuone_exit_scorer_field(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "backtesting" / "niuone_exits.py"
        ).read_text(encoding="utf-8")
        accessed = set(re.findall(r"scored\.get\(['\"]([^'\"]+)", source))
        self.assertTrue(accessed)
        self.assertEqual(accessed - set(NIUONE_REPLAY_SCORED_FIELDS), set())

    def _key(self, bar: HistoricalBar):
        return build_replay_cache_key(
            {"sh600519": (bar,)},
            protocol_version="niuone-backtest-v33",
            selector_id="niuone",
            strategy_ids=("niu_leader",),
            signal_start_date="2026-01-05",
            signal_end_date="2026-01-31",
            sources=("eastmoney", "tencent"),
            adjustment="qfq",
            stock_pool=("sh600519",),
            source_by_symbol={"sh600519": "eastmoney"},
        )

    def test_round_trips_compressed_tape_and_ignores_corruption(self):
        tape = SelectionReplayTape(
            frames={
                "2026-01-05": SelectionReplayFrame(
                    date="2026-01-05",
                    signals=(SelectionSignal(
                        "sh600519",
                        strategy_id="niu_leader",
                        score=9.2,
                        metadata={"scored": {"industry": "白酒"}},
                    ),),
                    scored={
                        "sh600519": {
                            "niu_leader": {"mainline_score": 88.0},
                        },
                    },
                ),
            },
            diagnostics={"signal_count": 1},
        )
        key = self._key(_bar())
        with tempfile.TemporaryDirectory(prefix="niuone-replay-cache-") as tmp:
            cache = ReplayTapeCache(Path(tmp))
            self.assertTrue(cache.store(key, tape))
            restored = cache.load(key)
            self.assertIsNotNone(restored)
            self.assertEqual(
                restored.frames["2026-01-05"].signals[0].strategy_id,
                "niu_leader",
            )
            self.assertEqual(
                restored.frames["2026-01-05"].scored["sh600519"]
                ["niu_leader"]["mainline_score"],
                88.0,
            )
            cache.path_for(key).write_bytes(b"not-a-gzip-stream")
            self.assertIsNone(cache.load(key))

    def test_key_changes_for_classification_or_bar_revision(self):
        baseline = self._key(_bar())
        classification_changed = self._key(_bar(industry="食品"))
        bar_changed = self._key(_bar(close=10.5))

        self.assertNotEqual(baseline.digest, classification_changed.digest)
        self.assertNotEqual(baseline.digest, bar_changed.digest)
        self.assertNotEqual(
            baseline.descriptor["classification_snapshot_hash"],
            classification_changed.descriptor["classification_snapshot_hash"],
        )

        alternate_source_order = build_replay_cache_key(
            {"sh600519": (_bar(),)},
            protocol_version="niuone-backtest-v33",
            selector_id="niuone",
            strategy_ids=("niu_leader",),
            signal_start_date="2026-01-05",
            signal_end_date="2026-01-31",
            sources=("tencent", "eastmoney"),
            adjustment="qfq",
            stock_pool=("sh600519",),
            source_by_symbol={"sh600519": "eastmoney"},
        )
        self.assertNotEqual(baseline.digest, alternate_source_order.digest)

    def test_key_reuses_preindexed_bar_mapping_without_changing_identity(self):
        bar = _bar()
        mapped = build_replay_cache_key(
            {"sh600519": {bar.date: bar}},
            protocol_version="niuone-backtest-v33",
            selector_id="niuone",
            strategy_ids=("niu_leader",),
            signal_start_date="2026-01-05",
            signal_end_date="2026-01-31",
            sources=("eastmoney", "tencent"),
            adjustment="qfq",
            stock_pool=("sh600519",),
            source_by_symbol={"sh600519": "eastmoney"},
        )

        self.assertEqual(mapped.digest, self._key(bar).digest)

    def test_build_lock_serializes_same_cache_key(self):
        key = self._key(_bar())
        with tempfile.TemporaryDirectory(prefix="niuone-replay-cache-") as tmp:
            cache = ReplayTapeCache(Path(tmp))
            with cache.build_lock(key) as first_acquired:
                with cache.build_lock(key, timeout_seconds=0) as second_acquired:
                    self.assertTrue(first_acquired)
                    self.assertFalse(second_acquired)
            with cache.build_lock(key, timeout_seconds=0) as acquired_after_release:
                self.assertTrue(acquired_after_release)


if __name__ == "__main__":
    unittest.main()
