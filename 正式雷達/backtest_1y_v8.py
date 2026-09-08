#!/usr/bin/env python3
"""CryptoRadar one-year all-market V8 low-chase research backtest.

LONG:
- Bullish 4H trend with 15m alignment.
- A completed 15m candle breaks a 20-bar resistance with volume.
- Within 2-12 completed candles, price retests that breakout level and closes
  back above it.
- Enter only after the stricter second-confirmation close.

SHORT:
- Bearish 4H trend with 15m alignment.
- Price rallies into a previously confirmed 15m resistance.
- Resistance holds and a completed bearish rejection candle turns down.
- Enter only after the stricter second-confirmation close.

Every market is judged independently; BTC is not a market-direction gate.
Stops sit beyond recent structure with at least one ATR and 1.2% of room. TP1
is the first confirmed opposing 4H structure at or beyond 4R and closes 100%.
TP1 always remains between 4R and 5R in the V8 candidates. V8 also rejects
large/overheated setup candles by capping the chase score, while preserving an
uncapped R2 control. There is no time exit: unresolved positions remain OPEN.
"""

from __future__ import annotations

import csv
import gc
import gzip
import hashlib
import json
import math
import pickle
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable, Optional

import ccxt
import pandas as pd


# ===== Research window =====
DAYS = 365
PERIOD_DAYS = 91
TIMEFRAME = "15m"

# ===== Signal rules =====
ATR_PERIOD = 14
VOLUME_RATIO = 1.30
RETEST_VOLUME_RATIO = 0.80
MIN_QUOTE_VOLUME_24H = 5_000_000.0
MIN_HISTORY_BARS = 14 * 96
SIGNAL_COOLDOWN_BARS = 48
STRUCTURE_LOOKBACK_DAYS = 90
MIN_TARGET_R = 4.0
MAX_TARGET_R = 5.0
ENTRY_TRIGGER_BARS = 4
ENTRY_TRIGGER_ATR_BUFFER = 0.05
STOP_ATR_BUFFER = 0.30
MIN_STOP_ATR = 1.00
MAX_STOP_ATR = 3.00
MIN_STOP_PCT = 1.20
DIRECTIONAL_VOLUME_BARS = 12
LONG_VOLUME_PRESSURE_MIN = 0.06
SHORT_VOLUME_PRESSURE_MAX = -0.06
MAX_TARGETS = 250
BREAKOUT_BOX_BARS = 20
BREAKOUT_MIN_AGE = 2
BREAKOUT_MAX_AGE = 12
RETEST_BELOW_PCT = 0.006
RETEST_ABOVE_PCT = 0.004
RESISTANCE_LOOKBACK_BARS = 40
RESISTANCE_CONFIRM_BARS = 6
RESISTANCE_TOUCH_PCT = 0.004
RESISTANCE_BREAK_PCT = 0.003
SHORT_MIN_RSI = 35.0
SHORT_MAX_EMA20_DISTANCE_ATR = 1.5

# ===== Trade and portfolio rules =====
# A position ends only at TP1 or stop loss; no maximum holding period.
FEE_PER_SIDE = 0.0005
SLIPPAGE_PER_SIDE = 0.0004
STARTING_EQUITY = 1_000.0
RISK_PER_TRADE_PCT = 1.0
MAX_POSITIONS = 3
MAX_NEW_TRADES_PER_BAR = 1
MAX_SAME_DIRECTION_POSITIONS = 2
MAX_SAME_DIRECTION_ENTRIES_6H = 2
SYMBOL_COOLDOWN_HOURS = 24
SCHEME_CONFIGS = {
    # Exact V7 R2 control. It proves whether the new filters add value instead
    # of merely hiding the original losing trades.
    "CONTROL_R2_VOL08": {
        "minimum_room_r": 2.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": None,
        "maximum_tp1_r": None,
        "score_order": "DESC",
    },
    # Primary V8 candidate: TP1 remains >=4R, but is not allowed to use a stale
    # structure beyond 5R. A score below 1 rejects large/overheated setups.
    "V8_R2_SCORE_LT1_RR4_5": {
        "minimum_room_r": 2.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": 1.0,
        "maximum_tp1_r": 5.0,
        "score_order": "ASC",
    },
    # Sensitivity check: same V8 filters, but demand 3R of room before the
    # first known obstacle. This is research output, not a separate live rule.
    "V8_R3_SCORE_LT1_RR4_5": {
        "minimum_room_r": 3.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": 1.0,
        "maximum_tp1_r": 5.0,
        "score_order": "ASC",
    },
}
SCHEMES = tuple(SCHEME_CONFIGS)
PRIMARY_SCHEME = "V8_R2_SCORE_LT1_RR4_5"

# ===== API and output =====
PAGE_LIMIT = 1_000
FETCH_RETRIES = 5
ROOT = Path.home() / "CryptoRadar"
OUTPUT_DIR = ROOT / "backtest_1y_v8_results"
DATABASE_PATH = OUTPUT_DIR / "candidates.sqlite3"
CACHE_DIR = ROOT / "backtest_cache_1y"
CACHE_LOOKBACK_DAYS = 14

TOKENIZED_TRADFI_BASES = {
    "MSTR", "TSLA", "NVDA", "AAPL", "META", "AMZN",
    "GOOGL", "GOOG", "MSFT", "COIN", "HOOD", "PLTR",
    "NFLX", "AMD", "INTC", "SKHYNIX",
}
TOKENIZED_TRADFI_PREFIXES = ("NCSK", "NCSI", "NCFX", "NCCO")


@dataclass(frozen=True)
class Target:
    exchange_name: str
    exchange: Any
    symbol: str
    base: str


def canonical_base(symbol: str) -> str:
    raw = symbol.split(":")[0].split("/")[0].upper()
    return raw[4:] if raw.startswith("1000") and len(raw) > 4 else raw


def asset_class(base: str) -> str:
    """Label tokenized stocks/indices/FX without excluding them."""
    upper = base.upper()
    if (
        upper in TOKENIZED_TRADFI_BASES
        or upper.startswith(TOKENIZED_TRADFI_PREFIXES)
    ):
        return "TOKENIZED_TRADFI"
    return "CRYPTO"


