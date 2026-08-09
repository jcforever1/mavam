"""Kronos adapter for the rudra-intraday-engine.

Kronos is a foundation model for financial candlesticks (AAAI 2026).
This module wires the real Kronos model (NeoQuasar/Kronos-small +
Kronos-Tokenizer-base) into the engine as an optional ML layer that
emits a `KronosSignal` alongside the book-derived `BookSignal`. The
Adjudicator then merges the two.

The integration is GATED: the kronos Python package must be importable
from the vendor directory (`vendor/kronos/`). The actual model is
lazy-loaded on first call (avoids 45s+ startup for the 24M-param
tokenizer+model load).

If anything fails (no kronos package, network down, model missing,
OOB horizon), the adapter returns None — never raises. The
Adjudicator's `when_no_kronos` policy decides what to do (typically
HOLD or fall through to book-only).

Council re-run verdict (2026-08-08): 2-4h feasibility test on whether
Kronos adds edge to the book-only signal. This module is the
implementation. The test is in `backtest/sweep_kronos.py`.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Optional

from ..signal_types import KronosSignal
from .profile import Bar


# ── paths & config ───────────────────────────────────────────────────


# vendor/kronos/ — where the Kronos source is checked in
_KRONOS_VENDOR_DIR = (
    Path(__file__).parent.parent.parent.parent / "vendor" / "kronos"
)
_KRONOS_VENDOR_DIR = _KRONOS_VENDOR_DIR.resolve()

DEFAULT_KRONOS_MODEL = os.environ.get(
    "KRONOS_MODEL_NAME", "NeoQuasar/Kronos-small"
)
DEFAULT_KRONOS_TOKENIZER = os.environ.get(
    "KRONOS_TOKENIZER_NAME", "NeoQuasar/Kronos-Tokenizer-base"
)
DEFAULT_KRONOS_MODEL_VERSION = "kronos-0.6.0"
DEFAULT_MAX_CONTEXT = 512  # Kronos-small hard limit


# Module-level cache for the predictor. Avoids 45s+ reload on every call.
_PREDICTOR_CACHE: dict = {"predictor": None, "load_attempted": False}


# ── availability ─────────────────────────────────────────────────────


def kronos_available() -> bool:
    """Return True if the kronos source tree is reachable."""
    # The Kronos package is not on PyPI; it lives in vendor/kronos/.
    # Check the model.py directly.
    if not _KRONOS_VENDOR_DIR.exists():
        return False
    if not (_KRONOS_VENDOR_DIR / "model").is_dir():
        return False
    return True


# ── lazy load ────────────────────────────────────────────────────────


def _load_kronos_predictor():
    """Lazy-load the Kronos model + tokenizer. Returns None on failure."""
    if _PREDICTOR_CACHE["load_attempted"]:
        return _PREDICTOR_CACHE["predictor"]
    _PREDICTOR_CACHE["load_attempted"] = True

    if not kronos_available():
        return None

    # Make the vendor dir importable
    vendor_str = str(_KRONOS_VENDOR_DIR)
    if vendor_str not in sys.path:
        sys.path.insert(0, vendor_str)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from model import Kronos, KronosTokenizer, KronosPredictor  # type: ignore
            from huggingface_hub import hf_hub_download  # type: ignore

            # The KronosTokenizer class uses PyTorchModelHubMixin but the
            # config isn't auto-injected; we load it explicitly.
            cfg_path = hf_hub_download(
                DEFAULT_KRONOS_TOKENIZER, "config.json"
            )
            with open(cfg_path) as f:
                cfg = json.load(f)
            tokenizer = KronosTokenizer.from_pretrained(
                DEFAULT_KRONOS_TOKENIZER, **cfg
            )
            model = Kronos.from_pretrained(DEFAULT_KRONOS_MODEL)
            predictor = KronosPredictor(
                model, tokenizer,
                max_context=DEFAULT_MAX_CONTEXT,
                device="cpu",
            )
            _PREDICTOR_CACHE["predictor"] = predictor
            return predictor
    except Exception:
        # Any failure leaves the predictor as None; subsequent calls
        # are no-ops.
        return None


# ── public API ───────────────────────────────────────────────────────


def predict_kronos(
    bars: list[Bar],
    horizon_bars: int = 1,
    model_version: str = DEFAULT_KRONOS_MODEL_VERSION,
    min_confidence: float = 0.10,
) -> Optional[KronosSignal]:
    """Run a Kronos forecast on the given bars.

    Returns a KronosSignal if Kronos is available and the call succeeds.
    Returns None if:
      - kronos vendor dir is missing
      - the model load fails (network, missing weights, OOM)
      - the bars list is empty
      - the kronos predict() call raises
    """
    predictor = _load_kronos_predictor()
    if predictor is None:
        return None
    if not bars:
        return None

    try:
        import pandas as pd  # type: ignore
        import numpy as np  # type: ignore

        # Truncate to max_context bars
        lookback = min(len(bars), DEFAULT_MAX_CONTEXT)
        recent = bars[-lookback:]

        # DataFrame with [open, high, low, close, volume, amount].
        # KronosTokenizer config says d_in=6, so amount is required.
        df = pd.DataFrame(
            [
                {
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "amount": b.volume * (b.high + b.low + b.close) / 3.0,
                }
                for b in recent
            ]
        )
        x_timestamp = pd.to_datetime(
            [b.timestamp_unix for b in recent], unit="s"
        )
        # Vendor bug: calc_time_stamps calls .dt.minute on a DatetimeIndex.
        # Convert to Series first so .dt accessor works.
        x_timestamp = pd.Series(x_timestamp)
        last_ts = recent[-1].timestamp_unix
        # 5-minute bars is the canonical interval; adjust if known.
        step_seconds = 300
        if len(recent) >= 2:
            step_seconds = max(
                60,
                min(3600, recent[-1].timestamp_unix - recent[-2].timestamp_unix),
            )
        y_timestamp = pd.Series(pd.to_datetime(
            [last_ts + (i + 1) * step_seconds for i in range(horizon_bars)],
            unit="s",
        ))

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pred_df = predictor.predict(
                df, x_timestamp, y_timestamp,
                pred_len=horizon_bars, verbose=False,
            )
        forecast_close = float(pred_df["close"].iloc[-1])
        last_close = recent[-1].close
        delta = forecast_close - last_close

        # Confidence: magnitude of predicted move vs recent bar range
        recent_ranges = [b.high - b.low for b in recent[-20:]]
        avg_range = (
            sum(recent_ranges) / len(recent_ranges)
            if recent_ranges
            else max(0.01, abs(last_close) * 0.001)
        )
        confidence = min(1.0, abs(delta) / max(avg_range, 0.01))

        if confidence < min_confidence:
            prediction = "FLAT"
        elif delta > 0:
            prediction = "UP"
        else:
            prediction = "DOWN"

        return KronosSignal(
            prediction=prediction,  # type: ignore[arg-type]
            confidence=confidence,
            model_version=model_version,
            horizon_bars=horizon_bars,
            notes=(
                f"kronos forecast close: {forecast_close:.2f} "
                f"(delta {delta:+.2f}, range {avg_range:.2f}, "
                f"conf {confidence:.2f})"
            ),
        )
    except Exception:
        return None


# Test-only escape hatch
def _reset_cache() -> None:
    """Clear the cached predictor. Used in tests."""
    _PREDICTOR_CACHE["predictor"] = None
    _PREDICTOR_CACHE["load_attempted"] = False


__all__ = [
    "kronos_available",
    "predict_kronos",
    "DEFAULT_KRONOS_MODEL",
    "DEFAULT_KRONOS_TOKENIZER",
    "DEFAULT_KRONOS_MODEL_VERSION",
    "_KRONOS_VENDOR_DIR",
    "_reset_cache",
]
