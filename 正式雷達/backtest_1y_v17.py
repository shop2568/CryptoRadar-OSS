#!/usr/bin/env python3
"""CryptoRadar V17: per-market relative-volume research backtest.

This runner keeps the verified V8 execution, stop, fee, portfolio and
tokenized-TradFi rules.  It imports V11 only for its no-look-ahead structural
zone target finder, then corrects the two V11 research mistakes:

1. TP1 must be a real pre-entry structure at >=4R, but ordinary nearer market
   structure does not have to be >=4R.  The old 2R safety-room rule is restored.
2. Signal volume is never a universal raw amount.  Every market is compared
   with its own previous 20-bar median volume.  Breakout requires expansion,
   retest may contract, and second confirmation must expand again.

The fixed 5M USDT rolling gate remains only as an execution/liquidity safety
floor.  It is not used to score signal strength. V17 keeps funding as analysis-only, uses the symmetric breakdown-retest short setup,
keeps the +2R/+3R/+5R protection ladder, and promotes the profitable research
branch to SHORT-only and compares three real-TP distance bands without changing signal logic.
"""

from __future__ import annotations

import builtins
import csv
import importlib.util
import json
import math
import time
import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd


REAL_PRINT = builtins.print
ROOT = Path.home() / "CryptoRadar"
V11_PATH = ROOT / "backtest_1y_v11.py"
if not V11_PATH.exists():
    V11_PATH = Path(__file__).with_name("backtest_1y_v11.py")
if not V11_PATH.exists():
    raise RuntimeError(
        "找不到 backtest_1y_v11.py；請先把V11與V17放在同一目錄。"
    )

spec = importlib.util.spec_from_file_location(
    "cryptoradar_v11_components_v17", V11_PATH
)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V11元件：{V11_PATH}")
v11 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v11
spec.loader.exec_module(v11)
engine = v11.engine


# ===== V17 research matrix: isolate the TP-distance hypothesis =====
# All three schemes share the exact same SHORT signal, entry, stop, volume,
# funding-analysis and profit-protection rules.  Only the real structural TP
# distance filter differs, so the comparison is interpretable.
SCHEME_BASE = "V17_BASE"
SCHEME_SKIP_5_TO_6R = "V17_SKIP_5_TO_6R"
SCHEME_STRICT_GE6 = "V17_STRICT_GE6"

_COMMON_SCHEME = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}
engine.SCHEME_CONFIGS = {
    SCHEME_BASE: dict(_COMMON_SCHEME),
    SCHEME_SKIP_5_TO_6R: dict(_COMMON_SCHEME),
    SCHEME_STRICT_GE6: dict(_COMMON_SCHEME),
}
engine.SCHEMES = tuple(engine.SCHEME_CONFIGS)
engine.PRIMARY_SCHEME = SCHEME_BASE
engine.OUTPUT_DIR = ROOT / "backtest_1y_v17_results"
engine.DATABASE_PATH = engine.OUTPUT_DIR / "candidates.sqlite3"
engine.MAX_TARGET_R = None


# V8 CSV writer assumes every row has exactly the same keys as the first row.
# Funding/data-quality rows can legitimately have different optional fields, so
# write the union of all columns and leave unavailable values blank.
def write_csv_union_fields(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            restval="",
        )
        writer.writeheader()
        writer.writerows(rows)


engine.write_csv = write_csv_union_fields


# ===== Per-market volume rules =====
VOLUME_BASELINE_BARS = 20
BREAKOUT_RELATIVE_VOLUME = 1.30
RETEST_MIN_RELATIVE_VOLUME = 0.60
RETEST_MAX_RELATIVE_VOLUME = 1.20
CONFIRM_MIN_RELATIVE_VOLUME = 1.00
CONFIRM_VS_SIGNAL_MULTIPLIER = 1.10

# ===== Historical funding-rate anti-crowding rules =====
# Rates are decimal per funding event: 0.0003 = 0.03%.
# Longs may be neutral/negative, but reject excessively positive crowded funding.
# Shorts may be neutral/positive, but reject excessively negative crowded funding.
FUNDING_PAGE_LIMIT = 1_000
FUNDING_FETCH_RETRIES = 4
FUNDING_MISSING_POLICY = "ALLOW"  # ALLOW avoids exchange-history gaps becoming fake signals.
FUNDING_CACHE_DIR = ROOT / "backtest_cache_1y_funding"

