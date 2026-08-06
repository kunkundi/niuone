import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.market_data.eastmoney_boards import (
    EastmoneyBoardError,
    EastmoneyBoardSnapshot,
    EastmoneyStockBoard,
    fetch_eastmoney_board_snapshot,
    load_eastmoney_board_snapshot,
    parse_eastmoney_board_payload,
)


class _Response:
    def __init__(self, payload):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def payload(rows, total=None):
    return {"data": {"total": len(rows) if total is None else total, "diff": rows}}


class EastmoneyBoardTests(unittest.TestCase):
    def test_parses_industry_and_multiple_concepts(self):
        snapshot = parse_eastmoney_board_payload(
            payload([
                {
                    "f12": "000977",
                    "f14": "浪潮信息",
                    "f100": "计算机设备",
                    "f102": "山东板块",
                    "f103": "存储芯片,先进封装,存储芯片",
                },
                {
                    "f12": "600001",
                    "f14": "示例",
                    "f100": "银行Ⅱ",
                    "f102": "上海板块",
                    "f103": "--",
                },
            ]),
            captured_at="2026-08-02 10:00:00",
        )

        self.assertEqual(snapshot.stocks["000977"].industry, "计算机设备")
        self.assertEqual(snapshot.stocks["000977"].concepts, ("存储芯片", "先进封装"))
        self.assertEqual(snapshot.stocks["600001"].themes, ("银行Ⅱ",))
        self.assertEqual(snapshot.as_of_date, "2026-08-02")

    def test_rejects_partial_batch_instead_of_caching_it(self):
        with self.assertRaisesRegex(EastmoneyBoardError, "incomplete"):
            parse_eastmoney_board_payload(
                payload([{"f12": "000977", "f100": "计算机设备"}], total=2),
                captured_at="2026-08-02 10:00:00",
            )

    def test_fetch_uses_second_eastmoney_host_after_first_failure(self):
        calls = []

        def opener(request, **_kwargs):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise OSError("first host unavailable")
            return _Response(payload([{
                "f12": "000977",
                "f14": "浪潮信息",
                "f100": "计算机设备",
                "f103": "存储芯片",
            }]))

        snapshot = fetch_eastmoney_board_snapshot(
            opener=opener,
            now=datetime(2026, 8, 2, 10, 0, 0),
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(snapshot.stocks["000977"].themes, ("存储芯片",))

    def test_fetch_retries_a_page_after_both_hosts_fail_transiently(self):
        calls = []

        def opener(request, **_kwargs):
            calls.append(request.full_url)
            if len(calls) <= 2:
                raise OSError("temporary outage")
            return _Response(payload([{
                "f12": "000977",
                "f14": "浪潮信息",
                "f100": "计算机设备",
                "f103": "存储芯片",
            }]))

        with patch("app.market_data.eastmoney_boards.time.sleep"):
            snapshot = fetch_eastmoney_board_snapshot(
                opener=opener,
                now=datetime(2026, 8, 2, 10, 0, 0),
            )

        self.assertEqual(len(calls), 3)
        self.assertEqual(snapshot.stocks["000977"].industry, "计算机设备")

    def test_stale_cache_is_used_only_after_eastmoney_refresh_fails(self):
        with tempfile.TemporaryDirectory(prefix="niuone-eastmoney-board-") as directory:
            path = Path(directory) / "boards.json"
            cached = EastmoneyBoardSnapshot(
                captured_at="2026-08-01 15:00:00",
                as_of_date="2026-08-01",
                stocks={
                    "000977": EastmoneyStockBoard(
                        code="000977",
                        industry="计算机设备",
                        concepts=("存储芯片",),
                    )
                },
            )
            path.write_text(json.dumps(cached.to_dict(), ensure_ascii=False), encoding="utf-8")

            loaded = load_eastmoney_board_snapshot(
                cache_path=path,
                ttl_seconds=-1,
                fetcher=lambda: (_ for _ in ()).throw(OSError("offline")),
            )

        self.assertTrue(loaded.stale)
        self.assertEqual(loaded.theme_map({"000977"}), {"000977": ("存储芯片",)})

    def test_stale_cache_does_not_hide_programming_errors(self):
        with tempfile.TemporaryDirectory(prefix="niuone-eastmoney-board-") as directory:
            path = Path(directory) / "boards.json"
            cached = EastmoneyBoardSnapshot(
                captured_at="2026-08-01 15:00:00",
                as_of_date="2026-08-01",
                stocks={
                    "000977": EastmoneyStockBoard(code="000977", industry="计算机设备")
                },
            )
            path.write_text(json.dumps(cached.to_dict(), ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(AssertionError, "programming error"):
                load_eastmoney_board_snapshot(
                    cache_path=path,
                    ttl_seconds=-1,
                    fetcher=lambda: (_ for _ in ()).throw(
                        AssertionError("programming error")
                    ),
                )


if __name__ == "__main__":
    unittest.main()
