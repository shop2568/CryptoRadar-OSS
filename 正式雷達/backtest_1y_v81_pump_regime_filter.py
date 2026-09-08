#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V81：在 V80 暴漲回踩候選上加入該幣自己的高週期趨勢與過熱過濾。"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_1y_v79_pump_first_pullback as core
import backtest_1y_v80_pump_observe_entry as v80


OUT = Path(__file__).with_name("backtest_1y_v81_pump_regime_filter_results")
OUT.mkdir(parents=True, exist_ok=True)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    d = v80.prepare(frame)
    if d.empty:
        return d
    # 15分鐘資料上的 320/800 EMA，約等於已完成4H的 EMA20/EMA50。
    d["ema320"] = d["close"].ewm(span=320, adjust=False).mean()
    d["ema800"] = d["close"].ewm(span=800, adjust=False).mean()
    d["ema320_slope16"] = (d["ema320"] - d["ema320"].shift(16)) / d["atr"].replace(0, np.nan)
    d["ema800_slope16"] = (d["ema800"] - d["ema800"].shift(16)) / d["atr"].replace(0, np.nan)
    d["ret24h"] = d["close"] / d["close"].shift(96) - 1
    d["old_high30d"] = d["high"].shift(32).rolling(96 * 30, min_periods=96 * 7).max()
    return d


def enrich(entries: list[dict], audit: Counter):
    out = []
    for x0 in entries:
        x = dict(x0)
        d = x["_frame"]
        e = int(x["entry_index"])
        k = e - 1
        atr = float(d["atr"].iloc[k])
        close = float(d["close"].iloc[k])
        e320 = float(d["ema320"].iloc[k])
        e800 = float(d["ema800"].iloc[k])
        slope320 = float(d["ema320_slope16"].iloc[k])
        slope800 = float(d["ema800_slope16"].iloc[k])
        ret24 = float(d["ret24h"].iloc[k])
        old_high = float(d["old_high30d"].iloc[k])
        risk = float(x["risk_price"])
        room_r = (old_high - float(x["entry"])) / risk if math.isfinite(old_high) else float("nan")
        heat_atr = (close - e320) / max(atr, 1e-12)
        pump_multiple = float(x["pump_8bar_pct"]) / max(float(x["own_threshold_pct"]), 1e-12)

        x.update({
            "trend_ema20_4h_proxy": e320,
            "trend_ema50_4h_proxy": e800,
            "trend20_slope_atr": slope320,
            "trend50_slope_atr": slope800,
            "return_24h_pct": ret24 * 100,
            "distance_from_4h_ema20_atr": heat_atr,
            "pump_vs_own_threshold": pump_multiple,
            "prior_resistance_room_r": room_r,
            "trend_continuation": bool(close > e320 > e800 and slope320 > 0 and slope800 >= -0.10 and ret24 > 0),
            "trend_recovery": bool(close > e320 and slope320 > 0.05 and ret24 > 0),
        })

        # 通用的反追高品質條件。這些都在進場前已知。
        checks = [
            (float(x["pre_compression_atr"]) <= 5.2, "突破前不夠收斂"),
            (pump_multiple <= 2.20, "暴漲相對自身波動過熱"),
            (float(x["pullback_depth_pct"]) <= 42.0, "回踩仍太深"),
            (float(x["pullback_volume_ratio_to_impulse"]) <= 0.60, "回踩量縮不足"),
            (float(x["reclaim_volume_ratio"]) >= 1.20, "站回量能不足"),
            (int(x["wait_bars"]) <= 10, "回踩等待太久"),
            (float(x["stop_pct"]) <= 0.055, "停損過寬"),
            (heat_atr <= 5.0, "距高週期均線過熱"),
        ]
        failed = [reason for ok, reason in checks if not ok]
        if failed:
            audit.update(failed)
            continue
        out.append(x)
    return out


def run_variant(rows: list[dict], label: str, rr: float):
    sim = pd.DataFrame(core.simulate(x, rr) for x in rows)
    if not sim.empty:
        sim["bucket"] = pd.to_datetime(sim["entry_time"], utc=True).dt.floor("15min")
        sim = sim.sort_values("score", ascending=False).drop_duplicates(["base", "bucket"])
    chosen = core.portfolio_filter(sim)
    chosen.to_csv(OUT / f"{label}_所有交易.csv", index=False, encoding="utf-8-sig")
    btc = chosen[chosen["base"].astype(str).str.upper().eq("BTC")] if not chosen.empty else chosen
    btc.to_csv(OUT / f"{label}_BTC交易.csv", index=False, encoding="utf-8-sig")
    return core.metrics(chosen, label)


def main() -> int:
    files = sorted(core.CACHE.glob("*.pkl.gz"))
    seen = set()
    observations, raw_entries, errors = [], [], []
    v80_rejects = Counter()
    print(f"V81 個幣高週期趨勢＋反過熱｜快取檔 {len(files)}", flush=True)
    for n, path in enumerate(files, 1):
        try:
            frame, meta = core.load_cache(path)
            key = (str(meta.get("exchange")), str(meta.get("symbol")), str(meta.get("first_candle")), str(meta.get("last_candle")))
            if key in seen:
                continue
            seen.add(key)
            d = prepare(frame)
            if d.empty:
                continue
            obs, ent, rej = v80.find(d, meta)
            observations.extend(obs)
            raw_entries.extend(ent)
            v80_rejects.update(rej)
        except Exception as exc:
            errors.append({"file": path.name, "error": repr(exc)})
        if n == 1 or n % 25 == 0 or n == len(files):
            print(f"\r掃描中 {n}/{len(files)}", end="", flush=True)
    print()

    quality_rejects = Counter()
    quality = enrich(raw_entries, quality_rejects)
    continuation = [x for x in quality if x["trend_continuation"]]
    recovery = [x for x in quality if x["trend_recovery"]]

    variants = {
        "V81趨勢延續": continuation,
        "V81重新轉強": recovery,
    }
    metrics = []
    for name, rows in variants.items():
        pd.DataFrame([{k: v for k, v in x.items() if not k.startswith("_")} for x in rows]).to_csv(
            OUT / f"{name}_進場前因果特徵.csv", index=False, encoding="utf-8-sig"
        )
        for rr in (3.0, 4.0):
            metrics.append(run_variant(rows, f"{name}_TP1_{rr:g}R", rr))

    comp = pd.DataFrame(metrics)
    comp.to_csv(OUT / "版本比較.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(observations).to_csv(OUT / "所有暴漲觀察.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(errors).to_csv(OUT / "讀取錯誤.csv", index=False, encoding="utf-8-sig")
    (OUT / "訊號漏斗.json").write_text(json.dumps({
        "unique_markets": len(seen),
        "pump_observations": len(observations),
        "v80_entries": len(raw_entries),
        "quality_after_common_filters": len(quality),
        "trend_continuation": len(continuation),
        "trend_recovery": len(recovery),
        "quality_rejections": dict(quality_rejects),
        "errors": len(errors),
        "btc_used_as_market_filter": False,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 100)
    print(comp.to_string(index=False))
    print(f"\n品質候選={len(quality)}｜趨勢延續={len(continuation)}｜重新轉強={len(recovery)}")
    print(f"結果：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
