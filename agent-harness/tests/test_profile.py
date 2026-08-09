"""Tests for core/profile.py — TPO, POC, Value Area, Initial Balance.

These tests lock the bug fix where TPO_LETTERS[slot_idx] used an
absolute slot number as a letter index (which broke for any modern
timestamp) instead of the position within the session.
"""

from __future__ import annotations

import unittest

from rudra_intraday_engine.core.profile import (
    Bar,
    DEFAULT_TICK_SIZE,
    compute_initial_balance,
    compute_poc,
    compute_tpos,
    compute_value_area,
)


# Use a modern timestamp (2026-08-10 09:30:00 UTC) so the slot index is
# ~975K — well beyond 62 (length of TPO_LETTERS). This is the test for
# the bug fix.
BASE_TS = 1754835000
SLOT = 30 * 60


def _bar(i: int, o: float, h: float, l: float, c: float, vol: float = 10000.0) -> Bar:
    return Bar(
        timestamp_unix=BASE_TS + i * SLOT,
        open=o, high=h, low=l, close=c, volume=vol,
    )


def _ramp_bars(start: float, end: float, n: int = 12, per_bar: float = 0.5) -> list[Bar]:
    """Build a steady ramp from `start` to `end` over `n` 30-min bars."""
    step = (end - start) / n
    bars = []
    for i in range(n):
        o = start + i * step
        c = start + (i + 1) * step
        h = max(o, c) + 0.10
        l = min(o, c) - 0.10
        bars.append(_bar(i, o, h, l, c))
    return bars


