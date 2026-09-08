"""Public CryptoRadar OSS research primitives."""

from .backtest import BacktestConfig, BacktestResult, Trade, run_backtest
from .metrics import PerformanceMetrics
from .models import Candle, Exchange, Side

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Candle",
    "Exchange",
    "PerformanceMetrics",
    "Side",
    "Trade",
    "run_backtest",
]
