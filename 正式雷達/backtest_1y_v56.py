#!/usr/bin/env python3
"""CryptoRadar V56 = proven V39 exchange core + corrected Alpaca 1Y Daily stock research.

Why V56
-------
V42 proved the Alpaca data path works, but its exchange branch was the weaker V40
Lifecycle branch. V56 restores the V39 Structural Ignition exchange logic unchanged
and keeps the corrected V42 Alpaca-underlying daily research.

Branch A — Exchange / crypto
----------------------------
Exact V39-style Structural Ignition:
* Binance / Bitget / BingX;
* crypto remains included;
* HIGH_CONFIDENCE stock tokens remain discovered;
* LONG + SHORT;
* real structural stop;
* >=4R real room / inherited Runner;
* fees/slippage and fixed-risk portfolio rules unchanged.

Branch B — Current stock-token universe -> Alpaca underlying 1Y
---------------------------------------------------------------
* use current HIGH_CONFIDENCE exchange stock-token discovery to get underlyings;
* fetch 365 research days of Alpaca native 15m regular-session history + warmup;
* completed DAILY structure creates signals;
* next available 15m open executes;
* prior 4 completed daily highs/lows = break structure;
* prior 3 completed daily lows/highs +/-0.25 daily ATR = initial stop;
* real stock H1/D1 target >=4R first; Runner only as fallback;
* one structural re-entry after a true STOP.

Instead of hard-coding a conclusion from V42, V56 evaluates THREE US-stock portfolio
variants from the SAME candidate set:
1) ALL signals;
2) FIRST_PULLBACK_ONLY, LONG + SHORT;
3) SHORT_PULLBACK_OR_REENTRY.

This lets the backtest test the V42 observation without silently overfitting the
signal generator. Underlying history before token listing remains research proxy data.


V56 inherited portfolio/output features
---------------------------------------
* V39/V43 core remains protected at 3 positions with its original direction throttles;
* positions 4/5 are optional Elite Expansion only;
* same-group de-duplication applies to Elite Expansion, not to the protected core;
* keep raw LONG/SHORT win-rate outputs plus group catalogs and grouped audit files.

Signal generation, stops, >=4R requirement, Runner, fees/slippage, 1% risk per trade,
universe, timeframes and historical period are unchanged.

V56 research change
-------------------
The V49 result showed that a hard same-sector veto plus unrestricted extra signals can
delete proven winners while filling capacity with marginal trades. V56 therefore:
* preserves the exact V39/V43 core portfolio selector;
* treats positions 4/5 as optional Elite Expansion slots;
* filters expansion by pre-entry setup/regime/geometry only;
* applies same-group de-duplication to expansion trades so it cannot veto the core;
* keeps 1% risk, costs, stops, Runner and no-time-exit unchanged.

The Elite gate is a research hypothesis from the same 1Y sample and must be validated
by a fresh backtest / forward sample before live use.

V56 post-TP1 trend-hold
-----------------------
For REAL structural TP1 trades, TP1 realizes 50% of the position immediately.
The remaining 50% becomes a trend-following remainder.
BEFORE TP1 there is NO profit-protection ladder: the original structural stop stays fixed.
If TP1 is not reached, the trade can only exit at the original structural stop (or remain open at the research-window end).
After TP1:
* TP1 closes 50% immediately; the remaining 50% follows the selected MA;
* strong aligned daily trend uses the prior-completed-day 10-day MA;
* otherwise uses the prior-completed-day 5-day MA;
* LONG exits after a completed 15m close falls below the selected MA;
* SHORT exits after a completed 15m close rises above the selected MA;
* execution is the NEXT 15m open (no look-ahead);
* no time exit is added.
Synthetic Price Discovery Runner trades retain the existing V32 Runner unchanged.

V56 complete repair
-------------------
* fixes the V53 ndarray-in-DataFrame.attrs cache crash without changing any trading rule;
* cache is now external + weak-reference based, so Pandas copies cannot compare NumPy arrays;
* cache entries are released with each market frame to protect the 1GB server memory;

V56 speed + same-entry exit comparison
---------------------------------------
* cache completed-day 5D/10D MA arrays once per market frame instead of rebuilding them for every trade;
* do NOT change entry signals, stops, risk, fees, core/Elite allocation, LONG/SHORT, crypto, or US-stock logic;
* keep the current research exit as TP1 50% + MA-held 50%;
* additionally calculate a SAME-ENTRY observational control: REAL TP1 hit -> 100% exit at TP1;
* report TP2/TP3 reach statistics for the 50% MA-held remainder without using TP2/TP3 as exits;
* the A/B report does not alter portfolio occupancy or entry selection, so both methods are compared on the exact same selected entries.

V56 stop-rule correction
------------------------
* removes the inherited pre-TP1 profit-protection ladder for REAL structural TP1 trades;
* before TP1, the original structural stop never moves;
* if TP1 is not reached, the trade exits only at that original stop (or stays open at the end);
* TP1 50% + 5D/10D MA remainder remains unchanged AFTER TP1;
* Price Discovery Runner also removes its adaptive profit-protection/trailing ladder; because it has no real TP1, it keeps the original structural stop for the whole research window.

V56 research additions
----------------------
* fixes the inherited empty-frame market_heat_ok crash: empty/unsupported exchange frames are recorded as SKIP instead of becoming errors;
* US stocks fetch SPY / QQQ / DIA as no-look-ahead proxies for S&P 500 / Nasdaq / Dow trend;
* each US-stock candidate is labeled with the three index states and aggregate market regime using only completed prior-day data;
* compare BASELINE vs STRONG-3/3-VETO vs MAJORITY-2/3-TREND portfolio variants from the same raw candidate set;
* index filters are research variants only: the original V54-style FORMAL_COMBINATION remains available unchanged for clean comparison;
* add an observational CORE quality report that asks which protected core trades would also pass the existing Elite pre-entry gate; it does not delete core trades in V56.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import sqlite3
import sys
import weakref
from itertools import groupby
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

ROOT = Path.home() / "CryptoRadar"
V32_PATH = ROOT / "backtest_1y_v32.py"
if not V32_PATH.exists():
    V32_PATH = Path(__file__).with_name("backtest_1y_v32.py")
if not V32_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v32.py；請把V32與V56放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v32_components_v56", V32_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V32：{V32_PATH}")
v32 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v32
spec.loader.exec_module(v32)

engine = v32.engine
v22 = v32.v22

# V56 research portfolio:
# 1) preserve the proven V39/V43 3-slot CORE selection exactly;
# 2) add at most TWO simultaneous ELITE expansion positions;
# 3) expansion positions cannot duplicate an active asset group;
# 4) no separate LONG/SHORT cap is imposed on ELITE expansion positions.
#
# This deliberately avoids letting ordinary candidates fill slots 4/5.
engine.MAX_POSITIONS = 5
if hasattr(v22, "v19"):
    v22.v19.STOCK_MAX_POSITIONS = 5

V56_CORE_MAX_POSITIONS = 3
V56_CORE_MAX_SAME_DIRECTION = 2
V56_CORE_MAX_SAME_DIRECTION_ENTRIES_6H = 2
V56_MAX_EXPANSION_POSITIONS = 2
V56_MAX_ACTIVE_PER_GROUP = 1

# V56 post-TP1 trend-hold:
# - REAL TP1 trades: before TP1, NO profit protection. Keep the ORIGINAL structural stop unchanged.
# - Price Discovery Runner: also keep the ORIGINAL structural stop unchanged for the whole trade.
# - If TP1 is not reached: only the original structural stop may close the trade.
# - After TP1: keep holding until the short-term trend weakens.
# - Strong aligned daily trend -> 10-day line; otherwise -> 5-day line.
# - Daily MA uses ONLY prior completed daily closes (shifted one day; no look-ahead).
# - Weakness is confirmed by a completed 15m close; fill is NEXT 15m open.
V56_POST_TP1_FAST_MA_DAYS = 5
V56_POST_TP1_SLOW_MA_DAYS = 10
V56_POST_TP1_USE_ADAPTIVE_5_10 = True
V56_TP1_TAKE_PROFIT_FRACTION = 0.50
V56_TREND_REMAINDER_FRACTION = 0.50
assert abs(V56_TP1_TAKE_PROFIT_FRACTION + V56_TREND_REMAINDER_FRACTION - 1.0) < 1e-12


# Keep a pointer to the inherited V39-style allocator before V56 replaces it at runtime.
V56_ORIGINAL_SELECT_PORTFOLIO = engine.select_portfolio

# Safety checks: signal generation/risk model remain unchanged.
assert int(engine.MAX_POSITIONS) == 5
assert float(engine.RISK_PER_TRADE_PCT) == 1.0
if hasattr(v22, "v19"):
    assert int(v22.v19.STOCK_MAX_POSITIONS) == 5
    assert float(v22.v19.STOCK_RISK_PER_TRADE_PCT) == 1.0

OUTPUT_DIR = ROOT / "backtest_1y_v56_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"
ALPACA_D1_OUTPUT_DIR = OUTPUT_DIR / "us_stocks_alpaca_d1"

SCHEME_V56 = "V56_V32_CORE_PLUS_STRUCTURAL_IGNITION_FAST"
V56_SCHEME_CONFIG = dict(v32.V32_SCHEME_CONFIG)

# Structure module only. V32 core settings remain in v32 unchanged.
STRUCTURE_BREAKOUT_LOOKBACK_4H = 4
STRUCTURE_STOP_LOOKBACK_4H = 3
STRUCTURE_STOP_ATR_BUFFER_4H = 0.25
STRUCTURE_DIRECT_MAX_EXTENSION_ATR = 0.55
STRUCTURE_ENTRY_MAX_EXTENSION_ATR = 0.70
STRUCTURE_PULLBACK_TOL_ATR = 0.20
STRUCTURE_PULLBACK_MAX_BARS = 32          # 12h on 15m
STRUCTURE_MIN_STOP_PCT = 0.80
STRUCTURE_MAX_STOP_PCT = 8.00
STRUCTURE_MIN_ROOM_R = 4.0
STRUCTURE_IGNITION_MIN_BODY_ATR = 0.20
STRUCTURE_IGNITION_MIN_CLOSE_POS = 0.60
STRUCTURE_IGNITION_MAX_COUNTER_EMA_SLOPE_ATR = 0.10
STRUCTURE_MIN_BREAK_DISTANCE_ATR = 0.05
STRUCTURE_REARM_BARS = 32                 # avoid repeated signals from the same level

_STRUCTURE_AUDIT: list[dict[str, Any]] = []
_PER_MARKET_STRUCTURE: list[dict[str, Any]] = []


def _write_union_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def _period_name(signal_time: pd.Timestamp, research_start: pd.Timestamp) -> str:
    elapsed = max(0.0, (signal_time - research_start).total_seconds())
    number = min(4, int(elapsed // (91 * 86400)) + 1)
    return f"P{number}"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return float("nan")



# ======================================================================================
# V56 group classification + portfolio de-duplication
# ======================================================================================

# Existing live radar already used AI / Meme / 主流 / 美股合約 sector de-duplication.
# V56 expands that idea for research. Unknown assets get a UNIQUE fallback group so
# an uncertain classification never blocks an unrelated asset.

CRYPTO_GROUPS: dict[str, set[str]] = {
    "AI智能": {
        "AI", "AIOZ", "AKT", "ARKM", "FET", "GRT", "IO", "MIRA", "NEAR",
        "NMR", "OCEAN", "RENDER", "RNDR", "RLC", "TAO", "THETA", "WLD",
    },
    "Meme迷因": {
        "BABY", "BOME", "BONK", "BRETT", "BROCCOLIF3B", "DOGE", "FLOKI",
        "MELANIA", "MEME", "PEOPLE", "PEPE", "PENGU", "PNUT", "SHIB",
        "TOSHI", "TRUMP", "TST", "WIF",
    },
    "Layer1公鏈": {
        "ADA", "APT", "ATOM", "AVAX", "BERA", "BNB", "DOT", "DYM", "EGLD",
        "FLOW", "INJ", "KAS", "LUNC", "NEAR", "SEI", "SAGA", "SOL", "SUI",
        "TON", "TRX", "XRP",
    },
    "Layer2擴容": {
        "ARB", "BLAST", "IMX", "MANTA", "METIS", "OP", "STRK", "ZK", "ZKSYNC",
    },
    "DeFi/DEX": {
        "1INCH", "AAVE", "CAKE", "COMP", "CRV", "DEXE", "DYDX", "ENA", "GMX",
        "JUP", "KMNO", "KNC", "MAV", "MKR", "PENDLE", "RAY", "RUNE", "SKY",
        "SUSHI", "UNI", "WLFI",
    },
    "RWA實體資產": {
        "CFG", "MPL", "OM", "ONDO", "POLYX", "RSR",
    },
    "遊戲/元宇宙": {
        "ACE", "AXS", "CARV", "GALA", "GMT", "MANA", "MOCA", "PIXEL", "SAND", "YGG",
    },
    "隱私幣": {"DASH", "XMR", "ZEC"},
    "預言機/基礎設施": {
        "API3", "AR", "BICO", "ENS", "FIL", "LINK", "PYTH", "QNT", "SAFE", "SSV",
    },
    "交易所/錢包": {
        "BGB", "CRO", "GT", "OKB", "TWT",
    },
}

# BTC/ETH are deliberately independent rather than sharing "主流" so they do not
# accidentally block each other under the one-sector rule.
CRYPTO_SPECIAL_GROUP = {"BTC": "BTC獨立", "ETH": "ETH獨立"}

STOCK_GROUPS: dict[str, set[str]] = {
    "半導體/晶片": {
        "ADI", "AEHR", "ALAB", "AMAT", "AMD", "AMKR", "ARM", "ASML", "ASX",
        "AVGO", "AXTI", "CRDO", "DRAM", "EUV", "INTC", "INTW", "KLAC", "LRCX",
        "MRVL", "MU", "MUU", "MVLL", "NVDA", "QCOM", "SNDK", "SMH", "SOXL",
        "SOXS", "TER", "TOKYOEL", "TSEM", "TSM", "TSMU", "TXN", "UMC",
    },
    "光通訊/伺服器硬體": {
        "AAOI", "AAPL", "CIEN", "COHR", "CSCO", "DELL", "FLEX", "GLW", "HPE",
        "HPQ", "KOPN", "LITE", "LWLG", "NOK", "PENG", "SMCI", "STX", "VRT", "WDC",
        "XIAOMI",
    },
    "AI/軟體/雲端": {
        "ADBE", "APLD", "APP", "BB", "CRM", "CRWD", "CRWV", "FIG", "IBM",
        "IBMR", "IGV", "MSFT", "NBIS", "NOW", "ORCL", "PANW", "PLTR", "SNOW",
        "SNXX", "SOUN", "TEAM", "TWLO", "ZETA", "ZM",
    },
    "量子運算": {"ARQQ", "IONQ", "QBTS", "QUBT", "RGTI"},
    "網路/媒體/電商": {
        "AMZN", "BABA", "BZ", "DIS", "EBAY", "GOOGL", "META", "NFLX", "RDDT",
        "SNAP", "SONY", "TTD",
    },
    "金融/銀行/支付": {
        "AGNC", "BAC", "BX", "GS", "HSBC", "IYF", "JPM", "MA", "MS", "NU",
        "OWL", "PAYP", "PYPL", "RY", "SOFI", "V", "XLF",
    },
    "加密概念股": {
        "BITO", "BMNR", "CIFR", "CLSK", "COIN", "CONL", "CRCL", "ETHA", "HIVE",
        "HOOD", "IREN", "MSTR", "STRC", "WULF",
    },
    "醫療/生技": {
        "ABBV", "BSX", "HIMS", "JNJ", "LLY", "MRK", "NVO", "NVS", "PFE",
        "SMMT", "TEM", "XBI",
    },
    "能源/油氣": {
        "BATL", "COP", "CVX", "LNG", "OXY", "PBR", "SHELL", "SLB", "USO",
        "VG", "XLE", "XOM", "XOP",
    },
    "核能/電力/儲能": {
        "BE", "CEG", "DNN", "ENVX", "EOSE", "FLNC", "GEV", "OKLO", "SMR",
        "URNM", "XLU",
    },
    "航空/太空/國防": {
        "AAL", "ACHR", "ASTS", "AVAV", "FLY", "HEI", "JBLU", "JOBY", "LMT",
        "LUNR", "NASA", "ONDS", "RCAT", "RDW", "RKLB", "SIDU", "UMAC",
    },
    "汽車/自駕/交通": {
        "AUR", "GRAB", "OUST", "QS", "RACE", "RIVN", "TSLA", "UBER",
    },
    "消費/零售/餐飲/旅遊": {
        "AMC", "CCL", "CL", "CMG", "COST", "DKNG", "GME", "HD", "KO", "MCD",
        "MGM", "NCLH", "NKE", "PG", "PM", "WEN", "WMT",
    },
    "工業/材料/礦業": {
        "CAT", "CDE", "CRML", "GDX", "GE", "MP", "SLV", "USAR", "VALE",
    },
    "通訊": {"T"},
    "遊戲娛樂": {"TTWO"},
    "房地產": {"OPEN"},
    "大盤/區域ETF": {
        "EEM", "EFA", "EWJ", "EWT", "EWY", "EWZ", "GGLL", "IEMG", "IWM",
        "KORU", "KSTR", "QQQ", "SCHD", "SPCX", "SPY", "SPYM", "STXX", "SQQQ",
        "TQQQ", "TZA", "VWO", "XLK", "XLP",
    },
    "債券/現金ETF": {
        "BIL", "HYG", "LQD", "SGOV", "SKHY", "TBT", "TLT", "TMF", "USHY",
    },
    "波動率ETF": {"UVXY"},
}

_V56_EXCHANGE_GROUP_REJECTIONS: dict[str, int] = {
    "elite_filtered": 0,
    "expansion_same_group": 0,
    "expansion_capacity": 0,
}
_V56_EXPANSION_AUDIT: list[dict[str, Any]] = []


def _v56_stock_underlyings_now() -> set[str]:
    meta = getattr(v22, "_V22_TOKEN_META", {}) or {}
    return {
        str(row.get("underlying") or "").strip().upper()
        for row in meta.values()
        if str(row.get("confidence") or "") == "HIGH_CONFIDENCE"
        and str(row.get("underlying") or "").strip()
    }


def _v56_group_from_sets(base: str, groups: dict[str, set[str]], prefix: str = "") -> str | None:
    for group_name, members in groups.items():
        if base in members:
            return f"{prefix}{group_name}" if prefix else group_name
    return None


def v56_asset_group(
    base: str,
    exchange: str = "",
    symbol: str = "",
    force_stock: bool = False,
) -> str:
    b = str(base or "").strip().upper()
    if b.startswith("1000") and len(b) > 4:
        b = b[4:]

    if b in CRYPTO_SPECIAL_GROUP and not force_stock:
        return CRYPTO_SPECIAL_GROUP[b]

    stock_now = _v56_stock_underlyings_now()
    stock_match = force_stock or b in stock_now
    if stock_match:
        group = _v56_group_from_sets(b, STOCK_GROUPS, "美股-")
        return group if group else f"美股-其他-{b}"

    # Some exchange TradFi wrappers may not be Alpaca-US underlyings. Known stock-style
    # tickers still receive stock grouping; otherwise crypto grouping is attempted.
    stock_group = _v56_group_from_sets(b, STOCK_GROUPS, "美股-")
    if stock_group is not None:
        return stock_group

    crypto_group = _v56_group_from_sets(b, CRYPTO_GROUPS)
    if crypto_group is not None:
        return crypto_group

    return f"其他-{b}"


def _v56_raw_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(str(r.get("outcome")) == "WIN" for r in rows)
    losses = sum(str(r.get("outcome")) == "LOSS" for r in rows)
    opens = sum(str(r.get("outcome")) == "OPEN" for r in rows)
    closed = wins + losses
    return {
        "signals": len(rows),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "closed": closed,
        "win_rate_pct": round(wins / closed * 100.0, 3) if closed else 0.0,
        "total_net_r": round(sum(float(r.get("net_r") or 0.0) for r in rows), 5),
    }


def _v56_structure_audit_map() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in _STRUCTURE_AUDIT:
        key = (
            str(row.get("exchange") or ""),
            str(row.get("symbol") or ""),
            str(row.get("direction") or ""),
            str(row.get("entry_time") or ""),
        )
        out[key] = row
    return out


def _v56_candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("exchange") or ""),
        str(row.get("symbol") or ""),
        str(row.get("direction") or ""),
        str(row.get("entry_time") or ""),
    )


def _v56_aligned_pressure(row: dict[str, Any]) -> float:
    p = float(row.get("confirmation_pressure") or 0.0)
    return p if str(row.get("direction")) == "LONG" else -p


def _v56_elite_expansion_gate(row: dict[str, Any], audit: dict[str, Any] | None) -> tuple[bool, str]:
    """
    PRE-ENTRY-only research gate discovered from V39/V44/V49 diagnostics.
    It is intentionally applied ONLY to expansion slots, never to the protected V39 core.

    Goal:
      * preserve all V39 core selections;
      * slots 4/5 are optional, not a target to fill;
      * reject broad low-quality signal clusters;
      * admit only structure/regime combinations that showed better asymmetry.
    """
    direction = str(row.get("direction") or "")
    setup = str((audit or {}).get("setup_type") or "V32_CORE")
    regime = str((audit or {}).get("regime_audit") or "")
    rr1 = float(row.get("rr1") or 0.0)
    vol = float(row.get("confirmation_volume") or 0.0)
    pressure = _v56_aligned_pressure(row)
    signal_range = float(row.get("signal_range_atr") or 0.0)
    stop_pct = float(row.get("stop_pct") or 0.0)
    score = float(row.get("score") or 0.0)
    extension = float((audit or {}).get("entry_extension_atr") or 999.0)
    heat = float((audit or {}).get("market_heat_ratio_audit_only") or 0.0)

    # V32 core candidate that the original 3-slot portfolio could not take:
    # require meaningful structural stop room instead of a very tight stop.
    if setup == "V32_CORE":
        ok = stop_pct >= 2.40
        return ok, "ELITE_V32_CORE_WIDE_STRUCTURE" if ok else "REJECT_V32_CORE_TIGHT_STOP"

    if direction == "LONG":
        # Pullback long had materially better research behaviour than direct chase.
        if setup == "FIRST_PULLBACK_RECLAIM" and regime in {"RANGE", "TRANSITION"}:
            return True, "ELITE_LONG_FIRST_PULLBACK"

        # Direct breakout long is the clearest garbage source, so it gets a strict gate.
        if (
            setup == "DIRECT_BREAKOUT"
            and regime == "TRANSITION"
            and pressure >= 0.20
            and 1.20 <= vol <= 3.50
            and signal_range <= 0.35
            and 2.00 <= stop_pct <= 4.50
        ):
            return True, "ELITE_LONG_DIRECT_BREAKOUT"
        return False, "REJECT_LONG_NON_ELITE"

    # SHORT direct breakdown — RANGE was the strongest broad structural bucket.
    if (
        setup == "DIRECT_BREAKDOWN"
        and regime == "RANGE"
        and 4.0 <= rr1 <= 8.0
        and signal_range <= 0.45
        and stop_pct >= 4.50
        and score <= 0.30
        and 1.0 <= vol <= 8.0
    ):
        return True, "ELITE_SHORT_BREAKDOWN_RANGE"

    # TRANSITION / TREND_BEAR need a much tighter quality gate.
    if (
        setup == "DIRECT_BREAKDOWN"
        and regime in {"TRANSITION", "TREND_BEAR"}
        and 4.0 <= rr1 <= 8.50
        and stop_pct >= 5.0
        and signal_range <= 0.45
        and 1.0 <= vol <= 8.0
        and score <= 0.35
    ):
        return True, "ELITE_SHORT_BREAKDOWN_TRANSITION"

    # Short pullback: only transition regime, tight extension and non-overheated heat.
    if (
        setup == "FIRST_PULLBACK_RECLAIM"
        and regime == "TRANSITION"
        and signal_range <= 0.28
        and score <= 0.35
        and heat <= 0.90
    ):
        return True, "ELITE_SHORT_FIRST_PULLBACK"

    return False, "REJECT_SHORT_NON_ELITE"


def _v56_core_selection(
    connection: Any,
    scheme: str,
    period: str | None,
) -> list[dict[str, Any]]:
    """Run the inherited allocator with the exact V39/V43 portfolio constraints."""
    old_max = engine.MAX_POSITIONS
    old_same = engine.MAX_SAME_DIRECTION_POSITIONS
    old_recent = engine.MAX_SAME_DIRECTION_ENTRIES_6H
    try:
        engine.MAX_POSITIONS = V56_CORE_MAX_POSITIONS
        engine.MAX_SAME_DIRECTION_POSITIONS = V56_CORE_MAX_SAME_DIRECTION
        engine.MAX_SAME_DIRECTION_ENTRIES_6H = V56_CORE_MAX_SAME_DIRECTION_ENTRIES_6H
        return V56_ORIGINAL_SELECT_PORTFOLIO(connection, scheme, period)
    finally:
        engine.MAX_POSITIONS = old_max
        engine.MAX_SAME_DIRECTION_POSITIONS = old_same
        engine.MAX_SAME_DIRECTION_ENTRIES_6H = old_recent


def select_portfolio_v56(connection: Any, scheme: str, period: str | None = None) -> list[dict[str, Any]]:
    """
    V56 = protected V39 core + optional elite expansion.

    CORE selection is computed first and cannot be displaced by extra signals.
    Expansion positions:
      * max 2 simultaneously;
      * no LONG/SHORT hard cap;
      * same group cannot overlap an ACTIVE core/expansion position at the moment of entry;
      * must pass the pre-entry elite gate;
      * 24h symbol cooldown and max 1 new expansion per timestamp remain.

    The final selected rows are re-sized at 1% risk using the combined realized-equity path.
    """
    core = _v56_core_selection(connection, scheme, period)
    core_keys = {_v56_candidate_key(row) for row in core}

    sql = """
        SELECT exchange, symbol, base, direction, signal_time, entry_time,
               exit_time, period, scheme, entry, stop, tp1, tp2, tp3,
               rr1, rr2, rr3, exit_price, score, outcome, exit_reason,
               timed_out, gross_r, cost_r, net_r, confirmation_volume,
               signal_range_atr, confirmation_pressure, nearest_rr, stop_atr,
               stop_pct, entry_delay_bars, holding_bars, mfe_r, mae_r
        FROM candidates
        WHERE scheme = ?
    """
    params: list[Any] = [scheme]
    if period is not None:
        sql += " AND period = ?"
        params.append(period)
    score_order = engine.SCHEME_CONFIGS[scheme]["score_order"]
    sql += f" ORDER BY entry_time ASC, score {score_order}"

    cursor = connection.execute(sql, params)
    candidates = [engine.row_dict(cursor, row) for row in cursor]
    audit_map = _v56_structure_audit_map()

    # Prepare protected core windows.
    core_windows: list[dict[str, Any]] = []
    for row in core:
        item = dict(row)
        item["_entry_ts"] = pd.Timestamp(item["entry_time"])
        item["_exit_ts"] = pd.Timestamp(item["exit_time"])
        item["asset_group"] = v56_asset_group(
            str(item.get("base") or ""),
            str(item.get("exchange") or ""),
            str(item.get("symbol") or ""),
        )
        item["portfolio_tier"] = "CORE_V39"
        item["elite_reason"] = "PROTECTED_V39_CORE"
        core_windows.append(item)

    expansion: list[dict[str, Any]] = []
    active_expansion: list[dict[str, Any]] = []
    last_expansion_by_base: dict[str, pd.Timestamp] = {}

    for entry_time, grouped_rows in groupby(candidates, key=lambda item: item["entry_time"]):
        entry_ts = pd.Timestamp(entry_time)
        active_expansion = [
            trade for trade in active_expansion
            if pd.Timestamp(trade["exit_time"]) > entry_ts
        ]
        active_core = [
            trade for trade in core_windows
            if trade["_entry_ts"] <= entry_ts < trade["_exit_ts"]
        ]
        active_groups = {str(t["asset_group"]) for t in active_core}
        active_groups |= {str(t["asset_group"]) for t in active_expansion}
        active_bases = {str(t["base"]) for t in active_core}
        active_bases |= {str(t["base"]) for t in active_expansion}

        opened = 0
        seen_bases: set[str] = set()
        for row in grouped_rows:
            key = _v56_candidate_key(row)
            if key in core_keys:
                continue

            base = str(row["base"])
            if base in seen_bases:
                continue
            seen_bases.add(base)

            audit = audit_map.get(key)
            elite_ok, elite_reason = _v56_elite_expansion_gate(row, audit)
            if not elite_ok:
                _V56_EXCHANGE_GROUP_REJECTIONS["elite_filtered"] += 1
                continue

            previous = last_expansion_by_base.get(base)
            if previous is not None and entry_ts < previous + pd.Timedelta(hours=engine.SYMBOL_COOLDOWN_HOURS):
                continue
            if base in active_bases:
                continue

            if len(active_expansion) >= V56_MAX_EXPANSION_POSITIONS:
                _V56_EXCHANGE_GROUP_REJECTIONS["expansion_capacity"] += 1
                continue
            if opened >= engine.MAX_NEW_TRADES_PER_BAR:
                continue

            group_name = v56_asset_group(
                base,
                exchange=str(row.get("exchange") or ""),
                symbol=str(row.get("symbol") or ""),
            )
            if group_name in active_groups:
                _V56_EXCHANGE_GROUP_REJECTIONS["expansion_same_group"] += 1
                continue

            item = dict(row)
            item["asset_group"] = group_name
            item["portfolio_tier"] = "ELITE_EXPANSION"
            item["elite_reason"] = elite_reason
            if audit:
                item["setup_type"] = audit.get("setup_type")
                item["regime_audit"] = audit.get("regime_audit")
                item["entry_extension_atr"] = audit.get("entry_extension_atr")
                item["market_heat_ratio_audit_only"] = audit.get("market_heat_ratio_audit_only")

            expansion.append(item)
            active_expansion.append(item)
            last_expansion_by_base[base] = entry_ts
            active_groups.add(group_name)
            active_bases.add(base)
            opened += 1

            if period is None:
                _V56_EXPANSION_AUDIT.append(dict(item))

    # Merge protected core + expansion and recompute 1%-risk compounding from scratch.
    merged = [dict(row) for row in core_windows] + [dict(row) for row in expansion]
    merged.sort(key=lambda row: (pd.Timestamp(row["entry_time"]), float(row.get("score") or 0.0)))

    equity = engine.STARTING_EQUITY
    active: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []

    for entry_time, grouped_rows in groupby(merged, key=lambda item: item["entry_time"]):
        completed = sorted(
            (trade for trade in active if pd.Timestamp(trade["exit_time"]) <= pd.Timestamp(entry_time)),
            key=lambda trade: pd.Timestamp(trade["exit_time"]),
        )
        for trade in completed:
            equity += float(trade["profit_usdt"])
        active = [
            trade for trade in active
            if pd.Timestamp(trade["exit_time"]) > pd.Timestamp(entry_time)
        ]

        for item in grouped_rows:
            risk_usdt = max(0.0, equity) * engine.RISK_PER_TRADE_PCT / 100.0
            item["risk_usdt"] = risk_usdt
            item["profit_usdt"] = (
                float(item["net_r"]) * risk_usdt
                if item["outcome"] != "OPEN" else 0.0
            )
            item["unrealized_profit_usdt"] = (
                float(item["net_r"]) * risk_usdt
                if item["outcome"] == "OPEN" else 0.0
            )
            item["equity_at_entry"] = equity
            # Internal helper fields must not leak into CSV writers.
            item.pop("_entry_ts", None)
            item.pop("_exit_ts", None)
            selected.append(item)
            active.append(item)

    return selected


def _v56_exchange_raw_direction_stats(direction: str) -> dict[str, Any]:
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"不支援方向：{direction}")

    db = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    empty = {"signals": 0, "wins": 0, "losses": 0, "open": 0, "closed": 0, "win_rate_pct": 0.0}
    if not db.exists():
        return empty

    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            """
            SELECT exchange, symbol, base, outcome, net_r
            FROM candidates
            WHERE scheme = ? AND direction = ?
            ORDER BY entry_time
            """,
            (SCHEME_V56, direction),
        )
        rows = [
            {
                "exchange": r[0],
                "symbol": r[1],
                "base": r[2],
                "outcome": r[3],
                "net_r": r[4],
                "asset_group": v56_asset_group(r[2], r[0], r[1]),
            }
            for r in cur.fetchall()
        ]
    finally:
        con.close()

    metrics = _v56_raw_metrics(rows)
    by_group: list[dict[str, Any]] = []
    for group_name in sorted({str(r["asset_group"]) for r in rows}):
        part = [r for r in rows if r["asset_group"] == group_name]
        row = {"asset_group": group_name}
        row.update(_v56_raw_metrics(part))
        by_group.append(row)

    if direction == "LONG":
        group_path = MARKETS_OUTPUT_DIR / "raw_long_winrate_by_group_v56.csv"
        summary_path = MARKETS_OUTPUT_DIR / "raw_long_winrate_v56.json"
    else:
        group_path = MARKETS_OUTPUT_DIR / "raw_short_winrate_by_group_v56.csv"
        summary_path = MARKETS_OUTPUT_DIR / "raw_short_winrate_v56.json"

    _write_union_csv(group_path, by_group)
    summary_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def _v56_write_exchange_group_outputs() -> None:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V56.lower()}.csv"
    rows = v22._read_csv_rows(trade_path)
    grouped: list[dict[str, Any]] = []
    catalog: dict[str, dict[str, Any]] = {}
    for row in rows:
        base = str(row.get("base") or "")
        group_name = v56_asset_group(
            base,
            str(row.get("exchange") or ""),
            str(row.get("symbol") or ""),
        )
        out = dict(row)
        out["asset_group"] = group_name
        grouped.append(out)
        catalog[f"{row.get('exchange')}|{row.get('symbol')}|{base}"] = {
            "exchange": row.get("exchange"),
            "symbol": row.get("symbol"),
            "base": base,
            "asset_group": group_name,
        }
    _write_union_csv(MARKETS_OUTPUT_DIR / "trades_with_groups_v56.csv", grouped)
    _write_union_csv(MARKETS_OUTPUT_DIR / "group_catalog_exchange_v56.csv", list(catalog.values()))



def _v56_combination_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(str(r.get("outcome")) == "WIN" for r in rows)
    losses = sum(str(r.get("outcome")) == "LOSS" for r in rows)
    opens = sum(str(r.get("outcome")) == "OPEN" for r in rows)
    closed = wins + losses
    return {
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "closed": closed,
        "win_rate_pct": round(wins / closed * 100.0, 3) if closed else 0.0,
        "net_profit_usdt": round(sum(float(r.get("profit_usdt") or 0.0) for r in rows), 4),
        "unrealized_profit_usdt": round(
            sum(float(r.get("unrealized_profit_usdt") or 0.0) for r in rows), 4
        ),
        "total_net_r": round(sum(float(r.get("net_r") or 0.0) for r in rows), 5),
    }


def _v56_write_exchange_combination_outputs() -> dict[str, Any]:
    """
    FORMAL COMBINATION:
      1. preserve V39/V43 core selection;
      2. extra positions must pass Elite Expansion gate;
      3. Elite Expansion cannot duplicate an active group;
      4. core 3 + expansion 2 = max total 5;
      5. expansion has no separate LONG/SHORT cap;
      6. risk/cost/exit rules remain unchanged.
    """
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V56.lower()}.csv"
    rows = v22._read_csv_rows(trade_path)
    combo_rows: list[dict[str, Any]] = []

    for row in rows:
        out = dict(row)
        out["asset_group"] = v56_asset_group(
            str(row.get("base") or ""),
            str(row.get("exchange") or ""),
            str(row.get("symbol") or ""),
        )
        out["combination_rule"] = (
            "V39_CORE_PROTECTED>ELITE_EXPANSION>MAX5"
        )
        combo_rows.append(out)

    metrics = _v56_combination_metrics(combo_rows)

    by_group: list[dict[str, Any]] = []
    groups = sorted({str(r.get("asset_group") or "") for r in combo_rows})
    for group_name in groups:
        part = [r for r in combo_rows if str(r.get("asset_group") or "") == group_name]
        item = {"asset_group": group_name}
        item.update(_v56_combination_metrics(part))
        by_group.append(item)

    _write_union_csv(MARKETS_OUTPUT_DIR / "combination_trades_v56.csv", combo_rows)
    _write_union_csv(MARKETS_OUTPUT_DIR / "combination_by_group_v56.csv", by_group)
    (MARKETS_OUTPUT_DIR / "combination_summary_v56.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def apply_stock_portfolio_v56(
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """V19 stock allocator + total 5 + max 3 same direction + one active per group."""
    ordered = sorted(candidates, key=lambda r: (pd.Timestamp(r["entry_time"]), str(r["symbol"])))
    accepted: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    symbol_last_entry: dict[str, pd.Timestamp] = {}
    new_count_by_time: dict[pd.Timestamp, int] = {}
    equity = v22.v19.STOCK_STARTING_EQUITY
    equity_events: list[tuple[pd.Timestamp, float]] = [
        (pd.Timestamp.min.tz_localize("UTC"), equity)
    ]

    def realize_until(ts: pd.Timestamp) -> None:
        nonlocal equity, open_positions
        closing = [p for p in open_positions if pd.Timestamp(p["exit_time"]) <= ts]
        if not closing:
            return
        for p in sorted(closing, key=lambda x: pd.Timestamp(x["exit_time"])):
            equity += float(p["profit_usdt"])
            equity_events.append((pd.Timestamp(p["exit_time"]), equity))
        ids = {id(p) for p in closing}
        open_positions = [p for p in open_positions if id(p) not in ids]

    rejected_capacity = 0
    rejected_cooldown = 0
    rejected_same_time = 0
    rejected_same_direction = 0
    rejected_same_group = 0

    for row in ordered:
        entry_time = pd.Timestamp(row["entry_time"])
        if entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize("UTC")
        realize_until(entry_time)

        if len(open_positions) >= v22.v19.STOCK_MAX_POSITIONS:
            rejected_capacity += 1
            continue
        if new_count_by_time.get(entry_time, 0) >= v22.v19.STOCK_MAX_NEW_TRADES_PER_TIMESTAMP:
            rejected_same_time += 1
            continue

        symbol = str(row["symbol"]).upper()
        previous = symbol_last_entry.get(symbol)
        if (
            previous is not None
            and entry_time - previous < pd.Timedelta(hours=v22.v19.STOCK_SYMBOL_COOLDOWN_HOURS)
        ):
            rejected_cooldown += 1
            continue

        direction = str(row.get("direction") or "")

        group_name = v56_asset_group(symbol, force_stock=True)
        active_groups = {str(p.get("asset_group")) for p in open_positions}
        if group_name in active_groups:
            rejected_same_group += 1
            continue

        risk_usdt = equity * v22.v19.STOCK_RISK_PER_TRADE_PCT / 100.0
        trade = dict(row)
        trade["asset_group"] = group_name
        trade["equity_at_entry"] = round(equity, 6)
        trade["risk_usdt"] = round(risk_usdt, 6)
        trade["profit_usdt"] = round(float(row["net_r"]) * risk_usdt, 6)
        accepted.append(trade)
        open_positions.append(trade)
        symbol_last_entry[symbol] = entry_time
        new_count_by_time[entry_time] = new_count_by_time.get(entry_time, 0) + 1

    realize_until(pd.Timestamp.max.tz_localize("UTC"))
    equity_curve = [value for _, value in sorted(equity_events, key=lambda x: x[0])]
    peak = v22.v19.STOCK_STARTING_EQUITY
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100.0)

    wins = sum(str(r["outcome"]) == "WIN" for r in accepted)
    losses = sum(str(r["outcome"]) == "LOSS" for r in accepted)
    opens = sum(str(r["outcome"]) == "OPEN" for r in accepted)
    closed = wins + losses

    summary = {
        "scheme": "V56_US_STOCK_GROUP_FILTERED",
        "trades": len(accepted),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate_pct": round(wins / closed * 100.0, 3) if closed else 0.0,
        "average_net_r": round(
            sum(float(r["net_r"]) for r in accepted if str(r["outcome"]) != "OPEN") / closed,
            5,
        ) if closed else 0.0,
        "total_net_r": round(sum(float(r["net_r"]) for r in accepted), 5),
        "starting_equity": v22.v19.STOCK_STARTING_EQUITY,
        "ending_equity": round(equity, 4),
        "net_profit_usdt": round(equity - v22.v19.STOCK_STARTING_EQUITY, 4),
        "max_drawdown_pct": round(max_dd, 3),
        "rejected_capacity": rejected_capacity,
        "rejected_symbol_cooldown": rejected_cooldown,
        "rejected_same_timestamp": rejected_same_time,
        "rejected_same_direction": rejected_same_direction,
        "rejected_same_group": rejected_same_group,
    }
    return accepted, summary



def _v56_build_completed_daily_ma_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    Build no-look-ahead daily MA lines aligned to every intraday bar.

    For each intraday bar on date D, the line is calculated from daily closes
    strictly BEFORE D. For US stocks this naturally means prior trading days;
    for crypto it means prior calendar days.
    """
    close = pd.to_numeric(frame["close"], errors="coerce")
    date_key = pd.DatetimeIndex(frame.index).normalize()
    daily_close = pd.Series(close.to_numpy(dtype="float64", copy=False), index=date_key).groupby(level=0).last()

    ma5_daily = daily_close.rolling(
        V56_POST_TP1_FAST_MA_DAYS,
        min_periods=V56_POST_TP1_FAST_MA_DAYS,
    ).mean().shift(1)
    ma10_daily = daily_close.rolling(
        V56_POST_TP1_SLOW_MA_DAYS,
        min_periods=V56_POST_TP1_SLOW_MA_DAYS,
    ).mean().shift(1)

    ma5_slope_daily = ma5_daily - ma5_daily.shift(1)
    ma10_slope_daily = ma10_daily - ma10_daily.shift(1)

    mapper = pd.Series(date_key, index=frame.index)

    def mapped(series: pd.Series) -> np.ndarray:
        return pd.to_numeric(mapper.map(series), errors="coerce").to_numpy(dtype="float64", copy=False)

    return {
        "ma5": mapped(ma5_daily),
        "ma10": mapped(ma10_daily),
        "ma5_slope": mapped(ma5_slope_daily),
        "ma10_slope": mapped(ma10_slope_daily),
    }


