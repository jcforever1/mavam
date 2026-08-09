"""Session classification from Mind Markets And Money (ch4-6).

Five classifiers that turn a bar sequence + Market Profile snapshot
into a typed classification of the trading day:

  1. DayType         - 6 day types from ch5 (NORMAL, TREND_*, NEUTRAL,
                       P_DAY, B_DAY, DOUBLE_DISTRIBUTION)
  2. OpenType        - 6 open types from ch4 (OPEN_DRIVE, OPEN_TEST_DRIVE,
                       OPEN_REJECTION_REVERSE, OPEN_RANGE_EXTENSION,
                       OPEN_RANGE_TRANSITION, OPEN_AUCTION)
  3. BalanceState    - 3 balanced + 3 imbalanced cases from ch5
  4. InitiativeActivity - INITIATIVE_* vs RESPONSIVE_* from ch6
  5. TrendState      - TRENDING_* vs BRACKETED / TWO_SIDED from ch5

These classifications are the book rules that the Adjudicator consumes
via RuleTrace records. Every classifier is PURE: no I/O, no `time`, no
`random`, no network. All randomness is banned. The output is a frozen
dataclass so the artifact layer can content-hash it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .profile import (
    Bar,
    InitialBalance,
    POC,
    ValueArea,
    compute_initial_balance,
    compute_poc,
    compute_tpos,
    compute_value_area,
    DEFAULT_TICK_SIZE,
)


# ── Enums (string-valued so they JSON-serialize cleanly) ──────────────


class DayType(str, Enum):
    """The book's 6 day types (ch5) + neutral fallback."""

    NORMAL_DAY = "normal_day"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    NEUTRAL = "neutral"
    P_DAY = "p_day"        # short covering — opened near low, closed near high
    B_DAY = "b_day"        # long liquidation — opened near high, closed near low
    DOUBLE_DISTRIBUTION = "double_distribution"


class OpenType(str, Enum):
    """The book's 6 open types (ch4)."""

    OPEN_DRIVE = "open_drive"                       # OR committed immediately
    OPEN_TEST_DRIVE = "open_test_drive"             # tested opposite side, then drove
    OPEN_REJECTION_REVERSE = "open_rejection_reverse"  # opened, immediately failed
    OPEN_RANGE_EXTENSION = "open_range_extension"   # OR held, then extended
    OPEN_RANGE_TRANSITION = "open_range_transition" # OR became a new balance
    OPEN_AUCTION = "open_auction"                   # tested both sides without committing


class BalanceState(str, Enum):
    """The book's 3+3 balance cases (ch5)."""

    BALANCED_AT_TOP = "balanced_at_top"
    BALANCED_AT_MID = "balanced_at_mid"
    BALANCED_AT_BOT = "balanced_at_bot"
    IMBALANCED_UP = "imbalanced_up"
    IMBALANCED_DOWN = "imbalanced_down"
    IMBALANCED_TWO_SIDED = "imbalanced_two_sided"


class InitiativeActivity(str, Enum):
    """Initiative vs responsive activity (ch6)."""

    INITIATIVE_BUYING = "initiative_buying"
    INITIATIVE_SELLING = "initiative_selling"
    RESPONSIVE_BUYING = "responsive_buying"
    RESPONSIVE_SELLING = "responsive_selling"
    NEUTRAL = "neutral"


