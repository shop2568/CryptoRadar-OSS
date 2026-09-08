#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V101 正式即時訊號適配器。"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

import backtest_1y_v84_pre_pump_entry as v84
import v003_live_engine as legacy


def number(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _prepared_now(raw: pd.DataFrame):
    now_bar = pd.Timestamp.now(tz="UTC").floor("15min")
    work = raw.copy().sort_index()
    closed = work[work.index < now_bar].tail(1000).copy()
    if len(closed) < 850:
        return None, None, None
    current = work[work.index >= now_bar].head(1)
    entry_open = number(current.open.iloc[0], number(closed.close.iloc[-1])) if not current.empty else number(closed.close.iloc[-1])
    synthetic = pd.DataFrame(
        {"open": [entry_open], "high": [entry_open], "low": [entry_open], "close": [entry_open], "volume": [0.0]},
        index=[now_bar],
    )
    return v84.prepare(pd.concat([closed, synthetic])), now_bar, entry_open


def _attach_features(row: dict, prepared: pd.DataFrame, now_bar: pd.Timestamp) -> dict:
    x = dict(row)
    signal = prepared[prepared.index < now_bar].iloc[-1]
    entry = number(x.get("entry")); stop = number(x.get("stop"))
    x.update({
        "volume_ratio": number(signal.get("vol_ratio"), number(x.get("volume"), 0.0)),
        "volume_build_3v20": number(signal.get("volume_build")),
        "ema20_slope8_atr": number(signal.get("ema20_slope8")),
        "ret8_before_entry_pct": number(signal.get("ret8")) * 100.0,
        "stop_pct": abs(entry - stop) / entry if min(entry, stop) > 0 else float("nan"),
    })
    return x


def _core(target: Any, raw: pd.DataFrame, prepared: pd.DataFrame, now_bar: pd.Timestamp):
    row = legacy.causal_candidate(target, raw)
    if not row or number(row.get("rr"), number(row.get("rr1"), 0.0)) < 4.0:
        return None
    row = _attach_features(row, prepared, now_bar)
    row.update({
        "family": "CORE", "portfolio_kind": "CORE", "module": str(row.get("setup") or row.get("setup_type") or "V94核心"),
        "priority": 1000.0 + number(row.get("v61_priority"), number(row.get("priority"), 0.0)),
        "live_rule": "V101_CORE_4R",
    })
    return row


def _v96_validation_filter(row: dict) -> bool:
    score = number(row.get("score")); volume = number(row.get("volume_ratio")); build = number(row.get("volume_build_3v20"))
    heat = number(row.get("ret8_before_entry_pct")); stop = number(row.get("stop_pct")); slope = number(row.get("ema20_slope8_atr"))
    room = number(row.get("prior_resistance_room_r"), float("inf"))
    return (
        45.0 <= score <= 50.0 and 2.20 <= volume <= 3.50 and 1.15 <= build <= 1.80
        and heat <= 3.5 and stop <= 0.045 and 0.10 <= slope <= 0.90 and room >= 4.0
    )


def _expansion(target: Any, prepared: pd.DataFrame, now_bar: pd.Timestamp) -> list[dict]:
    meta = {"exchange": target.exchange_name, "symbol": target.symbol, "base": target.base}
    out: list[dict] = []
    for variant in ("壓縮早突破", "均線假跌破站回"):
        rows, _ = v84.candidates(prepared, meta, variant)
        for source in rows:
            if pd.Timestamp(source["entry_time"]) != now_bar or not _v96_validation_filter(source):
                continue
            row = {k: v for k, v in source.items() if k != "_frame"}
            entry = number(row["entry"]); stop = number(row["stop"]); risk = entry - stop
            if min(entry, stop, risk) <= 0:
                continue
            row.update({
                "direction": "LONG", "family": "EXPANSION", "portfolio_kind": "NEW",
                "module": "V96強勢突破延續_驗證型", "setup": variant,
                "rr": 4.0, "rr1": 4.0, "tp1": entry + 4.0 * risk,
                "tp2": entry + 5.0 * risk, "tp3": entry + 6.0 * risk,
                "volume": number(row.get("volume_ratio")), "pressure": 0.0,
                "priority": number(row.get("score")), "target_mode": "REAL_STRUCTURE",
                "live_rule": "V101_V96_EXPANSION_4R",
            })
            out.append(row)
    return out


def causal_candidates(target: Any, raw: pd.DataFrame) -> list[dict]:
    prepared, now_bar, _ = _prepared_now(raw)
    if prepared is None or prepared.empty:
        return []
    rows: list[dict] = []
    core = _core(target, raw, prepared, now_bar)
    if core:
        rows.append(core)
    rows.extend(_expansion(target, prepared, now_bar))
    # 同標的、同方向、同模組當根只留分數最高者。
    best: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (str(row.get("base") or "").upper(), str(row.get("direction") or "").upper(), str(row.get("portfolio_kind")))
        if key not in best or number(row.get("priority"), -999.0) > number(best[key].get("priority"), -999.0):
            best[key] = row
    return list(best.values())


def discover_targets(scanners):
    return legacy.discover_targets(scanners)