_V56_MA_CACHE_STATS = {"builds": 0, "hits": 0, "evictions": 0}
# IMPORTANT: do NOT store NumPy arrays inside DataFrame.attrs. Pandas may compare attrs
# while copying/concatenating frames, and ndarray equality returns an array instead of a
# single boolean. That was the V53 crash source. V56 keeps the cache outside the frame.
_V56_MA_CACHE: dict[int, tuple[weakref.ReferenceType[pd.DataFrame], dict[str, np.ndarray]]] = {}


def _v56_get_completed_daily_ma_arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    Cache completed-day 5D/10D arrays per live DataFrame object without touching attrs.

    The weak-reference callback removes arrays when the market frame is released, so the
    1GB Oracle VM does not accumulate every symbol's MA arrays across the full universe.
    """
    key = id(frame)
    cached = _V56_MA_CACHE.get(key)
    if cached is not None:
        frame_ref, arrays = cached
        if frame_ref() is frame:
            _V56_MA_CACHE_STATS["hits"] += 1
            return arrays
        _V56_MA_CACHE.pop(key, None)

    built = _v56_build_completed_daily_ma_arrays(frame)

    def _evict(_ref: weakref.ReferenceType[pd.DataFrame], cache_key: int = key) -> None:
        if _V56_MA_CACHE.pop(cache_key, None) is not None:
            _V56_MA_CACHE_STATS["evictions"] += 1

    _V56_MA_CACHE[key] = (weakref.ref(frame, _evict), built)
    _V56_MA_CACHE_STATS["builds"] += 1
    return built


def _v56_choose_post_tp1_line(
    direction: str,
    i: int,
    ma: dict[str, np.ndarray],
) -> tuple[int, float]:
    ma5 = float(ma["ma5"][i])
    ma10 = float(ma["ma10"][i])
    s5 = float(ma["ma5_slope"][i])
    s10 = float(ma["ma10_slope"][i])

    if not (math.isfinite(ma5) and math.isfinite(ma10)):
        if math.isfinite(ma5):
            return V56_POST_TP1_FAST_MA_DAYS, ma5
        if math.isfinite(ma10):
            return V56_POST_TP1_SLOW_MA_DAYS, ma10
        return 0, float("nan")

    if not V56_POST_TP1_USE_ADAPTIVE_5_10:
        return V56_POST_TP1_FAST_MA_DAYS, ma5

    if direction == "LONG":
        strong = ma5 > ma10 and math.isfinite(s5) and math.isfinite(s10) and s5 > 0 and s10 >= 0
    else:
        strong = ma5 < ma10 and math.isfinite(s5) and math.isfinite(s10) and s5 < 0 and s10 <= 0

    if strong:
        return V56_POST_TP1_SLOW_MA_DAYS, ma10
    return V56_POST_TP1_FAST_MA_DAYS, ma5


def simulate_fixed_stop_runner_v56(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
) -> dict[str, Any]:
    """V56 Price Discovery Runner: original structural stop only, no profit protection/trailing."""
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    end_i = len(frame) - 1
    peak_r = 0.0

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])

        stop_fill = engine.bar_hits_stop(direction, o, h, l, float(stop))
        if stop_fill is not None:
            gross_r = sign * (float(stop_fill) - entry) / stop_distance
            result = engine.finish_result(
                direction,
                entry,
                stop_distance,
                gross_r,
                float(stop_fill) * cost_rate / stop_distance,
                i,
                float(stop_fill),
                "STOP",
                0,
            )
            result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
            result["target_mode"] = "FIXED_STOP_PRICE_DISCOVERY_RUNNER"
            result["runner_peak_r"] = float(peak_r)
            result["runner_hit_4r"] = bool(peak_r >= 4.0)
            result["tp1_was_hit"] = False
            result["tp1_exit_fraction"] = 0.0
            result["trend_remainder_fraction"] = 0.0
            result["post_tp1_ma_days"] = 0
            return result

        favorable = (h - entry) / stop_distance if direction == "LONG" else (entry - l) / stop_distance
        if math.isfinite(favorable):
            peak_r = max(peak_r, float(favorable))

    result = engine.finish_open_result(
        direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i])
    )
    result["target_mode"] = "FIXED_STOP_PRICE_DISCOVERY_RUNNER"
    result["runner_peak_r"] = float(peak_r)
    result["runner_hit_4r"] = bool(peak_r >= 4.0)
    result["tp1_was_hit"] = False
    result["tp1_exit_fraction"] = 0.0
    result["trend_remainder_fraction"] = 0.0
    result["post_tp1_ma_days"] = 0
    return result


def simulate_post_tp1_weakness_v56(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
) -> dict[str, Any]:
    """
    V56 REAL-TP1 simulator.

    BEFORE TP1:
      * keep the ORIGINAL structural stop fixed;
      * NO break-even / +1R / +3R profit protection;
      * if TP1 is not reached, only the original stop can close 100%.

    AT TP1:
      * close exactly 50% at TP1;
      * keep exactly 50% as the trend remainder.

    AFTER TP1:
      * the remaining 50% is NOT closed by the old R-protection ladder;
      * it exits only when the selected 5D/10D short-term trend line weakens;
      * LONG: completed 15m close < MA;
      * SHORT: completed 15m close > MA;
      * execute next 15m open (no look-ahead).

    Synthetic Price Discovery Runner also uses V56 fixed-stop-only behavior (no adaptive profit protection).
    """
    if abs(tp1 - entry) / max(stop_distance, 1e-12) >= v32.RUNNER_SENTINEL_THRESHOLD_R:
        return simulate_fixed_stop_runner_v56(
            frame, entry_i, direction, entry, stop, tp1, stop_distance
        )

    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    active_stop = float(stop)
    end_i = len(frame) - 1
    ma = _v56_get_completed_daily_ma_arrays(frame)

    tp1_hit = False
    tp1_hit_i: int | None = None
    selected_days = 0

    tp1_fraction = V56_TP1_TAKE_PROFIT_FRACTION
    remainder_fraction = V56_TREND_REMAINDER_FRACTION

    def full_close_result(
        fill_price: float,
        fill_i: int,
        reason: str,
    ) -> dict[str, Any]:
        gross_r = sign * (fill_price - entry) / stop_distance
        exit_cost_r = fill_price * cost_rate / stop_distance
        result = engine.finish_result(
            direction,
            entry,
            stop_distance,
            gross_r,
            exit_cost_r,
            fill_i,
            fill_price,
            reason,
            0,
        )
        result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
        result["tp1_was_hit"] = False
        result["tp1_exit_fraction"] = 0.0
        result["trend_remainder_fraction"] = 0.0
        result["post_tp1_ma_days"] = 0
        return result

    def partial_finish_result(
        remainder_price: float,
        exit_i: int,
        reason: str,
    ) -> dict[str, Any]:
        tp1_r = sign * (tp1 - entry) / stop_distance
        remainder_r = sign * (remainder_price - entry) / stop_distance
        gross_r = tp1_fraction * tp1_r + remainder_fraction * remainder_r

        # Full entry cost + weighted exit costs for the two partial closes.
        exit_cost_r = (
            tp1_fraction * tp1 * cost_rate / stop_distance
            + remainder_fraction * remainder_price * cost_rate / stop_distance
        )
        weighted_exit_price = (
            tp1_fraction * tp1
            + remainder_fraction * remainder_price
        )
        result = engine.finish_result(
            direction,
            entry,
            stop_distance,
            gross_r,
            exit_cost_r,
            exit_i,
            weighted_exit_price,
            reason,
            0,
        )
        result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
        result["tp1_was_hit"] = True
        result["tp1_exit_fraction"] = float(tp1_fraction)
        result["trend_remainder_fraction"] = float(remainder_fraction)
        result["tp1_realized_r_component"] = float(tp1_fraction * tp1_r)
        result["remainder_r_component"] = float(remainder_fraction * remainder_r)
        result["remainder_exit_price"] = float(remainder_price)
        result["post_tp1_ma_days"] = int(selected_days)
        return result

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        # BEFORE TP1: V56 keeps the ORIGINAL structural stop fixed.
        # No break-even / +1R / +3R profit-protection ladder is allowed.
        if not tp1_hit:
            stop_fill = engine.bar_hits_stop(direction, o, h, l, active_stop)
            if stop_fill is not None:
                return full_close_result(float(stop_fill), i, "STOP")

            if engine.bar_hits_target(direction, o, h, l, tp1):
                tp1_hit = True
                tp1_hit_i = i
                selected_days, selected_line = _v56_choose_post_tp1_line(
                    direction, i, ma
                )

                # If we cannot build a prior-completed-day 5D/10D line, do not
                # invent future data. Preserve a safe fallback: close the remainder
                # at TP1 as well.
                if selected_days == 0 or not math.isfinite(selected_line):
                    result = partial_finish_result(
                        float(tp1),
                        i,
                        "TP1_50PCT_MA_HISTORY_FALLBACK_REMAINDER_AT_TP1",
                    )
                    result["post_tp1_ma_days"] = 0
                    return result

        # AFTER TP1: remaining 50% is governed by MA weakness only.
        if tp1_hit and tp1_hit_i is not None and i > tp1_hit_i:
            line_arr = ma["ma10"] if selected_days == 10 else ma["ma5"]
            line = float(line_arr[i])
            if math.isfinite(line):
                weak = (c < line) if direction == "LONG" else (c > line)
                if weak:
                    fill_i = i + 1
                    if fill_i <= end_i:
                        fill_price = float(frame["open"].iat[fill_i])
                        result = partial_finish_result(
                            fill_price,
                            fill_i,
                            f"TP1_50PCT_POST_TP1_MA{selected_days}_WEAKNESS",
                        )
                        result["post_tp1_weakness_signal_i"] = int(i)
                        return result

    # End of research window.
    if tp1_hit:
        # 50% has been realized at TP1; remaining 50% is still OPEN and marked
        # to the final close. Do NOT charge an exit fee on the still-open remainder.
        mark_price = float(frame["close"].iat[end_i])
        tp1_r = sign * (tp1 - entry) / stop_distance
        remainder_mark_r = sign * (mark_price - entry) / stop_distance
        gross_r = tp1_fraction * tp1_r + remainder_fraction * remainder_mark_r

        entry_cost_r = entry * cost_rate / stop_distance
        realized_exit_cost_r = (
            tp1_fraction * tp1 * cost_rate / stop_distance
        )
        cost_r = entry_cost_r + realized_exit_cost_r
        weighted_mark_price = tp1_fraction * tp1 + remainder_fraction * mark_price

        return {
            "exit_i": end_i,
            "exit_price": weighted_mark_price,
            "outcome": "OPEN",
            "exit_reason": "TP1_50PCT_REMAINDER_OPEN_AT_END",
            "timed_out": 0,
            "gross_r": gross_r,
            "cost_r": cost_r,
            "net_r": gross_r - cost_r,
            "tp1_was_hit": True,
            "tp1_exit_fraction": float(tp1_fraction),
            "trend_remainder_fraction": float(remainder_fraction),
            "tp1_realized_r_component": float(tp1_fraction * tp1_r),
            "remainder_r_component": float(remainder_fraction * remainder_mark_r),
            "remainder_exit_price": mark_price,
            "post_tp1_ma_days": int(selected_days),
        }

    result = engine.finish_open_result(
        direction,
        entry,
        stop_distance,
        end_i,
        float(frame["close"].iat[end_i]),
    )
    result["tp1_was_hit"] = False
    result["tp1_exit_fraction"] = 0.0
    result["trend_remainder_fraction"] = 0.0
    result["post_tp1_ma_days"] = 0
    return result


def _structure_candidates(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
) -> list[tuple[Any, ...]]:
    """V56 FAST: same V36 structure rules, event-driven instead of bar-by-bar Python scan."""
    out: list[tuple[Any, ...]] = []
    n = len(frame)
    if n < 400:
        return out

    # Convert hot-path columns to NumPy once.
    o = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype="float64", copy=False)
    h = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    l = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    c = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64", copy=False)
    vol = pd.to_numeric(frame.get("volume_ratio"), errors="coerce").to_numpy(dtype="float64", copy=False)
    pressure = pd.to_numeric(frame.get("volume_pressure"), errors="coerce").to_numpy(dtype="float64", copy=False)
    heat = pd.to_numeric(frame.get("market_heat_ratio"), errors="coerce").to_numpy(dtype="float64", copy=False)
    regime = frame.get("v32_regime", pd.Series("TRANSITION", index=frame.index)).astype(str).to_numpy(copy=False)

    # Build 4H state once for this market.
    four = pd.DataFrame({
        "open": pd.Series(o, index=frame.index).resample("4h").first(),
        "high": pd.Series(h, index=frame.index).resample("4h").max(),
        "low": pd.Series(l, index=frame.index).resample("4h").min(),
        "close": pd.Series(c, index=frame.index).resample("4h").last(),
    }).dropna()
    if len(four) < 30:
        return out

    prev_close = four["close"].shift(1)
    tr = pd.concat([
        four["high"] - four["low"],
        (four["high"] - prev_close).abs(),
        (four["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    four["atr14"] = tr.rolling(14, min_periods=14).mean()
    four["ema20"] = four["close"].ewm(span=20, adjust=False).mean()
    four["ema50"] = four["close"].ewm(span=50, adjust=False).mean()
    four["break_high"] = four["high"].shift(1).rolling(
        STRUCTURE_BREAKOUT_LOOKBACK_4H, min_periods=STRUCTURE_BREAKOUT_LOOKBACK_4H
    ).max()
    four["break_low"] = four["low"].shift(1).rolling(
        STRUCTURE_BREAKOUT_LOOKBACK_4H, min_periods=STRUCTURE_BREAKOUT_LOOKBACK_4H
    ).min()
    four["stop_low3"] = four["low"].shift(1).rolling(
        STRUCTURE_STOP_LOOKBACK_4H, min_periods=STRUCTURE_STOP_LOOKBACK_4H
    ).min()
    four["stop_high3"] = four["high"].shift(1).rolling(
        STRUCTURE_STOP_LOOKBACK_4H, min_periods=STRUCTURE_STOP_LOOKBACK_4H
    ).max()

    completed = four[
        ["break_high", "break_low", "stop_low3", "stop_high3", "atr14", "ema20", "ema50", "close"]
    ].shift(1)
    aligned = completed.reindex(frame.index, method="ffill")

    break_hi = pd.to_numeric(aligned["break_high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    break_lo = pd.to_numeric(aligned["break_low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    stop_low3 = pd.to_numeric(aligned["stop_low3"], errors="coerce").to_numpy(dtype="float64", copy=False)
    stop_high3 = pd.to_numeric(aligned["stop_high3"], errors="coerce").to_numpy(dtype="float64", copy=False)
    atr4 = pd.to_numeric(aligned["atr14"], errors="coerce").to_numpy(dtype="float64", copy=False)
    ema20_4 = pd.to_numeric(aligned["ema20"], errors="coerce").to_numpy(dtype="float64", copy=False)
    ema50_4 = pd.to_numeric(aligned["ema50"], errors="coerce").to_numpy(dtype="float64", copy=False)
    close4 = pd.to_numeric(aligned["close"], errors="coerce").to_numpy(dtype="float64", copy=False)

    # V56: structure first, EMA only acts as a veto against clearly opposite momentum.
    ema20_prev4 = np.roll(ema20_4, 16)
    ema20_prev4[:16] = np.nan
    ema_slope_atr = (ema20_4 - ema20_prev4) / atr4

    bull = (
        np.isfinite(ema20_4) & np.isfinite(close4) & np.isfinite(ema_slope_atr)
        & (close4 >= ema20_4 - 0.15 * atr4)
        & (ema_slope_atr >= -STRUCTURE_IGNITION_MAX_COUNTER_EMA_SLOPE_ATR)
    )
    bear = (
        np.isfinite(ema20_4) & np.isfinite(close4) & np.isfinite(ema_slope_atr)
        & (close4 <= ema20_4 + 0.15 * atr4)
        & (ema_slope_atr <= STRUCTURE_IGNITION_MAX_COUNTER_EMA_SLOPE_ATR)
    )

    prev_break_hi = np.roll(break_hi, 1)
    prev_break_lo = np.roll(break_lo, 1)
    prev_break_hi[0] = np.nan
    prev_break_lo[0] = np.nan
    prev_c = np.roll(c, 1)
    prev_c[0] = np.nan

    valid = np.isfinite(atr4) & (atr4 > 0) & np.isfinite(c) & np.isfinite(prev_c)

    body = np.abs(c - o)
    candle_range = np.maximum(h - l, 1e-12)
    close_pos_long = (c - l) / candle_range
    close_pos_short = (h - c) / candle_range
    body_atr = body / atr4
    long_break_dist = (c - break_hi) / atr4
    short_break_dist = (break_lo - c) / atr4

    # Structural ignition: real break + meaningful displacement + close in the
    # directional part of the candle. EMA is only an anti-countertrend veto.
    long_mask = (
        valid & bull & np.isfinite(break_hi)
        & (c > break_hi) & (prev_c <= prev_break_hi)
        & (long_break_dist >= STRUCTURE_MIN_BREAK_DISTANCE_ATR)
        & (body_atr >= STRUCTURE_IGNITION_MIN_BODY_ATR)
        & (close_pos_long >= STRUCTURE_IGNITION_MIN_CLOSE_POS)
    )
    short_mask = (
        valid & bear & np.isfinite(break_lo)
        & (c < break_lo) & (prev_c >= prev_break_lo)
        & (short_break_dist >= STRUCTURE_MIN_BREAK_DISTANCE_ATR)
        & (body_atr >= STRUCTURE_IGNITION_MIN_BODY_ATR)
        & (close_pos_short >= STRUCTURE_IGNITION_MIN_CLOSE_POS)
    )

    start_i = max(300, STRUCTURE_PULLBACK_MAX_BARS)
    end_signal_i = n - 2
    long_events = np.flatnonzero(long_mask & (np.arange(n) >= start_i) & (np.arange(n) <= end_signal_i))
    short_events = np.flatnonzero(short_mask & (np.arange(n) >= start_i) & (np.arange(n) <= end_signal_i))
    events = sorted([(int(i), "LONG") for i in long_events] + [(int(i), "SHORT") for i in short_events])

    funnel["V56_structure_breakouts"] = funnel.get("V56_structure_breakouts", 0) + len(events)
    last_publish = {"LONG": -10_000, "SHORT": -10_000}

    def publish(i: int, direction: str, breakout_line: float, setup_type: str, breakout_i: int) -> bool:
        atr = atr4[i]
        if not np.isfinite(atr) or atr <= 0 or i + 1 >= n:
            return False
        entry_i = i + 1
        entry = o[entry_i]
        if not np.isfinite(entry) or entry <= 0:
            return False

        if direction == "LONG":
            base = stop_low3[i]
            if not np.isfinite(base):
                return False
            stop = base - STRUCTURE_STOP_ATR_BUFFER_4H * atr
            stop_distance = entry - stop
            entry_extension_atr = (entry - breakout_line) / atr
        else:
            base = stop_high3[i]
            if not np.isfinite(base):
                return False
            stop = base + STRUCTURE_STOP_ATR_BUFFER_4H * atr
            stop_distance = stop - entry
            entry_extension_atr = (breakout_line - entry) / atr

        if stop_distance <= 0 or not np.isfinite(entry_extension_atr):
            return False

        stop_pct = stop_distance / entry * 100.0
        if stop_pct < STRUCTURE_MIN_STOP_PCT or stop_pct > STRUCTURE_MAX_STOP_PCT:
            funnel["V56_structure_bad_stop_geometry"] = funnel.get("V56_structure_bad_stop_geometry", 0) + 1
            return False
        if entry_extension_atr > STRUCTURE_ENTRY_MAX_EXTENSION_ATR:
            funnel["V56_structure_entry_too_extended"] = funnel.get("V56_structure_entry_too_extended", 0) + 1
            return False

        # Expensive target work happens only after cheap geometry filters.
        targets = v32.selective_structure_or_runner_targets(
            frame, i, direction, float(entry), float(stop_distance), pivot_highs, pivot_lows,
            minimum_room_r=STRUCTURE_MIN_ROOM_R,
        )
        if targets is None:
            funnel["V56_structure_no_4r_room"] = funnel.get("V56_structure_no_4r_room", 0) + 1
            return False
        tp1, tp2, tp3, rr1, rr2, rr3, _ = targets
        if float(rr1) < STRUCTURE_MIN_ROOM_R - 1e-9:
            return False

        if float(rr1) >= v32.RUNNER_SENTINEL_THRESHOLD_R:
            result = simulate_fixed_stop_runner_v56(
                frame, entry_i, direction, float(entry), float(stop), float(tp1), float(stop_distance)
            )
            target_mode = "PRICE_DISCOVERY_RUNNER"
        else:
            result = simulate_post_tp1_weakness_v56(
                frame,
                entry_i,
                direction,
                float(entry),
                float(stop),
                float(tp1),
                float(stop_distance),
            )
            target_mode = "REAL_STRUCTURE_4R_PLUS_POST_TP1_MA"

        exit_i = int(result["exit_i"])
        if direction == "LONG":
            mfe_r = (np.nanmax(h[entry_i:exit_i + 1]) - entry) / stop_distance
            mae_r = (entry - np.nanmin(l[entry_i:exit_i + 1])) / stop_distance
        else:
            mfe_r = (entry - np.nanmin(l[entry_i:exit_i + 1])) / stop_distance
            mae_r = (np.nanmax(h[entry_i:exit_i + 1]) - entry) / stop_distance

        signal_time = frame.index[i]
        v = vol[i] if np.isfinite(vol[i]) else 0.0
        pr = pressure[i] if np.isfinite(pressure[i]) else 0.0
        ht = heat[i] if np.isfinite(heat[i]) else 0.0
        score = max(0.0, float(entry_extension_atr))
        stop_atr = stop_distance / atr
        candle_range_atr = abs(c[i] - o[i]) / atr

        row = (
            getattr(target, "exchange_name", ""), getattr(target, "symbol", ""), getattr(target, "base", ""),
            direction, signal_time.isoformat(), frame.index[entry_i].isoformat(), frame.index[exit_i].isoformat(),
            _period_name(signal_time, research_start), SCHEME_V56,
            float(entry), float(stop), float(tp1), float(tp2), float(tp3),
            float(rr1), float(rr2), float(rr3),
            float(result["exit_price"]), score, result["outcome"], result["exit_reason"], result["timed_out"],
            float(result["gross_r"]), float(result["cost_r"]), float(result["net_r"]),
            float(v), float(candle_range_atr), float(pr),
            float(rr1), float(stop_atr), float(stop_pct), 1, exit_i - entry_i + 1, float(mfe_r), float(mae_r),
        )
        out.append(row)
        last_publish[direction] = i
        funnel["V56_structure_published"] = funnel.get("V56_structure_published", 0) + 1
        _STRUCTURE_AUDIT.append({
            "exchange": row[0], "symbol": row[1], "base": row[2], "direction": direction,
            "setup_type": setup_type,
            "signal_time": row[4], "entry_time": row[5], "exit_time": row[6],
            "breakout_time": frame.index[breakout_i].isoformat(),
            "breakout_line": float(breakout_line),
            "entry": float(entry), "stop": float(stop), "structure_stop_base": float(base),
            "stop_buffer_4h_atr": STRUCTURE_STOP_ATR_BUFFER_4H,
            "stop_pct": float(stop_pct), "entry_extension_atr": float(entry_extension_atr),
            "regime_audit": str(regime[i]),
            "target_mode": target_mode, "tp1": float(tp1), "rr1": float(rr1),
            "relative_volume_audit_only": float(v),
            "market_heat_ratio_audit_only": float(ht),
            "exit_reason": result["exit_reason"], "net_r": float(result["net_r"]),
            "tp1_was_hit": bool(result.get("tp1_was_hit", False)),
            "tp1_exit_fraction": float(result.get("tp1_exit_fraction", 0.0) or 0.0),
            "trend_remainder_fraction": float(result.get("trend_remainder_fraction", 0.0) or 0.0),
            "post_tp1_ma_days": int(result.get("post_tp1_ma_days", 0) or 0),
            "tp1_realized_r_component": float(result.get("tp1_realized_r_component", 0.0) or 0.0),
            "remainder_r_component": float(result.get("remainder_r_component", 0.0) or 0.0),
            "mfe_r": float(mfe_r), "mae_r": float(mae_r),
        })
        return True

    # Event-driven: process only true breakout events.
    for breakout_i, direction in events:
        if breakout_i - last_publish[direction] < STRUCTURE_REARM_BARS:
            continue

        atr = atr4[breakout_i]
        line = break_hi[breakout_i] if direction == "LONG" else break_lo[breakout_i]
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(line):
            continue

        ext = ((c[breakout_i] - line) / atr) if direction == "LONG" else ((line - c[breakout_i]) / atr)

        if ext <= STRUCTURE_DIRECT_MAX_EXTENSION_ATR:
            funnel["V56_structure_direct_quality_pass"] = funnel.get("V56_structure_direct_quality_pass", 0) + 1
            if publish(breakout_i, direction, float(line),
                       "DIRECT_BREAKOUT" if direction == "LONG" else "DIRECT_BREAKDOWN",
                       breakout_i):
                continue

        # First valid pullback/reclaim, maximum 48 x 15m bars.
        j0 = breakout_i + 1
        j1 = min(end_signal_i, breakout_i + STRUCTURE_PULLBACK_MAX_BARS)
        if j0 > j1:
            continue

        js = np.arange(j0, j1 + 1)
        atrs = atr4[js]
        finite = np.isfinite(atrs) & (atrs > 0)
        if direction == "LONG":
            cond = (
                finite
                & bull[js]
                & (l[js] <= line + STRUCTURE_PULLBACK_TOL_ATR * atrs)
                & (c[js] > line)
                & (c[js] > c[js - 1])
                & ((c[js] - l[js]) / np.maximum(h[js] - l[js], 1e-12) >= 0.55)
                & (np.abs(c[js] - o[js]) / atrs >= 0.12)
                & (l[js] > stop_low3[js] - STRUCTURE_STOP_ATR_BUFFER_4H * atrs)
            )
        else:
            cond = (
                finite
                & bear[js]
                & (h[js] >= line - STRUCTURE_PULLBACK_TOL_ATR * atrs)
                & (c[js] < line)
                & (c[js] < c[js - 1])
                & ((h[js] - c[js]) / np.maximum(h[js] - l[js], 1e-12) >= 0.55)
                & (np.abs(c[js] - o[js]) / atrs >= 0.12)
                & (h[js] < stop_high3[js] + STRUCTURE_STOP_ATR_BUFFER_4H * atrs)
            )

        hits = np.flatnonzero(cond)
        if len(hits):
            i = int(js[int(hits[0])])
            funnel["V56_structure_first_pullbacks"] = funnel.get("V56_structure_first_pullbacks", 0) + 1
            publish(i, direction, float(line), "FIRST_PULLBACK_RECLAIM", breakout_i)

    funnel["V56_structure_candidates"] = funnel.get("V56_structure_candidates", 0) + len(out)
    return out


def _relabel_v32_to_v56(row: tuple[Any, ...]) -> tuple[Any, ...]:
    values = list(row)
    values[8] = SCHEME_V56
    return tuple(values)


def candidate_rows_v56(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    # V56 repair: V32's adaptive-heat helper intentionally returns immediately on
    # an empty frame, after which V32 used to index a non-existent market_heat_ok
    # column. Empty frames contain no tradable evidence, so record a clean skip.
    if frame is None or frame.empty:
        funnel["V56_empty_frame_skips"] = funnel.get("V56_empty_frame_skips", 0) + 1
        _PER_MARKET_STRUCTURE.append({
            "exchange": getattr(target, "exchange_name", ""),
            "symbol": getattr(target, "symbol", ""),
            "base": getattr(target, "base", ""),
            "v32_core_candidates": 0,
            "structure_candidates": 0,
            "published_total": 0,
            "status": "EMPTY_FRAME_SKIPPED",
        })
        return

    # 1) Freeze V32 core. No V56 condition can remove these rows.
    core = list(v32.candidate_rows_v32(target, frame, research_start, pivot_highs, pivot_lows, funnel))
    core = [_relabel_v32_to_v56(row) for row in core]

    # 2) Independent Structure scan after V32 has attached heat/regime columns.
    structure = _structure_candidates(target, frame, research_start, pivot_highs, pivot_lows, funnel)

    _PER_MARKET_STRUCTURE.append({
        "exchange": getattr(target, "exchange_name", ""),
        "symbol": getattr(target, "symbol", ""),
        "base": getattr(target, "base", ""),
        "v32_core_candidates": len(core),
        "structure_candidates": len(structure),
        "published_total": len(core) + len(structure),
    })
    funnel["V56_v32_core_candidates"] = funnel.get("V56_v32_core_candidates", 0) + len(core)
    funnel["V56_total_source_candidates"] = funnel.get("V56_total_source_candidates", 0) + len(core) + len(structure)

    # Sort only; portfolio rules/cooldowns remain the inherited execution layer.
    merged = core + structure
    merged.sort(key=lambda r: pd.Timestamp(r[5]))
    yield from merged


def prefilter_no_fixed_5m(targets: list[Any], exchanges: dict[str, Any], errors: list[dict[str, str]]) -> list[Any]:
    return list(targets)


def _v56_float(row: dict[str, Any], key: str, default: float = float("nan")) -> float:
    try:
        value = float(row.get(key))
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _v56_real_tp_levels(row: dict[str, Any]) -> tuple[bool, bool, bool, bool, bool, bool]:
    """Return real_tp1, tp1_hit, distinct_tp2, tp2_hit, distinct_tp3/tp3_hit via MFE."""
    rr1 = _v56_float(row, "rr1")
    rr2 = _v56_float(row, "rr2")
    rr3 = _v56_float(row, "rr3")
    mfe = _v56_float(row, "mfe_r", -float("inf"))
    real_tp1 = math.isfinite(rr1) and rr1 < v32.RUNNER_SENTINEL_THRESHOLD_R
    reason = str(row.get("exit_reason") or "")
    tp1_hit = bool(real_tp1 and reason.startswith("TP1_50PCT"))
    distinct_tp2 = bool(tp1_hit and math.isfinite(rr2) and rr2 > rr1 + 1e-9)
    tp2_hit = bool(distinct_tp2 and mfe + 1e-9 >= rr2)
    distinct_tp3 = bool(tp1_hit and math.isfinite(rr3) and math.isfinite(rr2) and rr3 > rr2 + 1e-9)
    tp3_hit = bool(distinct_tp3 and mfe + 1e-9 >= rr3)
    return real_tp1, tp1_hit, distinct_tp2, tp2_hit, distinct_tp3, tp3_hit


def _v56_tp1_full_exit_net_r(row: dict[str, Any]) -> float:
    """
    Same-entry control: if a REAL TP1 was touched, close 100% at TP1.
    Before TP1 (or for Price Discovery Runner) the path is identical to V56, so reuse net_r.
    """
    real_tp1, tp1_hit, _, _, _, _ = _v56_real_tp_levels(row)
    current = _v56_float(row, "net_r", 0.0)
    if not (real_tp1 and tp1_hit):
        return current
    entry = _v56_float(row, "entry")
    stop = _v56_float(row, "stop")
    tp1 = _v56_float(row, "tp1")
    stop_distance = abs(entry - stop)
    if not all(math.isfinite(x) for x in (entry, stop, tp1)) or stop_distance <= 0:
        return current
    sign = 1.0 if str(row.get("direction") or "").upper() == "LONG" else -1.0
    gross_r = sign * (tp1 - entry) / stop_distance
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    cost_r = (entry + tp1) * cost_rate / stop_distance
    return gross_r - cost_r


def _v56_same_entry_exit_report(
    rows: list[dict[str, Any]],
    output_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    """
    Observational A/B on the exact same selected entries.
    It intentionally does NOT change entry selection or slot availability.
    """
    detail: list[dict[str, Any]] = []
    current_wins = current_losses = current_open = 0
    control_wins = control_losses = control_open = 0
    current_profit = control_profit = 0.0
    current_net_r = control_net_r = 0.0

    tp1_hits = 0
    tp2_distinct = tp2_hits = weakened_before_tp2 = 0
    tp3_distinct = tp3_hits = tp2_not_tp3 = tp3_then_weaken = 0
    remainder_open_before_tp2 = 0

    for source in rows:
        row = dict(source)
        outcome = str(row.get("outcome") or "")
        cur_r = _v56_float(row, "net_r", 0.0)
        ctrl_r = _v56_tp1_full_exit_net_r(row)
        risk_usdt = _v56_float(row, "risk_usdt", 0.0)
        cur_p = _v56_float(row, "profit_usdt", 0.0)
        real_tp1, tp1_hit, d2, h2, d3, h3 = _v56_real_tp_levels(row)
        # Current scheme can still be OPEN after taking 50% at TP1, while the
        # TP1-100% control would already be fully closed on that same entry.
        control_is_open = outcome == "OPEN" and not (real_tp1 and tp1_hit)
        ctrl_p = 0.0 if control_is_open else ctrl_r * risk_usdt

        if outcome == "OPEN":
            current_open += 1
        else:
            if cur_r > 0:
                current_wins += 1
            else:
                current_losses += 1
            current_profit += cur_p

        if control_is_open:
            control_open += 1
        else:
            if ctrl_r > 0:
                control_wins += 1
            else:
                control_losses += 1
            control_profit += ctrl_p

        current_net_r += cur_r
        control_net_r += ctrl_r

        reason = str(row.get("exit_reason") or "")
        ma_weaken = "POST_TP1_MA" in reason and "WEAKNESS" in reason
        if tp1_hit:
            tp1_hits += 1
        if d2:
            tp2_distinct += 1
            if h2:
                tp2_hits += 1
            elif ma_weaken:
                weakened_before_tp2 += 1
            elif outcome == "OPEN":
                remainder_open_before_tp2 += 1
        if d3:
            tp3_distinct += 1
            if h3:
                tp3_hits += 1
                if ma_weaken:
                    tp3_then_weaken += 1
            elif h2:
                tp2_not_tp3 += 1

        row.update({
            "v56_real_tp1": real_tp1,
            "v56_tp1_hit": tp1_hit,
            "v56_tp2_distinct": d2,
            "v56_tp2_hit_before_ma_exit": h2,
            "v56_tp3_distinct": d3,
            "v56_tp3_hit_before_ma_exit": h3,
            "v56_current_50_50_net_r": cur_r,
            "v56_tp1_full_exit_net_r": ctrl_r,
            "v56_same_entry_net_r_difference_50_50_minus_full_tp1": cur_r - ctrl_r,
            "v56_tp1_full_exit_profit_usdt_same_risk": ctrl_p,
        })
        detail.append(row)

    current_closed = current_wins + current_losses
    control_closed = control_wins + control_losses
    summary = {
        "comparison_type": "SAME_SELECTED_ENTRIES_OBSERVATIONAL",
        "portfolio_occupancy_changed": False,
        "selected_entries": len(rows),
        "tp1_50pct_plus_ma_remainder": {
            "wins": current_wins,
            "losses": current_losses,
            "open": current_open,
            "closed": current_closed,
            "win_rate_pct": round(100.0 * current_wins / current_closed, 3) if current_closed else 0.0,
            "net_profit_usdt": round(current_profit, 4),
            "total_net_r": round(current_net_r, 5),
        },
        "tp1_100pct_full_exit_control": {
            "wins": control_wins,
            "losses": control_losses,
            "open": control_open,
            "closed": control_closed,
            "win_rate_pct": round(100.0 * control_wins / control_closed, 3) if control_closed else 0.0,
            "net_profit_usdt": round(control_profit, 4),
            "total_net_r": round(control_net_r, 5),
        },
        "difference_50_50_minus_full_tp1": {
            "net_profit_usdt": round(current_profit - control_profit, 4),
            "total_net_r": round(current_net_r - control_net_r, 5),
        },
        "tp2_tp3_tracking_on_50pct_remainder": {
            "real_tp1_hit_trades": tp1_hits,
            "distinct_tp2_available": tp2_distinct,
            "reached_tp2": tp2_hits,
            "reached_tp2_pct": round(100.0 * tp2_hits / tp2_distinct, 3) if tp2_distinct else 0.0,
            "weakened_before_tp2": weakened_before_tp2,
            "remainder_open_before_tp2": remainder_open_before_tp2,
            "distinct_tp3_available": tp3_distinct,
            "reached_tp3": tp3_hits,
            "reached_tp3_pct": round(100.0 * tp3_hits / tp3_distinct, 3) if tp3_distinct else 0.0,
            "reached_tp2_but_not_tp3": tp2_not_tp3,
            "reached_tp3_then_ma_weakened": tp3_then_weaken,
        },
    }
    _write_union_csv(output_dir / f"{prefix}_same_entry_exit_comparison_v56.csv", detail)
    (output_dir / f"{prefix}_same_entry_exit_summary_v56.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _v56_core_quality_observational_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify selected exchange trades by the existing pre-entry Elite gate without changing selection."""
    audit_map = _v56_structure_audit_map()
    detailed: list[dict[str, Any]] = []
    for src in rows:
        row = dict(src)
        tier = str(row.get("portfolio_tier") or "")
        if tier == "ELITE_EXPANSION":
            bucket = "ELITE_EXPANSION"
            reason = str(row.get("elite_reason") or "")
        else:
            key = _v56_candidate_key(row)
            audit = audit_map.get(key)
            ok, reason = _v56_elite_expansion_gate(row, audit)
            bucket = "CORE_ELITE_LIKE" if ok else "CORE_NON_ELITE"
        row["v56_quality_bucket"] = bucket
        row["v56_quality_reason"] = reason
        detailed.append(row)

    _write_union_csv(MARKETS_OUTPUT_DIR / "formal_quality_research_v56.csv", detailed)
    by_bucket: list[dict[str, Any]] = []
    for bucket in ("ELITE_EXPANSION", "CORE_ELITE_LIKE", "CORE_NON_ELITE"):
        part = [r for r in detailed if r.get("v56_quality_bucket") == bucket]
        if not part:
            continue
        closed = [r for r in part if str(r.get("outcome") or "") != "OPEN"]
        wins = [r for r in closed if float(r.get("net_r") or 0.0) > 0]
        losses = [r for r in closed if float(r.get("net_r") or 0.0) < 0]
        by_bucket.append({
            "quality_bucket": bucket,
            "trades": len(part),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "open": len(part) - len(closed),
            "win_rate_pct": 100.0 * len(wins) / len(closed) if closed else 0.0,
            "profit_usdt": sum(float(r.get("profit_usdt") or 0.0) for r in part),
            "total_net_r": sum(float(r.get("net_r") or 0.0) for r in part),
        })
    _write_union_csv(MARKETS_OUTPUT_DIR / "formal_quality_summary_v56.csv", by_bucket)
    out = {"buckets": by_bucket, "observational_only": True, "selection_changed": False}
    (MARKETS_OUTPUT_DIR / "formal_quality_summary_v56.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def run_exchange_branch_v56() -> dict[str, Any]:
    MARKETS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Re-point inherited discovery output files to V56.
    v22.OUTPUT_DIR = OUTPUT_DIR
    v22.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
    v22.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
    v22.V22_TOKEN_CATALOG_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_catalog.csv"
    v22.V22_REJECTED_COLLISIONS_PATH = MARKETS_OUTPUT_DIR / "rejected_stock_ticker_collisions.csv"
    v22.V22_DISCOVERY_SUMMARY_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_discovery_summary.json"
    v22.V22_RUN_NATIVE_STOCKS = False

    old_configs = engine.SCHEME_CONFIGS
    old_schemes = engine.SCHEMES
    old_primary = engine.PRIMARY_SCHEME
    old_output = engine.OUTPUT_DIR
    old_database = engine.DATABASE_PATH
    old_candidates = engine.candidate_rows
    old_discover = engine.discover_unique_targets
    old_prefilter = engine.prefilter_liquid_targets
    old_target = engine.confirmed_structure_targets
    old_simulator = engine.simulate_tp1_all
    old_select_portfolio = engine.select_portfolio

    engine.discover_unique_targets = v22.discover_three_exchange_targets
    engine.prefilter_liquid_targets = prefilter_no_fixed_5m
    engine.SCHEME_CONFIGS = {SCHEME_V56: dict(V56_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V56,)
    engine.PRIMARY_SCHEME = SCHEME_V56
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_v56
    engine.confirmed_structure_targets = v32.selective_structure_or_runner_targets
    engine.simulate_tp1_all = simulate_post_tp1_weakness_v56
    engine.select_portfolio = select_portfolio_v56

    print("\n[V56] V32核心完整保留 + 4H Structure Entry FAST 平行模組")
    print("[V56] Structural Ignition：前3根完成4H結構突破；近突破下一根，拉太遠才等第一次回踩")
    print("[V56] SL：前3根完成4H極值 ±0.25ATR；回踩需V32趨勢Regime；成本已恢復")
    print("[V56] Portfolio：V39核心3槽保護 + 最多2個Elite Expansion；額外單才做族群去重")
    print("[V56] TP1分批：真實TP1先出50%；剩50%強趨勢看10日線、一般看5日線，15m收盤轉弱後下一根開盤出場")

    try:
        rc = int(engine.main())
    finally:
        engine.SCHEME_CONFIGS = old_configs
        engine.SCHEMES = old_schemes
        engine.PRIMARY_SCHEME = old_primary
        engine.OUTPUT_DIR = old_output
        engine.DATABASE_PATH = old_database
        engine.candidate_rows = old_candidates
        engine.discover_unique_targets = old_discover
        engine.prefilter_liquid_targets = old_prefilter
        engine.confirmed_structure_targets = old_target
        engine.simulate_tp1_all = old_simulator
        engine.select_portfolio = old_select_portfolio

    segments = v22._split_exchange_outputs()
    _write_union_csv(MARKETS_OUTPUT_DIR / "structure_entry_audit_v56.csv", _STRUCTURE_AUDIT)
    _write_union_csv(MARKETS_OUTPUT_DIR / "elite_expansion_trades_v56.csv", _V56_EXPANSION_AUDIT)
    _write_union_csv(MARKETS_OUTPUT_DIR / "per_market_structure_v56.csv", _PER_MARKET_STRUCTURE)
    # Preserve V32 diagnostic writers as separate evidence that the core ran.
    v32._write_union_csv(MARKETS_OUTPUT_DIR / "v32_core_per_market_funnel_v56.csv", v32._V32_PER_MARKET_FUNNEL)
    v32._write_union_csv(MARKETS_OUTPUT_DIR / "v32_core_regime_audit_v56.csv", v32._V32_REGIME_AUDIT)
    v32._write_union_csv(MARKETS_OUTPUT_DIR / "v32_core_runner_stop_audit_v56.csv", v32._V32_STOP_AUDIT)

    summary_path = MARKETS_OUTPUT_DIR / "summary.json"
    native_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    raw_long = _v56_exchange_raw_direction_stats("LONG")
    raw_short = _v56_exchange_raw_direction_stats("SHORT")
    _v56_write_exchange_group_outputs()
    combo = _v56_write_exchange_combination_outputs()
    formal_trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V56.lower()}.csv"
    formal_rows = v22._read_csv_rows(formal_trade_path)
    exit_compare = _v56_same_entry_exit_report(
        formal_rows, MARKETS_OUTPUT_DIR, "exchange"
    )
    quality_research = _v56_core_quality_observational_report(formal_rows)
    print(
        f"[V56] 正式組合｜V39核心保護 + Elite Expansion 最多2張｜"
        f"交易 {combo.get('trades', 0)}｜勝率 {combo.get('win_rate_pct', 0.0):.2f}%｜"
        f"淨利 {combo.get('net_profit_usdt', 0.0):+.2f}U"
    )
    cur_cmp = exit_compare.get("tp1_50pct_plus_ma_remainder", {})
    ctrl_cmp = exit_compare.get("tp1_100pct_full_exit_control", {})
    tp_cmp = exit_compare.get("tp2_tp3_tracking_on_50pct_remainder", {})
    print(
        f"[V56] 同進場出場比較｜TP1全賣：勝率 {ctrl_cmp.get('win_rate_pct', 0.0):.2f}% / "
        f"淨利 {ctrl_cmp.get('net_profit_usdt', 0.0):+.2f}U｜"
        f"TP1賣半+均線：勝率 {cur_cmp.get('win_rate_pct', 0.0):.2f}% / "
        f"淨利 {cur_cmp.get('net_profit_usdt', 0.0):+.2f}U"
    )
    print(
        f"[V56] TP2/TP3追蹤｜TP1命中 {tp_cmp.get('real_tp1_hit_trades', 0)}｜"
        f"TP2 {tp_cmp.get('reached_tp2', 0)}/{tp_cmp.get('distinct_tp2_available', 0)}｜"
        f"TP3 {tp_cmp.get('reached_tp3', 0)}/{tp_cmp.get('distinct_tp3_available', 0)}"
    )
    print(
        f"[V56] 均線快取｜建立 {_V56_MA_CACHE_STATS.get('builds', 0)} 次｜"
        f"重用 {_V56_MA_CACHE_STATS.get('hits', 0)} 次"
    )
    print(
        f"[V56] 符合就做多｜原始多單訊號 {raw_long.get('signals', 0)}｜"
        f"勝率 {raw_long.get('win_rate_pct', 0.0):.2f}%"
    )
    print(
        f"[V56] 符合就做空｜原始空單訊號 {raw_short.get('signals', 0)}｜"
        f"勝率 {raw_short.get('win_rate_pct', 0.0):.2f}%"
    )
    print(
        f"[V56] 族群過濾擋單：{_V56_EXCHANGE_GROUP_REJECTIONS.get('same_group', 0)}"
    )
    return {
        "return_code": rc,
        "combination_metrics": combo,
        "raw_long_candidate_metrics": raw_long,
        "raw_short_candidate_metrics": raw_short,
        "group_rejections": dict(_V56_EXCHANGE_GROUP_REJECTIONS),
        "same_entry_exit_comparison": exit_compare,
        "quality_research": quality_research,
        "ma_cache_stats": dict(_V56_MA_CACHE_STATS),
        "engine_summary": native_summary,
        "segments": segments,
        "v32_core_frozen": True,
        "structure_candidates": len(_STRUCTURE_AUDIT),
        "structure_policy": {
            "breakout_level": "prior 4 completed 4H highs/lows",
            "direct_entry": "next 15m open when structural ignition extension <=0.55x 4H ATR",
            "anti_chase": "if extended, wait first pullback/reclaim up to 12h",
            "stop": "previous 3 completed 4H extreme +/-0.25x 4H ATR",
            "minimum_room_r": 4.0,
            "price_discovery": "V32 selective Trend Runner only",
            "ticker_exceptions": False,
            "lookahead": False,
        },
    }


def audit_v56(exchange_result: dict[str, Any]) -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V56.lower()}.csv"
    invalid_direction = 0
    invalid_rr = 0
    for row in v22._read_csv_rows(trade_path):
        if str(row.get("direction")) not in {"LONG", "SHORT"}:
            invalid_direction += 1
        try:
            if float(row.get("rr1") or 0.0) < 4.0 - 1e-9:
                invalid_rr += 1
        except Exception:
            invalid_rr += 1

    required = {"CRYPTO_LONG", "CRYPTO_SHORT", "US_TOKEN_LONG", "US_TOKEN_SHORT"}
    segments = exchange_result.get("segments", {})
    audit = {
        "version": "V56_FAST",
        "v32_core_frozen": True,
        "structure_parallel_only": True,
        "fast_event_driven_scan": True,
        "cheap_ignition_prefilter_before_targets": True,
        "pullback_search_bars": STRUCTURE_PULLBACK_MAX_BARS,
        "structure_volume_is_hard_gate": False,
        "structure_costs_restored": True,
        "pullback_requires_aligned_v32_regime": False,
        "structure_first_then_context": True,
        "ignition_min_body_atr": STRUCTURE_IGNITION_MIN_BODY_ATR,
        "ignition_min_close_position": STRUCTURE_IGNITION_MIN_CLOSE_POS,
        "structure_stop": "previous 3 completed 4H extreme +/-0.25x 4H ATR",
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required - set(segments)),
        "passed": invalid_direction == 0 and invalid_rr == 0 and required.issubset(set(segments)),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v56_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


