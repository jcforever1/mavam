"""Kronos adapter for the rudra-intraday-engine.

Kronos is a foundation model for financial candlesticks (AAAI 2026,
~36K stars on GitHub). v1.1 of this engine MAY integrate Kronos as
an optional ML layer that emits a `KronosSignal` alongside the
book-derived `BookSignal`. The Adjudicator then merges the two.

This module is GATED: it tries to import kronos at call time. If
kronos is not installed (the v1 default), the adapter returns None
— the Adjudicator's `when_no_kronos` policy decides what to do
(typically HOLD).

To enable Kronos: `pip install rudra-intraday-engine[kronos]` and
set `[inputs.kronos] required = true` in the Adjudicator TOML.

The user's 2-4 hour Kronos test (Council re-run, 2026-08-08) will
validate whether the integration is worth shipping as v1.1 default.
"""

from __future__ import annotations

from typing import Optional

from ..signal_types import KronosSignal
from .profile import Bar


def kronos_available() -> bool:
    """Return True if the kronos package can be imported."""
    try:
        import kronos  # noqa: F401
        return True
    except ImportError:
        return False


DEFAULT_KRONOS_MODEL_VERSION = "kronos-0.6.0"


def predict_kronos(
    bars: list[Bar],
    horizon_bars: int = 1,
    model_version: str = DEFAULT_KRONOS_MODEL_VERSION,
    min_confidence: float = 0.50,
) -> Optional[KronosSignal]:
    """Run a Kronos forecast on the given bars.

    Returns a KronosSignal if Kronos is installed and the call succeeds.
    Returns None if:
      - kronos is not installed (the v1 default)
      - the bars list is empty
      - the kronos call raises (model version mismatch, OOM, etc.)

    The Adjudicator treats None as "no Kronos signal available" and
    applies its `[fallback] when_no_kronos` policy.
    """
    if not kronos_available():
        return None
    if not bars:
        return None

    try:
        import kronos  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]

        # Convert bars to the (N, 5) OHLCV array Kronos expects.
        ohlcv = np.array(
            [[b.open, b.high, b.low, b.close, b.volume] for b in bars],
            dtype=np.float32,
        )
        forecast = kronos.predict(ohlcv, horizon=horizon_bars)
        # forecast is (horizon_bars, 5); the last row's close is the
        # final predicted close.
        last_close = bars[-1].close
        forecast_close = float(forecast[-1, 3])
        delta = forecast_close - last_close

        # Confidence from the magnitude of the predicted move relative
        # to the recent average bar range.
        recent_ranges = [b.high - b.low for b in bars[-20:]]
        avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1.0
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
            notes=f"kronos forecast close: {forecast_close:.2f} (delta {delta:+.2f})",
        )
    except Exception:
        # Never crash — return None on any failure. Real failure modes
        # (model version mismatch, OOM, network) all become "we don't
        # know" rather than propagating the exception.
        return None


__all__ = [
    "kronos_available",
    "predict_kronos",
    "DEFAULT_KRONOS_MODEL_VERSION",
]
