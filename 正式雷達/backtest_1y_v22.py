#!/usr/bin/env python3
"""CryptoRadar V22 strict three-exchange US-stock-token discovery backtest.

V22 strictly identifies US-stock tokens on Binance, Bitget and BingX and keeps each exchange listing separately.

Unlike V21, a plain ticker collision with an Alpaca stock symbol is NOT sufficient.
A contract must have strong venue evidence (explicit stock/TradFi metadata, a known
BingX NCSK wrapper, or a vetted legacy tokenized-stock symbol) and then pass an
Alpaca underlying check. Ambiguous direct ticker collisions are written to a
REJECTED_COLLISION audit file and never enter the backtest.

CRYPTO / TOKENIZED-TRADFI branch
---------------------------------
* Binance / Bitget / BingX USDT linear swaps discovered by the existing engine.
* CRYPTO and TOKENIZED_TRADFI are both kept.
* LONG and SHORT are both enabled.
* Relative-volume sequence is unchanged:
  breakout/breakdown >=1.30x own previous-20-bar median; retest 0.60x-1.20x;
  second confirmation >=1.00x and >=1.10x retest volume.
* Real pre-entry structure TP >=4R, no maximum.
* Funding is analysis-only; tokenized TradFi does not use funding as a gate.
* +2R -> BE, +3R -> +1R, +5R -> +3R; no time exit.

NATIVE US-STOCK branch
----------------------
* Alpaca native US-equity 15-minute regular-session bars.
* By default discovers ALL currently-active tradable US-equity symbols from Alpaca.
  Set V22_STOCK_SYMBOLS to a comma-separated list to override, or V22_MAX_STOCKS
  to a positive integer for a temporary capped research run. 0 means no cap.
* LONG and SHORT are both enabled.
* Same relative-volume and confirmation sequence as above.
* H1 + completed-daily pre-entry major structures are used as targets:
  resistance for LONG, support for SHORT; TP >=4R, no maximum.
* Same profit-protection ladder and no time exit.

Important research caveat: Alpaca asset discovery is a current-active universe, so
native-equity results can have survivorship bias for delisted names. Historical
short-borrow availability is not reconstructed; current 'shortable' is recorded
only as metadata and never used to create/erase historical signals.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path.home() / "CryptoRadar"
V19_PATH = ROOT / "backtest_1y_v19.py"
if not V19_PATH.exists():
    V19_PATH = Path(__file__).with_name("backtest_1y_v19.py")
if not V19_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v19.py；請把V19與V22放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v19_components_v22", V19_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V19：{V19_PATH}")
v19 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v19
spec.loader.exec_module(v19)

engine = v19.engine
v17 = v19.v17

# ===== V22 paths =====
OUTPUT_DIR = ROOT / "backtest_1y_v22_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"

# ===== Native-stock universe =====
ALPACA_ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
V22_MAX_STOCKS = int(os.getenv("V22_MAX_STOCKS", "0") or 0)  # 0 = all
V22_SYMBOL_OVERRIDE = [
    s.strip().upper()
    for s in os.getenv("V22_STOCK_SYMBOLS", "").split(",")
    if s.strip()
]

# One research scheme only. V22 keeps the common >=4R rule;
# direction/market segments are compared after the exact same >=4R rule.
SCHEME_V22 = "V22_STRICT_THREE_EXCHANGE_BIDIRECTIONAL"
V22_SCHEME_CONFIG = {
    "minimum_room_r": 2.0,
    "confirm_volume": 1.0,
    "maximum_chase_score": None,
    "maximum_tp1_r": None,
    "score_order": "ASC",
}


# ===== V22 dynamic three-exchange US-stock-token discovery =====
V22_RUN_NATIVE_STOCKS = os.getenv("V22_RUN_NATIVE_STOCKS", "0").strip().lower() in {"1", "true", "yes", "on"}
V22_TOKEN_CATALOG_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_catalog.csv"
V22_REJECTED_COLLISIONS_PATH = MARKETS_OUTPUT_DIR / "rejected_stock_ticker_collisions.csv"
V22_DISCOVERY_SUMMARY_PATH = MARKETS_OUTPUT_DIR / "us_stock_tokens_discovery_summary.json"
_V22_TOKEN_KEYS: set[tuple[str, str]] = set()
_V22_TOKEN_META: dict[tuple[str, str], dict[str, Any]] = {}
_V22_REJECTED: list[dict[str, Any]] = []
_V22_ALPACA_SYMBOLS: set[str] | None = None

# Avoid obvious crypto/equity ticker collisions when a venue uses an unwrapped base.
_V22_CRYPTO_COLLISION_BLOCKLIST = {
    "BTC","ETH","BNB","SOL","XRP","DOGE","ADA","TRX","AVAX","LINK","DOT","LTC","BCH",
    "SUI","APT","ARB","OP","NEAR","ATOM","FIL","ETC","UNI","AAVE","MKR","LDO","INJ",
    "TIA","SEI","TON","ENA","JUP","FET","WIF","S","MOVE","ONDO","ICP","HBAR","XLM",
}
_V22_TOKEN_MARKERS = (
    "stock", "equity", "xstock", "xstocks", "tokenized stock", "tokenized equity",
    "tradfi", "traditional finance", "nasdaq", "nyse", "etf", "rwa stock",
)

# Vetted legacy symbols from the older engine are accepted only on Bitget when
# their underlying also exists in Alpaca. This preserves the known Bitget set
# without allowing arbitrary same-ticker crypto collisions.
_V22_VETTED_BITGET_LEGACY = set(getattr(engine, "TOKENIZED_TRADFI_BASES", set()))

def _v22_explicit_tradfi_metadata(market: dict[str, Any]) -> bool:
    info = market.get("info") or {}
    parts = [
        market.get("id"), market.get("symbol"), market.get("base"),
        market.get("type"), market.get("subType"), market.get("category"),
        market.get("description"), market.get("name"), info,
    ]
    text = json.dumps(parts, ensure_ascii=False, default=str).lower()
    return any(marker in text for marker in _V22_TOKEN_MARKERS)



def _v22_all_alpaca_equity_symbols() -> set[str]:
    global _V22_ALPACA_SYMBOLS
    if _V22_ALPACA_SYMBOLS is not None:
        return _V22_ALPACA_SYMBOLS
    key, secret = v19.alpaca_credentials()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "CryptoRadar-V22/1.0",
    }
    params = urllib.parse.urlencode({"status": "active", "asset_class": "us_equity"})
    payload = v19._request_json(ALPACA_ASSETS_URL + "?" + params, headers)
    symbols: set[str] = set()
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict) or not bool(item.get("tradable")):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if symbol and "/" not in symbol and " " not in symbol:
                symbols.add(symbol)
    _V22_ALPACA_SYMBOLS = symbols
    return symbols


def _v22_compact(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.]+", "", str(value or "").upper())


def _v22_token_underlying(
    exchange_name: str,
    symbol: str,
    market: dict[str, Any],
    stocks: set[str],
) -> tuple[str | None, str, str | None]:
    """Return (underlying, match_reason, rejected_collision).

    Strict rule: Alpaca ticker equality alone is never enough.
    """
    raw = engine.canonical_base(symbol).upper()
    compact = _v22_compact(raw)

    # BingX official-style stock wrapper, e.g. NCSKTSLA2USD.
    m = re.fullmatch(r"NCSK(.+?)2USD", compact)
    if m:
        underlying = m.group(1)
        if underlying in stocks:
            return underlying, "HIGH_CONFIDENCE_NCSK_WRAPPER", None
        return None, "", f"wrapper_underlying_not_in_alpaca:{underlying}"

    candidates = [compact]
    if compact.endswith("2USD"):
        candidates.append(compact[:-4])
    if compact.endswith("X"):
        candidates.append(compact[:-1])
    candidates = list(dict.fromkeys(c for c in candidates if c))

    explicit_meta = _v22_explicit_tradfi_metadata(market)
    for candidate in candidates:
        if candidate not in stocks:
            continue
        if candidate in _V22_CRYPTO_COLLISION_BLOCKLIST:
            return None, "", f"blocked_known_crypto_collision:{candidate}"

        # Strongest generic rule: venue explicitly describes this contract as stock/TradFi.
        if explicit_meta:
            return candidate, "HIGH_CONFIDENCE_VENUE_METADATA", None

        # Bitget vetted legacy tokenized-stock bases. No direct-ticker expansion.
        if exchange_name == "Bitget" and candidate in _V22_VETTED_BITGET_LEGACY:
            return candidate, "HIGH_CONFIDENCE_BITGET_LEGACY", None

        # Critical V22 change: a direct Alpaca ticker match without venue evidence is rejected.
        return None, "", f"alpaca_ticker_collision_no_tradfi_metadata:{candidate}"

    return None, "", None


def discover_three_exchange_targets(exchanges: dict[str, Any]):
    """Keep every HIGH-CONFIDENCE US-stock token listing per exchange; dedupe ordinary crypto by base."""
    global _V22_TOKEN_KEYS, _V22_TOKEN_META, _V22_REJECTED
    _V22_TOKEN_KEYS = set()
    _V22_TOKEN_META = {}
    _V22_REJECTED = []
    stocks = _v22_all_alpaca_equity_symbols()
    crypto_selected: dict[str, Any] = {}
    stock_targets: list[Any] = []
    errors: list[dict[str, str]] = []

    for exchange_name, exchange in exchanges.items():
        try:
            markets = exchange.load_markets()
        except Exception as exc:
            errors.append({"stage":"load_markets","exchange":exchange_name,"symbol":"","error":f"{type(exc).__name__}: {exc}"})
            continue
        for symbol, market in markets.items():
            valid = (
                market.get("active") is not False
                and bool(market.get("swap")) and bool(market.get("linear"))
                and market.get("quote") == "USDT"
                and market.get("settle") in (None, "USDT")
            )
            if not valid:
                continue

            underlying, reason, rejected = _v22_token_underlying(exchange_name, symbol, market, stocks)
            if underlying:
                key = (exchange_name, symbol)
                _V22_TOKEN_KEYS.add(key)
                _V22_TOKEN_META[key] = {
                    "confidence": "HIGH_CONFIDENCE",
                    "exchange": exchange_name,
                    "symbol": symbol,
                    "exchange_id": market.get("id", ""),
                    "underlying": underlying,
                    "match_reason": reason,
                    "active": market.get("active"),
                }
                stock_targets.append(engine.Target(exchange_name, exchange, symbol, underlying))
                continue

            if rejected:
                candidate_underlying = rejected.split(":", 1)[1] if ":" in rejected else ""
                _V22_REJECTED.append({
                    "confidence": "REJECTED_COLLISION",
                    "exchange": exchange_name,
                    "symbol": symbol,
                    "exchange_id": market.get("id", ""),
                    "candidate_underlying": candidate_underlying,
                    "rejection_reason": rejected,
                    "active": market.get("active"),
                })

            base = engine.canonical_base(symbol)
            crypto_selected.setdefault(base, engine.Target(exchange_name, exchange, symbol, base))

    rows = sorted(_V22_TOKEN_META.values(), key=lambda r: (r["exchange"], r["underlying"], r["symbol"]))
    rejected_rows = sorted(_V22_REJECTED, key=lambda r: (r["exchange"], r["candidate_underlying"], r["symbol"]))
    write_csv_union(V22_TOKEN_CATALOG_PATH, rows)
    write_csv_union(V22_REJECTED_COLLISIONS_PATH, rejected_rows)

    counts = {name: 0 for name in exchanges}
    for row in rows:
        counts[row["exchange"]] = counts.get(row["exchange"], 0) + 1
    rejected_counts = {name: 0 for name in exchanges}
    for row in rejected_rows:
        rejected_counts[row["exchange"]] = rejected_counts.get(row["exchange"], 0) + 1
    unique_underlyings = sorted({row["underlying"] for row in rows})
    reasons: dict[str, int] = {}
    for row in rows:
        reasons[row["match_reason"]] = reasons.get(row["match_reason"], 0) + 1
    discovery_summary = {
        "version": "V22",
        "policy": "HIGH_CONFIDENCE only; direct Alpaca ticker equality without venue TradFi evidence is rejected",
        "accepted_contracts": len(rows),
        "unique_underlyings": len(unique_underlyings),
        "accepted_by_exchange": counts,
        "accepted_by_reason": reasons,
        "rejected_collisions": len(rejected_rows),
        "rejected_by_exchange": rejected_counts,
    }
    V22_DISCOVERY_SUMMARY_PATH.write_text(json.dumps(discovery_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[V22-DISCOVERY] HIGH_CONFIDENCE美股代幣 {len(rows)} 個合約 / {len(unique_underlyings)} 個underlying｜"
        + "｜".join(f"{k}:{v}" for k,v in counts.items())
    )
    print(
        f"[V22-DISCOVERY] REJECTED_COLLISION={len(rejected_rows)}｜"
        + "｜".join(f"{k}:{v}" for k,v in rejected_counts.items())
    )
    return list(crypto_selected.values()) + stock_targets, errors


def prefilter_keep_all_stock_tokens(targets: list[Any], exchanges: dict[str, Any], errors: list[dict[str, str]]):
    """Never drop a discovered US-stock token because of the crypto top-N liquidity cap."""
    token_targets = [t for t in targets if (str(t.exchange_name), str(t.symbol)) in _V22_TOKEN_KEYS]
    crypto_targets = [t for t in targets if (str(t.exchange_name), str(t.symbol)) not in _V22_TOKEN_KEYS]
    crypto_filtered = _V22_ORIGINAL_PREFILTER(crypto_targets, exchanges, errors)
    return crypto_filtered + token_targets


def write_csv_union(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _metrics(rows: list[dict[str, Any]], starting_equity: float = 1000.0) -> dict[str, Any]:
    wins = sum(str(r.get("outcome")) == "WIN" for r in rows)
    losses = sum(str(r.get("outcome")) == "LOSS" for r in rows)
    opens = sum(str(r.get("outcome")) == "OPEN" for r in rows)
    closed = wins + losses
    net_r_values = []
    profits = []
    for r in rows:
        try:
            net_r_values.append(float(r.get("net_r") or 0.0))
        except Exception:
            pass
        try:
            profits.append(float(r.get("profit_usdt") or 0.0))
        except Exception:
            pass
    return {
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate_pct": round(wins / closed * 100.0, 3) if closed else 0.0,
        "average_net_r": round(
            sum(float(r.get("net_r") or 0.0) for r in rows if str(r.get("outcome")) != "OPEN") / closed,
            5,
        ) if closed else 0.0,
        "total_net_r": round(sum(net_r_values), 5),
        "net_profit_usdt": round(sum(profits), 4),
        "ending_equity_simple": round(starting_equity + sum(profits), 4),
    }


# ---------------------------------------------------------------------------
# Exchange-markets branch: crypto + tokenized TradFi, LONG + SHORT
# ---------------------------------------------------------------------------

def candidate_rows_both_directions(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    """Publish both directions from the validated symmetric V17 signal engine."""
    total = long_count = short_count = 0
    for candidate in v17._original_candidate_rows_v17(
        target, frame, research_start, pivot_highs, pivot_lows, funnel
    ):
        total += 1
        direction = str(candidate[3])
        if direction == "LONG":
            long_count += 1
        elif direction == "SHORT":
            short_count += 1
        yield candidate
    funnel["V22_source_candidates"] = funnel.get("V22_source_candidates", 0) + total
    funnel["V22_long_candidates"] = funnel.get("V22_long_candidates", 0) + long_count
    funnel["V22_short_candidates"] = funnel.get("V22_short_candidates", 0) + short_count


def _split_exchange_outputs() -> dict[str, Any]:
    trade_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V22.lower()}.csv"
    candidate_path = MARKETS_OUTPUT_DIR / f"candidates_{SCHEME_V22.lower()}.csv"
    trades = _read_csv_rows(trade_path)
    candidates = _read_csv_rows(candidate_path)

    def classify(row: dict[str, Any]) -> str:
        key = (str(row.get("exchange") or ""), str(row.get("symbol") or ""))
        if key in _V22_TOKEN_KEYS:
            return "TOKENIZED_TRADFI"
        # V22 segmentation is strict: rejected/ambiguous listings never fall back
        # to the old static asset_class list, which could reintroduce collisions.
        return "CRYPTO"

    segments: dict[str, list[dict[str, Any]]] = {
        "CRYPTO_LONG": [], "CRYPTO_SHORT": [],
        "US_TOKEN_LONG": [], "US_TOKEN_SHORT": [],
    }
    for row in trades:
        asset = classify(row)
        direction = str(row.get("direction") or "")
        if asset == "TOKENIZED_TRADFI":
            key = f"US_TOKEN_{direction}"
        else:
            key = f"CRYPTO_{direction}"
        if key in segments:
            segments[key].append(row)

    candidate_segments: dict[str, list[dict[str, Any]]] = {k: [] for k in segments}
    for row in candidates:
        asset = classify(row)
        direction = str(row.get("direction") or "")
        key = f"US_TOKEN_{direction}" if asset == "TOKENIZED_TRADFI" else f"CRYPTO_{direction}"
        if key in candidate_segments:
            candidate_segments[key].append(row)

    summary: dict[str, Any] = {}
    for key, rows in segments.items():
        write_csv_union(MARKETS_OUTPUT_DIR / f"trades_{key.lower()}.csv", rows)
        write_csv_union(MARKETS_OUTPUT_DIR / f"candidates_{key.lower()}.csv", candidate_segments[key])
        summary[key] = _metrics(rows)
        summary[key]["candidates"] = len(candidate_segments[key])
    return summary


def run_exchange_branch() -> dict[str, Any]:
    MARKETS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    old_configs = engine.SCHEME_CONFIGS
    old_schemes = engine.SCHEMES
    old_primary = engine.PRIMARY_SCHEME
    old_output = engine.OUTPUT_DIR
    old_database = engine.DATABASE_PATH
    old_candidates = engine.candidate_rows
    old_discover = engine.discover_unique_targets
    old_prefilter = engine.prefilter_liquid_targets

    global _V22_ORIGINAL_PREFILTER
    _V22_ORIGINAL_PREFILTER = old_prefilter
    engine.discover_unique_targets = discover_three_exchange_targets
    engine.prefilter_liquid_targets = prefilter_keep_all_stock_tokens
    engine.SCHEME_CONFIGS = {SCHEME_V22: dict(V22_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V22,)
    engine.PRIMARY_SCHEME = SCHEME_V22
    engine.OUTPUT_DIR = MARKETS_OUTPUT_DIR
    engine.DATABASE_PATH = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    engine.candidate_rows = candidate_rows_both_directions

    print("\n[V22-MARKETS] 開始：幣圈＋全部交易所美股代幣｜LONG + SHORT")
    print("[V22-MARKETS] 同一套成交量規則｜真實結構TP>=4R｜Funding只記錄")
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

    segments = _split_exchange_outputs()
    summary_path = MARKETS_OUTPUT_DIR / "summary.json"
    native_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    result = {
        "return_code": rc,
        "engine_summary": native_summary,
        "segments": segments,
    }
    (MARKETS_OUTPUT_DIR / "segments_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


# ---------------------------------------------------------------------------
# Native US stocks: dynamic universe + bidirectional H1/D1 structure targets
# ---------------------------------------------------------------------------

def discover_alpaca_stock_universe() -> tuple[list[str], dict[str, dict[str, Any]]]:
    if V22_SYMBOL_OVERRIDE:
        return V22_SYMBOL_OVERRIDE, {s: {} for s in V22_SYMBOL_OVERRIDE}

    key, secret = v19.alpaca_credentials()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "CryptoRadar-V22/1.0",
    }
    params = urllib.parse.urlencode({"status": "active", "asset_class": "us_equity"})
    payload = v19._request_json(ALPACA_ASSETS_URL + "?" + params, headers)
    if not isinstance(payload, list):
        raise RuntimeError(f"Alpaca assets回傳格式錯誤：{type(payload).__name__}")

    meta: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or not bool(item.get("tradable")):
            continue
        # Keep simple US-equity symbols. Class-A/B dots are supported by Alpaca;
        # slash-style or malformed symbols are omitted because the bar endpoint is
        # inconsistent for those synthetic identifiers.
        if "/" in symbol or " " in symbol:
            continue
        meta[symbol] = {
            "name": item.get("name", ""),
            "exchange": item.get("exchange", ""),
            "tradable": bool(item.get("tradable")),
            "shortable_now": bool(item.get("shortable")),
            "easy_to_borrow_now": bool(item.get("easy_to_borrow")),
            "fractionable": bool(item.get("fractionable")),
            "status": item.get("status", ""),
        }

    symbols = sorted(meta)
    if V22_MAX_STOCKS > 0:
        symbols = symbols[:V22_MAX_STOCKS]
    return symbols, meta


def _confirmed_swing_lows(bars: pd.DataFrame) -> list[tuple[pd.Timestamp, float]]:
    if len(bars) < 3:
        return []
    low = bars["low"].astype(float)
    piv = (low < low.shift(1)) & (low <= low.shift(-1))
    piv.iloc[-1] = False
    return [(pd.Timestamp(ts), float(v)) for ts, v in low.loc[piv].items()]


def stock_major_structure_targets_both(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    cached_pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    minimum_room_r: float = 2.0,
) -> Optional[tuple[float, float, float, float, float, float, float]]:
    """Confirmed pre-entry H1/D1 major structure >=4R for LONG or SHORT."""
    del cached_pivot_highs, cached_pivot_lows, minimum_room_r
    if direction not in {"LONG", "SHORT"} or entry <= 0 or stop_distance <= 0:
        return None

    signal_time = pd.Timestamp(frame.index[signal_i])
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")

    hourly = v19._ny_regular_hourly(frame, signal_time)
    daily = v19._ny_completed_daily(frame, signal_time)
    if not hourly.empty:
        hourly = hourly[hourly.index >= signal_time - pd.Timedelta(days=v19.STOCK_TARGET_HOURLY_LOOKBACK_DAYS)]
    if not daily.empty:
        daily = daily[daily.index >= signal_time - pd.Timedelta(days=v19.STOCK_TARGET_DAILY_LOOKBACK_DAYS)]

    levels: list[tuple[pd.Timestamp, float, str]] = []
    if direction == "LONG":
        levels.extend((ts, level, "H1") for ts, level in v19._confirmed_swing_highs(hourly) if level > entry)
        levels.extend((ts, level, "D1") for ts, level in v19._confirmed_swing_highs(daily) if level > entry)
    else:
        levels.extend((ts, level, "H1") for ts, level in _confirmed_swing_lows(hourly) if level < entry)
        levels.extend((ts, level, "D1") for ts, level in _confirmed_swing_lows(daily) if level < entry)
    if not levels:
        return None

    tolerance = max(
        entry * v19.STOCK_TARGET_ZONE_TOLERANCE_PCT,
        stop_distance * v19.STOCK_TARGET_ZONE_TOLERANCE_R,
    )
    zones = v19._cluster_stock_resistance(levels, tolerance)

    major: list[dict[str, Any]] = []
    all_rr: list[float] = []
    for zone in zones:
        if direction == "LONG":
            target = float(zone["low"])   # first resistance edge hit from below
            rr = (target - entry) / stop_distance
        else:
            target = float(zone["high"])  # first support edge hit from above
            rr = (entry - target) / stop_distance
        if rr > 0:
            all_rr.append(rr)
        if zone["daily_touches"] >= 1 or zone["hourly_touches"] >= v19.STOCK_TARGET_MIN_HOURLY_TOUCHES:
            z = dict(zone)
            z["target"] = target
            z["rr"] = rr
            major.append(z)

    eligible = [z for z in major if float(z["rr"]) >= v19.STOCK_TARGET_MIN_R]
    if not eligible:
        return None
    # nearest valid structure in the trade direction
    eligible.sort(key=lambda z: float(z["rr"]))
    chosen = eligible[0]
    tp1 = float(chosen["target"])
    rr1 = float(chosen["rr"])
    later = eligible[1:]
    tp2 = float(later[0]["target"]) if len(later) >= 1 else tp1
    rr2 = abs(tp2 - entry) / stop_distance
    tp3 = float(later[1]["target"]) if len(later) >= 2 else tp2
    rr3 = abs(tp3 - entry) / stop_distance
    nearest_rr = min(all_rr) if all_rr else 999.0
    return tp1, tp2, tp3, rr1, rr2, rr3, nearest_rr


def _candidate_tuple_to_row(candidate: tuple[Any, ...]) -> dict[str, Any]:
    return v19._candidate_tuple_to_row(candidate)


def generate_stock_both_candidates(
    symbol: str,
    raw: pd.DataFrame,
    research_start: pd.Timestamp,
    asset_meta: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    target = v19.StockTarget("Alpaca", symbol, symbol)
    frame, pivot_highs, pivot_lows = v19.prepare_stock_indicators(raw)
    funnel: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for candidate in v17._original_candidate_rows_v17(
        target, frame, research_start, pivot_highs, pivot_lows, funnel
    ):
        row = _candidate_tuple_to_row(candidate)
        if row.get("direction") not in {"LONG", "SHORT"}:
            continue
        if row.get("scheme") != SCHEME_V22:
            continue
        row["scheme"] = "V22_US_STOCK_BOTH"
        row["data_source"] = f"Alpaca:{v19.ALPACA_FEED}"
        row["funding_rate_at_signal"] = ""
        row["funding_used_as_filter"] = False
        row["shortable_now_metadata"] = asset_meta.get("shortable_now", "")
        row["easy_to_borrow_now_metadata"] = asset_meta.get("easy_to_borrow_now", "")
        row["listing_exchange"] = asset_meta.get("exchange", "")
        rows.append(row)
    return rows, funnel


def _stock_direction_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "US_STOCK_LONG": _metrics([r for r in trades if str(r.get("direction")) == "LONG"]),
        "US_STOCK_SHORT": _metrics([r for r in trades if str(r.get("direction")) == "SHORT"]),
        "US_STOCK_ALL": _metrics(trades),
    }


def run_stock_branch() -> dict[str, Any]:
    STOCK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols, meta = discover_alpaca_stock_universe()
    if not symbols:
        raise RuntimeError("Alpaca沒有找到可研究的active tradable US-equity symbols。")

    now = pd.Timestamp.now(tz="UTC").floor("15min")
    research_start = now - pd.Timedelta(days=v19.STOCK_RESEARCH_DAYS)
    fetch_start = research_start - pd.Timedelta(days=v19.STOCK_WARMUP_CALENDAR_DAYS)

    print("\n[V22-US] 開始：Alpaca原生美股全市場｜LONG + SHORT")
    print(
        f"[V22-US] feed={v19.ALPACA_FEED}｜股票={len(symbols)}｜研究={v19.STOCK_RESEARCH_DAYS}天｜"
        f"MAX={V22_MAX_STOCKS or 'ALL'}"
    )
    print("[V22-US] 注意：第一次全市場下載可能非常久；之後會使用既有15m快取。")

    all_candidates: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    funnel_total: dict[str, int] = {}
    errors: list[dict[str, str]] = []

    old_fee = engine.FEE_PER_SIDE
    old_slippage = engine.SLIPPAGE_PER_SIDE
    old_schemes = engine.SCHEMES
    old_configs = engine.SCHEME_CONFIGS
    old_primary = engine.PRIMARY_SCHEME
    old_target = engine.confirmed_structure_targets

    engine.FEE_PER_SIDE = v19.STOCK_FEE_PER_SIDE
    engine.SLIPPAGE_PER_SIDE = v19.STOCK_SLIPPAGE_PER_SIDE
    engine.SCHEME_CONFIGS = {SCHEME_V22: dict(V22_SCHEME_CONFIG)}
    engine.SCHEMES = (SCHEME_V22,)
    engine.PRIMARY_SCHEME = SCHEME_V22
    engine.confirmed_structure_targets = stock_major_structure_targets_both

    try:
        for idx, symbol in enumerate(symbols, start=1):
            try:
                raw = v19.fetch_alpaca_15m(symbol, fetch_start, now)
                research_rows = int((raw.index >= research_start).sum())
                if research_rows < 200:
                    quality_rows.append({
                        "symbol": symbol, "bars": len(raw), "research_bars": research_rows,
                        "status": "TOO_SHORT", **meta.get(symbol, {}),
                    })
                    print(f"[V22-US {idx}/{len(symbols)}] {symbol} SKIP research={research_rows}")
                    continue
                candidates, funnel = generate_stock_both_candidates(
                    symbol, raw, research_start, meta.get(symbol, {})
                )
                all_candidates.extend(candidates)
                for key, value in funnel.items():
                    funnel_total[key] = funnel_total.get(key, 0) + int(value)
                quality_rows.append({
                    "symbol": symbol,
                    "bars": len(raw),
                    "research_bars": research_rows,
                    "first_bar": raw.index.min().isoformat() if len(raw) else "",
                    "last_bar": raw.index.max().isoformat() if len(raw) else "",
                    "candidates": len(candidates),
                    "status": "OK",
                    "source": f"Alpaca:{v19.ALPACA_FEED}",
                    **meta.get(symbol, {}),
                })
                print(
                    f"[V22-US {idx}/{len(symbols)}] {symbol} bars={len(raw)} research={research_rows} "
                    f"candidates={len(candidates)}"
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append({"symbol": symbol, "error": message})
                print(f"[V22-US {idx}/{len(symbols)}] {symbol} ERROR {message}")
    finally:
        engine.FEE_PER_SIDE = old_fee
        engine.SLIPPAGE_PER_SIDE = old_slippage
        engine.SCHEMES = old_schemes
        engine.SCHEME_CONFIGS = old_configs
        engine.PRIMARY_SCHEME = old_primary
        engine.confirmed_structure_targets = old_target

    trades, portfolio_summary = v19.apply_stock_portfolio(all_candidates)
    long_candidates = [r for r in all_candidates if str(r.get("direction")) == "LONG"]
    short_candidates = [r for r in all_candidates if str(r.get("direction")) == "SHORT"]
    long_trades = [r for r in trades if str(r.get("direction")) == "LONG"]
    short_trades = [r for r in trades if str(r.get("direction")) == "SHORT"]

    write_csv_union(STOCK_OUTPUT_DIR / "candidates_us_stock_all.csv", all_candidates)
    write_csv_union(STOCK_OUTPUT_DIR / "candidates_us_stock_long.csv", long_candidates)
    write_csv_union(STOCK_OUTPUT_DIR / "candidates_us_stock_short.csv", short_candidates)
    write_csv_union(STOCK_OUTPUT_DIR / "trades_us_stock_all.csv", trades)
    write_csv_union(STOCK_OUTPUT_DIR / "trades_us_stock_long.csv", long_trades)
    write_csv_union(STOCK_OUTPUT_DIR / "trades_us_stock_short.csv", short_trades)
    write_csv_union(STOCK_OUTPUT_DIR / "data_quality.csv", quality_rows)
    write_csv_union(STOCK_OUTPUT_DIR / "errors.csv", errors)
    write_csv_union(
        STOCK_OUTPUT_DIR / "funnel.csv",
        [{"stage": k, "count": v} for k, v in sorted(funnel_total.items())],
    )

    directions = _stock_direction_summary(trades)
    summary = {
        "version": "V22",
        "branch": "US_STOCK_BIDIRECTIONAL",
        "data_source": f"Alpaca:{v19.ALPACA_FEED}",
        "universe_policy": "all current active tradable us_equity assets; overrideable; survivorship bias noted",
        "symbols_requested": len(symbols),
        "symbols_ok": sum(r.get("status") == "OK" for r in quality_rows),
        "research_days": v19.STOCK_RESEARCH_DAYS,
        "timeframe": v19.STOCK_TIMEFRAME,
        "regular_session_only": True,
        "directions": directions,
        "combined_portfolio": portfolio_summary,
        "candidates": {
            "all": len(all_candidates), "long": len(long_candidates), "short": len(short_candidates)
        },
        "target": "confirmed pre-entry H1/D1 major resistance/support >=4R; no maximum; minor nearby structures do not veto",
        "historical_short_borrow": "NOT_MODELED; current shortable/easy_to_borrow stored as metadata only",
        "errors_count": len(errors),
    }
    (STOCK_OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[V22-US] 完成｜候選={len(all_candidates)} (L{len(long_candidates)}/S{len(short_candidates)})｜"
        f"實際={len(trades)} (L{len(long_trades)}/S{len(short_trades)})｜errors={len(errors)}"
    )
    return {"summary": summary, "errors": errors}


def audit_v22(exchange_result: dict[str, Any], stock_result: dict[str, Any]) -> dict[str, Any]:
    invalid_rr = 0
    invalid_direction = 0
    for path in [
        STOCK_OUTPUT_DIR / "trades_us_stock_all.csv",
        MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V22.lower()}.csv",
    ]:
        for row in _read_csv_rows(path):
            if str(row.get("direction")) not in {"LONG", "SHORT"}:
                invalid_direction += 1
            try:
                if float(row.get("rr1") or 0.0) < 4.0 - 1e-9:
                    invalid_rr += 1
            except Exception:
                invalid_rr += 1

    segments = exchange_result.get("segments", {})
    required_segments = {"CRYPTO_LONG", "CRYPTO_SHORT", "US_TOKEN_LONG", "US_TOKEN_SHORT"}
    allowed_token_reasons = {
        "HIGH_CONFIDENCE_NCSK_WRAPPER",
        "HIGH_CONFIDENCE_VENUE_METADATA",
        "HIGH_CONFIDENCE_BITGET_LEGACY",
    }
    invalid_token_match_reasons = sum(
        str(row.get("match_reason") or "") not in allowed_token_reasons
        for row in _V22_TOKEN_META.values()
    )
    accepted_keys = set(_V22_TOKEN_META)
    rejected_keys = {(str(r.get("exchange")), str(r.get("symbol"))) for r in _V22_REJECTED}
    accepted_rejected_overlap = len(accepted_keys & rejected_keys)
    audit = {
        "version": "V22",
        "exchange_policy": "Binance/Bitget/BingX: HIGH_CONFIDENCE tokenized-stock contracts only; plain Alpaca ticker collisions rejected; crypto deduped by base; LONG+SHORT",
        "us_stock_policy": "all current active tradable Alpaca us_equity; LONG+SHORT",
        "relative_volume": "20-bar lagged median; breakout 1.30x; retest 0.60-1.20x; confirm >=1.00x and >=1.10x retest",
        "target": "real pre-entry structure >=4R, no max",
        "profit_protection": "+2R->BE; +3R->+1R; +5R->+3R; no time exit",
        "historical_stock_short_borrow_modeled": False,
        "invalid_direction": invalid_direction,
        "invalid_rr": invalid_rr,
        "missing_exchange_segments": sorted(required_segments - set(segments)),
        "stock_errors": len(stock_result.get("errors", [])),
        "accepted_token_contracts": len(_V22_TOKEN_META),
        "rejected_ticker_collisions": len(_V22_REJECTED),
        "invalid_token_match_reasons": invalid_token_match_reasons,
        "accepted_rejected_overlap": accepted_rejected_overlap,
        "passed": (
            invalid_direction == 0
            and invalid_rr == 0
            and invalid_token_match_reasons == 0
            and accepted_rejected_overlap == 0
            and required_segments.issubset(set(segments))
        ),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v22_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def main() -> int:
    print("CryptoRadar V22 啟動｜三大交易所美股代幣嚴格辨識＋幣圈雙向研究")
    print("交易所：Binance / Bitget / BingX；只保留HIGH_CONFIDENCE美股代幣：交易所明確TradFi/stock metadata、BingX NCSK wrapper、Bitget vetted legacy；LONG / SHORT 都開。")
    print("Alpaca：只用來核對underlying；單純ticker同名不算美股代幣，會輸出REJECTED_COLLISION。原生美股預設關閉。")
    print("成交量：各標的自己的前20根中位量；突破/跌破>=1.30x；回踩0.60～1.20x；確認>=1.00x且+10%。")
    print("TP：進場前真實結構>=4R、無上限；+2R保本、+3R鎖1R、+5R鎖3R；無時間出場。")
    print("美股空單提醒：歷史借券可得性未重建，shortable欄位只做當前metadata，不做歷史過濾。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exchange_result = run_exchange_branch()
    stock_result = run_stock_branch() if V22_RUN_NATIVE_STOCKS else {"skipped": True, "reason": "V22_RUN_NATIVE_STOCKS=0"}
    audit = audit_v22(exchange_result, stock_result)

    combined = {
        "version": "V22",
        "exchange_markets": exchange_result,
        "us_stocks": stock_result,
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v22.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 96)
    print("V22 完成")
    print(f"交易所市場結果：{MARKETS_OUTPUT_DIR}")
    print(f"三交易所HIGH_CONFIDENCE美股代幣清單：{V22_TOKEN_CATALOG_PATH}")
    print(f"被拒絕ticker碰撞清單：{V22_REJECTED_COLLISIONS_PATH}")
    print(f"辨識摘要：{V22_DISCOVERY_SUMMARY_PATH}")
    print(f"原生美股結果：{STOCK_OUTPUT_DIR if V22_RUN_NATIVE_STOCKS else 'SKIPPED'}")
    print(f"總結：{OUTPUT_DIR / 'summary_v22.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v22_audit.json'}")
    print("=" * 96)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