# ======================================================================================
# V56 corrected Alpaca-underlying DAILY branch
# ======================================================================================

V56_STOCK_RESEARCH_DAYS = 365
V56_STOCK_WARMUP_DAYS = 300
V56_STOCK_BREAKOUT_DAYS = 4
V56_STOCK_STOP_DAYS = 3
V56_STOCK_STOP_ATR_BUFFER = 0.25
V56_STOCK_DIRECT_MAX_EXTENSION_ATR = 0.55
V56_STOCK_PULLBACK_TOL_ATR = 0.20
V56_STOCK_PULLBACK_MAX_DAYS = 5
V56_STOCK_REENTRY_MAX_DAYS = 10
V56_STOCK_MIN_STOP_PCT = 0.80
V56_STOCK_MAX_STOP_PCT = 12.0
V56_STOCK_MIN_ROOM_R = 4.0
V56_STOCK_MIN_BODY_ATR = 0.20
V56_STOCK_MIN_CLOSE_POSITION = 0.60
V56_STOCK_MIN_BREAK_DISTANCE_ATR = 0.05
V56_STOCK_SCHEME = "V56_ALPACA_UNDERLYING_D1_LIFECYCLE"

# Three-index trend research. Alpaca exposes tradable ETFs rather than the index
# objects themselves, so SPY/QQQ/DIA are used as liquid proxies for the three
# broad US benchmarks. Only completed prior-day data may influence an entry.
V56_INDEX_PROXIES = {
    "sp500": "SPY",
    "nasdaq": "QQQ",
    "dow": "DIA",
}
V56_INDEX_MA_FAST = 5
V56_INDEX_MA_MID = 10
V56_INDEX_MA_SLOW = 20
V56_INDEX_SLOPE_LOOKBACK = 3
V56_INDEX_RETURN_LOOKBACK = 10


