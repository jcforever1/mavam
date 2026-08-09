"""Kronos-enabled walk-forward comparison.

Runs the same sweep as `sweep.py` but with Kronos active. The
Adjudicator can be configured to require Kronos confirmation, so a
BUY only fires when the book engine says BUY AND Kronos predicts UP.

This is the Council's 2-4h feasibility test (2026-08-08). The verdict:
"does ML confirmation add edge to the book-only signal?"

Usage:
    python3 -m rudra_intraday_engine.backtest.kronos_compare \
        --tickers KO AAPL MSFT PLTR
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from .compare import make_mean_reverting_adjudicator
from .sweep import TICKERS_50, fetch_bars_cached
from .walkforward import _split_bars_by_date, _run_on_bars


def _make_kronos_adjudicator(adj_dir: Path, base: Path) -> Path:
    """Build a Kronos-enhanced version of the book-only strategy.

    The rules require book.action == 'BUY' AND kronos.prediction == 'UP'
    (or DOWN for SELL). Anything else is HOLD (decide_no).
    """
    import tomlkit

    with base.open() as f:
        cfg = tomlkit.load(f)

    cfg["adjudicator"]["name"] = "book+kronos"
    cfg["adjudicator"]["kronos"]["required"] = True
    cfg["adjudicator"]["kronos"]["min_confidence"] = 0.05  # be permissive in v1
    cfg["adjudicator"]["fallback"]["when_no_kronos"] = "HOLD"

    # Replace the merge rules with Kronos-gated versions
    new_rules = tomlkit.aot()
    new_rules.append(tomlkit.table())  # placeholder; replaced below
    new_rules.clear()

    def rule(when, emit, reason, size=1.0):
        t = tomlkit.table()
        t["when"] = when
        t["emit"] = emit
        t["size_multiplier"] = size
        t["reason"] = reason
        return t

    new_rules.append(rule(
        "book.action == 'BUY' and kronos.prediction == 'UP' and kronos.confidence >= 0.10",
        "BUY", "book-and-kronos-bullish", 1.0,
    ))
    new_rules.append(rule(
        "book.action == 'SELL' and kronos.prediction == 'DOWN' and kronos.confidence >= 0.10",
        "SELL", "book-and-kronos-bearish", 1.0,
    ))
    new_rules.append(rule(
        "book.action == 'BUY' and kronos.prediction == 'UP' and kronos.confidence >= 0.05",
        "BUY", "book-and-kronos-bullish-low-conf", 0.5,
    ))
    new_rules.append(rule(
        "book.action == 'SELL' and kronos.prediction == 'DOWN' and kronos.confidence >= 0.05",
        "SELL", "book-and-kronos-bearish-low-conf", 0.5,
    ))

    cfg["adjudicator"]["merge_rules"] = new_rules

    out = adj_dir / "kronos.toml"
    with out.open("w") as f:
        tomlkit.dump(cfg, f)
    return out


def main(
    tickers: Optional[List[str]] = None,
    period: str = "60d",
    train_days: int = 30,
) -> int:
    if tickers is None:
        tickers = ["KO", "AAPL", "MSFT", "PLTR", "XLF", "MSTR"]

    base = Path(__file__).parent.parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        raise SystemExit(f"book-only strategy not found at {base}")

    with tempfile.TemporaryDirectory() as tmp:
        kronos_adj = _make_kronos_adjudicator(Path(tmp), base)

        print("=" * 100)
        print(f"KRONOS-ENABLED WALK-FORWARD — {len(tickers)} tickers, {period} bars, {train_days}/{train_days} split")
        print("=" * 100)
        print()
        print(f"{'Ticker':<8} {'Adj':<22} {'IS PnL':>9} {'OOS PnL':>9} "
              f"{'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Trades':>11} {'Verdict':>10}")
        print("-" * 100)

        for ticker in tickers:
            try:
                bars = fetch_bars_cached(ticker, period=period)
            except Exception as e:
                print(f"{ticker:<8} {'fetch failed':<22} {e}")
                continue
            if bars is None or not bars:
                print(f"{ticker:<8} {'(no data)':<22}")
                continue

            try:
                train_bars, test_bars = _split_bars_by_date(bars, train_days=train_days)
                is_res = _run_on_bars(kronos_adj, train_bars, ticker)
                oos_res = _run_on_bars(kronos_adj, test_bars, ticker)
            except Exception as e:
                print(f"{ticker:<8} {'backtest failed':<22} {e}")
                continue

            is_pnl = is_res["total_pnl"]
            oos_pnl = oos_res["total_pnl"]
            is_sharpe = is_res["sharpe"]
            oos_sharpe = oos_res["sharpe"]
            oos_trades = oos_res["n_trades"]
            verdict = "✓ ALPHA" if oos_pnl > 0 and oos_sharpe > 0.3 else (
                "~" if oos_pnl > 0 else "✗"
            )
            print(
                f"{ticker:<8} {'book+kronos':<22} "
                f"${is_pnl:>+8.2f} ${oos_pnl:>+8.2f} "
                f"{is_sharpe:>+10.3f}  {oos_sharpe:>+11.3f}  "
                f"{oos_trades:>11}  "
                f"{verdict:>10}"
            )
        print()
        print("Compare to book-only sweep (TICKERS_50):")
        print("  KO   book-only (trend)      OOS PnL $  +5.46  OOS Sharpe +3.939")
        print("  XLF  book-only (trend)      OOS PnL $  +2.19  OOS Sharpe +4.900")
        print("  PLTR book-only (trend)      OOS PnL $ +16.95  OOS Sharpe +3.214")
        print("  MSFT book-only (trend)      OOS PnL $ +28.72  OOS Sharpe +2.948")
        print("  AAPL book-only (trend)      OOS PnL $ +10.97  OOS Sharpe +1.965")
        print("  MSTR book-only (trend)      OOS PnL $  +3.37  OOS Sharpe +0.516")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--period", default="60d")
    parser.add_argument("--train-days", type=int, default=30)
    args = parser.parse_args()
    import sys
    sys.exit(main(args.tickers, args.period, args.train_days))
