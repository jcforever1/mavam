"""Wide-ticker walk-forward sweep.

Tests 50 tickers across both book-only and mean-reverting policies.
Reports in-sample vs out-of-sample for each combination. The
result is a 2D grid: ticker × policy → IS P&L, OOS P&L, OOS Sharpe.

The point: find the (ticker, policy) cells where OOS P&L is positive
and OOS Sharpe is reasonable (>0.3). Those are the verified-alpha
cells. Everything else is overfit or noise.

v2 changes (2026-08-09):
  - Expanded from 8 → 50 tickers across sectors
  - yfinance cache at ~/.cache/mavam/yf/<ticker>.csv to avoid re-fetch
  - Configurable minimum Sharpe threshold
  - Output: verdict list at end, sorted by OOS Sharpe
"""

from __future__ import annotations

import csv
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.profile import Bar
from ..data import YFinanceConfig, fetch_yfinance_bars
from .compare import make_mean_reverting_adjudicator
from .walkforward import _split_bars_by_date, _run_on_bars


# 50-ticker universe, organized by sector. Chosen to span regimes
# (trending, range-bound, high-vol, low-vol) and give the engine a
# real stress test.
TICKERS_50: List[str] = [
    # Broad market ETFs (8)
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs (8)
    "XLF", "XLE", "XLV", "XLY",
    "XLP", "XLU", "XLK", "XLB",
    # Consumer staples (8) — range-bound regime, KO's neighborhood
    "KO", "PG", "COST", "WMT",
    "MDLZ", "CL", "KMB", "GIS",
    # Tech mega-cap (8) — trending regime
    "AAPL", "MSFT", "GOOGL", "META",
    "AMZN", "NVDA", "TSLA", "AVGO",
    # Financials (6) — mixed regime
    "JPM", "BAC", "WFC", "GS",
    "BLK", "MS",
    # Energy (4) — mixed regime
    "XOM", "CVX", "COP", "SLB",
    # Healthcare (5) — defensive, range-bound
    "JNJ", "PFE", "UNH", "MRK", "ABBV",
    # Crypto-adjacent (2)
    "COIN", "MSTR",
    # Memes / surprises (3)
    "GME", "PLTR", "RIVN",
    # International / utility (2) — round out to 50
    "BABA", "NEE",
]

CACHE_DIR = Path.home() / ".cache" / "mavam" / "yf"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}.json"


def _bars_to_json(bars: List[Bar]) -> List[dict]:
    return [
        {
            "ts": b.timestamp_unix,
            "o": b.open,
            "h": b.high,
            "l": b.low,
            "c": b.close,
            "v": b.volume,
        }
        for b in bars
    ]


def _bars_from_json(raw: List[dict]) -> List[Bar]:
    return [
        Bar(
            timestamp_unix=int(r["ts"]),
            open=float(r["o"]),
            high=float(r["h"]),
            low=float(r["l"]),
            close=float(r["c"]),
            volume=float(r["v"]),
        )
        for r in raw
    ]


def _cache_age_seconds(path: Path) -> float:
    if not path.exists():
        return float("inf")
    return time.time() - path.stat().st_mtime


def fetch_bars_cached(
    ticker: str,
    period: str = "60d",
    interval: str = "5m",
    cache_max_age_seconds: int = 24 * 3600,
) -> Optional[List[Bar]]:
    """Fetch yfinance bars with on-disk cache.

    Cache key: ticker. Cache value: full bar list. Cache is honored if
    younger than `cache_max_age_seconds` (default 24h). A `stale` path
    re-fetches but the old cache stays as a fallback.
    """
    cache = _cache_path(ticker)
    age = _cache_age_seconds(cache)
    if age < cache_max_age_seconds:
        try:
            raw = json.loads(cache.read_text())
            return _bars_from_json(raw)
        except (OSError, ValueError, KeyError):
            pass  # fall through to re-fetch

    yf_config = YFinanceConfig(
        ticker=ticker, period=period, interval=interval,
        stale_after_seconds=0,
    )
    bars = fetch_yfinance_bars(yf_config)
    if bars:
        try:
            cache.write_text(json.dumps(_bars_to_json(bars)))
        except OSError:
            pass  # best-effort cache write
    return bars


