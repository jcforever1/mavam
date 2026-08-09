"""Artifact package — content-addressed store + explain + verify."""

from .explain import explain_signal
from .store import read_trade_signal, write_trade_signal

__all__ = ["explain_signal", "read_trade_signal", "write_trade_signal"]
