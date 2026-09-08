#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V010 hotfix entrypoint.

V010 is intentionally a minimal live-management upgrade over the installed V009:
- preserves the complete V009 scanner / signal / ranking / Alpaca behaviour;
- preserves the existing state file and therefore current open positions;
- replaces only TP1 -> cost-break-even stop transition to fix Binance -4130;
- inherited logic still uses TP1 50%, TP2 25%, TP3 remaining, plus fixed 5/10D MA exit.
"""

from __future__ import annotations

import logging
import re

import radar_v009 as previous_version
from v010_tp1_manager import build_live_trader


__version__ = "V010"
app = previous_version.app

# Patch only the live execution manager. Scanner / strategy / policy stay V009.
app.LiveTrader = build_live_trader(app.LiveTrader)

# Make Telegram messages identify the running wrapper as V010.
_v009_tg = app.common.tg


def _v010_tg(message):
    text = re.sub(r"V00[3-9](?:\.\d+)?", "V010", str(message))
    return _v009_tg(text)


app.common.tg = _v010_tg

# Preserve the previous signal format, only relabel the version if present.
_previous_signal_message = app.signal_message


def _v010_signal_message(row):
    return re.sub(
        r"V00[3-9](?:\.\d+)?",
        "V010",
        str(_previous_signal_message(row)),
    )


app.signal_message = _v010_signal_message


def main():
    logging.getLogger("V010").warning(
        "V010 started: TP1 stop replacement hotfix active; strategy remains V009"
    )
    return previous_version.main()


app.main = main


if __name__ == "__main__":
    raise SystemExit(main())
