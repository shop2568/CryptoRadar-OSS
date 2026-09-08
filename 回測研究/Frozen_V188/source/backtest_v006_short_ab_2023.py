#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.1 上線策略｜2023 歷史下架幣完整回測。

固定期間：2023-08-01 00:00 UTC <= 進場 < 2024-08-01 00:00 UTC

本檔刻意直接重播目前正式雷達的實際鏈：
  V101 即時核心/擴張候選 -> V139 窄結構且量能過度堆積閘門
  -> V101 MAX5 / NEW MAX2 / 核心優先 / 方向冷卻
  -> V006.1 全倉20倍、停損風險1%、每倉名目價值最多1.5倍本金
  -> V006.1 出場（TP1 50%；TP2 25%；TP3 剩餘25%；期間由5/10日線保護）

只使用 Binance 官方歷史壓縮檔下載至本機的 15 分鐘 K 線，包含現在已下架標的。
沒有該期間資料的標的不冒充、不補插值，列入資料限制報告。
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import math
import os
import pickle
import sys
import time
import copy
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
PROJECT = Path.home() / "CryptoRadar"
LEGACY = Path.home() / "Documents" / "ChatGPT" / "回測專案"
for p in (PROJECT, LEGACY):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import backtest_1y_v60_r3 as r3  # noqa: E402
import backtest_1y_v84_pre_pump_entry as v84  # noqa: E402
import v003_live_engine as legacy_live  # noqa: E402
import v005_quality_gate as live_quality  # noqa: E402
import v101_live_policy as live_policy  # noqa: E402


PERIOD_ID = os.environ.get("V006_PERIOD", "2023")
if PERIOD_ID == "2024":
    START = pd.Timestamp("2024-08-01T00:00:00Z")
    END = pd.Timestamp("2025-08-01T00:00:00Z")
    OUT = HERE / "2024空單AB結果"
    ARCHIVE_ROOT = PROJECT / "backtest_v145_previous_year_upstream_rebuild_results"
    CACHE = ARCHIVE_ROOT / "historical_cache_15m"
    SOURCE_MARKETS = ARCHIVE_ROOT / "01_v52_rr4" / "exchange_markets"
    QUALITY_CSV = SOURCE_MARKETS / "data_quality.csv"
    SOURCE_ERRORS_CSV = SOURCE_MARKETS / "errors.csv"
    STOCK_TOKEN_CSV = SOURCE_MARKETS / "us_stock_tokens_catalog.csv"
elif PERIOD_ID == "2025":
    # 現有穩定快取實際只到 2026-07-29；不杜撰 7/30～8/1 的資料。
    START = pd.Timestamp("2025-08-01T00:00:00Z")
    END = pd.Timestamp("2026-07-30T00:00:00Z")
    OUT = HERE.parent / "V006_1真正逐根回測" / "近期一年驗收"
    ARCHIVE_ROOT = PROJECT
    CACHE = PROJECT / "backtest_cache_1y"
    SOURCE_MARKETS = PROJECT / "backtest_1y_v52_results" / "exchange_markets"
    QUALITY_CSV = SOURCE_MARKETS / "data_quality.csv"
    SOURCE_ERRORS_CSV = SOURCE_MARKETS / "errors.csv"
    STOCK_TOKEN_CSV = SOURCE_MARKETS / "us_stock_tokens_catalog.csv"
else:
    PERIOD_ID = "2023"
    START = pd.Timestamp("2023-08-01T00:00:00Z")
    END = pd.Timestamp("2024-08-01T00:00:00Z")
    OUT = HERE / "2023空單AB結果"
    ARCHIVE_ROOT = Path.home() / "Documents" / "Codex" / "2026-07-23" / "new-chat" / "backtest_v147_2023_delisted_results"
    CACHE = ARCHIVE_ROOT / "historical_cache_15m"
    QUALITY_CSV = ARCHIVE_ROOT / "V147_binance_historical_universe.csv"
    SOURCE_ERRORS_CSV = ARCHIVE_ROOT / "不存在的來源錯誤.csv"
    STOCK_TOKEN_CSV = ARCHIVE_ROOT / "不存在的美股代幣.csv"

MAX_POSITIONS = 5
MAX_NEW_POSITIONS = 2
MIN_RR = 4.0
RISK_PER_TRADE = 0.01
POSITION_NOTIONAL_CAP = 1.50
LEVERAGE = 20
BUILD_LIMIT = float(live_quality.BUILD_LIMIT)
RANGE_LIMIT = float(live_quality.RANGE_LIMIT)
FEE_SIDE = float(r3.engine.FEE_PER_SIDE) + float(r3.engine.SLIPPAGE_PER_SIDE)
INITIAL_EQUITY = 1000.0
EPS = 1e-12


def num(value: Any, default: float = math.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def base_of(symbol: str) -> str:
    return str(symbol).split("/")[0].replace("1000", "").upper()


def cache_path(exchange: str, symbol: str) -> Path | None:
    identities = [f"{exchange}|{symbol}|15m|365"]
    if exchange != "Binance":
        identities.insert(0, identities[0] + "|gapfix_v1")
    for identity in identities:
        key = hashlib.sha256(identity.encode()).hexdigest()
        for p in (CACHE / f"stable_{key}.pkl.gz", CACHE / f"{key}.pkl.gz"):
            if p.exists():
                return p
    return None


def load_frame(exchange: str, symbol: str) -> pd.DataFrame:
    p = cache_path(exchange, symbol)
    if p is None:
        return pd.DataFrame()
    with gzip.open(p, "rb") as fh:
        payload = pickle.load(fh)
    d = payload[0] if isinstance(payload, tuple) else payload
    if not isinstance(d, pd.DataFrame) or d.empty:
        return pd.DataFrame()
    d = d.copy()
    d.index = pd.to_datetime(d.index, utc=True, errors="coerce")
    d = d[~d.index.isna()].sort_index()
    # 正式雷達在區間第一根也已持有前 1,000 根暖機資料。若從 START
    # 才切 K 線，區間開頭約十天必然漏訊號，且所有長週期指標都會重置。
    warmup_start = START - pd.Timedelta(minutes=15 * 1000)
    d = d[(d.index >= warmup_start) & (d.index < END)]
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["open", "high", "low", "close", "volume"])


