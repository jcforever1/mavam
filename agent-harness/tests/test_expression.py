"""Tests for adjudicator/expression.py — the safe when-expression evaluator."""

from __future__ import annotations

import unittest

from rudra_intraday_engine.adjudicator.expression import (
    ALLOWED_ROOTS,
    ExpressionError,
    evaluate_when,
)


class _Obj:
    """Helper: a generic object with attributes for testing."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestEvaluateWhen(unittest.TestCase):

    def test_simple_equality(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        self.assertTrue(evaluate_when("book.action == 'BUY'", ctx))

    def test_simple_inequality(self):
        ctx = {"book": _Obj(action="HOLD"), "kronos": None}
        self.assertTrue(evaluate_when("book.action != 'BUY'", ctx))

    def test_numeric_comparison(self):
        ctx = {"book": _Obj(confidence=0.75), "kronos": None}
        self.assertTrue(evaluate_when("book.confidence >= 0.70", ctx))
        self.assertFalse(evaluate_when("book.confidence < 0.70", ctx))

    def test_and_combination(self):
        ctx = {"book": _Obj(action="BUY", confidence=0.75), "kronos": None}
        self.assertTrue(evaluate_when(
            "book.action == 'BUY' and book.confidence >= 0.70", ctx
        ))
        self.assertFalse(evaluate_when(
            "book.action == 'SELL' and book.confidence >= 0.70", ctx
        ))

    def test_or_combination(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        self.assertTrue(evaluate_when(
            "book.action == 'BUY' or book.action == 'SELL'", ctx
        ))

    def test_not(self):
        ctx = {"book": _Obj(action="HOLD"), "kronos": None}
        self.assertTrue(evaluate_when("not book.action == 'BUY'", ctx))
        self.assertFalse(evaluate_when("not book.action == 'HOLD'", ctx))

    def test_parentheses(self):
        ctx = {"book": _Obj(action="BUY", confidence=0.80), "kronos": None}
        self.assertTrue(evaluate_when(
            "(book.action == 'BUY' or book.action == 'SELL') and book.confidence >= 0.70",
            ctx
        ))

    def test_kronos_root(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": _Obj(confidence=0.80)}
        self.assertTrue(evaluate_when("kronos.confidence >= 0.70", ctx))

    def test_attribute_on_kronos(self):
        kronos = _Obj(prediction="UP", confidence=0.65)
        ctx = {"book": _Obj(action="BUY"), "kronos": kronos}
        self.assertTrue(evaluate_when("kronos.prediction == 'UP'", ctx))


class TestEvaluateWhenRejections(unittest.TestCase):

    def test_unknown_variable_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("unknown_var == 1", ctx)

    def test_function_call_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("print('hello')", ctx)

    def test_subscript_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("book['action'] == 'BUY'", ctx)

    def test_arithmetic_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("book.confidence + 0.1 >= 0.7", ctx)

    def test_syntax_error_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("book.action ==" , ctx)  # incomplete

    def test_empty_string_rejected(self):
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("", ctx)

    def test_arbitrary_attribute_chain_rejected(self):
        """Attribute access must terminate on a known root (book/kronos)."""
        ctx = {"book": _Obj(action="BUY"), "kronos": None}
        with self.assertRaises(ExpressionError):
            evaluate_when("(1).__class__", ctx)


class TestAllowedRoots(unittest.TestCase):

    def test_only_book_and_kronos(self):
        self.assertEqual(ALLOWED_ROOTS, frozenset({"book", "kronos"}))


if __name__ == "__main__":
    unittest.main()
