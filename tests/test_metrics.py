from cryptoradar.metrics import calculate_metrics


def test_metrics():
    metrics = calculate_metrics([4.0, -1.0, -1.0, 4.0])
    assert metrics.trades == 4
    assert metrics.wins == 2
    assert metrics.losses == 2
    assert metrics.win_rate == 0.5
    assert metrics.net_r == 6.0
    assert metrics.profit_factor == 4.0
    assert metrics.max_drawdown_r == 2.0
