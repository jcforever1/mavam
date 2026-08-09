"""Historical paper-trade replay.

Replays the strategy over a historical period, then computes the
realized P&L that would have occurred if the signals had been taken.
This is the bridge between backtest and paper-trade: same signal logic,
different settlement model.

For each (day, signal) the replay:
  1. Loads bars up to that day's close.
  2. Runs the book engine + Adjudicator to get a signal.
  3. Records the signal in-memory.
  4. Looks up subsequent bars to settle the trade.

Used by:
  - The daily cron to test "if I had taken yesterday's signal, P&L"
  - The 30-day paper-trade demo in this build
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..adjudicator import load_adjudicator, merge
from ..config_schema import load_config_from_argv
from ..core.book_engine import evaluate_session
from ..core.profile import Bar
from ..data import fetch_yfinance_bars, YFinanceConfig
from ..signal_types import TradeSignal
from .realized import RealizedPnL, compute_realized_pnl
from .signal_log import PaperLogRecord


def _bars_for_day(bars: List[Bar], day_unix_start: int, day_unix_end: int) -> List[Bar]:
    return [b for b in bars if day_unix_start <= b.timestamp_unix < day_unix_end]


def replay_historical_signals(
    ticker: str,
    *,
    config_path: str,
    period: str = "60d",
    interval: str = "5m",
    interval_seconds: int = 300,
    max_hold_bars: int = 200,
) -> Tuple[List[PaperLogRecord], RealizedPnL]:
    """Replay a ticker over the given period. Returns (records, pnl).

    The config_path may be either:
      - A full config TOML (with [data] and [adjudicator]) — its
        adjudicator.file is used.
      - A pure strategy TOML (just [adjudicator] with merge_rules
        and risk) — the strategy itself becomes the adjudicator.
    """
    import tomllib
    from pathlib import Path as _Path

    cfg_path = _Path(config_path)
    with cfg_path.open("rb") as f:
        raw = tomllib.load(f)

    if "adjudicator" in raw and "merge_rules" in raw.get("adjudicator", {}):
        # Pure strategy file: the strategy IS the adjudicator.
        # We synthesize an AdjudicatorRef pointing back to the file.
        from ..adjudicator import load_adjudicator

        # Update the in-memory config so the adjudicator has the
        # correct file path. We do this by writing a temporary file
        # with [adjudicator] pointing to the original.
        import tempfile
        import tomlkit

        with cfg_path.open() as f:
            doc = tomlkit.load(f)
        # Set the file path explicitly so load_adjudicator uses the right one
        doc["adjudicator"]["file"] = str(cfg_path.resolve())
        # Add a minimal [data] section so config_schema accepts it
        if "data" not in doc:
            data = tomlkit.table()
            data.add("ticker", ticker.upper())
            doc["data"] = data

        with tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False
        ) as tmp:
            tomlkit.dump(doc, tmp)
            tmp_path = tmp.name
        try:
            config = load_config_from_argv([tmp_path])
            adj = load_adjudicator(config.adjudicator.file)
        finally:
            _Path(tmp_path).unlink(missing_ok=True)
    else:
        # Full config file
        config = load_config_from_argv([config_path])
        adj = load_adjudicator(config.adjudicator.file)

    yf_config = YFinanceConfig(
        ticker=ticker, period=period, interval=interval,
        stale_after_seconds=0,
    )
    bars = fetch_yfinance_bars(yf_config)
    if bars is None or not bars:
        return [], RealizedPnL(ticker=ticker.upper())

    # Group bars by trading day. Use UTC midnight to UTC midnight,
    # which is the closest to ET without timezone awareness in profile.
    by_day: dict = {}
    for b in bars:
        day = b.timestamp_unix - (b.timestamp_unix % 86400)
        by_day.setdefault(day, []).append(b)

    days = sorted(by_day.keys())
    records: List[PaperLogRecord] = []
    for i, day_start in enumerate(days):
        day_bars = by_day[day_start]
        if not day_bars:
            continue
        book, kronos, sc, cd, div = evaluate_session(day_bars, include_kronos=False)
        entry_price = day_bars[-1].close
        trade = merge(
            adj, book, kronos,
            symbol=ticker.upper(), entry_price=entry_price,
        )
        rec = PaperLogRecord(
            ts_unix=day_bars[-1].timestamp_unix,
            ts_iso=trade.timestamp_iso,
            ticker=ticker.upper(),
            action=trade.action.value,
            entry_price=entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            confidence=trade.confidence,
            reason=trade.reason,
            config_sha=config.config_sha256,
            is_decide_no=trade.is_decide_no,
            decide_no_reasons=list(trade.decide_no_reasons),
            source="replay",
        )
        records.append(rec)

    # Build subsequent-bars lookup: for each record, the bars AFTER its ts_unix
    subsequent_by_ticker: dict = {ticker.upper(): []}
    if records:
        first_ts = records[0].ts_unix
        subsequent_by_ticker[ticker.upper()] = [
            b for b in bars if b.timestamp_unix > first_ts
        ]

    pnl = compute_realized_pnl(
        records, subsequent_by_ticker, max_hold_bars=max_hold_bars
    )
    return records, pnl


__all__ = [
    "replay_historical_signals",
]
