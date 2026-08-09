"""Market Profile primitives from Mind Markets And Money (ch3).

Computes from intraday OHLCV bars:
- TPOs (Time Price Opportunities): 30-min letters marking each price
  level that traded during that 30-min slot
- POC (Point of Control): the price with the most TPOs
- Value Area (70%): the price range containing 70% of TPOs
- Initial Balance (IB): high/low of the first hour (first two 30-min slots)

The book chapter 3 (Market Profile) introduces TPOs as the foundational
"letter" that turns a continuous price tape into a discrete distribution.

This module is PURE: no I/O, no `time`, no `random`, no network.
All inputs come from `data/loader.py`. All randomness is banned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# ── Input contract: 30-min OHLCV bars ───────────────────────────────────
# A "bar" is (timestamp_unix, open, high, low, close, volume).
# TPOs use high and low (the range traded in that 30-min slot), not
# close, because we want the discrete distribution of touched prices.


@dataclass(frozen=True)
class Bar:
    """A single intraday OHLCV bar. Frozen for hashability."""

    timestamp_unix: int
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── TPO computation ────────────────────────────────────────────────────

# TPO granularity: 30-minute slots (per the book's convention).
# 30-min means each bar represents a 30-minute window.
# In v1 we accept already-binned 30-min bars; binning from 1-min
# is a v1.1 enhancement.

# Price granularity for TPO letter placement: typically one tick
# (0.01 for liquid US equities, $0.25 for SPY/QQQ).
# For v1, we round to a configurable tick size; default is $0.25.

DEFAULT_TICK_SIZE = 0.25


def _price_levels_in_range(low: float, high: float, tick: float) -> list[float]:
    """Return the discrete price levels touched between low and high,
    inclusive, in increments of `tick`. Used for TPO letter placement.
    """
    if high < low:
        return []
    # Round low DOWN to nearest tick, high UP to nearest tick
    lo = (low // tick) * tick
    hi = (high // tick + 1) * tick
    # Build the list of ticks
    n = int(round((hi - lo) / tick)) + 1
    return [lo + i * tick for i in range(n)]


def compute_tpos(
    bars: Sequence[Bar],
    tick_size: float = DEFAULT_TICK_SIZE,
) -> dict[float, str]:
    """Compute the TPO letter for each price level from intraday bars.

    Each 30-min bar gets a letter (A, B, C, ..., Z, then a, b, c, ...).
    Every price level touched during that bar gets that letter.

    Returns a dict mapping price level -> TPO string. A level with
    TPOs "AB" means it traded during the first two 30-min slots.

    The book's ch3 example: a single letter per slot, stacked
    vertically to form the profile's "TPO shape".
    """
    if not bars:
        return {}
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size}")

    # 30-min slot index from unix timestamp
    SLOT_SECONDS = 30 * 60
    TPO_LETTERS = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"  # 62 slots; beyond 62 we wrap (rare)
    )

    # Group bars by slot index
    slot_bars: dict[int, list[Bar]] = {}
    for bar in bars:
        slot = bar.timestamp_unix // SLOT_SECONDS
        slot_bars.setdefault(slot, []).append(bar)

    # Build TPO map
    tpo_map: dict[float, str] = {}
    # Letter assignment is by POSITION in the session, not by absolute
    # slot index. Use enumerate so the first slot gets 'A', the second 'B',
    # etc. (The prior code used `slot_idx` as the letter index, which broke
    # for any timestamp in the modern epoch because slot_idx >> 62.)
    sorted_slots = sorted(slot_bars.keys())[: len(TPO_LETTERS)]
    for letter_idx, slot_idx in enumerate(sorted_slots):
        letter = TPO_LETTERS[letter_idx]
        for bar in slot_bars[slot_idx]:
            for level in _price_levels_in_range(bar.low, bar.high, tick_size):
                if level in tpo_map:
                    tpo_map[level] = tpo_map[level] + letter
                else:
                    tpo_map[level] = letter
    return tpo_map


# ── POC: Point of Control ─────────────────────────────────────────────

@dataclass(frozen=True)
class POC:
    """The price with the most TPO letters (= the most-traded level)."""

    price: float
    tpo_count: int


def compute_poc(tpo_map: dict[float, str]) -> POC | None:
    """Return the price level with the longest TPO string (= POC)."""
    if not tpo_map:
        return None
    poc_price, poc_tpos = max(tpo_map.items(), key=lambda kv: len(kv[1]))
    return POC(price=poc_price, tpo_count=len(poc_tpos))


# ── Value Area (70% rule) ──────────────────────────────────────────────

@dataclass(frozen=True)
class ValueArea:
    """The price range containing 70% of all TPO letters."""

    high: float
    low: float
    coverage_pct: float  # actual % of TPOs within the band; should be ~70%
    total_tpos: int

    @property
    def width(self) -> float:
        return self.high - self.low


def compute_value_area(
    tpo_map: dict[float, str],
    target_pct: float = 0.70,
) -> ValueArea | None:
    """Compute the Value Area using the book's 70% rule.

    Algorithm (from the book):
    1. Start at the POC.
    2. Expand outward to the price level (above or below POC) that
       contributes the most TPOs and is NOT yet in the value area.
    3. Stop when adding the next-best level would push the cumulative
       TPO coverage above target_pct.
    4. Return the high and low of the band.
    """
    if not tpo_map:
        return None
    if not 0.0 < target_pct <= 1.0:
        raise ValueError(f"target_pct must be in (0, 1], got {target_pct}")

    total_tpos = sum(len(s) for s in tpo_map.values())
    if total_tpos == 0:
        return None

    # POC
    poc_price = max(tpo_map.items(), key=lambda kv: len(kv[1]))[0]
    included = {poc_price}
    included_tpos = len(tpo_map[poc_price])
    target_count = target_pct * total_tpos

    # Get all levels sorted by distance from POC
    levels = sorted(tpo_map.keys())
    above = [p for p in levels if p > poc_price]
    below = [p for p in levels if p < poc_price]

    # Expand outward — at each step, add the level with more TPOs
    while included_tpos < target_count and (above or below):
        best_above = above[0] if above else None
        best_below = below[-1] if below else None
        cand_above = len(tpo_map[best_above]) if best_above is not None else -1
        cand_below = len(tpo_map[best_below]) if best_below is not None else -1
        if cand_above >= cand_below and best_above is not None:
            included.add(best_above)
            included_tpos += len(tpo_map[best_above])
            above = above[1:]
        elif best_below is not None:
            included.add(best_below)
            included_tpos += len(tpo_map[best_below])
            below = below[:-1]
        else:
            break

    return ValueArea(
        high=max(included),
        low=min(included),
        coverage_pct=included_tpos / total_tpos,
        total_tpos=total_tpos,
    )


# ── Initial Balance (IB) ───────────────────────────────────────────────

@dataclass(frozen=True)
class InitialBalance:
    """The high and low of the first hour (first two 30-min slots).

    The book calls out IB as a key reference level: a break above IB
    high is often a trend-day signal; a break below IB low is the
    bearish analog. The IB range itself defines the "balance area"
    before the market commits to a direction.
    """

    high: float
    low: float
    start_unix: int  # unix timestamp of the first bar in the IB window
    end_unix: int    # unix timestamp of the last bar

    @property
    def width(self) -> float:
        return self.high - self.low


def compute_initial_balance(
    bars: Sequence[Bar],
    slot_minutes: int = 30,
    ib_slots: int = 2,
) -> InitialBalance | None:
    """Compute the Initial Balance (first `ib_slots` slots of the session)."""
    if not bars:
        return None
    if slot_minutes <= 0 or ib_slots <= 0:
        raise ValueError(
            f"slot_minutes and ib_slots must be > 0, "
            f"got {slot_minutes}, {ib_slots}"
        )

    SLOT_SECONDS = slot_minutes * 60
    # Take the first `ib_slots` unique slot indices
    slots_seen: list[int] = []
    for bar in bars:
        slot = bar.timestamp_unix // SLOT_SECONDS
        if slot not in slots_seen:
            slots_seen.append(slot)
            if len(slots_seen) >= ib_slots:
                break

    target_slots = set(slots_seen)
    ib_bars = [b for b in bars if (b.timestamp_unix // SLOT_SECONDS) in target_slots]
    if not ib_bars:
        return None

    return InitialBalance(
        high=max(b.high for b in ib_bars),
        low=min(b.low for b in ib_bars),
        start_unix=ib_bars[0].timestamp_unix,
        end_unix=ib_bars[-1].timestamp_unix,
    )
