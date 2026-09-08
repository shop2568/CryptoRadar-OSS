#!/usr/bin/env python3
"""CryptoRadar V60_R3 — strict entries + protected runner + funding audit.

V60_R3 principles
-----------------
1) V56 remains the structural signal baseline, but V60 adds the user-requested anti-low-chase SHORT rule:
   when a SHORT is already >=2.0 completed-4H ATR below its recent 4H swing-high context, a DIRECT
   breakdown is forbidden. A FIRST_PULLBACK / REENTRY must retrace meaningfully above the broken
   level before failing, otherwise it is rejected. A separate 4H REBOUND_FAILURE_SHORT rescue path can
   re-enter later after a real rebound into short-term resistance and bearish rejection.
   V58 hard 1D/4H direction blocking and the old >3.25 ATR blanket veto remain disabled.
2) Before REAL TP1 the original structural stop never moves. Price-discovery Runner also
   keeps only its original structural stop, matching V56.
3) Two TRUE exit portfolios are compared from the same signal rules:
   A. TP1_100PCT: REAL TP1 closes 100%.
   B. TP1_50_TP2_25_TP3_25_PLUS_MA:
      - TP1 closes 50%;
      - TP2 closes 25% of original size when a distinct real TP2 exists;
      - TP3 closes the remaining position when a distinct real TP3 exists;
      - short-term 5D/10D MA weakness can close whatever remainder is still open first;
      - MA weakness is confirmed on a completed 15m close and filled at next 15m open.
   TP2/TP3 and MA are one combined exit method, not separate strategies.
4) Two money-management reports are calculated for each true exit portfolio:
   A. ORIGINAL_1PCT_COMPOUND: start 1000U, risk 1% of current realized equity per trade.
   B. FIXED20_MAXLOSS10U: start 1000U, leverage setting 20x, structural stop unchanged,
      reverse-size notional so an exact structural-stop fill is approximately 10U all-in
      loss including modeled entry/exit fee+slippage. Leverage is margin only; P&L is NOT
      multiplied by 20.
5) V58 ideas are research labels only in V60:
   - completed 1D + 4H trend state;
   - chase-distance buckets (<2, 2-3.25, 3.25-4, 4-5, >5 ATR);
   - first-pullback subset;
   - MAX5 vs unlimited-capacity comparator;
   - optional V57-style 4H early-timing research, with the old 3.25ATR hard cutoff disabled.
6) No look-ahead, no ticker exceptions.
7) Speed/restart:
   - exchange is scanned once; TP1-all is rebuilt by transforming the same candidate DB;
   - stock baseline preferentially reuses V58 per-symbol entry checkpoints and only re-simulates
     exits on shared Alpaca 15m cache;
   - V60 stock re-simulation and early-timing rows have per-symbol checkpoints.

This remains a research backtest, not a live-trading recommendation.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from contextlib import contextmanager
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

# ======================================================================================
# Load V56 baseline
# ======================================================================================

ROOT = Path.home() / "CryptoRadar"
V56_PATH = ROOT / "backtest_1y_v56.py"
if not V56_PATH.exists():
    V56_PATH = Path(__file__).with_name("backtest_1y_v56.py")
if not V56_PATH.exists():
    raise RuntimeError("找不到 backtest_1y_v56.py；請把 V56 與 V60 放在 ~/CryptoRadar。")

spec = importlib.util.spec_from_file_location("cryptoradar_v56_components_v60", V56_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"無法載入 V56：{V56_PATH}")
v56 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v56
spec.loader.exec_module(v56)

engine = v56.engine
v22 = v56.v22
v32 = v56.v32

# ======================================================================================
# Version / output / money-management constants
# ======================================================================================

OUTPUT_DIR = ROOT / "backtest_1y_v60_r3_results"
MARKETS_OUTPUT_DIR = OUTPUT_DIR / "exchange_markets"
STOCK_OUTPUT_DIR = OUTPUT_DIR / "us_stocks"
ALPACA_D1_OUTPUT_DIR = OUTPUT_DIR / "us_stocks_alpaca_d1"
RESEARCH_OUTPUT_DIR = OUTPUT_DIR / "research"
STOCK_RESIM_CACHE_DIR = OUTPUT_DIR / "stock_resim_cache_v60"
STOCK_EARLY_CACHE_DIR = OUTPUT_DIR / "stock_early_cache_v60"

SCHEME_V60 = "V60_V56_CORE_EXIT_RISK_RESEARCH"
STOCK_SCHEME_V60 = "V60_ALPACA_V56_BASELINE_RESIM"
STOCK_EARLY_SCHEME_V60 = "V60_ALPACA_4H_EARLY_RESEARCH"

STARTING_EQUITY = 1000.0
FIXED_LEVERAGE = float(os.getenv("V60_FIXED_LEVERAGE", "20"))
FIXED_MAX_LOSS_U = float(os.getenv("V60_FIXED_MAX_LOSS_U", "10"))
# Warning only. Exact liquidation depends on venue maintenance margin/tier/fees/margin mode.
ISOLATED_20X_WARNING_STOP_PCT = float(os.getenv("V60_ISOLATED_WARNING_STOP_PCT", "4.0"))
EARLY_EPISODE_DEDUPE_HOURS = int(os.getenv("V60_EARLY_EPISODE_DEDUPE_HOURS", "72"))
EARLY_QUALITY_THRESHOLD = float(os.getenv("V60_EARLY_QUALITY_THRESHOLD", "70"))
ENABLE_EARLY_RESEARCH = os.getenv("V60_ENABLE_EARLY_RESEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}
# R3 defaults to a fresh stock scan. Set this to 1 only when deliberately reproducing
# the older V58 checkpoint snapshot.
REUSE_V58_STOCK_CHECKPOINTS = os.getenv("V60_REUSE_V58_STOCK_CHECKPOINTS", "0").strip().lower() not in {"0", "false", "no", "off"}

# Historical funding is analysis-only: it is attached as-of each signal and never
# blocks a trade. Missing history remains missing rather than being treated as zero.
FUNDING_PAGE_LIMIT = int(os.getenv("V60_FUNDING_PAGE_LIMIT", "1000"))
FUNDING_FETCH_RETRIES = int(os.getenv("V60_FUNDING_FETCH_RETRIES", "4"))
FUNDING_CACHE_DIR = ROOT / "backtest_cache_1y_funding"

# V60 formal anti-low-chase SHORT rule.  These are global, no ticker exceptions.
V60_DEEP_SHORT_ATR = float(os.getenv("V60_DEEP_SHORT_ATR", "2.0"))
V60_MIN_PULLBACK_RETRACE_ATR = float(os.getenv("V60_MIN_PULLBACK_RETRACE_ATR", "0.75"))
V60_REBOUND_RESCUE_WINDOW_DAYS = int(os.getenv("V60_REBOUND_RESCUE_WINDOW_DAYS", "90"))
V60_REBOUND_STOP_BUFFER_ATR = float(os.getenv("V60_REBOUND_STOP_BUFFER_ATR", "0.25"))
V60_REBOUND_MIN_STOP_PCT = float(os.getenv("V60_REBOUND_MIN_STOP_PCT", "0.8"))
V60_REBOUND_MAX_STOP_PCT = float(os.getenv("V60_REBOUND_MAX_STOP_PCT", "8.0"))
V60_REBOUND_MIN_ROOM_R = float(os.getenv("V60_REBOUND_MIN_ROOM_R", "4.0"))
V60_REBOUND_REARM_HOURS = int(os.getenv("V60_REBOUND_REARM_HOURS", "72"))

if FIXED_LEVERAGE <= 0 or FIXED_MAX_LOSS_U <= 0:
    raise ValueError("V60_FIXED_LEVERAGE 與 V60_FIXED_MAX_LOSS_U 必須 > 0")
if V60_DEEP_SHORT_ATR <= 0 or V60_MIN_PULLBACK_RETRACE_ATR < 0:
    raise ValueError("V60_DEEP_SHORT_ATR 必須 > 0；V60_MIN_PULLBACK_RETRACE_ATR 必須 >= 0")

# Force the requested 1000U comparison baseline.
engine.STARTING_EQUITY = STARTING_EQUITY
if hasattr(v22, "v19"):
    v22.v19.STOCK_STARTING_EQUITY = STARTING_EQUITY

# Redirect inherited writers into V60.
v56.OUTPUT_DIR = OUTPUT_DIR
v56.MARKETS_OUTPUT_DIR = MARKETS_OUTPUT_DIR
v56.STOCK_OUTPUT_DIR = STOCK_OUTPUT_DIR
v56.ALPACA_D1_OUTPUT_DIR = ALPACA_D1_OUTPUT_DIR
v56.SCHEME_V56 = SCHEME_V60
v56.V56_STOCK_SCHEME = STOCK_SCHEME_V60

# Clean inherited in-process audits for deterministic reruns.
for _name in ("_STRUCTURE_AUDIT", "_PER_MARKET_STRUCTURE", "_V56_EXPANSION_AUDIT"):
    _obj = getattr(v56, _name, None)
    if hasattr(_obj, "clear"):
        _obj.clear()
if hasattr(v56, "_V56_MA_CACHE"):
    v56._V56_MA_CACHE.clear()
if hasattr(v56, "_V56_MA_CACHE_STATS"):
    v56._V56_MA_CACHE_STATS.update({"builds": 0, "hits": 0, "evictions": 0})

# V60 global research stores.
_SIM_AUDIT: dict[str, dict[str, Any]] = {}
_EXCHANGE_FILTER_AUDIT: list[dict[str, Any]] = []
_EARLY_STOCK_ROWS: list[dict[str, Any]] = []
_V60_ENTRY_GATE_AUDIT: list[dict[str, Any]] = []
_V60_EXCHANGE_REBOUND_AUDIT: list[dict[str, Any]] = []

POLICY_SIGNATURE = (
    "V60_R3|V56_STRUCTURE_BASE|DEEP_SHORT>=2ATR_NO_DIRECT|PULLBACK_RETRACE>=0.75ATR|REBOUND_FAIL_RESCUE|"
    "STRUCT_STOP_PRE_TP1|BREAKEVEN_STOP_POST_TP1|TP1=0.50|TP2=0.25|TP3=REST|MA=5D10D|"
    "STOCK_LONG_BULL_FIRST_PULLBACK|FUNDING_AUDIT_ONLY|NO_V58_3P25_BLANKET_VETO"
)


# ======================================================================================
# Generic helpers
# ======================================================================================

def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _to_utc(value: Any) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        return t.tz_localize("UTC")
    return t.tz_convert("UTC")


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_union(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
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


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ======================================================================================
# Historical funding audit (exchange markets only; never a signal gate)
# ======================================================================================

_ORIGINAL_FETCH_CLOSED_OHLCV_R3 = engine.fetch_closed_ohlcv


def _funding_cache_path(target: Any) -> Path:
    safe_symbol = "".join(ch if ch.isalnum() else "_" for ch in str(target.symbol))
    return FUNDING_CACHE_DIR / f"{target.exchange_name}_{safe_symbol}.csv.gz"


def _fetch_historical_funding(target: Any, candle_index: pd.DatetimeIndex) -> pd.Series:
    """Return the last funding event known at each candle, without look-ahead."""
    if len(candle_index) == 0 or engine.asset_class(target.base) == "TOKENIZED_TRADFI":
        return pd.Series(float("nan"), index=candle_index, dtype="float64")
    exchange = target.exchange
    if not bool(getattr(exchange, "has", {}).get("fetchFundingRateHistory")):
        return pd.Series(float("nan"), index=candle_index, dtype="float64")

    FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _funding_cache_path(target)
    start_ms = int(candle_index[0].timestamp() * 1000)
    end_ms = int(candle_index[-1].timestamp() * 1000)
    records: list[tuple[int, float]] = []
    if cache_path.exists():
        try:
            cached = pd.read_csv(cache_path)
            for row in cached.itertuples(index=False):
                records.append((int(row.timestamp), float(row.fundingRate)))
        except Exception:
            records = []

    cursor = max(start_ms, max((ts for ts, _ in records), default=start_ms - 1) + 1)
    while cursor <= end_ms:
        batch = None
        for attempt in range(FUNDING_FETCH_RETRIES):
            try:
                batch = exchange.fetch_funding_rate_history(
                    target.symbol, since=cursor, limit=FUNDING_PAGE_LIMIT
                )
                break
            except Exception:
                if attempt + 1 < FUNDING_FETCH_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
        if batch is None or not batch:
            break
        newest = cursor
        for item in batch:
            ts = item.get("timestamp")
            rate = item.get("fundingRate")
            if ts is None or rate is None:
                continue
            ts = int(ts); rate = float(rate)
            if math.isfinite(rate):
                records.append((ts, rate)); newest = max(newest, ts)
        if newest <= cursor:
            break
        cursor = newest + 1
        if newest >= end_ms:
            break

    if not records:
        return pd.Series(float("nan"), index=candle_index, dtype="float64")
    dedup = pd.DataFrame(records, columns=["timestamp", "fundingRate"])
    dedup = dedup.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    dedup.to_csv(cache_path, index=False, compression="gzip")
    events = pd.Series(
        dedup["fundingRate"].to_numpy(dtype="float64"),
        index=pd.to_datetime(dedup["timestamp"], unit="ms", utc=True),
    )
    return events.reindex(candle_index, method="ffill", tolerance=pd.Timedelta(hours=24))


def _fetch_closed_ohlcv_with_funding_r3(target: Any):
    data, quality = _ORIGINAL_FETCH_CLOSED_OHLCV_R3(target)
    data = data.copy(); quality = dict(quality)
    try:
        data["funding_rate"] = _fetch_historical_funding(target, data.index)
        data["funding_available"] = data["funding_rate"].notna()
        known = int(data["funding_available"].sum())
        quality["funding_known_bars"] = known
        quality["funding_coverage_pct"] = round(100.0 * known / len(data), 3) if len(data) else 0.0
    except Exception as exc:
        data["funding_rate"] = float("nan")
        data["funding_available"] = False
        quality["funding_known_bars"] = 0
        quality["funding_coverage_pct"] = 0.0
        quality["funding_error"] = f"{type(exc).__name__}: {exc}"
    quality["funding_used_as_filter"] = False
    return data, quality


engine.fetch_closed_ohlcv = _fetch_closed_ohlcv_with_funding_r3


def _funding_at_signal_r3(exchange_name: str, symbol: str, signal_time: Any, base: str) -> tuple[float | None, float | None]:
    if engine.asset_class(base) == "TOKENIZED_TRADFI":
        return None, None
    safe_symbol = "".join(ch if ch.isalnum() else "_" for ch in str(symbol))
    path = FUNDING_CACHE_DIR / f"{exchange_name}_{safe_symbol}.csv.gz"
    if not path.exists():
        return None, None
    try:
        data = pd.read_csv(path)
        data["timestamp"] = pd.to_numeric(data["timestamp"], errors="coerce")
        data["fundingRate"] = pd.to_numeric(data["fundingRate"], errors="coerce")
        data = data.dropna(subset=["timestamp", "fundingRate"]).sort_values("timestamp")
        signal_ms = int(_to_utc(signal_time).timestamp() * 1000)
        eligible = data[data["timestamp"] <= signal_ms]
        if eligible.empty:
            return None, None
        last = eligible.iloc[-1]
        age = (signal_ms - float(last["timestamp"])) / 3_600_000.0
        if age > 24.0:
            return None, round(age, 4)
        return float(last["fundingRate"]), round(age, 4)
    except Exception:
        return None, None


def _price_key(value: Any) -> str:
    x = _safe_float(value)
    return "nan" if not math.isfinite(x) else f"{x:.12g}"


def _trade_key(
    exchange: Any,
    symbol: Any,
    direction: Any,
    entry_time: Any,
    entry: Any,
    stop: Any,
    tp1: Any,
) -> str:
    try:
        ts = _to_utc(entry_time).isoformat()
    except Exception:
        ts = str(entry_time)
    return "|".join([
        str(exchange or ""), str(symbol or ""), str(direction or "").upper(), ts,
        _price_key(entry), _price_key(stop), _price_key(tp1),
    ])


def _row_trade_key(row: dict[str, Any]) -> str:
    return _trade_key(
        row.get("exchange"), row.get("symbol"), row.get("direction"), row.get("entry_time"),
        row.get("entry"), row.get("stop"), row.get("tp1"),
    )


def _frame_target_meta(frame: pd.DataFrame) -> dict[str, Any]:
    meta = dict(frame.attrs.get("_v60_target_meta", {}) or {})
    target = getattr(v32, "_CURRENT_TARGET", None)
    if target is not None:
        meta.setdefault("exchange", getattr(target, "exchange_name", ""))
        meta.setdefault("symbol", getattr(target, "symbol", ""))
        meta.setdefault("base", getattr(target, "base", ""))
    return meta


def _attach_sim_audit(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    audit = _SIM_AUDIT.get(_row_trade_key(out))
    if audit:
        for key, value in audit.items():
            if key not in {"exchange", "symbol", "base", "direction", "entry_time", "entry", "stop", "tp1"}:
                out[key] = value
    rate, age = _funding_at_signal_r3(
        str(out.get("exchange") or out.get("exchange_name") or ""),
        str(out.get("symbol") or ""),
        out.get("signal_time") or out.get("entry_time"),
        str(out.get("base") or ""),
    )
    out["funding_rate_at_signal"] = "" if rate is None else rate
    out["funding_age_hours"] = "" if age is None else age
    out["funding_used_as_filter"] = False
    return out


# ======================================================================================
# V58-style trend/chase context — observational only, never a formal V60 gate
# ======================================================================================

def _state_on_bars(bars: pd.DataFrame, *, daily: bool) -> pd.DataFrame:
    if bars.empty:
        return bars
    d = bars.copy()
    c = pd.to_numeric(d["close"], errors="coerce")
    for span in (5, 10, 20, 50):
        d[f"ema{span}"] = c.ewm(span=span, adjust=False).mean()
    slope_lb = 3 if daily else 4
    return_lb = 10 if daily else 6
    d["ema20_slope"] = d["ema20"] - d["ema20"].shift(slope_lb)
    d["ret"] = c / c.shift(return_lb) - 1.0
    bull = (
        (c > d["ema20"]).astype(int)
        + (d["ema5"] > d["ema10"]).astype(int)
        + (d["ema20"] > d["ema50"]).astype(int)
        + (d["ema20_slope"] > 0).astype(int)
        + (d["ret"] > 0).astype(int)
    )
    bear = (
        (c < d["ema20"]).astype(int)
        + (d["ema5"] < d["ema10"]).astype(int)
        + (d["ema20"] < d["ema50"]).astype(int)
        + (d["ema20_slope"] < 0).astype(int)
        + (d["ret"] < 0).astype(int)
    )
    state = np.full(len(d), "NEUTRAL", dtype=object)
    state[bull.to_numpy() >= 4] = "BULL"
    state[bear.to_numpy() >= 4] = "BEAR"
    d["trend_state"] = state
    d["bull_score"] = bull.astype(int)
    d["bear_score"] = bear.astype(int)
    return d


def _individual_trend_context(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(index=getattr(frame, "index", None))
    base = frame[["open", "high", "low", "close"]].copy()
    four = base.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    day = base.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    if four.empty or day.empty:
        return pd.DataFrame(index=frame.index)

    prev_close = four["close"].shift(1)
    tr = pd.concat([
        four["high"] - four["low"],
        (four["high"] - prev_close).abs(),
        (four["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    four["atr14"] = tr.rolling(14, min_periods=14).mean()
    four["swing_low20"] = four["low"].shift(1).rolling(20, min_periods=10).min()
    four["swing_high20"] = four["high"].shift(1).rolling(20, min_periods=10).max()

    fs = _state_on_bars(four, daily=False)
    ds = _state_on_bars(day, daily=True)
    # Entry inside current 4H/day may only use the previous completed 4H/day.
    fcomp = fs[["trend_state", "bull_score", "bear_score", "atr14", "swing_low20", "swing_high20"]].shift(1)
    dcomp = ds[["trend_state", "bull_score", "bear_score"]].shift(1)
    out = pd.DataFrame(index=frame.index)
    fa = fcomp.reindex(frame.index, method="ffill")
    da = dcomp.reindex(frame.index, method="ffill")
    out["trend_4h"] = fa["trend_state"]
    out["trend_4h_bull_score"] = fa["bull_score"]
    out["trend_4h_bear_score"] = fa["bear_score"]
    out["atr4h"] = fa["atr14"]
    out["swing_low20_4h"] = fa["swing_low20"]
    out["swing_high20_4h"] = fa["swing_high20"]
    out["trend_1d"] = da["trend_state"]
    out["trend_1d_bull_score"] = da["bull_score"]
    out["trend_1d_bear_score"] = da["bear_score"]
    return out


def _context_at(ctx: pd.DataFrame, frame: pd.DataFrame, ts: Any) -> dict[str, Any]:
    if ctx is None or ctx.empty or frame is None or frame.empty:
        return {"trend_4h": "UNKNOWN", "trend_1d": "UNKNOWN"}
    t = _to_utc(ts)
    idx = frame.index
    t_cmp = t.tz_localize(None) if idx.tz is None else t.tz_convert(idx.tz)
    pos = int(idx.searchsorted(t_cmp, side="right") - 1)
    if pos < 0 or pos >= len(ctx):
        return {"trend_4h": "UNKNOWN", "trend_1d": "UNKNOWN"}
    r = ctx.iloc[pos]
    return {
        "trend_4h": str(r.get("trend_4h") or "UNKNOWN"),
        "trend_1d": str(r.get("trend_1d") or "UNKNOWN"),
        "trend_4h_bull_score": _safe_float(r.get("trend_4h_bull_score")),
        "trend_4h_bear_score": _safe_float(r.get("trend_4h_bear_score")),
        "trend_1d_bull_score": _safe_float(r.get("trend_1d_bull_score")),
        "trend_1d_bear_score": _safe_float(r.get("trend_1d_bear_score")),
        "atr4h": _safe_float(r.get("atr4h")),
        "swing_low20_4h": _safe_float(r.get("swing_low20_4h")),
        "swing_high20_4h": _safe_float(r.get("swing_high20_4h")),
    }


def _chase_distance(direction: str, entry: float, info: dict[str, Any]) -> float:
    atr = _safe_float(info.get("atr4h"))
    if not math.isfinite(atr) or atr <= 0:
        return float("nan")
    direction = str(direction).upper()
    if direction == "LONG":
        swing = _safe_float(info.get("swing_low20_4h"))
        return (entry - swing) / atr if math.isfinite(swing) else float("nan")
    swing = _safe_float(info.get("swing_high20_4h"))
    return (swing - entry) / atr if math.isfinite(swing) else float("nan")


def _timing_quality_score_fixed(chase_atr: float, stop_pct: float, rr1: float, setup_type: str) -> float:
    """V57 observational quality score, preserving the original 3.25ATR scoring scale.

    V60 disables the 3.25ATR *hard rejection*, but does not redefine the old score so the
    quality>=70 research remains comparable.
    """
    if not math.isfinite(chase_atr):
        chase_pts = 10.0
    elif chase_atr <= 1.25:
        chase_pts = 40.0
    elif chase_atr >= 3.25:
        chase_pts = 0.0
    else:
        chase_pts = 40.0 * (3.25 - chase_atr) / (3.25 - 1.25)
    if not math.isfinite(stop_pct) or stop_pct <= 0:
        stop_pts = 0.0
    elif 1.0 <= stop_pct <= 6.0:
        stop_pts = 25.0
    elif stop_pct < 1.0:
        stop_pts = max(0.0, 25.0 * stop_pct)
    else:
        stop_pts = max(0.0, 25.0 * (12.0 - stop_pct) / 6.0)
    if not math.isfinite(rr1) or rr1 < 4.0:
        room_pts = 0.0
    else:
        room_pts = min(25.0, 12.5 + (min(rr1, 8.0) - 4.0) / 4.0 * 12.5)
    freshness = {
        "EARLY_FIRST_TURN_LONG": 10.0,
        "EARLY_HIGH_TURN_SHORT": 10.0,
        "EARLY_REBOUND_FAIL_SHORT": 10.0,
        "FIRST_PULLBACK_RECLAIM": 9.0,
        "DIRECT_BREAK": 6.0,
        "STRUCTURAL_REENTRY": 7.0,
    }.get(str(setup_type), 5.0)
    return round(max(0.0, min(100.0, chase_pts + stop_pts + room_pts + freshness)), 2)


def _atr_bucket(value: Any) -> str:
    x = _safe_float(value)
    if not math.isfinite(x):
        return "UNKNOWN"
    if x < 2.0:
        return "LT_2"
    if x < 3.25:
        return "2_TO_3P25"
    if x < 4.0:
        return "3P25_TO_4"
    if x < 5.0:
        return "4_TO_5"
    return "GE_5"


def _trend_bucket(direction: str, trend_1d: str, trend_4h: str) -> str:
    direction = str(direction).upper()
    d1 = str(trend_1d or "UNKNOWN").upper()
    h4 = str(trend_4h or "UNKNOWN").upper()
    aligned = "BULL" if direction == "LONG" else "BEAR"
    opposite = "BEAR" if direction == "LONG" else "BULL"
    if d1 == aligned and h4 == aligned:
        return "ALIGNED_1D_4H"
    if d1 == opposite:
        return "D1_OPPOSITE"
    if d1 == "NEUTRAL" and h4 == opposite:
        return "D1_NEUTRAL_4H_OPPOSITE"
    if h4 == opposite:
        return "4H_OPPOSITE"
    if d1 == aligned or h4 == aligned:
        return "PARTIAL_ALIGNED"
    return "NEUTRAL_UNKNOWN"


def _v58_observational_block_reason(direction: str, trend_1d: str, trend_4h: str, chase: Any) -> str:
    """What V58 ordinary baseline gating would have done. Research label only."""
    d = str(direction).upper()
    d1 = str(trend_1d or "UNKNOWN").upper()
    h4 = str(trend_4h or "UNKNOWN").upper()
    x = _safe_float(chase)
    if d == "LONG":
        if d1 == "BEAR":
            return "V58_BLOCK_D1_BEAR_LONG"
        if d1 == "NEUTRAL" and h4 == "BEAR":
            return "V58_BLOCK_D1_NEUTRAL_4H_BEAR_LONG"
    elif d == "SHORT":
        if d1 == "BULL":
            return "V58_BLOCK_D1_BULL_SHORT"
        if d1 == "NEUTRAL" and h4 == "BULL":
            return "V58_BLOCK_D1_NEUTRAL_4H_BULL_SHORT"
    if math.isfinite(x) and x > 3.25:
        return "V58_BLOCK_CHASE_GT_3P25_ATR"
    return "V58_KEEP_ORDINARY"


def _annotate_trend_row(row: dict[str, Any], ctx: pd.DataFrame, frame: pd.DataFrame) -> dict[str, Any]:
    out = dict(row)
    try:
        info = _context_at(ctx, frame, out.get("entry_time"))
        chase = _chase_distance(str(out.get("direction") or ""), _safe_float(out.get("entry")), info)
        out["trend_1d"] = info.get("trend_1d")
        out["trend_4h"] = info.get("trend_4h")
        out["trend_1d_bull_score"] = info.get("trend_1d_bull_score")
        out["trend_1d_bear_score"] = info.get("trend_1d_bear_score")
        out["trend_4h_bull_score"] = info.get("trend_4h_bull_score")
        out["trend_4h_bear_score"] = info.get("trend_4h_bear_score")
        out["chase_distance_4h_atr"] = chase
        out["atr_bucket"] = _atr_bucket(chase)
        out["trend_bucket"] = _trend_bucket(str(out.get("direction") or ""), str(info.get("trend_1d")), str(info.get("trend_4h")))
        out["v58_observational_gate"] = _v58_observational_block_reason(
            str(out.get("direction") or ""), str(info.get("trend_1d")), str(info.get("trend_4h")), chase
        )
    except Exception:
        out.setdefault("trend_1d", "UNKNOWN")
        out.setdefault("trend_4h", "UNKNOWN")
        out.setdefault("atr_bucket", "UNKNOWN")
        out.setdefault("trend_bucket", "NEUTRAL_UNKNOWN")
        out.setdefault("v58_observational_gate", "AUDIT_ERROR_FAIL_OPEN")
    return out


# ======================================================================================
# Capture full TP1/TP2/TP3 target tuple for engine paths whose simulator signature has only TP1
# ======================================================================================

_ORIGINAL_TARGET_SELECTOR = v32.selective_structure_or_runner_targets


def selective_structure_or_runner_targets_v60(
    frame: pd.DataFrame,
    signal_i: int,
    direction: str,
    entry: float,
    stop_distance: float,
    cached_pivot_highs: Any,
    cached_pivot_lows: Any,
    minimum_room_r: float = 2.0,
):
    targets = _ORIGINAL_TARGET_SELECTOR(
        frame, signal_i, direction, entry, stop_distance,
        cached_pivot_highs, cached_pivot_lows, minimum_room_r=minimum_room_r,
    )
    if targets is not None:
        try:
            frame.attrs["_v60_last_targets"] = {
                "signal_i": int(signal_i),
                "direction": str(direction).upper(),
                "entry": float(entry),
                "stop_distance": float(stop_distance),
                "tp1": float(targets[0]),
                "tp2": float(targets[1]),
                "tp3": float(targets[2]),
                "rr1": float(targets[3]),
                "rr2": float(targets[4]),
                "rr3": float(targets[5]),
            }
        except Exception:
            pass
    return targets


v32.selective_structure_or_runner_targets = selective_structure_or_runner_targets_v60


def _resolve_tp23_from_frame(
    frame: pd.DataFrame,
    direction: str,
    entry: float,
    tp1: float,
    stop_distance: float,
) -> tuple[float, float]:
    d = frame.attrs.get("_v60_last_targets", {}) or {}
    try:
        same = (
            str(d.get("direction") or "").upper() == str(direction).upper()
            and math.isclose(float(d.get("entry")), float(entry), rel_tol=1e-8, abs_tol=max(1e-10, abs(entry) * 1e-10))
            and math.isclose(float(d.get("tp1")), float(tp1), rel_tol=1e-8, abs_tol=max(1e-10, abs(tp1) * 1e-10))
            and math.isclose(float(d.get("stop_distance")), float(stop_distance), rel_tol=1e-8, abs_tol=max(1e-10, abs(stop_distance) * 1e-10))
        )
        if same:
            return _safe_float(d.get("tp2")), _safe_float(d.get("tp3"))
    except Exception:
        pass
    return float("nan"), float("nan")


# ======================================================================================
# V60 combined exit simulator: TP1 50% + TP2 25% + TP3 rest + MA weakness
# ======================================================================================

def simulate_tp1_tp23_ma_v60(
    frame: pd.DataFrame,
    entry_i: int,
    direction: str,
    entry: float,
    stop: float,
    tp1: float,
    stop_distance: float,
    *,
    tp2: float | None = None,
    tp3: float | None = None,
) -> dict[str, Any]:
    direction = str(direction).upper()
    sign = 1.0 if direction == "LONG" else -1.0
    cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    end_i = len(frame) - 1

    meta = _frame_target_meta(frame)
    exchange = str(meta.get("exchange") or "")
    symbol = str(meta.get("symbol") or "")
    base = str(meta.get("base") or symbol)
    entry_time = frame.index[entry_i].isoformat()

    def _record(result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        exit_i = int(result.get("exit_i", end_i))
        key = _trade_key(exchange, symbol, direction, entry_time, entry, stop, tp1)
        audit = {
            "exchange": exchange,
            "symbol": symbol,
            "base": base,
            "direction": direction,
            "entry_time": entry_time,
            "entry": float(entry),
            "stop": float(stop),
            "tp1": float(tp1),
            "tp2": state.get("tp2"),
            "tp3": state.get("tp3"),
            "real_tp1": bool(state.get("real_tp1")),
            "tp1_hit": bool(state.get("tp1_hit")),
            "tp2_hit": bool(state.get("tp2_hit")),
            "tp3_hit": bool(state.get("tp3_hit")),
            "tp1_hit_time": state.get("tp1_hit_time", ""),
            "tp2_hit_time": state.get("tp2_hit_time", ""),
            "tp3_hit_time": state.get("tp3_hit_time", ""),
            "ma_weakness_signal_time": state.get("ma_weakness_signal_time", ""),
            "ma_exit_time": state.get("ma_exit_time", ""),
            "post_tp1_ma_days": int(state.get("post_tp1_ma_days", 0) or 0),
            "tp1_exit_fraction": float(state.get("tp1_exit_fraction", 0.0) or 0.0),
            "tp2_exit_fraction": float(state.get("tp2_exit_fraction", 0.0) or 0.0),
            "tp3_exit_fraction": float(state.get("tp3_exit_fraction", 0.0) or 0.0),
            "ma_exit_fraction": float(state.get("ma_exit_fraction", 0.0) or 0.0),
            "open_remainder_fraction": float(state.get("open_remainder_fraction", 0.0) or 0.0),
            "v60_exit_policy": "TP1_50_TP2_25_TP3_REST_PLUS_MA",
            "exit_time_v60": frame.index[exit_i].isoformat() if 0 <= exit_i < len(frame) else "",
            "exit_reason_v60": str(result.get("exit_reason") or ""),
            "net_r_v60": _safe_float(result.get("net_r"), 0.0),
        }
        _SIM_AUDIT[key] = audit
        result.update({
            "tp1_was_hit": audit["tp1_hit"],
            "tp1_exit_fraction": audit["tp1_exit_fraction"],
            "tp2_exit_fraction": audit["tp2_exit_fraction"],
            "tp3_exit_fraction": audit["tp3_exit_fraction"],
            "ma_exit_fraction": audit["ma_exit_fraction"],
            "post_tp1_ma_days": audit["post_tp1_ma_days"],
        })
        return result

    # Price-discovery Runner has no real TP1: preserve V56 fixed structural stop only.
    rr1_sentinel = abs(float(tp1) - float(entry)) / max(float(stop_distance), 1e-12)
    if rr1_sentinel >= v32.RUNNER_SENTINEL_THRESHOLD_R:
        result = v56.simulate_fixed_stop_runner_v56(frame, entry_i, direction, entry, stop, tp1, stop_distance)
        return _record(result, {
            "tp2": float("nan"), "tp3": float("nan"), "real_tp1": False,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "open_remainder_fraction": 1.0 if str(result.get("outcome")) == "OPEN" else 0.0,
        })

    if tp2 is None or tp3 is None:
        r2, r3 = _resolve_tp23_from_frame(frame, direction, entry, tp1, stop_distance)
        if tp2 is None:
            tp2 = r2
        if tp3 is None:
            tp3 = r3
    tp2f = _safe_float(tp2)
    tp3f = _safe_float(tp3)
    rr1 = sign * (float(tp1) - float(entry)) / float(stop_distance)
    rr2 = sign * (tp2f - float(entry)) / float(stop_distance) if math.isfinite(tp2f) else float("nan")
    rr3 = sign * (tp3f - float(entry)) / float(stop_distance) if math.isfinite(tp3f) else float("nan")
    distinct2 = bool(math.isfinite(rr2) and rr2 > rr1 + 1e-9)
    distinct3 = bool(math.isfinite(rr3) and rr3 > max(rr1, rr2 if distinct2 else rr1) + 1e-9)

    ma = v56._v56_get_completed_daily_ma_arrays(frame)
    active_stop = float(stop)
    tp1_hit_i: int | None = None
    selected_days = 0
    remaining = 1.0
    legs: list[tuple[float, float, str, int]] = []
    state: dict[str, Any] = {
        "tp2": tp2f, "tp3": tp3f, "real_tp1": True,
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        "tp1_hit_time": "", "tp2_hit_time": "", "tp3_hit_time": "",
        "ma_weakness_signal_time": "", "ma_exit_time": "", "post_tp1_ma_days": 0,
        "tp1_exit_fraction": 0.0, "tp2_exit_fraction": 0.0, "tp3_exit_fraction": 0.0,
        "ma_exit_fraction": 0.0, "open_remainder_fraction": 0.0,
    }

    def _full_before_tp1(fill_price: float, fill_i: int, reason: str) -> dict[str, Any]:
        gross_r = sign * (fill_price - entry) / stop_distance
        result = engine.finish_result(
            direction, entry, stop_distance, gross_r,
            fill_price * cost_rate / stop_distance,
            fill_i, fill_price, reason, 0,
        )
        result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
        return _record(result, state)

    def _finish_all_closed(fill_i: int, reason: str) -> dict[str, Any]:
        total_fraction = sum(frac for frac, _, _, _ in legs)
        if total_fraction < 1.0 - 1e-8:
            raise RuntimeError(f"V60 partial exit fractions do not sum to 1: {total_fraction}")
        gross_r = sum(frac * sign * (price - entry) / stop_distance for frac, price, _, _ in legs)
        exit_cost_r = sum(frac * price * cost_rate / stop_distance for frac, price, _, _ in legs)
        weighted_exit = sum(frac * price for frac, price, _, _ in legs)
        result = engine.finish_result(
            direction, entry, stop_distance, gross_r, exit_cost_r,
            fill_i, weighted_exit, reason, 0,
        )
        result["outcome"] = "WIN" if float(result["net_r"]) > 0 else "LOSS"
        return _record(result, state)

    for i in range(entry_i, end_i + 1):
        row = frame.iloc[i]
        o = float(row["open"]); h = float(row["high"]); l = float(row["low"]); c = float(row["close"])

        if tp1_hit_i is None:
            # Conservative same-bar sequencing: stop is checked before TP1.
            stop_fill = engine.bar_hits_stop(direction, o, h, l, active_stop)
            if stop_fill is not None:
                return _full_before_tp1(float(stop_fill), i, "STOP")

            if engine.bar_hits_target(direction, o, h, l, float(tp1)):
                frac = 0.50
                legs.append((frac, float(tp1), "TP1", i))
                remaining -= frac
                tp1_hit_i = i
                state["tp1_hit"] = True
                state["tp1_hit_time"] = frame.index[i].isoformat()
                state["tp1_exit_fraction"] = frac
                selected_days, selected_line = v56._v56_choose_post_tp1_line(direction, i, ma)
                state["post_tp1_ma_days"] = int(selected_days)

                # Same conservative V56 fallback: if prior-completed-day MA history is not
                # available, do not invent future data; close the entire remainder at TP1.
                if selected_days == 0 or not math.isfinite(selected_line):
                    if remaining > 1e-12:
                        legs.append((remaining, float(tp1), "MA_HISTORY_FALLBACK_AT_TP1", i))
                        state["ma_exit_fraction"] += remaining
                        remaining = 0.0
                    return _finish_all_closed(i, "TP1_50PCT_MA_HISTORY_FALLBACK_REMAINDER_AT_TP1")

                # Do not grant TP2/TP3 on the same bar that first touched TP1; intrabar order
                # beyond TP1 is unknown. Target checks start from the next completed bar.
                continue

        if tp1_hit_i is not None and i > tp1_hit_i and remaining > 1e-12:
            # R3 profit protection: after REAL TP1, the remaining position cannot turn
            # into a structural-stop loss. The original stop is replaced by breakeven.
            breakeven_fill = engine.bar_hits_stop(direction, o, h, l, float(entry))
            if breakeven_fill is not None:
                frac = remaining
                legs.append((frac, float(breakeven_fill), "POST_TP1_BREAKEVEN_STOP", i))
                remaining = 0.0
                state["post_tp1_breakeven_stop"] = True
                state["post_tp1_breakeven_stop_time"] = frame.index[i].isoformat()
                return _finish_all_closed(i, "TP1_50PCT_REMAINDER_BREAKEVEN_STOP")

            # Favorable structural targets happen intrabar; MA weakness is a completed-close
            # signal, so TP2/TP3 are processed before an MA signal on that same candle.
            if distinct2 and not state["tp2_hit"] and engine.bar_hits_target(direction, o, h, l, tp2f):
                frac = min(0.25, remaining)
                if frac > 1e-12:
                    legs.append((frac, tp2f, "TP2", i))
                    remaining -= frac
                    state["tp2_hit"] = True
                    state["tp2_hit_time"] = frame.index[i].isoformat()
                    state["tp2_exit_fraction"] += frac

            if distinct3 and not state["tp3_hit"] and remaining > 1e-12 and engine.bar_hits_target(direction, o, h, l, tp3f):
                # TP3 closes all remaining size. Normally this is 25%; if TP2 is not distinct,
                # TP3 can close the full 50% remainder rather than inventing a fake TP2 exit.
                frac = remaining
                legs.append((frac, tp3f, "TP3", i))
                remaining = 0.0
                state["tp3_hit"] = True
                state["tp3_hit_time"] = frame.index[i].isoformat()
                state["tp3_exit_fraction"] += frac
                return _finish_all_closed(i, "TP1_50PCT_TP2_TP3_ALL_TARGETS_CLOSED")

            if remaining > 1e-12:
                line_arr = ma["ma10"] if selected_days == 10 else ma["ma5"]
                line = float(line_arr[i])
                if math.isfinite(line):
                    weak = (c < line) if direction == "LONG" else (c > line)
                    if weak:
                        fill_i = i + 1
                        if fill_i <= end_i:
                            fill_price = float(frame["open"].iat[fill_i])
                            state["ma_weakness_signal_time"] = frame.index[i].isoformat()
                            state["ma_exit_time"] = frame.index[fill_i].isoformat()
                            frac = remaining
                            legs.append((frac, fill_price, f"MA{selected_days}_WEAKNESS", fill_i))
                            remaining = 0.0
                            state["ma_exit_fraction"] += frac
                            return _finish_all_closed(
                                fill_i,
                                f"TP1_50PCT_TP23_PARTIAL_POST_TP1_MA{selected_days}_WEAKNESS",
                            )

    # Research-window end. Realized TP legs keep their exit costs; an open remainder is
    # marked to final close without charging a future exit fee.
    if tp1_hit_i is not None:
        mark = float(frame["close"].iat[end_i])
        gross_r = sum(frac * sign * (price - entry) / stop_distance for frac, price, _, _ in legs)
        gross_r += remaining * sign * (mark - entry) / stop_distance
        entry_cost_r = entry * cost_rate / stop_distance
        realized_exit_cost_r = sum(frac * price * cost_rate / stop_distance for frac, price, _, _ in legs)
        cost_r = entry_cost_r + realized_exit_cost_r
        weighted_mark = sum(frac * price for frac, price, _, _ in legs) + remaining * mark
        state["open_remainder_fraction"] = float(remaining)
        result = {
            "exit_i": end_i,
            "exit_price": weighted_mark,
            "outcome": "OPEN" if remaining > 1e-12 else ("WIN" if gross_r - cost_r > 0 else "LOSS"),
            "exit_reason": "TP1_50PCT_TP23_PARTIAL_REMAINDER_OPEN_AT_END" if remaining > 1e-12 else "TARGETS_CLOSED_AT_END",
            "timed_out": 0,
            "gross_r": gross_r,
            "cost_r": cost_r,
            "net_r": gross_r - cost_r,
        }
        return _record(result, state)

    result = engine.finish_open_result(direction, entry, stop_distance, end_i, float(frame["close"].iat[end_i]))
    return _record(result, state)


# Engine/V56 paths use this simulator name during the exchange run.
v56.simulate_post_tp1_weakness_v56 = simulate_tp1_tp23_ma_v60


def _sim_stock_trade_v60(
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
    if target_mode == "PRICE_DISCOVERY_RUNNER" or float(rr1) >= v32.RUNNER_SENTINEL_THRESHOLD_R:
        result = simulate_tp1_tp23_ma_v60(
            frame, entry_i, direction, entry, stop, float(tp1), stop_distance,
            tp2=float(tp2), tp3=float(tp3),
        )
    else:
        result = simulate_tp1_tp23_ma_v60(
            frame, entry_i, direction, entry, stop, float(tp1), stop_distance,
            tp2=float(tp2), tp3=float(tp3),
        )
    result["tp1"] = float(tp1); result["tp2"] = float(tp2); result["tp3"] = float(tp3)
    result["rr1"] = float(rr1); result["rr2"] = float(rr2); result["rr3"] = float(rr3)
    result["target_mode"] = target_mode
    return result


v56._v56_sim_stock_trade = _sim_stock_trade_v60


# ======================================================================================
# V60 formal anti-low-chase SHORT gate + rebound-failure rescue
# ======================================================================================

_V57_V60 = None


def _load_v57_v60():
    global _V57_V60
    if _V57_V60 is not None:
        return _V57_V60
    path = ROOT / "backtest_1y_v57.py"
    if not path.exists():
        path = Path(__file__).with_name("backtest_1y_v57.py")
    if not path.exists():
        return None
    spec57 = importlib.util.spec_from_file_location("cryptoradar_v57_rebound_for_v60", path)
    if spec57 is None or spec57.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec57)
    sys.modules[spec57.name] = mod
    spec57.loader.exec_module(mod)
    _V57_V60 = mod
    return mod


def _v60_short_entry_gate(row: dict[str, Any]) -> tuple[bool, str]:
    """Block low-chasing SHORTs; allow a genuine retrace/rebound-failure instead.

    No future bars are used. V60_R2 is deliberately fail-closed for SHORT entry context:
    if a SHORT cannot prove it is not deep, or a deep SHORT cannot prove a real retrace /
    rebound-failure setup, it is rejected. LONG logic is unchanged.
    """
    direction = str(row.get("direction") or "").upper()
    if direction != "SHORT":
        return True, "KEEP_NON_SHORT"
    chase = _safe_float(row.get("chase_distance_4h_atr"))
    if not math.isfinite(chase):
        return False, "BLOCK_SHORT_MISSING_CHASE_CONTEXT"
    if chase < V60_DEEP_SHORT_ATR:
        return True, "KEEP_NOT_DEEP"
    setup = str(row.get("setup_type") or "").upper()
    if "REBOUND_FAIL" in setup or "REBOUND_FAILURE" in setup:
        return True, "KEEP_REBOUND_FAILURE_SHORT"
    if "DIRECT_BREAK" in setup or setup == "DIRECT_BREAKDOWN":
        return False, "BLOCK_DEEP_DIRECT_SHORT"
    if setup in {"FIRST_PULLBACK_RECLAIM", "STRUCTURAL_REENTRY"}:
        ext = _safe_float(row.get("entry_extension_atr"))
        # V56 definition is symmetric: a negative value means the pullback/rebound crossed
        # back beyond the broken level before the next entry. Require a meaningful retrace.
        if math.isfinite(ext) and ext <= -V60_MIN_PULLBACK_RETRACE_ATR:
            return True, "KEEP_DEEP_SHORT_AFTER_REAL_RETRACE"
        return False, "BLOCK_DEEP_SHORT_SHALLOW_PULLBACK"
    return False, "BLOCK_DEEP_SHORT_UNVERIFIED_SETUP"


def _v60_record_gate(row: dict[str, Any], keep: bool, reason: str, market: str) -> None:
    x = dict(row)
    x["v60_market"] = market
    x["v60_entry_gate_keep"] = bool(keep)
    x["v60_entry_gate_reason"] = reason
    x["v60_deep_short_atr_threshold"] = V60_DEEP_SHORT_ATR
    x["v60_min_pullback_retrace_atr"] = V60_MIN_PULLBACK_RETRACE_ATR
    _V60_ENTRY_GATE_AUDIT.append(x)


def _exchange_rebound_failure_rows_v60(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
) -> list[tuple[Any, ...]]:
    """General 4H rebound-failure SHORT rescue for crypto and tokenized markets.

    Required sequence: prior downswing is already extended -> prior completed 4H candle
    rebounds into EMA10/EMA20 resistance -> current completed 4H candle rejects lower.
    Entry is the next 15m open; stop is beyond the rebound structure + ATR buffer.
    """
    mod = _load_v57_v60()
    if mod is None or frame is None or frame.empty:
        return []
    four = mod._four_hour_stock_frame(frame)
    if four is None or four.empty or len(four) < 30:
        return []
    rows: list[tuple[Any, ...]] = []
    last_pub: pd.Timestamp | None = None
    highs15 = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    lows15 = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    for i in range(20, len(four) - 1):
        r = four.iloc[i]; p = four.iloc[i - 1]
        atr = _safe_float(r.get("atr14"))
        if not math.isfinite(atr) or atr <= 0:
            continue
        c = _safe_float(r.get("close")); o = _safe_float(r.get("open"))
        ema5 = _safe_float(r.get("ema5")); ema10 = _safe_float(r.get("ema10")); ema20 = _safe_float(r.get("ema20"))
        ema20_slope = _safe_float(r.get("ema20_slope")); close_pos_short = _safe_float(r.get("close_pos_short"))
        recent_hi20 = _safe_float(r.get("recent_high20")); recent_lo6 = _safe_float(r.get("recent_low6"))
        recent_hi6 = _safe_float(r.get("recent_high6")); rh = _safe_float(r.get("high"))
        pc = _safe_float(p.get("close")); ph = _safe_float(p.get("high")); pema10 = _safe_float(p.get("ema10")); pema20 = _safe_float(p.get("ema20"))
        vals = [c,o,ema5,ema10,ema20,ema20_slope,close_pos_short,recent_hi20,recent_lo6,recent_hi6,rh,pc,ph,pema10,pema20]
        if not all(math.isfinite(x) for x in vals):
            continue
        # The preceding move must genuinely be a deep downswing, not an ordinary pullback.
        prior_downswing_atr = (recent_hi20 - recent_lo6) / atr
        if prior_downswing_atr < max(3.0, V60_DEEP_SHORT_ATR + 0.75):
            continue
        damaged = (ema5 < ema10 and c < ema20) or (ema20_slope <= 0 and c < ema10)
        # A real rebound must reach the short-term resistance zone, not merely tick the old low.
        prev_rebound = ph >= min(pema10, pema20) - 0.10 * atr
        rebound_depth_atr = (ph - recent_lo6) / atr
        rejection = c < o and close_pos_short >= 0.60 and c < ema5 and c < pc
        if not (damaged and prev_rebound and rebound_depth_atr >= 0.75 and rejection):
            continue
        signal_ts = pd.Timestamp(r.get("source_last_ts"))
        if pd.isna(signal_ts) or signal_ts < research_start:
            continue
        if last_pub is not None and signal_ts - last_pub < pd.Timedelta(hours=V60_REBOUND_REARM_HOURS):
            continue
        signal_i = int(frame.index.searchsorted(signal_ts, side="right") - 1)
        entry_i = int(frame.index.searchsorted(signal_ts, side="right"))
        if signal_i < 0 or entry_i <= signal_i or entry_i >= len(frame):
            continue
        entry = _safe_float(frame["open"].iat[entry_i])
        base = max(recent_hi6, rh, ph)
        stop = base + V60_REBOUND_STOP_BUFFER_ATR * atr
        stop_distance = stop - entry
        if not math.isfinite(entry) or entry <= 0 or stop_distance <= 0:
            continue
        stop_pct = stop_distance / entry * 100.0
        if stop_pct < V60_REBOUND_MIN_STOP_PCT or stop_pct > V60_REBOUND_MAX_STOP_PCT:
            continue
        targets = v32.selective_structure_or_runner_targets(
            frame, signal_i, "SHORT", float(entry), float(stop_distance), pivot_highs, pivot_lows,
            minimum_room_r=V60_REBOUND_MIN_ROOM_R,
        )
        if targets is None:
            continue
        tp1,tp2,tp3,rr1,rr2,rr3,_ = targets
        if float(rr1) < V60_REBOUND_MIN_ROOM_R - 1e-9:
            continue
        if float(rr1) >= v32.RUNNER_SENTINEL_THRESHOLD_R:
            result = v56.simulate_fixed_stop_runner_v56(frame, entry_i, "SHORT", float(entry), float(stop), float(tp1), float(stop_distance))
            target_mode = "V60_REBOUND_FAIL_PRICE_DISCOVERY"
        else:
            result = v56.simulate_post_tp1_weakness_v56(frame, entry_i, "SHORT", float(entry), float(stop), float(tp1), float(stop_distance))
            target_mode = "V60_REBOUND_FAIL_REAL_STRUCTURE"
        exit_i = int(result["exit_i"])
        mfe_r = (entry - np.nanmin(lows15[entry_i:exit_i+1])) / stop_distance
        mae_r = (np.nanmax(highs15[entry_i:exit_i+1]) - entry) / stop_distance
        vr = _safe_float(frame.get("volume_ratio", pd.Series(index=frame.index, dtype=float)).iat[signal_i], 0.0)
        pr = _safe_float(frame.get("volume_pressure", pd.Series(index=frame.index, dtype=float)).iat[signal_i], 0.0)
        candle_atr = abs(_safe_float(frame["close"].iat[signal_i]) - _safe_float(frame["open"].iat[signal_i])) / atr
        row = (
            getattr(target,"exchange_name",""), getattr(target,"symbol",""), getattr(target,"base",""),
            "SHORT", signal_ts.isoformat(), frame.index[entry_i].isoformat(), frame.index[exit_i].isoformat(),
            v56._period_name(signal_ts, research_start), SCHEME_V60,
            float(entry), float(stop), float(tp1), float(tp2), float(tp3), float(rr1), float(rr2), float(rr3),
            float(result["exit_price"]), 0.05, result["outcome"], result["exit_reason"], result["timed_out"],
            float(result["gross_r"]), float(result["cost_r"]), float(result["net_r"]), float(vr), float(candle_atr),
            float(pr), float(rr1), float(stop_distance/atr), float(stop_pct), 1, exit_i-entry_i+1, float(mfe_r), float(mae_r),
        )
        chase = (recent_hi20 - entry) / atr
        audit = {
            "exchange": row[0], "symbol": row[1], "base": row[2], "direction": "SHORT",
            "setup_type": "V60_REBOUND_FAILURE_SHORT", "signal_time": row[4], "entry_time": row[5], "exit_time": row[6],
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr1": rr1,
            "chase_distance_4h_atr": chase, "prior_downswing_atr": prior_downswing_atr,
            "rebound_depth_atr": rebound_depth_atr, "rebound_resistance": min(pema10,pema20),
            "target_mode": target_mode, "exit_reason": result["exit_reason"], "net_r": result["net_r"],
            "entry_extension_atr": -rebound_depth_atr, "stop_pct": stop_pct,
        }
        _V60_EXCHANGE_REBOUND_AUDIT.append(audit)
        v56._STRUCTURE_AUDIT.append({
            **audit, "regime_audit": "V60_REBOUND_FAILURE", "structure_stop_base": base,
            "relative_volume_audit_only": vr, "market_heat_ratio_audit_only": 0.0,
            "tp1_was_hit": str(result.get("exit_reason","")).startswith("TP1_50PCT"),
        })
        rows.append(row)
        last_pub = signal_ts
    return rows


# ======================================================================================
# Exchange candidate wrapper: V56 candidates + V60 formal anti-low-chase gate + rebound rescue
# ======================================================================================

_ORIGINAL_CANDIDATE_ROWS_V56 = v56.candidate_rows_v56


def candidate_rows_v60(
    target: Any,
    frame: pd.DataFrame,
    research_start: pd.Timestamp,
    pivot_highs: Any,
    pivot_lows: Any,
    funnel: dict[str, int],
):
    if frame is None or frame.empty:
        yield from _ORIGINAL_CANDIDATE_ROWS_V56(target, frame, research_start, pivot_highs, pivot_lows, funnel)
        return

    frame.attrs["_v60_target_meta"] = {
        "exchange": getattr(target, "exchange_name", ""),
        "symbol": getattr(target, "symbol", ""),
        "base": getattr(target, "base", ""),
    }
    baseline = list(_ORIGINAL_CANDIDATE_ROWS_V56(target, frame, research_start, pivot_highs, pivot_lows, funnel))
    ctx = _individual_trend_context(frame)
    structure_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in getattr(v56, "_STRUCTURE_AUDIT", []):
        structure_lookup[(str(a.get("symbol")), str(a.get("direction")), str(a.get("entry_time")))] = a

    kept: list[tuple[Any, ...]] = []
    for row in baseline:
        try:
            r = {
                "exchange": row[0], "symbol": row[1], "base": row[2], "direction": row[3],
                "signal_time": row[4], "entry_time": row[5], "exit_time": row[6],
                "entry": row[9], "stop": row[10], "tp1": row[11], "tp2": row[12], "tp3": row[13],
                "rr1": row[14], "rr2": row[15], "rr3": row[16], "outcome": row[19],
                "exit_reason": row[20], "net_r": row[24], "mfe_r": row[33], "mae_r": row[34],
            }
            a = structure_lookup.get((str(row[1]), str(row[3]), str(row[5]))) or {}
            r["setup_type"] = str(a.get("setup_type") or "V32_CORE")
            r["entry_extension_atr"] = a.get("entry_extension_atr")
            r = _annotate_trend_row(r, ctx, frame)
            r = _attach_sim_audit(r)
            keep, reason = _v60_short_entry_gate(r)
            _v60_record_gate(r, keep, reason, "EXCHANGE_BASELINE")
            _EXCHANGE_FILTER_AUDIT.append(r)
            if keep:
                kept.append(row)
            else:
                funnel[f"V60_{reason}"] = funnel.get(f"V60_{reason}", 0) + 1
        except Exception as exc:
            # Strict for SHORT: an audit/context failure must not let a low-chase SHORT slip through.
            # LONG is unaffected by the anti-low-chase rule and therefore remains fail-open.
            funnel["V60_entry_gate_audit_errors"] = funnel.get("V60_entry_gate_audit_errors", 0) + 1
            print(f"[V60-GATE] {getattr(target,'symbol','')} audit error: {type(exc).__name__}: {exc}")
            try:
                if str(row[3]).upper() == "SHORT":
                    funnel["V60_BLOCK_SHORT_AUDIT_ERROR"] = funnel.get("V60_BLOCK_SHORT_AUDIT_ERROR", 0) + 1
                    continue
            except Exception:
                continue
            kept.append(row)

    rescue = _exchange_rebound_failure_rows_v60(target, frame, research_start, pivot_highs, pivot_lows)
    # Per-market episode de-duplication: keep the earliest surviving entry within 72h.
    combined = [(r, "BASELINE") for r in kept] + [(r, "REBOUND_RESCUE") for r in rescue]
    combined.sort(key=lambda x: pd.Timestamp(x[0][5]))
    final: list[tuple[Any, ...]] = []
    last_by_dir: dict[str, pd.Timestamp] = {}
    for row, source in combined:
        direction = str(row[3]).upper(); ts = pd.Timestamp(row[5])
        prev = last_by_dir.get(direction)
        if prev is not None and ts - prev < pd.Timedelta(hours=V60_REBOUND_REARM_HOURS):
            continue
        final.append(row); last_by_dir[direction] = ts
        if source == "REBOUND_RESCUE":
            funnel["V60_rebound_failure_rescue_kept"] = funnel.get("V60_rebound_failure_rescue_kept", 0) + 1
    yield from final


v56.candidate_rows_v56 = candidate_rows_v60


# Stock full-scan fallback: attach symbol metadata before inherited candidate simulation.
_ORIGINAL_MAKE_STOCK_CANDIDATE = v56._v56_make_stock_candidate


def _make_stock_candidate_v60(
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
    frame.attrs["_v60_target_meta"] = {
        "exchange": "AlpacaUnderlying", "symbol": symbol, "base": symbol,
    }
    row = _ORIGINAL_MAKE_STOCK_CANDIDATE(
        symbol, frame, daily, dpos, direction, breakout_line, setup_type,
        research_start, pivot_highs, pivot_lows,
    )
    if row is not None:
        ctx = _individual_trend_context(frame)
        row = _annotate_trend_row(row, ctx, frame)
        row = _attach_sim_audit(row)
        keep, reason = _v60_short_entry_gate(row)
        _v60_record_gate(row, keep, reason, "STOCK_FULL_SCAN_BASELINE")
        if not keep:
            return None
    return row


v56._v56_make_stock_candidate = _make_stock_candidate_v60


# ======================================================================================
# Exit-policy transformation: TP1 100% without rescanning signal generation
# ======================================================================================

def _tp1_all_row(source: dict[str, Any], *, cost_rate: float) -> dict[str, Any]:
    out = _attach_sim_audit(dict(source))
    audit = _SIM_AUDIT.get(_row_trade_key(out), {})
    tp1_hit = bool(audit.get("tp1_hit"))
    real_tp1 = bool(audit.get("real_tp1"))
    hit_time = str(audit.get("tp1_hit_time") or "")
    if not (real_tp1 and tp1_hit and hit_time):
        out["v60_exit_policy"] = "TP1_100PCT_CONTROL_SAME_PRE_TP1_PATH"
        return out

    entry = _safe_float(out.get("entry")); stop = _safe_float(out.get("stop")); tp1 = _safe_float(out.get("tp1"))
    if not all(math.isfinite(x) for x in (entry, stop, tp1)):
        return out
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        return out
    direction = str(out.get("direction") or "").upper()
    sign = 1.0 if direction == "LONG" else -1.0
    gross_r = sign * (tp1 - entry) / stop_distance
    cost_r = (entry + tp1) * cost_rate / stop_distance
    net_r = gross_r - cost_r
    out.update({
        "exit_time": hit_time,
        "exit_price": tp1,
        "outcome": "WIN" if net_r > 0 else "LOSS",
        "exit_reason": "TP1_100PCT",
        "timed_out": 0,
        "gross_r": gross_r,
        "cost_r": cost_r,
        "net_r": net_r,
        "tp1_exit_fraction": 1.0,
        "tp2_exit_fraction": 0.0,
        "tp3_exit_fraction": 0.0,
        "ma_exit_fraction": 0.0,
        "open_remainder_fraction": 0.0,
        "v60_exit_policy": "TP1_100PCT",
    })
    try:
        hours = max(0.0, (_to_utc(hit_time) - _to_utc(out.get("entry_time"))).total_seconds() / 3600.0)
        out["holding_bars"] = max(1, int(math.ceil(hours * 4.0)))
    except Exception:
        pass
    return out


def _transform_exchange_db_tp1_all(src: Path, dst: Path) -> dict[str, int]:
    if not src.exists():
        raise RuntimeError(f"找不到交易所候選DB：{src}")
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    con = sqlite3.connect(dst)
    stats = {"rows": 0, "tp1_transformed": 0, "audit_missing": 0}
    try:
        cur = con.execute(
            """
            SELECT rowid, exchange, symbol, direction, entry_time, entry, stop, tp1,
                   exit_time, exit_price, outcome, exit_reason, timed_out, gross_r, cost_r, net_r, holding_bars
            FROM candidates WHERE scheme = ?
            """,
            (SCHEME_V60,),
        )
        rows = cur.fetchall()
        cost_rate = engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
        for r in rows:
            stats["rows"] += 1
            d = {
                "exchange": r[1], "symbol": r[2], "direction": r[3], "entry_time": r[4],
                "entry": r[5], "stop": r[6], "tp1": r[7],
                "exit_time": r[8], "exit_price": r[9], "outcome": r[10], "exit_reason": r[11],
                "timed_out": r[12], "gross_r": r[13], "cost_r": r[14], "net_r": r[15], "holding_bars": r[16],
            }
            key = _row_trade_key(d)
            if key not in _SIM_AUDIT:
                stats["audit_missing"] += 1
                continue
            x = _tp1_all_row(d, cost_rate=cost_rate)
            if str(x.get("exit_reason")) != "TP1_100PCT":
                continue
            con.execute(
                """
                UPDATE candidates
                SET exit_time=?, exit_price=?, outcome=?, exit_reason=?, timed_out=?,
                    gross_r=?, cost_r=?, net_r=?, holding_bars=?
                WHERE rowid=?
                """,
                (
                    str(x.get("exit_time")), float(x.get("exit_price")), str(x.get("outcome")),
                    str(x.get("exit_reason")), int(x.get("timed_out") or 0), float(x.get("gross_r")),
                    float(x.get("cost_r")), float(x.get("net_r")), int(x.get("holding_bars") or r[16] or 0),
                    int(r[0]),
                ),
            )
            stats["tp1_transformed"] += 1
        con.commit()
    finally:
        con.close()
    return stats


@contextmanager
def _scheme_context():
    old_configs = engine.SCHEME_CONFIGS
    old_schemes = engine.SCHEMES
    old_primary = engine.PRIMARY_SCHEME
    try:
        engine.SCHEME_CONFIGS = {SCHEME_V60: dict(v56.V56_SCHEME_CONFIG)}
        engine.SCHEMES = (SCHEME_V60,)
        engine.PRIMARY_SCHEME = SCHEME_V60
        yield
    finally:
        engine.SCHEME_CONFIGS = old_configs
        engine.SCHEMES = old_schemes
        engine.PRIMARY_SCHEME = old_primary


def _select_exchange_v56_portfolio_from_db(db: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(db)
    try:
        with _scheme_context():
            rows = v56.select_portfolio_v56(con, SCHEME_V60, None)
    finally:
        con.close()
    return [_attach_sim_audit(dict(r)) for r in rows]


def _select_exchange_unlimited_from_db(db: Path) -> list[dict[str, Any]]:
    attrs = ["MAX_POSITIONS", "MAX_SAME_DIRECTION_POSITIONS", "MAX_SAME_DIRECTION_ENTRIES_6H"]
    old = {name: getattr(engine, name, None) for name in attrs}
    con = sqlite3.connect(db)
    try:
        for name in attrs:
            if hasattr(engine, name):
                setattr(engine, name, 100000)
        with _scheme_context():
            rows = v56.V56_ORIGINAL_SELECT_PORTFOLIO(con, SCHEME_V60, None)
    finally:
        con.close()
        for name, value in old.items():
            if value is not None:
                setattr(engine, name, value)
    return [_attach_sim_audit(dict(r)) for r in rows]


# ======================================================================================
# Performance / money-management reports
# ======================================================================================

def _max_losing_streak(rows: list[dict[str, Any]]) -> int:
    closed = [r for r in rows if str(r.get("outcome") or "") != "OPEN"]
    closed.sort(key=lambda r: _to_utc(r.get("exit_time")))
    best = cur = 0
    for r in closed:
        if _safe_float(r.get("net_r"), 0.0) < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _selected_metrics(rows: list[dict[str, Any]], *, starting_equity: float = STARTING_EQUITY) -> dict[str, Any]:
    rows = [dict(r) for r in rows]
    closed = [r for r in rows if str(r.get("outcome") or "") != "OPEN"]
    wins = [r for r in closed if _safe_float(r.get("net_r"), 0.0) > 0]
    losses = [r for r in closed if _safe_float(r.get("net_r"), 0.0) < 0]
    realized = sum(_safe_float(r.get("profit_usdt"), 0.0) for r in closed)
    unreal = sum(_safe_float(r.get("unrealized_profit_usdt"), 0.0) for r in rows if str(r.get("outcome")) == "OPEN")
    ending = starting_equity + realized

    # Realized-equity DD, consistent with inherited portfolio accounting.
    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0
    for r in sorted(closed, key=lambda x: _to_utc(x.get("exit_time"))):
        equity += _safe_float(r.get("profit_usdt"), 0.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)

    pos = sum(max(0.0, _safe_float(r.get("profit_usdt"), 0.0)) for r in closed)
    neg = abs(sum(min(0.0, _safe_float(r.get("profit_usdt"), 0.0)) for r in closed))
    pf = (pos / neg) if neg > 1e-12 else (float("inf") if pos > 0 else 0.0)

    holding_hours: list[float] = []
    for r in rows:
        try:
            holding_hours.append(max(0.0, (_to_utc(r.get("exit_time")) - _to_utc(r.get("entry_time"))).total_seconds() / 3600.0))
        except Exception:
            pass

    tp1_hits = sum(bool(r.get("tp1_hit")) for r in rows)
    tp2_hits = sum(bool(r.get("tp2_hit")) for r in rows)
    tp3_hits = sum(bool(r.get("tp3_hit")) for r in rows)
    ma_exits = sum(_safe_float(r.get("ma_exit_fraction"), 0.0) > 0 for r in rows)

    return {
        "trades": len(rows),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(rows) - len(closed),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 3) if closed else 0.0,
        "total_net_r": round(sum(_safe_float(r.get("net_r"), 0.0) for r in rows), 5),
        "starting_equity": starting_equity,
        "ending_equity_realized": round(ending, 4),
        "unrealized_profit_usdt": round(unreal, 4),
        "mark_to_market_equity": round(ending + unreal, 4),
        "net_profit_usdt": round(realized, 4),
        "growth_pct": round(realized / starting_equity * 100.0, 3) if starting_equity else 0.0,
        "max_drawdown_pct": round(max_dd, 3),
        "max_losing_streak": _max_losing_streak(rows),
        "profit_factor": None if math.isinf(pf) else round(pf, 4),
        "profit_factor_infinite": bool(math.isinf(pf)),
        "average_holding_hours": round(sum(holding_hours) / len(holding_hours), 3) if holding_hours else 0.0,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "tp3_hits": tp3_hits,
        "ma_exit_trades": ma_exits,
    }


def _fixed20_money_management(
    rows: list[dict[str, Any]],
    *,
    cost_rate: float,
    starting_equity: float = STARTING_EQUITY,
    leverage: float = FIXED_LEVERAGE,
    max_loss_u: float = FIXED_MAX_LOSS_U,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply fixed-U structural-stop sizing to an already selected strategy trade set.

    20x is treated only as initial-margin requirement. It does not multiply trade P&L.
    Exact exchange liquidation is intentionally not invented; wide stops are flagged as
    isolated-20x warnings using a conservative configurable threshold.
    """
    ordered = sorted((dict(r) for r in rows), key=lambda r: (_to_utc(r.get("entry_time")), str(r.get("symbol") or "")))
    active: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    equity = float(starting_equity)
    reserved_margin = 0.0
    equity_events: list[tuple[pd.Timestamp, float]] = []
    max_reserved = 0.0
    max_effective_lev = 0.0
    margin_rejected = 0
    undercapitalized = 0
    liq_warning_count = 0
    gap_over_budget = 0

    def realize_until(ts: pd.Timestamp) -> None:
        nonlocal equity, active, reserved_margin
        closing = [p for p in active if _to_utc(p.get("exit_time")) <= ts]
        for p in sorted(closing, key=lambda x: _to_utc(x.get("exit_time"))):
            if str(p.get("outcome")) != "OPEN":
                equity += _safe_float(p.get("fixed20_profit_usdt"), 0.0)
                equity_events.append((_to_utc(p.get("exit_time")), equity))
            reserved_margin = max(0.0, reserved_margin - _safe_float(p.get("fixed20_initial_margin"), 0.0))
        ids = {id(p) for p in closing}
        active = [p for p in active if id(p) not in ids]

    for entry_time, grp in groupby(ordered, key=lambda r: _to_utc(r.get("entry_time"))):
        realize_until(entry_time)
        for row in grp:
            if equity <= max_loss_u:
                undercapitalized += 1
                continue
            entry = _safe_float(row.get("entry")); stop = _safe_float(row.get("stop"))
            if not all(math.isfinite(x) and x > 0 for x in (entry, stop)):
                continue
            stop_distance = abs(entry - stop)
            if stop_distance <= 0:
                continue
            stop_fraction = stop_distance / entry
            # Linear-position approximation: entry notional + stop-fill notional costs.
            stop_notional_ratio = stop / entry
            allin_stop_loss_fraction = stop_fraction + cost_rate * (1.0 + stop_notional_ratio)
            if allin_stop_loss_fraction <= 0:
                continue
            notional = max_loss_u / allin_stop_loss_fraction
            risk_1r_usdt = notional * stop_fraction
            initial_margin = notional / leverage
            available = max(0.0, equity - reserved_margin)
            if initial_margin > available + 1e-9:
                margin_rejected += 1
                continue

            trade = dict(row)
            trade["fixed20_notional_usdt"] = notional
            trade["fixed20_initial_margin"] = initial_margin
            trade["fixed20_risk_1r_usdt"] = risk_1r_usdt
            trade["fixed20_structural_stop_budget_usdt"] = max_loss_u
            trade["fixed20_stop_pct"] = stop_fraction * 100.0
            trade["fixed20_isolated_liquidation_warning"] = bool(stop_fraction * 100.0 >= ISOLATED_20X_WARNING_STOP_PCT)
            trade["fixed20_profit_usdt"] = (
                _safe_float(row.get("net_r"), 0.0) * risk_1r_usdt
                if str(row.get("outcome")) != "OPEN" else 0.0
            )
            trade["fixed20_unrealized_profit_usdt"] = (
                _safe_float(row.get("net_r"), 0.0) * risk_1r_usdt
                if str(row.get("outcome")) == "OPEN" else 0.0
            )
            if trade["fixed20_isolated_liquidation_warning"]:
                liq_warning_count += 1
            if str(row.get("exit_reason")) == "STOP" and trade["fixed20_profit_usdt"] < -(max_loss_u + 0.25):
                # Gap/slippage can make a real stop fill exceed the exact-stop budget.
                gap_over_budget += 1

            executed.append(trade)
            active.append(trade)
            reserved_margin += initial_margin
            max_reserved = max(max_reserved, reserved_margin)
            active_notional = sum(_safe_float(p.get("fixed20_notional_usdt"), 0.0) for p in active)
            if equity > 0:
                max_effective_lev = max(max_effective_lev, active_notional / equity)

    realize_until(pd.Timestamp.max.tz_localize("UTC"))
    closed = [r for r in executed if str(r.get("outcome")) != "OPEN"]
    wins = [r for r in closed if _safe_float(r.get("fixed20_profit_usdt"), 0.0) > 0]
    losses = [r for r in closed if _safe_float(r.get("fixed20_profit_usdt"), 0.0) < 0]
    unreal = sum(_safe_float(r.get("fixed20_unrealized_profit_usdt"), 0.0) for r in executed)

    peak = starting_equity
    max_dd = 0.0
    for _, value in sorted(equity_events, key=lambda x: x[0]):
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100.0)

    pos = sum(max(0.0, _safe_float(r.get("fixed20_profit_usdt"), 0.0)) for r in closed)
    neg = abs(sum(min(0.0, _safe_float(r.get("fixed20_profit_usdt"), 0.0)) for r in closed))
    pf = pos / neg if neg > 1e-12 else (float("inf") if pos > 0 else 0.0)
    realized = equity - starting_equity
    summary = {
        "mode": "FIXED20_MAXLOSS10U",
        "starting_equity": starting_equity,
        "leverage_setting": leverage,
        "max_structural_stop_loss_budget_usdt": max_loss_u,
        "trades": len(executed),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(executed) - len(closed),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 3) if closed else 0.0,
        "ending_equity_realized": round(equity, 4),
        "net_profit_usdt": round(realized, 4),
        "growth_pct": round(realized / starting_equity * 100.0, 3),
        "unrealized_profit_usdt": round(unreal, 4),
        "mark_to_market_equity": round(equity + unreal, 4),
        "max_drawdown_pct": round(max_dd, 3),
        "max_losing_streak": _max_losing_streak(executed),
        "profit_factor": None if math.isinf(pf) else round(pf, 4),
        "profit_factor_infinite": bool(math.isinf(pf)),
        "average_initial_margin_usdt": round(sum(_safe_float(r.get("fixed20_initial_margin"), 0.0) for r in executed) / len(executed), 4) if executed else 0.0,
        "max_reserved_margin_usdt": round(max_reserved, 4),
        "max_account_effective_leverage": round(max_effective_lev, 4),
        "margin_rejected": margin_rejected,
        "undercapitalized_rejected": undercapitalized,
        "isolated_20x_stop_warning_count": liq_warning_count,
        "isolated_warning_stop_pct_threshold": ISOLATED_20X_WARNING_STOP_PCT,
        "stop_gap_loss_over_10p25u_count": gap_over_budget,
        "liquidation_note": (
            "Exact liquidation is venue/tier/margin-mode specific and is not fabricated. "
            "The isolated-20x warning is conservative research only; main P&L treats 20x as margin setting."
        ),
    }
    return executed, summary


