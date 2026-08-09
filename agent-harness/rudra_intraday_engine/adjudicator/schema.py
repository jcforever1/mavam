"""Adjudicator TOML schema (dataclass-based).

A frozen-dataclass schema for the Adjudicator policy TOML. The loader
parses the TOML, validates against this schema, and produces an
`Adjudicator` instance that the merger consumes.

The schema is intentionally explicit: every field the user is allowed
to set is a named field here. Unknown keys in the TOML are rejected
at load time. This is the durable contract between the user and the
engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


class AdjudicatorError(ValueError):
    """Raised when the Adjudicator TOML violates the schema."""


# ── Sub-schemas ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BookInputs:
    """Validation rules for the incoming BookSignal."""

    required: bool = True
    min_rules_fired: int = 0
    required_rules: tuple[str, ...] = ()
    blocked_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class KronosInputs:
    """Validation rules for the incoming KronosSignal (optional)."""

    required: bool = False
    min_confidence: float = 0.0
    allowed_model_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fallback:
    """What to do when inputs are missing or validation fails."""

    when_no_kronos: str = "HOLD"
    when_no_book: str = "HOLD"
    when_validation_fails: str = "HOLD"


@dataclass(frozen=True)
class MergeRule:
    """One row in [[merge.rules]] — when this matches, emit this action."""

    when: str
    emit: str
    size_multiplier: float = 1.0
    reason: str = ""


@dataclass(frozen=True)
class Risk:
    """Position-sizing and trade constraints applied to the TradeSignal."""

    max_position_pct: float = 0.05
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.045
    max_daily_trades: int = 6


# ── Top-level Adjudicator ────────────────────────────────────────────


@dataclass(frozen=True)
class Adjudicator:
    """The user's policy for merging BookSignal and KronosSignal into a TradeSignal."""

    name: str
    version: str
    description: str = ""
    book: BookInputs = field(default_factory=BookInputs)
    kronos: KronosInputs = field(default_factory=KronosInputs)
    fallback: Fallback = field(default_factory=Fallback)
    merge_rules: tuple[MergeRule, ...] = ()
    risk: Risk = field(default_factory=Risk)

    @classmethod
    def from_toml_dict(cls, d: Mapping[str, Any]) -> "Adjudicator":
        """Parse a TOML dict (from tomllib.load) into an Adjudicator.

        Validates: every key must be in the allowlist, every value
        must be the right type. Unknown keys raise AdjudicatorError.
        """
        if not isinstance(d, Mapping):
            raise AdjudicatorError(
                f"Adjudicator must be a TOML table, got {type(d).__name__}"
            )

        # Allowlist: every key the user is allowed to set.
        _ALLOWED_TOP_KEYS = {
            "name", "version", "description",
            "book", "kronos", "fallback", "merge_rules", "risk",
        }
        unknown = set(d.keys()) - _ALLOWED_TOP_KEYS
        if unknown:
            raise AdjudicatorError(
                f"Adjudicator: unknown keys: {sorted(unknown)}; "
                f"allowed: {sorted(_ALLOWED_TOP_KEYS)}"
            )

        # Required fields
        name = _required_str(d, "name")
        version = _required_str(d, "version")

        # Optional fields with defaults
        description = _optional_str(d, "description", default="")
        book_d = d.get("book", {}) or {}
        kronos_d = d.get("kronos", {}) or {}
        fallback_d = d.get("fallback", {}) or {}
        merge_rules_d = d.get("merge_rules", []) or []
        risk_d = d.get("risk", {}) or {}

        if not isinstance(book_d, Mapping):
            raise AdjudicatorError(f"[adjudicator.book] must be a table, got {type(book_d).__name__}")
        if not isinstance(kronos_d, Mapping):
            raise AdjudicatorError(f"[adjudicator.kronos] must be a table, got {type(kronos_d).__name__}")
        if not isinstance(fallback_d, Mapping):
            raise AdjudicatorError(f"[adjudicator.fallback] must be a table, got {type(fallback_d).__name__}")
        if not isinstance(merge_rules_d, list):
            raise AdjudicatorError(f"[[adjudicator.merge_rules]] must be an array, got {type(merge_rules_d).__name__}")
        if not isinstance(risk_d, Mapping):
            raise AdjudicatorError(f"[adjudicator.risk] must be a table, got {type(risk_d).__name__}")

        book = _build_book_inputs(book_d)
        kronos = _build_kronos_inputs(kronos_d)
        fallback = _build_fallback(fallback_d)
        merge_rules = _build_merge_rules(merge_rules_d)
        risk = _build_risk(risk_d)

        return cls(
            name=name,
            version=version,
            description=description,
            book=book,
            kronos=kronos,
            fallback=fallback,
            merge_rules=merge_rules,
            risk=risk,
        )


# ── Sub-builders ────────────────────────────────────────────────────


def _build_book_inputs(d: Mapping[str, Any]) -> BookInputs:
    if not isinstance(d, Mapping):
        raise AdjudicatorError(f"[book] must be a table")
    allowed = {"required", "min_rules_fired", "required_rules", "blocked_rules"}
    for k in d:
        if k not in allowed:
            raise AdjudicatorError(f"unknown key in [book]: {k!r}")
    return BookInputs(
        required=_optional_bool(d, "required", default=True),
        min_rules_fired=_optional_int(d, "min_rules_fired", default=0),
        required_rules=tuple(_optional_list_str(d, "required_rules", default=[])),
        blocked_rules=tuple(_optional_list_str(d, "blocked_rules", default=[])),
    )


