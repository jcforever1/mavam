"""Adjudicator merger — (Adjudicator, BookSignal, KronosSignal?) -> TradeSignal.

The merger is the single place that converts a BookSignal + optional
KronosSignal into a TradeSignal. It applies the Adjudicator's
validation rules, evaluates the merge rules, and respects the risk
parameters. The merger never reads the data source or the bars
themselves — it's a pure function of (Adjudicator, signals).

Output is a TradeSignal with is_decide_no=True when the policy
refuses to act (so the bot can read decide_no_reasons).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Optional

from ..signal_types import (
    Action,
    BookSignal,
    KronosSignal,
    TradeSignal,
)
from .expression import ExpressionError, evaluate_when
from .schema import Adjudicator


def _canonical_signal_ref(signal: Any) -> str:
    """Return a content hash of the signal for provenance."""
    if signal is None:
        return ""
    if is_dataclass(signal):
        d = asdict(signal)
    elif hasattr(signal, "__dict__"):
        d = signal.__dict__
    else:
        return ""
    blob = json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _validate_book(
    adj: Adjudicator, book: Optional[BookSignal]
) -> list[str]:
    """Apply [inputs.book] validation. Returns a list of rejection reasons."""
    if not adj.book.required and book is None:
        return []
    if book is None:
        return ["book_required_but_missing"]

    reasons: list[str] = []
    fired_count = len([r for r in book.fired_rules if r.fired])
    if fired_count < adj.book.min_rules_fired:
        reasons.append(
            f"min_rules_fired: {fired_count} < {adj.book.min_rules_fired}"
        )

    fired_ids = {r.rule_id for r in book.fired_rules if r.fired}
    for req in adj.book.required_rules:
        if req not in fired_ids:
            reasons.append(f"required_rule_missing: {req}")

    for blocked in adj.book.blocked_rules:
        if blocked in fired_ids:
            reasons.append(f"blocked_rule_fired: {blocked}")

    return reasons


def _validate_kronos(
    adj: Adjudicator, kronos: Optional[KronosSignal]
) -> list[str]:
    """Apply [inputs.kronos] validation."""
    if not adj.kronos.required:
        if kronos is None:
            return []
        if kronos.confidence < adj.kronos.min_confidence:
            return ["kronos_low_confidence"]
        if (adj.kronos.allowed_model_versions
                and kronos.model_version not in adj.kronos.allowed_model_versions):
            return ["kronos_version_not_allowed"]
        return []

    # Kronos is required
    if kronos is None:
        return ["kronos_required_but_missing"]

    reasons: list[str] = []
    if kronos.confidence < adj.kronos.min_confidence:
        reasons.append(
            f"kronos_low_confidence: {kronos.confidence:.2f} < "
            f"{adj.kronos.min_confidence:.2f}"
        )
    if (adj.kronos.allowed_model_versions
            and kronos.model_version not in adj.kronos.allowed_model_versions):
        reasons.append(
            f"kronos_version_not_allowed: {kronos.model_version!r} not in "
            f"{list(adj.kronos.allowed_model_versions)}"
        )
    return reasons


def _evaluate_merge_rules(
    adj: Adjudicator,
    book: BookSignal,
    kronos: Optional[KronosSignal],
) -> Optional[tuple[str, float, str]]:
    """Walk [[merge.rules]] in order, return the first match."""
    ctx = {"book": book, "kronos": kronos}
    for rule in adj.merge_rules:
        try:
            matched = evaluate_when(rule.when, ctx)
        except ExpressionError:
            continue
        if matched:
            return (rule.emit, rule.size_multiplier, rule.reason)
    return None


def _fallback_action(
    adj: Adjudicator, book: Optional[BookSignal], kronos: Optional[KronosSignal]
) -> tuple[Action, str]:
    if book is None:
        return Action(adj.fallback.when_no_book), "book missing"
    if kronos is None and adj.kronos.required:
        return Action(adj.fallback.when_no_kronos), "kronos missing"
    return Action(adj.fallback.when_validation_fails), "no rule matched"


def merge(
    adj: Adjudicator,
    book: Optional[BookSignal],
    kronos: Optional[KronosSignal],
    *,
    symbol: str = "",
    entry_price: Optional[float] = None,
) -> TradeSignal:
    """Apply the Adjudicator's policy to produce a TradeSignal."""
    # 1. Validate inputs
    book_reasons = _validate_book(adj, book)
    kronos_reasons = _validate_kronos(adj, kronos)
    validation_reasons = book_reasons + kronos_reasons

    book_ref = _canonical_signal_ref(book)
    kronos_ref = _canonical_signal_ref(kronos)

    if validation_reasons:
        action, reason = _fallback_action(adj, book, kronos)
        return TradeSignal(
            action=action,
            is_decide_no=(action == Action.HOLD),
            decide_no_reasons=validation_reasons,
            symbol=symbol,
            qty=0,
            confidence=book.confidence if book else 0.0,
            reason=(
                f"validation_failed: {'; '.join(validation_reasons)}; "
                f"fallback={reason}"
            ),
            book_signal_ref=book_ref,
            kronos_signal_ref=kronos_ref,
            adjudicator_version=adj.version,
            book_engine_version=book.rule_version if book else "",
            predictor_version=kronos.model_version if kronos else "",
        )

    # 2. If book is None but not required
    if book is None:
        return TradeSignal(
            action=Action.HOLD,
            is_decide_no=True,
            decide_no_reasons=["book_missing_but_not_required"],
            symbol=symbol,
            qty=0,
            confidence=0.0,
            reason="no book signal and not required",
            book_signal_ref="",
            kronos_signal_ref=kronos_ref,
            adjudicator_version=adj.version,
            book_engine_version="",
            predictor_version=kronos.model_version if kronos else "",
        )

    # 3. Evaluate merge rules
    match = _evaluate_merge_rules(adj, book, kronos)
    if match is None:
        action, reason = _fallback_action(adj, book, kronos)
        return TradeSignal(
            action=action,
            is_decide_no=(action == Action.HOLD),
            decide_no_reasons=["no_merge_rule_matched"],
            symbol=symbol,
            qty=0,
            confidence=book.confidence,
            reason=f"no merge rule matched; fallback={reason}",
            book_signal_ref=book_ref,
            kronos_signal_ref=kronos_ref,
            adjudicator_version=adj.version,
            book_engine_version=book.rule_version,
            predictor_version=kronos.model_version if kronos else "",
        )

    emit, size_mult, rule_reason = match
    action = Action(emit)

    # 4. Apply risk parameters
    qty = 0
    if action in (Action.BUY, Action.SELL) and entry_price is not None:
        qty = max(1, int(round(adj.risk.max_position_pct * size_mult * 100)))

    stop_loss = None
    take_profit = None
    if entry_price is not None and action in (Action.BUY, Action.SELL):
        sign = 1 if action == Action.BUY else -1
        stop_loss = entry_price * (1 - sign * adj.risk.stop_loss_pct)
        take_profit = entry_price * (1 + sign * adj.risk.take_profit_pct)

    is_decide_no = (action == Action.HOLD)

    return TradeSignal(
        action=action,
        is_decide_no=is_decide_no,
        decide_no_reasons=(
            [] if not is_decide_no else ["hold_emitted_by_policy"]
        ),
        symbol=symbol,
        qty=qty,
        order_type="limit",
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=book.confidence,
        reason=rule_reason or f"merge_rule_matched_{action.value}",
        book_signal_ref=book_ref,
        kronos_signal_ref=kronos_ref,
        adjudicator_version=adj.version,
        book_engine_version=book.rule_version,
        predictor_version=kronos.model_version if kronos else "",
    )


__all__ = ["merge"]
