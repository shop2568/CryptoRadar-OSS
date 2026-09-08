#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.3：V006.2 完整策略 + 長時間掃描穩定性修正。"""

from __future__ import annotations

import logging
import os
import re
import threading

__version__ = "V006.3"

# V006.2 的心跳、完整掃描標記與記憶體回收全部改用 V006.3 runtime。
os.environ["V0062_HEARTBEAT"] = os.getenv(
    "V0063_HEARTBEAT", "/run/cryptoradar-v0063/heartbeat"
)
os.environ["V0062_RUNTIME"] = os.getenv(
    "V0063_RUNTIME", "/run/cryptoradar-v0063"
)

import radar_v006_2 as previous_version

app = previous_version.app
_recycle_scheduled = False


def _current_rss_mb():
    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _safe_recycle_after_cycle():
    """只在整輪結束後回收高記憶體程序，由 systemd 接續啟動。"""
    global _recycle_scheduled
    rss_mb = _current_rss_mb()
    if rss_mb < 400 or _recycle_scheduled:
        return
    _recycle_scheduled = True
    logging.getLogger("V006.3").warning(
        "V006.3 完整掃描後記憶體 %.1fMB，安全重啟釋放記憶體", rss_mb
    )
    timer = threading.Timer(2.0, lambda: os._exit(75))
    timer.daemon = True
    timer.start()


class _V0063LogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        text = re.sub(r"V00[3-6](?:\.\d+)?", "V006.3", str(msg))
        if "本輪掃描完成" in text or "shadow complete" in text:
            previous_version._touch_cycle_complete()
            previous_version._release_unused_memory()
            _safe_recycle_after_cycle()
        return text, kwargs


app.log = _V0063LogAdapter(logging.getLogger("V006.3"), {})

_previous_tg = app.common.tg


def _v006_3_tg(message):
    # 先把所有繼承版本文案統一，避免內層包裝器把 V006.3 改回舊版本。
    text = re.sub(r"V00[3-6](?:\.\d+)?", "V006.3", str(message))
    # 「新增模組最多2倉」只是子模組限制；總持倉上限一直是5倉。
    # 在 Telegram 同一段直接寫清楚，避免被誤認成整體只能開2倉。
    text = text.replace(
        "原核心優先｜新增模組最多2倉",
        "原核心優先｜總持倉最多5倉（其中新增模組最多2倉）",
    )
    text = text.replace("｜最多5倉｜", "｜總持倉最多5倉｜")
    return _previous_tg(text)


app.common.tg = _v006_3_tg


if __name__ == "__main__":
    raise SystemExit(app.main())
