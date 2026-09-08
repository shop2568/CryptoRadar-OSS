#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V007：V006.3 防當機執行層 + V157 研究核心。"""

from __future__ import annotations

import logging
import os
import re

__version__ = "V007"

os.environ["V0063_HEARTBEAT"] = os.getenv("V007_HEARTBEAT", "/run/cryptoradar-v007/heartbeat")
os.environ["V0063_RUNTIME"] = os.getenv("V007_RUNTIME", "/run/cryptoradar-v007")

import radar_v006_3 as previous_version
import v157_live_engine

app = previous_version.app
app.engine = v157_live_engine


class _V007LogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return re.sub(r"V00[3-6](?:\.\d+)?", "V007", str(msg)), kwargs


app.log = _V007LogAdapter(logging.getLogger("V007"), {})
_previous_tg = app.common.tg


def _v007_tg(message):
    text = re.sub(r"V00[3-6](?:\.\d+)?", "V007", str(message))
    text = text.replace(
        "V101 merged market pool",
        "V007三大交易所合併市場｜V157頂級救援",
    )
    text = text.replace("原核心優先｜新增模組最多2倉", "原核心優先｜總持倉最多5倉（其中新增模組最多2倉）")
    return _previous_tg(text)


app.common.tg = _v007_tg


if __name__ == "__main__":
    raise SystemExit(app.main())