engine.VOLUME_RATIO = BREAKOUT_RELATIVE_VOLUME
engine.RETEST_VOLUME_RATIO = RETEST_MIN_RELATIVE_VOLUME

_original_fetch_closed_ohlcv = engine.fetch_closed_ohlcv
_original_prepare_indicators = engine.prepare_indicators
_original_triggered_entry = engine.triggered_entry
_original_candidate_rows_v17 = v11._original_candidate_rows


def _funding_cache_path(target: Any) -> Path:
    safe_symbol = "".join(ch if ch.isalnum() else "_" for ch in str(target.symbol))
    return FUNDING_CACHE_DIR / f"{target.exchange_name}_{safe_symbol}.csv.gz"


def _fetch_historical_funding(target: Any, candle_index: pd.DatetimeIndex) -> pd.Series:
    """Return last funding event known at or before each candle; never backfill."""
    if engine.asset_class(target.base) == "TOKENIZED_TRADFI":
        return pd.Series(0.0, index=candle_index, dtype="float64")

    exchange = target.exchange
    if not bool(getattr(exchange, "has", {}).get("fetchFundingRateHistory")):
        return pd.Series(float("nan"), index=candle_index, dtype="float64")

    FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _funding_cache_path(target)
    start_ms = int(candle_index[0].timestamp() * 1_000)
    end_ms = int(candle_index[-1].timestamp() * 1_000)
    records: list[tuple[int, float]] = []

    if cache_path.exists():
        try:
            cached = pd.read_csv(cache_path)
            for row in cached.itertuples(index=False):
                records.append((int(row.timestamp), float(row.fundingRate)))
        except Exception:
            records = []

    cursor = max(start_ms, max((ts for ts, _ in records), default=start_ms - 1) + 1)
    while cursor <= end_ms:
        batch = None
        last_error: Optional[Exception] = None
        for attempt in range(FUNDING_FETCH_RETRIES):
            try:
                batch = exchange.fetch_funding_rate_history(
                    target.symbol,
                    since=cursor,
                    limit=FUNDING_PAGE_LIMIT,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < FUNDING_FETCH_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
        if batch is None:
            if records:
                break
            raise RuntimeError(
                f"funding history failed: {type(last_error).__name__}: {last_error}"
            )
        if not batch:
            break

        newest = cursor
        for item in batch:
            timestamp = item.get("timestamp")
            rate = item.get("fundingRate")
            if timestamp is None or rate is None:
                continue
            timestamp = int(timestamp)
            rate = float(rate)
            if math.isfinite(rate):
                records.append((timestamp, rate))
                newest = max(newest, timestamp)
        if newest <= cursor:
            break
        cursor = newest + 1
        if newest >= end_ms:
            break

    if records:
        dedup = pd.DataFrame(records, columns=["timestamp", "fundingRate"])
        dedup = dedup.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        dedup.to_csv(cache_path, index=False, compression="gzip")
        event_index = pd.to_datetime(dedup["timestamp"], unit="ms", utc=True)
        events = pd.Series(dedup["fundingRate"].to_numpy(), index=event_index)
        # Do not carry a stale event forever across an API/history gap.
        return events.reindex(
            candle_index,
            method="ffill",
            tolerance=pd.Timedelta(hours=24),
        )

    return pd.Series(float("nan"), index=candle_index, dtype="float64")


def fetch_with_funding(target: Any):
    data, quality = _original_fetch_closed_ohlcv(target)
    data = data.copy()
    try:
        data["funding_rate"] = _fetch_historical_funding(target, data.index)
        data["funding_available"] = data["funding_rate"].notna()
        known = int(data["funding_available"].sum())
        quality = dict(quality)
        quality["funding_known_bars"] = known
        quality["funding_coverage_pct"] = round(known / len(data) * 100, 3) if len(data) else 0.0
        quality["funding_missing_policy"] = FUNDING_MISSING_POLICY
    except Exception as exc:
        data["funding_rate"] = float("nan")
        data["funding_available"] = False
        quality = dict(quality)
        quality["funding_error"] = f"{type(exc).__name__}: {exc}"
        quality["funding_known_bars"] = 0
        quality["funding_coverage_pct"] = 0.0
        quality["funding_missing_policy"] = FUNDING_MISSING_POLICY
    return data, quality


def prepare_relative_volume_indicators(data: pd.DataFrame):
    """Replace mean volume and preserve funding missingness through V8 dropna."""
    prepared = data.copy()
    if "funding_available" not in prepared:
        prepared["funding_available"] = False
    if "funding_rate" not in prepared:
        prepared["funding_rate"] = float("nan")
    # V8 drops rows containing any NaN. Keep a separate availability flag and
    # use neutral zero only as a transport value; the filter checks the flag.
    prepared["funding_rate"] = prepared["funding_rate"].fillna(0.0)
    frame, pivot_highs, pivot_lows = _original_prepare_indicators(prepared)

    own_median = frame["volume"].shift(1).rolling(
        VOLUME_BASELINE_BARS,
        min_periods=VOLUME_BASELINE_BARS,
    ).median()
    own_median = own_median.replace(0.0, float("nan"))
    frame["own_volume_median20"] = own_median
    frame["own_relative_volume"] = frame["volume"] / own_median

    # The V8 signal functions consume volume_ratio.  Replacing this column is
    # enough to make breakout/retest/score calculations market-relative while
    # leaving every price, trend and stop calculation unchanged.
    frame["volume_ratio"] = frame["own_relative_volume"]
    frame = frame.dropna(subset=["own_relative_volume"])
    return frame, pivot_highs, pivot_lows


def triggered_entry_with_volume_reexpansion(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    atr: float,
    invalidation: float,
) -> Optional[tuple[int, float, int]]:
    """Enter only when price confirms and this market's volume expands again."""
    signal_bar = frame.iloc[signal_i]
    signal_relative_volume = float(signal_bar["own_relative_volume"])
    if not math.isfinite(signal_relative_volume):
        return None

    # A retest/rejection may contract, but a still-explosive setup candle is
    # treated as chasing instead of a clean pullback.
    if signal_relative_volume > RETEST_MAX_RELATIVE_VOLUME:
        return None

    if direction == "LONG":
        trigger = float(signal_bar["high"]) + atr * engine.ENTRY_TRIGGER_ATR_BUFFER
    else:
        trigger = float(signal_bar["low"]) - atr * engine.ENTRY_TRIGGER_ATR_BUFFER

    final_i = min(signal_i + engine.ENTRY_TRIGGER_BARS, len(frame) - 2)
    for confirmation_i in range(signal_i + 1, final_i + 1):
        bar = frame.iloc[confirmation_i]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        # Same-candle trigger/invalidation ambiguity stays conservative.
        if direction == "LONG":
            if low <= invalidation:
                return None
            price_confirmed = close >= trigger
        else:
            if high >= invalidation:
                return None
            price_confirmed = close <= trigger

        if not price_confirmed:
            continue

        confirmation_relative_volume = float(bar["own_relative_volume"])
        volume_confirmed = (
            math.isfinite(confirmation_relative_volume)
            and confirmation_relative_volume >= CONFIRM_MIN_RELATIVE_VOLUME
            and confirmation_relative_volume
            >= signal_relative_volume * CONFIRM_VS_SIGNAL_MULTIPLIER
        )
        if not volume_confirmed:
            # Keep looking within the original trigger window.  A later closed
            # candle may provide both price confirmation and renewed volume.
            continue

        entry_i = confirmation_i + 1
        return (
            entry_i,
            float(frame.iloc[entry_i]["open"]),
            confirmation_i,
        )
    return None


# Funding is deliberately not a gate in V17.  It is fetched historically and
# later attached to trade CSV rows for analysis only.


def short_breakdown_retest_level(frame: pd.DataFrame, signal_i: int, cached_pivots: Any = None) -> Optional[float]:
    """Mirror the long setup: breakdown -> 2-12 bar retest -> bearish rejection."""
    signal_bar = frame.iloc[signal_i]
    previous = frame.iloc[signal_i - 1]
    signal_range = max(float(signal_bar["high"] - signal_bar["low"]), 1e-12)
    close_location = float(signal_bar["close"] - signal_bar["low"]) / signal_range
    body = abs(float(signal_bar["close"] - signal_bar["open"]))
    upper_wick = float(signal_bar["high"] - max(signal_bar["open"], signal_bar["close"]))
    if not (
        signal_bar["close"] < signal_bar["open"]
        and signal_bar["close"] < previous["close"]
        and close_location <= 0.40
        and upper_wick >= max(body * 0.20, signal_range * 0.05)
        and RETEST_MIN_RELATIVE_VOLUME <= signal_bar["own_relative_volume"] <= RETEST_MAX_RELATIVE_VOLUME
    ):
        return None
    oldest = signal_i - engine.BREAKOUT_MAX_AGE
    newest = signal_i - engine.BREAKOUT_MIN_AGE
    for breakout_i in range(newest, oldest - 1, -1):
        if breakout_i < engine.BREAKOUT_BOX_BARS:
            continue
        breakdown = frame.iloc[breakout_i]
        box = frame.iloc[breakout_i - engine.BREAKOUT_BOX_BARS:breakout_i]
        support = float(box["low"].min())
        bar_range = max(float(breakdown["high"] - breakdown["low"]), 1e-12)
        bar_close_location = float(breakdown["close"] - breakdown["low"]) / bar_range
        valid = (
            breakdown["close"] < support
            and breakdown["close"] < breakdown["open"]
            and bar_close_location <= 0.35
            and breakdown["own_relative_volume"] >= BREAKOUT_RELATIVE_VOLUME
        )
        if not valid:
            continue
        after = frame.iloc[breakout_i + 1:signal_i + 1]
        if (after["close"] > support * (1.0 + engine.RETEST_BELOW_PCT)).any():
            continue
        retest = (
            float(signal_bar["high"]) >= support * (1.0 - engine.RETEST_ABOVE_PCT)
            and float(signal_bar["high"]) <= support * (1.0 + engine.RETEST_BELOW_PCT)
            and float(signal_bar["close"]) < support
        )
        if retest:
            return support
    return None


def simulate_tp1_with_profit_protection(
    frame: pd.DataFrame, entry_i: int, direction: str, entry: float,
    stop: float, tp1: float, stop_distance: float,
) -> dict[str, Any]:
    """No time exit. +2R->BE, +3R->+1R, +5R->+3R."""
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    active_stop = stop
    stage = 0
    end_i = len(frame) - 1
    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        stop_fill = engine.bar_hits_stop(direction, o, h, l, active_stop)
        if stop_fill is not None:
            gross_r = sign * (stop_fill - entry) / stop_distance
            reasons = {0: "STOP", 1: "PROTECT_BE", 2: "PROTECT_1R", 3: "PROTECT_3R"}
            result = engine.finish_result(
                direction, entry, stop_distance, gross_r,
                stop_fill * cost_rate / stop_distance, i, stop_fill,
                reasons[stage], 0,
            )
            result["outcome"] = "WIN" if result["net_r"] > 0 else "LOSS"
            return result
        if engine.bar_hits_target(direction, o, h, l, tp1):
            gross_r = sign * (tp1 - entry) / stop_distance
            return engine.finish_result(
                direction, entry, stop_distance, gross_r,
                tp1 * cost_rate / stop_distance, i, tp1, "TP1_ALL", 0,
            )
        # Conservative sequencing: a new protection stage activates only after
        # this completed candle, so an intrabar touch cannot retroactively move SL.
        favorable = (h - entry) / stop_distance if direction == "LONG" else (entry - l) / stop_distance
        if favorable >= 5.0 and stage < 3:
            active_stop = entry + sign * 3.0 * stop_distance
            stage = 3
        elif favorable >= 3.0 and stage < 2:
            active_stop = entry + sign * stop_distance
            stage = 2
        elif favorable >= 2.0 and stage < 1:
            active_stop = entry
            stage = 1
    return engine.finish_open_result(direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i]))


