"""Multi-config backtest comparison.

Runs the engine against several ticker/policy combinations and
prints a side-by-side report. The point is to separate:
  - "the book has no alpha on SPY" (specific to one setup)
  - "the engine produces no alpha anywhere" (general verdict)

Each backtest is fully independent. The comparison answers the
honest question: did I build a tool that works in any market, or
just a tool that's well-tested and breaks the same way everywhere?
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

from .runner import run_backtest


def make_mean_reverting_adjudicator(tmpdir: Path) -> Path:
    """An Adjudicator that fades the extremes (mean-reversion).
    Different policy from the book-only trend-following default.
    """
    toml = """
[adjudicator]
name = "mean-reverting"
version = "1.0.0"
description = "Fade the VA extremes. BUY near VA low, SELL near VA high."

[adjudicator.book]
required = true
min_rules_fired = 2

[adjudicator.fallback]
when_no_kronos = "HOLD"
when_no_book = "HOLD"
when_validation_fails = "HOLD"

[[adjudicator.merge_rules]]
when = "book.action == 'SELL' and book.confidence >= 0.60"
emit = "SELL"
size_multiplier = 1.0
reason = "fade-up-extreme"

[[adjudicator.merge_rules]]
when = "book.action == 'BUY' and book.confidence >= 0.60"
emit = "BUY"
size_multiplier = 1.0
reason = "fade-down-extreme"

[adjudicator.risk]
max_position_pct = 0.05
stop_loss_pct = 0.012
take_profit_pct = 0.025
max_daily_trades = 6
"""
    p = tmpdir / "mean-reverting.toml"
    p.write_text(toml)
    return p


def run_comparison(
    ticker: str,
    adjudicator_path: Path,
    period: str = "60d",
) -> dict:
    """Run one backtest and return a metrics dict."""
    from rudra_intraday_engine.adjudicator import load_adjudicator
    adj = load_adjudicator(adjudicator_path)
    trades, metrics = run_backtest(adj, ticker=ticker, period=period)
    return {
        "ticker": ticker,
        "strategy": adj.name,
        "n_trades": metrics.n_trades,
        "win_rate": round(metrics.win_rate, 4),
        "total_pnl": round(metrics.total_pnl, 2),
        "profit_factor": (
            round(metrics.profit_factor, 3)
            if metrics.profit_factor != float("inf")
            else "inf"
        ),
        "sharpe": round(metrics.sharpe, 3),
        "max_dd_pct": round(metrics.max_drawdown_pct, 4),
        "avg_win": round(metrics.avg_win, 2),
        "avg_loss": round(metrics.avg_loss, 2),
    }


def main() -> None:
    """Run the comparison."""
    from rudra_intraday_engine.adjudicator import load_adjudicator

    base = Path(__file__).parent.parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        raise SystemExit(f"book-only strategy not found at {base}")

    with tempfile.TemporaryDirectory() as tmp:
        mr_path = make_mean_reverting_adjudicator(Path(tmp))

        setups = [
            ("SPY", base, "60d", "book-only trend-following"),
            ("QQQ", base, "60d", "book-only on QQQ (tech, similar regime)"),
            ("KO", base, "60d", "book-only on KO (consumer staple, range-bound)"),
            ("NVDA", base, "60d", "book-only on NVDA (high vol, mixed regime)"),
            ("SPY", mr_path, "60d", "mean-reverting on SPY"),
        ]

        print("=" * 78)
        print("MULTI-CONFIG BACKTEST COMPARISON — 60 days, 5-min bars")
        print("=" * 78)
        print()
        results = []
        for ticker, adj_path, period, desc in setups:
            try:
                r = run_comparison(ticker, adj_path, period=period)
                r["description"] = desc
                results.append(r)
            except Exception as e:
                print(f"  {desc}: FAILED ({e})")

        # Print a comparison table
        print(f"{'Description':<40} {'Trades':>7} {'Win%':>7} {'PnL':>10} {'PF':>7} {'Sharpe':>7}")
        print("-" * 78)
        for r in results:
            print(
                f"{r['description']:<40} "
                f"{r['n_trades']:>7} "
                f"{r['win_rate']*100:>6.1f}% "
                f"{r['total_pnl']:>+9.2f} "
                f"{str(r['profit_factor']):>7} "
                f"{r['sharpe']:>+7.3f}"
            )
        print()
        print("Buy-and-hold baselines (60d, 100k invested at first close):")
        import yfinance as yf
        for ticker in ("SPY", "QQQ", "KO", "NVDA"):
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="60d", interval="5m")
                if len(hist) > 0:
                    first = hist["Close"].iloc[0]
                    last = hist["Close"].iloc[-1]
                    pnl = (last - first) * (100000.0 / first)
                    ret = (last - first) / first * 100
                    print(f"  {ticker:<6} {ret:+5.2f}%  (PnL ${pnl:+,.2f})")
            except Exception as e:
                print(f"  {ticker:<6} FAILED ({e})")


if __name__ == "__main__":
    main()
