"""Tests for the yfinance data source (HTTP-only, no browser)."""

from __future__ import annotations

import unittest

from rudra_intraday_engine.data import (
    YFinanceConfig,
    YFINANCE_STALE_AFTER_SECONDS,
    fetch_yfinance_bars,
    yfinance_available,
)


class TestYFinanceConfig(unittest.TestCase):

    def test_defaults(self):
        c = YFinanceConfig(ticker="SPY")
        self.assertEqual(c.ticker, "SPY")
        self.assertEqual(c.period, "5d")
        self.assertEqual(c.interval, "5m")
        self.assertEqual(c.stale_after_seconds, YFINANCE_STALE_AFTER_SECONDS)

    def test_custom_values(self):
        c = YFinanceConfig(
            ticker="QQQ",
            period="1mo",
            interval="15m",
            stale_after_seconds=0,
        )
        self.assertEqual(c.ticker, "QQQ")
        self.assertEqual(c.period, "1mo")
        self.assertEqual(c.interval, "15m")
        self.assertEqual(c.stale_after_seconds, 0)


class TestYFinanceAvailable(unittest.TestCase):

    def test_returns_bool(self):
        result = yfinance_available()
        self.assertIsInstance(result, bool)


class TestFetchYFinanceBars(unittest.TestCase):

    def test_invalid_ticker_returns_none_or_list(self):
        """Either None (ticker not found) or a (possibly empty) list."""
        result = fetch_yfinance_bars(
            YFinanceConfig(ticker="DEFINITELY_NOT_REAL_TICKER_XYZ", period="5d")
        )
        self.assertTrue(result is None or isinstance(result, list))

    def test_stale_after_zero_disables_filter(self):
        """stale_after_seconds <= 0 means no freshness check; we get
        all bars from the period."""
        # We don't assert specific counts (depends on yfinance
        # availability and network), just that the call completes.
        result = fetch_yfinance_bars(
            YFinanceConfig(ticker="SPY", period="5d", stale_after_seconds=0)
        )
        self.assertTrue(result is None or isinstance(result, list))


if __name__ == "__main__":
    unittest.main()