def _v56_write_union(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _v56_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if str(r.get("outcome") or "") != "OPEN"]
    wins = [r for r in closed if float(r.get("net_r") or 0.0) > 0]
    losses = [r for r in closed if float(r.get("net_r") or 0.0) < 0]
    return {
        "trades": len(rows),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(rows) - len(closed),
        "win_rate_pct": (100.0 * len(wins) / len(closed)) if closed else 0.0,
        "total_net_r": sum(float(r.get("net_r") or 0.0) for r in rows),
        "profit_usdt": sum(float(r.get("profit_usdt") or 0.0) for r in rows),
    }


def _v56_current_stock_underlyings() -> tuple[list[str], list[dict[str, Any]]]:
    meta = getattr(v22, "_V22_TOKEN_META", {}) or {}
    contracts = list(meta.values())
    symbols = sorted({
        str(r.get("underlying") or "").strip().upper()
        for r in contracts
        if str(r.get("confidence") or "") == "HIGH_CONFIDENCE"
        and str(r.get("underlying") or "").strip()
    })
    return symbols, contracts


def _v56_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Completed US regular-session daily OHLC from Alpaca 15m rows."""
    d = frame.resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"])
    pc = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - pc).abs(),
        (d["low"] - pc).abs(),
    ], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean()
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["break_high"] = d["high"].shift(1).rolling(
        V56_STOCK_BREAKOUT_DAYS, min_periods=V56_STOCK_BREAKOUT_DAYS
    ).max()
    d["break_low"] = d["low"].shift(1).rolling(
        V56_STOCK_BREAKOUT_DAYS, min_periods=V56_STOCK_BREAKOUT_DAYS
    ).min()
    d["stop_low"] = d["low"].shift(1).rolling(
        V56_STOCK_STOP_DAYS, min_periods=V56_STOCK_STOP_DAYS
    ).min()
    d["stop_high"] = d["high"].shift(1).rolling(
        V56_STOCK_STOP_DAYS, min_periods=V56_STOCK_STOP_DAYS
    ).max()
    d["ema20_slope_atr"] = (d["ema20"] - d["ema20"].shift(1)) / d["atr14"]
    d["body_atr"] = (d["close"] - d["open"]).abs() / d["atr14"]
    rng = (d["high"] - d["low"]).clip(lower=1e-12)
    d["close_pos_long"] = (d["close"] - d["low"]) / rng
    d["close_pos_short"] = (d["high"] - d["close"]) / rng
    return d


def _v56_index_daily_state(raw: pd.DataFrame) -> pd.DataFrame:
    """Fast no-look-ahead daily trend state for one index proxy."""
    frame, _, _ = v22.v19.prepare_stock_indicators(raw)
    if frame.empty:
        return pd.DataFrame()
    d = frame.resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["close"])
    c = pd.to_numeric(d["close"], errors="coerce")
    d["ma5"] = c.rolling(V56_INDEX_MA_FAST, min_periods=V56_INDEX_MA_FAST).mean()
    d["ma10"] = c.rolling(V56_INDEX_MA_MID, min_periods=V56_INDEX_MA_MID).mean()
    d["ma20"] = c.rolling(V56_INDEX_MA_SLOW, min_periods=V56_INDEX_MA_SLOW).mean()
    d["ma5_slope"] = d["ma5"] - d["ma5"].shift(V56_INDEX_SLOPE_LOOKBACK)
    d["ret10"] = c / c.shift(V56_INDEX_RETURN_LOOKBACK) - 1.0

    bull_score = (
        (c > d["ma20"]).astype(int)
        + (d["ma5"] > d["ma10"]).astype(int)
        + (d["ma5_slope"] > 0).astype(int)
        + (d["ret10"] > 0).astype(int)
    )
    bear_score = (
        (c < d["ma20"]).astype(int)
        + (d["ma5"] < d["ma10"]).astype(int)
        + (d["ma5_slope"] < 0).astype(int)
        + (d["ret10"] < 0).astype(int)
    )
    state = np.full(len(d), "NEUTRAL", dtype=object)
    state[bull_score.to_numpy() >= 3] = "BULL"
    state[bear_score.to_numpy() >= 3] = "BEAR"
    d["trend_state"] = state
    d["trend_bull_score"] = bull_score.astype(int)
    d["trend_bear_score"] = bear_score.astype(int)
    return d


def _v56_build_three_index_regime(
    fetch_start: pd.Timestamp,
    now: pd.Timestamp,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Fetch SPY/QQQ/DIA once and build an aggregate completed-day regime table."""
    parts: dict[str, pd.DataFrame] = {}
    quality: list[dict[str, Any]] = []
    for key, symbol in V56_INDEX_PROXIES.items():
        try:
            raw = v22.v19.fetch_alpaca_15m(symbol, fetch_start, now)
            state = _v56_index_daily_state(raw)
            if state.empty:
                quality.append({"index_key": key, "proxy": symbol, "status": "EMPTY"})
                continue
            parts[key] = state
            quality.append({
                "index_key": key,
                "proxy": symbol,
                "status": "OK",
                "bars_15m": len(raw),
                "daily_bars": len(state),
                "first_day": state.index.min().isoformat(),
                "last_day": state.index.max().isoformat(),
            })
        except Exception as exc:
            quality.append({
                "index_key": key,
                "proxy": symbol,
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            })

    if not parts:
        return pd.DataFrame(), quality

    all_days = sorted(set().union(*(set(x.index) for x in parts.values())))
    regime = pd.DataFrame(index=pd.DatetimeIndex(all_days))
    for key in V56_INDEX_PROXIES:
        if key not in parts:
            regime[f"{key}_trend"] = "UNKNOWN"
            regime[f"{key}_bull_score"] = np.nan
            regime[f"{key}_bear_score"] = np.nan
            continue
        cur = parts[key].reindex(regime.index)
        regime[f"{key}_trend"] = cur["trend_state"].fillna("UNKNOWN")
        regime[f"{key}_bull_score"] = cur["trend_bull_score"]
        regime[f"{key}_bear_score"] = cur["trend_bear_score"]

    def aggregate(row: pd.Series) -> pd.Series:
        states = [str(row.get(f"{k}_trend") or "UNKNOWN") for k in V56_INDEX_PROXIES]
        bulls = sum(x == "BULL" for x in states)
        bears = sum(x == "BEAR" for x in states)
        known = sum(x in {"BULL", "BEAR", "NEUTRAL"} for x in states)
        if bulls == 3:
            market = "STRONG_BULL"
        elif bears == 3:
            market = "STRONG_BEAR"
        elif bulls >= 2:
            market = "BULL"
        elif bears >= 2:
            market = "BEAR"
        elif known >= 2:
            market = "MIXED"
        else:
            market = "UNKNOWN"
        return pd.Series({
            "index_bull_count": bulls,
            "index_bear_count": bears,
            "index_known_count": known,
            "three_index_regime": market,
        })

    agg = regime.apply(aggregate, axis=1)
    regime = pd.concat([regime, agg], axis=1)
    return regime.sort_index(), quality


