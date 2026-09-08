#!/usr/bin/env python3
"""CryptoRadar V19 hybrid research backtest.

CRYPTO branch
-------------
* Binance / Bitget / BingX USDT linear swaps.
* SHORT only, using the validated V17 signal/execution model.
* Real structural TP >=4R, but skip 5R <= TP < 6R.
* Funding is recorded for analysis only and never blocks a trade.
* Profit protection: +2R -> BE, +3R -> +1R, +5R -> +3R.
* No time exit.

US STOCK branch
---------------
* Native US-equity 15-minute bars from Alpaca Market Data (IEX feed by default).
* LONG only. No tokenized-stock prices are used for this branch.
* Same relative-volume sequence as the crypto research:
  breakout >=1.30x own previous-20-bar median; retest 0.60x-1.20x;
  confirmation >=1.00x and >=1.10x retest volume.
* Same structural stop. Stock TP is independently defined from confirmed pre-entry
  60-minute and daily resistance zones; TP >=4R, no maximum. Nearby minor
  structure does not veto the trade.
* Same +2R/+3R/+5R protection ladder; no time exit.
* Alpaca keys are read from /home/ubuntu/CryptoRadar/.env or environment vars.

This remains a research backtest, not a live-trading recommendation.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path.home() / "CryptoRadar"
V17_PATH = ROOT / "backtest_1y_v17.py"
if not V17_PATH.exists():
    V17_PATH = Path(__file__).with_name("backtest_1y_v17.py")
if not V17_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v17.py；請把V17與V18放在同一目錄。")

spec = importlib.util.spec_from_file_location("cryptoradar_v17_components_v18", V17_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入V17：{V17_PATH}")
v17 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v17
spec.loader.exec_module(v17)
engine = v17.engine

# ===== V19 paths =====
OUTPUT_DIR = ROOT / "backtest_1y_v19_results"
CRYPTO_OUTPUT_DIR = OUTPUT_DIR / "crypto"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"
STOCK_CACHE_DIR = ROOT / "backtest_cache_alpaca_15m"
ENV_PATH = ROOT / ".env"

# ===== Alpaca / US-stock research settings =====
ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
ALPACA_FEED = os.getenv("ALPACA_FEED", "iex")
STOCK_TIMEFRAME = "15Min"
STOCK_RESEARCH_DAYS = 365
# Fetch extra history so 4H structure and long EMAs are known before research start.
STOCK_WARMUP_CALENDAR_DAYS = 300
STOCK_PAGE_LIMIT = 10_000
STOCK_FETCH_RETRIES = 4
STOCK_CACHE_MAX_AGE_HOURS = 12
STOCK_RISK_PER_TRADE_PCT = 1.0
STOCK_STARTING_EQUITY = 1_000.0
STOCK_MAX_POSITIONS = 3
STOCK_MAX_NEW_TRADES_PER_TIMESTAMP = 1
STOCK_SYMBOL_COOLDOWN_HOURS = 24
# Alpaca equities are commission-free in many retail contexts; model slippage only.
STOCK_FEE_PER_SIDE = 0.0
STOCK_SLIPPAGE_PER_SIDE = 0.0002

# ===== V19 stock structural-target rules =====
# Equity targets are deliberately separate from the crypto 4H-zone finder.
STOCK_TARGET_MIN_R = 4.0
STOCK_TARGET_HOURLY_LOOKBACK_DAYS = 180
STOCK_TARGET_DAILY_LOOKBACK_DAYS = 540
STOCK_TARGET_ZONE_TOLERANCE_PCT = 0.0035
STOCK_TARGET_ZONE_TOLERANCE_R = 0.20
STOCK_TARGET_MIN_HOURLY_TOUCHES = 2

DEFAULT_STOCK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
    "AVGO", "PLTR", "COIN", "MSTR", "NFLX", "MU", "ARM", "SMCI",
]
STOCK_SYMBOLS = [
    s.strip().upper() for s in os.getenv(
        "V19_STOCK_SYMBOLS", ",".join(DEFAULT_STOCK_SYMBOLS)
    ).split(",") if s.strip()
]


@dataclass(frozen=True)
class StockTarget:
    exchange_name: str
    symbol: str
    base: str


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


def load_dotenv_simple(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def alpaca_credentials() -> tuple[str, str]:
    load_dotenv_simple(ENV_PATH)
    key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID") or ""
    secret = os.getenv("ALPACA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY") or ""
    if not key or not secret:
        raise RuntimeError(
            f"找不到Alpaca金鑰。請確認 {ENV_PATH} 內有 "
            "ALPACA_API_KEY 與 ALPACA_API_SECRET。"
        )
    return key, secret


def _stock_cache_path(symbol: str) -> Path:
    return STOCK_CACHE_DIR / f"{symbol.upper()}_15m.csv.gz"


def _request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_alpaca_15m(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Fetch native US-stock 15m bars, cache them, and keep regular session only."""
    symbol = symbol.upper()
    STOCK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _stock_cache_path(symbol)

    # A recently-created cache that spans the requested interval can be reused.
    if cache_path.exists():
        try:
            age_h = (time.time() - cache_path.stat().st_mtime) / 3600.0
            cached = pd.read_csv(cache_path)
            if not cached.empty and "timestamp" in cached:
                idx = pd.to_datetime(cached["timestamp"], utc=True, errors="coerce")
                frame = cached.drop(columns=["timestamp"]).copy()
                frame.index = idx
                frame = frame[~frame.index.isna()].sort_index()
                if (
                    age_h <= STOCK_CACHE_MAX_AGE_HOURS
                    and len(frame)
                    and frame.index.min() <= start + pd.Timedelta(days=7)
                    and frame.index.max() >= end - pd.Timedelta(days=7)
                ):
                    return frame[(frame.index >= start) & (frame.index <= end)].copy()
        except Exception:
            pass

    key, secret = alpaca_credentials()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "User-Agent": "CryptoRadar-V18/1.0",
    }
    bars: list[dict[str, Any]] = []
    page_token: Optional[str] = None

    while True:
        params = {
            "timeframe": STOCK_TIMEFRAME,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": STOCK_PAGE_LIMIT,
            "adjustment": "all",
            "feed": ALPACA_FEED,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        url = ALPACA_DATA_URL.format(symbol=urllib.parse.quote(symbol)) + "?" + urllib.parse.urlencode(params)

        payload: Optional[dict[str, Any]] = None
        last_error: Optional[Exception] = None
        for attempt in range(STOCK_FETCH_RETRIES):
            try:
                payload = _request_json(url, headers)
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < STOCK_FETCH_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
        if payload is None:
            raise RuntimeError(f"Alpaca {symbol} 下載失敗：{type(last_error).__name__}: {last_error}")

        batch = payload.get("bars") or []
        bars.extend(batch)
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not bars:
        raise RuntimeError(f"Alpaca {symbol} 沒有回傳15分鐘K線")

    raw = pd.DataFrame(bars)
    required = {"t", "o", "h", "l", "c", "v"}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"Alpaca {symbol} K線欄位不完整：{sorted(raw.columns)}")

    idx = pd.to_datetime(raw["t"], utc=True, errors="coerce")
    frame = pd.DataFrame(index=idx)
    frame["open"] = pd.to_numeric(raw["o"], errors="coerce").to_numpy()
    frame["high"] = pd.to_numeric(raw["h"], errors="coerce").to_numpy()
    frame["low"] = pd.to_numeric(raw["l"], errors="coerce").to_numpy()
    frame["close"] = pd.to_numeric(raw["c"], errors="coerce").to_numpy()
    frame["volume"] = pd.to_numeric(raw["v"], errors="coerce").fillna(0.0).to_numpy()
    frame = frame[~frame.index.isna()].dropna(subset=["open", "high", "low", "close"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()

    # Alpaca can return extended-hours bars.  V19 stock research uses regular
    # US session only (09:30 <= NY time < 16:00), making volume comparisons sane.
    ny = frame.index.tz_convert("America/New_York")
    minute = ny.hour * 60 + ny.minute
    regular = (minute >= 9 * 60 + 30) & (minute < 16 * 60)
    frame = frame.loc[regular]

    cache = frame.reset_index().rename(columns={"index": "timestamp"})
    cache.to_csv(cache_path, index=False, compression="gzip")
    return frame[(frame.index >= start) & (frame.index <= end)].copy()


def prepare_stock_indicators(data: pd.DataFrame):
    """Use V17 indicators but correct the equity '24h' liquidity window to 1 session."""
    frame, highs, lows = v17.prepare_relative_volume_indicators(data)
    # 15m regular session = 26 bars.  This is only an execution-liquidity floor,
    # never a signal-strength score.
    frame["quote_volume_24h"] = (frame["close"] * frame["volume"]).rolling(26).sum()
    return frame, highs, lows


def _ny_regular_hourly(frame: pd.DataFrame, signal_time: pd.Timestamp) -> pd.DataFrame:
    """Build session-aligned 60m bars known at signal_time, with no future bars."""
    history = frame.loc[frame.index < signal_time, ["open", "high", "low", "close", "volume"]].copy()
    if history.empty:
        return pd.DataFrame()
    ny = history.tz_convert("America/New_York")
    pieces: list[pd.DataFrame] = []
    for _, day in ny.groupby(ny.index.date):
        if day.empty:
            continue
        # Regular session is already enforced upstream.  Resampling with a
        # 09:30 origin creates 09:30, 10:30, ... session-aligned bars.
        bars = day.resample(
            "60min", origin="start_day", offset="30min", label="left", closed="left"
        ).agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
        }).dropna(subset=["open", "high", "low", "close"])
        if not bars.empty:
            pieces.append(bars)
    if not pieces:
        return pd.DataFrame()
    result = pd.concat(pieces).sort_index()
    # A 60m pivot is usable only after its right-side hourly bar has closed.
    signal_ny = signal_time.tz_convert("America/New_York")
    result = result[result.index + pd.Timedelta(hours=1) <= signal_ny]
    return result.tz_convert("UTC")


