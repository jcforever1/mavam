"""Tests for artifact/ (content-addressed store + explain + verify round-trip)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from rudra_intraday_engine.artifact import (
    explain_signal,
    read_trade_signal,
    write_trade_signal,
)
from rudra_intraday_engine.signal_types import (
    Action,
    MarketStateHash,
    TradeSignal,
    canonical_hash,
)


def _make_signal() -> TradeSignal:
    return TradeSignal(
        action=Action.BUY,
        is_decide_no=False,
        decide_no_reasons=[],
        symbol="SPY",
        qty=5,
        order_type="limit",
        entry_price=105.85,
        stop_loss=104.26,
        take_profit=110.61,
        confidence=0.75,
        reason="test",
        book_signal_ref="abc123",
        kronos_signal_ref="",
        adjudicator_version="1.0.0",
        book_engine_version="0.1.0",
        predictor_version="",
        market_state_hash=MarketStateHash(
            algorithm="sha256",
            value="deadbeef",
            data_kind="csv",
            data_ref="spy.csv",
        ),
        timestamp_unix=1754835000,
        timestamp_iso="2026-08-10T13:30:00+00:00",
    )


class TestArtifactStore(unittest.TestCase):

    def setUp(self):
        # Use a temp config_sha so tests don't pollute real $HOME/state
        self.config_sha = "test_config_" + os.urandom(4).hex()
        self.ts = _make_signal()

    def test_write_creates_file_in_correct_path(self):
        path = write_trade_signal(self.ts, self.config_sha)
        expected_dir = Path(os.path.expanduser("~")) / "state" / self.config_sha / "signals"
        self.assertTrue(str(path).startswith(str(expected_dir)))
        self.assertTrue(path.exists())

    def test_write_then_read_round_trip(self):
        path = write_trade_signal(self.ts, self.config_sha)
        # Read by hash
        h = path.stem
        rt = read_trade_signal(h)
        self.assertIsNotNone(rt)
        self.assertEqual(rt.action, Action.BUY)
        self.assertEqual(rt.symbol, "SPY")
        self.assertEqual(rt.qty, 5)
        self.assertEqual(rt.entry_price, 105.85)
        self.assertEqual(rt.timestamp_unix, 1754835000)

    def test_verify_hash_matches(self):
        path = write_trade_signal(self.ts, self.config_sha)
        h = path.stem
        rt = read_trade_signal(h)
        # Re-canonicalize and confirm equality
        payload = rt.to_json_dict()
        self.assertEqual(canonical_hash(payload), h)

    def test_explain_returns_text(self):
        path = write_trade_signal(self.ts, self.config_sha)
        h = path.stem
        text = explain_signal(h)
        self.assertIsNotNone(text)
        self.assertIn("BUY", text)
        self.assertIn("SPY", text)
        self.assertIn("stop_loss", text.lower())

    def test_explain_unknown_hash_returns_none(self):
        self.assertIsNone(explain_signal("nonexistent_hash_xx"))

    def test_idempotent_write(self):
        """Writing the same signal twice lands at the same path."""
        p1 = write_trade_signal(self.ts, self.config_sha)
        p2 = write_trade_signal(self.ts, self.config_sha)
        self.assertEqual(p1, p2)

    def test_different_config_yields_different_dir(self):
        p1 = write_trade_signal(self.ts, self.config_sha)
        p2 = write_trade_signal(self.ts, "other_config_" + os.urandom(4).hex())
        self.assertNotEqual(p1.parent.parent, p2.parent.parent)


if __name__ == "__main__":
    unittest.main()
