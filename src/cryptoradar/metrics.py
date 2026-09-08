from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import inf


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_r: float
    profit_factor: float
    max_drawdown_r: float


def calculate_metrics(r_multiples: Iterable[float]) -> PerformanceMetrics:
    values = list(r_multiples)
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = inf if gross_loss == 0 and gross_profit > 0 else (
        0.0 if gross_loss == 0 else gross_profit / gross_loss
    )

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    total = len(values)
    return PerformanceMetrics(
        trades=total,
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / total) if total else 0.0,
        net_r=sum(values),
        profit_factor=profit_factor,
        max_drawdown_r=max_dd,
    )
