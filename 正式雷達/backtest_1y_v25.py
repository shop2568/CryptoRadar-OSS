#!/usr/bin/env python3
"""CryptoRadar V25 adaptive trend-runner backtest.

V25 keeps V24's market-heat model and all entry/stop/volume rules, but fixes the
main V24 failure discovered in the result audit: strong-trend setups such as MU
and SKY could pass heat, setup, second confirmation, stop, volume and score, yet
still be discarded because no pre-entry >=4R structure existed and the old
90-day price-discovery fallback was too narrow.

Target policy in V25:
1) Prefer a real pre-entry structure target >=4R exactly as before.
2) If no such structure exists, allow TREND_RUNNER only when the signal is near
   a prior directional extreme on a shorter rolling horizon:
      - 20-day extreme within 1.5%, OR
      - 45-day extreme within 0.8%, OR
      - 90-day extreme within 0.3% (V24 compatibility).
   This is evaluated using bars strictly before the signal bar, so there is no
   look-ahead.
3) The runner has no fixed TP. Profit protection remains +2R->BE, +3R->+1R,
   +5R->+3R, with a new +4R->+2R step and a ratcheting trail above +6R that
   locks peak-R minus 2R. There is still no time exit.

V25 also writes trend_runner_target_audit_v25.csv for every setup that reaches
the target-selection stage but has no real >=4R structure. This makes MU, SKY,
and any other market auditable without hard-coding or special-casing them.
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
V24_PATH = ROOT / "backtest_1y_v24.py"
if not V24_PATH.exists():
    V24_PATH = Path(__file__).with_name("backtest_1y_v24.py")
if not V24_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v24.py；請把V24與V25放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v24_components_v25", V24_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V24：{V24_PATH}")
v24 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v24
spec.loader.exec_module(v24)

v23 = v24.v23
v22 = v24.v22
v17 = v24.v17
engine = v24.engine

OUTPUT_DIR = ROOT / "backtest_1y_v25_results"
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
    (20, 0.015),  # early trend expansion / fresh monthly breakout
    (45, 0.008),  # intermediate continuation
    (90, 0.003),  # old V24 price-discovery rule
)
RUNNER_SENTINEL_R = v24.RUNNER_SENTINEL_R
RUNNER_SENTINEL_THRESHOLD_R = v24.RUNNER_SENTINEL_THRESHOLD_R

SCHEME_V25 = "V25_HEAT_ADAPTIVE_TREND_RUNNER"
V25_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}
v22.SCHEME_V22 = SCHEME_V25
v22.V22_SCHEME_CONFIG = dict(V25_SCHEME_CONFIG)

_V25_PER_MARKET_FUNNEL: list[dict[str, Any]] = []
_V25_TARGET_AUDIT: list[dict[str, Any]] = []
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


def adaptive_structure_or_runner_targets(
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
    matched = next((r for r in stats if r["eligible"]), None)
    row = frame.iloc[signal_i]
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
        "runner_eligible": bool(matched is not None),
        "matched_horizon_days": int(matched["days"]) if matched else "",
    }
    for r in stats:
        d = int(r["days"])
        audit_row[f"extreme_{d}d"] = r["extreme"]
        audit_row[f"distance_{d}d_pct"] = r["distance_pct"] * 100 if math.isfinite(r["distance_pct"]) else float("nan")
        audit_row[f"eligible_{d}d"] = r["eligible"]
    _V25_TARGET_AUDIT.append(audit_row)

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


def candidate_rows_v25(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    global _CURRENT_TARGET
    v24._add_market_heat_columns(frame)
    heat_ok = frame["market_heat_ok"].astype(bool)
    heat_eval = int(frame["market_heat_ratio"].notna().sum())
    heat_ratio = int(frame["market_heat_ratio"].ge(MARKET_HEAT_MIN_RATIO).fillna(False).sum())
    heat_final = int(heat_ok.sum())
    funnel["V25_heat_evaluable_bars"] = funnel.get("V25_heat_evaluable_bars", 0) + heat_eval
    funnel["V25_heat_ratio_ge_1_20_bars"] = funnel.get("V25_heat_ratio_ge_1_20_bars", 0) + heat_ratio
    funnel["V25_heat_and_500k_floor_bars"] = funnel.get("V25_heat_and_500k_floor_bars", 0) + heat_final

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
    _V25_PER_MARKET_FUNNEL.append(out)
    funnel["V25_source_candidates"] = funnel.get("V25_source_candidates", 0) + len(candidates)
    funnel["V25_runner_candidates"] = funnel.get("V25_runner_candidates", 0) + runner_count
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


def run_exchange_branch_v25() -> dict[str, Any]:
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
    engine.SCHEME_CONFIGS = {SCHEME_V25: dict(V25_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V25,)
    engine.PRIMARY_SCHEME = SCHEME_V25
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_v25
    engine.confirmed_structure_targets = adaptive_structure_or_runner_targets
    engine.simulate_tp1_all = simulate_adaptive_runner

    print("\n[V25-MARKETS] 開始：幣圈＋HIGH_CONFIDENCE美股代幣｜LONG + SHORT")
    print("[V25-MARKETS] 市場熱度沿用V24：24H成交額>=50萬且>=自身20日中位數1.20x")
    print("[V25-MARKETS] 真實結構TP>=4R優先；無真實TP時，20/45/90日方向極值可啟動Adaptive Runner")
    print("[V25-MARKETS] Runner保護：+2R BE、+3R鎖1R、+4R鎖2R、+5R鎖3R、+6R後鎖peakR-2R")

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
    _write_union_csv(MARKETS_OUTPUT_DIR / "per_market_funnel_v25.csv", _V25_PER_MARKET_FUNNEL)
    _write_union_csv(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v25.csv", _V25_TARGET_AUDIT)

    summary_path = MARKETS_OUTPUT_DIR / "summary.json"
    native_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    result = {
        "return_code": rc,
        "engine_summary": native_summary,
        "segments": segments,
        "market_heat": {
            "rolling_24h_safety_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "baseline": f"previous {MARKET_HEAT_DAYS} completed UTC daily quote-turnover median",
            "minimum_relative_ratio": MARKET_HEAT_MIN_RATIO,
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


def audit_v25(exchange_result: dict[str, Any]) -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V25.lower()}.csv"
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
        "version": "V25",
        "fixed_5m_gate_removed": True,
        "top_n_gate_removed": True,
        "market_heat_rule": {
            "rolling_24h_quote_turnover_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "comparison": f">={MARKET_HEAT_MIN_RATIO:.2f}x own previous-{MARKET_HEAT_DAYS}-completed-day median",
            "current_day_excluded_from_baseline": True,
            "historical_bar_specific": True,
        },
        "signal_relative_volume": "previous-20-bar median; breakout>=1.30x; retest 0.60-1.20x; confirm>=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R first; otherwise adaptive 20/45/90-day no-look-ahead trend runner",
        "runner_target_audit_csv": str(MARKETS_OUTPUT_DIR / "trend_runner_target_audit_v25.csv"),
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required_segments.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v25_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V25 啟動｜Adaptive Trend Runner＋相對市場熱度")
    print("市場：Binance / Bitget / BingX；幣圈與HIGH_CONFIDENCE美股代幣；LONG / SHORT 都開。")
    print("不指定SKY/MU，不做單幣特判；它們只會在結果分析時當強趨勢驗收樣本。")
    print("市場熱度：24H成交額>=50萬 USDT，且>=自己前20個完整日中位數1.20x。")
    print("進場量能：突破/跌破>=1.30x；回踩/反抽0.60~1.20x；確認>=1.00x且比回踩+10%。")
    print("TP：真實結構>=4R優先；沒有時，20/45/90日方向極值可啟動無固定TP Runner。")
    print("Funding只記錄、不擋單；無時間出場。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch_v25()
    audit = audit_v25(exchange_result)
    combined = {
        "version": "V25",
        "exchange_markets": exchange_result,
        "us_stocks": {"skipped": True, "reason": "V25 focuses on exchange markets; Alpaca only validates stock-token underlyings"},
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v25.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V25 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"逐市場漏斗：{MARKETS_OUTPUT_DIR / 'per_market_funnel_v25.csv'}")
    print(f"Runner目標稽核：{MARKETS_OUTPUT_DIR / 'trend_runner_target_audit_v25.csv'}")
    print(f"總結：{OUTPUT_DIR / 'summary_v25.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v25_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