class TrendState(str, Enum):
    """Trending vs bracketed vs two-sided (ch5)."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    BRACKETED = "bracketed"
    TWO_SIDED = "two_sided"


# ── Result dataclasses (frozen, hashable) ─────────────────────────────


@dataclass(frozen=True)
class DayClassification:
    """Result of the day-type classifier."""

    day_type: DayType
    confidence: float          # 0.0 (no signal) .. 1.0 (high conviction)
    rationale: str
    supporting_metrics: dict[str, float]


@dataclass(frozen=True)
class OpenClassification:
    """Result of the open-type classifier."""

    open_type: OpenType
    confidence: float
    rationale: str
    or_high: float
    or_low: float


@dataclass(frozen=True)
class BalanceClassification:
    """Result of the balance classifier."""

    state: BalanceState
    confidence: float
    rationale: str
    close_position_in_va: float   # 0.0 = at VA low, 1.0 = at VA high, may be outside [0,1]


@dataclass(frozen=True)
class InitiativeClassification:
    """Result of the initiative-vs-responsive classifier."""

    activity: InitiativeActivity
    confidence: float
    rationale: str


@dataclass(frozen=True)
class TrendClassification:
    """Result of the trend classifier."""

    state: TrendState
    confidence: float
    rationale: str


@dataclass(frozen=True)
class SessionClassification:
    """Composite classification of one intraday session.

    All five classifiers in one frozen record, plus the reference levels
    they used (denormalized for downstream consumers — the Adjudicator
    wants to read them without re-walking the bars).
    """

    day: DayClassification
    open: OpenClassification
    balance: BalanceClassification
    initiative: InitiativeClassification
    trend: TrendClassification

    # Reference levels
    ib_high: float
    ib_low: float
    va_high: float
    va_low: float
    poc: float
    session_high: float
    session_low: float
    session_close: float


# ── Opening Range helper ─────────────────────────────────────────────

SLOT_SECONDS = 30 * 60


def _first_n_bars(bars: Sequence[Bar], minutes: int) -> list[Bar]:
    """Return all bars in the first `minutes` minutes of the session.

    Assumes 30-min slot granularity (the book's convention). For
    non-30-min bar sizes, all bars in the slot containing the first bar
    are returned.
    """
    if not bars or minutes <= 0:
        return []
    target_slot = bars[0].timestamp_unix // SLOT_SECONDS
    if minutes <= 30:
        return [b for b in bars if (b.timestamp_unix // SLOT_SECONDS) == target_slot]
    # multi-slot OR (e.g. 60 min = first 2 slots)
    n_slots = minutes // 30
    slots = {target_slot + i for i in range(n_slots)}
    return [b for b in bars if (b.timestamp_unix // SLOT_SECONDS) in slots]


def _compute_opening_range(
    bars: Sequence[Bar], or_minutes: int = 30
) -> tuple[float, float] | None:
    """Opening Range: high and low of the first `or_minutes` minutes."""
    or_bars = _first_n_bars(bars, or_minutes)
    if not or_bars:
        return None
    return (max(b.high for b in or_bars), min(b.low for b in or_bars))


def _session_extremes(bars: Sequence[Bar]) -> tuple[float, float]:
    """Return (session_high, session_low) over the full bar set."""
    return (max(b.high for b in bars), min(b.low for b in bars))


# ── Day-type classifier (ch5) ────────────────────────────────────────


def classify_day_type(
    bars: Sequence[Bar],
    ib: InitialBalance,
    va: ValueArea,
    poc: POC,
) -> DayClassification:
    """Classify the day type using the book's Market Profile taxonomy.

    Decision tree (applied in order):

      The book distinguishes trend days from P-/B-days by the FIRST BAR's
      direction (book ch5):

      - TREND_UP    : bullish first bar + close extended >0.5 IB above IB.high
      - P_DAY       : bearish first bar + close extended >0.5 IB above IB.high
                      (shorts covering — no new buyers, just short squeeze)
      - TREND_DOWN  : bearish first bar + close extended >0.5 IB below IB.low
      - B_DAY       : bullish first bar + close extended >0.5 IB below IB.low
                      (longs liquidating — no new sellers, just profit-take)

      Falls through to DOUBLE_DISTRIBUTION (very wide VA), NEUTRAL
      (narrow range, close near mid), or NORMAL_DAY (default).
    """
    if not bars:
        return DayClassification(DayType.NEUTRAL, 0.0, "no bars", {})

    close = bars[-1].close
    session_high, session_low = _session_extremes(bars)
    session_range = session_high - session_low
    if session_range <= 0:
        return DayClassification(DayType.NEUTRAL, 0.0, "zero session range", {})

    close_pct = (close - session_low) / session_range
    va_width = va.high - va.low
    ib_width = ib.high - ib.low
    close_relative_to_poc = (close - poc.price) / va_width if va_width > 0 else 0.0

    first_bar = bars[0]
    first_bullish = first_bar.close > first_bar.open
    first_bearish = first_bar.close < first_bar.open

    # Extension thresholds — book ch5: "price extended well beyond IB"
    up_extended = close > ib.high + 0.5 * ib_width
    down_extended = close < ib.low - 0.5 * ib_width
    close_in_upper = close_pct > 0.50
    close_in_lower = close_pct < 0.50

    metrics = {
        "close_pct_in_session": close_pct,
        "close_relative_to_poc": close_relative_to_poc,
        "session_range": session_range,
        "va_width": va_width,
        "ib_width": ib_width,
        "first_bullish": float(first_bullish),
        "first_bearish": float(first_bearish),
        "up_extended": float(up_extended),
        "down_extended": float(down_extended),
    }

    # 1. TREND_UP — bullish first bar + extended up
    if first_bullish and up_extended and close_in_upper:
        return DayClassification(
            day_type=DayType.TREND_UP,
            confidence=0.85,
            rationale=(
                f"bullish first bar (open {first_bar.open:.2f} -> "
                f"close {first_bar.close:.2f}) and close {close:.2f} extended "
                f"{close - ib.high:.2f} above IB high {ib.high:.2f}"
            ),
            supporting_metrics=metrics,
        )

    # 2. P_DAY — bearish first bar + extended up (shorts covering)
    if first_bearish and up_extended and close_in_upper:
        return DayClassification(
            day_type=DayType.P_DAY,
            confidence=0.80,
            rationale=(
                f"bearish first bar (open {first_bar.open:.2f} -> "
                f"close {first_bar.close:.2f}) then close {close:.2f} recovered "
                f"to upper half — short-covering profile (book ch5)"
            ),
            supporting_metrics=metrics,
        )

    # 3. TREND_DOWN — bearish first bar + extended down
    if first_bearish and down_extended and close_in_lower:
        return DayClassification(
            day_type=DayType.TREND_DOWN,
            confidence=0.85,
            rationale=(
                f"bearish first bar (open {first_bar.open:.2f} -> "
                f"close {first_bar.close:.2f}) and close {close:.2f} extended "
                f"{ib.low - close:.2f} below IB low {ib.low:.2f}"
            ),
            supporting_metrics=metrics,
        )

    # 4. B_DAY — bullish first bar + extended down (longs liquidating)
    if first_bullish and down_extended and close_in_lower:
        return DayClassification(
            day_type=DayType.B_DAY,
            confidence=0.80,
            rationale=(
                f"bullish first bar (open {first_bar.open:.2f} -> "
                f"close {first_bar.close:.2f}) then close {close:.2f} dropped "
                f"to lower half — long-liquidation profile (book ch5)"
            ),
            supporting_metrics=metrics,
        )

    # 5. DOUBLE_DISTRIBUTION
    if va_width >= 2 * ib_width and ib_width > 0:
        return DayClassification(
            day_type=DayType.DOUBLE_DISTRIBUTION,
            confidence=0.70,
            rationale=(
                f"VA width {va_width:.2f} >= 2x IB range {ib_width:.2f}"
            ),
            supporting_metrics=metrics,
        )

    # 6. NEUTRAL
    if session_range < 1.5 * va_width and 0.30 < close_pct < 0.70:
        return DayClassification(
            day_type=DayType.NEUTRAL,
            confidence=0.70,
            rationale=(
                f"close {close:.2f} near session mid, range {session_range:.2f} "
                f"narrow vs VA {va_width:.2f}"
            ),
            supporting_metrics=metrics,
        )

    # 7. NORMAL_DAY (default — value-area acceptance with no trend)
    return DayClassification(
        day_type=DayType.NORMAL_DAY,
        confidence=0.65,
        rationale=(
            f"close {close:.2f} in VA [{va.low:.2f}, {va.high:.2f}], "
            f"no trend extension observed"
        ),
        supporting_metrics=metrics,
    )


# ── Open-type classifier (ch4) ───────────────────────────────────────


def classify_open_type(
    bars: Sequence[Bar],
    ib: InitialBalance,
) -> OpenClassification:
    """Classify the open type using the book's 6-type taxonomy (ch4).

    Reads the first two 30-min bars (Opening Range + first extension)
    and decides which of the 6 open types fits.
    """
    if len(bars) < 2:
        return OpenClassification(
            open_type=OpenType.OPEN_AUCTION,
            confidence=0.0,
            rationale="insufficient bars",
            or_high=0.0,
            or_low=0.0,
        )

    or_range = _compute_opening_range(bars, or_minutes=30)
    if or_range is None:
        return OpenClassification(
            open_type=OpenType.OPEN_AUCTION,
            confidence=0.0,
            rationale="no opening range",
            or_high=0.0,
            or_low=0.0,
        )
    or_high, or_low = or_range
    or_width = or_high - or_low

    if or_width <= 0:
        return OpenClassification(
            open_type=OpenType.OPEN_AUCTION,
            confidence=0.0,
            rationale="zero OR width",
            or_high=or_high,
            or_low=or_low,
        )

    first_bar = bars[0]
    second_bar = bars[1]
    third_bar = bars[2] if len(bars) >= 3 else None

    first_dir = "up" if first_bar.close > first_bar.open else "down"
    second_dir = "up" if second_bar.close > second_bar.open else "down"

    # 1. OPEN_DRIVE — second bar commits in opening direction
    if first_dir == "up" and second_bar.close > or_high and second_dir == "up":
        return OpenClassification(
            open_type=OpenType.OPEN_DRIVE,
            confidence=0.85,
            rationale=(
                f"second bar closed {second_bar.close:.2f} above OR high "
                f"{or_high:.2f} in opening direction"
            ),
            or_high=or_high,
            or_low=or_low,
        )
    if first_dir == "down" and second_bar.close < or_low and second_dir == "down":
        return OpenClassification(
            open_type=OpenType.OPEN_DRIVE,
            confidence=0.85,
            rationale=(
                f"second bar closed {second_bar.close:.2f} below OR low "
                f"{or_low:.2f} in opening direction"
            ),
            or_high=or_high,
            or_low=or_low,
        )

    # 2. OPEN_REJECTION_REVERSE — second bar immediately reverses
    if first_dir == "up" and second_bar.close < or_low:
        return OpenClassification(
            open_type=OpenType.OPEN_REJECTION_REVERSE,
            confidence=0.80,
            rationale=(
                f"opened up; second bar closed {second_bar.close:.2f} below "
                f"OR low {or_low:.2f} — opening drive rejected"
            ),
            or_high=or_high,
            or_low=or_low,
        )
    if first_dir == "down" and second_bar.close > or_high:
        return OpenClassification(
            open_type=OpenType.OPEN_REJECTION_REVERSE,
            confidence=0.80,
            rationale=(
                f"opened down; second bar closed {second_bar.close:.2f} above "
                f"OR high {or_high:.2f} — opening drive rejected"
            ),
            or_high=or_high,
            or_low=or_low,
        )

    # 3. OPEN_TEST_DRIVE — second bar dips into OR from the other side, then drives
    tested_opposite = (
        (first_dir == "up" and second_bar.low < or_high and second_bar.close > second_bar.open and second_bar.close > first_bar.open)
        or (first_dir == "down" and second_bar.high > or_low and second_bar.close < second_bar.open and second_bar.close < first_bar.open)
    )
    if tested_opposite:
        return OpenClassification(
            open_type=OpenType.OPEN_TEST_DRIVE,
            confidence=0.75,
            rationale=(
                f"second bar tested opposite OR side and resumed opening direction"
            ),
            or_high=or_high,
            or_low=or_low,
        )

    # 4. OPEN_RANGE_EXTENSION — third bar extends after first two stayed inside OR
    if third_bar is not None:
        if first_dir == "up" and third_bar.close > or_high and third_bar.close > third_bar.open:
            return OpenClassification(
                open_type=OpenType.OPEN_RANGE_EXTENSION,
                confidence=0.70,
                rationale=(
                    f"OR held for two bars; third bar extended above OR high "
                    f"{or_high:.2f}"
                ),
                or_high=or_high,
                or_low=or_low,
            )
        if first_dir == "down" and third_bar.close < or_low and third_bar.close < third_bar.open:
            return OpenClassification(
                open_type=OpenType.OPEN_RANGE_EXTENSION,
                confidence=0.70,
                rationale=(
                    f"OR held for two bars; third bar extended below OR low "
                    f"{or_low:.2f}"
                ),
                or_high=or_high,
                or_low=or_low,
            )

    # 5. OPEN_RANGE_TRANSITION — close near OR mid, OR becomes a balance
    last_bar = bars[-1]
    mid_distance = abs(last_bar.close - (or_high + or_low) / 2)
    if mid_distance < 0.30 * or_width:
        return OpenClassification(
            open_type=OpenType.OPEN_RANGE_TRANSITION,
            confidence=0.65,
            rationale=(
                f"close {last_bar.close:.2f} near OR mid — OR has become a balance"
            ),
            or_high=or_high,
            or_low=or_low,
        )

    # 6. OPEN_AUCTION — default
    return OpenClassification(
        open_type=OpenType.OPEN_AUCTION,
        confidence=0.55,
        rationale=(
            f"price tested both sides of OR without committing; auction profile"
        ),
        or_high=or_high,
        or_low=or_low,
    )


# ── Balance classifier (ch5) ─────────────────────────────────────────


def classify_balance(
    bars: Sequence[Bar],
    va: ValueArea,
) -> BalanceClassification:
    """Classify balance state (3 balanced + 3 imbalanced cases per book ch5).

    Balanced cases: close within VA, in upper / mid / lower third.
    Imbalanced cases: close outside VA, on the up / down / two-sided side.
    """
    if not bars:
        return BalanceClassification(
            state=BalanceState.BALANCED_AT_MID,
            confidence=0.0,
            rationale="no bars",
            close_position_in_va=0.5,
        )

    close = bars[-1].close
    va_width = va.high - va.low
    if va_width <= 0:
        return BalanceClassification(
            state=BalanceState.BALANCED_AT_MID,
            confidence=0.0,
            rationale="zero VA",
            close_position_in_va=0.5,
        )

    close_pos = (close - va.low) / va_width

    # Imbalanced: close outside VA
    if close > va.high:
        return BalanceClassification(
            state=BalanceState.IMBALANCED_UP,
            confidence=0.85,
            rationale=(
                f"close {close:.2f} > VA high {va.high:.2f} — one-sided imbalance up"
            ),
            close_position_in_va=close_pos,
        )
    if close < va.low:
        return BalanceClassification(
            state=BalanceState.IMBALANCED_DOWN,
            confidence=0.85,
            rationale=(
                f"close {close:.2f} < VA low {va.low:.2f} — one-sided imbalance down"
            ),
            close_position_in_va=close_pos,
        )

    # Two-sided imbalance: price traded BOTH sides of VA during the session
    session_high, session_low = _session_extremes(bars)
    if session_high > va.high and session_low < va.low:
        return BalanceClassification(
            state=BalanceState.IMBALANCED_TWO_SIDED,
            confidence=0.75,
            rationale=(
                f"close in VA but session extended both above {va.high:.2f} "
                f"and below {va.low:.2f} — two-sided imbalance"
            ),
            close_position_in_va=close_pos,
        )

    # Balanced cases — split VA into thirds
    if close_pos > 0.66:
        return BalanceClassification(
            state=BalanceState.BALANCED_AT_TOP,
            confidence=0.75,
            rationale=(
                f"close in upper third of VA ({close_pos:.2f}) — balanced at top"
            ),
            close_position_in_va=close_pos,
        )
    if close_pos < 0.33:
        return BalanceClassification(
            state=BalanceState.BALANCED_AT_BOT,
            confidence=0.75,
            rationale=(
                f"close in lower third of VA ({close_pos:.2f}) — balanced at bottom"
            ),
            close_position_in_va=close_pos,
        )
    return BalanceClassification(
        state=BalanceState.BALANCED_AT_MID,
        confidence=0.70,
        rationale=(
            f"close in middle third of VA ({close_pos:.2f}) — balanced at mid"
        ),
        close_position_in_va=close_pos,
    )


# ── Initiative-vs-responsive classifier (ch6) ────────────────────────


def classify_initiative_vs_responsive(
    bars: Sequence[Bar],
    poc: POC,
    va: ValueArea,
) -> InitiativeClassification:
    """Classify whether the session was initiative or responsive (ch6).

    Initiative: net move from open-to-close extends well beyond the
    initial balance, and close is outside VA.
    Responsive: net move is small, or close re-enters VA.
    """
    if not bars:
        return InitiativeClassification(
            activity=InitiativeActivity.NEUTRAL,
            confidence=0.0,
            rationale="no bars",
        )

    first_bar = bars[0]
    last_bar = bars[-1]
    net_move = last_bar.close - first_bar.open
    va_width = va.high - va.low
    if va_width <= 0:
        return InitiativeClassification(
            activity=InitiativeActivity.NEUTRAL,
            confidence=0.0,
            rationale="zero VA",
        )

    initiative_threshold = 0.5 * va_width
    small_move_threshold = 0.2 * va_width

    # Initiative buying
    if net_move > initiative_threshold and last_bar.close > va.high:
        return InitiativeClassification(
            activity=InitiativeActivity.INITIATIVE_BUYING,
            confidence=0.80,
            rationale=(
                f"net move {net_move:.2f} > {initiative_threshold:.2f} "
                f"and close {last_bar.close:.2f} above VA high {va.high:.2f} — "
                f"initiative buying"
            ),
        )

    # Initiative selling
    if net_move < -initiative_threshold and last_bar.close < va.low:
        return InitiativeClassification(
            activity=InitiativeActivity.INITIATIVE_SELLING,
            confidence=0.80,
            rationale=(
                f"net move {net_move:.2f} < -{initiative_threshold:.2f} "
                f"and close {last_bar.close:.2f} below VA low {va.low:.2f} — "
                f"initiative selling"
            ),
        )

    # Small move — ranged / neutral
    if abs(net_move) < small_move_threshold:
        return InitiativeClassification(
            activity=InitiativeActivity.NEUTRAL,
            confidence=0.65,
            rationale=(
                f"net move {net_move:.2f} < {small_move_threshold:.2f} — "
                f"ranged session, no initiative"
            ),
        )

    # Directional but contained — responsive
    if net_move > 0:
        return InitiativeClassification(
            activity=InitiativeActivity.RESPONSIVE_BUYING,
            confidence=0.60,
            rationale=(
                f"small upward move {net_move:.2f} from open, contained in VA — "
                f"responsive buying"
            ),
        )
    return InitiativeClassification(
        activity=InitiativeActivity.RESPONSIVE_SELLING,
        confidence=0.60,
        rationale=(
            f"small downward move {net_move:.2f} from open, contained in VA — "
            f"responsive selling"
        ),
    )


# ── Trend classifier (ch5) ───────────────────────────────────────────


def classify_trend(
    bars: Sequence[Bar],
    va: ValueArea,
) -> TrendClassification:
    """Classify session as trending, bracketed, or two-sided (ch5).

    Uses IB-based extension as the primary trend signal (more robust than
    VA-based, because in a strong trend the value area often captures
    the trending portion and the close sits inside VA).

    Two-sided: VA contains a large fraction of the session range.
    Bracketed: contained in balance (default).
    """
    if not bars:
        return TrendClassification(
            state=TrendState.BRACKETED,
            confidence=0.0,
            rationale="no bars",
        )

    close = bars[-1].close
    session_high, session_low = _session_extremes(bars)
    session_range = session_high - session_low
    if session_range <= 0:
        return TrendClassification(
            state=TrendState.BRACKETED,
            confidence=0.0,
            rationale="zero session range",
        )

    # Compute IB here so we don't recompute the bars list in the caller.
    # The IB-based extension is more robust than VA-based for trends.
    ib = compute_initial_balance(bars, slot_minutes=30, ib_slots=2)
    if ib is None:
        return TrendClassification(
            state=TrendState.BRACKETED,
            confidence=0.0,
            rationale="no initial balance",
        )
    ib_width = ib.high - ib.low

    va_width = va.high - va.low
    close_pct = (close - session_low) / session_range

    # Trending up: close extended >0.5 IB above IB high, in upper half
    if close > ib.high + 0.5 * ib_width and close_pct > 0.55:
        return TrendClassification(
            state=TrendState.TRENDING_UP,
            confidence=0.85,
            rationale=(
                f"close {close:.2f} extended {close - ib.high:.2f} above IB "
                f"high {ib.high:.2f} — trending up"
            ),
        )

    # Trending down
    if close < ib.low - 0.5 * ib_width and close_pct < 0.45:
        return TrendClassification(
            state=TrendState.TRENDING_DOWN,
            confidence=0.85,
            rationale=(
                f"close {close:.2f} extended {ib.low - close:.2f} below IB "
                f"low {ib.low:.2f} — trending down"
            ),
        )

    # Two-sided: VA spans most of the session (no clear direction)
    if va_width > 0.70 * session_range and session_range > 0:
        return TrendClassification(
            state=TrendState.TWO_SIDED,
            confidence=0.70,
            rationale=(
                f"VA width {va_width:.2f} > 70% of session range "
                f"{session_range:.2f} — two-sided auction"
            ),
        )

    # Bracketed (default)
    return TrendClassification(
        state=TrendState.BRACKETED,
        confidence=0.65,
        rationale=(
            f"close in VA, no IB-based trend extension — bracketed balance"
        ),
    )


# ── High-level composite ─────────────────────────────────────────────


def classify_session(
    bars: Sequence[Bar],
    tick_size: float = DEFAULT_TICK_SIZE,
) -> SessionClassification:
    """Compute all five classifications for one intraday session.

    The single entry point. Calls profile.py to build the Market Profile
    snapshot, then runs all five classifiers. Pure: no I/O, no time,
    no random.
    """
    if not bars:
        raise ValueError("bars must be non-empty")

    tpo_map = compute_tpos(bars, tick_size)
    poc = compute_poc(tpo_map)
    va = compute_value_area(tpo_map, target_pct=0.70)
    ib = compute_initial_balance(bars, slot_minutes=30, ib_slots=2)

    if poc is None or va is None or ib is None:
        # Degenerate case — not enough bars to build a profile
        return SessionClassification(
            day=DayClassification(DayType.NEUTRAL, 0.0, "no profile", {}),
            open=OpenClassification(
                OpenType.OPEN_AUCTION, 0.0, "no profile", 0.0, 0.0
            ),
            balance=BalanceClassification(
                BalanceState.BALANCED_AT_MID, 0.0, "no profile", 0.5
            ),
            initiative=InitiativeClassification(
                InitiativeActivity.NEUTRAL, 0.0, "no profile"
            ),
            trend=TrendClassification(TrendState.BRACKETED, 0.0, "no profile"),
            ib_high=0.0,
            ib_low=0.0,
            va_high=0.0,
            va_low=0.0,
            poc=0.0,
            session_high=0.0,
            session_low=0.0,
            session_close=0.0,
        )

    session_high, session_low = _session_extremes(bars)

    return SessionClassification(
        day=classify_day_type(bars, ib, va, poc),
        open=classify_open_type(bars, ib),
        balance=classify_balance(bars, va),
        initiative=classify_initiative_vs_responsive(bars, poc, va),
        trend=classify_trend(bars, va),
        ib_high=ib.high,
        ib_low=ib.low,
        va_high=va.high,
        va_low=va.low,
        poc=poc.price,
        session_high=session_high,
        session_low=session_low,
        session_close=bars[-1].close,
    )


# ── RuleTrace integration (for signal_types.py) ──────────────────────


def session_to_rule_names(sc: SessionClassification) -> list[str]:
    """Emit the rule names that fired in this session classification.

    These are the rule identifiers the Adjudicator can match on in its
    [[merge.rules]] `when` expressions. The mapping is stable — renaming
    a rule is a breaking change for any user-authored Adjudicator TOML.
    """
    return [
        f"day_type={sc.day.day_type.value}",
        f"open_type={sc.open.open_type.value}",
        f"balance={sc.balance.state.value}",
        f"initiative={sc.initiative.activity.value}",
        f"trend={sc.trend.state.value}",
    ]


__all__ = [
    # Enums
    "DayType",
    "OpenType",
    "BalanceState",
    "InitiativeActivity",
    "TrendState",
    # Dataclasses
    "DayClassification",
    "OpenClassification",
    "BalanceClassification",
    "InitiativeClassification",
    "TrendClassification",
    "SessionClassification",
    # Classifiers
    "classify_day_type",
    "classify_open_type",
    "classify_balance",
    "classify_initiative_vs_responsive",
    "classify_trend",
    "classify_session",
    # Helpers
    "session_to_rule_names",
]
