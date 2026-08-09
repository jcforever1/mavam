"""Realized P&L from a paper-trade signal log.

A BUY signal at entry_price P with stop S and target T is closed at the
first subsequent bar where:
  - low <= stop (closed at stop_loss)   OR
  - high >= target (closed at take_profit)   OR
  - end-of-window (closed at the last bar's close)

A SELL signal is the symmetric short version: stops above entry, targets
below. We only handle long-side in v1; SELL signals are recorded but not
PnL-tracked (HOLD is also just recorded).

This is honest simulation: stop / target use bar high/low, not just close,
so intraday excursions are honored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..core.profile import Bar
from .signal_log import PaperLogRecord


@dataclass(frozen=True)
class ClosedTrade:
    record: PaperLogRecord
    exit_price: float
    exit_reason: str  # "stop" | "target" | "eod"
    pnl: float         # exit - entry for long
    bars_held: int


@dataclass
class RealizedPnL:
    ticker: str
    closed: List[ClosedTrade] = field(default_factory=list)
    skipped: int = 0   # signals with no stop / no target / SELL

    @property
    def n_trades(self) -> int:
        return len(self.closed)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.closed)

    @property
    def win_rate(self) -> float:
        if not self.closed:
            return 0.0
        return sum(1 for t in self.closed if t.pnl > 0) / len(self.closed)

    @property
    def avg_pnl(self) -> float:
        if not self.closed:
            return 0.0
        return self.total_pnl / len(self.closed)


def _close_long(record: PaperLogRecord, subsequent: List[Bar]) -> Optional[ClosedTrade]:
    """Walk forward through `subsequent` bars until the trade closes.

    subsequent MUST be sorted by timestamp and start AFTER record.ts_unix.
    """
    if record.action != "BUY":
        return None
    if record.stop_loss is None or record.take_profit is None:
        return None

    entry = record.entry_price
    stop = record.stop_loss
    target = record.take_profit
    if stop >= entry or target <= entry:
        # malformed record; refuse to PnL it
        return None

    for i, bar in enumerate(subsequent):
        if bar.low <= stop:
            pnl = stop - entry
            return ClosedTrade(record=record, exit_price=stop, exit_reason="stop", pnl=pnl, bars_held=i + 1)
        if bar.high >= target:
            pnl = target - entry
            return ClosedTrade(record=record, exit_price=target, exit_reason="target", pnl=pnl, bars_held=i + 1)
    # EOD exit at last close
    if subsequent:
        last = subsequent[-1]
        pnl = last.close - entry
        return ClosedTrade(record=record, exit_price=last.close, exit_reason="eod", pnl=pnl, bars_held=len(subsequent))
    return None


def compute_realized_pnl(
    records: List[PaperLogRecord],
    subsequent_by_ticker: dict,
    *,
    max_hold_bars: int = 200,
) -> RealizedPnL:
    """For each BUY record, find the matching trade close.

    subsequent_by_ticker: {ticker_upper: [Bar, ...]} where the bars are
    the *post-signal* OHLCV, sorted by time. Bars are scanned until
    stop / target hit OR `max_hold_bars` elapses, whichever comes first.
    """
    if not records:
        return RealizedPnL(ticker="")

    ticker = records[0].ticker.upper()
    out = RealizedPnL(ticker=ticker)
    for rec in records:
        if rec.ticker.upper() != ticker:
            continue
        bars = subsequent_by_ticker.get(ticker, [])
        # truncate to max_hold_bars
        bars = bars[:max_hold_bars]
        ct = _close_long(rec, bars)
        if ct is None:
            out.skipped += 1
        else:
            out.closed.append(ct)
    return out


__all__ = [
    "ClosedTrade",
    "RealizedPnL",
    "compute_realized_pnl",
]