def _bucket_metrics(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets = sorted({str(r.get(field) or "UNKNOWN") for r in rows})
    out: list[dict[str, Any]] = []
    for bucket in buckets:
        part = [r for r in rows if str(r.get(field) or "UNKNOWN") == bucket]
        closed = [r for r in part if str(r.get("outcome") or "") != "OPEN"]
        wins = [r for r in closed if _safe_float(r.get("net_r"), 0.0) > 0]
        out.append({
            field: bucket,
            "trades": len(part),
            "closed": len(closed),
            "wins": len(wins),
            "win_rate_pct": round(100.0 * len(wins) / len(closed), 3) if closed else 0.0,
            "total_net_r": round(sum(_safe_float(r.get("net_r"), 0.0) for r in part), 5),
        })
    return out


# ======================================================================================
# Exchange post-study
# ======================================================================================

def _enrich_exchange_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {
        (str(r.get("exchange")), str(r.get("symbol")), str(r.get("direction")), str(r.get("entry_time"))): r
        for r in _EXCHANGE_FILTER_AUDIT
    }
    loose = {
        (str(r.get("symbol")), str(r.get("direction")), str(r.get("entry_time"))): r
        for r in _EXCHANGE_FILTER_AUDIT
    }
    out: list[dict[str, Any]] = []
    for src in rows:
        row = _attach_sim_audit(dict(src))
        a = lookup.get((str(row.get("exchange")), str(row.get("symbol")), str(row.get("direction")), str(row.get("entry_time"))))
        if a is None:
            a = loose.get((str(row.get("symbol")), str(row.get("direction")), str(row.get("entry_time"))))
        if a:
            for field in (
                "setup_type", "trend_1d", "trend_4h", "trend_1d_bull_score", "trend_1d_bear_score",
                "trend_4h_bull_score", "trend_4h_bear_score", "chase_distance_4h_atr", "atr_bucket",
                "trend_bucket", "v58_observational_gate",
            ):
                row[field] = a.get(field)
        out.append(row)
    return out


def _run_exchange_poststudy(exchange_result: dict[str, Any]) -> dict[str, Any]:
    db_b = MARKETS_OUTPUT_DIR / "candidates.sqlite3"
    db_a = MARKETS_OUTPUT_DIR / "candidates_tp1_all_v60.sqlite3"
    transform_stats = _transform_exchange_db_tp1_all(db_b, db_a)

    b_path = MARKETS_OUTPUT_DIR / f"trades_{SCHEME_V60.lower()}.csv"
    b_rows = _enrich_exchange_rows(_read_csv(b_path))
    a_rows = _enrich_exchange_rows(_select_exchange_v56_portfolio_from_db(db_a))
    b_unlimited = _enrich_exchange_rows(_select_exchange_unlimited_from_db(db_b))
    a_unlimited = _enrich_exchange_rows(_select_exchange_unlimited_from_db(db_a))

    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_b_tp1half_tp23_ma_v60.csv", b_rows)
    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_a_tp1_all_v60.csv", a_rows)
    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_b_unlimited_v60.csv", b_unlimited)
    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_a_unlimited_v60.csv", a_unlimited)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_v60_trend_chase_audit.csv", _EXCHANGE_FILTER_AUDIT)
    _write_union(RESEARCH_OUTPUT_DIR / "v60_entry_gate_audit.csv", _V60_ENTRY_GATE_AUDIT)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_rebound_failure_rescue_v60.csv", _V60_EXCHANGE_REBOUND_AUDIT)

    original_a = _selected_metrics(a_rows)
    original_b = _selected_metrics(b_rows)
    fixed_a_rows, fixed_a = _fixed20_money_management(
        a_rows, cost_rate=engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    )
    fixed_b_rows, fixed_b = _fixed20_money_management(
        b_rows, cost_rate=engine.FEE_PER_SIDE + engine.SLIPPAGE_PER_SIDE
    )
    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_a_tp1_all_fixed20_10u_v60.csv", fixed_a_rows)
    _write_union(MARKETS_OUTPUT_DIR / "trades_exit_b_tp23_ma_fixed20_10u_v60.csv", fixed_b_rows)

    formal_a_atr = _bucket_metrics(a_rows, "atr_bucket")
    formal_b_atr = _bucket_metrics(b_rows, "atr_bucket")
    formal_a_trend = _bucket_metrics(a_rows, "trend_bucket")
    formal_b_trend = _bucket_metrics(b_rows, "trend_bucket")
    raw_gate = _bucket_metrics(_EXCHANGE_FILTER_AUDIT, "v58_observational_gate")
    raw_atr = _bucket_metrics(_EXCHANGE_FILTER_AUDIT, "atr_bucket")
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_raw_atr_buckets_v60.csv", raw_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_raw_v58_gate_outcomes_v60.csv", raw_gate)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_formal_exit_a_atr_buckets_v60.csv", formal_a_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_formal_exit_b_atr_buckets_v60.csv", formal_b_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_formal_exit_a_trend_buckets_v60.csv", formal_a_trend)
    _write_union(RESEARCH_OUTPUT_DIR / "exchange_formal_exit_b_trend_buckets_v60.csv", formal_b_trend)

    first_pullback_b = [r for r in b_rows if str(r.get("setup_type")) == "FIRST_PULLBACK_RECLAIM"]
    first_pullback_a = [r for r in a_rows if str(r.get("setup_type")) == "FIRST_PULLBACK_RECLAIM"]

    summary = {
        "entry_core": "V56 structural core + V60 anti-low-chase SHORT gate + 4H rebound-failure rescue; V58 blanket >3.25ATR veto disabled",
        "v60_anti_low_chase": {
            "deep_short_atr": V60_DEEP_SHORT_ATR,
            "min_pullback_retrace_atr": V60_MIN_PULLBACK_RETRACE_ATR,
            "exchange_rebound_failure_rescue_candidates": len(_V60_EXCHANGE_REBOUND_AUDIT),
        },
        "exit_a_tp1_100pct": {
            "original_1pct_compound": original_a,
            "fixed20_maxloss10u": fixed_a,
            "unlimited_capacity_observational": _selected_metrics(a_unlimited),
        },
        "exit_b_tp1half_tp2tp3_ma": {
            "original_1pct_compound": original_b,
            "fixed20_maxloss10u": fixed_b,
            "unlimited_capacity_observational": _selected_metrics(b_unlimited),
        },
        "first_pullback": {
            "exit_a": _selected_metrics(first_pullback_a),
            "exit_b": _selected_metrics(first_pullback_b),
        },
        "tp1_all_db_transform": transform_stats,
        "raw_v58_gate_observational": raw_gate,
        "raw_atr_buckets": raw_atr,
        "formal_exit_a_atr_buckets": formal_a_atr,
        "formal_exit_b_atr_buckets": formal_b_atr,
        "formal_exit_a_trend_buckets": formal_a_trend,
        "formal_exit_b_trend_buckets": formal_b_trend,
        "v56_engine_result": exchange_result,
    }
    _atomic_json(RESEARCH_OUTPUT_DIR / "exchange_summary_v60.json", summary)
    return summary


