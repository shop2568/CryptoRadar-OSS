#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V005可獨立測試的V139低品質交集判斷。"""

import json
import math
from pathlib import Path


QUALITY = json.loads((Path(__file__).resolve().parent / "v005_quality_gate.json").read_text(encoding="utf-8"))
BUILD_LIMIT = float(QUALITY["volume_build_75pct"])
RANGE_LIMIT = float(QUALITY["range20_atr_25pct"])


def number(value, default=float("nan")):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def quality_reason(row: dict) -> str:
    asset = str(row.get("base") or "").upper()
    if asset in {"BTC", "ETH"}:
        return ""
    build = number(row.get("volume_build_3v20"))
    width = number(row.get("range20_atr"))
    if build > BUILD_LIMIT and width < RANGE_LIMIT:
        return (
            "低品質硬排除：結構太窄且量能堆積過頭"
            f"（量能堆積 {build:.2f}>{BUILD_LIMIT:.2f}；結構寬度 {width:.2f}<{RANGE_LIMIT:.2f} ATR）"
        )
    return ""
