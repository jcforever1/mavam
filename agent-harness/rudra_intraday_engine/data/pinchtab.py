"""TradingView chart data source via pinchtab (Playwright-backed).

The 'pinchtab' name in the architecture refers to the Chrome DevTools
Protocol pattern — a headless browser, a stateless pull, an explicit
`stale_after_unix` cut-off. This module implements that pattern using
Playwright (the Python-native equivalent of pinchtab's Go binary).

The integration is:
  1. Stateless: no persistent browser, no cookies, no history.
  2. Pull-based: every fetch is a fresh browser launch + close.
  3. Bounded: an explicit `stale_after_unix` rejects data older
     than the freshness window.

Requires:
    pip install rudra-intraday-engine[pinchtab]
    playwright install chromium

The integration returns None on any failure (no playwright, no
network, chart not found). The CLI treats None as a hard error
and exits with a helpful message.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core.profile import Bar


# Default staleness window — 5 minutes. The CLI can override this.
DEFAULT_STALE_AFTER_SECONDS = 300

# TradingView chart URL template
_TV_CHART_URL = "https://www.tradingview.com/chart/?symbol={exchange}:{ticker}"


@dataclass(frozen=True)
class ChartConfig:
    """A TradingView chart data source."""

    ticker: str
    exchange: str = "NASDAQ"
    interval: str = "5"  # 5-minute bars per the book's convention
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    screenshot_path: Optional[Path] = None  # for debugging / audit


def pinchtab_available() -> bool:
    """Return True if playwright + chromium are available."""
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    # Check if chromium browser is installed
    try:
        with sync_playwright() as p:
            # Try to launch — this will fail if chromium isn't installed
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def fetch_chart_bars(
    config: ChartConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Pull OHLCV bars from a TradingView chart.

    Returns None if:
      - playwright is not installed
      - chromium browser is not installed (`playwright install chromium`)
      - the chart fails to load
      - the bar data cannot be extracted

    On success, returns a list of Bar objects sorted by timestamp
    ascending. The bars are bounded by `stale_after_seconds`: any
    bar with timestamp_unix < (now - stale_after_seconds) is filtered
    out.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    as_of = as_of_unix if as_of_unix is not None else int(time.time())
    cutoff = as_of - config.stale_after_seconds

    url = _TV_CHART_URL.format(exchange=config.exchange, ticker=config.ticker)
    raw_bars: List[Bar] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.set_default_timeout(30_000)  # 30s
                page.goto(url, wait_until="domcontentloaded")
                # Wait for the chart canvas to appear
                try:
                    page.wait_for_selector(
                        "canvas.chart-gui-wrapper canvas, "
                        "[data-name='chart-markup-table']",
                        timeout=20_000,
                    )
                except Exception:
                    pass  # we'll try to extract anyway

                # Extract bars via the embedded chart widget.
                # The widget exposes its data via `tvWidget.activeChart().data()`.
                # This is an internal API and may break; we wrap in try/except.
                bars_raw = page.evaluate(
                    """
                    () => {
                        const w = window;
                        // Try the lightweight-charts API
                        if (w.tvWidget && w.tvWidget.activeChart) {
                            try {
                                const data = w.tvWidget.activeChart().data();
                                return JSON.stringify(data.map(b => ({
                                    t: b.time, o: b.open, h: b.high, l: b.low, c: b.close
                                })));
                            } catch (e) { /* fall through */ }
                        }
                        // Try the chart-data API on the global
                        if (w.ChartData && w.ChartData.bars) {
                            return JSON.stringify(w.ChartData.bars);
                        }
                        return null;
                    }
                    """
                )

                if config.screenshot_path is not None:
                    try:
                        page.screenshot(path=str(config.screenshot_path))
                    except Exception:
                        pass

                context.close()
            finally:
                browser.close()

    except Exception:
        return None

    if not bars_raw:
        return None

    try:
        parsed = json.loads(bars_raw)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(parsed, list):
        return None

    for b in parsed:
        try:
            # TradingView uses seconds-since-epoch
            ts = int(b.get("t", 0))
            if ts < 1_000_000_000:
                # Probably milliseconds — convert
                ts = ts // 1000
            if ts < cutoff:
                continue
            raw_bars.append(Bar(
                timestamp_unix=ts,
                open=float(b["o"]),
                high=float(b["h"]),
                low=float(b["l"]),
                close=float(b["c"]),
                volume=float(b.get("v", 0.0)),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    raw_bars.sort(key=lambda b: b.timestamp_unix)
    if not raw_bars:
        return None
    return raw_bars


__all__ = [
    "ChartConfig",
    "DEFAULT_STALE_AFTER_SECONDS",
    "pinchtab_available",
    "fetch_chart_bars",
]
