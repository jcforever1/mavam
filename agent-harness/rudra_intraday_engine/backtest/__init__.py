"""Backtest package — simulate the engine against historical 5-min data."""

from .metrics import BacktestMetrics, TradeResult, compute_metrics
from .runner import run_backtest

__all__ = ["BacktestMetrics", "TradeResult", "compute_metrics", "run_backtest"]
