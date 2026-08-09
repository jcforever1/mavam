"""rudra-intraday CLI — argv-strict main entry.

Usage:
    rudra-intraday run <config.toml>
    rudra-intraday explain <hash>
    rudra-intraday verify <hash>
    rudra-intraday predict <data.csv>

The CLI takes one positional argv per command. No flags, no env vars,
no dotfile walks. The config TOML is the only policy input. The
artifact store under $HOME/state/ is the only output side-effect.

This is Branch C of the architecture — minimal attack surface.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Sequence

from .artifact import explain_signal, write_trade_signal
from .config_schema import (
    ArgvError,
    Config,
    ConfigError,
    TomlParseError,
    SchemaViolation,
    load_config_from_argv,
)
from .adjudicator import load_adjudicator, merge
from .core.book_engine import BOOK_ENGINE_VERSION, evaluate_session
from .core.predictor import kronos_available
from .data import DataLoadError, load_bars_from_csv
from .signal_types import TradeSignal, now_iso


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_DATA = 4
EXIT_RUNTIME = 5


def main(argv: Sequence[str]) -> int:
    """Dispatch argv to the right subcommand. Returns process exit code."""
    # Strip the program name
    args = list(argv[1:])
    if not args:
        _print_usage()
        return EXIT_USAGE

    cmd = args[0]
    rest = args[1:]

    if cmd == "run":
        return _cmd_run(rest)
    if cmd == "explain":
        return _cmd_explain(rest)
    if cmd == "verify":
        return _cmd_verify(rest)
    if cmd == "predict":
        return _cmd_predict(rest)
    if cmd in ("-h", "--help", "help"):
        _print_usage()
        return EXIT_OK
    if cmd in ("-v", "--version"):
        print(f"rudra-intraday {BOOK_ENGINE_VERSION}")
        return EXIT_OK

    print(f"rudra-intraday: unknown command {cmd!r}", file=sys.stderr)
    _print_usage()
    return EXIT_USAGE


# ── run ──────────────────────────────────────────────────────────────


def _cmd_run(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: rudra-intraday run <config.toml>", file=sys.stderr)
        return EXIT_USAGE

    config_path = args[0]
    try:
        config = load_config_from_argv([config_path])
    except ArgvError as e:
        print(f"argv error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except (TomlParseError, SchemaViolation) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except FileNotFoundError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # Load adjudicator
    try:
        adj = load_adjudicator(config.adjudicator.file)
    except FileNotFoundError as e:
        print(f"adjudicator not found: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as e:
        print(f"adjudicator error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # Load data
    try:
        if config.data.csv is not None:
            bars = load_bars_from_csv(config.data.csv)
        elif config.data.ticker is not None:
            # yfinance HTTP path (no browser)
            from .data import fetch_yfinance_bars, YFinanceConfig, yfinance_available
            if not yfinance_available():
                print(
                    "data error: ticker source requires yfinance. "
                    "Install with: pip install rudra-intraday-engine[yfinance]",
                    file=sys.stderr,
                )
                return EXIT_DATA
            yf_config = YFinanceConfig(
                ticker=config.data.ticker,
                period="5d",
                interval="5m",
                stale_after_seconds=0,  # 0 = no filter, return all bars
            )
            bars = fetch_yfinance_bars(yf_config, as_of_unix=config.as_of_unix)
            if bars is None:
                print(
                    f"data error: yfinance returned no bars for "
                    f"{config.data.ticker}; check network or ticker symbol",
                    file=sys.stderr,
                )
                return EXIT_DATA
        elif config.data.chart is not None:
            # pinchtab: CDP connection to the user's local TradingView
            # Desktop running with --remote-debugging-port=9222.
            from .data import fetch_chart_bars, ChartConfig, pinchtab_available
            chart_d = config.data.chart
            chart_config = ChartConfig(
                ticker=chart_d.get("ticker", ""),
                exchange=chart_d.get("exchange", "NASDAQ"),
                interval=str(chart_d.get("interval", "5")),
                stale_after_seconds=int(chart_d.get("stale_after_seconds", 0)),
                cdp_url=chart_d.get("cdp_url", "http://localhost:9222"),
                page_url_contains=chart_d.get("page_url_contains"),
            )
            if not pinchtab_available(chart_config.cdp_url):
                print(
                    f"data error: no TradingView Desktop reachable at "
                    f"{chart_config.cdp_url}. Start your desktop app with "
                    f"--remote-debugging-port=9222, or use [data] ticker=... "
                    f"with yfinance for an HTTP-only path.",
                    file=sys.stderr,
                )
                return EXIT_DATA
            bars = fetch_chart_bars(chart_config, as_of_unix=config.as_of_unix)
            if bars is None:
                print(
                    f"data error: pinchtab chart fetch returned no bars; "
                    f"is a chart open in the connected TradingView Desktop?",
                    file=sys.stderr,
                )
                return EXIT_DATA
        else:
            # For v1, only CSV, ticker, and chart are supported
            print(
                f"data error: no data source configured "
                f"(csv={config.data.csv}, ticker={config.data.ticker}, "
                f"chart={config.data.chart}, fixture={config.data.fixture})",
                file=sys.stderr,
            )
            return EXIT_DATA
    except DataLoadError as e:
        print(f"data error: {e}", file=sys.stderr)
        return EXIT_DATA

    if not bars:
        print("data error: no bars loaded", file=sys.stderr)
        return EXIT_DATA

    # Run the book engine
    book, kronos, sc, cd, div = evaluate_session(
        bars, include_kronos=config.predictor.enabled
    )

    # Determine the symbol (if any) and entry price
    symbol = ""
    if config.data.csv is not None:
        symbol = config.data.csv.stem.upper()
    elif config.data.ticker is not None:
        symbol = config.data.ticker.upper()
    elif config.data.chart is not None:
        chart_d = config.data.chart
        symbol = f"{chart_d.get('exchange', '')}:{chart_d.get('ticker', '')}"
    entry_price = bars[-1].close

    # Merge
    trade_signal = merge(
        adj, book, kronos,
        symbol=symbol, entry_price=entry_price,
    )

    # Stamp the timestamp
    ts_unix = int(time.time()) if config.as_of_unix is None else config.as_of_unix
    trade_signal = TradeSignal(
        action=trade_signal.action,
        is_decide_no=trade_signal.is_decide_no,
        decide_no_reasons=trade_signal.decide_no_reasons,
        symbol=trade_signal.symbol,
        qty=trade_signal.qty,
        order_type=trade_signal.order_type,
        entry_price=trade_signal.entry_price,
        stop_loss=trade_signal.stop_loss,
        take_profit=trade_signal.take_profit,
        confidence=trade_signal.confidence,
        reason=trade_signal.reason,
        book_signal_ref=trade_signal.book_signal_ref,
        kronos_signal_ref=trade_signal.kronos_signal_ref,
        adjudicator_version=trade_signal.adjudicator_version,
        adjudicator_commit=trade_signal.adjudicator_commit,
        book_engine_version=trade_signal.book_engine_version,
        predictor_version=trade_signal.predictor_version,
        market_state_hash=trade_signal.market_state_hash,
        counterfactuals=trade_signal.counterfactuals,
        timestamp_unix=ts_unix,
        timestamp_iso=now_iso(ts_unix),
    )

    # Write to artifact store
    try:
        path = write_trade_signal(trade_signal, config.config_sha256)
    except OSError as e:
        print(f"artifact write error: {e}", file=sys.stderr)
        return EXIT_RUNTIME

    # Emit JSON to stdout
    payload = trade_signal.to_json_dict()
    payload["_artifact_path"] = str(path)
    print(json.dumps(payload, indent=2, default=str))

    return EXIT_OK


# ── explain ──────────────────────────────────────────────────────────


def _cmd_explain(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: rudra-intraday explain <hash>", file=sys.stderr)
        return EXIT_USAGE
    sha = args[0]
    text = explain_signal(sha)
    if text is None:
        print(f"no artifact found for hash {sha!r}", file=sys.stderr)
        return EXIT_RUNTIME
    print(text)
    return EXIT_OK


# ── verify ───────────────────────────────────────────────────────────


def _cmd_verify(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: rudra-intraday verify <hash>", file=sys.stderr)
        return EXIT_USAGE
    sha = args[0]
    from .artifact import read_trade_signal
    ts = read_trade_signal(sha)
    if ts is None:
        print(f"no artifact found for hash {sha!r}", file=sys.stderr)
        return EXIT_RUNTIME
    # Verification: re-canonicalize the JSON dict and confirm hash
    from .signal_types import canonical_hash
    payload = ts.to_json_dict()
    h = canonical_hash(payload)
    if h != sha:
        print(
            f"VERIFICATION FAILED: stored hash {sha!r} != "
            f"recomputed hash {h!r}",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    print(f"OK: artifact {sha[:16]}... verified")
    print(f"  action:     {ts.action.value}")
    print(f"  symbol:     {ts.symbol}")
    print(f"  confidence: {ts.confidence:.2f}")
    print(f"  reason:     {ts.reason}")
    return EXIT_OK


# ── predict ──────────────────────────────────────────────────────────


def _cmd_predict(args: list[str]) -> int:
    if len(args) != 1:
        print("usage: rudra-intraday predict <data.csv>", file=sys.stderr)
        return EXIT_USAGE
    if not kronos_available():
        print(
            "predict error: kronos package not installed; "
            "install with `pip install rudra-intraday-engine[kronos]`",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    csv_path = Path(args[0])
    try:
        bars = load_bars_from_csv(csv_path)
    except DataLoadError as e:
        print(f"data error: {e}", file=sys.stderr)
        return EXIT_DATA
    from .core.predictor import predict_kronos
    ks = predict_kronos(bars)
    if ks is None:
        print("predict: no signal (kronos call failed)", file=sys.stderr)
        return EXIT_RUNTIME
    payload = {
        "prediction": ks.prediction,
        "confidence": ks.confidence,
        "model_version": ks.model_version,
        "horizon_bars": ks.horizon_bars,
        "notes": ks.notes,
    }
    print(json.dumps(payload, indent=2))
    return EXIT_OK


# ── helpers ──────────────────────────────────────────────────────────


def _print_usage() -> None:
    print(
        """rudra-intraday — re-implements the "Mind Markets And Money" rules

Usage:
    rudra-intraday run <config.toml>       Run a strategy, emit TradeSignal JSON
    rudra-intraday explain <hash>          Render a past signal as text
    rudra-intraday verify <hash>           Re-canonicalize and assert equality
    rudra-intraday predict <data.csv>      One-shot Kronos inference (optional)

Options:
    -h, --help       Show this help
    -v, --version    Show version

State directory: $HOME/state/<config_sha256>/signals/
""",
        file=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))


def _main() -> int:
    """Console-script entry point. Wraps main(sys.argv).

    setup.py's `entry_points` calls this with no arguments; the
    real argv-parsing logic lives in `main(argv)`.
    """
    return main(sys.argv)
