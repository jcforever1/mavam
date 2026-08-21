"""Data package — CSV + yfinance (HTTP) + pinchtab (local CDP) + tradingview-mcp (rich CDP) + tradingview (server, ToS-restricted) loaders."""

from .loader import DataLoadError, load_bars_from_csv
from .pinchtab import (
    DEFAULT_CDP_URL,
    DEFAULT_STALE_AFTER_SECONDS as PINCHTAB_STALE_AFTER_SECONDS,
    ChartConfig,
    fetch_chart_bars,
    pinchtab_available,
)
from .tradingview_source import (
    DesktopConfig,
    fetch_desktop_bars,
    get_indicator_values,
    get_quote as get_desktop_quote,
    get_state as get_desktop_state,
    screenshot as desktop_screenshot,
    tv_cli_available,
    tv_desktop_reachable,
)
from .tradingview_server import (
    DEFAULT_STALE_AFTER_SECONDS as TRADINGVIEW_STALE_AFTER_SECONDS,
    TradingViewServerConfig,
    fetch_tradingview_bars,
)
from .tv_stream import (
    VALID_STREAMS,
    stream_tv,
    stream_tv_to_file,
)
from .orderflow_install import install_orderflow
from .yfinance_source import (
    DEFAULT_STALE_AFTER_SECONDS as YFINANCE_STALE_AFTER_SECONDS,
    YF_FETCH_BACKOFF_SECONDS,
    YF_FETCH_MAX_BACKOFF_SECONDS,
    YF_FETCH_RETRIES,
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
    "DesktopConfig",
    "fetch_desktop_bars",
    "get_desktop_quote",
    "get_desktop_state",
    "get_indicator_values",
    "desktop_screenshot",
    "tv_cli_available",
    "tv_desktop_reachable",
    "VALID_STREAMS",
    "stream_tv",
    "stream_tv_to_file",
    "install_orderflow",
    "TradingViewServerConfig",
    "TRADINGVIEW_STALE_AFTER_SECONDS",
    "fetch_tradingview_bars",
    "YFinanceConfig",
    "YFINANCE_STALE_AFTER_SECONDS",
    "YF_FETCH_RETRIES",
    "YF_FETCH_BACKOFF_SECONDS",
    "YF_FETCH_MAX_BACKOFF_SECONDS",
    "fetch_yfinance_bars",
    "yfinance_available",
]
