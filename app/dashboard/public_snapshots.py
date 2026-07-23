"""Content-addressed public snapshot storage for Dashboard v2."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.dashboard.public_projection import PUBLIC_SCHEMA_VERSION


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest_json(value: Any) -> tuple[str, bytes]:
    content = canonical_json_bytes(value)
    return hashlib.sha256(content).hexdigest(), content


class SnapshotPublisher:
    """Publish immutable objects and manifests, then atomically move latest."""

    def __init__(
        self,
        root: Path,
        *,
        lock_timeout_seconds: float = 5.0,
        stale_lock_seconds: float = 60.0,
        max_revisions: int = 1_024,
    ):
        self.root = Path(root)
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))
        self.stale_lock_seconds = max(self.lock_timeout_seconds, float(stale_lock_seconds))
        self.max_revisions = max(2, int(max_revisions))
        self._thread_lock = threading.Lock()
        self._manifest_object_cache: dict[int, frozenset[str]] = {}

    @property
    def latest_path(self) -> Path:
        return self.root / "latest.json"

    def read_latest(self) -> dict[str, Any] | None:
        return self._read_mapping(self.latest_path)

    def read_manifest(self, revision: int | str) -> dict[str, Any] | None:
        return self._read_mapping(self.root / "manifests" / f"{revision}.json")

    def read_object(self, digest: str) -> dict[str, Any] | None:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        return self._read_mapping(self.root / "objects" / f"{digest}.json")

    def publish(self, sections: Mapping[str, Mapping[str, Any]], *, generated_at: str = "") -> dict[str, Any]:
        if not sections:
            raise ValueError("at least one public section is required")
        with self._thread_lock, self._process_lock():
            latest = self.read_latest() or {}
            object_refs: dict[str, dict[str, Any]] = {}
            for name in sorted(sections):
                if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in name):
                    raise ValueError(f"invalid section name: {name!r}")
                digest, content = digest_json(sections[name])
                object_path = self.root / "objects" / f"{digest}.json"
                self._write_once(object_path, content)
                object_refs[name] = {
                    "digest": digest,
                    "path": f"objects/{digest}.json",
                    "bytes": len(content),
                }

            previous_revision = int(latest.get("revision") or 0)
            previous_manifest = self.read_manifest(previous_revision) if previous_revision else None
            if previous_manifest and previous_manifest.get("sections") == object_refs:
                return latest

            manifest_revisions = self._manifest_revisions()
            for orphan_revision in manifest_revisions:
                if orphan_revision <= previous_revision:
                    continue
                orphan = self.read_manifest(orphan_revision)
                if orphan and orphan.get("sections") == object_refs:
                    next_latest = self._point_latest_at_manifest(orphan)
                    self._prune_snapshots(manifest_revisions)
                    return next_latest

            revision = max([previous_revision, *manifest_revisions], default=0) + 1
            manifest = {
                "schema_version": PUBLIC_SCHEMA_VERSION,
                "revision": revision,
                "generated_at": generated_at,
                "sections": object_refs,
            }
            _, manifest_content = digest_json(manifest)
            manifest_path = self.root / "manifests" / f"{revision}.json"
            self._write_once(manifest_path, manifest_content)
            next_latest = self._point_latest_at_manifest(manifest)
            self._prune_snapshots([*manifest_revisions, revision])
            return next_latest

    def _point_latest_at_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        manifest_digest, _ = digest_json(manifest)
        revision = int(manifest["revision"])
        next_latest = {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "revision": revision,
            "generated_at": str(manifest.get("generated_at") or ""),
            "manifest": f"manifests/{revision}.json",
            "manifest_digest": manifest_digest,
        }
        self._atomic_write(self.latest_path, canonical_json_bytes(next_latest))
        return next_latest

    def _manifest_revisions(self) -> list[int]:
        manifest_dir = self.root / "manifests"
        try:
            return sorted(
                int(path.stem)
                for path in manifest_dir.glob("*.json")
                if path.stem.isdigit() and int(path.stem) > 0
            )
        except OSError:
            return []

    def _read_mapping(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    def _manifest_object_digests(self, revision: int) -> frozenset[str]:
        cached = self._manifest_object_cache.get(revision)
        if cached is not None:
            return cached
        manifest = self.read_manifest(revision) or {}
        digests = frozenset(
            str(reference.get("digest") or "")
            for reference in manifest.get("sections", {}).values()
            if isinstance(reference, Mapping)
            and len(str(reference.get("digest") or "")) == 64
        )
        self._manifest_object_cache[revision] = digests
        return digests

    def _prune_snapshots(self, revisions: list[int] | None = None) -> None:
        revisions = self._manifest_revisions() if revisions is None else sorted(revisions)
        if len(revisions) <= self.max_revisions:
            return
        retained = set(revisions[-self.max_revisions:])
        latest_revision = int((self.read_latest() or {}).get("revision") or 0)
        if latest_revision:
            retained.add(latest_revision)
        referenced_objects: set[str] = set()
        for revision in retained:
            referenced_objects.update(self._manifest_object_digests(revision))
        for revision in revisions:
            if revision in retained:
                continue
            try:
                (self.root / "manifests" / f"{revision}.json").unlink()
            except (FileNotFoundError, OSError):
                pass
            self._manifest_object_cache.pop(revision, None)
        try:
            object_paths = tuple((self.root / "objects").glob("*.json"))
        except OSError:
            object_paths = ()
        for path in object_paths:
            if path.stem in referenced_objects:
                continue
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                pass

    def _write_once(self, path: Path, content: bytes) -> None:
        if path.exists():
            return
        self._atomic_write(path, content)

    def _atomic_write(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".publish.lock"
        deadline = time.monotonic() + self.lock_timeout_seconds
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(f"{os.getpid()} {time.time()}\n")
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > self.stale_lock_seconds:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for snapshot lock: {lock_path.name}")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