def v96_filter(row: dict) -> bool:
    return (
        45.0 <= num(row.get("score")) <= 50.0
        and 2.20 <= num(row.get("volume_ratio")) <= 3.50
        and 1.15 <= num(row.get("volume_build_3v20")) <= 1.80
        and num(row.get("ret8_before_entry_pct")) <= 3.5
        and num(row.get("stop_pct")) <= 0.045
        and 0.10 <= num(row.get("ema20_slope8_atr")) <= 0.90
        and num(row.get("prior_resistance_room_r"), math.inf) >= 4.0
    )


def short_expansion_candidates(d: pd.DataFrame, meta: dict) -> list[dict]:
    """V84 做多擴張模組的因果對稱空單；不使用未來資料。"""
    if d.empty:
        return []
    x = d.copy()
    atr = x["atr"].replace(0, np.nan)
    span = (x["high"] - x["low"]).replace(0, np.nan)
    x["body_down_atr"] = (x["open"] - x["close"]) / atr
    x["down_close_location"] = (x["high"] - x["close"]) / span
    x["above_ema20_recent"] = (x["close"] > x["ema20"]).rolling(8, min_periods=8).max().shift(1)
    x["old_low30d"] = x["low"].shift(32).rolling(96 * 30, min_periods=96 * 7).min()
    base = str(meta.get("base") or str(meta.get("symbol", "")).split("/")[0]).replace("1000", "").upper()
    group = "BTC" if base == "BTC" else "ETH" if base == "ETH" else "小幣"
    lim = v84._adaptive_limits(group)
    trend = (
        (x["close"] < x["ema20"])
        & (x["ema5"] < x["ema10"])
        & (x["ema20_slope8"] < 0.0)
        & (x["close"] < x["ema320"] * 1.015)
        & (x["ema320_slope16"] < 0.20)
    )
    early_not_dumped = (
        (x["ret4"] <= -lim["min_ret4"])
        & (x["ret8"] >= -lim["max_ret8"])
    )
    quality = (
        (x["body_down_atr"] >= 0.18)
        & (x["body_down_atr"] <= 1.30)
        & (x["down_close_location"] >= 0.64)
        & (x["vol_ratio"] >= lim["min_vol"])
        & (x["vol_ratio"] <= 5.0)
        & (x["volume_build"] >= 1.02)
    )
    setups = {
        "壓縮跌破回踩空": (
            (x["close"] < x["prior_low20"])
            & (x["range20_atr"] >= 1.3)
            & (x["range20_atr"] <= lim["max_range"])
        ),
        "均線假突破跌回空": (
            (x["above_ema20_recent"] > 0)
            & (x["close"] < x["ema20"])
            & (x["close"] < x["prior_low8"])
            & (x["range8_atr"] <= 4.5)
        ),
    }
    rows: list[dict] = []
    last_entry = -99999
    for setup_name, setup_mask in setups.items():
        idxs = np.flatnonzero((trend & early_not_dumped & quality & setup_mask).fillna(False).to_numpy())
        for j0 in idxs:
            j = int(j0); e = j + 1
            if e >= len(x) or e - last_entry < 32:
                continue
            entry = num(x["open"].iloc[e]); close = num(x["close"].iloc[j]); a = num(x["atr"].iloc[j])
            if min(entry, close, a) <= 0 or entry < close - 0.20 * a:
                continue
            lookback = 20 if setup_name == "壓縮跌破回踩空" else 12
            structural_high = num(x["high"].iloc[max(0, j - lookback + 1):j + 1].max())
            stop = structural_high + 0.12 * a
            risk = stop - entry; stop_pct = risk / entry
            min_stop = 0.004 if group in {"BTC", "ETH"} else 0.006
            if not (min_stop <= stop_pct <= 0.045):
                continue
            old_low = num(x["old_low30d"].iloc[j])
            room_r = math.inf if not math.isfinite(old_low) or old_low >= entry else (entry - old_low) / risk
            if room_r < MIN_RR:
                continue
            build = num(x["volume_build"].iloc[j]); vol = num(x["vol_ratio"].iloc[j])
            slope = num(x["ema20_slope8"].iloc[j]); ret8 = num(x["ret8"].iloc[j])
            down_loc = num(x["down_close_location"].iloc[j]); rng = num(x["range20_atr"].iloc[j])
            score = ((18.0 if setup_name == "壓縮跌破回踩空" else 14.0)
                     + min(build, 3.0) * 8.0 + min(vol, 3.0) * 4.0
                     + min(max(-slope, 0.0), 2.0) * 4.0 + down_loc * 5.0
                     - max(rng - 3.0, 0.0) * 2.0 - max(-ret8 - 0.025, 0.0) * 100.0)
            row = {
                "version": "V006_SHORT_RESEARCH", "entry_mode": setup_name,
                "exchange": str(meta.get("exchange", "")), "symbol": str(meta.get("symbol", "")),
                "base": base, "asset_group": group, "signal_time": x.index[j], "entry_time": x.index[e],
                "entry_index": e, "entry": entry, "stop": stop, "risk_price": risk, "stop_pct": stop_pct,
                "ret4_before_entry_pct": num(x["ret4"].iloc[j]) * 100.0,
                "ret8_before_entry_pct": ret8 * 100.0, "volume_ratio": vol,
                "volume_build_3v20": build, "range20_atr": rng, "ema20_slope8_atr": slope,
                "prior_resistance_room_r": room_r, "score": score, "direction": "SHORT",
                "family": "EXPANSION", "portfolio_kind": "NEW", "module": "對稱擴張空單研究",
                "setup": setup_name, "rr": 4.0, "rr1": 4.0,
                "tp1": entry - 4.0 * risk, "tp2": entry - 5.0 * risk, "tp3": entry - 6.0 * risk,
                "priority": score, "live_rule": "SHORT_RESEARCH_4R", "research_variant": "SHORT_SYM",
                "short_environment_ok": bool(
                    num(x["close"].iloc[j]) < num(x["ema320"].iloc[j])
                    and num(x["ema20"].iloc[j]) < num(x["ema320"].iloc[j])
                    and slope <= -0.10 and num(x["ema320_slope16"].iloc[j]) < 0.0
                ),
            }
            if (45.0 <= score <= 50.0 and 2.20 <= vol <= 3.50 and 1.15 <= build <= 1.80
                    and -3.5 <= row["ret8_before_entry_pct"] <= 0.0 and -0.90 <= slope <= -0.10):
                rows.append(row); last_entry = e
    return rows


