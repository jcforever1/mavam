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

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.profile import Bar


DEFAULT_STALE_AFTER_SECONDS = 300

# Retry policy for transient Yahoo Finance failures (429 rate limits,
# empty responses, brief network blips — e.g. a scheduled fire landing
# right after the Mac wakes from sleep).
#
# Defaults: 3 attempts, backoff 1s/2s (exponential, capped).
# The launchd scheduler overrides via environment (see the plist):
#   YF_FETCH_RETRIES            total attempts
#   YF_FETCH_BACKOFF_SECONDS    base backoff, doubled per attempt
#   YF_FETCH_MAX_BACKOFF_SECONDS  per-sleep cap (default 60s)
# A post-sleep-wake fire can land up to ~30-50 min late; a 1s/2s
# retry covers blips but not that window, so the scheduler widens it.
YF_FETCH_RETRIES = 3
YF_FETCH_BACKOFF_SECONDS = 1.0
YF_FETCH_MAX_BACKOFF_SECONDS = 60.0


def _retry_policy() -> Tuple[int, float, float]:
    """Resolve the retry policy from env overrides (else defaults).

    Read at call time (not import time) so the scheduler plist can
    widen the budget and tests can patch os.environ.
    """
    return (
        int(os.environ.get("YF_FETCH_RETRIES", YF_FETCH_RETRIES)),
        float(os.environ.get("YF_FETCH_BACKOFF_SECONDS", YF_FETCH_BACKOFF_SECONDS)),
        float(
            os.environ.get(
                "YF_FETCH_MAX_BACKOFF_SECONDS", YF_FETCH_MAX_BACKOFF_SECONDS
            )
        ),
    )


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
    # responses). Retry with exponential capped backoff before giving up —
    # a 16:05 daily paper-log run failing on one bad call silently loses
    # the signal for the day (observed 2026-08-13: SPY "possibly delisted"
    # with zero bars; retry succeeds seconds later; post-sleep-wake fires
    # on Aug 13/14/19/20 needed a wider scheduler budget).
    retries, backoff, max_backoff = _retry_policy()
    last_hist = None
    for attempt in range(retries):
        try:
            t = yf.Ticker(config.ticker)
            hist = t.history(period=config.period, interval=config.interval)
            if hist is not None and not hist.empty:
                last_hist = hist
                break
            last_hist = None
        except Exception:
            last_hist = None
        if attempt + 1 < retries:
            time.sleep(min(backoff * (2 ** attempt), max_backoff))

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
