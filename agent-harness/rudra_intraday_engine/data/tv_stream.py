"""Live TradingView Desktop stream via the `tv` CLI.

The `tv stream` family of commands emits JSONL records as the chart
changes in real time. This module wraps them as Python generators
that yield each parsed record as it arrives.

Streams available:
  - quote:   real-time price ticks (OHLCV per bar, 300ms default)
  - bars:    last bar updates on price change (500ms default)
  - values:  indicator values from the data window (500ms default)
  - lines:   Pine Script line.new() price levels
  - labels:  Pine Script label.new() annotations
  - tables:  Pine Script table.new() data
  - all:     all panes at once (multi-symbol monitoring)

This is the "live view" the user asked for. The CLI wires it as
`rudra-intraday stream <sub> [-i ms] [--pretty]`. Default output
is JSONL (one record per line); with --pretty, each record is
printed as an indented dict.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
from typing import Iterator, List, Optional

from .tradingview_source import DEFAULT_TV_BIN, _run_tv, tv_cli_available


VALID_STREAMS = ("quote", "bars", "values", "lines", "labels", "tables", "all")


def stream_tv(
    sub: str = "quote",
    *,
    interval_ms: int = 500,
    timeout: Optional[float] = None,
) -> Iterator[dict]:
    """Yield live records from `tv stream <sub>` as parsed JSON dicts.

    Args:
      sub:         one of VALID_STREAMS
      interval_ms: poll interval in milliseconds
      timeout:     if set, stop after this many seconds (None = run forever)

    Yields:
      Parsed JSON dict per record. Each record has a `_ts` field
      (local unix ms when the record was emitted) and a `_stream`
      field (the subcommand name).

    The generator cleans up the subprocess on close (including
      Ctrl+C / GeneratorExit).
    """
    if sub not in VALID_STREAMS:
        raise ValueError(f"invalid stream: {sub!r}; must be one of {VALID_STREAMS}")
    if not tv_cli_available():
        raise RuntimeError(
            "tv CLI not found; install with `npm install -g tradingview-mcp`"
        )

    args = [DEFAULT_TV_BIN, "stream", sub, "-i", str(interval_ms)]
    # bufsize=1 for line-buffered stdout; text mode for str I/O
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    import queue
    import threading

    q: "queue.Queue[str]" = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)  # sentinel

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    try:
        import time
        start = time.time()
        while True:
            # If the user-supplied timeout elapsed, stop
            if timeout is not None and (time.time() - start) > timeout:
                break
            # If the upstream process exited on its own, drain and stop
            if proc.poll() is not None and q.empty():
                break
            # Try to read with a short timeout so we can check conditions
            try:
                line = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                # sentinel — process closed stdout
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Skip non-JSON lines (e.g. the warning banner)
                continue
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def stream_tv_to_file(
    sub: str = "quote",
    *,
    interval_ms: int = 500,
    output_path: str,
    timeout: Optional[float] = None,
    max_records: Optional[int] = None,
) -> int:
    """Stream live records to a file (JSONL format).

    Returns the number of records written. Stops on timeout or
    max_records, whichever comes first.
    """
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for record in stream_tv(sub, interval_ms=interval_ms, timeout=timeout):
            f.write(json.dumps(record) + "\n")
            f.flush()
            count += 1
            if max_records is not None and count >= max_records:
                break
    return count


__all__ = [
    "VALID_STREAMS",
    "stream_tv",
    "stream_tv_to_file",
]
