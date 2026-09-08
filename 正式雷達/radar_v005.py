#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V005：V004完整實盤 + V139低品質硬排除。"""

from __future__ import annotations

import logging
import os

# 沿用V004狀態與鎖，確保升級時能管理既有持倉且不會雙開雷達。
os.environ["V004_SHADOW"] = os.getenv("V005_SHADOW", "0")
os.environ["V004_ONCE"] = os.getenv("V005_ONCE", "0")
os.environ["V004_SHADOW_LIMIT"] = os.getenv("V005_SHADOW_LIMIT", "80")

import radar_v004 as app
import v005_live_engine as engine
from live_v005 import LiveTrader


app.engine = engine
app.LiveTrader = LiveTrader
app.log = logging.getLogger("V005")

_original_tg = app.common.tg


def _v005_tg(message):
    text = str(message).replace("CryptoRadar V004", "CryptoRadar V005").replace("⚠️ V004", "⚠️ V005")
    text = text.replace(
        "策略：V101 MAX2 乾淨加斜率｜原核心優先｜新增模組最多2倉｜TP1最低4R。",
        "策略：V139低品質硬排除｜原核心優先｜新增模組最多2倉｜TP1最低4R。",
    )
    return _original_tg(text)


app.common.tg = _v005_tg


if __name__ == "__main__":
    raise SystemExit(app.main())
