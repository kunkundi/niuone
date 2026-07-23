#!/usr/bin/env python3
"""SQLite storage for NiuOne dashboard push history.

This module is intentionally standalone so scripts, local workers, and small
dashboards can share the same durable history store without importing the
dashboard service.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from niuone_paths import get_dashboard_home

if __package__ == "app":
    from .storage.history_records import (
        content_hash,
        dumps,
        message_dedupe_key,
        row_to_dict,
        stable_id,
        x_metadata_priority,
        x_row_is_better,
    )
else:
    from storage.history_records import (
        content_hash,
        dumps,
        message_dedupe_key,
        row_to_dict,
        stable_id,
        x_metadata_priority,
        x_row_is_better,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_HOME = get_dashboard_home(PROJECT_ROOT)
DB_PATH = Path(os.environ.get("DASHBOARD_PUSH_HISTORY_DB") or str(DASHBOARD_HOME / "push_history.db"))
SCHEMA_VERSION = 3
MESSAGE_COLUMNS = (
    "id",
    "timestamp",
    "time_text",
    "category",
    "source_type",
    "source_id",
    "source_label",
    "platform",
    "platform_label",
    "chat",
    "chat_label",
    "external_id",
    "title",
    "content",
    "content_hash",
    "chars",
    "matched",
    "kind",
    "delivery_json",
    "metadata_json",
    "raw_path",
    "created_at",
    "updated_at",
)

_DB_INIT_LOCK = threading.Lock()
_INITIALIZED_DB_IDENTITIES: dict[str, tuple[int, int]] = {}


def _database_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        stat = db_path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _open_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    path_key = str(db_path.resolve())
    identity = _database_identity(db_path)
    if identity is None or _INITIALIZED_DB_IDENTITIES.get(path_key) != identity:
        with _DB_INIT_LOCK:
            identity = _database_identity(db_path)
            if identity is None or _INITIALIZED_DB_IDENTITIES.get(path_key) != identity:
                con = _open_connection(db_path)
                con.execute("PRAGMA journal_mode=WAL")
                init_db(con)
                refreshed_identity = _database_identity(db_path)
                if refreshed_identity is not None:
                    _INITIALIZED_DB_IDENTITIES[path_key] = refreshed_identity
                return con
    return _open_connection(db_path)


def init_db(con: sqlite3.Connection) -> None:
    # FTS triggers from the first DB draft made repeated upserts fragile on some
    # markdown/emoji payloads. The dashboard currently filters client-side, and
    # server-side search below uses indexed rows + LIKE, so drop those triggers
    # to keep ingestion reliable. A future FTS migration can rebuild the index
    # out-of-band without blocking delivery/history writes.
    con.executescript(
        """
        DROP TRIGGER IF EXISTS dashboard_messages_ai;
        DROP TRIGGER IF EXISTS dashboard_messages_ad;
        DROP TRIGGER IF EXISTS dashboard_messages_au;
        """
    )
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dashboard_messages (
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
            dedupe_key TEXT NOT NULL DEFAULT '',
            x_priority INTEGER NOT NULL DEFAULT 0,
            raw_path TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        """
    )
    schema_row = con.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    try:
        previous_schema_version = int(schema_row[0]) if schema_row else 0
    except (TypeError, ValueError):
        previous_schema_version = 0
    columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info(dashboard_messages)")
    }
    derived_columns_added = False
    if "dedupe_key" not in columns:
        con.execute(
            "ALTER TABLE dashboard_messages "
            "ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT ''"
        )
        derived_columns_added = True
    if "x_priority" not in columns:
        con.execute(
            "ALTER TABLE dashboard_messages "
            "ADD COLUMN x_priority INTEGER NOT NULL DEFAULT 0"
        )
        derived_columns_added = True
    if derived_columns_added or previous_schema_version < SCHEMA_VERSION:
        derived_rows = con.execute(
            """
            SELECT id, category, content, external_id, metadata_json
            FROM dashboard_messages
            """
        ).fetchall()
        con.executemany(
            """
            UPDATE dashboard_messages
            SET dedupe_key = ?, x_priority = ?
            WHERE id = ?
            """,
            [
                (
                    message_dedupe_key(
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                    ),
                    x_metadata_priority(row[1], row[4]),
                    row[0],
                )
                for row in derived_rows
            ],
        )
    con.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_source_external
            ON dashboard_messages(source_type, source_id, external_id)
            WHERE external_id IS NOT NULL AND external_id != '';

        CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_raw_path
            ON dashboard_messages(raw_path)
            WHERE raw_path IS NOT NULL AND raw_path != '';

        CREATE INDEX IF NOT EXISTS idx_dashboard_category_time
            ON dashboard_messages(category, timestamp DESC);

        CREATE INDEX IF NOT EXISTS idx_dashboard_category_external
            ON dashboard_messages(category, external_id);

        CREATE INDEX IF NOT EXISTS idx_dashboard_time
            ON dashboard_messages(timestamp DESC);

        CREATE INDEX IF NOT EXISTS idx_dashboard_platform_chat
            ON dashboard_messages(platform, chat);

        CREATE INDEX IF NOT EXISTS idx_dashboard_category_dedupe
            ON dashboard_messages(category, dedupe_key);

        CREATE INDEX IF NOT EXISTS idx_dashboard_dedupe_time
            ON dashboard_messages(dedupe_key, timestamp DESC);
        """
    )
    con.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


def register_query_functions(con: sqlite3.Connection) -> None:
    con.create_function("dashboard_message_dedupe_key", 4, message_dedupe_key, deterministic=True)
    con.create_function("dashboard_x_metadata_priority", 2, x_metadata_priority, deterministic=True)
    con.create_function("dashboard_x_row_is_better", 10, x_row_is_better, deterministic=True)


def upsert_message(con: sqlite3.Connection, message: dict[str, Any]) -> str:
    """Insert or update one dashboard message.

    Required: timestamp, category, source_type, content.
    Recommended for dedupe: external_id or raw_path. If id is absent, it is
    derived from source/external/content fields.
    """
    now = time.time()
    timestamp = float(message.get("timestamp") or now)
    content = str(message.get("content") or "")
    source_type = str(message.get("source_type") or message.get("source") or "unknown")
    source_id = str(message.get("source_id") or "")
    external_id = str(message.get("external_id") or "")
    raw_path = str(message.get("raw_path") or "")
    category = str(message.get("category") or "other")
    metadata_json = dumps(message.get("metadata"))
    base_key_parts = [source_type, source_id, external_id, raw_path]
    if external_id or raw_path:
        msg_id = str(message.get("id") or stable_id(*base_key_parts))
    else:
        msg_id = str(message.get("id") or stable_id(source_type, source_id, timestamp, content_hash(content)))
    if external_id:
        con.execute(
            """
            DELETE FROM dashboard_messages
            WHERE source_type = ? AND source_id = ? AND external_id = ? AND id != ?
            """,
            (source_type, source_id, external_id, msg_id),
        )
    if raw_path:
        con.execute(
            """
            DELETE FROM dashboard_messages
            WHERE raw_path = ? AND id != ?
            """,
            (raw_path, msg_id),
        )
    params = {
        "id": msg_id,
        "timestamp": timestamp,
        "time_text": message.get("time_text") or message.get("time") or "",
        "category": category,
        "source_type": source_type,
        "source_id": source_id,
        "source_label": message.get("source_label") or "",
        "platform": message.get("platform") or "",
        "platform_label": message.get("platform_label") or "",
        "chat": message.get("chat") or "",
        "chat_label": message.get("chat_label") or "",
        "external_id": external_id,
        "title": message.get("title") or "",
        "content": content,
        "content_hash": content_hash(content),
        "chars": int(message.get("chars") if message.get("chars") is not None else len(content)),
        "matched": 1 if message.get("matched") else 0,
        "kind": message.get("kind") or "",
        "delivery_json": dumps(message.get("delivery")),
        "metadata_json": metadata_json,
        "dedupe_key": message_dedupe_key(msg_id, category, content, external_id),
        "x_priority": x_metadata_priority(category, metadata_json),
        "raw_path": raw_path,
        "created_at": float(message.get("created_at") or now),
        "updated_at": now,
    }
    con.execute(
        """
        INSERT INTO dashboard_messages (
            id, timestamp, time_text, category, source_type, source_id, source_label,
            platform, platform_label, chat, chat_label, external_id, title, content,
            content_hash, chars, matched, kind, delivery_json, metadata_json,
            dedupe_key, x_priority, raw_path, created_at, updated_at
        ) VALUES (
            :id, :timestamp, :time_text, :category, :source_type, :source_id, :source_label,
            :platform, :platform_label, :chat, :chat_label, :external_id, :title, :content,
            :content_hash, :chars, :matched, :kind, :delivery_json, :metadata_json,
            :dedupe_key, :x_priority, :raw_path, :created_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
            timestamp=excluded.timestamp,
            time_text=excluded.time_text,
            category=excluded.category,
            source_type=excluded.source_type,
            source_id=excluded.source_id,
            source_label=excluded.source_label,
            platform=excluded.platform,
            platform_label=excluded.platform_label,
            chat=excluded.chat,
            chat_label=excluded.chat_label,
            external_id=excluded.external_id,
            title=excluded.title,
            content=excluded.content,
            content_hash=excluded.content_hash,
            chars=excluded.chars,
            matched=excluded.matched,
            kind=excluded.kind,
            delivery_json=excluded.delivery_json,
            metadata_json=excluded.metadata_json,
            dedupe_key=excluded.dedupe_key,
            x_priority=excluded.x_priority,
            raw_path=excluded.raw_path,
            updated_at=excluded.updated_at
        """,
        params,
    )
    return msg_id


def upsert_many(messages: Iterable[dict[str, Any]]) -> int:
    con = connect()
    count = 0
    try:
        with con:
            for message in messages:
                upsert_message(con, message)
                count += 1
    finally:
        con.close()
    return count


def query_messages(
    *,
    category: str | None = None,
    chat: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    con = connect()
    try:
        register_query_functions(con)
        where = []
        params: list[Any] = []
        if category:
            where.append("m.category = ?")
            params.append(category)
        if chat:
            where.append("m.chat = ?")
            params.append(chat)
        if q:
            like = f"%{q}%"
            where.append("(m.title LIKE ? OR m.content LIKE ? OR m.source_label LIKE ? OR m.chat_label LIKE ? OR m.source_id LIKE ?)")
            params.extend([like, like, like, like, like])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        categories = {
            str(row["category"]): int(row["count"] or 0)
            for row in con.execute(
                """
                SELECT
                    m.category,
                    COUNT(DISTINCT COALESCE(NULLIF(m.dedupe_key, ''), m.id)) AS count
                FROM dashboard_messages m
                GROUP BY m.category
                """
            )
        }
        if not chat and not q and not category:
            matched_total = sum(categories.values())
        elif not chat and not q and category:
            matched_total = int(categories.get(category, 0))
        else:
            matched_total = int(
                con.execute(
                    f"""
                    SELECT COUNT(DISTINCT COALESCE(NULLIF(m.dedupe_key, ''), m.id))
                    FROM dashboard_messages m
                    {where_sql}
                    """,
                    params,
                ).fetchone()[0]
                or 0
            )
        row_limit = max(int(limit), 0) if limit is not None else -1
        row_offset = max(int(offset or 0), 0)
        column_select = ", ".join(f"m.{column}" for column in MESSAGE_COLUMNS)
        ranked_column_select = ", ".join(f"ranked.{column}" for column in MESSAGE_COLUMNS)
        rows = []
        if row_limit != 0:
            if category == "x_monitor" and not chat and not q:
                # X pages are small and ordered by time. Use the time index for
                # pagination and probe only same-post candidates for legacy
                # duplicate resolution instead of ranking the entire history.
                rows = con.execute(
                    f"""
                    SELECT {column_select}
                    FROM dashboard_messages m INDEXED BY idx_dashboard_category_time
                    LEFT JOIN dashboard_messages group_head
                      ON group_head.id = CASE
                        WHEN COALESCE(m.external_id, '') = '' THEN m.id
                        ELSE (
                            SELECT head.id
                            FROM dashboard_messages head INDEXED BY idx_dashboard_category_external
                            WHERE head.category = 'x_monitor'
                              AND head.external_id = m.external_id
                            ORDER BY
                                head.timestamp DESC,
                                CASE WHEN head.kind = 'cron_output' THEN 0 ELSE 1 END ASC,
                                length(COALESCE(head.content, '')) DESC,
                                head.id DESC
                            LIMIT 1
                        )
                      END
                    WHERE m.category = 'x_monitor'
                      AND (
                        COALESCE(m.external_id, '') = ''
                        OR NOT EXISTS (
                            SELECT 1
                            FROM dashboard_messages better INDEXED BY idx_dashboard_category_external
                            WHERE better.category = 'x_monitor'
                              AND better.external_id = m.external_id
                              AND better.id != m.id
                              AND dashboard_x_row_is_better(
                                  better.metadata_json, better.kind, better.content, better.timestamp, better.id,
                                  m.metadata_json, m.kind, m.content, m.timestamp, m.id
                              ) = 1
                        )
                      )
                    ORDER BY
                        group_head.timestamp DESC,
                        CASE WHEN group_head.kind = 'cron_output' THEN 0 ELSE 1 END ASC,
                        length(COALESCE(group_head.content, '')) DESC,
                        group_head.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (row_limit, row_offset),
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                WITH base AS (
                    SELECT
                        {column_select},
                        COALESCE(NULLIF(m.dedupe_key, ''), m.id) AS dedupe_key,
                        m.x_priority AS x_priority,
                        CASE WHEN m.kind = 'cron_output' THEN 0 ELSE 1 END AS kind_priority,
                        length(COALESCE(m.content, '')) AS content_len
                    FROM dashboard_messages m
                    {where_sql}
                ),
                ranked AS (
                    SELECT
                        base.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY base.dedupe_key
                            ORDER BY
                                base.x_priority ASC,
                                base.kind_priority ASC,
                                base.content_len DESC,
                                base.timestamp DESC,
                                base.id DESC
                        ) AS best_rank,
                        FIRST_VALUE(base.timestamp) OVER (
                            PARTITION BY base.dedupe_key
                            ORDER BY
                                base.timestamp DESC,
                                base.kind_priority ASC,
                                base.content_len DESC,
                                base.id DESC
                        ) AS group_timestamp,
                        FIRST_VALUE(base.kind_priority) OVER (
                            PARTITION BY base.dedupe_key
                            ORDER BY
                                base.timestamp DESC,
                                base.kind_priority ASC,
                                base.content_len DESC,
                                base.id DESC
                        ) AS group_kind_priority,
                        FIRST_VALUE(base.content_len) OVER (
                            PARTITION BY base.dedupe_key
                            ORDER BY
                                base.timestamp DESC,
                                base.kind_priority ASC,
                                base.content_len DESC,
                                base.id DESC
                        ) AS group_content_len,
                        FIRST_VALUE(base.id) OVER (
                            PARTITION BY base.dedupe_key
                            ORDER BY
                                base.timestamp DESC,
                                base.kind_priority ASC,
                                base.content_len DESC,
                                base.id DESC
                        ) AS group_id
                    FROM base
                )
                SELECT {ranked_column_select}
                FROM ranked
                WHERE best_rank = 1
                ORDER BY
                    group_timestamp DESC,
                    group_kind_priority ASC,
                    group_content_len DESC,
                    group_id DESC
                LIMIT ? OFFSET ?
                """,
                    [*params, row_limit, row_offset],
                ).fetchall()
        platforms = [
            row["platform"]
            for row in con.execute("SELECT DISTINCT platform FROM dashboard_messages WHERE platform != '' ORDER BY platform")
        ]
        chats = [
            row["chat"]
            for row in con.execute("SELECT DISTINCT chat FROM dashboard_messages WHERE chat != '' ORDER BY chat")
        ]
        total = sum(categories.values())
        return {
            "total": total,
            "matched_total": matched_total,
            "categories": categories,
            "platforms": platforms,
            "chats": chats,
            "records": [row_to_dict(row) for row in rows],
        }
    finally:
        con.close()


if __name__ == "__main__":
    con = connect()
    try:
        print(DB_PATH)
        print("messages", con.execute("SELECT COUNT(*) FROM dashboard_messages").fetchone()[0])
    finally:
        con.close()
