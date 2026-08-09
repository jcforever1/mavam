"""TradingView Desktop data source via the `tv` CLI (tradingview-mcp).

This source uses the `tv` CLI (npm package `tradingview-mcp` by
blasesc) to talk to the user's local TradingView Desktop via
Chrome DevTools Protocol. It's the new default over pinchtab because
it exposes more of the chart:

  - OHLCV bars (tv ohlcv)
  - Real-time quote (tv quote)
  - Current symbol/timeframe/type (tv state, tv symbol, tv timeframe)
  - Indicator values from the data window (tv values)
  - Screenshots (tv screenshot)
  - Pine Script tools (tv pine ...)
  - Chart drawings, alerts, watchlists, multi-pane layouts (tv ...)

The integration is the same as pinchtab: read-only access to
whatever the user has visible on their local Desktop. No broker,
no account, no server connection. CDP = the protocol DevTools uses.

The engine sees the same `list[Bar]` regardless of source. The
differences from `YFinanceConfig` / `ChartConfig`:
  - Uses the user's local TradingView Desktop (not a remote API)
  - Can read indicator values (a future v1.1 feature)
  - Can take screenshots for visual verification
  - Can write/draw on the chart (the "set" side of the integration)

Requires:
    npm install -g tradingview-mcp
    (TradingView Desktop must be running with CDP enabled, or
    `tv launch` can start it for you.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.profile import Bar


DEFAULT_TV_BIN = "tv"


# ── availability ─────────────────────────────────────────────────────


def tv_cli_available() -> bool:
    """Return True if the `tv` CLI is on PATH."""
    return shutil.which(DEFAULT_TV_BIN) is not None


def tv_desktop_reachable() -> bool:
    """Return True if TradingView Desktop is reachable via CDP.

    Calls `tv status` and checks for `cdp_connected: true`.
    """
    if not tv_cli_available():
        return False
    payload = _run_tv(["status"], timeout=5)
    if payload is None:
        return False
    return bool(payload.get("cdp_connected"))


# ── config ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DesktopConfig:
    """Configuration for the TradingView Desktop data source.

    Fields:
      ticker:     e.g. "NVDA", "BINANCE:BTCUSDT", "BATS:SPY"
                  If set, the chart will be switched to this symbol
                  before bars are pulled (unless switch_chart=False).
      timeframe:  TradingView resolution string: "1", "5", "15", "60",
                  "240", "1D", "1W", "1M" etc. Default "1D".
      count:      How many bars to pull (max 500 per the tv CLI).
      switch_chart: If True, switch the chart to (ticker, timeframe)
                    before pulling. If False, just read whatever is
                    on the current chart. Default True.
      timeout:    Subprocess timeout in seconds. Default 30.
    """

    ticker: str = ""
    timeframe: str = "1D"
    count: int = 0
    # Default False: do NOT navigate the chart. Just read whatever
    # the user has visible. This is critical for concurrent use:
    # if mavam's cron fires while you're using TradingView
    # interactively, the default behavior is "do nothing visible"
    # rather than "hijack your chart". Set True only when you
    # explicitly want mavam to drive the chart (e.g. backtest
    # sweeps over many tickers).
    switch_chart: bool = False
    timeout: int = 30


# ── low-level subprocess helper ──────────────────────────────────────


def _run_tv(args: List[str], *, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """Run a `tv` subcommand and return the parsed JSON, or None on failure."""
    if not tv_cli_available():
        return None
    try:
        out = subprocess.run(
            [DEFAULT_TV_BIN, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if out.returncode != 0:
            return None
        if not out.stdout.strip():
            return None
        return json.loads(out.stdout.strip())
    except (subprocess.TimeoutExpired, subprocess.SubprocessError,
            json.JSONDecodeError, OSError):
        return None


# ── public API ───────────────────────────────────────────────────────


def _switch_chart(ticker: str, timeframe: str, timeout: int) -> bool:
    """Navigate the chart to (ticker, timeframe). Returns True on success.

    Skips switching if the chart is already on the requested symbol +
    timeframe. This avoids the "chart_ready: false" race after
    switching away from and back to the same symbol.
    """
    if ticker or timeframe:
        state = _run_tv(["state"], timeout=timeout)
        cur_sym = str(state.get("symbol", "")).upper() if state else ""
        cur_tf = str(state.get("resolution", "")).upper() if state else ""
    else:
        cur_sym, cur_tf = "", ""
    if ticker and ticker.upper() != cur_sym:
        r = _run_tv(["symbol", ticker], timeout=timeout)
        if r is None or not r.get("success"):
            return False
    if timeframe and timeframe.upper() != cur_tf:
        r = _run_tv(["timeframe", timeframe], timeout=timeout)
        if r is None or not r.get("success"):
            return False
    return True


def fetch_desktop_bars(
    config: DesktopConfig,
    *,
    as_of_unix: Optional[int] = None,
) -> Optional[List[Bar]]:
    """Fetch OHLCV bars from the user's local TradingView Desktop.

    Returns None if:
      - the `tv` CLI is not installed
      - the Desktop is not reachable via CDP
      - the chart switch fails
      - the OHLCV pull returns no bars

    On success, returns a sorted list of Bar objects. Note: TradingView
    Desktop's bars are in **chronological order** (oldest first), so no
    extra sorting is needed.
    """
    if not tv_cli_available():
        return None
    if not tv_desktop_reachable():
        return None
    if config.switch_chart:
        if not _switch_chart(config.ticker, config.timeframe, config.timeout):
            return None
    elif config.ticker:
        # switch_chart=False but ticker is set: validate that the
        # current chart matches what the user asked for. If not,
        # warn (don't switch — that would hijack).
        state = _run_tv(["state"], timeout=config.timeout)
        if state:
            cur_sym = str(state.get("symbol", "")).upper()
            if config.ticker.upper() != cur_sym:
                # Stderr-style warning, not raised — concurrent use
                # is allowed, the user knows their chart.
                import sys
                print(
                    f"warning: data.desktop.ticker={config.ticker!r} but "
                    f"current chart is {cur_sym!r}; reading bars for "
                    f"the visible chart, not the requested ticker. "
                    f"Set switch_chart=true to navigate, or open the "
                    f"correct chart in TradingView.",
                    file=sys.stderr,
                )

    # TradingView Desktop's data feed has a finite per-chart history
    # (typically 300 daily bars, fewer for 1-min/5-min). The CLI
    # returns whatever's available up to `count`. We pass through
    # count=0 as "use the CLI's default" (which is 100, but the
    # total_available in the response tells the truth).

    count_arg = ["-n", str(config.count)] if config.count > 0 else []
    payload = _run_tv(
        ["ohlcv", *count_arg],
        timeout=config.timeout,
    )
    if payload is None or not payload.get("success"):
        return None

    bars: List[Bar] = []
    for raw in payload.get("bars", []):
        try:
            ts = int(raw["time"])
            bars.append(Bar(
                timestamp_unix=ts,
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=float(raw.get("volume", 0.0) or 0.0),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    if not bars:
        return None

    # TradingView Desktop returns chronological (oldest first) — sort
    # to be safe in case of any reordering.
    bars.sort(key=lambda b: b.timestamp_unix)
    return bars


def get_quote(ticker: str = "", *, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Get a real-time quote for the current chart (or specified ticker).

    Returns the parsed JSON from `tv quote` (with success, symbol,
    last, open, high, low, volume, etc.) or None on failure.
    """
    if ticker:
        _run_tv(["symbol", ticker], timeout=timeout)
    return _run_tv(["quote"], timeout=timeout)


def get_indicator_values(*, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Read the current values from the chart's indicator data window.

    Returns the parsed JSON from `tv values` (with study_count, studies)
    or None on failure.
    """
    return _run_tv(["values"], timeout=timeout)


def get_state(*, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Get the current chart's state: symbol, timeframe, type, studies.

    Returns the parsed JSON from `tv state` or None on failure.
    """
    return _run_tv(["state"], timeout=timeout)


def screenshot(output_path: str, *, timeout: int = 30) -> Optional[str]:
    """Take a screenshot of the current chart.

    Returns the file path where the screenshot was saved, or None
    on failure. The `tv screenshot` command may rewrite the filename;
    check the return JSON for the actual file_path field.
    """
    payload = _run_tv(
        ["screenshot", "--output", output_path],
        timeout=timeout,
    )
    if payload is None or not payload.get("success"):
        return None
    return payload.get("file_path") or output_path


__all__ = [
    "DesktopConfig",
    "tv_cli_available",
    "tv_desktop_reachable",
    "fetch_desktop_bars",
    "get_quote",
    "get_indicator_values",
    "get_state",
    "screenshot",
]