def v139_reject(row: dict) -> str:
    # 直接呼叫正式 V005/V139 品質閘門，避免回測另抄一份規則後漂移。
    return live_quality.quality_reason(row)


def scan_one(item: tuple[str, str, str]) -> dict:
    exchange, symbol, base = item
    try:
        raw = load_frame(exchange, symbol)
        if len(raw) < 850:
            return {"item": item, "rows": [], "error": f"完整K線不足：{len(raw)}"}
        target = SimpleNamespace(exchange_name=exchange, symbol=symbol, base=base)
        prepared_core, piv_hi, piv_lo = r3.engine.prepare_indicators(raw)
        prepared_v84 = v84.prepare(raw)
        funnel: dict[str, int] = defaultdict(int)
        raw_core = list(r3.candidate_rows_v60(target, prepared_core, START, piv_hi, piv_lo, funnel))
        rows: list[dict] = []
        grouped: dict[tuple[pd.Timestamp, str], list[dict]] = defaultdict(list)
        for values in raw_core:
            row = dict(zip(legacy_live.COLUMNS, values))
            when = pd.to_datetime(row.get("entry_time"), utc=True, errors="coerce")
            if pd.isna(when) or not (START <= when < END):
                continue
            audit = legacy_live._audit_for(row)
            for key in ("setup_type", "regime_audit", "entry_extension_atr", "market_heat_ratio_audit_only",
                        "chase_distance_4h_atr", "rebound_depth_atr", "target_mode"):
                if key in audit:
                    row[key] = audit[key]
            row.setdefault("setup_type", "V32_CORE")
            row["setup"] = row["setup_type"]
            row["rr"] = num(row.get("rr1"), 0.0)
            if row["rr"] < MIN_RR:
                continue
            score, parts = legacy_live._v61_priority(row)
            row.update({"v61_priority": score, "priority_parts": parts, "priority": 1000.0 + score,
                         "family": "CORE", "portfolio_kind": "CORE", "module": str(row["setup_type"]),
                         "live_rule": "V101_CORE_4R", "research_variant": "BASE"})
            signal = prepared_v84[prepared_v84.index < when].tail(1)
            if not signal.empty:
                s = signal.iloc[-1]
                row.update({
                    "volume_ratio": num(s.get("vol_ratio"), num(row.get("confirmation_volume"), 0.0)),
                    "volume_build_3v20": num(s.get("volume_build")),
                    "range20_atr": num(s.get("range20_atr")),
                    "ema20_slope8_atr": num(s.get("ema20_slope8")),
                    "ret8_before_entry_pct": num(s.get("ret8")) * 100.0,
                })
            entry, stop = num(row.get("entry")), num(row.get("stop"))
            row["stop_pct"] = abs(entry - stop) / entry if min(entry, stop) > 0 else math.nan
            row["base"] = base
            grouped[(when, str(row.get("direction", "")))].append(row)
        for candidates in grouped.values():
            rows.append(max(candidates, key=lambda x: num(x.get("v61_priority"), -999.0)))

        meta = {"exchange": exchange, "symbol": symbol, "base": base}
        for variant in ("壓縮早突破", "均線假跌破站回"):
            found, _ = v84.candidates(prepared_v84, meta, variant)
            for source in found:
                if not v96_filter(source):
                    continue
                row = {k: v for k, v in source.items() if k != "_frame"}
                entry, stop = num(row.get("entry")), num(row.get("stop"))
                risk = entry - stop
                if min(entry, stop, risk) <= 0:
                    continue
                row.update({
                    "direction": "LONG", "family": "EXPANSION", "portfolio_kind": "NEW",
                    "module": "V96強勢突破延續_驗證型", "setup": variant,
                    "rr": 4.0, "rr1": 4.0, "tp1": entry + 4 * risk,
                    "tp2": entry + 5 * risk, "tp3": entry + 6 * risk,
                    "priority": num(row.get("score")), "live_rule": "V101_V96_EXPANSION_4R",
                    "research_variant": "BASE",
                })
                rows.append(row)

        raw_direction = Counter(str(x.get("direction", "")).upper() for x in rows)
        # 快速等價重播只把這些 V139 前事件當「正式引擎檢查時間」。
        # 不把舊快掃的方向、價格或品質判定當正式結果。
        event_rows = [
            {"entry_time": pd.to_datetime(x.get("entry_time"), utc=True, errors="coerce")}
            for x in rows
            if not pd.isna(pd.to_datetime(x.get("entry_time"), utc=True, errors="coerce"))
        ]
        dedup: dict[tuple[str, str, str], dict] = {}
        for row in rows:
            row["entry_time"] = pd.to_datetime(row["entry_time"], utc=True)
            reason = v139_reject(row)
            row["V139品質判定"] = reason or "通過"
            if reason:
                continue
            key = (row["entry_time"].isoformat(), str(row.get("direction")), str(row.get("portfolio_kind")),
                   str(row.get("research_variant", "BASE")))
            if key not in dedup or num(row.get("priority"), -999) > num(dedup[key].get("priority"), -999):
                dedup[key] = row
        clean = []
        for row in dedup.values():
            clean.append({k: v for k, v in row.items() if not isinstance(v, (pd.Series, pd.DataFrame))})
        kept_direction = Counter(str(x.get("direction", "")).upper() for x in clean)
        return {"item": item, "rows": clean, "event_rows": event_rows,
                "error": "", "funnel": dict(funnel),
                "方向稽核": {
                    "原始候選_做多": raw_direction.get("LONG", 0),
                    "原始候選_做空": raw_direction.get("SHORT", 0),
                    "V139後_做多": kept_direction.get("LONG", 0),
                    "V139後_做空": kept_direction.get("SHORT", 0),
                }}
    except Exception as exc:
        return {"item": item, "rows": [], "error": f"{type(exc).__name__}: {exc}"}


def daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    day = frame.resample("1D").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                   close=("close", "last"), volume=("volume", "sum")).dropna(subset=["close"])
    day["sma5"] = day.close.rolling(5, min_periods=5).mean()
    day["sma10"] = day.close.rolling(10, min_periods=10).mean()
    return day


def dynamic_ma(hist: pd.DataFrame, direction: str) -> tuple[int, str]:
    if len(hist) < 12:
        return 5, "日K不足，使用5日線保護"
    x = hist.tail(12); last = x.iloc[-1]; sign = 1.0 if direction == "LONG" else -1.0
    c, m5, m10 = num(last.close), num(last.sma5), num(last.sma10)
    if not all(math.isfinite(v) for v in (c, m5, m10)):
        return 5, "均線資料不足，使用5日線保護"
    ordered = sign * (c - m5) > 0 and sign * (m5 - m10) > 0
    slopes = sign * (m5 - num(x.sma5.iloc[-4])) > 0 and sign * (m10 - num(x.sma10.iloc[-4])) > 0
    signed_move = sign * (c - num(x.close.iloc[-6]))
    travel = float(x.close.tail(6).diff().abs().sum())
    efficiency = signed_move / max(travel, EPS)
    extension = sign * (c - m10) / max(abs(m10), EPS)
    if ordered and slopes and efficiency >= 0.35 and extension <= 0.18:
        return 10, f"趨勢完整，使用10日線（效率{efficiency:.2f}）"
    return 5, f"趨勢轉弱，使用5日線（效率{efficiency:.2f}）"


