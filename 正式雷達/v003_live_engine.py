#!/usr/bin/env python3
"""V003 causal live candidate adapter based on V72.8S research rules.

The historical V71 file is a portfolio experiment, not a live scanner.  This
adapter runs its real upstream V60/V56 signal generator on completed 15-minute
bars, applies the V56 elite gate, V70 ranking and the conservative V71 filter.
Only information available before the order is used. CORE remains 4R+; the
3R-4R target-floor patch applies only to structural FLEX candidates.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd

import backtest_1y_v60_r3 as r3

engine = r3.engine
v56 = r3.v56


def _install_structure_rr3_floor() -> None:
    """Permit real 3R targets only for the V52 structural expansion path."""
    v11 = v56.v32.v17.v11
    if getattr(v11, "_v003_rr3_floor_installed", False):
        v56.STRUCTURE_MIN_ROOM_R = 3.0
        return
    original = v11.confirmed_zone_targets
    original_floor = float(v11.engine.MIN_TARGET_R)
    if abs(original_floor - 4.0) > 1e-9:
        raise RuntimeError(f"Unexpected core target floor: {original_floor}")

    def patched(frame, signal_i, direction, entry, stop_distance,
                cached_pivot_highs, cached_pivot_lows, minimum_room_r=4.0):
        temporary = 3.0 if abs(float(minimum_room_r) - 3.0) <= 1e-9 else original_floor
        old = float(v11.engine.MIN_TARGET_R)
        try:
            v11.engine.MIN_TARGET_R = temporary
            return original(frame, signal_i, direction, entry, stop_distance,
                            cached_pivot_highs, cached_pivot_lows,
                            minimum_room_r=minimum_room_r)
        finally:
            v11.engine.MIN_TARGET_R = old

    if hasattr(v11, "_target_cache"):
        v11._target_cache.clear()
    v11.confirmed_zone_targets = patched
    v11._v003_rr3_floor_installed = True
    v56.STRUCTURE_MIN_ROOM_R = 3.0


_install_structure_rr3_floor()

COLUMNS = (
    "exchange", "symbol", "base", "direction", "signal_time", "entry_time",
    "exit_time", "period", "scheme", "entry", "stop", "tp1", "tp2", "tp3",
    "rr1", "rr2", "rr3", "exit_price", "score", "outcome", "exit_reason",
    "timed_out", "gross_r", "cost_r", "net_r", "confirmation_volume",
    "signal_range_atr", "confirmation_pressure", "nearest_rr", "stop_atr",
    "stop_pct", "entry_delay_bars", "holding_bars", "mfe_r", "mae_r",
)


def f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _audit_for(row: dict[str, Any]) -> dict[str, Any]:
    wanted = (str(row["symbol"]), str(row["direction"]), str(row["entry_time"]))
    sources = list(getattr(r3, "_EXCHANGE_FILTER_AUDIT", []))
    sources += list(getattr(v56, "_STRUCTURE_AUDIT", []))
    found: dict[str, Any] = {}
    for item in sources:
        key = (str(item.get("symbol") or ""), str(item.get("direction") or ""), str(item.get("entry_time") or ""))
        if key == wanted:
            found.update(item)
    return found


def _v61_priority(row: dict[str, Any]) -> tuple[float, dict[str, float | str]]:
    direction = str(row.get("direction") or "").upper()
    setup = str(row.get("setup_type") or "V32_CORE").upper()
    if direction == "SHORT":
        if "REBOUND_FAIL" in setup or "REBOUND_FAILURE" in setup: structure = 38.0
        elif setup == "FIRST_PULLBACK_RECLAIM": structure = 32.0
        elif setup == "STRUCTURAL_REENTRY": structure = 29.0
        elif setup == "V32_CORE": structure = 24.0
        elif "DIRECT_BREAK" in setup: structure = 16.0
        else: structure = 18.0
    else:
        if setup == "FIRST_PULLBACK_RECLAIM": structure = 34.0
        elif setup == "STRUCTURAL_REENTRY": structure = 30.0
        elif setup == "V32_CORE": structure = 25.0
        elif "DIRECT_BREAK" in setup: structure = 15.0
        else: structure = 18.0
    rr = min(max(f(row.get("rr1"), 4.0), 4.0), 10.0)
    room = min(18.0, (rr - 4.0) * 3.0)
    delay = max(1.0, f(row.get("entry_delay_bars"), 1.0))
    freshness = max(0.0, 10.0 - (delay - 1.0) * 3.0)
    extension = f(row.get("entry_extension_atr"), float("nan"))
    chase = f(row.get("chase_distance_4h_atr"), float("nan"))
    if direction == "SHORT" and ("REBOUND_FAIL" in setup or "FIRST_PULLBACK" in setup or "REENTRY" in setup):
        retrace = max(0.0, -extension) if math.isfinite(extension) else f(row.get("rebound_depth_atr"), 0.0)
        timing = min(12.0, 5.0 + retrace * 4.0)
    elif math.isfinite(extension): timing = max(-8.0, 10.0 - abs(extension) * 8.0)
    elif math.isfinite(chase): timing = max(-8.0, 9.0 - max(0.0, chase - 1.0) * 4.0)
    else: timing = 0.0
    vol = f(row.get("confirmation_volume"), 0.0)
    volume = 8.0 if 1.20 <= vol <= 3.50 else 4.0 if 1.0 <= vol <= 8.0 else -2.0 if vol > 8.0 else 0.0
    signal_range = f(row.get("signal_range_atr"), 9.0)
    candle = 8.0 if signal_range <= .28 else 5.0 if signal_range <= .45 else 1.0 if signal_range <= .75 else -4.0
    pressure_raw = f(row.get("confirmation_pressure"), 0.0)
    pressure = max(-4.0, min(6.0, (pressure_raw if direction == "LONG" else -pressure_raw) * 15.0))
    stop_pct = f(row.get("stop_pct"), 0.0)
    stop_quality = 5.0 if 2.4 <= stop_pct <= 8.0 else 1.0 if 1.2 <= stop_pct < 2.4 else -5.0 if 0 < stop_pct < 1.2 else 0.0
    legacy_quality = max(-4.0, min(8.0, 8.0 - f(row.get("score"), .5) * 20.0))
    parts = dict(structure=structure, room=room, freshness=freshness, timing=timing,
                 volume=volume, candle=candle, pressure=pressure, stop=stop_quality,
                 legacy=legacy_quality)
    return round(sum(float(x) for x in parts.values()), 8), parts


def _v70_expansion_rank(row: dict[str, Any]) -> float:
    reason = str(row.get("elite_reason") or "").upper()
    setup = str(row.get("setup_type") or "").upper()
    if "FIRST_PULLBACK" in reason or setup == "FIRST_PULLBACK_RECLAIM": structure = 36.0
    elif "BREAKDOWN_TRANSITION" in reason: structure = 31.0
    elif "BREAKDOWN_RANGE" in reason: structure = 25.0
    elif "WIDE_STRUCTURE" in reason: structure = 28.0
    else: structure = 0.0
    rr = min(max(f(row.get("rr1"), 4.0), 4.0), 20.0)
    vol = min(max(f(row.get("confirmation_volume"), 1.0), 1.0), 4.0)
    delay = max(f(row.get("entry_delay_bars"), 1.0), 1.0)
    ext = f(row.get("entry_extension_atr"), 0.0)
    return structure + (8.0 if row["direction"] == "SHORT" else 0.0) + (rr - 4.0) * 3.0 + (vol - 1.0) * 7.0 + max(0.0, 14.0 - 5.0 * (delay - 1.0)) - max(0.0, ext - .35) * 25.0


def _v003_flex(row: dict[str, Any]) -> tuple[bool, str]:
    reason = str(row.get("elite_reason") or "").upper()
    setup = str(row.get("setup_type") or "").upper()
    side = str(row.get("direction") or "").upper()
    rr = f(row.get("rr1")); vol = f(row.get("confirmation_volume"))
    delay = f(row.get("entry_delay_bars"), 99.0); ext = f(row.get("entry_extension_atr"), 99.0)
    if row.get("portfolio_tier") != "ELITE_EXPANSION": return False, "非頂級擴張訊號"
    if rr < 3.0: return False, "TP1不足3R"
    if vol < 1.0: return False, "該幣成交量不足"
    if delay > 2.0: return False, "訊號太舊"
    if ext > .75: return False, "追價太遠"
    if side == "LONG": return False, "暫停擴張型多單"
    if not reason.startswith("ELITE_SHORT"): return False, "不是頂級擴張空單"
    pressure = f(row.get("confirmation_pressure"))
    if 3.0 <= rr < 4.0:
        ok = vol >= 4.0 and ext <= .20
        return ok, "V72.8S 3R空單" if ok else "3R空單量能或追價不合格"
    if setup == "FIRST_PULLBACK_RECLAIM":
        ok = rr >= 4.0 and vol >= 1.45 and pressure <= -.23
        return ok, "V72.4S反彈失敗空單" if ok else "反彈失敗空單品質不足"
    if 4.0 <= rr <= 6.50 and vol >= 1.10 and pressure <= -.14:
        return True, "V72.4S直接空單"
    if 6.50 < rr <= 7.30 and vol >= 4.0 and pressure <= -.25:
        return True, "V72.4S高R例外空單"
    return False, "直接空單空間或壓力不足"


def causal_candidate(target: Any, raw: pd.DataFrame) -> dict[str, Any] | None:
    """Return only a candidate whose entry is the currently forming 15m open."""
    if raw is None or len(raw) < 850:
        return None
    work = raw.copy().sort_index()
    now_bar = pd.Timestamp.now(tz="UTC").floor("15min")
    closed = work[work.index < now_bar].tail(1000).copy()
    if len(closed) < 850:
        return None
    # Synthetic next bar lets the unchanged backtest engine use last completed
    # candle as signal and this bar's open as causal entry.  No future high/low is used.
    current = work[work.index >= now_bar].head(1)
    entry_open = f(current.open.iloc[0], f(closed.close.iloc[-1])) if not current.empty else f(closed.close.iloc[-1])
    synthetic = pd.DataFrame({"open":[entry_open], "high":[entry_open], "low":[entry_open],
                              "close":[entry_open], "volume":[0.0]}, index=[now_bar])
    prepared, piv_hi, piv_lo = engine.prepare_indicators(pd.concat([closed, synthetic]))
    funnel: dict[str, int] = defaultdict(int)
    rows = list(r3.candidate_rows_v60(target, prepared, now_bar - pd.Timedelta(days=2), piv_hi, piv_lo, funnel))
    fresh = [r for r in rows if pd.Timestamp(r[5]) == now_bar]
    if not fresh:
        return None
    # A single venue/base/direction may expose more than one setup. Highest V61
    # pre-entry score wins; outcomes and future path are deliberately ignored.
    candidates: list[dict[str, Any]] = []
    for raw_row in fresh:
        row = dict(zip(COLUMNS, raw_row))
        audit = _audit_for(row)
        for key in ("setup_type", "regime_audit", "entry_extension_atr", "market_heat_ratio_audit_only",
                    "chase_distance_4h_atr", "rebound_depth_atr"):
            if key in audit: row[key] = audit[key]
        row.setdefault("setup_type", "V32_CORE")
        row["setup"] = row["setup_type"]
        row["rr"] = f(row.get("rr1"))
        row["volume"] = f(row.get("confirmation_volume"))
        row["pressure"] = f(row.get("confirmation_pressure"))
        row["target_mode"] = str(audit.get("target_mode") or "REAL_STRUCTURE")
        score, parts = _v61_priority(row)
        row["v61_priority"] = score; row["priority_parts"] = parts
        elite, elite_reason = v56._v56_elite_expansion_gate(row, audit or None)
        row["elite_ok"] = bool(elite); row["elite_reason"] = elite_reason
        row["portfolio_tier"] = "ELITE_EXPANSION" if elite else "CORE_CANDIDATE"
        row["v70_priority"] = _v70_expansion_rank(row) if elite else score
        row["flex_ok"], row["flex_reject_reason"] = _v003_flex(row) if elite else (False, "核心候選")
        candidates.append(row)
    return max(candidates, key=lambda x: f(x.get("v61_priority"), -999.0))


def discover_targets(scanners: list[tuple[str, Any]]) -> tuple[list[Any], list[dict[str, str]]]:
    """Use the same V22/V71 market universe: crypto deduped by base, verified
    tokenized US markets retained per venue and mapped to their underlying base.
    """
    return r3.v22.discover_three_exchange_targets({name: exchange for name, exchange in scanners})


def finalize_cycle(rows: list[dict[str, Any]], core_slots_available: int,
                   active_directions: dict[str, int] | None = None,
                   recent_directions: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Classify the current bar: protected 4R core first, then V72.8S flex."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("base") or "").upper(), str(row.get("direction") or "").upper())
        if key not in best or f(row.get("v61_priority"), -999) > f(best[key].get("v61_priority"), -999):
            best[key] = row
    ranked = sorted(best.values(), key=lambda x: (-f(x.get("v61_priority"), -999), str(x.get("symbol") or "")))
    out: list[dict[str, Any]] = []; core_added = 0; flex_added = 0
    active_directions = dict(active_directions or {})
    recent_directions = dict(recent_directions or {})
    for row in ranked:
        x = dict(row)
        side = str(x.get("direction") or "").upper()
        core_allowed = (f(x.get("rr"), f(x.get("rr1"))) >= 4.0
                        and core_added < 1 and core_added < max(0, core_slots_available)
                        and active_directions.get(side, 0) < 2
                        and recent_directions.get(side, 0) < 2)
        if core_allowed:
            x["family"] = "CORE"; x["priority"] = 1000.0 + f(x.get("v61_priority"))
            out.append(x); core_added += 1
            active_directions[side] = active_directions.get(side, 0) + 1
            recent_directions[side] = recent_directions.get(side, 0) + 1
        elif flex_added < 1 and x.get("elite_ok") and x.get("flex_ok"):
            x["family"] = "EXPANSION"; x["priority"] = f(x.get("v70_priority"))
            out.append(x); flex_added += 1
    return out
