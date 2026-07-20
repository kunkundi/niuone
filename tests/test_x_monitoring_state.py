#!/usr/bin/env python3
from datetime import datetime, timedelta, timezone
import unittest

from app.monitoring.x.state import (
    X_SNOWFLAKE_EPOCH_MS,
    canonical_post_time,
    choose_latest_value,
    is_newer_post,
    normalize_post_time,
    parse_post_time,
    post_time_is_implausible,
    x_snowflake_post_time,
)


def snowflake_id(utc_time: datetime) -> str:
    timestamp_ms = int(utc_time.timestamp() * 1000)
    return str((timestamp_ms - X_SNOWFLAKE_EPOCH_MS) << 22)


class XMonitoringStateTests(unittest.TestCase):
    def test_parse_post_time_converts_utc_labels_and_iso_values_to_beijing(self):
        expected = datetime(2026, 7, 16, 23, 21)
        self.assertEqual(parse_post_time("2026-07-16 15:21:00 GMT"), expected)
        self.assertEqual(parse_post_time("2026-07-16 15:21:00 UTC"), expected)
        self.assertEqual(parse_post_time("2026-07-16T15:21:00Z"), expected)
        self.assertEqual(parse_post_time("2026-07-16 23:21:00 北京时间"), expected)

    def test_numeric_post_id_overrides_a_double_converted_model_time(self):
        post_id = snowflake_id(datetime(2026, 7, 16, 15, 21, tzinfo=timezone.utc))
        normalized = normalize_post_time(
            {"post_id": post_id, "time": "2026-07-17 07:21:00"},
            post_id,
        )
        self.assertEqual(normalized["time"], "2026-07-16 23:21:00")

    def test_latest_selection_uses_post_ids_instead_of_future_model_times(self):
        old_id = snowflake_id(datetime(2026, 7, 16, 15, 21, tzinfo=timezone.utc))
        new_id = snowflake_id(datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc))
        result = choose_latest_value(
            {"post_id": old_id, "time": "2026-07-17 07:21:00", "display_name": "测试"},
            [{"post_id": new_id, "time": "2026-07-16 23:30:00"}],
            "测试",
        )
        self.assertEqual(result["post_id"], new_id)
        self.assertEqual(result["time"], "2026-07-16 23:30:00")

    def test_future_snowflake_is_rejected_instead_of_overriding_model_time(self):
        now = datetime(2026, 7, 20, 8, tzinfo=timezone.utc)
        future_id = snowflake_id(datetime(2065, 12, 14, 22, 59, 6, tzinfo=timezone.utc))

        self.assertIsNone(x_snowflake_post_time(future_id, now=now))
        self.assertIsNone(canonical_post_time("2026-07-20 16:00:00", future_id, now=now))
        self.assertTrue(post_time_is_implausible("2026-07-20 16:00:00", future_id, now=now))

    def test_future_snowflake_cannot_advance_latest_cursor(self):
        now = datetime.now(timezone.utc)
        current_id = snowflake_id(now - timedelta(minutes=30))
        future_id = snowflake_id(now + timedelta(days=365))
        latest = {
            "post_id": current_id,
            "time": (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "display_name": "测试",
        }
        future_post = {
            "post_id": future_id,
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

        self.assertFalse(is_newer_post(future_post, latest, future_id))
        self.assertEqual(choose_latest_value(latest, [future_post], "测试"), latest)


if __name__ == "__main__":
    unittest.main()
