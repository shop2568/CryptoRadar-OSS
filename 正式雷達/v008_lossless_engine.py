#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V008 無損計算適配：V157 規則完全不變，只重用同輪相同 prepared frame。"""

from __future__ import annotations

import v101_live_engine as helpers
import v157_live_engine as base_engine


def causal_candidates(target, raw):
    original = helpers._prepared_now
    cache_key = None
    cache_value = None

    def memoized(frame):
        nonlocal cache_key, cache_value
        key = id(frame)
        if key != cache_key:
            cache_key = key
            cache_value = original(frame)
        return cache_value

    helpers._prepared_now = memoized
    try:
        return base_engine.causal_candidates(target, raw)
    finally:
        helpers._prepared_now = original


discover_targets = base_engine.discover_targets