engine.short_resistance_rejection_level = short_breakdown_retest_level
engine.simulate_tp1_all = simulate_tp1_with_profit_protection


engine.fetch_closed_ohlcv = fetch_with_funding
engine.prepare_indicators = prepare_relative_volume_indicators
engine.triggered_entry = triggered_entry_with_volume_reexpansion


def candidate_rows_short_only(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    """Publish SHORT candidates into three pre-declared TP-distance schemes.

    BASE reproduces V16. SKIP_5_TO_6R removes only 5R <= TP < 6R.
    STRICT_GE6 keeps only real structural targets at >=6R.  Filtering happens
    before portfolio simulation, so rejected rows cannot consume position slots.
    """
    generated = 0
    suppressed_long = 0
    rejected_rr = {SCHEME_SKIP_5_TO_6R: 0, SCHEME_STRICT_GE6: 0}
    published = {scheme: 0 for scheme in engine.SCHEMES}

    for candidate in _original_candidate_rows_v17(
        target, frame, research_start, pivot_highs, pivot_lows, funnel
    ):
        generated += 1
        direction = str(candidate[3])
        if direction != "SHORT":
            suppressed_long += 1
            continue

        scheme = str(candidate[8])
        rr1 = float(candidate[14])
        keep = True
        if scheme == SCHEME_SKIP_5_TO_6R and 5.0 <= rr1 < 6.0:
            keep = False
            rejected_rr[scheme] += 1
        elif scheme == SCHEME_STRICT_GE6 and rr1 < 6.0:
            keep = False
            rejected_rr[scheme] += 1

        if not keep:
            continue
        published[scheme] = published.get(scheme, 0) + 1
        yield candidate

    funnel["V17_source_candidates"] = funnel.get("V17_source_candidates", 0) + generated
    funnel["V17_long_suppressed"] = funnel.get("V17_long_suppressed", 0) + suppressed_long
    for scheme, count in rejected_rr.items():
        funnel[f"{scheme}_rr_filtered"] = funnel.get(f"{scheme}_rr_filtered", 0) + count
    for scheme, count in published.items():
        funnel[f"{scheme}_published"] = funnel.get(f"{scheme}_published", 0) + count


# V17 deliberately promotes the V17 SHORT branch. The old V11 1H-long research
# filter is irrelevant because LONG candidates are not published at all.
engine.candidate_rows = candidate_rows_short_only

# Keep the V11 real-zone finder, now called with minimum_room_r=2 by V17.
# TP1 itself is still rejected unless its real structure is >=4R.
engine.confirmed_structure_targets = v11.confirmed_zone_targets
v11._target_cache.clear()


def score_rr_diagnostics_v17(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trade in trades:
        rr1 = float(trade["rr1"])
        if rr1 < 5.0:
            rr_band = "4_TO_LT5"
        elif rr1 < 6.0:
            rr_band = "5_TO_LT6"
        else:
            rr_band = "GE6"
        groups.setdefault((str(trade["direction"]), rr_band), []).append(trade)

    rows: list[dict[str, Any]] = []
    for (direction, rr_band), part in groups.items():
        wins = sum(row["outcome"] == "WIN" for row in part)
        losses = sum(row["outcome"] == "LOSS" for row in part)
        opens = sum(row["outcome"] == "OPEN" for row in part)
        closed = wins + losses
        closed_rows = [row for row in part if row["outcome"] != "OPEN"]
        rows.append({
            "direction": direction,
            "rr_band": rr_band,
            "trades": len(part),
            "wins": wins,
            "losses": losses,
            "open": opens,
            "win_rate_pct": round(wins / closed * 100, 3) if closed else 0.0,
            "average_net_r": round(
                sum(float(row["net_r"]) for row in closed_rows) / closed,
                5,
            ) if closed else 0.0,
            "total_net_r": round(
                sum(float(row["net_r"]) for row in closed_rows), 5
            ),
            "net_profit_usdt": round(
                sum(float(row["profit_usdt"]) for row in part), 4
            ),
        })
    return sorted(rows, key=lambda row: (row["direction"], row["rr_band"]))


engine.score_rr_diagnostics = score_rr_diagnostics_v17


def print_comparison_v17(rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 104)
    print("CryptoRadar V17｜365天｜SHORT ONLY｜各幣自身增量｜真實結構TP最低4R、無上限")
    print("突破放量→回踩量縮→確認再放量｜停損不變｜TP1全平｜無時間出場")
    print("=" * 104)
    print(
        "方案                            區段   交易   勝   敗  未平"
        "   總勝率    平均R       淨利      回撤"
    )
    for row in rows:
        print(
            f"{row['scheme']:<31} {row['period']:<4} "
            f"{row['trades']:>5} {row['wins']:>4} "
            f"{row['losses']:>4} {row['open']:>5} "
            f"{row['win_rate_pct']:>7.2f}% "
            f"{row['average_net_r']:>+8.3f} "
            f"{row['net_profit_usdt']:>+10.2f} "
            f"{row['max_drawdown_pct']:>7.2f}%"
        )


engine.print_comparison = print_comparison_v17



def _funding_at_signal(exchange_name: str, symbol: str, signal_time: str, base: str) -> tuple[Optional[float], Optional[float]]:
    """Read cached funding as-of signal time; return (rate, age_hours)."""
    if engine.asset_class(base) == "TOKENIZED_TRADFI":
        return None, None
    safe_symbol = "".join(ch if ch.isalnum() else "_" for ch in str(symbol))
    path = FUNDING_CACHE_DIR / f"{exchange_name}_{safe_symbol}.csv.gz"
    if not path.exists():
        return None, None
    try:
        data = pd.read_csv(path)
        if data.empty:
            return None, None
        data["timestamp"] = pd.to_numeric(data["timestamp"], errors="coerce")
        data["fundingRate"] = pd.to_numeric(data["fundingRate"], errors="coerce")
        data = data.dropna(subset=["timestamp", "fundingRate"]).sort_values("timestamp")
        ts = pd.Timestamp(signal_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        signal_ms = int(ts.timestamp() * 1000)
        eligible = data[data["timestamp"] <= signal_ms]
        if eligible.empty:
            return None, None
        row = eligible.iloc[-1]
        age_hours = (signal_ms - float(row["timestamp"])) / 3_600_000.0
        if age_hours > 24.0:
            return None, round(age_hours, 4)
        return float(row["fundingRate"]), round(age_hours, 4)
    except Exception:
        return None, None


def enrich_v17_outputs() -> None:
    """Attach analysis-only funding and emit per-scheme direction diagnostics."""
    direction_rows: list[dict[str, Any]] = []
    for scheme in engine.SCHEMES:
        path = engine.OUTPUT_DIR / f"trades_{scheme.lower()}.csv"
        if not path.exists() or not path.stat().st_size:
            continue
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rate, age = _funding_at_signal(
                    str(row.get("exchange", "")),
                    str(row.get("symbol", "")),
                    str(row.get("signal_time", "")),
                    str(row.get("base", "")),
                )
                row["funding_rate_at_signal"] = "" if rate is None else rate
                row["funding_age_hours"] = "" if age is None else age
                row["funding_used_as_filter"] = False
                rows.append(row)
        write_csv_union_fields(path, rows)

        part = [row for row in rows if str(row.get("direction")) == "SHORT"]
        wins = sum(str(row.get("outcome")) == "WIN" for row in part)
        losses = sum(str(row.get("outcome")) == "LOSS" for row in part)
        opens = sum(str(row.get("outcome")) == "OPEN" for row in part)
        closed = wins + losses
        direction_rows.append({
            "scheme": scheme,
            "direction": "SHORT",
            "trades": len(part),
            "wins": wins,
            "losses": losses,
            "open": opens,
            "win_rate_pct": round(wins / closed * 100, 3) if closed else 0.0,
            "total_net_r": round(sum(float(row.get("net_r") or 0.0) for row in part), 5),
            "net_profit_usdt": round(sum(float(row.get("profit_usdt") or 0.0) for row in part), 4),
        })
    write_csv_union_fields(engine.OUTPUT_DIR / "diagnosis_direction_v17.csv", direction_rows)


def audit_v17() -> dict[str, Any]:
    audit: dict[str, Any] = {
        "version": "V17",
        "minimum_tp1_r": 4.0,
        "maximum_tp1_r": None,
        "volume_policy": "each market versus its own lagged 20-bar median",
        "funding_policy": "analysis-only; historical as-of funding never blocks entry",
        "direction_policy": "SHORT_ONLY; LONG candidates are generated only for audit funnel then suppressed before portfolio simulation",
        "tp_distance_research": "BASE vs exclude 5R-<6R vs require >=6R; hypothesis declared before this run",
        "schemes": {},
        "passed": True,
    }
    allowed_exits = {"TP1_ALL", "STOP", "PROTECT_BE", "PROTECT_1R", "PROTECT_3R", "OPEN_AT_END"}
    for scheme in engine.SCHEMES:
        path = engine.OUTPUT_DIR / f"trades_{scheme.lower()}.csv"
        rows = 0
        invalid_rr = 0
        invalid_exit = 0
        minimum_rr: Optional[float] = None
        maximum_rr: Optional[float] = None
        if path.exists() and path.stat().st_size:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    rows += 1
                    rr1 = float(row["rr1"])
                    minimum_rr = rr1 if minimum_rr is None else min(minimum_rr, rr1)
                    maximum_rr = rr1 if maximum_rr is None else max(maximum_rr, rr1)
                    invalid_rr += int(rr1 < 4.0 - 1e-9)
                    invalid_exit += int(row["exit_reason"] not in allowed_exits)
        invalid_direction = 0
        if path.exists() and path.stat().st_size:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                invalid_direction = sum(
                    1 for row in csv.DictReader(handle)
                    if str(row.get("direction", "")) != "SHORT"
                )
        invalid_rr_band = 0
        if path.exists() and path.stat().st_size:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    rr1 = float(row["rr1"])
                    if scheme == SCHEME_SKIP_5_TO_6R and 5.0 <= rr1 < 6.0:
                        invalid_rr_band += 1
                    elif scheme == SCHEME_STRICT_GE6 and rr1 < 6.0 - 1e-9:
                        invalid_rr_band += 1
        passed = invalid_rr == 0 and invalid_exit == 0 and invalid_direction == 0 and invalid_rr_band == 0
        audit["schemes"][scheme] = {
            "trades": rows,
            "minimum_observed_rr": minimum_rr,
            "maximum_observed_rr": maximum_rr,
            "invalid_rr": invalid_rr,
            "invalid_exit": invalid_exit,
            "invalid_direction": invalid_direction,
            "invalid_rr_band": invalid_rr_band,
            "passed": passed,
        }
        audit["passed"] = bool(audit["passed"] and passed)

    summary_path = engine.OUTPUT_DIR / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        settings = summary.setdefault("settings", {})
        settings.update({
            "strategy_version": "V17",
            "target_policy": (
                "real pre-entry confirmed 4H swing/touch zone; "
                "TP1 >=4R; no maximum; no fixed-R fallback"
            ),
            "maximum_tp1_r": None,
            "volume_signal_policy": (
                "each market versus its own lagged 20-bar median; "
                "breakout >=1.30x; retest 0.60x-1.20x; "
                "confirmation >=1.00x and >=1.10x the retest"
            ),
            "direction_policy": "SHORT_ONLY; V17 compares BASE vs skip 5R-<6R vs strict >=6R without changing signals",
            "funding_rate_policy": (
                "analysis-only; never blocks trades; historical as-of events only; "
                "tokenized TradFi has no funding gate; missing values remain disclosed"
            ),
            "absolute_liquidity_policy": (
                "5M USDT rolling quote volume is execution safety only, "
                "not signal strength"
            ),
        })
        settings.pop("v8_maximum_tp1_r", None)
        summary["limitations"] = [
            item for item in summary.get("limitations", [])
            if "V8_R2/V8_R3" not in str(item)
        ] + [
            "V17成交量來自OHLCV，不是真實主動買賣量；仍需樣本外驗證。",
            "資金費率歷史可能因交易所/API覆蓋不足而缺值；V17只用於分析，不作為進場過濾。"
        ]
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    audit_path = engine.OUTPUT_DIR / "v17_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not audit["passed"]:
        raise RuntimeError(f"V17稽核失敗：{audit_path}")
    return audit


def translated_print_v17(*args: Any, **kwargs: Any) -> None:
    converted: list[Any] = []
    for value in args:
        if isinstance(value, str):
            value = value.replace(
                "高速核心V8：低追價分數＋TP1 4R至5R，並保留V7 R2對照組。",
                "V17：SHORT ONLY＋各幣自身增量＋真實結構TP最低4R無上限。",
            ).replace(
                "候選方案資料共", "V17候選方案資料共"
            ).replace(
                "筆，開始模擬V8研究矩陣。", "筆，開始模擬V17研究矩陣。"
            )
        converted.append(value)
    REAL_PRINT(*converted, **kwargs)


def main() -> int:
    print("CryptoRadar V17程式啟動。")
    print("成交量：每個幣與自己的前20根中位量比較，不拿不同幣互相比。")
    print("TP：真實結構最低4R、最高無上限；停損與美股代幣規則不變。")
    print("資金費率：只記錄分析，不再阻擋任何交易。")
    print("獲利保護：+2R移保本、+3R移到+1R、+5R移到+3R；仍然沒有時間出場。")
    print("方向：V17只做SHORT；LONG候選直接停用，不佔用資金與持倉名額。")
    print("三組比較：BASE原版｜SKIP排除5R～<6R｜STRICT只做真實TP>=6R。")
    print("空單：跌破支撐→回踩→二次確認。")
    original_print = builtins.print
    try:
        builtins.print = translated_print_v17
        result = int(engine.main())
    finally:
        builtins.print = original_print
    enrich_v17_outputs()
    audit = audit_v17()
    print("V17結果稽核：PASS")
    print(f"稽核檔案：{engine.OUTPUT_DIR / 'v17_audit.json'}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
