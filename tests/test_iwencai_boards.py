import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.market_data.iwencai_boards import (
    IwencaiBoardError,
    IwencaiBoardSnapshot,
    IwencaiStockBoard,
    fetch_iwencai_board_snapshot,
    load_iwencai_board_snapshot,
    parse_iwencai_board_rows,
)
from app.market_data.iwencai_client import IwencaiConfig


class _Client:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.pages[kwargs["page"]]


class IwencaiBoardTests(unittest.TestCase):
    def test_parses_leaf_industry_and_removes_industry_hierarchy_from_concepts(self):
        snapshot = parse_iwencai_board_rows(
            [{
                "股票代码": "603110.SH",
                "股票简称": "东方材料",
                "所属同花顺行业": ["基础化工", "化学制品", "涂料油墨"],
                "所属概念": ["算力租赁", "基础化工", "涂料油墨", "算力租赁"],
            }],
            expected_total=1,
            captured_at="2026-08-06 16:00:00",
        )

        stock = snapshot.stocks["603110"]
        self.assertEqual(stock.industry, "涂料油墨")
        self.assertEqual(stock.concepts, ("算力租赁",))
        self.assertEqual(stock.themes, ("算力租赁",))

    def test_rejects_incomplete_result_instead_of_caching_it(self):
        with self.assertRaisesRegex(IwencaiBoardError, "incomplete"):
            parse_iwencai_board_rows(
                [{"股票代码": "603110.SH", "所属同花顺行业": ["涂料油墨"]}],
                expected_total=2,
                captured_at="2026-08-06 16:00:00",
            )

    def test_fetches_every_reported_page_with_bounded_page_size(self):
        pages = {
            1: {
                "code_count": 3,
                "datas": [
                    {"股票代码": "600000.SH", "所属同花顺行业": ["银行"]},
                    {"股票代码": "000001.SZ", "所属同花顺行业": ["银行"]},
                ],
            },
            2: {
                "code_count": 3,
                "datas": [
                    {"股票代码": "920001.BJ", "所属同花顺行业": ["专用设备"]},
                ],
            },
        }
        client = _Client(pages)
        config = IwencaiConfig(
            enabled=True,
            base_url="https://openapi.iwencai.com",
            api_key="test-key",
            max_concurrency=1,
        )

        with patch("app.market_data.iwencai_boards.IWENCAI_BOARD_PAGE_SIZE", 2):
            snapshot = fetch_iwencai_board_snapshot(
                config=config,
                client=client,
                now=datetime(2026, 8, 6, 16, 0, 0),
            )

        self.assertEqual(len(snapshot.stocks), 3)
        self.assertEqual([call[1]["page"] for call in client.calls], [1, 2])
        self.assertTrue(all(call[1]["expand_index"] is False for call in client.calls))

    def test_stale_cache_is_used_only_after_refresh_fails(self):
        with tempfile.TemporaryDirectory(prefix="niuone-iwencai-board-") as directory:
            path = Path(directory) / "boards.json"
            cached = IwencaiBoardSnapshot(
                captured_at="2026-08-05 18:00:00",
                as_of_date="2026-08-05",
                stocks={
                    "603110": IwencaiStockBoard(
                        code="603110",
                        industry="涂料油墨",
                        concepts=("算力租赁",),
                    )
                },
            )
            path.write_text(
                json.dumps(cached.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )

            loaded = load_iwencai_board_snapshot(
                cache_path=path,
                ttl_seconds=-1,
                fetcher=lambda: (_ for _ in ()).throw(OSError("offline")),
            )

        self.assertTrue(loaded.stale)
        self.assertEqual(loaded.industry_map({"603110"}), {"603110": "涂料油墨"})


if __name__ == "__main__":
    unittest.main()
