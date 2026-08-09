"""Tests for the book engine orchestrator + predictor stub."""

from __future__ import annotations

import unittest

from rudra_intraday_engine.core.book_engine import (
    BOOK_ENGINE_VERSION,
    RULE_SET_LABEL,
    evaluate_session,
)
from rudra_intraday_engine.core.orderflow import (
    compute_cumulative_delta,
    detect_divergence,
    orderflow_signal,
)
from rudra_intraday_engine.core.predictor import (
    DEFAULT_KRONOS_MODEL_VERSION,
    kronos_available,
    predict_kronos,
)
from rudra_intraday_engine.core.profile import Bar
from rudra_intraday_engine.signal_types import Action


BASE_TS = 1754835000
SLOT = 30 * 60


def _bar(i, o, h, l, c, vol=10000.0):
    return Bar(timestamp_unix=BASE_TS + i * SLOT, open=o, high=h, low=l, close=c, volume=vol)


def _trend_up_bars(n=12, start=100.0, end=106.0):
    step = (end - start) / n
    bars = []
    for i in range(n):
        o = start + i * step
        c = start + (i + 1) * step
        h = max(o, c) + 0.10
        l = min(o, c) - 0.10
        bars.append(_bar(i, o, h, l, c))
    return bars


def _p_day_bars():
    """P-day: bearish first bar, rally to upper half."""
    return [
        _bar(0, 100.00, 100.00, 99.00, 99.10),   # bearish first bar
        _bar(1, 99.10, 99.50, 99.00, 99.20),
        _bar(2, 99.20, 100.00, 99.20, 99.90),
        _bar(3, 99.90, 100.50, 99.85, 100.40),
        _bar(4, 100.40, 100.75, 100.30, 100.65),
        _bar(5, 100.65, 101.00, 100.60, 100.90),
        _bar(6, 100.90, 101.25, 100.85, 101.15),
        _bar(7, 101.15, 101.50, 101.10, 101.40),
        _bar(8, 101.40, 101.60, 101.30, 101.50),
        _bar(9, 101.50, 101.75, 101.40, 101.65),
        _bar(10, 101.65, 101.90, 101.55, 101.80),
        _bar(11, 101.80, 102.00, 101.70, 101.90),
    ]


class TestBookEngineOrchestrator(unittest.TestCase):

    def test_trend_up_emits_buy(self):
        bs, kronos, sc, cd, div = evaluate_session(_trend_up_bars())
        self.assertEqual(bs.action, Action.BUY)
        self.assertGreater(bs.confidence, 0.5)
        self.assertEqual(bs.rule_version, BOOK_ENGINE_VERSION)
        self.assertNotEqual(bs.rule_set_sha256, "")
        self.assertIsNone(kronos)  # Kronos not installed

    def test_p_day_emits_buy(self):
        bs, _, _, _, _ = evaluate_session(_p_day_bars())
        self.assertEqual(bs.action, Action.BUY)

    def test_fired_rules_present(self):
        bs, _, _, _, _ = evaluate_session(_trend_up_bars())
        self.assertGreater(len(bs.fired_rules), 0)
        # Each fired rule has a stable id
        for r in bs.fired_rules:
            self.assertNotEqual(r.rule_id, "")
            self.assertGreaterEqual(r.confidence, 0.0)
            self.assertLessEqual(r.confidence, 1.0)

    def test_empty_bars_raises(self):
        with self.assertRaises(ValueError):
            evaluate_session([])

    def test_engine_version_constant(self):
        self.assertEqual(BOOK_ENGINE_VERSION, "0.1.0")
        self.assertEqual(RULE_SET_LABEL, "mind-markets-and-money-v0.1.0")


class TestPredictor(unittest.TestCase):

    def test_kronos_not_available(self):
        """v1 default — Kronos package not installed."""
        self.assertFalse(kronos_available())

    def test_predict_kronos_returns_none_when_unavailable(self):
        result = predict_kronos(_trend_up_bars())
        self.assertIsNone(result)

    def test_predict_kronos_empty_bars(self):
        """Even if Kronos were available, empty bars → None."""
        result = predict_kronos([])
        self.assertIsNone(result)

    def test_default_model_version(self):
        self.assertEqual(DEFAULT_KRONOS_MODEL_VERSION, "kronos-0.6.0")


class TestOrderflow(unittest.TestCase):

    def test_cumulative_delta_trend_up_positive(self):
        bars = _trend_up_bars()
        cd = compute_cumulative_delta(bars)
        self.assertGreater(cd.final_delta, 0)
        self.assertEqual(cd.positive_bars, 12)
        self.assertEqual(cd.negative_bars, 0)

    def test_cumulative_delta_empty(self):
        cd = compute_cumulative_delta([])
        self.assertEqual(cd.final_delta, 0.0)
        self.assertEqual(cd.bars, ())

    def test_divergence_no_clear_in_trend_up(self):
        bars = _trend_up_bars()
        _, div = orderflow_signal(bars)
        # A steady trend has no divergence — delta is consistent with price
        self.assertEqual(div.divergence_type.value, "none")

    def test_divergence_insufficient_bars(self):
        cd, div = orderflow_signal([_bar(0, 100, 100.5, 99.5, 100)])
        self.assertEqual(div.divergence_type.value, "none")
        self.assertEqual(div.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
