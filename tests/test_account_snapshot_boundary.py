"""A missing or unreadable account cannot become a fresh simulated account."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "app/compat")]
import niuniu_practice_trader as trader


class AccountSnapshotBoundaryTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = directory / "account.json"
        self.db = directory / "ledger.db"
        self.protocol = directory / "cron" / "state" / "niuone_forward_protocol.json"
        stack.enter_context(patch.object(trader, "DASHBOARD_HOME", directory))
        stack.enter_context(patch.object(trader, "STATE_FILE", self.state))
        stack.enter_context(patch.dict(os.environ, {"DASHBOARD_NIUNIU_DB": str(self.db)}))
        self.save = stack.enter_context(patch.object(trader, "save_state"))

    def test_invalid_snapshot_is_preserved(self):
        for data in (b'{', b'{}', b'{"positions":{},"cash":0,"initial_cash":"invalid"}'):
            with self.subTest(data=data):
                self.state.write_bytes(data)
                with self.assertRaises(ValueError):
                    trader.load_state()
                self.assertEqual(self.state.read_bytes(), data)
        self.save.assert_not_called()

    def test_read_permission_error_propagates(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                trader.load_state()
        self.save.assert_not_called()

    def test_ledger_rows_prevent_reinitialization(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE trades(id INTEGER)")
            db.execute("INSERT INTO trades VALUES(1)")
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "已有账户记录"):
            trader.load_state()
        self.assertFalse(self.state.exists())
        self.save.assert_not_called()

    def test_legacy_pristine_balance_keeps_its_own_capital(self):
        self.state.write_text(json.dumps({"created_at": "2020-01-01 00:00:00",
                                          "initial_cash": 72500, "cash": 72500}), encoding="utf-8")
        snapshot = trader._read_account_snapshot()
        self.assertEqual((snapshot["cash"], snapshot["positions"]), (72500, {}))
        self.save.assert_not_called()

    def test_frozen_empty_account_boundary_is_protected(self):
        self.protocol.parent.mkdir(parents=True)
        self.protocol.write_text(json.dumps({"account_baseline": {"status": "captured"}}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "已有账户记录"):
            trader.load_state()
        self.save.assert_not_called()

    def test_first_install_with_empty_ledger_can_still_bootstrap(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE trades(id INTEGER)")
        self.assertEqual(trader.load_state()["cash"], trader.INITIAL_CASH)
        self.save.assert_called_once()
