from datetime import datetime, timedelta, timezone

from cryptoradar import BacktestConfig, Candle, Side, run_backtest


def _candle(i, o, h, l, c):
    return Candle(
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i),
        o,
        h,
        l,
        c,
        1000,
    )


def test_long_target_hit():
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 105, 99.5, 104),
    ]
    result = run_backtest(
        candles,
        {0: Side.LONG},
        BacktestConfig(stop_pct=0.01, reward_r=4),
    )
    assert result.trades[0].outcome == "win"
    assert result.trades[0].r_multiple == 4


def test_pessimistic_intrabar_conflict_is_loss():
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 105, 98, 100),
    ]
    result = run_backtest(
        candles,
        {0: Side.LONG},
        BacktestConfig(stop_pct=0.01, reward_r=4, pessimistic_intrabar=True),
    )
    assert result.trades[0].outcome == "loss"