def simulate(row: dict, frame: pd.DataFrame, prepared_daily: pd.DataFrame | None = None) -> tuple[dict, list[dict]]:
    x = dict(row); direction = str(x.get("direction", "LONG")).upper(); sign = 1.0 if direction == "LONG" else -1.0
    entry_time = pd.to_datetime(x["entry_time"], utc=True)
    entry, stop, tp1 = num(x.get("entry")), num(x.get("stop")), num(x.get("tp1"))
    tp2, tp3 = num(x.get("tp2")), num(x.get("tp3"))
    risk = abs(entry - stop)
    d = frame[frame.index >= entry_time]
    if d.empty or min(entry, stop, tp1, risk) <= 0:
        x.update(final_exit_reason_v92="資料不足", outcome_v92="OPEN", remaining_pct_v92=100.0)
        return x, []
    if not math.isfinite(tp2) or (sign * (tp2 - tp1) <= 0): tp2 = entry + sign * 5 * risk
    if not math.isfinite(tp3) or (sign * (tp3 - tp2) <= 0): tp3 = entry + sign * 6 * risk
    x.update(tp2=tp2, tp3=tp3)
    # 正式 V006.1：所有標的一律 TP1 50%、TP2 25%、TP3 剩餘25%。
    # TP1 後成本保護；在 TP2/TP3 前，5/10 日線弱化可提前退出剩餘部位。
    weights = (0.50, 0.25, 0.25)
    remaining = 1.0; realized_r = -entry * FEE_SIDE / risk; events = [{"time": entry_time, "pnl_r": realized_r, "event": "進場成本"}]
    tp1_hit = tp2_hit = tp3_hit = False
    tp1_time = tp2_time = tp3_time = pd.NaT
    final_time = pd.NaT; final_price = math.nan; final_reason = "資料結束仍持有"; ma_period = math.nan
    day = prepared_daily if prepared_daily is not None else daily_frame(frame)
    ma_active_day = None; checked_day = None

    def sell(weight: float, price: float, when: pd.Timestamp, reason: str):
        nonlocal remaining, realized_r, final_time, final_price, final_reason
        weight = min(weight, remaining)
        if weight <= EPS: return
        component = weight * (sign * (price - entry) / risk) - weight * price * FEE_SIDE / risk
        realized_r += component; remaining = max(0.0, remaining - weight)
        events.append({"time": when, "pnl_r": component, "event": reason})
        if remaining <= EPS:
            final_time, final_price, final_reason = when, price, reason

    forced = str(x.get("exit_reason")) == "核心訊號進場讓位"
    forced_time = pd.to_datetime(x.get("exit_time"), utc=True, errors="coerce") if forced else pd.NaT
    forced_price = num(x.get("exit_price"))
    for when, bar in d.iterrows():
        o, high, low = num(bar.open), num(bar.high), num(bar.low)
        if pd.notna(forced_time) and when >= forced_time:
            sell(remaining, forced_price if math.isfinite(forced_price) else o, when, "核心訊號進場讓位"); break
        # 與 live_v006_1._cost_break_even_price 相同：把雙邊0.09%成本納入保本價。
        if tp1_hit:
            stop_now = (entry * (1.0 + FEE_SIDE) / (1.0 - FEE_SIDE)
                        if direction == "LONG"
                        else entry * (1.0 - FEE_SIDE) / (1.0 + FEE_SIDE))
        else:
            stop_now = stop
        hit_stop = low <= stop_now if direction == "LONG" else high >= stop_now
        hit1 = high >= tp1 if direction == "LONG" else low <= tp1
        hit2 = high >= tp2 if direction == "LONG" else low <= tp2
        hit3 = high >= tp3 if direction == "LONG" else low <= tp3
        # 15分K無法知道同根先後，採實盤保守的停損優先。
        if hit_stop:
            sell(remaining, stop_now, when, "原停損" if not tp1_hit else "成本保護"); break
        if not tp1_hit and hit1:
            tp1_hit = True; tp1_time = when; sell(weights[0], tp1, when, f"TP1賣{weights[0]*100:.0f}%")
            ma_active_day = when.floor("D") + pd.Timedelta(days=1)
        if tp1_hit and remaining > 0 and not tp2_hit and hit2:
            tp2_hit = True; tp2_time = when; sell(weights[1], tp2, when, "TP2賣25%")
        if tp1_hit and remaining > 0 and not tp3_hit and hit3:
            tp3_hit = True; tp3_time = when; sell(weights[2], tp3, when, "TP3賣剩餘25%")
        if remaining <= EPS: break
        current_day = when.floor("D"); prev_day = current_day - pd.Timedelta(days=1)
        if tp1_hit and ma_active_day is not None and current_day >= ma_active_day and prev_day != checked_day:
            checked_day = prev_day; hist = day[day.index <= prev_day]
            if not hist.empty:
                period, _ = dynamic_ma(hist, direction); last = hist.iloc[-1]
                crossed = num(last.close) < num(last[f"sma{period}"]) if direction == "LONG" else num(last.close) > num(last[f"sma{period}"])
                if crossed:
                    ma_period = period; sell(remaining, o, when, f"{period}日均線退出"); break
    last_price = num(d.close.iloc[-1]); unrealized_r = remaining * sign * (last_price - entry) / risk - remaining * last_price * FEE_SIDE / risk if remaining > 0 else 0.0
    closed = remaining <= EPS
    x.update({
        "direction_v92": direction, "tp1_time_v92": tp1_time, "tp2_time_v92": tp2_time, "tp3_time_v92": tp3_time,
        "final_exit_time_v92": final_time, "final_exit_price_v92": final_price, "final_exit_reason_v92": final_reason,
        "remaining_pct_v92": remaining * 100.0, "realized_net_r_v92": realized_r,
        "unrealized_net_r_v92": unrealized_r, "outcome_v92": "WIN" if closed and realized_r > 0 else "LOSS" if closed else "OPEN",
        "ma_exit_period_v92": ma_period, "last_time_v92": d.index[-1], "last_price_v92": last_price,
        "exit_time": final_time if closed else d.index[-1], "exit_price": final_price if closed else last_price,
        "exit_reason": final_reason, "stop_pct": risk / entry, "rr1": sign * (tp1 - entry) / risk,
    })
    return x, events


def end_time(trade: dict) -> pd.Timestamp:
    t = pd.to_datetime(trade.get("final_exit_time_v92"), utc=True, errors="coerce")
    return t if pd.notna(t) else END


def elite_reason(obj: dict) -> str:
    tr = obj["trade"]; side = str(tr.get("direction", "")).upper(); module = str(tr.get("module", ""))
    if side == "SHORT" and obj.get("kind") == "CORE": return "核心反彈失敗空單"
    score, build = num(tr.get("score")), num(tr.get("volume_build_3v20"))
    vol, slope = num(tr.get("volume_ratio")), num(tr.get("ema20_slope8_atr"))
    ret8, stop = num(tr.get("ret8_before_entry_pct")), num(tr.get("stop_pct"))
    if score >= 45 and build >= 2.0: return "高結構分與量能"
    if score >= 40 and vol >= 2.3 and ret8 <= .6 and stop <= .01: return "低追價窄停損早期趨勢"
    if score >= 38 and slope >= .8 and ret8 <= .6 and stop <= .01: return "高斜率低追價早期趨勢"
    return ""


