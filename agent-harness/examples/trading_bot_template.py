"""Trading bot integration template for rudra-intraday-engine.

This is a minimal example showing how an AI trading agent (or any
bot) can integrate with the engine. The pattern is:

  1. The bot writes a config TOML to a path only it controls.
  2. The bot invokes `rudra-intraday run <config.toml>` as a
     subprocess.
  3. The bot reads the JSON TradeSignal from stdout.
  4. The bot decides whether to act (BROKER INTEGRATION NOT SHOWN).

In production, step 4 would call your broker's API (Alpaca, IBKR,
TD Ameritrade, etc.) with the action, symbol, qty, and prices.
The engine emits everything the broker needs.

This template is intentionally minimal — it's a starting point,
not a complete trading system. Real bots need: position tracking,
error handling, rate limits, fill confirmation, drawdown limits,
exchange connectivity, etc. The engine handles the decision; the
bot handles the rest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


RUDRA_INTRADAY = "rudra-intraday"  # the installed console command


def write_config(
    adjudicator_path: Path,
    data_csv_path: Path,
    predictor_enabled: bool = False,
) -> Path:
    """Write a run config TOML to a temp file. Return the path.

    The bot owns this path. The engine never reads from $HOME — it
    only reads from the path on the command line.
    """
    content = f"""[adjudicator]
file = "{adjudicator_path}"

[predictor]
enabled = {str(predictor_enabled).lower()}

[data]
csv = "{data_csv_path}"
"""
    fd, path = tempfile.mkstemp(suffix=".toml", prefix="rudra-bot-")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return Path(path)


def run_engine(config_path: Path) -> dict[str, Any]:
    """Invoke the engine as a subprocess. Return the parsed JSON signal.

    Raises subprocess.CalledProcessError on non-zero exit.
    """
    result = subprocess.run(
        [RUDRA_INTRADAY, "run", str(config_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def act_on_signal(signal: dict[str, Any]) -> None:
    """Translate a TradeSignal into a broker call.

    This is a stub. Replace with your broker's API client. The signal
    dict has all the fields you need: action, symbol, qty, order_type,
    entry_price, stop_loss, take_profit, confidence, reason.
    """
    if signal["is_decide_no"]:
        # The engine decided NOT to trade. Log the reason and return.
        reasons = signal.get("decide_no_reasons", [])
        print(f"[bot] decide_no: {', '.join(reasons)}")
        return

    action = signal["action"]
    if action == "HOLD":
        # Not decide_no but also not actionable.
        print(f"[bot] HOLD ({signal['reason']})")
        return

    # Build the broker order (replace with your broker's API)
    order = {
        "action": action,
        "symbol": signal["symbol"],
        "qty": signal["qty"],
        "order_type": signal["order_type"],
        "entry_price": signal["entry_price"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "reason": signal["reason"],
        "confidence": signal["confidence"],
    }
    # TODO: send `order` to your broker here.
    print(f"[bot] would send to broker: {json.dumps(order, indent=2)}")


def main() -> int:
    """Entry point: write config → run engine → act on signal."""
    if len(sys.argv) != 3:
        print("usage: trading_bot_template.py <adjudicator.toml> <data.csv>")
        return 2

    adj_path = Path(sys.argv[1]).resolve()
    csv_path = Path(sys.argv[2]).resolve()
    if not adj_path.exists():
        print(f"adjudicator not found: {adj_path}")
        return 2
    if not csv_path.exists():
        print(f"data not found: {csv_path}")
        return 2

    config_path = write_config(adj_path, csv_path, predictor_enabled=False)
    try:
        signal = run_engine(config_path)
        act_on_signal(signal)
    finally:
        # Always clean up the temp config
        try:
            os.unlink(config_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
