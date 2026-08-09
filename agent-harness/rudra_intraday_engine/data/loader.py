"""Data loader — CSV → list[Bar].

For v1, we support CSV files with a 6-column header:
  timestamp,open,high,low,close,volume

The timestamp column is auto-detected:
  - If the value parses as an integer (10+ digits), it's treated as
    Unix epoch seconds.
  - Otherwise, it's parsed as ISO 8601 (e.g. "2026-08-10T09:30:00").

Lines starting with '#' are treated as comments and skipped.

The loader is the only I/O surface in the engine (besides the
artifact store). It's a pure transformation: file → list[Bar].
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from ..core.profile import Bar


class DataLoadError(ValueError):
    """Raised when a data file cannot be parsed."""


def _parse_timestamp(raw: str) -> int:
    """Parse a timestamp string as ISO 8601 or Unix epoch seconds."""
    raw = raw.strip()
    if not raw:
        raise DataLoadError("empty timestamp")
    # Try Unix epoch first if it looks like a positive integer
    if raw.lstrip("-").isdigit():
        return int(raw)
    # Fall back to ISO 8601
    try:
        # Tolerate trailing 'Z' (UTC marker)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError as e:
        raise DataLoadError(
            f"cannot parse timestamp {raw!r}: not a unix integer and "
            f"not ISO 8601 ({e})"
        ) from e


def load_bars_from_csv(path: str | Path) -> list[Bar]:
    """Load OHLCV bars from a CSV file.

    The CSV must have a header row with at minimum these columns:
      timestamp, open, high, low, close, volume

    Returns bars sorted by timestamp ascending. The book engine
    requires monotonically increasing timestamps.
    """
    p = Path(path)
    if not p.exists():
        raise DataLoadError(f"CSV not found: {p}")
    if not p.is_file():
        raise DataLoadError(f"CSV path is not a file: {p}")

    bars: list[Bar] = []
    with p.open("r", newline="", encoding="utf-8") as f:
        # Skip comment lines starting with '#'
        rows = (line for line in f if not line.lstrip().startswith("#"))
        reader = csv.DictReader(rows)
        if reader.fieldnames is None:
            raise DataLoadError(f"CSV has no header: {p}")
        # Normalize column names (strip whitespace, lowercase)
        reader.fieldnames = [n.strip().lower() for n in reader.fieldnames]
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise DataLoadError(
                f"CSV missing required columns: {sorted(missing)}; "
                f"got {reader.fieldnames}"
            )

        for line_no, row in enumerate(reader, start=2):  # +1 for header
            try:
                ts = _parse_timestamp(row["timestamp"])
                bars.append(Bar(
                    timestamp_unix=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
            except (ValueError, DataLoadError) as e:
                raise DataLoadError(
                    f"CSV parse error at line {line_no} of {p}: {e}"
                ) from e

    bars.sort(key=lambda b: b.timestamp_unix)
    if not bars:
        raise DataLoadError(f"CSV has no data rows: {p}")
    return bars


__all__ = ["load_bars_from_csv", "DataLoadError"]
