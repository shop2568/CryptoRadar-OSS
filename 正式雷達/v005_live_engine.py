#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V005：V101即時訊號 + V139低品質交集硬排除。"""

from __future__ import annotations

import pandas as pd

import v101_live_engine as base_engine
from v005_quality_gate import BUILD_LIMIT, RANGE_LIMIT, quality_reason


def causal_candidates(target, raw: pd.DataFrame) -> list[dict]:
    rows = base_engine.causal_candidates(target, raw)
    prepared, now_bar, _ = base_engine._prepared_now(raw)
    if prepared is None or prepared.empty:
        return []
    signal = prepared[prepared.index < now_bar].iloc[-1]
    kept = []
    for source in rows:
        row = dict(source)
        row.setdefault("range20_atr", base_engine.number(signal.get("range20_atr")))
        row.setdefault("volume_build_3v20", base_engine.number(signal.get("volume_build")))
        reason = quality_reason(row)
        row["V005低品質判定"] = reason or "通過"
        row["V005量能堆積門檻"] = BUILD_LIMIT
        row["V005結構寬度門檻"] = RANGE_LIMIT
        if not reason:
            kept.append(row)
    return kept


discover_targets = base_engine.discover_targets