def _v56_attach_index_regime(
    row: dict[str, Any],
    regime: pd.DataFrame,
) -> None:
    """Attach the last fully completed daily three-index state before entry."""
    entry = pd.Timestamp(row.get("entry_time"))
    if entry.tzinfo is None:
        entry = entry.tz_localize("UTC")
    else:
        entry = entry.tz_convert("UTC")
    entry_day = entry.normalize()
    if regime.empty:
        rec = None
    else:
        eligible = regime.loc[regime.index < entry_day]
        rec = eligible.iloc[-1] if len(eligible) else None

    if rec is None:
        for key in V56_INDEX_PROXIES:
            row[f"{key}_trend"] = "UNKNOWN"
            row[f"{key}_bull_score"] = None
            row[f"{key}_bear_score"] = None
        row["index_bull_count"] = 0
        row["index_bear_count"] = 0
        row["three_index_regime"] = "UNKNOWN"
        row["three_index_direction_match"] = "UNKNOWN"
        return

    for key in V56_INDEX_PROXIES:
        row[f"{key}_trend"] = str(rec.get(f"{key}_trend") or "UNKNOWN")
        row[f"{key}_bull_score"] = rec.get(f"{key}_bull_score")
        row[f"{key}_bear_score"] = rec.get(f"{key}_bear_score")
    row["index_bull_count"] = int(rec.get("index_bull_count") or 0)
    row["index_bear_count"] = int(rec.get("index_bear_count") or 0)
    market = str(rec.get("three_index_regime") or "UNKNOWN")
    row["three_index_regime"] = market
    direction = str(row.get("direction") or "")
    aligned = (
        direction == "LONG" and market in {"BULL", "STRONG_BULL"}
    ) or (
        direction == "SHORT" and market in {"BEAR", "STRONG_BEAR"}
    )
    counter = (
        direction == "SHORT" and market in {"BULL", "STRONG_BULL"}
    ) or (
        direction == "LONG" and market in {"BEAR", "STRONG_BEAR"}
    )
    row["three_index_direction_match"] = "ALIGNED" if aligned else "COUNTER" if counter else "MIXED"


