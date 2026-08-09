"""Tests for the Adjudicator (schema + loader + merger)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rudra_intraday_engine.adjudicator import (
    Adjudicator,
    AdjudicatorError,
    ExpressionError,
    load_adjudicator,
    merge,
)
from rudra_intraday_engine.adjudicator.expression import (
    ALLOWED_ROOTS,
    ExpressionError,
    evaluate_when,
)
from rudra_intraday_engine.adjudicator.schema import (
    AdjudicatorError,
    BookInputs,
    Fallback,
    KronosInputs,
    MergeRule,
    Risk,
)
from rudra_intraday_engine.core.book_engine import evaluate_session
from rudra_intraday_engine.core.profile import Bar
from rudra_intraday_engine.signal_types import Action, BookSignal, KronosSignal


# ── Schema validation tests ──────────────────────────────────────────


class TestAdjudicatorSchema(unittest.TestCase):

    def test_minimal_required_fields(self):
        a = Adjudicator.from_toml_dict({
            "name": "minimal",
            "version": "1.0.0",
        })
        self.assertEqual(a.name, "minimal")
        self.assertEqual(a.version, "1.0.0")
        # Defaults
        self.assertEqual(a.merge_rules, ())
        self.assertTrue(a.book.required)
        self.assertFalse(a.kronos.required)
        self.assertEqual(a.fallback.when_no_kronos, "HOLD")

    def test_missing_name_raises(self):
        with self.assertRaises(AdjudicatorError):
            Adjudicator.from_toml_dict({"version": "1.0.0"})

    def test_missing_version_raises(self):
        with self.assertRaises(AdjudicatorError):
            Adjudicator.from_toml_dict({"name": "x"})

    def test_unknown_top_key_rejected(self):
        with self.assertRaises(AdjudicatorError) as cm:
            Adjudicator.from_toml_dict({
                "name": "x", "version": "1.0.0",
                "rogue_key": "value",
            })
        self.assertIn("unknown", str(cm.exception).lower())

    def test_unknown_book_key_rejected(self):
        with self.assertRaises(AdjudicatorError) as cm:
            Adjudicator.from_toml_dict({
                "name": "x", "version": "1.0.0",
                "book": {"rogue": True},
            })
        self.assertIn("rogue", str(cm.exception))

    def test_invalid_fallback_action_rejected(self):
        with self.assertRaises(AdjudicatorError) as cm:
            Adjudicator.from_toml_dict({
                "name": "x", "version": "1.0.0",
                "fallback": {"when_no_kronos": "YOLO"},
            })
        self.assertIn("must be one of", str(cm.exception).lower())

    def test_invalid_merge_emit_rejected(self):
        with self.assertRaises(AdjudicatorError) as cm:
            Adjudicator.from_toml_dict({
                "name": "x", "version": "1.0.0",
                "merge_rules": [
                    {"when": "True", "emit": "GARBAGE"},
                ],
            })
        self.assertIn("must be", str(cm.exception).lower())

    def test_risk_max_position_must_be_in_range(self):
        with self.assertRaises(AdjudicatorError):
            Adjudicator.from_toml_dict({
                "name": "x", "version": "1.0.0",
                "risk": {"max_position_pct": 1.5},
            })


# ── Loader tests ─────────────────────────────────────────────────────


class TestAdjudicatorLoader(unittest.TestCase):

    def test_load_valid_toml(self):
        toml = """
[adjudicator]
name = "test"
version = "1.0.0"

[adjudicator.book]
required = true
min_rules_fired = 2

[[adjudicator.merge_rules]]
when = "book.action == 'BUY'"
emit = "BUY"
size_multiplier = 1.0
reason = "test"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(toml)
            path = f.name
        a = load_adjudicator(path)
        self.assertEqual(a.name, "test")
        self.assertEqual(len(a.merge_rules), 1)
        self.assertEqual(a.merge_rules[0].emit, "BUY")

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_adjudicator("/nonexistent/path.toml")

    def test_load_invalid_when_expression(self):
        toml = """
[adjudicator]
name = "test"
version = "1.0.0"

[[adjudicator.merge_rules]]
when = "@#$ invalid"
emit = "BUY"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(toml)
            path = f.name
        with self.assertRaises(ExpressionError):
            load_adjudicator(path)


# ── Merger tests ─────────────────────────────────────────────────────


def _make_book_signal(action: str = "BUY", confidence: float = 0.75) -> BookSignal:
    from rudra_intraday_engine.signal_types import RuleTrace
    return BookSignal(
        action=Action(action),
        confidence=confidence,
        rule_version="0.1.0",
        fired_rules=[
            RuleTrace(
                rule_id="day_type=trend_up",
                rule_version="0.1.0",
                fired=True,
                confidence=0.85,
            ),
        ],
        rejected_rules=[],
        rule_set_sha256="abc123",
    )


def _make_kronos_signal(confidence: float = 0.80, model: str = "kronos-0.6.0") -> KronosSignal:
    return KronosSignal(
        prediction="UP",
        confidence=confidence,
        model_version=model,
        horizon_bars=1,
    )


def _write_toml(content: str) -> str:
    with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
        f.write(content)
        return f.name


class TestAdjudicatorMerger(unittest.TestCase):

    def test_book_only_buy_rule_matches(self):
        path = _write_toml("""
