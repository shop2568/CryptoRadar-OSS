#!/usr/bin/env python3
"""CryptoRadar V57 — V56 clean baseline + entry-timing research + MAX5 vs unlimited-capacity A/B.

Purpose
-------
V57 does NOT hard-code IBM / CAT / PLTR or any other ticker.  Those charts only exposed a
repeating timing problem: confirmation can arrive after a large part of the move has already
happened.  V57 therefore keeps the V56 formal strategy intact and adds general, no-look-ahead
research around entry location.

Formal baseline kept from V56
-----------------------------
* exchange crypto + stock tokens, LONG + SHORT;
* protected V39-style 3-slot core + up to 2 Elite expansion positions (MAX5);
* 1% account risk per trade;
* real structural stop;
* before REAL TP1 the original stop never moves;
* REAL TP1: 50% exits, remaining 50% follows prior-completed-day 5D/10D MA;
* Price Discovery Runner: original structural stop only, NO profit-protection/trailing ladder;
* US-stock Alpaca underlying research and SPY/QQQ/DIA regime research remain available.

New V57 research
----------------
A) Capacity A/B
   1. original MAX5 portfolio;
   2. unlimited-capacity research portfolio: remove only total/same-direction capacity limits
      where practical, while preserving signal generation, risk, costs and same-symbol cooldown.

B) Late-entry / chase audit
   * For every exchange candidate and US-stock candidate, measure how far the entry already is
     from the prior completed 4H 20-bar swing extreme, normalized by 4H ATR.
   * distance > 3.25 ATR is tagged CHASE.  This is research output only; the formal V56 baseline
     is not silently changed.

C) Native US-stock 4H early-timing research
   * EARLY_FIRST_TURN_LONG: fresh bottom + first short-term turn/reclaim;
   * EARLY_HIGH_TURN_SHORT: fresh high + first short-term bearish turn;
   * EARLY_REBOUND_FAIL_SHORT: damaged trend + rebound into short-term resistance + rejection.
   * all signals use completed 4H bars only and execute at the next available 15m open;
   * stops use known 4H swing structure + 0.25 ATR buffer;
   * targets still require >=4R real structure, with inherited price-discovery Runner only as fallback;
   * no ticker-specific exceptions.

D) Entry-quality score
   * 0-100 observational score based on chase distance, stop geometry, >=4R room and setup freshness;
   * compare all early-timing signals vs score >=70 without changing the formal baseline.

E) Early-vs-late replacement research
   * merge baseline + early candidates, preferring the first signal in the same symbol/direction
     episode (72h de-duplication window), then compare MAX5 vs unlimited capacity.

This is an in-sample research version.  Any useful filter must be validated on a fresh/forward
sample before live trading.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path.home() / "CryptoRadar"
V56_PATH = ROOT / "backtest_1y_v56.py"
if not V56_PATH.exists():
    V56_PATH = Path(__file__).with_name("backtest_1y_v56.py")
if not V56_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v56.py；請把 V56 與 V57 放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v56_components_v57", V56_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入 V56：{V56_PATH}")
v56 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v56
spec.loader.exec_module(v56)

engine = v56.engine
v22 = v56.v22
v32 = v56.v32

# --------------------------------------------------------------------------------------
# Version/output redirection.  V56 function names are intentionally reused, but every output
# lands in the V57 result directory and the candidate scheme is relabeled V57.
# --------------------------------------------------------------------------------------
OUTPUT_DIR = ROOT / "backtest_1y_v57_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"
ALPACA_D1_OUTPUT_DIR = OUTPUT_DIR / "us_stocks_alpaca_d1"
TIMING_OUTPUT_DIR = OUTPUT_DIR / "timing_research"

SCHEME_V57 = "V57_V32_CORE_PLUS_STRUCTURAL_IGNITION_FAST"
STOCK_SCHEME_V57 = "V57_ALPACA_UNDERLYING_D1_LIFECYCLE"

v56.OUTPUT_DIR = OUTPUT_DIR
v56.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
v56.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
v56.ALPACA_D1_OUTPUT_DIR = ALPACA_D1_OUTPUT_DIR
v56.SCHEME_V56 = SCHEME_V57
v56.V56_STOCK_SCHEME = STOCK_SCHEME_V57

# Reset inherited global audits/caches so rerunning V57 in the same interpreter is deterministic.
for name in ("_STRUCTURE_AUDIT", "_PER_MARKET_STRUCTURE", "_V56_EXPANSION_AUDIT"):
    obj = getattr(v56, name, None)
    if hasattr(obj, "clear"):
        obj.clear()
if hasattr(v56, "_V56_MA_CACHE"):
    v56._V56_MA_CACHE.clear()
if hasattr(v56, "_V56_MA_CACHE_STATS"):
    v56._V56_MA_CACHE_STATS.update({"builds": 0, "hits": 0, "evictions": 0})

V57_CHASE_LIMIT_ATR = 3.25
V57_EARLY_REARM_HOURS = 24
V57_EPISODE_DEDUPE_HOURS = 72
V57_EARLY_MIN_STOP_PCT = 0.80
V57_EARLY_MAX_STOP_PCT = 12.0
V57_EARLY_STOP_BUFFER_ATR = 0.25
V57_EARLY_MIN_ROOM_R = 4.0
V57_EARLY_QUALITY_THRESHOLD = 70.0

_EXCHANGE_TIMING_AUDIT: list[dict[str, Any]] = []
_EARLY_STOCK_ROWS: list[dict[str, Any]] = []
_EARLY_STOCK_FUNNEL: dict[str, int] = {}
_INDEX_REGIME_CAPTURE: pd.DataFrame = pd.DataFrame()


def _write_union(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _closed_win(row: dict[str, Any]) -> bool:
    return str(row.get("outcome") or "") != "OPEN" and _safe_float(row.get("net_r"), 0.0) > 0


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if str(r.get("outcome") or "") != "OPEN"]
    wins = [r for r in closed if _safe_float(r.get("net_r"), 0.0) > 0]
    losses = [r for r in closed if _safe_float(r.get("net_r"), 0.0) < 0]
    return {
        "trades": len(rows),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(rows) - len(closed),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 3) if closed else 0.0,
        "total_net_r": round(sum(_safe_float(r.get("net_r"), 0.0) for r in rows), 5),
        "net_profit_usdt": round(sum(_safe_float(r.get("profit_usdt"), 0.0) for r in rows), 4),
        "unrealized_profit_usdt": round(sum(_safe_float(r.get("unrealized_profit_usdt"), 0.0) for r in rows), 4),
    }


def _drawdown_from_selected(rows: list[dict[str, Any]], starting_equity: float) -> float:
    events: list[tuple[pd.Timestamp, float]] = []
    for r in rows:
        if str(r.get("outcome") or "") == "OPEN":
            continue
        try:
            ts = pd.Timestamp(r.get("exit_time"))
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
        except Exception:
            continue
        events.append((ts, _safe_float(r.get("profit_usdt"), 0.0)))
    equity = float(starting_equity)
    peak = equity
    max_dd = 0.0
    for _, pnl in sorted(events, key=lambda x: x[0]):
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return round(max_dd, 3)


# --------------------------------------------------------------------------------------
# Guaranteed fixed-stop Runner.  This deliberately overrides any inherited runner path so a
# synthetic/no-real-TP1 trade cannot use RUNNER_TRAIL_* in V57.
# --------------------------------------------------------------------------------------
def simulate_fixed_stop_runner_v57(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
) -> dict[str, Any]:
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    end_i = len(frame) - 1
    peak_r = 0.0

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
        stop_fill = engine.bar_hits_stop(direction, o, h, l, float(stop))
        if stop_fill is not None:
            gross_r = sign * (float(stop_fill) - entry) / stop_distance
            result = engine.finish_result(
                direction, entry, stop_distance, gross_r,
                float(stop_fill) * cost_rate / stop_distance,
                i, float(stop_fill), "STOP", 0,
            )
            result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
            result["target_mode"] = "V57_FIXED_STOP_PRICE_DISCOVERY_RUNNER"
            result["runner_peak_r"] = float(peak_r)
            result["runner_hit_4r"] = bool(peak_r >= 4.0)
            result["tp1_was_hit"] = False
            result["tp1_exit_fraction"] = 0.0
            result["trend_remainder_fraction"] = 0.0
            result["post_tp1_ma_days"] = 0
            return result
        favorable = (h - entry) / stop_distance if direction == "LONG" else (entry - l) / stop_distance
        if math.isfinite(favorable):
            peak_r = max(peak_r, float(favorable))

    result = engine.finish_open_result(
        direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i])
    )
    result["target_mode"] = "V57_FIXED_STOP_PRICE_DISCOVERY_RUNNER"
    result["runner_peak_r"] = float(peak_r)
    result["runner_hit_4r"] = bool(peak_r >= 4.0)
    result["tp1_was_hit"] = False
    result["tp1_exit_fraction"] = 0.0
    result["trend_remainder_fraction"] = 0.0
    result["post_tp1_ma_days"] = 0
    return result


v56.simulate_fixed_stop_runner_v56 = simulate_fixed_stop_runner_v57


# --------------------------------------------------------------------------------------
# Completed-4H timing context.  All swing/ATR measurements are shifted one 4H bar before they
# are aligned to a 15m signal; therefore a signal never sees an unfinished/future 4H bar.
# --------------------------------------------------------------------------------------
def _completed_4h_context(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(index=getattr(frame, "index", None))
    four = frame.resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"])
    if len(four) < 25:
        return pd.DataFrame(index=frame.index)
    prev_close = four["close"].shift(1)
    tr = pd.concat([
        four["high"] - four["low"],
        (four["high"] - prev_close).abs(),
        (four["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    four["atr14"] = tr.rolling(14, min_periods=14).mean()
    four["ema5"] = four["close"].ewm(span=5, adjust=False).mean()
    four["ema10"] = four["close"].ewm(span=10, adjust=False).mean()
    four["ema20"] = four["close"].ewm(span=20, adjust=False).mean()
    four["ema50"] = four["close"].ewm(span=50, adjust=False).mean()
    four["swing_low20"] = four["low"].shift(1).rolling(20, min_periods=10).min()
    four["swing_high20"] = four["high"].shift(1).rolling(20, min_periods=10).max()
    four["swing_low6"] = four["low"].shift(1).rolling(6, min_periods=4).min()
    four["swing_high6"] = four["high"].shift(1).rolling(6, min_periods=4).max()
    four["high3"] = four["high"].shift(1).rolling(3, min_periods=3).max()
    four["low3"] = four["low"].shift(1).rolling(3, min_periods=3).min()

    # Shift the entire state so an intrabar 15m signal uses only the previous completed 4H bar.
    completed = four[[
        "atr14", "ema5", "ema10", "ema20", "ema50",
        "swing_low20", "swing_high20", "swing_low6", "swing_high6", "high3", "low3"
    ]].shift(1)
    return completed.reindex(frame.index, method="ffill")


def _chase_from_context(
    direction: str,
    entry: float,
    row: pd.Series,
) -> tuple[float, str]:
    atr = _safe_float(row.get("atr14"))
    if not math.isfinite(atr) or atr <= 0:
        return float("nan"), "UNKNOWN"
    if direction == "LONG":
        swing = _safe_float(row.get("swing_low20"))
        distance = (entry - swing) / atr if math.isfinite(swing) else float("nan")
    else:
        swing = _safe_float(row.get("swing_high20"))
        distance = (swing - entry) / atr if math.isfinite(swing) else float("nan")
    if not math.isfinite(distance):
        return float("nan"), "UNKNOWN"
    return float(distance), "CHASE" if distance > V57_CHASE_LIMIT_ATR else "OK"


def _entry_quality_score(chase_atr: float, stop_pct: float, rr1: float, setup_type: str) -> float:
    # Chase: 40 pts. Full marks <=1.25 ATR; fades to zero at 3.25 ATR.
    if not math.isfinite(chase_atr):
        chase_pts = 10.0
    elif chase_atr <= 1.25:
        chase_pts = 40.0
    elif chase_atr >= V57_CHASE_LIMIT_ATR:
        chase_pts = 0.0
    else:
        chase_pts = 40.0 * (V57_CHASE_LIMIT_ATR - chase_atr) / (V57_CHASE_LIMIT_ATR - 1.25)

    # Stop geometry: 25 pts. 1-6% is the clean zone, then it decays.
    if not math.isfinite(stop_pct) or stop_pct <= 0:
        stop_pts = 0.0
    elif 1.0 <= stop_pct <= 6.0:
        stop_pts = 25.0
    elif stop_pct < 1.0:
        stop_pts = max(0.0, 25.0 * stop_pct / 1.0)
    else:
        stop_pts = max(0.0, 25.0 * (12.0 - stop_pct) / 6.0)

    # Room: 25 pts; 4R starts at 12.5 points, 8R reaches full marks.
    if not math.isfinite(rr1) or rr1 < 4.0:
        room_pts = 0.0
    else:
        room_pts = min(25.0, 12.5 + (min(rr1, 8.0) - 4.0) / 4.0 * 12.5)

    freshness = {
        "EARLY_FIRST_TURN_LONG": 10.0,
        "EARLY_HIGH_TURN_SHORT": 10.0,
        "EARLY_REBOUND_FAIL_SHORT": 10.0,
        "FIRST_PULLBACK_RECLAIM": 9.0,
        "DIRECT_BREAK": 6.0,
        "STRUCTURAL_REENTRY": 7.0,
    }.get(str(setup_type), 5.0)
    return round(max(0.0, min(100.0, chase_pts + stop_pts + room_pts + freshness)), 2)


# --------------------------------------------------------------------------------------
# Exchange candidate timing audit.  It observes the V56 candidate stream without removing it.
# --------------------------------------------------------------------------------------
_ORIGINAL_CANDIDATE_ROWS = v56.candidate_rows_v56


def candidate_rows_v57(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    rows = list(_ORIGINAL_CANDIDATE_ROWS(target, frame, research_start, pivot_highs, pivot_lows, funnel))
    if rows and frame is not None and not frame.empty:
        ctx = _completed_4h_context(frame)
        for tup in rows:
            try:
                direction = str(tup[3])
                signal_ts = pd.Timestamp(tup[4])
                entry_time = str(tup[5])
                entry = float(tup[9])
                stop = float(tup[10])
                rr1 = float(tup[14])
                setup_type = ""
                # structure audit is separate; V32 core rows legitimately have no setup label here.
                pos = int(frame.index.searchsorted(signal_ts, side="right") - 1)
                if pos < 0 or pos >= len(ctx):
                    continue
                chase, flag = _chase_from_context(direction, entry, ctx.iloc[pos])
                stop_pct = abs(entry - stop) / entry * 100.0 if entry > 0 else float("nan")
                _EXCHANGE_TIMING_AUDIT.append({
                    "exchange": str(tup[0]), "symbol": str(tup[1]), "base": str(tup[2]),
                    "direction": direction, "signal_time": str(tup[4]), "entry_time": entry_time,
                    "entry": entry, "stop": stop, "rr1": rr1,
                    "chase_distance_4h_atr": chase, "late_entry_flag": flag,
                    "timing_quality_score": _entry_quality_score(chase, stop_pct, rr1, setup_type),
                })
            except Exception:
                continue
    yield from rows


v56.candidate_rows_v56 = candidate_rows_v57


# --------------------------------------------------------------------------------------
# Native US-stock early 4H timing model.
# --------------------------------------------------------------------------------------
def _four_hour_stock_frame(frame: pd.DataFrame) -> pd.DataFrame:
    four = frame.resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"])
    if four.empty:
        return four
    last_ts = frame.index.to_series().resample("4h").last().reindex(four.index)
    prev_close = four["close"].shift(1)
    tr = pd.concat([
        four["high"] - four["low"],
        (four["high"] - prev_close).abs(),
        (four["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    four["atr14"] = tr.rolling(14, min_periods=14).mean()
    for span in (5, 10, 20, 50):
        four[f"ema{span}"] = four["close"].ewm(span=span, adjust=False).mean()
    four["ema5_slope"] = four["ema5"] - four["ema5"].shift(1)
    four["ema20_slope"] = four["ema20"] - four["ema20"].shift(1)
    four["prev_high2"] = four["high"].shift(1).rolling(2, min_periods=2).max()
    four["prev_low2"] = four["low"].shift(1).rolling(2, min_periods=2).min()
    four["recent_low6"] = four["low"].shift(1).rolling(6, min_periods=4).min()
    four["recent_high6"] = four["high"].shift(1).rolling(6, min_periods=4).max()
    four["recent_low20"] = four["low"].shift(1).rolling(20, min_periods=10).min()
    four["recent_high20"] = four["high"].shift(1).rolling(20, min_periods=10).max()
    rng = (four["high"] - four["low"]).clip(lower=1e-12)
    four["close_pos_long"] = (four["close"] - four["low"]) / rng
    four["close_pos_short"] = (four["high"] - four["close"]) / rng
    four["source_last_ts"] = last_ts
    return four


def _make_early_stock_candidate(
    symbol: str,
    frame: pd.DataFrame,
    four: pd.DataFrame,
    fpos: int,
    direction: str,
    setup_type: str,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
) -> dict[str, Any] | None:
    fr = four.iloc[fpos]
    atr = _safe_float(fr.get("atr14"))
    signal_ts = pd.Timestamp(fr.get("source_last_ts"))
    if not math.isfinite(atr) or atr <= 0 or pd.isna(signal_ts):
        return None
    if signal_ts < research_start:
        return None
    signal_i = int(frame.index.searchsorted(signal_ts, side="right") - 1)
    entry_i = int(frame.index.searchsorted(signal_ts, side="right"))
    if signal_i < 0 or entry_i <= signal_i or entry_i >= len(frame):
        return None

    entry = _safe_float(frame["open"].iat[entry_i])
    if not math.isfinite(entry) or entry <= 0:
        return None

    if direction == "LONG":
        base = min(_safe_float(fr.get("recent_low6")), _safe_float(fr.get("low")))
        stop = base - V57_EARLY_STOP_BUFFER_ATR * atr
        stop_distance = entry - stop
        chase = (entry - _safe_float(fr.get("recent_low20"))) / atr
        trigger_line = _safe_float(fr.get("ema10"))
    else:
        base = max(_safe_float(fr.get("recent_high6")), _safe_float(fr.get("high")))
        stop = base + V57_EARLY_STOP_BUFFER_ATR * atr
        stop_distance = stop - entry
        chase = (_safe_float(fr.get("recent_high20")) - entry) / atr
        trigger_line = _safe_float(fr.get("ema10"))

    if not all(math.isfinite(x) for x in (base, stop, stop_distance, chase, trigger_line)) or stop_distance <= 0:
        return None
    stop_pct = stop_distance / entry * 100.0
    if stop_pct < V57_EARLY_MIN_STOP_PCT or stop_pct > V57_EARLY_MAX_STOP_PCT:
        return None
    if chase > V57_CHASE_LIMIT_ATR:
        _EARLY_STOCK_FUNNEL["anti_chase_rejected"] = _EARLY_STOCK_FUNNEL.get("anti_chase_rejected", 0) + 1
        return None

    targets, target_mode = v56._v56_stock_targets(
        frame, signal_i, direction, entry, stop_distance, pivot_highs, pivot_lows
    )
    if targets is None or float(targets[3]) < V57_EARLY_MIN_ROOM_R - 1e-9:
        _EARLY_STOCK_FUNNEL["no_4r_room"] = _EARLY_STOCK_FUNNEL.get("no_4r_room", 0) + 1
        return None

    result = v56._v56_sim_stock_trade(
        frame, entry_i, direction, entry, stop, targets, target_mode
    )
    exit_i = int(result["exit_i"])
    highs = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    lows = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    if direction == "LONG":
        mfe_r = (np.nanmax(highs[entry_i:exit_i + 1]) - entry) / stop_distance
        mae_r = (entry - np.nanmin(lows[entry_i:exit_i + 1])) / stop_distance
    else:
        mfe_r = (entry - np.nanmin(lows[entry_i:exit_i + 1])) / stop_distance
        mae_r = (np.nanmax(highs[entry_i:exit_i + 1]) - entry) / stop_distance

    rr1 = float(result["rr1"])
    quality = _entry_quality_score(float(chase), float(stop_pct), rr1, setup_type)
    row = {
        "exchange": "AlpacaUnderlying",
        "symbol": symbol, "base": symbol, "underlying": symbol,
        "direction": direction,
        "signal_time": signal_ts.isoformat(),
        "entry_time": frame.index[entry_i].isoformat(),
        "exit_time": frame.index[exit_i].isoformat(),
        "period": v56._period_name(signal_ts, research_start),
        "scheme": "V57_US_STOCK_4H_EARLY_TIMING",
        "setup_type": setup_type,
        "breakout_line": trigger_line,
        "entry": entry, "stop": stop, "structure_stop_base": base,
        "stop_pct": stop_pct,
        "entry_extension_atr": (entry - trigger_line) / atr if direction == "LONG" else (trigger_line - entry) / atr,
        "chase_distance_4h_atr": float(chase),
        "late_entry_flag": "CHASE" if chase > V57_CHASE_LIMIT_ATR else "OK",
        "timing_quality_score": quality,
        "tp1": float(result["tp1"]), "tp2": float(result["tp2"]), "tp3": float(result["tp3"]),
        "rr1": rr1, "rr2": float(result["rr2"]), "rr3": float(result["rr3"]),
        "exit_price": float(result["exit_price"]),
        "outcome": result["outcome"], "exit_reason": result["exit_reason"],
        "timed_out": result["timed_out"],
        "gross_r": float(result["gross_r"]), "cost_r": float(result["cost_r"]), "net_r": float(result["net_r"]),
        "mfe_r": float(mfe_r), "mae_r": float(mae_r), "target_mode": target_mode,
        "structure_timeframe": "4H_COMPLETED", "execution_timeframe": "15m",
        "data_source": f"Alpaca:{v22.v19.ALPACA_FEED}", "research_proxy": "UNDERLYING_HISTORY",
    }
    return row


def _early_stock_candidates_for_symbol(
    symbol: str,
    raw: pd.DataFrame,
    research_start: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    frame, pivot_highs, pivot_lows = v22.v19.prepare_stock_indicators(raw)
    if frame.empty:
        return [], {"four_hour_bars": 0, "published": 0}
    four = _four_hour_stock_frame(frame)
    rows: list[dict[str, Any]] = []
    funnel = {
        "four_hour_bars": len(four),
        "first_turn_long_raw": 0,
        "high_turn_short_raw": 0,
        "rebound_fail_short_raw": 0,
        "published": 0,
    }
    if len(four) < 30:
        return rows, funnel

    last_pub: dict[tuple[str, str], pd.Timestamp] = {}
    for i in range(20, len(four) - 1):
        r = four.iloc[i]
        p = four.iloc[i - 1]
        atr = _safe_float(r.get("atr14"))
        if not math.isfinite(atr) or atr <= 0:
            continue
        signal_ts = pd.Timestamp(r.get("source_last_ts"))
        if pd.isna(signal_ts) or signal_ts < research_start:
            continue

        o, h, l, c = map(_safe_float, (r.get("open"), r.get("high"), r.get("low"), r.get("close")))
        ema5, ema10, ema20 = map(_safe_float, (r.get("ema5"), r.get("ema10"), r.get("ema20")))
        pema5, pema10 = _safe_float(p.get("ema5")), _safe_float(p.get("ema10"))
        pc = _safe_float(p.get("close"))
        recent_low20, recent_high20 = _safe_float(r.get("recent_low20")), _safe_float(r.get("recent_high20"))
        recent_low6, recent_high6 = _safe_float(r.get("recent_low6")), _safe_float(r.get("recent_high6"))
        prev_high2, prev_low2 = _safe_float(r.get("prev_high2")), _safe_float(r.get("prev_low2"))
        cpl, cps = _safe_float(r.get("close_pos_long")), _safe_float(r.get("close_pos_short"))
        ema5_slope, ema20_slope = _safe_float(r.get("ema5_slope")), _safe_float(r.get("ema20_slope"))
        vals = (o, h, l, c, ema5, ema10, ema20, pema5, pema10, pc,
                recent_low20, recent_high20, recent_low6, recent_high6,
                prev_high2, prev_low2, cpl, cps, ema5_slope, ema20_slope)
        if not all(math.isfinite(x) for x in vals):
            continue

        setups: list[tuple[str, str]] = []

        # Fresh bottom + first short-term turn.  It does not wait for a full daily breakout.
        bottom_fresh = recent_low6 <= recent_low20 + 0.35 * atr
        fresh_up = (pc <= pema10 and c > ema10) or (pema5 <= pema10 and ema5 > ema10)
        long_bar = c > o and cpl >= 0.60 and ema5_slope > 0 and c > prev_high2
        if bottom_fresh and fresh_up and long_bar:
            setups.append(("LONG", "EARLY_FIRST_TURN_LONG"))
            funnel["first_turn_long_raw"] += 1

        # Fresh high + first bearish turn.  Designed for top reversals before a late breakdown.
        top_fresh = recent_high6 >= recent_high20 - 0.35 * atr
        fresh_down = (pc >= pema10 and c < ema10) or (pema5 >= pema10 and ema5 < ema10)
        short_bar = c < o and cps >= 0.60 and ema5_slope < 0 and c < prev_low2
        if top_fresh and fresh_down and short_bar:
            setups.append(("SHORT", "EARLY_HIGH_TURN_SHORT"))
            funnel["high_turn_short_raw"] += 1

        # Trend already damaged, then a rebound reaches EMA resistance and fails.
        damaged = (ema5 < ema10 and c < ema20) or (ema20_slope <= 0 and c < ema10)
        prev_rebound = _safe_float(p.get("high")) >= min(pema10, _safe_float(p.get("ema20"))) - 0.25 * atr
        rejection = c < o and cps >= 0.60 and c < ema5 and c < pc
        if damaged and prev_rebound and rejection:
            setups.append(("SHORT", "EARLY_REBOUND_FAIL_SHORT"))
            funnel["rebound_fail_short_raw"] += 1

        # Prefer the more specific high-turn short if both short setups fire on the same bar.
        dedup: list[tuple[str, str]] = []
        seen_direction: set[str] = set()
        for direction, setup in setups:
            if direction in seen_direction:
                continue
            seen_direction.add(direction)
            dedup.append((direction, setup))

        for direction, setup in dedup:
            key = (direction, setup)
            previous = last_pub.get(key)
            if previous is not None and signal_ts - previous < pd.Timedelta(hours=V57_EARLY_REARM_HOURS):
                continue
            candidate = _make_early_stock_candidate(
                symbol, frame, four, i, direction, setup, research_start, pivot_highs, pivot_lows
            )
            if candidate is None:
                continue
            rows.append(candidate)
            last_pub[key] = signal_ts
            funnel["published"] += 1

    return rows, funnel


def _annotate_baseline_stock_timing(
    baseline_rows: list[dict[str, Any]],
    raw: pd.DataFrame,
) -> None:
    if not baseline_rows:
        return
    frame, _, _ = v22.v19.prepare_stock_indicators(raw)
    ctx = _completed_4h_context(frame)
    if ctx.empty:
        return
    for row in baseline_rows:
        try:
            signal_ts = pd.Timestamp(row["signal_time"])
            pos = int(frame.index.searchsorted(signal_ts, side="right") - 1)
            if pos < 0 or pos >= len(ctx):
                continue
            entry = _safe_float(row.get("entry"))
            direction = str(row.get("direction") or "")
            chase, flag = _chase_from_context(direction, entry, ctx.iloc[pos])
            row["chase_distance_4h_atr"] = chase
            row["late_entry_flag"] = flag
            row["timing_quality_score"] = _entry_quality_score(
                chase,
                _safe_float(row.get("stop_pct")),
                _safe_float(row.get("rr1")),
                str(row.get("setup_type") or ""),
            )
        except Exception:
            continue


_ORIGINAL_STOCK_GENERATOR = v56._v56_stock_candidates_for_symbol


def stock_candidates_for_symbol_v57(
    symbol: str,
    raw: pd.DataFrame,
    research_start: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    baseline, funnel = _ORIGINAL_STOCK_GENERATOR(symbol, raw, research_start)
    _annotate_baseline_stock_timing(baseline, raw)
    early, ef = _early_stock_candidates_for_symbol(symbol, raw, research_start)
    _EARLY_STOCK_ROWS.extend(early)
    for k, val in ef.items():
        _EARLY_STOCK_FUNNEL[k] = _EARLY_STOCK_FUNNEL.get(k, 0) + int(val)
    funnel = dict(funnel)
    funnel["v57_early_published"] = len(early)
    return baseline, funnel


v56._v56_stock_candidates_for_symbol = stock_candidates_for_symbol_v57


# Capture the no-look-ahead SPY/QQQ/DIA state built by the inherited stock branch, so the same
# regime labels can be attached to the new early-timing rows without another download.
_ORIGINAL_INDEX_BUILDER = v56._v56_build_three_index_regime


def _capture_index_regime(fetch_start: pd.Timestamp, now: pd.Timestamp):
    global _INDEX_REGIME_CAPTURE
    regime, quality = _ORIGINAL_INDEX_BUILDER(fetch_start, now)
    _INDEX_REGIME_CAPTURE = regime.copy()
    return regime, quality


v56._v56_build_three_index_regime = _capture_index_regime


# --------------------------------------------------------------------------------------
# Portfolio helpers: same candidate rules, compare MAX5 vs unlimited capacity.
# --------------------------------------------------------------------------------------
def apply_stock_capacity_v57(
    candidates: list[dict[str, Any]],
    unlimited_capacity: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered = sorted(candidates, key=lambda r: (pd.Timestamp(r["entry_time"]), str(r.get("symbol") or "")))
    accepted: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    symbol_last_entry: dict[str, pd.Timestamp] = {}
    new_count_by_time: dict[pd.Timestamp, int] = {}
    equity = float(v22.v19.STOCK_STARTING_EQUITY)
    equity_events: list[tuple[pd.Timestamp, float]] = [(pd.Timestamp.min.tz_localize("UTC"), equity)]

    rejected_capacity = rejected_cooldown = rejected_same_time = rejected_same_group = 0

    def realize_until(ts: pd.Timestamp) -> None:
        nonlocal equity, open_positions
        closing = [p for p in open_positions if pd.Timestamp(p["exit_time"]) <= ts]
        for p in sorted(closing, key=lambda x: pd.Timestamp(x["exit_time"])):
            equity += _safe_float(p.get("profit_usdt"), 0.0)
            equity_events.append((pd.Timestamp(p["exit_time"]), equity))
        ids = {id(p) for p in closing}
        open_positions = [p for p in open_positions if id(p) not in ids]

    for source in ordered:
        entry_time = pd.Timestamp(source["entry_time"])
        if entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize("UTC")
        realize_until(entry_time)

        if (not unlimited_capacity) and len(open_positions) >= int(v22.v19.STOCK_MAX_POSITIONS):
            rejected_capacity += 1
            continue
        if new_count_by_time.get(entry_time, 0) >= int(v22.v19.STOCK_MAX_NEW_TRADES_PER_TIMESTAMP):
            rejected_same_time += 1
            continue

        symbol = str(source.get("symbol") or source.get("underlying") or "").upper()
        previous = symbol_last_entry.get(symbol)
        if previous is not None and entry_time - previous < pd.Timedelta(hours=v22.v19.STOCK_SYMBOL_COOLDOWN_HOURS):
            rejected_cooldown += 1
            continue

        group_name = v56.v56_asset_group(symbol, force_stock=True)
        active_groups = {str(p.get("asset_group") or "") for p in open_positions}
        if group_name in active_groups:
            rejected_same_group += 1
            continue

        risk_usdt = max(0.0, equity) * float(v22.v19.STOCK_RISK_PER_TRADE_PCT) / 100.0
        trade = dict(source)
        trade["asset_group"] = group_name
        trade["equity_at_entry"] = round(equity, 6)
        trade["risk_usdt"] = round(risk_usdt, 6)
        if str(trade.get("outcome") or "") == "OPEN":
            trade["profit_usdt"] = 0.0
            trade["unrealized_profit_usdt"] = round(_safe_float(trade.get("net_r"), 0.0) * risk_usdt, 6)
        else:
            trade["profit_usdt"] = round(_safe_float(trade.get("net_r"), 0.0) * risk_usdt, 6)
            trade["unrealized_profit_usdt"] = 0.0
        accepted.append(trade)
        open_positions.append(trade)
        symbol_last_entry[symbol] = entry_time
        new_count_by_time[entry_time] = new_count_by_time.get(entry_time, 0) + 1

    realize_until(pd.Timestamp.max.tz_localize("UTC"))
    peak = float(v22.v19.STOCK_STARTING_EQUITY)
    max_dd = 0.0
    for _, value in sorted(equity_events, key=lambda x: x[0]):
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100.0)

    m = _metrics(accepted)
    summary = {
        "mode": "UNLIMITED_CAPACITY" if unlimited_capacity else "MAX5",
        **m,
        "starting_equity": float(v22.v19.STOCK_STARTING_EQUITY),
        "ending_equity": round(equity, 4),
        "net_profit_usdt": round(equity - float(v22.v19.STOCK_STARTING_EQUITY), 4),
        "max_drawdown_pct": round(max_dd, 3),
        "rejected_capacity": rejected_capacity,
        "rejected_symbol_cooldown": rejected_cooldown,
        "rejected_same_timestamp": rejected_same_time,
        "rejected_same_group": rejected_same_group,
        "note": "Unlimited removes only total position capacity; same-symbol cooldown, same-timestamp throttle and same-group overlap remain.",
    }
    return accepted, summary


def _merge_prefer_earliest_episode(
    baseline: list[dict[str, Any]],
    early: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combined = [dict(r, timing_source="BASELINE") for r in baseline] + [dict(r, timing_source="EARLY_4H") for r in early]
    combined.sort(key=lambda r: (pd.Timestamp(r["entry_time"]), str(r.get("symbol") or ""), str(r.get("direction") or "")))
    kept: list[dict[str, Any]] = []
    last: dict[tuple[str, str], pd.Timestamp] = {}
    for row in combined:
        key = (str(row.get("symbol") or "").upper(), str(row.get("direction") or ""))
        ts = pd.Timestamp(row["entry_time"])
        prev = last.get(key)
        if prev is not None and ts - prev < pd.Timedelta(hours=V57_EPISODE_DEDUPE_HOURS):
            continue
        kept.append(row)
        last[key] = ts
    return kept


def _exchange_unlimited_capacity() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    if not db.exists():
        return [], {"error": "missing candidates.sqlite3"}
    attrs = [
        "MAX_POSITIONS", "MAX_SAME_DIRECTION_POSITIONS", "MAX_SAME_DIRECTION_ENTRIES_6H"
    ]
    old = {name: getattr(engine, name, None) for name in attrs}
    con = sqlite3.connect(db)
    try:
        if hasattr(engine, "MAX_POSITIONS"):
            engine.MAX_POSITIONS = 100000
        if hasattr(engine, "MAX_SAME_DIRECTION_POSITIONS"):
            engine.MAX_SAME_DIRECTION_POSITIONS = 100000
        if hasattr(engine, "MAX_SAME_DIRECTION_ENTRIES_6H"):
            engine.MAX_SAME_DIRECTION_ENTRIES_6H = 100000
        rows = v56.V56_ORIGINAL_SELECT_PORTFOLIO(con, SCHEME_V57, None)
    finally:
        con.close()
        for name, value in old.items():
            if value is not None:
                setattr(engine, name, value)

    m = _metrics(rows)
    m["max_drawdown_pct"] = _drawdown_from_selected(rows, float(engine.STARTING_EQUITY))
    m["mode"] = "UNLIMITED_CAPACITY"
    m["note"] = "Removes total/same-direction capacity limits; inherited symbol cooldown / timestamp throttles remain. This is a research comparator, not the formal portfolio."
    return rows, m


def _attach_exchange_timing_to_formal() -> dict[str, Any]:
    path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V57.lower()}.csv"
    rows = v22._read_csv_rows(path) if path.exists() else []
    lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    lookup_loose: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in _EXCHANGE_TIMING_AUDIT:
        key = (str(a.get("exchange")), str(a.get("symbol")), str(a.get("direction")), str(a.get("entry_time")))
        lookup[key] = a
        lookup_loose[(str(a.get("symbol")), str(a.get("direction")), str(a.get("entry_time")))] = a

    enriched: list[dict[str, Any]] = []
    for r in rows:
        out = dict(r)
        key = (str(r.get("exchange")), str(r.get("symbol")), str(r.get("direction")), str(r.get("entry_time")))
        a = lookup.get(key) or lookup_loose.get((str(r.get("symbol")), str(r.get("direction")), str(r.get("entry_time"))))
        if a:
            for field in ("chase_distance_4h_atr", "late_entry_flag", "timing_quality_score"):
                out[field] = a.get(field)
        enriched.append(out)
    non_chase = [r for r in enriched if str(r.get("late_entry_flag") or "UNKNOWN") != "CHASE"]
    _write_union(MARKETS_OUTPUT_DIR / "formal_trades_timing_audit_v57.csv", enriched)
    _write_union(MARKETS_OUTPUT_DIR / "formal_non_chase_same_selected_subset_v57.csv", non_chase)
    return {
        "formal": _metrics(enriched),
        "same_selected_non_chase_subset": _metrics(non_chase),
        "flagged_chase": sum(str(r.get("late_entry_flag")) == "CHASE" for r in enriched),
        "caveat": "Non-chase subset is observational on the same selected entries; removing trades would change later slot availability, so it is not presented as a replacement portfolio result.",
    }


def _run_stock_timing_poststudy(stock_summary: dict[str, Any]) -> dict[str, Any]:
    # Attach the already-computed no-look-ahead three-index state to early rows.
    for row in _EARLY_STOCK_ROWS:
        row["asset_group"] = v56.v56_asset_group(str(row.get("symbol") or ""), force_stock=True)
        if not _INDEX_REGIME_CAPTURE.empty:
            v56._v56_attach_index_regime(row, _INDEX_REGIME_CAPTURE)

    baseline_path = ALPACA_D1_OUTPUT_DIR / "candidates_all.csv"
    baseline = v22._read_csv_rows(baseline_path) if baseline_path.exists() else []
    # CSV rows are strings; timing fields were persisted by our wrapper.
    anti_chase = [r for r in baseline if str(r.get("late_entry_flag") or "UNKNOWN") != "CHASE"]
    quality_baseline = [r for r in baseline if _safe_float(r.get("timing_quality_score"), 0.0) >= V57_EARLY_QUALITY_THRESHOLD]
    early_all = list(_EARLY_STOCK_ROWS)
    early_quality = [r for r in early_all if _safe_float(r.get("timing_quality_score"), 0.0) >= V57_EARLY_QUALITY_THRESHOLD]
    merged = _merge_prefer_earliest_episode(baseline, early_all)

    variants = {
        "BASELINE": baseline,
        "BASELINE_ANTI_CHASE": anti_chase,
        "BASELINE_TIMING_SCORE70": quality_baseline,
        "EARLY_4H_ALL": early_all,
        "EARLY_4H_SCORE70": early_quality,
        "EARLY_AUGMENTED_PREFER_FIRST_72H": merged,
    }
    report: dict[str, Any] = {}
    for name, candidates in variants.items():
        max5_trades, max5 = apply_stock_capacity_v57(candidates, unlimited_capacity=False)
        unlimited_trades, unlimited = apply_stock_capacity_v57(candidates, unlimited_capacity=True)
        report[name] = {
            "candidate_count": len(candidates),
            "max5": max5,
            "unlimited_capacity": unlimited,
        }
        _write_union(TIMING_OUTPUT_DIR / f"stocks_{name.lower()}_max5.csv", max5_trades)
        _write_union(TIMING_OUTPUT_DIR / f"stocks_{name.lower()}_unlimited.csv", unlimited_trades)

    _write_union(TIMING_OUTPUT_DIR / "stocks_early_4h_candidates_v57.csv", early_all)
    _write_union(TIMING_OUTPUT_DIR / "stocks_baseline_candidates_timing_audit_v57.csv", baseline)
    _write_union(TIMING_OUTPUT_DIR / "stocks_early_funnel_v57.csv", [
        {"stage": k, "count": v} for k, v in sorted(_EARLY_STOCK_FUNNEL.items())
    ])
    return {
        "rules": {
            "chase_limit_4h_atr": V57_CHASE_LIMIT_ATR,
            "early_quality_threshold": V57_EARLY_QUALITY_THRESHOLD,
            "episode_dedupe_hours": V57_EPISODE_DEDUPE_HOURS,
            "no_ticker_exceptions": True,
            "lookahead": False,
        },
        "early_funnel": dict(_EARLY_STOCK_FUNNEL),
        "variants": report,
    }


def main() -> int:
    print("CryptoRadar V57 啟動")
    print("A. V56 正式策略保留；Runner 強制只守原始結構停損")
    print("B. 新增 MAX5 vs 容量不限 A/B（正式組合不被偷偷改掉）")
    print("C. 新增 4H 進場位置研究：早期轉強多 / 高檔轉弱空 / 反彈不過空")
    print("D. 新增追高追空距離與 0-100 進場品質分數；所有規則無個股例外")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TIMING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 96)
    print("[V57-A] 交易所正式 MAX5 基準")
    exchange = v56.run_exchange_branch_v56()
    timing_exchange = _attach_exchange_timing_to_formal()
    unlimited_exchange_rows, unlimited_exchange = _exchange_unlimited_capacity()
    _write_union(MARKETS_OUTPUT_DIR / "trades_unlimited_capacity_v57.csv", unlimited_exchange_rows)
    _write_union(MARKETS_OUTPUT_DIR / "candidate_timing_audit_all_v57.csv", _EXCHANGE_TIMING_AUDIT)

    print(
        f"[V57-EX] MAX5：{exchange.get('combination_metrics', {}).get('trades', 0)}筆 / "
        f"勝率 {exchange.get('combination_metrics', {}).get('win_rate_pct', 0.0):.2f}% / "
        f"{exchange.get('combination_metrics', {}).get('net_profit_usdt', 0.0):+.2f}U"
    )
    print(
        f"[V57-EX] 容量不限：{unlimited_exchange.get('trades', 0)}筆 / "
        f"勝率 {unlimited_exchange.get('win_rate_pct', 0.0):.2f}% / "
        f"{unlimited_exchange.get('net_profit_usdt', 0.0):+.2f}U / "
        f"DD {unlimited_exchange.get('max_drawdown_pct', 0.0):.2f}%"
    )

    print("\n" + "=" * 96)
    print("[V57-B] Alpaca 原生美股基準 + 4H 提早進場研究")
    stock = v56.run_alpaca_underlying_d1_v56()
    stock_timing = _run_stock_timing_poststudy(stock)

    for name in ("BASELINE", "BASELINE_ANTI_CHASE", "EARLY_4H_ALL", "EARLY_4H_SCORE70", "EARLY_AUGMENTED_PREFER_FIRST_72H"):
        r = stock_timing["variants"].get(name, {})
        a = r.get("max5", {})
        b = r.get("unlimited_capacity", {})
        print(
            f"[V57-US] {name}｜MAX5 {a.get('trades', 0)}筆 / {a.get('win_rate_pct', 0.0):.2f}% / "
            f"{a.get('net_profit_usdt', 0.0):+.2f}U｜不限 {b.get('trades', 0)}筆 / "
            f"{b.get('win_rate_pct', 0.0):.2f}% / {b.get('net_profit_usdt', 0.0):+.2f}U"
        )

    summary = {
        "version": "V57",
        "formal_baseline": {
            "exchange": exchange,
            "stocks": stock,
        },
        "exchange_capacity_research": {
            "max5_formal": exchange.get("combination_metrics", {}),
            "unlimited_capacity": unlimited_exchange,
            "timing_audit": timing_exchange,
        },
        "stock_entry_timing_research": stock_timing,
        "important_caveat": (
            "V57 keeps the V56 formal baseline and reports new timing/capacity variants side-by-side. "
            "The 3.25 ATR chase line and score>=70 are research hypotheses, not live-trading rules."
        ),
    }
    (OUTPUT_DIR / "summary_v57.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n[V57] 完成")
    print(f"結果：{OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
