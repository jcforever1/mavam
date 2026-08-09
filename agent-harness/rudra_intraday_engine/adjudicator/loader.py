"""Adjudicator TOML loader.

Reads a TOML file from disk and produces an `Adjudicator` instance.
Wraps the `tomllib` stdlib module (Python 3.11+). Re-validates the
`when` expressions at load time so a bad expression fails fast
rather than at first match attempt.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping

from .expression import ExpressionError, evaluate_when
from .schema import Adjudicator, AdjudicatorError


def load_adjudicator(path: str | Path) -> Adjudicator:
    """Load an Adjudicator TOML from a file path.

    Raises:
        FileNotFoundError: if the file doesn't exist
        AdjudicatorError: if the TOML is malformed or violates the schema
        ExpressionError: if any `when` expression is invalid
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Adjudicator TOML not found: {p}")
    if not p.is_file():
        raise AdjudicatorError(f"Adjudicator path is not a file: {p}")

    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise AdjudicatorError(f"invalid TOML in {p}: {e}") from e

    if not isinstance(data, Mapping):
        raise AdjudicatorError(
            f"Adjudicator TOML must be a table at the top level, got {type(data).__name__}"
        )

    # The top-level table has a single [adjudicator] key (since the file
    # is named after the engine's policy layer). Allow either: a bare
    # table with the keys, or a nested [adjudicator] table.
    if "adjudicator" in data and isinstance(data["adjudicator"], Mapping):
        adj_dict = data["adjudicator"]
    else:
        adj_dict = data

    adj = Adjudicator.from_toml_dict(adj_dict)

    # Validate every `when` expression at load time
    for i, rule in enumerate(adj.merge_rules):
        try:
            # Try to parse without evaluating (no context needed for parse)
            import ast
            ast.parse(rule.when.strip(), mode="eval")
        except SyntaxError as e:
            raise ExpressionError(
                f"merge_rules[{i}].when is not valid Python: {e}"
            ) from e

    return adj


__all__ = ["load_adjudicator"]
