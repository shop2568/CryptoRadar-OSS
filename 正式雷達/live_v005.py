#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V005實盤層：交易與出場完全沿用V004，只更新正式版本識別。"""

import time

from live_v004 import LiveTrader as V004Trader


class LiveTrader(V004Trader):
    def _client_id(self, prefix):
        return ("V005" + prefix + str(int(time.time() * 1000)))[-32:]
