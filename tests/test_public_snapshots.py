from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.dashboard.public_snapshots import SnapshotPublisher, canonical_json_bytes, digest_json


class SnapshotPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="niuone-snapshots-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_publish_is_content_addressed_and_idempotent(self) -> None:
        publisher = SnapshotPublisher(self.root)
        sections = {"account": {"cash": 100}, "history": {"points": []}}

        first = publisher.publish(sections, generated_at="first")
        second = publisher.publish(sections, generated_at="second")

        self.assertEqual(first, second)
        self.assertEqual(first["revision"], 1)
        manifest = publisher.read_manifest(1)
        self.assertIsNotNone(manifest)
        account_digest, account_content = digest_json(sections["account"])
        self.assertEqual(manifest["sections"]["account"]["digest"], account_digest)
        self.assertEqual((self.root / "objects" / f"{account_digest}.json").read_bytes(), account_content)

    def test_unchanged_publish_does_not_rescan_retention_tree(self) -> None:
        publisher = SnapshotPublisher(self.root, max_revisions=2)
        sections = {"account": {"cash": 100}}
        publisher.publish(sections)
        original_prune = publisher._prune_snapshots
        publisher._prune_snapshots = Mock(wraps=original_prune)  # type: ignore[method-assign]

        unchanged = publisher.publish(sections)

        self.assertEqual(unchanged["revision"], 1)
        publisher._prune_snapshots.assert_not_called()

    def test_latest_changes_only_after_manifest_and_objects_exist(self) -> None:
        publisher = SnapshotPublisher(self.root)
        publisher.publish({"account": {"cash": 100}}, generated_at="one")
        original_latest = publisher.latest_path.read_bytes()
        original_atomic_write = publisher._atomic_write

        def fail_latest(path: Path, content: bytes) -> None:
            if path == publisher.latest_path:
                raise OSError("simulated final pointer failure")
            original_atomic_write(path, content)

        publisher._atomic_write = fail_latest  # type: ignore[method-assign]
        with self.assertRaises(OSError):
            publisher.publish({"account": {"cash": 200}}, generated_at="two")

        self.assertEqual(publisher.latest_path.read_bytes(), original_latest)
        self.assertEqual(json.loads(original_latest)["revision"], 1)
        self.assertTrue((self.root / "manifests" / "2.json").exists())

        publisher._atomic_write = original_atomic_write  # type: ignore[method-assign]
        recovered = publisher.publish({"account": {"cash": 300}}, generated_at="three")
        self.assertEqual(recovered["revision"], 3)
        self.assertEqual(publisher.read_manifest(3)["sections"]["account"]["digest"], digest_json({"cash": 300})[0])

    def test_orphan_manifest_is_adopted_after_latest_pointer_failure(self) -> None:
        publisher = SnapshotPublisher(self.root)
        publisher.publish({"account": {"cash": 100}})
        original_atomic_write = publisher._atomic_write

        def fail_latest(path: Path, content: bytes) -> None:
            if path == publisher.latest_path:
                raise OSError("simulated final pointer failure")
            original_atomic_write(path, content)

        publisher._atomic_write = fail_latest  # type: ignore[method-assign]
        with self.assertRaises(OSError):
            publisher.publish({"account": {"cash": 200}}, generated_at="two")
        publisher._atomic_write = original_atomic_write  # type: ignore[method-assign]

        recovered = publisher.publish({"account": {"cash": 200}}, generated_at="retry")

        self.assertEqual(recovered["revision"], 2)
        self.assertEqual(recovered["generated_at"], "two")

    def test_concurrent_publishers_do_not_lose_revisions(self) -> None:
        barrier = threading.Barrier(3)
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def publish(value: int) -> None:
            try:
                barrier.wait()
                results.append(SnapshotPublisher(self.root).publish({"account": {"cash": value}}))
            except BaseException as exc:  # test worker must report every failure
                errors.append(exc)

        workers = [threading.Thread(target=publish, args=(value,)) for value in (100, 200)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertFalse(errors)
        self.assertEqual({int(item["revision"]) for item in results}, {1, 2})
        latest = json.loads((self.root / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["revision"], 2)
        self.assertTrue((self.root / "manifests" / "1.json").exists())
        self.assertTrue((self.root / "manifests" / "2.json").exists())

    def test_old_manifests_and_unreferenced_objects_are_pruned(self) -> None:
        publisher = SnapshotPublisher(self.root, max_revisions=2)
        digests = []
        for cash in (100, 200, 300, 400):
            publisher.publish({"account": {"cash": cash}})
            digests.append(digest_json({"cash": cash})[0])

        self.assertEqual(
            sorted(path.name for path in (self.root / "manifests").glob("*.json")),
            ["3.json", "4.json"],
        )
        self.assertFalse((self.root / "objects" / f"{digests[0]}.json").exists())
        self.assertFalse((self.root / "objects" / f"{digests[1]}.json").exists())
        self.assertTrue((self.root / "objects" / f"{digests[2]}.json").exists())
        self.assertTrue((self.root / "objects" / f"{digests[3]}.json").exists())

    def test_pruning_reuses_cached_manifest_object_references(self) -> None:
        publisher = SnapshotPublisher(self.root, max_revisions=2)
        for cash in (100, 200, 300):
            publisher.publish({"account": {"cash": cash}})
        self.assertEqual(set(publisher._manifest_object_cache), {2, 3})
        original_read_manifest = publisher.read_manifest
        read_revisions: list[int] = []

        def tracked_read_manifest(revision: int | str):
            read_revisions.append(int(revision))
            return original_read_manifest(revision)

        publisher.read_manifest = tracked_read_manifest  # type: ignore[method-assign]
        publisher.publish({"account": {"cash": 400}})

        self.assertEqual(read_revisions.count(3), 1)
        self.assertEqual(read_revisions.count(4), 1)
        self.assertNotIn(2, read_revisions)
        self.assertEqual(set(publisher._manifest_object_cache), {3, 4})

    def test_canonical_json_is_stable(self) -> None:
        self.assertEqual(canonical_json_bytes({"b": 1, "a": "牛"}), b'{"a":"\xe7\x89\x9b","b":1}\n')


if __name__ == "__main__":
    unittest.main()
