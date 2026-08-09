"""Safe `when`-expression evaluator for Adjudicator TOML.

The Adjudicator's `[[merge.rules]]` have a `when` string like:

    when = "book.action == 'BUY' and kronos.confidence >= 0.70"

We need to evaluate these safely — no `eval()`, no function calls,
no index access, no surprises. We use Python's `ast` module to parse
the expression and walk the tree, rejecting anything outside the
allowlist.

Allowed:
  - String/number/bool literals
  - Attribute access on the names `book` and `kronos`
  - Comparisons: ==, !=, >=, <=, >, <
  - Boolean: and, or, not
  - Parenthesized sub-expressions

Anything else is a `ValueError` at load time.
"""

from __future__ import annotations

import ast
from typing import Any, Mapping


# Allowed root names (the only variables the expression can reference)
ALLOWED_ROOTS = frozenset({"book", "kronos"})

# Node types we accept
_ALLOWED_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Attribute,
    ast.Compare,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Load,
    # Comparison operator nodes (Eq, NotEq, etc.) — also validated
    # separately via _ALLOWED_COMPARE_OPS / _ALLOWED_BOOL_OPS /
    # _ALLOWED_UNARY_OPS.
    ast.Eq, ast.NotEq,
    ast.Gt, ast.Lt, ast.GtE, ast.LtE,
    ast.And, ast.Or,
    ast.Not,
)

_ALLOWED_COMPARE_OPS = (
    ast.Eq, ast.NotEq,
    ast.Gt, ast.Lt, ast.GtE, ast.LtE,
)

_ALLOWED_BOOL_OPS = (ast.And, ast.Or)

_ALLOWED_UNARY_OPS = (ast.Not,)


class ExpressionError(ValueError):
    """Raised when a `when` expression is malformed or references
    anything outside the allowlist."""


def _validate(tree: ast.AST) -> None:
    """Walk the AST and reject anything outside the allowlist."""
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"disallowed expression node: {type(node).__name__}"
            )
        if isinstance(node, ast.Name) and node.id not in ALLOWED_ROOTS:
            raise ExpressionError(
                f"unknown variable: {node.id!r}; only 'book' and 'kronos' allowed"
            )
        if isinstance(node, ast.Attribute):
            # Walk back to the root and ensure it's an allowed name
            root: ast.AST = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if not isinstance(root, ast.Name) or root.id not in ALLOWED_ROOTS:
                raise ExpressionError(
                    f"attribute root must be 'book' or 'kronos', got "
                    f"{ast.dump(root)}"
                )
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, _ALLOWED_COMPARE_OPS):
                    raise ExpressionError(
                        f"unsupported comparison operator: {type(op).__name__}"
                    )
        if isinstance(node, ast.BoolOp) and not isinstance(node.op, _ALLOWED_BOOL_OPS):
            raise ExpressionError(
                f"unsupported boolean operator: {type(node.op).__name__}"
            )
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, _ALLOWED_UNARY_OPS):
            raise ExpressionError(
                f"unsupported unary operator: {type(node.op).__name__}"
            )


def _eval(node: ast.AST, ctx: Mapping[str, Any]) -> Any:
    """Evaluate an AST node against the context."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        try:
            return ctx[node.id]
        except KeyError as e:
            raise ExpressionError(
                f"variable {node.id!r} not in context"
            ) from e
    if isinstance(node, ast.Attribute):
        obj = _eval(node.value, ctx)
        return getattr(obj, node.attr)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not operand
    raise ExpressionError(f"unsupported node: {type(node).__name__}")


def _compare(op: ast.AST, left: Any, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.LtE):
        return left <= right
    raise ExpressionError(f"unsupported comparison: {type(op).__name__}")


def evaluate_when(when_str: str, ctx: Mapping[str, Any]) -> bool:
    """Parse and evaluate a `when` expression against `ctx`.

    `ctx` must contain the keys 'book' and/or 'kronos', each mapping to
    an object with attribute access. Returns the boolean result.

    Raises ExpressionError on syntax or allowlist violations.
    """
    if not isinstance(when_str, str) or not when_str.strip():
        raise ExpressionError("'when' must be a non-empty string")
    try:
        tree = ast.parse(when_str.strip(), mode="eval")
    except SyntaxError as e:
        raise ExpressionError(
            f"invalid 'when' expression: {when_str!r}: {e}"
        ) from e
    _validate(tree)
    return bool(_eval(tree, ctx))


__all__ = ["evaluate_when", "ExpressionError", "ALLOWED_ROOTS"]
