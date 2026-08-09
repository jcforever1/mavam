"""pinchtab data source — CDP client to the user's local TradingView Desktop.

pinchtab is a debugging-interface client, not a scraper. It connects
to a Chromium-based application (here: TradingView Desktop) over the
standard Chrome DevTools Protocol, using the `--remote-debugging-port=9222`
flag the user explicitly enables on their own machine.

What this module does NOT do:
  - Connect to TradingView's servers or any remote infrastructure
  - Reverse-engineer any proprietary TradingView protocol
  - Bypass any access controls (the debug port requires the user's
    explicit opt-in via a Chromium command-line flag)
  - Read data from the public tradingview.com web app

What this module DOES do:
  - Connect to a locally-running TradingView Desktop that the user
    started with `--remote-debugging-port=9222`
  - Use raw CDP over the page-level WebSocket (NOT playwright — that
    library hangs on the Desktop's protocol handshake, a known issue
    with Electron-based apps) to evaluate JavaScript in the chart
    page's context
  - Read the bar data via TradingView's internal chart model:
        _exposed_chartWidgetCollection.activeChartWidget.value()
            .model().mainSeries().data().bars()
    Each bar is a 6-element array [unix_time, open, high, low, close, volume]
  - Return a typed list[Bar] to the engine

IMPORTANT CONSTRAINTS:
  - The user must have a chart open in the connected Desktop with the
    resolution AND history range they want. TradingView's free tier
    limits 5-min data to recent history (~10 days). For longer backtests
    use yfinance (no such limit).
  - The data returned is exactly what the chart is currently showing.
    If the chart is on 1M resolution, you get monthly bars. If on
    5min, you get 5-min bars (but limited by TradingView's history depth).

Setup:
    /Applications/TradingView.app/Contents/MacOS/TradingView \\
        --remote-debugging-port=9222
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core.profile import Bar


DEFAULT_STALE_AFTER_SECONDS = 300
DEFAULT_CDP_URL = "http://localhost:9222"


@dataclass(frozen=True)
class ChartConfig:
    """A pinchtab chart data source."""

    ticker: str
    exchange: str = "NASDAQ"
    interval: str = "5"
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    cdp_url: str = DEFAULT_CDP_URL
    screenshot_path: Optional[Path] = None
    page_url_contains: Optional[str] = None


def pinchtab_available(cdp_url: str = DEFAULT_CDP_URL) -> bool:
    """Return True if a CDP endpoint is reachable at the given URL."""
    try:
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _find_chart_page(cdp_url: str) -> Optional[dict]:
    """Find the TradingView chart page in the CDP page list."""
    try:
        with urllib.request.urlopen(f"{cdp_url}/json", timeout=5) as r:
            pages = json.loads(r.read())
    except Exception:
        return None
    for p in pages:
        if p.get("type") != "page":
            continue
        url = p.get("url", "")
        if "tradingview.com/chart" in url.lower() or "/chart" in url.lower():
            return p
    return None


# The real bar-extraction snippet. Path discovered against the
# user's actual TradingView Desktop via runtime introspection:
#   _exposed_chartWidgetCollection.activeChartWidget.value()
#     .model().mainSeries().data().bars()
# Each call to bars.valueAt(i) returns a 6-element array
# [unix_time, open, high, low, close, volume].
_BAR_EXTRACTION_SNIPPET = """
(() => {
    const out = {size: 0, symbol: null, resolution: null, bars: []};
    try {
        const c = window._exposed_chartWidgetCollection;
        if (!c) return {error: '_exposed_chartWidgetCollection not found'};
        const active = c.activeChartWidget;
        if (!active) return {error: 'no activeChartWidget'};
        const real = typeof active.value === 'function' ? active.value() : active;
        if (!real) return {error: 'cannot unwrap active chart widget'};
        const model = real.model();
        if (!model) return {error: 'no model'};
        const series = model.mainSeries();
        if (!series) return {error: 'no mainSeries'};
        const data = series.data();
        if (!data) return {error: 'no series.data'};
        const bars = data.bars();
        if (!bars) return {error: 'no data.bars()'};
        const size = data.size();
        if (typeof size !== 'number' || size <= 0) {
            return {error: 'no bars', size};
        }
        // Get symbol + resolution for diagnostics
        try { out.symbol = real.getSymbol ? real.getSymbol() : null; } catch (e) {}
        try { out.resolution = real.getResolution ? real.getResolution() : null; } catch (e) {}
        out.size = size;
        // Walk all bars. Use valueAt(i) which returns the raw array
        // directly. Cap at 10000 bars to avoid serializing massive
        // datasets (5-min × 60 days ≈ 2000 bars is the realistic max).
        const cap = Math.min(size, 10000);
        for (let i = 0; i < cap; i++) {
            const arr = bars.valueAt(i);
            if (arr && arr.length >= 5) {
                out.bars.push({
                    t: arr[0],
                    o: arr[1],
                    h: arr[2],
                    l: arr[3],
                    c: arr[4],
                    v: arr[5] || 0
                });
            }
        }
    } catch (e) {
        out.error = String(e);
    }
    return out;
})()
"""


def fetch_chart_bars(
    config: ChartConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Pull OHLCV bars from the user's local TradingView Desktop via raw CDP.

    Returns None if:
      - No CDP endpoint is reachable at config.cdp_url
      - No TradingView chart page is open
      - The runtime evaluation fails
      - No bars come back

    On success, returns a list of Bar objects sorted by timestamp
    ascending. Bars older than `stale_after_seconds` are filtered out.
    """
    as_of = as_of_unix if as_of_unix is not None else int(time.time())
    apply_staleness = config.stale_after_seconds > 0
    cutoff = as_of - config.stale_after_seconds if apply_staleness else None

    if not pinchtab_available(config.cdp_url):
        return None

    page = _find_chart_page(config.cdp_url)
    if page is None or not page.get("webSocketDebuggerUrl"):
        return None
    if config.page_url_contains and config.page_url_contains not in page.get("url", ""):
        return None

    # Use raw CDP over the page's WebSocket. NOT playwright — that
    # library hangs on the Electron app's protocol handshake.
    try:
        import websockets
    except ImportError:
        return None

    raw_bars: List[Bar] = []
    symbol: Optional[str] = None
    resolution: Optional[str] = None

    async def _run() -> Optional[dict]:
        async with websockets.connect(
            page["webSocketDebuggerUrl"], max_size=50_000_000, ping_interval=None,
        ) as ws:
            # Runtime.enable
            await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if msg.get("id") == 1:
                    break

            # Runtime.evaluate the extraction snippet
            await ws.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": _BAR_EXTRACTION_SNIPPET,
                    "returnByValue": True,
                },
            }))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("id") == 2:
                    if "result" in msg and "result" in msg["result"]:
                        r = msg["result"]["result"].get("value")
                        if isinstance(r, dict) and "bars" in r:
                            return r
                        if isinstance(r, dict) and "error" in r:
                            return None
                        return None
                    return None

    try:
        result = asyncio.run(_run())
    except Exception:
        return None

    if not result or "bars" not in result:
        return None

    symbol = result.get("symbol")
    resolution = result.get("resolution")

    for b in result["bars"]:
        try:
            ts = int(b.get("t", 0))
            # TradingView returns timestamps in Unix seconds (not ms).
            # Modern timestamps are ~1.7e9 seconds, so we use as-is.
            # (The previous ms-vs-seconds heuristic was wrong here.)
            if cutoff is not None and ts < cutoff:
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
    "DEFAULT_CDP_URL",
    "pinchtab_available",
    "fetch_chart_bars",
]