def _v56_keep_strong_index_veto(row: dict[str, Any]) -> bool:
    market = str(row.get("three_index_regime") or "UNKNOWN")
    direction = str(row.get("direction") or "")
    if market == "STRONG_BULL" and direction == "SHORT":
        return False
    if market == "STRONG_BEAR" and direction == "LONG":
        return False
    return True


def _v56_keep_majority_index_trend(row: dict[str, Any]) -> bool:
    market = str(row.get("three_index_regime") or "UNKNOWN")
    direction = str(row.get("direction") or "")
    if market in {"BULL", "STRONG_BULL"}:
        return direction == "LONG"
    if market in {"BEAR", "STRONG_BEAR"}:
        return direction == "SHORT"
    return True


def _v56_index_regime_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    regimes = ["STRONG_BULL", "BULL", "MIXED", "BEAR", "STRONG_BEAR", "UNKNOWN"]
    for regime_name in regimes:
        for direction in ("LONG", "SHORT"):
            part = [
                r for r in rows
                if str(r.get("three_index_regime") or "UNKNOWN") == regime_name
                and str(r.get("direction") or "") == direction
            ]
            if not part:
                continue
            m = _v56_metrics(part)
            out.append({"three_index_regime": regime_name, "direction": direction, **m})
    return out


def _v56_next_15m_index(frame: pd.DataFrame, after_ts: pd.Timestamp) -> int | None:
    pos = int(frame.index.searchsorted(after_ts, side="right"))
    return pos if 0 <= pos < len(frame) else None


