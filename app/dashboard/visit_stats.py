"""Persistent visit counters for the Dashboard composition layer."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable


Clock = Callable[[], float]
Migration = Callable[[sqlite3.Connection], bool]


def database_signature(stats_db: Path, legacy_stats_db: Path) -> tuple[Any, ...]:
    """Describe both database files strongly enough to detect replacement."""

    try:
        stats_stat = stats_db.stat()
        stats_marker: tuple[int, int] | None = (stats_stat.st_dev, stats_stat.st_ino)
    except OSError:
        stats_marker = None
    try:
        legacy_stat = legacy_stats_db.stat()
        legacy_marker: tuple[int, int, int, int] | None = (
            legacy_stat.st_dev,
            legacy_stat.st_ino,
            legacy_stat.st_mtime_ns,
            legacy_stat.st_size,
        )
    except OSError:
        legacy_marker = None
    return (
        str(stats_db.resolve()),
        stats_marker,
        str(legacy_stats_db.resolve()),
        legacy_marker,
    )


def sqlite_table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def migrate_legacy_database(
    connection: sqlite3.Connection,
    *,
    stats_db: Path,
    legacy_stats_db: Path,
    migration_key: str,
    now: Clock,
    warn: Callable[[str], None],
) -> bool:
    """Append legacy counters once without replacing newer real records."""

    if legacy_stats_db == stats_db or not legacy_stats_db.exists():
        return True
    if connection.execute(
        "SELECT 1 FROM stats_migrations WHERE key=?",
        (migration_key,),
    ).fetchone():
        return True

    try:
        with closing(sqlite3.connect(legacy_stats_db)) as legacy:
            has_visit_stats = sqlite_table_exists(legacy, "visit_stats")
            has_unique_visitors = sqlite_table_exists(legacy, "unique_visitors")
            if not has_visit_stats and not has_unique_visitors:
                return True

            legacy_views = 0
            legacy_updated_at = 0.0
            if has_visit_stats:
                visit_row = legacy.execute(
                    "SELECT value, updated_at FROM visit_stats WHERE key='home_views'"
                ).fetchone()
                if visit_row:
                    legacy_views = int(visit_row[0] or 0)
                    legacy_updated_at = float(visit_row[1] or 0.0)

            legacy_visitors = []
            if has_unique_visitors:
                legacy_visitors = legacy.execute(
                    "SELECT visitor_hash, first_seen_at, last_seen_at FROM unique_visitors"
                ).fetchall()
    except sqlite3.Error as exc:
        warn(f"访问统计迁移跳过：无法读取旧统计库 {legacy_stats_db}: {exc}")
        return False

    current_row = connection.execute(
        "SELECT value, updated_at FROM visit_stats WHERE key='home_views'"
    ).fetchone()
    current_views = int(current_row[0] or 0) if current_row else 0
    current_updated_at = float(current_row[1] or 0.0) if current_row else 0.0
    if legacy_views > current_views:
        migrated_views = legacy_views + current_views
    else:
        migrated_views = current_views
    if migrated_views or current_row:
        connection.execute(
            "INSERT INTO visit_stats(key,value,updated_at) VALUES('home_views',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (migrated_views, max(legacy_updated_at, current_updated_at, now())),
        )

    connection.executemany(
        "INSERT INTO unique_visitors(visitor_hash,first_seen_at,last_seen_at) VALUES(?,?,?) "
        "ON CONFLICT(visitor_hash) DO UPDATE SET "
        "first_seen_at=MIN(unique_visitors.first_seen_at,excluded.first_seen_at), "
        "last_seen_at=MAX(unique_visitors.last_seen_at,excluded.last_seen_at)",
        legacy_visitors,
    )
    connection.execute(
        "INSERT OR REPLACE INTO stats_migrations(key,completed_at) VALUES(?,?)",
        (migration_key, now()),
    )
    return True


def ensure_database(
    *,
    stats_db: Path,
    legacy_stats_db: Path,
    initialized_signature: tuple[Any, ...] | None,
    lock: Any,
    migrate_legacy: Migration,
    now: Clock,
) -> tuple[Any, ...] | None:
    """Initialize the statistics schema and return its current file signature."""

    stats_db.parent.mkdir(parents=True, exist_ok=True)
    signature = database_signature(stats_db, legacy_stats_db)
    if initialized_signature == signature and stats_db.exists():
        return initialized_signature
    with lock:
        signature = database_signature(stats_db, legacy_stats_db)
        if initialized_signature == signature and stats_db.exists():
            return initialized_signature
        with closing(sqlite3.connect(stats_db, timeout=5.0)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS visit_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS unique_visitors (
                    visitor_hash TEXT PRIMARY KEY,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS stats_migrations (
                    key TEXT PRIMARY KEY,
                    completed_at REAL NOT NULL
                )
            """)
            migration_ready = migrate_legacy(connection)
            current_time = now()
            unique_count = int(
                connection.execute("SELECT COUNT(*) FROM unique_visitors").fetchone()[0]
                or 0
            )
            connection.execute(
                "INSERT INTO visit_stats(key,value,updated_at) VALUES('home_unique',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (unique_count, current_time),
            )
            connection.commit()
        if not migration_ready:
            return None
        return database_signature(stats_db, legacy_stats_db)


def increment_visit_count(
    visitor_id: str,
    *,
    stats_db: Path,
    lock: Any,
    ensure_initialized: Callable[[], None],
    hash_visitor: Callable[[str], str],
    now: Clock,
) -> dict[str, int]:
    """Increment page views and unique visitors in one transaction."""

    ensure_initialized()
    current_time = now()
    visitor_hash = hash_visitor(visitor_id)
    with lock:
        with closing(sqlite3.connect(stats_db, timeout=5.0)) as connection:
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                "INSERT OR IGNORE INTO visit_stats(key,value,updated_at) "
                "VALUES('home_views',0,?)",
                (current_time,),
            )
            connection.execute(
                "UPDATE visit_stats SET value=value+1, updated_at=? WHERE key='home_views'",
                (current_time,),
            )
            inserted = connection.execute(
                "INSERT OR IGNORE INTO unique_visitors"
                "(visitor_hash,first_seen_at,last_seen_at) VALUES(?,?,?)",
                (visitor_hash, current_time, current_time),
            ).rowcount
            if not inserted:
                connection.execute(
                    "UPDATE unique_visitors SET last_seen_at=? WHERE visitor_hash=?",
                    (current_time, visitor_hash),
                )
            connection.execute(
                "INSERT OR IGNORE INTO visit_stats(key,value,updated_at) "
                "VALUES('home_unique',0,?)",
                (current_time,),
            )
            if inserted:
                connection.execute(
                    "UPDATE visit_stats SET value=value+1, updated_at=? "
                    "WHERE key='home_unique'",
                    (current_time,),
                )
            visit_row = connection.execute(
                "SELECT value FROM visit_stats WHERE key='home_views'"
            ).fetchone()
            unique_row = connection.execute(
                "SELECT value FROM visit_stats WHERE key='home_unique'"
            ).fetchone()
            connection.commit()
    return {
        "visits": int(visit_row[0] if visit_row else 0),
        "unique": int(unique_row[0] if unique_row else 0),
    }
