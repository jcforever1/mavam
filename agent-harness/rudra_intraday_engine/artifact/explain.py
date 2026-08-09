"""Artifact explain — render a TradeSignal as a human narrative.

Given a content hash, this looks up the artifact and produces a
multi-line text explanation. Pure: no I/O beyond reading the file
we're explaining.
"""

from __future__ import annotations

from typing import Optional

from ..signal_types import TradeSignal
from .store import read_trade_signal


def explain_signal(sha256: str) -> Optional[str]:
    """Return a human-readable explanation of a TradeSignal, or None if not found."""
    ts = read_trade_signal(sha256)
    if ts is None:
        return None
    return _render(ts)


def _render(ts: TradeSignal) -> str:
    """Render a TradeSignal as text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"TradeSignal — {ts.action.value} {ts.symbol}")
    lines.append("=" * 72)
    lines.append("")

    # Header
    lines.append(f"  action:           {ts.action.value}")
    lines.append(f"  is_decide_no:     {ts.is_decide_no}")
    if ts.is_decide_no and ts.decide_no_reasons:
        lines.append(f"  decide_no_reasons:")
        for r in ts.decide_no_reasons:
            lines.append(f"    - {r}")
    lines.append("")

    # Trade parameters
    if ts.action.value in ("BUY", "SELL"):
        lines.append("Trade parameters:")
        lines.append(f"  symbol:           {ts.symbol}")
        lines.append(f"  qty:              {ts.qty}")
        lines.append(f"  order_type:       {ts.order_type}")
        if ts.entry_price is not None:
            lines.append(f"  entry_price:      {ts.entry_price:.2f}")
        if ts.stop_loss is not None:
            lines.append(f"  stop_loss:        {ts.stop_loss:.2f}")
        if ts.take_profit is not None:
            lines.append(f"  take_profit:      {ts.take_profit:.2f}")
        lines.append("")

    # Confidence + reasoning
    lines.append(f"  confidence:       {ts.confidence:.2f}")
    lines.append(f"  reason:           {ts.reason}")
    lines.append("")

    # Provenance
    lines.append("Provenance:")
    if ts.book_signal_ref:
        lines.append(f"  book_signal_ref:  {ts.book_signal_ref[:16]}...")
    if ts.kronos_signal_ref:
        lines.append(f"  kronos_signal_ref:{ts.kronos_signal_ref[:16]}...")
    if ts.adjudicator_version:
        lines.append(f"  adjudicator_ver:  {ts.adjudicator_version}")
    if ts.book_engine_version:
        lines.append(f"  book_engine_ver:  {ts.book_engine_version}")
    if ts.predictor_version:
        lines.append(f"  predictor_ver:    {ts.predictor_version}")
    if ts.market_state_hash and ts.market_state_hash.value:
        msh = ts.market_state_hash
        lines.append(f"  market_state:     {msh.algorithm}:{msh.value[:16]}...")
        lines.append(f"                    kind={msh.data_kind} ref={msh.data_ref}")
    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


__all__ = ["explain_signal"]
