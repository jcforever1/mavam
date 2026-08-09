"""Paper-trade module — record live signals, compute realized P&L.

The paper-trade loop is:
  1. `rudra-intraday paper log <config>` runs the strategy on current
     data and appends a JSONL record to the daily log.
  2. `rudra-intraday paper report <ticker> --since 30d` reads the log
     and computes realized P&L against subsequent bar data.

A scheduled cron at market close (16:00 ET) runs (1) every weekday.
Step (2) gives the daily / weekly / 30-day forward P&L.

The log lives at $HOME/.local/state/mavam/paperlog/<date>.jsonl.
"""

from __future__ import annotations

from .signal_log import (
    DEFAULT_LOG_DIR,
    PaperLogRecord,
    append_record,
    read_records,
)
from .realized import (
    ClosedTrade,
    RealizedPnL,
    compute_realized_pnl,
)
from .replay import replay_historical_signals

__all__ = [
    "DEFAULT_LOG_DIR",
    "PaperLogRecord",
    "append_record",
    "read_records",
    "ClosedTrade",
    "RealizedPnL",
    "compute_realized_pnl",
    "replay_historical_signals",
]
