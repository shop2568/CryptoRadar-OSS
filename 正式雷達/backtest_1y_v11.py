#!/usr/bin/env python3
"""CryptoRadar V11 one-year structural-target research backtest.

V11 deliberately keeps the verified V8/V10 execution model:

* 365 days of closed 15-minute exchange candles;
* each market is independent and BTC is not a direction gate;
* tokenized US stocks/indices/FX start at the first exchange candle;
* the existing structure stop, fees, slippage and portfolio rules are unchanged;
* TP1 closes 100%, stop closes 100%, and there is no time exit.

The V10 fixed-4R fallback is removed.  A trade now needs a target that was
already knowable before entry.  Targets are confirmed 4H swing levels or
zones with repeated 4H touches, must be at least 4R away, and have no maximum
R cap.  Three chase-score thresholds are compared without changing stops.
"""

from __future__ import annotations

import builtins
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


_REAL_PRINT = builtins.print


ROOT = Path.home() / "CryptoRadar"
ENGINE_PATH = ROOT / "backtest_1y_v8.py"
if not ENGINE_PATH.exists():
    ENGINE_PATH = Path(__file__).with_name("backtest_1y_v8.py")
if not ENGINE_PATH.exists():
    raise RuntimeError(
        "找不到 backtest_1y_v8.py；請把V8引擎與V11放在同一目錄。"
    )

spec = importlib.util.spec_from_file_location(
    "cryptoradar_v8_engine_v11", ENGINE_PATH
)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V8引擎：{ENGINE_PATH}")
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)


# ===== V11 research matrix =====
SCHEME_LT08 = "V11_STRUCTURE_SCORE_LT08"
SCHEME_LT10 = "V11_STRUCTURE_SCORE_LT10"
SCHEME_CONTROL = "V11_STRUCTURE_NO_SCORE"

engine.SCHEME_CONFIGS = {
    SCHEME_LT08: {
        "minimum_room_r": 4.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": 0.8,
        "maximum_tp1_r": None,
        "score_order": "ASC",
    },
    SCHEME_LT10: {
        "minimum_room_r": 4.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": 1.0,
        "maximum_tp1_r": None,
        "score_order": "ASC",
    },
    SCHEME_CONTROL: {
        "minimum_room_r": 4.0,
        "confirm_volume": 0.8,
        "maximum_chase_score": None,
        "maximum_tp1_r": None,
        "score_order": "ASC",
    },
}
engine.SCHEMES = tuple(engine.SCHEME_CONFIGS)
engine.PRIMARY_SCHEME = SCHEME_LT10
engine.OUTPUT_DIR = ROOT / "backtest_1y_v11_results"
engine.DATABASE_PATH = engine.OUTPUT_DIR / "candidates.sqlite3"
engine.MAX_TARGET_R = None


# ===== Structural target rules =====
TARGET_LOOKBACK_DAYS = 180
ZONE_TOLERANCE_PCT = 0.0035
ZONE_TOLERANCE_STOP_R = 0.20
MIN_ZONE_TOUCHES = 2

_target_cache: dict[
    tuple[int, int, int, int, str, float, float, float],
    Optional[tuple[float, float, float, float, float, float, float]],
] = {}


def _cluster_levels(
    raw_levels: list[tuple[pd.Timestamp, float, bool]],
    tolerance: float,
) -> list[dict[str, Any]]:
    """Cluster nearby historical levels into zones without future data."""
    if not raw_levels:
        return []
    ordered = sorted(raw_levels, key=lambda item: item[1])
    groups: list[list[tuple[pd.Timestamp, float, bool]]] = []
    for item in ordered:
        if not groups:
            groups.append([item])
            continue
        center = sum(level for _, level, _ in groups[-1]) / len(groups[-1])
        if abs(item[1] - center) <= tolerance:
            groups[-1].append(item)
        else:
            groups.append([item])

    zones: list[dict[str, Any]] = []
    for group in groups:
        values = [level for _, level, _ in group]
        zones.append({
            "low": min(values),
            "high": max(values),
            "center": sum(values) / len(values),
            "touches": len(group),
            "strong": any(strong for _, _, strong in group),
            "latest": max(timestamp for timestamp, _, _ in group),
        })
    return zones


