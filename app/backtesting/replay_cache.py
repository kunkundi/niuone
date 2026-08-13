"""Versioned, content-addressed storage for reusable selection replay tapes."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .selection import (
    HistoricalBar,
    SelectionReplayFrame,
    SelectionReplayTape,
    SelectionSignal,
)


REPLAY_CACHE_SCHEMA_VERSION = 1
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


BarSeries = Iterable[HistoricalBar] | Mapping[str, HistoricalBar]


def _bar_values(rows: BarSeries) -> Iterable[HistoricalBar]:
    return rows.values() if isinstance(rows, Mapping) else rows


def _stable_bar_series(
    bars_by_symbol: Mapping[str, BarSeries],
) -> Mapping[str, BarSeries]:
    """Materialize only one-shot iterables; mappings and tuples stay shared."""
    return {
        symbol: (
            rows
            if isinstance(rows, (_MAPPING_PROXY_TYPE, tuple))
            else tuple(rows.values()) if isinstance(rows, Mapping)
            else tuple(rows)
        )
        for symbol, rows in bars_by_symbol.items()
    }


def _hash_value(hasher: Any, value: Any) -> None:
    hasher.update(str(value if value is not None else "").encode("utf-8"))
    hasher.update(b"\0")


def _classification_snapshot_hash(
    bars_by_symbol: Mapping[str, BarSeries],
) -> str:
    hasher = hashlib.sha256()
    for symbol in sorted(bars_by_symbol):
        first = next(iter(_bar_values(bars_by_symbol[symbol])), None)
        themes = (
            first.extras.get("themes", ())
            if first is not None and isinstance(first.extras, Mapping)
            else ()
        )
        if isinstance(themes, str):
            themes = (themes,)
        _hash_value(hasher, symbol)
        _hash_value(hasher, first.industry if first is not None else "")
        for theme in sorted(str(item or "").strip() for item in themes or ()):
            _hash_value(hasher, theme)
    return hasher.hexdigest()


def _bar_snapshot_hash(
    bars_by_symbol: Mapping[str, BarSeries],
    source_by_symbol: Mapping[str, str],
) -> str:
    """Fingerprint fetched values so upstream history revisions miss the cache."""
    hasher = hashlib.sha256()
    for symbol in sorted(bars_by_symbol):
        _hash_value(hasher, symbol)
        _hash_value(hasher, source_by_symbol.get(symbol, ""))
        for bar in _bar_values(bars_by_symbol[symbol]):
            for value in (
                bar.date,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.amount,
                bar.turnover,
                bar.previous_close,
                bar.limit_up,
                bar.limit_down,
                bar.suspended,
                bar.is_st,
            ):
                _hash_value(hasher, value)
    return hasher.hexdigest()


@dataclass(frozen=True)
class ReplayCacheKey:
    digest: str
    descriptor: Mapping[str, Any]


def build_replay_cache_key(
    bars_by_symbol: Mapping[str, BarSeries],
    *,
    protocol_version: str,
    selector_id: str,
    strategy_ids: Iterable[str],
    signal_start_date: str,
    signal_end_date: str,
    sources: Iterable[str],
    adjustment: str,
    stock_pool: Iterable[str],
    source_by_symbol: Mapping[str, str],
) -> ReplayCacheKey:
    """Build the stable identity required before selector output can be reused."""
    stable_bars = _stable_bar_series(bars_by_symbol)
    descriptor = {
        "schema_version": REPLAY_CACHE_SCHEMA_VERSION,
        "protocol_version": str(protocol_version or ""),
        "selector_id": str(selector_id or ""),
        "strategy_ids": list(dict.fromkeys(
            str(item or "") for item in strategy_ids
        )),
        "signal_start_date": str(signal_start_date or "")[:10],
        "signal_end_date": str(signal_end_date or "")[:10],
        "sources": list(dict.fromkeys(str(item or "") for item in sources)),
        "adjustment": str(adjustment or ""),
        "stock_pool": sorted(dict.fromkeys(str(item or "") for item in stock_pool)),
        "classification_snapshot_hash": _classification_snapshot_hash(stable_bars),
        "bar_snapshot_hash": _bar_snapshot_hash(stable_bars, source_by_symbol),
    }
    encoded = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ReplayCacheKey(
        digest=hashlib.sha256(encoded).hexdigest(),
        descriptor=MappingProxyType(descriptor),
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _dump_json(handle: Any, value: Any) -> None:
    json.dump(
        value,
        handle,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _dump_tape(handle: Any, tape: SelectionReplayTape) -> None:
    """Stream one frame at a time instead of duplicating the complete tape."""

    handle.write('{"frames":{')
    first = True
    for trading_date, frame in tape.frames.items():
        if not first:
            handle.write(",")
        first = False
        _dump_json(handle, str(trading_date))
        handle.write(":")
        _dump_json(handle, {
            "date": frame.date,
            "signals": [
                {
                    "symbol": signal.symbol,
                    "strategy_id": signal.strategy_id,
                    "reason": signal.reason,
                    "score": signal.score,
                    "metadata": _plain(signal.metadata),
                }
                for signal in frame.signals
            ],
            "scored": _plain(frame.scored),
            "cross_section": _plain(frame.cross_section),
        })
    handle.write('},"diagnostics":')
    _dump_json(handle, _plain(tape.diagnostics))
    handle.write("}")


def _mapping_proxy_tree(values: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(values, dict) and all(isinstance(key, str) for key in values):
        result = values
        for key, value in tuple(result.items()):
            if isinstance(value, Mapping):
                result[key] = _mapping_proxy_tree(value)
    else:
        result = {
            str(key): (
                _mapping_proxy_tree(value)
                if isinstance(value, Mapping)
                else value
            )
            for key, value in values.items()
        }
    return MappingProxyType(result)


def _tape_from_payload(payload: Mapping[str, Any]) -> SelectionReplayTape | None:
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, Mapping):
        return None
    frames: dict[str, SelectionReplayFrame] = {}
    if isinstance(raw_frames, dict):
        frame_items = (
            (trading_date, raw_frames.pop(trading_date))
            for trading_date in tuple(raw_frames)
        )
    else:
        frame_items = raw_frames.items()
    try:
        for trading_date, raw_frame in frame_items:
            if not isinstance(raw_frame, Mapping):
                return None
            signals = tuple(
                SelectionSignal(
                    symbol=str(item.get("symbol") or ""),
                    strategy_id=str(item.get("strategy_id") or ""),
                    reason=str(item.get("reason") or ""),
                    score=item.get("score"),
                    metadata=(
                        item.get("metadata")
                        if isinstance(item.get("metadata"), Mapping)
                        else {}
                    ),
                )
                for item in (raw_frame.get("signals") or ())
                if isinstance(item, Mapping)
            )
            scored = raw_frame.get("scored")
            cross_section = raw_frame.get("cross_section")
            if not isinstance(scored, Mapping) or not isinstance(
                cross_section, Mapping
            ):
                return None
            resolved_date = str(raw_frame.get("date") or trading_date)
            frames[str(trading_date)] = SelectionReplayFrame(
                date=resolved_date,
                signals=signals,
                scored=_mapping_proxy_tree(scored),
                cross_section=_mapping_proxy_tree(cross_section),
            )
    except (TypeError, ValueError):
        return None
    diagnostics = payload.get("diagnostics")
    return SelectionReplayTape(
        frames=MappingProxyType(frames),
        diagnostics=_mapping_proxy_tree(
            diagnostics if isinstance(diagnostics, Mapping) else {}
        ),
    )


class ReplayTapeCache:
    """Store gzip-compressed JSON tapes using atomic file replacement."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    def path_for(self, key: ReplayCacheKey) -> Path:
        if len(key.digest) != 64 or any(
            character not in "0123456789abcdef" for character in key.digest
        ):
            raise ValueError("invalid replay cache digest")
        return self.root / key.digest[:2] / f"{key.digest}.json.gz"

    def load(self, key: ReplayCacheKey) -> SelectionReplayTape | None:
        try:
            with gzip.open(self.path_for(key), "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, EOFError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, Mapping):
            return None
        if payload.get("schema_version") != REPLAY_CACHE_SCHEMA_VERSION:
            return None
        if payload.get("key") != key.digest:
            return None
        if payload.get("descriptor") != dict(key.descriptor):
            return None
        tape = payload.get("tape")
        return _tape_from_payload(tape) if isinstance(tape, Mapping) else None

    @contextmanager
    def build_lock(
        self,
        key: ReplayCacheKey,
        *,
        timeout_seconds: float = 30.0,
        stale_seconds: float = 7_200.0,
    ):
        """Serialize cache misses across Dashboard processes with a bounded wait."""
        target = self.path_for(key)
        lock_path = target.with_suffix(target.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        acquired = False
        while not acquired:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime >= stale_seconds:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            else:
                try:
                    os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
                finally:
                    os.close(descriptor)
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass

    def store(self, key: ReplayCacheKey, tape: SelectionReplayTape) -> bool:
        target = self.path_for(key)
        temporary = target.with_name(
            f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(temporary, "wt", encoding="utf-8") as handle:
                handle.write('{"schema_version":')
                _dump_json(handle, REPLAY_CACHE_SCHEMA_VERSION)
                handle.write(',"key":')
                _dump_json(handle, key.digest)
                handle.write(',"descriptor":')
                _dump_json(handle, dict(key.descriptor))
                handle.write(',"tape":')
                _dump_tape(handle, tape)
                handle.write("}")
            temporary.replace(target)
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "REPLAY_CACHE_SCHEMA_VERSION",
    "ReplayCacheKey",
    "ReplayTapeCache",
    "build_replay_cache_key",
]
