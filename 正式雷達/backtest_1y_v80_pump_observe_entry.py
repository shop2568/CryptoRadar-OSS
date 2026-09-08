#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V80：暴漲先觀察，第一次高品質回踩才做多。只讀既有全年快取。"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_1y_v79_pump_first_pullback as old


OUT = Path(__file__).with_name("backtest_1y_v80_pump_observe_entry_results")
OUT.mkdir(parents=True, exist_ok=True)


def prepare(d: pd.DataFrame) -> pd.DataFrame:
    d = old.indicators(d)
    if d.empty:
        return d
    # 全部 shift(1)：只使用暴漲發生前已完成的資料。
    d["pre_range16"] = (
        d["high"].rolling(16).max().shift(1)
        - d["low"].rolling(16).min().shift(1)
    ) / d["atr"].replace(0, np.nan)
    d["body_atr"] = (d["close"] - d["open"]).abs() / d["atr"].replace(0, np.nan)
    d["close_location"] = (
        (d["close"] - d["low"])
        / (d["high"] - d["low"]).replace(0, np.nan)
    )
    d["vol_prev5"] = d["volume"].rolling(5).median().shift(1)
    return d


def find(d: pd.DataFrame, meta: dict):
    observations, entries = [], []
    reject = Counter()
    threshold = d["pump_q98"].clip(lower=0.012)
    mask = (
        (d["pump8"] >= threshold)
        & (d["close"] > d["prior_high32"])
        & (d["vol_ratio"] >= 1.5)
        & (d["body_atr"] >= 0.55)
        & (d["close_location"] >= 0.68)
    )
    idxs = np.flatnonzero(mask.fillna(False).to_numpy())
    last_impulse = -99999
    last_entry = -99999

    for i in idxs:
        if i - last_impulse <= 16:
            continue
        last_impulse = i
        base = str(meta.get("base", ""))
        common = {
            "exchange": str(meta.get("exchange", "")),
            "symbol": str(meta.get("symbol", "")),
            "base": base,
            "pump_time": d.index[i],
            "pump_8bar_pct": float(d["pump8"].iloc[i]) * 100,
            "own_threshold_pct": float(threshold.iloc[i]) * 100,
            "pump_volume_ratio": float(d["vol_ratio"].iloc[i]),
            "pre_compression_atr": float(d["pre_range16"].iloc[i]),
        }
        observations.append(common)

        start = float(d["prior_close8"].iloc[i])
        breakout = float(d["prior_high32"].iloc[i])
        peak = float(d["high"].iloc[i])
        move = peak - start
        if move <= 0:
            continue
        touched = False
        reason = "12根內沒有合格回踩站回"
        for j in range(i + 1, min(i + 13, len(d) - 1)):
            peak = max(peak, float(d["high"].iloc[j]))
            low = float(d["low"].iloc[j])
            close = float(d["close"].iloc[j])
            depth = (peak - low) / max(peak - start, 1e-12)
            if low <= float(d["ema10"].iloc[j]) * 1.004 or close < float(d["ema5"].iloc[j]):
                touched = True
            if not touched:
                continue
            if depth > 0.52:
                reason = "回踩超過漲幅52%"
                break
            if depth < 0.14:
                continue
            if close < breakout:
                reason = "沒有守住突破位"
                continue

            pull_vol = float(d["volume"].iloc[i + 1 : j + 1].median())
            impulse_vol = float(d["volume"].iloc[i])
            volume_contract = pull_vol / max(impulse_vol, 1e-12)
            reclaim_vol = float(d["volume"].iloc[j]) / max(float(d["vol_prev5"].iloc[j]), 1e-12)
            reclaim = (
                close > float(d["ema5"].iloc[j])
                and close > float(d["high"].iloc[j - 1])
                and close > float(d["open"].iloc[j])
                and float(d["close_location"].iloc[j]) >= 0.62
            )
            if not reclaim:
                reason = "尚未重新轉強"
                continue
            if volume_contract > 0.72:
                reason = "回踩沒有量縮"
                continue
            if reclaim_vol < 1.05:
                reason = "站回時沒有重新放量"
                continue

            e = j + 1
            if e - last_entry < 32:
                reason = "同幣進場冷卻"
                break
            entry = float(d["open"].iloc[e])
            atr = float(d["atr"].iloc[j])
            stop = float(d["low"].iloc[i + 1 : j + 1].min()) - 0.15 * atr
            risk = entry - stop
            if not (0.006 <= risk / entry <= 0.10):
                reason = "停損距離不合理"
                break
            if entry > close + 0.20 * atr:
                reason = "隔根跳高追價"
                break

            # 排序不讀未來結果：壓縮、暴漲強度、量縮與站回量能。
            score = (
                min(float(d["pump8"].iloc[i]) / max(float(threshold.iloc[i]), 1e-12), 3) * 10
                + min(float(d["vol_ratio"].iloc[i]), 8) * 2
                + min(reclaim_vol, 4) * 3
                + max(0, 0.72 - volume_contract) * 15
                - max(0, float(d["pre_range16"].iloc[i]) - 5) * 2
                - (e - i) * 0.2
            )
            entries.append({
                **common,
                "signal_time": d.index[j],
                "entry_time": d.index[e],
                "entry_index": e,
                "entry": entry,
                "stop": stop,
                "risk_price": risk,
                "stop_pct": risk / entry,
                "pullback_depth_pct": depth * 100,
                "pullback_volume_ratio_to_impulse": volume_contract,
                "reclaim_volume_ratio": reclaim_vol,
                "wait_bars": e - i,
                "score": score,
                "_frame": d,
            })
            last_entry = e
            reason = "合格進場"
            break
        reject[reason] += 1
    return observations, entries, reject


