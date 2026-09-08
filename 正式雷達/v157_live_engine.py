#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V007 即時訊號引擎：V006.3 核心 + V157 V85 頂級救援。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import backtest_1y_v84_pre_pump_entry as v84
import v005_live_engine as stable_engine
import v101_live_engine as helpers
from v005_quality_gate import quality_reason


MODEL_PATH = Path(__file__).with_name("v157_live_model.json")
MIN_RR = 4.0
MAX_SLOPE = 0.60
NORMAL_MAX_STOP = 0.020
RESCUE_MAX_STOP = 0.023
RESCUE_MIN_BUILD = 1.90
RESCUE_MAX_RET8 = 0.01
FINAL_MAX_STOP = 0.025
MAX_RISK_GROWTH = 0.30
STRUCTURE_BUFFER_ATR = 0.15


def _num(value: Any, default: float = float("nan")) -> float:
    return helpers.number(value, default)


def _load_model() -> dict:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8-sig"))


MODEL = _load_model()


def _quality_probability(row: dict) -> float:
    values = []
    for name in MODEL["特徵"]:
        value = _num(row.get(name), _num(MODEL["中位數"].get(name)))
        mean = _num(MODEL["平均"].get(name), 0.0)
        std = max(_num(MODEL["標準差"].get(name), 1.0), 1e-12)
        values.append(max(-5.0, min(5.0, (value - mean) / std)))
    weights = [_num(x, 0.0) for x in MODEL["權重_含截距"]]
    z = weights[0] + sum(w * x for w, x in zip(weights[1:], values))
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _confirmed_pivot_low(prepared: pd.DataFrame, now_bar: pd.Timestamp) -> float:
    closed = prepared[prepared.index < now_bar].tail(28)
    if len(closed) < 7:
        return float("nan")
    lows = pd.to_numeric(closed["low"], errors="coerce")
    # 右邊兩根也已收完，才算已確認前低。
    pivots = []
    for i in range(2, len(lows) - 2):
        value = _num(lows.iloc[i])
        around = lows.iloc[i - 2 : i + 3]
        if math.isfinite(value) and value <= _num(around.min()):
            pivots.append(value)
    return pivots[-1] if pivots else float("nan")


def _v156_failure_guard(row: dict) -> bool:
    room = _num(row.get("prior_resistance_room_r"), float("inf"))
    if room == float("inf") or room >= 10.0:
        return True
    bad = 0
    bad += int(_num(row.get("range20_atr"), 0.0) > 4.5)
    bad += int(_num(row.get("ret8_before_entry_pct"), 0.0) > 1.5)
    bad += int(_num(row.get("score"), 999.0) < 45.0)
    bad += int(
        _num(row.get("volume_ratio"), 999.0) < 1.8
        and _num(row.get("volume_build_3v20"), 999.0) < 1.6
    )
    return bad < 3


def _v85_selected(row: dict) -> bool:
    group = str(row.get("asset_group") or "").upper()
    if group in {"BTC", "ETH"}:
        return True
    probability = _quality_probability(row)
    row["V157走查品質機率"] = probability
    row["V157走查品質門檻"] = _num(MODEL["小幣入選門檻"])
    return probability >= _num(MODEL["小幣入選門檻"])


def _transform_v157(source: dict, prepared: pd.DataFrame, now_bar: pd.Timestamp) -> dict | None:
    row = {k: v for k, v in source.items() if k != "_frame"}
    entry = _num(row.get("entry")); old_stop = _num(row.get("stop"))
    if not (entry > old_stop > 0):
        return None
    old_risk = entry - old_stop
    old_stop_pct = old_risk / entry
    build = _num(row.get("volume_build_3v20"))
    ret8 = _num(row.get("ret8_before_entry_pct")) / 100.0
    rescue = (
        NORMAL_MAX_STOP < old_stop_pct <= RESCUE_MAX_STOP
        and build >= RESCUE_MIN_BUILD
        and ret8 <= RESCUE_MAX_RET8
    )
    if old_stop_pct > NORMAL_MAX_STOP and not rescue:
        return None
    if _num(row.get("ema20_slope8_atr"), 999.0) > MAX_SLOPE:
        return None
    if not _v85_selected(row) or not _v156_failure_guard(row):
        return None

    signal = prepared[prepared.index < now_bar].iloc[-1]
    atr = _num(signal.get("atr"))
    pivot = _confirmed_pivot_low(prepared, now_bar)
    final_stop = old_stop
    if math.isfinite(atr) and atr > 0 and math.isfinite(pivot):
        final_stop = min(old_stop, pivot - STRUCTURE_BUFFER_ATR * atr)
    risk = entry - final_stop
    if risk <= 0 or risk > old_risk * (1.0 + MAX_RISK_GROWTH) + 1e-12:
        return None
    stop_pct = risk / entry
    if stop_pct > FINAL_MAX_STOP + 1e-12:
        return None

    # 實盤不偷看未來回踩；保留原本最低4R，不為等回踩假造成交。
    tp1 = entry + 4.0 * risk
    row.update({
        "stop": final_stop, "stop_pct": stop_pct, "risk_price": risk,
        "direction": "LONG", "family": "EXPANSION", "portfolio_kind": "NEW",
        "module": "V157_V85頂級救援", "setup": str(row.get("entry_mode") or "V85暴漲前佈局"),
        "rr": MIN_RR, "rr1": MIN_RR, "tp1": tp1,
        "tp2": entry + 5.0 * risk, "tp3": entry + 6.0 * risk,
        "volume": _num(row.get("volume_ratio"), 0.0), "pressure": 0.0,
        "priority": 60.0 + _num(row.get("score"), 0.0),
        "target_mode": "REAL_STRUCTURE", "live_rule": "V007_V157_V85",
        "V157頂級救援": bool(rescue), "V157原停損_pct": old_stop_pct * 100.0,
        "V157量能堆積": build, "V157進場前8根漲幅_pct": ret8 * 100.0,
        "V157結構前低": pivot, "V157結構緩衝_ATR": STRUCTURE_BUFFER_ATR,
    })
    if quality_reason(row):
        return None
    return row


