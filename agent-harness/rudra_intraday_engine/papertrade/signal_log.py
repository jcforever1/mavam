"""Append-only JSONL signal log.

One file per day at $HOME/.local/state/mavam/paperlog/<YYYY-MM-DD>.jsonl.
Each line is a `PaperLogRecord` serialized as JSON.

Records are immutable. If a config change is made, the new config's
sha is part of the record, so old and new records can be distinguished.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_LOG_DIR = Path.home() / ".local" / "state" / "mavam" / "paperlog"


@dataclass(frozen=True)
class PaperLogRecord:
    """A single paper-trade signal record.

    Fields:
      ts_unix:    unix time when the signal was generated
      ts_iso:     ISO-8601 human-readable timestamp
      ticker:     e.g. "KO", "SPY"
      action:     "BUY" | "SELL" | "HOLD" (matches TradeSignal.action.value)
      entry_price:  close of the last bar at signal time
      stop_loss:   proposed stop
      take_profit: proposed target
      confidence:  0.0–1.0
      reason:      short human-readable string from the Adjudicator
      config_sha:  the sha256 of the config TOML that produced this signal
      is_decide_no: True if the Adjudicator vetoed the signal
      decide_no_reasons: list of reason strings
      source:      "run" | "replay" — distinguishes live vs backtest replay
    """

    ts_unix: int
    ts_iso: str
    ticker: str
    action: str
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    confidence: float
    reason: str
    config_sha: str
    is_decide_no: bool = False
    decide_no_reasons: List[str] = field(default_factory=list)
    source: str = "run"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "PaperLogRecord":
        d = json.loads(s)
        return PaperLogRecord(**d)


def _daily_path(log_dir: Path, day: str) -> Path:
    """day is 'YYYY-MM-DD'."""
    if not day or len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError(f"bad day string: {day!r}")
    return log_dir / f"{day}.jsonl"


def _day_for_unix(ts_unix: int) -> str:
    return datetime.fromtimestamp(ts_unix, tz=timezone.utc).strftime("%Y-%m-%d")


def append_record(
    record: PaperLogRecord,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> Path:
    """Append a record to the daily JSONL. Returns the file path written."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = _daily_path(log_dir, _day_for_unix(record.ts_unix))
    # O_APPEND is atomic for small writes (<PIPE_BUF) on POSIX.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        line = record.to_json() + "\n"
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def read_records(
    log_dir: Path = DEFAULT_LOG_DIR,
    since_day: Optional[str] = None,
    until_day: Optional[str] = None,
    ticker: Optional[str] = None,
) -> List[PaperLogRecord]:
    """Read records from the log directory, optionally filtered.

    Filters:
      since_day / until_day: inclusive YYYY-MM-DD bounds.
      ticker: case-insensitive ticker match.
    """
    if not log_dir.exists():
        return []

    files = sorted(log_dir.glob("*.jsonl"))
    out: List[PaperLogRecord] = []
    for path in files:
        day = path.stem
        if since_day is not None and day < since_day:
            continue
        if until_day is not None and day > until_day:
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = PaperLogRecord.from_json(line)
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
                    if ticker is not None and rec.ticker.upper() != ticker.upper():
                        continue
                    out.append(rec)
        except OSError:
            continue
    return out


__all__ = [
    "DEFAULT_LOG_DIR",
    "PaperLogRecord",
    "append_record",
    "read_records",
]