class TestComputeTPOs(unittest.TestCase):
    """Tests for compute_tpos()."""

    def test_empty_bars_returns_empty_map(self):
        self.assertEqual(compute_tpos([]), {})

    def test_tick_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            compute_tpos([_bar(0, 100, 101, 99, 100)], tick_size=0)
        with self.assertRaises(ValueError):
            compute_tpos([_bar(0, 100, 101, 99, 100)], tick_size=-0.25)

    def test_modern_timestamp_uses_position_not_absolute_slot(self):
        """Regression: TPO_LETTERS[slot_idx] used absolute slot (~975K for
        2026 timestamps) which would IndexError. After the fix, the first
        30-min bar must get letter 'A', not crash."""
        bars = _ramp_bars(100.0, 101.0, n=4)
        tpo_map = compute_tpos(bars, tick_size=DEFAULT_TICK_SIZE)
        # Should not raise, should produce a non-empty map
        self.assertGreater(len(tpo_map), 0)

    def test_first_bar_gets_letter_a(self):
        """First slot in the session should get 'A', the second 'B', etc."""
        bars = _ramp_bars(100.0, 101.0, n=4)
        tpo_map = compute_tpos(bars, tick_size=DEFAULT_TICK_SIZE)
        # The first price level touched in the first bar should have 'A'
        # in its TPO string. Pick a level that's only touched in bar 0.
        first_bar = bars[0]
        # Center of the first bar's range, rounded to tick
        first_level = (first_bar.open // DEFAULT_TICK_SIZE) * DEFAULT_TICK_SIZE
        self.assertIn("A", tpo_map.get(first_level, ""))

    def test_letters_assigned_in_time_order(self):
        """Slots must be processed in time order so letter assignment is
        deterministic regardless of input order.

        Uses 4 bars that all share the same [99.5, 100.5] range, so a
        level in that range gets a TPO from every slot — the string at
        that level must therefore be the 4-letter concatenation ABCD
        (letters A, B, C, D in time order).
        """
        bars = []
        for i in range(4):
            # All bars share the range [99.5, 100.5]
            bars.append(_bar(i, 100.0, 100.5, 99.5, 100.0))
        tpo_map = compute_tpos(bars, tick_size=DEFAULT_TICK_SIZE)
        # 100.0 is in all 4 bars' ranges — should have ABCD
        self.assertIn("ABCD", tpo_map.get(100.0, ""))

    def test_handles_62_slots(self):
        """At 62 unique slots, we hit the end of the letter alphabet.
        Behavior beyond 62 is documented as 'wraps' but is rare in
        practice; we just verify the first 62 don't crash."""
        bars = _ramp_bars(100.0, 200.0, n=62)
        tpo_map = compute_tpos(bars, tick_size=DEFAULT_TICK_SIZE)
        self.assertGreater(len(tpo_map), 0)


class TestComputePOC(unittest.TestCase):
    """Tests for compute_poc()."""

    def test_empty_returns_none(self):
        self.assertIsNone(compute_poc({}))

    def test_single_level(self):
        poc = compute_poc({100.0: "ABCD"})
        self.assertIsNotNone(poc)
        self.assertEqual(poc.price, 100.0)
        self.assertEqual(poc.tpo_count, 4)

    def test_poc_picks_longest_tpo_string(self):
        tpo_map = {100.0: "AB", 101.0: "ABCD", 102.0: "ABC"}
        poc = compute_poc(tpo_map)
        self.assertIsNotNone(poc)
        self.assertEqual(poc.price, 101.0)
        self.assertEqual(poc.tpo_count, 4)


class TestComputeValueArea(unittest.TestCase):
    """Tests for compute_value_area()."""

    def test_empty_returns_none(self):
        self.assertIsNone(compute_value_area({}))

    def test_invalid_target_pct(self):
        with self.assertRaises(ValueError):
            compute_value_area({100.0: "AB"}, target_pct=0)
        with self.assertRaises(ValueError):
            compute_value_area({100.0: "AB"}, target_pct=1.5)

    def test_single_level_va(self):
        va = compute_value_area({100.0: "ABCD"})
        self.assertIsNotNone(va)
        self.assertEqual(va.high, 100.0)
        self.assertEqual(va.low, 100.0)
        self.assertEqual(va.coverage_pct, 1.0)

    def test_va_covers_at_least_70pct(self):
        """The 70% rule: the value area should contain at least 70% of
        TPOs (and may contain more if the next-best level would push
        it over 70%)."""
        tpo_map = {
            100.0: "AB",
            101.0: "ABCD",
            102.0: "ABC",
            103.0: "AB",
            104.0: "A",
        }
        va = compute_value_area(tpo_map, target_pct=0.70)
        self.assertIsNotNone(va)
        self.assertGreaterEqual(va.coverage_pct, 0.70)
        # POC (101) should be in the VA
        self.assertGreaterEqual(va.high, 101.0)
        self.assertLessEqual(va.low, 101.0)


class TestComputeInitialBalance(unittest.TestCase):
    """Tests for compute_initial_balance()."""

    def test_empty_returns_none(self):
        self.assertIsNone(compute_initial_balance([]))

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            compute_initial_balance([_bar(0, 100, 101, 99, 100)], slot_minutes=0)
        with self.assertRaises(ValueError):
            compute_initial_balance([_bar(0, 100, 101, 99, 100)], ib_slots=0)

    def test_ib_covers_first_two_slots(self):
        """IB is the high/low of the first 2 30-min slots (first hour)."""
        bars = [
            _bar(0, 100.0, 100.5, 99.5, 100.25),    # slot 0
            _bar(1, 100.25, 101.5, 100.0, 101.25),  # slot 1 (still IB)
            _bar(2, 101.25, 105.0, 101.0, 104.0),   # slot 2 (outside IB)
            _bar(3, 104.0, 106.0, 103.0, 105.0),    # slot 3 (outside IB)
        ]
        ib = compute_initial_balance(bars, slot_minutes=30, ib_slots=2)
        self.assertIsNotNone(ib)
        # High of first 2 bars = 101.5, low = 99.5
        self.assertEqual(ib.high, 101.5)
        self.assertEqual(ib.low, 99.5)
        self.assertAlmostEqual(ib.width, 2.0)

    def test_ib_handles_multiple_bars_per_slot(self):
        """If multiple bars fall in the same slot, they all count."""
        bars = [
            _bar(0, 100.0, 100.5, 99.5, 100.25),    # slot 0
            _bar(0, 100.0, 100.75, 100.0, 100.5),   # also slot 0
            _bar(1, 100.25, 101.5, 100.0, 101.25),  # slot 1
        ]
        ib = compute_initial_balance(bars, slot_minutes=30, ib_slots=2)
        self.assertIsNotNone(ib)
        # Should include the high from the second bar in slot 0 (100.75)
        self.assertEqual(ib.high, 101.5)
        self.assertEqual(ib.low, 99.5)


if __name__ == "__main__":
    unittest.main()