def _completed_4h_swings(
    frame: pd.DataFrame,
    signal_i: int,
    window_start: pd.Timestamp,
) -> tuple[
    list[tuple[pd.Timestamp, float]],
    list[tuple[pd.Timestamp, float]],
]:
    """Return denser 1-left/1-right swings known before the signal bucket."""
    current_bucket = frame.index[signal_i].floor("4h")
    history = frame.loc[
        (frame.index >= window_start) & (frame.index < current_bucket),
        ["open", "high", "low", "close"],
    ]
    if len(history) < 12:
        return [], []

    four_hour = history.resample("4h", label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }).dropna()
    if len(four_hour) < 3:
        return [], []

    pivot_high = (
        (four_hour["high"] > four_hour["high"].shift(1))
        & (four_hour["high"] >= four_hour["high"].shift(-1))
    )
    pivot_low = (
        (four_hour["low"] < four_hour["low"].shift(1))
        & (four_hour["low"] <= four_hour["low"].shift(-1))
    )

    # Exclude the last row because a one-right-bar pivot is not knowable until
    # that right-side 4H candle has fully closed.
    pivot_high.iloc[-1] = False
    pivot_low.iloc[-1] = False
    highs = [
        (timestamp, float(value))
        for timestamp, value in four_hour.loc[pivot_high, "high"].items()
    ]
    lows = [
        (timestamp, float(value))
        for timestamp, value in four_hour.loc[pivot_low, "low"].items()
    ]
    return highs, lows


def confirmed_zone_targets(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    cached_pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    minimum_room_r: float = 4.0,
) -> Optional[tuple[float, float, float, float, float, float, float]]:
    """Choose the nearest real structural TP >=4R, with no maximum R cap."""
    cache_key = (
        id(frame), int(frame.index[0].value), int(frame.index[-1].value),
        signal_i, direction,
        round(entry, 12), round(stop_distance, 12), round(minimum_room_r, 4),
    )
    if cache_key in _target_cache:
        return _target_cache[cache_key]

    if not math.isfinite(entry) or not math.isfinite(stop_distance):
        _target_cache[cache_key] = None
        return None
    if entry <= 0 or stop_distance <= 0:
        _target_cache[cache_key] = None
        return None

    current_bucket = frame.index[signal_i].floor("4h")
    window_start = frame.index[
        max(0, signal_i - TARGET_LOOKBACK_DAYS * 96)
    ]
    dense_highs, dense_lows = _completed_4h_swings(
        frame, signal_i, window_start
    )

    # Strong pivots use the original two-left/two-right V8 confirmation.
    if direction == "LONG":
        strong = [
            (pivot_time, float(value), True)
            for pivot_time, confirmed_time, value in cached_pivot_highs
            if pivot_time >= window_start
            and confirmed_time <= current_bucket
            and value > entry
        ]
        dense = [
            (timestamp, value, False)
            for timestamp, value in dense_highs
            if value > entry
        ]
        reverse = False
    else:
        strong = [
            (pivot_time, float(value), True)
            for pivot_time, confirmed_time, value in cached_pivot_lows
            if pivot_time >= window_start
            and confirmed_time <= current_bucket
            and value < entry
        ]
        dense = [
            (timestamp, value, False)
            for timestamp, value in dense_lows
            if value < entry
        ]
        reverse = True

    # Deduplicate the same pivot appearing in both the strong and dense sets.
    merged: dict[tuple[int, int], tuple[pd.Timestamp, float, bool]] = {}
    for timestamp, level, is_strong in strong + dense:
        key = (int(timestamp.value), round(level / entry * 1_000_000))
        previous = merged.get(key)
        merged[key] = (
            timestamp,
            level,
            is_strong or (previous[2] if previous else False),
        )

    tolerance = max(
        entry * ZONE_TOLERANCE_PCT,
        stop_distance * ZONE_TOLERANCE_STOP_R,
    )
    zones = _cluster_levels(list(merged.values()), tolerance)
    if not zones:
        _target_cache[cache_key] = None
        return None

    # The near edge is the conservative first price at which the zone is hit.
    for zone in zones:
        zone["target"] = zone["low"] if direction == "LONG" else zone["high"]
        zone["rr"] = abs(float(zone["target"]) - entry) / stop_distance
    zones.sort(key=lambda zone: float(zone["target"]), reverse=reverse)

    nearest_rr = float(zones[0]["rr"])
    if nearest_rr < minimum_room_r:
        _target_cache[cache_key] = None
        return None

    valid = [
        zone for zone in zones
        if float(zone["rr"]) >= engine.MIN_TARGET_R
        and (bool(zone["strong"]) or int(zone["touches"]) >= MIN_ZONE_TOUCHES)
    ]
    if not valid:
        _target_cache[cache_key] = None
        return None

    chosen = (valid + [valid[-1], valid[-1]])[:3]
    tp1, tp2, tp3 = (float(zone["target"]) for zone in chosen)
    rr1, rr2, rr3 = (float(zone["rr"]) for zone in chosen)
    result = (tp1, tp2, tp3, rr1, rr2, rr3, nearest_rr)
    _target_cache[cache_key] = result
    return result


