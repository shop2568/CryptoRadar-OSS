from datetime import datetime, timezone

import pytest

from cryptoradar import Candle


def test_valid_candle():
    candle = Candle(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        100,
        101,
        99,
        100.5,
        1000,
    )
    assert candle.close == 100.5


def test_rejects_invalid_range():
    with pytest.raises(ValueError):
        Candle(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            100,
            99,
            98,
            100,
            1000,
        )
