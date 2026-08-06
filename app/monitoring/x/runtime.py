"""Environment parsing shared by the X monitor daemon."""
from __future__ import annotations

import shlex
from typing import Mapping


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        raw_value = raw_value.strip()
        try:
            parsed = shlex.split(raw_value, posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = raw_value.strip("\"'")
    return values


def env_int(name: str, default: int, environ: Mapping[str, str]) -> int:
    try:
        value = environ.get(name)
        return int(value) if value else default
    except (TypeError, ValueError):
        return default


def env_flag(name: str, default: bool, environ: Mapping[str, str]) -> bool:
    raw = environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def us_features_enabled(environ: Mapping[str, str]) -> bool:
    return env_flag("DASHBOARD_US_FEATURES_ENABLED", False, environ)


def x_watchlist_enabled(environ: Mapping[str, str]) -> bool:
    return env_flag("X_WATCHLIST_ENABLED", True, environ)
