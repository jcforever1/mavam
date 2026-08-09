"""Branch A — The signal is the artifact.

The typed signal dataclasses that flow through the engine. The book
rules emit a BookSignal. The optional Kronos predictor emits a
KronosSignal. The Adjudicator merges them into a TradeSignal.

Every signal is:
- Frozen (immutable after creation)
- JSON-serializable (no Python objects that don't round-trip)
- Hashable (canonical-bytes SHA-256 via the artifact layer)
- Provenanced (carries version + source metadata)

The signal is the API. The bot consumes TradeSignal, the explain
command renders TradeSignal, the verify command regenerates it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional


# ── Action enum (BUY | SELL | HOLD | EXIT | REDUCE) ──────────────────────

class Action(str, Enum):
    """The trade action a signal emits."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"
    REDUCE = "REDUCE"


# ── Reason codes ───────────────────────────────────────────────────────

REJECTION_REASONS = frozenset({
    "no_setup_present",           # book rules didn't fire on this data
    "rules_disagree",             # multiple rules fired, conflicting actions
    "volatility_out_of_bounds",   # vol too high or too low
    "regime_uncertain",           # mixed signals, no clear regime
    "low_confidence",             # confidence below floor
    "data_stale",                 # data is older than stale_after
    "data_insufficient",          # not enough bars to compute features
    "spread_too_wide",            # bid-ask spread exceeds threshold
    "drawdown_limit_hit",         # daily drawdown already at limit
    "adjudicator_validation_failed",  # [inputs.*] required-rule violated
})

DECIDE_NO_REASONS = frozenset(REJECTION_REASONS | {
    "out_of_session",             # market closed or pre-open
    "kronos_unavailable",         # predictor.enabled but model load failed
    "kronos_low_confidence",      # kronos.confidence < min_confidence
    "kronos_disagrees",           # book and kronos predictions disagree
    "book_blocked_rule",          # a [inputs.book].blocked_rules matched
    "kronos_version_not_allowed", # model_version not in allowed_model_versions
})


# ── Rule trace (every rule that fired or was rejected) ────────────────

@dataclass(frozen=True)
class RuleTrace:
    """A single rule's evaluation against a market context."""

    rule_id: str
    rule_version: str
    fired: bool
    confidence: float  # 0.0-1.0
    reason_code: Optional[str] = None  # populated if fired=False
    reason_detail: Optional[str] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"RuleTrace.confidence must be in [0, 1], got {self.confidence}"
            )


# ── Counterfactual (the top-N "what if" alternatives) ──────────────────

@dataclass(frozen=True)
class Counterfactual:
    """A near-miss alternative: the smallest input delta that would
    have flipped the decision, and the resulting alternative action.
    """

    input_name: str   # e.g., "trend", "vol_bucket", "news_polarity"
    actual_value: str
    counterfactual_value: str
    alternative_action: Action
    alternative_confidence: float
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.alternative_confidence <= 1.0:
            raise ValueError(
                f"Counterfactual.alternative_confidence must be in [0, 1], "
                f"got {self.alternative_confidence}"
            )


# ── Market-state hash (fingerprint of the input that produced this signal)