# ======================================================================================
# Fast stock path: reuse V58 entry checkpoints, only re-simulate V60 exits
# ======================================================================================

def _v58_checkpoint_dir() -> Path:
    return ROOT / "backtest_1y_v58_results" / "resume_stock_cache_v58"


def _discover_v58_checkpoint_asof(paths: list[Path]) -> pd.Timestamp | None:
    counts: Counter[str] = Counter()
    for path in paths[: min(50, len(paths))]:
        data = _load_json(path, {})
        if isinstance(data, dict) and data.get("complete") is True and data.get("as_of_utc"):
            counts[str(data["as_of_utc"])] += 1
    if not counts:
        return None
    return _to_utc(counts.most_common(1)[0][0])


def _find_entry_i(frame: pd.DataFrame, entry_time: Any) -> int | None:
    t = _to_utc(entry_time)
    idx = frame.index
    tc = t.tz_localize(None) if idx.tz is None else t.tz_convert(idx.tz)
    pos = int(idx.searchsorted(tc, side="left"))
    if 0 <= pos < len(idx) and abs((pd.Timestamp(idx[pos]) - tc).total_seconds()) <= 60:
        return pos
    # Conservative fallback: first available bar at/after the recorded entry timestamp.
    return pos if 0 <= pos < len(idx) else None


