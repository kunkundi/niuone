"""Validation shared by scan consumers and the read-only readiness endpoint."""
from __future__ import annotations

import json
from pathlib import Path
import re
import os
import subprocess
import sys
import time
from typing import Any


def validate_catalog(value: Any, day: str, *, minimum_rows: int = 5000) -> list[tuple[str, str]] | None:
    if not isinstance(value, dict) or value.get("schema_version") != 2 or value.get("date") != day:
        return None
    rows = value.get("rows")
    if not isinstance(rows, list) or not minimum_rows <= len(rows) <= 20000:
        return None
    names: dict[str, str] = {}
    segments: set[str] = set()
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            return None
        code, name = row
        if not isinstance(code, str) or not re.fullmatch(r"[036]\d{5}", code):
            return None
        if not isinstance(name, str) or not name.strip() or code in names:
            return None
        names[code] = name
        segments.add(code[:2])
    if not {"60", "68", "00", "30"} <= segments:
        return None
    return sorted(names.items())


class ListingCacheError(RuntimeError):
    pass


def load_current_listing(path: Path, day: str) -> list[tuple[str, str]]:
    """Share one exact official listing across scans and reports under a process lease."""
    from app.core.json_cache import read_json_cache, write_json_cache
    from app.core.process_lease import FileLease

    def read_current() -> list[tuple[str, str]] | None:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            return validate_catalog(read_json_cache(path), day)
        except (OSError, UnicodeError):
            return None

    rows = read_current()
    if rows is not None:
        return rows
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = FileLease(path.with_suffix('.lock'), stale_after_seconds=75)
    if not lease.acquire():
        deadline = time.monotonic() + 61
        while lease.path.exists() and time.monotonic() < deadline:
            time.sleep(.25)
        rows = read_current()
        if rows is not None:
            return rows
        raise ListingCacheError('concurrent exchange listing refresh did not publish a complete catalog')
    try:
        rows = read_current()
        if rows is not None:
            return rows
        worker = Path(__file__).resolve().parents[1] / 'entrypoints/exchange_listing_worker.py'
        try:
            result = subprocess.run(
                [sys.executable, str(worker)],
                input=json.dumps({'day':day,'parts_path':str(path.with_name('a_share_listing_parts.json'))}).encode(),
                capture_output=True, timeout=60, check=False,
                env={**os.environ, 'PYTHONIOENCODING':'utf-8'},
            )
        except subprocess.TimeoutExpired as exc:
            raise ListingCacheError('exchange listing refresh timed out') from exc
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ListingCacheError('oversized exchange listing response')
        try:
            value = json.loads(result.stdout)
        except (UnicodeError, ValueError) as exc:
            raise ListingCacheError('invalid exchange listing response') from exc
        if result.returncode:
            detail = str(value.get('diagnostic') or '')[:600] if isinstance(value,dict) and value.get('error_code') == 'exchange_listing_unavailable' else ''
            raise ListingCacheError('complete exchange listing unavailable' + (': ' + detail if detail else ''))
        rows = validate_catalog(value, day)
        if rows is None:
            raise ListingCacheError('incomplete exchange listing')
        write_json_cache(path, value)
        return rows
    finally:
        lease.release()


def catalog_readiness(path: Path, day: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('oversized exchange listing cache')
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = None
    rows = validate_catalog(value, day)
    return {
        "ready": rows is not None,
        "date": str(value.get("date") or "") if isinstance(value, dict) else "",
        "count": len(rows) if rows is not None else 0,
        "error_code": "" if rows is not None else "exchange_listing_unavailable",
    }
