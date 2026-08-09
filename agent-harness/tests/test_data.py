"""Tests for data/loader.py — CSV → list[Bar]."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rudra_intraday_engine.data import DataLoadError, load_bars_from_csv


class TestLoadBarsFromCSV(unittest.TestCase):

    def test_load_iso_timestamps(self):
        csv = """timestamp,open,high,low,close,volume
2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25,10000
2026-08-10T14:00:00Z,100.25,101.00,100.00,100.75,11000
2026-08-10T14:30:00Z,100.75,101.50,100.50,101.25,12000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        bars = load_bars_from_csv(path)
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].open, 100.00)
        self.assertEqual(bars[-1].close, 101.25)

    def test_load_unix_timestamps(self):
        csv = """timestamp,open,high,low,close,volume
1754835000,100.00,100.50,99.75,100.25,10000
1754836800,100.25,101.00,100.00,100.75,11000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        bars = load_bars_from_csv(path)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].timestamp_unix, 1754835000)

    def test_comments_skipped(self):
        csv = """# this is a comment
timestamp,open,high,low,close,volume
# another comment
2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25,10000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        bars = load_bars_from_csv(path)
        self.assertEqual(len(bars), 1)

    def test_bars_sorted_by_timestamp(self):
        csv = """timestamp,open,high,low,close,volume
2026-08-10T14:00:00Z,100.25,101.00,100.00,100.75,11000
2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25,10000
2026-08-10T14:30:00Z,100.75,101.50,100.50,101.25,12000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        bars = load_bars_from_csv(path)
        timestamps = [b.timestamp_unix for b in bars]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_missing_column_raises(self):
        csv = """timestamp,open,high,low,close
2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        with self.assertRaises(DataLoadError) as cm:
            load_bars_from_csv(path)
        self.assertIn("missing", str(cm.exception).lower())

    def test_empty_file_raises(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("")  # empty
            path = f.name
        with self.assertRaises(DataLoadError):
            load_bars_from_csv(path)

    def test_header_only_raises(self):
        csv = "timestamp,open,high,low,close,volume\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        with self.assertRaises(DataLoadError):
            load_bars_from_csv(path)

    def test_bad_timestamp_raises(self):
        csv = """timestamp,open,high,low,close,volume
not-a-timestamp,100.00,100.50,99.75,100.25,10000
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv)
            path = f.name
        with self.assertRaises(DataLoadError):
            load_bars_from_csv(path)

    def test_nonexistent_file_raises(self):
        with self.assertRaises(DataLoadError):
            load_bars_from_csv("/nonexistent/path.csv")


if __name__ == "__main__":
    unittest.main()