@dataclass(frozen=True)
class MarketStateHash:
    """SHA-256 of the canonical input data that produced this signal.

    Lets `rudra-intraday verify <hash>` regenerate the signal from
    the recorded state and assert equality.
    """

    algorithm: Literal["sha256"] = "sha256"
    value: str = ""
    data_kind: str = ""  # "csv", "ticker", "fixture"
    data_ref: str = ""   # path or symbol or fixture name

    def compute(self, payload: bytes | str) -> str:
        """Compute the SHA-256 over a payload. Returns the hex digest."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


# ── Version tags ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VersionTag:
    """A semantic version + a content hash for one component."""

    semver: str
    content_sha256: str

    def __str__(self) -> str:
        return f"{self.semver}+{self.content_sha256[:8]}"


# ── BookSignal (the book engine's output) ─────────────────────────────

@dataclass(frozen=True)
class BookSignal:
    """The book engine's evaluation of the market data.

    Carries which rules fired, which were rejected, the consensus
    action (if any), and the version of the rule set.
    """

    action: Action  # HOLD if no rules fired or rules disagreed
    confidence: float  # 0.0-1.0
    rule_version: str
    fired_rules: list[RuleTrace] = field(default_factory=list)
    rejected_rules: list[RuleTrace] = field(default_factory=list)
    rule_set_sha256: str = ""  # hash of the encoded rule definitions
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"BookSignal.confidence must be in [0, 1], got {self.confidence}"
            )


# ── KronosSignal (the optional predictor's output) ────────────────────

@dataclass(frozen=True)
class KronosSignal:
    """The Kronos ML predictor's evaluation of the market data.

    Optional in v1 — emitted only when config.predictor.enabled=True.
    """

    prediction: Literal["UP", "DOWN", "FLAT"]
    confidence: float  # 0.0-1.0
    model_version: str
    features_snapshot_sha256: str = ""  # hash of the input features
    horizon_bars: int = 1  # prediction horizon in bars
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"KronosSignal.confidence must be in [0, 1], got {self.confidence}"
            )
        if self.horizon_bars < 1:
            raise ValueError(
                f"KronosSignal.horizon_bars must be >= 1, got {self.horizon_bars}"
            )


# ── TradeSignal (the Adjudicator's output) ────────────────────────────

@dataclass(frozen=True)
class TradeSignal:
    """The final signal a trading bot consumes. This is the API.

    The bot reads this and executes via the broker. It has zero
    knowledge of the book or Kronos.
    """

    # Whether to act
    action: Action
    is_decide_no: bool  # True if action is HOLD and we should NOT trade
    decide_no_reasons: list[str] = field(default_factory=list)

    # What to trade
    symbol: str = ""
    qty: int = 0
    order_type: Literal["market", "limit"] = "limit"
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    # Confidence + reasoning
    confidence: float = 0.0
    reason: str = ""  # e.g., "book-buy-kronos-confirms"

    # Provenance
    book_signal_ref: str = ""  # canonical hash of the BookSignal
    kronos_signal_ref: str = ""  # canonical hash of the KronosSignal (or empty)
    adjudicator_version: str = ""  # semver of the adjudicator TOML
    adjudicator_commit: str = ""  # git commit of the adjudicator TOML
    book_engine_version: str = ""  # semver of the book engine
    predictor_version: str = ""  # semver of the Kronos predictor (or empty)

    # Market state at decision time
    market_state_hash: Optional[MarketStateHash] = None

    # The top-N counterfactual alternatives
    counterfactuals: list[Counterfactual] = field(default_factory=list)

    # Timestamp (sourced from as_of_unix if set, else wall clock at emit)
    timestamp_unix: int = 0
    timestamp_iso: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"TradeSignal.confidence must be in [0, 1], got {self.confidence}"
            )
        if self.qty < 0:
            raise ValueError(f"TradeSignal.qty must be >= 0, got {self.qty}")

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict. Datetime → ISO, enum → string."""
        d = asdict(self)
        if self.market_state_hash is not None:
            d["market_state_hash"] = asdict(self.market_state_hash)
        for cf in d.get("counterfactuals", []):
            if "alternative_action" in cf:
                cf["alternative_action"] = (
                    cf["alternative_action"].value
                    if hasattr(cf["alternative_action"], "value")
                    else cf["alternative_action"]
                )
        if isinstance(d.get("action"), Action):
            d["action"] = self.action.value
        return d


# ── Canonical hashing (used by the artifact layer) ─────────────────────

def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON for hashing. Sort keys, no whitespace, UTF-8."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_hash(obj: Any) -> str:
    """SHA-256 of canonical JSON. Stable across runs."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def now_iso(unix_ts: Optional[int] = None) -> str:
    """Return an ISO 8601 timestamp in UTC."""
    if unix_ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
