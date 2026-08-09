"""Backtest runner — simulate trades through the full mavam pipeline.

The runner pulls 5-min bars from yfinance, groups them by trading
day, and at each bar evaluates the book engine + adjudicator as if
the engine were running live at that bar's close. If the engine
emits a BUY or SELL signal, the runner opens a position at the
NEXT bar's open, holds until take-profit, stop-loss, end-of-day,
or a signal flip — whichever comes first.

This is an honest intraday backtest. No look-ahead: the engine only
sees bars up to the current bar's close. Position opens at the next
bar's open (not the signal bar's close) to model real execution
delay.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from ..adjudicator import Adjudicator, load_adjudicator, merge
from ..core.book_engine import evaluate_session
from ..core.predictor import predict_kronos
from ..core.profile import Bar
from ..data import YFinanceConfig, fetch_yfinance_bars, yfinance_available
from .metrics import BacktestMetrics, TradeResult, compute_metrics


def _bars_by_day(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    """Group bars by trading day (YYYY-MM-DD)."""
    by_day: dict[str, list[Bar]] = defaultdict(list)
    for b in bars:
        dt = datetime.fromtimestamp(b.timestamp_unix, tz=timezone.utc)
        key = dt.strftime("%Y-%m-%d")
        by_day[key].append(b)
    # Sort each day's bars
    for k in by_day:
        by_day[k].sort(key=lambda x: x.timestamp_unix)
    return by_day


def _run_day(
    day_bars: Sequence[Bar],
    day: str,
    adj: Adjudicator,
    symbol: str,
    starting_bar_idx: int,
) -> list[TradeResult]:
    """Simulate one trading day. Returns the trades taken on that day.

    For each bar i in [1, len-1]:
      - Run the book engine on bars[0:i+1]
      - If signal is BUY/SELL and no position open, open at bars[i+1].open
      - If position open, check TP/SL against bars[i+1] (high/low)
      - If no exit by EOD, close at last bar's close
    """
    trades: list[TradeResult] = []
    if len(day_bars) < 4:
        return trades  # not enough bars to make a decision

    # State for the day
    open_action: Optional[str] = None
    open_qty: int = 0
    open_entry_price: float = 0.0
    open_stop: Optional[float] = None
    open_target: Optional[float] = None
    open_entry_idx: int = -1
    open_exit_price: float = 0.0
    open_exit_reason: str = ""

    n = len(day_bars)
    # Walk from bar 1 onwards (need at least 1 prior bar for context)
    for i in range(1, n - 1):
        current = day_bars[i]
        next_bar = day_bars[i + 1]

        # 1) If position open, check for TP / SL exit on next bar
        if open_action is not None:
            exit_price: Optional[float] = None
            exit_reason: str = ""
            if open_action == "BUY":
                # Long: SL hit if next bar low <= stop, TP hit if high >= target
                if open_stop is not None and next_bar.low <= open_stop:
                    exit_price = open_stop
                    exit_reason = "stop_loss"
                elif open_target is not None and next_bar.high >= open_target:
                    exit_price = open_target
                    exit_reason = "take_profit"
            else:  # SELL
                if open_stop is not None and next_bar.high >= open_stop:
                    exit_price = open_stop
                    exit_reason = "stop_loss"
                elif open_target is not None and next_bar.low <= open_target:
                    exit_price = open_target
                    exit_reason = "take_profit"

            if exit_price is not None:
                # Close the trade
                if open_action == "BUY":
                    pnl = (exit_price - open_entry_price) * open_qty
                else:
                    pnl = (open_entry_price - exit_price) * open_qty
                trades.append(TradeResult(
                    symbol=symbol,
                    action=open_action,
                    entry_price=open_entry_price,
                    exit_price=exit_price,
                    qty=open_qty,
                    pnl=pnl,
                    exit_reason=exit_reason,
                    entry_bar_idx=starting_bar_idx + open_entry_idx,
                    exit_bar_idx=starting_bar_idx + i + 1,
                    date=day,
                ))
                # Reset position
                open_action = None
                open_qty = 0
                continue

        # 2) Run the book engine on the visible bars
        visible = list(day_bars[: i + 1])
        book, kronos, sc, cd, div = evaluate_session(visible, include_kronos=False)
        trade_signal = merge(
            adj, book, kronos,
            symbol=symbol, entry_price=current.close,
        )

        # 3) If a position is open and the engine says to flip/exit
        if open_action is not None:
            if trade_signal.action.value == "EXIT":
                # Close at next bar's open
                exit_price = next_bar.open
                if open_action == "BUY":
                    pnl = (exit_price - open_entry_price) * open_qty
                else:
                    pnl = (open_entry_price - exit_price) * open_qty
                trades.append(TradeResult(
                    symbol=symbol,
                    action=open_action,
                    entry_price=open_entry_price,
                    exit_price=exit_price,
                    qty=open_qty,
                    pnl=pnl,
                    exit_reason="signal_exit",
                    entry_bar_idx=starting_bar_idx + open_entry_idx,
                    exit_bar_idx=starting_bar_idx + i + 1,
                    date=day,
                ))
                open_action = None
                open_qty = 0

        # 4) If no position, open one on the new signal
        if open_action is None and trade_signal.action.value in ("BUY", "SELL") and trade_signal.qty > 0:
            open_action = trade_signal.action.value
            open_qty = trade_signal.qty
            open_entry_price = next_bar.open
            open_stop = trade_signal.stop_loss
            open_target = trade_signal.take_profit
            open_entry_idx = i + 1  # the bar where the entry fill happens

    # End of day: if position still open, close at last bar's close
    if open_action is not None:
        last_close = day_bars[-1].close
        if open_action == "BUY":
            pnl = (last_close - open_entry_price) * open_qty
        else:
            pnl = (open_entry_price - last_close) * open_qty
        trades.append(TradeResult(
            symbol=symbol,
            action=open_action,
            entry_price=open_entry_price,
            exit_price=last_close,
            qty=open_qty,
            pnl=pnl,
            exit_reason="eod",
            entry_bar_idx=starting_bar_idx + open_entry_idx,
            exit_bar_idx=starting_bar_idx + n - 1,
            date=day,
        ))

    return trades


def run_backtest(
    adj: Adjudicator,
    *,
    ticker: str = "SPY",
    period: str = "60d",
    interval: str = "5m",
    initial_equity: float = 100_000.0,
) -> tuple[list[TradeResult], BacktestMetrics]:
    """Run a full backtest. Returns (trades, metrics)."""
    if not yfinance_available():
        raise RuntimeError(
            "yfinance is not installed; install with: "
            "pip install rudra-intraday-engine[yfinance]"
        )
    config = YFinanceConfig(
        ticker=ticker, period=period, interval=interval,
        stale_after_seconds=0,  # no filter for backtest
    )
    bars = fetch_yfinance_bars(config)
    if bars is None:
        raise RuntimeError(f"yfinance returned no bars for {ticker}")

    by_day = _bars_by_day(bars)
    # Flatten bar index for entry/exit bar tracking
    all_days_sorted = sorted(by_day.keys())
    bar_offsets: dict[str, int] = {}
    offset = 0
    for d in all_days_sorted:
        bar_offsets[d] = offset
        offset += len(by_day[d])

    all_trades: list[TradeResult] = []
    for d in all_days_sorted:
        day_trades = _run_day(
            by_day[d], d, adj, ticker, bar_offsets[d],
        )
        all_trades.extend(day_trades)

    metrics = compute_metrics(all_trades, initial_equity=initial_equity)
    return all_trades, metrics


__all__ = ["run_backtest", "TradeResult", "BacktestMetrics"]
