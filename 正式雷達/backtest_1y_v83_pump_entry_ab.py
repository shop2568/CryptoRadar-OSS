#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V83：保留正式核心，僅研究兩種暴漲多單進場（TP1 固定 4R）。

保守版：淺回踩延續，或深回踩/假跌破後重新站回。
進取版：先用 25% 風險的先鋒倉，再以 75% 風險加上保守進場。
全部訊號使用當時已完成 K 線，下一根開盤進場。
"""

from __future__ import annotations

import gzip
import json
import math
import pickle
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_1y_v79_pump_first_pullback as core
import backtest_1y_v80_pump_observe_entry as v80
import backtest_1y_v81_pump_regime_filter as v81


OUT = Path(__file__).with_name("backtest_1y_v83_pump_entry_ab_results")
OUT.mkdir(parents=True, exist_ok=True)
RR = 4.0
RECENT = pd.Timestamp("2026-07-29 17:00:00", tz="UTC")


def clean_meta(meta: dict, frame: pd.DataFrame, path: Path) -> dict:
    m = dict(meta)
    m["cache_file"] = path.name
    if not m.get("base"):
        m["base"] = str(m.get("symbol", "")).split("/")[0]
    if not m.get("exchange"):
        m["exchange"] = str(m.get("exchange_name", ""))
    return m


def market_frames() -> tuple[list[tuple[pd.DataFrame, dict]], list[dict]]:
    """同交易所同商品只取資料最後時間最新者，BTC/ETH 使用已更新快取。"""
    files = list(core.CACHE.glob("*.pkl.gz"))
    for name in ("BTC", "ETH"):
        p = Path(__file__).with_name("backtest_1y_v82_core_plus_pump_watch_results") / f"{name}_USDT_15m_更新快取.pkl.gz"
        if p.exists():
            files.append(p)
    selected: dict[tuple[str, str], tuple[pd.Timestamp, pd.DataFrame, dict]] = {}
    errors = []
    for n, path in enumerate(files, 1):
        try:
            frame, meta = core.load_cache(path)
            meta = clean_meta(meta, frame, path)
            if frame.empty:
                continue
            idx = pd.to_datetime(frame.index, utc=True)
            key = (str(meta.get("exchange")), str(meta.get("symbol")))
            last = idx.max()
            prior = selected.get(key)
            if prior is None or last > prior[0] or (last == prior[0] and len(frame) > len(prior[1])):
                selected[key] = (last, frame, meta)
        except Exception as exc:
            errors.append({"file": path.name, "error": repr(exc)})
        if n == 1 or n % 100 == 0 or n == len(files):
            print(f"\r整理市場 {n}/{len(files)}", end="", flush=True)
    print()
    return [(x[1], x[2]) for x in selected.values()], errors


def pump_indexes(d: pd.DataFrame) -> list[int]:
    threshold = d["pump_q98"].clip(lower=0.012)
    mask = (
        (d["pump8"] >= threshold)
        & (d["close"] > d["prior_high32"])
        & (d["vol_ratio"] >= 1.5)
        & (d["body_atr"] >= 0.55)
        & (d["close_location"] >= 0.68)
    )
    indexes = []
    last = -99999
    for i in np.flatnonzero(mask.fillna(False).to_numpy()):
        if i - last <= 16:
            continue
        indexes.append(int(i))
        last = int(i)
    return indexes


def resistance_room(d: pd.DataFrame, k: int, entry: float, risk: float) -> float:
    old_high = float(d["old_high30d"].iloc[k])
    if not math.isfinite(old_high) or old_high <= entry:
        return float("inf")  # 創區間新高，上方沒有已知舊壓力。
    return (old_high - entry) / max(risk, 1e-12)


def candidate(d: pd.DataFrame, meta: dict, i: int, j: int, mode: str, risk_fraction: float):
    e = j + 1
    if e >= len(d):
        return None, "資料不足"
    entry = float(d["open"].iloc[e])
    confirm_close = float(d["close"].iloc[j])
    atr = float(d["atr"].iloc[j])
    stop = float(d["low"].iloc[i : j + 1].min()) - 0.15 * atr
    risk = entry - stop
    stop_pct = risk / entry
    if not (0.006 <= stop_pct <= 0.055):
        return None, "停損距離不在0.6%至5.5%"
    if entry > confirm_close + 0.20 * atr:
        return None, "隔根跳高追價"
    room = resistance_room(d, j, entry, risk)
    if room < RR:
        return None, "已知舊壓力前沒有真實4R空間"

    threshold = max(float(d["pump_q98"].iloc[i]), 0.012)
    heat = (confirm_close - float(d["ema320"].iloc[j])) / max(atr, 1e-12)
    pump_multiple = float(d["pump8"].iloc[i]) / max(threshold, 1e-12)
    # 過熱不再直接刪單，只在排序中扣分。
    score = (
        min(pump_multiple, 3.0) * 12
        + min(float(d["vol_ratio"].iloc[i]), 10.0) * 1.5
        - max(0.0, heat - 4.0) * 3.0
        - (e - i) * 0.25
        + (5.0 if mode == "淺回踩延續" else 2.0 if mode == "假跌破站回" else 0.0)
    )
    exchange = str(meta.get("exchange", ""))
    base = str(meta.get("base", ""))
    pump_time = d.index[i]
    return {
        "version": "V83",
        "entry_mode": mode,
        "exchange": exchange,
        "symbol": str(meta.get("symbol", "")),
        "base": base,
        "pump_time": pump_time,
        "chain_id": f"{exchange}|{base}|{pump_time.isoformat()}",
        "signal_time": d.index[j],
        "entry_time": d.index[e],
        "entry_index": e,
        "entry": entry,
        "stop": stop,
        "risk_price": risk,
        "stop_pct": stop_pct,
        "risk_fraction": risk_fraction,
        "pump_8bar_pct": float(d["pump8"].iloc[i]) * 100,
        "pump_volume_ratio": float(d["vol_ratio"].iloc[i]),
        "distance_from_4h_ema20_atr": heat,
        "prior_resistance_room_r": room,
        "wait_bars": e - i,
        "score": score,
        "_frame": d,
    }, "合格"


def conservative_for_pump(d: pd.DataFrame, meta: dict, i: int):
    start = float(d["prior_close8"].iloc[i])
    breakout = float(d["prior_high32"].iloc[i])
    peak = float(d["high"].iloc[i])
    saw_deep_or_failed = False
    reason = "16根內沒有合格回踩站回"
    for j in range(i + 1, min(i + 17, len(d) - 1)):
        peak = max(peak, float(d["high"].iloc[j]))
        low = float(d["low"].iloc[j])
        close = float(d["close"].iloc[j])
        depth = (peak - low) / max(peak - start, 1e-12)
        if depth > 0.35 or close < breakout:
            saw_deep_or_failed = True

        green_reclaim = (
            close > float(d["ema5"].iloc[j])
            and close > float(d["high"].iloc[j - 1])
            and close > float(d["open"].iloc[j])
            and float(d["close_location"].iloc[j]) >= 0.58
        )
        if not green_reclaim:
            reason = "尚未出現強勢站回K"
            continue

        if saw_deep_or_failed:
            if depth > 0.78:
                reason = "假跌破幅度超過78%"
                continue
            if close <= breakout or float(d["close_location"].iloc[j]) < 0.62:
                reason = "假跌破後尚未站回突破位"
                continue
            row, why = candidate(d, meta, i, j, "假跌破站回", 1.0)
        else:
            if not (0.06 <= depth <= 0.35):
                reason = "淺回踩深度不在6%至35%"
                continue
            if close < breakout * 0.998:
                reason = "淺回踩沒有守住突破位"
                continue
            impulse_vol = float(d["volume"].iloc[i])
            pull_vol = float(d["volume"].iloc[i + 1 : j + 1].median())
            if pull_vol / max(impulse_vol, 1e-12) > 0.95:
                reason = "淺回踩沒有量縮"
                continue
            row, why = candidate(d, meta, i, j, "淺回踩延續", 1.0)
        if row is not None:
            return row, "合格"
        reason = why
    return None, reason


def starter_for_pump(d: pd.DataFrame, meta: dict, i: int):
    # 暴漲確認後下一根開盤先鋒倉；只用 25% 正常風險。
    j = i
    row, reason = candidate(d, meta, i, j, "25%先鋒倉", 0.25)
    if row is None:
        return None, reason
    # 極端單根噴出仍不追；以該幣自身門檻判斷，而非全市場固定漲幅。
    threshold = max(float(d["pump_q98"].iloc[i]), 0.012)
    multiple = float(d["pump8"].iloc[i]) / max(threshold, 1e-12)
    if multiple > 2.8:
        return None, "相對該幣自身波動過熱2.8倍"
    return row, "合格"


def simulate(row: dict) -> dict:
    result = core.simulate(row, RR)
    fraction = float(row["risk_fraction"])
    result["profit_usdt"] = float(result["profit_usdt"]) * fraction
    result["risk_usdt"] = core.RISK_USDT * fraction
    return result


def portfolio_filter(trades: pd.DataFrame) -> pd.DataFrame:
    """Top5 以一段行情為一個倉；同鏈的75%加倉不另占位置。"""
    if trades.empty:
        return trades
    d = trades.sort_values(["entry_time", "score"], ascending=[True, False]).copy()
    chosen, active = [], []
    last_base = {}
    accepted_chains = set()
    for _, row in d.iterrows():
        t = pd.Timestamp(row["entry_time"])
        active = [x for x in active if pd.Timestamp(x[0]) > t]
        chain = str(row["chain_id"])
        base = str(row["base"])
        same_chain = chain in accepted_chains
        if not same_chain:
            if any(x[1] == base for x in active):
                continue
            prior = last_base.get(base)
            if prior is not None and (t - prior).total_seconds() < 24 * 3600:
                continue
            if len({x[2] for x in active}) >= core.MAX_POSITIONS:
                continue
        chosen.append(row.to_dict())
        accepted_chains.add(chain)
        active.append((row["exit_time"], base, chain))
        last_base[base] = t
    return pd.DataFrame(chosen)


def summarize(trades: pd.DataFrame, label: str) -> dict:
    m = core.metrics(trades, label)
    m["總投入風險U"] = round(float(trades.get("risk_usdt", pd.Series(dtype=float)).sum()), 2)
    m["BTC交易"] = int((trades.get("base", pd.Series(dtype=str)).astype(str).str.upper() == "BTC").sum())
    m["ETH交易"] = int((trades.get("base", pd.Series(dtype=str)).astype(str).str.upper() == "ETH").sum())
    return m


def main() -> int:
    markets, errors = market_frames()
    conservative, starters = [], []
    rejects_con, rejects_start = Counter(), Counter()
    observations = 0
    for n, (frame, meta) in enumerate(markets, 1):
        try:
            d = v81.prepare(frame)
            if d.empty:
                continue
            for i in pump_indexes(d):
                observations += 1
                con, reason = conservative_for_pump(d, meta, i)
                if con is not None:
                    conservative.append(con)
                else:
                    rejects_con[reason] += 1
                starter, reason = starter_for_pump(d, meta, i)
                if starter is not None:
                    starters.append(starter)
                else:
                    rejects_start[reason] += 1
        except Exception as exc:
            errors.append({"exchange": meta.get("exchange"), "symbol": meta.get("symbol"), "error": repr(exc)})
        if n == 1 or n % 25 == 0 or n == len(markets):
            print(f"\r掃描市場 {n}/{len(markets)}", end="", flush=True)
    print()

    # 保守版每筆完整 10U 風險。
    con_sim = pd.DataFrame(simulate(x) for x in conservative)
    con_chosen = portfolio_filter(con_sim)

    # 進取版：25%先鋒 + 同一暴漲若出現保守確認則加75%。
    con_add = []
    starter_chains = {x["chain_id"] for x in starters}
    for x in conservative:
        y = dict(x)
        y["risk_fraction"] = 0.75 if y["chain_id"] in starter_chains else 1.0
        y["entry_mode"] = "75%回踩加倉" if y["risk_fraction"] == 0.75 else y["entry_mode"]
        con_add.append(y)
    aggressive_sim = pd.DataFrame([simulate(x) for x in starters] + [simulate(x) for x in con_add])
    aggressive_chosen = portfolio_filter(aggressive_sim)

    con_chosen.to_csv(OUT / "V83保守版_所有交易.csv", index=False, encoding="utf-8-sig")
    aggressive_chosen.to_csv(OUT / "V83進取版_所有交易.csv", index=False, encoding="utf-8-sig")
    recent = pd.concat([
        con_chosen.assign(方案="保守版"),
        aggressive_chosen.assign(方案="進取版"),
    ], ignore_index=True)
    if not recent.empty:
        recent = recent[pd.to_datetime(recent["pump_time"], utc=True) > RECENT]
    recent.to_csv(OUT / "V83_7月29日後BTC_ETH與其他近期交易.csv", index=False, encoding="utf-8-sig")

    comparison = pd.DataFrame([
        summarize(con_chosen, "V83保守版_4R"),
        summarize(aggressive_chosen, "V83進取版_25%先鋒加回踩_4R"),
    ])
    comparison.to_csv(OUT / "V83版本比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(OUT / "V83讀取錯誤.csv", index=False, encoding="utf-8-sig")
    audit = {
        "unique_markets": len(markets),
        "pump_observations": observations,
        "conservative_candidates": len(conservative),
        "starter_candidates": len(starters),
        "conservative_rejections": dict(rejects_con),
        "starter_rejections": dict(rejects_start),
        "errors": len(errors),
        "tp1_rr": RR,
        "future_data_used": False,
        "formal_core_changed": False,
        "auto_trade_enabled": False,
    }
    (OUT / "V83訊號漏斗.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + comparison.to_string(index=False))
    print(f"近期交易：{len(recent)}｜結果：{OUT}")


if __name__ == "__main__":
    main()