def allocate(core: list[dict], new: list[dict], frames: dict) -> tuple[list[dict], list[dict]]:
    # 雷達是每 15 分鐘把三大交易所同一輪候選一次交給
    # v101_live_policy.select；不能逐筆自行重寫一套排倉順序。
    grouped: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for obj in core + new:
        grouped[pd.to_datetime(obj["trade"]["entry_time"], utc=True)].append(obj)

    active: list[dict] = []
    accepted: list[dict] = []
    rejected: list[dict] = []
    last_signal: dict[str, float] = {}

    def policy_row(obj: dict, when: pd.Timestamp) -> dict:
        row = dict(obj["trade"])
        row["portfolio_kind"] = obj["kind"]
        row["priority"] = num(obj.get("score"), num(row.get("priority"), num(row.get("score"), -999.0)))
        row["open_time"] = when.timestamp()
        return row

    for when in sorted(grouped):
        active[:] = [x for x in active if end_time(x["trade"]) > when]
        active_rows = [policy_row(x, pd.to_datetime(x["trade"]["entry_time"], utc=True)) for x in active]
        history_rows = []
        for x in accepted:
            tr = x["trade"]
            if end_time(tr) > when:
                continue
            history_rows.append({
                **tr,
                "portfolio_kind": x["kind"],
                "open_time": pd.to_datetime(tr["entry_time"], utc=True).timestamp(),
                "exit_time": end_time(tr).timestamp(),
                "exit_reason": tr.get("final_exit_reason_v92"),
            })

        candidates = []
        object_by_identity: dict[str, dict] = {}
        for replay_index, obj in enumerate(grouped[when]):
            row = policy_row(obj, when)
            replay_id = f"{when.isoformat()}|{replay_index}"
            row["__replay_id"] = replay_id
            key = f'{base_of(row.get("symbol", ""))}:{str(row.get("direction", "")).upper()}:{row["portfolio_kind"]}'
            if when.timestamp() - last_signal.get(key, 0.0) < 24 * 3600:
                rejected.append({**row, "未進場原因": "同標的同方向同模組24小時訊號冷卻"})
                continue
            candidates.append(row)
            object_by_identity[replay_id] = obj

        chosen, blocked, _ = live_policy.select(
            candidates, active_rows, history_rows, now=when.timestamp()
        )
        for row in blocked:
            reason = row.get("V101拒絕原因", "正式組合規則未選中")
            rejected.append({**row, "未進場原因": reason})
        for row in chosen:
            # policy.select 會複製 dict，但會保留這個純回測身分欄位。
            match = object_by_identity[str(row["__replay_id"])]
            active.append(match)
            accepted.append(match)
            key = f'{str(row.get("base", "")).upper()}:{str(row.get("direction", "")).upper()}:{row["portfolio_kind"]}'
            last_signal[key] = when.timestamp()
    return accepted, rejected


