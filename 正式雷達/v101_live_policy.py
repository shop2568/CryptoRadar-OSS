#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V101 正式雷達的即時組合風控。

只使用進場當下已知的訊號特徵、過去進場與已實現停損。
"""

from __future__ import annotations

import math
import time
from typing import Any


MAX_POSITIONS = 5
MAX_NEW_POSITIONS = 2
MAX_SAME_DIRECTION_24H = 2
STOP_WINDOW_SECONDS = 72 * 3600
COOLDOWN_SECONDS = 48 * 3600
MIN_RR = 4.0


def number(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def side_of(row: dict) -> str:
    return str(row.get("direction") or row.get("direction_v92") or "").upper()


def elite_reason(row: dict) -> str:
    """V101 MAX2 乾淨+斜率的四個可重現例外。"""
    side = side_of(row)
    module = str(row.get("module") or row.get("setup") or row.get("setup_type") or "").upper()
    if side == "SHORT" and ("V72.8S" in module or "REBOUND_FAIL" in module or "FIRST_PULLBACK_RECLAIM" in module):
        return "核心反彈失敗空單"

    score = number(row.get("score"))
    build = number(row.get("volume_build_3v20"))
    volume = number(row.get("volume_ratio"), number(row.get("volume")))
    slope = number(row.get("ema20_slope8_atr"))
    ret8 = number(row.get("ret8_before_entry_pct"))
    stop = number(row.get("stop_pct"))
    if math.isfinite(score) and math.isfinite(build) and score >= 45.0 and build >= 2.0:
        return "單標的量能堆積加高結構分"
    if all(math.isfinite(x) for x in (score, volume, ret8, stop)):
        if score >= 40.0 and volume >= 2.3 and ret8 <= 0.6 and stop <= 0.010:
            return "低追價窄停損早期趨勢"
    if all(math.isfinite(x) for x in (score, slope, ret8, stop)):
        if score >= 38.0 and slope >= 0.8 and ret8 <= 0.6 and stop <= 0.010:
            return "高斜率低追價早期趨勢"
    return ""


def entry_epoch(row: dict) -> float:
    return number(row.get("open_time"), number(row.get("entry_epoch"), 0.0))


def stop_epoch(row: dict) -> float:
    if str(row.get("exit_reason") or "").upper() not in {"STOP", "停損", "原停損"}:
        return 0.0
    return number(row.get("exit_time"), 0.0)


def risk_block(candidate: dict, accepted: list[dict], now: float | None = None) -> str:
    now = time.time() if now is None else float(now)
    side = side_of(candidate)
    if side not in {"LONG", "SHORT"}:
        return "方向資料錯誤"
    if number(candidate.get("rr"), number(candidate.get("rr1"), 0.0)) < MIN_RR:
        return "TP1不足4R"
    elite = elite_reason(candidate)
    stops = sorted(
        t for row in accepted if side_of(row) == side
        for t in [stop_epoch(row)] if t and now - STOP_WINDOW_SECONDS <= t < now
    )
    if len(stops) >= 2 and now < stops[-1] + COOLDOWN_SECONDS and not elite:
        return f"{side}過去72小時已有{len(stops)}筆停損，冷卻中"
    entries = [
        entry_epoch(row) for row in accepted
        if side_of(row) == side and now - 24 * 3600 <= entry_epoch(row) < now
    ]
    if len(entries) >= MAX_SAME_DIRECTION_24H and not elite:
        return f"{side}過去24小時已有{len(entries)}筆進場"
    return ""


def select(candidates: list[dict], active: list[dict], history: list[dict], now: float | None = None):
    """Core 先、NEW 後；同標的去重；返回選中、拒絕與需讓位的 NEW。"""
    now = time.time() if now is None else float(now)
    existing = list(history) + list(active)
    active_bases = {str(x.get("base") or "").upper() for x in active}
    active_new = [x for x in active if str(x.get("portfolio_kind") or x.get("family") or "").upper() == "NEW"]
    ranked = sorted(
        candidates,
        key=lambda x: (
            0 if str(x.get("portfolio_kind") or x.get("family") or "CORE").upper() == "CORE" else 1,
            -number(x.get("priority"), number(x.get("score"), -999.0)),
            str(x.get("base") or x.get("symbol") or ""),
        ),
    )
    chosen: list[dict] = []
    rejected: list[dict] = []
    evict: list[dict] = []
    used = set(active_bases)
    live_count = len(active)
    live_new = len(active_new)
    for source in ranked:
        row = dict(source)
        base = str(row.get("base") or "").upper()
        kind = str(row.get("portfolio_kind") or row.get("family") or "CORE").upper()
        kind = "NEW" if kind in {"NEW", "EXPANSION"} else "CORE"
        row["portfolio_kind"] = kind
        if not base or base in used:
            rejected.append({**row, "V101拒絕原因": "同標的已有持倉或已選中"})
            continue
        reason = risk_block(row, existing + chosen, now)
        if reason:
            rejected.append({**row, "V101拒絕原因": reason})
            continue
        if kind == "NEW":
            if live_count + len(chosen) >= MAX_POSITIONS:
                rejected.append({**row, "V101拒絕原因": "當下已滿五倉"})
                continue
            if live_new + sum(x["portfolio_kind"] == "NEW" for x in chosen) >= MAX_NEW_POSITIONS:
                rejected.append({**row, "V101拒絕原因": "新模組已滿兩倉"})
                continue
        else:
            # 回測只在同一根訊號排序，不會為新訊號強制砍掉已持有部位。
            if live_count + len(chosen) >= MAX_POSITIONS:
                rejected.append({**row, "V101拒絕原因": "當下已滿五倉"})
                continue
        row["V101頂級例外原因"] = elite_reason(row)
        used.add(base)
        chosen.append(row)
    return chosen, rejected, evict
