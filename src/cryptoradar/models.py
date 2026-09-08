from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Exchange(str, Enum):
    BINANCE = "binance"
    BITGET = "bitget"
    BINGX = "bingx"
    GENERIC = "generic"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    exchange: Exchange = Exchange.GENERIC
    symbol: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= open and close")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
