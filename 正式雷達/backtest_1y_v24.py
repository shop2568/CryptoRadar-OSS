#!/usr/bin/env python3
"""CryptoRadar V24 relative-market-heat + price-discovery runner backtest.

V24 is based on V23. It keeps the relative-market-heat gate and adds a
price-discovery trend-runner fallback when no valid pre-entry >=4R structure exists.

Key change
----------
The old fixed current 24H quote-volume >=5M USDT prefilter is removed.
No top-N liquidity ranking is used. Every active Binance / Bitget / BingX USDT
linear swap discovered by V22 is allowed to download history.

At each historical 15m bar, the market must instead satisfy BOTH:
1) trailing 24H quote turnover >= 500,000 USDT (execution safety floor), and
2) trailing 24H quote turnover >= 1.20x the median of the previous 20 COMPLETED
   UTC daily quote-turnover totals for that same market.

The baseline is lagged by one completed day, so the current day's future volume
is never used. The signal-level 20-bar median-volume sequence is unchanged:
breakout/breakdown >=1.30x, retest 0.60-1.20x, confirmation >=1.00x and >=1.10x
retest volume.

Normal setups still use a real pre-entry structure TP >=4R. If that finder returns
no target AND price is genuinely in 90-day price discovery (near/through the prior
extreme), the setup is no longer discarded: it becomes RUNNER mode with no fixed
profit target. The +2R->BE, +3R->+1R, +5R->+3R protection ladder and no-time-exit
logic manage the runner. Funding remains analysis-only. A per-market funnel CSV is
also written so SKY/MU and any other symbol can be audited directly.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path.home() / "CryptoRadar"
V23_PATH = ROOT / "backtest_1y_v23.py"
if not V23_PATH.exists():
    V23_PATH = Path(__file__).with_name("backtest_1y_v23.py")
if not V23_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v23.py；請把V23與V24放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v23_components_v24", V23_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V23：{V23_PATH}")
v23 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v23
spec.loader.exec_module(v23)

v22 = v23.v22
engine = v23.engine
v17 = v23.v17

# ===== V24 outputs =====
OUTPUT_DIR = ROOT / "backtest_1y_v24_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"

# Re-point V22 helpers so all inherited writers land under V24.
v22.OUTPUT_DIR = OUTPUT_DIR
v22.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
v22.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
v22.V22_TOKEN_CATALOG_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_catalog.csv"
v22.V22_REJECTED_COLLISIONS_PATH = MARKETS_OUTPUT_DIR / "rejected_stock_ticker_collisions.csv"
v22.V22_DISCOVERY_SUMMARY_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_discovery_summary.json"
v22.V22_RUN_NATIVE_STOCKS = False

# ===== V24 historical market-heat + runner rules =====
MARKET_HEAT_DAYS = 20
MARKET_HEAT_MIN_RATIO = 1.20
MIN_ROLLING_24H_QUOTE_TURNOVER = 500_000.0
BARS_24H = 96  # 15m bars


# ===== V24 price-discovery fallback =====
PRICE_DISCOVERY_LOOKBACK_DAYS = 90
PRICE_DISCOVERY_TOLERANCE_PCT = 0.003
RUNNER_SENTINEL_R = 1_000_000.0
RUNNER_SENTINEL_THRESHOLD_R = 100_000.0
_V24_PER_MARKET_FUNNEL: list[dict[str, Any]] = []

def _is_price_discovery(frame: pd.DataFrame, signal_i: int, direction: str, entry: float) -> tuple[bool, float]:
    bars = PRICE_DISCOVERY_LOOKBACK_DAYS * 96
    start = max(0, signal_i - bars)
    hist = frame.iloc[start:signal_i]
    if hist.empty:
        return False, float("nan")
    if direction == "LONG":
        extreme = float(pd.to_numeric(hist["high"], errors="coerce").max())
        return bool(math.isfinite(extreme) and entry >= extreme * (1.0 - PRICE_DISCOVERY_TOLERANCE_PCT)), extreme
    extreme = float(pd.to_numeric(hist["low"], errors="coerce").min())
    return bool(math.isfinite(extreme) and extreme > 0 and entry <= extreme * (1.0 + PRICE_DISCOVERY_TOLERANCE_PCT)), extreme

def structure_or_price_discovery_targets(
    frame: pd.DataFrame, signal_i: int, direction: str, entry: float, stop_distance: float,
    cached_pivot_highs: Any, cached_pivot_lows: Any, minimum_room_r: float = 2.0,
):
    real = v17.v11.confirmed_zone_targets(
        frame, signal_i, direction, entry, stop_distance,
        cached_pivot_highs, cached_pivot_lows, minimum_room_r=minimum_room_r,
    )
    if real is not None:
        return real
    discovery, _ = _is_price_discovery(frame, signal_i, direction, entry)
    if not discovery or not math.isfinite(stop_distance) or stop_distance <= 0:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    synthetic = entry + sign * RUNNER_SENTINEL_R * stop_distance
    # The price itself is only an internal sentinel. The V24 simulator detects
    # the huge R distance and deliberately NEVER uses it as a take-profit.
    return (synthetic, synthetic, synthetic, RUNNER_SENTINEL_R, RUNNER_SENTINEL_R, RUNNER_SENTINEL_R, RUNNER_SENTINEL_R)

def simulate_with_price_discovery_runner(
    frame: pd.DataFrame, entry_i: int, direction: str, entry: float,
    stop: float, tp1: float, stop_distance: float,
) -> dict[str, Any]:
    runner = abs(tp1 - entry) / stop_distance >= RUNNER_SENTINEL_THRESHOLD_R
    if not runner:
        return v17.simulate_tp1_with_profit_protection(frame, entry_i, direction, entry, stop, tp1, stop_distance)
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
            result["target_mode"] = "PRICE_DISCOVERY_RUNNER"
            return result
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
    result = engine.finish_open_result(direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i]))
    result["target_mode"] = "PRICE_DISCOVERY_RUNNER"
    return result

SCHEME_V23 = "V24_HEAT_PRICE_DISCOVERY_RUNNER"
V24_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}

# Make inherited V22 output splitters look for the V23 scheme filename.
v22.SCHEME_V22 = SCHEME_V23
v22.V22_SCHEME_CONFIG = dict(V24_SCHEME_CONFIG)


def prefilter_no_fixed_5m(
    targets: list[Any],
    exchanges: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[Any]:
    """Do not use current 24H quote volume or top-N ranking.

    Historical liquidity/heat is evaluated at each signal bar instead, avoiding a
    fixed 5M gate and avoiding today's volume from deciding whether a market was
    tradable months ago.
    """
    return list(targets)


def _add_market_heat_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach no-look-ahead historical 24H turnover heat metrics."""
    if frame.empty:
        return frame

    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    quote_turnover = close * volume

    # Trailing 24H turnover ending at the CLOSED current 15m candle.
    rolling_24h = quote_turnover.rolling(BARS_24H, min_periods=BARS_24H).sum()

    # Completed UTC-day totals. shift(1) guarantees the current day's incomplete
    # total is never included in the 20-day median baseline.
    daily_turnover = quote_turnover.resample("1D").sum(min_count=1)
    lagged_daily_median20 = daily_turnover.shift(1).rolling(
        MARKET_HEAT_DAYS,
        min_periods=MARKET_HEAT_DAYS,
    ).median()

    # Map each bar to its day's baseline, which only uses days completed before it.
    day_index = frame.index.normalize()
    baseline = pd.Series(
        lagged_daily_median20.reindex(day_index).to_numpy(),
        index=frame.index,
        dtype="float64",
    )
    baseline = baseline.replace(0.0, float("nan"))

    ratio = rolling_24h / baseline
    heat_ok = (
        rolling_24h.ge(MIN_ROLLING_24H_QUOTE_TURNOVER)
        & ratio.ge(MARKET_HEAT_MIN_RATIO)
        & ratio.map(math.isfinite)
    )

    frame["rolling_24h_quote_turnover"] = rolling_24h
    frame["market_heat_median20d"] = baseline
    frame["market_heat_ratio"] = ratio
    frame["market_heat_ok"] = heat_ok.fillna(False)
    return frame