engine.confirmed_structure_targets = confirmed_zone_targets


# ===== Long-only extra confirmation =====
_original_candidate_rows = engine.candidate_rows


def candidate_rows_with_direction_rules(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
) -> Iterable[tuple[Any, ...]]:
    """Require the last completed 1H trend for longs; shorts stay unchanged."""
    # The V8 long context already requires bull4h.  Intersecting that column
    # with the shifted (therefore completed) bull1h flag applies the new rule
    # before signal cooldown and target simulation.  Restore it afterwards so
    # the caller's frame is not permanently mutated.  Short rules use bear4h
    # and are therefore untouched.
    original_bull4h = frame["bull4h"].copy()
    frame["bull4h"] = original_bull4h.astype(bool) & frame["bull1h"].astype(bool)
    funnel["V11_long_1h_trend_bars"] = (
        funnel.get("V11_long_1h_trend_bars", 0)
        + int(frame["bull4h"].sum())
    )
    try:
        yield from _original_candidate_rows(
            target,
            frame,
            research_start,
            pivot_highs,
            pivot_lows,
            funnel,
        )
    finally:
        frame["bull4h"] = original_bull4h


engine.candidate_rows = candidate_rows_with_direction_rules


def v11_score_rr_diagnostics(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Report the requested 4-5R, 5-6R and >=6R performance bands."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for trade in trades:
        score = float(trade["score"])
        rr1 = float(trade["rr1"])
        if score < 0.8:
            score_band = "LT_0_8"
        elif score < 1.0:
            score_band = "0_8_TO_LT_1_0"
        else:
            score_band = "GE_1_0"
        if rr1 < 5.0:
            rr_band = "4_0_TO_LT_5_0"
        elif rr1 < 6.0:
            rr_band = "5_0_TO_LT_6_0"
        else:
            rr_band = "GE_6_0"
        groups.setdefault(
            (str(trade["direction"]), score_band, rr_band), []
        ).append(trade)

    rows: list[dict[str, Any]] = []
    for (direction, score_band, rr_band), part in groups.items():
        wins = sum(item["outcome"] == "WIN" for item in part)
        losses = sum(item["outcome"] == "LOSS" for item in part)
        opens = sum(item["outcome"] == "OPEN" for item in part)
        closed = wins + losses
        closed_part = [item for item in part if item["outcome"] != "OPEN"]
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
                sum(float(item["net_r"]) for item in closed_part) / closed,
                5,
            ) if closed else 0.0,
            "total_net_r": round(
                sum(float(item["net_r"]) for item in closed_part), 5
            ),
            "net_profit_usdt": round(
                sum(float(item["profit_usdt"]) for item in part), 4
            ),
            "average_mfe_r": round(
                sum(float(item["mfe_r"]) for item in part) / len(part), 5
            ),
            "average_mae_r": round(
                sum(float(item["mae_r"]) for item in part) / len(part), 5
            ),
        })
    return sorted(
        rows,
        key=lambda item: (
            item["direction"], item["score_band"], item["rr_band"]
        ),
    )


engine.score_rr_diagnostics = v11_score_rr_diagnostics


