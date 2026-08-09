"""Tests for cli.py — argv dispatch + the end-to-end run flow."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from rudra_intraday_engine.cli import (
    EXIT_OK,
    EXIT_USAGE,
    EXIT_CONFIG,
    EXIT_DATA,
    EXIT_RUNTIME,
    main,
)


def _write_csv() -> str:
    """A trend-up CSV for end-to-end tests."""
    csv = """timestamp,open,high,low,close,volume
2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25,10000
2026-08-10T14:00:00Z,100.25,101.50,100.25,101.25,12500
2026-08-10T14:30:00Z,101.25,102.50,101.00,102.25,13800
2026-08-10T15:00:00Z,102.25,103.00,102.00,102.75,14200
2026-08-10T15:30:00Z,102.75,103.50,102.50,103.25,13500
2026-08-10T16:00:00Z,103.25,104.00,103.00,103.75,12800
2026-08-10T16:30:00Z,103.75,104.50,103.50,104.25,13200
2026-08-10T17:00:00Z,104.25,105.00,104.00,104.75,14100
2026-08-10T17:30:00Z,104.75,105.50,104.50,105.25,13800
2026-08-10T18:00:00Z,105.25,106.00,105.00,105.75,13500
2026-08-10T18:30:00Z,105.75,106.50,105.50,106.25,12800
2026-08-10T19:00:00Z,106.25,106.50,105.75,105.85,11200
"""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as f:
        f.write(csv)
    return path


def _write_toml(csv_path: str) -> str:
    toml = f"""[adjudicator]
file = "../strategies/book-only.toml"

[predictor]
enabled = false

[data]
csv = "{csv_path}"
"""
    fd, path = tempfile.mkstemp(suffix=".toml")
    with os.fdopen(fd, "w") as f:
        f.write(toml)
    return path


class TestCliDispatch(unittest.TestCase):

    def test_no_args_shows_usage(self):
        self.assertEqual(main(["rudra-intraday"]), EXIT_USAGE)

    def test_help_returns_ok(self):
        self.assertEqual(main(["rudra-intraday", "help"]), EXIT_OK)

    def test_version_returns_ok(self):
        self.assertEqual(main(["rudra-intraday", "--version"]), EXIT_OK)

    def test_unknown_command_returns_usage(self):
        self.assertEqual(main(["rudra-intraday", "frobnicate"]), EXIT_USAGE)

    def test_run_with_no_args_returns_usage(self):
        self.assertEqual(main(["rudra-intraday", "run"]), EXIT_USAGE)

    def test_run_with_too_many_args_returns_usage(self):
        self.assertEqual(main(["rudra-intraday", "run", "a", "b"]), EXIT_USAGE)


class TestCliRunEndToEnd(unittest.TestCase):

    def test_run_full_flow(self):
        """End-to-end: a real CSV + a real Adjudicator → JSON stdout."""
        # Mirror the project's examples layout under a tmp dir:
        #   tmp/configs/run.toml
        #   tmp/strategies/book-only.toml
        #   tmp/data/test.csv
        # so that relative paths in run.toml resolve inside the sandbox.
        import tempfile as tf
        with tf.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "configs").mkdir()
            (tmp / "strategies").mkdir()
            (tmp / "data").mkdir()

            # Copy the project's example strategy into tmp/strategies/
            repo = Path(__file__).parent.parent
            src_strategy = repo / "examples" / "strategies" / "book-only.toml"
            dst_strategy = tmp / "strategies" / "book-only.toml"
            dst_strategy.write_text(src_strategy.read_text())

            # Write the CSV to tmp/data/
            (tmp / "data" / "test.csv").write_text(
                "timestamp,open,high,low,close,volume\n"
                "2026-08-10T13:30:00Z,100.00,100.50,99.75,100.25,10000\n"
                "2026-08-10T14:00:00Z,100.25,101.50,100.25,101.25,12500\n"
                "2026-08-10T14:30:00Z,101.25,102.50,101.00,102.25,13800\n"
                "2026-08-10T15:00:00Z,102.25,103.00,102.00,102.75,14200\n"
                "2026-08-10T15:30:00Z,102.75,103.50,102.50,103.25,13500\n"
                "2026-08-10T16:00:00Z,103.25,104.00,103.00,103.75,12800\n"
                "2026-08-10T16:30:00Z,103.75,104.50,103.50,104.25,13200\n"
                "2026-08-10T17:00:00Z,104.25,105.00,104.00,104.75,14100\n"
                "2026-08-10T17:30:00Z,104.75,105.50,104.50,105.25,13800\n"
                "2026-08-10T18:00:00Z,105.25,106.00,105.00,105.75,13500\n"
                "2026-08-10T18:30:00Z,105.75,106.50,105.50,106.25,12800\n"
                "2026-08-10T19:00:00Z,106.25,106.50,105.75,105.85,11200\n"
            )

            # Write the run config in tmp/configs/
            toml_path = tmp / "configs" / "run.toml"
            toml_path.write_text(
                "[adjudicator]\n"
                "file = \"../strategies/book-only.toml\"\n"
                "\n"
                "[predictor]\n"
                "enabled = false\n"
                "\n"
                "[data]\n"
                "csv = \"../data/test.csv\"\n"
            )

            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rudra-intraday", "run", str(toml_path)])
            self.assertEqual(code, EXIT_OK, f"stderr: {buf.getvalue()}")
            out = buf.getvalue()
            import json
            payload = json.loads(out)
            self.assertIn("action", payload)
            self.assertIn("symbol", payload)
            self.assertIn("reason", payload)
            self.assertIn("_artifact_path", payload)

    def test_run_nonexistent_config(self):
        code = main(["rudra-intraday", "run", "/nonexistent/config.toml"])
        self.assertEqual(code, EXIT_CONFIG)

    def test_run_flag_rejected(self):
        """Flags are forbidden on the main verb."""
        code = main(["rudra-intraday", "run", "--flag", "config.toml"])
        self.assertEqual(code, EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
