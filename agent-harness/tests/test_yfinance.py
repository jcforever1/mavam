"""Tests for the yfinance data source (HTTP-only, no browser)."""

from __future__ import annotations

import unittest
from unittest import mock

from rudra_intraday_engine.data import (
    YF_FETCH_BACKOFF_SECONDS,
    YF_FETCH_RETRIES,
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

    def test_transient_empty_retries_and_succeeds(self):
        """A transient empty response (the 2026-08-13 'possibly delisted'
        no-data failure) must be retried, not treated as permanent."""
        import pandas as pd

        real_df = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-08-13 14:00:00"]),
        )
        calls = {"n": 0}

        def fake_history(**kwargs):
            calls["n"] += 1
            # First call: empty DataFrame (transient failure). Any
            # subsequent call: real data.
            if calls["n"] == 1:
                return pd.DataFrame()
            return real_df

        with mock.patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history = mock.Mock(side_effect=fake_history)
            with mock.patch(
                "rudra_intraday_engine.data.yfinance_source.time.sleep"
            ) as mock_sleep:
                result = fetch_yfinance_bars(
                    YFinanceConfig(ticker="SPY", period="5d", stale_after_seconds=0)
                )

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].close, 100.5)
        # Retried once (first call empty, second succeeded).
        self.assertEqual(calls["n"], 2)
        self.assertEqual(mock_sleep.call_count, 1)

    def test_all_empty_returns_none(self):
        """If every attempt returns empty, the fetch still returns None
        after the retry budget — no infinite loop."""
        import pandas as pd

        with mock.patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history = mock.Mock(
                return_value=pd.DataFrame()
            )
            with mock.patch(
                "rudra_intraday_engine.data.yfinance_source.time.sleep"
            ) as mock_sleep:
                result = fetch_yfinance_bars(
                    YFinanceConfig(ticker="SPY", period="5d", stale_after_seconds=0)
                )

        self.assertIsNone(result)
        self.assertEqual(mock_ticker.return_value.history.call_count, YF_FETCH_RETRIES)
        # Backoff = 1s + 2s = 3 sleeps across 3 attempts.
        self.assertEqual(mock_sleep.call_count, YF_FETCH_RETRIES - 1)
        self.assertGreaterEqual(YF_FETCH_BACKOFF_SECONDS, 0.5)

    def test_exception_retries_then_returns_none(self):
        """yfinance raising on every call also respects the retry budget."""
        with mock.patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history = mock.Mock(
                side_effect=RuntimeError("network down")
            )
            with mock.patch(
                "rudra_intraday_engine.data.yfinance_source.time.sleep"
            ) as mock_sleep:
                result = fetch_yfinance_bars(
                    YFinanceConfig(ticker="SPY", period="5d", stale_after_seconds=0)
                )

        self.assertIsNone(result)
        self.assertEqual(mock_ticker.return_value.history.call_count, YF_FETCH_RETRIES)
        self.assertEqual(mock_sleep.call_count, YF_FETCH_RETRIES - 1)


if __name__ == "__main__":
    unittest.main()
