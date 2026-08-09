"""Walk-forward backtest — out-of-sample validation of the alpha claim.

The 60-day in-sample backtest showed:
  - SPY mean-reverting: +$28.12, Sharpe +0.53
  - KO book-only: +$6.35, Sharpe +2.40

But in-sample results are biased. The honest test is walk-forward:
split the 60 days into a train half and a test half, only count
the test half's P&L as evidence of alpha.

For each ticker/policy, this reports:
  - in-sample P&L (train half) — what the backtest showed
  - out-of-sample P&L (test half) — what would have actually happened
  - the gap between them is the overfit

If out-of-sample P&L is positive, the alpha is real.
If out-of-sample P&L is negative, the in-sample result was overfit.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import yfinance as yf

from ..adjudicator import load_adjudicator
from ..data import YFinanceConfig, fetch_yfinance_bars
from ..core.profile import Bar
from .runner import run_backtest
from .compare import make_mean_reverting_adjudicator


def _split_bars_by_date(
    bars: List[Bar],
    train_days: int,
) -> tuple[List[Bar], List[Bar]]:
    """Split bars into a train set (first `train_days` trading days)
    and a test set (the rest)."""
    from collections import defaultdict
    from datetime import datetime, timezone

    by_day = defaultdict(list)
    for b in bars:
        dt = datetime.fromtimestamp(b.timestamp_unix, tz=timezone.utc)
        key = dt.strftime("%Y-%m-%d")
        by_day[key].append(b)

    days_sorted = sorted(by_day.keys())
    train_keys = set(days_sorted[:train_days])
    train = [b for b in bars if datetime.fromtimestamp(b.timestamp_unix, tz=timezone.utc).strftime("%Y-%m-%d") in train_keys]
    test = [b for b in bars if datetime.fromtimestamp(b.timestamp_unix, tz=timezone.utc).strftime("%Y-%m-%d") not in train_keys]
    return train, test


def _run_on_bars(adj_path: Path, bars: List[Bar], ticker: str) -> dict:
    """Run the backtest on a custom bar list (bypasses yfinance)."""
    from datetime import datetime, timezone
    from collections import defaultdict
    from .runner import _run_day

    adj = load_adjudicator(adj_path)
    by_day = defaultdict(list)
    for b in bars:
        dt = datetime.fromtimestamp(b.timestamp_unix, tz=timezone.utc)
        key = dt.strftime("%Y-%m-%d")
        by_day[key].append(b)
    days_sorted = sorted(by_day.keys())
    bar_offsets = {}
    offset = 0
    for d in days_sorted:
        bar_offsets[d] = offset
        offset += len(by_day[d])

    all_trades = []
    for d in days_sorted:
        trades = _run_day(by_day[d], d, adj, ticker, bar_offsets[d])
        all_trades.extend(trades)

    from .metrics import compute_metrics
    metrics = compute_metrics(all_trades)
    return {
        "n_trades": metrics.n_trades,
        "win_rate": round(metrics.win_rate, 4),
        "total_pnl": round(metrics.total_pnl, 2),
        "profit_factor": (
            round(metrics.profit_factor, 3)
            if metrics.profit_factor != float("inf") else "inf"
        ),
        "sharpe": round(metrics.sharpe, 3),
        "max_dd_pct": round(metrics.max_drawdown_pct, 4),
    }


def main() -> None:
    print("=" * 78)
    print("WALK-FORWARD BACKTEST — 60 days, 30-day train / 30-day test")
    print("=" * 78)
    print()
    print("If out-of-sample P&L > 0, the alpha is real.")
    print("If out-of-sample P&L <= 0, the in-sample result was overfit.")
    print()

    base = Path(__file__).parent.parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        raise SystemExit(f"book-only strategy not found at {base}")

    with tempfile.TemporaryDirectory() as tmp:
        mr_path = make_mean_reverting_adjudicator(Path(tmp))

        # Pull 60 days of bars once
        configs = [
            ("SPY", base, "SPY book-only (trend)"),
            ("KO", base, "KO book-only"),
            ("SPY", mr_path, "SPY mean-reverting"),
        ]

        print(f"{'Setup':<35} {'IS PnL':>10} {'OOS PnL':>10} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'Verdict':>15}")
        print("-" * 95)
        for ticker, adj_path, desc in configs:
            try:
                yf_config = YFinanceConfig(
                    ticker=ticker, period="60d", interval="5m", stale_after_seconds=0,
                )
                bars = fetch_yfinance_bars(yf_config)
                if bars is None:
                    print(f"  {desc}: yfinance returned no bars")
                    continue
                train_bars, test_bars = _split_bars_by_date(bars, train_days=30)
                is_result = _run_on_bars(adj_path, train_bars, ticker)
                oos_result = _run_on_bars(adj_path, test_bars, ticker)
                verdict = "✓ alpha" if oos_result["total_pnl"] > 0 else "✗ overfit"
                print(
                    f"{desc:<35} "
                    f"${is_result['total_pnl']:>+8.2f}  "
                    f"${oos_result['total_pnl']:>+8.2f}  "
                    f"{is_result['sharpe']:>+10.3f}  "
                    f"{oos_result['sharpe']:>+11.3f}  "
                    f"{verdict:>15}"
                )
            except Exception as e:
                print(f"  {desc}: FAILED ({e})")
        print()
        print("Honest read:")
        print("  - 'IS PnL' = what the 60-day backtest showed (in-sample)")
        print("  - 'OOS PnL' = what would have happened on days 31-60 only (out-of-sample)")
        print("  - The gap between them is the overfit")


if __name__ == "__main__":
    main()
