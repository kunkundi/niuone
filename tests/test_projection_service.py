from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.dashboard.projection_service import ProjectionService
from app.dashboard.public_snapshots import SnapshotPublisher


class MutableSources:
    def __init__(self) -> None:
        self.cash = 100
        self.fail_candidates = False
        self.summary_generated_at = "2026-07-24 10:00:00"
        self.summary_tone_label = "平衡"

    def practice(self) -> dict[str, object]:
        return {"generated_at": f"cash-{self.cash}", "cash": self.cash, "positions": []}

    def candidates(self) -> dict[str, object]:
        if self.fail_candidates:
            raise TimeoutError("upstream timeout")
        return {"generated_at": "one", "items": [{"code": "600000", "name": "浦发银行"}]}

    def benchmarks(self) -> dict[str, object]:
        return {"items": []}

    def messages(self) -> dict[str, object]:
        return {"records": [], "total": 0}

    def market_summary(self) -> dict[str, object]:
        return {
            "available": True,
            "summary": "指数与资金结构维持平衡。",
            "tone_label": self.summary_tone_label,
            "generated_at": self.summary_generated_at,
            "stage": "completed",
        }


class ProjectionServiceTests(unittest.TestCase):
    def test_market_summary_digest_changes_when_evaluation_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="niuone-projection-") as directory:
            publisher = SnapshotPublisher(Path(directory))
            sources = MutableSources()
            service = ProjectionService(sources, publisher)

            first = service.refresh()
            first_manifest = publisher.read_manifest(first["revision"])
            sources.summary_generated_at = "2026-07-24 10:05:00"
            sources.summary_tone_label = "偏强"
            second = service.refresh()
            second_manifest = publisher.read_manifest(second["revision"])

            self.assertNotEqual(
                first_manifest["sections"]["market_summary"]["digest"],
                second_manifest["sections"]["market_summary"]["digest"],
            )

    def test_optional_source_failure_preserves_previous_valid_section(self) -> None:
        with tempfile.TemporaryDirectory(prefix="niuone-projection-") as directory:
            publisher = SnapshotPublisher(Path(directory))
            sources = MutableSources()
            service = ProjectionService(sources, publisher)
            first = service.refresh()
            first_manifest = publisher.read_manifest(first["revision"])
            first_candidates = first_manifest["sections"]["candidates"]["digest"]

            sources.cash = 200
            sources.fail_candidates = True
            second = service.refresh()
            second_manifest = publisher.read_manifest(second["revision"])

            self.assertNotEqual(
                first_manifest["sections"]["account"]["digest"],
                second_manifest["sections"]["account"]["digest"],
            )
            self.assertEqual(second_manifest["sections"]["candidates"]["digest"], first_candidates)
            metadata = publisher.read_object(second_manifest["sections"]["metadata"]["digest"])
            self.assertTrue(metadata["degraded"])
            self.assertEqual(metadata["stale_sections"], ["candidates"])


if __name__ == "__main__":
    unittest.main()