def _resim_existing_stock_row(frame: pd.DataFrame, source: dict[str, Any], *, scheme: str) -> dict[str, Any] | None:
    row = dict(source)
    entry_i = _find_entry_i(frame, row.get("entry_time"))
    if entry_i is None:
        return None
    entry = _safe_float(row.get("entry")); stop = _safe_float(row.get("stop"))
    tp1 = _safe_float(row.get("tp1")); tp2 = _safe_float(row.get("tp2")); tp3 = _safe_float(row.get("tp3"))
    if not all(math.isfinite(x) for x in (entry, stop, tp1)):
        return None
    frame.attrs["_v60_target_meta"] = {
        "exchange": "AlpacaUnderlying",
        "symbol": str(row.get("symbol") or row.get("underlying") or ""),
        "base": str(row.get("symbol") or row.get("underlying") or ""),
    }
    stop_distance = abs(entry - stop)
    result = simulate_tp1_tp23_ma_v60(
        frame, entry_i, str(row.get("direction") or ""), entry, stop, tp1, stop_distance,
        tp2=tp2, tp3=tp3,
    )
    exit_i = int(result["exit_i"])
    highs = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype="float64", copy=False)
    lows = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype="float64", copy=False)
    direction = str(row.get("direction") or "").upper()
    if direction == "LONG":
        mfe_r = (np.nanmax(highs[entry_i:exit_i + 1]) - entry) / stop_distance
        mae_r = (entry - np.nanmin(lows[entry_i:exit_i + 1])) / stop_distance
    else:
        mfe_r = (entry - np.nanmin(lows[entry_i:exit_i + 1])) / stop_distance
        mae_r = (np.nanmax(highs[entry_i:exit_i + 1]) - entry) / stop_distance
    row.update({
        "scheme": scheme,
        "exit_time": frame.index[exit_i].isoformat(),
        "exit_price": float(result["exit_price"]),
        "outcome": result["outcome"],
        "exit_reason": result["exit_reason"],
        "timed_out": result.get("timed_out", 0),
        "gross_r": float(result["gross_r"]),
        "cost_r": float(result["cost_r"]),
        "net_r": float(result["net_r"]),
        "mfe_r": float(mfe_r),
        "mae_r": float(mae_r),
        "v60_exit_policy": "TP1_50_TP2_25_TP3_REST_PLUS_MA",
    })
    ctx = _individual_trend_context(frame)
    row = _annotate_trend_row(row, ctx, frame)
    row = _attach_sim_audit(row)
    keep, reason = _v60_short_entry_gate(row)
    row["v60_entry_gate_keep"] = bool(keep)
    row["v60_entry_gate_reason"] = reason
    _v60_record_gate(row, keep, reason, "STOCK_FAST_BASELINE")
    return row