def main(
    tickers: Optional[List[str]] = None,
    period: str = "60d",
    train_days: int = 30,
    min_sharpe: float = 0.3,
    use_cache: bool = True,
) -> int:
    """Run the sweep. Returns 0 on success, non-zero on error.

    All 50 tickers × 2 policies = 100 cells. The output table reports
    every cell; the verdict section lists only those with OOS P&L > 0
    AND OOS Sharpe > min_sharpe.
    """
    if tickers is None:
        tickers = TICKERS_50

    base = Path(__file__).parent.parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        raise SystemExit(f"book-only strategy not found at {base}")

    with tempfile.TemporaryDirectory() as tmp:
        mr_path = make_mean_reverting_adjudicator(Path(tmp))

        policies = [
            ("book-only (trend)", base),
            ("mean-reverting", mr_path),
        ]

        # Compute the actual sweep start time so the cache max-age
        # window is from the start of the run, not 24h ago.
        sweep_started_at = time.time()

        print("=" * 110)
        print(f"TICKER × POLICY WALK-FORWARD SWEEP — {len(tickers)} tickers × {len(policies)} policies, "
              f"{period} bars, {train_days}/{train_days} split")
        print(f"Sweep started: {datetime.now(tz=timezone.utc).isoformat(timespec='seconds')}")
        print("=" * 110)
        print()
        print(
            f"{'Ticker':<8} {'Policy':<22} {'IS PnL':>9} {'OOS PnL':>9} "
            f"{'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS Trades':>11} {'Verdict':>10}"
        )
        print("-" * 110)

        winners: List[Tuple[str, str, float, float, int]] = []
        failures: List[str] = []

        for ticker in tickers:
            if use_cache:
                try:
                    bars = fetch_bars_cached(ticker, period=period)
                except Exception as e:
                    print(f"{ticker:<8} {'(fetch)':<22} FAILED ({e})")
                    failures.append(ticker)
                    continue
            else:
                yf_config = YFinanceConfig(
                    ticker=ticker, period=period, interval="5m",
                    stale_after_seconds=0,
                )
                bars = fetch_yfinance_bars(yf_config)

            if bars is None or not bars:
                print(f"{ticker:<8} {'(no data)':<22} {'yfinance returned empty':>40}")
                failures.append(ticker)
                continue

            for policy_name, adj_path in policies:
                try:
                    train_bars, test_bars = _split_bars_by_date(bars, train_days=train_days)
                    is_result = _run_on_bars(adj_path, train_bars, ticker)
                    oos_result = _run_on_bars(adj_path, test_bars, ticker)
                    is_pnl = is_result["total_pnl"]
                    oos_pnl = oos_result["total_pnl"]
                    is_sharpe = is_result["sharpe"]
                    oos_sharpe = oos_result["sharpe"]
                    oos_trades = oos_result["n_trades"]
                    verdict = "✓ ALPHA" if oos_pnl > 0 and oos_sharpe > min_sharpe else (
                        "~" if oos_pnl > 0 else "✗"
                    )
                    print(
                        f"{ticker:<8} {policy_name:<22} "
                        f"${is_pnl:>+8.2f} ${oos_pnl:>+8.2f} "
                        f"{is_sharpe:>+10.3f}  {oos_sharpe:>+11.3f}  "
                        f"{oos_trades:>11}  "
                        f"{verdict:>10}"
                    )
                    if verdict == "✓ ALPHA":
                        winners.append((ticker, policy_name, oos_pnl, oos_sharpe, oos_trades))
                except Exception as e:
                    print(f"{ticker:<8} {policy_name:<22} FAILED ({e})")
            print()

        print("=" * 110)
        print(f"VERIFIED ALPHA (OOS PnL > 0 AND OOS Sharpe > {min_sharpe}):")
        print("=" * 110)
        if not winners:
            print("  (none)")
        else:
            winners.sort(key=lambda r: -r[3])  # by OOS Sharpe, descending
            for ticker, policy, pnl, sharpe, trades in winners:
                print(
                    f"  {ticker:<6} {policy:<22} "
                    f"OOS PnL ${pnl:+7.2f}  OOS Sharpe {sharpe:+.3f}  OOS Trades {trades}"
                )

        print()
        print("=" * 110)
        print(f"SUMMARY: {len(tickers) - len(failures)}/{len(tickers)} tickers fetched, "
              f"{len(winners)}/{len(tickers) * len(policies)} cells with alpha, "
              f"{len(failures)} ticker failures")
        print("=" * 110)
        if failures:
            print(f"Failed tickers: {', '.join(failures)}")

        sweep_elapsed = time.time() - sweep_started_at
        print(f"Sweep took {sweep_elapsed:.1f}s")
        return 0 if not failures else 1


if __name__ == "__main__":
    import sys
    rc = main()
    sys.exit(rc)
