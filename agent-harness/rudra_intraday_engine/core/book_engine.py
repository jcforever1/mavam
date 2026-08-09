"""Book engine orchestrator — turns raw bars into a typed BookSignal.

This is the glue between the four book modules (profile, classify,
orderflow, predictor) and the Adjudicator. It runs the full book
pipeline on a bar sequence and emits a BookSignal with the full
rule trace — which rules fired, with what confidence, and why.

The Adjudicator never sees bars. It sees a BookSignal. The book
engine never sees a TOML. It sees bars.

Module is PURE: no I/O, no `time`, no `random`, no network. The
single entry point is `evaluate_session(bars, ...)`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional, Sequence

from ..signal_types import Action, BookSignal, RuleTrace
from .classify import (
    SessionClassification,
    classify_session,
    session_to_rule_names,
)
from .orderflow import CumulativeDelta, Divergence, orderflow_signal
from .predictor import predict_kronos
from .profile import Bar, DEFAULT_TICK_SIZE


# Engine version — bump on rule changes
BOOK_ENGINE_VERSION = "0.1.0"
RULE_SET_LABEL = "mind-markets-and-money-v0.1.0"


# ── Heuristic action derivation ─────────────────────────────────────


def _derive_action(
    sc: SessionClassification,
    divergence: Divergence,
) -> tuple[Action, float, str]:
    """Map (classification, divergence) to a (action, confidence, rationale).

    This is the book's "first-cut" decision. The Adjudicator can
    override via its merge rules. If the book engine is run in
    isolation (no Adjudicator), this is the final answer.
    """
    day = sc.day.day_type
    initiative = sc.initiative.activity
    balance = sc.balance.state

    # Trend up + initiative buying / imbalanced up → BUY
    if day.value in ("trend_up", "p_day") and (
        initiative.value in ("initiative_buying", "responsive_buying")
        or balance.value == "imbalanced_up"
    ):
        confidence = 0.75 if day.value == "trend_up" else 0.65
        return Action.BUY, confidence, f"book {day.value} supports BUY"

    # Trend down + initiative selling / imbalanced down → SELL
    if day.value in ("trend_down", "b_day") and (
        initiative.value in ("initiative_selling", "responsive_selling")
        or balance.value == "imbalanced_down"
    ):
        confidence = 0.75 if day.value == "trend_down" else 0.65
        return Action.SELL, confidence, f"book {day.value} supports SELL"

    # Bearish divergence strengthens a HOLD
    if divergence.divergence_type.value == "bearish":
        return Action.HOLD, 0.55, (
            f"bearish divergence (price {divergence.price_at_first:.2f} -> "
            f"{divergence.price_at_second:.2f}, delta "
            f"{divergence.delta_at_first:.0f} -> {divergence.delta_at_second:.0f}) "
            f"argues against buying"
        )

    # Bullish divergence strengthens a BUY signal in a neutral context
    if divergence.divergence_type.value == "bullish" and day.value in (
        "normal_day", "neutral", "p_day"
    ):
        return Action.BUY, 0.60, (
            f"bullish divergence (price {divergence.price_at_first:.2f} -> "
            f"{divergence.price_at_second:.2f}, delta "
            f"{divergence.delta_at_first:.0f} -> {divergence.delta_at_second:.0f}) "
            f"in {day.value} context"
        )

    # Default — no clear signal
    return Action.HOLD, 0.50, f"book {day.value} in {balance.value} — no clear bias"


# ── Rule trace construction ──────────────────────────────────────────


def _build_rule_traces(
    sc: SessionClassification,
    divergence: Divergence,
    action: Action,
) -> list[RuleTrace]:
    """Convert the classifications to a list of RuleTrace records.

    Each fired rule has a stable rule_id (Adjudicator-matchable),
    a confidence (0-1), and a reason_detail (human-readable).
    """
    traces: list[RuleTrace] = []

    # Day-type rule
    traces.append(RuleTrace(
        rule_id=f"day_type={sc.day.day_type.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=sc.day.confidence > 0.0,
        confidence=sc.day.confidence,
        reason_detail=sc.day.rationale,
    ))

    # Open-type rule
    traces.append(RuleTrace(
        rule_id=f"open_type={sc.open.open_type.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=sc.open.confidence > 0.0,
        confidence=sc.open.confidence,
        reason_detail=sc.open.rationale,
    ))

    # Balance rule
    traces.append(RuleTrace(
        rule_id=f"balance={sc.balance.state.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=sc.balance.confidence > 0.0,
        confidence=sc.balance.confidence,
        reason_detail=sc.balance.rationale,
    ))

    # Initiative rule
    traces.append(RuleTrace(
        rule_id=f"initiative={sc.initiative.activity.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=sc.initiative.confidence > 0.0,
        confidence=sc.initiative.confidence,
        reason_detail=sc.initiative.rationale,
    ))

    # Trend rule
    traces.append(RuleTrace(
        rule_id=f"trend={sc.trend.state.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=sc.trend.confidence > 0.0,
        confidence=sc.trend.confidence,
        reason_detail=sc.trend.rationale,
    ))

    # Divergence rule
    div_fired = divergence.divergence_type.value != "none"
    traces.append(RuleTrace(
        rule_id=f"divergence={divergence.divergence_type.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=div_fired,
        confidence=divergence.confidence,
        reason_detail=divergence.rationale,
    ))

    # Action summary rule (so the Adjudicator can match on it)
    traces.append(RuleTrace(
        rule_id=f"action={action.value}",
        rule_version=BOOK_ENGINE_VERSION,
        fired=True,
        confidence=1.0,
        reason_detail="derived from combination of above rules",
    ))

    return traces


# ── Rule-set hash (for provenance) ──────────────────────────────────


def _compute_rule_set_sha256(
    sc: SessionClassification,
    divergence: Divergence,
) -> str:
    """Hash a canonical encoding of the rule set used.

    The hash is a content identifier: if the engine's rules change,
    the hash changes, and any artifact that cited the old hash is
    no longer verifiable. This is the durability primitive.
    """
    canonical = {
        "rule_set_label": RULE_SET_LABEL,
        "engine_version": BOOK_ENGINE_VERSION,
        "day_types": [d.value for d in sc.day.day_type.__class__],
        "classification": session_to_rule_names(sc),
        "divergence": divergence.divergence_type.value,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ── Main entry ──────────────────────────────────────────────────────


def evaluate_session(
    bars: list[Bar],
    tick_size: float = DEFAULT_TICK_SIZE,
    include_kronos: bool = True,
) -> tuple[BookSignal, Optional[object], SessionClassification, CumulativeDelta, Divergence]:
    """Run the full book pipeline on a bar sequence.

    Returns:
        (BookSignal, KronosSignal or None, SessionClassification,
         CumulativeDelta, Divergence)

    The BookSignal is what the Adjudicator consumes. The other
    artifacts are returned for the explain/verify paths.
    """
    if not bars:
        raise ValueError("bars must be non-empty")

    # Profile + classify
    sc = classify_session(bars, tick_size=tick_size)

    # Order flow
    cd, divergence = orderflow_signal(bars)

    # Optional Kronos (returns None when not installed)
    kronos_signal = predict_kronos(bars) if include_kronos else None

    # Derive action + confidence
    action, confidence, rationale = _derive_action(sc, divergence)

    # Build the rule trace
    traces = _build_rule_traces(sc, divergence, action)
    fired = [t for t in traces if t.fired]
    rejected = [t for t in traces if not t.fired]

    # Rule-set hash
    rule_set_sha = _compute_rule_set_sha256(sc, divergence)

    # BookSignal
    book_signal = BookSignal(
        action=action,
        confidence=confidence,
        rule_version=BOOK_ENGINE_VERSION,
        fired_rules=fired,
        rejected_rules=rejected,
        rule_set_sha256=rule_set_sha,
        notes=rationale,
    )

    return book_signal, kronos_signal, sc, cd, divergence


__all__ = [
    "BOOK_ENGINE_VERSION",
    "RULE_SET_LABEL",
    "evaluate_session",
    "_derive_action",
    "_build_rule_traces",
]