def _load_v57_for_early_research():
    if not ENABLE_EARLY_RESEARCH:
        return None
    path = ROOT / "backtest_1y_v57.py"
    if not path.exists():
        path = Path(__file__).with_name("backtest_1y_v57.py")
    if not path.exists():
        print("[V60-US] 找不到 V57，跳過4H提早進場研究；正式V56基線不受影響。")
        return None
    spec57 = importlib.util.spec_from_file_location("cryptoradar_v57_early_for_v60", path)
    if spec57 is None or spec57.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec57)
    sys.modules[spec57.name] = mod
    spec57.loader.exec_module(mod)
    # Old 3.25 ATR hard cutoff is explicitly disabled for V60 early research.
    mod.V57_CHASE_LIMIT_ATR = 1e9
    mod.V57_EARLY_QUALITY_THRESHOLD = EARLY_QUALITY_THRESHOLD

    def bridge_stock_sim(frame, entry_i, direction, entry, stop, targets, target_mode):
        return _sim_stock_trade_v60(frame, entry_i, direction, entry, stop, targets, target_mode)

    mod.v56._v56_sim_stock_trade = bridge_stock_sim
    original_make = mod._make_early_stock_candidate

    def make_with_meta(symbol, frame, four, fpos, direction, setup_type, research_start, pivot_highs, pivot_lows):
        frame.attrs["_v60_target_meta"] = {
            "exchange": "AlpacaUnderlying", "symbol": symbol, "base": symbol,
        }
        row = original_make(symbol, frame, four, fpos, direction, setup_type, research_start, pivot_highs, pivot_lows)
        if row is not None:
            row["scheme"] = STOCK_EARLY_SCHEME_V60
            # Generation temporarily uses a huge cutoff so >3.25ATR rows are not deleted.
            # Re-label and re-score with the original 3.25ATR observational scale afterward.
            chase = _safe_float(row.get("chase_distance_4h_atr"))
            row["late_entry_flag"] = "CHASE" if math.isfinite(chase) and chase > 3.25 else "OK"
            row["timing_quality_score"] = _timing_quality_score_fixed(
                chase, _safe_float(row.get("stop_pct")), _safe_float(row.get("rr1")), str(row.get("setup_type") or "")
            )
            ctx = _individual_trend_context(frame)
            row = _annotate_trend_row(row, ctx, frame)
            row = _attach_sim_audit(row)
        return row

    mod._make_early_stock_candidate = make_with_meta
    return mod


