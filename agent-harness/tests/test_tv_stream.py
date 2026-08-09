"""Tests for the live TradingView Desktop stream (tv CLI wrapper)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from rudra_intraday_engine.data.tv_stream import (
    VALID_STREAMS,
    stream_tv,
    stream_tv_to_file,
)


def test_valid_streams_list():
    assert "quote" in VALID_STREAMS
    assert "bars" in VALID_STREAMS
    assert "values" in VALID_STREAMS
    assert "all" in VALID_STREAMS


def test_stream_tv_rejects_invalid_sub():
    with pytest.raises(ValueError, match="invalid stream"):
        list(stream_tv("nope"))


def test_stream_tv_errors_when_cli_missing(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tv_stream.tv_cli_available",
        lambda: False,
    )
    with pytest.raises(RuntimeError, match="tv CLI not found"):
        list(stream_tv("quote"))


def test_stream_tv_yields_records():
    """End-to-end: start the real `tv stream quote` subprocess, consume
    records until max_records, then break. Requires the `tv` CLI to be
    installed and TradingView Desktop running.

    Skipped if the `tv` CLI is not on PATH.
    """
    import shutil

    if shutil.which("tv") is None:
        pytest.skip("tv CLI not installed")

    records = []
    for r in stream_tv("quote", interval_ms=500, timeout=10):
        records.append(r)
        if len(records) >= 2:
            break
    # NVDA's price may not change during the test window, so we may
    # get 1 (initial state) or 2+ records. The point is that the
    # generator exits cleanly without hanging.
    assert len(records) >= 1
    assert "symbol" in records[0]
    assert "_stream" in records[0]


def test_stream_tv_to_file_writes_jsonl(tmp_path):
    """Smoke test: write 0 records (process exits immediately) to verify file path."""
    out = tmp_path / "test.jsonl"

    class FakeProc:
        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

    with patch(
        "rudra_intraday_engine.data.tv_stream.subprocess.Popen",
        return_value=FakeProc(),
    ), patch(
        "rudra_intraday_engine.data.tv_stream.tv_cli_available",
        return_value=True,
    ):
        n = stream_tv_to_file("quote", output_path=str(out), max_records=0)
    assert n == 0
    assert out.exists()
    # File should be empty (no records because process exits before emitting)
    assert out.read_text() == ""


def test_module_exports():
    from rudra_intraday_engine.data import tv_stream
    assert hasattr(tv_stream, "VALID_STREAMS")
    assert hasattr(tv_stream, "stream_tv")
    assert hasattr(tv_stream, "stream_tv_to_file")
