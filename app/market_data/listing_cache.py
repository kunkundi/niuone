"""Validation shared by scan consumers and the read-only readiness endpoint."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def validate_catalog(value: Any, day: str, *, minimum_rows: int = 5000) -> list[tuple[str, str]] | None:
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("date") != day:
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
        if not isinstance(code, str) or not re.fullmatch(r"(?:60|68|00|30)\d{4}", code):
            return None
        if not isinstance(name, str) or not name.strip() or code in names:
            return None
        names[code] = name
        segments.add(code[:2])
    if segments != {"60", "68", "00", "30"}:
        return None
    return sorted(names.items())


def catalog_readiness(path: Path, day: str) -> dict[str, Any]:
    try:
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