def _run_stock_fast_from_v58() -> dict[str, Any] | None:
    checkpoint_dir = _v58_checkpoint_dir()
    paths = sorted(checkpoint_dir.glob("*.json")) if checkpoint_dir.exists() else []
    if not (REUSE_V58_STOCK_CHECKPOINTS and paths):
        return None
    as_of = _discover_v58_checkpoint_asof(paths)
    if as_of is None:
        return None

    print("\n" + "=" * 96)
    print(f"[V60-US] 快速模式：沿用 V58 的 V56-baseline 進場checkpoint，共 {len(paths)} 檔")
    print(f"[V60-US] 固定研究截止：{as_of.isoformat()}；只重算 V60 出場，不重做 baseline 訊號掃描")

    research_start = as_of - pd.Timedelta(days=v56.V56_STOCK_RESEARCH_DAYS)
    fetch_start = research_start - pd.Timedelta(days=v56.V56_STOCK_WARMUP_DAYS)
    v57early = _load_v57_for_early_research()

    baseline_all: list[dict[str, Any]] = []
    early_all: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    old_fee = engine.FEE_PER_SIDE
    old_slip = engine.SLIPPAGE_PER_SIDE
    engine.FEE_PER_SIDE = v22.v19.STOCK_FEE_PER_SIDE
    engine.SLIPPAGE_PER_SIDE = v22.v19.STOCK_SLIPPAGE_PER_SIDE
    try:
        for idx, path in enumerate(paths, start=1):
            source = _load_json(path, {})
            symbol = str(source.get("symbol") or path.stem).upper()
            source_baseline = list(source.get("baseline_rows") or [])
            cache_path = STOCK_RESIM_CACHE_DIR / f"{symbol}.json"
            cached = _load_json(cache_path, {})
            if (
                isinstance(cached, dict)
                and cached.get("complete") is True
                and cached.get("policy_signature") == POLICY_SIGNATURE
                and cached.get("source_as_of_utc") == as_of.isoformat()
            ):
                baseline = list(cached.get("baseline_rows") or [])
                early = list(cached.get("early_rows") or [])
                baseline_all.extend(baseline)
                early_all.extend(early)
                q = cached.get("quality")
                if isinstance(q, dict):
                    quality.append(q)
                e = cached.get("error")
                if isinstance(e, dict):
                    errors.append(e)
                print(f"[V60-US {idx}/{len(paths)}] {symbol} 已有V60 checkpoint，跳過")
                continue

            try:
                # Early research can exist even where baseline has no entries, so load every symbol
                # when early research is enabled. Shared Alpaca cache means this should be disk-first.
                need_raw = bool(source_baseline) or v57early is not None
                raw = v22.v19.fetch_alpaca_15m(symbol, fetch_start, as_of) if need_raw else pd.DataFrame()
                baseline: list[dict[str, Any]] = []
                if source_baseline:
                    for src in source_baseline:
                        row = _resim_existing_stock_row(raw, src, scheme=STOCK_SCHEME_V60)
                        if row is not None:
                            baseline.append(row)

                early: list[dict[str, Any]] = []
                if v57early is not None and not raw.empty:
                    e_rows, _ = v57early._early_stock_candidates_for_symbol(symbol, raw, research_start)
                    for er in e_rows:
                        er = dict(er)
                        er["scheme"] = STOCK_EARLY_SCHEME_V60
                        er["v60_early_old_3p25_gate_disabled"] = True
                        early.append(er)

                qrow = {
                    "symbol": symbol,
                    "source_baseline_candidates": len(source_baseline),
                    "v60_baseline_resim_candidates": len(baseline),
                    "v60_early_candidates": len(early),
                    "bars": len(raw),
                    "status": "OK",
                }
                payload = {
                    "complete": True,
                    "policy_signature": POLICY_SIGNATURE,
                    "source_as_of_utc": as_of.isoformat(),
                    "symbol": symbol,
                    "baseline_rows": baseline,
                    "early_rows": early,
                    "quality": qrow,
                    "error": None,
                }
                _atomic_json(cache_path, payload)
                baseline_all.extend(baseline)
                early_all.extend(early)
                quality.append(qrow)
                print(
                    f"[V60-US {idx}/{len(paths)}] {symbol} baseline={len(baseline)} "
                    f"early={len(early)}"
                )
            except Exception as exc:
                erow = {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"}
                errors.append(erow)
                qrow = {"symbol": symbol, "status": "ERROR", "error": erow["error"]}
                quality.append(qrow)
                _atomic_json(cache_path, {
                    "complete": True,
                    "policy_signature": POLICY_SIGNATURE,
                    "source_as_of_utc": as_of.isoformat(),
                    "symbol": symbol,
                    "baseline_rows": [],
                    "early_rows": [],
                    "quality": qrow,
                    "error": erow,
                })
                print(f"[V60-US {idx}/{len(paths)}] {symbol} ERROR {erow['error']}")
    finally:
        engine.FEE_PER_SIDE = old_fee
        engine.SLIPPAGE_PER_SIDE = old_slip

    # Rebuild the no-look-ahead broad-market labels once, then attach to all rows.
    try:
        index_regime, index_quality = v56._v56_build_three_index_regime(fetch_start, as_of)
    except Exception as exc:
        print(f"[V60-US] 三大指數研究建立失敗：{type(exc).__name__}: {exc}")
        index_regime, index_quality = pd.DataFrame(), []
    for row in baseline_all + early_all:
        row["asset_group"] = v56.v56_asset_group(str(row.get("symbol") or ""), force_stock=True)
        if not index_regime.empty:
            v56._v56_attach_index_regime(row, index_regime)

    return {
        "as_of": as_of,
        "research_start": research_start,
        "fetch_start": fetch_start,
        "baseline": baseline_all,
        "early": early_all,
        "quality": quality,
        "errors": errors,
        "index_quality": index_quality,
        "source": "V58_RESUME_STOCK_CHECKPOINT_BASELINE_ENTRIES_RESIMMED_WITH_V60_EXITS",
    }


def _merge_prefer_earliest_episode(baseline: list[dict[str, Any]], early: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined = [dict(r, timing_source="BASELINE") for r in baseline] + [dict(r, timing_source="EARLY_4H") for r in early]
    combined.sort(key=lambda r: (_to_utc(r.get("entry_time")), str(r.get("symbol") or ""), str(r.get("direction") or "")))
    kept: list[dict[str, Any]] = []
    last: dict[tuple[str, str], pd.Timestamp] = {}
    for row in combined:
        key = (str(row.get("symbol") or "").upper(), str(row.get("direction") or "").upper())
        ts = _to_utc(row.get("entry_time"))
        prev = last.get(key)
        if prev is not None and ts - prev < pd.Timedelta(hours=EARLY_EPISODE_DEDUPE_HOURS):
            continue
        kept.append(row)
        last[key] = ts
    return kept


def _apply_stock_portfolio(candidates: list[dict[str, Any]], *, unlimited: bool = False):
    old = v22.v19.STOCK_MAX_POSITIONS
    try:
        if unlimited:
            v22.v19.STOCK_MAX_POSITIONS = 100000
        return v56.apply_stock_portfolio_v56(candidates)
    finally:
        v22.v19.STOCK_MAX_POSITIONS = old


def _v60_stock_formal_candidates(baseline: list[dict[str, Any]], early: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """R3 formal stock book: trend-aligned LONG + first structural pullback only.

    Stock SHORT and non-first-pullback rows remain available in research CSVs, but they
    cannot enter the formal portfolio. A valid long requires completed 1D BULL context
    and a non-BEAR 4H context; UNKNOWN context fails closed.
    """
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for src in baseline:
        row = dict(src)
        direction = str(row.get("direction") or "").upper()
        setup = str(row.get("setup_type") or "").upper()
        trend_1d = str(row.get("trend_1d") or "UNKNOWN").upper()
        trend_4h = str(row.get("trend_4h") or "UNKNOWN").upper()
        keep = (
            direction == "LONG"
            and setup == "FIRST_PULLBACK_RECLAIM"
            and trend_1d == "BULL"
            and trend_4h in {"BULL", "NEUTRAL"}
        )
        if direction != "LONG":
            reason = "BLOCK_STOCK_SHORT_FORMAL"
        elif setup != "FIRST_PULLBACK_RECLAIM":
            reason = "BLOCK_STOCK_NOT_FIRST_PULLBACK"
        elif trend_1d != "BULL":
            reason = "BLOCK_STOCK_D1_NOT_BULL"
        elif trend_4h not in {"BULL", "NEUTRAL"}:
            reason = "BLOCK_STOCK_4H_BEAR_OR_UNKNOWN"
        else:
            reason = "KEEP_STOCK_BULL_FIRST_PULLBACK_LONG"
        row["v60_entry_gate_keep"] = keep; row["v60_entry_gate_reason"] = reason
        if keep:
            kept.append(row)
        else:
            blocked.append(row)
    return kept, blocked


def _run_stock_poststudy_fast(stock_data: dict[str, Any]) -> dict[str, Any]:
    baseline_control_b = [dict(r) for r in stock_data["baseline"]]
    early_b = [dict(r) for r in stock_data["early"]]
    baseline_b, blocked_b = _v60_stock_formal_candidates(baseline_control_b, early_b)
    cost_rate = v22.v19.STOCK_FEE_PER_SIDE + v22.v19.STOCK_SLIPPAGE_PER_SIDE
    baseline_a = [_tp1_all_row(r, cost_rate=cost_rate) for r in baseline_b]
    early_a = [_tp1_all_row(r, cost_rate=cost_rate) for r in early_b]

    selected_b, port_b = _apply_stock_portfolio(baseline_b)
    selected_a, port_a = _apply_stock_portfolio(baseline_a)
    selected_b = [_attach_sim_audit(r) for r in selected_b]
    selected_a = [_attach_sim_audit(r) for r in selected_a]

    unlimited_b, unlimited_port_b = _apply_stock_portfolio(baseline_b, unlimited=True)
    unlimited_a, unlimited_port_a = _apply_stock_portfolio(baseline_a, unlimited=True)

    first_b_candidates = [r for r in baseline_b if str(r.get("setup_type")) == "FIRST_PULLBACK_RECLAIM"]
    first_a_candidates = [r for r in baseline_a if str(r.get("setup_type")) == "FIRST_PULLBACK_RECLAIM"]
    first_b, first_b_port = _apply_stock_portfolio(first_b_candidates)
    first_a, first_a_port = _apply_stock_portfolio(first_a_candidates)

    merged_b = _merge_prefer_earliest_episode(baseline_b, early_b)
    merged_a = _merge_prefer_earliest_episode(baseline_a, early_a)
    early_aug_b, early_aug_b_port = _apply_stock_portfolio(merged_b)
    early_aug_a, early_aug_a_port = _apply_stock_portfolio(merged_a)
    early_score70_b = [r for r in early_b if _safe_float(r.get("timing_quality_score"), 0.0) >= EARLY_QUALITY_THRESHOLD]
    early_score70_a = [r for r in early_a if _safe_float(r.get("timing_quality_score"), 0.0) >= EARLY_QUALITY_THRESHOLD]
    early_only_b, early_only_b_port = _apply_stock_portfolio(early_score70_b)
    early_only_a, early_only_a_port = _apply_stock_portfolio(early_score70_a)

    # Three-index research remains observational/parallel; it is not a formal V60 gate.
    strong_b, strong_b_port = _apply_stock_portfolio([r for r in baseline_b if v56._v56_keep_strong_index_veto(r)])
    strong_a, strong_a_port = _apply_stock_portfolio([r for r in baseline_a if v56._v56_keep_strong_index_veto(r)])

    original_b = _selected_metrics(selected_b)
    original_a = _selected_metrics(selected_a)
    fixed_b_rows, fixed_b = _fixed20_money_management(selected_b, cost_rate=cost_rate)
    fixed_a_rows, fixed_a = _fixed20_money_management(selected_a, cost_rate=cost_rate)

    _write_union(ALPACA_D1_OUTPUT_DIR / "candidates_baseline_exit_b_v60.csv", baseline_b)
    _write_union(ALPACA_D1_OUTPUT_DIR / "candidates_baseline_exit_a_v60.csv", baseline_a)
    _write_union(ALPACA_D1_OUTPUT_DIR / "candidates_early_4h_exit_b_v60.csv", early_b)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_b_tp23_ma_v60.csv", selected_b)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_a_tp1_all_v60.csv", selected_a)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_b_fixed20_10u_v60.csv", fixed_b_rows)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_a_fixed20_10u_v60.csv", fixed_a_rows)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_first_pullback_exit_b_v60.csv", first_b)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_first_pullback_exit_a_v60.csv", first_a)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_early_augmented_exit_b_v60.csv", early_aug_b)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_early_augmented_exit_a_v60.csv", early_aug_a)
    _write_union(ALPACA_D1_OUTPUT_DIR / "data_quality_v60.csv", stock_data.get("quality", []))
    _write_union(ALPACA_D1_OUTPUT_DIR / "errors_v60.csv", stock_data.get("errors", []))

    _write_union(RESEARCH_OUTPUT_DIR / "v60_entry_gate_audit.csv", _V60_ENTRY_GATE_AUDIT)
    _write_union(RESEARCH_OUTPUT_DIR / "stocks_blocked_deep_short_v60.csv", blocked_b)
    control_selected_b, control_port_b = _apply_stock_portfolio(baseline_control_b)
    control_selected_a, control_port_a = _apply_stock_portfolio([_tp1_all_row(r, cost_rate=cost_rate) for r in baseline_control_b])

    baseline_atr = _bucket_metrics(baseline_b, "atr_bucket")
    selected_b_atr = _bucket_metrics(selected_b, "atr_bucket")
    selected_a_atr = _bucket_metrics(selected_a, "atr_bucket")
    baseline_trend = _bucket_metrics(baseline_b, "trend_bucket")
    _write_union(RESEARCH_OUTPUT_DIR / "stocks_baseline_atr_buckets_v60.csv", baseline_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "stocks_formal_exit_b_atr_buckets_v60.csv", selected_b_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "stocks_formal_exit_a_atr_buckets_v60.csv", selected_a_atr)
    _write_union(RESEARCH_OUTPUT_DIR / "stocks_baseline_trend_buckets_v60.csv", baseline_trend)

    summary = {
        "source": stock_data.get("source"),
        "as_of_utc": _to_utc(stock_data.get("as_of")).isoformat(),
        "baseline_candidates_before_v60_gate": len(baseline_control_b),
        "formal_candidates_after_v60_gate_and_rescue": len(baseline_b),
        "blocked_deep_short_candidates": len(blocked_b),
        "formal_rebound_failure_rescues": sum(bool(r.get("v60_formal_rescue")) for r in baseline_b),
        "early_candidates_unfiltered_3p25": len(early_b),
        "v59_control_same_entries": {
            "exit_a": _selected_metrics(control_selected_a),
            "exit_b": _selected_metrics(control_selected_b),
            "portfolio_exit_a": control_port_a,
            "portfolio_exit_b": control_port_b,
        },
        "errors_count": len(stock_data.get("errors", [])),
        "exit_a_tp1_100pct": {
            "original_1pct_compound": original_a,
            "fixed20_maxloss10u": fixed_a,
            "portfolio_native": port_a,
            "unlimited_capacity": unlimited_port_a,
        },
        "exit_b_tp1half_tp2tp3_ma": {
            "original_1pct_compound": original_b,
            "fixed20_maxloss10u": fixed_b,
            "portfolio_native": port_b,
            "unlimited_capacity": unlimited_port_b,
        },
        "first_pullback_only": {
            "exit_a": _selected_metrics(first_a),
            "exit_b": _selected_metrics(first_b),
            "portfolio_exit_a": first_a_port,
            "portfolio_exit_b": first_b_port,
        },
        "early_timing_research": {
            "old_3p25_hard_cutoff_disabled": True,
            "quality_threshold_observational": EARLY_QUALITY_THRESHOLD,
            "early_score70_exit_a": _selected_metrics(early_only_a),
            "early_score70_exit_b": _selected_metrics(early_only_b),
            "early_score70_portfolio_exit_a": early_only_a_port,
            "early_score70_portfolio_exit_b": early_only_b_port,
            "baseline_plus_early_prefer_first_72h_exit_a": _selected_metrics(early_aug_a),
            "baseline_plus_early_prefer_first_72h_exit_b": _selected_metrics(early_aug_b),
            "baseline_plus_early_portfolio_exit_a": early_aug_a_port,
            "baseline_plus_early_portfolio_exit_b": early_aug_b_port,
        },
        "three_index_strong_3of3_observational": {
            "exit_a": _selected_metrics(strong_a),
            "exit_b": _selected_metrics(strong_b),
            "portfolio_exit_a": strong_a_port,
            "portfolio_exit_b": strong_b_port,
        },
        "atr_buckets": {
            "baseline": baseline_atr,
            "formal_exit_a": selected_a_atr,
            "formal_exit_b": selected_b_atr,
        },
        "trend_buckets_baseline": baseline_trend,
        "proxy_caveat": (
            "Alpaca underlying history is research proxy for current stock-token underlyings; "
            "historical token listing/liquidity is not reconstructed."
        ),
    }
    _atomic_json(RESEARCH_OUTPUT_DIR / "stocks_summary_v60.json", summary)
    return summary