def _ny_completed_daily(frame: pd.DataFrame, signal_time: pd.Timestamp) -> pd.DataFrame:
    """Build only fully completed prior-session daily bars."""
    history = frame.loc[frame.index < signal_time, ["open", "high", "low", "close", "volume"]].copy()
    if history.empty:
        return pd.DataFrame()
    ny = history.tz_convert("America/New_York")
    signal_date = signal_time.tz_convert("America/New_York").date()
    ny = ny[[idx.date() < signal_date for idx in ny.index]]
    if ny.empty:
        return pd.DataFrame()
    daily = ny.groupby(ny.index.date).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    })
    daily.index = pd.to_datetime(daily.index).tz_localize("America/New_York").tz_convert("UTC")
    return daily


def _confirmed_swing_highs(bars: pd.DataFrame) -> list[tuple[pd.Timestamp, float]]:
    """1-left/1-right swing highs; the right bar must already be completed."""
    if len(bars) < 3:
        return []
    high = bars["high"].astype(float)
    piv = (high > high.shift(1)) & (high >= high.shift(-1))
    piv.iloc[-1] = False
    return [(pd.Timestamp(ts), float(v)) for ts, v in high.loc[piv].items()]


def _cluster_stock_resistance(
    levels: list[tuple[pd.Timestamp, float, str]], tolerance: float
) -> list[dict[str, Any]]:
    if not levels:
        return []
    ordered = sorted(levels, key=lambda x: x[1])
    groups: list[list[tuple[pd.Timestamp, float, str]]] = []
    for item in ordered:
        if not groups:
            groups.append([item])
            continue
        center = sum(v for _, v, _ in groups[-1]) / len(groups[-1])
        if abs(item[1] - center) <= tolerance:
            groups[-1].append(item)
        else:
            groups.append([item])
    zones: list[dict[str, Any]] = []
    for group in groups:
        vals = [v for _, v, _ in group]
        kinds = [k for _, _, k in group]
        zones.append({
            "low": min(vals),
            "high": max(vals),
            "center": sum(vals) / len(vals),
            "hourly_touches": sum(k == "H1" for k in kinds),
            "daily_touches": sum(k == "D1" for k in kinds),
            "latest": max(ts for ts, _, _ in group),
        })
    return zones


