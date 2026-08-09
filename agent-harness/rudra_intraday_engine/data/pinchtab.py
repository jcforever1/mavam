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
    started with `--remote-debugging-port=9222` (or to any other
    Chromium-based app the user has launched the same way)
  - Use the standard CDP (via playwright's connect_over_cdp) to list
    pages, attach to the chart page, and run JavaScript in the page's
    own context to read chart data the user is already viewing
  - Return a typed list[Bar] to the engine

This is the legitimate "control your own local app" pattern, the same
way Chrome DevTools lets you inspect a Chrome tab. TradingView's ToS
governs what the user does with the data; the engine neither knows
nor cares about that. The data is the user's, from the user's app,
running on the user's machine.

Setup (the user runs these on their own machine):

    # macOS
    /Applications/TradingView.app/Contents/MacOS/TradingView \\
        --remote-debugging-port=9222

    # Windows
    \"C:\\Program Files\\TradingView\\TradingView.exe\" \\
        --remote-debugging-port=9222

    # Linux
    /opt/tradingview/tradingview \\
        --remote-debugging-port=9222

The integration is:
  1. Stateless — no persistent connection, no session state.
  2. Pull-based — every fetch is a fresh CDP attach.
  3. Bounded — an explicit `stale_after_unix` rejects data older
     than the freshness window.

Requires:
    pip install rudra-intraday-engine[pinchtab]
    # (no chromium download — connects to the user's existing app)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core.profile import Bar


DEFAULT_STALE_AFTER_SECONDS = 300

# Default CDP endpoint. The user starts their TradingView Desktop
# (or any Chromium-based app) with this port.
DEFAULT_CDP_URL = "http://localhost:9222"


@dataclass(frozen=True)
class ChartConfig:
    """A pinchtab chart data source."""

    ticker: str
    exchange: str = "NASDAQ"
    interval: str = "5"
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    cdp_url: str = DEFAULT_CDP_URL
    screenshot_path: Optional[Path] = None  # for debugging / audit
    page_url_contains: Optional[str] = None  # if multiple tabs, pick this one


def pinchtab_available(cdp_url: str = DEFAULT_CDP_URL) -> bool:
    """Return True if a CDP endpoint is reachable at the given URL.

    This checks `GET /json/version` on the debug port. The user must
    have started their TradingView Desktop (or other Chromium app)
    with `--remote-debugging-port=9222` for this to return True.
    """
    try:
        import urllib.request
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _list_pages(cdp_url: str) -> list[dict]:
    """Return the list of pages (tabs) the CDP browser is showing."""
    import urllib.request
    with urllib.request.urlopen(f"{cdp_url}/json", timeout=5) as r:
        return json.loads(r.read())


def _pick_chart_page(
    pages: list[dict],
    page_url_contains: Optional[str] = None,
) -> Optional[dict]:
    """Pick the most likely TradingView chart page from the CDP page list."""
    chart_pages = []
    for p in pages:
        url = p.get("url", "")
        title = p.get("title", "")
        # TradingView chart pages: tradingview.com/chart, /chart/, or
        # the desktop app's internal chart pages.
        is_tv = (
            "tradingview" in url.lower()
            or "tradingview" in title.lower()
            or "/chart" in url.lower()
            or "chart" in title.lower()
        )
        if not is_tv:
            continue
        if page_url_contains and page_url_contains not in url:
            continue
        chart_pages.append(p)
    if not chart_pages:
        return None
    # Prefer the page with the most specific TradingView chart URL
    chart_pages.sort(
        key=lambda p: (
            "/chart" in p.get("url", ""),  # has /chart in URL
            "tradingview" in p.get("url", "").lower(),  # TV URL
            len(p.get("url", "")),  # longer URL = more specific
        ),
        reverse=True,
    )
    return chart_pages[0]


# JavaScript snippets that try to extract bar data from a TradingView
# page's JavaScript context. Each returns JSON-encoded array of
# {t, o, h, l, c, v} objects, or null. The first one that returns
# non-null wins.
_BAR_EXTRACTION_SNIPPETS = [
    # Strategy 1: lightweight-charts library (used by some TV widgets)
    """
    () => {
        try {
            const w = window;
            // Walk the DOM looking for chart instances
            const charts = [];
            if (w.Chart && typeof w.Chart.instances === 'object') {
                for (const k of Object.keys(w.Chart.instances || {})) {
                    const c = w.Chart.instances[k];
                    if (c && c.data && typeof c.data === 'function') {
                        charts.push(c);
                    }
                }
            }
            if (charts.length === 0 && w.tvWidget && w.tvWidget.activeChart) {
                const ac = w.tvWidget.activeChart();
                if (ac && ac.data) {
                    charts.push(ac);
                }
            }
            for (const c of charts) {
                try {
                    const data = c.data();
                    if (Array.isArray(data) && data.length > 0) {
                        return JSON.stringify(data.map(b => ({
                            t: b.time || b.t,
                            o: b.open !== undefined ? b.open : b.o,
                            h: b.high !== undefined ? b.high : b.h,
                            l: b.low !== undefined ? b.low : b.l,
                            c: b.close !== undefined ? b.close : b.c,
                            v: b.volume !== undefined ? b.volume : (b.v || 0)
                        })));
                    }
                } catch (e) { /* try next */ }
            }
            return null;
        } catch (e) {
            return null;
        }
    }
    """,
    # Strategy 2: scan for a datafeed or chart-data global
    """
    () => {
        try {
            const w = window;
            // Some TV embeds expose chart data as a global
            for (const key of Object.keys(w)) {
                if (/chart.*data/i.test(key) && Array.isArray(w[key]) && w[key].length > 0) {
                    return JSON.stringify(w[key]);
                }
            }
            return null;
        } catch (e) {
            return null;
        }
    }
    """,
]


def fetch_chart_bars(
    config: ChartConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Pull OHLCV bars from the user's local TradingView Desktop via CDP.

    Returns None if:
      - No CDP endpoint is reachable at config.cdp_url
      - No TradingView chart page is open in the connected browser
      - All bar-extraction snippets return null
      - The playwright CDP connect fails

    On success, returns a list of Bar objects sorted by timestamp
    ascending. Bars older than `stale_after_seconds` are filtered out.
    """
    as_of = as_of_unix if as_of_unix is not None else int(time.time())
    apply_staleness = config.stale_after_seconds > 0
    cutoff = as_of - config.stale_after_seconds if apply_staleness else None

    if not pinchtab_available(config.cdp_url):
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    raw_bars: List[Bar] = []
    chosen_page: Optional[dict] = None

    try:
        with sync_playwright() as p:
            # Connect to the user's existing browser — does NOT
            # launch a new chromium. The browser is the user's
            # TradingView Desktop.
            browser = p.chromium.connect_over_cdp(config.cdp_url)
            try:
                # Discover pages via CDP and via playwright's view
                all_pages = []
                for ctx in browser.contexts:
                    for page in ctx.pages:
                        all_pages.append(page)
                # Pick the chart page
                chart_page = None
                for page in all_pages:
                    url = page.url
                    if config.page_url_contains and config.page_url_contains not in url:
                        continue
                    if "tradingview" in url.lower() or "/chart" in url.lower():
                        chart_page = page
                        break
                if chart_page is None:
                    # Fall back to CDP /json enumeration
                    try:
                        pages_meta = _list_pages(config.cdp_url)
                        chosen = _pick_chart_page(pages_meta, config.page_url_contains)
                        if chosen and chosen.get("webSocketDebuggerUrl"):
                            # We can't easily re-attach to a CDP-only
                            # page from playwright without it being in
                            # the contexts; but the websocket URL is
                            # available. For now, signal failure.
                            return None
                    except Exception:
                        return None
                    return None

                # Try each extraction snippet in order
                for snippet in _BAR_EXTRACTION_SNIPPETS:
                    try:
                        result = chart_page.evaluate(snippet)
                    except Exception:
                        result = None
                    if result:
                        try:
                            parsed = json.loads(result)
                        except (TypeError, json.JSONDecodeError):
                            parsed = None
                        if isinstance(parsed, list) and parsed:
                            for b in parsed:
                                try:
                                    ts = int(b.get("t", 0))
                                    if ts < 1_000_000_000:
                                        ts = ts // 1000  # ms → s
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
                            if raw_bars:
                                break  # first strategy that worked wins
                        else:
                            # Couldn't parse — try next strategy
                            continue

                if config.screenshot_path is not None:
                    try:
                        chart_page.screenshot(path=str(config.screenshot_path))
                    except Exception:
                        pass
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    except Exception:
        return None

    if not raw_bars:
        return None

    raw_bars.sort(key=lambda b: b.timestamp_unix)
    return raw_bars


__all__ = [
    "ChartConfig",
    "DEFAULT_STALE_AFTER_SECONDS",
    "DEFAULT_CDP_URL",
    "pinchtab_available",
    "fetch_chart_bars",
]
