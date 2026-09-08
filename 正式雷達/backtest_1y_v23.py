#!/usr/bin/env python3
"""CryptoRadar V23 relative-market-heat backtest.

V23 is based on V22 and changes only the market-liquidity/heat gate.

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

All V22 strict US-stock-token identification, LONG+SHORT logic, real pre-entry
structure TP >=4R, funding analysis-only, +2R/+3R/+5R protection and no-time-exit
rules remain unchanged.
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
V22_PATH = ROOT / "backtest_1y_v22.py"
if not V22_PATH.exists():
    V22_PATH = Path(__file__).with_name("backtest_1y_v22.py")
if not V22_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v22.py；請把V22與V23放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v22_components_v23", V22_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V22：{V22_PATH}")
v22 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v22
spec.loader.exec_module(v22)

engine = v22.engine
v17 = v22.v17

# ===== V23 outputs =====
OUTPUT_DIR = ROOT / "backtest_1y_v23_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"

# Re-point V22 helpers so all inherited writers land under V23.
v22.OUTPUT_DIR = OUTPUT_DIR
v22.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
v22.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
v22.V22_TOKEN_CATALOG_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_catalog.csv"
v22.V22_REJECTED_COLLISIONS_PATH = MARKETS_OUTPUT_DIR / "rejected_stock_ticker_collisions.csv"
v22.V22_DISCOVERY_SUMMARY_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_discovery_summary.json"
v22.V22_RUN_NATIVE_STOCKS = False

# ===== V23 historical market-heat rules =====
MARKET_HEAT_DAYS = 20
MARKET_HEAT_MIN_RATIO = 1.20
MIN_ROLLING_24H_QUOTE_TURNOVER = 500_000.0
BARS_24H = 96  # 15m bars

SCHEME_V23 = "V23_RELATIVE_MARKET_HEAT_BIDIRECTIONAL"
V23_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}

# Make inherited V22 output splitters look for the V23 scheme filename.
v22.SCHEME_V22 = SCHEME_V23
v22.V22_SCHEME_CONFIG = dict(V23_SCHEME_CONFIG)


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
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    """Apply the historical relative-market-heat gate before both LONG and SHORT."""
    _add_market_heat_columns(frame)
    heat_ok = frame["market_heat_ok"].astype(bool)

    funnel["V23_heat_evaluable_bars"] = funnel.get("V23_heat_evaluable_bars", 0) + int(
        frame["market_heat_ratio"].notna().sum()
    )
    funnel["V23_heat_ratio_ge_1_20_bars"] = funnel.get("V23_heat_ratio_ge_1_20_bars", 0) + int(
        frame["market_heat_ratio"].ge(MARKET_HEAT_MIN_RATIO).fillna(False).sum()
    )
    funnel["V23_heat_and_500k_floor_bars"] = funnel.get("V23_heat_and_500k_floor_bars", 0) + int(
        heat_ok.sum()
    )

    # Both directions must be in a historically hot market at the signal bar.
    original_bull4h = frame["bull4h"].copy()
    original_bear4h = frame["bear4h"].copy()
    frame["bull4h"] = original_bull4h.astype(bool) & heat_ok
    frame["bear4h"] = original_bear4h.astype(bool) & heat_ok

    total = long_count = short_count = 0
    try:
        for candidate in v17._original_candidate_rows_v17(
            target, frame, research_start, pivot_highs, pivot_lows, funnel
        ):
            total += 1
            direction = str(candidate[3])
            if direction == "LONG":
                long_count += 1
            elif direction == "SHORT":
                short_count += 1
            yield candidate
    finally:
        frame["bull4h"] = original_bull4h
        frame["bear4h"] = original_bear4h

    funnel["V23_source_candidates"] = funnel.get("V23_source_candidates", 0) + total
    funnel["V23_long_candidates"] = funnel.get("V23_long_candidates", 0) + long_count
    funnel["V23_short_candidates"] = funnel.get("V23_short_candidates", 0) + short_count


def run_exchange_branch_v23() -> dict[str, Any]:
    MARKETS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    old_configs = engine.SCHEME_CONFIGS
    old_schemes = engine.SCHEMES
    old_primary = engine.PRIMARY_SCHEME
    old_output = engine.OUTPUT_DIR
    old_database = engine.DATABASE_PATH
    old_candidates = engine.candidate_rows
    old_discover = engine.discover_unique_targets
    old_prefilter = engine.prefilter_liquid_targets

    engine.discover_unique_targets = v22.discover_three_exchange_targets
    engine.prefilter_liquid_targets = prefilter_no_fixed_5m
    engine.SCHEME_CONFIGS = {SCHEME_V23: dict(V23_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V23,)
    engine.PRIMARY_SCHEME = SCHEME_V23
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_market_heat

    print("\n[V23-MARKETS] 開始：幣圈＋HIGH_CONFIDENCE美股代幣｜LONG + SHORT")
    print("[V23-MARKETS] 已移除固定24H 500萬與Top-N市場篩選")
    print(
        f"[V23-MARKETS] 歷史市場熱度：rolling24H成交額 >= {MIN_ROLLING_24H_QUOTE_TURNOVER:,.0f} USDT "
        f"且 >= 自己前{MARKET_HEAT_DAYS}個完整日中位數 {MARKET_HEAT_MIN_RATIO:.2f}x"
    )
    print("[V23-MARKETS] 進場量能仍為1.30x → 0.60~1.20x → >=1.00x且+10%")
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

    segments = v22._split_exchange_outputs()
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
        },
    }
    (MARKETS_OUTPUT_DIR / "segments_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def audit_v23(exchange_result: dict[str, Any]) -> dict[str, Any]:
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
        "version": "V23",
        "fixed_5m_gate_removed": True,
        "top_n_gate_removed": True,
        "market_heat_rule": {
            "rolling_24h_quote_turnover_floor_usdt": MIN_ROLLING_24H_QUOTE_TURNOVER,
            "comparison": f">={MARKET_HEAT_MIN_RATIO:.2f}x own previous-{MARKET_HEAT_DAYS}-completed-day median",
            "current_day_excluded_from_baseline": True,
            "historical_bar_specific": True,
        },
        "signal_relative_volume": "previous-20-bar median; breakout>=1.30x; retest 0.60-1.20x; confirm>=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R, no maximum",
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required_segments.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v23_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V23 啟動｜相對市場熱度＋三交易所雙向回測")
    print("市場：Binance / Bitget / BingX；幣圈與HIGH_CONFIDENCE美股代幣；LONG / SHORT 都開。")
    print("已拿掉固定24H >=500萬 USDT 與Top-N流動性排名。")
    print(
        f"改成歷史動態門檻：最近24H成交額 >= {MIN_ROLLING_24H_QUOTE_TURNOVER:,.0f} USDT，"
        f"且 >= 自己前{MARKET_HEAT_DAYS}個完整日24H成交額中位數 {MARKET_HEAT_MIN_RATIO:.2f}x。"
    )
    print("訊號量能不變：突破/跌破>=1.30x；回踩/反抽0.60～1.20x；確認>=1.00x且比回踩+10%。")
    print("TP：進場前真實結構>=4R、無上限；+2R保本、+3R鎖1R、+5R鎖3R；無時間出場。")
    print("Funding只記錄、不擋單。原生Alpaca美股此版不跑。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch_v23()
    audit = audit_v23(exchange_result)
    combined = {
        "version": "V23",
        "exchange_markets": exchange_result,
        "us_stocks": {"skipped": True, "reason": "V23 focuses on exchange markets; Alpaca only validates stock-token underlyings"},
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v23.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V23 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"美股代幣清單：{v22.V22_TOKEN_CATALOG_PATH}")
    print(f"碰撞拒絕清單：{v22.V22_REJECTED_COLLISIONS_PATH}")
    print(f"總結：{OUTPUT_DIR / 'summary_v23.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v23_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