[adjudicator]
name = "book-only"
version = "1.0.0"

[[adjudicator.merge_rules]]
when = "book.action == 'BUY' and book.confidence >= 0.60"
emit = "BUY"
reason = "test-buy"
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)
        ts = merge(adj, book, None, symbol="SPY", entry_price=100.0)
        self.assertEqual(ts.action, Action.BUY)
        self.assertEqual(ts.reason, "test-buy")
        self.assertEqual(ts.symbol, "SPY")
        self.assertGreater(ts.qty, 0)
        self.assertIsNotNone(ts.stop_loss)
        self.assertIsNotNone(ts.take_profit)

    def test_no_match_falls_back_to_hold(self):
        path = _write_toml("""
[adjudicator]
name = "no-match"
version = "1.0.0"

[adjudicator.fallback]
when_validation_fails = "HOLD"

[[adjudicator.merge_rules]]
when = "book.action == 'SELL'"
emit = "SELL"
reason = "never"
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)
        ts = merge(adj, book, None, symbol="SPY")
        self.assertEqual(ts.action, Action.HOLD)
        self.assertIn("no_merge_rule_matched", ts.decide_no_reasons)

    def test_kronos_required_but_missing(self):
        path = _write_toml("""
[adjudicator]
name = "kronos-required"
version = "1.0.0"

[adjudicator.kronos]
required = true
min_confidence = 0.50
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)
        ts = merge(adj, book, None)
        self.assertEqual(ts.action, Action.HOLD)
        self.assertTrue(any("kronos" in r for r in ts.decide_no_reasons))

    def test_book_blocked_rule(self):
        path = _write_toml("""
[adjudicator]
name = "blocked"
version = "1.0.0"

[adjudicator.book]
required = true
blocked_rules = ["day_type=trend_up"]

[adjudicator.fallback]
when_validation_fails = "HOLD"
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)  # has day_type=trend_up
        ts = merge(adj, book, None)
        # Should HOLD because the rule is blocked
        self.assertEqual(ts.action, Action.HOLD)
        self.assertTrue(any("blocked" in r for r in ts.decide_no_reasons))

    def test_book_required_rule_missing(self):
        path = _write_toml("""
[adjudicator]
name = "required-missing"
version = "1.0.0"

[adjudicator.book]
required = true
required_rules = ["day_type=double_distribution"]
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)  # has trend_up, not double_distribution
        ts = merge(adj, book, None)
        self.assertEqual(ts.action, Action.HOLD)
        self.assertTrue(any("required_rule_missing" in r for r in ts.decide_no_reasons))

    def test_signal_provenance_populated(self):
        path = _write_toml("""
[adjudicator]
name = "prov"
version = "2.5.0"

[[adjudicator.merge_rules]]
when = "book.action == 'BUY'"
emit = "BUY"
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)
        ts = merge(adj, book, None)
        self.assertEqual(ts.adjudicator_version, "2.5.0")
        self.assertEqual(ts.book_engine_version, "0.1.0")
        self.assertNotEqual(ts.book_signal_ref, "")  # canonical hash

    def test_kronos_match_increases_size(self):
        path = _write_toml("""
[adjudicator]
name = "kronos-confirm"
version = "1.0.0"

[adjudicator.book]
required = true
min_rules_fired = 0

[adjudicator.kronos]
required = true
min_confidence = 0.50
allowed_model_versions = ["kronos-0.6.0"]

[[adjudicator.merge_rules]]
when = "book.action == 'BUY' and kronos.prediction == 'UP'"
emit = "BUY"
size_multiplier = 1.5
reason = "confirmed"
""")
        adj = load_adjudicator(path)
        book = _make_book_signal("BUY", 0.75)
        kronos = _make_kronos_signal(0.80, "kronos-0.6.0")
        ts = merge(adj, book, kronos, entry_price=100.0)
        self.assertEqual(ts.action, Action.BUY)
        # size_multiplier = 1.5 with max_position 0.05 → qty = max(1, round(0.05 * 1.5 * 100)) = 8
        self.assertEqual(ts.qty, 8)
        self.assertEqual(ts.reason, "confirmed")


if __name__ == "__main__":
    unittest.main()