def candidate_rows_market_heat(
    target: Any, frame: pd.DataFrame, research_start: pd.Timestamp,
    pivot_highs: Any, pivot_lows: Any, funnel: dict[str, int],
):
    """V24 heat gate + per-market funnel diagnostics for LONG and SHORT."""
    _add_market_heat_columns(frame)
    heat_ok = frame["market_heat_ok"].astype(bool)
    heat_eval = int(frame["market_heat_ratio"].notna().sum())
    heat_ratio = int(frame["market_heat_ratio"].ge(MARKET_HEAT_MIN_RATIO).fillna(False).sum())
    heat_final = int(heat_ok.sum())
    funnel["V24_heat_evaluable_bars"] = funnel.get("V24_heat_evaluable_bars", 0) + heat_eval
    funnel["V24_heat_ratio_ge_1_20_bars"] = funnel.get("V24_heat_ratio_ge_1_20_bars", 0) + heat_ratio
    funnel["V24_heat_and_500k_floor_bars"] = funnel.get("V24_heat_and_500k_floor_bars", 0) + heat_final

    original_bull4h = frame["bull4h"].copy()
    original_bear4h = frame["bear4h"].copy()
    frame["bull4h"] = original_bull4h.astype(bool) & heat_ok
    frame["bear4h"] = original_bear4h.astype(bool) & heat_ok
    before = dict(funnel)
    try:
        candidates = list(v17._original_candidate_rows_v17(
            target, frame, research_start, pivot_highs, pivot_lows, funnel
        ))
    finally:
        frame["bull4h"] = original_bull4h
        frame["bear4h"] = original_bear4h

    delta = {k: int(v) - int(before.get(k, 0)) for k, v in funnel.items() if int(v) - int(before.get(k, 0))}
    runner_count = sum(float(c[14]) >= RUNNER_SENTINEL_THRESHOLD_R for c in candidates)
    long_count = sum(str(c[3]) == "LONG" for c in candidates)
    short_count = sum(str(c[3]) == "SHORT" for c in candidates)
    row = {
        "exchange": getattr(target, "exchange_name", ""),
        "symbol": getattr(target, "symbol", ""),
        "base": getattr(target, "base", ""),
        "heat_evaluable_bars": heat_eval,
        "heat_ratio_ge_1_20_bars": heat_ratio,
        "heat_and_500k_floor_bars": heat_final,
        "published_candidates": len(candidates),
        "long_candidates": long_count,
        "short_candidates": short_count,
        "price_discovery_runner_candidates": runner_count,
    }
    row.update({f"funnel__{k}": v for k, v in delta.items()})
    _V24_PER_MARKET_FUNNEL.append(row)
    funnel["V24_source_candidates"] = funnel.get("V24_source_candidates", 0) + len(candidates)
    funnel["V24_runner_candidates"] = funnel.get("V24_runner_candidates", 0) + runner_count
    for c in candidates:
        yield c