def _v56_stock_targets(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    pivot_highs: Any,
    pivot_lows: Any,
):
    """Real stock structure first; price discovery Runner only as fallback."""
    real = v22.stock_major_structure_targets_both(
        frame, signal_i, direction, entry, stop_distance,
        pivot_highs, pivot_lows, minimum_room_r=V56_STOCK_MIN_ROOM_R,
    )
    if real is not None and float(real[3]) >= V56_STOCK_MIN_ROOM_R - 1e-9:
        return real, "REAL_STOCK_H1_D1_STRUCTURE"

    # Runner fallback uses inherited V32 quality rules. Reject any accidental
    # non-runner real target returned by that helper; only the sentinel is accepted.
    old_target = getattr(v32, "_CURRENT_TARGET", None)
    try:
        runner = v32.selective_structure_or_runner_targets(
            frame, signal_i, direction, entry, stop_distance,
            pivot_highs, pivot_lows, minimum_room_r=V56_STOCK_MIN_ROOM_R,
        )
    finally:
        v32._CURRENT_TARGET = old_target
    if runner is None:
        return None, ""
    if float(runner[3]) < v32.RUNNER_SENTINEL_THRESHOLD_R:
        return None, ""
    return runner, "PRICE_DISCOVERY_RUNNER"


def _v56_sim_stock_trade(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    targets: tuple[Any, ...],
    target_mode: str,
) -> dict[str, Any]:
    stop_distance = abs(entry - stop)
    tp1, tp2, tp3, rr1, rr2, rr3, _ = targets
    if target_mode == "PRICE_DISCOVERY_RUNNER":
        result = simulate_fixed_stop_runner_v56(
            frame, entry_i, direction, entry, stop, float(tp1), stop_distance
        )
    else:
        result = simulate_post_tp1_weakness_v56(
            frame,
            entry_i,
            direction,
            entry,
            stop,
            float(tp1),
            stop_distance,
        )

    result["tp1"] = float(tp1)
    result["tp2"] = float(tp2)
    result["tp3"] = float(tp3)
    result["rr1"] = float(rr1)
    result["rr2"] = float(rr2)
    result["rr3"] = float(rr3)
    result["target_mode"] = target_mode
    return result


def _v56_make_stock_candidate(
    symbol: str,
    frame: pd.DataFrame,
    daily: pd.DataFrame,
    dpos: int,
    direction: str,
    breakout_line: float,
    setup_type: str,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
) -> dict[str, Any] | None:
    drow = daily.iloc[dpos]
    atr = float(drow["atr14"])
    if not math.isfinite(atr) or atr <= 0:
        return None

    # Signal is valid only after the daily regular-session bar is complete.
    day = pd.Timestamp(daily.index[dpos])
    same_day = frame.index.normalize() == day.normalize()
    day_indices = np.flatnonzero(same_day)
    if not len(day_indices):
        return None
    signal_i = int(day_indices[-1])
    entry_i = _v56_next_15m_index(frame, frame.index[signal_i])
    if entry_i is None:
        return None

    entry = float(frame["open"].iat[entry_i])
    if direction == "LONG":
        base = float(drow["stop_low"])
        stop = base - V56_STOCK_STOP_ATR_BUFFER * atr
        stop_distance = entry - stop
        extension = (entry - breakout_line) / atr
    else:
        base = float(drow["stop_high"])
        stop = base + V56_STOCK_STOP_ATR_BUFFER * atr
        stop_distance = stop - entry
        extension = (breakout_line - entry) / atr

    if not all(math.isfinite(x) for x in (entry, stop, stop_distance, extension)) or stop_distance <= 0:
        return None
    stop_pct = stop_distance / entry * 100.0
    if stop_pct < V56_STOCK_MIN_STOP_PCT or stop_pct > V56_STOCK_MAX_STOP_PCT:
        return None
    if extension > 0.70:
        return None

    targets, target_mode = _v56_stock_targets(
        frame, signal_i, direction, entry, stop_distance, pivot_highs, pivot_lows
    )
    if targets is None:
        return None

    result = _v56_sim_stock_trade(
        frame, entry_i, direction, entry, stop, targets, target_mode
    )
    exit_i = int(result["exit_i"])
    h = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    l = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    if direction == "LONG":
        mfe_r = (np.nanmax(h[entry_i:exit_i + 1]) - entry) / stop_distance
        mae_r = (entry - np.nanmin(l[entry_i:exit_i + 1])) / stop_distance
    else:
        mfe_r = (entry - np.nanmin(l[entry_i:exit_i + 1])) / stop_distance
        mae_r = (np.nanmax(h[entry_i:exit_i + 1]) - entry) / stop_distance

    signal_time = frame.index[signal_i]
    return {
        "exchange": "AlpacaUnderlying",
        "symbol": symbol,
        "base": symbol,
        "underlying": symbol,
        "direction": direction,
        "signal_time": signal_time.isoformat(),
        "entry_time": frame.index[entry_i].isoformat(),
        "exit_time": frame.index[exit_i].isoformat(),
        "period": _period_name(signal_time, research_start),
        "scheme": V56_STOCK_SCHEME,
        "setup_type": setup_type,
        "breakout_line": breakout_line,
        "entry": entry,
        "stop": stop,
        "structure_stop_base": base,
        "stop_pct": stop_pct,
        "entry_extension_atr": extension,
        "tp1": result["tp1"],
        "tp2": result["tp2"],
        "tp3": result["tp3"],
        "rr1": result["rr1"],
        "rr2": result["rr2"],
        "rr3": result["rr3"],
        "exit_price": float(result["exit_price"]),
        "outcome": result["outcome"],
        "exit_reason": result["exit_reason"],
        "timed_out": result["timed_out"],
        "gross_r": float(result["gross_r"]),
        "cost_r": float(result["cost_r"]),
        "net_r": float(result["net_r"]),
        "mfe_r": float(mfe_r),
        "mae_r": float(mae_r),
        "target_mode": target_mode,
        "structure_timeframe": "1D_COMPLETED",
        "execution_timeframe": "15m",
        "data_source": f"Alpaca:{v22.v19.ALPACA_FEED}",
        "research_proxy": "UNDERLYING_HISTORY",
    }


