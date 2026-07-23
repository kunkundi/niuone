#!/usr/bin/env python3
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app"
COMPAT = SRC / "compat"
ENTRYPOINTS = SRC / "entrypoints"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(COMPAT))

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
        if "count(distinct coalesce(nullif(m.dedupe_key" in normalized:
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

    def test_general_message_query_does_not_call_python_dedupe_function(self):
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

        original_dedupe = push_history.message_dedupe_key
        try:
            push_history.message_dedupe_key = lambda *_args: (_ for _ in ()).throw(
                AssertionError("query must use the persisted dedupe key")
            )
            data = push_history.query_messages(limit=40)
        finally:
            push_history.message_dedupe_key = original_dedupe

        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["records"]), 1)

    def test_x_monitor_fast_page_keeps_preferred_legacy_duplicate(self):
        con = push_history.connect(self.db_path)
        try:
            with con:
                push_history.upsert_message(
                    con,
                    {
                        "timestamp": 1002,
                        "category": "x_monitor",
                        "source_type": "legacy",
                        "source_id": "lean-copy",
                        "external_id": "tweet-duplicate",
                        "content": "newer but lean copy",
                        "metadata": {"post": {}},
                    },
                )
                push_history.upsert_message(
                    con,
                    {
                        "timestamp": 1001,
                        "category": "x_monitor",
                        "source_type": "legacy",
                        "source_id": "rich-copy",
                        "external_id": "tweet-duplicate",
                        "content": "preferred copy with media",
                        "metadata": {"post": {"media": [{"url": "https://example.test/image.jpg"}]}},
                    },
                )
                push_history.upsert_message(
                    con,
                    {
                        "timestamp": 1001.5,
                        "category": "x_monitor",
                        "source_type": "x",
                        "source_id": "watchlist",
                        "external_id": "tweet-middle",
                        "content": "middle post",
                        "metadata": {"post": {}},
                    },
                )
        finally:
            con.close()

        data = push_history.query_messages(category="x_monitor", limit=10)

        self.assertEqual(data["matched_total"], 2)
        self.assertEqual(data["categories"]["x_monitor"], 2)
        self.assertEqual(len(data["records"]), 2)
        self.assertEqual(data["records"][0]["source_id"], "rich-copy")
        self.assertEqual(data["records"][1]["external_id"], "tweet-middle")

    def test_connect_reinitializes_database_replaced_at_same_path(self):
        con = push_history.connect(self.db_path)
        con.close()

        replacement = self.db_path.with_name("replacement.db")
        sqlite3.connect(replacement).close()
        replacement.replace(self.db_path)

        con = push_history.connect(self.db_path)
        try:
            indexes = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_dashboard_%'"
                )
            }
        finally:
            con.close()

        self.assertIn("idx_dashboard_category_external", indexes)
        self.assertEqual(push_history.query_messages(category="x_monitor", limit=10)["records"], [])

    def test_schema_upgrade_backfills_derived_keys_without_rewriting_records(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(
                """
                CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO schema_meta(key, value) VALUES('schema_version', '2');
                CREATE TABLE dashboard_messages (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    time_text TEXT,
                    category TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    source_label TEXT,
                    platform TEXT,
                    platform_label TEXT,
                    chat TEXT,
                    chat_label TEXT,
                    external_id TEXT,
                    title TEXT,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    chars INTEGER,
                    matched INTEGER NOT NULL DEFAULT 0,
                    kind TEXT,
                    delivery_json TEXT,
                    metadata_json TEXT,
                    raw_path TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                INSERT INTO dashboard_messages (
                    id, timestamp, category, source_type, external_id, content,
                    content_hash, metadata_json, created_at, updated_at
                ) VALUES (
                    'legacy-x', 1000, 'x_monitor', 'legacy', 'tweet-1',
                    'legacy body', 'hash', '{"post":{}}', 1000, 1000
                );
                """
            )
            con.commit()
            push_history.init_db(con)
            row = con.execute(
                "SELECT id, content, dedupe_key, x_priority FROM dashboard_messages"
            ).fetchone()
        finally:
            con.close()

        self.assertEqual(row[0], 'legacy-x')
        self.assertEqual(row[1], 'legacy body')
        self.assertEqual(row[2], 'x_monitor:tweet-1')
        self.assertEqual(row[3], 1)
        self.assertEqual(
            push_history.query_messages(category='x_monitor', limit=10)['total'],
            1,
        )


if __name__ == "__main__":
    unittest.main()
