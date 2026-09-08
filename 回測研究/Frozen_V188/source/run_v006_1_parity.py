#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CryptoRadar V006.1 正式雷達規則等價歷史重播。

本程式不重新挑參數，也不新增策略。候選訊號沿用同日已完成的三年度
V101 -> V139 掃描結果，再按雲端正式原碼重播：跨所訊號轉幣安、24 小時
訊號冷卻、V101 組合風控、V006.1 50/25/25 出場與 5/10 日線退出。
"""

from __future__ import annotations

import argparse
import copy
import heapq
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


HERE = Path(__file__).resolve().parent
WORK = HERE.parent
BASE_SCRIPT = WORK / "V006三年比較" / "backtest_v006_short_ab_2023.py"
COST = 0.0009
EPS = 1e-12
INITIAL_EQUITY = 1000.0
RISK_PER_TRADE = 0.01
POSITION_NOTIONAL_CAP = 1.50


PERIODS = {
    "2023": {
        "start": "2023-08-01T00:00:00Z", "end": "2024-08-01T00:00:00Z",
        "candidate": WORK / "V006三年比較" / "2023空單AB結果" / "V006.1前一年全部合格候選.csv",
        "quality": Path.home() / "Documents" / "Codex" / "2026-07-23" / "new-chat" / "backtest_v147_2023_delisted_results" / "V147_binance_historical_universe.csv",
    },
    "2024": {
        "start": "2024-08-01T00:00:00Z", "end": "2025-08-01T00:00:00Z",
        # 正式 V139_A 因果重建輸出的「成交＋組合未選中」，兩者合併才是
        # 當時進入 V101 組合風控前的完整合格候選池。
        "candidate": [
            Path.home() / "CryptoRadar" / "backtest_v139_previous_year_true_rebuild_results" / "V139_A全部交易.csv",
            Path.home() / "CryptoRadar" / "backtest_v139_previous_year_true_rebuild_results" / "V139_A組合風控未選中.csv",
        ],
        "quality": Path.home() / "CryptoRadar" / "backtest_v145_previous_year_upstream_rebuild_results" / "01_v52_rr4" / "exchange_markets" / "data_quality.csv",
        "source_note": "正式V139_A前一年候選池",
    },
    "2025": {
        "start": "2025-08-01T00:00:00Z", "end": "2026-07-30T00:00:00Z",
        "candidate": [
            WORK / "backtest_1y_v139_narrow_overbuild_hard_exit_results" / "report_candidate_all_trades.csv",
            WORK / "backtest_1y_v139_narrow_overbuild_hard_exit_results" / "portfolio_rejected.csv",
        ],
        "quality": Path.home() / "CryptoRadar" / "backtest_1y_v52_results" / "exchange_markets" / "data_quality.csv",
        "source_note": "正式V139_A近一年候選池",
    },
}


def load_candidates(source: Path | list[Path]) -> list[dict]:
    paths = source if isinstance(source, list) else [source]
    frames = [pd.read_csv(path, low_memory=False) for path in paths]
    data = pd.concat(frames, ignore_index=True, sort=False)
    # 不讓「原本成交」與「未選中」檔案中的重複列進入兩次。
    for col in ("exchange", "symbol", "entry_time"):
        if col not in data.columns:
            data[col] = ""
    direction = data.get("direction", pd.Series("", index=data.index)).fillna("").astype(str)
    direction_v92 = data.get("direction_v92", pd.Series("", index=data.index)).fillna("").astype(str)
    data["direction"] = direction.where(direction.str.upper().isin(["LONG", "SHORT"]), direction_v92)
    data = data[data.direction.str.upper().isin(["LONG", "SHORT"])].copy()
    data.drop_duplicates(["exchange", "symbol", "direction", "entry_time"], keep="first", inplace=True)
    records = data.to_dict("records")
    for row in records:
        # 欄名差異只做語意等價映射，不修改門檻或分數。
        if not str(row.get("base") or "").strip() or str(row.get("base")).lower() == "nan":
            row["base"] = str(row.get("symbol", "")).split("/")[0]
        family = str(row.get("portfolio_kind") or "")
        if not family or family.lower() == "nan":
            family = str(row.get("candidate_family") or row.get("family") or "CORE")
        row["portfolio_kind"] = "NEW" if family.upper() in {"NEW", "EXPANSION"} else "CORE"
        priority = number(row.get("priority"))
        if not math.isfinite(priority):
            priority = number(row.get("priority_score"), number(row.get("score"), 0.0))
        row["priority"] = priority
    return records


def number(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def load_base(period: str):
    os.environ["V006_PERIOD"] = period
    project = str(Path.home() / "CryptoRadar")
    if project not in sys.path:
        sys.path.insert(0, project)
    spec = importlib.util.spec_from_file_location(f"v006_base_{period}", BASE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    day = frame[["open", "high", "low", "close", "volume"]].resample("1D").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).dropna(subset=["close"])
    day["sma5"] = day.close.rolling(5, min_periods=5).mean()
    day["sma10"] = day.close.rolling(10, min_periods=10).mean()
    return day


def dynamic_trend_ma(history: pd.DataFrame, direction: str) -> tuple[int, str]:
    if len(history) < 12:
        return 5, "日K不足，先用5日線保護"
    sample = history.tail(12); last = sample.iloc[-1]
    sign = 1.0 if direction == "LONG" else -1.0
    close, ma5, ma10 = number(last.close), number(last.sma5), number(last.sma10)
    if not all(math.isfinite(x) for x in (close, ma5, ma10)):
        return 5, "均線資料不足，先用5日線保護"
    ma5_old, ma10_old = number(sample.sma5.iloc[-4]), number(sample.sma10.iloc[-4])
    ordered = sign * (close - ma5) > 0 and sign * (ma5 - ma10) > 0
    slopes = sign * (ma5 - ma5_old) > 0 and sign * (ma10 - ma10_old) > 0
    signed_move = sign * (close - number(sample.close.iloc[-6]))
    travel = float(sample.close.tail(6).diff().abs().sum())
    efficiency = signed_move / max(travel, EPS)
    extension = sign * (close - ma10) / max(abs(ma10), EPS)
    if ordered and slopes and efficiency >= 0.35 and extension <= 0.18:
        return 10, f"趨勢完整且順暢（效率{efficiency:.2f}），使用10日線"
    return 5, f"短線轉弱或趨勢不夠順（效率{efficiency:.2f}），使用5日線"


def cost_break_even(entry: float, direction: str) -> float:
    if direction == "LONG":
        return entry * (1.0 + COST) / (1.0 - COST)
    return entry * (1.0 - COST) / (1.0 + COST)


def simulate(row: dict, frame: pd.DataFrame) -> tuple[dict, list[dict]]:
    x = dict(row)
    direction = str(x.get("direction", "LONG")).upper()
    sign = 1.0 if direction == "LONG" else -1.0
    entry_time = pd.to_datetime(x["entry_time"], utc=True)
    entry, stop, tp1 = number(x.get("entry")), number(x.get("stop")), number(x.get("tp1"))
    risk = abs(entry - stop)
    tp2, tp3 = number(x.get("tp2")), number(x.get("tp3"))
    data = frame[frame.index >= entry_time]
    if data.empty or min(entry, stop, tp1, risk) <= 0:
        x.update(final_exit_reason_v92="資料不足", outcome_v92="OPEN", remaining_pct_v92=100.0)
        return x, []
    if not math.isfinite(tp2) or sign * (tp2 - tp1) <= 0:
        tp2 = math.nan
    base_for_tp3 = tp2 if math.isfinite(tp2) else tp1
    if not math.isfinite(tp3) or sign * (tp3 - base_for_tp3) <= 0:
        tp3 = math.nan
    x.update(tp2=tp2, tp3=tp3)
    day = daily_frame(frame)
    remaining = 1.0
    realized_r = -entry * COST / risk
    events = [{"time": entry_time, "pnl_r": realized_r, "event": "進場成本"}]
    tp1_hit = tp2_hit = tp3_hit = False
    tp1_time = tp2_time = tp3_time = pd.NaT
    final_time = pd.NaT; final_price = math.nan; final_reason = "資料結束仍持有"
    selected_ma = 0; selected_ma_reason = ""; ma_checked_bar = None

    def sell(weight: float, price: float, when: pd.Timestamp, reason: str):
        nonlocal remaining, realized_r, final_time, final_price, final_reason
        weight = min(weight, remaining)
        if weight <= EPS:
            return
        component = weight * sign * (price - entry) / risk - weight * price * COST / risk
        realized_r += component; remaining = max(0.0, remaining - weight)
        events.append({"time": when, "pnl_r": component, "event": reason})
        if remaining <= EPS:
            final_time, final_price, final_reason = when, price, reason

    rows = list(data.itertuples())
    for i, bar in enumerate(rows):
        when = pd.Timestamp(bar.Index)
        o, high, low, close = number(bar.open), number(bar.high), number(bar.low), number(bar.close)
        stop_now = cost_break_even(entry, direction) if tp1_hit else stop
        hit_stop = low <= stop_now if direction == "LONG" else high >= stop_now
        hit1 = high >= tp1 if direction == "LONG" else low <= tp1
        hit2 = math.isfinite(tp2) and (high >= tp2 if direction == "LONG" else low <= tp2)
        hit3 = math.isfinite(tp3) and (high >= tp3 if direction == "LONG" else low <= tp3)
        # 15m 內無法辨識先後，沿用正式研究的保守停損優先。
        if hit_stop:
            sell(remaining, stop_now, when, "原停損" if not tp1_hit else "含成本保本")
            break
        if not tp1_hit and hit1:
            tp1_hit = True; tp1_time = when; sell(0.50, tp1, when, "TP1賣50%")
            completed_days = day[day.index < when.floor("D")]
            selected_ma, selected_ma_reason = dynamic_trend_ma(completed_days, direction)
        if tp1_hit and remaining > EPS and not tp2_hit and hit2:
            tp2_hit = True; tp2_time = when; sell(0.25, tp2, when, "TP2賣25%")
        if tp1_hit and remaining > EPS and not tp3_hit and hit3:
            tp3_hit = True; tp3_time = when; sell(remaining, tp3, when, "TP3賣全部剩餘")
        if remaining <= EPS:
            break
        # V006.1：TP1 時固定選 5D/10D；其後每根已完成 15m 收盤確認，下一根開盤近似市價退出。
        if tp1_hit and selected_ma in (5, 10) and i + 1 < len(rows):
            candle_key = when.isoformat()
            if candle_key != ma_checked_bar:
                ma_checked_bar = candle_key
                completed_days = day[day.index < when.floor("D")]
                if not completed_days.empty:
                    ma_value = number(completed_days.iloc[-1].get(f"sma{selected_ma}"))
                    crossed = close < ma_value if direction == "LONG" else close > ma_value
                    if math.isfinite(ma_value) and crossed:
                        nxt = rows[i + 1]
                        sell(remaining, number(nxt.open), pd.Timestamp(nxt.Index), f"{selected_ma}日線弱化退出")
                        break

    last_price = number(data.close.iloc[-1])
    unrealized_r = (remaining * sign * (last_price - entry) / risk - remaining * last_price * COST / risk) if remaining > EPS else 0.0
    closed = remaining <= EPS
    x.update(
        tp1_time_v92=tp1_time, tp2_time_v92=tp2_time, tp3_time_v92=tp3_time,
        final_exit_time_v92=final_time, final_exit_price_v92=final_price,
        final_exit_reason_v92=final_reason, remaining_pct_v92=remaining * 100.0,
        realized_net_r_v92=realized_r, unrealized_net_r_v92=unrealized_r,
        outcome_v92="WIN" if tp1_hit else ("LOSS" if closed else "OPEN"),
        post_tp1_ma_days=selected_ma, post_tp1_ma_reason=selected_ma_reason,
        last_time_v92=data.index[-1], last_price_v92=last_price,
        exit_time=final_time if closed else data.index[-1],
        exit_price=final_price if closed else last_price, exit_reason=final_reason,
        stop_pct=risk / entry, rr1=sign * (tp1 - entry) / risk,
    )
    return x, events


def base_name(row: dict) -> str:
    return str(row.get("base") or str(row.get("symbol", "")).split("/")[0]).replace("1000", "").upper()


def elite_reason(row: dict) -> str:
    side = str(row.get("direction", "")).upper()
    module = str(row.get("module") or row.get("setup") or row.get("setup_type") or "").upper()
    if side == "SHORT" and any(x in module for x in ("V72.8S", "REBOUND_FAIL", "FIRST_PULLBACK_RECLAIM")):
        return "核心反彈失敗空單"
    score, build = number(row.get("score")), number(row.get("volume_build_3v20"))
    volume = number(row.get("volume_ratio"), number(row.get("volume")))
    slope, heat, stop_pct = number(row.get("ema20_slope8_atr")), number(row.get("ret8_before_entry_pct")), number(row.get("stop_pct"))
    if math.isfinite(score) and math.isfinite(build) and score >= 45.0 and build >= 2.0:
        return "單標的量能堆積加高結構分"
    if all(math.isfinite(v) for v in (score, volume, heat, stop_pct)) and score >= 40.0 and volume >= 2.3 and heat <= 0.6 and stop_pct <= 0.010:
        return "低追價窄停損早期趨勢"
    if all(math.isfinite(v) for v in (score, slope, heat, stop_pct)) and score >= 38.0 and slope >= 0.8 and heat <= 0.6 and stop_pct <= 0.010:
        return "高斜率低追價早期趨勢"
    return ""


def trade_end(obj: dict) -> pd.Timestamp:
    value = pd.to_datetime(obj["trade"].get("final_exit_time_v92"), utc=True, errors="coerce")
    return value if pd.notna(value) else obj["period_end"]


def allocate_exact(objects: list[dict], period_end: pd.Timestamp) -> tuple[list[dict], list[dict]]:
    groups: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for obj in objects:
        groups[pd.to_datetime(obj["trade"]["entry_time"], utc=True)].append(obj)
        obj["period_end"] = period_end
    accepted: list[dict] = []; rejected: list[dict] = []; last_signal: dict[str, pd.Timestamp] = {}
    for when in sorted(groups):
        active = [x for x in accepted if trade_end(x) > when]
        history = [x for x in accepted if trade_end(x) <= when]
        candidates = []
        for obj in groups[when]:
            tr = obj["trade"]; kind = "NEW" if str(tr.get("portfolio_kind") or tr.get("family")).upper() in {"NEW", "EXPANSION"} else "CORE"
            tr["portfolio_kind"] = kind
            key = f"{base_name(tr)}:{str(tr.get('direction')).upper()}:{kind}"
            if key in last_signal and when - last_signal[key] < pd.Timedelta(hours=24):
                rejected.append({**tr, "V101拒絕原因": "同類訊號24小時冷卻"})
            else:
                candidates.append(obj)
        ranked = sorted(candidates, key=lambda o: (
            0 if o["trade"]["portfolio_kind"] == "CORE" else 1,
            -number(o["trade"].get("priority"), number(o["trade"].get("score"), -999.0)),
            base_name(o["trade"]),
        ))
        used = {base_name(x["trade"]) for x in active}; chosen: list[dict] = []
        live_new = sum(x["trade"].get("portfolio_kind") == "NEW" for x in active)
        for obj in ranked:
            tr = obj["trade"]; base = base_name(tr); side = str(tr.get("direction", "")).upper(); kind = tr["portfolio_kind"]
            reason = ""
            if not base or base in used:
                reason = "同標的已有持倉或已選中"
            elite = elite_reason(tr)
            prior_stops = [trade_end(x) for x in history if str(x["trade"].get("direction", "")).upper() == side and x["trade"].get("final_exit_reason_v92") == "原停損" and when - pd.Timedelta(hours=72) <= trade_end(x) < when]
            if not reason and len(prior_stops) >= 2 and when < max(prior_stops) + pd.Timedelta(hours=48) and not elite:
                reason = f"{side}過去72小時已有{len(prior_stops)}筆停損，冷卻中"
            prior_entries = [pd.to_datetime(x["trade"]["entry_time"], utc=True) for x in accepted if str(x["trade"].get("direction", "")).upper() == side and when - pd.Timedelta(hours=24) <= pd.to_datetime(x["trade"]["entry_time"], utc=True) < when]
            if not reason and len(prior_entries) >= 2 and not elite:
                reason = f"{side}過去24小時已有{len(prior_entries)}筆進場"
            if not reason and len(active) + len(chosen) >= 5:
                reason = "當下已滿五倉"
            if not reason and kind == "NEW" and live_new + sum(x["trade"].get("portfolio_kind") == "NEW" for x in chosen) >= 2:
                reason = "新模組已滿兩倉"
            if reason:
                rejected.append({**tr, "V101拒絕原因": reason}); continue
            tr["V101頂級例外原因"] = elite
            used.add(base); chosen.append(obj)
        for obj in chosen:
            tr = obj["trade"]; kind = tr["portfolio_kind"]
            key = f"{base_name(tr)}:{str(tr.get('direction')).upper()}:{kind}"
            last_signal[key] = when; accepted.append(obj)
    return accepted, rejected


def money_management(objects: list[dict]) -> tuple[pd.DataFrame, dict]:
    equity = INITIAL_EQUITY; peak = equity; max_dd = 0.0; risk_cash: dict[int, float] = {}
    timeline = []; seq = 0; pnl_by = defaultdict(float)
    for i, obj in enumerate(objects):
        entry_time = pd.to_datetime(obj["trade"]["entry_time"], utc=True)
        heapq.heappush(timeline, (entry_time, 0, seq, i, None)); seq += 1
        for event in obj["events"]:
            heapq.heappush(timeline, (pd.to_datetime(event["time"], utc=True), 1, seq, i, event)); seq += 1
    while timeline:
        when, kind, _, i, event = heapq.heappop(timeline)
        tr = objects[i]["trade"]
        if kind == 0:
            effective_risk = min(RISK_PER_TRADE, POSITION_NOTIONAL_CAP * number(tr.get("stop_pct"), 0.0))
            risk_cash[i] = equity * max(effective_risk, 0.0)
            continue
        pnl = number(event.get("pnl_r"), 0.0) * risk_cash.get(i, 0.0)
        equity += pnl; pnl_by[i] += pnl; peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / max(peak, EPS) * 100.0)
    rows = []
    for i, obj in enumerate(objects):
        tr = dict(obj["trade"]); tr["已實現淨利_USDT"] = pnl_by[i]; rows.append(tr)
    trades = pd.DataFrame(rows)
    tp1 = int(trades.tp1_time_v92.notna().sum()) if not trades.empty else 0
    stops = int((trades.final_exit_reason_v92 == "原停損").sum()) if not trades.empty else 0
    long_df = trades[trades.direction.astype(str).str.upper() == "LONG"] if not trades.empty else trades
    short_df = trades[trades.direction.astype(str).str.upper() == "SHORT"] if not trades.empty else trades
    positive = float(trades.loc[trades["已實現淨利_USDT"] > 0, "已實現淨利_USDT"].sum()) if not trades.empty else 0.0
    negative = abs(float(trades.loc[trades["已實現淨利_USDT"] < 0, "已實現淨利_USDT"].sum())) if not trades.empty else 0.0
    ordered = trades.sort_values("entry_time") if not trades.empty else trades
    longest_loss = current_loss = 0
    for _, tr in ordered.iterrows():
        if str(tr.get("outcome_v92", "")) == "LOSS":
            current_loss += 1; longest_loss = max(longest_loss, current_loss)
        elif str(tr.get("outcome_v92", "")) == "WIN":
            current_loss = 0
    summary = {
        "交易數": len(trades), "TP1筆數": tp1, "TP1前停損": stops,
        "未平倉": int((trades.outcome_v92 == "OPEN").sum()) if not trades.empty else 0,
        "總勝率_pct": round(tp1 / max(tp1 + stops, 1) * 100.0, 4),
        "已實現淨利_USDT": round(equity - INITIAL_EQUITY, 4),
        "期末權益_USDT": round(equity, 4), "最大回撤_pct": round(max_dd, 4),
        "Profit_Factor": round(positive / negative, 4) if negative > EPS else None,
        "最大連敗": longest_loss,
        "做多交易": len(long_df), "做多TP1": int(long_df.tp1_time_v92.notna().sum()) if len(long_df) else 0,
        "做多淨利_USDT": round(float(long_df["已實現淨利_USDT"].sum()), 4) if len(long_df) else 0.0,
        "做空交易": len(short_df), "做空TP1": int(short_df.tp1_time_v92.notna().sum()) if len(short_df) else 0,
        "做空淨利_USDT": round(float(short_df["已實現淨利_USDT"].sum()), 4) if len(short_df) else 0.0,
    }
    return trades, summary


def binance_map(period: str, quality: Path) -> dict[str, str]:
    if period == "2023":
        data = pd.read_csv(quality)
        symbol_col = "symbol" if "symbol" in data.columns else "ccxt_symbol"
        return {str(r.get("base", "")).replace("1000", "").upper(): str(r[symbol_col]) for _, r in data.iterrows() if str(r.get("exchange", "Binance")) == "Binance"}
    data = pd.read_csv(quality)
    return {str(r.base).replace("1000", "").upper(): str(r.symbol) for r in data.itertuples(index=False) if str(r.exchange) == "Binance"}


def translate(row: dict, source: pd.DataFrame, target: pd.DataFrame, target_symbol: str) -> dict | None:
    when = pd.to_datetime(row["entry_time"], utc=True)
    bar = target[target.index >= when].head(1)
    if bar.empty:
        return None
    source_entry = number(row.get("entry")); target_entry = number(bar.open.iloc[0])
    if min(source_entry, target_entry) <= 0:
        return None
    ratio = target_entry / source_entry; out = dict(row)
    out.update(source_entry=source_entry, source_symbol=row.get("symbol"), symbol=target_symbol, cross_exchange_ratio=ratio)
    for key in ("entry", "stop", "tp1", "tp2", "tp3"):
        value = number(out.get(key))
        if math.isfinite(value): out[key] = value * ratio
    out["priority"] = number(out.get("priority"), 0.0) - 2.0
    return out


def run(period: str) -> dict:
    cfg = PERIODS[period]; start = pd.Timestamp(cfg["start"]); end = pd.Timestamp(cfg["end"])
    base = load_base(period)
    out = HERE / f"{period}_V006.1實盤等價結果"; out.mkdir(parents=True, exist_ok=True)
    candidates = load_candidates(cfg["candidate"])
    candidates = [x for x in candidates if str(x.get("research_variant", "BASE")) in {"BASE", "", "nan"}]
    bmap = binance_map(period, cfg["quality"])
    frames: dict[tuple[str, str], pd.DataFrame] = {}; prepared = []; errors = []
    keys = sorted({(str(x.get("exchange")), str(x.get("symbol"))) for x in candidates})
    for n, key in enumerate(keys, 1):
        frames[key] = base.load_frame(*key)
        print(f"\r{period} 載入K線 {n}/{len(keys)}", end="", flush=True)
    print()
    bkeys = sorted({("Binance", symbol) for symbol in bmap.values()})
    for key in bkeys:
        if key not in frames: frames[key] = base.load_frame(*key)
    for n, row in enumerate(candidates, 1):
        try:
            row["entry_time"] = pd.to_datetime(row["entry_time"], utc=True)
            source_key = (str(row.get("exchange")), str(row.get("symbol"))); source = frames.get(source_key, pd.DataFrame())
            if source.empty: raise RuntimeError("來源K線不存在")
            actual = dict(row); target_frame = source
            if source_key[0] != "Binance":
                target_symbol = bmap.get(base_name(row))
                if not target_symbol: raise RuntimeError("幣安沒有同標的，正式雷達不會下單")
                target_frame = frames.get(("Binance", target_symbol), pd.DataFrame())
                if target_frame.empty: raise RuntimeError("幣安同標的歷史K線不足")
                actual = translate(row, source, target_frame, target_symbol)
                if actual is None: raise RuntimeError("跨交易所價位轉換失敗")
            trade, events = simulate(actual, target_frame)
            prepared.append({"input": actual, "trade": trade, "events": events})
        except Exception as exc:
            errors.append({"exchange": row.get("exchange"), "symbol": row.get("symbol"), "entry_time": row.get("entry_time"), "錯誤": f"{type(exc).__name__}: {exc}"})
        if n % 100 == 0 or n == len(candidates):
            print(f"\r{period} 重播出場 {n}/{len(candidates)}", end="", flush=True)
    print()
    accepted, rejected = allocate_exact(prepared, end)
    trades, summary = money_management(accepted)
    summary.update({"期間": period, "開始": str(start), "結束": str(end), "候選來源": cfg.get("source_note", "舊三年候選池（近似）"), "候選數": len(candidates), "可模擬候選": len(prepared), "組合未進場": len(rejected), "錯誤": len(errors)})
    trades.to_csv(out / "全部成交.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rejected).to_csv(out / "未進場.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(out / "錯誤.csv", index=False, encoding="utf-8-sig")
    (out / "摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "限制說明.txt").write_text(
        "策略規則與雲端 V006.1 對齊。歷史結果仍無法逐筆重現實盤的 Mark Price、即時 ticker 延遲、"
        "交易所數量/價格精度、真實滑價、資金費率與 API 失敗；跨所訊號以同時段幣安開盤價近似正式即時轉價。\n",
        encoding="utf-8-sig",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--period", choices=["2023", "2024", "2025", "all"], default="all")
    args = ap.parse_args(); periods = list(PERIODS) if args.period == "all" else [args.period]
    summaries = [run(p) for p in periods]
    pd.DataFrame(summaries).to_csv(HERE / "三年實盤等價比較.csv", index=False, encoding="utf-8-sig")
    (HERE / "三年實盤等價摘要.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