def stock_major_structure_targets(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    cached_pivot_lows: list[tuple[pd.Timestamp, pd.Timestamp, float]],
    minimum_room_r: float = 2.0,
) -> Optional[tuple[float, float, float, float, float, float, float]]:
    """V19 equity LONG target: confirmed H1/D1 resistance >=4R, no minor-level veto."""
    del cached_pivot_highs, cached_pivot_lows, minimum_room_r
    if direction != "LONG" or entry <= 0 or stop_distance <= 0:
        return None
    signal_time = pd.Timestamp(frame.index[signal_i])
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")

    hourly = _ny_regular_hourly(frame, signal_time)
    daily = _ny_completed_daily(frame, signal_time)
    if not hourly.empty:
        cutoff_h = signal_time - pd.Timedelta(days=STOCK_TARGET_HOURLY_LOOKBACK_DAYS)
        hourly = hourly[hourly.index >= cutoff_h]
    if not daily.empty:
        cutoff_d = signal_time - pd.Timedelta(days=STOCK_TARGET_DAILY_LOOKBACK_DAYS)
        daily = daily[daily.index >= cutoff_d]

    levels: list[tuple[pd.Timestamp, float, str]] = []
    levels.extend((ts, level, "H1") for ts, level in _confirmed_swing_highs(hourly) if level > entry)
    levels.extend((ts, level, "D1") for ts, level in _confirmed_swing_highs(daily) if level > entry)
    if not levels:
        return None

    tolerance = max(entry * STOCK_TARGET_ZONE_TOLERANCE_PCT, stop_distance * STOCK_TARGET_ZONE_TOLERANCE_R)
    zones = _cluster_stock_resistance(levels, tolerance)

    # Major resistance means either a daily swing is present, or the hourly zone
    # has at least two independently confirmed touches.  Minor nearby structure
    # is retained diagnostically but does not reject the setup.
    major: list[dict[str, Any]] = []
    all_rr: list[float] = []
    for zone in zones:
        target = float(zone["low"])  # conservative near edge for LONG
        rr = (target - entry) / stop_distance
        if rr > 0:
            all_rr.append(rr)
        if zone["daily_touches"] >= 1 or zone["hourly_touches"] >= STOCK_TARGET_MIN_HOURLY_TOUCHES:
            zone["target"] = target
            zone["rr"] = rr
            major.append(zone)

    eligible = [z for z in major if float(z["rr"]) >= STOCK_TARGET_MIN_R]
    eligible.sort(key=lambda z: float(z["target"]))
    if not eligible:
        return None

    chosen = eligible[0]
    tp1 = float(chosen["target"])
    rr1 = float(chosen["rr"])
    # TP2/TP3 remain informational; execution exits 100% at TP1 or protection/SL.
    later = [z for z in eligible[1:] if float(z["target"]) > tp1]
    tp2 = float(later[0]["target"]) if len(later) >= 1 else tp1
    rr2 = (tp2 - entry) / stop_distance
    tp3 = float(later[1]["target"]) if len(later) >= 2 else tp2
    rr3 = (tp3 - entry) / stop_distance
    nearest_rr = min(all_rr) if all_rr else 999.0
    return tp1, tp2, tp3, rr1, rr2, rr3, nearest_rr


