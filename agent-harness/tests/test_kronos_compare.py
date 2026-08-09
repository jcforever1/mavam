"""Tests for the Kronos-enabled walk-forward comparison."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rudra_intraday_engine.backtest.kronos_compare import _make_kronos_adjudicator


def test_make_kronos_adjudicator(tmp_path):
    """The Kronos-enabled adjudicator TOML is valid and adds rules."""
    base = Path(__file__).parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        pytest.skip("book-only.toml not found")
    out = _make_kronos_adjudicator(tmp_path, base)
    assert out.exists()
    text = out.read_text()
    # Kronos is required
    assert "kronos" in text
    # Has merge rules
    assert "[[adjudicator.merge_rules]]" in text or "merge_rules" in text
    # References kronos.prediction
    assert "kronos.prediction" in text


def test_make_kronos_adjudicator_changes_name(tmp_path):
    """The new adjudicator is named 'book+kronos' to distinguish it."""
    base = Path(__file__).parent.parent / "examples" / "strategies" / "book-only.toml"
    if not base.exists():
        pytest.skip("book-only.toml not found")
    out = _make_kronos_adjudicator(tmp_path, base)
    text = out.read_text()
    assert "book+kronos" in text
    # Original "book-only" should NOT be in the new adjudicator name
    assert "name = \"book-only\"" not in text