def _apply_v96_v157(row: dict, prepared: pd.DataFrame, now_bar: pd.Timestamp) -> dict | None:
    """把 V157 已驗證的 V96 層套到原本 V006.3 候選。

    實盤不使用後來才出現的回踩；當下無法同時滿足結構停損與4R就不進。
    """
    module = str(row.get("module") or "").upper()
    direction = str(row.get("direction") or "").upper()
    if "V96" not in module or direction != "LONG":
        return row

    room = _num(row.get("prior_resistance_room_r"), float("inf"))
    ret8_pct = _num(row.get("ret8_before_entry_pct"), 0.0)
    # V156 高信心敗單防護：兩個弱點必須同時出現才排除。
    if room != float("inf") and room < 5.0 and ret8_pct > 2.5:
        return None

    entry = _num(row.get("entry")); old_stop = _num(row.get("stop")); tp1 = _num(row.get("tp1"))
    if not (tp1 > entry > old_stop > 0):
        return None
    old_risk = entry - old_stop
    signal = prepared[prepared.index < now_bar].iloc[-1]
    atr = _num(signal.get("atr"))
    pivot = _confirmed_pivot_low(prepared, now_bar)
    final_stop = old_stop
    if math.isfinite(atr) and atr > 0 and math.isfinite(pivot):
        final_stop = min(old_stop, pivot - STRUCTURE_BUFFER_ATR * atr)
    risk = entry - final_stop
    rr = (tp1 - entry) / risk if risk > 0 else float("nan")
    if (
        risk <= 0
        or risk > old_risk * (1.0 + MAX_RISK_GROWTH) + 1e-12
        or risk / entry > 0.04 + 1e-12
        or not math.isfinite(rr)
        or rr < MIN_RR - 1e-9
    ):
        return None
    out = dict(row)
    out.update({
        "stop": final_stop, "stop_pct": risk / entry, "risk_price": risk,
        "rr": rr, "rr1": rr, "V157結構前低": pivot,
        "V157結構緩衝_ATR": STRUCTURE_BUFFER_ATR,
        "V157_V96高信心防護": "通過",
    })
    return out


def _v85_candidates(target: Any, prepared: pd.DataFrame, now_bar: pd.Timestamp) -> list[dict]:
    meta = {"exchange": target.exchange_name, "symbol": target.symbol, "base": target.base}
    out = []
    for variant in ("壓縮早突破", "均線假跌破站回"):
        rows, _ = v84.candidates(prepared, meta, variant)
        for source in rows:
            if pd.Timestamp(source.get("entry_time")) != now_bar:
                continue
            row = _transform_v157(source, prepared, now_bar)
            if row:
                out.append(row)
    return out


def causal_candidates(target: Any, raw: pd.DataFrame) -> list[dict]:
    rows = stable_engine.causal_candidates(target, raw)
    prepared, now_bar, _ = helpers._prepared_now(raw)
    if prepared is None or prepared.empty:
        return []
    aligned = []
    for source in rows:
        row = _apply_v96_v157(dict(source), prepared, now_bar)
        if row is not None:
            aligned.append(row)
    rows = aligned
    rows.extend(_v85_candidates(target, prepared, now_bar))
    best: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (
            str(row.get("base") or "").upper(),
            str(row.get("direction") or "").upper(),
            str(row.get("portfolio_kind") or ""),
        )
        if key not in best or _num(row.get("priority"), -999.0) > _num(best[key].get("priority"), -999.0):
            best[key] = row
    return list(best.values())


discover_targets = stable_engine.discover_targets
