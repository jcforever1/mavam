"""Data package — CSV + yfinance (HTTP) + pinchtab/TradingView (playwright) loaders."""

from .loader import DataLoadError, load_bars_from_csv
from .pinchtab import (
    DEFAULT_STALE_AFTER_SECONDS as PINCHTAB_STALE_AFTER_SECONDS,
    ChartConfig,
    fetch_chart_bars,
    pinchtab_available,
)
from .yfinance_source import (
    DEFAULT_STALE_AFTER_SECONDS as YFINANCE_STALE_AFTER_SECONDS,
    YFinanceConfig,
    fetch_yfinance_bars,
    yfinance_available,
)

__all__ = [
    "DataLoadError",
    "load_bars_from_csv",
    "ChartConfig",
    "PINCHTAB_STALE_AFTER_SECONDS",
    "fetch_chart_bars",
    "pinchtab_available",
    "YFinanceConfig",
    "YFINANCE_STALE_AFTER_SECONDS",
    "fetch_yfinance_bars",
    "yfinance_available",
]