def _v56_stock_candidates_for_symbol(
    symbol: str,
    raw: pd.DataFrame,
    research_start: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    frame, pivot_highs, pivot_lows = v22.v19.prepare_stock_indicators(raw)
    v32._add_adaptive_market_heat_columns(frame)
    daily = _v56_daily_frame(frame)
    rows: list[dict[str, Any]] = []
    funnel = {
        "daily_bars": len(daily),
        "direct_breaks": 0,
        "pullback_setups": 0,
        "published": 0,
        "reentries": 0,
    }
    if len(daily) < 60:
        return rows, funnel

    # Track one setup per direction and one re-entry after a true STOP.
    last_publish_day = {"LONG": None, "SHORT": None}
    stopped: dict[str, dict[str, Any] | None] = {"LONG": None, "SHORT": None}

    for dpos in range(20, len(daily) - 1):
        day_ts = pd.Timestamp(daily.index[dpos])
        # Signal day itself must be inside research interval.
        day_end = day_ts + pd.Timedelta(days=1)
        if day_end < research_start:
            continue

        r = daily.iloc[dpos]
        prev = daily.iloc[dpos - 1]
        atr = float(r["atr14"])
        if not math.isfinite(atr) or atr <= 0:
            continue

        close = float(r["close"])
        op = float(r["open"])
        bh, bl = float(r["break_high"]), float(r["break_low"])
        body_atr = float(r["body_atr"])
        cpl = float(r["close_pos_long"])
        cps = float(r["close_pos_short"])
        slope = float(r["ema20_slope_atr"])
        if not all(math.isfinite(x) for x in (close, op, bh, bl, body_atr, cpl, cps, slope)):
            continue

        long_break_dist = (close - bh) / atr
        short_break_dist = (bl - close) / atr
        bull_veto_ok = close >= float(r["ema20"]) - 0.15 * atr and slope >= -0.10
        bear_veto_ok = close <= float(r["ema20"]) + 0.15 * atr and slope <= 0.10

        direct: list[tuple[str, float]] = []
        if (
            bull_veto_ok and close > bh and float(prev["close"]) <= float(prev["break_high"])
            and long_break_dist >= V56_STOCK_MIN_BREAK_DISTANCE_ATR
            and body_atr >= V56_STOCK_MIN_BODY_ATR and cpl >= V56_STOCK_MIN_CLOSE_POSITION
        ):
            direct.append(("LONG", bh))
        if (
            bear_veto_ok and close < bl and float(prev["close"]) >= float(prev["break_low"])
            and short_break_dist >= V56_STOCK_MIN_BREAK_DISTANCE_ATR
            and body_atr >= V56_STOCK_MIN_BODY_ATR and cps >= V56_STOCK_MIN_CLOSE_POSITION
        ):
            direct.append(("SHORT", bl))

        for direction, line in direct:
            funnel["direct_breaks"] += 1
            candidate = None
            ext_close = ((close - line) / atr) if direction == "LONG" else ((line - close) / atr)

            if ext_close <= V56_STOCK_DIRECT_MAX_EXTENSION_ATR:
                candidate = _v56_make_stock_candidate(
                    symbol, frame, daily, dpos, direction, line, "DIRECT_BREAK",
                    research_start, pivot_highs, pivot_lows
                )

            # If direct entry is too extended or fails geometry/target, wait for the
            # first completed-daily pullback/reclaim in the next five trading bars.
            if candidate is None:
                endp = min(len(daily) - 1, dpos + V56_STOCK_PULLBACK_MAX_DAYS)
                for j in range(dpos + 1, endp + 1):
                    pr = daily.iloc[j]
                    patr = float(pr["atr14"])
                    if not math.isfinite(patr) or patr <= 0:
                        continue
                    if direction == "LONG":
                        ok = (
                            float(pr["low"]) <= line + V56_STOCK_PULLBACK_TOL_ATR * patr
                            and float(pr["close"]) > line
                            and float(pr["close"]) > float(pr["open"])
                            and float(pr["close_pos_long"]) >= 0.55
                            and float(pr["low"]) > float(pr["stop_low"]) - V56_STOCK_STOP_ATR_BUFFER * patr
                        )
                    else:
                        ok = (
                            float(pr["high"]) >= line - V56_STOCK_PULLBACK_TOL_ATR * patr
                            and float(pr["close"]) < line
                            and float(pr["close"]) < float(pr["open"])
                            and float(pr["close_pos_short"]) >= 0.55
                            and float(pr["high"]) < float(pr["stop_high"]) + V56_STOCK_STOP_ATR_BUFFER * patr
                        )
                    if ok:
                        funnel["pullback_setups"] += 1
                        candidate = _v56_make_stock_candidate(
                            symbol, frame, daily, j, direction, line, "FIRST_PULLBACK_RECLAIM",
                            research_start, pivot_highs, pivot_lows
                        )
                        if candidate is not None:
                            break

            if candidate is None:
                continue

            # Avoid duplicate same-direction publication on the same daily episode.
            ep = last_publish_day[direction]
            cand_day = pd.Timestamp(candidate["signal_time"]).normalize()
            if ep is not None and (cand_day - ep).days < 2:
                continue

            rows.append(candidate)
            funnel["published"] += 1
            last_publish_day[direction] = cand_day

            # One structural re-entry after a true STOP.
            if str(candidate["exit_reason"]) == "STOP":
                exit_day = pd.Timestamp(candidate["exit_time"]).normalize()
                prev_swing = float(candidate["structure_stop_base"])
                search_positions = [
                    k for k in range(dpos + 1, min(len(daily), dpos + 1 + V56_STOCK_REENTRY_MAX_DAYS))
                    if pd.Timestamp(daily.index[k]).normalize() > exit_day
                ]
                for k in search_positions:
                    rr = daily.iloc[k]
                    ratr = float(rr["atr14"])
                    if not math.isfinite(ratr) or ratr <= 0:
                        continue
                    if direction == "LONG":
                        recent_low = float(daily["low"].iloc[max(0, k-3):k].min())
                        ok = (
                            float(rr["close"]) >= line + 0.05 * ratr
                            and float(rr["close"]) > float(rr["open"])
                            and recent_low > prev_swing
                        )
                    else:
                        recent_high = float(daily["high"].iloc[max(0, k-3):k].max())
                        ok = (
                            float(rr["close"]) <= line - 0.05 * ratr
                            and float(rr["close"]) < float(rr["open"])
                            and recent_high < prev_swing
                        )
                    if not ok:
                        continue
                    re = _v56_make_stock_candidate(
                        symbol, frame, daily, k, direction, line, "STRUCTURAL_REENTRY",
                        research_start, pivot_highs, pivot_lows
                    )
                    if re is not None:
                        rows.append(re)
                        funnel["published"] += 1
                        funnel["reentries"] += 1
                        break

    return rows, funnel


def run_alpaca_underlying_d1_v56() -> dict[str, Any]:
    ALPACA_D1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols, contracts = _v56_current_stock_underlyings()
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "current_token_contracts.csv", contracts)
    _v56_write_union(
        ALPACA_D1_OUTPUT_DIR / "current_token_underlyings.csv",
        [{"underlying": x} for x in symbols],
    )
    if not symbols:
        raise RuntimeError("交易所 discovery 沒有 HIGH_CONFIDENCE 美股代幣 underlying。")

    now = pd.Timestamp.now(tz="UTC").floor("15min")
    research_start = now - pd.Timedelta(days=V56_STOCK_RESEARCH_DAYS)
    fetch_start = research_start - pd.Timedelta(days=V56_STOCK_WARMUP_DAYS)

    print("\n[V56-US] 開始：交易所目前美股代幣 -> Alpaca underlying 1年日線")
    print(
        f"[V56-US] {len(contracts)} contracts / {len(symbols)} underlyings｜"
        f"feed={v22.v19.ALPACA_FEED}"
    )
    print("[V56-US] 三大指數研究：SPY / QQQ / DIA，只用進場前已完成日線判斷多空")
    index_regime, index_quality = _v56_build_three_index_regime(fetch_start, now)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "three_index_data_quality_v56.csv", index_quality)
    if not index_regime.empty:
        index_out = index_regime.reset_index().rename(columns={"index": "date"})
        _v56_write_union(
            ALPACA_D1_OUTPUT_DIR / "three_index_daily_regime_v56.csv",
            index_out.to_dict("records"),
        )

    all_rows: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    funnel_total: dict[str, int] = {}

    old_fee = engine.FEE_PER_SIDE
    old_slip = engine.SLIPPAGE_PER_SIDE
    engine.FEE_PER_SIDE = v22.v19.STOCK_FEE_PER_SIDE
    engine.SLIPPAGE_PER_SIDE = v22.v19.STOCK_SLIPPAGE_PER_SIDE
    try:
        for idx, symbol in enumerate(symbols, start=1):
            try:
                raw = v22.v19.fetch_alpaca_15m(symbol, fetch_start, now)
                research_rows = int((raw.index >= research_start).sum())
                if research_rows < 200:
                    quality.append({
                        "symbol": symbol, "bars": len(raw),
                        "research_bars": research_rows, "status": "TOO_SHORT"
                    })
                    print(f"[V56-US {idx}/{len(symbols)}] {symbol} SKIP")
                    continue
                rows, funnel = _v56_stock_candidates_for_symbol(
                    symbol, raw, research_start
                )
                all_rows.extend(rows)
                for key, val in funnel.items():
                    funnel_total[key] = funnel_total.get(key, 0) + int(val)
                quality.append({
                    "symbol": symbol,
                    "bars": len(raw),
                    "research_bars": research_rows,
                    "candidates": len(rows),
                    "status": "OK",
                    "first_bar": raw.index.min().isoformat() if len(raw) else "",
                    "last_bar": raw.index.max().isoformat() if len(raw) else "",
                })
                print(
                    f"[V56-US {idx}/{len(symbols)}] {symbol} "
                    f"research={research_rows} candidates={len(rows)}"
                )
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                errors.append({"symbol": symbol, "error": msg})
                print(f"[V56-US {idx}/{len(symbols)}] {symbol} ERROR {msg}")
    finally:
        engine.FEE_PER_SIDE = old_fee
        engine.SLIPPAGE_PER_SIDE = old_slip

    # Attach broad group labels + no-look-ahead three-index regime before portfolio experiments.
    for row in all_rows:
        row["asset_group"] = v56_asset_group(
            str(row.get("symbol") or row.get("underlying") or ""),
            force_stock=True,
        )
        _v56_attach_index_regime(row, index_regime)

    stock_group_catalog = sorted({
        (str(row.get("symbol") or row.get("underlying") or ""), str(row.get("asset_group") or ""))
        for row in all_rows
    })
    _v56_write_union(
        ALPACA_D1_OUTPUT_DIR / "group_catalog_stocks_v56.csv",
        [{"symbol": sym, "asset_group": grp} for sym, grp in stock_group_catalog],
    )

    # One candidate generation, three portfolio experiments. This avoids
    # re-downloading/recomputing 258 underlyings and avoids choosing a winner in advance.
    variants = {
        "FORMAL_COMBINATION": list(all_rows),
        "ALL": list(all_rows),
        "INDEX_STRONG_3OF3_VETO": [r for r in all_rows if _v56_keep_strong_index_veto(r)],
        "INDEX_MAJORITY_2OF3_TREND": [r for r in all_rows if _v56_keep_majority_index_trend(r)],
        "FIRST_PULLBACK_ONLY": [
            r for r in all_rows if str(r.get("setup_type")) == "FIRST_PULLBACK_RECLAIM"
        ],
        "SHORT_PULLBACK_OR_REENTRY": [
            r for r in all_rows
            if str(r.get("direction")) == "SHORT"
            and str(r.get("setup_type")) in {"FIRST_PULLBACK_RECLAIM", "STRUCTURAL_REENTRY"}
        ],
    }
    portfolio_results: dict[str, Any] = {}
    trade_sets: dict[str, list[dict[str, Any]]] = {}
    for variant, candidate_rows in variants.items():
        selected, portfolio = apply_stock_portfolio_v56(candidate_rows)
        for row in selected:
            row["portfolio_variant"] = variant
        trade_sets[variant] = selected
        portfolio_results[variant] = portfolio
        _v56_write_union(
            ALPACA_D1_OUTPUT_DIR / f"trades_{variant.lower()}.csv", selected
        )

    trades = trade_sets["FORMAL_COMBINATION"]
    portfolio = portfolio_results["FORMAL_COMBINATION"]
    long_rows = [r for r in all_rows if r["direction"] == "LONG"]
    short_rows = [r for r in all_rows if r["direction"] == "SHORT"]
    long_trades = [r for r in trades if r["direction"] == "LONG"]
    short_trades = [r for r in trades if r["direction"] == "SHORT"]

    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "candidates_all.csv", all_rows)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "candidates_long.csv", long_rows)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "candidates_short.csv", short_rows)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "trades_all.csv", trades)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "trades_long.csv", long_trades)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "trades_short.csv", short_trades)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "data_quality.csv", quality)
    _v56_write_union(ALPACA_D1_OUTPUT_DIR / "errors.csv", errors)
    _v56_write_union(
        ALPACA_D1_OUTPUT_DIR / "funnel.csv",
        [{"stage": k, "count": v} for k, v in sorted(funnel_total.items())],
    )

    stock_exit_compare = _v56_same_entry_exit_report(
        trades, ALPACA_D1_OUTPUT_DIR, "stocks_formal"
    )
    index_regime_breakdown = _v56_index_regime_metrics(trades)
    _v56_write_union(
        ALPACA_D1_OUTPUT_DIR / "formal_trades_by_three_index_regime_v56.csv",
        index_regime_breakdown,
    )
    _v56_write_union(
        ALPACA_D1_OUTPUT_DIR / "formal_trades_with_three_index_regime_v56.csv",
        trades,
    )

    summary = {
        "version": "V56",
        "branch": "CURRENT_STOCK_TOKENS_TO_ALPACA_UNDERLYING_D1",
        "contracts": len(contracts),
        "underlyings": len(symbols),
        "symbols_ok": sum(r.get("status") == "OK" for r in quality),
        "research_days": V56_STOCK_RESEARCH_DAYS,
        "structure_timeframe": "COMPLETED_1D",
        "execution_timeframe": "ALPACA_15M_REGULAR_SESSION",
        "proxy_caveat": (
            "Underlying history before token listing is research-only; "
            "historical token liquidity and availability are not reconstructed."
        ),
        "rules": {
            "breakout_days": V56_STOCK_BREAKOUT_DAYS,
            "stop_days": V56_STOCK_STOP_DAYS,
            "stop_atr_buffer": V56_STOCK_STOP_ATR_BUFFER,
            "minimum_room_r": V56_STOCK_MIN_ROOM_R,
            "reentry_after_stop_max": 1,
            "directions": ["LONG", "SHORT"],
        },
        "candidate_metrics": {
            "all": _v56_metrics(all_rows),
            "long": _v56_metrics(long_rows),
            "short": _v56_metrics(short_rows),
        },
        "raw_long_candidate_metrics": _v56_metrics(long_rows),
        "raw_short_candidate_metrics": _v56_metrics(short_rows),
        "formal_combination_metrics": _v56_metrics(trades),
        "same_entry_exit_comparison": stock_exit_compare,
        "three_index_research": {
            "proxies": V56_INDEX_PROXIES,
            "rule": "entry uses only the last completed prior-day proxy state; 4-vote fast trend per proxy; aggregate 3/3 or 2/3",
            "data_quality": index_quality,
            "formal_regime_breakdown": index_regime_breakdown,
            "strong_3of3_veto_metrics": _v56_metrics(trade_sets["INDEX_STRONG_3OF3_VETO"]),
            "majority_2of3_trend_metrics": _v56_metrics(trade_sets["INDEX_MAJORITY_2OF3_TREND"]),
        },
        "trade_metrics": {
            "all": _v56_metrics(trades),
            "long": _v56_metrics(long_trades),
            "short": _v56_metrics(short_trades),
        },
        "portfolio": portfolio,
        "portfolio_variants": {
            name: {
                "candidate_count": len(variants[name]),
                "trade_metrics": _v56_metrics(trade_sets[name]),
                "portfolio": portfolio_results[name],
            }
            for name in variants
        },
        "errors_count": len(errors),
    }
    (ALPACA_D1_OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    base_port = portfolio_results.get("FORMAL_COMBINATION", {})
    strong_port = portfolio_results.get("INDEX_STRONG_3OF3_VETO", {})
    majority_port = portfolio_results.get("INDEX_MAJORITY_2OF3_TREND", {})
    print(
        f"[V56-US] 三大指數組合｜原版 {base_port.get('trades', 0)}筆 / "
        f"勝率 {base_port.get('win_rate_pct', 0.0):.2f}% / {base_port.get('net_profit_usdt', 0.0):+.2f}U｜"
        f"3/3強趨勢反向禁做 {strong_port.get('trades', 0)}筆 / "
        f"勝率 {strong_port.get('win_rate_pct', 0.0):.2f}% / {strong_port.get('net_profit_usdt', 0.0):+.2f}U｜"
        f"2/3多數順勢 {majority_port.get('trades', 0)}筆 / "
        f"勝率 {majority_port.get('win_rate_pct', 0.0):.2f}% / {majority_port.get('net_profit_usdt', 0.0):+.2f}U"
    )
    stock_cur = stock_exit_compare.get("tp1_50pct_plus_ma_remainder", {})
    stock_ctrl = stock_exit_compare.get("tp1_100pct_full_exit_control", {})
    stock_tp = stock_exit_compare.get("tp2_tp3_tracking_on_50pct_remainder", {})
    print(
        f"[V56-US] 同進場出場比較｜TP1全賣：勝率 {stock_ctrl.get('win_rate_pct', 0.0):.2f}% / "
        f"淨利 {stock_ctrl.get('net_profit_usdt', 0.0):+.2f}U｜"
        f"TP1賣半+均線：勝率 {stock_cur.get('win_rate_pct', 0.0):.2f}% / "
        f"淨利 {stock_cur.get('net_profit_usdt', 0.0):+.2f}U"
    )
    print(
        f"[V56-US] TP2/TP3追蹤｜TP1命中 {stock_tp.get('real_tp1_hit_trades', 0)}｜"
        f"TP2 {stock_tp.get('reached_tp2', 0)}/{stock_tp.get('distinct_tp2_available', 0)}｜"
        f"TP3 {stock_tp.get('reached_tp3', 0)}/{stock_tp.get('distinct_tp3_available', 0)}"
    )
    return summary


def main() -> int:
    print("CryptoRadar V56 啟動")
    print("A. 幣圈/交易所：恢復 V39 Structural Ignition 核心")
    print("B. 美股：目前交易所美股代幣 -> Alpaca underlying 1年完成日線")
    print("C. 正式組合：V39核心3槽先保護；額外第4/5槽只有Elite訊號可進，額外單做族群去重")
    print("D. V56研究：修空資料錯誤；美股三大指數順勢A/B；核心單Elite-like品質分層")
    print("E. V56：保留TP1全賣 vs 賣半+5/10日線與TP2/TP3追蹤")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 96)
    print("[V56-A] 開始 V39-style 幣圈 / 交易所")
    exchange_result = run_exchange_branch_v56()

    print("\n" + "=" * 96)
    stock_result = run_alpaca_underlying_d1_v56()
    stock_raw_long = stock_result.get("raw_long_candidate_metrics", {})
    stock_raw_short = stock_result.get("raw_short_candidate_metrics", {})
    print(
        f"[V56-US] 符合就做多｜原始多單訊號 {stock_raw_long.get('trades', 0)}｜"
        f"勝率 {stock_raw_long.get('win_rate_pct', 0.0):.2f}%"
    )
    print(
        f"[V56-US] 符合就做空｜原始空單訊號 {stock_raw_short.get('trades', 0)}｜"
        f"勝率 {stock_raw_short.get('win_rate_pct', 0.0):.2f}%"
    )

    combined = {
        "version": "V56",
        "exchange_branch": exchange_result,
        "alpaca_underlying_d1": stock_result,
        "research_design": (
            "Crypto uses the protected V39-style core plus Elite expansion. US-stock candidates are generated once, then baseline, 3/3 strong-trend veto, and 2/3 majority-trend portfolios are compared side-by-side. V56 also preserves the same-entry TP1 exit comparison and adds observational core-quality buckets."
        ),
    }
    (OUTPUT_DIR / "summary_v56.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V56 完成")
    print(f"幣圈/交易所：{MARKETS_OUTPUT_DIR}")
    print(f"Alpaca美股日線：{ALPACA_D1_OUTPUT_DIR}")
    print(f"總結：{OUTPUT_DIR / 'summary_v56.json'}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
