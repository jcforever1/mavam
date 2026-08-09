"""Backtest metrics: returns, Sharpe, drawdown, win rate.

Pure functions over a list of trade P&Ls and an equity curve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class TradeResult:
    """A single simulated trade."""

    symbol: str
    action: str          # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    qty: int
    pnl: float           # signed: positive = profit, negative = loss
    exit_reason: str     # "take_profit", "stop_loss", "eod", "signal_flip"
    entry_bar_idx: int
    exit_bar_idx: int
    date: str            # YYYY-MM-DD


@dataclass(frozen=True)
class BacktestMetrics:
    """Aggregate metrics from a backtest run."""

    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float        # sum(wins) / sum(|losses|); inf if no losses
    max_drawdown_pct: float     # max peak-to-trough drawdown as a fraction
    sharpe: float               # annualized (assuming 252 trading days)
    equity_curve: tuple[float, ...]

    def to_dict(self) -> dict:
        return {
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 2),
            "avg_pnl": round(self.avg_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": (
                round(self.profit_factor, 3)
                if math.isfinite(self.profit_factor) else "inf"
            ),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe": round(self.sharpe, 3),
            "n_equity_points": len(self.equity_curve),
        }


def compute_metrics(
    trades: Sequence[TradeResult],
    initial_equity: float = 100_000.0,
) -> BacktestMetrics:
    """Compute aggregate metrics from a list of trade results.

    The equity curve starts at initial_equity and adds each trade's
    P&L as it closes. The Sharpe is annualized assuming 252 trading
    days, using the daily P&L series derived from the trade sequence.
    """
    n = len(trades)
    if n == 0:
        return BacktestMetrics(
            n_trades=0, n_wins=0, n_losses=0, win_rate=0.0,
            total_pnl=0.0, avg_pnl=0.0, avg_win=0.0, avg_loss=0.0,
            profit_factor=0.0, max_drawdown_pct=0.0, sharpe=0.0,
            equity_curve=(initial_equity,),
        )

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n_w = len(wins)
    n_l = len(losses)

    total = sum(pnls)
    avg = total / n
    avg_win = (sum(wins) / n_w) if n_w > 0 else 0.0
    avg_loss = (sum(losses) / n_l) if n_l > 0 else 0.0
    sum_loss_abs = sum(abs(p) for p in losses) if losses else 0.0
    profit_factor = (sum(wins) / sum_loss_abs) if sum_loss_abs > 0 else math.inf

    # Equity curve (cumulative P&L added to initial)
    eq = [initial_equity]
    running = initial_equity
    for p in pnls:
        running += p
        eq.append(running)
    eq_tuple = tuple(eq)

    # Max drawdown
    peak = eq[0]
    max_dd = 0.0
    for v in eq:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

    # Sharpe: simplified as mean(daily P&L) / std(daily P&L) * sqrt(252)
    # We don't have a clean daily P&L series (trades are intraday),
    # so we approximate using per-trade P&L — this overstates Sharpe
    # for low-trade-count strategies. Honest disclosure: this is per-trade
    # Sharpe, not per-day. For per-day, group trades by date.
    if n > 1:
        mean_p = avg
        var = sum((p - mean_p) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std > 0:
            sharpe = mean_p / std * math.sqrt(252)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return BacktestMetrics(
        n_trades=n,
        n_wins=n_w,
        n_losses=n_l,
        win_rate=n_w / n if n > 0 else 0.0,
        total_pnl=total,
        avg_pnl=avg,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        equity_curve=eq_tuple,
    )


__all__ = ["TradeResult", "BacktestMetrics", "compute_metrics"]
