#!/usr/bin/env python3
"""CryptoRadar V32 structure-aware regime backtest.

V32 keeps V31 execution, adaptive heat, selective Trend Runner, major-structure
Runner stop and slow profit protection.  It strengthens market-regime detection
without symbol-specific tuning.

New in V32:
1) Completed 4H candles only; no look-ahead.
2) Add 6-bar structural overlap ratio. Repeated overlapping 4H ranges are evidence
   of consolidation even when EMA20/EMA50 still point in one direction.
3) Add 6-bar directional efficiency: net close displacement divided by total close
   travel. Low efficiency means price is chopping rather than trending.
4) TREND now needs EMA alignment plus adequate efficiency and limited overlap.
5) RANGE is triggered by EMA compression OR high structural overlap + low efficiency,
   or repeated EMA20 crossings with weak slope.
6) After a true STOP, re-entry requires a stronger aligned trend confirmation.
7) Same rules apply to crypto and tokenized TradFi. No ticker exceptions.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path.home() / "CryptoRadar"
V26_PATH = ROOT / "backtest_1y_v26.py"
if not V26_PATH.exists():
    V26_PATH = Path(__file__).with_name("backtest_1y_v26.py")
if not V26_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v26.py；請把V26與V32放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v26_components_v32", V26_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V26：{V26_PATH}")
v26base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v26base
spec.loader.exec_module(v26base)

v24 = v26base.v24
v23 = v26base.v23
v22 = v26base.v22
v17 = v26base.v17
engine = v26base.engine

OUTPUT_DIR = ROOT / "backtest_1y_v32_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"

# Re-point inherited writers.
v22.OUTPUT_DIR = OUTPUT_DIR
v22.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
v22.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
v22.V22_TOKEN_CATALOG_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_catalog.csv"
v22.V22_REJECTED_COLLISIONS_PATH = MARKETS_OUTPUT_DIR / "rejected_stock_ticker_collisions.csv"
v22.V22_DISCOVERY_SUMMARY_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_discovery_summary.json"
v22.V22_RUN_NATIVE_STOCKS = False

# Keep V24 heat model unchanged.
MARKET_HEAT_DAYS = v24.MARKET_HEAT_DAYS
MARKET_HEAT_MIN_RATIO = v24.MARKET_HEAT_MIN_RATIO
MIN_ROLLING_24H_QUOTE_TURNOVER = v24.MIN_ROLLING_24H_QUOTE_TURNOVER
BARS_24H = v24.BARS_24H

# Adaptive no-look-ahead trend-runner horizons.
RUNNER_EXTREME_RULES = (
    (20, 0.010),
    (45, 0.005),
    (90, 0.003),
)
RUNNER_MIN_HEAT_RATIO = 1.50
RUNNER_MIN_EMA_GAP_R = 0.75
RUNNER_RESCUE_20D_TOLERANCE = 0.0105
RUNNER_RESCUE_MIN_HEAT_RATIO = 3.00
RUNNER_RESCUE_MIN_EMA_GAP_R = 0.75
NEW_LISTING_HEAT_TIERS = (
    (20, 20, 1.20),  # history_days >=20 -> previous 20 completed days
    (7, 7, 1.35),    # 7-19 days -> previous 7 completed days
    (3, 3, 1.50),    # 3-6 days -> previous 3 completed days
)
RUNNER_SENTINEL_R = v24.RUNNER_SENTINEL_R
RUNNER_SENTINEL_THRESHOLD_R = v24.RUNNER_SENTINEL_THRESHOLD_R
RUNNER_STOP_EMA50_ATR_BUFFER = 0.30
RUNNER_MAX_INITIAL_STOP_PCT = 8.0

# V32 completed-4H market regime thresholds.
REGIME_TREND_MIN_EMA_SEP_ATR = 0.35
REGIME_TREND_MIN_EMA20_SLOPE_ATR = 0.20
REGIME_RANGE_MAX_EMA_SEP_ATR = 0.20
REGIME_RANGE_MAX_EMA20_SLOPE_ATR = 0.15
REGIME_RANGE_MIN_CROSSINGS_6 = 3
REGIME_MAX_TREND_CROSSINGS_6 = 2
# V32 structure-aware regime thresholds.
REGIME_TREND_MIN_EFFICIENCY_6 = 0.42
REGIME_TREND_MAX_OVERLAP_6 = 0.62
REGIME_RANGE_MAX_EFFICIENCY_6 = 0.32
REGIME_RANGE_MIN_OVERLAP_6 = 0.58
POST_STOP_MIN_WAIT_HOURS = 4
POST_STOP_MIN_EFFICIENCY_6 = 0.50
POST_STOP_MAX_OVERLAP_6 = 0.52

SCHEME_V32 = "V32_ADAPTIVE_HEAT_SELECTIVE_RUNNER"
V32_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}
v22.SCHEME_V22 = SCHEME_V32
v22.V22_SCHEME_CONFIG = dict(V32_SCHEME_CONFIG)

_V32_PER_MARKET_FUNNEL: list[dict[str, Any]] = []
_V32_TARGET_AUDIT: list[dict[str, Any]] = []
_V32_STOP_AUDIT: list[dict[str, Any]] = []
_V32_REGIME_AUDIT: list[dict[str, Any]] = []
_V32_REASSESS_AUDIT: list[dict[str, Any]] = []
_CURRENT_TARGET: Any = None


def _extreme_stats(frame: pd.DataFrame, signal_i: int, direction: str, entry: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for days, tolerance in RUNNER_EXTREME_RULES:
        bars = days * 96
        start = max(0, signal_i - bars)
        hist = frame.iloc[start:signal_i]
        if hist.empty:
            rows.append({"days": days, "tolerance": tolerance, "extreme": float("nan"), "distance_pct": float("nan"), "eligible": False})
            continue
        if direction == "LONG":
            extreme = float(pd.to_numeric(hist["high"], errors="coerce").max())
            distance_pct = (extreme - entry) / extreme if math.isfinite(extreme) and extreme > 0 else float("nan")
            eligible = bool(math.isfinite(extreme) and extreme > 0 and entry >= extreme * (1.0 - tolerance))
        else:
            extreme = float(pd.to_numeric(hist["low"], errors="coerce").min())
            distance_pct = (entry - extreme) / extreme if math.isfinite(extreme) and extreme > 0 else float("nan")
            eligible = bool(math.isfinite(extreme) and extreme > 0 and entry <= extreme * (1.0 + tolerance))
        rows.append({"days": days, "tolerance": tolerance, "extreme": extreme, "distance_pct": distance_pct, "eligible": eligible})
    return rows


def selective_structure_or_runner_targets(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: Any,
    cached_pivot_lows: Any,
    minimum_room_r: float = 2.0,
):
    real = v17.v11.confirmed_zone_targets(
        frame,
        signal_i,
        direction,
        entry,
        stop_distance,
        cached_pivot_highs,
        cached_pivot_lows,
        minimum_room_r=minimum_room_r,
    )
    if real is not None:
        return real

    stats = _extreme_stats(frame, signal_i, direction, entry)
    raw_match = next((r for r in stats if r["eligible"]), None)
    row = frame.iloc[signal_i]
    heat_ratio = float(row.get("market_heat_ratio", float("nan")))
    ema20 = float(row.get("ema20_15m", float("nan")))
    ema50 = float(row.get("ema50_15m", float("nan")))
    if math.isfinite(stop_distance) and stop_distance > 0 and math.isfinite(ema20) and math.isfinite(ema50):
        ema_gap_r = ((ema20 - ema50) / stop_distance) if direction == "LONG" else ((ema50 - ema20) / stop_distance)
    else:
        ema_gap_r = float("nan")
    strong_trend = bool(
        raw_match is not None
        and math.isfinite(heat_ratio) and heat_ratio >= RUNNER_MIN_HEAT_RATIO
        and math.isfinite(ema_gap_r) and ema_gap_r >= RUNNER_MIN_EMA_GAP_R
    )

    # V32 narrow rescue: allow a near-20d-breakout setup through a slightly softer
    # quality gate. This rule is symmetric and global; no MU/SKY hard-code exists.
    stat20 = next((r for r in stats if int(r["days"]) == 20), None)
    rescue_20d = bool(
        stat20 is not None
        and math.isfinite(float(stat20["distance_pct"]))
        and float(stat20["distance_pct"]) <= RUNNER_RESCUE_20D_TOLERANCE
        and math.isfinite(heat_ratio) and heat_ratio >= RUNNER_RESCUE_MIN_HEAT_RATIO
        and math.isfinite(ema_gap_r) and ema_gap_r >= RUNNER_RESCUE_MIN_EMA_GAP_R
    )
    matched = raw_match if strong_trend else (stat20 if rescue_20d else None)
    target = _CURRENT_TARGET
    audit_row: dict[str, Any] = {
        "exchange": getattr(target, "exchange_name", ""),
        "symbol": getattr(target, "symbol", ""),
        "base": getattr(target, "base", ""),
        "signal_time": frame.index[signal_i].isoformat(),
        "direction": direction,
        "entry": entry,
        "stop_distance": stop_distance,
        "market_heat_ratio": float(row.get("market_heat_ratio", float("nan"))),
        "own_relative_volume": float(row.get("own_relative_volume", float("nan"))),
        "close": float(row.get("close", float("nan"))),
        "ema20_15m": float(row.get("ema20_15m", float("nan"))),
        "ema50_15m": float(row.get("ema50_15m", float("nan"))),
        "ema_gap_r": ema_gap_r,
        "runner_min_ema_gap_r": RUNNER_MIN_EMA_GAP_R,
        "runner_min_heat_ratio": RUNNER_MIN_HEAT_RATIO,
        "runner_rescue_20d_tolerance_pct": RUNNER_RESCUE_20D_TOLERANCE * 100,
        "runner_rescue_min_heat_ratio": RUNNER_RESCUE_MIN_HEAT_RATIO,
        "runner_rescue_min_ema_gap_r": RUNNER_RESCUE_MIN_EMA_GAP_R,
        "runner_rescue_20d": bool(rescue_20d),
        "extreme_match_before_quality_gate": bool(raw_match is not None),
        "runner_eligible": bool(matched is not None),
        "matched_horizon_days": int(matched["days"]) if matched else "",
    }
    for r in stats:
        d = int(r["days"])
        audit_row[f"extreme_{d}d"] = r["extreme"]
        audit_row[f"distance_{d}d_pct"] = r["distance_pct"] * 100 if math.isfinite(r["distance_pct"]) else float("nan")
        audit_row[f"eligible_{d}d"] = r["eligible"]
    _V32_TARGET_AUDIT.append(audit_row)

    if matched is None or not math.isfinite(stop_distance) or stop_distance <= 0:
        return None

    sign = 1.0 if direction == "LONG" else -1.0
    synthetic = entry + sign * RUNNER_SENTINEL_R * stop_distance
    return (
        synthetic,
        synthetic,
        synthetic,
        RUNNER_SENTINEL_R,
        RUNNER_SENTINEL_R,
        RUNNER_SENTINEL_R,
        RUNNER_SENTINEL_R,
    )


def simulate_adaptive_runner(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
) -> dict[str, Any]:
    runner = abs(tp1 - entry) / stop_distance >= RUNNER_SENTINEL_THRESHOLD_R
    if not runner:
        return v17.simulate_tp1_with_profit_protection(
            frame, entry_i, direction, entry, stop, tp1, stop_distance
        )

    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    active_stop = stop
    stage = 0
    peak_r = 0.0
    end_i = len(frame) - 1

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        stop_fill = engine.bar_hits_stop(direction, o, h, l, active_stop)
        if stop_fill is not None:
            gross_r = sign * (stop_fill - entry) / stop_distance
            reasons = {
                0: "STOP",
                1: "PROTECT_BE_AT_4R",
                3: "PROTECT_2R_AT_6R",
                4: "RUNNER_TRAIL_3R",
            }
            result = engine.finish_result(
                direction,
                entry,
                stop_distance,
                gross_r,
                stop_fill * cost_rate / stop_distance,
                i,
                stop_fill,
                reasons.get(stage, "RUNNER_TRAIL"),
                0,
            )
            result["outcome"] = "WIN" if result["net_r"] > 0 else "LOSS"
            result["target_mode"] = "ADAPTIVE_TREND_RUNNER"
            result["runner_peak_r"] = peak_r
            result["runner_hit_4r"] = peak_r >= 4.0
            return result

        favorable = (h - entry) / stop_distance if direction == "LONG" else (entry - l) / stop_distance
        peak_r = max(peak_r, favorable)

        # V32: strong Trend Runner positions get more breathing room.
        # Do not move the stop at +2R or +3R. This specifically avoids the
        # V25/V26-style early BE washout seen in strong trends such as MU.
        desired_lock_r: float | None = None
        desired_stage = stage
        if peak_r >= 8.0:
            desired_lock_r = max(4.0, peak_r - 3.0)
            desired_stage = 4
        elif peak_r >= 6.0:
            desired_lock_r = 2.0
            desired_stage = 3
        elif peak_r >= 4.0:
            desired_lock_r = 0.0
            desired_stage = 1

        if desired_lock_r is not None:
            candidate_stop = entry + sign * desired_lock_r * stop_distance
            if direction == "LONG":
                active_stop = max(active_stop, candidate_stop)
            else:
                active_stop = min(active_stop, candidate_stop)
            stage = max(stage, desired_stage)

    result = engine.finish_open_result(
        direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i])
    )
    result["target_mode"] = "ADAPTIVE_TREND_RUNNER"
    result["runner_peak_r"] = peak_r
    result["runner_hit_4r"] = peak_r >= 4.0
    return result



def _add_adaptive_market_heat_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """No-look-ahead rolling-24H turnover heat with shorter baselines for new listings."""
    if frame.empty:
        return frame
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    quote_turnover = close * volume
    rolling_24h = quote_turnover.rolling(BARS_24H, min_periods=BARS_24H).sum()
    daily_turnover = quote_turnover.resample("1D").sum(min_count=1)
    lagged = daily_turnover.shift(1)
    med20 = lagged.rolling(20, min_periods=20).median()
    med7 = lagged.rolling(7, min_periods=7).median()
    med3 = lagged.rolling(3, min_periods=3).median()
    completed_days = lagged.notna().astype(int).cumsum()
    day_index = frame.index.normalize()

    def map_daily(series: pd.Series) -> pd.Series:
        return pd.Series(series.reindex(day_index).to_numpy(), index=frame.index, dtype="float64")

    b20, b7, b3 = map_daily(med20), map_daily(med7), map_daily(med3)
    nday = map_daily(completed_days).fillna(0.0)
    baseline = b20.copy()
    required = pd.Series(1.20, index=frame.index, dtype="float64")
    tier_days = pd.Series(20.0, index=frame.index, dtype="float64")

    use7 = nday.lt(20) & nday.ge(7)
    use3 = nday.lt(7) & nday.ge(3)
    baseline.loc[use7] = b7.loc[use7]
    required.loc[use7] = 1.35
    tier_days.loc[use7] = 7.0
    baseline.loc[use3] = b3.loc[use3]
    required.loc[use3] = 1.50
    tier_days.loc[use3] = 3.0
    baseline.loc[nday.lt(3)] = float("nan")
    required.loc[nday.lt(3)] = float("nan")
    tier_days.loc[nday.lt(3)] = float("nan")

    baseline = baseline.replace(0.0, float("nan"))
    ratio = rolling_24h / baseline
    heat_ok = (
        rolling_24h.ge(MIN_ROLLING_24H_QUOTE_TURNOVER)
        & ratio.ge(required)
        & ratio.map(math.isfinite)
    )
    frame["rolling_24h_quote_turnover"] = rolling_24h
    frame["market_heat_baseline"] = baseline
    frame["market_heat_baseline_days"] = tier_days
    frame["market_heat_required_ratio"] = required
    frame["market_heat_ratio"] = ratio
    frame["market_heat_ok"] = heat_ok.fillna(False)
    return frame



def _add_completed_4h_regime_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach no-look-ahead regime values derived only from completed 4H candles."""
    four = frame.resample("4h", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()
    if four.empty:
        frame["v32_regime"] = "TRANSITION"
        frame["v32_regime_sep_atr"] = float("nan")
        frame["v32_regime_slope_atr"] = float("nan")
        frame["v32_regime_crossings6"] = 0
        frame["v32_regime_overlap6"] = float("nan")
        frame["v32_regime_efficiency6"] = float("nan")
        return frame

    four["ema20"] = four["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    four["ema50"] = four["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    prev_close = four["close"].shift(1)
    tr = pd.concat([
        (four["high"] - four["low"]).abs(),
        (four["high"] - prev_close).abs(),
        (four["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    four["atr14"] = tr.rolling(14, min_periods=14).mean()
    four["sep_atr"] = (four["ema20"] - four["ema50"]).abs() / four["atr14"]
    four["slope_atr"] = (four["ema20"] - four["ema20"].shift(3)).abs() / four["atr14"]
    side = (four["close"] >= four["ema20"]).astype(int)
    crossings = side.ne(side.shift(1)).astype(int)
    four["crossings6"] = crossings.rolling(6, min_periods=3).sum().fillna(0)

    # V32 structural chop metrics.  Pairwise range overlap is normalized by the
    # smaller candle range so persistent nesting/overlap approaches 1.0.
    prev_high = four["high"].shift(1)
    prev_low = four["low"].shift(1)
    overlap_points = (pd.concat([four["high"], prev_high], axis=1).min(axis=1)
                      - pd.concat([four["low"], prev_low], axis=1).max(axis=1)).clip(lower=0.0)
    current_range = (four["high"] - four["low"]).clip(lower=1e-12)
    previous_range = (prev_high - prev_low).clip(lower=1e-12)
    smaller_range = pd.concat([current_range, previous_range], axis=1).min(axis=1).clip(lower=1e-12)
    pair_overlap = (overlap_points / smaller_range).clip(lower=0.0, upper=1.0)
    four["overlap6"] = pair_overlap.rolling(6, min_periods=3).mean()

    close_travel = four["close"].diff().abs().rolling(6, min_periods=3).sum()
    net_move = (four["close"] - four["close"].shift(6)).abs()
    four["efficiency6"] = (net_move / close_travel.replace(0.0, float("nan"))).clip(lower=0.0, upper=1.0)

    bull = (
        (four["ema20"] > four["ema50"])
        & (four["close"] > four["ema20"])
        & (four["ema20"] > four["ema20"].shift(3))
        & (four["sep_atr"] >= REGIME_TREND_MIN_EMA_SEP_ATR)
        & (four["slope_atr"] >= REGIME_TREND_MIN_EMA20_SLOPE_ATR)
        & (four["crossings6"] <= REGIME_MAX_TREND_CROSSINGS_6)
        & (four["efficiency6"] >= REGIME_TREND_MIN_EFFICIENCY_6)
        & (four["overlap6"] <= REGIME_TREND_MAX_OVERLAP_6)
    )
    bear = (
        (four["ema20"] < four["ema50"])
        & (four["close"] < four["ema20"])
        & (four["ema20"] < four["ema20"].shift(3))
        & (four["sep_atr"] >= REGIME_TREND_MIN_EMA_SEP_ATR)
        & (four["slope_atr"] >= REGIME_TREND_MIN_EMA20_SLOPE_ATR)
        & (four["crossings6"] <= REGIME_MAX_TREND_CROSSINGS_6)
        & (four["efficiency6"] >= REGIME_TREND_MIN_EFFICIENCY_6)
        & (four["overlap6"] <= REGIME_TREND_MAX_OVERLAP_6)
    )
    ranged = (
        (four["sep_atr"] <= REGIME_RANGE_MAX_EMA_SEP_ATR)
        | (
            (four["crossings6"] >= REGIME_RANGE_MIN_CROSSINGS_6)
            & (four["slope_atr"] <= REGIME_RANGE_MAX_EMA20_SLOPE_ATR)
        )
        | (
            (four["overlap6"] >= REGIME_RANGE_MIN_OVERLAP_6)
            & (four["efficiency6"] <= REGIME_RANGE_MAX_EFFICIENCY_6)
        )
    )
    regime = pd.Series("TRANSITION", index=four.index, dtype="object")
    regime.loc[ranged] = "RANGE"
    regime.loc[bull] = "TREND_BULL"
    regime.loc[bear] = "TREND_BEAR"
    four["regime"] = regime

    # The current 4H bucket is incomplete. Shift one 4H bucket before carrying
    # state down to 15m bars, preventing any future information from leaking in.
    completed = four[["regime", "sep_atr", "slope_atr", "crossings6", "overlap6", "efficiency6"]].shift(1)
    aligned = completed.reindex(frame.index, method="ffill")
    frame["v32_regime"] = aligned["regime"].fillna("TRANSITION")
    frame["v32_regime_sep_atr"] = aligned["sep_atr"]
    frame["v32_regime_slope_atr"] = aligned["slope_atr"]
    frame["v32_regime_crossings6"] = aligned["crossings6"].fillna(0)
    frame["v32_regime_overlap6"] = aligned["overlap6"]
    frame["v32_regime_efficiency6"] = aligned["efficiency6"]
    return frame


def _regime_at(frame: pd.DataFrame, ts: Any) -> tuple[str, float, float, float, float, float]:
    idx = int(frame.index.get_indexer([pd.Timestamp(ts)], method="pad")[0])
    if idx < 0:
        return "TRANSITION", float("nan"), float("nan"), 0.0, float("nan"), float("nan")
    row = frame.iloc[idx]
    return (
        str(row.get("v32_regime", "TRANSITION")),
        float(row.get("v32_regime_sep_atr", float("nan"))),
        float(row.get("v32_regime_slope_atr", float("nan"))),
        float(row.get("v32_regime_crossings6", 0.0)),
        float(row.get("v32_regime_overlap6", float("nan"))),
        float(row.get("v32_regime_efficiency6", float("nan"))),
    )


def _aligned_trend(regime: str, direction: str) -> bool:
    return (direction == "LONG" and regime == "TREND_BULL") or (
        direction == "SHORT" and regime == "TREND_BEAR"
    )


def _apply_regime_and_post_stop_policy(frame: pd.DataFrame, candidates: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Filter candidates chronologically and reassess the market after true stops."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: pd.Timestamp(c[5]))
    kept: list[tuple[Any, ...]] = []
    last_stop_exit: pd.Timestamp | None = None
    last_stop_direction: str | None = None

    for c in ordered:
        direction = str(c[3])
        signal_ts = pd.Timestamp(c[4])
        entry_ts = pd.Timestamp(c[5])
        exit_ts = pd.Timestamp(c[6])
        exit_reason = str(c[20])
        is_runner = float(c[14]) >= RUNNER_SENTINEL_THRESHOLD_R
        regime, sep_atr, slope_atr, crossings6, overlap6, efficiency6 = _regime_at(frame, signal_ts)
        aligned = _aligned_trend(regime, direction)

        decision = "KEEP"
        reason = "ALIGNED_TREND"
        if regime == "RANGE":
            decision, reason = "SKIP", "RANGE_NO_BREAKOUT"
        elif is_runner and not aligned:
            decision, reason = "SKIP", "RUNNER_REQUIRES_ALIGNED_TREND"
        elif (not is_runner) and regime == "TRANSITION":
            reason = "STRUCTURE_ALLOWED_IN_TRANSITION"
        elif not aligned and regime.startswith("TREND_"):
            decision, reason = "SKIP", "OPPOSITE_TREND"

        post_stop = last_stop_exit is not None and signal_ts > last_stop_exit
        if decision == "KEEP" and post_stop:
            hours = (signal_ts - last_stop_exit).total_seconds() / 3600.0
            if hours < POST_STOP_MIN_WAIT_HOURS:
                decision, reason = "SKIP", "POST_STOP_MIN_WAIT"
            elif not aligned:
                decision, reason = "SKIP", "POST_STOP_REASSESS_NOT_TREND"
            elif (not math.isfinite(efficiency6) or efficiency6 < POST_STOP_MIN_EFFICIENCY_6
                  or not math.isfinite(overlap6) or overlap6 > POST_STOP_MAX_OVERLAP_6):
                decision, reason = "SKIP", "POST_STOP_TREND_NOT_CLEAN_ENOUGH"
            else:
                reason = "POST_STOP_TREND_RECONFIRMED"

        audit = {
            "exchange": c[0], "symbol": c[1], "base": c[2], "direction": direction,
            "signal_time": c[4], "entry_time": c[5], "exit_time": c[6],
            "target_mode": "RUNNER" if is_runner else "STRUCTURE",
            "regime": regime, "ema_sep_atr": sep_atr, "ema20_slope_atr": slope_atr,
            "crossings6": crossings6, "overlap6": overlap6, "efficiency6": efficiency6,
            "aligned_trend": aligned,
            "post_stop_reassessment": post_stop, "decision": decision, "decision_reason": reason,
            "prior_stop_exit": "" if last_stop_exit is None else last_stop_exit.isoformat(),
            "prior_stop_direction": last_stop_direction or "",
            "candidate_exit_reason": exit_reason,
        }
        _V32_REGIME_AUDIT.append(audit)
        if post_stop:
            _V32_REASSESS_AUDIT.append(dict(audit))

        if decision != "KEEP":
            continue
        kept.append(c)

        # Only a genuine initial-stop exit invalidates the current trend thesis.
        # BE/trailing exits are profit-management events and do not trigger reassessment.
        if exit_reason == "STOP":
            last_stop_exit = exit_ts
            last_stop_direction = direction

    return kept


def _atr14_at(frame: pd.DataFrame, i: int) -> float:
    row = frame.iloc[i]
    direct = float(row.get("atr", float("nan")))
    if math.isfinite(direct) and direct > 0:
        return direct
    start = max(1, i - 20)
    part = frame.iloc[start - 1:i + 1]
    high = pd.to_numeric(part["high"], errors="coerce")
    low = pd.to_numeric(part["low"], errors="coerce")
    close = pd.to_numeric(part["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    val = float(tr.tail(14).mean())
    return val if math.isfinite(val) and val > 0 else float("nan")


def _runner_major_stop(frame: pd.DataFrame, signal_i: int, direction: str, entry: float, original_stop: float) -> tuple[float, dict[str, Any]]:
    atr = _atr14_at(frame, signal_i)
    row = frame.iloc[signal_i]
    ema50 = float(row.get("ema50_15m", float("nan")))
    original_distance = abs(entry - original_stop)
    cap_distance = entry * RUNNER_MAX_INITIAL_STOP_PCT / 100.0

    if not (math.isfinite(atr) and atr > 0 and math.isfinite(ema50) and ema50 > 0):
        return original_stop, {"ema50": ema50, "atr": atr, "stop_source": "ORIGINAL_FALLBACK"}

    if direction == "LONG":
        structural = ema50 - RUNNER_STOP_EMA50_ATR_BUFFER * atr
        proposed = min(original_stop, structural)
        effective = max(proposed, entry - cap_distance)
    else:
        structural = ema50 + RUNNER_STOP_EMA50_ATR_BUFFER * atr
        proposed = max(original_stop, structural)
        effective = min(proposed, entry + cap_distance)

    # Never tighten a Runner's initial stop by this module.
    effective_distance = abs(entry - effective)
    if not math.isfinite(effective_distance) or effective_distance <= original_distance:
        effective = original_stop
        source = "ORIGINAL_ALREADY_WIDER"
    else:
        source = "EMA50_ATR_MAJOR_STRUCTURE"
    return effective, {"ema50": ema50, "atr": atr, "structural_stop": structural, "stop_source": source}


def _resimulate_runner_row(frame: pd.DataFrame, row_tuple: tuple[Any, ...]) -> tuple[Any, ...]:
    row = list(row_tuple)
    # rr1 is tuple index 14. Sentinel RR identifies a Trend Runner candidate.
    if float(row[14]) < RUNNER_SENTINEL_THRESHOLD_R:
        return row_tuple

    direction = str(row[3])
    signal_ts = pd.Timestamp(row[4])
    entry_ts = pd.Timestamp(row[5])
    entry = float(row[9])
    original_stop = float(row[10])
    signal_i = int(frame.index.get_indexer([signal_ts], method="nearest")[0])
    entry_i = int(frame.index.get_indexer([entry_ts], method="nearest")[0])
    if signal_i < 0 or entry_i < 0:
        return row_tuple

    effective_stop, meta = _runner_major_stop(frame, signal_i, direction, entry, original_stop)
    original_distance = abs(entry - original_stop)
    effective_distance = abs(entry - effective_stop)
    sign = 1.0 if direction == "LONG" else -1.0
    synthetic = entry + sign * RUNNER_SENTINEL_R * effective_distance

    result = simulate_adaptive_runner(
        frame, entry_i, direction, entry, effective_stop, synthetic, effective_distance
    )
    exit_i = int(result["exit_i"])
    trade_window = frame.iloc[entry_i:exit_i + 1]
    if direction == "LONG":
        mfe_r = (float(trade_window["high"].max()) - entry) / effective_distance
        mae_r = (entry - float(trade_window["low"].min())) / effective_distance
    else:
        mfe_r = (entry - float(trade_window["low"].min())) / effective_distance
        mae_r = (float(trade_window["high"].max()) - entry) / effective_distance

    atr = float(meta.get("atr", float("nan")))
    stop_atr = effective_distance / atr if math.isfinite(atr) and atr > 0 else float("nan")
    stop_pct = effective_distance / entry * 100.0

    # Rewrite final candidate tuple so CSV/SQLite reports the stop actually simulated.
    row[6] = frame.index[exit_i].isoformat()
    row[10] = effective_stop
    row[11] = synthetic
    row[12] = synthetic
    row[13] = synthetic
    row[14] = RUNNER_SENTINEL_R
    row[15] = RUNNER_SENTINEL_R
    row[16] = RUNNER_SENTINEL_R
    row[17] = result["exit_price"]
    row[19] = result["outcome"]
    row[20] = result["exit_reason"]
    row[21] = result["timed_out"]
    row[22] = result["gross_r"]
    row[23] = result["cost_r"]
    row[24] = result["net_r"]
    row[28] = RUNNER_SENTINEL_R
    row[29] = stop_atr
    row[30] = stop_pct
    row[32] = exit_i - entry_i + 1
    row[33] = mfe_r
    row[34] = mae_r

    _V32_STOP_AUDIT.append({
        "exchange": row[0], "symbol": row[1], "base": row[2], "direction": direction,
        "signal_time": row[4], "entry_time": row[5], "entry": entry,
        "original_stop": original_stop, "effective_stop": effective_stop,
        "original_stop_pct": original_distance / entry * 100.0,
        "effective_stop_pct": stop_pct,
        "ema50_15m": meta.get("ema50"), "atr": meta.get("atr"),
        "structural_stop": meta.get("structural_stop"), "stop_source": meta.get("stop_source"),
        "mfe_r_after_widening": mfe_r, "mae_r_after_widening": mae_r,
        "exit_reason_after_widening": result["exit_reason"], "net_r_after_widening": result["net_r"],
    })
    return tuple(row)


def candidate_rows_v32(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    global _CURRENT_TARGET
    _add_adaptive_market_heat_columns(frame)
    _add_completed_4h_regime_columns(frame)
    heat_ok = frame["market_heat_ok"].astype(bool)
    heat_eval = int(frame["market_heat_ratio"].notna().sum())
    heat_ratio = int(frame["market_heat_ratio"].ge(frame["market_heat_required_ratio"]).fillna(False).sum())
    heat_final = int(heat_ok.sum())
    funnel["V32_heat_evaluable_bars"] = funnel.get("V32_heat_evaluable_bars", 0) + heat_eval
    funnel["V32_heat_ratio_ge_threshold_bars"] = funnel.get("V32_heat_ratio_ge_threshold_bars", 0) + heat_ratio
    funnel["V32_heat_and_500k_floor_bars"] = funnel.get("V32_heat_and_500k_floor_bars", 0) + heat_final

    original_bull4h = frame["bull4h"].copy()
    original_bear4h = frame["bear4h"].copy()
    frame["bull4h"] = original_bull4h.astype(bool) & heat_ok
    frame["bear4h"] = original_bear4h.astype(bool) & heat_ok
    before = dict(funnel)
    _CURRENT_TARGET = target
    try:
        candidates = list(
            v17._original_candidate_rows_v17(
                target, frame, research_start, pivot_highs, pivot_lows, funnel
            )
        )
    finally:
        _CURRENT_TARGET = None
        frame["bull4h"] = original_bull4h
        frame["bear4h"] = original_bear4h

    candidates = [_resimulate_runner_row(frame, c) for c in candidates]
    raw_candidate_count = len(candidates)
    candidates = _apply_regime_and_post_stop_policy(frame, candidates)
    regime_filtered_count = raw_candidate_count - len(candidates)

    delta = {
        k: int(v) - int(before.get(k, 0))
        for k, v in funnel.items()
        if int(v) - int(before.get(k, 0))
    }
    runner_count = sum(float(c[14]) >= RUNNER_SENTINEL_THRESHOLD_R for c in candidates)
    long_count = sum(str(c[3]) == "LONG" for c in candidates)
    short_count = sum(str(c[3]) == "SHORT" for c in candidates)
    out = {
        "exchange": getattr(target, "exchange_name", ""),
        "symbol": getattr(target, "symbol", ""),
        "base": getattr(target, "base", ""),
        "heat_evaluable_bars": heat_eval,
        "heat_ratio_ge_1_20_bars": heat_ratio,
        "heat_and_500k_floor_bars": heat_final,
        "published_candidates": len(candidates),
        "long_candidates": long_count,
        "short_candidates": short_count,
        "adaptive_runner_candidates": runner_count,
        "raw_candidates_before_regime": raw_candidate_count,
        "regime_filtered_candidates": regime_filtered_count,
    }
    out.update({f"funnel__{k}": v for k, v in delta.items()})
    _V32_PER_MARKET_FUNNEL.append(out)
    funnel["V32_raw_candidates_before_regime"] = funnel.get("V32_raw_candidates_before_regime", 0) + raw_candidate_count
    funnel["V32_regime_filtered_candidates"] = funnel.get("V32_regime_filtered_candidates", 0) + regime_filtered_count
    funnel["V32_source_candidates"] = funnel.get("V32_source_candidates", 0) + len(candidates)
    funnel["V32_runner_candidates"] = funnel.get("V32_runner_candidates", 0) + runner_count
    yield from candidates


def prefilter_no_fixed_5m(targets: list[Any], exchanges: dict[str, Any], errors: list[dict[str, str]]) -> list[Any]:
    return list(targets)


def _write_union_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                fields.append(k)
                seen.add(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def run_exchange_branch_v32() -> dict[str, Any]:
    MARKETS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    old_configs = engine.SCHEME_CONFIGS
    old_schemes = engine.SCHEMES
    old_primary = engine.PRIMARY_SCHEME
    old_output = engine.OUTPUT_DIR
    old_database = engine.DATABASE_PATH
    old_candidates = engine.candidate_rows
    old_discover = engine.discover_unique_targets
    old_prefilter = engine.prefilter_liquid_targets
    old_target = engine.confirmed_structure_targets
    old_simulator = engine.simulate_tp1_all

    engine.discover_unique_targets = v22.discover_three_exchange_targets
    engine.prefilter_liquid_targets = prefilter_no_fixed_5m
    engine.SCHEME_CONFIGS = {SCHEME_V32: dict(V32_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V32,)
    engine.PRIMARY_SCHEME = SCHEME_V32
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_v32
    engine.confirmed_structure_targets = selective_structure_or_runner_targets
    engine.simulate_tp1_all = simulate_adaptive_runner

    print("\n[V32-MARKETS] 開始：幣圈＋HIGH_CONFIDENCE美股代幣｜LONG + SHORT")
    print("[V32-MARKETS] 市場熱度：24H>=50萬；>=20日資料用1.20x，7-19日用1.35x，3-6日用1.50x")
    print("[V32-MARKETS] Regime-aware：4H用EMA＋K棒重疊率＋趨勢效率判斷；Runner只允許乾淨順勢TREND，STOP後需更強重新確認。")
    print("[V32-MARKETS] Runner保護：+2R/+3R不動、+4R才BE、+6R鎖2R、+8R鎖4R、之後peakR-3R")

    try:
        rc = int(engine.main())
    finally:
        engine.SCHEME_CONFIGS = old_configs
        engine.SCHEMES = old_schemes
        engine.PRIMARY_SCHEME = old_primary
        engine.OUTPUT_DIR = old_output
        engine.DATABASE_PATH = old_database
        engine.candidate_rows = old_candidates
        engine.discover_unique_targets = old_discover
        engine.prefilter_liquid_targets = old_prefilter
        engine.confirmed_structure_targets = old_target
        engine.simulate_tp1_all = old_simulator

    segments = v22._split_exchange_outputs()
    _write_union_csv(MARKETS_OUTPUT_DIR / "per_market_funnel_v32.csv", _V32_PER_MARKET_FUNNEL)
    _write_union_csv(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v32.csv", _V32_TARGET_AUDIT)
    _write_union_csv(MARKETS_OUTPUT_DIR / "runner_stop_audit_v32.csv", _V32_STOP_AUDIT)
    _write_union_csv(MARKETS_OUTPUT_DIR / "regime_audit_v32.csv", _V32_REGIME_AUDIT)
    _write_union_csv(MARKETS_OUTPUT_DIR / "post_stop_reassessment_v32.csv", _V32_REASSESS_AUDIT)

    summary_path = MARKETS_OUTPUT_DIR / "summary.json"
    native_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    result = {
        "return_code": rc,
        "engine_summary": native_summary,
        "segments": segments,
        "market_heat": {
            "rolling_24h_safety_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "baseline": "adaptive completed-day median: 20d/7d/3d",
            "minimum_relative_ratio": "20d:1.20x; 7d:1.35x; 3d:1.50x",
            "lookahead": False,
        },
        "adaptive_runner": {
            "real_structure_first": True,
            "extreme_rules": [
                {"lookback_days": d, "tolerance_pct": t * 100}
                for d, t in RUNNER_EXTREME_RULES
            ],
            "fixed_tp": False,
            "protection": "+2R/+3R unchanged,+4R->BE,+6R->+2R,+8R->+4R,then peakR-3R",
        },
    }
    (MARKETS_OUTPUT_DIR / "segments_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def audit_v32(exchange_result: dict[str, Any]) -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V32.lower()}.csv"
    invalid_rr = 0
    invalid_direction = 0
    for row in v22._read_csv_rows(trade_path):
        if str(row.get("direction")) not in {"LONG", "SHORT"}:
            invalid_direction += 1
        try:
            if float(row.get("rr1") or 0.0) < 4.0 - 1e-9:
                invalid_rr += 1
        except Exception:
            invalid_rr += 1

    required_segments = {"CRYPTO_LONG", "CRYPTO_SHORT", "US_TOKEN_LONG", "US_TOKEN_SHORT"}
    segments = exchange_result.get("segments", {})
    audit = {
        "version": "V32",
        "fixed_5m_gate_removed": True,
        "top_n_gate_removed": True,
        "market_heat_rule": {
            "rolling_24h_quote_turnover_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "comparison": "adaptive: >=1.20x previous-20d median; new listings >=1.35x previous-7d or >=1.50x previous-3d median",
            "current_day_excluded_from_baseline": True,
            "historical_bar_specific": True,
        },
        "signal_relative_volume": "previous-20-bar median; breakout>=1.30x; retest 0.60-1.20x; confirm>=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R first; otherwise strict runner or narrow 20d breakout-rescue runner",
        "runner_protection": "+2R/+3R no move; +4R BE; +6R +2R; +8R +4R; >8R trail peakR-3R",
        "runner_target_audit_csv": str(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v32.csv"),
        "runner_stop_audit_csv": str(MARKETS_OUTPUT_DIR / "runner_stop_audit_v32.csv"),
        "runner_initial_stop": "widen-only EMA50 +/-0.30ATR, capped at 8% from entry",
        "market_regime_policy": "completed 4H only; EMA + crossings + structural overlap + directional efficiency; Runner requires clean aligned TREND",
        "post_stop_policy": "after STOP, wait >=4h and require aligned completed-4H trend before re-entry",
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required_segments.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v32_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V32 啟動｜Adaptive Trend Runner＋相對市場熱度")
    print("市場：Binance / Bitget / BingX；幣圈與HIGH_CONFIDENCE美股代幣；LONG / SHORT 都開。")
    print("不指定SKY/MU，不做單幣特判；它們只會在結果分析時當強趨勢驗收樣本。")
    print("市場熱度：24H>=50萬；20日成熟市場>=1.20x，7-19日新市場>=1.35x，3-6日>=1.50x。")
    print("進場量能：突破/跌破>=1.30x；回踩/反抽0.60~1.20x；確認>=1.00x且比回踩+10%。")
    print("TP：真實結構>=4R優先；Runner需順勢TREND。STOP後不盲目重進，先重新判斷4H趨勢/區間。")
    print("Runner保護：+2R/+3R不移停損；+4R才BE；+6R鎖+2R；+8R鎖+4R；之後peakR-3R。")
    print("Funding只記錄、不擋單；無時間出場。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch_v32()
    audit = audit_v32(exchange_result)
    combined = {
        "version": "V32",
        "exchange_markets": exchange_result,
        "us_stocks": {"skipped": True, "reason": "V32 focuses on exchange markets; Alpaca only validates stock-token underlyings"},
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v32.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V32 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"逐市場漏斗：{MARKETS_OUTPUT_DIR / 'per_market_funnel_v32.csv'}")
    print(f"Runner目標稽核：{MARKETS_OUTPUT_DIR / 'trend_runner_target_audit_v32.csv'}")
    print(f"總結：{OUTPUT_DIR / 'summary_v32.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v32_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
