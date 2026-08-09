"""Adjudicator package — the user's policy layer.

The Adjudicator is a versioned TOML file that the user (or their AI
agent) authors, forks, and shares. It is the only place where
BookSignal and KronosSignal get merged into a TradeSignal. The
merger is a pure function of (Adjudicator, BookSignal, KronosSignal).

This package:
- expression.py: safe `when`-expression evaluator
- schema.py: dataclass schema for the Adjudicator TOML
- loader.py: load + validate the TOML
- merger.py: (Adjudicator, signals) -> TradeSignal
"""

from .expression import ExpressionError, evaluate_when
from .loader import load_adjudicator
from .merger import merge
from .schema import (
    Adjudicator,
    AdjudicatorError,
    BookInputs,
    Fallback,
    KronosInputs,
    MergeRule,
    Risk,
)

__all__ = [
    "Adjudicator",
    "AdjudicatorError",
    "BookInputs",
    "ExpressionError",
    "Fallback",
    "KronosInputs",
    "MergeRule",
    "Risk",
    "evaluate_when",
    "load_adjudicator",
    "merge",
]
