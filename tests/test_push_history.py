#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
sys.path.insert(0, str(SRC))

import push_history


class GuardedConnection:
    def __init__(self, con: sqlite3.Connection):
        object.__setattr__(self, "_con", con)

    def __getattr__(self, name):
        return getattr(self._con, name)

    def __setattr__(self, name, value):
        setattr(self._con, name, value)

    def execute(self, sql, parameters=()):
        normalized = " ".join(str(sql).lower().split())
        if (
            "select m.* from dashboard_messages m" in normalized
            and " limit " not in normalized
        ):
            raise AssertionError("query_messages must not fetch all message rows before pagination")
        return self._con.execute(sql, parameters)


class CountingConnection(GuardedConnection):
    def __init__(self, con: sqlite3.Connection):
        super().__init__(con)
        object.__setattr__(self, "distinct_count_queries", 0)

    def execute(self, sql, parameters=()):
        normalized = " ".join(str(sql).lower().split())
        if "count(distinct dashboard_message_dedupe_key" in normalized:
            object.__setattr__(self, "distinct_count_queries", self.distinct_count_queries + 1)
        return super().execute(sql, parameters)


class PushHistoryQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "push_history.db"
        self.original_db_path = push_history.DB_PATH
        push_history.DB_PATH = self.db_path

    def tearDown(self):
        push_history.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_query_messages_applies_pagination_in_sql_before_fetching_rows(self):
        con = push_history.connect(self.db_path)
        try:
            with con:
                for i in range(80):
                    push_history.upsert_message(
                        con,
                        {
                            "timestamp": 1000 + i,
                            "category": "x_monitor",
                            "source_type": "x",
                            "source_id": "watchlist",
                            "external_id": f"tweet-{i}",
                            "content": f"tweet content {i}",
                        },
                    )
        finally:
            con.close()

        real_connect = push_history.sqlite3.connect

        def guarded_connect(path):
            con = real_connect(path)
            con.row_factory = sqlite3.Row
            push_history.init_db(con)
            return GuardedConnection(con)

        original_connect = push_history.connect
        push_history.connect = lambda path=None: guarded_connect(path or push_history.DB_PATH)
        try:
            data = push_history.query_messages(category="x_monitor", limit=5, offset=10)
        finally:
            push_history.connect = original_connect

        self.assertEqual(len(data["records"]), 5)
        self.assertEqual(data["matched_total"], 80)
        self.assertEqual(data["categories"]["x_monitor"], 80)

    def test_query_messages_limit_zero_returns_counts_without_records(self):
        con = push_history.connect(self.db_path)
        try:
            with con:
                push_history.upsert_message(
                    con,
                    {
                        "timestamp": 1000,
                        "category": "market_monitor",
                        "source_type": "cron",
                        "source_id": "market",
                        "content": "market snapshot",
                    },
                )
        finally:
            con.close()

        data = push_history.query_messages(limit=0)

        self.assertEqual(data["records"], [])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["matched_total"], 1)
        self.assertEqual(data["categories"]["market_monitor"], 1)

    def test_query_messages_limit_zero_reuses_category_counts_for_matched_total(self):
        con = push_history.connect(self.db_path)
        try:
            with con:
                for i in range(3):
                    push_history.upsert_message(
                        con,
                        {
                            "timestamp": 1000 + i,
                            "category": "market_monitor",
                            "source_type": "cron",
                            "source_id": "market",
                            "content": f"market snapshot {i}",
                        },
                    )
        finally:
            con.close()

        real_connect = push_history.sqlite3.connect
        counting_connection = None

        def counting_connect(path):
            nonlocal counting_connection
            con = real_connect(path)
            con.row_factory = sqlite3.Row
            push_history.init_db(con)
            counting_connection = CountingConnection(con)
            return counting_connection

        original_connect = push_history.connect
        push_history.connect = lambda path=None: counting_connect(path or push_history.DB_PATH)
        try:
            data = push_history.query_messages(limit=0)
        finally:
            push_history.connect = original_connect

        self.assertEqual(data["matched_total"], 3)
        self.assertIsNotNone(counting_connection)
        self.assertEqual(counting_connection.distinct_count_queries, 1)


if __name__ == "__main__":
    unittest.main()
