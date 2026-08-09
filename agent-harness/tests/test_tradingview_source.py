"""Tests for the TradingView Desktop data source (tv CLI wrapper)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from rudra_intraday_engine.core.profile import Bar
from rudra_intraday_engine.data.tradingview_source import (
    DesktopConfig,
    _run_tv,
    fetch_desktop_bars,
    get_indicator_values,
    get_quote,
    get_state,
    screenshot,
    tv_cli_available,
    tv_desktop_reachable,
)


def test_desktop_config_defaults():
    c = DesktopConfig(ticker="NVDA")
    assert c.ticker == "NVDA"
    assert c.timeframe == "1D"
    assert c.count == 300  # capped at 300 to avoid PIPE_BUF truncation
    assert c.switch_chart is True
    assert c.timeout == 30


def test_tv_cli_available_when_installed(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.shutil.which",
        lambda x: "/usr/local/bin/tv" if x == "tv" else None,
    )
    assert tv_cli_available() is True


def test_tv_cli_available_when_missing(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.shutil.which",
        lambda x: None,
    )
    assert tv_cli_available() is False


def test_tv_desktop_reachable_when_connected(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.shutil.which",
        lambda x: "/usr/local/bin/tv",
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source._run_tv",
        lambda *a, **k: {"success": True, "cdp_connected": True},
    )
    assert tv_desktop_reachable() is True


def test_tv_desktop_reachable_when_disconnected(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.shutil.which",
        lambda x: "/usr/local/bin/tv",
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source._run_tv",
        lambda *a, **k: {"success": True, "cdp_connected": False},
    )
    assert tv_desktop_reachable() is False


def test_fetch_desktop_bars_returns_none_when_cli_missing(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.tv_cli_available",
        lambda: False,
    )
    result = fetch_desktop_bars(DesktopConfig(ticker="NVDA"))
    assert result is None


def test_fetch_desktop_bars_returns_bars(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.tv_cli_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.tv_desktop_reachable",
        lambda: True,
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source._run_tv",
        lambda *a, **k: {
            "success": True,
            "bar_count": 2,
            "bars": [
                {"time": 1700000000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000.0},
                {"time": 1700000600, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1500.0},
            ],
        },
    )
    cfg = DesktopConfig(ticker="NVDA", switch_chart=False)
    bars = fetch_desktop_bars(cfg)
    assert bars is not None
    assert len(bars) == 2
    assert bars[0].open == 100.0
    assert bars[1].close == 101.5


def test_fetch_desktop_bars_handles_missing_volume(monkeypatch):
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.tv_cli_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source.tv_desktop_reachable",
        lambda: True,
    )
    monkeypatch.setattr(
        "rudra_intraday_engine.data.tradingview_source._run_tv",
        lambda *a, **k: {
            "success": True,
            "bars": [
                {"time": 1700000000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": None},
            ],
        },
    )
    cfg = DesktopConfig(ticker="NVDA", switch_chart=False)
    bars = fetch_desktop_bars(cfg)
    assert bars is not None
    assert bars[0].volume == 0.0


def test_module_exports():
    from rudra_intraday_engine.data import tradingview_source
    assert hasattr(tradingview_source, "DesktopConfig")
    assert hasattr(tradingview_source, "fetch_desktop_bars")
    assert hasattr(tradingview_source, "get_quote")
    assert hasattr(tradingview_source, "get_indicator_values")
    assert hasattr(tradingview_source, "screenshot")
