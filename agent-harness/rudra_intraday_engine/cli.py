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
    if cmd == "paper":
        return _cmd_paper(rest)
    if cmd == "stream":
        return _cmd_stream(rest)
    if cmd == "install":
        return _cmd_install(rest)
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
        elif config.data.desktop is not None:
            # tv CLI (tradingview-mcp npm package): rich CDP bridge to
            # the user's local TradingView Desktop. Replaces pinchtab
            # as the recommended path. Requires:
            #   npm install -g tradingview-mcp
            from .data import (
                DesktopConfig,
                fetch_desktop_bars,
                tv_cli_available,
                tv_desktop_reachable,
            )
            d = config.data.desktop
            desktop_config = DesktopConfig(
                ticker=d.get("ticker", ""),
                timeframe=d.get("timeframe", "1D"),
                count=int(d.get("count", 0)),
                switch_chart=bool(d.get("switch_chart", False)),
                timeout=int(d.get("timeout", 30)),
            )
            if not tv_cli_available():
                print(
                    "data error: tv CLI not found; install with "
                    "`npm install -g tradingview-mcp`",
                    file=sys.stderr,
                )
                return EXIT_DATA
            if not tv_desktop_reachable():
                print(
                    "data error: TradingView Desktop not reachable via CDP. "
                    "Start your desktop app, or use `tv launch` to start it.",
                    file=sys.stderr,
                )
                return EXIT_DATA
            bars = fetch_desktop_bars(desktop_config, as_of_unix=config.as_of_unix)
            if bars is None:
                print(
                    f"data error: tv CLI returned no bars for "
                    f"{desktop_config.ticker or '(current chart)'}; "
                    f"check the chart is loaded",
                    file=sys.stderr,
                )
                return EXIT_DATA
        elif config.data.tradingview is not None:
            # Server-side TradingView (ToS-restricted, user has accepted)
            from .data import fetch_tradingview_bars, TradingViewServerConfig
            tv_d = config.data.tradingview
            tv_config = TradingViewServerConfig(
                ticker=tv_d.get("ticker", ""),
                exchange=tv_d.get("exchange", "NASDAQ"),
                interval=str(tv_d.get("interval", "5")),
                session_id=tv_d.get("session_id", ""),
                stale_after_seconds=int(tv_d.get("stale_after_seconds", 0)),
            )
            print(
                "WARNING: server-side TradingView data source — this "
                "violates TradingView's ToS and may result in account "
                "termination. You have explicitly accepted this risk.",
                file=sys.stderr,
            )
            bars = fetch_tradingview_bars(tv_config, as_of_unix=config.as_of_unix)
            if bars is None:
                print(
                    "data error: tradingview server fetch returned no bars; "
                    "is the sessionid valid? Check the network and TradingView's "
                    "current API endpoints.",
                    file=sys.stderr,
                )
                return EXIT_DATA
        else:
            # For v1, only CSV, ticker, chart, desktop, and tradingview are supported
            print(
                f"data error: no data source configured "
                f"(csv={config.data.csv}, ticker={config.data.ticker}, "
                f"chart={config.data.chart}, desktop={config.data.desktop}, "
                f"tradingview={config.data.tradingview}, "
                f"fixture={config.data.fixture})",
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
    elif config.data.tradingview is not None:
        tv_d = config.data.tradingview
        symbol = f"{tv_d.get('exchange', '')}:{tv_d.get('ticker', '')}"
    elif config.data.desktop is not None:
        d = config.data.desktop
        symbol = str(d.get("ticker", "")).upper()
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


# ── paper ────────────────────────────────────────────────────────────


def _cmd_paper(args: list[str]) -> int:
    """Dispatch `rudra-intraday paper <subcommand> ...`."""
    if not args:
        print(
            "usage: rudra-intraday paper <log|report|replay> ...",
            file=sys.stderr,
        )
        return EXIT_USAGE
    sub = args[0]
    rest = args[1:]
    if sub == "log":
        return _cmd_paper_log(rest)
    if sub == "report":
        return _cmd_paper_report(rest)
    if sub == "replay":
        return _cmd_paper_replay(rest)
    print(f"rudra-intraday paper: unknown subcommand {sub!r}", file=sys.stderr)
    return EXIT_USAGE


def _cmd_paper_log(args: list[str]) -> int:
    """Run a strategy and append a record to today's paper-trade log.

    `rudra-intraday paper log <config.toml>`
    """
    if len(args) != 1:
        print("usage: rudra-intraday paper log <config.toml>", file=sys.stderr)
        return EXIT_USAGE
    config_path = args[0]
    try:
        config = load_config_from_argv([config_path])
    except (ArgvError, TomlParseError, SchemaViolation, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # Determine ticker from config
    ticker = ""
    if config.data.ticker is not None:
        ticker = config.data.ticker.upper()
    elif config.data.csv is not None:
        ticker = config.data.csv.stem.upper()
    elif config.data.chart is not None:
        ticker = str(config.data.chart.get("ticker", "")).upper()
    elif config.data.tradingview is not None:
        ticker = str(config.data.tradingview.get("ticker", "")).upper()
    elif config.data.desktop is not None:
        ticker = str(config.data.desktop.get("ticker", "")).upper()
    if not ticker:
        print(
            "config error: paper log needs a ticker (csv, ticker, chart, desktop, or tradingview)",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    # Reuse the run pipeline by calling _cmd_run, but capture the JSON output
    import io
    import contextlib
    buf = io.StringIO()
    rc = -1
    with contextlib.redirect_stdout(buf):
        rc = _cmd_run([config_path])
    if rc != EXIT_OK:
        print(buf.getvalue(), file=sys.stderr)
        return rc
    # Parse the entire JSON output (multi-line indented). The run
    # command writes one JSON object; the whole buffer IS the JSON.
    try:
        payload = json.loads(buf.getvalue().strip())
    except (json.JSONDecodeError, ValueError):
        print(
            "paper log: could not parse run output:\n"
            + buf.getvalue()[:500],
            file=sys.stderr,
        )
        return EXIT_RUNTIME

    from .papertrade import PaperLogRecord, append_record
    from .signal_types import now_iso
    ts_unix = int(payload.get("timestamp_unix", time.time()))
    rec = PaperLogRecord(
        ts_unix=ts_unix,
        ts_iso=payload.get("timestamp_iso", now_iso(ts_unix)),
        ticker=ticker,
        action=str(payload.get("action", "HOLD")),
        entry_price=float(payload.get("entry_price") or 0.0),
        stop_loss=payload.get("stop_loss"),
        take_profit=payload.get("take_profit"),
        confidence=float(payload.get("confidence", 0.0)),
        reason=str(payload.get("reason", "")),
        config_sha=str(payload.get("config_sha256", config.config_sha256)),
        is_decide_no=bool(payload.get("is_decide_no", False)),
        decide_no_reasons=list(payload.get("decide_no_reasons", [])),
        source="run",
    )
    path = append_record(rec)
    print(f"paper log: appended {ticker} {rec.action} @ {rec.entry_price} → {path}")
    return EXIT_OK


def _cmd_paper_report(args: list[str]) -> int:
    """Read the log and print realized P&L for a ticker.

    `rudra-intraday paper report <ticker> [--since 30d] [--max-hold 200]`
    """
    if not args:
        print("usage: rudra-intraday paper report <ticker> [--since Nd] [--max-hold N]", file=sys.stderr)
        return EXIT_USAGE
    ticker = args[0].upper()
    since_day = None
    max_hold = 200
    i = 1
    while i < len(args):
        if args[i] == "--since" and i + 1 < len(args):
            n_str = args[i + 1]
            if n_str.endswith("d"):
                days = int(n_str[:-1])
                from datetime import datetime, timedelta, timezone
                cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
                since_day = cutoff.strftime("%Y-%m-%d")
            i += 2
        elif args[i] == "--max-hold" and i + 1 < len(args):
            max_hold = int(args[i + 1])
            i += 2
        else:
            i += 1

    from .papertrade import read_records, compute_realized_pnl
    from .data import YFinanceConfig, fetch_yfinance_bars
    records = read_records(since_day=since_day, ticker=ticker)
    if not records:
        print(f"paper report: no records for {ticker} (since {since_day or 'all'})")
        return EXIT_OK

    # Pull subsequent bars from yfinance (best-effort; if missing, skip)
    yf_config = YFinanceConfig(
        ticker=ticker, period="60d", interval="5m", stale_after_seconds=0,
    )
    bars = fetch_yfinance_bars(yf_config)
    subsequent: dict = {ticker: []}
    if bars and records:
        first_ts = records[0].ts_unix
        subsequent[ticker] = [b for b in bars if b.timestamp_unix > first_ts]

    pnl = compute_realized_pnl(records, subsequent, max_hold_bars=max_hold)
    print(
        f"paper report: {ticker} (since {since_day or 'all'}, max_hold={max_hold})"
    )
    print(f"  records:     {len(records)}")
    print(f"  closed:      {pnl.n_trades}")
    print(f"  open:        {pnl.n_open}  (waiting for forward data)")
    print(f"  skipped:     {pnl.skipped}  (HOLD/decide_no records)")
    print(f"  total PnL:   ${pnl.total_pnl:+.2f}")
    print(f"  avg PnL:     ${pnl.avg_pnl:+.2f}")
    if pnl.closed:
        print(f"  win rate:    {pnl.win_rate * 100:.1f}%")
        by_reason: dict = {}
        for t in pnl.closed:
            by_reason.setdefault(t.exit_reason, 0)
            by_reason[t.exit_reason] += 1
        print(f"  exits:       " + ", ".join(
            f"{k}={v}" for k, v in sorted(by_reason.items())
        ))
        avg_hold = sum(t.bars_held for t in pnl.closed) / len(pnl.closed)
        print(f"  avg hold:    {avg_hold:.1f} bars")
    if pnl.n_open and not pnl.closed:
        print("  note: open trades will close as forward bars arrive")
    return EXIT_OK


def _cmd_paper_replay(args: list[str]) -> int:
    """Replay a strategy historically and report the realized P&L.

    `rudra-intraday paper replay <ticker> --config <config.toml>
                                 [--period 60d] [--max-hold 200]`
    """
    ticker = ""
    config_path = ""
    period = "60d"
    max_hold = 200
    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] == "--period" and i + 1 < len(args):
            period = args[i + 1]
            i += 2
        elif args[i] == "--max-hold" and i + 1 < len(args):
            max_hold = int(args[i + 1])
            i += 2
        else:
            if not ticker:
                ticker = args[i].upper()
            i += 1
    if not ticker or not config_path:
        print(
            "usage: rudra-intraday paper replay <ticker> --config <config.toml> "
            "[--period 60d] [--max-hold 200]",
            file=sys.stderr,
        )
        return EXIT_USAGE

    from .papertrade import replay_historical_signals
    records, pnl = replay_historical_signals(
        ticker, config_path=config_path, period=period, max_hold_bars=max_hold,
    )
    print(
        f"paper replay: {ticker} over {period}, config {config_path}, max_hold={max_hold}"
    )
    print(f"  signals:     {len(records)}")
    print(f"  closed:      {pnl.n_trades}")
    print(f"  skipped:     {pnl.skipped}")
    print(f"  total PnL:   ${pnl.total_pnl:+.2f}")
    print(f"  avg PnL:     ${pnl.avg_pnl:+.2f}")
    print(f"  win rate:    {pnl.win_rate * 100:.1f}%")
    if pnl.closed:
        avg_hold = sum(t.bars_held for t in pnl.closed) / len(pnl.closed)
        print(f"  avg hold:    {avg_hold:.1f} bars")
        # Show the last 5 trades
        print("  last 5 trades:")
        for t in pnl.closed[-5:]:
            print(
                f"    {t.record.ts_iso[:10]} "
                f"{t.record.action} @ {t.record.entry_price:.2f} "
                f"→ exit {t.exit_reason} @ {t.exit_price:.2f} "
                f"PnL ${t.pnl:+.2f} ({t.bars_held} bars)"
            )
    return EXIT_OK


# ── helpers ──────────────────────────────────────────────────────────


def _cmd_install(args: list[str]) -> int:
    """Install an indicator onto the TradingView chart.

    `rudra-intraday install orderflow`
    """
    if not args:
        print(
            "usage: rudra-intraday install <indicator>\n"
            "  indicators: orderflow",
            file=sys.stderr,
        )
        return EXIT_USAGE
    sub = args[0]
    if sub == "orderflow":
        from .data import install_orderflow
        result = install_orderflow()
        if not result["tv_reachable"]:
            for step in result["manual_steps"]:
                print(f"  {step}", file=sys.stderr)
            return EXIT_RUNTIME
        print(f"orderflow install:")
        print(f"  tv reachable:   {result['tv_reachable']}")
        print(f"  pine file:      {result['pine_file']}")
        print(f"  lines set:      {result['lines_set']}")
        print(f"  saved:          {result['saved']}")
        print(f"  on chart:       {result['added_to_chart']}  (manual click required)")
        print()
        for step in result["manual_steps"]:
            print(step)
        return EXIT_OK
    print(f"install: unknown indicator {sub!r}", file=sys.stderr)
    return EXIT_USAGE


def _cmd_stream(args: list[str]) -> int:
    """Stream live data from the user's TradingView Desktop.

    `rudra-intraday stream <sub> [-i MS] [--pretty] [--output PATH] [--max-records N]`

    Subs: quote, bars, values, lines, labels, tables, all
    Default output: JSONL to stdout (one record per line).
    Use --pretty for human-readable indented output.
    Use --output to write to a file.
    Use --max-records to stop after N records (default: run forever).
    Use -i / --interval to set poll interval in ms.
    """
    from .data import (
        VALID_STREAMS,
        stream_tv,
        stream_tv_to_file,
        tv_cli_available,
    )

    if not args:
        print(
            f"usage: rudra-intraday stream <sub> [options]\n"
            f"  subs: {', '.join(VALID_STREAMS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    sub = args[0]
    if sub not in VALID_STREAMS:
        print(
            f"stream: invalid sub {sub!r}; must be one of {VALID_STREAMS}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    interval_ms = 500
    pretty = False
    output_path = None
    max_records = None
    i = 1
    while i < len(args):
        a = args[i]
        if a in ("-i", "--interval") and i + 1 < len(args):
            interval_ms = int(args[i + 1])
            i += 2
        elif a == "--pretty":
            pretty = True
            i += 1
        elif a == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif a == "--max-records" and i + 1 < len(args):
            max_records = int(args[i + 1])
            i += 2
        elif a in ("-h", "--help"):
            print(
                f"usage: rudra-intraday stream <sub> [options]\n"
                f"  subs: {', '.join(VALID_STREAMS)}\n"
                f"  -i MS        poll interval in ms (default 500)\n"
                f"  --pretty     indented human output (default JSONL)\n"
                f"  --output P   write to file (default stdout)\n"
                f"  --max-records N   stop after N records\n",
                file=sys.stderr,
            )
            return EXIT_OK
        else:
            i += 1

    if not tv_cli_available():
        print(
            "stream error: tv CLI not found; install with "
            "`npm install -g tradingview-mcp`",
            file=sys.stderr,
        )
        return EXIT_RUNTIME

    if output_path:
        # File mode
        n = stream_tv_to_file(
            sub,
            interval_ms=interval_ms,
            output_path=output_path,
            max_records=max_records,
        )
        print(f"stream: wrote {n} records to {output_path}", file=sys.stderr)
        return EXIT_OK

    # stdout mode
    count = 0
    try:
        for record in stream_tv(sub, interval_ms=interval_ms):
            if pretty:
                print(json.dumps(record, indent=2))
            else:
                print(json.dumps(record, separators=(",", ":")))
            sys.stdout.flush()
            count += 1
            if max_records is not None and count >= max_records:
                break
    except KeyboardInterrupt:
        print(f"\nstream: stopped after {count} records", file=sys.stderr)
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
    rudra-intraday paper log <config>      Run + append a paper-trade record
    rudra-intraday paper report <ticker>   Compute realized P&L from the log
    rudra-intraday paper replay <ticker>   Replay a strategy historically
                                           --config <config.toml>
    rudra-intraday stream <sub>            Live JSONL from TradingView Desktop
                                           subs: quote, bars, values, lines,
                                                 labels, tables, all

Options:
    -h, --help       Show this help
    -v, --version    Show version

State directory: $HOME/state/<config_sha256>/signals/
Paper-trade log: $HOME/.local/state/mavam/paperlog/
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