def print_v11_comparison(rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print("CryptoRadar V11｜365天全市場｜真實結構TP最低4R、最高不設限")
    print("做多加已完成1H趨勢確認｜做空維持原規則｜TP1全平｜無時間出場")
    print("=" * 100)
    print(
        "方案                         區段   交易   勝   敗  未平"
        "   總勝率    平均R       淨利      回撤"
    )
    for row in rows:
        print(
            f"{row['scheme']:<28} {row['period']:<4} "
            f"{row['trades']:>5} {row['wins']:>4} "
            f"{row['losses']:>4} {row['open']:>5} "
            f"{row['win_rate_pct']:>7.2f}% "
            f"{row['average_net_r']:>+8.3f} "
            f"{row['net_profit_usdt']:>+10.2f} "
            f"{row['max_drawdown_pct']:>7.2f}%"
        )


engine.print_comparison = print_v11_comparison


def _audit_results() -> dict[str, Any]:
    """Fail loudly if a generated trade violates the promised V11 rules."""
    audit: dict[str, Any] = {
        "minimum_tp1_r": engine.MIN_TARGET_R,
        "maximum_tp1_r": None,
        "schemes": {},
        "passed": True,
    }
    allowed_exits = {"TP1_ALL", "STOP", "OPEN_AT_END"}
    for scheme in engine.SCHEMES:
        path = engine.OUTPUT_DIR / f"trades_{scheme.lower()}.csv"
        total = 0
        min_rr: Optional[float] = None
        max_rr: Optional[float] = None
        invalid_rr = 0
        invalid_exit = 0
        if path.exists() and path.stat().st_size:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    total += 1
                    rr = float(row["rr1"])
                    min_rr = rr if min_rr is None else min(min_rr, rr)
                    max_rr = rr if max_rr is None else max(max_rr, rr)
                    invalid_rr += int(rr < engine.MIN_TARGET_R - 1e-9)
                    invalid_exit += int(row["exit_reason"] not in allowed_exits)
        scheme_ok = invalid_rr == 0 and invalid_exit == 0
        audit["schemes"][scheme] = {
            "trades": total,
            "minimum_observed_rr": min_rr,
            "maximum_observed_rr": max_rr,
            "invalid_rr": invalid_rr,
            "invalid_exit": invalid_exit,
            "passed": scheme_ok,
        }
        audit["passed"] = bool(audit["passed"] and scheme_ok)

    summary_path = engine.OUTPUT_DIR / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        settings = summary.setdefault("settings", {})
        settings["strategy_version"] = "V11"
        settings["target_policy"] = (
            "nearest pre-entry confirmed 4H swing/touch zone; "
            "minimum 4R; no maximum R cap; no fixed-R fallback"
        )
        settings["target_lookback_days"] = TARGET_LOOKBACK_DAYS
        settings["zone_tolerance_pct"] = ZONE_TOLERANCE_PCT
        settings["minimum_zone_touches"] = MIN_ZONE_TOUCHES
        settings["maximum_tp1_r"] = None
        settings.pop("v8_maximum_tp1_r", None)
        limitations = [
            item for item in summary.get("limitations", [])
            if "V8_R2/V8_R3" not in str(item)
        ]
        limitations.append(
            "V11結構區由已收完4H K線推導；雖已防止偷看未來，仍需樣本外驗證。"
        )
        summary["limitations"] = limitations
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    audit_path = engine.OUTPUT_DIR / "v11_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not audit["passed"]:
        raise RuntimeError(f"V11結果稽核失敗，請檢查：{audit_path}")
    return audit


def _translated_print(*args: Any, **kwargs: Any) -> None:
    """Replace only stale V8 status lines emitted by the imported engine."""
    converted: list[Any] = []
    for value in args:
        if isinstance(value, str):
            value = value.replace(
                "高速核心V8：低追價分數＋TP1 4R至5R，並保留V7 R2對照組。",
                "V11：真實結構TP最低4R無上限，比較score<0.8、<1.0與對照組。",
            )
            value = value.replace(
                "候選方案資料共",
                "V11候選方案資料共",
            ).replace(
                "筆，開始模擬V8研究矩陣。",
                "筆，開始模擬V11研究矩陣。",
            )
        converted.append(value)
    _REAL_PRINT(*converted, **kwargs)


def main() -> int:
    print("CryptoRadar V11程式檢查完成。")
    print("規則：真實結構TP最低4R、最高不設限；找不到目標就不進場。")
    print("停損、美股代幣、手續費與無時間出場規則全部沿用原引擎。")
    original_print = builtins.print
    try:
        builtins.print = _translated_print
        result = int(engine.main())
    finally:
        builtins.print = original_print
    audit = _audit_results()
    print(
        "V11結果稽核：PASS"
        if audit["passed"] else "V11結果稽核：FAILED"
    )
    print(f"稽核檔案：{engine.OUTPUT_DIR / 'v11_audit.json'}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
