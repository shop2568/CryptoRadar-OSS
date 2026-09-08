from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import Candle, Exchange


def load_csv(
    path: str | Path,
    *,
    exchange: Exchange = Exchange.GENERIC,
    symbol: str = "UNKNOWN",
) -> list[Candle]:
    """Load timestamp,open,high,low,close,volume CSV data."""
    candles: list[Candle] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing CSV columns: {sorted(missing)}")

        for row in reader:
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    exchange=exchange,
                    symbol=symbol,
                )
            )

    candles.sort(key=lambda c: c.timestamp)
    return candles