# ======================================================================================
# Slow stock fallback if V58 checkpoints are unavailable
# ======================================================================================

def _run_stock_fallback_full_scan() -> dict[str, Any]:
    print("\n[V60-US] 找不到可重用的 V58 stock checkpoint，改跑 V56 完整原生美股掃描。")
    print("[V60-US] 這條 fallback 仍會用共享 Alpaca 15m cache；但速度會比快速重算慢。")
    stock_result = v56.run_alpaca_underlying_d1_v56()
    # In fallback, inherited writer already created baseline selected trades with V60 exit-B simulator.
    candidates = _read_csv(ALPACA_D1_OUTPUT_DIR / "candidates_all.csv")
    selected_b = _read_csv(ALPACA_D1_OUTPUT_DIR / "trades_formal_combination.csv")
    if not selected_b:
        selected_b = _read_csv(ALPACA_D1_OUTPUT_DIR / "trades_all.csv")
    cost_rate = v22.v19.STOCK_FEE_PER_SIDE + v22.v19.STOCK_SLIPPAGE_PER_SIDE
    candidates = [_attach_sim_audit(r) for r in candidates]
    candidates, blocked_stock_formal = _v60_stock_formal_candidates(candidates, [])
    _write_union(ALPACA_D1_OUTPUT_DIR / "candidates_blocked_by_r3_stock_formal.csv", blocked_stock_formal)
    candidates_a = [_tp1_all_row(r, cost_rate=cost_rate) for r in candidates]
    selected_a, port_a = _apply_stock_portfolio(candidates_a)
    selected_b, port_b = _apply_stock_portfolio(candidates)
    selected_b = [_attach_sim_audit(r) for r in selected_b]
    fixed_a_rows, fixed_a = _fixed20_money_management(selected_a, cost_rate=cost_rate)
    fixed_b_rows, fixed_b = _fixed20_money_management(selected_b, cost_rate=cost_rate)
    summary = {
        "source": "FULL_V56_FALLBACK_SCAN",
        "exit_a_tp1_100pct": {
            "original_1pct_compound": _selected_metrics(selected_a),
            "fixed20_maxloss10u": fixed_a,
            "portfolio_native": port_a,
        },
        "exit_b_tp1half_tp2tp3_ma": {
            "original_1pct_compound": _selected_metrics(selected_b),
            "fixed20_maxloss10u": fixed_b,
        },
        "inherited_stock_result": stock_result,
        "early_timing_research": {"status": "SKIPPED_IN_SLOW_FALLBACK"},
    }
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_a_tp1_all_v60.csv", selected_a)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_a_fixed20_10u_v60.csv", fixed_a_rows)
    _write_union(ALPACA_D1_OUTPUT_DIR / "trades_exit_b_fixed20_10u_v60.csv", fixed_b_rows)
    _atomic_json(RESEARCH_OUTPUT_DIR / "stocks_summary_v60.json", summary)
    return summary


