from datetime import datetime, timezone

from cryptoradar import BacktestConfig, Candle, Side, run_backtest


candles = [
    Candle(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), 100, 101, 99, 100, 1000),
    Candle(datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc), 100, 105, 99.5, 104, 1200),
    Candle(datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc), 104, 106, 103, 105, 900),
]

result = run_backtest(
    candles,
    signals={0: Side.LONG},
    config=BacktestConfig(stop_pct=0.01, reward_r=4.0),
)

print(result.metrics)
for trade in result.trades:
    print(trade)