def run_exchange_branch_v24() -> dict[str, Any]:
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
    engine.SCHEME_CONFIGS = {SCHEME_V23: dict(V24_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V23,)
    engine.PRIMARY_SCHEME = SCHEME_V23
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_market_heat
    engine.confirmed_structure_targets = structure_or_price_discovery_targets
    engine.simulate_tp1_all = simulate_with_price_discovery_runner

    print("\n[V24-MARKETS] 開始：幣圈＋HIGH_CONFIDENCE美股代幣｜LONG + SHORT")
    print("[V24-MARKETS] 已移除固定24H 500萬與Top-N市場篩選")
    print(
        f"[V24-MARKETS] 歷史市場熱度：rolling24H成交額 >= {MIN_ROLLING_24H_QUOTE_TURNOVER:,.0f} USDT "
        f"且 >= 自己前{MARKET_HEAT_DAYS}個完整日中位數 {MARKET_HEAT_MIN_RATIO:.2f}x"
    )
    print("[V24-MARKETS] 進場量能仍為1.30x → 0.60~1.20x → >=1.00x且+10%")
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
    if _V24_PER_MARKET_FUNNEL:
        v22.write_csv_union(MARKETS_OUTPUT_DIR / "per_market_funnel_v24.csv", _V24_PER_MARKET_FUNNEL)
    else:
        (MARKETS_OUTPUT_DIR / "per_market_funnel_v24.csv").write_text("", encoding="utf-8")
    summary_path = MARKETS_OUTPUT_DIR / "summary.json"
    native_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    result = {
        "return_code": rc,
        "engine_summary": native_summary,
        "segments": segments,
        "market_heat": {
            "fixed_5m_gate": False,
            "top_n_gate": False,
            "rolling_24h_safety_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "baseline": f"previous {MARKET_HEAT_DAYS} completed UTC daily quote-turnover median",
            "minimum_relative_ratio": MARKET_HEAT_MIN_RATIO,
            "lookahead": False,
            "price_discovery_runner": True,
            "price_discovery_lookback_days": PRICE_DISCOVERY_LOOKBACK_DAYS,
            "price_discovery_tolerance_pct": PRICE_DISCOVERY_TOLERANCE_PCT,
            "runner_fixed_tp": False,
        },
    }
    (MARKETS_OUTPUT_DIR / "segments_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def audit_v24(exchange_result: dict[str, Any]) -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V23.lower()}.csv"
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
        "version": "V24",
        "fixed_5m_gate_removed": True,
        "top_n_gate_removed": True,
        "market_heat_rule": {
            "rolling_24h_quote_turnover_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "comparison": f">={MARKET_HEAT_MIN_RATIO:.2f}x own previous-{MARKET_HEAT_DAYS}-completed-day median",
            "current_day_excluded_from_baseline": True,
            "historical_bar_specific": True,
        },
        "signal_relative_volume": "previous-20-bar median; breakout>=1.30x; retest 0.60-1.20x; confirm>=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R; price-discovery fallback uses no-fixed-TP runner",
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required_segments.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v24_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V24 啟動｜相對市場熱度＋三交易所雙向回測")
    print("市場：Binance / Bitget / BingX；幣圈與HIGH_CONFIDENCE美股代幣；LONG / SHORT 都開。")
    print("已拿掉固定24H >=500萬 USDT 與Top-N流動性排名。")
    print(
        f"改成歷史動態門檻：最近24H成交額 >= {MIN_ROLLING_24H_QUOTE_TURNOVER:,.0f} USDT，"
        f"且 >= 自己前{MARKET_HEAT_DAYS}個完整日24H成交額中位數 {MARKET_HEAT_MIN_RATIO:.2f}x。"
    )
    print("訊號量能不變：突破/跌破>=1.30x；回踩/反抽0.60～1.20x；確認>=1.00x且比回踩+10%。")
    print("TP：優先使用進場前真實結構>=4R；若為90日價格發現且無有效結構，改用Runner無固定TP；+2R保本、+3R鎖1R、+5R鎖3R；無時間出場。")
    print("Funding只記錄、不擋單。原生Alpaca美股此版不跑。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch_v24()
    audit = audit_v24(exchange_result)
    combined = {
        "version": "V24",
        "exchange_markets": exchange_result,
        "us_stocks": {"skipped": True, "reason": "V24 focuses on exchange markets; Alpaca only validates stock-token underlyings"},
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v24.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V24 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"美股代幣清單：{v22.V22_TOKEN_CATALOG_PATH}")
    print(f"碰撞拒絕清單：{v22.V22_REJECTED_COLLISIONS_PATH}")
    print(f"總結：{OUTPUT_DIR / 'summary_v24.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v24_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