def _candidate_tuple_to_row(candidate: tuple[Any, ...]) -> dict[str, Any]:
    names = [
        "exchange", "symbol", "base", "direction", "signal_time", "entry_time",
        "exit_time", "period", "scheme", "entry", "stop", "tp1", "tp2", "tp3",
        "rr1", "rr2", "rr3", "exit_price", "score", "outcome", "exit_reason",
        "timed_out", "gross_r", "cost_r", "net_r", "confirmation_volume",
        "range_atr", "confirmation_pressure", "nearest_rr", "stop_atr", "stop_pct",
        "entry_delay_bars", "holding_bars", "mfe_r", "mae_r",
    ]
    return dict(zip(names, candidate))


def generate_stock_long_candidates(symbol: str, raw: pd.DataFrame, research_start: pd.Timestamp) -> tuple[list[dict[str, Any]], dict[str, int]]:
    target = StockTarget("Alpaca", symbol, symbol)
    frame, pivot_highs, pivot_lows = prepare_stock_indicators(raw)
    funnel: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    # The V17 engine declares three TP-distance research schemes.  For US-stock
    # LONG we intentionally use only its BASE scheme; the 5R-6R skip hypothesis
    # came from crypto SHORT and is not silently transferred to equities.
    for candidate in v17._original_candidate_rows_v17(
        target, frame, research_start, pivot_highs, pivot_lows, funnel
    ):
        row = _candidate_tuple_to_row(candidate)
        if row.get("direction") != "LONG":
            continue
        if row.get("scheme") != v17.SCHEME_BASE:
            continue
        row["scheme"] = "V19_US_STOCK_LONG"
        row["data_source"] = f"Alpaca:{ALPACA_FEED}"
        row["funding_rate_at_signal"] = ""
        row["funding_used_as_filter"] = False
        rows.append(row)
    return rows, funnel