def timeframe_ms(timeframe: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    unit = timeframe[-1]
    if unit not in units:
        raise ValueError(f"不支援的週期：{timeframe}")
    return int(timeframe[:-1]) * units[unit]


def make_exchanges() -> dict[str, Any]:
    common = {"enableRateLimit": True, "timeout": 30_000}
    return {
        "Binance": ccxt.binanceusdm(dict(common)),
        "Bitget": ccxt.bitget({
            **common,
            "options": {"defaultType": "swap"},
        }),
        "BingX": ccxt.bingx({
            **common,
            "options": {"defaultType": "swap"},
        }),
    }


def discover_unique_targets(
    exchanges: dict[str, Any],
) -> tuple[list[Target], list[dict[str, str]]]:
    selected: dict[str, Target] = {}
    errors: list[dict[str, str]] = []

    for exchange_name, exchange in exchanges.items():
        try:
            markets = exchange.load_markets()
        except Exception as exc:
            errors.append({
                "stage": "load_markets",
                "exchange": exchange_name,
                "symbol": "",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        for symbol, market in markets.items():
            base = canonical_base(symbol)
            valid = (
                market.get("active") is not False
                and bool(market.get("swap"))
                and bool(market.get("linear"))
                and market.get("quote") == "USDT"
                and market.get("settle") in (None, "USDT")
            )
            if valid:
                selected.setdefault(
                    base,
                    Target(exchange_name, exchange, symbol, base),
                )

    return list(selected.values()), errors


def prefilter_liquid_targets(
    targets: list[Target],
    exchanges: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[Target]:
    """Rank markets with one ticker request per exchange before OHLCV download."""
    tickers_by_exchange: dict[str, dict[str, Any]] = {}
    for exchange_name, exchange in exchanges.items():
        try:
            tickers_by_exchange[exchange_name] = exchange.fetch_tickers()
        except Exception as exc:
            errors.append({
                "stage": "fetch_tickers",
                "exchange": exchange_name,
                "symbol": "",
                "error": f"{type(exc).__name__}: {exc}",
            })
            tickers_by_exchange[exchange_name] = {}

    ranked: list[tuple[float, Target]] = []
    for target in targets:
        ticker = tickers_by_exchange.get(
            target.exchange_name, {}
        ).get(target.symbol, {})
        last = float(ticker.get("last") or 0.0)
        quote_volume = float(ticker.get("quoteVolume") or 0.0)
        if quote_volume <= 0:
            quote_volume = float(
                ticker.get("baseVolume") or 0.0
            ) * last
        if quote_volume >= MIN_QUOTE_VOLUME_24H:
            ranked.append((quote_volume, target))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [target for _, target in ranked[:MAX_TARGETS]]


def _fetch_closed_ohlcv_uncached(
    target: Target,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tf_ms = timeframe_ms(TIMEFRAME)
    now_ms = int(time.time() * 1_000)
    current_bucket = (now_ms // tf_ms) * tf_ms
    end_ms = current_bucket - 1
    start_ms = end_ms - DAYS * 86_400_000 + 1
    cursor = start_ms
    rows: list[list[float]] = []
    pages = 0
    empty_pages = 0

    while cursor <= end_ms:
        batch: Optional[list[list[float]]] = None
        last_error: Optional[Exception] = None

        for attempt in range(FETCH_RETRIES):
            try:
                batch = target.exchange.fetch_ohlcv(
                    target.symbol,
                    TIMEFRAME,
                    since=cursor,
                    limit=PAGE_LIMIT,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < FETCH_RETRIES:
                    message = str(exc)
                    if "109429" in message:
                        time.sleep(min(15 * (attempt + 1), 60))
                    else:
                        time.sleep(min(2 ** attempt, 8))

        if batch is None:
            assert last_error is not None
            raise last_error
        if not batch:
            # Empty pages also occur across closed sessions and temporary API
            # holes. Keep probing forward instead of truncating the history at
            # the first empty response after some rows were already collected.
            if cursor < end_ms:
                empty_pages += 1
                if empty_pages > 40:
                    break
                cursor += PAGE_LIMIT * tf_ms
                continue
            break

        empty_pages = 0
        pages += 1
        rows.extend(
            row for row in batch
            if start_ms <= int(row[0]) <= end_ms
        )
        last_ts = max(int(row[0]) for row in batch)
        next_cursor = last_ts + tf_ms
        if next_cursor <= cursor:
            raise RuntimeError("K線翻頁停滯")
        cursor = next_cursor
        if last_ts >= end_ms - tf_ms:
            break

    if not rows:
        raise RuntimeError("沒有取得已收完的K線")

    data = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    del rows
    data = data.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["time"] = pd.to_datetime(data["ts"], unit="ms", utc=True)
    data = data.dropna().set_index("time").drop(columns="ts")

    diffs = data.index.to_series().diff().dropna()
    gaps = int((diffs > pd.Timedelta(milliseconds=tf_ms * 1.5)).sum())
    span_days = (
        (data.index[-1] - data.index[0]).total_seconds() / 86_400
        if len(data) > 1 else 0.0
    )
    expected = max(1, math.floor(span_days * 86_400_000 / tf_ms) + 1)
    quality = {
        "exchange": target.exchange_name,
        "symbol": target.symbol,
        "base": target.base,
        "asset_class": asset_class(target.base),
        "history_policy": (
            "FROM_FIRST_AVAILABLE"
            if span_days < DAYS - 2 else "FULL_WINDOW"
        ),
        "rows": len(data),
        "pages": pages,
        "first_candle": data.index[0].isoformat(),
        "last_candle": data.index[-1].isoformat(),
        "span_days": round(span_days, 2),
        "coverage_pct": round(min(100.0, len(data) / expected * 100), 2),
        "detected_gaps": gaps,
        "has_14d_indicator_warmup": len(data) >= MIN_HISTORY_BARS,
    }
    return data, quality


def fetch_closed_ohlcv(
    target: Target,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Binance's existing cache is complete. Bitget/BingX need a new cache key
    # so V6 repairs histories that V5 truncated at an empty API page.
    repair_tag = "" if target.exchange_name == "Binance" else "|gapfix_v1"
    identity = (
        f"{target.exchange_name}|{target.symbol}|{TIMEFRAME}|{DAYS}"
        f"{repair_tag}"
    )
    cache_key = hashlib.sha256(identity.encode()).hexdigest()
    cache_path = CACHE_DIR / f"stable_{cache_key}.pkl.gz"

    if cache_path.exists():
        try:
            with gzip.open(cache_path, "rb") as handle:
                data, quality = pickle.load(handle)
            quality = dict(quality)
            quality["cache"] = "HIT"
            return data, quality
        except Exception:
            cache_path.unlink(missing_ok=True)

    # V4 included the UTC date in each cache filename. Reuse any cache made
    # during the previous two weeks, then promote it to the permanent V5 key.
    now = pd.Timestamp.now(tz="UTC").normalize()
    for age in range(CACHE_LOOKBACK_DAYS + 1):
        cache_day = (now - pd.Timedelta(days=age)).strftime("%Y%m%d")
        legacy_key = hashlib.sha256(
            f"{identity}|{cache_day}".encode()
        ).hexdigest()
        legacy_path = CACHE_DIR / f"{legacy_key}.pkl.gz"
        if not legacy_path.exists():
            continue
        try:
            with gzip.open(legacy_path, "rb") as handle:
                data, quality = pickle.load(handle)
            quality = dict(quality)
            quality["cache"] = "LEGACY_HIT"
            try:
                # The payload itself is unchanged. Copying the compressed V4
                # cache is much faster than decompressing/recompressing a full
                # year for every market.
                shutil.copy2(legacy_path, cache_path)
            except Exception:
                cache_path.unlink(missing_ok=True)
            return data, quality
        except Exception:
            continue

    data, quality = _fetch_closed_ohlcv_uncached(target)
    quality = dict(quality)
    quality["cache"] = "MISS"
    try:
        with gzip.open(cache_path, "wb", compresslevel=3) as handle:
            pickle.dump((data, quality), handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        cache_path.unlink(missing_ok=True)
    return data, quality


def prepare_benchmark(data: pd.DataFrame) -> pd.DataFrame:
    four_hour = data.resample("4h", label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()
    four_hour["ema20"] = four_hour["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    four_hour["ema50"] = four_hour["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    four_hour["market_bull"] = (
        (four_hour["ema20"] > four_hour["ema50"])
        & (four_hour["close"] > four_hour["ema20"])
        & (four_hour["ema20"] > four_hour["ema20"].shift(3))
    )
    four_hour["market_bear"] = (
        (four_hour["ema20"] < four_hour["ema50"])
        & (four_hour["close"] < four_hour["ema20"])
        & (four_hour["ema20"] < four_hour["ema20"].shift(3))
    )
    return four_hour[["market_bull", "market_bear"]].shift(1)


def prepare_indicators(
    data: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    list[tuple[pd.Timestamp, pd.Timestamp, float]],
    list[tuple[pd.Timestamp, pd.Timestamp, float]],
]:
    frame = data.copy()
    frame["history_bars"] = range(1, len(frame) + 1)
    previous_close = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    frame["atr"] = true_range.rolling(ATR_PERIOD).mean()
    delta = frame["close"].diff()
    gain = delta.clip(lower=0).ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14, adjust=False, min_periods=14
    ).mean()
    relative_strength = gain / loss.replace(0, float("nan"))
    frame["rsi14"] = 100 - (100 / (1 + relative_strength))
    frame["avg_volume"] = frame["volume"].shift(1).rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["avg_volume"]
    candle_span = (frame["high"] - frame["low"]).replace(0, float("nan"))
    signed_volume = frame["volume"] * (
        (2 * frame["close"] - frame["high"] - frame["low"]) / candle_span
    ).clip(-1.0, 1.0)
    frame["volume_pressure"] = (
        signed_volume.rolling(DIRECTIONAL_VOLUME_BARS).sum()
        / frame["volume"].rolling(DIRECTIONAL_VOLUME_BARS).sum()
    )
    frame["quote_volume_24h"] = (
        frame["close"] * frame["volume"]
    ).rolling(96).sum()
    frame["ema20_15m"] = frame["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    frame["ema50_15m"] = frame["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    frame["ema50_rising"] = (
        frame["ema50_15m"] > frame["ema50_15m"].shift(8)
    )
    frame["ema50_falling"] = (
        frame["ema50_15m"] < frame["ema50_15m"].shift(8)
    )

    four_hour = frame.resample("4h", label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()
    four_hour["ema20"] = four_hour["close"].ewm(
        span=20, adjust=False, min_periods=20
    ).mean()
    four_hour["ema50"] = four_hour["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    four_hour["bull4h"] = (
        (four_hour["ema20"] > four_hour["ema50"])
        & (four_hour["close"] > four_hour["ema20"])
        & (four_hour["ema20"] > four_hour["ema20"].shift(3))
    )
    four_hour["bear4h"] = (
        (four_hour["ema20"] < four_hour["ema50"])
        & (four_hour["close"] < four_hour["ema20"])
        & (four_hour["ema20"] < four_hour["ema20"].shift(3))
    )
    pivot_high = (
        (four_hour["high"] > four_hour["high"].shift(1))
        & (four_hour["high"] > four_hour["high"].shift(2))
        & (four_hour["high"] > four_hour["high"].shift(-1))
        & (four_hour["high"] > four_hour["high"].shift(-2))
    )
    pivot_low = (
        (four_hour["low"] < four_hour["low"].shift(1))
        & (four_hour["low"] < four_hour["low"].shift(2))
        & (four_hour["low"] < four_hour["low"].shift(-1))
        & (four_hour["low"] < four_hour["low"].shift(-2))
    )
    # A two-bars-on-each-side pivot becomes knowable only after the second
    # following 4H candle has closed. Store it once and enforce that
    # confirmation timestamp later so this optimization introduces no
    # look-ahead.
    cached_pivot_highs = [
        (timestamp, timestamp + pd.Timedelta(hours=12), float(value))
        for timestamp, value in four_hour.loc[pivot_high, "high"].items()
    ]
    cached_pivot_lows = [
        (timestamp, timestamp + pd.Timedelta(hours=12), float(value))
        for timestamp, value in four_hour.loc[pivot_low, "low"].items()
    ]
    completed_four_hour = four_hour[["bull4h", "bear4h"]].shift(1)

    hourly = frame.resample("1h", label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()
    hourly["ema50"] = hourly["close"].ewm(
        span=50, adjust=False, min_periods=50
    ).mean()
    hourly["ema200"] = hourly["close"].ewm(
        span=200, adjust=False, min_periods=200
    ).mean()
    hourly["bull1h"] = (
        (hourly["ema50"] > hourly["ema200"])
        & (hourly["close"] > hourly["ema50"])
    )
    hourly["bear1h"] = (
        (hourly["ema50"] < hourly["ema200"])
        & (hourly["close"] < hourly["ema50"])
    )
    completed_hourly = hourly[["bull1h", "bear1h"]].shift(1)

    frame = frame.join(completed_four_hour, how="left").ffill()
    frame = frame.join(completed_hourly, how="left").ffill()
    frame = frame.dropna()
    return frame, cached_pivot_highs, cached_pivot_lows


def confirmed_structure_targets(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    cached_pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    minimum_room_r: float = MIN_TARGET_R,
) -> Optional[tuple[float, float, float, float, float, float, float]]:
    bars = STRUCTURE_LOOKBACK_DAYS * 96
    current_bucket = frame.index[signal_i].floor("4h")
    window_start = frame.index[max(0, signal_i - bars)]

    if direction == "LONG":
        cached_pivots = cached_pivot_highs
        levels = sorted(set(
            value
            for pivot_time, confirmed_time, value in cached_pivots
            if pivot_time >= window_start
            and confirmed_time <= current_bucket
            and value > entry
        ))
    else:
        cached_pivots = cached_pivot_lows
        levels = sorted(set(
            value
            for pivot_time, confirmed_time, value in cached_pivots
            if pivot_time >= window_start
            and confirmed_time <= current_bucket
            and value < entry
        ), reverse=True)

    if not levels:
        return None

    nearest_rr = abs(levels[0] - entry) / stop_distance
    if nearest_rr < minimum_room_r:
        return None

    # TP1 remains at least 4R. Research variants may tolerate a known nearer
    # structure only when that first obstacle still leaves 2R or 3R of room.
    target_levels = [
        level for level in levels
        if abs(level - entry) / stop_distance >= MIN_TARGET_R
    ]
    if not target_levels:
        return None
    padded_levels = (
        target_levels + [target_levels[-1], target_levels[-1]]
    )[:3]
    tp1, tp2, tp3 = padded_levels
    rr1 = abs(tp1 - entry) / stop_distance
    rr2 = abs(tp2 - entry) / stop_distance
    rr3 = abs(tp3 - entry) / stop_distance
    return tp1, tp2, tp3, rr1, rr2, rr3, nearest_rr


def bar_hits_stop(
    direction: str,
    bar_open: float,
    high: float,
    low: float,
    stop: float,
) -> Optional[float]:
    if direction == "LONG":
        if bar_open <= stop:
            return bar_open
        if low <= stop:
            return stop
    else:
        if bar_open >= stop:
            return bar_open
        if high >= stop:
            return stop
    return None


def bar_hits_target(
    direction: str,
    bar_open: float,
    high: float,
    low: float,
    target: float,
) -> bool:
    if direction == "LONG":
        return bar_open >= target or high >= target
    return bar_open <= target or low <= target


def finish_result(
    direction: str,
    entry: float,
    stop_distance: float,
    gross_r: float,
    exit_cost_r: float,
    exit_i: int,
    exit_price: float,
    exit_reason: str,
    timed_out: int,
) -> dict[str, Any]:
    entry_cost_r = entry * (FEE_PER_SIDE + SLIPPAGE_PER_SIDE) / stop_distance
    cost_r = entry_cost_r + exit_cost_r
    net_r = gross_r - cost_r
    return {
        "exit_i": exit_i,
        "exit_price": exit_price,
        "outcome": "WIN" if exit_reason == "TP1_ALL" else "LOSS",
        "exit_reason": exit_reason,
        "timed_out": timed_out,
        "gross_r": gross_r,
        "cost_r": cost_r,
        "net_r": net_r,
    }


def finish_open_result(
    direction: str,
    entry: float,
    stop_distance: float,
    exit_i: int,
    mark_price: float,
) -> dict[str, Any]:
    sign = 1.0 if direction == "LONG" else -1.0
    gross_r = sign * (mark_price - entry) / stop_distance
    entry_cost_r = (
        entry * (FEE_PER_SIDE + SLIPPAGE_PER_SIDE) / stop_distance
    )
    return {
        "exit_i": exit_i,
        "exit_price": mark_price,
        "outcome": "OPEN",
        "exit_reason": "OPEN_AT_END",
        "timed_out": 0,
        "gross_r": gross_r,
        "cost_r": entry_cost_r,
        "net_r": gross_r - entry_cost_r,
    }


def simulate_tp1_all(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
) -> dict[str, Any]:
    end_i = len(frame) - 1
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    opens = frame["open"].to_numpy(copy=False)[entry_i:]
    highs = frame["high"].to_numpy(copy=False)[entry_i:]
    lows = frame["low"].to_numpy(copy=False)[entry_i:]

    if direction == "LONG":
        stop_hits = (opens <= stop) | (lows <= stop)
        target_hits = (opens >= tp1) | (highs >= tp1)
    else:
        stop_hits = (opens >= stop) | (highs >= stop)
        target_hits = (opens <= tp1) | (lows <= tp1)

    stop_positions = stop_hits.nonzero()[0]
    target_positions = target_hits.nonzero()[0]
    stop_pos = int(stop_positions[0]) if len(stop_positions) else None
    target_pos = int(target_positions[0]) if len(target_positions) else None

    # Same-candle ambiguity remains conservative: stop is evaluated first.
    if stop_pos is not None and (
        target_pos is None or stop_pos <= target_pos
    ):
        i = entry_i + stop_pos
        bar_open = float(opens[stop_pos])
        if direction == "LONG":
            stop_fill = bar_open if bar_open <= stop else stop
        else:
            stop_fill = bar_open if bar_open >= stop else stop
        gross_r = sign * (stop_fill - entry) / stop_distance
        return finish_result(
            direction, entry, stop_distance, gross_r,
            stop_fill * cost_rate / stop_distance,
            i, stop_fill, "STOP", 0,
        )

    if target_pos is not None:
        i = entry_i + target_pos
        gross_r = sign * (tp1 - entry) / stop_distance
        return finish_result(
            direction, entry, stop_distance, gross_r,
            tp1 * cost_rate / stop_distance,
            i, tp1, "TP1_ALL", 0,
        )

    mark_price = float(frame["close"].iat[end_i])
    return finish_open_result(
        direction, entry, stop_distance, end_i, mark_price,
    )


def simulate_scaled(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    stop_distance: float,
) -> dict[str, Any]:
    end_i = min(entry_i + MAX_HOLD_BARS - 1, len(frame) - 1)
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = FEE_PER_SIDE + SLIPPAGE_PER_SIDE
    remaining = 1.0
    gross_r = 0.0
    exit_cost_r = 0.0
    weighted_exit = 0.0
    stage = 0

    def close_fraction(price: float, fraction: float) -> None:
        nonlocal remaining, gross_r, exit_cost_r, weighted_exit
        fraction = min(fraction, remaining)
        gross_r += (
            fraction * sign * (price - entry) / stop_distance
        )
        exit_cost_r += (
            fraction * price * cost_rate / stop_distance
        )
        weighted_exit += fraction * price
        remaining -= fraction

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        bar_open = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])

        if stage == 0:
            stop_fill = bar_hits_stop(
                direction, bar_open, high, low, stop
            )
            if stop_fill is not None:
                close_fraction(stop_fill, remaining)
                return finish_result(
                    direction, entry, stop_distance, gross_r,
                    exit_cost_r, i, weighted_exit, "STOP", 0,
                )

            if not bar_hits_target(
                direction, bar_open, high, low, tp1
            ):
                continue

            close_fraction(tp1, 0.50)
            stage = 1

            # Intrabar order is unknown. Conservatively, if the TP1 candle
            # also touched entry, the remainder is treated as protected out.
            be_fill = bar_hits_stop(
                direction, bar_open, high, low, entry
            )
            if be_fill is not None:
                close_fraction(be_fill, remaining)
                return finish_result(
                    direction, entry, stop_distance, gross_r,
                    exit_cost_r, i, weighted_exit,
                    "TP1_THEN_ENTRY_PROTECT", 0,
                )

        else:
            # Once TP1 has filled, entry price is the protection level.
            be_fill = bar_hits_stop(
                direction, bar_open, high, low, entry
            )
            if be_fill is not None:
                close_fraction(be_fill, remaining)
                return finish_result(
                    direction, entry, stop_distance, gross_r,
                    exit_cost_r, i, weighted_exit,
                    "ENTRY_PROTECT_END", 0,
                )

        if stage == 1 and bar_hits_target(
            direction, bar_open, high, low, tp2
        ):
            close_fraction(tp2, 0.25)
            stage = 2

        if stage == 2 and bar_hits_target(
            direction, bar_open, high, low, tp3
        ):
            close_fraction(tp3, remaining)
            return finish_result(
                direction, entry, stop_distance, gross_r,
                exit_cost_r, i, weighted_exit, "TP3_END", 0,
            )

    exit_price = float(frame["close"].iloc[end_i])
    close_fraction(exit_price, remaining)
    return finish_result(
        direction, entry, stop_distance, gross_r,
        exit_cost_r, end_i, weighted_exit, "TIMEOUT_CLOSE", 1,
    )


def period_name(
    signal_time: pd.Timestamp,
    research_start: pd.Timestamp,
) -> str:
    elapsed = max(
        0.0,
        (signal_time - research_start).total_seconds(),
    )
    number = min(4, int(elapsed // (PERIOD_DAYS * 86_400)) + 1)
    return f"P{number}"


def long_breakout_retest_level(
    frame: pd.DataFrame,
    signal_i: int,
) -> Optional[float]:
    """Return the broken resistance when the current bar confirms its retest."""
    signal_bar = frame.iloc[signal_i]
    previous = frame.iloc[signal_i - 1]
    signal_range = max(
        float(signal_bar["high"] - signal_bar["low"]),
        1e-12,
    )
    signal_close_location = float(
        signal_bar["close"] - signal_bar["low"]
    ) / signal_range
    signal_body = abs(
        float(signal_bar["close"] - signal_bar["open"])
    )
    signal_lower_wick = float(
        min(signal_bar["open"], signal_bar["close"])
        - signal_bar["low"]
    )

    if not (
        signal_bar["close"] > signal_bar["open"]
        and signal_bar["close"] > previous["close"]
        and signal_close_location >= 0.60
        and signal_lower_wick >= max(
            signal_body * 0.20,
            signal_range * 0.05,
        )
        and signal_bar["volume_ratio"] >= RETEST_VOLUME_RATIO
    ):
        return None

    oldest = signal_i - BREAKOUT_MAX_AGE
    newest = signal_i - BREAKOUT_MIN_AGE
    for breakout_i in range(newest, oldest - 1, -1):
        if breakout_i < BREAKOUT_BOX_BARS:
            continue

        breakout = frame.iloc[breakout_i]
        box = frame.iloc[
            breakout_i - BREAKOUT_BOX_BARS:breakout_i
        ]
        resistance = float(box["high"].max())
        breakout_range = max(
            float(breakout["high"] - breakout["low"]),
            1e-12,
        )
        breakout_close_location = float(
            breakout["close"] - breakout["low"]
        ) / breakout_range

        valid_breakout = (
            breakout["close"] > resistance
            and breakout["close"] > breakout["open"]
            and breakout_close_location >= 0.65
            and breakout["volume_ratio"] >= VOLUME_RATIO
        )
        if not valid_breakout:
            continue

        after_breakout = frame.iloc[
            breakout_i + 1:signal_i + 1
        ]
        if after_breakout.empty:
            continue

        # A close materially below the level means the breakout failed.
        if (
            after_breakout["close"]
            < resistance * (1.0 - RETEST_BELOW_PCT)
        ).any():
            continue

        retest = (
            float(signal_bar["low"])
            <= resistance * (1.0 + RETEST_ABOVE_PCT)
            and float(signal_bar["low"])
            >= resistance * (1.0 - RETEST_BELOW_PCT)
            and float(signal_bar["close"]) > resistance
        )
        if retest:
            return resistance

    return None


def short_resistance_rejection_level(
    frame: pd.DataFrame,
    signal_i: int,
    cached_pivots: list[tuple[pd.Timestamp, pd.Timestamp, float]],
) -> Optional[float]:
    """Use the nearest confirmed 4H pivot high as resistance."""
    signal_bar = frame.iloc[signal_i]
    previous = frame.iloc[signal_i - 1]
    current_bucket = frame.index[signal_i].floor("4h")
    window_start = frame.index[
        max(0, signal_i - STRUCTURE_LOOKBACK_DAYS * 96)
    ]
    levels = sorted(
        {
            value
            for pivot_time, confirmed_time, value in cached_pivots
            if pivot_time >= window_start
            and confirmed_time <= current_bucket
            and value > float(signal_bar["close"])
        }
    )
    if not levels:
        return None

    resistance = levels[0]
    recent = frame.iloc[
        max(0, signal_i - RESISTANCE_CONFIRM_BARS):signal_i + 1
    ]
    touched = (
        recent["high"]
        >= resistance * (1.0 - RESISTANCE_TOUCH_PCT)
    ).any()
    held = (
        recent["close"]
        <= resistance * (1.0 + RESISTANCE_BREAK_PCT)
    ).all()

    candle_range = max(
        float(signal_bar["high"] - signal_bar["low"]),
        1e-12,
    )
    body = abs(
        float(signal_bar["close"] - signal_bar["open"])
    )
    upper_wick = float(
        signal_bar["high"]
        - max(signal_bar["open"], signal_bar["close"])
    )
    close_location = float(
        signal_bar["close"] - signal_bar["low"]
    ) / candle_range

    rejected = (
        signal_bar["close"] < signal_bar["open"]
        and signal_bar["close"] < previous["low"]
        and close_location <= 0.35
        and upper_wick >= max(body * 0.35, candle_range * 0.10)
        and signal_bar["volume_ratio"] >= RETEST_VOLUME_RATIO
    )

    if touched and held and rejected:
        return resistance
    return None


def triggered_entry(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    atr: float,
    invalidation: float,
) -> Optional[tuple[int, float, int]]:
    signal_bar = frame.iloc[signal_i]
    if direction == "LONG":
        trigger = float(signal_bar["high"]) + atr * ENTRY_TRIGGER_ATR_BUFFER
    else:
        trigger = float(signal_bar["low"]) - atr * ENTRY_TRIGGER_ATR_BUFFER

    final_i = min(signal_i + ENTRY_TRIGGER_BARS, len(frame) - 2)
    for confirmation_i in range(signal_i + 1, final_i + 1):
        bar = frame.iloc[confirmation_i]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        # If invalidation and trigger occur in the same 15m candle, the
        # intrabar order is unknown; conservatively cancel the setup.
        if direction == "LONG":
            if low <= invalidation:
                return None
            if close >= trigger:
                entry_i = confirmation_i + 1
                return (
                    entry_i,
                    float(frame.iloc[entry_i]["open"]),
                    confirmation_i,
                )
        else:
            if high >= invalidation:
                return None
            if close <= trigger:
                entry_i = confirmation_i + 1
                return (
                    entry_i,
                    float(frame.iloc[entry_i]["open"]),
                    confirmation_i,
                )
    return None


def candidate_rows(
    target: Target,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    cached_pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    cached_pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    funnel: dict[str, int],
) -> Iterable[tuple[Any, ...]]:
    final_signal_i = len(frame) - ENTRY_TRIGGER_BARS - 2
    last_signal_i = {
        scheme: -SIGNAL_COOLDOWN_BARS for scheme in SCHEMES
    }

    # Build every cheap condition as a vector once. This replaces roughly
    # 35,000 pandas ``iloc``/Series operations per market with a few native
    # vector operations, while producing the same eligible candle set.
    candle_range_s = (frame["high"] - frame["low"]).clip(lower=1e-12)
    candle_body_s = (frame["close"] - frame["open"]).abs()
    close_location_s = (
        (frame["close"] - frame["low"]) / candle_range_s
    )
    lower_wick_s = (
        frame[["open", "close"]].min(axis=1) - frame["low"]
    )
    upper_wick_s = (
        frame["high"] - frame[["open", "close"]].max(axis=1)
    )
    atr_s = frame["atr"]
    range_atr_s = candle_range_s / atr_s
    atr_pct_s = atr_s / frame["close"] * 100
    body_ratio_s = candle_body_s / candle_range_s

    common_mask = (
        (frame["history_bars"] >= MIN_HISTORY_BARS)
        & (frame["quote_volume_24h"] >= MIN_QUOTE_VOLUME_24H)
        & atr_s.notna()
        & (atr_s > 0)
        & atr_pct_s.between(0.20, 3.00)
        & range_atr_s.between(0.35, 2.50)
        & (body_ratio_s >= 0.35)
    )
    long_context_mask = (
        common_mask
        & frame["bull4h"].astype(bool)
        & frame["ema50_rising"].astype(bool)
        & (frame["ema20_15m"] > frame["ema50_15m"])
        & (frame["close"] > frame["ema50_15m"])
        & (frame["volume_pressure"] >= LONG_VOLUME_PRESSURE_MIN)
        & (frame["close"] > frame["open"])
        & (frame["close"] > frame["close"].shift(1))
        & (close_location_s >= 0.60)
        & (lower_wick_s >= candle_body_s * 0.20)
        & (lower_wick_s >= candle_range_s * 0.05)
        & (frame["volume_ratio"] >= RETEST_VOLUME_RATIO)
    )
    short_context_mask = (
        common_mask
        & frame["bear4h"].astype(bool)
        & frame["ema50_falling"].astype(bool)
        & (frame["ema20_15m"] < frame["ema50_15m"])
        & (frame["close"] < frame["ema20_15m"])
        & (frame["rsi14"] >= SHORT_MIN_RSI)
        & (frame["volume_pressure"] <= SHORT_VOLUME_PRESSURE_MAX)
        & (
            (frame["ema20_15m"] - frame["close"]) / atr_s
            <= SHORT_MAX_EMA20_DISTANCE_ATR
        )
        & (frame["close"] < frame["open"])
        & (frame["close"] < frame["low"].shift(1))
        & (close_location_s <= 0.35)
        & (upper_wick_s >= candle_body_s * 0.35)
        & (upper_wick_s >= candle_range_s * 0.10)
        & (frame["volume_ratio"] >= RETEST_VOLUME_RATIO)
    )
    eligible_positions = (
        (long_context_mask | short_context_mask)
        .fillna(False)
        .to_numpy()
        .nonzero()[0]
    )
    funnel["bars"] = funnel.get("bars", 0) + len(frame)
    funnel["common"] = funnel.get("common", 0) + int(common_mask.sum())
    funnel["long_context"] = (
        funnel.get("long_context", 0) + int(long_context_mask.sum())
    )
    funnel["short_context"] = (
        funnel.get("short_context", 0) + int(short_context_mask.sum())
    )

    for signal_i in eligible_positions:
        signal_i = int(signal_i)
        if signal_i < 220 or signal_i > final_signal_i:
            continue
        if all(
            signal_i - last_signal_i[scheme] < SIGNAL_COOLDOWN_BARS
            for scheme in SCHEMES
        ):
            continue

        row = frame.iloc[signal_i]
        atr = float(row["atr"])
        candle_range = max(float(row["high"] - row["low"]), 1e-12)
        range_atr = candle_range / atr

        # Cheap vectorized conditions must pass before running either of the
        # expensive structure searches. The old version recalculated up to
        # 90 days of 4H candles on almost every 15m bar, even when trend or
        # volume had already invalidated the setup.
        long_context = bool(long_context_mask.iloc[signal_i])
        short_context = bool(short_context_mask.iloc[signal_i])
        if not long_context and not short_context:
            continue

        breakout_level = (
            long_breakout_retest_level(frame, signal_i)
            if long_context else None
        )
        resistance_level = (
            short_resistance_rejection_level(
                frame, signal_i, cached_pivot_highs
            )
            if short_context else None
        )
        long_signal = long_context and breakout_level is not None
        short_signal = short_context and resistance_level is not None
        if not long_signal and not short_signal:
            continue
        funnel["structure_setup"] = funnel.get("structure_setup", 0) + 1

        direction = "LONG" if long_signal else "SHORT"
        structure = frame.iloc[signal_i - 6:signal_i + 1]
        if direction == "LONG":
            structure_stop = (
                float(structure["low"].min()) - atr * STOP_ATR_BUFFER
            )
        else:
            structure_stop = (
                float(structure["high"].max()) + atr * STOP_ATR_BUFFER
            )

        triggered = triggered_entry(
            frame, signal_i, direction, atr, structure_stop
        )
        if triggered is None:
            continue
        funnel["double_confirm_entry"] = (
            funnel.get("double_confirm_entry", 0) + 1
        )
        entry_i, entry, confirmation_i = triggered
        confirmation_bar = frame.iloc[confirmation_i]
        confirmation_volume = float(confirmation_bar["volume_ratio"])

        if direction == "LONG":
            stop = min(
                structure_stop,
                entry - atr * MIN_STOP_ATR,
                entry * (1.0 - MIN_STOP_PCT / 100),
            )
            stop_distance = entry - stop
        else:
            stop = max(
                structure_stop,
                entry + atr * MIN_STOP_ATR,
                entry * (1.0 + MIN_STOP_PCT / 100),
            )
            stop_distance = stop - entry

        if entry <= 0 or stop <= 0 or stop_distance <= 0:
            continue
        stop_atr = stop_distance / atr
        stop_pct = stop_distance / entry * 100
        if (
            stop_atr > MAX_STOP_ATR
            or not MIN_STOP_PCT <= stop_pct <= 3.5
        ):
            continue
        funnel["double_confirm_stop"] = (
            funnel.get("double_confirm_stop", 0) + 1
        )

        signal_time = frame.index[signal_i]
        period = period_name(signal_time, research_start)
        score = confirmation_volume * min(range_atr, 2.0)
        confirmation_pressure = float(confirmation_bar["volume_pressure"])

        for scheme, config in SCHEME_CONFIGS.items():
            if signal_i - last_signal_i[scheme] < SIGNAL_COOLDOWN_BARS:
                continue
            if confirmation_volume < config["confirm_volume"]:
                continue
            funnel[f"{scheme}_volume"] = (
                funnel.get(f"{scheme}_volume", 0) + 1
            )
            maximum_chase_score = config["maximum_chase_score"]
            if (
                maximum_chase_score is not None
                and score >= maximum_chase_score
            ):
                continue
            funnel[f"{scheme}_score"] = (
                funnel.get(f"{scheme}_score", 0) + 1
            )

            targets = confirmed_structure_targets(
                frame,
                signal_i,
                direction,
                entry,
                stop_distance,
                cached_pivot_highs,
                cached_pivot_lows,
                minimum_room_r=config["minimum_room_r"],
            )
            if targets is None:
                continue
            tp1, tp2, tp3, rr1, rr2, rr3, nearest_rr = targets
            funnel[f"{scheme}_target_4r"] = (
                funnel.get(f"{scheme}_target_4r", 0) + 1
            )
            maximum_tp1_r = config["maximum_tp1_r"]
            if maximum_tp1_r is not None and rr1 > maximum_tp1_r:
                continue
            funnel[f"{scheme}_target_range"] = (
                funnel.get(f"{scheme}_target_range", 0) + 1
            )
            last_signal_i[scheme] = signal_i

            result = simulate_tp1_all(
                frame, entry_i, direction, entry, stop, tp1, stop_distance
            )
            funnel[f"{scheme}_candidate"] = (
                funnel.get(f"{scheme}_candidate", 0) + 1
            )
            trade_window = frame.iloc[entry_i:result["exit_i"] + 1]
            if direction == "LONG":
                mfe_r = (
                    float(trade_window["high"].max()) - entry
                ) / stop_distance
                mae_r = (
                    entry - float(trade_window["low"].min())
                ) / stop_distance
            else:
                mfe_r = (
                    entry - float(trade_window["low"].min())
                ) / stop_distance
                mae_r = (
                    float(trade_window["high"].max()) - entry
                ) / stop_distance
            holding_bars = result["exit_i"] - entry_i + 1
            yield (
                target.exchange_name,
                target.symbol,
                target.base,
                direction,
                signal_time.isoformat(),
                frame.index[entry_i].isoformat(),
                frame.index[result["exit_i"]].isoformat(),
                period,
                scheme,
                entry,
                stop,
                tp1,
                tp2,
                tp3,
                rr1,
                rr2,
                rr3,
                result["exit_price"],
                score,
                result["outcome"],
                result["exit_reason"],
                result["timed_out"],
                result["gross_r"],
                result["cost_r"],
                result["net_r"],
                confirmation_volume,
                range_atr,
                confirmation_pressure,
                nearest_rr,
                stop_atr,
                stop_pct,
                entry_i - signal_i,
                holding_bars,
                mfe_r,
                mae_r,
            )


INSERT_SQL = """
    INSERT INTO candidates (
        exchange, symbol, base, direction, signal_time, entry_time, exit_time,
        period, scheme, entry, stop, tp1, tp2, tp3, rr1, rr2, rr3,
        exit_price, score, outcome, exit_reason, timed_out,
        gross_r, cost_r, net_r, confirmation_volume, signal_range_atr,
        confirmation_pressure, nearest_rr, stop_atr, stop_pct,
        entry_delay_bars, holding_bars, mfe_r, mae_r
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
"""


def open_database() -> sqlite3.Connection:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in (
        DATABASE_PATH,
        Path(f"{DATABASE_PATH}-wal"),
        Path(f"{DATABASE_PATH}-shm"),
    ):
        if path.exists():
            path.unlink()

    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("""
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            base TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal_time TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT NOT NULL,
            period TEXT NOT NULL,
            scheme TEXT NOT NULL,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            rr1 REAL NOT NULL,
            rr2 REAL NOT NULL,
            rr3 REAL NOT NULL,
            exit_price REAL NOT NULL,
            score REAL NOT NULL,
            outcome TEXT NOT NULL,
            exit_reason TEXT NOT NULL,
            timed_out INTEGER NOT NULL,
            gross_r REAL NOT NULL,
            cost_r REAL NOT NULL,
            net_r REAL NOT NULL,
            confirmation_volume REAL NOT NULL,
            signal_range_atr REAL NOT NULL,
            confirmation_pressure REAL NOT NULL,
            nearest_rr REAL NOT NULL,
            stop_atr REAL NOT NULL,
            stop_pct REAL NOT NULL,
            entry_delay_bars INTEGER NOT NULL,
            holding_bars INTEGER NOT NULL,
            mfe_r REAL NOT NULL,
            mae_r REAL NOT NULL
        )
    """)
    connection.execute("""
        CREATE INDEX candidate_order
        ON candidates(scheme, period, entry_time, score DESC)
    """)
    connection.commit()
    return connection


def row_dict(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
) -> dict[str, Any]:
    return {
        item[0]: value
        for item, value in zip(cursor.description, row)
    }


def select_portfolio(
    connection: sqlite3.Connection,
    scheme: str,
    period: Optional[str] = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT exchange, symbol, base, direction, signal_time, entry_time,
               exit_time, period, scheme, entry, stop, tp1, tp2, tp3,
               rr1, rr2, rr3, exit_price, score, outcome, exit_reason,
               timed_out, gross_r, cost_r, net_r, confirmation_volume,
               signal_range_atr, confirmation_pressure, nearest_rr, stop_atr,
               stop_pct, entry_delay_bars, holding_bars, mfe_r, mae_r
        FROM candidates
        WHERE scheme = ?
    """
    params: list[Any] = [scheme]
    if period is not None:
        sql += " AND period = ?"
        params.append(period)
    score_order = SCHEME_CONFIGS[scheme]["score_order"]
    if score_order not in {"ASC", "DESC"}:
        raise ValueError(f"無效的score_order：{score_order}")
    sql += f" ORDER BY entry_time ASC, score {score_order}"

    cursor = connection.execute(sql, params)
    rows = (row_dict(cursor, row) for row in cursor)
    selected: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    last_entry_by_base: dict[str, pd.Timestamp] = {}
    equity = STARTING_EQUITY

    for entry_time, grouped_rows in groupby(
        rows, key=lambda item: item["entry_time"]
    ):
        completed = sorted(
            (trade for trade in active if trade["exit_time"] <= entry_time),
            key=lambda trade: trade["exit_time"],
        )
        for trade in completed:
            equity += trade["profit_usdt"]
        active = [
            trade for trade in active
            if trade["exit_time"] > entry_time
        ]

        opened = 0
        seen_bases: set[str] = set()
        for item in grouped_rows:
            base = item["base"]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            if any(trade["base"] == base for trade in active):
                continue

            entry_timestamp = pd.Timestamp(item["entry_time"])
            previous_entry = last_entry_by_base.get(base)
            if (
                previous_entry is not None
                and entry_timestamp
                < previous_entry + pd.Timedelta(hours=SYMBOL_COOLDOWN_HOURS)
            ):
                continue
            if len(active) >= MAX_POSITIONS:
                continue
            if opened >= MAX_NEW_TRADES_PER_BAR:
                continue
            direction = item["direction"]
            if sum(
                trade["direction"] == direction for trade in active
            ) >= MAX_SAME_DIRECTION_POSITIONS:
                continue
            recent_same_direction = sum(
                trade["direction"] == direction
                and pd.Timestamp(trade["entry_time"])
                > entry_timestamp - pd.Timedelta(hours=6)
                for trade in selected
            )
            if recent_same_direction >= MAX_SAME_DIRECTION_ENTRIES_6H:
                continue
            risk_usdt = max(0.0, equity) * RISK_PER_TRADE_PCT / 100
            item["risk_usdt"] = risk_usdt
            item["profit_usdt"] = (
                item["net_r"] * risk_usdt
                if item["outcome"] != "OPEN" else 0.0
            )
            item["unrealized_profit_usdt"] = (
                item["net_r"] * risk_usdt
                if item["outcome"] == "OPEN" else 0.0
            )
            item["equity_at_entry"] = equity
            selected.append(item)
            active.append(item)
            last_entry_by_base[base] = entry_timestamp
            opened += 1

    return selected


def direction_summary(
    trades: list[dict[str, Any]],
    direction: str,
) -> dict[str, Any]:
    part = [trade for trade in trades if trade["direction"] == direction]
    total = len(part)
    wins = sum(trade["outcome"] == "WIN" for trade in part)
    losses = sum(trade["outcome"] == "LOSS" for trade in part)
    opens = sum(trade["outcome"] == "OPEN" for trade in part)
    closed = wins + losses
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate_pct": round(wins / closed * 100, 3) if closed else 0.0,
        "net_profit_usdt": round(
            sum(trade["profit_usdt"] for trade in part), 4
        ),
        "unrealized_profit_usdt": round(
            sum(trade["unrealized_profit_usdt"] for trade in part), 4
        ),
    }


def calculate_summary(
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(trades)
    wins = sum(trade["outcome"] == "WIN" for trade in trades)
    losses = sum(trade["outcome"] == "LOSS" for trade in trades)
    opens = sum(trade["outcome"] == "OPEN" for trade in trades)
    closed_trades = [
        trade for trade in trades if trade["outcome"] != "OPEN"
    ]
    closed = wins + losses
    equity = STARTING_EQUITY
    peak = equity
    max_drawdown = 0.0
    max_drawdown_pct = 0.0

    for trade in sorted(trades, key=lambda item: item["exit_time"]):
        equity += trade["profit_usdt"]
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, drawdown / peak * 100
            )

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "open": opens,
        "total_win_rate_pct": round(
            wins / closed * 100, 3
        ) if closed else 0.0,
        "average_net_r": round(
            sum(trade["net_r"] for trade in closed_trades) / closed, 5
        ) if closed else 0.0,
        "starting_equity": STARTING_EQUITY,
        "ending_equity": round(equity, 4),
        "net_profit_usdt": round(equity - STARTING_EQUITY, 4),
        "unrealized_profit_usdt": round(
            sum(trade["unrealized_profit_usdt"] for trade in trades), 4
        ),
        "max_drawdown_usdt": round(max_drawdown, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "long": direction_summary(trades, "LONG"),
        "short": direction_summary(trades, "SHORT"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def grouped_diagnostics(
    trades: list[dict[str, Any]],
    grouping: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        if grouping == "month":
            key = str(trade["signal_time"])[:7]
        else:
            key = str(trade[grouping])
        groups.setdefault(key, []).append(trade)

    rows: list[dict[str, Any]] = []
    for key, part in groups.items():
        total = len(part)
        wins = sum(item["outcome"] == "WIN" for item in part)
        losses = sum(item["outcome"] == "LOSS" for item in part)
        opens = sum(item["outcome"] == "OPEN" for item in part)
        closed_part = [item for item in part if item["outcome"] != "OPEN"]
        closed = wins + losses
        rows.append({
            grouping: key,
            "trades": total,
            "wins": wins,
            "losses": losses,
            "open": opens,
            "win_rate_pct": round(wins / closed * 100, 3) if closed else 0.0,
            "average_net_r": round(
                sum(float(item["net_r"]) for item in closed_part) / closed,
                5,
            ) if closed else 0.0,
            "total_net_r": round(
                sum(float(item["net_r"]) for item in closed_part),
                5,
            ),
            "net_profit_usdt": round(
                sum(float(item["profit_usdt"]) for item in part),
                4,
            ),
            "unrealized_profit_usdt": round(
                sum(float(item["unrealized_profit_usdt"]) for item in part),
                4,
            ),
        })
    return sorted(rows, key=lambda item: str(item[grouping]))


def score_rr_diagnostics(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose whether low-chase and nearby 4R targets survive by direction."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for trade in trades:
        score = float(trade["score"])
        rr1 = float(trade["rr1"])
        if score < 0.8:
            score_band = "LT_0_8"
        elif score < 1.0:
            score_band = "0_8_TO_LT_1_0"
        elif score < 2.0:
            score_band = "1_0_TO_LT_2_0"
        else:
            score_band = "GE_2_0"
        if rr1 < 4.5:
            rr_band = "4_0_TO_LT_4_5"
        elif rr1 <= 5.0:
            rr_band = "4_5_TO_5_0"
        elif rr1 < 6.0:
            rr_band = "GT_5_0_TO_LT_6_0"
        else:
            rr_band = "GE_6_0"
        key = (str(trade["direction"]), score_band, rr_band)
        groups.setdefault(key, []).append(trade)

    rows: list[dict[str, Any]] = []
    for (direction, score_band, rr_band), part in groups.items():
        wins = sum(item["outcome"] == "WIN" for item in part)
        losses = sum(item["outcome"] == "LOSS" for item in part)
        opens = sum(item["outcome"] == "OPEN" for item in part)
        closed = wins + losses
        rows.append({
            "direction": direction,
            "score_band": score_band,
            "rr_band": rr_band,
            "trades": len(part),
            "wins": wins,
            "losses": losses,
            "open": opens,
            "win_rate_pct": round(wins / closed * 100, 3) if closed else 0.0,
            "average_net_r": round(
                sum(float(item["net_r"]) for item in part) / len(part), 5
            ),
            "total_net_r": round(
                sum(float(item["net_r"]) for item in part), 5
            ),
            "average_mfe_r": round(
                sum(float(item["mfe_r"]) for item in part) / len(part), 5
            ),
            "average_mae_r": round(
                sum(float(item["mae_r"]) for item in part) / len(part), 5
            ),
            "average_holding_hours": round(
                sum(float(item["holding_bars"]) for item in part)
                * 0.25 / len(part),
                3,
            ),
        })
    return sorted(
        rows,
        key=lambda item: (
            item["direction"], item["score_band"], item["rr_band"]
        ),
    )


def print_comparison(rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 98)
    print("CryptoRadar V8｜一年全市場、上市不足則從首根K線開始")
    print("突破回踩做多＋4H壓力轉弱做空｜只使用二次確認進場")
    print("各市場獨立、不看BTC｜V8低追價分數｜TP1 4R至5R全平｜無時間出場")
    print("=" * 98)
    print(
        "方案                 區段   交易   勝   敗  未平"
        "   總勝率    平均R       淨利      回撤"
    )
    for row in rows:
        print(
            f"{row['scheme']:<20} {row['period']:<4} "
            f"{row['trades']:>5} {row['wins']:>4} "
            f"{row['losses']:>4} {row['open']:>5} "
            f"{row['win_rate_pct']:>7.2f}% "
            f"{row['average_net_r']:>+8.3f} "
            f"{row['net_profit_usdt']:>+10.2f} "
            f"{row['max_drawdown_pct']:>7.2f}%"
        )


def load_known_unavailable_markets() -> set[tuple[str, str]]:
    """Skip only contracts that BingX explicitly reported as paused.

    Error 109429 is a temporary circuit breaker raised after too many 109415
    responses. It does not prove that the affected symbol itself is paused,
    so those markets must be retried in the next run.
    """
    path = ROOT / "backtest_1y_v4_results" / "errors.csv"
    unavailable: set[tuple[str, str]] = set()
    if not path.exists():
        return unavailable
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                error = str(row.get("error", ""))
                code_match = re.search(
                    r'\{"code"\s*:\s*(\d+)', error
                )
                if code_match and code_match.group(1) == "109415":
                    unavailable.add((
                        str(row.get("exchange", "")),
                        str(row.get("symbol", "")),
                    ))
    except Exception:
        return set()
    return unavailable


def main() -> int:
    exchanges = make_exchanges()
    all_targets, errors = discover_unique_targets(exchanges)
    known_unavailable = load_known_unavailable_markets()
    targets = [
        item for item in all_targets
        if (item.exchange_name, item.symbol) not in known_unavailable
    ]
    research_start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=DAYS)

    connection = open_database()
    quality_rows: list[dict[str, Any]] = []
    funnel: dict[str, int] = {}
    candidate_count = 0

    print(f"三交易所合併去重後共有{len(all_targets)}個市場。")
    print(
        f"只跳過前次明確回報109415暫停的市場："
        f"{len(all_targets) - len(targets)}個。"
    )
    print("109429屬暫時請求封鎖，本次會重新嘗試，不會直接排除。")
    print("高速核心V8：低追價分數＋TP1 4R至5R，並保留V7 R2對照組。")
    print(f"本次回測市場：{len(targets)}個；每個市場獨立判斷，不看BTC。")
    print("每幣最多下載365天；上市不足一年則從實際首根15分鐘K線開始。")
    print("代幣化美股／指數／外匯不排除；首14天只供指標暖機，之後開始判斷訊號。")

    for number, target in enumerate(targets, start=1):
        print(
            f"\r[{number:>4}/{len(targets)}] "
            f"{target.exchange_name:<8} {target.symbol:<22}",
            end="",
            flush=True,
        )
        raw: Optional[pd.DataFrame] = None
        frame: Optional[pd.DataFrame] = None
        pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
        pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
        try:
            raw, quality = fetch_closed_ohlcv(target)
            frame, pivot_highs, pivot_lows = prepare_indicators(raw)
            quality_rows.append(quality)
            before = connection.total_changes
            connection.executemany(
                INSERT_SQL,
                candidate_rows(
                    target,
                    frame,
                    research_start,
                    pivot_highs,
                    pivot_lows,
                    funnel,
                ),
            )
            connection.commit()
            candidate_count += connection.total_changes - before
        except Exception as exc:
            connection.rollback()
            errors.append({
                "stage": "fetch_or_prepare",
                "exchange": target.exchange_name,
                "symbol": target.symbol,
                "error": f"{type(exc).__name__}: {exc}",
            })
        finally:
            del raw
            del frame
            del pivot_highs
            del pivot_lows
            gc.collect()

    print(f"\n候選方案資料共{candidate_count}筆，開始模擬V8研究矩陣。")
    comparison: list[dict[str, Any]] = []
    all_trades: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, Any] = {}

    for scheme in SCHEMES:
        for period in (None, "P1", "P2", "P3", "P4"):
            trades = select_portfolio(connection, scheme, period)
            summary = calculate_summary(trades)
            label = "ALL" if period is None else period
            comparison.append({
                "scheme": scheme,
                "period": label,
                "trades": summary["total_trades"],
                "wins": summary["wins"],
                "losses": summary["losses"],
                "open": summary["open"],
                "win_rate_pct": summary["total_win_rate_pct"],
                "average_net_r": summary["average_net_r"],
                "net_profit_usdt": summary["net_profit_usdt"],
                "max_drawdown_pct": summary["max_drawdown_pct"],
            })
            if period is None:
                all_trades[scheme] = trades
                summaries[scheme] = summary

    for scheme in SCHEMES:
        write_csv(
            OUTPUT_DIR / f"trades_{scheme.lower()}.csv",
            all_trades.get(scheme, []),
        )
    diagnostics_by_scheme: dict[str, Any] = {}
    for scheme in SCHEMES:
        scheme_trades = all_trades.get(scheme, [])
        slug = scheme.lower()
        scheme_exchange = grouped_diagnostics(scheme_trades, "exchange")
        scheme_month = grouped_diagnostics(scheme_trades, "month")
        scheme_coin = grouped_diagnostics(scheme_trades, "base")
        scheme_score_rr = score_rr_diagnostics(scheme_trades)
        diagnostics_by_scheme[scheme] = {
            "exchange": scheme_exchange,
            "month": scheme_month,
            "coin": scheme_coin,
            "score_rr": scheme_score_rr,
        }
        write_csv(
            OUTPUT_DIR / f"diagnosis_exchange_{slug}.csv",
            scheme_exchange,
        )
        write_csv(
            OUTPUT_DIR / f"diagnosis_month_{slug}.csv",
            scheme_month,
        )
        write_csv(
            OUTPUT_DIR / f"diagnosis_coin_{slug}.csv",
            scheme_coin,
        )
        write_csv(
            OUTPUT_DIR / f"diagnosis_score_rr_{slug}.csv",
            scheme_score_rr,
        )
    month_stats = diagnostics_by_scheme[PRIMARY_SCHEME]["month"]
    write_csv(OUTPUT_DIR / "comparison.csv", comparison)
    write_csv(
        OUTPUT_DIR / "diagnosis_funnel.csv",
        [
            {"stage": stage, "count": count}
            for stage, count in funnel.items()
        ],
    )
    write_csv(OUTPUT_DIR / "data_quality.csv", quality_rows)
    write_csv(OUTPUT_DIR / "errors.csv", errors)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps({
            "settings": {
                "maximum_days": DAYS,
                "short_history_policy": "start from first available candle",
                "tokenized_tradfi_policy": "included from first available candle; first 14 days are indicator warmup",
                "period_days": PERIOD_DAYS,
                "timeframe": TIMEFRAME,
                "minimum_tp1_r": MIN_TARGET_R,
                "v8_maximum_tp1_r": MAX_TARGET_R,
                "market_filter": "every market is independent; BTC is not used as a direction gate",
                "long_setup": "breakout, confirmed retest and second-confirmation close; enter next open",
                "short_setup": "resistance rejection and second-confirmation close; enter next open",
                "research_schemes": SCHEME_CONFIGS,
                "directional_volume": "OHLCV price-volume pressure proxy over 12 completed 15m candles",
                "chase_score": "confirmation volume ratio multiplied by the capped signal-candle range/ATR; V8 requires score < 1",
                "minimum_stop_atr": MIN_STOP_ATR,
                "maximum_stop_atr": MAX_STOP_ATR,
                "minimum_stop_pct": MIN_STOP_PCT,
                "exit_plan": "TP1 hit closes 100%; stop closes 100%; otherwise remains OPEN at dataset end",
                "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            },
            "summaries": summaries,
            "comparison": comparison,
            "funnel": funnel,
            "diagnostics": diagnostics_by_scheme,
            "limitations": [
                "同根K線同時觸及停損與止盈時保守判定停損先發生。",
                "方向量壓是由OHLCV價格位置與成交量推算，不是真實主動買賣量或未平倉量。",
                "未模擬資金費率、訂單簿深度、最低下單量與下市合約。",
                "OPEN部位不列入已實現損益與總勝率，只另外顯示期末浮動損益。",
                "V8_R2/V8_R3允許4R前存在較近結構，只供研究比較，不代表可直接實盤。",
                "V8門檻由V7樣本提出，仍需新的樣本外資料確認，不能把本次結果直接當成實盤績效。",
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    connection.close()

    print_comparison(comparison)
    print("\n--- 做多／做空分開統計（完整一年）---")
    for scheme in SCHEMES:
        summary = summaries.get(scheme, {})
        long_part = summary.get("long", {})
        short_part = summary.get("short", {})
        print(
            f"{scheme}\n"
            f"  做多｜交易 {long_part.get('trades', 0)}｜"
            f"勝 {long_part.get('wins', 0)}｜"
            f"敗 {long_part.get('losses', 0)}｜"
            f"未平 {long_part.get('open', 0)}｜"
            f"總勝率 {long_part.get('win_rate_pct', 0.0):.2f}%｜"
            f"已實現 {long_part.get('net_profit_usdt', 0.0):+.2f} USDT｜"
            f"浮動 {long_part.get('unrealized_profit_usdt', 0.0):+.2f} USDT\n"
            f"  做空｜交易 {short_part.get('trades', 0)}｜"
            f"勝 {short_part.get('wins', 0)}｜"
            f"敗 {short_part.get('losses', 0)}｜"
            f"未平 {short_part.get('open', 0)}｜"
            f"總勝率 {short_part.get('win_rate_pct', 0.0):.2f}%｜"
            f"已實現 {short_part.get('net_profit_usdt', 0.0):+.2f} USDT｜"
            f"浮動 {short_part.get('unrealized_profit_usdt', 0.0):+.2f} USDT"
        )
    print(f"\n--- 每月穩定性（{PRIMARY_SCHEME}，TP1全平）---")
    for row in month_stats:
        print(
            f"{row['month']}｜交易 {row['trades']}｜"
            f"未平 {row['open']}｜"
            f"勝率 {row['win_rate_pct']:.2f}%｜"
            f"平均R {row['average_net_r']:+.3f}｜"
            f"淨利 {row['net_profit_usdt']:+.2f} USDT"
        )
    print("\n交易所與幣種詳細統計已輸出CSV。")
    print("\n--- 訊號漏斗 ---")
    for stage, count in funnel.items():
        print(f"{stage:<32} {count:>12}")
    print("\n總勝率：TP1筆數 ÷（TP1筆數＋停損筆數）；OPEN不列入分母。")
    print("P1/P2/P3/P4各約91天；每個區段皆使用獨立1000 USDT資金曲線。")
    print(f"成功下載市場：{len(quality_rows)}")
    print(f"錯誤紀錄：{len(errors)}")
    print(f"結果目錄：{OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
