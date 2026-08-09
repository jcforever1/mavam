"""Data package — CSV + yfinance (HTTP) + pinchtab (local CDP) + tradingview (server, ToS-restricted) loaders."""

from .loader import DataLoadError, load_bars_from_csv
from .pinchtab import (
    DEFAULT_CDP_URL,
    DEFAULT_STALE_AFTER_SECONDS as PINCHTAB_STALE_AFTER_SECONDS,
    ChartConfig,
    fetch_chart_bars,
    pinchtab_available,
)
from .tradingview_server import (
    DEFAULT_STALE_AFTER_SECONDS as TRADINGVIEW_STALE_AFTER_SECONDS,
    TradingViewServerConfig,
    fetch_tradingview_bars,
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
    "DEFAULT_CDP_URL",
    "PINCHTAB_STALE_AFTER_SECONDS",
    "fetch_chart_bars",
    "pinchtab_available",
    "TradingViewServerConfig",
    "TRADINGVIEW_STALE_AFTER_SECONDS",
    "fetch_tradingview_bars",
    "YFinanceConfig",
    "YFINANCE_STALE_AFTER_SECONDS",
    "fetch_yfinance_bars",
    "yfinance_available",
]