def apply_stock_portfolio(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply V8-like position constraints and 1% equity risk to stock candidates."""
    ordered = sorted(candidates, key=lambda r: (pd.Timestamp(r["entry_time"]), str(r["symbol"])))
    accepted: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    symbol_last_entry: dict[str, pd.Timestamp] = {}
    new_count_by_time: dict[pd.Timestamp, int] = {}
    equity = STOCK_STARTING_EQUITY
    equity_events: list[tuple[pd.Timestamp, float]] = [(pd.Timestamp.min.tz_localize("UTC"), equity)]

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

    rejected_capacity = rejected_cooldown = rejected_same_time = 0
    for row in ordered:
        entry_time = pd.Timestamp(row["entry_time"])
        if entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize("UTC")
        realize_until(entry_time)

        if len(open_positions) >= STOCK_MAX_POSITIONS:
            rejected_capacity += 1
            continue
        if new_count_by_time.get(entry_time, 0) >= STOCK_MAX_NEW_TRADES_PER_TIMESTAMP:
            rejected_same_time += 1
            continue
        symbol = str(row["symbol"])
        previous = symbol_last_entry.get(symbol)
        if previous is not None and entry_time - previous < pd.Timedelta(hours=STOCK_SYMBOL_COOLDOWN_HOURS):
            rejected_cooldown += 1
            continue

        risk_usdt = equity * STOCK_RISK_PER_TRADE_PCT / 100.0
        trade = dict(row)
        trade["equity_at_entry"] = round(equity, 6)
        trade["risk_usdt"] = round(risk_usdt, 6)
        trade["profit_usdt"] = round(float(row["net_r"]) * risk_usdt, 6)
        accepted.append(trade)
        open_positions.append(trade)
        symbol_last_entry[symbol] = entry_time
        new_count_by_time[entry_time] = new_count_by_time.get(entry_time, 0) + 1

    realize_until(pd.Timestamp.max.tz_localize("UTC"))
    equity_curve = [value for _, value in sorted(equity_events, key=lambda x: x[0])]
    peak = STOCK_STARTING_EQUITY
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
        "scheme": "V19_US_STOCK_LONG",
        "trades": len(accepted),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate_pct": round(wins / closed * 100.0, 3) if closed else 0.0,
        "average_net_r": round(sum(float(r["net_r"]) for r in accepted if str(r["outcome"]) != "OPEN") / closed, 5) if closed else 0.0,
        "total_net_r": round(sum(float(r["net_r"]) for r in accepted), 5),
        "starting_equity": STOCK_STARTING_EQUITY,
        "ending_equity": round(equity, 4),
        "net_profit_usdt": round(equity - STOCK_STARTING_EQUITY, 4),
        "max_drawdown_pct": round(max_dd, 3),
        "rejected_capacity": rejected_capacity,
        "rejected_symbol_cooldown": rejected_cooldown,
        "rejected_same_timestamp": rejected_same_time,
    }
    return accepted, summary


def run_crypto_branch() -> dict[str, Any]:
    """Run only the winning V17 crypto branch: SHORT + skip 5R-<6R."""
    CRYPTO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    common = dict(v17._COMMON_SCHEME)
    engine.SCHEME_CONFIGS = {v17.SCHEME_SKIP_5_TO_6R: common}
    engine.SCHEMES = (v17.SCHEME_SKIP_5_TO_6R,)
    engine.PRIMARY_SCHEME = v17.SCHEME_SKIP_5_TO_6R
    engine.OUTPUT_DIR = CRYPTO_OUTPUT_DIR
    engine.DATABASE_PATH = CRYPTO_OUTPUT_DIR / "candidates.sqlite3"
    print("\n[V19-CRYPTO] 開始：SHORT ONLY＋排除真實TP 5R～<6R")
    rc = int(v17.main())

    summary_path = CRYPTO_OUTPUT_DIR / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {"return_code": rc, "summary": summary}


def run_stock_branch() -> dict[str, Any]:
    STOCK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    research_start = now - pd.Timedelta(days=STOCK_RESEARCH_DAYS)
    fetch_start = research_start - pd.Timedelta(days=STOCK_WARMUP_CALENDAR_DAYS)

    print("\n[V19-US] 開始：Alpaca原生美股15m LONG")
    print(f"[V19-US] feed={ALPACA_FEED}｜股票={len(STOCK_SYMBOLS)}｜研究區間={STOCK_RESEARCH_DAYS}天")

    all_candidates: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    funnel_total: dict[str, int] = {}
    errors: list[dict[str, str]] = []

    old_fee = engine.FEE_PER_SIDE
    old_slippage = engine.SLIPPAGE_PER_SIDE
    old_schemes = engine.SCHEMES
    old_scheme_configs = engine.SCHEME_CONFIGS
    old_primary_scheme = engine.PRIMARY_SCHEME
    old_target_finder = engine.confirmed_structure_targets
    engine.FEE_PER_SIDE = STOCK_FEE_PER_SIDE
    engine.SLIPPAGE_PER_SIDE = STOCK_SLIPPAGE_PER_SIDE
    # Crypto branch narrows engine.SCHEMES to V17_SKIP_5_TO_6R.  The equity
    # branch intentionally uses V17_BASE, so restore BASE here before asking
    # the original candidate generator to create stock LONG rows.
    engine.SCHEME_CONFIGS = {v17.SCHEME_BASE: dict(v17._COMMON_SCHEME)}
    engine.SCHEMES = (v17.SCHEME_BASE,)
    engine.PRIMARY_SCHEME = v17.SCHEME_BASE
    engine.confirmed_structure_targets = stock_major_structure_targets
    try:
        for index, symbol in enumerate(STOCK_SYMBOLS, start=1):
            try:
                raw = fetch_alpaca_15m(symbol, fetch_start, now)
                research_rows = int((raw.index >= research_start).sum())
                print(f"[V19-US {index}/{len(STOCK_SYMBOLS)}] {symbol} bars={len(raw)} research={research_rows}")
                candidates, funnel = generate_stock_long_candidates(symbol, raw, research_start)
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
                    "source": f"Alpaca:{ALPACA_FEED}",
                })
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"[V19-US {index}/{len(STOCK_SYMBOLS)}] {symbol} ERROR {message}")
                errors.append({"symbol": symbol, "error": message})
    finally:
        engine.FEE_PER_SIDE = old_fee
        engine.SLIPPAGE_PER_SIDE = old_slippage
        engine.SCHEME_CONFIGS = old_scheme_configs
        engine.SCHEMES = old_schemes
        engine.PRIMARY_SCHEME = old_primary_scheme
        engine.confirmed_structure_targets = old_target_finder

    trades, summary = apply_stock_portfolio(all_candidates)
    write_csv_union(STOCK_OUTPUT_DIR / "candidates_us_stock_long.csv", all_candidates)
    write_csv_union(STOCK_OUTPUT_DIR / "trades_us_stock_long.csv", trades)
    write_csv_union(STOCK_OUTPUT_DIR / "data_quality.csv", quality_rows)
    write_csv_union(STOCK_OUTPUT_DIR / "errors.csv", errors)
    write_csv_union(STOCK_OUTPUT_DIR / "funnel.csv", [{"stage": k, "count": v} for k, v in sorted(funnel_total.items())])
    (STOCK_OUTPUT_DIR / "summary.json").write_text(json.dumps({
        "version": "V19",
        "branch": "US_STOCK_LONG",
        "data_source": f"Alpaca:{ALPACA_FEED}",
        "symbols": STOCK_SYMBOLS,
        "research_days": STOCK_RESEARCH_DAYS,
        "timeframe": STOCK_TIMEFRAME,
        "regular_session_only": True,
        "relative_volume": {
            "baseline": "previous 20 bars median",
            "breakout_min": 1.30,
            "retest_min": 0.60,
            "retest_max": 1.20,
            "confirmation_min": 1.00,
            "confirmation_vs_retest_min": 1.10,
        },
        "target": "confirmed pre-entry H1/D1 major resistance >=4R; no maximum; minor nearby structures do not veto",
        "profit_protection": "+2R->BE; +3R->+1R; +5R->+3R; no time exit",
        "cost_model": {"fee_per_side": STOCK_FEE_PER_SIDE, "slippage_per_side": STOCK_SLIPPAGE_PER_SIDE},
        "portfolio": summary,
        "errors": errors,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[V19-US] 完成｜候選={len(all_candidates)}｜實際交易={summary['trades']}｜"
        f"勝率={summary['win_rate_pct']:.2f}%｜淨利={summary['net_profit_usdt']:+.2f}｜"
        f"回撤={summary['max_drawdown_pct']:.2f}%"
    )
    return {"summary": summary, "errors": errors, "candidates": len(all_candidates)}


def audit_v19(crypto: dict[str, Any], stocks: dict[str, Any]) -> dict[str, Any]:
    stock_trade_path = STOCK_OUTPUT_DIR / "trades_us_stock_long.csv"
    invalid_stock_direction = 0
    invalid_stock_rr = 0
    if stock_trade_path.exists() and stock_trade_path.stat().st_size:
        with stock_trade_path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                invalid_stock_direction += int(str(row.get("direction")) != "LONG")
                try:
                    invalid_stock_rr += int(float(row.get("rr1") or 0.0) < 4.0 - 1e-9)
                except Exception:
                    invalid_stock_rr += 1

    audit = {
        "version": "V19",
        "crypto_policy": "SHORT ONLY; V17_SKIP_5_TO_6R; funding analysis-only",
        "us_stock_policy": "native Alpaca 15m LONG ONLY; regular session; H1/D1 pre-entry major-resistance TP; no funding",
        "invalid_stock_direction": invalid_stock_direction,
        "invalid_stock_rr": invalid_stock_rr,
        "alpaca_errors": len(stocks.get("errors", [])),
        "passed": invalid_stock_direction == 0 and invalid_stock_rr == 0,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "v19_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> int:
    print("CryptoRadar V19 啟動")
    print("Crypto：SHORT ONLY，沿用V17最佳分支，排除真實TP 5R～<6R。")
    print("US Stocks：Alpaca原生15m，LONG ONLY；H1＋日線主要壓力TP；不使用美股代幣價格。")
    print("成交量：各市場自己的前20根中位量；突破1.30x、回踩0.60～1.20x、確認>=1.00x且+10%。")
    print("出場：真實結構TP>=4R無上限；+2R保本、+3R鎖1R、+5R鎖3R；無時間出場。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crypto = run_crypto_branch()
    stocks = run_stock_branch()
    audit = audit_v19(crypto, stocks)

    combined = {
        "version": "V19",
        "crypto": crypto,
        "us_stocks": stocks,
        "audit": audit,
    }
    (OUTPUT_DIR / "summary_v19.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 88)
    print("V19 完成")
    print(f"Crypto結果：{CRYPTO_OUTPUT_DIR}")
    print(f"美股結果：{STOCK_OUTPUT_DIR}")
    print(f"總結：{OUTPUT_DIR / 'summary_v19.json'}")
    print(f"稽核：{'PASS' if audit['passed'] else 'FAIL'}｜{OUTPUT_DIR / 'v19_audit.json'}")
    print("=" * 88)
    return 0 if audit["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
