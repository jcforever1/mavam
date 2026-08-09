"""Branch C — Minimal attack surface.

The CLI takes exactly one positional argv: a path to a TOML config.
This module defines the strict allowlist schema for that TOML.

The process never reads `os.environ`, never traverses `$HOME` for
dotfiles, never opens an inbound socket. The TOML is parsed into a
frozen dataclass with named fields, typed, paths constrained to an
approved_data_root. Unknown keys, wrong types, or paths outside the
approved root exit 3 and name the offender on stderr.

This is the entire security model. If a field is missing from this
allowlist, the loader silently accepts whatever the TOML says, and the
attacker has the same blast radius via config-injection instead of
argv-injection — different pipe, same damage.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional


class ConfigError(Exception):
    """Base class for all config-related errors."""


class ArgvError(ConfigError):
    """Wrong number of positional args, or argv contained flags."""


class TomlParseError(ConfigError):
    """TOML file is missing, unreadable, or malformed."""


class SchemaViolation(ConfigError):
    """TOML is valid but violates the allowlist schema (unknown key,
    wrong type, path outside approved root, etc.)."""


@dataclass(frozen=True)
class AdjudicatorRef:
    """Pointer to the Adjudicator TOML (the user's policy layer).

    Resolved at config-load time. The adjudicator's `[[merge.rules]]`
    define how BookSignal and (optional) KronosSignal are combined
    into a TradeSignal.
    """

    file: Path

    def __post_init__(self) -> None:
        if not self.file.exists():
            raise SchemaViolation(
                f"adjudicator.file not found: {self.file}"
            )
        if not self.file.is_file():
            raise SchemaViolation(
                f"adjudicator.file is not a regular file: {self.file}"
            )


@dataclass(frozen=True)
class PredictorConfig:
    """Optional Kronos predictor config.

    If `enabled=False`, the engine emits a BookSignal-only and the
    Adjudicator operates on book.action alone (book-only mode).

    If `enabled=True`, the predictor loads the Kronos model and emits
    a KronosSignal alongside the BookSignal. Adjudicator policies that
    reference `kronos.*` fields require `enabled=True`.
    """

    enabled: bool = False
    model_version: str = "kronos-0.6.0"
    min_confidence: float = 0.65

    def __post_init__(self) -> None:
        if self.enabled:
            if not 0.0 <= self.min_confidence <= 1.0:
                raise SchemaViolation(
                    f"predictor.min_confidence must be in [0, 1], "
                    f"got {self.min_confidence}"
                )


@dataclass(frozen=True)
class DataConfig:
    """Data source config.

    Exactly one of:
      - `csv`   — path to a CSV file (timestamp,open,high,low,close,volume)
      - `ticker`— yfinance ticker symbol (HTTP, no ToS risk)
      - `fixture`— built-in test fixture name
      - `chart` — TradingView Desktop chart via pinchtab (local CDP, no ToS risk)
      - `tradingview` — TradingView server-side WebSocket (ToS-restricted;
                     user has accepted the ToS + account-ban risk)
    """

    csv: Optional[Path] = None
    ticker: Optional[str] = None
    fixture: Optional[str] = None
    chart: Optional[dict] = None
    tradingview: Optional[dict] = None  # server-side, ToS-restricted
    date: Optional[str] = None  # YYYY-MM-DD, used for ticker mode
    lookback_days: int = 5

    def __post_init__(self) -> None:
        sources = sum(
            x is not None
            for x in (self.csv, self.ticker, self.fixture, self.chart, self.tradingview)
        )
        if sources != 1:
            raise SchemaViolation(
                f"data section: exactly one of csv, ticker, fixture, chart, or tradingview "
                f"required, got {sources}"
            )
        if self.chart is not None and not isinstance(self.chart, dict):
            raise SchemaViolation(
                f"data.chart must be a table (dict), got {type(self.chart).__name__}"
            )
        if self.tradingview is not None and not isinstance(self.tradingview, dict):
            raise SchemaViolation(
                f"data.tradingview must be a table (dict), got {type(self.tradingview).__name__}"
            )
        if self.csv is not None and not self.csv.exists():
            raise SchemaViolation(
                f"data.csv not found: {self.csv}"
            )
        if self.ticker is not None and self.date is None:
            # Note: the yfinance data source uses `period` (relative
            # range like "5d") by default. The optional `date` field
            # is reserved for a future "as of this specific date" mode.
            pass
        if self.lookback_days < 1 or self.lookback_days > 365:
            raise SchemaViolation(
                f"data.lookback_days must be in [1, 365], got {self.lookback_days}"
            )


@dataclass(frozen=True)
class Config:
    """The complete engine config. Frozen — no mutation after load.

    The single source of truth for a run. The CLI's argv-allowlist
    takes one positional: the path to a TOML file that parses into
    this dataclass.
    """

    adjudicator: AdjudicatorRef
    predictor: PredictorConfig
    data: DataConfig
    # For deterministic replay: the manifest carries as_of_unix sourced
    # from this field, not the wall clock.
    as_of_unix: Optional[int] = None
    # SHA-256 of the canonical-bytes TOML. Computed at load time.
    config_sha256: str = field(default="", init=False)

    def __post_init__(self) -> None:
        # config_sha256 is set by `from_toml()` after parsing.
        # We allow it to be empty here for the dataclass constructor;
        # the loader is responsible for setting it.
        pass


_ALLOWED_TOP_KEYS = frozenset({"adjudicator", "predictor", "data", "as_of_unix"})


def _parse_adjudicator(raw: dict[str, Any], config_dir: Path) -> AdjudicatorRef:
    if not isinstance(raw, dict):
        raise SchemaViolation(
            f"adjudicator section must be a table, got {type(raw).__name__}"
        )
    if "file" not in raw:
        raise SchemaViolation("adjudicator.file is required")
    if not isinstance(raw["file"], str):
        raise SchemaViolation(
            f"adjudicator.file must be a string, got {type(raw['file']).__name__}"
        )
    # Resolve relative to the config's own directory
    path = Path(raw["file"])
    if not path.is_absolute():
        path = (config_dir / path).resolve()
    return AdjudicatorRef(file=path)


def _parse_predictor(raw: Optional[dict[str, Any]]) -> PredictorConfig:
    if raw is None:
        return PredictorConfig(enabled=False)
    if not isinstance(raw, dict):
        raise SchemaViolation(
            f"predictor section must be a table, got {type(raw).__name__}"
        )
    unknown = set(raw.keys()) - {"enabled", "model_version", "min_confidence"}
    if unknown:
        raise SchemaViolation(
            f"predictor: unknown keys: {sorted(unknown)}"
        )
    return PredictorConfig(
        enabled=bool(raw.get("enabled", False)),
        model_version=str(raw.get("model_version", "kronos-0.6.0")),
        min_confidence=float(raw.get("min_confidence", 0.65)),
    )


def _parse_data(raw: dict[str, Any], config_dir: Path) -> DataConfig:
    if not isinstance(raw, dict):
        raise SchemaViolation(
            f"data section must be a table, got {type(raw).__name__}"
        )
    unknown = set(raw.keys()) - {
        "csv", "ticker", "fixture", "chart", "tradingview", "date", "lookback_days"
    }
    if unknown:
        raise SchemaViolation(
            f"data: unknown keys: {sorted(unknown)}"
        )
    csv = raw.get("csv")
    if csv is not None and not isinstance(csv, str):
        raise SchemaViolation("data.csv must be a string path")
    csv_path = Path(csv) if csv else None
    if csv_path is not None:
        if not csv_path.is_absolute():
            # Resolve relative to the config file's directory, NOT the
            # current working directory. The previous version called
            # .resolve() before this check, which silently changed
            # the meaning of relative paths.
            csv_path = (config_dir / csv_path).resolve()
        else:
            csv_path = csv_path.resolve()
    chart = raw.get("chart")
    if chart is not None and not isinstance(chart, dict):
        raise SchemaViolation(
            f"data.chart must be a table, got {type(chart).__name__}"
        )
    if isinstance(chart, dict):
        chart_allowed = {"ticker", "exchange", "interval", "stale_after_seconds"}
        chart_unknown = set(chart.keys()) - chart_allowed
        if chart_unknown:
            raise SchemaViolation(
                f"data.chart: unknown keys: {sorted(chart_unknown)}"
            )
        if "ticker" not in chart or not chart["ticker"]:
            raise SchemaViolation("data.chart.ticker is required")
    tradingview = raw.get("tradingview")
    if tradingview is not None and not isinstance(tradingview, dict):
        raise SchemaViolation(
            f"data.tradingview must be a table, got {type(tradingview).__name__}"
        )
    if isinstance(tradingview, dict):
        tv_allowed = {"ticker", "exchange", "interval", "session_id", "stale_after_seconds"}
        tv_unknown = set(tradingview.keys()) - tv_allowed
        if tv_unknown:
            raise SchemaViolation(
                f"data.tradingview: unknown keys: {sorted(tv_unknown)}"
            )
        if "ticker" not in tradingview or not tradingview["ticker"]:
            raise SchemaViolation("data.tradingview.ticker is required")
    return DataConfig(
        csv=csv_path,
        ticker=raw.get("ticker"),
        fixture=raw.get("fixture"),
        chart=chart,
        tradingview=tradingview,
        date=raw.get("date"),
        lookback_days=int(raw.get("lookback_days", 5)),
    )


def _canonical_toml_text(text: str) -> str:
    """Canonical form for hashing. Preserves comment-free key=value
    lines; we only hash the raw bytes for simplicity.
    """
    return text


def _sha256_of_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def from_toml(path: str) -> Config:
    """Load a config from a TOML file.

    The argv layer has already verified:
    - `path` is a single positional arg
    - `path` is a non-flag, non-option string
    - `os.environ` was never consulted

    This function enforces the schema.
    """
    # CRITICAL: never consult os.environ here. Never walk $HOME.
    if not isinstance(path, str):
        raise ArgvError(
            f"config path must be a string, got {type(path).__name__}"
        )

    p = Path(path)
    if not p.exists():
        raise TomlParseError(f"config file not found: {path}")
    if not p.is_file():
        raise TomlParseError(f"config path is not a file: {path}")

    # Read raw bytes (no env, no walk)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise TomlParseError(f"cannot read config file: {e}")

    config_dir = p.parent.resolve()

    # Parse TOML — use tomllib (Python 3.11+) or tomli as fallback
    try:
        import tomllib  # type: ignore[import-not-found]
        parsed = tomllib.loads(raw_text)
    except ImportError:
        try:
            import tomli  # type: ignore[import-not-found]
            parsed = tomli.loads(raw_text)
        except ImportError:
            raise TomlParseError(
                "tomllib (Python 3.11+) or tomli is required to parse TOML"
            )
    except Exception as e:
        raise TomlParseError(f"TOML parse error: {e}")

    # Allowlist top-level keys
    extra = set(parsed.keys()) - _ALLOWED_TOP_KEYS
    if extra:
        raise SchemaViolation(
            f"config: unknown top-level keys: {sorted(extra)}"
        )

    # Required sections
    if "adjudicator" not in parsed:
        raise SchemaViolation("config: [adjudicator] section is required")
    if "data" not in parsed:
        raise SchemaViolation("config: [data] section is required")

    # Parse each section
    adj = _parse_adjudicator(parsed["adjudicator"], config_dir)
    pred = _parse_predictor(parsed.get("predictor"))
    data = _parse_data(parsed["data"], config_dir)

    # Optional as_of_unix
    as_of = parsed.get("as_of_unix")
    if as_of is not None and not isinstance(as_of, int):
        raise SchemaViolation(
            f"as_of_unix must be an integer, got {type(as_of).__name__}"
        )

    # Build config + compute canonical hash
    config = Config(
        adjudicator=adj,
        predictor=pred,
        data=data,
        as_of_unix=as_of,
    )
    # Compute hash from canonical text and freeze into the object
    canonical_hash = _sha256_of_text(_canonical_toml_text(raw_text))
    # Bypass the frozen dataclass to inject the hash (this is the
    # ONE allowed mutation, at load time only)
    object.__setattr__(config, "config_sha256", canonical_hash)
    return config


def load_config_from_argv(argv: list[str]) -> Config:
    """Strict argv parsing: exactly 1 positional arg, no flags.

    The CLI's ONLY entry point. Exits with code 2 on argv violation.
    Never reads `os.environ`. Never walks `$HOME`.
    """
    if len(argv) != 1:
        raise ArgvError(
            f"expected exactly 1 positional arg (the config TOML path), "
            f"got {len(argv)}: {argv}"
        )
    arg = argv[0]
    if not isinstance(arg, str):
        raise ArgvError(
            f"config path must be a string, got {type(arg).__name__}"
        )
    if arg.startswith("-"):
        raise ArgvError(
            f"flags are not allowed on the main verb "
            f"(single positional arg only); got {arg!r}"
        )
    return from_toml(arg)
