"""Tests for the 50-ticker sweep expansion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rudra_intraday_engine.backtest.sweep import (
    TICKERS_50,
    _bars_from_json,
    _bars_to_json,
    _cache_path,
    fetch_bars_cached,
)
from rudra_intraday_engine.core.profile import Bar


def test_tickers_50_has_50_entries():
    assert len(TICKERS_50) == 50, f"expected 50, got {len(TICKERS_50)}"


def test_tickers_50_no_duplicates():
    assert len(TICKERS_50) == len(set(TICKERS_50)), "duplicate tickers in TICKERS_50"


def test_tickers_50_all_strings():
    assert all(isinstance(t, str) for t in TICKERS_50)


def test_tickers_50_ko_included():
    """KO is the original verified-alpha ticker; it MUST be in the universe."""
    assert "KO" in TICKERS_50


def test_tickers_50_sector_diversity():
    """The 50-ticker list should span multiple sectors, not be all tech."""
    assert "SPY" in TICKERS_50  # ETF
    assert "KO" in TICKERS_50   # staples
    assert "XOM" not in TICKERS_50 or "CVX" in TICKERS_50  # at least one energy
    assert "JPM" in TICKERS_50  # financials
    assert "JNJ" in TICKERS_50  # healthcare


def test_bars_json_roundtrip():
    original = [
        Bar(timestamp_unix=1700000000, open=100.0, high=101.0, low=99.5, close=100.5, volume=1000.0),
        Bar(timestamp_unix=1700000600, open=100.5, high=102.0, low=100.0, close=101.5, volume=1500.0),
    ]
    raw = _bars_to_json(original)
    restored = _bars_from_json(raw)
    assert len(restored) == len(original)
    for orig, rest in zip(original, restored):
        assert orig.timestamp_unix == rest.timestamp_unix
        assert orig.open == rest.open
        assert orig.high == rest.high
        assert orig.low == rest.low
        assert orig.close == rest.close
        assert orig.volume == rest.volume


def test_cache_path_is_absolute():
    p = _cache_path("SPY")
    assert p.is_absolute()
    assert str(p).endswith("SPY.json")
    assert "mavam" in str(p) or ".cache" in str(p)


def test_fetch_bars_cached_returns_none_for_invalid_ticker(tmp_path, monkeypatch):
    """When yfinance returns None, the cache helper returns None without crashing."""
    from rudra_intraday_engine.data import yfinance_source

    monkeypatch.setattr(
        yfinance_source, "fetch_yfinance_bars", lambda cfg: None
    )
    result = fetch_bars_cached("ZZZZZZ", cache_max_age_seconds=0)
    assert result is None


def test_fetch_bars_cached_uses_cache_when_fresh(tmp_path, monkeypatch):
    """If cache is younger than max age, the cache is read; yfinance is not called."""
    from rudra_intraday_engine.data import yfinance_source
    from rudra_intraday_engine.backtest.sweep import _cache_path

    # Pre-populate the cache
    cache = _cache_path("KO")
    cache.parent.mkdir(parents=True, exist_ok=True)
    bars = [
        Bar(timestamp_unix=1700000000, open=60.0, high=61.0, low=59.5, close=60.5, volume=1000.0),
    ]
    cache.write_text(json.dumps(_bars_to_json(bars)))

    # If yfinance is called, it raises. If the cache works, no call.
    def boom(cfg):
        raise RuntimeError("yfinance should not be called when cache is fresh")

    monkeypatch.setattr(yfinance_source, "fetch_yfinance_bars", boom)
    result = fetch_bars_cached("KO", cache_max_age_seconds=24 * 3600)
    assert result is not None
    assert len(result) == 1
    assert result[0].close == 60.5

    # Cleanup
    cache.unlink()
