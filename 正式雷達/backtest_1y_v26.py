#!/usr/bin/env python3
"""CryptoRadar V26 adaptive-heat selective trend-runner backtest.

V26 keeps the verified V25 entry/stop/relative-volume rules but fixes two issues found
in the V25 results:
- newly listed markets had to wait 20 completed days before the market-heat gate could
  even be evaluated, which can miss the first major trend after listing;
- the V25 20-day runner fallback was too permissive and expanded to many low-quality
  LONG trades.
main V24 failure discovered in the result audit: strong-trend setups such as MU
and SKY could pass heat, setup, second confirmation, stop, volume and score, yet
still be discarded because no pre-entry >=4R structure existed and the old
90-day price-discovery fallback was too narrow.

V26 policy:
1) Real pre-entry structure TP >=4R remains first priority.
2) Market heat becomes adaptive with no look-ahead:
   - >=20 completed days: current 24H turnover >=1.20x prior-20d median;
   - 7-19 completed days: >=1.35x prior-7d median;
   - 3-6 completed days: >=1.50x prior-3d median.
   The 500k USDT rolling-24H safety floor remains.
3) If no real >=4R structure exists, Trend Runner is allowed only when price is near a
   directional 20/45/90-day extreme AND the 15m EMA20-vs-EMA50 separation is at least
   0.75R in the trade direction AND market heat is >=1.50x. This avoids V25's broad
   runner flood while still allowing genuine price-discovery trends.
4) Runner exits remain +2R->BE, +3R->+1R, +4R->+2R, +5R->+3R, and above +6R trails
   peak-R minus 2R. No time exit and no fixed TP for runner positions.

V26 writes per_market_funnel_v26.csv and trend_runner_target_audit_v26.csv.
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
V25_PATH = ROOT / "backtest_1y_v25.py"
if not V25_PATH.exists():
    V25_PATH = Path(__file__).with_name("backtest_1y_v25.py")
if not V25_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v25.py；請把V25與V26放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v25_components_v26", V25_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V25：{V25_PATH}")
v25 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v25
spec.loader.exec_module(v25)

v24 = v25.v24
v23 = v25.v23
v22 = v25.v22
v17 = v25.v17
engine = v25.engine

OUTPUT_DIR = ROOT / "backtest_1y_v26_results"
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
NEW_LISTING_HEAT_TIERS = (
    (20, 20, 1.20),  # history_days >=20 -> previous 20 completed days
    (7, 7, 1.35),    # 7-19 days -> previous 7 completed days
    (3, 3, 1.50),    # 3-6 days -> previous 3 completed days
)
RUNNER_SENTINEL_R = v24.RUNNER_SENTINEL_R
RUNNER_SENTINEL_THRESHOLD_R = v24.RUNNER_SENTINEL_THRESHOLD_R

SCHEME_V26 = "V26_ADAPTIVE_HEAT_SELECTIVE_RUNNER"
V26_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}
v22.SCHEME_V22 = SCHEME_V26
v22.V22_SCHEME_CONFIG = dict(V26_SCHEME_CONFIG)

_V26_PER_MARKET_FUNNEL: list[dict[str, Any]] = []
_V26_TARGET_AUDIT: list[dict[str, Any]] = []
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
    matched = raw_match if strong_trend else None
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
        "extreme_match_before_quality_gate": bool(raw_match is not None),
        "runner_eligible": bool(matched is not None),
        "matched_horizon_days": int(matched["days"]) if matched else "",
    }
    for r in stats:
        d = int(r["days"])
        audit_row[f"extreme_{d}d"] = r["extreme"]
        audit_row[f"distance_{d}d_pct"] = r["distance_pct"] * 100 if math.isfinite(r["distance_pct"]) else float("nan")
        audit_row[f"eligible_{d}d"] = r["eligible"]
    _V26_TARGET_AUDIT.append(audit_row)

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
                1: "PROTECT_BE",
                2: "PROTECT_1R",
                3: "PROTECT_2R",
                4: "PROTECT_3R",
                5: "RUNNER_TRAIL",
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

        # Ratchet only in the favorable direction; never loosen a stop.
        desired_lock_r: float | None = None
        desired_stage = stage
        if peak_r >= 6.0:
            desired_lock_r = max(3.0, peak_r - 2.0)
            desired_stage = 5
        elif peak_r >= 5.0:
            desired_lock_r = 3.0
            desired_stage = 4
        elif peak_r >= 4.0:
            desired_lock_r = 2.0
            desired_stage = 3
        elif peak_r >= 3.0:
            desired_lock_r = 1.0
            desired_stage = 2
        elif peak_r >= 2.0:
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


def candidate_rows_v26(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    global _CURRENT_TARGET
    _add_adaptive_market_heat_columns(frame)
    heat_ok = frame["market_heat_ok"].astype(bool)
    heat_eval = int(frame["market_heat_ratio"].notna().sum())
    heat_ratio = int(frame["market_heat_ratio"].ge(frame["market_heat_required_ratio"]).fillna(False).sum())
    heat_final = int(heat_ok.sum())
    funnel["V26_heat_evaluable_bars"] = funnel.get("V26_heat_evaluable_bars", 0) + heat_eval
    funnel["V26_heat_ratio_ge_threshold_bars"] = funnel.get("V26_heat_ratio_ge_threshold_bars", 0) + heat_ratio
    funnel["V26_heat_and_500k_floor_bars"] = funnel.get("V26_heat_and_500k_floor_bars", 0) + heat_final

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
    }
    out.update({f"funnel__{k}": v for k, v in delta.items()})
    _V26_PER_MARKET_FUNNEL.append(out)
    funnel["V26_source_candidates"] = funnel.get("V26_source_candidates", 0) + len(candidates)
    funnel["V26_runner_candidates"] = funnel.get("V26_runner_candidates", 0) + runner_count
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


def run_exchange_branch_v26() -> dict[str, Any]:
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
    engine.SCHEME_CONFIGS = {SCHEME_V26: dict(V26_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V26,)
    engine.PRIMARY_SCHEME = SCHEME_V26
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_v26
    engine.confirmed_structure_targets = selective_structure_or_runner_targets
    engine.simulate_tp1_all = simulate_adaptive_runner

    print("\n[V26-MARKETS] 開始：幣圈＋HIGH_CONFIDENCE美股代幣｜LONG + SHORT")
    print("[V26-MARKETS] 市場熱度：24H>=50萬；>=20日資料用1.20x，7-19日用1.35x，3-6日用1.50x")
    print("[V26-MARKETS] 真實結構TP>=4R優先；Runner還需方向極值＋heat>=1.50x＋EMA20/50強度>=0.75R")
    print("[V26-MARKETS] Runner保護：+2R BE、+3R鎖1R、+4R鎖2R、+5R鎖3R、+6R後鎖peakR-2R")

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
    _write_union_csv(MARKETS_OUTPUT_DIR / "per_market_funnel_v26.csv", _V26_PER_MARKET_FUNNEL)
    _write_union_csv(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v26.csv", _V26_TARGET_AUDIT)

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
            "protection": "+2R->BE,+3R->+1R,+4R->+2R,+5R->+3R,+6R onward peakR-2R",
        },
    }
    (MARKETS_OUTPUT_DIR / "segments_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def audit_v26(exchange_result: dict[str, Any]) -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V26.lower()}.csv"
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
        "version": "V26",
        "fixed_5m_gate_removed": True,
        "top_n_gate_removed": True,
        "market_heat_rule": {
            "rolling_24h_quote_turnover_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "comparison": "adaptive: >=1.20x previous-20d median; new listings >=1.35x previous-7d or >=1.50x previous-3d median",
            "current_day_excluded_from_baseline": True,
            "historical_bar_specific": True,
        },
        "signal_relative_volume": "previous-20-bar median; breakout>=1.30x; retest 0.60-1.20x; confirm>=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R first; otherwise selective extreme+heat+EMA-strength trend runner",
        "runner_target_audit_csv": str(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v26.csv"),
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required_segments.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v26_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V26 啟動｜Adaptive Trend Runner＋相對市場熱度")
    print("市場：Binance / Bitget / BingX；幣圈與HIGH_CONFIDENCE美股代幣；LONG / SHORT 都開。")
    print("不指定SKY/MU，不做單幣特判；它們只會在結果分析時當強趨勢驗收樣本。")
    print("市場熱度：24H>=50萬；20日成熟市場>=1.20x，7-19日新市場>=1.35x，3-6日>=1.50x。")
    print("進場量能：突破/跌破>=1.30x；回踩/反抽0.60~1.20x；確認>=1.00x且比回踩+10%。")
    print("TP：真實結構>=4R優先；Runner必須再通過heat>=1.50x與EMA20/50方向強度>=0.75R。")
    print("Funding只記錄、不擋單；無時間出場。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch_v26()
    audit = audit_v26(exchange_result)
    combined = {
        "version": "V26",
        "exchange_markets": exchange_result,
        "us_stocks": {"skipped": True, "reason": "V26 focuses on exchange markets; Alpaca only validates stock-token underlyings"},
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v26.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V26 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"逐市場漏斗：{MARKETS_OUTPUT_DIR / 'per_market_funnel_v26.csv'}")
    print(f"Runner目標稽核：{MARKETS_OUTPUT_DIR / 'trend_runner_target_audit_v26.csv'}")
    print(f"總結：{OUTPUT_DIR / 'summary_v26.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v26_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
