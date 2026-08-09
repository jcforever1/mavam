"""Wide-ticker walk-forward sweep.

Tests 8 tickers across both book-only and mean-reverting policies.
Reports in-sample vs out-of-sample for each combination. The
result is a 2D grid: ticker × policy → IS P&L, OOS P&L, OOS Sharpe.

The point: find the (ticker, policy) cells where OOS P&L is positive
and OOS Sharpe is reasonable (>0.5). Those are the verified-alpha
cells. Everything else is overfit or noise.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from ..core.profile import Bar
from .walkforward import _split_bars_by_date, _run_on_bars
from .compare import make_mean_reverting_adjudicator


def main() -> None:
    base = Path(__file__).parent.parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        raise SystemExit(f"book-only strategy not found at {base}")

    with tempfile.TemporaryDirectory() as tmp:
        mr_path = make_mean_reverting_adjudicator(Path(tmp))

        # Tickers chosen to span regimes: ETFs, consumer staples, tech,
        # energy, financials, healthcare, meme-ish, recent IPO-ish
        tickers = [
            "SPY",   # S&P 500 ETF (broad market, trending)
            "QQQ",   # Nasdaq-100 (tech, trending)
            "KO",    # Coca-Cola (consumer staple, range-bound)  ← previous alpha
            "JNJ",   # Johnson & Johnson (defensive, range-bound)
            "XOM",   # ExxonMobil (energy, mixed)
            "JPM",   # JPMorgan (financial, mixed)
            "NVDA",  # NVIDIA (high vol, trending)
            "TSLA",  # Tesla (high vol, mixed)
        ]

        policies = [
            ("book-only (trend)", base),
            ("mean-reverting", mr_path),
        ]

        print("=" * 100)
        print("TICKER × POLICY WALK-FORWARD SWEEP — 60d, 5min, 30/30 split")
        print("=" * 100)
        print()
        print(f"{'Ticker':<8} {'Policy':<22} {'IS PnL':>9} {'OOS PnL':>9} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Trades':>11} {'Verdict':>12}")
        print("-" * 100)

        from ..data import YFinanceConfig, fetch_yfinance_bars

        winners = []
        for ticker in tickers:
            for policy_name, adj_path in policies:
                try:
                    yf_config = YFinanceConfig(
                        ticker=ticker, period="60d", interval="5m",
                        stale_after_seconds=0,
                    )
                    bars = fetch_yfinance_bars(yf_config)
                    if bars is None:
                        print(f"{ticker:<8} {policy_name:<22} {'yfinance failed':>40}")
                        continue
                    train_bars, test_bars = _split_bars_by_date(bars, train_days=30)
                    is_result = _run_on_bars(adj_path, train_bars, ticker)
                    oos_result = _run_on_bars(adj_path, test_bars, ticker)
                    is_pnl = is_result["total_pnl"]
                    oos_pnl = oos_result["total_pnl"]
                    is_sharpe = is_result["sharpe"]
                    oos_sharpe = oos_result["sharpe"]
                    oos_trades = oos_result["n_trades"]
                    verdict = "✓ ALPHA" if oos_pnl > 0 and oos_sharpe > 0.3 else ("~" if oos_pnl > 0 else "✗")
                    print(
                        f"{ticker:<8} {policy_name:<22} "
                        f"${is_pnl:>+7.2f}  ${oos_pnl:>+7.2f}  "
                        f"{is_sharpe:>+10.3f}  {oos_sharpe:>+11.3f}  "
                        f"{oos_trades:>11}  "
                        f"{verdict:>12}"
                    )
                    if verdict == "✓ ALPHA":
                        winners.append((ticker, policy_name, oos_pnl, oos_sharpe))
                except Exception as e:
                    print(f"{ticker:<8} {policy_name:<22} FAILED ({e})")
            print()
        print("=" * 100)
        print("VERIFIED ALPHA (out-of-sample PnL > 0 AND Sharpe > 0.3):")
        print("=" * 100)
        if not winners:
            print("  (none — only KO from the small sample)")
        for ticker, policy, pnl, sharpe in winners:
            print(f"  {ticker:<6} {policy:<22} OOS PnL ${pnl:+.2f}  OOS Sharpe {sharpe:+.3f}")


if __name__ == "__main__":
    main()
