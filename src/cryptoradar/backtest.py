from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .metrics import PerformanceMetrics, calculate_metrics
from .models import Candle, Side


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    stop_pct: float = 0.01
    reward_r: float = 4.0
    max_bars_in_trade: int = 32
    pessimistic_intrabar: bool = True

    def __post_init__(self) -> None:
        if self.stop_pct <= 0:
            raise ValueError("stop_pct must be > 0")
        if self.reward_r <= 0:
            raise ValueError("reward_r must be > 0")
        if self.max_bars_in_trade <= 0:
            raise ValueError("max_bars_in_trade must be > 0")


@dataclass(frozen=True, slots=True)
class Trade:
    signal_index: int
    exit_index: int
    side: Side
    entry: float
    stop: float
    target: float
    r_multiple: float
    outcome: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    metrics: PerformanceMetrics


def _levels(entry: float, side: Side, config: BacktestConfig) -> tuple[float, float]:
    risk = entry * config.stop_pct
    if side is Side.LONG:
        return entry - risk, entry + risk * config.reward_r
    return entry + risk, entry - risk * config.reward_r


def run_backtest(
    candles: Sequence[Candle],
    signals: Mapping[int, Side],
    config: BacktestConfig | None = None,
) -> BacktestResult:
    config = config or BacktestConfig()
    trades: list[Trade] = []

    for signal_index, side in sorted(signals.items()):
        if signal_index < 0 or signal_index >= len(candles) - 1:
            continue

        # Signal is generated on candle close; entry occurs on next candle open.
        entry_index = signal_index + 1
        entry = candles[entry_index].open
        stop, target = _levels(entry, side, config)

        final_index = min(len(candles) - 1, entry_index + config.max_bars_in_trade - 1)
        outcome = "timeout"
        r_multiple = 0.0
        exit_index = final_index

        for i in range(entry_index, final_index + 1):
            candle = candles[i]
            if side is Side.LONG:
                hit_stop = candle.low <= stop
                hit_target = candle.high >= target
            else:
                hit_stop = candle.high >= stop
                hit_target = candle.low <= target

            if hit_stop and hit_target:
                if config.pessimistic_intrabar:
                    outcome, r_multiple, exit_index = "loss", -1.0, i
                else:
                    outcome, r_multiple, exit_index = "win", config.reward_r, i
                break
            if hit_stop:
                outcome, r_multiple, exit_index = "loss", -1.0, i
                break
            if hit_target:
                outcome, r_multiple, exit_index = "win", config.reward_r, i
                break

        trades.append(
            Trade(
                signal_index=signal_index,
                exit_index=exit_index,
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                r_multiple=r_multiple,
                outcome=outcome,
            )
        )

    metrics = calculate_metrics(t.r_multiple for t in trades)
    return BacktestResult(trades=tuple(trades), metrics=metrics)