def money_management(objs: list[dict]) -> tuple[pd.DataFrame, dict]:
    equity = INITIAL_EQUITY; peak = equity; max_dd = 0.0; risk_map: dict[int, float] = {}; rows = []
    # 最後一欄使用唯一事件序號，避免同一交易同一時間的兩個事件
    # 讓 heapq 繼續比較 dict 而觸發 TypeError。
    timeline: list[tuple[pd.Timestamp, int, int, int, dict | None]] = []
    event_seq = 0
    for i, obj in enumerate(objs):
        heapq.heappush(timeline, (pd.to_datetime(obj["trade"]["entry_time"], utc=True), 1, i, event_seq, None))
        event_seq += 1
        for event in obj["events"]:
            heapq.heappush(timeline, (pd.to_datetime(event["time"], utc=True), 0 if event["event"] != "進場成本" else 2, i, event_seq, event))
            event_seq += 1
    realized_by = defaultdict(float)
    while timeline:
        when, kind, i, _, event = heapq.heappop(timeline)
        obj = objs[i]; tr = obj["trade"]
        if kind == 1:
            effective = min(RISK_PER_TRADE, POSITION_NOTIONAL_CAP * num(tr.get("stop_pct"), 0.0))
            risk_map[i] = equity * max(effective, 0.0)
            continue
        if i not in risk_map:  # 同時點先遇到成本事件時延後到進場後。
            heapq.heappush(timeline, (when, 2, i, event_seq, event)); event_seq += 1; continue
        pnl = num(event.get("pnl_r"), 0.0) * risk_map[i]
        equity += pnl; realized_by[i] += pnl; peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / max(peak, EPS) * 100.0)
    profits, losses = [], []
    for i, obj in enumerate(objs):
        tr = dict(obj["trade"]); risk_u = risk_map.get(i, 0.0); realized = realized_by[i]
        floating = num(tr.get("unrealized_net_r_v92"), 0.0) * risk_u
        tr.update({"風險_USDT": risk_u, "已實現淨利_USDT": realized, "浮動損益_USDT": floating,
                   "合計損益_USDT": realized + floating, "槓桿": LEVERAGE, "保證金模式": "全倉",
                   "單倉名目上限_本金倍數": POSITION_NOTIONAL_CAP})
        rows.append(tr)
        if realized > 0: profits.append(realized)
        elif realized < 0: losses.append(-realized)
    if rows:
        df = pd.DataFrame(rows)
        # 正式引擎的候選可能同時保留 ISO 文字與 pandas Timestamp。
        # 報表排序前統一成 UTC 時間；這只修正輸出型別，不影響策略與成交模擬。
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
        df = df.sort_values("entry_time")
    else:
        df = pd.DataFrame()
    wins = int(pd.to_datetime(df.get("tp1_time_v92"), utc=True, errors="coerce").notna().sum()) if len(df) else 0
    stops = int((df.get("final_exit_reason_v92") == "原停損").sum()) if len(df) else 0
    opens = int((df.get("outcome_v92") == "OPEN").sum()) if len(df) else 0
    summary = {
        "版本": f"V006.1真正逐根回測_{PERIOD_ID}", "期間": f"{START.isoformat()} ~ {END.isoformat()}（右界不含）",
        "交易數": len(df), "TP1筆數": wins, "TP1前停損": stops, "未平倉": opens,
        "總勝率_pct": round(wins / max(wins + stops, 1) * 100, 2),
        "做多交易": int((df.get("direction") == "LONG").sum()) if len(df) else 0,
        "做空交易": int((df.get("direction") == "SHORT").sum()) if len(df) else 0,
        "已實現淨利_USDT": round(float(df.get("已實現淨利_USDT", pd.Series(dtype=float)).sum()), 2),
        "浮動損益_USDT": round(float(df.get("浮動損益_USDT", pd.Series(dtype=float)).sum()), 2),
        "期末權益_USDT": round(equity, 2), "最大回撤_pct": round(max_dd, 2),
        "Profit_Factor": round(sum(profits) / max(sum(losses), EPS), 3),
        "槓桿": "全倉20倍", "單筆停損風險": "當下權益1%", "單倉名目上限": "當下權益1.5倍",
        "同根停損與停利並存": "保守採停損優先", "Mark_Price限制": "僅有成交價K線，無法證明實盤標記價格不會先觸發",
    }
    return df, summary


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not QUALITY_CSV.exists() or not CACHE.exists():
        raise FileNotFoundError("找不到 2023 Binance 歷史快取或市場清單")
    q = pd.read_csv(QUALITY_CSV)
    stock_catalog = pd.read_csv(STOCK_TOKEN_CSV) if STOCK_TOKEN_CSV.exists() else pd.DataFrame()
    stock_keys = {
        (str(r.exchange), str(r.symbol))
        for r in stock_catalog.itertuples(index=False)
    }
    # 2023 使用 Binance 官方歷史封存；2024/2025 使用既有三交易所穩定快取。
    # 所有期間都只納入真的有足夠K線的市場，不補插值、不把缺資料算成零交易。
    items = []
    for r in q.itertuples(index=False):
        exchange = "Binance" if PERIOD_ID == "2023" else str(getattr(r, "exchange", ""))
        symbol = str(getattr(r, "ccxt_symbol", "")) if PERIOD_ID == "2023" else str(getattr(r, "symbol", ""))
        base = str(getattr(r, "base", ""))
        rows_in_period = int(num(getattr(r, "v145_rows_in_period", getattr(r, "rows", 0)), 0))
        downloaded = str(getattr(r, "status", "下載完成")) == "下載完成"
        if downloaded and rows_in_period >= 850 and cache_path(exchange, symbol):
            items.append((exchange, symbol, base))
    print(f"V006.1 {PERIOD_ID}回測｜可用完整市場 {len(items)}｜固定期間 {START.date()}～{END.date()}")
    all_rows: list[dict] = []; errors = []; funnel = Counter(); direction_audit = Counter(); started = time.time()
    # 本機回測可用環境變數調整；預設最多6個，避免1,000多市場只用3核拖太久。
    workers = min(int(os.environ.get("V006_WORKERS", "6")), max(1, (os.cpu_count() or 2) // 2))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scan_one, item): item for item in items}
        done = 0
        for future in as_completed(futures):
            done += 1; result = future.result(); all_rows.extend(result.get("rows", [])); funnel.update(result.get("funnel", {}))
            direction_audit.update(result.get("方向稽核", {}))
            if result.get("error"): errors.append({"exchange": result["item"][0], "symbol": result["item"][1], "錯誤": result["error"]})
            print(f"\r歷史訊號掃描 {done}/{len(items)}｜候選 {len(all_rows)}｜錯誤 {len(errors)}", end="", flush=True)
    print()
    # 同一標的同方向同一根只留優先分最高者。
    best = {}
    for row in all_rows:
        key = (base_of(row["symbol"]), str(row["direction"]),
               pd.to_datetime(row["entry_time"], utc=True), str(row.get("research_variant", "BASE")))
        if key not in best or num(row.get("priority"), -999) > num(best[key].get("priority"), -999): best[key] = row
    candidates = list(best.values())
    needed = sorted({(str(x["exchange"]), str(x["symbol"])) for x in candidates})
    frames = {}; daily_frames = {}; sim_errors = []
    for n, key in enumerate(needed, 1):
        frame = load_frame(*key); frames[key] = frame; daily_frames[key] = daily_frame(frame)
        print(f"\r重播出場 {n}/{len(needed)}", end="", flush=True)
    print()
    all_objs = []
    for row in candidates:
        key = (str(row["exchange"]), str(row["symbol"])); frame = frames.get(key)
        try:
            trade, events = simulate(row, frame, daily_frames.get(key))
            obj = {"input": dict(row), "trade": trade, "events": events,
                   "kind": "CORE" if row.get("portfolio_kind") == "CORE" else "NEW",
                   "score": num(row.get("priority"), 0.0)}
            all_objs.append(obj)
        except Exception as exc:
            sim_errors.append({"exchange": key[0], "symbol": key[1], "錯誤": f"{type(exc).__name__}: {exc}"})
    baseline_objs = [x for x in all_objs if str(x["trade"].get("research_variant", "BASE")) == "BASE"]
    variants = {"正式V006.1": baseline_objs}
    comparison = []
    all_rejected = []
    for label, source_objs in variants.items():
        objs = copy.deepcopy(source_objs)
        core_objs = [x for x in objs if x["kind"] == "CORE"]
        new_objs = [x for x in objs if x["kind"] == "NEW"]
        accepted, rejected = allocate(core_objs, new_objs, frames)
        trades, summary = money_management(accepted)
        direction_audit["排倉後_做多"] = int((trades.get("direction") == "LONG").sum()) if len(trades) else 0
        direction_audit["排倉後_做空"] = int((trades.get("direction") == "SHORT").sum()) if len(trades) else 0
        direction_audit["最終成交_做多"] = direction_audit["排倉後_做多"]
        direction_audit["最終成交_做空"] = direction_audit["排倉後_做空"]
        summary.update({"方案": label, "可用完整市場": len(items), "候選訊號": len(source_objs),
                        "核心候選": len(core_objs), "新增候選": len(new_objs),
                        "新增空單候選": sum(str(x["trade"].get("research_variant")) == "SHORT_SYM" for x in source_objs),
                        "組合未選中": len(rejected), "掃描錯誤": len(errors) + len(sim_errors),
                        "耗時秒": round(time.time() - started, 1)})
        comparison.append(summary)
        trades.to_csv(OUT / "正式V006.1_全部成交.csv", index=False, encoding="utf-8-sig")
        all_rejected.extend([{**x, "方案": label} for x in rejected])
    pd.DataFrame(comparison).to_csv(OUT / "三方案比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(candidates).to_csv(OUT / "V006.1全部合格候選.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(all_rejected).to_csv(OUT / "三方案組合未進場.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors + sim_errors).to_csv(OUT / "錯誤.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"項目": k, "數量": v} for k, v in funnel.most_common()]).to_csv(OUT / "訊號漏斗.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"層級方向": k, "數量": v} for k, v in direction_audit.items()]).to_csv(
        OUT / "方向稽核.csv", index=False, encoding="utf-8-sig")

    # 完整列出目前市場為何被納入或排除，避免把後來上市或美股代幣
    # 誤認為回測漏掃。API 無資料者不武斷宣稱上市日，只如實標示無可得K線。
    coverage_rows = []
    item_keys = {(x[0], x[1]) for x in items}
    for r in q.itertuples(index=False):
        exchange = "Binance" if PERIOD_ID == "2023" else str(getattr(r, "exchange", ""))
        symbol = str(getattr(r, "ccxt_symbol", "")) if PERIOD_ID == "2023" else str(getattr(r, "symbol", ""))
        key = (exchange, symbol)
        rows_in_period = int(num(getattr(r, "v145_rows_in_period", getattr(r, "rows", 0)), 0))
        if key in item_keys:
            status, reason = "納入回測", "該期間K線足夠（包含目前已下架市場）"
        elif str(getattr(r, "status", "")) != "下載完成":
            status, reason = "跳過", f"歷史下載失敗：{getattr(r, 'error', '')}"
        else:
            status, reason = "跳過", f"期間K線不足以暖機（{rows_in_period}根）"
        coverage_rows.append({"交易所": key[0], "標的": key[1], "分類": "加密貨幣",
                              "目前已下架": bool(getattr(r, "delisted_now", False)),
                              "處理": status, "原因": reason, "期間K線根數": rows_in_period})
    if SOURCE_ERRORS_CSV.exists():
        source_errors = pd.read_csv(SOURCE_ERRORS_CSV)
        for r in source_errors.itertuples(index=False):
            key = (str(r.exchange), str(r.symbol))
            is_stock = key in stock_keys
            raw_error = str(getattr(r, "error", ""))
            if is_stock:
                reason = f"美股代幣：{PERIOD_ID}期間無完整可用K線，因此不冒充納入"
            elif "沒有取得已收完的K線" in raw_error:
                reason = "該期間無可取得K線（通常為期間結束後才上市；亦可能是API不供應舊資料）"
            else:
                reason = f"來源資料錯誤：{raw_error}"
            coverage_rows.append({"交易所": key[0], "標的": key[1], "分類": "美股代幣" if is_stock else "其他／加密貨幣",
                                  "處理": "排除" if is_stock else "跳過", "原因": reason, "期間K線根數": 0})
    pd.DataFrame(coverage_rows).to_csv(OUT / "全部市場納入與排除原因.csv", index=False, encoding="utf-8-sig")
    (OUT / "summary.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    notes = [
        f"CryptoRadar V006.1 真正逐根回測｜{PERIOD_ID}", "=" * 62,
        f"固定區間：{START} <= 進場 < {END}",
        "策略鏈：V005/V101訊號 → V139低品質閘門 → V101排倉 → V006.1 50/25/25與5/10日線出場。",
        f"市場資料：所有取得足夠15分鐘K線的{len(items)}個不重複市場全部納入；缺資料者不冒充為零交易。",
        ("限制：2023歷史封存只涵蓋Binance USD-M；Bitget、BingX沒有等價官方完整封存。"
         if PERIOD_ID == "2023" else "資料來源：既有Binance、Bitget、BingX穩定快取；各市場獨立判斷。"),
        "限制：回測K線是成交價，實盤停損使用Mark Price；邊界停損仍可能與實盤不同。",
        "同一15分鐘K同時碰停損與停利時，固定採停損優先。",
    ]
    (OUT / "README_請先看.txt").write_text("\n".join(notes), encoding="utf-8-sig")
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    print(f"結果資料夾：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
