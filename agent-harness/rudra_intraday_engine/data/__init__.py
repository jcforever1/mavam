"""Data package — CSV and (future) yfinance loaders."""

from .loader import DataLoadError, load_bars_from_csv

__all__ = ["DataLoadError", "load_bars_from_csv"]