# ======================================================================================
# Main
# ======================================================================================

def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MARKETS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ALPACA_D1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STOCK_RESIM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STOCK_EARLY_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("CryptoRadar V60 啟動")
    print("A. 正式進場核心：V56結構核心 + V60禁止低檔追空；V58 >3.25ATR一刀切仍取消")
    print(f"A2. SHORT 已延伸 >= {V60_DEEP_SHORT_ATR:g}個4H ATR：DIRECT空單禁止；回踩至少跨回破位 {V60_MIN_PULLBACK_RETRACE_ATR:g}ATR，或等4H反彈失敗再空")
    print("B. 出場A：TP1 100%全賣")
    print("C. 出場B：TP1賣50% + TP2賣25% + TP3出剩餘；短期均線可先把剩餘出掉")
    print("D. 資金A：1000U / 當下本金1%複利")
    print(f"E. 資金B：1000U / 固定{FIXED_LEVERAGE:g}x / 每單結構停損預算約{FIXED_MAX_LOSS_U:g}U")
    print("F. 研究：ATR分區、1D+4H軟性標籤、第一次回踩、MAX5 vs 不限、4H提早進場")
    print("G. TP1前與無TP1 Runner：原始結構停損固定不移動")

    # Exchange: one scan only using V56 entries + V60 exit-B. The true TP1-all portfolio is
    # built afterward by transforming the same candidate DB, so no second exchange download.
    print("\n" + "=" * 96)
    print("[V60-EX] 開始 V56 正式進場核心（只掃一次）")
    exchange_result = v56.run_exchange_branch_v56()
    exchange_summary = _run_exchange_poststudy(exchange_result)

    # Stocks: prefer V58 per-symbol entry checkpoints and re-sim exits only.
    stock_fast = _run_stock_fast_from_v58()
    if stock_fast is not None:
        stock_summary = _run_stock_poststudy_fast(stock_fast)
    else:
        stock_summary = _run_stock_fallback_full_scan()

    combined = {
        "version": "V60",
        "starting_equity": STARTING_EQUITY,
        "formal_entry_core": "V56 + V60 anti-low-chase SHORT / rebound-failure rescue",
        "formal_v58_hard_filters": False,
        "v60_anti_low_chase_short": {
            "deep_short_atr": V60_DEEP_SHORT_ATR,
            "direct_short_when_deep": "BLOCK",
            "first_pullback_when_deep": f"requires entry_extension_atr <= -{V60_MIN_PULLBACK_RETRACE_ATR:g}",
            "rebound_failure_rescue": True,
            "stock_rescue_window_days": V60_REBOUND_RESCUE_WINDOW_DAYS,
        },
        "exit_methods": {
            "A": "TP1 100% exit",
            "B": "TP1 50% + TP2 25% + TP3 remaining, with 5D/10D MA weakness able to close remaining first",
        },
        "money_management": {
            "A": "1% current-realized-equity compound",
            "B": f"{FIXED_LEVERAGE:g}x leverage setting + approximately {FIXED_MAX_LOSS_U:g}U all-in structural-stop budget",
        },
        "exchange": exchange_summary,
        "stocks": stock_summary,
        "research_rules": {
            "pre_tp1_stop": "ORIGINAL_STRUCTURE_STOP_ONLY",
            "price_discovery_runner": "ORIGINAL_STRUCTURE_STOP_ONLY",
            "v58_1d_4h_gate": "OBSERVATIONAL_ONLY",
            "v58_3p25_atr_gate": "OBSERVATIONAL_ONLY",
            "v60_low_chase_short_gate": "FORMAL",
            "v60_rebound_failure_short": "FORMAL_RESCUE",
            "atr_buckets": ["<2", "2-3.25", "3.25-4", "4-5", ">=5"],
            "max5_vs_unlimited": True,
            "early_timing": "RESEARCH_ONLY_NO_3P25_HARD_GATE" if ENABLE_EARLY_RESEARCH else "DISABLED",
            "lookahead": False,
            "ticker_exceptions": False,
        },
        "fixed20_note": (
            "20x is a margin setting, not a P&L multiplier. Exact liquidation is not modeled without venue/tier/margin-mode rules; "
            "V60 reports conservative isolated-leverage stop warnings separately."
        ),
    }
    _atomic_json(OUTPUT_DIR / "summary_v60.json", combined)

    # Compact human-readable comparison CSV.
    compare_rows: list[dict[str, Any]] = []
    for market_name, branch in (("EXCHANGE", exchange_summary), ("STOCK_PROXY", stock_summary)):
        for exit_key, exit_label in (("exit_a_tp1_100pct", "TP1_ALL"), ("exit_b_tp1half_tp2tp3_ma", "TP1_HALF_TP23_MA")):
            data = branch.get(exit_key, {}) if isinstance(branch, dict) else {}
            original = data.get("original_1pct_compound", {}) if isinstance(data, dict) else {}
            fixed = data.get("fixed20_maxloss10u", {}) if isinstance(data, dict) else {}
            compare_rows.append({
                "market": market_name, "exit": exit_label, "money": "ORIGINAL_1PCT_COMPOUND",
                "trades": original.get("trades", 0), "win_rate_pct": original.get("win_rate_pct", 0),
                "net_profit_usdt": original.get("net_profit_usdt", 0),
                "ending_equity": original.get("ending_equity_realized", original.get("ending_equity", 0)),
                "growth_pct": original.get("growth_pct", 0), "max_drawdown_pct": original.get("max_drawdown_pct", 0),
            })
            compare_rows.append({
                "market": market_name, "exit": exit_label, "money": "FIXED20_MAXLOSS10U",
                "trades": fixed.get("trades", 0), "win_rate_pct": fixed.get("win_rate_pct", 0),
                "net_profit_usdt": fixed.get("net_profit_usdt", 0),
                "ending_equity": fixed.get("ending_equity_realized", 0),
                "growth_pct": fixed.get("growth_pct", 0), "max_drawdown_pct": fixed.get("max_drawdown_pct", 0),
            })
    _write_union(OUTPUT_DIR / "v60_main_comparison.csv", compare_rows)

    print("\n" + "=" * 96)
    print("V60 完成")
    print(f"主比較：{OUTPUT_DIR / 'v60_main_comparison.csv'}")
    print(f"完整總結：{OUTPUT_DIR / 'summary_v60.json'}")
    print(f"交易所：{MARKETS_OUTPUT_DIR}")
    print(f"美股proxy：{ALPACA_D1_OUTPUT_DIR}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
