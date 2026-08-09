"""Tests for the paper-trade module."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from rudra_intraday_engine.core.profile import Bar
from rudra_intraday_engine.papertrade import (
    DEFAULT_LOG_DIR,
    PaperLogRecord,
    append_record,
    read_records,
    compute_realized_pnl,
    replay_historical_signals,
    ClosedTrade,
    RealizedPnL,
)


# ── signal_log tests ─────────────────────────────────────────────────


def _make_record(
    ts: int,
    ticker: str = "KO",
    action: str = "BUY",
    entry: float = 60.0,
    stop: float = 59.0,
    target: float = 62.0,
    source: str = "run",
) -> PaperLogRecord:
    return PaperLogRecord(
        ts_unix=ts,
        ts_iso="2026-08-09T00:00:00+00:00",
        ticker=ticker,
        action=action,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        confidence=0.7,
        reason="test",
        config_sha="abc123",
        is_decide_no=False,
        decide_no_reasons=[],
        source=source,
    )


def test_append_and_read_roundtrip(tmp_path):
    log_dir = tmp_path / "paperlog"
    r1 = _make_record(ts=1723161600)  # 2024-08-09 00:00 UTC
    r2 = _make_record(ts=1723248000)  # 2024-08-10
    append_record(r1, log_dir=log_dir)
    append_record(r2, log_dir=log_dir)
    out = read_records(log_dir=log_dir)
    assert len(out) == 2
    assert out[0].ticker == "KO"
    assert out[0].entry_price == 60.0
    assert out[1].action == "BUY"


def test_read_filters_by_ticker(tmp_path):
    log_dir = tmp_path / "paperlog"
    append_record(_make_record(ts=1723161600, ticker="KO"), log_dir=log_dir)
    append_record(_make_record(ts=1723161600, ticker="SPY"), log_dir=log_dir)
    ko_only = read_records(log_dir=log_dir, ticker="KO")
    assert len(ko_only) == 1
    assert ko_only[0].ticker == "KO"


def test_read_filters_by_since_day(tmp_path):
    log_dir = tmp_path / "paperlog"
    append_record(_make_record(ts=1723161600), log_dir=log_dir)  # 2024-08-09
    append_record(_make_record(ts=1723334400), log_dir=log_dir)  # 2024-08-11
    out = read_records(log_dir=log_dir, since_day="2024-08-10")
    assert len(out) == 1
    assert out[0].ts_unix == 1723334400


def test_read_missing_dir_returns_empty(tmp_path):
    out = read_records(log_dir=tmp_path / "does_not_exist")
    assert out == []


def test_record_json_roundtrip():
    r = _make_record(ts=1723161600, ticker="SPY")
    raw = r.to_json()
    r2 = PaperLogRecord.from_json(raw)
    assert r == r2


# ── realized P&L tests ───────────────────────────────────────────────


def _bar(ts: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(timestamp_unix=ts, open=o, high=h, low=l, close=c, volume=1000.0)


def test_compute_realized_pnl_target_hit():
    rec = _make_record(ts=1, entry=100.0, stop=98.0, target=104.0)
    # Subsequent: low stays above stop, high hits 104 at bar 2
    bars = [
        _bar(2, 100, 101, 99, 100.5),
        _bar(3, 100.5, 104, 100, 103.5),  # target hit
        _bar(4, 103, 105, 102, 104),
    ]
    pnl = compute_realized_pnl([rec], {"KO": bars})
    assert pnl.n_trades == 1
    assert pnl.closed[0].exit_reason == "target"
    assert pnl.closed[0].exit_price == 104.0
    assert pnl.closed[0].pnl == 4.0


def test_compute_realized_pnl_stop_hit():
    rec = _make_record(ts=1, entry=100.0, stop=98.0, target=104.0)
    bars = [
        _bar(2, 100, 99.5, 97.5, 98.0),  # stop hit (low=97.5 <= 98)
    ]
    pnl = compute_realized_pnl([rec], {"KO": bars})
    assert pnl.n_trades == 1
    assert pnl.closed[0].exit_reason == "stop"
    assert pnl.closed[0].pnl == -2.0


def test_compute_realized_pnl_eod_exit():
    rec = _make_record(ts=1, entry=100.0, stop=98.0, target=104.0)
    bars = [
        _bar(2, 100, 101, 99, 100.5),
        _bar(3, 100.5, 101, 99.5, 100.0),  # neither stop nor target
    ]
    pnl = compute_realized_pnl([rec], {"KO": bars})
    assert pnl.n_trades == 1
    assert pnl.closed[0].exit_reason == "eod"
    assert pnl.closed[0].exit_price == 100.0
    assert pnl.closed[0].pnl == 0.0


def test_compute_realized_pnl_skips_sell_and_missing_stop():
    buy = _make_record(ts=1, action="BUY", entry=100.0, stop=98.0, target=104.0)
    sell = _make_record(ts=1, action="SELL", entry=100.0, stop=98.0, target=104.0)
    no_stop = _make_record(ts=1, action="BUY", entry=100.0, stop=None, target=104.0)
    bad_targets = _make_record(ts=1, action="BUY", entry=100.0, stop=110.0, target=104.0)  # stop > entry
    pnl = compute_realized_pnl(
        [buy, sell, no_stop, bad_targets],
        {"KO": [_bar(2, 100, 102, 99, 101)]},
    )
    assert pnl.n_trades == 1  # only the first BUY
    assert pnl.skipped == 3


def test_compute_realized_pnl_max_hold_caps_bars():
    rec = _make_record(ts=1, entry=100.0, stop=98.0, target=104.0)
    # 250 bars, all neutral — should close at max_hold (200th bar)
    bars = [_bar(i + 2, 100, 100.5, 99.5, 100.0) for i in range(250)]
    pnl = compute_realized_pnl([rec], {"KO": bars}, max_hold_bars=200)
    assert pnl.n_trades == 1
    assert pnl.closed[0].exit_reason == "eod"
    assert pnl.closed[0].bars_held == 200


def test_compute_realized_pnl_empty():
    pnl = compute_realized_pnl([], {"KO": []})
    assert pnl.n_trades == 0
    assert pnl.total_pnl == 0.0


# ── module surface test ──────────────────────────────────────────────


def test_papertrade_module_exports():
    from rudra_intraday_engine import papertrade
    assert hasattr(papertrade, "PaperLogRecord")
    assert hasattr(papertrade, "append_record")
    assert hasattr(papertrade, "read_records")
    assert hasattr(papertrade, "compute_realized_pnl")
    assert hasattr(papertrade, "replay_historical_signals")


def test_default_log_dir_is_under_home():
    assert str(DEFAULT_LOG_DIR).startswith(str(Path.home()))
    assert "mavam" in str(DEFAULT_LOG_DIR)