def main() -> int:
    files = sorted(old.CACHE.glob("*.pkl.gz"))
    seen = set()
    observations, entries, errors = [], [], []
    rejects = Counter()
    print(f"V80 暴漲觀察→高品質第一次回踩｜快取檔 {len(files)}", flush=True)
    for n, path in enumerate(files, 1):
        try:
            frame, meta = old.load_cache(path)
            key = (
                str(meta.get("exchange")), str(meta.get("symbol")),
                str(meta.get("first_candle")), str(meta.get("last_candle")),
            )
            if key in seen:
                continue
            seen.add(key)
            d = prepare(frame)
            if d.empty:
                continue
            obs, ent, rej = find(d, meta)
            observations.extend(obs)
            entries.extend(ent)
            rejects.update(rej)
        except Exception as exc:
            errors.append({"file": path.name, "error": repr(exc)})
        if n == 1 or n % 25 == 0 or n == len(files):
            print(f"\r掃描中 {n}/{len(files)}", end="", flush=True)
    print()

    pd.DataFrame(observations).to_csv(OUT / "所有暴漲觀察.csv", index=False, encoding="utf-8-sig")
    audit = pd.DataFrame([{k: v for k, v in x.items() if not k.startswith("_")} for x in entries])
    audit.to_csv(OUT / "所有合格回踩候選.csv", index=False, encoding="utf-8-sig")
    comparisons = []
    for rr in (3.0, 4.0):
        sim = pd.DataFrame(old.simulate(x, rr) for x in entries)
        if not sim.empty:
            sim["bucket"] = pd.to_datetime(sim["entry_time"], utc=True).dt.floor("15min")
            sim = sim.sort_values("score", ascending=False).drop_duplicates(["base", "bucket"])
        selected = old.portfolio_filter(sim)
        label = f"V80高品質第一次回踩_TP1_{rr:g}R"
        selected.to_csv(OUT / f"{label}_所有交易.csv", index=False, encoding="utf-8-sig")
        btc = selected[selected["base"].astype(str).str.upper().eq("BTC")] if not selected.empty else selected
        btc.to_csv(OUT / f"{label}_BTC交易.csv", index=False, encoding="utf-8-sig")
        comparisons.append(old.metrics(selected, label))

    comp = pd.DataFrame(comparisons)
    comp.to_csv(OUT / "版本比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(OUT / "讀取錯誤.csv", index=False, encoding="utf-8-sig")
    (OUT / "觀察到但未進場原因.json").write_text(
        json.dumps({
            "unique_markets": len(seen),
            "observations": len(observations),
            "qualified_entries": len(entries),
            "reasons": dict(rejects),
            "errors": len(errors),
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 90)
    print(comp.to_string(index=False))
    print(f"\n暴漲觀察={len(observations)}｜合格回踩={len(entries)}｜結果={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
