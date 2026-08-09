"""Order flow installation helper.

The `tv` CLI's `tv indicator add` only matches TradingView's
hardcoded built-in names. Custom Pine scripts (like the one in
examples/pine/orderflow-summary.pine) cannot be added through
the CLI's `add` command — they have to go through the Pine
Editor's "Add to chart" button, which is not reliably clickable
via CDP synthetic events.

This module does what it CAN do:

  1. Verify TradingView Desktop is reachable.
  2. Open a new Pine Script in the editor with our source loaded.
  3. Save the script (so it appears in your "Saved Scripts").
  4. Print clear manual instructions for the user to click
     "Add to chart" in the editor.

The `mavam install orderflow` command calls this module.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .tradingview_source import (
    DEFAULT_TV_BIN,
    _run_tv,
    tv_cli_available,
    tv_desktop_reachable,
)


PIN_FILE = (
    Path(__file__).parent.parent.parent
    / "examples"
    / "pine"
    / "orderflow-summary.pine"
)


def install_orderflow() -> dict:
    """Open the order flow Pine Script in TradingView's editor.

    Returns a status dict:
      {
        "tv_reachable": bool,
        "pine_file": str,
        "lines_set": int,
        "saved": bool,
        "added_to_chart": bool,
        "manual_steps": [str, ...],
      }
    """
    result = {
        "tv_reachable": False,
        "pine_file": str(PIN_FILE),
        "lines_set": 0,
        "saved": False,
        "added_to_chart": False,
        "manual_steps": [],
    }
    if not tv_cli_available():
        result["manual_steps"].append(
            "tv CLI not installed; install with `npm install -g tradingview-mcp`"
        )
        return result
    if not tv_desktop_reachable():
        result["manual_steps"].append(
            "TradingView Desktop not reachable; start it with `tv launch`"
        )
        return result
    result["tv_reachable"] = True

    if not PIN_FILE.exists():
        result["manual_steps"].append(f"Pine file not found: {PIN_FILE}")
        return result

    # Open a new Pine editor and load the source
    new_resp = _run_tv(["pine", "new"], timeout=10)
    if new_resp is None:
        result["manual_steps"].append(
            "Could not open Pine Editor; open it manually in TradingView"
        )
        return result

    # Set the source
    set_resp = subprocess.run(
        [DEFAULT_TV_BIN, "pine", "set", "--file", str(PIN_FILE)],
        capture_output=True, text=True, timeout=15,
    )
    if set_resp.returncode == 0:
        try:
            set_data = json.loads(set_resp.stdout)
            result["lines_set"] = int(set_data.get("lines_set", 0))
        except (json.JSONDecodeError, ValueError):
            pass

    # Save
    save_resp = _run_tv(["pine", "save"], timeout=10)
    if save_resp is not None and save_resp.get("success"):
        result["saved"] = True

    # Note: `tv indicator add` does not work for custom scripts.
    # The user must click "Add to chart" in the Pine Editor.

    result["manual_steps"] = [
        "The Pine Script has been opened in TradingView's editor.",
        "1. Look at the Pine Editor pane (right side or bottom).",
        "2. Click the green 'Add to chart' button at the top of the editor.",
        "3. The indicator will appear in a new pane below the price chart.",
        "",
        "If the editor is not visible:",
        "  - Click 'Pine Editor' at the bottom of TradingView",
        "  - Or run `tv pine new` to re-open it",
        "",
        "After 'Add to chart' is clicked, verify with:",
        "  tv state          # should show 'OrderFlow Summary' in studies",
        "  tv values         # should show CumDelta, OBV, VWAP, etc.",
    ]
    return result


__all__ = ["install_orderflow", "PIN_FILE"]
