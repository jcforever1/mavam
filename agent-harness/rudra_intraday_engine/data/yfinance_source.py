"""yfinance data source — HTTP-only, no browser.

This is the "actually works" pinchtab-compatible path. yfinance
returns OHLCV bars for any ticker Yahoo Finance covers (SPY, QQQ,
most US equities, many ETFs, some internationals) without needing
a headless browser. The engine sees the same `list[Bar]` regardless
of whether the bars came from yfinance, the pinchtab playwright
adapter, or a CSV file.

The integration is:
  1. Stateless — no persistent connections, no session state.
  2. Pull-based — every fetch is a fresh HTTP request.
  3. Bounded — an explicit `stale_after_seconds` cuts off data
     older than the freshness window.

Requires:
    pip install rudra-intraday-engine[yfinance]
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core.profile import Bar


DEFAULT_STALE_AFTER_SECONDS = 300

# Retry policy for transient Yahoo Finance failures (429 rate limits,
# empty responses, brief network blips). 3 attempts, backoff 1s/2s.
YF_FETCH_RETRIES = 3
YF_FETCH_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class YFinanceConfig:
    """A yfinance data source."""

    ticker: str
    period: str = "5d"        # e.g. "1d", "5d", "1mo"
    interval: str = "5m"      # e.g. "1m", "5m", "15m", "1h", "1d"
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS


def yfinance_available() -> bool:
    """Return True if yfinance is importable."""
    try:
        import yfinance  # noqa: F401
        return True
    except ImportError:
        return False


def fetch_yfinance_bars(
    config: YFinanceConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Fetch OHLCV bars from Yahoo Finance.

    Returns None if:
      - yfinance is not installed
      - the network call fails
      - the ticker is unknown
      - no data is returned

    On success, returns a sorted list of Bar objects. Any bar
    with timestamp_unix < (now - stale_after_seconds) is filtered
    out.
    """
    try:
        import pandas as pd  # noqa: F401
        import yfinance as yf
    except ImportError:
        return None

    as_of = as_of_unix if as_of_unix is not None else int(time.time())
    # Staleness filter: only apply if explicitly positive. A non-positive
    # value (including 0 or the default for historical pulls) means
    # "return all bars from the period, no freshness check".
    apply_staleness = config.stale_after_seconds > 0
    cutoff = as_of - config.stale_after_seconds if apply_staleness else None

    # Transient fetch resilience: Yahoo sometimes hiccups (429/empty
    # responses). Retry a few times with short backoff before giving up —
    # a 16:05 daily paper-log run failing on one bad call silently loses
    # the signal for the day (observed 2026-08-13: SPY "possibly delisted"
    # with zero bars; retry succeeds seconds later).
    last_hist = None
    for attempt in range(YF_FETCH_RETRIES):
        try:
            t = yf.Ticker(config.ticker)
            hist = t.history(period=config.period, interval=config.interval)
            if hist is not None and not hist.empty:
                last_hist = hist
                break
            last_hist = None
        except Exception:
            last_hist = None
        if attempt + 1 < YF_FETCH_RETRIES:
            time.sleep(YF_FETCH_BACKOFF_SECONDS * (attempt + 1))

    if last_hist is None:
        return None

    hist = last_hist

    raw_bars: List[Bar] = []
    for idx, row in hist.iterrows():
        try:
            # yfinance returns a DatetimeIndex (tz-aware for US equities).
            # Convert to unix seconds.
            ts = int(idx.timestamp())
            if cutoff is not None and ts < cutoff:
                continue
            # yfinance columns are title-case: Open, High, Low, Close, Volume
            raw_bars.append(Bar(
                timestamp_unix=ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0) or 0.0),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue

    raw_bars.sort(key=lambda b: b.timestamp_unix)
    if not raw_bars:
        return None
    return raw_bars


__all__ = [
    "YFinanceConfig",
    "DEFAULT_STALE_AFTER_SECONDS",
    "fetch_yfinance_bars",
    "yfinance_available",
]
