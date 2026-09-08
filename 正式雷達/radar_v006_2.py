#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.2：V006.1 完整策略 + 停損可靠性修正。"""

from __future__ import annotations

import logging
import os
import ctypes
import gc
from pathlib import Path

__version__ = "V006.2"

# V006.1 的心跳包裝在 import 時建立；先把路徑改到 V006.2 runtime。
os.environ["V0061_HEARTBEAT"] = os.getenv(
    "V0062_HEARTBEAT", "/run/cryptoradar-v0062/heartbeat"
)

import radar_v006_1 as previous_version
from live_v006_2 import LiveTrader

app = previous_version.app
app.LiveTrader = LiveTrader

# 只做執行穩定性管理，不改任何訊號或交易計算。
_RUNTIME = Path(os.getenv("V0062_RUNTIME", "/run/cryptoradar-v0062"))
_CYCLE_COMPLETE = _RUNTIME / "cycle_complete"
_frame_calls = 0


def _touch_cycle_complete():
    try:
        _CYCLE_COMPLETE.touch(exist_ok=True)
    except OSError:
        pass


def _release_unused_memory():
    """回收上一標的留下的 pandas 物件，並盡量把空閒記憶體還給系統。"""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


_previous_frame = app.common.frame


def _v006_2_frame(*args, **kwargs):
    global _frame_calls
    _frame_calls += 1
    if _frame_calls % 25 == 0:
        _release_unused_memory()
    return _previous_frame(*args, **kwargs)


app.common.frame = _v006_2_frame
_touch_cycle_complete()


class _V0062LogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        text = str(msg).replace("V006.1", "V006.2")
        if "本輪掃描完成" in text or "shadow complete" in text:
            _touch_cycle_complete()
            _release_unused_memory()
        return text, kwargs


app.log = _V0062LogAdapter(logging.getLogger("V006.2"), {})

_previous_tg = app.common.tg


def _v006_2_tg(message):
    return _previous_tg(str(message).replace("V006.1", "V006.2"))


app.common.tg = _v006_2_tg


if __name__ == "__main__":
    raise SystemExit(app.main())
