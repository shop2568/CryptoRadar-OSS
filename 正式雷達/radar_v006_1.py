#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.1：V005 完整策略 + V006.1 實盤層。

V006.1：
- 實盤交易層使用 live_v006_1.LiveTrader。
- 全倉固定 20 倍；單筆停損風險 1%。
- Telegram 統一顯示 V006.1 / 全倉20倍 / 單筆停損風險1%。
- 相容 V004 / V005 / V006 舊版本與 3x / 3倍舊文案。
- Shadow / Once / Shadow Limit 沿用 V006 環境變數介面。
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

__version__ = "V006.1"

# 對外維持 V006_* 環境變數，內部傳給 radar_v005 -> radar_v004。
os.environ["V005_SHADOW"] = os.getenv("V006_SHADOW", "0")
os.environ["V005_ONCE"] = os.getenv("V006_ONCE", "0")
os.environ["V005_SHADOW_LIMIT"] = os.getenv("V006_SHADOW_LIMIT", "80")

import radar_v005 as previous_version
from live_v006_1 import LiveTrader

app = previous_version.app
app.LiveTrader = LiveTrader

# 只供 systemd 健康監控使用，不參與任何訊號、排倉或交易判斷。
_HEARTBEAT = Path(os.getenv("V0061_HEARTBEAT", "/run/cryptoradar-v0061/heartbeat"))


def _touch_heartbeat():
    try:
        _HEARTBEAT.touch(exist_ok=True)
    except OSError:
        # 健康檔失敗不能影響雷達交易主流程。
        pass


_previous_frame = app.common.frame


def _heartbeat_frame(*args, **kwargs):
    """行情讀取前後更新健康時間；回傳內容完全沿用原函式。"""
    _touch_heartbeat()
    result = _previous_frame(*args, **kwargs)
    _touch_heartbeat()
    return result


app.common.frame = _heartbeat_frame
_touch_heartbeat()


class _V0061LogAdapter(logging.LoggerAdapter):
    """統一繼承引擎的內部版本名稱，不影響任何交易邏輯。"""

    def process(self, msg, kwargs):
        text = str(msg)
        text = text.replace("CryptoRadar V005", "CryptoRadar V006.1")
        text = text.replace("CryptoRadar V004", "CryptoRadar V006.1")
        text = re.sub(r"CryptoRadar V006(?!\.\d)", "CryptoRadar V006.1", text)
        text = text.replace("V004", "V006.1").replace("V005", "V006.1")
        text = re.sub(r"V006(?!\.\d)", "V006.1", text)
        return text, kwargs


app.log = _V0061LogAdapter(logging.getLogger("V006.1"), {})

_previous_tg = app.common.tg


def _v006_1_tg(message):
    """將所有繼承的 Telegram 舊版本／舊槓桿文案統一為 V006.1。"""
    text = str(message)

    # 啟動通知只呈現交易所目前持倉數，不再顯示舊版「V004 不接管舊單」文案。
    text = re.sub(
        r"啟動前已有持倉：\s*(\d+)\s*倉[（(]V004不接管舊單[）)]。?",
        r"目前交易所持倉：\1 倉。",
        text,
    )

    # 版本名稱：V004 / V005 / V006 -> V006.1；已是 V006.1 的不重複改寫。
    text = text.replace("CryptoRadar V005", "CryptoRadar V006.1")
    text = text.replace("CryptoRadar V004", "CryptoRadar V006.1")
    text = re.sub(r"CryptoRadar V006(?!\.\d)", "CryptoRadar V006.1", text)

    text = text.replace("⚠️ V005", "⚠️ V006.1")
    text = text.replace("⚠️ V004", "⚠️ V006.1")
    text = re.sub(r"⚠️ V006(?!\.\d)", "⚠️ V006.1", text)

    # 交易通知中可能使用「V004｜」「V005｜」「V006｜」。
    text = text.replace("V004｜", "V006.1｜")
    text = text.replace("V005｜", "V006.1｜")
    text = re.sub(r"V006(?!\.\d)｜", "V006.1｜", text)

    # 槓桿舊文案：全倉 3x / 全倉3x / 全倉 3倍 / 全倉3倍。
    text = re.sub(r"全倉\s*3\s*(?:x|X|倍)", "全倉20倍", text)

    # 單筆風險舊文案。
    text = re.sub(r"單筆(?:完整)?風險\s*1%", "單筆停損風險1%", text)

    return _previous_tg(text)


app.common.tg = _v006_1_tg


if __name__ == "__main__":
    raise SystemExit(app.main())
