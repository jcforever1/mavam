"""Order-flow proxy from Mind Markets And Money (ch8-9).

Real order flow requires L2/L3 tick data (buy vs sell volume at each
price). In v1 we don't have that, so we use a candle-structure proxy
that approximates buy/sell pressure from the bar's OHLC + volume.

Proxy formula (per bar):

    range = high - low
    body  = close - open
    delta = (body / range) * volume   if range > 0
            0                          otherwise

This says: a bar that closed near its high with a small range is
all-buying (delta ~= +volume); a bar that closed near its low is
all-selling (delta ~= -volume); a doji (close ~= open) is neutral.

The book (ch8) calls this a "cumulative delta proxy" and acknowledges
it as an approximation. v1.1 can wire in real L2 data via the same
delta interface.

Module is PURE: no I/O, no `time`, no `random`, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .profile import Bar


# ── Per-bar delta ────────────────────────────────────────────────────


@dataclass(frozen=True)
class BarDelta:
    """Order-flow proxy for a single bar."""

    timestamp_unix: int
    delta: float             # signed: + = net buying, - = net selling
    volume: float            # raw volume
    body_to_range: float     # 0.0 (doji) .. 1.0 (full-body bar)
    direction: str           # "buy", "sell", or "neutral"


def compute_bar_delta(bar: Bar) -> BarDelta:
    """Compute the order-flow delta proxy for one bar."""
    rng = bar.high - bar.low
    if rng <= 0:
        return BarDelta(
            timestamp_unix=bar.timestamp_unix,
            delta=0.0,
            volume=bar.volume,
            body_to_range=0.0,
            direction="neutral",
        )
    body = bar.close - bar.open
    ratio = body / rng
    delta = ratio * bar.volume
    if ratio > 0.10:
        direction = "buy"
    elif ratio < -0.10:
        direction = "sell"
    else:
        direction = "neutral"
    return BarDelta(
        timestamp_unix=bar.timestamp_unix,
        delta=delta,
        volume=bar.volume,
        body_to_range=abs(ratio),
        direction=direction,
    )


# ── Cumulative delta ────────────────────────────────────────────────


@dataclass(frozen=True)
class CumulativeDelta:
    """Cumulative delta over a session, plus a few derived signals."""

    bars: tuple[BarDelta, ...]                # per-bar deltas (in order)
    cumulative: tuple[float, ...]             # running sum
    final_delta: float                        # cumulative at the close
    max_delta: float                          # peak cumulative
    min_delta: float                          # trough cumulative
    positive_bars: int                        # count of buy-pressure bars
    negative_bars: int                        # count of sell-pressure bars


def compute_cumulative_delta(bars: Sequence[Bar]) -> CumulativeDelta:
    """Compute per-bar deltas and the running cumulative."""
    if not bars:
        return CumulativeDelta(
            bars=(),
            cumulative=(),
            final_delta=0.0,
            max_delta=0.0,
            min_delta=0.0,
            positive_bars=0,
            negative_bars=0,
        )

    deltas = [compute_bar_delta(b) for b in bars]
    cumulative: list[float] = []
    running = 0.0
    pos = 0
    neg = 0
    for d in deltas:
        running += d.delta
        cumulative.append(running)
        if d.delta > 0:
            pos += 1
        elif d.delta < 0:
            neg += 1

    return CumulativeDelta(
        bars=tuple(deltas),
        cumulative=tuple(cumulative),
        final_delta=running,
        max_delta=max(cumulative),
        min_delta=min(cumulative),
        positive_bars=pos,
        negative_bars=neg,
    )


# ── Divergence detection ────────────────────────────────────────────


class DivergenceType(str, Enum):
    """Detected divergence between price and cumulative delta."""

    BULLISH = "bullish"        # price made lower low, delta made higher low
    BEARISH = "bearish"        # price made higher high, delta made lower high
    NONE = "none"


@dataclass(frozen=True)
class Divergence:
    """A detected divergence event between price and cumulative delta.

    Book ch9: divergences are the most actionable order-flow signals.
    A bearish divergence (price higher high + delta lower high) suggests
    the uptrend is weakening and a reversal is near.
    """

    divergence_type: DivergenceType
    confidence: float          # 0.0 .. 1.0
    rationale: str
    price_at_first: float      # price at the first extreme
    price_at_second: float     # price at the second extreme
    delta_at_first: float
    delta_at_second: float


def detect_divergence(
    bars: Sequence[Bar],
    cumulative: CumulativeDelta,
) -> Divergence:
    """Detect a single dominant divergence in the session.

    Heuristic: find the two most-significant price extremes in the
    session and check if the cumulative delta at those points
    diverges. Returns the strongest divergence (bearish or bullish),
    or NONE if the deltas are consistent with price.
    """
    if len(bars) < 4 or len(cumulative.cumulative) < 4:
        return Divergence(
            divergence_type=DivergenceType.NONE,
            confidence=0.0,
            rationale="insufficient bars",
            price_at_first=0.0,
            price_at_second=0.0,
            delta_at_first=0.0,
            delta_at_second=0.0,
        )

    # Find the most-significant swing high and swing low in price.
    # A simple approach: split the session in two halves and compare
    # the highs and lows.
    mid = len(bars) // 2
    first_half = bars[:mid]
    second_half = bars[mid:]

    first_high = max(b.high for b in first_half)
    second_high = max(b.high for b in second_half)
    first_low = min(b.low for b in first_half)
    second_low = min(b.low for b in second_half)

    first_high_idx = next(i for i, b in enumerate(first_half) if b.high == first_high)
    second_high_idx = mid + next(i for i, b in enumerate(second_half) if b.high == second_high)
    first_low_idx = next(i for i, b in enumerate(first_half) if b.low == first_low)
    second_low_idx = mid + next(i for i, b in enumerate(second_half) if b.low == second_low)

    # Cumulative delta at the bar indices
    cum = cumulative.cumulative
    delta_first_high = cum[first_high_idx]
    delta_second_high = cum[second_high_idx]
    delta_first_low = cum[first_low_idx]
    delta_second_low = cum[second_low_idx]

    # Bearish divergence: price higher high, delta lower high
    if second_high > first_high and delta_second_high < delta_first_high:
        confidence = min(1.0, abs(delta_first_high - delta_second_high) / max(1.0, abs(delta_first_high)))
        return Divergence(
            divergence_type=DivergenceType.BEARISH,
            confidence=confidence,
            rationale=(
                f"price made higher high ({first_high:.2f} -> {second_high:.2f}) "
                f"but cumulative delta made lower high "
                f"({delta_first_high:.0f} -> {delta_second_high:.0f}) — "
                f"weakening uptrend"
            ),
            price_at_first=first_high,
            price_at_second=second_high,
            delta_at_first=delta_first_high,
            delta_at_second=delta_second_high,
        )

    # Bullish divergence: price lower low, delta higher low
    if second_low < first_low and delta_second_low > delta_first_low:
        confidence = min(1.0, abs(delta_second_low - delta_first_low) / max(1.0, abs(delta_first_low)))
        return Divergence(
            divergence_type=DivergenceType.BULLISH,
            confidence=confidence,
            rationale=(
                f"price made lower low ({first_low:.2f} -> {second_low:.2f}) "
                f"but cumulative delta made higher low "
                f"({delta_first_low:.0f} -> {delta_second_low:.0f}) — "
                f"weakening downtrend"
            ),
            price_at_first=first_low,
            price_at_second=second_low,
            delta_at_first=delta_first_low,
            delta_at_second=delta_second_low,
        )

    return Divergence(
        divergence_type=DivergenceType.NONE,
        confidence=0.0,
        rationale="no clear divergence between price and cumulative delta",
        price_at_first=0.0,
        price_at_second=0.0,
        delta_at_first=0.0,
        delta_at_second=0.0,
    )


# ── Convenience classifier ──────────────────────────────────────────


def orderflow_signal(
    bars: Sequence[Bar],
) -> tuple[CumulativeDelta, Divergence]:
    """One-shot helper: compute cumulative delta + detect divergence."""
    cd = compute_cumulative_delta(bars)
    div = detect_divergence(bars, cd)
    return cd, div


__all__ = [
    "BarDelta",
    "CumulativeDelta",
    "Divergence",
    "DivergenceType",
    "compute_bar_delta",
    "compute_cumulative_delta",
    "detect_divergence",
    "orderflow_signal",
]