def _build_kronos_inputs(d: Mapping[str, Any]) -> KronosInputs:
    if not isinstance(d, Mapping):
        raise AdjudicatorError(f"[kronos] must be a table")
    allowed = {"required", "min_confidence", "allowed_model_versions"}
    for k in d:
        if k not in allowed:
            raise AdjudicatorError(f"unknown key in [kronos]: {k!r}")
    return KronosInputs(
        required=_optional_bool(d, "required", default=False),
        min_confidence=_optional_float(d, "min_confidence", default=0.0),
        allowed_model_versions=tuple(_optional_list_str(d, "allowed_model_versions", default=[])),
    )


def _build_fallback(d: Mapping[str, Any]) -> Fallback:
    if not isinstance(d, Mapping):
        raise AdjudicatorError(f"[fallback] must be a table")
    allowed = {"when_no_kronos", "when_no_book", "when_validation_fails"}
    for k in d:
        if k not in allowed:
            raise AdjudicatorError(f"unknown key in [fallback]: {k!r}")
    for fname in allowed:
        v = _optional_str(d, fname, default="HOLD")
        if v not in ("BUY", "SELL", "HOLD", "EXIT", "REDUCE"):
            raise AdjudicatorError(
                f"[fallback.{fname}] must be one of BUY/SELL/HOLD/EXIT/REDUCE, got {v!r}"
            )
    return Fallback(
        when_no_kronos=d.get("when_no_kronos", "HOLD"),
        when_no_book=d.get("when_no_book", "HOLD"),
        when_validation_fails=d.get("when_validation_fails", "HOLD"),
    )


def _build_merge_rules(items: list) -> tuple[MergeRule, ...]:
    if not isinstance(items, list):
        raise AdjudicatorError(f"[[merge.rules]] must be an array")
    rules: list[MergeRule] = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise AdjudicatorError(f"merge_rules[{i}] must be a table, got {type(item).__name__}")
        allowed = {"when", "emit", "size_multiplier", "reason"}
        for k in item:
            if k not in allowed:
                raise AdjudicatorError(f"unknown key in merge_rules[{i}]: {k!r}")
        when = _required_str(item, "when")
        emit = _required_str(item, "emit")
        if emit not in ("BUY", "SELL", "HOLD", "EXIT", "REDUCE"):
            raise AdjudicatorError(
                f"merge_rules[{i}].emit must be BUY/SELL/HOLD/EXIT/REDUCE, got {emit!r}"
            )
        rules.append(MergeRule(
            when=when,
            emit=emit,
            size_multiplier=_optional_float(item, "size_multiplier", default=1.0),
            reason=_optional_str(item, "reason", default=""),
        ))
    return tuple(rules)


def _build_risk(d: Mapping[str, Any]) -> Risk:
    if not isinstance(d, Mapping):
        raise AdjudicatorError(f"[risk] must be a table")
    allowed = {"max_position_pct", "stop_loss_pct", "take_profit_pct", "max_daily_trades"}
    for k in d:
        if k not in allowed:
            raise AdjudicatorError(f"unknown key in [risk]: {k!r}")
    max_pct = _optional_float(d, "max_position_pct", default=0.05)
    stop_pct = _optional_float(d, "stop_loss_pct", default=0.015)
    tp_pct = _optional_float(d, "take_profit_pct", default=0.045)
    if not 0.0 < max_pct <= 1.0:
        raise AdjudicatorError(f"risk.max_position_pct must be in (0, 1], got {max_pct}")
    if stop_pct < 0:
        raise AdjudicatorError(f"risk.stop_loss_pct must be >= 0, got {stop_pct}")
    if tp_pct < 0:
        raise AdjudicatorError(f"risk.take_profit_pct must be >= 0, got {tp_pct}")
    return Risk(
        max_position_pct=max_pct,
        stop_loss_pct=stop_pct,
        take_profit_pct=tp_pct,
        max_daily_trades=_optional_int(d, "max_daily_trades", default=6),
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _required_str(d: Mapping[str, Any], key: str) -> str:
    v = d.get(key)
    if v is None:
        raise AdjudicatorError(f"missing required key: {key!r}")
    if not isinstance(v, str):
        raise AdjudicatorError(f"{key!r} must be a string, got {type(v).__name__}")
    return v


def _optional_str(d: Mapping[str, Any], key: str, default: str = "") -> str:
    v = d.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise AdjudicatorError(f"{key!r} must be a string, got {type(v).__name__}")
    return v


def _optional_bool(d: Mapping[str, Any], key: str, default: bool) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise AdjudicatorError(f"{key!r} must be a bool, got {type(v).__name__}")
    return v


def _optional_int(d: Mapping[str, Any], key: str, default: int) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise AdjudicatorError(f"{key!r} must be an int, got {type(v).__name__}")
    return v


def _optional_float(d: Mapping[str, Any], key: str, default: float) -> float:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise AdjudicatorError(f"{key!r} must be a number, got {type(v).__name__}")
    return float(v)


def _optional_list_str(d: Mapping[str, Any], key: str, default: list) -> list:
    v = d.get(key, default)
    if v is None:
        return list(default)
    if not isinstance(v, list):
        raise AdjudicatorError(f"{key!r} must be an array, got {type(v).__name__}")
    for i, item in enumerate(v):
        if not isinstance(item, str):
            raise AdjudicatorError(
                f"{key!r}[{i}] must be a string, got {type(item).__name__}"
            )
    return list(v)


__all__ = [
    "Adjudicator",
    "AdjudicatorError",
    "BookInputs",
    "KronosInputs",
    "Fallback",
    "MergeRule",
    "Risk",
]
